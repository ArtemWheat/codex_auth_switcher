from __future__ import annotations

import argparse
import curses
import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from .constants import APP_NAME, APP_STORAGE_DIR, APP_VERSION
from .manager import AccountManager
from .rate_limits import (
    LimitWindow,
    RateLimitSnapshot,
    display_limit_percent,
    fetch_rate_limits,
    format_reset_timestamp,
)
from .storage import StoredAccount


PAIR_DEFAULT = 1
PAIR_ACTIVE = 2
PAIR_GOOD = 3
PAIR_WARN = 4
PAIR_BAD = 5
PAIR_MUTED = 6
PAIR_HEADER = 7
LIMITS_CACHE_PATH = APP_STORAGE_DIR / "limits_cache.json"


@dataclass(slots=True)
class LimitState:
    status: str
    fetched_at: float | None = None
    snapshot: RateLimitSnapshot | None = None
    error: str | None = None
    refreshing: bool = False
    cached: bool = False


class TuiApp:
    def __init__(self, refresh_interval: int = 60, timeout_seconds: int = 10) -> None:
        self.manager = AccountManager()
        self.refresh_interval = refresh_interval
        self.timeout_seconds = timeout_seconds
        self.accounts: list[StoredAccount] = []
        self.active_fingerprint: str | None = None
        self.selected_index = 0
        self.scroll_offset = 0
        self.status = "Loading accounts..."
        self.limit_states: dict[str, LimitState] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self._last_auto_refresh = 0.0
        self._last_auth_check = 0.0
        self._load_limit_cache()

    def run(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.nodelay(True)
        stdscr.timeout(200)
        self._init_colors()
        self.reload_accounts()
        self.refresh_active(force=True)

        while not self.stop_event.is_set():
            self._maybe_detect_active_account()
            self._maybe_auto_refresh()
            self.draw(stdscr)
            key = stdscr.getch()
            if key == -1:
                continue
            if key in (ord("q"), ord("Q"), 27):
                self.stop_event.set()
            elif key in (curses.KEY_UP, ord("k")):
                self.move_selection(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.move_selection(1)
            elif key == curses.KEY_PPAGE:
                self.move_selection(-10)
            elif key == curses.KEY_NPAGE:
                self.move_selection(10)
            elif key in (curses.KEY_HOME, ord("g")):
                self.selected_index = 0
            elif key in (curses.KEY_END, ord("G")):
                self.selected_index = max(0, len(self.accounts) - 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                self.activate_selected()
            elif key == ord("r"):
                self.refresh_active(force=True)
            elif key == ord("R"):
                self.refresh_active(force=True)
            elif key == ord("u"):
                self.reload_accounts()
            elif key in (ord("l"), ord("L")):
                self.login_via_codex(stdscr)

    def _init_colors(self) -> None:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(PAIR_DEFAULT, curses.COLOR_WHITE, -1)
        curses.init_pair(PAIR_ACTIVE, curses.COLOR_CYAN, -1)
        curses.init_pair(PAIR_GOOD, curses.COLOR_GREEN, -1)
        curses.init_pair(PAIR_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(PAIR_BAD, curses.COLOR_RED, -1)
        curses.init_pair(PAIR_MUTED, curses.COLOR_BLUE, -1)
        curses.init_pair(PAIR_HEADER, curses.COLOR_BLACK, curses.COLOR_CYAN)

    def reload_accounts(
        self,
        select_account_id: str | None = None,
        select_active: bool = False,
        status: str | None = None,
    ) -> None:
        previous_id = self.selected_account.id if self.selected_account else None
        snapshot = self.manager.snapshot()
        self.accounts = snapshot.accounts
        self.active_fingerprint = snapshot.active_fingerprint
        with self.lock:
            for account in self.accounts:
                self.limit_states.setdefault(account.id, LimitState(status="empty"))
        target_id = select_account_id
        if target_id is None and select_active:
            for account in self.accounts:
                if account.fingerprint == self.active_fingerprint:
                    target_id = account.id
                    break
        if target_id is None:
            target_id = previous_id
        if target_id:
            for index, account in enumerate(self.accounts):
                if account.id == target_id:
                    self.selected_index = index
                    break
        self.selected_index = min(self.selected_index, max(0, len(self.accounts) - 1))
        self.status = status or f"Loaded {len(self.accounts)} account(s)"

    @property
    def selected_account(self) -> StoredAccount | None:
        if not self.accounts:
            return None
        if self.selected_index < 0 or self.selected_index >= len(self.accounts):
            return None
        return self.accounts[self.selected_index]

    def move_selection(self, delta: int) -> None:
        if not self.accounts:
            return
        self.selected_index = max(0, min(len(self.accounts) - 1, self.selected_index + delta))

    def activate_selected(self) -> None:
        account = self.selected_account
        if account is None:
            return
        try:
            self.manager.activate_account(account.id)
            self.reload_accounts()
            self.status = f"Activated: {account.name}"
            self.refresh_active(force=True)
        except Exception as error:
            self.status = f"Activation failed: {error}"

    def login_via_codex(self, stdscr) -> None:
        codex_bin = shutil.which("codex")
        if not codex_bin:
            self.status = "Command 'codex' was not found in PATH"
            return

        curses.def_prog_mode()
        curses.endwin()
        print("Starting `codex login`.")
        print("After the browser/login flow finishes, this TUI will save the active account.")
        result = subprocess.run([codex_bin, "login"], check=False)
        if result.returncode != 0:
            print(f"`codex login` failed with exit code {result.returncode}.")
            input("Press Enter to return to TUI...")
            curses.reset_prog_mode()
            curses.curs_set(0)
            stdscr.keypad(True)
            stdscr.clear()
            self.status = f"codex login failed with exit code {result.returncode}"
            return

        try:
            stored = self.manager.add_current_account()
            message = f"Logged in and saved: {stored.name}"
        except Exception as error:
            message = f"Login finished, but account was not saved: {error}"

        print(message)
        input("Press Enter to return to TUI...")
        curses.reset_prog_mode()
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.clear()
        self.reload_accounts(select_active=True, status=message)
        self.refresh_active(force=True)

    def active_account(self) -> StoredAccount | None:
        for account in self.accounts:
            if account.fingerprint == self.active_fingerprint:
                return account
        return None

    def refresh_active(self, force: bool = False) -> None:
        account = self.active_account()
        if account is None:
            self.status = "No saved active account to refresh"
            return
        self.refresh_account(account, force=force)

    def refresh_account(self, account: StoredAccount, force: bool = False) -> None:
        now = time.time()
        with self.lock:
            state = self.limit_states.get(account.id)
            if state is None:
                state = LimitState(status="loading")
                self.limit_states[account.id] = state
            if account.fingerprint != self.active_fingerprint:
                self.status = "Only active account is refreshed; inactive rows use cache"
                return
            if state.refreshing:
                return
            if not force and state.fetched_at and now - state.fetched_at < self.refresh_interval:
                return
            state.refreshing = True
            state.status = "loading" if state.snapshot is None else state.status

        thread = threading.Thread(
            target=self._fetch_limits_worker,
            args=(account.id,),
            name=f"tui-limits-{account.id[:8]}",
            daemon=True,
        )
        thread.start()

    def _fetch_limits_worker(self, account_id: str) -> None:
        try:
            account = self.manager.store.get(account_id)
            snapshot = fetch_rate_limits(account.auth, timeout_seconds=self.timeout_seconds)
        except Exception as error:
            with self.lock:
                previous = self.limit_states.get(account_id)
                if previous and previous.snapshot is not None:
                    previous.error = str(error)
                    previous.refreshing = False
                    previous.cached = True
                    self.limit_states[account_id] = previous
                    self.status = "Refresh failed; showing cached limits"
                else:
                    self.limit_states[account_id] = LimitState(
                        status="error",
                        fetched_at=time.time(),
                        error=str(error),
                        refreshing=False,
                    )
                    self.status = "Refresh failed"
            return

        fetched_at = time.time()
        with self.lock:
            self.limit_states[account_id] = LimitState(
                status="ready",
                fetched_at=fetched_at,
                snapshot=snapshot,
                refreshing=False,
            )
            self.status = "Active limits updated"
            self._save_limit_cache_locked()

    def _maybe_auto_refresh(self) -> None:
        now = time.time()
        if now - self._last_auto_refresh < 5:
            return
        self._last_auto_refresh = now
        self.refresh_active(force=False)

    def _maybe_detect_active_account(self) -> None:
        now = time.time()
        if now - self._last_auth_check < 2:
            return
        self._last_auth_check = now
        fingerprint = self.manager.get_active_fingerprint()
        if fingerprint == self.active_fingerprint and self.active_account() is not None:
            return
        if not fingerprint:
            self.reload_accounts(select_active=True, status="Active auth disappeared")
            return

        existing = self.manager.store.find_by_fingerprint(fingerprint)
        if existing is None:
            try:
                stored = self.manager.add_current_account()
            except Exception as error:
                self.reload_accounts(status=f"Detected new active auth, but save failed: {error}")
                return
            self.reload_accounts(
                select_account_id=stored.id,
                status=f"Detected and saved new active account: {stored.name}",
            )
            self.refresh_active(force=True)
            return

        self.reload_accounts(
            select_account_id=existing.id,
            status=f"Detected active account switch: {existing.name}",
        )
        self.refresh_active(force=True)

    def draw(self, stdscr) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 12 or width < 70:
            self._addstr(stdscr, 0, 0, "Terminal is too small. Need at least 70x12.", PAIR_BAD)
            stdscr.refresh()
            return

        self._draw_header(stdscr, width)
        list_top = 3
        detail_height = 7
        list_height = max(1, height - list_top - detail_height - 1)
        self._draw_list(stdscr, list_top, list_height, width)
        self._draw_detail(stdscr, height - detail_height, detail_height, width)
        self._draw_footer(stdscr, height - 1, width)
        stdscr.refresh()

    def _draw_header(self, stdscr, width: int) -> None:
        title = f" {APP_NAME} TUI "
        self._addstr(stdscr, 0, 0, title.ljust(width), PAIR_HEADER)
        active = self._active_label()
        self._addstr(stdscr, 1, 0, self._fit(active, width), PAIR_MUTED)
        headings = "   ID        Name                    Plan   Primary                          Secondary"
        self._addstr(stdscr, 2, 0, self._fit(headings, width), PAIR_MUTED)

    def _draw_list(self, stdscr, top: int, height: int, width: int) -> None:
        if not self.accounts:
            self._addstr(stdscr, top, 2, "No accounts saved. Use `python3 cli.py login` first.", PAIR_WARN)
            return
        self._keep_selection_visible(height)
        visible = self.accounts[self.scroll_offset : self.scroll_offset + height]
        for row, account in enumerate(visible):
            index = self.scroll_offset + row
            is_selected = index == self.selected_index
            is_active = account.fingerprint == self.active_fingerprint
            marker = ">" if is_selected else " "
            active = "*" if is_active else " "
            plan = account.auth.plan_type or "-"
            state = self._display_state(account)
            primary = self._window_summary(state, "primary")
            secondary = self._window_summary(state, "secondary")
            line = (
                f"{marker}{active} {account.id[:8]}  "
                f"{self._fit(account.name, 22):22}  "
                f"{self._fit(plan, 5):5}  "
                f"{self._fit(primary, 31):31}  "
                f"{secondary}"
            )
            pair = self._state_pair(state)
            attr = curses.color_pair(pair)
            if is_selected:
                attr |= curses.A_REVERSE
            if is_active:
                attr |= curses.A_BOLD
            self._addstr_attr(stdscr, top + row, 0, self._fit(line, width), attr)

    def _draw_detail(self, stdscr, top: int, height: int, width: int) -> None:
        for offset in range(height - 1):
            self._addstr(stdscr, top + offset, 0, " ".ljust(width), PAIR_DEFAULT)
        account = self.selected_account
        if account is None:
            return
        state = self._display_state(account)
        self._addstr(stdscr, top, 0, self._fit("-" * width, width), PAIR_MUTED)
        name = f"{account.name}  <{account.auth.email or '-'}>  id={account.id}"
        self._addstr(stdscr, top + 1, 0, self._fit(name, width), PAIR_DEFAULT)
        plan = self._snapshot_plan(state) or account.auth.plan_type or "-"
        self._addstr(stdscr, top + 2, 0, self._fit(f"Plan: {plan}", width), PAIR_DEFAULT)
        self._addstr(stdscr, top + 3, 0, self._fit(f"Primary: {self._detail_window(state, 'primary')}", width), self._state_pair(state))
        self._addstr(stdscr, top + 4, 0, self._fit(f"Secondary: {self._detail_window(state, 'secondary')}", width), self._state_pair(state))
        self._addstr(stdscr, top + 5, 0, self._fit(self._state_footer(state), width), PAIR_MUTED)

    def _draw_footer(self, stdscr, row: int, width: int) -> None:
        keys = "Enter activate | l login/add | r/R refresh active | u reload | q quit"
        text = f" {self.status} | {keys} "
        self._addstr(stdscr, row, 0, self._fit(text, width).ljust(width), PAIR_HEADER)

    def _active_label(self) -> str:
        for account in self.accounts:
            if account.fingerprint == self.active_fingerprint:
                return f"Active: {account.name} | auth: {self.manager.active_auth_path}"
        if self.active_fingerprint:
            return f"Active: unsaved account | auth: {self.manager.active_auth_path}"
        return f"Active: none | auth: {self.manager.active_auth_path}"

    def _keep_selection_visible(self, list_height: int) -> None:
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        bottom = self.scroll_offset + list_height - 1
        if self.selected_index > bottom:
            self.scroll_offset = self.selected_index - list_height + 1
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, len(self.accounts) - 1)))

    def _limit_state(self, account_id: str) -> LimitState:
        with self.lock:
            return self.limit_states.get(account_id, LimitState(status="loading"))

    def _display_state(self, account: StoredAccount) -> LimitState:
        state = self._limit_state(account.id)
        if account.fingerprint == self.active_fingerprint:
            return state
        if state.status == "error" and state.snapshot is None:
            return LimitState(status="empty")
        return state

    def _state_pair(self, state: LimitState) -> int:
        if state.status == "error":
            return PAIR_BAD
        if state.status != "ready" or state.snapshot is None:
            return PAIR_WARN
        value = self._available_percent(state.snapshot.primary)
        if value is None:
            return PAIR_WARN
        if value <= 0:
            return PAIR_BAD
        if value < 20:
            return PAIR_WARN
        return PAIR_GOOD

    def _window_summary(self, state: LimitState, attr_name: str) -> str:
        if state.status == "error":
            return "error"
        if state.status == "empty":
            return "no cache"
        if state.status != "ready" or state.snapshot is None:
            return "loading..."
        window = getattr(state.snapshot, attr_name)
        if window is None:
            return "no data"
        available = self._available_percent(window)
        if available is None:
            return "unknown"
        if attr_name == "primary":
            reset_label = self._reset_remaining_compact(state, window) or "unknown"
            return f"{available:.1f}% available, {reset_label} left"
        elif attr_name == "secondary":
            reset = self._reset_timestamp(state, window)
            reset_label = reset.split(" ")[0] if " " in reset else reset
            return f"{available:.1f}% available, {reset_label}"
        else:
            reset = self._reset_timestamp(state, window)
            reset_label = reset.split(" ")[1] if " " in reset else reset
            mins = f"{window.window_minutes}m" if window.window_minutes else "?m"
            return f"{available:.1f}% available, {mins}, {reset_label}"

    def _detail_window(self, state: LimitState, attr_name: str) -> str:
        if state.status == "error":
            return state.error or "error"
        if state.status == "empty":
            return "no cached limits"
        if state.status != "ready" or state.snapshot is None:
            return "loading..."
        window = getattr(state.snapshot, attr_name)
        if window is None:
            return "no data"
        available = self._available_percent(window)
        available_text = f"{available:.1f}% available" if available is not None else "unknown"
        mins = f"{window.window_minutes} min" if window.window_minutes else "unknown window"
        reset = self._reset_timestamp(state, window)
        remaining = self._reset_remaining_text(state, window)
        if remaining:
            return f"{available_text}, {mins}, resets {reset} ({remaining})"
        return f"{available_text}, {mins}, resets {reset}"

    def _snapshot_plan(self, state: LimitState) -> str | None:
        if state.snapshot is None:
            return None
        return state.snapshot.plan_type

    def _state_footer(self, state: LimitState) -> str:
        if state.refreshing:
            return "Refreshing limits..."
        if state.fetched_at is None:
            return "No cached limits yet. Activate this account to fetch them."
        fetched = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.fetched_at))
        prefix = "Cached refresh" if state.cached else "Last refresh"
        suffix = "Only active account is auto-refreshed."
        if state.error:
            suffix = f"Last refresh failed; showing cached data. {state.error}"
        return f"{prefix}: {fetched}. {suffix}"

    def _load_limit_cache(self) -> None:
        try:
            rows = json.loads(LIMITS_CACHE_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            return
        if not isinstance(rows, dict):
            return
        for account_id, row in rows.items():
            if not isinstance(account_id, str) or not isinstance(row, dict):
                continue
            try:
                snapshot = self._snapshot_from_cache(row["snapshot"])
                fetched_at = float(row["fetched_at"])
            except Exception:
                continue
            self.limit_states[account_id] = LimitState(
                status="ready",
                fetched_at=fetched_at,
                snapshot=snapshot,
                cached=True,
            )

    def _save_limit_cache_locked(self) -> None:
        rows: dict[str, dict[str, object]] = {}
        for account_id, state in self.limit_states.items():
            if state.snapshot is None or state.fetched_at is None:
                continue
            rows[account_id] = {
                "fetched_at": state.fetched_at,
                "snapshot": self._snapshot_to_cache(state.snapshot),
            }
        try:
            LIMITS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            LIMITS_CACHE_PATH.write_text(
                json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def _window_to_cache(self, window: LimitWindow | None) -> dict[str, object] | None:
        if window is None:
            return None
        return {
            "used_percent": window.used_percent,
            "window_minutes": window.window_minutes,
            "resets_at": window.resets_at,
        }

    def _window_from_cache(self, row: object) -> LimitWindow | None:
        if not isinstance(row, dict):
            return None
        return LimitWindow(
            used_percent=row.get("used_percent"),
            window_minutes=row.get("window_minutes"),
            resets_at=row.get("resets_at"),
        )

    def _snapshot_to_cache(self, snapshot: RateLimitSnapshot) -> dict[str, object]:
        return {
            "plan_type": snapshot.plan_type,
            "primary": self._window_to_cache(snapshot.primary),
            "secondary": self._window_to_cache(snapshot.secondary),
            "credits_balance": snapshot.credits_balance,
            "credits_unlimited": snapshot.credits_unlimited,
        }

    def _snapshot_from_cache(self, row: object) -> RateLimitSnapshot:
        if not isinstance(row, dict):
            raise ValueError("invalid cached snapshot")
        return RateLimitSnapshot(
            plan_type=row.get("plan_type"),
            primary=self._window_from_cache(row.get("primary")),
            secondary=self._window_from_cache(row.get("secondary")),
            credits_balance=row.get("credits_balance"),
            credits_unlimited=bool(row.get("credits_unlimited")),
            raw={},
        )

    def _available_percent(self, window: LimitWindow | None) -> float | None:
        if window is None:
            return None
        return display_limit_percent(window.used_percent)

    def _reset_epoch(self, state: LimitState, window: LimitWindow) -> float | None:
        if window.resets_at:
            return float(window.resets_at)
        if state.fetched_at is None or window.window_minutes is None:
            return None
        return state.fetched_at + window.window_minutes * 60

    def _reset_timestamp(self, state: LimitState, window: LimitWindow) -> str:
        reset_epoch = self._reset_epoch(state, window)
        if reset_epoch is None:
            return "unknown"
        if window.resets_at:
            return format_reset_timestamp(window.resets_at)
        return datetime.fromtimestamp(reset_epoch).strftime("%Y-%m-%d %H:%M:%S")

    def _reset_remaining_text(self, state: LimitState, window: LimitWindow) -> str | None:
        reset_epoch = self._reset_epoch(state, window)
        if reset_epoch is None:
            return None
        remaining = int(reset_epoch - time.time())
        if remaining <= 0:
            return "now"
        hours, remainder = divmod(remaining, 3600)
        minutes = remainder // 60
        if hours:
            return f"in {hours}h {minutes}m"
        return f"in {minutes}m"

    def _reset_remaining_compact(self, state: LimitState, window: LimitWindow) -> str | None:
        value = self._reset_remaining_text(state, window)
        if value is None:
            return None
        if value.startswith("in "):
            return value[3:]
        return value

    def _fit(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        value = str(text).replace("\n", " ")
        if len(value) <= width:
            return value
        if width <= 3:
            return value[:width]
        return value[: width - 3] + "..."

    def _addstr(self, stdscr, y: int, x: int, text: str, pair: int) -> None:
        self._addstr_attr(stdscr, y, x, text, curses.color_pair(pair))

    def _addstr_attr(self, stdscr, y: int, x: int, text: str, attr: int) -> None:
        try:
            stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TUI for Codex Auth Switcher.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=60,
        help="Seconds between automatic limit refreshes.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Rate limit request timeout in seconds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app = TuiApp(
        refresh_interval=max(10, args.refresh_interval),
        timeout_seconds=max(1, args.timeout),
    )
    curses.wrapper(app.run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
