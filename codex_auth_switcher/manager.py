from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .auth import dump_auth_file, load_auth_file
from .constants import resolve_active_auth_path
from .storage import AccountStore, StoredAccount


@dataclass(slots=True)
class AccountSnapshot:
    active_auth_path: Path
    active_fingerprint: str | None
    current_account: StoredAccount | None
    accounts: list[StoredAccount]


class AccountManager:
    def __init__(self) -> None:
        self.store = AccountStore()
        self.active_auth_path = resolve_active_auth_path()

    def get_active_fingerprint(self) -> str | None:
        if not self.active_auth_path.exists():
            return None
        try:
            return load_auth_file(self.active_auth_path).fingerprint
        except Exception:
            return None

    def snapshot(self) -> AccountSnapshot:
        accounts = self.store.list_accounts()
        active_fingerprint = self.get_active_fingerprint()
        current_account = None
        if active_fingerprint:
            for account in accounts:
                if account.fingerprint == active_fingerprint:
                    current_account = account
                    break
        return AccountSnapshot(
            active_auth_path=self.active_auth_path,
            active_fingerprint=active_fingerprint,
            current_account=current_account,
            accounts=accounts,
        )

    def activate_account(self, account_id: str) -> StoredAccount:
        account = self.store.get(account_id)
        dump_auth_file(self.active_auth_path, account.auth.raw)
        return account

    def add_current_account(self, name: str | None = None) -> StoredAccount:
        return self.store.upsert_from_file(self.active_auth_path, name=name)

    def import_auth_file(self, source_path: Path, name: str | None = None) -> StoredAccount:
        return self.store.upsert_from_file(source_path, name=name)
