"""Fleet Ops web API (FastAPI). Serves the React UI and a small JSON API.

    GET  /api/config                     where Grafana lives, etc.
    GET  /api/trailers                   live status + desired vs. reported
    POST /api/trailers/{id}/commands     {"action": "stop"|"start"}  -> pending command
    GET  /api/commands/{id}              poll for the trailer's answer
    GET  /api/commands                   recent commands
    POST /api/trailers/{id}/outage       SIMULATOR ONLY: cut that trailer's cell signal

Interactive docs: /docs (FastAPI generates them from this file).
LOCAL DEMO: no login, no roles (D-119).
"""
import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from .commands import STATUS_SQL, create_command, simulate_outage
from .main import DB_DSN

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3300")
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))

app = FastAPI(title="Fleet Ops API", version="0.1",
              description="Simulated modular-generator fleet: status and remote commands.")


def db():
    return psycopg.connect(DB_DSN, row_factory=dict_row)


class CommandIn(BaseModel):
    action: str = Field(pattern="^(stop|start)$")
    expires_s: int = Field(default=60, ge=5, le=3600)


class OutageIn(BaseModel):
    seconds: int = Field(default=60, ge=5, le=3600)


@app.get("/api/config")
def config():
    return {"grafana_url": GRAFANA_URL}


@app.get("/api/trailers")
def trailers():
    with db() as conn:
        return conn.execute("""
            SELECT s.trailer_id, s.site_id, s.state, s.reason AS state_reason, s.n_modules,
                   r.active_modules, r.total_load_kw, r.fuel_tank_pct, r.outbox_backlog,
                   extract(epoch FROM now() - r.time) AS last_seen_s,
                   d.desired_mode, d.reported_mode,
                   (d.desired_mode IS NULL OR d.desired_mode = d.reported_mode) AS converged,
                   c.command_id AS last_command_id, c.action AS last_action,
                   CASE WHEN c.status = 'pending' AND now() > c.expires_at THEN 'expired' ELSE c.status END
                       AS last_status,
                   c.reason AS last_reason
            FROM trailer_status s
            LEFT JOIN LATERAL (SELECT * FROM trailer_readings t WHERE t.trailer_id = s.trailer_id
                               ORDER BY time DESC LIMIT 1) r ON true
            LEFT JOIN device_state d ON d.trailer_id = s.trailer_id
            LEFT JOIN LATERAL (SELECT * FROM commands x WHERE x.trailer_id = s.trailer_id
                               ORDER BY created_at DESC LIMIT 1) c ON true
            ORDER BY s.trailer_id""").fetchall()


@app.post("/api/trailers/{trailer_id}/commands", status_code=202)
def send_command(trailer_id: str, body: CommandIn):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM trailer_status WHERE trailer_id = %s", (trailer_id,)).fetchone():
            raise HTTPException(404, f"no trailer {trailer_id}")
        command_id, expires_at = create_command(conn, trailer_id, body.action, "web-ui", body.expires_s)
    # 202 Accepted = "we sent your request"; the trailer's decision comes later.
    return {"command_id": str(command_id), "status": "pending", "expires_at": expires_at.isoformat()}


@app.get("/api/commands/{command_id}")
def get_command(command_id: str):
    with db() as conn:
        row = conn.execute(f"""SELECT command_id::text, trailer_id, action, {STATUS_SQL} AS status, reason,
                                      ack_count, created_at, acked_at, expires_at
                               FROM commands WHERE command_id::text = %s""", (command_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such command")
    return row


@app.get("/api/commands")
def recent_commands(limit: int = 15):
    with db() as conn:
        return conn.execute(f"""SELECT command_id::text, trailer_id, action, {STATUS_SQL} AS status, reason,
                                       ack_count, requested_by, created_at
                                FROM commands ORDER BY created_at DESC LIMIT %s""", (min(limit, 100),)).fetchall()


@app.post("/api/trailers/{trailer_id}/outage", status_code=202)
def outage(trailer_id: str, body: OutageIn):
    simulate_outage(trailer_id, body.seconds)
    return {"trailer_id": trailer_id, "seconds": body.seconds, "note": "simulator control, not a real command"}


# The built React app. Mounted last so /api/* and /docs win.
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
