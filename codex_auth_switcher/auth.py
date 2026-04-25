from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuthProfile:
    auth_mode: str | None
    email: str | None
    plan_type: str | None
    account_id: str | None
    user_id: str | None
    access_token: str | None
    organization_ids: tuple[str, ...]
    organization_titles: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def fingerprint(self) -> str:
        parts = [
            self.user_id or "",
            self.account_id or "",
            self.email or "",
            self.plan_type or "",
            "|".join(self.organization_ids),
            "|".join(self.organization_titles),
        ]
        key = "||".join(parts)
        if not key.strip("|"):
            key = self.access_token or json.dumps(self.raw, sort_keys=True)
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @property
    def display_name(self) -> str:
        if self.email:
            local_part = self.email.split("@", 1)[0].strip()
            return local_part or self.email
        if self.account_id:
            return f"Account {self.account_id[:8]}"
        return "Unknown account"

    @property
    def organization_label(self) -> str | None:
        if self.organization_titles:
            return ", ".join(self.organization_titles)
        if self.organization_ids:
            return ", ".join(self.organization_ids)
        return None


def _pad_base64url(value: str) -> str:
    missing = len(value) % 4
    if missing:
        value += "=" * (4 - missing)
    return value


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    try:
        decoded = base64.urlsafe_b64decode(_pad_base64url(payload))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def parse_auth_payload(raw: dict[str, Any]) -> AuthProfile:
    tokens = raw.get("tokens") or {}
    id_token = tokens.get("id_token")
    claims = _decode_jwt_payload(id_token) if isinstance(id_token, str) else {}
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    profile_claims = claims.get("https://api.openai.com/profile") or {}
    email = claims.get("email") or profile_claims.get("email")
    organizations = auth_claims.get("organizations")
    organization_ids: list[str] = []
    organization_titles: list[str] = []
    if isinstance(organizations, list):
        for organization in organizations:
            if not isinstance(organization, dict):
                continue
            org_id = organization.get("id")
            org_title = organization.get("title")
            if isinstance(org_id, str) and org_id:
                organization_ids.append(org_id)
            if isinstance(org_title, str) and org_title:
                organization_titles.append(org_title)
    return AuthProfile(
        auth_mode=raw.get("auth_mode"),
        email=email,
        plan_type=auth_claims.get("chatgpt_plan_type"),
        account_id=tokens.get("account_id") or auth_claims.get("chatgpt_account_id"),
        user_id=auth_claims.get("chatgpt_user_id") or auth_claims.get("user_id"),
        access_token=tokens.get("access_token") or raw.get("OPENAI_API_KEY"),
        organization_ids=tuple(organization_ids),
        organization_titles=tuple(organization_titles),
        raw=raw,
    )


def load_auth_file(path: Path) -> AuthProfile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return parse_auth_payload(raw)


def dump_auth_file(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(raw, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    temp_path.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
