# M2 — Storage and dashboards

```
broker ──► ingest worker ──► TimescaleDB ──► Grafana (http://localhost:3300)
```

## The three rules in the ingest worker
1. **Ack only after commit.** MQTT manual acks: the broker hears "got it" only
   once rows are committed. A crash in between means the broker redelivers.
2. **Persistent session.** Same client id + `clean_start=False`: while the
   worker is down, the broker holds its messages (up to 1 h).
3. **Idempotent writes.** Redelivery creates duplicates; every insert is
   `ON CONFLICT DO NOTHING` on a key that includes the *measured* time.

## TimescaleDB in one paragraph
It's Postgres plus an extension. A **hypertable** looks like one table but is
automatically split into time chunks (here, 1 hour each), which is the
partitioning you'd otherwise do by hand. A **continuous aggregate**
(`module_1m`) is a 1-minute rollup that Timescale keeps up to date, so
dashboards over weeks don't scan billions of raw rows. **Compression** and
**retention** policies shrink, then drop, old raw data.

## Try it

**Write**: builds and starts everything (broker, sim, database, ingest, Grafana):

```
docker compose up -d --build
```

**Read-only**: the ingest log ("N new rows, 0 duplicates ignored"):

```
docker compose logs -f ingest
```

Open **http://localhost:3300**. It loads the "Fleet overview" dashboard
without a login (read-only viewer). To edit panels, sign in as `admin` / `admin`.
Use the **Trailer** dropdown at the top. TR-001 (construction, 10 modules) is
the most interesting.

## Poke at the database

**Read-only**: opens a SQL prompt inside the database container (`\q` to quit):

```
docker compose exec timescaledb psql -U fleet -d fleet
```

Queries to try at that prompt (all read-only):

```sql
-- rows per second the fleet is producing
SELECT count(*) / 60.0 AS rows_per_s FROM module_readings WHERE time > now() - interval '1 minute';

-- the hypertable's automatic time chunks
SELECT chunk_name, range_start, range_end FROM timescaledb_information.chunks
WHERE hypertable_name = 'module_readings' ORDER BY range_start DESC LIMIT 5;

-- the 1-minute rollup for one module
SELECT * FROM module_1m WHERE module_id = 'TR-001-M01' ORDER BY bucket DESC LIMIT 5;

-- measured time vs. arrival time
SELECT time, received_at, received_at - time AS delay
FROM trailer_readings WHERE trailer_id = 'TR-001' ORDER BY time DESC LIMIT 5;
```

## See "ack after commit" + persistent session work

**Write**: stops the ingest worker (trailers keep publishing):

```
docker compose stop ingest
```

Wait about a minute: the dashboard's newest data stops advancing.

**Write**: starts it again:

```
docker compose start ingest
```

✅ The log says `session resumed: True`, then a burst of rows, and the gap on
the dashboard fills in. The **broker** held the messages for the worker.
