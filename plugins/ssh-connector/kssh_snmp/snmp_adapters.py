# GENERATED COPY of monitoring/snmp_adapters.py - edit the original, then run scripts/sync_plugin_snmp.py
"""Device adapters: generic standard-MIB logic plus vendor hooks.

Only standard MIBs (SNMPv2-MIB, ENTITY-MIB, ENTITY-SENSOR-MIB) are used.
No vendor-private OID is assumed. Anything a device does not expose is
reported as ``"UNAVAILABLE"`` - values are never estimated or invented.
"""
from __future__ import annotations

from typing import Any

from .errors import ErrorCode, MonitorError
from .snmp_client import END_OF_MIB, NO_SUCH, SnmpClient

UNAVAILABLE = "UNAVAILABLE"

SYSTEM = {
    "sys_descr": "1.3.6.1.2.1.1.1.0",
    "sys_object_id": "1.3.6.1.2.1.1.2.0",
    "sys_uptime_ticks": "1.3.6.1.2.1.1.3.0",
    "sys_name": "1.3.6.1.2.1.1.5.0",
    "sys_location": "1.3.6.1.2.1.1.6.0",
}
ENT_PHYSICAL = {
    "descr": "1.3.6.1.2.1.47.1.1.1.1.2",
    "class": "1.3.6.1.2.1.47.1.1.1.1.5",
    "name": "1.3.6.1.2.1.47.1.1.1.1.7",
    "hardware_rev": "1.3.6.1.2.1.47.1.1.1.1.8",
    "firmware_rev": "1.3.6.1.2.1.47.1.1.1.1.9",
    "software_rev": "1.3.6.1.2.1.47.1.1.1.1.10",
    "serial": "1.3.6.1.2.1.47.1.1.1.1.11",
    "model": "1.3.6.1.2.1.47.1.1.1.1.13",
}
ENT_SENSOR = {
    "type": "1.3.6.1.2.1.99.1.1.1.1",
    "scale": "1.3.6.1.2.1.99.1.1.1.2",
    "precision": "1.3.6.1.2.1.99.1.1.1.3",
    "value": "1.3.6.1.2.1.99.1.1.1.4",
    "oper_status": "1.3.6.1.2.1.99.1.1.1.5",
}
ENT_CLASS = {3: "chassis", 6: "powerSupply", 7: "fan", 8: "sensor", 9: "module"}
SENSOR_TYPE = {3: "voltsAC", 4: "voltsDC", 5: "amperes", 6: "watts", 8: "celsius", 10: "rpm"}
SENSOR_STATUS = {1: "ok", 2: "unavailable", 3: "nonoperational"}
# EntitySensorDataScale exponent (power of ten), RFC 3433
SCALE_EXP = {1: -24, 2: -21, 3: -18, 4: -15, 5: -12, 6: -9, 7: -6, 8: -3, 9: 0,
             10: 3, 11: 6, 12: 9, 13: 12, 14: 15, 15: 18, 16: 21, 17: 24}


def _clean(value: Any) -> Any:
    return UNAVAILABLE if value is NO_SUCH or value is END_OF_MIB or value is None else value


def sensor_reading(value: int, scale: int | None, precision: int | None) -> float | None:
    """Convert an ENTITY-SENSOR-MIB raw value to SI units."""
    if not isinstance(value, int):
        return None
    exp = SCALE_EXP.get(scale if isinstance(scale, int) else 9, 0)
    prec = precision if isinstance(precision, int) and -8 <= precision <= 9 else 0
    return round(value * (10 ** exp) / (10 ** prec), 3)


class GenericAdapter:
    """Standard-MIB adapter. Vendor adapters subclass and add verified OIDs."""

    name = "generic"
    vendor_oids_verified = False  # flips to True only when a vendor OID is confirmed on hardware

    def __init__(self, client: SnmpClient):
        self.client = client

    async def device_info(self) -> dict:
        raw = await self.client.get(list(SYSTEM.values()))
        info = {k: _clean(raw.get(oid)) for k, oid in SYSTEM.items()}
        info.update(await self._chassis())
        info["adapter"] = self.name
        return info

    async def _walk_table(self, columns: dict[str, str], max_rows: int = 256) -> dict[int, dict]:
        rows: dict[int, dict] = {}
        for key, col in columns.items():
            res = await self.client.walk(col, max_results=max_rows)
            for oid, value in res.items:
                idx = int(oid.rsplit(".", 1)[1])
                rows.setdefault(idx, {})[key] = value
        return rows

    async def _chassis(self) -> dict:
        try:
            rows = await self._walk_table(ENT_PHYSICAL)
        except MonitorError as exc:
            if exc.code.fails_check:
                raise
            rows = {}
        chassis = next((r for _, r in sorted(rows.items()) if r.get("class") == 3), None)
        if not chassis:
            return {"model": UNAVAILABLE, "firmware": UNAVAILABLE, "hardware_rev": UNAVAILABLE}
        firmware = chassis.get("firmware_rev") or chassis.get("software_rev")
        return {
            "model": _clean(chassis.get("model") or None),
            "firmware": _clean(firmware or None),
            "hardware_rev": _clean(chassis.get("hardware_rev") or None),
        }

    async def environment(self) -> dict:
        """Temperature / fan / PSU from ENTITY-SENSOR-MIB only."""
        try:
            sensors = await self._walk_table(ENT_SENSOR)
        except MonitorError as exc:
            if exc.code.fails_check:
                raise
            sensors = {}
        temps, fans, power = [], [], []
        for idx, s in sorted(sensors.items()):
            stype = SENSOR_TYPE.get(s.get("type"))
            reading = {
                "entity_index": idx,
                "status": SENSOR_STATUS.get(s.get("oper_status"), UNAVAILABLE),
                "value": sensor_reading(s.get("value"), s.get("scale"), s.get("precision")),
            }
            if reading["status"] != "ok" or reading["value"] is None:
                reading["value"] = UNAVAILABLE
            if stype == "celsius":
                temps.append(reading)
            elif stype == "rpm":
                fans.append(reading)
            elif stype in {"watts", "voltsAC", "voltsDC", "amperes"}:
                power.append({**reading, "unit": stype})
        return {
            "temperature_c": temps or UNAVAILABLE,
            "fans": fans or UNAVAILABLE,
            "power": power or UNAVAILABLE,
            "source": "ENTITY-SENSOR-MIB",
        }

    async def health(self) -> dict:
        """Cheap liveness: authenticated GET of sysUpTime + sysName."""
        raw = await self.client.get([SYSTEM["sys_uptime_ticks"], SYSTEM["sys_name"]])
        up = raw.get(SYSTEM["sys_uptime_ticks"])
        if not isinstance(up, int):
            raise MonitorError(ErrorCode.PROTOCOL_ERROR, "sysUpTime missing")
        return {"ok": True, "sys_uptime_ticks": up, "sys_name": _clean(raw.get(SYSTEM["sys_name"]))}

    def capabilities(self) -> dict:
        return {
            "adapter": self.name,
            "standard_mibs": ["SNMPv2-MIB", "IF-MIB", "IF-X-MIB", "ENTITY-MIB", "ENTITY-SENSOR-MIB"],
            "vendor_oids_verified": self.vendor_oids_verified,
            "write_operations": False,
        }


class RealHDAdapter(GenericAdapter):
    """Real HD managed 10GbE switch (e.g. SW5-10G-RJ45).

    No vendor-private OID for temperature/fans/PSU has been verified on the
    real device, so this adapter deliberately adds none. Environment data is
    reported only if the switch implements ENTITY-SENSOR-MIB; otherwise
    UNAVAILABLE. Add OIDs here only after confirming them from the vendor MIB
    and a read-only walk (PENDING_EXPLICIT_APPROVAL for any real-device test).
    """

    name = "realhd"


class CiscoAdapter(GenericAdapter):
    """Placeholder for CISCO-ENVMON-MIB / CISCO-PROCESS-MIB (not implemented)."""

    name = "cisco"


class UniFiAdapter(GenericAdapter):
    """Placeholder for UniFi switches (not implemented; standard MIBs only)."""

    name = "unifi"


ADAPTERS = {cls.name: cls for cls in (GenericAdapter, RealHDAdapter, CiscoAdapter, UniFiAdapter)}


def make_adapter(name: str, client: SnmpClient) -> GenericAdapter:
    try:
        return ADAPTERS[name or "generic"](client)
    except KeyError:
        raise MonitorError(ErrorCode.CONFIG_ERROR, f"unknown adapter {name!r}") from None
