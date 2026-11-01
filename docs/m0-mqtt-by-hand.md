# M0 — Learn MQTT by hand (≈20 min)

Goal: see the core MQTT ideas with nothing but the broker and its built-in
command-line clients. Use **two terminal windows**, both in the repo folder.
All commands run the clients *inside* the broker container, so nothing needs
installing on your Mac.

---

## 1. Start the broker

**Write** — starts the `fleet-broker` container:

```
docker compose up -d
```

**Read-only** — watch the broker's own log (leave it running in a third window if you like):

```
docker compose logs -f broker
```

---

## 2. Topics and the `#` wildcard

The broker routes by **topic** — an address like `fleet/trailer-01/telemetry`.
`#` means "this level and everything below it."

**Terminal A (read-only)** — subscribe to everything under `fleet/`:

```
docker compose exec broker mosquitto_sub -h localhost -t 'fleet/#' -v
```

**Terminal B (write — publishes one message)**:

```
docker compose exec broker mosquitto_pub -h localhost -t fleet/trailer-01/telemetry -m '{"rpm":1450,"load_kw":12.3}'
```

✅ Terminal A prints the topic and the message. The publisher never knew who
was listening — that's **publish/subscribe**: the broker is the middleman.

---

## 3. The `+` wildcard (exactly one level)

Stop Terminal A (Ctrl-C). **Terminal A (read-only)** — alarms from *every* trailer, nothing else:

```
docker compose exec broker mosquitto_sub -h localhost -t 'fleet/+/alarms' -v
```

**Terminal B (write)** — this one should NOT appear in A:

```
docker compose exec broker mosquitto_pub -h localhost -t fleet/trailer-01/telemetry -m 'not an alarm'
```

**Terminal B (write)** — this one SHOULD appear:

```
docker compose exec broker mosquitto_pub -h localhost -t fleet/trailer-07/alarms -m '{"severity":"warning","code":"EXHAUST_TEMP_HIGH"}'
```

---

## 4. Retained messages — "what's the status right now?"

A **retained** message is kept by the broker; anyone who subscribes later
immediately gets the last one. Perfect for `online`/`offline` status.

Stop Terminal A. **Terminal B (write — stores a retained message on the broker)**:

```
docker compose exec broker mosquitto_pub -h localhost -t fleet/trailer-01/status -m online -r
```

**Terminal A (read-only)** — subscribe *after* it was sent:

```
docker compose exec broker mosquitto_sub -h localhost -t 'fleet/+/status' -v
```

✅ You get `online` instantly, even though it was published before you subscribed.

**Terminal B (write — clears the retained message)**:

```
docker compose exec broker mosquitto_pub -h localhost -t fleet/trailer-01/status -r -n
```

---

## 5. Last Will — free offline detection

When a device connects, it can register a **Last Will**: "if I vanish
without saying goodbye, publish *this* for me." A clean disconnect does NOT
trigger it; a crash or lost signal does.

Leave Terminal A subscribed to `fleet/+/status`.

**Terminal B (read-only subscribe, but registers a will)** — pretend to be trailer-02:

```
docker compose exec broker mosquitto_sub -h localhost -i trailer-02 -t fleet/trailer-02/cmd --will-topic fleet/trailer-02/status --will-payload offline --will-retain
```

Now open a **third terminal** and "pull the plug" on trailer-02.

**Write — kills only the fake trailer-02 process inside the container**:

```
docker compose exec broker pkill -9 -f trailer-02
```

✅ Terminal A prints `fleet/trailer-02/status offline`.

Try it again but stop trailer-02 with **Ctrl-C** instead: no `offline` message.
That's the difference between a goodbye and a disappearance.

---

## 6. QoS 1 + persistent sessions — the broker's own store-and-forward

**QoS 1** = "at least once": the receiver must acknowledge, or it gets resent
(so duplicates are possible). A **persistent session** (`-c` plus a fixed client
id) asks the broker to hold QoS 1 messages while you're offline.

**Terminal A (read-only)** — an "ops" subscriber with a persistent session:

```
docker compose exec broker mosquitto_sub -h localhost -i ops-1 -c -q 1 -t 'fleet/+/alarms' -v
```

Stop Terminal A with Ctrl-C — ops-1 is now offline.

**Terminal B (write)** — an alarm fires while ops is away:

```
docker compose exec broker mosquitto_pub -h localhost -q 1 -t fleet/trailer-03/alarms -m '{"severity":"critical","code":"OVERTEMP_SHUTDOWN"}'
```

**Terminal A (read-only)** — reconnect with the same id:

```
docker compose exec broker mosquitto_sub -h localhost -i ops-1 -c -q 1 -t 'fleet/+/alarms' -v
```

✅ The missed alarm arrives. That's the **broker** buffering for a disconnected
*subscriber*. In M3 we build the mirror image: the **gateway** buffering for a
disconnected *trailer* (store-and-forward).

---

## What you should be able to say after M0

- The broker is just a program holding connections and routing by topic.
- `+` matches one level, `#` matches everything below.
- Retained = "last known value" for late subscribers.
- Last Will = automatic offline detection when a device disappears.
- QoS 1 = at-least-once, so duplicates happen → writes must be idempotent.
- Persistent sessions = the broker holds messages for an offline client.
