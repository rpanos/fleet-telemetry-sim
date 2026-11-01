"""The trailer's gateway: its one internet-connected computer.

It collects 1-second samples from every module, bundles them, and publishes
over MQTT. Each gateway has its own MQTT connection and its own Last Will,
exactly like a real trailer would.

M1 LIMITATION (deliberate, see docs/decisions.md D-111): while disconnected,
paho-mqtt queues messages in *memory*. A gateway crash or reboot loses them.
M3 replaces this with an on-disk store-and-forward queue.
"""
import json
import logging
import time
import uuid

import paho.mqtt.client as mqtt

from .config import Settings, TrailerSpec

SCHEMA = "telemetry.v1"   # versioned message contract shared with the cloud side
log = logging.getLogger("gateway")


def now_ms():
    return int(time.time() * 1000)


class Gateway:
    def __init__(self, spec: TrailerSpec, settings: Settings):
        self.spec = spec
        self.seq = 0
        self.boot_id = uuid.uuid4().hex[:8]   # seq restarts at 1 on every reboot
        self.module_samples = []
        self.trailer_samples = []
        tid = spec.trailer_id
        self.t_telemetry = f"fleet/{tid}/telemetry"
        self.t_status = f"fleet/{tid}/status"
        self.t_alarms = f"fleet/{tid}/alarms"

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"gw-{tid}", protocol=mqtt.MQTTv5)
        # Last Will: if this gateway vanishes without a goodbye, the BROKER
        # publishes "offline" for it (retained, so late subscribers see it too).
        self.client.will_set(self.t_status,
                             json.dumps({"state": "offline", "reason": "connection_lost"}),
                             qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port,
                                  keepalive=settings.keepalive_s)
        self.client.loop_start()   # background network thread; handles reconnects

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("%s connect failed: %s", self.spec.trailer_id, reason_code)
            return
        client.publish(self.t_status, json.dumps({
            "state": "online", "ts_ms": now_ms(),
            "site_id": self.spec.site.site_id, "n_modules": self.spec.n_modules,
        }), qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("%s disconnected: %s", self.spec.trailer_id, reason_code)

    def add(self, module_samples, trailer_sample):
        self.module_samples.extend(module_samples)
        self.trailer_samples.append(trailer_sample)

    def flush(self):
        """Publish everything collected since the last flush as one bundle."""
        if not self.trailer_samples:
            return 0
        self.seq += 1
        bundle = {
            "schema": SCHEMA,
            "trailer_id": self.spec.trailer_id,
            "site_id": self.spec.site.site_id,
            "boot_id": self.boot_id,
            "seq": self.seq,            # per-trailer counter: lets the cloud spot gaps/dupes
            "sent_at_ms": now_ms(),     # arrival-ish time; each sample keeps its own ts_ms
            "module_samples": self.module_samples,
            "trailer_samples": self.trailer_samples,
        }
        n = len(self.module_samples)
        self.module_samples, self.trailer_samples = [], []
        self.client.publish(self.t_telemetry, json.dumps(bundle, separators=(",", ":")), qos=1)
        return n

    def publish_event(self, event):
        payload = dict(event, trailer_id=self.spec.trailer_id)
        self.client.publish(self.t_alarms, json.dumps(payload), qos=1)

    def shutdown(self):
        """Clean goodbye: say offline ourselves, so the Last Will is NOT used."""
        info = self.client.publish(self.t_status, json.dumps(
            {"state": "offline", "reason": "shutdown", "ts_ms": now_ms()}), qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=3)
        except Exception:  # not connected; nothing more we can do
            pass
        self.client.disconnect()
        self.client.loop_stop()
