# M4 — Remote commands: the cloud asks, the trailer decides

See diagram 3 in [architecture.md](architecture.md).

## The four rules
1. **Safety lives on the device.** The cloud can only *request*. The trailer's
   controller refuses a STOP if the site draws more than 40% of capacity,
   because someone's crane or pump depends on that power. There is no cloud
   override (D-118).
2. **Idempotent.** Every command has a unique `command_id`. The trailer
   remembers its answers on disk, so a resend gets the *same answer* and is
   never executed twice.
3. **Commands expire.** Each command carries an expiry. The broker discards it
   if the trailer is offline past that time, and the trailer double-checks and
   rejects anything stale. A STOP sent at 2 pm must never run at 8 pm.
4. **Desired vs. reported.** The cloud records what it *asked for* (desired);
   the trailer's answer says what's *actually true* (reported). If they differ,
   the trailer is "not converged."

## Flow
```
./fleetctl send TR-002 stop
  -> commands row (pending) + device_state.desired = standby
  -> MQTT fleet/TR-002/cmd  (QoS 1, message expiry)
  -> gateway: seen it? expired? -> controller.execute("stop") -> safety check
  -> MQTT fleet/TR-002/cmd/ack {accepted|rejected, reason, reported mode}
  -> ingest: commands row updated, device_state.reported updated, event logged
```

## Try it

**Write**: rebuilds the simulator and ingest worker with M4 (ingest creates the new tables itself):

```
docker compose up -d --build
```

Which trailer accepts depends on the time of day, since the safety rule looks
at the real load:

| Local time | Likely REJECTED (busy) | Likely ACCEPTED (quiet) |
|---|---|---|
| Daytime (7 am – 3:30 pm) | TR-001 construction, TR-003 mine | TR-002 festival |
| Evening (5 – 11 pm) | TR-002 festival, TR-003 mine | TR-001 construction |

Mines run 24/7 at ~60%, so they always refuse.

### 1. An unsafe stop gets refused

**Write**: asks the mine trailer to stop (it will refuse):

```
./fleetctl send TR-003 stop
```

✅ `REJECTED: unsafe: site is drawing 62% of capacity (limit 40%)...`

### 2. A safe stop is accepted

**Write**: asks a quiet trailer to stop (pick one from the table above):

```
./fleetctl send TR-002 stop
```

✅ `ACCEPTED`. On the dashboard (pick that trailer), load drops to 0 and every
module goes to standby.

### 3. Desired vs. reported

**Read-only**: shows desired vs. reported for every commanded trailer, plus recent commands:

```
./fleetctl status
```

TR-003 shows desired `standby`, reported `running`: **NOT converged**. We
asked; it said no. That's the system working, not failing.

### 4. Idempotency: resend the same command

Copy the 8-character id of the accepted stop from `status`.

**Write**: re-sends that exact command:

```
./fleetctl resend <id>
```

✅ `trailer answered again ... but did NOT act again`. The sim log says
`[duplicate: answered again, NOT executed again]`, and the dashboard's
"Answers" column shows 2.

### 5. A stale command is refused

**Write**: sends a command that had already expired before it left:

```
./fleetctl send TR-002 start --stale
```

✅ `REJECTED: expired before it reached the trailer`.

### 6. A command sent during an outage expires

**Write**: cuts TR-002's signal for 90 s:

```
docker compose exec broker mosquitto_pub -h localhost -t sim/control -m '{"action":"outage","trailer_id":"TR-002","seconds":90}'
```

**Write**: sends a start that expires in 20 s:

```
./fleetctl send TR-002 start --expires 20
```

✅ No answer (the trailer is offline). After 20 s, `./fleetctl status` shows
it **EXPIRED**. When TR-002 reconnects 70 s later, the broker has already
thrown the command away, so it never runs late.

### 7. Put things back

**Write**: restarts the trailer you stopped:

```
./fleetctl send TR-002 start
```

## What to say about it
> "The cloud never commands a machine directly. It records a desired state and
> sends a request with a unique id and an expiry. The controller on the
> trailer decides whether it's safe, and answers with what's actually true.
> Resends get the same answer without acting twice, and a request that sat in
> a queue during an outage expires instead of running hours late."
