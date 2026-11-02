import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Command, type CommandStatus, type Trailer } from "./api";

const MODULE_KW = 20;
const SITE_LABELS: Record<string, string> = {
  "SITE-SCOTTSDALE-BLDG": "Scottsdale · construction",
  "SITE-TEMPE-FESTIVAL": "Tempe · festival",
  "SITE-GLOBE-MINE": "Globe · mine",
};

interface Outcome {
  action: string;
  status: CommandStatus;
  reason: string | null;
}

function usePoll<T>(fn: () => Promise<T>, ms: number): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const tick = useCallback(() => {
    fnRef.current()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => setError(String(e)));
  }, []);
  useEffect(() => {
    tick();
    const id = window.setInterval(tick, ms);
    return () => window.clearInterval(id);
  }, [tick, ms]);
  return [data, error, tick];
}

export function App() {
  const [trailers, trailersError, refreshTrailers] = usePoll(api.trailers, 2000);
  const [commands, , refreshCommands] = usePoll(api.commands, 2000);
  const [grafana, setGrafana] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<Record<string, string>>({}); // trailer -> action
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});

  useEffect(() => {
    api.config().then((c) => setGrafana(c.grafana_url)).catch(() => setGrafana(null));
  }, []);

  const send = async (t: Trailer, action: "stop" | "start") => {
    const id = t.trailer_id;
    setWaiting((w) => ({ ...w, [id]: action }));
    setOutcomes((o) => {
      const next = { ...o };
      delete next[id];
      return next;
    });
    try {
      const { command_id } = await api.send(id, action);
      refreshCommands();
      // Poll for the trailer's decision. The cloud asked; now the trailer decides.
      const deadline = Date.now() + 20_000;
      let cmd: Command | null = null;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 500));
        cmd = await api.command(command_id);
        if (cmd.status !== "pending") break;
      }
      setOutcomes((o) => ({
        ...o,
        [id]: cmd && cmd.status !== "pending"
          ? { action, status: cmd.status, reason: cmd.reason }
          : { action, status: "pending", reason: "No answer yet. The trailer may be offline; the command will expire if it can't be delivered in time." },
      }));
    } catch (e) {
      setOutcomes((o) => ({ ...o, [id]: { action, status: "rejected", reason: `Could not send: ${String(e)}` } }));
    } finally {
      setWaiting((w) => {
        const next = { ...w };
        delete next[id];
        return next;
      });
      refreshTrailers();
      refreshCommands();
    }
  };

  const cutSignal = async (t: Trailer) => {
    await api.outage(t.trailer_id, 60).catch(() => undefined);
    setTimeout(refreshTrailers, 1500);
  };

  const online = trailers?.filter((t) => t.state === "online").length ?? 0;
  const totalKw = trailers?.reduce((s, t) => s + (t.state === "online" ? t.total_load_kw ?? 0 : 0), 0) ?? 0;

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Fleet Ops</h1>
          <p className="sub">Simulated fleet of modular generators · the cloud asks, the trailer decides</p>
        </div>
        <div className="top-right">
          {trailers && (
            <div className="kpis">
              <span><b>{online}</b>/{trailers.length} online</span>
              <span><b>{Math.round(totalKw)}</b> kW delivered</span>
            </div>
          )}
          {grafana && (
            <a className="link" href={grafana} target="_blank" rel="noreferrer">Open Grafana ↗</a>
          )}
        </div>
      </header>

      {trailersError && <div className="banner error">API unreachable: {trailersError}</div>}
      {!trailers && !trailersError && <p className="muted">Loading fleet…</p>}

      <section className="grid">
        {trailers?.map((t) => (
          <TrailerCard
            key={t.trailer_id}
            t={t}
            waitingFor={waiting[t.trailer_id]}
            outcome={outcomes[t.trailer_id]}
            onSend={(a) => send(t, a)}
            onCutSignal={() => cutSignal(t)}
          />
        ))}
      </section>

      <section className="panel">
        <h2>Recent commands</h2>
        <table>
          <thead>
            <tr>
              <th>Sent</th><th>Trailer</th><th>Action</th><th>Result</th><th>Answers</th><th>Trailer's reason</th><th>By</th>
            </tr>
          </thead>
          <tbody>
            {commands?.map((c) => (
              <tr key={c.command_id}>
                <td className="mono">{new Date(c.created_at).toLocaleTimeString()}</td>
                <td className="mono">{c.trailer_id}</td>
                <td>{c.action}</td>
                <td><StatusChip status={c.status} /></td>
                <td className="mono">{c.ack_count}</td>
                <td className="reason">{c.reason ?? ""}</td>
                <td className="muted">{c.requested_by}</td>
              </tr>
            ))}
            {commands?.length === 0 && (
              <tr><td colSpan={7} className="muted">No commands yet. Try Stop on a busy trailer.</td></tr>
            )}
          </tbody>
        </table>
      </section>

      <footer className="muted">
        Local demo · no login (decision D-119) · “Cut signal” is a simulator control, not a real command.
      </footer>
    </div>
  );
}

function TrailerCard(props: {
  t: Trailer;
  waitingFor?: string;
  outcome?: Outcome;
  onSend: (a: "stop" | "start") => void;
  onCutSignal: () => void;
}) {
  const { t, waitingFor, outcome } = props;
  const n = t.n_modules ?? 0;
  const capacity = n * MODULE_KW;
  const load = t.total_load_kw ?? 0;
  const pct = capacity ? Math.min(100, (load / capacity) * 100) : 0;
  const active = t.active_modules ?? 0;
  const offline = t.state !== "online";
  const backlog = t.outbox_backlog ?? 0;
  const busy = Boolean(waitingFor);

  return (
    <article className={`card ${offline ? "is-offline" : ""}`}>
      <div className="card-head">
        <div>
          <div className="tid">{t.trailer_id}</div>
          <div className="muted small">{SITE_LABELS[t.site_id ?? ""] ?? t.site_id} · {capacity} kW</div>
        </div>
        <span className={`pill ${offline ? "pill-bad" : "pill-good"}`}>
          {offline ? (t.state_reason === "connection_lost" ? "signal lost" : "offline") : "online"}
        </span>
      </div>

      <div className="row">
        <span className="label">Load</span>
        <div className="bar"><div className="bar-fill" style={{ width: `${pct}%` }} /></div>
        <span className="mono small">{load.toFixed(0)} kW · {pct.toFixed(0)}%</span>
      </div>

      <div className="row">
        <span className="label">Modules</span>
        <div className="modules">
          {Array.from({ length: n }, (_, i) => (
            <span key={i} className={`mod ${i < active ? "mod-on" : ""}`} title={i < active ? "running" : "standby"} />
          ))}
        </div>
        <span className="mono small">{active}/{n}</span>
      </div>

      <div className="row">
        <span className="label">Fuel</span>
        <span className="mono small">{t.fuel_tank_pct != null ? `${t.fuel_tank_pct.toFixed(0)}%` : "–"}</span>
        {backlog > 1 && <span className="note">{backlog} messages queued on trailer</span>}
      </div>

      <div className="state">
        <div><span className="label">Desired</span> <b>{t.desired_mode ?? "–"}</b></div>
        <div><span className="label">Reported</span> <b>{t.reported_mode ?? "–"}</b></div>
        {t.desired_mode && (
          <span className={`pill ${t.converged ? "pill-good" : "pill-warn"}`}>
            {t.converged ? "converged" : "not converged"}
          </span>
        )}
      </div>

      {busy && (
        <div className="outcome waiting">
          <span className="spinner" /> Sent {waitingFor}. Waiting for {t.trailer_id} to decide…
        </div>
      )}
      {!busy && outcome && (
        <div className={`outcome ${outcome.status}`}>
          <b>
            {outcome.status === "accepted" && `${t.trailer_id} accepted ${outcome.action}`}
            {outcome.status === "rejected" && `${t.trailer_id} refused ${outcome.action}`}
            {outcome.status === "expired" && `${outcome.action} expired before delivery`}
            {outcome.status === "pending" && `Still waiting on ${t.trailer_id}`}
          </b>
          {outcome.reason && <div className="small">{outcome.reason}</div>}
        </div>
      )}

      <div className="actions">
        <button className="btn danger" disabled={busy} onClick={() => props.onSend("stop")}>Stop</button>
        <button className="btn" disabled={busy} onClick={() => props.onSend("start")}>Start</button>
        <button className="btn ghost" disabled={busy || offline} onClick={props.onCutSignal} title="Simulator: drop this trailer's cell connection for 60 s">
          Cut signal 60 s
        </button>
      </div>
    </article>
  );
}

function StatusChip({ status }: { status: CommandStatus }) {
  return <span className={`chip chip-${status}`}>{status}</span>;
}
