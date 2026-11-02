# M5 — Fleet Ops web UI

A small React app (TypeScript, Vite) served by a FastAPI API. The API reuses
exactly the same command code as `./fleetctl` (`ingest/fleet_ingest/commands.py`),
so the CLI and the UI behave identically.

```
browser ──HTTP──► api (FastAPI) ──► TimescaleDB   (read status; record command + DESIRED)
                       └──MQTT──► broker ──► trailer gateway ──► controller decides
browser ◄─polls── api ◄── TimescaleDB ◄── ingest ◄── broker ◄── trailer's answer + REPORTED
```

## Run it

**Write**: builds the UI and API image (first build downloads Node packages, 1-2 min) and starts it:

```
docker compose up -d --build
```

Open **http://localhost:8300**.

## Things to click
- **Stop on a busy trailer** (a mine, or the construction site by day): the
  card shows *Waiting for TR-003 to decide…*, then **TR-003 refused stop** with
  the trailer's own reason. Desired = standby, Reported = running →
  **not converged**.
- **Stop on a quiet trailer**: accepted, the module squares go grey, load drops
  to 0. Open Grafana to see the same thing in the charts.
- **Cut signal 60 s** (dashed, simulator-only): the card turns red,
  *signal lost*, and on reconnect a "messages queued on trailer" note appears
  while the backlog drains.
- **http://localhost:8300/docs**: FastAPI's interactive API docs. You can send
  a command from there too.

## Developing the UI with hot reload

**Write**: installs the UI's packages on your Mac (one time):

```
cd ui && npm install
```

**Read-only**: starts Vite on http://localhost:5173. It forwards `/api` to the running API container:

```
npm run dev
```
