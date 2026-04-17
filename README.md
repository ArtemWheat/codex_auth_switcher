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
cd /home/maxim/Projects/codex_authquick/codex-auth-switcher
python3 app.py
```

## Запуск в трее

```bash
cd /home/maxim/Projects/codex_authquick/codex-auth-switcher
python3 tray_app.py
```

## Установка ярлыка в Linux

```bash
cd /home/maxim/Projects/codex_authquick/codex-auth-switcher
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
# codex_auth_switcher
