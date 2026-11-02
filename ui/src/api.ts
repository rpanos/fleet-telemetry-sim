// Typed client for the Fleet Ops API (ingest/fleet_ingest/api.py).

export type Mode = "running" | "standby";
export type CommandStatus = "pending" | "accepted" | "rejected" | "expired";

export interface Trailer {
  trailer_id: string;
  site_id: string | null;
  state: "online" | "offline" | string;
  state_reason: string | null;
  n_modules: number | null;
  active_modules: number | null;
  total_load_kw: number | null;
  fuel_tank_pct: number | null;
  outbox_backlog: number | null;
  last_seen_s: number | null;
  desired_mode: Mode | null;
  reported_mode: Mode | null;
  converged: boolean;
  last_command_id: string | null;
  last_action: string | null;
  last_status: CommandStatus | null;
  last_reason: string | null;
}

export interface Command {
  command_id: string;
  trailer_id: string;
  action: string;
  status: CommandStatus;
  reason: string | null;
  ack_count: number;
  requested_by?: string;
  created_at: string;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  config: () => call<{ grafana_url: string }>("/api/config"),
  trailers: () => call<Trailer[]>("/api/trailers"),
  commands: () => call<Command[]>("/api/commands?limit=12"),
  command: (id: string) => call<Command>(`/api/commands/${id}`),
  send: (trailerId: string, action: "stop" | "start") =>
    call<{ command_id: string }>(`/api/trailers/${trailerId}/commands`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  outage: (trailerId: string, seconds: number) =>
    call<unknown>(`/api/trailers/${trailerId}/outage`, {
      method: "POST",
      body: JSON.stringify({ seconds }),
    }),
};
