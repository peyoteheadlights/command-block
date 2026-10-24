"""The Overview. Simple: Suggested actions, the setup checklist on a new
server, the owner's one Donate / No thanks card (agent/supportask.py), and
four cards (Server address, Last backup, Mods, Your world).
Technical: graphs, the console's last lines, details and mods.

Everything shown comes from the agent; a value it doesn't know is left out
or said to be unknown, never guessed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .fixes import get_help, run_fix, sure
from .graphs import Graph
from .theme import SHARED
from .ui import Note, clock, content_page, label, push, refill
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

# The agent names pages its own way; these are the desktop's.
PAGE_FOR = {
    "overview": "Overview",
    "players": "Players and chat",
    "console": "Console",
    "backups": "Backups",
    "mods": "Mods",
    "schedules": "Schedules",
    "performance": "Performance",
    "settings": "Settings",
    "game-settings": "Settings",
    "app-settings": "PC settings",
    "tailscale": "Tailscale",  # the step, which Reconnect runs again
}
# Every "?" in the app: the key and its plain-language words.
HINTS = {"address": "desktop.hint.address", "backup": "desktop.hint.backup"}


class Hint(QToolButton):
    """A small "?" that explains a harder word: on hover, on Tab + Space,
    and to Narrator (its accessible description)."""

    def __init__(self, key: str) -> None:
        super().__init__()
        words = text(HINTS[key])
        self.setObjectName("hint")
        self.setText("?")
        self.setToolTip(words)
        self.setAccessibleName(text("help.what_is_this"))
        self.setAccessibleDescription(words)
        self.clicked.connect(
            lambda: QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), words, self)
        )


def card(title: str, hint: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("kind", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    top = QHBoxLayout()
    heading = label(title, name="h2")
    heading.setWordWrap(False)  # a short title on one line, not squeezed by the hint
    top.addWidget(heading)
    if hint:
        top.addWidget(Hint(hint))
    top.addStretch()
    layout.addLayout(top)
    return frame, layout


def title_of(frame: QFrame) -> QLabel:
    """A card's title, to change its words later."""
    found = frame.findChild(QLabel, "h2")
    assert found is not None
    return found


class SimpleOverview(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        self.suggestions, layout = card(text("overview.recommendations"))
        self.suggestion_rows = QVBoxLayout()
        layout.addLayout(self.suggestion_rows)
        self.fixed = Note()  # what a fix button did
        layout.addWidget(self.fixed)
        column.addWidget(self.suggestions)

        self.checklist, layout = card(text("start.title"))
        self.progress = label(kind="sub")
        layout.addWidget(self.progress)
        self.checklist_rows = QVBoxLayout()
        layout.addLayout(self.checklist_rows)
        hide = push(text("start.hide"), "plain")
        hide.clicked.connect(self.hide_checklist)
        layout.addWidget(hide, 0, Qt.AlignmentFlag.AlignRight)
        self.checklist.hide()
        column.addWidget(self.checklist)

        self.ask, layout = card(text("desktop.ask.title"))
        layout.addWidget(label(text("desktop.ask.text"), "sub"))
        answers = QHBoxLayout()
        for answer, words, kind in (
            ("donate", "desktop.ask.donate", "primary"),
            ("no_thanks", "desktop.ask.no", "plain"),
        ):
            choice = push(text(words), kind)
            choice.clicked.connect(lambda _=False, a=answer: self.answer_ask(a))
            answers.addWidget(choice)
        answers.addStretch()
        layout.addLayout(answers)
        self.ask.hide()
        column.addWidget(self.ask)

        self.grid = grid = QGridLayout()
        grid.setSpacing(12)
        address, layout = card(text("desktop.card.address"), "address")
        self.address = label(name="h2")
        self.address.setProperty("kind", "address")
        self.address_note = label(text("desktop.card.address_body"), "sub")
        copy = push(text("join.copy"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.address.text()))
        self.copy = copy
        for widget in (self.address, self.address_note):
            layout.addWidget(widget)
        layout.addStretch()
        layout.addWidget(copy)

        backup, layout = card(text("overview.last_backup"), "backup")
        self.backup = label()
        self.backup_note = label(kind="sub")
        self.back_up = push(text("action.back_up_now"))
        self.back_up.clicked.connect(self.make_backup)
        for widget in (self.backup, self.backup_note):
            layout.addWidget(widget)
        layout.addStretch()
        buttons = QHBoxLayout()
        buttons.addWidget(self.back_up)
        see = push(text("desktop.backups.see"), "plain")
        see.clicked.connect(lambda: window.open_page("Backups"))
        buttons.addWidget(see)
        buttons.addStretch()
        layout.addLayout(buttons)

        mods, layout = card(text("page.mods"))
        self.mods_card = mods  # hidden for a type that takes no add-ons (Vanilla)
        self.mods_title = mods.findChild(QLabel, "h2")
        self.mods = label()
        open_mods = push(text("desktop.card.open_mods"))
        open_mods.clicked.connect(lambda: window.open_page("Mods"))
        layout.addWidget(self.mods)
        layout.addStretch()
        layout.addWidget(open_mods)

        world, layout = card(text("desktop.card.world"))
        layout.addWidget(label(text("desktop.card.world_body")))
        layout.addWidget(label(text("world.lead"), "sub"))
        layout.addStretch()
        restore = push(text("page.world"))
        restore.clicked.connect(lambda: window.open_page("Backups"))
        layout.addWidget(restore)

        self.cards = [address, backup, mods, world]
        for i, frame in enumerate(self.cards):
            grid.addWidget(frame, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        column.addLayout(grid)
        column.addStretch()

    def place_cards(self) -> None:
        """The cards two to a row; with one hidden (Vanilla has no Mods card)
        the last one takes the whole row, so there's no gap."""
        shown = [c for c in self.cards if not c.isHidden()]
        for frame in self.cards:
            self.grid.removeWidget(frame)
        for i, frame in enumerate(shown):
            last_alone = i == len(shown) - 1 and i % 2 == 0
            self.grid.addWidget(frame, i // 2, i % 2, 1, 2 if last_alone else 1)

    def even_cards(self) -> None:
        """All four cards as tall as the tallest, at the width they have."""
        shown = [c for c in self.cards if not c.isHidden()]
        tallest = max(max(c.sizeHint().height(), c.heightForWidth(c.width())) for c in shown)
        for frame in self.cards:
            frame.setMinimumHeight(tallest)

    def resizeEvent(self, event) -> None:
        self.even_cards()
        super().resizeEvent(event)

    # ------------------------------------------------------------ filled from the agent
    def show_suggestions(self, data: dict) -> None:
        found = data.get("recommendations") or []
        rows: list[QWidget] = []
        for rec in found:
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 4, 0, 4)
            words = QVBoxLayout()
            words.addWidget(label(rec["title"], "step"))
            words.addWidget(label(rec.get("reason", ""), "sub"))
            h.addLayout(words, 1)
            details = rec.get("details") or {}
            if rec["id"] == "crash":
                level = label(
                    text("desktop.fix.sure", level=sure(details.get("confidence"))), "chip"
                )
                level.setWordWrap(False)
                h.addWidget(level)
            action = rec.get("action") or {}
            help_only = rec["id"] == "crash" and not details.get("fix")
            if action.get("label") and not (help_only and self.window_.role not in (None, "owner")):
                fix = push(action["label"], "primary")
                fix.setProperty("suggestion", rec["id"])
                fix.clicked.connect(lambda _=False, r=rec: self.run_action(r))
                h.addWidget(fix)
            for verb, words_key in (("snooze", "rec.snooze"), ("dismiss", "rec.dismiss")):
                choice = push(text(words_key), "plain")
                choice.setToolTip(text(f"{words_key}_title"))
                choice.setAccessibleName(f"{text(words_key)}: {rec['title']}")
                choice.clicked.connect(
                    lambda _=False, r=rec["id"], v=verb: self.hide_suggestion(r, v)
                )
                h.addWidget(choice)
            rows.append(line)
        if not rows:
            running = self.row.get("state") == "ONLINE"
            rows = [
                label(
                    text(
                        "desktop.overview.best_running"
                        if running
                        else "desktop.overview.ready_to_start"
                    )
                )
            ]
        refill(self.suggestion_rows, rows)

    def say(self, words: str, undo: Callable[[], None] | None = None) -> None:
        self.fixed.say(words, undo)

    def run_action(self, rec: dict) -> None:
        action, details = rec.get("action") or {}, rec.get("details") or {}
        if details.get("fix"):
            run_fix(self.window_, self.row["id"], details["fix"], self.say)
        elif rec["id"] == "crash":
            get_help(self.window_, self.say)
        elif action.get("url"):
            self.window_.pc.open_link(action["url"])
        elif action.get("page") in PAGE_FOR:
            self.window_.open_page(PAGE_FOR[action["page"]])

    def hide_suggestion(self, rec_id: str, verb: str) -> None:
        sid = self.row["id"]
        self.window_.run(
            lambda: self.window_.agent.suggestion(sid, rec_id, verb), self.show_suggestions
        )

    def show_checklist(self, data: dict) -> None:
        show = bool(data.get("show")) and not data.get("finished")
        self.checklist.setVisible(show)
        if not show:
            return
        self.progress.setText(text("start.progress", done=data["done"], total=data["total"]))
        rows: list[QWidget] = []
        for item in data.get("items") or []:
            evidence = item.get("evidence") or {}
            values: dict[str, str] = {
                "when": clock(evidence.get("created_at")),
                "name": evidence.get("name") or "",
            }
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 2, 0, 2)
            words = QVBoxLayout()
            title = text(f"start.{item['id']}")
            words.addWidget(label(("✓ " if item.get("done") else "") + title, "step"))
            words.addWidget(label(text(f"start.{item['evidence_key']}").format(**values), "sub"))
            h.addLayout(words, 1)
            if not item.get("done") and item.get("page") in PAGE_FOR:
                go = push(text(f"start.{item['id']}_action"))
                go.clicked.connect(
                    lambda _=False, p=PAGE_FOR[item["page"]]: self.window_.open_page(p)
                )
                h.addWidget(go)
            rows.append(line)
        refill(self.checklist_rows, rows)

    def hide_checklist(self) -> None:
        sid = self.row["id"]
        self.window_.run(lambda: self.window_.agent.dismiss_checklist(sid), self.show_checklist)

    def show_ask(self, data: dict) -> None:
        self.ask.setVisible(bool(data.get("show")))

    def answer_ask(self, answer: str) -> None:
        self.ask.hide()
        if answer == "donate":
            links = json.loads((SHARED / "links.json").read_text(encoding="utf-8"))
            self.window_.pc.open_link(links["sponsors"])
        self.window_.run(lambda: self.window_.agent.answer_support_ask(answer), self.show_ask)

    def show_address(self, join: dict) -> None:
        java = join.get("java") or {}
        tailscale = java.get("tailscale") or {}
        host = (tailscale.get("dns_name") or "").rstrip(".") or tailscale.get("address")
        if host and java.get("port") and not java.get("default_port"):
            host = f"{host}:{java['port']}"
        self.address.setText(host or "")
        self.address.setVisible(bool(host))
        self.copy.setVisible(bool(host))
        self.address_note.setText(
            text("desktop.card.address_body") if host else text("join.no_tailscale")
        )
        self.even_cards()

    def show_backups(self, data: dict) -> None:
        checked = [b for b in data.get("backups") or [] if b.get("status") == "ok"]
        if checked:
            newest = max(checked, key=lambda b: b.get("created_at") or 0)
            self.backup.setText(text("start.backup_done", when=clock(newest.get("created_at"))))
            self.backup_note.hide()
        else:
            self.backup.setText(text("backups.none"))
            self.backup_note.setText(text("backups.none_hint"))
            self.backup_note.show()
        self.back_up.setVisible(self.window_.may(self.row["id"], "backups.create"))
        self.even_cards()

    def make_backup(self) -> None:
        sid = self.row["id"]
        self.back_up.setEnabled(False)
        self.window_.run(
            lambda: self.window_.agent.back_up(sid),
            lambda _r: self.window_.load_server_parts(),
            self.backup_failed,
            lambda: self.back_up.setEnabled(True),
        )

    def backup_failed(self, exc: Exception) -> None:
        self.backup_note.setText(str(exc))
        self.backup_note.show()

    def show_mods(self, data: dict) -> None:
        assert self.mods_title is not None
        self.mods_title.setText(text(content_page(self.row) or "page.mods"))
        packs = "addons" in data  # a Bedrock server's add-ons
        count = len(data.get("addons" if packs else "mods") or [])
        kind = "addons.installed" if packs else "mods.installed"
        self.mods.setText(
            text(f"{kind}.one" if count == 1 else f"{kind}.other", count=count)
            if count
            else text("addons.none" if packs else "mods.none")
        )
        self.even_cards()


# ---------------------------------------------------------------- Technical
class TechnicalOverview(QWidget):
    """Graphs of TPS, MSPT, memory and CPU, the console's last lines, the
    details and the mods with versions. The graphs ask the agent for new
    samples only while they can be seen."""

    REFRESH_MS = 30_000  # the agent's samples are a minute apart

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.row: dict = {}
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        graphs, layout = card(text("page.performance", technical=True))
        row = QHBoxLayout()
        layout.addLayout(row)
        self.graphs: dict[str, Graph] = {}
        self.values: dict[str, QLabel] = {}
        for key, words, unit, top in (
            ("tps", "overview.speed", "", 20.0),
            ("mspt", "desktop.graph.mspt", "ms", 50.0),
            ("proc_ram_mb", "overview.memory", "GB", 4.0),
            ("cpu_percent", "overview.cpu", "%", 100.0),
        ):
            box = QVBoxLayout()
            box.addWidget(label(text(words, technical=True), "sub"))
            value = label(name="h2")
            box.addWidget(value)
            graph = Graph(text(words, technical=True), unit, top)
            box.addWidget(graph)
            row.addLayout(box, 1)
            self.graphs[key], self.values[key] = graph, value
        column.addWidget(graphs)

        lower = QHBoxLayout()
        lower.setSpacing(12)
        console, layout = card(text("overview.recent_console", technical=True))
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setAccessibleName(text("overview.recent_console", technical=True))
        self.console.setMinimumHeight(180)
        layout.addWidget(self.console)
        open_console = push(text("overview.open_console", technical=True), "plain")
        open_console.clicked.connect(lambda: window.open_page("Console"))
        layout.addWidget(open_console, 0, Qt.AlignmentFlag.AlignRight)
        lower.addWidget(console, 1)
        side = QVBoxLayout()
        details, layout = card(text("overview.details", technical=True))
        self.details = QGridLayout()
        layout.addLayout(self.details)
        side.addWidget(details)
        mods, layout = card(text("page.mods", technical=True))
        self.mods_card = mods
        self.mods_title = mods.findChild(QLabel, "h2")
        self.mods = QVBoxLayout()
        layout.addLayout(self.mods)
        side.addWidget(mods)
        side.addStretch()
        lower.addLayout(side, 1)
        column.addLayout(lower)
        column.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(self.REFRESH_MS)
        self.timer.timeout.connect(self.reload)

    def showEvent(self, event) -> None:
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def reload(self) -> None:
        if self.isVisible() and not self.window().isMinimized():
            self.window_.load_technical()

    def style_graphs(self, look: dict[str, str]) -> None:
        colors = {
            "line": look["accent"],
            "text": look["text-secondary"],
            "border": look["text-tertiary"],
            "plot": look["surface-sunken"],
        }
        for graph in self.graphs.values():
            graph.colors = colors
            graph.update()

    def show_performance(self, data: dict) -> None:
        series = data.get("series") or {}
        # Longer than this with no sample means the agent wasn't running.
        gap = max(float(series.get("bucket_seconds") or 60) * 2.5, data["sample_interval"] * 3)
        end = time.time()
        start = end - float(series.get("hours") or 1) * 3600
        points = series.get("points") or []
        limit = data.get("memory_limit_mb")
        if limit:
            self.graphs["proc_ram_mb"].top = limit / 1024
        current = data.get("current") or {}
        running = self.row.get("state") == "ONLINE"
        for key, graph in self.graphs.items():
            scale = 1 / 1024 if key == "proc_ram_mb" else 1  # MB to GB
            values = [(p["ts"], None if p.get(key) is None else p[key] * scale) for p in points]
            graph.show_points(values, start, end, gap)
            now = current.get(key)
            if now is not None:
                self.values[key].setText(graph.value(now * scale))
            elif key != "cpu_percent" and not running:
                self.values[key].setText(text("servers.not_running"))
            else:
                self.values[key].setText(text("overview.not_measured"))

    def show_unknown(self, _exc: Exception | None = None) -> None:
        """The agent can't be reached: no live value is shown, not even the
        last one it sent."""
        for value in self.values.values():
            value.setText(text("state.unknown"))
        for graph in self.graphs.values():
            graph.show_points([], 0, 0, 60)

    def show_status(self, status: dict) -> None:
        while self.details.count():
            old = self.details.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        port = (self.row.get("game_port") or {}).get("port")
        loader = status.get("loader_version") and (
            f"{status.get('loader_name') or ''} {status['loader_version']}".strip()
        )
        rows = [
            (text("overview.minecraft"), status.get("minecraft_version")),
            (status.get("loader_name") or text("overview.loader"), loader),
            (text("overview.java"), status.get("java_version")),
            (text("overview.port"), port),
        ]
        for i, (name, value) in enumerate(rows):
            self.details.addWidget(label(name, "sub"), i, 0)
            shown = label(str(value) if value else text("state.unknown"), "address")
            self.details.addWidget(shown, i, 1)

    def show_console(self, data: dict) -> None:
        lines = [line["raw"] for line in data.get("lines") or []]
        self.console.setPlainText("\n".join(lines) or text("console.empty_hint"))

    def show_mods(self, data: dict) -> None:
        assert self.mods_title is not None
        self.mods_title.setText(text(content_page(self.row) or "page.mods"))
        found = data.get("mods") or data.get("addons") or []
        rows: list[QWidget] = [
            label(f"{m.get('name') or m.get('filename')}  {m.get('version') or ''}".strip())
            for m in found
        ]
        refill(self.mods, rows or [label(text("mods.none"), "sub")])
