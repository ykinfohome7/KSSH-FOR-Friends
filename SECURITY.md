# Security policy / Политика за сигурност

**English** · [Български](#български)

## English
### Reporting a vulnerability
Please **do not** open a public issue. Use **[GitHub private vulnerability reporting](https://github.com/ykinfohome7/KSSH-FOR-Friends/security/advisories/new)**.
Include: affected version (`version_info`), steps to reproduce, impact. Never include real passwords, tokens or keys.
We answer within **7 days**.

### Supported versions
| Version | Supported |
|---|---|
| 1.3.x | ✅ |
| < 1.3 | ❌ |

### How the connector protects secrets
- Passwords, keys and tokens are never returned by commands or written to files in the repository — only environment-variable **names** and paths.
- The sudo password goes to `sudo` over stdin; one-time pages expire in 2 minutes and are single-use.
- HTTP mode requires a token (24+ characters); requests without it get `401`.
- SSH host keys are verified (`known_hosts`). Full standard: [docs/SECURITY-STANDARDS.md](docs/SECURITY-STANDARDS.md).

## Български
### Съобщаване за уязвимост
Моля, **не** отваряй публичен issue. Използвай **[частно съобщение в GitHub](https://github.com/ykinfohome7/KSSH-FOR-Friends/security/advisories/new)**.
Посочи: версия (`version_info`), стъпки за повторение, какво засяга. Никога не прилагай истински пароли, токени или ключове.
Отговаряме до **7 дни**.

### Поддържани версии
1.3.x — ✅ · по-стари — ❌

### Как конекторът пази тайните
- Пароли, ключове и токени никога не се връщат от командите и не се записват във файлове в хранилището — само **имена** на променливи и пътища.
- Паролата за sudo отива към `sudo` през stdin; еднократните страници изтичат след 2 минути и важат веднъж.
- HTTP режимът изисква токен (24+ знака); без него — `401`.
- SSH ключовете на сървърите се проверяват (`known_hosts`). Пълен стандарт: [docs/SECURITY-STANDARDS.bg.md](docs/SECURITY-STANDARDS.bg.md).
