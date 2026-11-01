"""Run the simulated fleet: one physics model + one gateway per trailer.

Fault injection: publish to the `sim/control` topic, e.g.
  {"action": "outage", "trailer_id": "TR-001", "seconds": 120}
  {"action": "outage", "trailer_id": "*", "seconds": 60}      (whole fleet)
"""
import json
import logging
import queue
import random
import signal
import threading
import time

import paho.mqtt.client as mqtt

from .config import build_fleet, load_settings
from .gateway import Gateway, now_ms
from .physics import Trailer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("sim")


def start_control_listener(settings, commands):
    """A separate MQTT client that only listens for fault-injection commands."""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sim-control", protocol=mqtt.MQTTv5)
    c.on_connect = lambda client, u, f, rc, p: client.subscribe("sim/control", qos=1)
    c.on_message = lambda client, u, msg: commands.put(msg.payload)
    c.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=30)
    c.loop_start()
    return c


def apply_command(raw, gateways):
    try:
        cmd = json.loads(raw)
        if cmd.get("action") != "outage":
            raise ValueError(f"unknown action {cmd.get('action')!r}")
        seconds = int(cmd.get("seconds", 120))
        target = cmd.get("trailer_id", "*")
    except (ValueError, TypeError) as e:
        log.warning("ignoring bad control message %r: %s", raw, e)
        return
    hit = [gw for tid, gw in gateways.items() if target in ("*", tid)]
    if not hit:
        log.warning("control: no trailer %s", target)
    for gw in hit:
        gw.start_outage(seconds)


def main():
    settings = load_settings()
    fleet = build_fleet(settings.fleet_size, settings.seed)

    log.info("Fleet of %d trailers (seed %d), broker %s:%d",
             len(fleet), settings.seed, settings.mqtt_host, settings.mqtt_port)
    for spec in fleet:
        log.info("  %s  %-22s %-12s %2d modules (%d kW)", spec.trailer_id, spec.site.site_id,
                 spec.site.kind, spec.n_modules, spec.n_modules * 20)

    pairs = [(Trailer(spec, random.Random(settings.seed * 1000 + i)), Gateway(spec, settings))
             for i, spec in enumerate(fleet)]
    gateways = {t.spec.trailer_id: gw for t, gw in pairs}
    for trailer, gw in pairs:
        gw.controller = trailer   # the gateway asks the controller; the controller decides
    commands = queue.Queue()
    control = start_control_listener(settings, commands)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    tick = 0
    next_t = time.monotonic()
    while not stop.is_set():
        while not commands.empty():
            apply_command(commands.get(), gateways)
        ts = now_ms()
        for trailer, gw in pairs:
            module_samples, trailer_sample, events = trailer.step(ts, 1.0)
            gw.add(module_samples, trailer_sample)
            for e in events:
                gw.publish_event(e)
                log.info("%s %-7s %s %s %s", trailer.spec.trailer_id, e["severity"],
                         e["code"], e["module_id"] or "", e["detail"])
        tick += 1
        if tick % settings.publish_every_s == 0:
            readings = sum(gw.flush() for _, gw in pairs)
            log.info("queued %d bundles (%d module readings)", len(pairs), readings)
        for _, gw in pairs:
            gw.pump()
        next_t += 1.0
        stop.wait(max(0.0, next_t - time.monotonic()))

    log.info("shutting down: sending clean 'offline' for every trailer")
    control.loop_stop()
    for _, gw in pairs:
        gw.shutdown()


if __name__ == "__main__":
    main()
