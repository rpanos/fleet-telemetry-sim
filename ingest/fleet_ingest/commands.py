"""The cloud side of the command path, shared by fleetctl (CLI) and the web API.

create_command() is the whole "command service" in one function:
  1. record the command as PENDING
  2. set the trailer's DESIRED mode
  3. publish it to the broker with an MQTT message expiry
The trailer's answer comes back separately (ingest main.py -> _cmd_ack).
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from .main import MQTT_HOST, MQTT_PORT

DESIRED = {"stop": "standby", "start": "running"}
STATUS_SQL = "CASE WHEN status = 'pending' AND now() > expires_at THEN 'expired' ELSE status END"


def _client(prefix):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"{prefix}-{uuid.uuid4().hex[:6]}", protocol=mqtt.MQTTv5)
    c.connect(MQTT_HOST, MQTT_PORT, 30)
    c.loop_start()
    return c


def publish(trailer_id, command_id, action, expires_at):
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    client = _client("cmd")
    props = Properties(PacketTypes.PUBLISH)
    # Broker-side expiry: an offline trailer's queued command is discarded once this passes.
    # (A --stale command is already past its own deadline; give the broker a few seconds
    #  so it reaches the trailer and we can watch the TRAILER's own expiry check reject it.)
    props.MessageExpiryInterval = max(remaining, 5)
    payload = json.dumps({"command_id": str(command_id), "action": action,
                          "expires_at_ms": int(expires_at.timestamp() * 1000)})
    info = client.publish(f"fleet/{trailer_id}/cmd", payload, qos=1, properties=props)
    info.wait_for_publish(timeout=5)
    client.disconnect()
    client.loop_stop()


def create_command(db, trailer_id, action, requested_by, expires_s=60, stale=False):
    """Record + set desired + publish. Returns (command_id, expires_at)."""
    if action not in DESIRED:
        raise ValueError(f"unknown action {action!r}; use one of: {', '.join(DESIRED)}")
    command_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(seconds=5) if stale else now + timedelta(seconds=expires_s)
    db.execute("INSERT INTO commands (command_id, trailer_id, action, requested_by, created_at, expires_at) "
               "VALUES (%s, %s, %s, %s, %s, %s)",
               (command_id, trailer_id, action, requested_by, now, expires_at))
    db.execute("INSERT INTO device_state (trailer_id, desired_mode, desired_at, desired_command) "
               "VALUES (%s, %s, %s, %s) ON CONFLICT (trailer_id) DO UPDATE SET "
               "desired_mode = EXCLUDED.desired_mode, desired_at = EXCLUDED.desired_at, "
               "desired_command = EXCLUDED.desired_command",
               (trailer_id, DESIRED[action], now, command_id))
    db.commit()              # recorded BEFORE we send: we never have a command we can't account for
    publish(trailer_id, command_id, action, expires_at)
    return command_id, expires_at


def simulate_outage(trailer_id, seconds):
    """Simulator-only: ask the fake trailer to drop its cell connection."""
    client = _client("simctl")
    info = client.publish("sim/control", json.dumps(
        {"action": "outage", "trailer_id": trailer_id, "seconds": int(seconds)}), qos=1)
    info.wait_for_publish(timeout=5)
    client.disconnect()
    client.loop_stop()
