"""A server's tab: the header, the open page, and the side notes (Online
now, Coming up, Recent activity, and the Simple or Technical switch), all on
the sheet. The list of its pages sits in the sheet's left margin. On a
narrow window the side notes move under the Overview.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import motion
from .activity import INTERNAL, ActivityPage, sentence
from .backups import BackupsPage
from .console import ConsolePage
from .mods import AddonsPage, ModsPage
from .overview import SimpleOverview, TechnicalOverview
from .parts import Face, IconButton, Segmented, Status
from .performance import PerformancePage
from .players import PlayersPage
from .quests import QuestsPage
from .schedules import SchedulesPage
from .settings import SettingsPage
from .shell import PageList
from .store import StorePage, store_tab
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
    ("Quests and events", "desktop.page.quests", "quests", False),
    ("Performance", "page.performance", "performance", True),
    ("Store", "desktop.page.store", "store", True),
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
    "Quests and events": QuestsPage,
    "Store": StorePage,
}


def state_words(row: dict) -> str:
    if row.get("state") == "ONLINE" and (row.get("answering") or {}).get("ok") is False:
        return text("state.not_answering")
    return text(STATE_WORDS.get(row.get("state", ""), "state.unknown"))


def state_mark(row: dict) -> str:
    """Which of the status mark's four colors a server's state gets."""
    state = row.get("state")
    if state == "ONLINE":
        return "bad" if (row.get("answering") or {}).get("ok") is False else "on"
    if state in BUSY:
        return "wait"
    return "bad" if state in ("CRASHED", "RESTART_PENDING") else "off"


def facts(row: dict, port: bool = True, players: bool = True) -> list[str]:
    """What is said about a server after its status mark, known values only."""
    out = []
    if row.get("test"):
        out.append(text("test.badge"))
    version = " ".join(x for x in (row.get("type_name"), row.get("minecraft_version")) if x)
    if version:
        out.append(version)
    if port and mode["technical"] and (row.get("game_port") or {}).get("port"):
        out.append(text("desktop.chip.port", port=row["game_port"]["port"]))
    count = row.get("players_online")
    if (
        players
        and row.get("state") == "ONLINE"
        and count is not None
        and row.get("players_verified")
    ):
        out.append(text("head.players.one" if count == 1 else "head.players.other", count=count))
    return out


class Header(QFrame):
    """Name, the status mark and a line of facts, named Undo, Diagnose
    server and Restart as icon buttons, the big Start/Stop and ⋯."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        self.changes: dict = {}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        column = QVBoxLayout()
        top = QHBoxLayout()
        self.name = label(name="h1")
        top.addWidget(self.name, 1)
        self.undo = QPushButton()
        self.undo.hide()  # until there is a change to undo
        self.undo.clicked.connect(lambda: window.move_change("undo"))
        self.restart = IconButton("restart", text("action.restart"))
        self.restart.clicked.connect(lambda: window.server_action(self.row["id"], "restart"))
        self.diagnose = IconButton("performance", text("action.diagnose"))
        self.diagnose.clicked.connect(self.open_diagnose)
        self.main = QPushButton()
        self.main.setProperty("big", True)
        self.main.clicked.connect(self.start_or_stop)
        self.more = QToolButton()
        self.more.setText("⋯")
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self)
        self.redo = self.menu.addAction("")
        self.redo.triggered.connect(lambda: window.move_change("redo"))
        self.folder = self.menu.addAction(text("server.open_folder"))
        self.folder.triggered.connect(lambda: window.pc.open_folder(self.row["directory"]))
        self.more.setMenu(self.menu)
        for widget in (self.undo, self.diagnose, self.restart, self.main, self.more):
            top.addWidget(widget)
        column.addLayout(top)
        under = QHBoxLayout()
        under.setSpacing(10)
        self.status = Status()
        self.facts = label(kind="sub")
        under.addWidget(self.status)
        under.addWidget(self.facts, 1)
        column.addLayout(under)
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
            ("restart-when-empty", "action.restart_when_empty"),
            ("cancel-restart-when-empty", "action.cancel"),
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
        # Changes waiting for a restart, and whether it restarts when nobody's on.
        self.waiting: list[str] = []
        self.when_empty = False
        self.ticker = QTimer(self)
        self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self.show_notice)
        self.show_notice()
        outer.addLayout(column, 1)

    def open_diagnose(self) -> None:
        from .diagnose import DiagnoseWindow

        DiagnoseWindow(self.window_, self.row["id"], self.row["name"]).show()

    def start_or_stop(self) -> None:
        running = self.row.get("state") in ("ONLINE", *BUSY)
        self.window_.server_action(self.row["id"], "stop" if running else "start")

    def show_row(self, row: dict) -> None:
        if row["id"] != self.row.get("id"):
            # another server: its own status says if a restart is waiting
            self.deadline, self.paused = None, ""
            self.waiting, self.when_empty = [], False
            self.show_notice()
        self.row = row
        self.name.setText(row["name"])
        state = row.get("state")
        action = "stop" if state in ("ONLINE", *BUSY) else "start"
        self.main.setText(text(f"action.{action}"))
        self.main.setAccessibleName(f"{text(f'action.{action}')} {row['name']}")
        self.main.setEnabled(state not in BUSY)
        # Only Start is filled: one filled button on a screen.
        self.main.setProperty("kind", "primary" if action == "start" else "")
        self.main.setStyle(self.main.style())
        control = self.window_.may(row["id"], "server.control")
        self.main.setVisible(control)
        self.restart.setVisible(state == "ONLINE" and control)
        self.diagnose.setVisible(self.window_.may(row["id"], "server.diagnose"))
        self.more.setAccessibleName(text("desktop.more"))
        # Only on the server's own PC: elsewhere the path is another PC's.
        here = self.window_.agent.place == "this PC" and self.window_.role in (None, "owner")
        self.folder.setVisible(here and bool(row.get("directory")))
        self.status.say(state_mark(row), state_words(row))
        self.facts.setText(" · ".join(facts(row)))

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
        self.waiting = list(status.get("restart_waiting") or [])
        self.when_empty = bool(status.get("restart_when_empty"))
        self.show_notice()

    def show_notice(self) -> None:
        control = bool(self.row) and self.window_.may(self.row["id"], "server.control")
        words, actions = self.paused, ["clear-crash-block"]
        if not words and self.when_empty:
            words, actions = text("restart.when_empty_set"), ["cancel-restart-when-empty"]
        elif not words and self.waiting:
            what = ", ".join(text(f"restart.what.{w}") for w in self.waiting)
            words, actions = text("restart.waiting", what=what), ["restart-when-empty"]
        if self.deadline is not None:
            # Rounded first: (t + 30) - t can come out a hair over 30 in floats.
            left = math.ceil(round(self.deadline - time.monotonic(), 3))
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
    """Online now, Coming up, Recent activity, and Simple or Technical."""

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
        self.view = Segmented(
            [("simple", text("mode.simple")), ("technical", text("mode.technical"))],
            "technical" if mode["technical"] else "simple",
            lambda key: window.set_mode(key == "technical"),
        )
        self.simple, self.technical = self.view.buttons.values()
        layout.addWidget(self.view, 0, Qt.AlignmentFlag.AlignHCenter)

    def show_words(self) -> None:
        self.coming_title.setText(text("desktop.panel.coming_up"))
        self.activity_title.setText(text("desktop.panel.activity"))
        self.see_all.setText(text("desktop.panel.see_all"))
        self.simple.setText(text("mode.simple"))
        self.technical.setText(text("mode.technical"))
        self.view.choose("technical" if mode["technical"] else "simple")

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
            h.addWidget(Face(person["username"]))
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
        body = self.body = QHBoxLayout(self)
        body.setContentsMargins(20, 18, 20, 16)
        body.setSpacing(16)
        center = QVBoxLayout()
        center.setSpacing(12)
        self.header = Header(window)
        center.addWidget(self.header)
        # The window puts the page list in the sheet's left margin.
        self.page_list = PageList(window.pc.animations)
        self.tabs = self.page_list.group
        self.pages = QStackedWidget()
        self.pages.currentChanged.connect(lambda: motion.turn(self.pages))
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
        """The page tabs for the mode: 7 in Simple, 9 in Technical (one fewer
        without Quests and events, one more with the owner's Store)."""
        self.simple_overview.setVisible(not mode["technical"])
        self.technical_overview.setVisible(mode["technical"])
        content = content_page(self.row)
        shown = [
            p
            for p in PAGES
            if (mode["technical"] or not p[3])
            and (p[0] != "Mods" or content is not None)
            # Only for people who may run quests here (the owner, Quests and events).
            and (p[0] != "Quests and events" or self.window_.may(self.row["id"], "quests.manage"))
            # Only the owner, only on a community server (agent/store.py).
            and (p[0] != "Store" or (self.window_.role in (None, "owner") and store_tab(self.row)))
        ]
        if self.current_page not in [self.real(p[0]) for p in shown] + ["Activity"]:
            self.current_page = "Overview"
        self.page_list.fill(
            [
                (self.real(page), text(content if page == "Mods" and content else words), icon)
                for page, words, icon, _technical in shown
            ],
            self.show_page,
        )
        self.show_page(self.current_page)

    def real(self, page: str) -> str:
        """The page shown for a tab: a Bedrock server's Mods are its add-ons."""
        return "Add-ons" if page == "Mods" and has_packs(self.row) else page

    def show_page(self, page: str) -> None:
        page = self.real(page)
        self.current_page = page
        self.pages.setCurrentWidget(self.page_widgets[page])
        self.page_list.mark(page)
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
        self.panel.setFixedWidth(236) if wide else self.panel.setMaximumWidth(16777215)
        if self.panel.property("under") != (not wide):  # the line is on its left, or above it
            self.panel.setProperty("under", not wide)
            self.panel.setStyle(self.panel.style())
        if not wide:
            self.panel.setMinimumWidth(0)

    def minimumSizeHint(self) -> QSize:
        # The side panel only stays while the window is wide, so it must not
        # keep the window from getting narrow enough to move it under.
        hint = super().minimumSizeHint()
        if self.side.indexOf(self.panel) >= 0:
            hint.setWidth(hint.width() - self.panel.width() - self.body.spacing())
        return hint

    def resizeEvent(self, event) -> None:
        self.place_panel()
        super().resizeEvent(event)
