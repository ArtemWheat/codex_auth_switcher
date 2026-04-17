from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from .auth import load_auth_file
from .constants import APP_NAME, APP_STORAGE_DIR
from .manager import AccountManager
from .rate_limits import (
    RateLimitSnapshot,
    display_limit_percent,
    fetch_rate_limits,
    format_reset_timestamp,
)
from .storage import StoredAccount

# ─── Palette ──────────────────────────────────────────────────────────────────
BG      = "#0d1117"   # page background
PANEL   = "#161b22"   # topbar / sidebar surfaces
SURF    = "#21262d"   # list-item default
SURF2   = "#2d333b"   # list-item hover
BORDER  = "#30363d"   # dividers / separators
ACCENT  = "#2da44e"   # primary emerald
ACCH    = "#3fb950"   # accent hover
ACDP    = "#238636"   # accent pressed
TEXT    = "#f0f6fc"   # primary text
TSEC    = "#8b949e"   # secondary text
TMUT    = "#6e7681"   # muted labels
DANGER  = "#f85149"   # destructive
SEL     = "#1c2b3a"   # selected row background

# ─── Fonts ────────────────────────────────────────────────────────────────────
_F  = "TkDefaultFont"
_FM = "TkFixedFont"

F_TITLE = (_F, 14, "bold")
F_HEAD  = (_F, 11, "bold")
F_BODY  = (_F, 10)
F_BOLD  = (_F, 10, "bold")
F_SEC   = (_F, 9)
F_SMALL = (_F, 8)
F_LABEL = (_F, 8, "bold")
F_MONO  = (_FM, 9)


# ─── Plan badge colors ────────────────────────────────────────────────────────

def _plan_colors(plan: str) -> tuple[str, str]:
    table: dict[str, tuple[str, str]] = {
        "plus":  ("#ffa657", "#2d1d0e"),
        "pro":   ("#58a6ff", "#0d1f38"),
        "free":  ("#8b949e", "#21262d"),
        "team":  ("#bc8cff", "#1e1633"),
        "teams": ("#bc8cff", "#1e1633"),
    }
    return table.get(plan, ("#8b949e", "#21262d"))


# ─── Reusable primitives ──────────────────────────────────────────────────────

def _sep(parent: tk.Widget, vertical: bool = False) -> tk.Frame:
    """1 px separator."""
    if vertical:
        return tk.Frame(parent, bg=BORDER, width=1)
    return tk.Frame(parent, bg=BORDER, height=1)


def _spacer(parent: tk.Widget, h: int = 12) -> tk.Frame:
    return tk.Frame(parent, bg=BG, height=h)


class _HoverBtn(tk.Frame):
    """Flat button with animated hover / press states."""

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command=None,
        fg: str = TSEC,
        bg: str = SURF,
        bg_h: str = SURF2,
        bg_p: str = BORDER,
        padx: int = 12,
        pady: int = 6,
        font=F_BODY,
        **kw,
    ) -> None:
        super().__init__(parent, bg=bg, cursor="hand2", **kw)
        self._bg, self._bg_h, self._bg_p = bg, bg_h, bg_p
        self._cmd = command
        self._lbl = tk.Label(
            self, text=text, bg=bg, fg=fg,
            font=font, padx=padx, pady=pady, cursor="hand2",
        )
        self._lbl.pack()
        for w in (self, self._lbl):
            w.bind("<Enter>",           self._enter)
            w.bind("<Leave>",           self._leave)
            w.bind("<ButtonPress-1>",   self._press)
            w.bind("<ButtonRelease-1>", self._release)

    def _set(self, color: str) -> None:
        self.config(bg=color)
        self._lbl.config(bg=color)

    def _enter(self, _=None) -> None:
        self._set(self._bg_h)

    def _leave(self, _=None) -> None:
        self._set(self._bg)

    def _press(self, _=None) -> None:
        self._set(self._bg_p)

    def _release(self, _=None) -> None:
        self._set(self._bg_h)
        if self._cmd:
            self._cmd()

    def set_text(self, text: str) -> None:
        self._lbl.config(text=text)


class _PrimaryBtn(_HoverBtn):
    def __init__(self, parent: tk.Widget, text: str, command=None, **kw) -> None:
        super().__init__(
            parent, text, command,
            fg="#0d1117", bg=ACCENT, bg_h=ACCH, bg_p=ACDP,
            font=F_BOLD, padx=14, pady=6, **kw,
        )


class _DangerBtn(_HoverBtn):
    def __init__(self, parent: tk.Widget, text: str, command=None, **kw) -> None:
        super().__init__(
            parent, text, command,
            fg=DANGER, bg=PANEL, bg_h="#2d1416", bg_p="#3d1c1e",
            padx=12, pady=6, **kw,
        )


# ─── Scrollable container ─────────────────────────────────────────────────────

class _ScrollFrame(tk.Frame):
    """Vertically-scrollable frame. Exposes `.inner` for child widgets."""

    def __init__(self, parent: tk.Widget, bg: str = BG, **kw) -> None:
        super().__init__(parent, bg=bg, **kw)
        self._bg = bg
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._sb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._update_scrollregion)
        self.canvas.bind("<Configure>", self._update_width)
        self.canvas.configure(yscrollcommand=self._sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._sb.pack(side="right", fill="y")
        # Linux scroll events
        self.canvas.bind("<Enter>", self._bind_scroll)
        self.canvas.bind("<Leave>", self._unbind_scroll)

    def _update_scrollregion(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _update_width(self, e) -> None:
        self.canvas.itemconfig(self._win, width=e.width)

    def _bind_scroll(self, _e=None) -> None:
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

    def _unbind_scroll(self, _e=None) -> None:
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")
        self.canvas.unbind_all("<MouseWheel>")


# ─── Account row widget ───────────────────────────────────────────────────────

class _AccountRow(tk.Frame):
    """Single account card in the sidebar list."""

    def __init__(
        self,
        parent: tk.Widget,
        account: StoredAccount,
        is_active: bool,
        on_select,
        on_activate,
        **kw,
    ) -> None:
        super().__init__(parent, bg=SURF, cursor="hand2", **kw)
        self.account = account
        self._is_active = is_active
        self._on_select = on_select
        self._on_activate = on_activate
        self._selected = False

        # Left accent strip (3 px, green if active)
        self._strip = tk.Frame(self, bg=ACCENT if is_active else SURF, width=3)
        self._strip.pack(side="left", fill="y")
        self._strip.pack_propagate(False)

        # Content
        self._body = tk.Frame(self, bg=SURF, padx=13, pady=11)
        self._body.pack(side="left", fill="both", expand=True)

        # Row 1 — name + active badge
        r1 = tk.Frame(self._body, bg=SURF)
        r1.pack(fill="x")
        self._name = tk.Label(r1, text=account.name, bg=SURF, fg=TEXT,
                               font=F_BOLD, anchor="w")
        self._name.pack(side="left")
        if is_active:
            self._badge = tk.Label(r1, text=" active ", bg="#0f2a19", fg=ACCENT,
                                    font=(_F, 7, "bold"), padx=2, pady=1)
            self._badge.pack(side="left", padx=(7, 0))
        else:
            self._badge = None

        # Row 2 — email + plan tag
        r2 = tk.Frame(self._body, bg=SURF)
        r2.pack(fill="x", pady=(3, 0))
        self._email = tk.Label(r2, text=account.auth.email or "unknown",
                                bg=SURF, fg=TSEC, font=F_SEC, anchor="w")
        self._email.pack(side="left")
        plan = (account.auth.plan_type or "free").lower()
        pfg, pbg = _plan_colors(plan)
        self._plan_tag = tk.Label(r2, text=f" {plan} ", bg=pbg, fg=pfg,
                                   font=(_F, 7, "bold"), padx=2)
        self._plan_tag.pack(side="right")

        # Widgets whose bg changes on hover/select (excludes fixed-color badges)
        self._managed = [self, self._body, r1, r2, self._name, self._email]
        self._bind_events()

    # ── Visual state ──────────────────────────────────────────────────────────

    def select(self, on: bool) -> None:
        self._selected = on
        bg = SEL if on else SURF
        self._apply_bg(bg)
        self._strip.config(bg=ACCENT if (on or self._is_active) else SURF)

    def _apply_bg(self, bg: str) -> None:
        for w in self._managed:
            w.config(bg=bg)

    def _on_hover(self, _e=None) -> None:
        if not self._selected:
            self._apply_bg(SURF2)

    def _on_leave(self, event=None) -> None:
        if not self._selected:
            # Only deselect if pointer truly left the row
            if event:
                widget = self.winfo_containing(event.x_root, event.y_root)
                if widget and str(widget).startswith(str(self)):
                    return
            self._apply_bg(SURF)

    # ── Events ────────────────────────────────────────────────────────────────

    def _bind_events(self) -> None:
        for w in self._managed:
            w.bind("<Enter>",           self._on_hover)
            w.bind("<Leave>",           self._on_leave)
            w.bind("<Button-1>",        lambda _e: self._on_select(self.account.id))
            w.bind("<Double-Button-1>", lambda _e: self._on_activate(self.account.id))


# ─── Main application ─────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1060x680")
        self.minsize(860, 560)
        self.configure(bg=BG)

        self.manager = AccountManager()
        self.store = self.manager.store
        self.active_auth_path = self.manager.active_auth_path
        self.accounts: list[StoredAccount] = []
        self.rate_limit_cache: dict[str, RateLimitSnapshot] = {}

        self._rows: dict[str, _AccountRow] = {}
        self._selected_id: str | None = None

        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self.refresh_accounts(select_active=True)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_topbar()
        _sep(self).pack(fill="x")
        self._build_body()
        _sep(self).pack(fill="x")
        self._build_actionbar()

    def _build_topbar(self) -> None:
        bar = tk.Frame(self, bg=PANEL, height=52)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        inner = tk.Frame(bar, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=20)
        tk.Label(inner, text=APP_NAME, bg=PANEL, fg=TEXT,
                 font=F_TITLE).pack(side="left", pady=0, anchor="center")
        # Active-auth badge on the right
        self._topbar_fp = tk.Label(inner, text="", bg=PANEL, fg=TMUT, font=F_SEC)
        self._topbar_fp.pack(side="right", anchor="center")
        # Ensure vertical centering
        inner.pack_configure(pady=14)

    def _build_body(self) -> None:
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = tk.Frame(body, bg=PANEL, width=290)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Sidebar header row
        sh = tk.Frame(sidebar, bg=PANEL, padx=16, pady=10)
        sh.pack(fill="x")
        tk.Label(sh, text="ACCOUNTS", bg=PANEL, fg=TMUT,
                 font=F_LABEL).pack(side="left")
        self._count_lbl = tk.Label(sh, text="0", bg=SURF2, fg=TSEC,
                                    font=(_F, 8), padx=6, pady=2)
        self._count_lbl.pack(side="left", padx=(7, 0))
        _HoverBtn(sh, "+ Add", command=self.add_current_auth,
                  fg=TSEC, bg=PANEL, bg_h=SURF, bg_p=SURF2,
                  padx=8, pady=3, font=F_SMALL).pack(side="right")
        _HoverBtn(sh, "Import", command=self.import_auth_file,
                  fg=TMUT, bg=PANEL, bg_h=SURF, bg_p=SURF2,
                  padx=8, pady=3, font=F_SMALL).pack(side="right", padx=(0, 2))

        _sep(sidebar).pack(fill="x")

        # Account list
        self._list_scroll = _ScrollFrame(sidebar, bg=PANEL)
        self._list_scroll.pack(fill="both", expand=True)
        self._list_inner = self._list_scroll.inner

        _sep(body, vertical=True).pack(side="left", fill="y")

        # ── Detail panel ──────────────────────────────────────────────────────
        detail_outer = tk.Frame(body, bg=BG)
        detail_outer.pack(side="left", fill="both", expand=True)

        detail_scroll = _ScrollFrame(detail_outer, bg=BG)
        detail_scroll.pack(fill="both", expand=True)
        detail_scroll.canvas.config(bg=BG)
        detail_scroll.inner.config(bg=BG)
        dp = detail_scroll.inner
        p = tk.Frame(dp, bg=BG, padx=32, pady=26)
        p.pack(fill="both", expand=True)

        # ── Detail: account name + badges ─────────────────────────────────────
        dh = tk.Frame(p, bg=BG)
        dh.pack(fill="x")
        self._d_name = tk.Label(dh, text="Select an account", bg=BG, fg=TEXT,
                                 font=(_F, 17, "bold"), anchor="w")
        self._d_name.pack(side="left", anchor="center")
        self._d_active_badge = tk.Label(dh, text="", bg=BG, fg=BG,
                                         font=(_F, 8, "bold"), padx=8, pady=3)
        self._d_active_badge.pack(side="left", padx=(10, 0), anchor="center")
        self._d_plan_badge = tk.Label(dh, text="", bg=BG, fg=BG,
                                       font=(_F, 8), padx=8, pady=3)
        self._d_plan_badge.pack(side="left", padx=(5, 0), anchor="center")

        # ── Detail: Profile ───────────────────────────────────────────────────
        _spacer(p, 18).pack(fill="x")
        self._section_lbl(p, "PROFILE")
        sf = tk.Frame(p, bg=BG)
        sf.pack(fill="x", pady=(8, 0))
        self._d_email = self._field(sf, "Email", "—")
        self._d_account_id = self._field(sf, "Account ID", "—", mono=True)
        self._d_fingerprint = self._field(sf, "Fingerprint", "—", mono=True)

        # ── Detail: Rate limits ───────────────────────────────────────────────
        _spacer(p, 20).pack(fill="x")
        _sep(p).pack(fill="x")
        _spacer(p, 20).pack(fill="x")
        self._section_lbl(p, "RATE LIMITS")
        rf = tk.Frame(p, bg=BG)
        rf.pack(fill="x", pady=(8, 0))
        self._d_primary = self._field(rf, "Primary window", "—")
        self._d_secondary = self._field(rf, "Secondary window", "—")

        # ── Detail: Credits ───────────────────────────────────────────────────
        _spacer(p, 20).pack(fill="x")
        _sep(p).pack(fill="x")
        _spacer(p, 20).pack(fill="x")
        self._section_lbl(p, "CREDITS & PLAN")
        cf = tk.Frame(p, bg=BG)
        cf.pack(fill="x", pady=(8, 0))
        self._d_credits = self._field(cf, "Balance", "—")
        self._d_plan_type = self._field(cf, "Plan type", "—")

    def _section_lbl(self, parent: tk.Frame, text: str) -> None:
        tk.Label(parent, text=text, bg=BG, fg=TMUT, font=F_LABEL).pack(anchor="w")

    def _field(self, parent: tk.Frame, label: str, value: str,
               mono: bool = False) -> tk.Label:
        """Labeled field row. Returns the value label."""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=BG, fg=TMUT, font=F_SEC,
                 width=17, anchor="w").pack(side="left")
        lbl = tk.Label(row, text=value, bg=BG, fg=TEXT,
                        font=F_MONO if mono else F_BODY, anchor="w")
        lbl.pack(side="left", fill="x", expand=True)
        return lbl

    def _build_actionbar(self) -> None:
        bar = tk.Frame(self, bg=PANEL, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        inner = tk.Frame(bar, bg=PANEL, padx=16)
        inner.pack(fill="both", expand=True)

        # Status text (left)
        tk.Label(inner, textvariable=self.status_var, bg=PANEL, fg=TMUT,
                 font=F_SEC).pack(side="left", anchor="center")

        # Buttons (right, RTL order)
        right = tk.Frame(inner, bg=PANEL)
        right.pack(side="right", anchor="center")

        _HoverBtn(right, "Storage dir", command=self.open_storage_hint,
                  fg=TMUT, bg=PANEL, bg_h=SURF, bg_p=SURF2,
                  padx=10, pady=5, font=F_SEC).pack(side="right", padx=(3, 0))

        tk.Frame(right, bg=BORDER, width=1).pack(side="right", fill="y",
                                                  padx=8, pady=8)

        _DangerBtn(right, "Delete", command=self.delete_selected).pack(
            side="right", padx=(3, 0))
        _HoverBtn(right, "Rename", command=self.rename_selected,
                  fg=TSEC, bg=PANEL, bg_h=SURF, bg_p=SURF2,
                  padx=10, pady=5).pack(side="right", padx=(3, 0))
        _HoverBtn(right, "Refresh limits", command=self.refresh_selected_limits,
                  fg=TSEC, bg=PANEL, bg_h=SURF, bg_p=SURF2,
                  padx=10, pady=5).pack(side="right", padx=(3, 0))
        _PrimaryBtn(right, "Activate", command=self.activate_selected).pack(
            side="right", padx=(3, 0))

    # ── Data helpers ──────────────────────────────────────────────────────────

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def refresh_accounts(self, select_active: bool = False) -> None:
        self.accounts = self.store.list_accounts()
        active_fp = self.manager.get_active_fingerprint()

        # Update topbar fingerprint
        if active_fp:
            short = f"{active_fp[:8]}…{active_fp[-4:]}"
            self._topbar_fp.config(text=f"Active fingerprint: {short}")
        else:
            self._topbar_fp.config(text="No active account")

        # Rebuild list
        for w in self._list_inner.winfo_children():
            w.destroy()
        self._rows.clear()

        self._count_lbl.config(text=str(len(self.accounts)))

        if not self.accounts:
            self._render_empty_state()
            self._selected_id = None
            self.clear_details()
            return

        selected_id: str | None = None
        for account in self.accounts:
            is_active = account.fingerprint == active_fp
            row = _AccountRow(
                self._list_inner, account, is_active,
                on_select=self._select_by_id,
                on_activate=self._activate_by_id,
            )
            row.pack(fill="x")
            tk.Frame(self._list_inner, bg=BORDER, height=1).pack(fill="x")
            self._rows[account.id] = row
            if select_active and is_active:
                selected_id = account.id

        target = selected_id or (self.accounts[0].id if self.accounts else None)
        if target:
            self._select_by_id(target)
        else:
            self.clear_details()

    def _render_empty_state(self) -> None:
        pad = tk.Frame(self._list_inner, bg=PANEL, pady=48)
        pad.pack(fill="x")
        tk.Label(pad, text="No accounts saved", bg=PANEL, fg=TSEC,
                 font=F_BODY).pack()
        tk.Label(pad, text='Use "+ Add" to save the current account',
                 bg=PANEL, fg=TMUT, font=F_SEC).pack(pady=(5, 0))

    def _select_by_id(self, account_id: str) -> None:
        if self._selected_id and self._selected_id in self._rows:
            self._rows[self._selected_id].select(False)
        self._selected_id = account_id
        if account_id in self._rows:
            self._rows[account_id].select(True)
        self.on_select_account()

    def _activate_by_id(self, account_id: str) -> None:
        self._select_by_id(account_id)
        self.activate_selected()

    def get_selected_account(self) -> StoredAccount | None:
        if not self._selected_id:
            return None
        for acc in self.accounts:
            if acc.id == self._selected_id:
                return acc
        return None

    def clear_details(self) -> None:
        self._d_name.config(text="Select an account")
        self._d_active_badge.config(text="", bg=BG, fg=BG)
        self._d_plan_badge.config(text="", bg=BG, fg=BG)
        self._d_email.config(text="—", fg=TEXT)
        self._d_account_id.config(text="—", fg=TEXT)
        self._d_fingerprint.config(text="—", fg=TEXT)
        self._d_primary.config(text="—", fg=TEXT)
        self._d_secondary.config(text="—", fg=TEXT)
        self._d_credits.config(text="—", fg=TEXT)
        self._d_plan_type.config(text="—", fg=TEXT)

    def on_select_account(self, _event=None) -> None:
        account = self.get_selected_account()
        if account is None:
            self.clear_details()
            return

        self._d_name.config(text=account.name)

        # Active badge
        active_fp = self.manager.get_active_fingerprint()
        if account.fingerprint == active_fp:
            self._d_active_badge.config(text=" active ", bg="#0f2a19", fg=ACCENT)
        else:
            self._d_active_badge.config(text="", bg=BG, fg=BG)

        # Plan badge
        plan = (account.auth.plan_type or "free").lower()
        pfg, pbg = _plan_colors(plan)
        self._d_plan_badge.config(text=f" {plan} ", bg=pbg, fg=pfg)

        # Profile fields
        self._d_email.config(text=account.auth.email or "—", fg=TEXT)
        self._d_account_id.config(text=account.auth.account_id or "—", fg=TEXT)
        fp = account.fingerprint or "—"
        self._d_fingerprint.config(
            text=(fp[:40] + "…") if len(fp) > 40 else fp, fg=TEXT)
        self._d_plan_type.config(text=account.auth.plan_type or "—", fg=TEXT)

        # Rate limits
        cached = self.rate_limit_cache.get(account.id)
        if cached is not None:
            self.render_rate_limits(cached)
        else:
            for lbl in (self._d_primary, self._d_secondary, self._d_credits):
                lbl.config(text="Loading…", fg=TMUT)
            self.fetch_limits_async(account)

    def render_rate_limits(self, snapshot: RateLimitSnapshot) -> None:
        self._d_primary.config(text=self._fmt_window(snapshot.primary), fg=TEXT)
        self._d_secondary.config(text=self._fmt_window(snapshot.secondary), fg=TEXT)
        if snapshot.credits_unlimited:
            credits_text = "Unlimited"
        else:
            credits_text = str(snapshot.credits_balance or "No credits")
        self._d_credits.config(text=credits_text, fg=TEXT)

    def _fmt_window(self, window: object) -> str:
        if window is None:
            return "No data"
        used = display_limit_percent(getattr(window, "used_percent", None))
        mins = getattr(window, "window_minutes", None)
        reset = getattr(window, "resets_at", None)
        usage = f"{used:.1f}%" if used is not None else "—"
        win_s = f"{mins} min" if mins else "—"
        rst_s = format_reset_timestamp(reset) or "—"
        return f"{usage} available  ·  {win_s} window  ·  resets {rst_s}"

    def fetch_limits_async(self, account: StoredAccount) -> None:
        self.set_status(f"Fetching limits for {account.name}…")

        def _worker() -> None:
            try:
                snapshot = fetch_rate_limits(account.auth)
            except Exception as exc:
                self.after(0, lambda: self._on_limits_error(str(exc)))
                return
            self.after(0, lambda: self._on_limits_loaded(account.id, snapshot))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_limits_loaded(self, account_id: str, snapshot: RateLimitSnapshot) -> None:
        self.rate_limit_cache[account_id] = snapshot
        sel = self.get_selected_account()
        if sel and sel.id == account_id:
            self.render_rate_limits(snapshot)
        self.set_status("Limits updated")

    def _on_limits_error(self, error: str) -> None:
        for lbl in (self._d_primary, self._d_secondary, self._d_credits):
            lbl.config(text="Error fetching data", fg=DANGER)
        self.set_status(f"Failed to fetch limits: {error}")

    # ── Actions ───────────────────────────────────────────────────────────────

    def refresh_selected_limits(self) -> None:
        account = self.get_selected_account()
        if not account:
            return
        self.rate_limit_cache.pop(account.id, None)
        self.fetch_limits_async(account)

    def activate_selected(self) -> None:
        account = self.get_selected_account()
        if account is None:
            messagebox.showinfo(APP_NAME, "Select an account first.")
            return
        self.manager.activate_account(account.id)
        self.set_status(f"Activated: {account.name}")
        self.refresh_accounts(select_active=True)

    def add_current_auth(self) -> None:
        if not self.active_auth_path.exists():
            messagebox.showerror(APP_NAME,
                                 f"File not found:\n{self.active_auth_path}")
            return
        profile = load_auth_file(self.active_auth_path)
        name = simpledialog.askstring(
            APP_NAME, "Account name", initialvalue=profile.display_name, parent=self)
        if name is None:
            return
        account = self.manager.add_current_account(name=name.strip() or None)
        self.set_status(f"Saved: {account.name}")
        self.refresh_accounts(select_active=True)

    def import_auth_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select auth.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            profile = load_auth_file(Path(path))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Failed to read auth.json:\n{exc}")
            return
        name = simpledialog.askstring(
            APP_NAME, "Account name", initialvalue=profile.display_name, parent=self)
        if name is None:
            return
        account = self.manager.import_auth_file(Path(path), name=name.strip() or None)
        self.set_status(f"Imported: {account.name}")
        self.refresh_accounts(select_active=False)
        self._select_by_id(account.id)

    def rename_selected(self) -> None:
        account = self.get_selected_account()
        if account is None:
            return
        name = simpledialog.askstring(
            APP_NAME, "New name", initialvalue=account.name, parent=self)
        if not name:
            return
        self.store.rename(account.id, name.strip())
        self.set_status("Account renamed")
        self.refresh_accounts(select_active=False)
        self._select_by_id(account.id)

    def delete_selected(self) -> None:
        account = self.get_selected_account()
        if account is None:
            return
        confirmed = messagebox.askyesno(
            APP_NAME, f"Delete '{account.name}' from storage?\n\nThis cannot be undone.")
        if not confirmed:
            return
        self.store.delete(account.id)
        self.rate_limit_cache.pop(account.id, None)
        self.refresh_accounts(select_active=True)
        self.set_status(f"Deleted: {account.name}")

    def open_storage_hint(self) -> None:
        APP_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        messagebox.showinfo(APP_NAME, f"Storage directory:\n{APP_STORAGE_DIR}")


def run() -> None:
    app = App()
    app.mainloop()
