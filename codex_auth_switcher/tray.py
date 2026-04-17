from __future__ import annotations

import os
import threading
from collections.abc import Callable


class TrayController:
    def __init__(
        self,
        icon_path: str,
        on_show: Callable[[], None],
        on_hide: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self.icon_path = icon_path
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_quit = on_quit
        self.available = False
        self._indicator = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if os.environ.get("CODEX_AUTH_SWITCHER_ENABLE_TRAY") != "1":
            self.available = False
            return
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3, GLib, Gtk
        except Exception:
            self.available = False
            return

        self.available = True

        def worker() -> None:
            indicator = AyatanaAppIndicator3.Indicator.new(
                "codex-auth-switcher",
                self.icon_path,
                AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
            indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
            indicator.set_title("Codex Auth Switcher")
            indicator.set_icon_full(self.icon_path, "Codex Auth Switcher")

            menu = Gtk.Menu()

            show_item = Gtk.MenuItem(label="Показать")
            show_item.connect("activate", lambda _item: GLib.idle_add(self.on_show))
            menu.append(show_item)

            hide_item = Gtk.MenuItem(label="Скрыть")
            hide_item.connect("activate", lambda _item: GLib.idle_add(self.on_hide))
            menu.append(hide_item)

            separator = Gtk.SeparatorMenuItem()
            menu.append(separator)

            quit_item = Gtk.MenuItem(label="Выход")
            quit_item.connect("activate", lambda _item: GLib.idle_add(self.on_quit))
            menu.append(quit_item)

            menu.show_all()
            indicator.set_menu(menu)
            self._indicator = indicator
            Gtk.main()

        self._thread = threading.Thread(target=worker, name="tray-indicator", daemon=True)
        self._thread.start()
