from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Codex Auth Switcher"
APP_VERSION = "1.0.0"
APP_STORAGE_DIR = Path.home() / ".local" / "share" / "codex-auth-switcher"
ACCOUNTS_DIR = APP_STORAGE_DIR / "accounts"
ACCOUNTS_DB_PATH = APP_STORAGE_DIR / "accounts.json"
ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "icon.svg"
ICON_NAME = "codex-auth-switcher"

DEFAULT_AUTH_PATH = Path.home() / ".codex" / "auth.json"
LEGACY_AUTH_PATH = Path.home() / "codex" / "auth.json"


def resolve_active_auth_path() -> Path:
    env_path = os.environ.get("CODEX_AUTH_PATH")
    if env_path:
        return Path(env_path).expanduser()
    if DEFAULT_AUTH_PATH.exists():
        return DEFAULT_AUTH_PATH
    if LEGACY_AUTH_PATH.exists():
        return LEGACY_AUTH_PATH
    return DEFAULT_AUTH_PATH
