# SNMP commands (read-only) / SNMP команди (само четене)

KSSH exposes 8 read-only SNMP commands. Total commands: **56** (48 + 8). There is **no SNMP SET** anywhere.
KSSH има 8 SNMP команди само за четене. Общо команди: **56** (48 + 8). **SNMP SET няма.**

> `docs/COMMANDS.md` / `docs/COMMANDS.bg.md` were not regenerated: on `main` they already differ from
> `scripts/gen_commands_docs.py` output and contain hand-written notes that regeneration would drop.
> The SNMP group is registered in the generator for the next full regeneration.

## Configuration / Настройка (`servers.yaml`)

```yaml
snmp:
  allowed_networks: [192.0.2.30/32]   # required / задължително
  devices:
    realhd-switch:
      host: 192.0.2.30
      adapter: realhd                    # generic | realhd | cisco | unifi
      version: "3"
      user: admin
      security_level: authPriv
      auth_protocol: SHA
      priv_protocol: DES
      allow_legacy_des: true             # Real HD supports only DES privacy
      auth_env: KSSH_REALHD_AUTH_PASS    # NAME of an env variable / ИМЕ на променлива
      priv_env: KSSH_REALHD_PRIV_PASS
```

* Commands accept only a configured **device name**, never a raw IP. / Командите приемат само **име на устройство** от настройките, не IP адрес.
* Every host must be inside `allowed_networks`. / Всеки адрес трябва да е в `allowed_networks`.
* Passwords only through environment variables. / Паролите — само чрез променливи на средата.
* Every call is written to `history` as `snmp:<command>`. / Всяко извикване се записва в `history`.

## Commands / Команди

| Command | EN | BG | Example |
|---|---|---|---|
| `snmp_health` | Authenticated GET of sysUpTime + sysName | Проверка: uptime и име | `{"device": "realhd-switch"}` |
| `snmp_device_info` | sysDescr, uptime, model/firmware (ENTITY-MIB), temperature/fans/power (ENTITY-SENSOR-MIB) or `UNAVAILABLE` | Модел, firmware, uptime, температура ако е налична | `{"device": "realhd-switch"}` |
| `snmp_get` | GET up to 32 numeric OIDs | GET до 32 OID | `{"device": "realhd-switch", "oids": ["1.3.6.1.2.1.1.5.0"]}` |
| `snmp_getnext` | GETNEXT up to 32 OIDs | GETNEXT до 32 OID | `{"device": "realhd-switch", "oids": ["1.3.6.1.2.1.1"]}` |
| `snmp_getbulk` | GETBULK, max_repetitions ≤ 25 (v2c/v3) | GETBULK, до 25 повторения | `{"device": "realhd-switch", "oids": ["1.3.6.1.2.1.2.2.1.2"], "max_repetitions": 10}` |
| `snmp_walk` | Bounded walk: ≤ 2000 results, ≤ 20 s, `truncated` flag | Ограничен WALK | `{"device": "realhd-switch", "oid": "1.3.6.1.2.1.2.2.1.2", "max_results": 500}` |
| `snmp_interfaces` | Ports: name, alias, admin/oper status, speed, error counters; `include` glob | Портове: състояние, скорост, грешки | `{"device": "realhd-switch", "include": "Te*"}` |
| `snmp_interface_stats` | Two samples `sample_seconds` apart (1–30): bit/s, utilisation %, errors/s; 64-bit counters, rollover/reboot safe | Трафик, натоварване, грешки/сек | `{"device": "realhd-switch", "include": "*", "sample_seconds": 5}` |

## Result format / Формат на отговора

Success: `{"device", "status": "UP", "read_only": true, ...data}`.
Failure: `{"device", "status": "DOWN" | "UNKNOWN", "error_code", "detail"}` —
`DOWN` = device side (`TIMEOUT`, `UNREACHABLE`, `AUTH_FAILED`, `PROTOCOL_ERROR`);
`UNKNOWN` = our setup (`CONFIG_ERROR`, `SECRET_MISSING`, `DEPENDENCY_MISSING`, `DEADLINE_EXCEEDED`, `INTERNAL`).
Strings longer than 1024 characters are truncated. Backend error texts are never returned (type only).

## Dependencies / Зависимости

Real SNMP needs `pysnmp>=7.1,<8` on the connector host (`monitoring/requirements-monitoring.txt`); without it the commands return `UNKNOWN / DEPENDENCY_MISSING`. **The PySNMP backend has not been executed yet** (PyPI blocked in the development environment). Real-device tests are `PENDING_EXPLICIT_APPROVAL`.

## Packaging / Пакетиране

The plugin is installed as the folder `plugins/ssh-connector` only, so the SNMP library is copied from `monitoring/` into `plugins/ssh-connector/kssh_snmp/` by `scripts/sync_plugin_snmp.py`. `tests/test_plugin_snmp.py` fails if the copies differ. Edit `monitoring/`, then run the script.
