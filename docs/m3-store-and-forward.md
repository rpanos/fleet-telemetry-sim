# M3 — Store-and-forward

## Why the dashboard worked before M3
Store-and-forward only matters **when the connection breaks**. On a laptop
the "cell link" (Docker's network) never breaks, so before M3 every bundle went
straight through: gateway → broker → ingest → database. Store-and-forward is
the trailer's safety net for the moment that path is cut.

Analogy: normally you hand letters straight to the mail carrier. Store-and-
forward is the outgoing mail tray on your desk. When the carrier doesn't come,
letters stack up in the tray, and when they return, they take the whole stack.

## How it works

See diagram 2 in [architecture.md](architecture.md).

1. **Disk first.** Every bundle and alarm is written to a SQLite file on the
   gateway (`/data/TR-xxx.sqlite`) *before* any attempt to send it.
2. **Delete only on ack.** A row is removed only after the broker's PUBACK.
   No ack → it stays → it's sent again later.
3. **Two lanes after reconnect.**
   - *Live lane:* anything from the last 15 s goes first, so the dashboard
     shows "now" immediately.
   - *Backfill lane:* older history, oldest first, capped at 2 messages/s
     (`BACKFILL_PER_TICK`), like a slow cellular link.
4. **Event time is preserved.** Each reading keeps the time it was measured,
   so a reading taken at 3:00 and delivered at 3:05 is stored at 3:00.
5. **Duplicates are harmless.** A lost ack means a resend, and the database's
   primary key turns the duplicate into a no-op.

## Try it (≈ 5 minutes)

**Write**: rebuilds the simulator and ingest worker with M3:

```
docker compose up -d --build
```

Open http://localhost:3300 and pick **TR-001** in the Trailer dropdown.
Scroll to *Gateway backlog* and *Cell signal*.

**Read-only**, in a second tab: watch the simulator narrate:

```
docker compose logs -f sim
```

### 1. Cut TR-001's cell signal for 2 minutes

**Write**: sends a fault-injection command to the simulator:

```
docker compose exec broker mosquitto_pub -h localhost -t sim/control -m '{"action":"outage","trailer_id":"TR-001","seconds":120}'
```

Watch:
- Sim log: `TR-001 CELL SIGNAL LOST for 120 s: queueing to disk`.
- Dashboard: within a few seconds TR-001 turns **red / offline** in Fleet
  status, and an `OFFLINE … connection_lost` warning appears in Alarms. That
  was the broker firing TR-001's **Last Will**.
- TR-001's charts stop advancing. Its readings are piling up on its disk.

### 2. Signal returns (after 120 s)

- Sim log: `cell signal back: 12 messages waiting on disk`, then
  `backfilling: …`, then `backlog drained`.
- Dashboard: TR-001 goes back to **online** right away (live lane first).
- Over the next ~10 s the **gap fills in**. The load, RPM and fuel charts have
  no hole, because the readings landed at their measured time.
- **Data delay** spikes to ~120 s for those readings: arrival time minus
  measured time, made visible.
- **Cell signal** shows -125 dBm for the outage window. Those readings were
  taken *during* the outage and only arrived afterwards.
- **Gateway backlog** shows the queue climbing during the outage, then draining.

### 3. Bonus: reboot during an outage (the disk is the point)

Start another outage, then within 2 minutes restart the simulator.

**Write**: another 2-minute outage:

```
docker compose exec broker mosquitto_pub -h localhost -t sim/control -m '{"action":"outage","trailer_id":"TR-001","seconds":120}'
```

**Write**: reboots every gateway (the queue files are on a Docker volume):

```
docker compose restart sim
```

✅ Sim log: `TR-001 booted with N messages still on disk from before`, then
they're delivered. An in-memory queue would have lost them.

### 4. Whole-fleet outage (a regional cell outage)

**Write**: every trailer loses signal for 60 s:

```
docker compose exec broker mosquitto_pub -h localhost -t sim/control -m '{"action":"outage","trailer_id":"*","seconds":60}'
```

## What to say about it

> "Every reading hits the gateway's disk first and is deleted only after the
> broker acknowledges it. After an outage, current status goes first and
> history backfills behind it at whatever the link allows. Readings keep their
> measured timestamps, so history lands in the right place, and because
> delivery is at-least-once, the database treats duplicates as no-ops."
