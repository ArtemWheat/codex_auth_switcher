from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .auth import load_auth_file
from .constants import APP_NAME, ICON_NAME, ICON_PATH
from .manager import AccountManager
from .rate_limits import RateLimitSnapshot, display_limit_percent, fetch_rate_limits


class TrayApplication:
    def __init__(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3, Gio, GLib, Gtk

        self.Gio = Gio
        self.GLib = GLib
        self.Gtk = Gtk
        self.AyatanaAppIndicator3 = AyatanaAppIndicator3
        self.manager = AccountManager()
        self.rate_limit_cache: dict[str, tuple[float, str]] = {}
        self.rate_limit_refreshing: set[str] = set()
        self.menu = None
        self.indicator = None
        self._menu_rebuilding = False
        self._menu_is_open = False
        self._menu_refresh_pending = False
        self._menu_refresh_idle_scheduled = False
        self._login_in_progress = False
        self.app = Gtk.Application(
            application_id="com.maxim.codexauthswitcher.tray",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.app.connect("startup", self._on_startup)
        self.app.connect("activate", self._on_activate)

    def _on_startup(self, app) -> None:
        app.hold()

        self.indicator = self.AyatanaAppIndicator3.Indicator.new(
            "codex-auth-switcher",
            ICON_NAME,
            self.AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(self.AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title(APP_NAME)
        self.indicator.set_icon_theme_path(str(ICON_PATH.parent))
        self.indicator.set_icon(ICON_NAME)
        self.indicator.set_icon_full(str(ICON_PATH), APP_NAME)
        self.indicator.set_attention_icon("applications-system-symbolic")
        self.indicator.set_label("Codex", APP_NAME)

        self.menu = self.Gtk.Menu()
        self.menu.connect("show", self._on_menu_show)
        self.menu.connect("hide", self._on_menu_hide)
        self.menu.connect("selection-done", self._on_menu_hide)
        self.menu.connect("deactivate", self._on_menu_hide)
        self.indicator.set_menu(self.menu)
        self.refresh_menu()
        self.GLib.timeout_add_seconds(120, self._refresh_menu_timer)

    def _on_activate(self, _app) -> None:
        self.refresh_menu()

    def _on_menu_show(self, _menu) -> None:
        self._menu_is_open = True

    def _on_menu_hide(self, _menu) -> None:
        self._menu_is_open = False
        if self._menu_refresh_pending:
            self._menu_refresh_pending = False
            self.refresh_menu()

    def _request_menu_refresh(self) -> None:
        if self._menu_is_open:
            self._menu_refresh_pending = True
            return
        if self._menu_refresh_idle_scheduled:
            return
        self._menu_refresh_idle_scheduled = True
        self.GLib.idle_add(self._refresh_menu_idle)

    def _refresh_menu_idle(self) -> bool:
        self._menu_refresh_idle_scheduled = False
        self.refresh_menu()
        return False

    def _refresh_menu_timer(self) -> bool:
        self.refresh_menu()
        return True

    def _rate_limit_summary(self, account) -> str:
        cached = self.rate_limit_cache.get(account.id)
        now = time.time()
        if cached is not None:
            cached_at, summary = cached
            if now - cached_at < 120:
                return summary
        self._refresh_rate_limits_async(account.id)
        if cached is not None:
            return cached[1]
        return "limits: loading..."

    def _format_rate_limit_summary(self, snapshot: RateLimitSnapshot) -> str:
        parts: list[str] = []
        primary_percent = display_limit_percent(
            snapshot.primary.used_percent if snapshot.primary is not None else None
        )
        secondary_percent = display_limit_percent(
            snapshot.secondary.used_percent if snapshot.secondary is not None else None
        )
        if primary_percent is not None:
            parts.append(f"P:{primary_percent:.0f}%")
        if secondary_percent is not None:
            parts.append(f"S:{secondary_percent:.0f}%")
        if not parts:
            parts.append("no limits")
        return " ".join(parts)

    def _shorten_email(self, value: str, limit: int = 16) -> str:
        if len(value) <= limit:
            return value
        if limit <= 1:
            return value[:limit]
        return f"{value[: limit - 1]}…"

    def _format_account_label(
        self,
        name: str,
        email: str | None,
        organization_label: str | None = None,
        disambiguate: bool = False,
    ) -> str:
        display_name = name.strip()
        normalized_email = (email or "").strip()

        if "@" in display_name:
            display_name = self._shorten_email(display_name)

        parts = [display_name]
        if normalized_email and normalized_email.casefold() != name.strip().casefold():
            parts.append(self._shorten_email(normalized_email))
        if disambiguate and organization_label:
            parts.append(organization_label.strip())
        return " | ".join(parts)

    def _refresh_rate_limits_async(self, account_id: str) -> None:
        if account_id in self.rate_limit_refreshing:
            return
        self.rate_limit_refreshing.add(account_id)

        def worker() -> None:
            try:
                account = self.manager.store.get(account_id)
                snapshot = fetch_rate_limits(account.auth, timeout_seconds=10)
                summary = self._format_rate_limit_summary(snapshot)
            except Exception:
                summary = "limits: error"
            self.rate_limit_cache[account_id] = (time.time(), summary)
            self.rate_limit_refreshing.discard(account_id)
            self._request_menu_refresh()

        threading.Thread(target=worker, name=f"limits-{account_id[:8]}", daemon=True).start()

    def _clear_menu(self) -> None:
        for child in list(self.menu.get_children()):
            self.menu.remove(child)

    def _append_item(self, label: str, callback=None, sensitive: bool = True):
        item = self.Gtk.MenuItem(label=label)
        item.set_sensitive(sensitive)
        if callback is not None:
            item.connect("activate", callback)
        self.menu.append(item)
        return item

    def _append_separator(self) -> None:
        self.menu.append(self.Gtk.SeparatorMenuItem())

    def _prompt_account_name(self, title: str, initial_value: str) -> str | None:
        dialog = self.Gtk.Dialog(title=title, modal=True)
        dialog.add_button("Отмена", self.Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", self.Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        entry = self.Gtk.Entry()
        entry.set_text(initial_value)
        entry.set_activates_default(True)
        dialog.set_default_response(self.Gtk.ResponseType.OK)
        content.pack_start(entry, False, False, 0)
        dialog.show_all()

        response = dialog.run()
        value = entry.get_text().strip()
        dialog.destroy()
        if response != self.Gtk.ResponseType.OK:
            return None
        return value or None

    def _select_auth_file(self) -> Path | None:
        dialog = self.Gtk.FileChooserDialog(
            title="Выбери auth.json",
            action=self.Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            "Отмена",
            self.Gtk.ResponseType.CANCEL,
            "Открыть",
            self.Gtk.ResponseType.OK,
        )
        response = dialog.run()
        filename = dialog.get_filename()
        dialog.destroy()
        if response != self.Gtk.ResponseType.OK or not filename:
            return None
        return Path(filename)

    def _show_error(self, message: str) -> None:
        dialog = self.Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=self.Gtk.MessageType.ERROR,
            buttons=self.Gtk.ButtonsType.CLOSE,
            text=message,
        )
        dialog.run()
        dialog.destroy()

    def _show_info(self, message: str) -> None:
        dialog = self.Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=self.Gtk.MessageType.INFO,
            buttons=self.Gtk.ButtonsType.CLOSE,
            text=message,
        )
        dialog.run()
        dialog.destroy()

    def _open_manager(self, _item=None) -> None:
        app_path = Path(__file__).resolve().parent.parent / "app.py"
        subprocess.Popen([sys.executable, str(app_path)])

    def _resolve_codex_bin(self) -> str | None:
        codex_bin = shutil.which("codex")
        if codex_bin:
            return codex_bin

        candidate_dirs = [
            Path.home() / ".asdf" / "shims",
            Path.home() / ".local" / "bin",
            Path.home() / ".nvm" / "versions" / "node",
            Path.home() / ".cargo" / "bin",
            Path("/usr/local/bin"),
            Path("/usr/bin"),
            Path("/bin"),
        ]

        for directory in candidate_dirs:
            if not directory.exists():
                continue
            if directory.name == "node":
                for nested in sorted(directory.glob("*/bin/codex"), reverse=True):
                    if nested.is_file() and os.access(nested, os.X_OK):
                        return str(nested)
                continue
            candidate = directory / "codex"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    def _build_login_terminal_command(self, codex_bin: str) -> list[str]:
        terminal = shutil.which("gnome-terminal") or shutil.which("x-terminal-emulator")
        if not terminal:
            raise RuntimeError("Не найден terminal emulator для запуска codex login.")

        quoted_codex_bin = shlex.quote(codex_bin)
        shell_command = (
            f"{quoted_codex_bin} login; "
            "status=$?; "
            "exit $status"
        )

        if terminal.endswith("gnome-terminal"):
            return [terminal, "--wait", "--", "bash", "-lc", shell_command]
        return [terminal, "-e", f"bash -lc '{shell_command}'"]

    def _handle_login_completion(self, before_fingerprint: str | None, returncode: int) -> bool:
        self._login_in_progress = False
        if returncode != 0:
            self._show_error("Авторизация Codex завершилась с ошибкой или была отменена.")
            self.refresh_menu()
            return False

        try:
            profile = load_auth_file(self.manager.active_auth_path)
        except Exception as error:
            self._show_error(f"Авторизация завершилась, но auth.json не прочитался:\n{error}")
            self.refresh_menu()
            return False

        fingerprint_changed = profile.fingerprint != before_fingerprint
        already_saved = self.manager.store.find_by_fingerprint(profile.fingerprint) is not None
        try:
            stored = self.manager.add_current_account()
        except Exception as error:
            self._show_error(f"Не удалось сохранить аккаунт после авторизации:\n{error}")
            self.refresh_menu()
            return False

        if fingerprint_changed or not already_saved:
            self._show_info(f"Аккаунт добавлен: {stored.name}")
        else:
            self._show_info(f"Аккаунт уже был сохранён: {stored.name}")
        self.refresh_menu()
        return False

    def _login_via_codex(self, _item=None) -> None:
        if self._login_in_progress:
            self._show_info("Авторизация уже запущена.")
            return

        codex_bin = self._resolve_codex_bin()
        if not codex_bin:
            self._show_error(
                "Команда codex не найдена. Проверь установку Codex или добавь бинарник в PATH."
            )
            return

        try:
            before_profile = load_auth_file(self.manager.active_auth_path)
            before_fingerprint = before_profile.fingerprint
        except Exception:
            before_fingerprint = None

        try:
            command = self._build_login_terminal_command(codex_bin)
        except Exception as error:
            self._show_error(str(error))
            return

        try:
            process = subprocess.Popen(command)
        except Exception as error:
            self._show_error(f"Не удалось запустить окно авторизации:\n{error}")
            return

        self._login_in_progress = True
        self.refresh_menu()

        def wait_for_login() -> None:
            returncode = process.wait()
            self.GLib.idle_add(self._handle_login_completion, before_fingerprint, returncode)

        threading.Thread(target=wait_for_login, name="codex-login", daemon=True).start()

    def _activate_account(self, item, account_id: str) -> None:
        # Ignore programmatic set_active() calls fired during menu rebuild
        if self._menu_rebuilding:
            return
        if not item.get_active():
            return
        try:
            self.manager.activate_account(account_id)
        except Exception as error:
            self._show_error(f"Не удалось переключить аккаунт:\n{error}")
            return
        self.refresh_menu()

    def _add_current_account(self, _item=None) -> None:
        try:
            profile = load_auth_file(self.manager.active_auth_path)
        except Exception as error:
            self._show_error(f"Не удалось прочитать текущий auth.json:\n{error}")
            return
        name = self._prompt_account_name("Сохранить текущий аккаунт", profile.display_name)
        if name is None:
            return
        try:
            self.manager.add_current_account(name=name)
        except Exception as error:
            self._show_error(f"Не удалось сохранить текущий аккаунт:\n{error}")
            return
        self.refresh_menu()

    def _import_auth_file(self, _item=None) -> None:
        path = self._select_auth_file()
        if path is None:
            return
        try:
            profile = load_auth_file(path)
        except Exception as error:
            self._show_error(f"Не удалось прочитать выбранный auth.json:\n{error}")
            return
        name = self._prompt_account_name("Импорт аккаунта", profile.display_name)
        if name is None:
            return
        try:
            self.manager.import_auth_file(path, name=name)
        except Exception as error:
            self._show_error(f"Не удалось импортировать аккаунт:\n{error}")
            return
        self.refresh_menu()

    def refresh_menu(self) -> bool:
        if self._menu_is_open:
            self._menu_refresh_pending = True
            return True
        self._menu_rebuilding = True
        try:
            try:
                self._rebuild_menu()
            except Exception as error:
                self._build_error_menu(error)
        finally:
            self._menu_rebuilding = False
        return True

    def _build_error_menu(self, error: Exception) -> None:
        self._clear_menu()
        self._append_item("Не удалось построить меню", sensitive=False)
        self._append_item(str(error), sensitive=False)
        self._append_separator()
        self._append_item("Обновить меню", lambda _item: self.refresh_menu())
        self._append_item("Открыть менеджер", self._open_manager)
        self._append_separator()
        self._append_item("Выход", lambda _item: self.app.quit())
        self.menu.show_all()

    def _rebuild_menu(self) -> None:
        snapshot = self.manager.snapshot()
        self._clear_menu()

        current_title = "Текущий: "
        if snapshot.current_account is not None:
            current_title += snapshot.current_account.name
        elif snapshot.active_fingerprint:
            current_title += "не сохранён в базе"
        else:
            current_title += "не определён"
        self._append_item(current_title, sensitive=False)

        active_path_label = f"Файл: {snapshot.active_auth_path}"
        self._append_item(active_path_label, sensitive=False)
        self._append_separator()

        if snapshot.accounts:
            accounts_root = self._append_item("Аккаунты")
            accounts_menu = self.Gtk.Menu()
            accounts_root.set_submenu(accounts_menu)

            base_labels: dict[str, int] = {}
            for account in snapshot.accounts:
                base_label = self._format_account_label(account.name, account.auth.email)
                base_labels[base_label] = base_labels.get(base_label, 0) + 1

            group = None
            for account in snapshot.accounts:
                base_label = self._format_account_label(account.name, account.auth.email)
                label = self._format_account_label(
                    account.name,
                    account.auth.email,
                    organization_label=account.auth.organization_label,
                    disambiguate=base_labels.get(base_label, 0) > 1,
                )
                summary = self._rate_limit_summary(account)
                label = f"{label} | {summary}"
                item = self.Gtk.RadioMenuItem.new_with_label(group, label)
                group = item.get_group()
                is_active = account.fingerprint == snapshot.active_fingerprint
                item.set_active(is_active)
                item.connect("activate", self._activate_account, account.id)
                accounts_menu.append(item)
            accounts_menu.show_all()
        else:
            self._append_item("Аккаунты: пока пусто", sensitive=False)

        add_root = self._append_item("Добавить аккаунт")
        add_menu = self.Gtk.Menu()
        add_root.set_submenu(add_menu)
        login_item = self.Gtk.MenuItem(label="Войти")
        login_item.set_sensitive(not self._login_in_progress)
        login_item.connect("activate", self._login_via_codex)
        add_menu.append(login_item)
        add_current = self.Gtk.MenuItem(label="Сохранить текущий")
        add_current.connect("activate", self._add_current_account)
        add_menu.append(add_current)
        import_item = self.Gtk.MenuItem(label="Импорт auth.json...")
        import_item.connect("activate", self._import_auth_file)
        add_menu.append(import_item)
        add_menu.show_all()

        self._append_separator()
        self._append_item("Открыть менеджер", self._open_manager)
        self._append_item("Обновить меню", lambda _item: self.refresh_menu())
        self._append_separator()
        self._append_item("Выход", lambda _item: self.app.quit())
        self.menu.show_all()

    def run(self) -> None:
        self.app.run(sys.argv)


def run() -> None:
    app = TrayApplication()
    app.run()
