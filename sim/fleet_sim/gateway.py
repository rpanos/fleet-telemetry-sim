"""The trailer's gateway: its one internet-connected computer.

STORE-AND-FORWARD (M3). Every message goes to an on-disk queue (the
"outbox", a SQLite file) FIRST. A sender drains it to the broker and deletes
a row only after the broker acknowledges it (PUBACK). So:

- Cell signal drops -> messages pile up on disk; nothing is lost.
- Gateway reboots mid-outage -> the queue survives on disk.
- An ack gets lost -> the row is sent again. That's a duplicate, and the cloud
  ignores it (idempotent writes).

After a reconnect there are two lanes:
- LIVE lane: anything from the last 15 s goes first, so the dashboard shows
  "what's happening now" immediately.
- BACKFILL lane: older history trickles out, oldest first, rate-limited the
  way a real cellular link would be.

Threading note: paho runs its network loop in a background thread. All
SQLite access stays on the main thread; the paho thread only drops acked
message ids into a deque.
"""
import collections
import json
import logging
import os
import sqlite3
import time
import uuid

import paho.mqtt.client as mqtt

from .config import Settings, TrailerSpec

SCHEMA = "telemetry.v1"   # versioned message contract shared with the cloud side
LIVE_WINDOW_MS = 15_000   # "live lane" = messages created in the last 15 s
MAX_INFLIGHT = 20         # sent but not yet acked
MAX_OUTBOX_ROWS = 50_000  # disk-full policy: drop the oldest beyond this (~5 days of bundles)
NO_SIGNAL_DBM = -125.0

log = logging.getLogger("gateway")


def now_ms():
    return int(time.time() * 1000)


class Outbox:
    """On-disk FIFO queue. Rows are deleted only after the broker acks them."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)   # autocommit: every write hits disk
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, "
                        "payload BLOB NOT NULL, created_ms INTEGER NOT NULL)")

    def put(self, topic, payload, created_ms):
        self.db.execute("INSERT INTO outbox (topic, payload, created_ms) VALUES (?, ?, ?)",
                        (topic, payload, created_ms))
        excess = self.count() - MAX_OUTBOX_ROWS
        if excess > 0:
            self.db.execute("DELETE FROM outbox WHERE id IN "
                            "(SELECT id FROM outbox ORDER BY id LIMIT ?)", (excess,))
        return max(excess, 0)

    def count(self):
        return self.db.execute("SELECT count(*) FROM outbox").fetchone()[0]

    def newest(self, since_ms, limit):
        return self.db.execute("SELECT id, topic, payload FROM outbox WHERE created_ms >= ? "
                               "ORDER BY id DESC LIMIT ?", (since_ms, limit)).fetchall()

    def oldest(self, limit):
        return self.db.execute("SELECT id, topic, payload FROM outbox ORDER BY id LIMIT ?",
                               (limit,)).fetchall()

    def delete(self, row_id):
        self.db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))

    def close(self):
        self.db.close()


class Gateway:
    def __init__(self, spec: TrailerSpec, settings: Settings):
        self.spec = spec
        self.settings = settings
        self.seq = 0
        self.boot_id = uuid.uuid4().hex[:8]   # seq restarts at 1 on every reboot
        self.module_samples = []
        self.trailer_samples = []
        tid = spec.trailer_id
        self.t_telemetry = f"fleet/{tid}/telemetry"
        self.t_status = f"fleet/{tid}/status"
        self.t_alarms = f"fleet/{tid}/alarms"

        os.makedirs(settings.outbox_dir, exist_ok=True)
        self.outbox = Outbox(os.path.join(settings.outbox_dir, f"{tid}.sqlite"))
        self.inflight = {}                 # MQTT message id -> outbox row id (main thread only)
        self.acked = collections.deque()   # message ids the broker acked (filled by paho thread)
        self.connected = False
        self.reconnected = False
        self.outage_until = None
        self.backlogged = False
        self.last_progress_log = 0.0

        leftover = self.outbox.count()
        if leftover:
            log.info("%s booted with %d messages still on disk from before", tid, leftover)

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"gw-{tid}", protocol=mqtt.MQTTv5)
        # Last Will: if this gateway vanishes without a goodbye, the BROKER
        # publishes "offline" for it (retained, so late subscribers see it too).
        self.client.will_set(self.t_status,
                             json.dumps({"state": "offline", "reason": "connection_lost"}),
                             qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.connect_async(settings.mqtt_host, settings.mqtt_port,
                                  keepalive=settings.keepalive_s)
        self.client.loop_start()   # background network thread; handles reconnects

    # ---- paho callbacks (run on paho's thread: keep them tiny) ----------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("%s connect failed: %s", self.spec.trailer_id, reason_code)
            return
        self.connected = True
        self.reconnected = True
        # Status is the "live lane" in its purest form: sent directly, not queued.
        client.publish(self.t_status, json.dumps({
            "state": "online", "ts_ms": now_ms(),
            "site_id": self.spec.site.site_id, "n_modules": self.spec.n_modules,
        }), qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        log.warning("%s disconnected: %s", self.spec.trailer_id, reason_code)

    def _on_publish(self, client, userdata, mid, reason_code, properties):
        self.acked.append(mid)

    # ---- producing messages (all go to disk first) ----------------------------
    def add(self, module_samples, trailer_sample):
        sig = trailer_sample["signals"]
        sig["outbox_backlog"] = self.outbox.count()
        if self.outage_until:
            sig["cell_signal_dbm"] = NO_SIGNAL_DBM
        self.module_samples.extend(module_samples)
        self.trailer_samples.append(trailer_sample)

    def flush(self):
        """Bundle everything since the last flush and queue it on disk."""
        if not self.trailer_samples:
            return 0
        self.seq += 1
        bundle = {
            "schema": SCHEMA,
            "trailer_id": self.spec.trailer_id,
            "site_id": self.spec.site.site_id,
            "boot_id": self.boot_id,
            "seq": self.seq,            # per-trailer counter: lets the cloud spot gaps/dupes
            "sent_at_ms": now_ms(),     # when the bundle was made; each sample keeps its own ts_ms
            "module_samples": self.module_samples,
            "trailer_samples": self.trailer_samples,
        }
        n = len(self.module_samples)
        self.module_samples, self.trailer_samples = [], []
        self._queue(self.t_telemetry, json.dumps(bundle, separators=(",", ":")))
        return n

    def publish_event(self, event):
        self._queue(self.t_alarms, json.dumps(dict(event, trailer_id=self.spec.trailer_id)))

    def _queue(self, topic, payload):
        dropped = self.outbox.put(topic, payload, now_ms())
        if dropped:
            log.warning("%s outbox full: dropped %d oldest messages", self.spec.trailer_id, dropped)

    # ---- the sender: called once per second from the main loop ----------------
    def pump(self):
        if self.outage_until and time.time() >= self.outage_until:
            self._end_outage()

        while self.acked:                       # broker confirmed these: safe to delete
            row_id = self.inflight.pop(self.acked.popleft(), None)
            if row_id is not None:
                self.outbox.delete(row_id)
        if self.reconnected:                    # new connection: resend anything unacked
            self.reconnected = False
            self.inflight.clear()

        if not self.connected or self.outage_until:
            return
        budget = MAX_INFLIGHT - len(self.inflight)
        if budget <= 0:
            return
        busy = set(self.inflight.values())
        picks = []
        for row in self.outbox.newest(now_ms() - LIVE_WINDOW_MS, budget):   # live lane first
            if row[0] not in busy:
                picks.append(row)
                busy.add(row[0])
        backfill = 0
        for row in self.outbox.oldest(budget + len(busy)):                  # then history
            if len(picks) >= budget or backfill >= self.settings.backfill_per_tick:
                break
            if row[0] not in busy:
                picks.append(row)
                busy.add(row[0])
                backfill += 1
        for row_id, topic, payload in picks:
            info = self.client.publish(topic, payload, qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                break
            self.inflight[info.mid] = row_id
        self._log_backlog()

    def _log_backlog(self):
        backlog = self.outbox.count()
        if backlog > 3:
            self.backlogged = True
            if time.time() - self.last_progress_log >= 5:
                self.last_progress_log = time.time()
                log.info("%s backfilling: %d messages still queued on disk", self.spec.trailer_id, backlog)
        elif self.backlogged:
            self.backlogged = False
            log.info("%s backlog drained: history is fully caught up", self.spec.trailer_id)

    # ---- fault injection -------------------------------------------------------
    def start_outage(self, seconds):
        """Simulate losing cell signal: the connection just dies, no goodbye."""
        if self.outage_until:
            self.outage_until = max(self.outage_until, time.time() + seconds)
            return
        self.outage_until = time.time() + seconds
        self.connected = False
        self.client.loop_stop()            # stop paho's auto-reconnect
        sock = self.client.socket()
        if sock:
            try:
                sock.close()               # abrupt close: broker fires our Last Will
            except OSError:
                pass
        log.warning("%s CELL SIGNAL LOST for %d s: queueing to disk", self.spec.trailer_id, seconds)

    def _end_outage(self):
        self.outage_until = None
        log.info("%s cell signal back: %d messages waiting on disk",
                 self.spec.trailer_id, self.outbox.count())
        try:
            self.client.reconnect()
        except OSError as e:
            log.warning("%s reconnect failed (%s); will keep retrying", self.spec.trailer_id, e)
        self.client.loop_start()

    def shutdown(self):
        """Clean goodbye. Anything unsent stays on disk for the next boot."""
        if not self.outage_until:
            info = self.client.publish(self.t_status, json.dumps(
                {"state": "offline", "reason": "shutdown", "ts_ms": now_ms()}), qos=1, retain=True)
            try:
                info.wait_for_publish(timeout=3)
            except Exception:  # not connected; nothing more we can do
                pass
            self.client.disconnect()
        self.client.loop_stop()
        left = self.outbox.count()
        if left:
            log.info("%s shutting down with %d messages kept on disk", self.spec.trailer_id, left)
        self.outbox.close()
