"""Settings (from environment variables) and the simulated fleet roster."""
import os
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SiteSpec:
    site_id: str
    kind: str  # "construction" | "event" | "mine" -> picks the load profile
    lat: float
    lon: float


@dataclass(frozen=True)
class TrailerSpec:
    trailer_id: str
    site: SiteSpec
    n_modules: int


# Made-up sites. Each has a different daily load shape.
SITES = [
    SiteSpec("SITE-SCOTTSDALE-BLDG", "construction", 33.4942, -111.9261),
    SiteSpec("SITE-TEMPE-FESTIVAL", "event", 33.4255, -111.9400),
    SiteSpec("SITE-GLOBE-MINE", "mine", 33.3942, -110.7865),
]

# Small / medium / large trailers hold 2 / 4 / 10 of the 20 kW modules.
MODULE_COUNTS = [2, 4, 10]


def build_fleet(size: int, seed: int) -> list[TrailerSpec]:
    """Deterministic roster: the same SEED always gives the same fleet."""
    rng = random.Random(seed)
    fleet = []
    for i in range(size):
        site = SITES[i % len(SITES)]
        fleet.append(TrailerSpec(f"TR-{i + 1:03d}", site, rng.choice(MODULE_COUNTS)))
    return fleet


@dataclass(frozen=True)
class Settings:
    mqtt_host: str
    mqtt_port: int
    fleet_size: int
    seed: int
    publish_every_s: int    # how many 1-second samples go into one bundle
    keepalive_s: int        # MQTT keepalive; Last Will fires ~1.5x this after silence
    outbox_dir: str         # where each gateway keeps its on-disk queue
    backfill_per_tick: int  # backlog messages sent per second after an outage (cellular bandwidth cap)


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        mqtt_host=env("MQTT_HOST", "localhost"),
        mqtt_port=int(env("MQTT_PORT", "1883")),
        fleet_size=int(env("FLEET_SIZE", "12")),
        seed=int(env("SEED", "42")),
        publish_every_s=int(env("PUBLISH_EVERY_S", "10")),
        keepalive_s=int(env("KEEPALIVE_S", "15")),
        outbox_dir=env("OUTBOX_DIR", "/data"),
        backfill_per_tick=int(env("BACKFILL_PER_TICK", "2")),
    )
