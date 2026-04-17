from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .auth import AuthProfile

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


@dataclass(slots=True)
class LimitWindow:
    used_percent: float | None
    window_minutes: int | None
    resets_at: int | None


@dataclass(slots=True)
class RateLimitSnapshot:
    plan_type: str | None
    primary: LimitWindow | None
    secondary: LimitWindow | None
    credits_balance: str | None
    credits_unlimited: bool
    raw: dict[str, Any]


def _window_from_payload(payload: dict[str, Any] | None) -> LimitWindow | None:
    if not payload:
        return None
    seconds = payload.get("limit_window_seconds")
    minutes = int(seconds / 60) if isinstance(seconds, (int, float)) else None
    return LimitWindow(
        used_percent=payload.get("used_percent"),
        window_minutes=minutes,
        resets_at=payload.get("reset_at"),
    )


def fetch_rate_limits(auth: AuthProfile, timeout_seconds: int = 20) -> RateLimitSnapshot:
    if auth.auth_mode == "api_key":
        raise RuntimeError("API key auth does not expose ChatGPT Codex rate limits.")
    if not auth.access_token:
        raise RuntimeError("Access token is missing in auth.json.")

    headers = {
        "Authorization": f"Bearer {auth.access_token}",
        "User-Agent": "codex-auth-switcher",
    }
    if auth.account_id:
        headers["ChatGPT-Account-Id"] = auth.account_id

    request = urllib.request.Request(USAGE_URL, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error

    rate_limit = payload.get("rate_limit") or {}
    credits = payload.get("credits") or {}
    return RateLimitSnapshot(
        plan_type=payload.get("plan_type"),
        primary=_window_from_payload(rate_limit.get("primary_window")),
        secondary=_window_from_payload(rate_limit.get("secondary_window")),
        credits_balance=credits.get("balance"),
        credits_unlimited=bool(credits.get("unlimited")),
        raw=payload,
    )


def format_reset_timestamp(value: int | None) -> str:
    if not value:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def display_limit_percent(used_percent: float | None) -> float | None:
    if not isinstance(used_percent, (int, float)):
        return None
    return max(0.0, min(100.0, 100.0 - float(used_percent)))
