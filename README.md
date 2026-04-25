# Codex Auth Switcher

Локальное Linux-приложение для быстрого переключения аккаунтов Codex через подмену активного `auth.json`.

Что умеет:

- хранить несколько аккаунтов как отдельные копии `auth.json`
- быстро активировать выбранный аккаунт
- добавлять аккаунт из текущего активного `auth.json`
- импортировать аккаунт из любого другого `auth.json`
- удалять сохранённые аккаунты
- показывать актуальные rate limits Codex для выбранного аккаунта
- запускаться как tray-приложение в верхней панели Linux
- показывать в tray-меню все добавленные аккаунты и текущий активный аккаунт
- переключать аккаунты прямо из tray-меню
- добавлять новые аккаунты прямо из tray-меню

Приложение не меняет код внутри `codex-main`. Оно использует:

- активный файл авторизации `~/.codex/auth.json` по умолчанию
- прямой запрос к `https://chatgpt.com/backend-api/wham/usage`, совместимый с тем, как Codex получает usage/rate limits

## Запуск

```bash
cd codex-auth-switcher
python3 app.py
```

## Запуск в терминале

```bash
cd codex-auth-switcher
python3 cli.py --help
```

Интерактивный TUI:

```bash
python3 tui.py
```

или:

```bash
python3 cli.py tui
```

Клавиши в TUI:

- `↑` / `↓` или `k` / `j` — выбрать аккаунт
- `Enter` — сделать выбранный аккаунт активным
- `l` — запустить `codex login`, сохранить новый активный аккаунт и выбрать его
- `r` — обновить лимиты активного аккаунта
- `R` — обновить лимиты активного аккаунта
- `u` — перечитать список аккаунтов с диска
- `q` или `Esc` — выйти

TUI автоматически опрашивает только активный аккаунт. Для неактивных аккаунтов
показывается последний сохранённый кеш лимитов из
`~/.local/share/codex-auth-switcher/limits_cache.json`.
По умолчанию статус активного аккаунта обновляется раз в минуту.
Если активный `auth.json` изменился извне, TUI автоматически добавит новый
аккаунт в базу и выберет его в списке.

Основные команды:

```bash
python3 cli.py list
python3 cli.py current
python3 cli.py login
python3 cli.py add-current --name personal
python3 cli.py import /path/to/auth.json --name work
python3 cli.py activate personal
python3 cli.py limits personal
python3 cli.py rename personal main
python3 cli.py delete main
python3 cli.py storage
```

В командах `activate`, `limits`, `rename` и `delete` можно указывать полный id,
короткий префикс id или точное имя аккаунта.

## Запуск в трее

```bash
cd codex-auth-switcher
python3 tray_app.py
```

## Установка ярлыка в Linux

```bash
cd codex-auth-switcher
./install_linux_shortcut.sh
```

После этого приложение появится в меню приложений как `Codex Auth Switcher`.

Ярлык теперь запускает именно tray-приложение. Основное окно менеджера открывается из tray-меню пунктом `Открыть менеджер`.

## Где хранятся данные

- Активный auth: `~/.codex/auth.json`
- База приложения: `~/.local/share/codex-auth-switcher`
- Сохранённые аккаунты: `~/.local/share/codex-auth-switcher/accounts/<id>/auth.json`
- Метаданные: `~/.local/share/codex-auth-switcher/accounts.json`

## Примечания

- Если у тебя используется нестандартный путь к активному auth, можно задать `CODEX_AUTH_PATH`.
- При активации аккаунта запись выполняется атомарно, а права на файл выставляются как `600`.
- Лимиты читаются напрямую для конкретного сохранённого аккаунта, поэтому их можно смотреть ещё до переключения на него.
