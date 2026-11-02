# Decisions & deferred work

## Quick decisions (cheap to reverse)

| ID | Decision | Why | Revisit if |
|---|---|---|---|
| D-001 | Python for simulator and services | Fastest for me; matches typical hardware-team tooling | Throughput becomes the bottleneck |
| D-002 | Mosquitto as the broker | Standard MQTT, one container. Swapping to AWS IoT Core is config + certs | We want Device Shadow / Jobs for real |
| D-003 | TimescaleDB | It's Postgres; hypertables automate time partitioning | Write volume outgrows one node |
| D-004 | Grafana for dashboards | Fast, standard for engineering views | We need a customer-facing UI |
| D-005 | ~~Narrow table~~ → **wide tables** (one row per module per second) | Did the math in M2: narrow = ~1,500 rows/s, ~130M rows/day on a laptop; wide = ~66 rows/s | Signals change often → add columns, or a JSONB column for extras |
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
| D-106 | ~~React command UI~~ **Done in M5** (Fleet Ops, polling every 2 s) | — | — |
| D-107 | ~~M4 command path deferred~~ **Done in M4** (CLI `./fleetctl`, no web UI yet) | — | — |
| D-108 | Unit tests for the physics model (checked by hand with a 24 h dry run) | Tuning changes could silently break the data shape | Before any further physics changes |
| D-109 | JSON payloads (verbose; ~10x bigger than needed) | Cellular data cost in the real world | Bandwidth matters → Protobuf or CBOR with the same schema version |
| D-110 | Battery modules not simulated; surge shown only as `boost_active` | Hybrid story is talk-only | If the demo needs the battery angle |
| D-111 | ~~Gateway buffers in memory while offline~~ **Done in M3:** SQLite outbox on disk | — | — |
| D-112 | 23 signals per module, not ~50 | Smaller than a real controller | Never, for a demo; it's a knob |
| D-113 | ~~Cloud deployment~~ **Done:** one EC2 box (t4g.medium) running the same Compose file + Caddy (HTTPS, password) | — | Serverless rewrite (IoT Core → Lambda) is D-105 |
| D-114 | Throwaway passwords in docker-compose.yml; Grafana anonymous viewer | Fine on localhost only | Any deployment (D-113) → secrets manager + real logins |
| D-115 | Bad messages are logged and dropped, not parked in a dead-letter table | A firmware bug could silently lose data | Real devices → dead-letter table + alert |
| D-116 | Ingest writes one bundle per transaction (no cross-bundle batching) | Fine at 1.2 bundles/s; slow at fleet scale | >~100 bundles/s → batch or use COPY |
| D-117 | Outbox drops the oldest raw data when full (50k messages); no summarize-then-drop yet | A multi-week outage loses the oldest detail | Real devices → keep summaries, drop raw first |
| D-118 | No local-operator override for a rejected STOP | A real emergency stop must always work | Real hardware: physical/local E-stop path, never a cloud override |
| D-119 | Commands have no auth or roles (anyone with broker access can send) | Unacceptable beyond a laptop | Before any deployment: per-user roles (view vs. operate), signed commands, per-device ACLs |
| D-120 | Desired state is not cleared or retried after a rejection | UI shows "not converged" until someone acts | Product decision: auto-clear, retry policy, or human follow-up |
| D-121 | UI polls every 2 s instead of pushing updates (WebSocket/SSE) | Wasteful at scale; up to 2 s stale | Many users or a big fleet → server push |
| D-122 | API opens a DB connection per request; publishes via a fresh MQTT connection per command | Fine for a demo; slow under load | Real traffic → connection pool + one long-lived MQTT client |
| D-123 | EC2 demo: shared basic-auth password, SSH open to one IP, no backups, single box | Fine for a week-long demo | Anything longer → SSM Session Manager (no SSH port), real auth, backups, IaC (CDK/Terraform) |
