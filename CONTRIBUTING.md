# Contributing / Участие

**English** · [Български](#български)

Thank you for helping! / Благодарим, че помагате!

## English
1. **Ideas and bugs** — open an issue: [💡 Feature request](https://github.com/ykinfohome7/KSSH-FOR-Friends/issues/new?template=feature_request.yml) or [🐞 Bug report](https://github.com/ykinfohome7/KSSH-FOR-Friends/issues/new?template=bug_report.yml).
2. **Code** — fork, create a branch, open a pull request to `main`.
3. **Before the pull request**
   ```bash
   pip install -r requirements.txt
   python tests/test_profiles.py
   python tests/test_sudo_password_mock.py
   python tests/test_v13_mock.py
   python tests/test_plugins.py
   python scripts/gen_commands_docs.py        # after changing a command
   ```
4. **Rules**
   - Never commit passwords, keys, tokens, IP addresses of private servers or `servers.yaml`.
   - Code comments in Bulgarian (what each line does); docs in English with short Bulgarian notes.
   - Every new command: an entry in `HELP` (BG + EN + ready example) and a mock test.
   - Bump the version in `VERSION` and both `plugin.json` files; add a line to `CHANGELOG.md`.

## Български
1. **Идеи и грешки** — отвори issue: [💡 Искам нова функция](https://github.com/ykinfohome7/KSSH-FOR-Friends/issues/new?template=feature_request.yml) или [🐞 Има грешка](https://github.com/ykinfohome7/KSSH-FOR-Friends/issues/new?template=bug_report.yml).
2. **Код** — fork, нов клон, pull request към `main`.
3. **Преди pull request** — пусни тестовете отгоре; след промяна на команда — `python scripts/gen_commands_docs.py`.
4. **Правила**
   - Никога не качвай пароли, ключове, токени, IP адреси на частни сървъри или `servers.yaml`.
   - Коментарите в кода — на български (какво прави всеки ред); документацията — на английски с кратки бележки на български.
   - Всяка нова команда: запис в `HELP` (БГ + EN + готов пример) и макетен тест.
   - Вдигни версията във `VERSION` и в двата `plugin.json`; добави ред в `CHANGELOG.md`.
