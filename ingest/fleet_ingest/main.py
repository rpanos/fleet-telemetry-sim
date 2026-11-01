"""Ingest worker: subscribe to the broker, write to TimescaleDB.

Three rules that matter more than the code:

1. ACK ONLY AFTER COMMIT. We use MQTT manual acks: the broker only hears
   "got it" after the rows are safely committed. If we crash in between,
   the broker redelivers the message when we reconnect.
2. PERSISTENT SESSION. We reconnect with the same client id and
   clean_start=False, so the broker holds QoS 1 messages while we're down
   (for up to an hour).
3. IDEMPOTENT WRITES. Redelivery means duplicates. Every insert is
   ON CONFLICT DO NOTHING on a key that includes the MEASURED time, so a
   duplicate changes nothing.

Crash-only design: on a database error we exit; Docker restarts us, and
rules 1-3 make that safe.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import psycopg
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("ingest")

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DB_DSN = os.environ.get("DB_DSN", "postgresql://fleet:fleet@localhost:5432/fleet")
CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "ingest-1")

MODULE_SIGNALS = [
    "running", "rpm", "load_kw", "load_pct", "boost_active", "fuel_rate_gph",
    "coolant_temp_c", "exhaust_temp_c", "inverter_temp_c", "oil_pressure_kpa",
    "engine_hours", "start_count", "frequency_hz", "power_factor",
    "v_l1", "v_l2", "v_l3", "p_l1_kw", "p_l2_kw", "p_l3_kw", "i_l1_a", "i_l2_a", "i_l3_a",
]
TRAILER_SIGNALS = [
    "total_load_kw", "active_modules", "fuel_tank_pct", "fuel_tank_gal",
    "ambient_temp_c", "lat", "lon", "cell_signal_dbm", "door_open",
]


def _insert_sql(table, keys, signals, conflict):
    cols = keys + signals
    return (f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT ({', '.join(conflict)}) DO NOTHING")


MODULE_SQL = _insert_sql("module_readings", ["time", "trailer_id", "module_id"],
                         MODULE_SIGNALS, ["module_id", "time"])
TRAILER_SQL = _insert_sql("trailer_readings", ["time", "trailer_id"],
                          TRAILER_SIGNALS, ["trailer_id", "time"])


def ts(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


class BadMessage(Exception):
    """A message we can never process (wrong schema, broken JSON)."""


class Ingest:
    def __init__(self, db):
        self.db = db
        self.window = {"bundles": 0, "new": 0, "dup": 0, "max_delay": 0.0}
        self.window_started = time.monotonic()

    def handle(self, topic, payload):
        parts = topic.split("/")
        if len(parts) != 3 or parts[0] != "fleet":
            raise BadMessage(f"unexpected topic {topic}")
        trailer_id, kind = parts[1], parts[2]
        if not payload:            # a cleared retained message
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            raise BadMessage(f"bad JSON on {topic}: {e}") from e

        with self.db.cursor() as cur:
            if kind == "telemetry":
                self._telemetry(cur, trailer_id, data)
            elif kind == "alarms":
                self._event(cur, trailer_id, data)
            elif kind == "status":
                self._status(cur, trailer_id, data)
        self.db.commit()
        self._maybe_log()

    def _telemetry(self, cur, trailer_id, b):
        if b.get("schema") != "telemetry.v1":
            raise BadMessage(f"{trailer_id}: unknown schema {b.get('schema')!r}")
        mrows = [(ts(s["ts_ms"]), trailer_id, s["module_id"],
                  *(s["signals"].get(k) for k in MODULE_SIGNALS))
                 for s in b["module_samples"]]
        trows = [(ts(s["ts_ms"]), trailer_id, *(s["signals"].get(k) for k in TRAILER_SIGNALS))
                 for s in b["trailer_samples"]]
        new = 0
        if mrows:
            cur.executemany(MODULE_SQL, mrows)
            new += max(cur.rowcount, 0)
        if trows:
            cur.executemany(TRAILER_SQL, trows)
            new += max(cur.rowcount, 0)
        total = len(mrows) + len(trows)
        cur.execute(
            "INSERT INTO bundles (trailer_id, boot_id, seq, sent_at, n_rows, n_new_rows) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (trailer_id, b.get("boot_id", "unknown"), b["seq"], ts(b["sent_at_ms"]), total, new))
        w = self.window
        w["bundles"] += 1
        w["new"] += new
        w["dup"] += total - new
        w["max_delay"] = max(w["max_delay"], time.time() - b["sent_at_ms"] / 1000)

    def _event(self, cur, trailer_id, e):
        cur.execute(
            "INSERT INTO events (time, trailer_id, module_id, severity, code, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (ts(e["ts_ms"]), trailer_id, e.get("module_id") or "", e["severity"],
             e["code"], e.get("detail", "")))

    def _status(self, cur, trailer_id, d):
        state = d.get("state", "unknown")
        when = ts(d["ts_ms"]) if "ts_ms" in d else datetime.now(timezone.utc)
        cur.execute("SELECT state FROM trailer_status WHERE trailer_id = %s", (trailer_id,))
        row = cur.fetchone()
        cur.execute(
            "INSERT INTO trailer_status (trailer_id, state, reason, site_id, n_modules, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (trailer_id) DO UPDATE SET state = EXCLUDED.state, reason = EXCLUDED.reason, "
            "site_id = COALESCE(EXCLUDED.site_id, trailer_status.site_id), "
            "n_modules = COALESCE(EXCLUDED.n_modules, trailer_status.n_modules), "
            "updated_at = EXCLUDED.updated_at",
            (trailer_id, state, d.get("reason"), d.get("site_id"), d.get("n_modules"), when))
        if row is None or row[0] != state:   # only log real changes as events
            severity = "warning" if d.get("reason") == "connection_lost" else "info"
            code = "OFFLINE" if state == "offline" else "ONLINE"
            self._event(cur, trailer_id, {"ts_ms": int(when.timestamp() * 1000),
                                          "severity": severity, "code": code,
                                          "detail": d.get("reason") or ""})

    def _maybe_log(self):
        if time.monotonic() - self.window_started < 10:
            return
        w = self.window
        log.info("last 10 s: %d bundles, %d new rows, %d duplicates ignored, max delay %.1f s",
                 w["bundles"], w["new"], w["dup"], w["max_delay"])
        self.window = {"bundles": 0, "new": 0, "dup": 0, "max_delay": 0.0}
        self.window_started = time.monotonic()


def connect_db():
    for attempt in range(60):
        try:
            return psycopg.connect(DB_DSN)
        except psycopg.OperationalError as e:
            log.info("database not ready (%s), retrying...", str(e).strip().splitlines()[0])
            time.sleep(2)
    raise SystemExit("database never became ready")


def main():
    ingest = Ingest(connect_db())
    log.info("connected to database")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID,
                         protocol=mqtt.MQTTv5, manual_ack=True)

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("broker refused connection: %s", reason_code)
            return
        log.info("connected to broker (session resumed: %s)", flags.session_present)
        c.subscribe([("fleet/+/telemetry", 1), ("fleet/+/alarms", 1), ("fleet/+/status", 1)])

    def on_message(c, userdata, msg):
        try:
            ingest.handle(msg.topic, msg.payload)
        except BadMessage as e:
            # Poison message: it will never succeed, so ack it and move on.
            # Production would park it in a dead-letter table for a human.
            log.error("dropping bad message: %s", e)
            ingest.db.rollback()
        # Any other exception (e.g. database down) propagates: we exit WITHOUT
        # acking, Docker restarts us, and the broker redelivers.
        c.ack(msg.mid, msg.qos)

    client.on_connect = on_connect
    client.on_message = on_message

    props = Properties(PacketTypes.CONNECT)
    props.SessionExpiryInterval = 3600   # broker keeps our queue for up to 1 h

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=30, clean_start=False, properties=props)
            break
        except OSError as e:
            log.info("broker not reachable (%s), retrying...", e)
            time.sleep(2)
    client.loop_forever()


if __name__ == "__main__":
    main()
