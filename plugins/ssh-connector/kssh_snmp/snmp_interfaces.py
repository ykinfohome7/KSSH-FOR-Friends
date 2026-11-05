# GENERATED COPY of monitoring/snmp_interfaces.py - edit the original, then run scripts/sync_plugin_snmp.py
"""Interface inventory and traffic-rate calculation from IF-MIB / IF-X-MIB.

Rules implemented here (all unit-tested):
* 64-bit HC counters (ifHCInOctets/ifHCOutOctets) are preferred; 32-bit
  ifInOctets/ifOutOctets are a fallback only.
* A decrease of a 32-bit counter between two samples is treated as one
  wrap (+2**32) - but only when the device did not reboot and the interval
  is short enough that a double wrap is impossible at the link speed.
* A decrease of a 64-bit counter is a discontinuity, never a wrap.
* Device reboot (sysUpTime decreased), changed ifCounterDiscontinuityTime,
  or an ifIndex now naming a different interface all reset the baseline:
  no rate is reported for that interval instead of a wrong one.
* Missing objects are reported as ``None``/UNAVAILABLE, never invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .snmp_client import NO_SUCH, END_OF_MIB, SnmpClient

SYS_UPTIME = "1.3.6.1.2.1.1.3.0"

IF_COLUMNS = {
    "descr": "1.3.6.1.2.1.2.2.1.2",
    "type": "1.3.6.1.2.1.2.2.1.3",
    "speed": "1.3.6.1.2.1.2.2.1.5",
    "admin_status": "1.3.6.1.2.1.2.2.1.7",
    "oper_status": "1.3.6.1.2.1.2.2.1.8",
    "last_change": "1.3.6.1.2.1.2.2.1.9",
    "in_octets": "1.3.6.1.2.1.2.2.1.10",
    "in_discards": "1.3.6.1.2.1.2.2.1.13",
    "in_errors": "1.3.6.1.2.1.2.2.1.14",
    "out_octets": "1.3.6.1.2.1.2.2.1.16",
    "out_discards": "1.3.6.1.2.1.2.2.1.19",
    "out_errors": "1.3.6.1.2.1.2.2.1.20",
}
IFX_COLUMNS = {
    "name": "1.3.6.1.2.1.31.1.1.1.1",
    "hc_in_octets": "1.3.6.1.2.1.31.1.1.1.6",
    "hc_out_octets": "1.3.6.1.2.1.31.1.1.1.10",
    "high_speed": "1.3.6.1.2.1.31.1.1.1.15",
    "alias": "1.3.6.1.2.1.31.1.1.1.18",
    "counter_discontinuity": "1.3.6.1.2.1.31.1.1.1.19",
}
STATUS_NAMES = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}
WRAP32 = 2**32
COUNTER_KEYS = ("in_octets", "out_octets", "in_errors", "out_errors", "in_discards", "out_discards")


@dataclass
class InterfaceSample:
    if_index: int
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str):
        v = self.data.get(key)
        return None if v is NO_SUCH or v is END_OF_MIB else v

    @property
    def identity(self) -> str:
        return str(self.get("name") or self.get("descr") or "")

    @property
    def speed_bps(self) -> int | None:
        hs = self.get("high_speed")
        if isinstance(hs, int) and hs > 0:
            return hs * 1_000_000
        sp = self.get("speed")
        if isinstance(sp, int) and 0 < sp < 4_294_967_295:  # max value means "see ifHighSpeed"
            return sp
        return None

    def octets(self, direction: str) -> tuple[int | None, int]:
        """(value, bits) using HC counter when present."""
        hc = self.get(f"hc_{direction}_octets")
        if isinstance(hc, int):
            return hc, 64
        lo = self.get(f"{direction}_octets")
        return (lo if isinstance(lo, int) else None), 32


@dataclass
class DeviceSample:
    taken_at: float                 # local monotonic-ish epoch seconds
    sys_uptime_ticks: int | None    # hundredths of a second
    interfaces: dict[int, InterfaceSample]


async def collect_sample(client: SnmpClient, now: float, *, max_interfaces: int = 512) -> DeviceSample:
    """Walk IF-MIB + IF-X-MIB columns (bounded) and return one sample."""
    up = (await client.get([SYS_UPTIME])).get(SYS_UPTIME)
    rows: dict[int, InterfaceSample] = {}
    for key, col in {**IF_COLUMNS, **IFX_COLUMNS}.items():
        res = await client.walk(col, max_results=max_interfaces)
        for oid, value in res.items:
            idx = int(oid.rsplit(".", 1)[1])
            rows.setdefault(idx, InterfaceSample(idx)).data[key] = value
    return DeviceSample(now, up if isinstance(up, int) else None, rows)


def _delta(prev: int | None, cur: int | None, bits: int, allow_wrap: bool) -> int | None:
    if prev is None or cur is None:
        return None
    if cur >= prev:
        return cur - prev
    if bits == 32 and allow_wrap:
        return cur + WRAP32 - prev
    return None


def compute_rates(prev: DeviceSample | None, cur: DeviceSample) -> dict:
    """Compare two samples; return per-interface status and rates."""
    device_events: list[str] = []
    rebooted = False
    if prev is not None and prev.sys_uptime_ticks is not None and cur.sys_uptime_ticks is not None:
        if cur.sys_uptime_ticks < prev.sys_uptime_ticks:
            rebooted = True
            device_events.append("REBOOT_OR_UPTIME_WRAP")
    interval = (cur.taken_at - prev.taken_at) if prev else None

    out = {}
    for idx, ifc in sorted(cur.interfaces.items()):
        entry: dict[str, Any] = {
            "if_index": idx,
            "name": ifc.identity or None,
            "alias": ifc.get("alias"),
            "admin_status": STATUS_NAMES.get(ifc.get("admin_status"), None),
            "oper_status": STATUS_NAMES.get(ifc.get("oper_status"), None),
            "speed_bps": ifc.speed_bps,
            "last_change_ticks": ifc.get("last_change"),
            "counters": {k: ifc.get(k) for k in ("in_errors", "out_errors", "in_discards", "out_discards")},
            "counter_bits": ifc.octets("in")[1],
            "rates": None,
            "events": [],
        }
        old = prev.interfaces.get(idx) if prev else None
        if old is None:
            entry["events"].append("BASELINE")
            out[idx] = entry
            continue
        if old.identity and ifc.identity and old.identity != ifc.identity:
            entry["events"].append("IFINDEX_REASSIGNED")
            out[idx] = entry
            continue
        if old.speed_bps != ifc.speed_bps:
            entry["events"].append("SPEED_CHANGED")
        if old.get("last_change") != ifc.get("last_change"):
            entry["events"].append("LINK_STATE_CHANGED")
        disc_old, disc_new = old.get("counter_discontinuity"), ifc.get("counter_discontinuity")
        if rebooted or (disc_old is not None and disc_new is not None and disc_old != disc_new):
            entry["events"].append("COUNTER_DISCONTINUITY")
            out[idx] = entry
            continue
        if not interval or interval <= 0:
            out[idx] = entry
            continue

        rates: dict[str, Any] = {}
        for direction in ("in", "out"):
            p, bits_p = old.octets(direction)
            c, bits_c = ifc.octets(direction)
            if bits_p != bits_c:
                rates[f"{direction}_bps"] = None
                continue
            # A 32-bit counter can wrap more than once at high speed; only
            # accept a single wrap when the interval is short enough.
            speed = ifc.speed_bps or 0
            single_wrap_ok = bits_c == 32 and (not speed or interval * speed / 8 < WRAP32)
            d = _delta(p, c, bits_c, allow_wrap=single_wrap_ok)
            rates[f"{direction}_bps"] = None if d is None else round(d * 8 / interval, 1)
        for key in ("in_errors", "out_errors", "in_discards", "out_discards"):
            d = _delta(old.get(key), ifc.get(key), 32, allow_wrap=True)
            rates[f"{key}_per_s"] = None if d is None else round(d / interval, 4)
        speed = ifc.speed_bps
        for direction in ("in", "out"):
            bps = rates.get(f"{direction}_bps")
            rates[f"{direction}_util_pct"] = (
                round(100.0 * bps / speed, 2) if (bps is not None and speed) else None
            )
        if any(rates.get(f"{d}_bps") is None for d in ("in", "out")) and "COUNTER_DISCONTINUITY" not in entry["events"]:
            entry["events"].append("COUNTER_UNUSABLE")
        entry["rates"] = rates
        out[idx] = entry
    removed = sorted(set(prev.interfaces) - set(cur.interfaces)) if prev else []
    return {
        "interval_s": None if interval is None else round(interval, 3),
        "device_events": device_events,
        "removed_if_indexes": removed,
        "interfaces": out,
    }


class FlapTracker:
    """Counts LINK_STATE_CHANGED events per interface in a sliding window."""

    def __init__(self, window_s: float = 600.0, threshold: int = 3):
        self.window_s = window_s
        self.threshold = threshold
        self._events: dict[str, list[float]] = {}

    def update(self, key: str, changed: bool, now: float) -> bool:
        times = [t for t in self._events.get(key, []) if now - t <= self.window_s]
        if changed:
            times.append(now)
        self._events[key] = times
        return len(times) >= self.threshold
