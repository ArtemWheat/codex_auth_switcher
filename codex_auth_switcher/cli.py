from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .auth import load_auth_file
from .constants import ACCOUNTS_DB_PATH, APP_STORAGE_DIR, APP_VERSION
from .manager import AccountManager
from .rate_limits import (
    LimitWindow,
    RateLimitSnapshot,
    display_limit_percent,
    fetch_rate_limits,
    format_reset_timestamp,
)
from .storage import StoredAccount


class CliError(RuntimeError):
    pass


def _short(value: str | None, head: int = 8, tail: int = 4) -> str:
    if not value:
        return "-"
    if len(value) <= head + tail + 1:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def _account_line(account: StoredAccount, active_fingerprint: str | None) -> str:
    active = "*" if account.fingerprint == active_fingerprint else " "
    plan = account.auth.plan_type or "-"
    email = account.auth.email or "-"
    return f"{active} {account.id[:8]}  {account.name}  <{email}>  plan={plan}"


def _find_account(manager: AccountManager, selector: str) -> StoredAccount:
    accounts = manager.store.list_accounts()
    exact_matches = [account for account in accounts if account.id == selector]
    if exact_matches:
        return exact_matches[0]

    id_matches = [account for account in accounts if account.id.startswith(selector)]
    if len(id_matches) == 1:
        return id_matches[0]
    if len(id_matches) > 1:
        raise CliError(f"Ambiguous account id prefix: {selector}")

    name_matches = [
        account for account in accounts if account.name.casefold() == selector.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise CliError(f"Ambiguous account name: {selector}")

    raise CliError(f"Account not found: {selector}")


def _print_accounts(accounts: list[StoredAccount], active_fingerprint: str | None) -> None:
    if not accounts:
        print("No accounts saved.")
        return
    for account in accounts:
        print(_account_line(account, active_fingerprint))


def _print_current(manager: AccountManager) -> None:
    snapshot = manager.snapshot()
    print(f"Active auth: {snapshot.active_auth_path}")
    if snapshot.current_account is not None:
        print(_account_line(snapshot.current_account, snapshot.active_fingerprint))
        return
    if snapshot.active_fingerprint:
        print(f"Current account is not saved. fingerprint={_short(snapshot.active_fingerprint)}")
    else:
        print("No active auth account detected.")


def _format_window(window: LimitWindow | None) -> str:
    if window is None:
        return "no data"
    available = display_limit_percent(window.used_percent)
    available_text = f"{available:.1f}% available" if available is not None else "unknown"
    window_text = f"{window.window_minutes} min" if window.window_minutes else "unknown window"
    reset_text = format_reset_timestamp(window.resets_at)
    return f"{available_text}, {window_text}, resets {reset_text}"


def _print_limits(snapshot: RateLimitSnapshot) -> None:
    print(f"Plan: {snapshot.plan_type or '-'}")
    print(f"Primary: {_format_window(snapshot.primary)}")
    print(f"Secondary: {_format_window(snapshot.secondary)}")
    if snapshot.credits_unlimited:
        credits = "unlimited"
    else:
        credits = snapshot.credits_balance or "no credits"
    print(f"Credits: {credits}")


def _resolve_codex_bin(explicit_path: str | None = None) -> str:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.is_file():
            return str(path)
        raise CliError(f"Codex binary not found: {path}")
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise CliError("Command 'codex' was not found in PATH.")
    return codex_bin


def _cmd_list(args: argparse.Namespace) -> int:
    manager = AccountManager()
    snapshot = manager.snapshot()
    if args.current:
        _print_current(manager)
    else:
        _print_accounts(snapshot.accounts, snapshot.active_fingerprint)
    return 0


def _cmd_current(_args: argparse.Namespace) -> int:
    _print_current(AccountManager())
    return 0


def _cmd_activate(args: argparse.Namespace) -> int:
    manager = AccountManager()
    account = _find_account(manager, args.account)
    manager.activate_account(account.id)
    print(f"Activated: {account.name}")
    return 0


def _cmd_add_current(args: argparse.Namespace) -> int:
    manager = AccountManager()
    if not manager.active_auth_path.exists():
        raise CliError(f"Active auth file not found: {manager.active_auth_path}")
    account = manager.add_current_account(name=args.name)
    print(f"Saved: {account.name} ({account.id[:8]})")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    account = AccountManager().import_auth_file(path, name=args.name)
    print(f"Imported: {account.name} ({account.id[:8]})")
    return 0


def _cmd_rename(args: argparse.Namespace) -> int:
    manager = AccountManager()
    account = _find_account(manager, args.account)
    manager.store.rename(account.id, args.name)
    print(f"Renamed: {account.name} -> {args.name}")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    manager = AccountManager()
    account = _find_account(manager, args.account)
    if not args.yes:
        answer = input(f"Delete '{account.name}' from storage? [y/N] ").strip().casefold()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return 1
    manager.store.delete(account.id)
    print(f"Deleted: {account.name}")
    return 0


def _cmd_limits(args: argparse.Namespace) -> int:
    manager = AccountManager()
    if args.account:
        account = _find_account(manager, args.account)
    else:
        snapshot = manager.snapshot()
        if snapshot.current_account is None:
            raise CliError("No saved active account. Pass an account id/name explicitly.")
        account = snapshot.current_account
    print(f"Fetching limits for: {account.name}")
    _print_limits(fetch_rate_limits(account.auth, timeout_seconds=args.timeout))
    return 0


def _cmd_storage(_args: argparse.Namespace) -> int:
    manager = AccountManager()
    print(f"Active auth: {manager.active_auth_path}")
    print(f"Storage: {APP_STORAGE_DIR}")
    print(f"Accounts DB: {ACCOUNTS_DB_PATH}")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    manager = AccountManager()
    before_fingerprint: str | None = None
    try:
        before_fingerprint = load_auth_file(manager.active_auth_path).fingerprint
    except Exception:
        pass

    codex_bin = _resolve_codex_bin(args.codex_bin)
    result = subprocess.run([codex_bin, "login"], check=False)
    if result.returncode != 0:
        raise CliError(f"codex login failed with exit code {result.returncode}")

    try:
        profile = load_auth_file(manager.active_auth_path)
    except Exception as error:
        raise CliError(f"Login finished, but auth.json could not be read: {error}") from error

    account = manager.add_current_account(name=args.name)
    if profile.fingerprint == before_fingerprint:
        print(f"Auth did not change. Saved account: {account.name} ({account.id[:8]})")
    else:
        print(f"Logged in and saved: {account.name} ({account.id[:8]})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-auth-switcher",
        description="Terminal account switcher for Codex auth.json files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", aliases=["ls"], help="List saved accounts")
    list_parser.add_argument("--current", action="store_true", help="Show only active account")
    list_parser.set_defaults(func=_cmd_list)

    current_parser = subparsers.add_parser("current", help="Show active auth account")
    current_parser.set_defaults(func=_cmd_current)

    activate_parser = subparsers.add_parser("activate", aliases=["use"], help="Activate account")
    activate_parser.add_argument("account", help="Account id prefix or exact name")
    activate_parser.set_defaults(func=_cmd_activate)

    add_parser = subparsers.add_parser("add-current", help="Save current active auth.json")
    add_parser.add_argument("-n", "--name", help="Saved account name")
    add_parser.set_defaults(func=_cmd_add_current)

    import_parser = subparsers.add_parser("import", help="Import account from auth.json")
    import_parser.add_argument("path", help="Path to auth.json")
    import_parser.add_argument("-n", "--name", help="Saved account name")
    import_parser.set_defaults(func=_cmd_import)

    rename_parser = subparsers.add_parser("rename", help="Rename saved account")
    rename_parser.add_argument("account", help="Account id prefix or exact name")
    rename_parser.add_argument("name", help="New account name")
    rename_parser.set_defaults(func=_cmd_rename)

    delete_parser = subparsers.add_parser("delete", aliases=["rm"], help="Delete saved account")
    delete_parser.add_argument("account", help="Account id prefix or exact name")
    delete_parser.add_argument("-y", "--yes", action="store_true", help="Do not ask")
    delete_parser.set_defaults(func=_cmd_delete)

    limits_parser = subparsers.add_parser("limits", help="Fetch Codex rate limits")
    limits_parser.add_argument("account", nargs="?", help="Account id prefix or exact name")
    limits_parser.add_argument("--timeout", type=int, default=20, help="Request timeout seconds")
    limits_parser.set_defaults(func=_cmd_limits)

    login_parser = subparsers.add_parser("login", help="Run 'codex login' and save the result")
    login_parser.add_argument("-n", "--name", help="Saved account name")
    login_parser.add_argument("--codex-bin", help="Path to codex executable")
    login_parser.set_defaults(func=_cmd_login)

    storage_parser = subparsers.add_parser("storage", help="Print storage paths")
    storage_parser.set_defaults(func=_cmd_storage)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except KeyError as error:
        print(f"Error: account not found: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
