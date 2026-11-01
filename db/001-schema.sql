-- TimescaleDB schema for the fleet simulator.
-- Runs once, the first time the database container starts with an empty volume.
-- To re-run it: docker compose down -v (deletes the data!) then up again.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- WIDE tables: one row per module per second, one column per signal.
-- (A narrow table, one row per signal, would be 66 modules x 23 signals =
--  ~1,500 rows/s = ~130M rows/day on a laptop. Wide is ~66 rows/s. See D-005.)
CREATE TABLE module_readings (
    time             timestamptz NOT NULL,   -- EVENT time: when it was measured
    trailer_id       text        NOT NULL,
    module_id        text        NOT NULL,
    running          smallint, rpm real, load_kw real, load_pct real, boost_active smallint,
    fuel_rate_gph    real, coolant_temp_c real, exhaust_temp_c real, inverter_temp_c real,
    oil_pressure_kpa real, engine_hours double precision, start_count integer,
    frequency_hz     real, power_factor real,
    v_l1 real, v_l2 real, v_l3 real,
    p_l1_kw real, p_l2_kw real, p_l3_kw real,
    i_l1_a real, i_l2_a real, i_l3_a real,
    received_at      timestamptz NOT NULL DEFAULT now(),   -- ARRIVAL time
    PRIMARY KEY (module_id, time)            -- same reading twice = no-op (idempotent)
);
SELECT create_hypertable('module_readings', by_range('time', INTERVAL '1 hour'));

CREATE TABLE trailer_readings (
    time            timestamptz NOT NULL,
    trailer_id      text        NOT NULL,
    total_load_kw   real, active_modules smallint, fuel_tank_pct real, fuel_tank_gal real,
    ambient_temp_c  real, lat double precision, lon double precision,
    cell_signal_dbm real, door_open smallint,
    outbox_backlog  integer,                 -- messages waiting on the gateway's disk
    received_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trailer_id, time)
);
SELECT create_hypertable('trailer_readings', by_range('time', INTERVAL '1 hour'));

CREATE TABLE events (
    time        timestamptz NOT NULL,
    trailer_id  text NOT NULL,
    module_id   text NOT NULL DEFAULT '',
    severity    text NOT NULL,                -- info / warning / critical
    code        text NOT NULL,
    detail      text,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trailer_id, module_id, code, time)
);
SELECT create_hypertable('events', by_range('time', INTERVAL '1 day'));

-- Latest known status per trailer (from the retained status topic + Last Will).
CREATE TABLE trailer_status (
    trailer_id text PRIMARY KEY,
    state      text NOT NULL,                 -- online / offline
    reason     text,
    site_id    text,
    n_modules  smallint,
    updated_at timestamptz NOT NULL
);

-- One row per bundle received: lets us see gaps and duplicates per gateway boot.
CREATE TABLE bundles (
    trailer_id       text NOT NULL,
    boot_id          text NOT NULL,           -- seq restarts when a gateway reboots
    seq              bigint NOT NULL,
    sent_at          timestamptz NOT NULL,
    received_at      timestamptz NOT NULL DEFAULT now(),
    n_rows           integer NOT NULL,
    n_new_rows       integer NOT NULL,
    PRIMARY KEY (trailer_id, boot_id, seq)
);

-- ROLLUP: a 1-minute summary that Timescale keeps up to date automatically.
-- start_offset = 1 day means data arriving up to a day late still gets rolled up (M3).
CREATE MATERIALIZED VIEW module_1m WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', time) AS bucket, trailer_id, module_id,
       avg(load_kw)        AS load_kw,
       avg(rpm)            AS rpm,
       avg(fuel_rate_gph)  AS fuel_rate_gph,
       max(exhaust_temp_c) AS exhaust_max_c,
       max(running)        AS running
FROM module_readings
GROUP BY 1, 2, 3
WITH NO DATA;
ALTER MATERIALIZED VIEW module_1m SET (timescaledb.materialized_only = false);
SELECT add_continuous_aggregate_policy('module_1m',
    start_offset => INTERVAL '1 day', end_offset => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute');

-- Keep a laptop's disk happy: compress after a day, drop raw data after a week.
ALTER TABLE module_readings SET (timescaledb.compress, timescaledb.compress_segmentby = 'module_id');
SELECT add_compression_policy('module_readings', INTERVAL '1 day');
SELECT add_retention_policy('module_readings', INTERVAL '7 days');
SELECT add_retention_policy('trailer_readings', INTERVAL '7 days');

-- Grafana gets a READ-ONLY login. Dashboards can never change data.
CREATE ROLE grafana_ro LOGIN PASSWORD 'grafana_ro';
GRANT CONNECT ON DATABASE fleet TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
