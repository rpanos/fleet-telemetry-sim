"""Run the simulated fleet: one physics model + one gateway per trailer."""
import logging
import random
import signal
import threading
import time

from .config import build_fleet, load_settings
from .gateway import Gateway, now_ms
from .physics import Trailer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("sim")


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

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    tick = 0
    next_t = time.monotonic()
    while not stop.is_set():
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
            log.info("published %d bundles, %d module readings", len(pairs), readings)
        next_t += 1.0
        stop.wait(max(0.0, next_t - time.monotonic()))

    log.info("shutting down: sending clean 'offline' for every trailer")
    for _, gw in pairs:
        gw.shutdown()


if __name__ == "__main__":
    main()
