# Architecture diagrams

Sources are Mermaid (`docs/diagrams/*.mmd`; GitHub renders them below). PNGs are for printing.

## System data flow

Where the data goes, from module to dashboard, and how commands come back down.

```mermaid
%%{init: {"flowchart": {"curve": "basis"}, "themeVariables": {"clusterBkg": "#fafafa", "clusterBorder": "#bbb"}}}%%
flowchart LR
  subgraph TR["Trailer on a jobsite"]
    direction TB
    M1["Module 1<br/>20 kW + controller"]
    M2["Module 2"]
    MN["Module N"]
    GW["Gateway<br/>Linux box + cell modem"]
    Q[("Disk queue<br/>store-and-forward")]
    M1 --> GW
    M2 --> GW
    MN --> GW
    GW <--> Q
  end

  subgraph CL["Cloud"]
    direction TB
    BR{{"MQTT broker<br/>Mosquitto or AWS IoT Core"}}
    ING["Ingest worker<br/>batch insert + dedupe"]
    DB[("TimescaleDB<br/>time-series tables + rollups")]
    GF["Grafana<br/>engineering dashboards"]
    APP["Ops and customer app<br/>future"]
    CMD["Command service<br/>desired vs reported"]
  end

  GW <-- "UP: telemetry every 10 s, QoS 1<br/>DOWN: commands<br/>over cellular" --> BR
  BR --> ING --> DB
  DB --> GF
  DB --> APP
  APP --> CMD
  CMD -- "fleet/id/cmd" --> BR

  classDef edge fill:#fff4e5,stroke:#d9822b,color:#222
  classDef cloud fill:#e8f1fb,stroke:#2b6cb0,color:#222
  classDef future fill:#f2f2f2,stroke:#999,color:#666,stroke-dasharray: 4 3
  class M1,M2,MN,GW,Q edge
  class BR,ING,DB,GF,CMD cloud
  class APP future
```

PNG: [diagrams/1-data-flow.png](diagrams/1-data-flow.png)

## Store-and-forward through a cell outage

Nothing is lost, nothing is double-counted, and history lands at the time it was measured.

```mermaid
sequenceDiagram
  participant M as Modules
  participant G as Gateway
  participant D as Disk queue
  participant B as Broker
  participant DB as Database
  M->>G: reading, measured 14:00:01
  G->>D: 1. write to disk first
  G->>B: 2. publish, QoS 1
  B-->>G: 3. PUBACK, got it
  G->>D: 4. now safe to delete
  Note over G,B: Cell signal lost 14:05 to 20:05
  loop every second while offline
    M->>G: reading
    G->>D: write, queue keeps growing
  end
  Note over G,B: Signal back at 20:05
  G->>B: live lane: current status FIRST
  G->>B: backfill lane: buffered history, oldest first
  B->>DB: stored at MEASURED time 14:05, not arrival time 20:05
  Note over G,DB: PUBACK lost, so the gateway resends. The duplicate hits the same primary key and changes nothing (idempotent).
```

PNG: [diagrams/2-store-and-forward.png](diagrams/2-store-and-forward.png)

## Remote command path

The cloud requests; the machine decides. Commands are idempotent and expire.

```mermaid
sequenceDiagram
  actor U as Ops user
  participant C as Command service
  participant B as Broker
  participant G as Gateway on trailer
  participant S as Controller safety check
  U->>C: set DESIRED mode = standby
  C->>C: give it command_id + expires in 60 s
  C->>B: publish fleet/TR-001/cmd, QoS 1, expiry 60 s
  B->>G: deliver
  G->>G: seen this command_id already? ignore it (idempotent)
  G->>G: expired? reject it, never run late
  G->>S: safe to stop now?
  alt load is low
    S-->>G: yes
    G->>B: ack ACCEPTED, REPORTED mode = standby
  else load 85%, a crane is running
    S-->>G: NO
    G->>B: ack REJECTED, reason load above 30%
  end
  B->>C: ack
  C->>U: shows desired vs reported side by side
  Note over C,S: The cloud only REQUESTS. The machine DECIDES.
```

PNG: [diagrams/3-command-path.png](diagrams/3-command-path.png)

## Inside one trailer (physical view)

Where the gateway physically lives. Each module has its own controller, which runs the safety logic. All controllers share one CAN bus data cable. The gateway sits in the control cabinet and is the only thing that talks to the internet, like a home Wi-Fi router. (In this simulator the modules and gateway are one Python process.)

```mermaid
%%{init: {"flowchart": {"curve": "basis"}, "themeVariables": {"clusterBkg": "#fafafa", "clusterBorder": "#bbb"}}}%%
flowchart LR
  subgraph TRAILER["Inside one Edge trailer - physical view"]
    direction TB
    subgraph BAY["Module bay - power side"]
      direction LR
      MA["Module 1<br/>engine + alternator + inverter<br/><b>controller</b> = small computer<br/>runs the safety logic"]
      MB["Module 2<br/>own controller"]
      MC["Module N<br/>own controller"]
    end
    BUS["CAN bus<br/>one shared 2-wire data cable<br/>like the network inside a car"]
    subgraph CAB["Control cabinet - small box on the wall"]
      direction TB
      GW["<b>GATEWAY</b><br/>rugged Linux computer<br/>store-and-forward on its disk"]
      MODEM["Cell modem + GPS"]
      GW --- MODEM
    end
    HMI["Local touchscreen<br/>for the crew on site"]
    ANT(("Antenna<br/>on the roof"))
    MA --- BUS
    MB --- BUS
    MC --- BUS
    BUS --- GW
    BUS --- HMI
    MODEM --- ANT
  end
  TOWER["Cell tower"]
  CLOUD["Cloud<br/>MQTT broker"]
  ANT -. "4G / LTE radio" .-> TOWER
  TOWER -- "internet" --> CLOUD

  classDef mod fill:#fff4e5,stroke:#d9822b,color:#222
  classDef gw fill:#e6f4ea,stroke:#2f855a,color:#222,stroke-width:2px
  classDef cloud fill:#e8f1fb,stroke:#2b6cb0,color:#222
  classDef bus fill:#fff,stroke:#666,color:#222,stroke-dasharray: 5 3
  class MA,MB,MC mod
  class GW,MODEM gw
  class TOWER,CLOUD cloud
  class BUS,HMI,ANT bus
```

PNG: [diagrams/4-inside-the-trailer.png](diagrams/4-inside-the-trailer.png)
