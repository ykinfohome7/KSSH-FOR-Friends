# Библиотека с команди (snippets) за KSSH

[English](SNIPPETS.md)

143 готови командни шаблона в 12 категории с описание на английски и български. **В тази фаза всички шаблони са само за четене (READ_ONLY).** Библиотеката е част от плъгина (`plugins/ssh-connector/snippets/*.yaml`, модул `snippet_library.py`), но **не е активирана на KSSH сървъра** и **не добавя MCP команда** — KSSH 1.4.1 остава с 60 команди.

> Файлът се генерира от `scripts/gen_snippet_docs.py` — редактирай YAML файловете, не тази страница.

## Сигурност

| Риск | Значение | Одобрение | Подготвя се от модула |
|---|---|---|---|
| `READ_ONLY` | само чете състояние, логове или настройки | не е нужно | да |
| `CHANGE` | променя настройки или състояние (рестарт, обновяване, scrub ...) | **задължително** (`requires_approval: true`) | не (фаза 1) |
| `DANGEROUS` | може да спре услуга или да загуби данни | **задължително** и второ потвърждение | не (фаза 1) |

* Параметрите имат тип и се проверяват. Стойностите се поставят само като цели думи в кавички (POSIX `shlex.quote`, PowerShell с единични кавички); числата — само след проверка, че са цифри. Параметър в кавички се отхвърля, стойност не може да започва с `-`, а `;`, `|`, `$()`, обратни кавички, кавички, интервали и нов ред не могат да стигнат до shell-а през параметър.
* Всеки READ_ONLY шаблон се проверява срещу списък със забранени действия (рестарт/спиране, изтриване, пакети, мрежа/firewall/VLAN, дискове/ZFS/Ceph, потребители и пароли, VM/LXC, Tailscale/WireGuard, пренасочване към файл, here-document, произволни интерпретатори). Списъкът е предпазна мрежа; всеки нов шаблон пак се преглежда от човек.
* Без тайни в GitHub: SNMPv3 данните са в `~/.snmp/snmp.conf` на хоста, частните ключове на WireGuard не се показват, а UniFi шаблонът показва само inform URL.
* PowerShell шаблоните се пращат като `powershell -NoProfile -NonInteractive -EncodedCommand ...`, pfSense — като `/bin/sh -c '...'`, затова login shell-ът не влияе на кавичките.
* Модулът само проверява, търси и подготвя команди. Не отваря SSH и нищо не изпълнява.

## Нива на права

| Ниво | Значение |
|---|---|
| `user` | обикновен потребител |
| `sudo` | потребител с sudo права (команди само за четене) |
| `root` | root достъп на хоста |
| `admin` | локален администратор (Windows) / админ shell (pfSense, UniFi) |
| `domain-admin` | права за четене в Active Directory; някои команди искат Domain Admins |
| `exchange-admin` | Exchange View-Only Organization Management или по-високо |

## Типове параметри

| Тип | Допустими стойности |
|---|---|
| `int` | цифри в границите min..max на шаблона |
| `port` | 1..65535 |
| `host` | IPv4/IPv6 адрес или DNS име |
| `ip` | IPv4/IPv6 адрес |
| `token` | букви, цифри и `. _ @ : -` (без `-` в началото), ограничена дължина |
| `enum` | една от изброените стойности |
| `email` | имейл адрес |
| `path` | пълен път, безопасни знаци, без `..` |
| `oid` | числов OID или `MIB::име` |
| `device` | `/dev/sdX`, `/dev/vdX`, `/dev/nvmeXnY` |
| `iface` | име на мрежов интерфейс (до 15 знака) |

## Търсене и използване (сега, без сървъра)

```bash
cd plugins/ssh-connector
python3 snippet_library.py validate
python3 snippet_library.py search --q "диск" --category linux --os debian --lang bg
python3 snippet_library.py search --q "certificate"                # търсене и на английски
python3 snippet_library.py show network.mtu_test --lang bg
python3 snippet_library.py render network.mtu_test host=192.0.2.1 size=8972
python3 snippet_library.py export linux.disk_usage linux.memory   # блок 'snippets:' за servers.yaml
```

Търсенето проверява всяка дума в id, имената и описанията на EN и BG, категорията, операционните системи и етикетите; филтри: `--category`, `--os`, `--risk`, `--tag`.

## Извикване през MCP

**Работи с KSSH 1.4.1 още сега (след одобрение, фаза 2):** шаблоните без параметри се експортират с `snippet_library.py export ...` в раздел `snippets:` на `servers.yaml`; после се виждат с `list_snippets` и се пускат със съществуващата `run_snippet(server, name)` — без промяна на кода и пак 60 MCP команди.

**Предложение за следваща версия (отделно одобрение, ще станат 63 команди):**

| Команда | Предназначение |
|---|---|
| `snippet_search(query, category, os, risk, lang)` | търсене в библиотеката (EN/BG) |
| `snippet_show(id, lang)` | пълно описание, параметри, пример, риск |
| `snippet_run(server, id, params)` | проверка на параметрите, подготовка и изпълнение през съществуващия SSH слой; само READ_ONLY, `sudo` само ако шаблонът го изисква, timeout от шаблона, всяко извикване се записва в `history` като `snippet:<id>` |

`snippet_run` ще отказва CHANGE/DANGEROUS, докато няма процес за одобрение (изрично `approve=true` от потребителя и запис в одита), и ще проверява дали `os` на сървъра е в списъка на шаблона.

## Категории

| Категория | Описание | Шаблони |
|---|---|---|
| [linux](#linux) | Linux — Debian, Ubuntu, RHEL, Rocky Linux | 14 |
| [proxmox](#proxmox) | Proxmox VE — хостове, VM, LXC, storage, backup, мрежа | 15 |
| [pfsense](#pfsense) | pfSense — интерфейси, маршрутизация, DNS, VPN, диагностика на firewall | 14 |
| [windows](#windows) | Windows Server 2019/2025 — PowerShell, Active Directory, DNS, DHCP, RDP | 25 |
| [exchange](#exchange) | Microsoft Exchange — услуги, бази данни, сертификати, SMTP | 10 |
| [pmg](#pmg) | Proxmox Mail Gateway — SMTP, Postfix, опашка, спам филтър | 9 |
| [network](#network) | Мрежа — VLAN, ARP, маршрути, ping, TCP, 10GbE, MTU | 14 |
| [unifi](#unifi) | UniFi — диагностика само за четене на устройства и мрежови услуги | 7 |
| [vpn](#vpn) | Tailscale и WireGuard — връзки, състояние, диагностика | 8 |
| [security](#security) | Киберсигурност — TLS, сертификати, SSH, логове, наблюдение | 11 |
| [snmp](#snmp) | SNMP — устройства, интерфейси, статистики (net-snmp инструменти) | 6 |
| [backup](#backup) | Backup и възстановяване — проверка на архиви и цялост на данните | 10 |

## linux

**Linux — Debian, Ubuntu, RHEL, Rocky Linux** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/linux.yaml`](../plugins/ssh-connector/snippets/linux.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `linux.block_devices` | Дискове и дялове | `user` | — |
| `linux.disk_usage` | Заето дисково място | `user` | — |
| `linux.failed_units` | Неуспешни systemd услуги | `user` | — |
| `linux.journal_errors` | Грешки от последното пускане | `sudo` | `lines` |
| `linux.last_logins` | Последни влизания | `user` | `count` |
| `linux.listening_ports` | Отворени портове | `sudo` | — |
| `linux.memory` | Памет и swap | `user` | — |
| `linux.reboot_required` | Нужен ли е рестарт? | `user` | — |
| `linux.service_status` | Състояние на услуга | `user` | `unit` |
| `linux.system_info` | Информация за системата | `user` | — |
| `linux.time_sync` | Синхронизация на часовника | `user` | — |
| `linux.top_processes` | Процеси с най-голямо натоварване на CPU | `user` | `count` |
| `linux.updates_debian` | Налични обновления (Debian/Ubuntu) | `user` | — |
| `linux.updates_rhel` | Налични обновления (RHEL/Rocky) | `user` | — |

## proxmox

**Proxmox VE — хостове, VM, LXC, storage, backup, мрежа** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/proxmox.yaml`](../plugins/ssh-connector/snippets/proxmox.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `proxmox.backup_jobs` | Планирани backup задачи | `root` | — |
| `proxmox.ceph_status` | Състояние на Ceph | `root` | — |
| `proxmox.cluster_guests` | Всички машини в клъстера | `root` | — |
| `proxmox.cluster_status` | Клъстер и кворум | `root` | — |
| `proxmox.firewall_status` | Състояние на Proxmox firewall | `root` | — |
| `proxmox.lxc_config` | Конфигурация на контейнер | `root` | `vmid` |
| `proxmox.lxc_list` | LXC контейнери | `root` | — |
| `proxmox.network_config` | Мрежова конфигурация | `root` | — |
| `proxmox.node_resources` | Ресурси на хостовете | `root` | — |
| `proxmox.recent_tasks` | Последни задачи на хоста | `root` | `count` |
| `proxmox.storage_status` | Състояние на storage | `root` | — |
| `proxmox.version` | Версия на Proxmox VE | `root` | — |
| `proxmox.vm_config` | Конфигурация на VM | `root` | `vmid` |
| `proxmox.vm_list` | Виртуални машини | `root` | — |
| `proxmox.zfs_health` | Състояние на ZFS | `root` | — |

## pfsense

**pfSense — интерфейси, маршрутизация, DNS, VPN, диагностика на firewall** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/pfsense.yaml`](../plugins/ssh-connector/snippets/pfsense.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `pfsense.dns_lookup` | DNS заявка от firewall | `admin` | `name` |
| `pfsense.dns_resolver` | DNS услуга (resolver / forwarder) | `admin` | — |
| `pfsense.filter_log` | Лог на firewall | `admin` | `lines` |
| `pfsense.gateway_status` | Състояние на gateway | `admin` | — |
| `pfsense.interface_counters` | Броячи на интерфейсите | `admin` | — |
| `pfsense.interfaces` | Интерфейси | `admin` | — |
| `pfsense.ipsec_status` | IPsec тунели | `admin` | — |
| `pfsense.openvpn_status` | OpenVPN инстанции | `admin` | — |
| `pfsense.pf_info` | Състояние на packet filter | `admin` | — |
| `pfsense.pf_rules` | Заредени правила на firewall | `admin` | — |
| `pfsense.pf_states` | Връзки на един хост | `admin` | `ip` |
| `pfsense.routes` | Таблица с маршрути | `admin` | — |
| `pfsense.system_log` | Системен лог | `admin` | `lines` |
| `pfsense.version` | Версия на pfSense | `admin` | — |

## windows

**Windows Server 2019/2025 — PowerShell, Active Directory, DNS, DHCP, RDP** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/windows.yaml`](../plugins/ssh-connector/snippets/windows.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `windows.ad_dc_list` | Домейн контролери | `domain-admin` | — |
| `windows.ad_dcdiag` | dcdiag (само грешки) | `domain-admin` | — |
| `windows.ad_domain_info` | Информация за AD домейна | `domain-admin` | — |
| `windows.ad_fsmo` | FSMO роли | `domain-admin` | — |
| `windows.ad_locked_users` | Заключени акаунти | `domain-admin` | — |
| `windows.ad_replication` | Обобщение на AD репликацията | `domain-admin` | — |
| `windows.ad_user_info` | Данни за AD потребител | `domain-admin` | `sam` |
| `windows.dhcp_failover` | DHCP резервиране (failover) | `admin` | — |
| `windows.dhcp_scopes` | DHCP обхвати | `admin` | — |
| `windows.dhcp_usage` | Заетост на DHCP обхватите | `admin` | — |
| `windows.disk_space` | Дисково пространство | `user` | — |
| `windows.dns_forwarders` | DNS препращане (forwarders) | `admin` | — |
| `windows.dns_resolve` | DNS заявка | `user` | `name`, `type` |
| `windows.dns_zones` | DNS зони | `admin` | — |
| `windows.event_errors` | Последни грешки в Event Log | `admin` | `log`, `count` |
| `windows.failed_logons` | Неуспешни влизания (4625) | `admin` | `count` |
| `windows.firewall_profiles` | Профили на защитната стена | `user` | — |
| `windows.installed_updates` | Инсталирани обновления | `user` | `count` |
| `windows.listening_ports` | Отворени TCP портове | `admin` | — |
| `windows.pending_reboot` | Чака ли рестарт? | `user` | — |
| `windows.rdp_sessions` | Влезли потребители (RDP) | `user` | — |
| `windows.rdp_settings` | Настройки на RDP | `admin` | — |
| `windows.service_status` | Състояние на услуга | `user` | `name` |
| `windows.stopped_auto_services` | Спрени автоматични услуги | `user` | — |
| `windows.system_info` | Информация за системата | `user` | — |

## exchange

**Microsoft Exchange — услуги, бази данни, сертификати, SMTP** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/exchange.yaml`](../plugins/ssh-connector/snippets/exchange.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `exchange.certificates` | Сертификати на Exchange | `exchange-admin` | — |
| `exchange.component_state` | Състояние на компонентите | `exchange-admin` | — |
| `exchange.database_copies` | Копия на базите (DAG) | `exchange-admin` | — |
| `exchange.database_status` | Пощенски бази данни | `exchange-admin` | — |
| `exchange.message_tracking` | Проследяване на писма по подател | `exchange-admin` | `sender`, `hours`, `count` |
| `exchange.queues` | Опашки за доставка | `exchange-admin` | — |
| `exchange.receive_connectors` | Receive конектори | `exchange-admin` | — |
| `exchange.send_connectors` | Send конектори | `exchange-admin` | — |
| `exchange.servers` | Exchange сървъри | `exchange-admin` | — |
| `exchange.service_health` | Задължителни услуги | `exchange-admin` | — |

## pmg

**Proxmox Mail Gateway — SMTP, Postfix, опашка, спам филтър** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/pmg.yaml`](../plugins/ssh-connector/snippets/pmg.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `pmg.clamav_db` | Сигнатури на ClamAV | `root` | — |
| `pmg.cluster_status` | PMG клъстер | `root` | — |
| `pmg.mail_stats` | Статистика за поща днес | `root` | — |
| `pmg.postfix_config` | Настройки на Postfix | `root` | — |
| `pmg.queue_list` | Писма в опашката | `root` | `lines` |
| `pmg.queue_summary` | Обобщение на опашката | `root` | — |
| `pmg.services` | Пощенски услуги | `root` | — |
| `pmg.smtp_log` | SMTP лог | `root` | `lines` |
| `pmg.version` | Версия на PMG | `root` | — |

## network

**Мрежа — VLAN, ARP, маршрути, ping, TCP, 10GbE, MTU** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/network.yaml`](../plugins/ssh-connector/snippets/network.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `network.arp_table` | ARP таблица | `user` | — |
| `network.bridge_vlans` | VLAN на мостовете | `user` | — |
| `network.dns_lookup` | Превод на име | `user` | `name` |
| `network.interfaces` | Интерфейси и адреси | `user` | — |
| `network.link_details` | Скорост и duplex (10GbE) | `user` | `iface` |
| `network.mtu_test` | Тест за MTU / jumbo frames | `user` | `host`, `size` |
| `network.nic_errors` | Броячи и грешки на мрежовата карта | `user` | `iface` |
| `network.ping` | Проверка с ping | `user` | `host`, `count` |
| `network.route_to` | Маршрут до адрес | `user` | `ip` |
| `network.routes` | Маршрути | `user` | — |
| `network.socket_summary` | Обобщение на връзките | `user` | — |
| `network.tcp_port` | Проверка на TCP порт | `user` | `host`, `port` |
| `network.trace` | Проследяване на пътя | `user` | `host` |
| `network.vlans` | VLAN интерфейси | `user` | — |

## unifi

**UniFi — диагностика само за четене на устройства и мрежови услуги** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/unifi.yaml`](../plugins/ssh-connector/snippets/unifi.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `unifi.console_info` | Информация за UniFi OS конзолата | `admin` | — |
| `unifi.device_info` | Информация за устройството | `admin` | — |
| `unifi.inform_url` | Адрес на контролера (inform URL) | `admin` | — |
| `unifi.interfaces` | Интерфейси | `admin` | — |
| `unifi.network_services` | UniFi услуги на конзолата | `admin` | — |
| `unifi.uplink_ping` | Ping от устройството | `admin` | `host` |
| `unifi.version` | Firmware и ядро | `admin` | — |

## vpn

**Tailscale и WireGuard — връзки, състояние, диагностика** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/vpn.yaml`](../plugins/ssh-connector/snippets/vpn.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `vpn.tailscale_ips` | Tailscale адреси и версия | `user` | — |
| `vpn.tailscale_netcheck` | Мрежова проверка на Tailscale | `user` | — |
| `vpn.tailscale_ping` | Ping през Tailscale | `user` | `peer`, `count` |
| `vpn.tailscale_serve` | Публикуване през Tailscale Serve / Funnel | `user` | — |
| `vpn.tailscale_status` | Състояние на Tailscale | `user` | — |
| `vpn.wireguard_handshakes` | Възраст на WireGuard handshake | `root` | — |
| `vpn.wireguard_status` | Състояние на WireGuard | `root` | — |
| `vpn.wireguard_transfer` | WireGuard трафик | `root` | — |

## security

**Киберсигурност — TLS, сертификати, SSH, логове, наблюдение** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/security.yaml`](../plugins/ssh-connector/snippets/security.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `security.certificate_file` | Локален файл със сертификат | `user` | `path` |
| `security.fail2ban` | Състояние на fail2ban | `root` | — |
| `security.failed_ssh_logins` | Неуспешни SSH влизания днес | `sudo` | `lines` |
| `security.firewall_rules_linux` | Правила на Linux firewall | `root` | — |
| `security.listening_exposure` | Услуги, слушащи на всички адреси | `sudo` | — |
| `security.smtp_starttls` | SMTP STARTTLS сертификат | `user` | `host`, `port` |
| `security.ssh_host_keys` | Отпечатъци на SSH host ключовете | `user` | — |
| `security.sshd_settings` | Настройки на SSH сървъра | `root` | — |
| `security.sudo_log` | Използване на sudo | `sudo` | `lines` |
| `security.tls_certificate` | TLS сертификат на отдалечен сървър | `user` | `host`, `port` |
| `security.tls_verify` | Проверка на TLS веригата | `user` | `host`, `port` |

## snmp

**SNMP — устройства, интерфейси, статистики (net-snmp инструменти)** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/snmp.yaml`](../plugins/ssh-connector/snippets/snmp.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `snmp.interface_counters` | 64-битови броячи на трафика | `user` | `host` |
| `snmp.interface_errors` | Грешки и изхвърлени пакети по портове | `user` | `host` |
| `snmp.interfaces` | Имена и състояние на интерфейсите | `user` | `host` |
| `snmp.local_agent` | Състояние на локалния snmpd | `user` | — |
| `snmp.system` | Системна информация за устройство | `user` | `host` |
| `snmp.walk` | Обхождане на OID поддърво | `user` | `host`, `oid` |

## backup

**Backup и възстановяване — проверка на архиви и цялост на данните** — пълни данни (ОС, параметри с граници, шаблон, очакван резултат, грешки) в: [`plugins/ssh-connector/snippets/backup.yaml`](../plugins/ssh-connector/snippets/backup.yaml)

| ID | Име | Права | Параметри |
|---|---|---|---|
| `backup.archive_test` | Проверка на tar.gz архив | `user` | `path` |
| `backup.checksum` | SHA-256 на файл | `user` | `path` |
| `backup.disk_health` | SMART състояние на диск | `root` | `device` |
| `backup.pbs_datastores` | Хранилища в Proxmox Backup Server | `root` | — |
| `backup.pve_backup_tasks` | Резултати от backup задачите в Proxmox | `root` | `count` |
| `backup.pve_local_dumps` | Локални vzdump файлове | `root` | `count` |
| `backup.verify_checksums` | Проверка на SHA256SUMS файл | `user` | `dir` |
| `backup.windows_backup_versions` | Версии в Windows Server Backup | `admin` | — |
| `backup.windows_shadow_copies` | Shadow копия (VSS) | `admin` | — |
| `backup.zfs_scrub_status` | Резултати от ZFS scrub | `root` | — |
