from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth import AuthProfile, dump_auth_file, load_auth_file, now_iso
from .constants import ACCOUNTS_DB_PATH, ACCOUNTS_DIR


@dataclass(slots=True)
class StoredAccount:
    id: str
    name: str
    created_at: str
    updated_at: str
    auth_path: Path
    fingerprint: str
    auth: AuthProfile


class AccountStore:
    def __init__(self) -> None:
        ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    def _read_db(self) -> list[dict[str, Any]]:
        if not ACCOUNTS_DB_PATH.exists():
            return []
        return json.loads(ACCOUNTS_DB_PATH.read_text(encoding="utf-8"))

    def _write_db(self, rows: list[dict[str, Any]]) -> None:
        ACCOUNTS_DB_PATH.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def list_accounts(self) -> list[StoredAccount]:
        accounts: list[StoredAccount] = []
        for row in self._read_db():
            auth_path = Path(row["auth_path"])
            if not auth_path.exists():
                continue
            auth = load_auth_file(auth_path)
            accounts.append(
                StoredAccount(
                    id=row["id"],
                    name=row["name"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    auth_path=auth_path,
                    fingerprint=auth.fingerprint,
                    auth=auth,
                )
            )
        accounts.sort(key=lambda item: item.name.lower())
        return accounts

    def upsert_from_file(self, source_path: Path, name: str | None = None) -> StoredAccount:
        auth = load_auth_file(source_path)
        existing = self.find_by_fingerprint(auth.fingerprint)
        if existing is not None:
            self._sync_record(existing.id, auth.fingerprint)
            if name and name != existing.name:
                self.rename(existing.id, name)
                existing.name = name
            return existing

        account_id = uuid.uuid4().hex
        target_dir = ACCOUNTS_DIR / account_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "auth.json"
        dump_auth_file(target_path, auth.raw)
        record = {
            "id": account_id,
            "name": name or auth.display_name,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "auth_path": str(target_path),
            "fingerprint": auth.fingerprint,
        }
        rows = self._read_db()
        rows.append(record)
        self._write_db(rows)
        return self.get(account_id)

    def get(self, account_id: str) -> StoredAccount:
        for account in self.list_accounts():
            if account.id == account_id:
                return account
        raise KeyError(account_id)

    def find_by_fingerprint(self, fingerprint: str) -> StoredAccount | None:
        for account in self.list_accounts():
            if account.fingerprint == fingerprint:
                return account
        return None

    def _sync_record(self, account_id: str, fingerprint: str) -> None:
        rows = self._read_db()
        changed = False
        for row in rows:
            if row["id"] != account_id:
                continue
            if row.get("fingerprint") != fingerprint:
                row["fingerprint"] = fingerprint
                row["updated_at"] = now_iso()
                changed = True
            break
        if changed:
            self._write_db(rows)

    def rename(self, account_id: str, name: str) -> None:
        rows = self._read_db()
        for row in rows:
            if row["id"] == account_id:
                row["name"] = name
                row["updated_at"] = now_iso()
                self._write_db(rows)
                return
        raise KeyError(account_id)

    def delete(self, account_id: str) -> None:
        rows = self._read_db()
        remaining: list[dict[str, Any]] = []
        target_path: Path | None = None
        for row in rows:
            if row["id"] == account_id:
                target_path = Path(row["auth_path"])
                continue
            remaining.append(row)
        self._write_db(remaining)
        if target_path is not None and target_path.parent.exists():
            shutil.rmtree(target_path.parent, ignore_errors=True)
