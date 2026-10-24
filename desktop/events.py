"""Events and polls on the Quests and events page: one server's events
(Going / Maybe / Can't answered in Discord, attendance to approve or fix for
7 days), the new event form, its polls and the new poll form. The agent
checks every value and the "Quests and events" permission again."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .overview import card
from .quests import RewardFields, reward_words, short
from .ui import clock, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .quests import QuestsPage

ANSWERS = 5  # a poll's answer fields
SHOWN_STATES = ("counting", "approving", "ended")  # attendance is shown then


def when_box(hours_ahead: int) -> QDateTimeEdit:
    box = QDateTimeEdit(QDateTime.currentDateTime().addSecs(hours_ahead * 3600))
    box.setCalendarPopup(True)
    box.setDisplayFormat("yyyy-MM-dd HH:mm")
    box.setMinimumDateTime(QDateTime.currentDateTime())
    return box


class EventsPart:
    """Its cards go in the page's column; ``show`` fills them."""

    def __init__(self, page: QuestsPage) -> None:
        self.page = page
        self.data: dict = {}
        self.listed, layout = card(text("game_events.list"))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.form = self.new_event_form()
        self.polls, layout = card(text("game_events.polls"))
        self.poll_rows = QVBoxLayout()
        layout.addLayout(self.poll_rows)
        self.poll_form = self.new_poll_form()
        self.cards = [self.listed, self.form, self.polls, self.poll_form]

    def load(self) -> None:
        self.page.ask("GET", "/quests/events", self.show)

    def show(self, data: dict) -> None:
        self.data = data
        roles = self.page.role_names()
        refill(
            self.rows,
            [self.event_row(e, roles) for e in data.get("events") or []]
            or [label(text("game_events.none"), "sub")],
        )
        refill(
            self.poll_rows,
            [self.poll_row(p) for p in data.get("polls") or []]
            or [label(text("game_events.no_polls"), "sub")],
        )
        kind = self.kind.currentData()
        self.kind.blockSignals(True)
        self.kind.clear()
        for key in data.get("kinds") or []:
            self.kind.addItem(text(f"game_events.kind.{key}"), key)
        self.kind.setCurrentIndex(max(self.kind.findData(kind), 0))
        self.kind.blockSignals(False)
        self.kind_changed()
        self.rewards.fill_roles(roles, bool(self.page.data.get("bot")))

    # ------------------------------------------------------------ rows
    def event_row(self, e: dict, roles: dict[str, str]) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        v = QVBoxLayout(row)
        v.setContentsMargins(12, 8, 8, 8)
        top = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(label(e["title"], "step"))
        how = text(f"game_events.counts.{e['kind']}", goal=short(e.get("goal") or ""))
        parts = [
            text("game_events.time", start=clock(e["starts_at"]), end=clock(e["ends_at"])),
            how,
        ]
        given = [text("game_events.attend_xp")] + [reward_words(r, roles) for r in e["rewards"]]
        parts.append(text("quests.rewards", rewards=", ".join(given)))
        if e.get("start_server"):
            parts.append(text("game_events.starts_server"))
        words.addWidget(label("  ·  ".join(parts), "sub"))
        if e.get("details"):
            words.addWidget(label(e["details"], "muted"))
        a = e["answers"]
        line = text("game_events.answers", going=a["going"], maybe=a["maybe"], no=a["no"])
        line += "  ·  " + text(f"game_events.state.{e['state']}")
        words.addWidget(label(line, "muted"))
        top.addLayout(words, 1)
        if e["state"] in ("coming", "on"):
            off = push(text("game_events.call_off"))
            off.setAccessibleName(f"{text('game_events.call_off')}: {e['title']}")
            off.clicked.connect(lambda _=False: self.call_off(e))
            top.addWidget(off, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)
        if e["state"] in SHOWN_STATES:
            v.addWidget(self.attendance(e))
        return row

    def attendance(self, e: dict) -> QWidget:
        """Who came: ticked boxes, changeable for 7 days after the end."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 4, 0, 0)
        people = e.get("attendance") or []
        hint = "game_events.approve_hint" if e["state"] == "approving" else "game_events.fix_hint"
        v.addWidget(label(text(hint) if people else text("game_events.nobody"), "muted"))
        for person in people:
            words = text(
                "game_events.person", name=person["discord_name"], minutes=person["minutes"]
            )
            if mode["technical"]:
                how = text(f"game_events.how.{person['how']}", by=person["fixed_by"] or "")
                words += f"  ·  {how}"
            tick = QCheckBox(words)
            tick.setChecked(person["attended"])
            tick.setEnabled(bool(e.get("fixable")))
            tick.setAccessibleName(f"{text('game_events.came')}: {person['discord_name']}")
            tick.toggled.connect(lambda on, p=person: self.fix(e, p, on))
            v.addWidget(tick)
        if e["state"] == "approving":
            ok = push(text("game_events.approve"), "primary")
            ok.setAccessibleName(f"{text('game_events.approve')}: {e['title']}")
            ok.clicked.connect(lambda _=False: self.approve(e))
            buttons = QHBoxLayout()
            buttons.addWidget(ok)
            buttons.addStretch()
            v.addLayout(buttons)
        return box

    def poll_row(self, p: dict) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        words.addWidget(label(p["question"], "step"))
        counts = [f"{a}: {n}" for a, n in zip(p["answers"], p["votes"], strict=True)]
        words.addWidget(label("  ·  ".join(counts), "sub"))
        state = (
            text("game_events.poll_open", n=p["total"], time=clock(p["closes_at"]))
            if p["open"]
            else text("game_events.poll_closed", n=p["total"])
        )
        words.addWidget(label(state, "muted"))
        h.addLayout(words, 1)
        if p["open"]:
            close = push(text("game_events.close_poll"))
            close.setAccessibleName(f"{text('game_events.close_poll')}: {p['question']}")
            close.clicked.connect(lambda _=False: self.close_poll(p))
            h.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        return row

    # ------------------------------------------------------------ forms
    def new_event_form(self) -> QFrame:
        frame, layout = card(text("game_events.new"))
        form = text("game_events.new")
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.details = QLineEdit()
        self.details.setMaxLength(300)
        self.kind = QComboBox()
        self.goal = QLineEdit()
        self.goal.setMaxLength(100)
        self.starts = when_box(24)
        self.ends = when_box(26)
        grid = QGridLayout()
        for i, (key, widget) in enumerate(
            (
                ("quests.title", self.title),
                ("quests.details", self.details),
                ("game_events.kind", self.kind),
                ("game_events.goal", self.goal),
                ("game_events.starts", self.starts),
                ("game_events.ends", self.ends),
            )
        ):
            widget.setAccessibleName(f"{text(key)}, {form}")
            grid.addWidget(label(text(key), "sub"), (i // 2) * 2, i % 2)
            grid.addWidget(widget, (i // 2) * 2 + 1, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.goal_hint = label(kind="muted")
        layout.addWidget(self.goal_hint)
        self.start_server = QCheckBox(text("game_events.start_server"))
        layout.addWidget(self.start_server)
        layout.addWidget(label(text("quests.rewards_title"), "step"))
        layout.addWidget(label(text("game_events.rewards_hint"), "muted"))
        self.rewards = RewardFields(form)
        layout.addLayout(self.rewards.grid)
        self.problem = label(kind="problem")
        self.problem.hide()
        layout.addWidget(self.problem)
        make = push(text("game_events.create"), "primary")
        make.clicked.connect(self.make)
        row = QHBoxLayout()
        row.addWidget(make)
        row.addStretch()
        layout.addLayout(row)
        self.kind.currentIndexChanged.connect(self.kind_changed)
        return frame

    def kind_changed(self, _index: int = 0) -> None:
        kind = self.kind.currentData() or "online"
        self.goal.setEnabled(kind == "advancement")
        hint = text(f"game_events.kind_hint.{kind}")
        if "advancement" not in (self.data.get("kinds") or ["advancement"]):
            hint += " " + text("game_events.limited_kinds")
        self.goal_hint.setText(hint)

    def new_poll_form(self) -> QFrame:
        frame, layout = card(text("game_events.new_poll"))
        form = text("game_events.new_poll")
        self.question = QLineEdit()
        self.question.setMaxLength(150)
        self.question.setAccessibleName(f"{text('game_events.question')}, {form}")
        layout.addWidget(label(text("game_events.question"), "sub"))
        layout.addWidget(self.question)
        grid = QGridLayout()
        self.answers: list[QLineEdit] = []
        for i in range(ANSWERS):
            box = QLineEdit()
            box.setMaxLength(55)
            box.setPlaceholderText(text("game_events.answer", n=i + 1))
            box.setAccessibleName(text("game_events.answer", n=i + 1))
            self.answers.append(box)
            grid.addWidget(box, i // 3, i % 3)
        layout.addLayout(grid)
        layout.addWidget(label(text("game_events.answers_hint"), "muted"))
        self.closes = when_box(24)
        self.closes.setAccessibleName(text("game_events.closes"))
        closes = QHBoxLayout()
        closes.addWidget(label(text("game_events.closes"), "sub"))
        closes.addWidget(self.closes)
        closes.addStretch()
        layout.addLayout(closes)
        self.poll_problem = label(kind="problem")
        self.poll_problem.hide()
        layout.addWidget(self.poll_problem)
        make = push(text("game_events.create_poll"), "primary")
        make.clicked.connect(self.make_poll)
        row = QHBoxLayout()
        row.addWidget(make)
        row.addStretch()
        layout.addLayout(row)
        return frame

    # ------------------------------------------------------------ sending
    def make(self) -> None:
        body: dict[str, Any] = {
            "title": self.title.text(),
            "details": self.details.text(),
            "kind": self.kind.currentData(),
            "goal": self.goal.text() if self.goal.isEnabled() else "",
            "starts_at": self.starts.dateTime().toSecsSinceEpoch(),
            "ends_at": self.ends.dateTime().toSecsSinceEpoch(),
            "start_server": self.start_server.isChecked(),
            "rewards": self.rewards.values(),
        }

        def done(data: dict) -> None:
            self.problem.hide()
            for box in (self.title, self.details, self.goal):
                box.clear()
            self.rewards.clear()
            self.start_server.setChecked(False)
            self.page.say(text("game_events.made"))
            self.show(data)

        self.page.ask("POST", "/quests/events", done, self.failed(self.problem), body=body)

    def make_poll(self) -> None:
        body = {
            "question": self.question.text(),
            "answers": [box.text() for box in self.answers],
            "closes_at": self.closes.dateTime().toSecsSinceEpoch(),
        }

        def done(data: dict) -> None:
            self.poll_problem.hide()
            for box in (self.question, *self.answers):
                box.clear()
            self.page.say(text("game_events.poll_made"))
            self.show(data)

        self.page.ask("POST", "/quests/polls", done, self.failed(self.poll_problem), body=body)

    @staticmethod
    def failed(problem):
        def show(exc: Exception) -> None:
            problem.setText(str(exc))
            problem.show()

        return show

    def call_off(self, e: dict) -> None:
        if self.page.window_.confirm(
            text("game_events.call_off_title", title=e["title"]),
            text("game_events.call_off_body"),
            text("game_events.call_off"),
        ):
            self.page.ask("DELETE", f"/quests/events/{e['id']}", self.show)

    def approve(self, e: dict) -> None:
        self.page.ask("POST", f"/quests/events/{e['id']}/approve", self.show)

    def fix(self, e: dict, person: dict, attended: bool) -> None:
        self.page.ask(
            "PUT",
            f"/quests/events/{e['id']}/attendance/{person['discord_id']}",
            self.show,
            body={"attended": attended},
        )

    def close_poll(self, p: dict) -> None:
        self.page.ask("DELETE", f"/quests/polls/{p['id']}", self.show)
