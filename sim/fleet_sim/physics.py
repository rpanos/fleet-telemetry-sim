"""A deliberately simple physics model of one trailer of 20 kW modules.

Not real engine physics -- just enough realism that the data has the right
*shape*: daily load cycles, crane spikes, engine speed that follows load,
temperatures that lag behind load changes, modules that start and stop as
demand changes, and a fuel tank that drains and gets refilled.
"""
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import TrailerSpec

MODULE_RATED_KW = 20.0
LOCAL_TZ = ZoneInfo("America/Phoenix")

# Daily load shape per site kind: (local hour, fraction of trailer capacity).
LOAD_PROFILES = {
    "construction": [(0, 0.06), (5.5, 0.06), (7, 0.55), (11.5, 0.55), (12, 0.25),
                     (12.75, 0.25), (13, 0.55), (15.5, 0.55), (17, 0.06), (24, 0.06)],
    "event": [(0, 0.15), (1, 0.08), (10, 0.08), (12, 0.35), (16, 0.35), (17, 0.75),
              (22.5, 0.75), (23.5, 0.15), (24, 0.15)],
    "mine": [(0, 0.6), (24, 0.6)],
}

# Dispatch rules: start another module above 85% per-module load, stop one if
# the rest could carry the load below 70%, and wait 60 s between changes so
# noise doesn't make modules flap on and off. Dispatch looks at the BASE load
# only: short crane spikes are covered by surge boost, not by starting modules
# ("no oversizing").
START_ABOVE = 0.85
STOP_BELOW = 0.70
MIN_DISPATCH_GAP_S = 60

EXHAUST_WARN_C = 520.0
EXHAUST_CLEAR_C = 480.0
LOW_FUEL_PCT = 20.0
REFUEL_AT_PCT = 12.0


def _interp(points, x):
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
    return points[-1][1]


def _lag(current, target, dt, tau):
    """First-order lag: move part of the way toward target, like a real temperature."""
    return current + (target - current) * (1 - math.exp(-dt / tau))


def ambient_temp_c(hour):
    # Phoenix in November: about 13 C before dawn, about 27 C mid-afternoon.
    return 20 + 7 * math.cos((hour - 15) / 24 * 2 * math.pi)


@dataclass
class Module:
    module_id: str
    running: bool = False
    rpm: float = 0.0
    load_kw: float = 0.0
    coolant_temp_c: float = 22.0
    exhaust_temp_c: float = 22.0
    inverter_temp_c: float = 22.0
    engine_hours: float = 0.0
    start_count: int = 0
    exhaust_warned: bool = False

    def step(self, load_kw, ambient, dt, rng):
        self.load_kw = load_kw if self.running else 0.0
        frac = self.load_kw / MODULE_RATED_KW
        if self.running:
            # Variable speed: engine RPM follows load instead of sitting at 1800.
            rpm_target = 1500 + 2100 * min(frac, 1.0) ** 0.7
            self.rpm = _lag(self.rpm, rpm_target, dt, 2.0)
            self.engine_hours += dt / 3600
            coolant_t, exhaust_t, inverter_t = ambient + 55 + 25 * frac, 120 + 380 * frac, ambient + 10 + 35 * frac
        else:
            self.rpm = _lag(self.rpm, 0.0, dt, 1.0)
            coolant_t = exhaust_t = inverter_t = ambient
        self.coolant_temp_c = _lag(self.coolant_temp_c, coolant_t, dt, 120)
        self.exhaust_temp_c = _lag(self.exhaust_temp_c, exhaust_t, dt, 30)
        self.inverter_temp_c = _lag(self.inverter_temp_c, inverter_t, dt, 60)

        fuel_gph = (0.12 + 0.068 * self.load_kw) if self.running else 0.0
        pf = 0.92 + rng.uniform(-0.02, 0.02)
        signals = {
            "running": 1 if self.running else 0,
            "rpm": self.rpm,
            "load_kw": self.load_kw,
            "load_pct": 100 * frac,
            "boost_active": 1 if frac > 1.0 else 0,   # surge above rating
            "fuel_rate_gph": fuel_gph,
            "coolant_temp_c": self.coolant_temp_c + rng.gauss(0, 0.2),
            "exhaust_temp_c": self.exhaust_temp_c + rng.gauss(0, 1.5),
            "inverter_temp_c": self.inverter_temp_c + rng.gauss(0, 0.3),
            "oil_pressure_kpa": (180 + 0.04 * self.rpm + rng.gauss(0, 3)) if self.running else 0.0,
            "engine_hours": self.engine_hours,
            "start_count": self.start_count,
            "frequency_hz": (60 + rng.gauss(0, 0.01)) if self.running else 0.0,
            "power_factor": pf if self.running else 0.0,
        }
        for ph in ("l1", "l2", "l3"):
            v = (277 + rng.gauss(0, 1.2)) if self.running else 0.0
            p = self.load_kw / 3 * (1 + rng.gauss(0, 0.03))
            signals[f"v_{ph}"] = v
            signals[f"p_{ph}_kw"] = p
            signals[f"i_{ph}_a"] = (p * 1000 / (v * pf)) if v else 0.0
        return {k: round(v, 3) for k, v in signals.items()}


@dataclass
class Trailer:
    spec: TrailerSpec
    rng: random.Random
    modules: list = field(default_factory=list)
    tank_gal: float = 0.0
    fuel_gal: float = 0.0
    spike_kw: float = 0.0
    spike_left_s: float = 0.0
    last_dispatch_s: float = -1e9
    clock_s: float = 0.0
    base_avg_kw: float = -1.0   # smoothed base load the dispatcher sees
    low_fuel_warned: bool = False

    def __post_init__(self):
        n = self.spec.n_modules
        self.modules = [Module(f"{self.spec.trailer_id}-M{i + 1:02d}",
                               engine_hours=self.rng.uniform(50, 900),
                               start_count=self.rng.randint(20, 400)) for i in range(n)]
        self.modules[0].running = True
        self.tank_gal = 50.0 * n
        self.fuel_gal = self.tank_gal * self.rng.uniform(0.3, 0.95)

    @property
    def capacity_kw(self):
        return MODULE_RATED_KW * self.spec.n_modules

    def _demand_kw(self, hour, dt):
        kind = self.spec.site.kind
        base = _interp(LOAD_PROFILES[kind], hour) * self.capacity_kw
        base *= 1 + self.rng.gauss(0, 0.04)
        # Construction: random crane / compressor hard starts during work hours.
        if kind == "construction" and 7 <= hour < 15.5 and self.spike_left_s <= 0 and self.rng.random() < 0.01:
            self.spike_kw = 0.3 * self.capacity_kw
            self.spike_left_s = self.rng.uniform(5, 15)
        spike = 0.0
        if self.spike_left_s > 0:
            self.spike_left_s -= dt
            spike = self.spike_kw
        return max(0.5, base), spike

    def _dispatch(self, demand_kw, events, ts_ms):
        active = [m for m in self.modules if m.running]
        idle = [m for m in self.modules if not m.running]
        if self.clock_s - self.last_dispatch_s < MIN_DISPATCH_GAP_S:
            return
        if idle and demand_kw / len(active) > START_ABOVE * MODULE_RATED_KW:
            m = idle[0]
            m.running, m.start_count = True, m.start_count + 1
            self.last_dispatch_s = self.clock_s
            events.append(_event(ts_ms, "info", "MODULE_STARTED", m.module_id, f"demand {demand_kw:.1f} kW"))
        elif len(active) > 1 and demand_kw / (len(active) - 1) < STOP_BELOW * MODULE_RATED_KW:
            m = active[-1]
            m.running = False
            self.last_dispatch_s = self.clock_s
            events.append(_event(ts_ms, "info", "MODULE_STOPPED", m.module_id, f"demand {demand_kw:.1f} kW"))

    def step(self, ts_ms, dt=1.0):
        """Advance one tick. Returns (module_samples, trailer_sample, events)."""
        self.clock_s += dt
        local = datetime.fromtimestamp(ts_ms / 1000, LOCAL_TZ)
        hour = local.hour + local.minute / 60 + local.second / 3600
        ambient = ambient_temp_c(hour)
        events = []

        base, spike = self._demand_kw(hour, dt)
        demand = base + spike
        # Dispatch on a 2-minute moving average so second-to-second noise
        # doesn't start and stop modules.
        self.base_avg_kw = base if self.base_avg_kw < 0 else _lag(self.base_avg_kw, base, dt, 120)
        self._dispatch(self.base_avg_kw, events, ts_ms)
        active = [m for m in self.modules if m.running]
        share = demand / len(active)

        module_samples = []
        total_fuel_gph = 0.0
        for m in self.modules:
            sig = m.step(share, ambient, dt, self.rng)
            total_fuel_gph += sig["fuel_rate_gph"]
            module_samples.append({"ts_ms": ts_ms, "module_id": m.module_id, "signals": sig})
            if not m.exhaust_warned and sig["exhaust_temp_c"] > EXHAUST_WARN_C:
                m.exhaust_warned = True
                events.append(_event(ts_ms, "warning", "EXHAUST_TEMP_HIGH", m.module_id,
                                     f"{sig['exhaust_temp_c']:.0f} C"))
            elif m.exhaust_warned and sig["exhaust_temp_c"] < EXHAUST_CLEAR_C:
                m.exhaust_warned = False
                events.append(_event(ts_ms, "info", "EXHAUST_TEMP_NORMAL", m.module_id, ""))

        self.fuel_gal = max(0.0, self.fuel_gal - total_fuel_gph * dt / 3600)
        fuel_pct = 100 * self.fuel_gal / self.tank_gal
        if fuel_pct < LOW_FUEL_PCT and not self.low_fuel_warned:
            self.low_fuel_warned = True
            events.append(_event(ts_ms, "warning", "LOW_FUEL", None, f"{fuel_pct:.0f}%"))
        if fuel_pct < REFUEL_AT_PCT:  # the fuel truck shows up
            self.fuel_gal = self.tank_gal * 0.95
            self.low_fuel_warned = False
            events.append(_event(ts_ms, "info", "REFUELED", None, "to 95%"))
            fuel_pct = 95.0

        trailer_sample = {"ts_ms": ts_ms, "signals": {k: round(v, 4) for k, v in {
            "total_load_kw": demand,
            "active_modules": len(active),
            "fuel_tank_pct": fuel_pct,
            "fuel_tank_gal": self.fuel_gal,
            "ambient_temp_c": ambient + self.rng.gauss(0, 0.2),
            "lat": self.spec.site.lat,
            "lon": self.spec.site.lon,
            "cell_signal_dbm": -85 + self.rng.gauss(0, 6),
            "door_open": 1 if self.rng.random() < 0.002 else 0,
        }.items()}}
        return module_samples, trailer_sample, events


def _event(ts_ms, severity, code, module_id, detail):
    return {"ts_ms": ts_ms, "severity": severity, "code": code,
            "module_id": module_id, "detail": detail}
