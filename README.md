# fleet-telemetry-sim

A small, working model of how a fleet of modular generators could stream
telemetry to the cloud and receive remote commands — built to learn
device-to-cloud data engineering by doing.

Simulated trailers each hold several 20 kW power modules. Each module reports
~50 signals per second over **MQTT**; data lands in **TimescaleDB** and shows
up in **Grafana**. The interesting parts are the failure modes: cell dropouts
(store-and-forward), duplicate deliveries (idempotent writes), late data
(event time vs. arrival time), and a remote-command path where **safety lives
on the device**.

All design choices are my own guesses about how such a system could work.

## Quick start

Requires Docker Desktop.

```
docker compose up -d --build
```

Then open http://localhost:3300 for the Grafana dashboard.

## Status

| Milestone | What | Status |
|---|---|---|
| M0 | MQTT broker + by-hand exercises | done |
| M1 | Simulator + gateway publishing | done |
| M2 | Ingest → TimescaleDB → Grafana | done |
| M3 | Store-and-forward + dropout / duplicate / late-data failures | done |
| M4 | Command path: desired/reported, safety, idempotency, expiry | done |

Deliberately deferred work is tracked, with triggers, in
[docs/decisions.md](docs/decisions.md).

## Docs

- [docs/m0-mqtt-by-hand.md](docs/m0-mqtt-by-hand.md) — learn MQTT with nothing but the broker
- [docs/architecture.md](docs/architecture.md) — diagrams: data flow, store-and-forward, commands, inside a trailer
- [docs/m1-simulator.md](docs/m1-simulator.md) — the simulated fleet and its message contract
- [docs/m2-storage-and-dashboards.md](docs/m2-storage-and-dashboards.md) — ingest, TimescaleDB, Grafana
- [docs/m3-store-and-forward.md](docs/m3-store-and-forward.md) — cut a trailer's cell signal and watch nothing get lost
- [docs/m4-commands.md](docs/m4-commands.md) — remote commands: the cloud asks, the trailer decides
- [docs/decisions.md](docs/decisions.md) — decisions and deferred work
