"""A server's tab: the header card, the server's pages as tabs, and the
right panel (Online now, Coming up, Recent activity, and the Simple or
Technical switch). On a narrow window the panel moves under the Overview.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .activity import INTERNAL, ActivityPage, sentence
from .backups import BackupsPage
from .console import ConsolePage
from .mods import AddonsPage, ModsPage
from .overview import SimpleOverview, TechnicalOverview
from .performance import PerformancePage
from .players import PlayersPage
from .schedules import SchedulesPage
from .settings import SettingsPage
from .ui import Page, clock, content_page, has_packs, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

WIDE = 1100  # narrower than this, the right panel goes under the Overview
BUSY = {"STARTING", "STOPPING", "RESTARTING"}
STATE_WORDS = {
    "ONLINE": "state.online",
    "OFFLINE": "state.offline",
    "STARTING": "state.starting",
    "STOPPING": "state.stopping",
    "RESTARTING": "state.restarting",
    "RESTART_PENDING": "state.crashed",
    "CRASHED": "state.crashed",
}
# (page, words, icon, Technical only). The mods page is named after what
# the server takes: mods, plugins or add-ons (a Bedrock server's packs, on
# their own page), and isn't there for a type that takes none.
PAGES = [
    ("Overview", "page.overview", "overview", False),
    ("Players and chat", "desktop.page.players_chat", "players", False),
    ("Console", "page.console", "console", True),
    ("Backups", "page.backups", "backups", False),
    ("Mods", "page.mods", "mods", False),
    ("Schedules", "page.schedules", "schedules", False),
    ("Performance", "page.performance", "performance", True),
    ("Settings", "desktop.page.settings", "settings", False),
]
PAGE_CLASSES: dict[str, type[Page]] = {
    "Players and chat": PlayersPage,
    "Console": ConsolePage,
    "Backups": BackupsPage,
    "Mods": ModsPage,
    "Performance": PerformancePage,
    "Schedules": SchedulesPage,
    "Settings": SettingsPage,
}


def state_words(row: dict) -> str:
    return text(STATE_WORDS.get(row.get("state", ""), "state.unknown"))


def chips(row: dict) -> list[str]:
    """What the header says about the server, known values only."""
    out = [state_words(row)]
    version = " ".join(x for x in (row.get("type_name"), row.get("minecraft_version")) if x)
    if version:
        out.append(version)
    if mode["technical"] and (row.get("game_port") or {}).get("port"):
        out.append(text("desktop.chip.port", port=row["game_port"]["port"]))
    count = row.get("players_online")
    if row.get("state") == "ONLINE" and count is not None and row.get("players_verified"):
        out.append(text("head.players.one" if count == 1 else "head.players.other", count=count))
    return out


class Header(QFrame):
    """Icon, name, chips, named Undo, Restart, the big Start/Stop and ⋯."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        self.changes: dict = {}
        self.setProperty("kind", "card")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(14)
        self.badge = icons.Badge(56)
        outer.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        top = QHBoxLayout()
        self.name = label(name="h1")
        top.addWidget(self.name, 1)
        self.undo = QPushButton()
        self.undo.hide()  # until there is a change to undo
        self.undo.clicked.connect(lambda: window.move_change("undo"))
        self.restart = QPushButton(text("action.restart"))
        self.restart.clicked.connect(lambda: window.server_action(self.row["id"], "restart"))
        self.main = QPushButton()
        self.main.setProperty("kind", "primary")
        self.main.setProperty("big", True)
        self.main.clicked.connect(self.start_or_stop)
        self.more = QToolButton()
        self.more.setText("⋯")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self)
        self.redo = self.menu.addAction("")
        self.redo.triggered.connect(lambda: window.move_change("redo"))
        self.more.setMenu(self.menu)
        for widget in (self.undo, self.restart, self.main, self.more):
            top.addWidget(widget)
        column.addLayout(top)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        column.addLayout(self.chips)
        self.problem = label(kind="problem")
        self.problem.hide()
        column.addWidget(self.problem)
        # An automatic restart counting down, or restarts paused after crashes.
        notice = QHBoxLayout()
        self.notice = label(kind="problem")
        notice.addWidget(self.notice, 1)
        self.notice_buttons = {}
        for action, words in (
            ("cancel-restart", "action.cancel"),
            ("restart-now", "action.restart_now"),
            ("clear-crash-block", "notice.allow_restarts"),
        ):
            button = push(text(words))
            button.clicked.connect(
                lambda _=False, a=action: window.server_action(self.row["id"], a)
            )
            notice.addWidget(button)
            self.notice_buttons[action] = button
        column.addLayout(notice)
        self.deadline: float | None = None
        self.paused = ""
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self.show_notice)
        self.show_notice()
        outer.addLayout(column, 1)

    def start_or_stop(self) -> None:
        running = self.row.get("state") in ("ONLINE", *BUSY)
        self.window_.server_action(self.row["id"], "stop" if running else "start")

    def show_row(self, row: dict) -> None:
        if row["id"] != self.row.get("id"):
            # another server: its own status says if a restart is waiting
            self.deadline, self.paused = None, ""
            self.show_notice()
        self.row = row
        self.name.setText(row["name"])
        self.badge.show_server(row.get("color") or "#8E8E93", row["name"][:1].upper())
        state = row.get("state")
        action = "stop" if state in ("ONLINE", *BUSY) else "start"
        self.main.setText(text(f"action.{action}"))
        self.main.setAccessibleName(f"{text(f'action.{action}')} {row['name']}")
        self.main.setEnabled(state not in BUSY)
        control = self.window_.may(row["id"], "server.control")
        self.main.setVisible(control)
        self.restart.setVisible(state == "ONLINE" and control)
        self.more.setAccessibleName(text("desktop.more"))
        while self.chips.count():
            old = self.chips.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        for i, words in enumerate(chips(row)):
            chip = label(words, "chip")
            chip.setWordWrap(False)
            chip.setProperty("strong", i == 0 and state == "ONLINE")
            self.chips.addWidget(chip)
        self.chips.addStretch()

    def show_changes(self, changes: dict) -> None:
        self.changes = changes
        undo, redo = changes.get("undo") or [], changes.get("redo") or []
        if not self.window_.may(self.row["id"], "backups.restore"):
            undo, redo = [], []  # Undo and Redo restore a backup
        self.undo.setVisible(bool(undo))
        if undo:
            self.undo.setText(text("desktop.undo_named", title=undo[0]["title"]))
        self.redo.setEnabled(bool(redo))
        self.redo.setText(
            text("desktop.redo_named", title=redo[0]["title"])
            if redo
            else text("desktop.nothing_to_redo")
        )

    def show_status(self, status: dict) -> None:
        # restart_in, not restart_at: this PC's clock may differ from the agent's.
        left = status.get("restart_in")
        self.deadline = None if left is None else time.monotonic() + left
        self.paused = ""
        if status.get("auto_restart_blocked"):
            reason = status.get("auto_restart_block_reason") or ""
            self.paused = f"{text('notice.restart_paused')} {reason}".strip()
        self.show_notice()

    def show_notice(self) -> None:
        control = bool(self.row) and self.window_.may(self.row["id"], "server.control")
        words, actions = self.paused, ["clear-crash-block"]
        if self.deadline is not None:
            left = math.ceil(self.deadline - time.monotonic())
            words = text("hero.restarting_in", s=left) if left > 0 else text("hero.starting_now")
            actions = ["cancel-restart", "restart-now"]
        self.notice.setText(words)
        self.notice.setVisible(bool(words))
        for action, button in self.notice_buttons.items():
            button.setVisible(bool(words) and control and action in actions)
        if self.deadline is not None and self.isVisible():
            self.ticker.start()
        else:
            self.ticker.stop()

    def showEvent(self, event) -> None:
        self.show_notice()  # the countdown ticks only while it can be seen
        super().showEvent(event)

    def say(self, problem: str, kind: str = "problem") -> None:
        """What went wrong under the server's name, or with kind "ok" what went right."""
        self.problem.setText(problem)
        self.problem.setProperty("kind", kind)
        self.problem.setStyle(self.problem.style())
        self.problem.setVisible(bool(problem))


class Panel(QFrame):
    """Online now, Coming up, Recent activity, and the View switch."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.online_title = label(name="h2")
        self.online = QVBoxLayout()
        self.coming_title = label(name="h2")
        self.coming = QVBoxLayout()
        self.activity_title = label(name="h2")
        self.activity = QVBoxLayout()
        for part in (self.online_title, self.online, self.coming_title, self.coming):
            if isinstance(part, QWidget):
                layout.addWidget(part)
            else:
                layout.addLayout(part)
                layout.addSpacing(10)
        layout.addWidget(self.activity_title)
        layout.addLayout(self.activity)
        self.see_all = push("", "plain")
        self.see_all.clicked.connect(lambda: window.open_activity())
        layout.addWidget(self.see_all, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self.view = label(kind="muted")
        layout.addWidget(self.view)
        switch = QHBoxLayout()
        switch.setSpacing(0)
        self.group = QButtonGroup(self)
        self.simple, self.technical = QPushButton(), QPushButton()
        for button, technical in ((self.simple, False), (self.technical, True)):
            button.setCheckable(True)
            button.setProperty("kind", "segment")
            button.clicked.connect(lambda _=False, t=technical: window.set_mode(t))
            self.group.addButton(button)
            switch.addWidget(button, 1)
        layout.addLayout(switch)

    def show_words(self) -> None:
        self.coming_title.setText(text("desktop.panel.coming_up"))
        self.activity_title.setText(text("desktop.panel.activity"))
        self.see_all.setText(text("desktop.panel.see_all"))
        self.view.setText(text("desktop.panel.view"))
        self.simple.setText(text("mode.simple"))
        self.technical.setText(text("mode.technical"))
        (self.technical if mode["technical"] else self.simple).setChecked(True)

    def show_players(self, row: dict, players: dict) -> None:
        count, most = players.get("online_count"), players.get("max_players") or "?"
        # None: the player list isn't checked yet, so no number is shown.
        shown = "?" if count is None else count
        self.online_title.setText(text("players.online", count=shown, max=most))
        if row.get("state") != "ONLINE":
            refill(self.online, [label(text("desktop.panel.server_off"), "sub")])
            return
        people = players.get("online") or []
        rows = []
        for person in people:
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(label(person["username"]), 1)
            h.addWidget(label(clock(person.get("session_started")), "muted"))
            rows.append(line)
        empty = text("state.unknown") if count is None else text("players.nobody")
        refill(self.online, rows or [label(empty, "sub")])

    def show_schedules(self, schedules: list[dict]) -> None:
        upcoming = sorted(
            (s for s in schedules if s.get("enabled") and s.get("next_run")),
            key=lambda s: s["next_run"],
        )[:3]
        refill(
            self.coming,
            [label(f"{clock(s['next_run'])}  {s['name']}") for s in upcoming]
            or [label(text("desktop.panel.nothing_planned"), "sub")],
        )

    def show_events(self, events: list[dict]) -> None:
        shown = [e for e in events if mode["technical"] or e.get("type") not in INTERNAL]
        refill(
            self.activity,
            [label(f"{clock(e.get('ts'))}  {sentence(e)}") for e in shown[:5]]
            or [label(text("desktop.panel.no_activity"), "sub")],
        )


class ServerScreen(QWidget):
    NAME = "Server"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        band = QFrame()
        band.setObjectName("band")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(band)
        body = QHBoxLayout(band)
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)
        center = QVBoxLayout()
        center.setSpacing(10)
        self.header = Header(window)
        center.addWidget(self.header)
        # The page tabs scroll sideways when they don't fit (big text sizes).
        row = QWidget()
        self.tab_row = QHBoxLayout(row)
        self.tab_row.setContentsMargins(0, 0, 0, 0)
        self.tab_row.setSpacing(4)
        tab_scroll = QScrollArea()
        tab_scroll.setWidget(row)
        tab_scroll.setWidgetResizable(True)
        tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tab_scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.tab_scroll = tab_scroll
        center.addWidget(tab_scroll)
        self.tabs = QButtonGroup(self)
        self.pages = QStackedWidget()
        center.addWidget(self.pages, 1)
        body.addLayout(center, 1)
        self.side = QVBoxLayout()
        body.addLayout(self.side)
        self.panel = Panel(window)

        # The Overview scrolls, with room under it for the panel on narrow windows.
        overview = QWidget()
        self.overview = QVBoxLayout(overview)
        self.overview.setContentsMargins(0, 0, 0, 0)
        self.simple_overview = SimpleOverview(window)
        self.technical_overview = TechnicalOverview(window)
        self.overview.addWidget(self.simple_overview)
        self.overview.addWidget(self.technical_overview)
        self.overview_cards = self.simple_overview.cards
        self.under = QVBoxLayout()
        self.overview.addLayout(self.under)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(overview)
        self.page_widgets: dict[str, QWidget] = {"Overview": scroll}
        self.pages.addWidget(scroll)
        for page, _words, _icon, _technical in PAGES[1:]:
            self.page_widgets[page] = PAGE_CLASSES[page](window)
            self.pages.addWidget(self.page_widgets[page])
        self.activity = ActivityPage(window)  # from "See all" and crash alerts, not a tab
        self.page_widgets["Activity"] = self.activity
        self.pages.addWidget(self.activity)
        self.page_widgets["Add-ons"] = AddonsPage(window)  # a Bedrock server's Mods tab
        self.pages.addWidget(self.page_widgets["Add-ons"])
        self.current_page = "Overview"
        self.place_panel()

    def build_tabs(self) -> None:
        """The page tabs for the mode: 6 in Simple, 8 in Technical."""
        for button in self.tabs.buttons():
            self.tabs.removeButton(button)
            button.setParent(None)
        while self.tab_row.count():
            self.tab_row.takeAt(0)
        self.simple_overview.setVisible(not mode["technical"])
        self.technical_overview.setVisible(mode["technical"])
        content = content_page(self.row)
        shown = [
            p
            for p in PAGES
            if (mode["technical"] or not p[3]) and (p[0] != "Mods" or content is not None)
        ]
        if self.current_page not in [self.real(p[0]) for p in shown] + ["Activity"]:
            self.current_page = "Overview"
        for page, words, icon, _technical in shown:
            if page == "Mods":
                words = content or words
            page = self.real(page)
            button = QToolButton()
            button.setObjectName("pageTab")
            button.setText(" " + text(words))
            button.setAccessibleName(text(words))
            button.setIcon(icons.icon(icon, self.window_.look["text-primary"]))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setProperty("page", page)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.clicked.connect(lambda _=False, p=page: self.show_page(p))
            self.tabs.addButton(button)
            self.tab_row.addWidget(button)
        self.tab_row.addStretch()
        buttons = self.tabs.buttons()
        for button in buttons:
            button.ensurePolished()  # so its size counts the style sheet's padding
        tall = max(b.sizeHint().height() for b in buttons)
        self.tab_scroll.setFixedHeight(
            tall + self.tab_scroll.horizontalScrollBar().sizeHint().height()
        )
        self.show_page(self.current_page)

    def real(self, page: str) -> str:
        """The page shown for a tab: a Bedrock server's Mods are its add-ons."""
        return "Add-ons" if page == "Mods" and has_packs(self.row) else page

    def show_page(self, page: str) -> None:
        page = self.real(page)
        self.current_page = page
        self.pages.setCurrentWidget(self.page_widgets[page])
        self.tabs.setExclusive(False)  # so Activity, not a tab, leaves none checked
        for button in self.tabs.buttons():
            button.setChecked(button.property("page") == page)
        self.tabs.setExclusive(True)
        self.header.say("")
        if isinstance(self.page, Page) and self.row:
            self.page.load()

    @property
    def page(self) -> QWidget:
        return self.page_widgets[self.current_page]

    def changed(self) -> None:
        """The feed says something about this server changed."""
        if isinstance(self.page, Page):
            self.page.changed()

    def live(self, event: dict) -> None:
        if isinstance(self.page, Page):
            self.page.live(event)

    def show_row(self, row: dict) -> None:
        changed = row.get("id") != self.row.get("id") or content_page(row) != content_page(self.row)
        self.row = row
        self.simple_overview.row = self.technical_overview.row = row
        for widget in self.page_widgets.values():
            if isinstance(widget, Page):
                widget.row = row
        self.header.show_row(row)
        self.panel.show_words()
        if changed:
            self.build_tabs()

    def place_panel(self) -> None:
        wide = self.window().width() >= WIDE
        target = self.side if wide else self.under
        if self.panel.parent() is None or target.indexOf(self.panel) < 0:
            target.addWidget(self.panel)
        self.panel.setFixedWidth(260) if wide else self.panel.setMaximumWidth(16777215)
        if not wide:
            self.panel.setMinimumWidth(0)

    def resizeEvent(self, event) -> None:
        self.place_panel()
        super().resizeEvent(event)
