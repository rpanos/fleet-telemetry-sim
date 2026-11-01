# M1 — The simulated fleet

12 trailers across 3 made-up sites, each holding 2, 4 or 10 of the 20 kW
modules. Every second each module produces 23 signals; every 10 seconds each
trailer's **gateway** publishes one bundle over MQTT.

| Site | Load shape |
|---|---|
| Construction (Scottsdale) | Ramp-up at 7 am, lunch dip, crane spikes, near-idle at night |
| Event (Tempe festival) | Light by day, heavy evening, quiet overnight |
| Mine (Globe) | Steady 24/7 |

## What the physics shows
- **Engine speed follows load** (variable RPM) instead of sitting at 1800.
- **Modules start and stop as load changes**, decided on a 2-minute moving
  average with hysteresis (start above 85% per-module load, stop when the
  rest could carry it under 70%), so modules run at an efficient load rather
  than all idling.
- **Crane spikes are covered by surge boost** (`boost_active = 1`), not by
  starting more modules. That's the "no oversizing" idea.
- Temperatures lag behind load, the tank drains, and a fuel truck refills it
  at 12%.

## Message contract (`telemetry.v1`)

Topic `fleet/{trailer_id}/telemetry`, QoS 1, every 10 s:

```json
{
  "schema": "telemetry.v1",
  "trailer_id": "TR-001",
  "site_id": "SITE-SCOTTSDALE-BLDG",
  "seq": 42,
  "sent_at_ms": 1762023610000,
  "module_samples":  [{"ts_ms": 1762023601000, "module_id": "TR-001-M01", "signals": {"rpm": 2950.1, "...": 0}}],
  "trailer_samples": [{"ts_ms": 1762023601000, "signals": {"fuel_tank_pct": 81.2, "...": 0}}]
}
```

- `schema` is versioned, because cloud and firmware teams must change it together.
- `seq` counts up per trailer, so the cloud can spot gaps and duplicates (M3).
- Every sample keeps its own **event time** (`ts_ms`, when it was measured).
  `sent_at_ms` is roughly the arrival time. They split apart during an outage (M3).

Other topics:
- `fleet/{id}/status`: retained `online` / `offline`. Last Will = `offline, connection_lost`; clean shutdown = `offline, shutdown`.
- `fleet/{id}/alarms`: severity-tagged events (`info` → `warning`): module started/stopped, exhaust high, low fuel, refueled.

## Try it

**Write**: builds the simulator image and starts it alongside the broker:

```
docker compose up -d --build
```

**Read-only**: the simulator's log (fleet roster, events, publish counts):

```
docker compose logs -f sim
```

**Read-only**: live status of every trailer (all 12 should say online):

```
docker compose exec broker mosquitto_sub -h localhost -t 'fleet/+/status' -v
```

**Read-only**: the alarm and event stream:

```
docker compose exec broker mosquitto_sub -h localhost -t 'fleet/+/alarms' -v
```

**Read-only**: grab one bundle and pretty-print the top of it:

```
docker compose exec -T broker mosquitto_sub -h localhost -t fleet/TR-002/telemetry -C 1 | python3 -m json.tool | head -60
```

### Last Will vs. clean shutdown

Keep the status subscriber running in another window.

**Write**: kills the simulator abruptly, like a trailer losing power:

```
docker compose kill sim
```

✅ All 12 trailers flip to `offline ... connection_lost`. The **broker** sent that, not the trailers.

**Write**: starts it again:

```
docker compose up -d sim
```

**Write**: stops it gracefully:

```
docker compose stop sim
```

✅ This time each trailer says `offline ... shutdown` itself, and the Last Will is not used.
