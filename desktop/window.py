"""The desktop app's main window: sign-in, then binder tabs for All servers,
each server and "+". The open server's color is on its tab, the band behind
the sheet, the buttons and the edges.

Every call to the agent runs off the window's thread (``MainWindow.run``),
so a slow network never freezes the window.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QMimeData,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QDrag, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent import API_VERSION, appinfo
from agent import __version__ as APP_VERSION

from . import icons, motion, self_update, streamer, theme, ui
from .agent import AgentClient, AgentError, SignedOut, Unreachable
from .alerts import KEPT, PAGES, PC_PAGES, RESTART_FOR, NotificationsScreen
from .backups import is_world
from .discord import Discord
from .feed import Feed
from .frame import ROOM, FramelessWindow
from .gear import Account, Devices, ListScreen, PCSettings, Updates
from .help import About, Guide
from .moderators import Moderators
from .mods import takes
from .new_server import NewServerScreen
from .overview import SimpleOverview, TechnicalOverview
from .parts import Box, Status
from .pc import WindowsPC
from .rented import ConnectPanel, RentedRow, RentedScreen
from .server_tab import ServerScreen, facts, main_action, state_mark, state_words
from .shell import BinderTabs, Desk, Sheet
from .tailscale_step import TailscaleStep
from .top_view import TopView
from .ui import clock, content_page, has_packs, label, push, refill
from .words import mode, text
from .yourpcs import YourPCs

log = logging.getLogger(__name__)

# The live feed's events that can play a sound on this PC, and their switch.
SOUNDS = {"server_crashed": "sound_crash", "player_joined": "sound_join"}


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class Task(QRunnable):
    def __init__(self, work: Callable[[], Any]) -> None:
        super().__init__()
        self.work = work
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.work()
        except AgentError as exc:
            self.signals.failed.emit(exc)
            return
        except Exception as exc:
            log.exception("desktop task failed")
            self.signals.failed.emit(AgentError(f"Something went wrong: {exc}"))
            return
        self.signals.done.emit(result)


class SignInScreen(QWidget):
    NAME = "Sign-in"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        sheet = QFrame()
        sheet.setObjectName("sheet")
        form = QVBoxLayout(sheet)
        form.setContentsMargins(28, 24, 28, 24)
        form.setSpacing(10)
        title = label(text("app.name"))
        title.setObjectName("appName")
        form.addWidget(title)
        self.username = QLineEdit()
        self.username.setObjectName("username")
        self.username.setPlaceholderText(text("login.username"))
        self.username.setAccessibleName(text("login.username"))
        # Filled in by the installer, so after its one-time sign-in runs out
        # only the password is needed.
        self.username.setText(window.pc.setting("sign_in_username") or "")
        self.password = QLineEdit()
        self.password.setObjectName("password")
        self.password.setPlaceholderText(text("login.password"))
        self.password.setAccessibleName(text("login.password"))
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox(text("login.remember"))
        self.remember.setObjectName("remember")
        self.problem = label(kind="problem")
        self.problem.hide()
        self.button = push(text("login.sign_in"), "primary")
        self.button.clicked.connect(self.sign_in)
        self.password.returnPressed.connect(self.sign_in)
        for widget in (self.username, self.password, self.remember, self.problem, self.button):
            form.addWidget(widget)
        outer = QVBoxLayout(self)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        sheet.setFixedWidth(360)
        row.addWidget(sheet)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def say(self, problem: str) -> None:
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))

    def sign_in(self) -> None:
        username, password = self.username.text().strip(), self.password.text()
        if not username or not password:
            return
        self.button.setEnabled(False)
        self.button.setText(text("login.signing_in"))
        agent, device = self.window_.agent, self.window_.pc.device_name()
        remember = self.remember.isChecked()

        def done(_result) -> None:
            self.password.clear()
            self.say("")
            self.window_.signed_in(remember)

        def failed(exc: AgentError) -> None:
            self.say(str(exc))

        def always() -> None:
            self.button.setEnabled(True)
            self.button.setText(text("login.sign_in"))

        self.window_.run(
            lambda: agent.sign_in(username, password, remember, device),
            done,
            failed,
            always,
        )


DRAGGED = "application/x-command-block-server"  # a server row being dragged


class ServerRow(Box):
    """One server on All servers: a raised box with its edge, color bar and
    Start button in the server's own color. ``act`` starts or stops a
    server on another of the owner's PCs (that PC checks the account may).
    ``arrange`` (the owner, this PC's servers): a ⋯ menu to move it or put
    it in a group, and the row can be dragged to a new place."""

    def __init__(
        self,
        window: MainWindow,
        row: dict,
        act: Callable[[str], None] | None = None,
        arrange: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__()
        self.server_id = row["id"]
        self.arrange = arrange
        self.pressed: QPoint | None = None
        # The parts in the box take their colors from here, not from the window.
        self.look = theme.tokens(window.dark, row.get("color"))
        self.setStyleSheet(theme.row_sheet(self.look))
        line = QHBoxLayout()
        line.setSpacing(12)
        self.column.addLayout(line)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {row.get('color') or self.look['neutral']};")
        line.addWidget(bar)
        words = QVBoxLayout()
        words.setSpacing(2)
        name = label(row["name"])
        name.setObjectName("title")
        words.addWidget(name)
        state = row.get("state", "UNKNOWN")
        said = facts(row, port=False, players=False)
        if state == "ONLINE":
            players = row.get("players_online")
            said.append(
                text("servers.players_of", n=players, max=row.get("max_players") or "?")
                if players is not None
                else text("state.unknown")
            )
        under = QHBoxLayout()
        under.setSpacing(10)
        under.addWidget(Status(state_mark(row), state_words(row)))
        under.addWidget(label(" · ".join(said), "sub"), 1)
        words.addLayout(under)
        line.addLayout(words, 1)
        action, allowed = main_action(row)
        button = push(text(f"action.{action}"), "primary" if action == "start" else "")
        button.setEnabled(allowed)
        button.setAccessibleName(f"{text(f'action.{action}')} {row['name']}")
        if act is not None:
            button.clicked.connect(lambda: act(action))
        else:
            button.clicked.connect(lambda: window.server_action(row["id"], action))
            button.setVisible(window.may(row["id"], "server.control"))
        line.addWidget(button)
        if arrange is not None:
            self.setToolTip(text("servers.drag_hint"))
            more = QToolButton()
            more.setText("⋯")
            more.setAccessibleName(text("servers.more", name=row["name"]))
            more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu = QMenu(more)
            for how, key in (
                ("up", "servers.move_up"),
                ("down", "servers.move_down"),
                ("group", "servers.group"),
            ):
                menu.addAction(text(key)).triggered.connect(
                    lambda _=False, h=how: arrange(row["id"], h)
                )
            more.setMenu(menu)
            line.addWidget(more)

    def mousePressEvent(self, event) -> None:
        self.pressed = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        moved = self.pressed is not None and (
            (event.position().toPoint() - self.pressed).manhattanLength()
            >= QApplication.startDragDistance()
        )
        if self.arrange is None or not moved:
            return super().mouseMoveEvent(event)
        self.pressed = None
        drag = QDrag(self)
        data = QMimeData()
        data.setData(DRAGGED, self.server_id.encode())
        drag.setMimeData(data)
        drag.setPixmap(self.grab())
        drag.exec(Qt.DropAction.MoveAction)


class NoticeScreen(QWidget):
    """A whole-window message: the agent isn't running, or an update is
    needed. At most one button."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.NAME = name
        sheet = QFrame()
        sheet.setObjectName("sheet")
        sheet.setFixedWidth(440)
        column = QVBoxLayout(sheet)
        column.setContentsMargins(28, 24, 28, 24)
        column.setSpacing(10)
        self.heading = label()
        self.heading.setObjectName("title")
        column.addWidget(self.heading)
        self.words = label()
        column.addWidget(self.words)
        self.note = label(kind="problem")
        self.note.hide()
        column.addWidget(self.note)
        self.button = push("", "primary")
        self.button.clicked.connect(lambda: self.action and self.action())
        column.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        self.action: Callable[[], None] | None = None
        outer = QVBoxLayout(self)
        outer.addStretch()
        row = QHBoxLayout()  # (an aligned widget would cut its wrapped words short)
        row.addStretch()
        row.addWidget(sheet)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def say(
        self, heading: str, words: str, button: str = "", action: Callable[[], None] | None = None
    ) -> None:
        self.heading.setText(heading)
        self.words.setText(words)
        self.note.hide()
        self.button.setText(button)
        self.button.setVisible(bool(button))
        self.action = action


class AllServersScreen(QWidget):
    """Every server as a raised box, under small headings where there is
    more than one group (this PC, rented servers, the owner's other PCs),
    then a dashed card to add one. With no server at all, only that."""

    NAME = "All servers"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        self.heading = label(text("page.servers"), name="h1")
        outer.addWidget(self.heading)
        outer.addSpacing(8)
        self.problem = label(kind="problem")
        self.problem.hide()
        outer.addWidget(self.problem)
        self.note = label(kind="sub")  # which side is older, when the versions differ
        self.note.hide()
        outer.addWidget(self.note)
        self.said = label(kind="ok")  # what was just done here, like a server removed
        self.said.hide()
        outer.addWidget(self.said)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(text("servers.search"))
        self.search.setAccessibleName(text("servers.search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.show_all)
        tools.addWidget(self.search, 1)
        self.start_all = push(text("servers.start_all"), "plain")
        self.start_all.clicked.connect(lambda: self.all_servers("start"))
        self.stop_all = push(text("servers.stop_all"), "plain")
        self.stop_all.clicked.connect(lambda: self.all_servers("stop"))
        tools.addWidget(self.start_all)
        tools.addWidget(self.stop_all)
        self.tools = QWidget()
        self.tools.setLayout(tools)
        tools.setContentsMargins(0, 0, 0, 4)
        outer.addWidget(self.tools)
        self.setAcceptDrops(True)
        self.placed: list[ServerRow] = []  # this PC's rows, which a dragged one can land on
        body = QWidget()
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)  # a box brings its own room for its shadow
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.servers: list[dict] = []
        self.add_button = self.connect_button = QPushButton()  # made again with each fill

    def add_card(self, first: bool) -> QWidget:
        """Add server, and for the owner Connect a rented server (on its
        Pterodactyl panel). ``first``: there is no server yet, so it is the
        whole page, with the filled button."""
        window = self.window_
        self.add_button = push(text("page.add_server"), "primary" if first else "plain")
        look = theme.tokens(window.dark)  # no server is open here: the copper
        self.add_button.setIcon(
            icons.icon("plus", look["accent-text" if first else "accent-ink"], 16)
        )
        self.add_button.clicked.connect(lambda: window.show_screen("New server"))
        self.connect_button = push(text("desktop.rented.connect"), "plain")
        self.connect_button.clicked.connect(lambda: ConnectPanel(window).exec())
        self.connect_button.setVisible(window.role in (None, "owner"))
        card = QFrame()
        buttons: QBoxLayout
        if first:
            self.add_button.setProperty("big", True)
            buttons = QVBoxLayout(card)
            buttons.setContentsMargins(0, 60, 0, 0)
            none = label(text("servers.none"), name="h2")
            buttons.addWidget(none, 0, Qt.AlignmentFlag.AlignHCenter)
            buttons.addSpacing(8)
            for button in (self.add_button, self.connect_button):
                buttons.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            return card
        card.setObjectName("newServer")
        buttons = QHBoxLayout(card)
        buttons.setContentsMargins(12, 10, 12, 10)
        buttons.addStretch()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.connect_button)
        buttons.addStretch()
        return card

    def failed(self, exc: AgentError) -> None:
        self.problem.setText(str(exc))
        self.problem.show()

    def say(self, words: str) -> None:
        self.said.setText(words)
        self.said.setVisible(bool(words))

    def fill(self, servers: list[dict]) -> None:
        self.problem.hide()
        self.servers = servers
        self.show_all()

    def matches(self, row: dict) -> bool:
        needle = self.search.text().strip().lower()
        return not needle or needle in f"{row.get('name', '')} {row.get('group') or ''}".lower()

    def show_all(self) -> None:
        """This PC's servers (under their group names, when the owner made
        groups), then each of the owner's other PCs' (Your PCs), under the
        PC's name. The search box narrows all of them by name or group."""
        refill(self.rows, [])
        window = self.window_
        others = window.other_pcs()
        group = lambda words: label(words, "group")  # noqa: E731
        everywhere = (
            len(self.servers)
            + len(window.rented)
            + sum(len(found) for found in window.elsewhere.values() if isinstance(found, list))
        )
        self.tools.setVisible(everywhere >= 2)
        control = [r for r in self.servers if window.may(r["id"], "server.control")]
        self.start_all.setVisible(len(control) >= 2)
        self.stop_all.setVisible(len(control) >= 2)
        if others or (window.rented and self.servers):
            self.rows.addWidget(group(window.this_pc_name()))
        shown = [r for r in self.servers if self.matches(r)]
        arrange = self.arrange if window.role in (None, "owner") else None
        names = [g for g in dict.fromkeys(r.get("group") or "" for r in shown) if g]
        self.placed = []
        for name in [*names, ""] if names else [""]:
            if names:
                self.rows.addWidget(label(name or text("servers.no_group"), "step"))
            for row in shown:
                if (row.get("group") or "") == name:
                    self.placed.append(ServerRow(window, row, arrange=arrange))
                    self.rows.addWidget(self.placed[-1])
        if self.search.text().strip() and not shown and self.servers:
            self.rows.addWidget(label(text("servers.no_match", search=self.search.text()), "sub"))
        if window.rented:
            self.rows.addWidget(group(text("desktop.rented.heading_list")))
            for row in window.rented:
                if self.matches(row):
                    self.rows.addWidget(RentedRow(window, row))
        for pc in others:
            self.rows.addWidget(group(pc.get("name") or ""))
            found = window.elsewhere.get(pc["id"])
            if found is None:
                self.rows.addWidget(label(text("desktop.pcs.loading"), "sub"))
            elif isinstance(found, str):
                self.rows.addWidget(label(found, "sub"))
            elif not found:
                self.rows.addWidget(label(text("servers.none"), "sub"))
            else:
                for row in filter(self.matches, found):
                    act = lambda action, p=pc, r=row: window.act_elsewhere(p, r, action)  # noqa: E731
                    self.rows.addWidget(ServerRow(window, row, act))
        first = not (self.servers or window.rented or others)
        if not first:
            self.rows.addSpacing(8)
        self.rows.addWidget(self.add_card(first))
        self.rows.addStretch()

    # -- the owner's order and groups -------------------------------------
    def arrange(self, server_id: str, how: str) -> None:
        """The ⋯ menu: move a server up or down among the ones shown, or put
        it in a group."""
        window = self.window_
        if how == "group":
            row = next(r for r in self.servers if r["id"] == server_id)
            name = window.ask(
                text("servers.group_title"),
                text("servers.group_ask"),
                text("action.save"),
                text("servers.group_title"),
                row.get("group") or "",
            )
            if name is not None:
                self.put(f"/servers/{server_id}/group", {"group": name.strip()})
            return
        group_of = {r["id"]: r.get("group") or "" for r in self.servers}
        # Among the shown servers of its own group: the group stays as it is.
        shown = [r.server_id for r in self.placed if group_of[r.server_id] == group_of[server_id]]
        at = shown.index(server_id) + (-1 if how == "up" else 1)
        if 0 <= at < len(shown):
            self.move_to(server_id, shown[at], after=how == "down")

    def move_to(self, server_id: str, target: str, after: bool) -> None:
        """Put a server just before (or after) another. Landing among another
        group's servers puts it in that group."""
        order = [
            r["id"] for r in self.window_.all_servers
        ]  # with Test servers, so they keep their place
        order.remove(server_id)
        order.insert(order.index(target) + after, server_id)
        group_of = {r["id"]: r.get("group") or "" for r in self.servers}
        if group_of[server_id] != group_of[target]:
            self.put(f"/servers/{server_id}/group", {"group": group_of[target]})
        self.put("/servers/order", {"ids": order})

    def put(self, path: str, body: dict) -> None:
        window = self.window_
        window.run(
            lambda: window.agent.call("PUT", path, body), lambda _r: window.refresh(), self.failed
        )

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(DRAGGED):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(DRAGGED):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Dropped on a row: before it when on its top half, after it when
        on its bottom half. Below the last row: after the last."""
        dragged = bytes(event.mimeData().data(DRAGGED).data()).decode()
        at = self.mapToGlobal(event.position().toPoint())
        for row in self.placed:
            spot = row.mapFromGlobal(at)
            if row.rect().contains(spot) or row is self.placed[-1] and spot.y() > 0:
                if row.server_id != dragged:
                    event.acceptProposedAction()
                    self.move_to(dragged, row.server_id, after=spot.y() > row.height() / 2)
                return

    def all_servers(self, action: str) -> None:
        """Start all or Stop all, for the servers shown (the search narrows it)."""
        window = self.window_
        ids = [r.server_id for r in self.placed if window.may(r.server_id, "server.control")]
        if action == "stop" and not window.confirm(
            text(
                "servers.stop_all_title",
                n=sum(r.get("state") != "OFFLINE" for r in self.servers if r["id"] in ids),
            ),
            text("servers.stop_all_body"),
            text("servers.stop_all"),
        ):
            return

        def done(result: dict) -> None:
            rows = result.get("servers") or []
            done_ = [r for r in rows if r["result"] in ("started", "stopped", "stopping")]
            failed = [
                text("servers.all_failed", name=r["name"], reason=r.get("detail") or "")
                for r in rows
                if r["result"] == "failed"
            ]
            words = (
                text(f"servers.all_done_{action}", n=len(done_))
                if done_
                else text(f"servers.all_none_{action}")
            )
            self.say(" ".join([words, *failed]))
            window.refresh()

        window.run(
            lambda: window.agent.call(
                "POST", f"/servers/{action}-all", {"ids": ids}, slow=action == "stop"
            ),
            done,
            self.failed,
        )


class ScreenStack(QStackedWidget):
    """Only the shown screen sets how small the window can get. A plain
    QStackedWidget takes the widest of all screens, so one wide hidden
    screen kept the window too wide for the narrow layout."""

    def __init__(self) -> None:
        super().__init__()
        self.currentChanged.connect(lambda _i: self.updateGeometry())

    def sizeHint(self) -> QSize:
        shown = self.currentWidget()
        return shown.sizeHint() if shown is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        shown = self.currentWidget()
        return shown.minimumSizeHint() if shown is not None else super().minimumSizeHint()


class MainWindow(FramelessWindow):
    STILL_DOWN_MS = 180_000  # then "may be off or asleep"
    TASK_AFTER_MS = 15_000  # after a Tailscale move, then run the agent's task
    WAKE_WAIT_MS = 120_000  # then "<PC> didn't wake"
    HELPER_SYNC_MS = 300_000  # the helping PC refreshes its key list this often
    OWN_UPDATE_FIRST_MS = 60_000  # a moderator's PC: the first update check
    OWN_UPDATE_POLL_MS = 3_600_000  # then is a day up? (self_update.due)

    def __init__(self, agent: AgentClient, pc: WindowsPC, dark: bool | None = None) -> None:
        super().__init__(pc)
        self.agent, self.pc = agent, pc
        self._tasks: set[Task] = set()
        # Agent calls wait on the network, not the CPU, and a backup can wait
        # for minutes, so a 2-core mini PC still needs room for the others.
        pool = QThreadPool.globalInstance()
        pool.setMaxThreadCount(max(8, pool.maxThreadCount()))
        self.dark = dark if dark is not None else self.dark_look(pc.setting("look"))
        self.scale = pc.text_scale()
        streamer.turn(str(pc.setting("streamer")).lower() == "true")
        self.servers: list[dict] = []
        self.all_servers: list[dict] = []  # with Test servers, which Simple mode hides
        # Other PCs' servers (Your PCs), by PC id: rows, or why there are none.
        self.elsewhere: dict[str, list[dict] | str] = {}
        self._elsewhere_at = 0.0
        self.types: list[dict] = []  # the server types, asked for once (/server-types)
        self.server_id: str | None = None
        self.rented: list[dict] = []  # servers on a host's panel (desktop/rented.py)
        self.alerts: list[dict] = []  # newest first, as the bell lists them
        self.last_alert = 0  # the newest alert id seen, to catch up after a reconnect
        self.caught_up = False  # the alerts from before signing in are loaded
        self.read_up_to = 0  # how far this account has read the alerts, on any device
        self.popped: dict[str, dict] = {}  # the alert behind each Windows alert, by id
        self.allowed: dict[str, list[str]] = {}  # server id -> what this account may do
        self.role: str | None = None  # owner or helper, once known
        self.account_permissions: list[str] = []  # PC-wide ones too ("agent.start")
        self.waking = False  # Wake was pressed; waiting for the PC to answer
        self.user: str | None = None  # the account's name, once known
        self.agent_version: str | None = None
        # The agent's PC was installed "Agent only": it has no desktop app,
        # so "do it on that PC" means running setup there (from /version).
        self.agent_only = False
        self.setup_unfinished = False  # the Tailscale step was skipped (yellow dot)
        self.quitting = False
        mode["technical"] = False
        self.mode_picked = False  # picked here since signing in
        self.welcome_open = False  # the first-run questions are showing
        self.tips_on = True  # the account's "tips" preference
        self.ask_answered = False  # the donation card was answered here
        self.setWindowTitle(text("app.name"))
        self.resize(1280, 800)
        theme.load_fonts()
        desk = Desk(self.see_through)
        column = QVBoxLayout(desk)
        column.setContentsMargins(ROOM)
        column.setSpacing(0)
        self.topbar = QWidget()
        bar = QHBoxLayout(self.topbar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.binder = BinderTabs()
        self.binder.setObjectName("binder")
        self.binder.setAccessibleName(text("page.servers"))
        self.binder.setDrawBase(False)
        self.binder.setExpanding(False)
        self.binder.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.binder.currentChanged.connect(self.binder_changed)
        self.binder.tabBarClicked.connect(self.binder_clicked)
        bar.addWidget(self.binder, 0, Qt.AlignmentFlag.AlignBottom)
        bar.addStretch()
        # The bell, gear and "?" share a strip with the window's own buttons.
        strip = QFrame()
        strip.setObjectName("strip")
        strip_row = QHBoxLayout(strip)
        strip_row.setContentsMargins(2, 2, 2, 2)
        strip_row.setSpacing(4)
        self.tools: dict[str, QToolButton] = {}
        for name, words, opens in (
            ("bell", "desktop.tool.alerts", "Notifications"),
            ("gear", "desktop.tool.pc_settings", "PC settings"),
            ("help", "desktop.tool.help", "Help"),
        ):
            tool = QToolButton()
            tool.setToolTip(text(words))
            tool.setAccessibleName(text(words))
            tool.setIconSize(QSize(20, 20))
            tool.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            tool.clicked.connect(lambda _=False, s=opens: self.show_screen(s))
            self.tools[name] = tool
            strip_row.addWidget(tool)
        self.tool_gap = QFrame()
        self.tool_gap.setObjectName("gap")
        self.tool_gap.setFixedHeight(16)
        strip_row.addWidget(self.tool_gap)
        for button in self.window_buttons.values():
            strip_row.addWidget(button)
        self.strip = strip
        bar.addWidget(strip, 0, Qt.AlignmentFlag.AlignVCenter)
        column.addWidget(self.topbar)
        # The band is the server's colored divider; the sheet lies on it.
        self.band = QFrame()
        self.band.setObjectName("band")
        on_band = QHBoxLayout(self.band)
        on_band.setContentsMargins(0, 12, 12, 12)
        self.sheet = Sheet()
        on_band.addWidget(self.sheet)
        desk.band = self.band
        column.addWidget(self.band, 1)
        self.stack = ScreenStack()
        # The four whole-window screens have no paper to turn.
        self.stack.currentChanged.connect(self.turn_page)
        self.sheet.inside.addWidget(self.stack)
        self.setCentralWidget(desk)
        self.place_frame()
        self.setAcceptDrops(True)
        self.screens: dict[str, QWidget] = {}
        self.server_screen = ServerScreen(self)
        self.sheet.set_list(self.server_screen.page_list)
        self.rented_screen = RentedScreen(self)
        self.notifications = NotificationsScreen(self)
        self.down_screen = NoticeScreen("Agent not running")
        self.update_screen = NoticeScreen("Update needed")
        self.tailscale_step = TailscaleStep(self)
        for screen in (
            SignInScreen(self),
            AllServersScreen(self),
            self.server_screen,
            self.rented_screen,
            self.notifications,
            self.down_screen,
            self.update_screen,
            self.tailscale_step,
        ):
            self.screens[screen.NAME] = screen
            self.stack.addWidget(screen)
        self.new_server = NewServerScreen(self)
        self.screens[self.new_server.NAME] = self.new_server
        self.stack.addWidget(self.new_server)
        self.gear = ListScreen(
            self,
            "PC settings",
            {
                "pc": PCSettings(self),
                "updates": Updates(self),
                "moderators": Moderators(self),
                "discord": Discord(self),
                "pcs": YourPCs(self),
                "devices": Devices(self),
                "account": Account(self),
            },
        )
        self.help = ListScreen(self, "Help", {"guide": Guide(self), "about": About(self)})
        for listed in (self.gear, self.help):
            self.screens[listed.NAME] = listed
            self.stack.addWidget(listed)
        self.feed = Feed(agent)
        self.feed.message.connect(self.feed_message)
        # Several events in a row (a server starting) bring one refresh.
        self.soon = QTimer(self)
        self.soon.setSingleShot(True)
        self.soon.setInterval(300)
        self.soon.timeout.connect(self.feed_changed)
        self.parts_due = False
        pc.when_alert_clicked(self.alert_clicked)
        self.feed.lost.connect(self.check_agent)
        self.back_to = "Sign-in"  # where to go when the agent is back
        self.still_down = QTimer(self)
        self.still_down.setSingleShot(True)
        self.still_down.timeout.connect(self.agent_still_down)
        self.retry = QTimer(self)
        self.retry.setInterval(5000)
        self.retry.timeout.connect(self.check_agent)
        self.checking = False
        self.wake_timer = QTimer(self)
        self.wake_timer.setSingleShot(True)
        self.wake_timer.timeout.connect(self.wake_failed)
        self.helper_timer = QTimer(self)
        self.helper_timer.setInterval(self.HELPER_SYNC_MS)
        self.helper_timer.timeout.connect(self.sync_helper)
        if self_update.wanted():
            # A moderator's PC updates its own copy, once a day.
            self.own_update_timer = QTimer(self)
            self.own_update_timer.setInterval(self.OWN_UPDATE_POLL_MS)
            self.own_update_timer.timeout.connect(self.check_own_update)
            self.own_update_timer.start()
            QTimer.singleShot(self.OWN_UPDATE_FIRST_MS, self.check_own_update)
        if pc.setting("help_wake"):
            # Help even before the agent answers: the PC may be asleep now.
            pc.helper.update(json.loads(pc.setting("help_wake_list") or "{}"))
            self.sync_helper()
        self.tray_menu = QMenu(self)
        pc.set_tray_menu(self.tray_menu)
        pc.when_tray_clicked(self.bring_back)
        self.update_tray()
        if agent.place == "this PC" and not pc.setting("start_with_windows_set"):
            # On by default on the agent's PC, so alerts work after a restart.
            pc.set_start_with_windows(True)
            pc.set_setting("start_with_windows_set", True)
        self.apply_look(None)
        self.tab_to_window_buttons_last()
        self.top_view = TopView(self)
        for keys, by in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
            QShortcut(QKeySequence(keys), self, lambda by=by: self.top_view.step(by))
        saved = pc.saved_sign_in(agent.place)
        once = pc.setting("one_time_sign_in") if agent.place == "this PC" else None
        if once:
            pc.set_setting("one_time_sign_in", "")  # used or not, it works once
            self.show_screen("Sign-in")
            self.run(
                lambda: agent.sign_in_once(once, pc.device_name()),
                lambda _r: self.signed_in_once(),
                lambda exc: self.screens["Sign-in"].say(str(exc)),  # type: ignore[attr-defined]
            )
        elif saved:
            # Checked by the first request; if it was signed out, that one
            # comes back to the sign-in screen.
            agent.token = saved
            self.signed_in(False)
        else:
            self.show_screen("Sign-in")
        self.check_agent()

    def closeEvent(self, event) -> None:
        """X hides the window in the notification area; Quit really closes."""
        if not self.quitting:
            event.ignore()
            self.hide()
            if not self.pc.setting("told_hidden"):
                self.pc.show_alert(
                    text("desktop.tray.hidden_title"), text("desktop.tray.hidden_body")
                )
                self.pc.set_setting("told_hidden", True)
            return
        self.feed.stop()
        self.pc.hide_tray()
        super().closeEvent(event)

    def quit_app(self) -> None:
        """Closes the app. The servers keep running: they belong to the agent."""
        self.quitting = True
        self.close()
        QApplication.quit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.parts_due:
            self.feed_changed()  # what changed while it was hidden

    # Dropping files on a server's tab: a world folder or zip is brought in
    # (Backups), .jar files or a modpack are added (Mods), and a Bedrock
    # server's packs too, after the same questions as those pages' buttons.
    def dropped(self, event) -> tuple[str, list[str]] | None:
        urls = event.mimeData().urls()
        if not urls or not all(u.isLocalFile() for u in urls):
            return None
        paths, sid = [u.toLocalFile() for u in urls], self.server_id or ""
        if self.screen_name == "New server":
            pack = len(paths) == 1 and paths[0].lower().endswith(".mrpack")
            return ("New server", paths) if pack else None
        if self.screen_name != "Server":
            return None
        if len(paths) == 1 and is_world(Path(paths[0])) and self.may(sid, "world.replace"):
            return "Backups", paths
        pack = paths[0].lower().endswith(".mrpack")  # importing one changes server settings too
        if (
            takes(self.server_screen.row, paths)
            and self.may(sid, "mods.manage")
            and (not pack or self.may(sid, "settings.server"))
        ):
            return "Mods", paths
        return None

    def dragEnterEvent(self, event) -> None:
        if self.dropped(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        found = self.dropped(event)
        if found:
            event.acceptProposedAction()
            if found[0] == "New server":
                self.new_server.take(found[1])
                return
            self.open_page(found[0])
            self.server_screen.page.take(found[1])  # type: ignore[attr-defined]

    def turn_page(self) -> None:
        if not self.sheet.bare:
            motion.turn(self.stack)

    def can_drag(self, pos: QPoint) -> bool:
        """By the top row and the band. A press on the paper is for what is
        on the page."""
        paper = self.sheet.paper().toRect().translated(self.sheet.mapTo(self, QPoint(0, 0)))
        return self.sheet.bare or not paper.contains(pos)

    def bring_back(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ the look
    @staticmethod
    def dark_look(look: str | None) -> bool:
        """Light, dark, or (anything else) the way Windows is set."""
        if look in ("light", "dark"):
            return look == "dark"
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def set_streamer(self, on: bool) -> None:
        """Streamer mode, kept on this PC like the look."""
        self.pc.set_setting("streamer", "true" if on else "false")
        streamer.turn(on)
        if not on and self.server_screen.row:
            self.load_server_parts()  # labels show their own words again

    def set_look(self, look: str) -> None:
        """Kept on this PC: each PC (and person) picks their own."""
        self.pc.set_setting("look", look)
        self.dark = self.dark_look(look)
        row = self.server_screen.row if self.screen_name == "Server" else {}
        self.apply_look(row.get("color"))
        self.fill_binder()

    def apply_look(self, color: str | None) -> None:
        """The theme, with the open server's color on it (None: the copper)."""
        self.look = theme.tokens(self.dark, color)
        self.setStyleSheet(theme.style_sheet(self.look, self.scale))
        for name, tool in self.tools.items():
            tool.setIcon(icons.icon(name, self.look["text-primary"], 20))
        self.show_frame_look(self.look)
        self.binder.look = self.sheet.look = self.look
        self.binder.update()
        self.sheet.update()
        self.server_screen.page_list.set_look(self.look)
        self.server_screen.technical_overview.style_graphs(self.look)

    @property
    def screen_name(self) -> str:
        return self.stack.currentWidget().NAME  # type: ignore[attr-defined]

    def show_screen(self, name: str) -> None:
        if name == "Notifications":
            self.mark_alerts_read()
            self.show_unread()
        # These four fill the window: no tabs, no tools, and their own small
        # sheet. The window's own buttons stay when it has no Windows frame.
        whole = name in ("Sign-in", "Agent not running", "Update needed", "Tailscale")
        for widget in (self.binder, self.tool_gap, *self.tools.values()):
            widget.setVisible(not whole)
        self.strip.setVisible(self.frameless or not whole)
        self.sheet.bare = whole
        self.binder.none_open = name in ("Notifications", "PC settings", "Help")
        self.binder.update()
        self.server_screen.page_list.setVisible(name == "Server")
        self.sheet.arrange()
        if name != "Server":
            self.apply_look(None)
        if name in ("PC settings", "Help"):
            # Before it shows: showing the list could otherwise pick its first
            # section by itself and forget the one asked for.
            self.screens[name].load()  # type: ignore[attr-defined]
        self.stack.setCurrentWidget(self.screens[name])
        self.binder.blockSignals(True)  # the tab that goes with the screen, however it was opened
        self.select_binder_tab()
        self.binder.blockSignals(False)
        if name == "New server":
            self.new_server.load()

    def open_list(self, name: str, section: str) -> None:
        """The gear's or "?"'s screen, on one section."""
        self.pc.set_setting(f"section:{name}", section)
        self.show_screen(name)

    # ------------------------------------------------------------ binder tabs
    def fill_binder(self) -> None:
        tabs = [("all", text("page.servers"))] + [(r["id"], r["name"]) for r in self.servers]
        current = [
            (self.binder.tabData(i), self.binder.tabText(i)) for i in range(self.binder.count())
        ]
        running = {r["id"] for r in self.servers if r.get("state") == "ONLINE"}
        shape = ([r.get("color") for r in self.servers], running, self.dark)
        if current == tabs + [("new", "")] and shape == getattr(self, "_binder_shape", None):
            return
        self._binder_shape = shape
        # Each server's tab is in that server's color, with a dot while it runs.
        self.binder.looks = {r["id"]: theme.tokens(self.dark, r.get("color")) for r in self.servers}
        self.binder.running = running
        self.binder.blockSignals(True)
        while self.binder.count():
            self.binder.removeTab(0)
        for which, words in tabs + [("new", "")]:
            i = self.binder.addTab(words)
            self.binder.setTabData(i, which)
        self.binder.setTabToolTip(i, text("page.add_server"))
        self.binder.setAccessibleTabName(i, text("page.add_server"))
        self.binder.refit()
        self.select_binder_tab()
        self.binder.blockSignals(False)

    def select_binder_tab(self) -> None:
        wanted = {
            "All servers": "all",
            "Rented server": "all",
            "New server": "new",
            "Server": self.server_id,
        }.get(self.screen_name)
        for i in range(self.binder.count()):
            if self.binder.tabData(i) == wanted:
                self.binder.setCurrentIndex(i)

    def binder_clicked(self, index: int) -> None:
        """A click on the tab that is already the current one: go back to
        its screen from the bell's, the gear's or "?"'s."""
        if index == self.binder.currentIndex() and self.binder.none_open:
            self.binder_changed(index)

    def binder_changed(self, index: int) -> None:
        which = self.binder.tabData(index)
        if which == "all":
            self.show_screen("All servers")
        elif which == "new":
            self.show_screen("New server")
        elif which:
            self.open_server(which)

    def open_server(self, server_id: str) -> None:
        row = next((r for r in self.servers if r["id"] == server_id), None)
        if row is None:
            return
        self.server_id = server_id
        self.apply_look(row.get("color"))
        self.server_screen.show_row(row)
        self.show_screen("Server")
        self.load_server_parts()

    def open_rented(self, server_id: str) -> None:
        row = next((r for r in self.rented if r["id"] == server_id), None)
        if row is None:
            return
        self.apply_look(None)
        self.rented_screen.open(row)
        self.show_screen("Rented server")
        self.select_binder_tab()

    def rented_loaded(self, found: dict) -> None:
        self.rented = found.get("servers") or []
        self.screens["All servers"].show_all()  # type: ignore[attr-defined]
        if self.screen_name == "Rented server":
            row = next(
                (r for r in self.rented if r["id"] == self.rented_screen.row.get("id")), None
            )
            if row is None:
                self.show_screen("All servers")
            else:
                self.rented_screen.show_row(row)

    # ------------------------------------------------------------ data
    def refresh(self) -> None:
        self.run(self.agent.servers, self.servers_loaded, self.screens["All servers"].failed)  # type: ignore[attr-defined]
        # A moderator with no rented server may be refused: then there are none.
        self.run(lambda: self.agent.call("GET", "/rented"), self.rented_loaded, lambda _e: None)
        if time.time() - self._elsewhere_at >= self.ELSEWHERE_EVERY:
            self._elsewhere_at = time.time()
            self.run(lambda: self.agent.call("GET", "/pcs"), self.pcs_loaded, lambda _e: None)

    # -- the owner's other PCs (Your PCs) ---------------------------------
    ELSEWHERE_EVERY = 30  # seconds: other PCs' servers are asked for this often at most

    def pcs_loaded(self, found: dict) -> None:
        """Keep the PC list on this device (it is learned from the agent's
        synced list), then ask each other PC for its servers, with this same
        sign-in: it works on every PC of the account."""
        pcs = [p for p in found.get("pcs") or [] if p.get("address") and p.get("fingerprint")]
        self.pc.set_setting("pcs", pcs)
        for pc in self.other_pcs():
            self.run(
                partial(self._servers_of, pc),
                partial(self._elsewhere_found, pc),
                partial(self._elsewhere_failed, pc),
                this_pc=False,
            )
        self.screens["All servers"].show_all()  # type: ignore[attr-defined]

    def other_pcs(self) -> list[dict]:
        return [p for p in self.pc.setting("pcs") or [] if not p.get("this")]

    def this_pc_name(self) -> str:
        mine = next((p for p in self.pc.setting("pcs") or [] if p.get("this")), None)
        return (mine or {}).get("name") or text("desktop.pcs.this_pc")

    def _client_for(self, pc: dict) -> AgentClient:
        client = AgentClient.connect(pc["address"], int(pc["port"]), pc["fingerprint"])
        client.token = self.agent.token
        return client

    def _servers_of(self, pc: dict) -> list[dict]:
        return self._client_for(pc).servers()

    def _elsewhere_failed(self, pc: dict, exc: AgentError) -> None:
        self._elsewhere_found(pc, str(exc))

    def _elsewhere_found(self, pc: dict, rows: list[dict] | str) -> None:
        self.elsewhere[pc["id"]] = rows
        self.screens["All servers"].show_all()  # type: ignore[attr-defined]

    def act_elsewhere(self, pc: dict, row: dict, action: str) -> None:
        def done(_result: Any) -> None:
            self._elsewhere_at = 0.0
            self.refresh()

        self.run(
            lambda: self._client_for(pc).server_action(row["id"], action),
            done,
            lambda exc: self.screens["All servers"].failed(exc),  # type: ignore[attr-defined]
            this_pc=False,
        )

    def servers_loaded(self, servers: list[dict]) -> None:
        self.all_servers = servers
        servers = [r for r in servers if mode["technical"] or not r.get("test")]
        self.servers = servers
        self.update_tray()
        self.screens["All servers"].fill(servers)  # type: ignore[attr-defined]
        self.fill_binder()
        if self.screen_name == "Server":
            row = next((r for r in servers if r["id"] == self.server_id), None)
            if row is None:
                self.show_screen("All servers")
                self.select_binder_tab()
                return
            self.server_screen.show_row(row)

    def with_types(self, then: Callable[[], None]) -> None:
        """``then`` once the server types are known; they're asked for once."""
        if self.types:
            then()
            return

        def got(found: dict) -> None:
            self.types = found["types"]
            self.recommended_type = found.get("recommended")
            then()

        self.run(lambda: self.agent.call("GET", "/server-types"), got, self.page_problem)

    def open_new(self, server_id: str, words: str) -> None:
        """A server was just made or added: its tab, once the list has it."""

        def loaded(servers: list[dict]) -> None:
            self.servers_loaded(servers)
            self.go_to_server(server_id)
            self.server_screen.header.say(words, "ok")

        self.run(self.agent.servers, loaded, self.screens["All servers"].failed)  # type: ignore[attr-defined]

    def server_removed(self, words: str) -> None:
        """A server left the list: back to All servers, saying so."""
        self.server_id = None
        self.show_screen("All servers")
        self.screens["All servers"].say(words)  # type: ignore[attr-defined]
        self.refresh()

    def open_page(self, page: str) -> None:
        """A page of the open server, or a screen of the app (PC settings)."""
        if page in self.screens:
            self.show_screen(page)
            return
        if page in ("Console", "Performance") and not mode["technical"]:
            self.set_mode(True)  # Simple hides these; Technical has every page
        self.server_screen.show_page(page)

    def install_addon(self, project: str) -> None:
        """Open the Mods page and run its normal install for one add-on (a
        Protection card or game settings offer), with its version check."""
        from .mods import ModsPage

        self.open_page("Mods")
        page = self.server_screen.page_widgets["Mods"]
        if isinstance(page, ModsPage):
            page.install_hit({"slug": project})

    def open_activity(self, crashes: bool = False) -> None:
        """Activity, with everything or only the crashes."""
        self.server_screen.activity.crashes_only = crashes
        self.open_page("Activity")

    def load_server_parts(self) -> None:
        """The header's Undo and Redo and the right panel, each on its own so
        one the account can't see doesn't hide the others."""
        sid, screen = self.server_id, self.server_screen
        if sid is None:
            return
        row = screen.row
        here = self.still_on(sid)
        self.run(lambda: self.agent.changes(sid), here(screen.header.show_changes), lambda _e: None)
        self.run(
            lambda: self.agent.players(sid),
            here(lambda players: screen.panel.show_players(row, players)),
            lambda _e: None,
        )
        self.run(
            lambda: self.agent.schedules(sid), here(screen.panel.show_schedules), lambda _e: None
        )
        self.run(lambda: self.agent.events(sid), here(screen.panel.show_events), lambda _e: None)
        self.run(lambda: self.agent.status(sid), here(screen.header.show_status), lambda _e: None)
        if self.role in (None, "owner"):  # the agent refuses moderators too
            self.run(self.agent.support_ask, here(self.show_ask), lambda _e: None)
        # Read once when the Overview opens, not on the graphs' timer.
        overview = screen.technical_overview if mode["technical"] else screen.simple_overview
        overview.protection.load(sid)
        if mode["technical"]:
            self.load_technical()
            return
        simple = screen.simple_overview
        parts: list[tuple[Callable[[str], Any], Callable[[Any], None]]] = [
            (self.agent.suggestions, simple.show_suggestions),
            (self.agent.checklist, simple.show_checklist),
            (self.agent.join, simple.show_address),
            (self.agent.backups, simple.show_backups),
        ]
        if self.show_mods_card(simple):
            parts.append((self.content_of(row), simple.show_mods))
        for work, done in parts:
            self.run(partial(work, sid), here(done), lambda _e: None)

    def show_ask(self, data: dict) -> None:
        """The donation card, the same in Simple and Technical."""
        screen = self.server_screen
        # an answer to a check sent before the click mustn't bring it back
        shown = bool(data.get("show")) and not self.ask_answered
        for overview in (screen.simple_overview, screen.technical_overview):
            overview.ask.setVisible(shown)

    def still_on(self, sid: str) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
        """Wraps a callback so an answer about a server that is no longer
        open is dropped: Undo must never show one server's change on another."""

        def wrap(done: Callable[[Any], None]) -> Callable[[Any], None]:
            return lambda result: done(result) if self.server_id == sid else None

        return wrap

    def load_technical(self) -> None:
        """The Technical Overview: graphs, details, console lines and mods."""
        sid, technical = self.server_id, self.server_screen.technical_overview
        if sid is None:
            return
        here = self.still_on(sid)
        self.run(
            partial(self.agent.performance, sid),
            here(technical.show_performance),
            here(technical.show_unknown),
        )
        self.run(partial(self.agent.status, sid), here(technical.show_status), lambda _e: None)
        self.run(partial(self.agent.logs, sid), here(technical.show_console), lambda _e: None)
        if self.show_mods_card(technical):
            content = self.content_of(self.server_screen.row)
            self.run(partial(content, sid), here(technical.show_mods), lambda _e: None)

    def show_mods_card(self, overview: SimpleOverview | TechnicalOverview) -> bool:
        """The Overview's Mods card, hidden for a type that takes none."""
        shown = content_page(self.server_screen.row) is not None
        if overview.mods_card.isHidden() == shown:
            overview.mods_card.setVisible(shown)
            if isinstance(overview, SimpleOverview):
                overview.place_cards()
        return shown

    def content_of(self, row: dict) -> Callable[[str], dict]:
        """What the Mods card lists: a Bedrock server's add-ons, else its mods."""
        return self.agent.addons if has_packs(row) else self.agent.mods

    # ------------------------------------------------------------ the live feed and alerts
    def feed_message(self, message: dict) -> None:
        if self.agent.token is None:
            return  # sent just before signing out
        kind = message.get("type")
        if kind == "ready":
            # Whatever happened while the feed was away (none of it pops up
            # the first time: those alerts are from before signing in).
            self.run(
                partial(self.agent.alerts, self.last_alert),
                self.caught_up_with,
                lambda _e: None,
            )
            self.soon.start()
        elif kind == "event":
            event = message.get("event") or {}
            if event.get("type") == "update_closing_app":
                self.close_for_update()
            elif event.get("type") == "alert":
                self.alerts_loaded([event.get("data") or {}], pop=True)
            elif event.get("type") in SOUNDS and self.pc.setting(SOUNDS[event["type"]]) in (
                True,
                "true",
            ):
                self.pc.play_sound(crash=event["type"] == "server_crashed")
                self.parts_due = self.parts_due or event.get("server_id") == self.server_id
                self.soon.start()
            elif event.get("type") in ("console", "metrics", "chat"):
                # Only the open page of the open server uses these, and only
                # while the window shows.
                if event.get("server_id") == self.server_id and self.isVisible():
                    self.server_screen.live(event)
            else:
                self.parts_due = self.parts_due or event.get("server_id") == self.server_id
                self.soon.start()
        elif kind == "error":
            self.signed_out(str(message.get("message") or ""))

    def close_for_update(self) -> None:
        """An update is about to replace this program's files. Only the app
        on the agent's PC runs from them; the update opens it again as it
        was (app.py, --after-update)."""
        if self.agent.place != "this PC":
            return
        self.pc.set_setting("reopen_after_update", "shown" if self.isVisible() else "hidden")
        self.quit_app()

    def caught_up_with(self, found: dict) -> None:
        # Unread is what this account hasn't read on any device: read on a
        # phone lowers the count here at the next catch up.
        self.read_up_to = max(self.read_up_to, int(found.get("read_up_to") or 0))
        self.alerts_loaded(found["alerts"], pop=self.caught_up)
        self.show_unread()
        self.caught_up = True

    def seeing_notifications(self) -> bool:
        """The Notifications screen is in front of someone, not hidden away."""
        return self.screen_name == "Notifications" and self.isVisible() and not self.isMinimized()

    def mark_alerts_read(self) -> None:
        if self.last_alert:
            up_to = self.last_alert
            self.read_up_to = max(self.read_up_to, up_to)
            self.run(lambda: self.agent.mark_alerts_read(up_to), lambda _r: None, lambda _e: None)

    def feed_changed(self) -> None:
        self.refresh()
        if not self.isVisible():
            return  # a hidden window loads nothing more; showing it catches up
        if self.parts_due and self.screen_name == "Server":
            self.load_server_parts()
            self.server_screen.changed()
        self.parts_due = False

    def alerts_loaded(self, found: list[dict], pop: bool) -> None:
        known = {a["id"] for a in self.alerts}
        new = [a for a in found if a.get("id") and a["id"] not in known]
        if not new:
            return
        self.alerts = sorted(self.alerts + new, key=lambda a: a["id"], reverse=True)[:KEPT]
        self.last_alert = max(self.last_alert, *(a["id"] for a in new))
        names = {r["id"]: r["name"] for r in self.servers}
        self.notifications.fill(self.alerts, names)
        if not pop:
            return
        if self.seeing_notifications():
            self.mark_alerts_read()  # seen as they arrive
        self.show_unread()
        shown = self.pc.setting("show_alerts") not in (False, "false")
        showing = [
            alert
            for alert in new
            if shown  # "Show alerts on this PC" off: the bell still counts them
            and not (
                self.isVisible()
                and not self.isMinimized()
                and self.screen_name == "Server"
                and alert.get("server_id") == self.server_id
            )
        ]
        if len(showing) > 1:
            # Several at once (catching up): one Windows alert, for the bell.
            self.pc.show_alert(
                text("desktop.alerts.several", count=len(showing)),
                ", ".join(a["title"] for a in showing[:3]),
                "bell",
            )
        elif showing:
            alert = showing[0]
            buttons = [(text("desktop.alerts.open"), "open")]
            server = alert.get("server_id")
            if alert.get("event") in RESTART_FOR and server and self.may(server, "server.control"):
                buttons.append((text("action.restart"), "restart"))
            if self.answers_start_requests(alert):
                buttons += [
                    (text("desktop.start_request.start"), "start"),
                    (text("desktop.start_request.no"), "no"),
                ]
            self.popped = {str(alert["id"]): alert, **dict(list(self.popped.items())[:49])}
            self.pc.show_alert(alert["title"], alert["body"], str(alert["id"]), tuple(buttons))

    @property
    def unread(self) -> int:
        return sum(1 for alert in self.alerts if alert["id"] > self.read_up_to)

    def show_unread(self) -> None:
        bell = self.tools["bell"]
        bell.setText(str(self.unread) if self.unread else "")
        words = text("desktop.tool.alerts")
        if self.unread:
            words += ", " + text("desktop.alerts.new", count=self.unread)
        bell.setAccessibleName(words)

    def alert_clicked(self, key: str, action: str) -> None:
        """A Windows alert, or one of its buttons, was clicked. Restart
        restarts the server without opening the window; anything else
        opens the alert's page (several at once: the bell)."""
        alert = self.popped.get(key)
        if action in ("start", "no") and alert:
            self.answer_start_request(alert, action == "start")
            return
        if action == "restart" and alert:
            server = str(alert["server_id"])
            self.run(
                lambda: self.agent.server_action(server, "restart"),
                lambda _r: self.refresh(),
                lambda exc: self.pc.show_alert(alert["title"], str(exc)),
            )
            return
        self.bring_back()
        if key == "bell":
            self.show_screen("Notifications")
        elif alert:
            self.open_alert(alert)

    def answers_start_requests(self, alert: dict) -> bool:
        server = alert.get("server_id")
        return bool(
            alert.get("event") == "start_request" and server and self.may(server, "server.control")
        )

    def answer_start_request(self, alert: dict, start: bool) -> None:
        """Start or Say no to a start request sent from Discord."""
        server = str(alert["server_id"])
        self.run(
            lambda: self.agent.call(
                "POST", f"/servers/{server}/start-request", {"answer": "start" if start else "no"}
            ),
            lambda _r: self.refresh(),
            lambda exc: self.pc.show_alert(alert["title"], str(exc)),
        )

    def open_alert(self, alert: dict) -> None:
        if self.answers_start_requests(alert):
            box = QMessageBox(QMessageBox.Icon.Question, alert["title"], alert["body"], parent=self)
            start = box.addButton(
                text("desktop.start_request.start"), QMessageBox.ButtonRole.AcceptRole
            )
            no = box.addButton(text("desktop.start_request.no"), QMessageBox.ButtonRole.RejectRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() in (start, no):
                self.answer_start_request(alert, box.clickedButton() is start)
                return
        page, server = str(alert.get("page")), str(alert.get("server_id"))
        if page in PC_PAGES:
            self.open_list("PC settings", PC_PAGES[page])
        elif any(r["id"] == server for r in self.servers):
            self.go_to_server(server)
            if page == "crashes":
                self.open_activity(crashes=True)
            else:
                self.open_page(PAGES.get(page, "Overview"))
        else:
            self.show_screen("All servers")
            self.select_binder_tab()

    # ------------------------------------------------------------ agent not running, versions
    def check_agent(self) -> None:
        # One at a time: a check can wait up to 15 s on a PC that's gone.
        if self.checking:
            return
        self.checking = True
        self.run(
            self.agent.health,
            self.agent_up,
            lambda exc: self.agent_down() if isinstance(exc, Unreachable) else None,
            lambda: setattr(self, "checking", False),
        )

    def agent_up(self, health: dict) -> None:
        if health.get("api_version") != API_VERSION:
            self.stop_for_update(health.get("api_version"))
            return
        if self.waking:
            self.waking = False
            self.wake_timer.stop()
            self.down_screen.say(text("desktop.wake.awake", pc=self.wake_name()), "")
            QTimer.singleShot(1500, lambda: self.agent_up(health))
            return
        if self.screen_name in ("Agent not running", "Update needed") or (
            self.tailscale_step.restarting
        ):
            self.still_down.stop()
            self.retry.stop()
            if self.tailscale_step.restarting:
                self.tailscale_step.load()  # back on its Tailscale address
            else:
                self.show_screen(self.back_to)
            if self.agent.token:
                self.feed.start()  # stopped while an update was needed
                self.refresh()

    def agent_down(self) -> None:
        if self.screen_name in ("Agent not running", "Update needed"):
            return
        if self.tailscale_step.restarting:
            # Restarting on purpose after moving onto Tailscale: wait for it here.
            if not self.retry.isActive():
                self.still_down.start(self.STILL_DOWN_MS)
                self.retry.start()
                # The keep-alive brings it back within a minute, but a PC set
                # to start it by hand has none: run its task once it's down.
                QTimer.singleShot(self.TASK_AFTER_MS, self.start_task_if_restarting)
            return
        self.back_to = self.screen_name
        # Starting it needs this PC, and an owner; anywhere else, a device
        # that may wake the PC kept what it needs while the PC was awake.
        here = self.agent.place == "this PC" and self.role != "helper"
        if here:
            self.down_screen.say(
                text("desktop.down.title"),
                text("desktop.down.first", pc=self.agent.place),
                text("desktop.down.start"),
                self.start_agent,
            )
        else:
            self.down_screen.say(
                text("desktop.down.title"),
                text("desktop.down.first", pc=self.agent.place),
                text("desktop.wake.button", pc=self.wake_name()) if self.can_wake() else "",
                self.wake_pc,
            )
        self.show_screen("Agent not running")
        self.still_down.start(self.STILL_DOWN_MS)
        self.retry.start()

    def start_task_if_restarting(self) -> None:
        if self.tailscale_step.restarting:
            self.run(self.pc.start_agent, lambda _problem: None)

    def agent_still_down(self) -> None:
        if self.tailscale_step.restarting:
            self.tailscale_step.restarting = False
            self.agent_down()  # it never came back: say so as usual
        place = self.agent.place
        self.down_screen.words.setText(text("desktop.down.later", pc=place[:1].upper() + place[1:]))

    def start_agent(self) -> None:
        problem = self.pc.start_agent()
        self.down_screen.note.setText(problem or "")
        self.down_screen.note.setVisible(bool(problem))

    # ------------------------------------------------------------ Wake-on-LAN
    def wake_kept(self) -> dict:
        """What this device kept to wake the PC (load_wake), or nothing."""
        return json.loads(self.pc.setting(f"wake:{self.agent.place}") or "{}")

    def wake_name(self) -> str:
        return self.wake_kept().get("pc") or self.agent.place

    def can_wake(self) -> bool:
        kept = self.wake_kept()
        return bool(kept.get("on") and (kept.get("helper") or kept.get("router_url")))

    def load_wake(self) -> None:
        """While the PC is awake, keep what waking it needs, with this
        device's own wake key when there's a helping PC."""
        if self.agent.place == "this PC" or not (
            self.role == "owner" or "agent.start" in self.account_permissions
        ):
            self.pc.set_setting(f"wake:{self.agent.place}", "")
            return
        agent, kept = self.agent, self.wake_kept()

        def work() -> dict:
            info = agent.call("GET", "/wake")
            key = kept.get("key")
            if info.get("helper") and not (info.get("has_key") and key):
                key = agent.call("POST", "/wake/key")["key"]
            return {**info, "key": key if info.get("helper") else None}

        self.run(
            work,
            lambda found: self.pc.set_setting(f"wake:{agent.place}", json.dumps(found)),
            lambda _e: None,
        )

    def wake_pc(self) -> None:
        """Wake: the signal straight from here (works at home) and the
        request to the helping PC; with only a router, its wake page."""
        kept, pc = self.wake_kept(), self.pc
        if not kept.get("helper") and kept.get("router_url"):
            pc.open_link(kept["router_url"])

        def work() -> None:
            if kept.get("mac"):
                pc.send_wake(kept["mac"], kept.get("broadcast"))
            helper = kept.get("helper")
            if helper and kept.get("key"):
                pc.ask_helper(helper["address"], int(helper["port"]), kept["key"])

        self.run(work, lambda _r: None, lambda exc: self.down_screen.note.setText(str(exc)))
        self.waking = True
        self.down_screen.say(text("desktop.wake.waking", pc=self.wake_name()), "")
        self.wake_timer.start(self.WAKE_WAIT_MS)
        self.retry.start()

    def wake_failed(self) -> None:
        if not self.waking:
            return
        self.waking = False
        self.down_screen.say(
            text("desktop.down.title"),
            text("desktop.wake.failed", pc=self.wake_name()),
            text("desktop.wake.button", pc=self.wake_name()),
            self.wake_pc,
        )

    def sync_helper(self) -> None:
        """The helping PC: listen on this PC's Tailscale address, and refresh
        the valid keys while the PC it helps is awake."""
        address = self.pc.setting("help_wake")
        if not address:
            self.helper_timer.stop()
            self.pc.help_wake(None)
            return
        if not self.pc.helper.listening:
            try:
                self.pc.help_wake(address)
            except OSError:
                pass  # Tailscale isn't up yet: the next sync tries again
        self.helper_timer.start()
        if self.agent.token:
            self.run(
                lambda: self.agent.call("GET", "/wake/keys"), self.helper_synced, lambda _e: None
            )

    def helper_synced(self, found: dict) -> None:
        self.pc.set_setting("help_wake_list", json.dumps(found))
        self.pc.helper.update(found)

    def check_own_update(self) -> None:
        if self_update.due(self.pc.setting("update_checked_at")):
            self.run(self_update.newer_release, self.own_update_found, lambda _e: None)

    def own_update_found(self, release) -> None:
        self.pc.set_setting("update_checked_at", time.time())
        if release is None or not self.confirm(
            text("desktop.self_update.title"),
            text("desktop.self_update.question", version=release.version),
            text("desktop.self_update.now"),
            no=text("desktop.self_update.later"),
        ):
            return  # Later: asked again tomorrow
        self.run(
            lambda: self_update.download(release),
            self.run_own_update,
            lambda exc: self.pc.show_alert(text("desktop.self_update.title"), str(exc)),
        )

    def run_own_update(self, setup: Path) -> None:
        """Close, and let the checked setup replace this copy; it opens the
        app again as it was (app.py, --after-update)."""
        self.pc.set_setting("reopen_after_update", "shown" if self.isVisible() else "hidden")
        self.pc.run_update(setup, appinfo.program_dir())
        self.quit_app()

    def stop_for_update(self, agent_api: int | None) -> None:
        """A different API version: stop before sending anything it can't
        handle, and keep asking, so the app carries on once the update is in."""
        if self.screen_name not in ("Agent not running", "Update needed"):
            self.back_to = self.screen_name
        self.feed.stop()
        self.still_down.stop()
        self.retry.start()
        newer = isinstance(agent_api, int) and agent_api > API_VERSION
        self.update_screen.say(
            text("desktop.update.title"),
            text("desktop.update.app_older")
            if newer
            else text("desktop.update.agent_older", pc=self.agent.place),
        )
        self.show_screen("Update needed")

    def version_loaded(self, found: dict) -> None:
        """Same API version: everything works. Say which side is older."""

        def parts(version: str) -> tuple[int, ...] | None:
            try:
                return tuple(int(x) for x in version.split("."))
            except ValueError:
                return None  # not a plain version: say nothing rather than guess

        self.agent_version = found.get("version")
        self.agent_only = bool(found.get("agent_only"))
        mine, theirs = parts(APP_VERSION), parts(str(found.get("version") or ""))
        note = self.screens["All servers"].note  # type: ignore[attr-defined]
        values = {"pc": self.agent.place, "agent": found.get("version"), "app": APP_VERSION}
        if mine is None or theirs is None or mine == theirs:
            note.hide()
            return
        # shortcut: nothing to grey out; the agent has no features list.
        note.setText(
            text(
                "desktop.version.agent_older" if theirs < mine else "desktop.version.app_older"
            ).format(**values)
        )
        note.show()

    # ------------------------------------------------------------ the notification area
    def update_tray(self) -> None:
        """The icon (red dot while a server is down after a crash) and its
        menu: each server with Start or Stop and Open, as this account may."""
        down = [r["name"] for r in self.servers if r.get("label") == "crashed"]
        words = "\n".join([text("app.name")] + [text("desktop.tray.crashed", name=n) for n in down])
        if self.setup_unfinished:
            words += "\n" + text("desktop.tray.setup_unfinished")
        dot = "red" if down else "yellow" if self.setup_unfinished else None
        self.pc.set_tray_look(icons.tray_icon(dot), words)
        menu = self.tray_menu
        if menu.isVisible():
            return  # not under the mouse; the next change rebuilds it
        menu.clear()
        for row in self.servers if self.agent.token else []:
            server = menu.addMenu(f"{row['name']}: {state_words(row)}")
            if "server.control" in self.allowed.get(row["id"], []):
                action, allowed = main_action(row)
                act = server.addAction(text(f"action.{action}"))
                act.setEnabled(allowed)
                act.triggered.connect(partial(self.server_action, row["id"], action))
            server.addAction(text("desktop.tray.open_server")).triggered.connect(
                partial(self.go_to_server, row["id"])
            )
        if self.servers and self.agent.token:
            menu.addSeparator()
        if self.setup_unfinished:
            menu.addAction(text("desktop.tray.finish_setup")).triggered.connect(self.finish_setup)
        menu.addAction(text("desktop.tray.open")).triggered.connect(self.bring_back)
        menu.addAction(text("desktop.tray.quit")).triggered.connect(self.quit_app)

    def go_to_server(self, server_id: str) -> None:
        self.bring_back()
        self.open_server(server_id)
        self.binder.blockSignals(True)
        self.select_binder_tab()
        self.binder.blockSignals(False)

    def may(self, server_id: str, permission: str) -> bool:
        """Whether this account may do it. Until /auth/me answers, yes: the
        agent refuses anything not allowed anyway."""
        return self.role in (None, "owner") or permission in self.allowed.get(server_id, [])

    def me_loaded(self, me: dict) -> None:
        self.hidden_loaded(me)
        self.allowed = me.get("server_permissions") or {}
        self.role = me.get("role")
        self.user = me.get("user")
        self.account_permissions = me.get("account_permissions") or []
        self.load_wake()
        if self.pc.setting("help_wake"):
            self.sync_helper()
        self.update_tray()
        self.servers_loaded(self.all_servers)  # hides what this account can't do
        if self.screen_name == "Server" and self.server_screen.row:
            # a page opened before this answered showed the owner's buttons
            self.server_screen.show_page(self.server_screen.current_page)
        if self.role == "owner" and self.agent.place == "this PC":
            self.run(self.agent.tailscale, self.tailscale_loaded, lambda _e: None)
        if self.role == "owner":
            self.run(
                lambda: self.agent.call("GET", "/account/welcome"),
                self.welcome_loaded,
                lambda _e: None,
            )

    def welcome_loaded(self, answers: dict) -> None:
        """The first-run questions, once, until answered or skipped."""
        if answers.get("answered") or self.welcome_open:
            return
        from .welcome import WelcomeDialog

        self.welcome_open = True
        dialog = WelcomeDialog(self)
        dialog.finished.connect(lambda _r: setattr(self, "welcome_open", False))
        dialog.open()

    def tailscale_loaded(self, state: dict) -> None:
        self.setup_unfinished = bool(state.get("skipped")) and not state.get("moved")
        self.update_tray()

    def tailscale_skipped(self, state: dict) -> None:
        self.tailscale_loaded(state)
        self.show_screen("All servers")

    def finish_setup(self) -> None:
        self.bring_back()
        self.show_screen("Tailscale")

    # ------------------------------------------------------------ signing in and out
    def signed_in(self, remember: bool) -> None:
        if remember and self.agent.token:
            self.pc.save_sign_in(self.agent.place, self.agent.token)
        self.show_screen("All servers")
        self.feed.start()
        self.refresh()
        self.mode_picked = False
        self.run(self.agent.preferences, self.mode_loaded, lambda _e: None)
        self.run(self.agent.me, self.me_loaded, lambda _e: None)
        self.run(self.agent.version, self.version_loaded, lambda _e: None)

    def signed_in_once(self) -> None:
        """From the installer: kept signed in, then the Tailscale step."""
        self.signed_in(True)
        self.show_screen("Tailscale")

    def signed_out(self, problem: str = "") -> None:
        """Back to the sign-in screen, forgetting any saved sign-in."""
        self.feed.stop()
        self.alerts, self.last_alert, self.read_up_to, self.popped = [], 0, 0, {}
        self.caught_up = False
        self.allowed, self.role, self.user = {}, None, None
        self.account_permissions = []
        self.pc.set_setting("help_wake", "")  # a signed-out PC helps no more
        self.sync_helper()
        self.show_unread()
        self.agent.token = None
        ui.hidden.clear()  # the next account has its own
        self.pc.forget_sign_in(self.agent.place)
        screen = self.screens["Sign-in"]
        assert isinstance(screen, SignInScreen)
        screen.say(problem)
        self.show_screen("Sign-in")
        self.update_tray()

    def sign_out(self) -> None:
        """Ends this sign-in on the agent. Servers keep running."""
        self.run(self.agent.sign_out, lambda _result: self.signed_out(), self.signed_out_anyway)

    def signed_out_anyway(self, exc: AgentError) -> None:
        # The agent couldn't be told, but this PC still forgets the sign-in.
        self.signed_out(str(exc))

    # ------------------------------------------------------------ Don't show again
    def hidden_loaded(self, answer: dict) -> None:
        ui.hidden.clear()
        ui.hidden.update(answer.get("hidden") or [])
        ui.on_hide = self.hide_warning

    def hide_warning(self, key: str) -> None:
        self.run(
            lambda: self.agent.call("POST", "/account/hidden", {"key": key}),
            lambda _r: None,
            lambda _e: None,
        )

    # ------------------------------------------------------------ Simple and Technical
    def mode_loaded(self, prefs: dict) -> None:
        self.tips_on = prefs.get("tips", "on") == "on"
        if self.mode_picked:  # a slow answer mustn't undo the person's own pick
            return
        mode["technical"] = prefs.get("mode") == "technical"
        self.show_mode()

    def set_mode(self, technical: bool) -> None:
        mode["technical"] = technical
        self.mode_picked = True
        self.show_mode()
        self.run(
            lambda: self.agent.set_mode("technical" if technical else "simple"), lambda _r: None
        )

    def show_mode(self) -> None:
        """Rebuilds what the mode changes: page tabs, chips and words."""
        if self.server_screen.row:
            self.server_screen.build_tabs()
            self.server_screen.show_row(self.server_screen.row)
            self.load_server_parts()
        # Simple mode hides Test servers.
        self.servers = [r for r in self.all_servers if mode["technical"] or not r.get("test")]
        self.screens["All servers"].fill(self.servers)  # type: ignore[attr-defined]
        self.fill_binder()

    # ------------------------------------------------------------ actions
    def server_action(self, server_id: str, action: str) -> None:
        def failed(exc: AgentError) -> None:
            self.screens["All servers"].failed(exc)  # type: ignore[attr-defined]
            self.server_screen.header.say(str(exc))

        def done(_result) -> None:
            self.refresh()
            self.load_server_parts()

        if action == "force_stop" and not self.confirm(
            text("confirm.force_stop_title"),
            text("confirm.force_stop_body"),
            text("action.force_stop"),
        ):
            return
        self.run(lambda: self.agent.server_action(server_id, action), done, failed)

    def confirm(self, title: str, body: str, yes: str, no: str | None = None) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, title, body, parent=self)
        ok = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        if no:
            box.addButton(no, QMessageBox.ButtonRole.RejectRole)
        else:
            box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is ok

    def ask(self, title: str, body: str, yes: str, field: str, value: str = "") -> str | None:
        """A question with a box to type in (a reason); None when cancelled."""
        box = QInputDialog(self)
        box.setWindowTitle(title)
        box.setLabelText(f"{body}\n\n{field}")
        box.setTextValue(value)
        box.setOkButtonText(yes)
        return box.textValue() if box.exec() else None

    def tick(
        self,
        title: str,
        body: str,
        choices: list[tuple[str, bool]],
        yes: str,
        locked: set[int] | tuple[()] = (),
    ) -> list[int] | None:
        """A question with a list to tick, each (words, ticked at first): the
        ticked ones' indexes, or None when cancelled. ``locked``: indexes
        shown greyed out, which can't be ticked."""
        box = QDialog(self)
        box.setWindowTitle(title)
        column = QVBoxLayout(box)
        column.addWidget(label(body))
        rows = QWidget()
        ticks = QVBoxLayout(rows)
        boxes = []
        for i, (words, ticked) in enumerate(choices):
            tick = QCheckBox(words)
            tick.setChecked(ticked and i not in locked)
            tick.setEnabled(i not in locked)
            ticks.addWidget(tick)
            boxes.append(tick)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(rows)
        scroll.setMaximumHeight(round(360 * self.scale))
        column.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(yes, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(box.accept)
        buttons.rejected.connect(box.reject)
        column.addWidget(buttons)
        if not box.exec():
            return None
        return [i for i, tick in enumerate(boxes) if tick.isChecked()]

    def pick(self, title: str, body: str, choices: list[str]) -> int | None:
        """One of a list, as its index; None when cancelled."""
        box = QInputDialog(self)
        box.setWindowTitle(title)
        box.setLabelText(body)
        box.setComboBoxItems(choices)
        if not box.exec():
            return None
        found = box.findChild(QComboBox)
        return found.currentIndex() if found else choices.index(box.textValue())

    def page_problem(self, exc: Exception) -> None:
        """What went wrong on a page, under the server's name."""
        self.server_screen.header.say(str(exc))

    def move_change(self, which: str) -> None:
        """Undo or Redo the server's newest change, after asking: it puts
        files back, and anything changed there since is lost."""
        header, sid = self.server_screen.header, self.server_id
        stack = header.changes.get(which) or []
        if not stack or sid is None:
            return
        change = stack[0]
        what = ", ".join(change.get("scope") or []) or text("changes.everything")
        body = text(f"changes.{which}_body", what=what, when=clock(change.get("backup_at")))
        running = header.row.get("state") == "ONLINE"
        if running:
            body += "\n\n" + text("changes.server_stops")
        if not self.confirm(
            text(f"changes.{which}_title", title=change["title"]), body, text(f"changes.{which}")
        ):
            return
        self.run(
            lambda: self.agent.move_change(sid, which, change["id"], start_after=running),
            lambda _r: self.load_server_parts(),
            lambda exc: header.say(str(exc)),
        )

    def run(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[AgentError], None] | None = None,
        always: Callable[[], None] | None = None,
        this_pc: bool = True,
    ) -> None:
        """Run ``work`` off the window's thread, then ``done`` or ``failed``
        back on it. Being signed out always goes back to the sign-in screen.
        ``this_pc=False`` for work on the owner's other PCs: their problems
        never sign this PC out or show it as not running."""
        task = Task(work)
        self._tasks.add(task)

        def finish() -> None:
            self._tasks.discard(task)
            if always:
                always()

        def on_failed(exc: AgentError) -> None:
            finish()
            if this_pc and isinstance(exc, SignedOut) and self.screen_name != "Sign-in":
                self.signed_out(text("error.session_ended"))
                return
            if failed:
                failed(exc)
            if this_pc and isinstance(exc, Unreachable):
                self.agent_down()

        def on_done(result: Any) -> None:
            finish()
            done(result)

        task.signals.done.connect(on_done)
        task.signals.failed.connect(on_failed)
        QThreadPool.globalInstance().start(task)
