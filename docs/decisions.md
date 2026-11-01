# Decisions & deferred work

## Quick decisions (cheap to reverse)

| ID | Decision | Why | Revisit if |
|---|---|---|---|
| D-001 | Python for simulator and services | Fastest for me; matches typical hardware-team tooling | Throughput becomes the bottleneck |
| D-002 | Mosquitto as the broker | Standard MQTT, one container. Swapping to AWS IoT Core is config + certs | We want Device Shadow / Jobs for real |
| D-003 | TimescaleDB | It's Postgres; hypertables automate time partitioning | Write volume outgrows one node |
| D-004 | Grafana for dashboards | Fast, standard for engineering views | We need a customer-facing UI |
| D-005 | Narrow telemetry table (one row per signal) | Flexible while signals are still changing | Storage/query cost matters → wide table |
| D-006 | Docker Compose for everything | One command to run; hands-on container practice | — |

## Deferred on purpose

Each entry: what we skipped, what could go wrong, and what ends the deferral.

| ID | Deferred | Risk while deferred | Trigger to do it |
|---|---|---|---|
| D-101 | TLS + per-device certificates on the broker | Anyone on the network can publish/subscribe (local only, so acceptable) | Any deployment beyond localhost |
| D-102 | Failures 4–8 (module overtemp/derate, stuck sensor, slow drift, fuel theft, wet stacking) | Can describe but not demo them | Core M0–M4 done with time left |
| D-103 | Fuel-savings report + SPC drift alarm (old M5) | Analytics story is talk-only | After M4, or if the role goes forward |
| D-104 | Grafana geomap + alerting rules | Dashboard is less impressive | After M2 if time allows |
| D-105 | AWS IoT Core + Device Shadow version | Can't show the managed-service path | Learning goal for DVA-C02 / after the interview |
| D-106 | React command UI (CLI only for now) | Less visual demo | After M4 if time allows |
| D-107 | M4 command path (desired/reported, safety check, idempotency, expiry) | Talk-only; can't demo the safety story | M3 done with time left before the demo |
| D-108 | Unit tests for the physics model (checked by hand with a 24 h dry run) | Tuning changes could silently break the data shape | Before any further physics changes |
| D-109 | JSON payloads (verbose; ~10x bigger than needed) | Cellular data cost in the real world | Bandwidth matters → Protobuf or CBOR with the same schema version |
| D-110 | Battery modules not simulated; surge shown only as `boost_active` | Hybrid story is talk-only | If the demo needs the battery angle |
| D-111 | Gateway buffers in memory while offline (paho's queue) | A gateway crash loses buffered data | M3 (store-and-forward to disk) |
| D-112 | 23 signals per module, not ~50 | Smaller than a real controller | Never, for a demo; it's a knob |
| D-113 | Cloud deployment (demo runs on a laptop) | No shareable live link | After the interview: one EC2 box running this same Compose file (~1 h). The serverless rewrite (IoT Core → Lambda) is D-105. |
