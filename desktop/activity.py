"""Activity: everything that happened to the server as plain sentences grouped
by day, and a Crashes filter with what the agent's crash analyzer found. It
opens from "See all" under Recent activity and from a crash alert."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from .fixes import get_help, run_fix, sure
from .overview import card, title_of
from .ui import Page, clock, duration, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

# Event type to its sentence. Anything else
# shows the agent's own message.
FEED = {
    kind: f"feed.{kind}"
    for kind in (
        "server_started",
        "server_stopped",
        "server_crashed",
        "server_restarted",
        "server_recovered",
        "crash_loop",
        "player_joined",
        "player_left",
        "backup_completed",
        "backup_failed",
        "low_tps",
        "high_mspt",
        "high_ram",
        "high_cpu",
        "low_disk",
        "auth_failure",
        "agent_started",
        "agent_stopping",
    )
}
# Steps on the way to something that matters: Simple leaves them out.
INTERNAL = {
    "state",
    "console",
    "metrics",
    "job",
    "chat",
    "plain",
    "text",
    "server_start_requested",
    "server_stop_requested",
    "backup_started",
    "restore_stopping",
    "backup_retention",
    "tps_detection_started",
    "tps_command_detected",
    "tps_detection_failed",
    "server_changed",
    "data_folder_moved",
    "data_folder_not_moved",
}


def event_data(event: dict) -> dict:
    """The event's data: a dict when live, JSON text from the history."""
    data = event.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def sentence(event: dict) -> str:
    key = FEED.get(event.get("type", ""))
    if not key:
        return event.get("message") or event.get("type", "")
    data = event_data(event)
    if event["type"] == "server_crashed" and data.get("reason") == "startup_failure":
        key = "feed.server_failed_to_start"
    played = data.get("session_seconds")
    if event["type"] == "player_left" and played is None:
        key = "feed.player_left_only"
    return text(
        key,
        name=data.get("username") or data.get("server_name") or data.get("name") or "",
        duration=duration(played) if played is not None else "",
    )


def day_label(ts: float) -> str:
    days = (date.today() - date.fromtimestamp(ts)).days
    if days <= 0:
        return text("feed.today")
    if days == 1:
        return text("feed.yesterday")
    return time.strftime("%A" if days < 7 else "%d %b %Y", time.localtime(ts))


def cause(category: str | None) -> str:
    """The crash cause in words; the analyzer's own name in Technical."""
    if not category or category == "Unknown":
        return text("crashes.cause_unknown")
    words = text(f"cause.{category}")
    if mode["technical"] or words == f"cause.{category}":
        return category
    return words[:1].upper() + words[1:]


class ActivityPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.crashes_only = False
        top = QHBoxLayout()
        self.filters = QButtonGroup(self)
        for crashes in (False, True):
            button = push("")
            button.setCheckable(True)
            button.setProperty("kind", "segment")
            button.setChecked(crashes == self.crashes_only)
            button.clicked.connect(lambda _=False, c=crashes: self.show_crashes(c))
            self.filters.addButton(button, int(crashes))
            top.addWidget(button)
        top.addStretch()
        self.help = push("")
        self.help.clicked.connect(lambda: get_help(self.window_, self.say))
        top.addWidget(self.help)
        self.column.addLayout(top)
        self.box, layout = card("")
        self.items = QVBoxLayout()
        layout.addLayout(self.items)
        self.column.addWidget(self.box)
        self.column.addStretch()

    def show_crashes(self, on: bool) -> None:
        self.crashes_only = on
        self.load()

    def load(self) -> None:
        self.filters.button(0).setText(text("desktop.activity.everything"))
        self.filters.button(1).setText(text("page.crashes"))
        self.filters.button(int(self.crashes_only)).setChecked(True)
        self.help.setText(text("gethelp.button"))
        self.help.setVisible(self.window_.role in (None, "owner"))  # help.bundle
        if self.crashes_only:
            self.ask("GET", "/crashes", self.show_crash_list)
        else:
            self.ask("GET", "/events?limit=200", self.show_events)

    # ------------------------------------------------------------ everything
    def show_events(self, data: dict) -> None:
        if self.crashes_only:
            return  # an answer from before Crashes was clicked
        title_of(self.box).setText(text("desktop.page.activity"))
        shown = [
            e for e in data.get("events") or [] if mode["technical"] or e["type"] not in INTERNAL
        ]
        rows: list[QWidget] = []
        day = None
        for event in shown:
            if day_label(event["ts"]) != day:
                day = day_label(event["ts"])
                rows.append(label(day, "step"))
            rows.append(self.event_row(event))
        refill(self.items, rows or [label(text("events.none"), "sub")])

    def event_row(self, event: dict) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 2, 0, 2)
        h.addWidget(label(clock(event["ts"]), "muted"))
        words = QVBoxLayout()
        words.addWidget(label(sentence(event), "problem" if event.get("level") == "error" else ""))
        if mode["technical"]:
            data = event_data(event)
            raw = " ".join(f"{k}={v}" for k, v in data.items() if not isinstance(v, dict | list))
            words.addWidget(label(f"{event['type']}  {raw}".strip(), "address"))
        h.addLayout(words, 1)
        return row

    # ------------------------------------------------------------ crashes
    def show_crash_list(self, data: dict) -> None:
        if not self.crashes_only:
            return  # an answer from before Everything was clicked
        title_of(self.box).setText(text("crashes.title"))
        crashes = data.get("crashes") or []
        refill(
            self.items,
            [self.crash_row(c, newest=i == 0) for i, c in enumerate(crashes)]
            or [label(text("crashes.none"), "sub")],
        )

    def crash_row(self, crash: dict, newest: bool = False) -> QFrame:
        frame = QFrame()
        frame.setProperty("kind", "row")
        column = QVBoxLayout(frame)
        column.setContentsMargins(12, 8, 8, 8)
        top = QHBoxLayout()
        analysis = (crash.get("context") or {}).get("analysis") or {}
        category = crash.get("category") or analysis.get("category")
        confidence = crash.get("confidence") or analysis.get("confidence")
        top.addWidget(label(clock(crash["ts"]), "muted"))
        top.addWidget(label(cause(category), "step"), 1)
        if mode["technical"]:
            code = crash.get("exit_code")
            top.addWidget(
                label(
                    f"{text('crashes.f_code')}: {code if code is not None else text('state.unknown')}",
                    "address",
                )
            )
        chip = label(sure(confidence), "chip")
        chip.setWordWrap(False)
        top.addWidget(chip)
        fix = crash.get("fix")
        if newest and fix:  # an older crash's fix may no longer apply
            button = push(fix["label"], "primary")
            button.clicked.connect(lambda: run_fix(self.window_, self.row["id"], fix, self.say))
            top.addWidget(button)
        more = push(text("crashes.details"))
        top.addWidget(more)
        column.addLayout(top)
        details = self.crash_details(crash, analysis, confidence)
        details.hide()

        def toggle() -> None:
            details.setVisible(not details.isVisible())
            more.setText(text("desktop.crash.hide" if details.isVisible() else "crashes.details"))

        more.clicked.connect(toggle)
        column.addWidget(details)
        if confidence not in ("confirmed", "likely"):
            column.addWidget(label(text("desktop.crash.unclear"), "sub"))
        return frame

    def crash_details(self, crash: dict, analysis: dict, confidence: str | None) -> QWidget:
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 6, 0, 0)
        if crash.get("summary") or analysis.get("summary"):
            column.addWidget(label(crash.get("summary") or analysis["summary"]))
        if mode["technical"] and analysis.get("advice"):  # Simple gets the fix button
            column.addWidget(label(analysis["advice"], "sub"))
        if analysis.get("suspect_mods"):
            column.addWidget(
                label(
                    text("crashes.suspect_mods")
                    + ", ".join(analysis["suspect_mods"])
                    + text("crashes.suspect_hint"),
                    "sub",
                )
            )
        if mode["technical"] and analysis.get("evidence_basis"):
            column.addWidget(label(analysis["evidence_basis"], "sub"))
        evidence = crash.get("evidence") or analysis.get("evidence") or []
        if evidence:
            column.addWidget(label(text("crashes.evidence"), "step"))
            column.addWidget(label("\n".join(evidence), "address"))
        context = crash.get("context") or {}
        column.addWidget(label(text("crashes.context"), "step"))
        for key, value in (
            ("crashes.f_minecraft", context.get("minecraft_version")),
            ("crashes.f_loader", context.get("fabric_loader")),
            ("crashes.f_java", context.get("java_version")),
            (
                "crashes.f_players",
                ", ".join(context.get("players_online") or []) or text("crashes.no_players"),
            ),
            ("crashes.f_report", context.get("crash_report") or text("crashes.no_report")),
            ("crashes.f_log", context.get("log_file") or context.get("console_file")),
        ):
            shown = value if value not in (None, "") else text("state.unknown")
            column.addWidget(label(f"{text(key)}: {shown}", "sub"))
        column.addWidget(label(text("crashes.rule_match"), "muted"))
        return box
