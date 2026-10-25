"""The Quests and events page: one server's quests (made here, joined in
Discord), "done" claims to approve with their screenshot, quest ideas from
/quest suggest, the rewards not given yet (Technical), events and polls
(desktop/events.py), and the Level roles of each Discord server it's set up
in. For the owner and people with Quests and events; the agent checks that
too."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .discord import levels_box
from .overview import card
from .ui import Page, clock, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

KINDS = ("stat", "advancement", "playtime", "joins", "items", "approval")
REPEATS = ("once", "daily", "weekly", "monthly", "yearly")
NEEDS_GOAL = ("stat", "advancement", "items")
SHOT_WIDTH = 320  # the screenshot as shown; the full picture stays on the PC


def short(name: str) -> str:
    return name.removeprefix("minecraft:").replace("_", " ")


def task_words(q: dict) -> str:
    goal = q.get("goal") or ""
    if q["kind"] == "stat":
        section, _, name = goal.partition(" ")
        goal = f"{short(section)}: {short(name)}"
    return text(f"quests.task.{q['kind']}", count=q["count"], goal=short(goal))


def reward_words(r: dict, roles: dict[str, str]) -> str:
    return text(
        f"quests.reward.{r['kind']}",
        role=roles.get(r.get("role_id", ""), text("value.unknown")),
        item=short(r.get("item", "")),
        count=r.get("count", ""),
        levels=r.get("levels", ""),
        amount=r.get("amount", ""),
    )


class RewardFields:
    """The reward templates' fields (a Discord role, an item, Minecraft XP,
    Level XP), shared by the quest and event forms. ``form`` tells the two
    apart for screen readers."""

    def __init__(self, form: str = "") -> None:
        self.role = QComboBox()
        self.item = QLineEdit()
        self.item.setPlaceholderText("diamond")
        self.item.setMaxLength(100)
        self.item_count = QSpinBox()
        self.item_count.setRange(1, 64)
        self.xp = QSpinBox()
        self.xp.setRange(0, 30)
        self.level_xp = QSpinBox()
        self.level_xp.setRange(0, 500)
        self.level_xp.setSingleStep(10)
        self.grid = QGridLayout()
        for i, (key, widget) in enumerate(
            (
                ("quests.reward_role", self.role),
                ("quests.reward_item", self.item),
                ("quests.reward_item_count", self.item_count),
                ("quests.reward_xp", self.xp),
                ("quests.reward_level_xp", self.level_xp),
            )
        ):
            widget.setAccessibleName(f"{text(key)}, {form}" if form else text(key))
            self.grid.addWidget(label(text(key), "sub"), 0, i)
            self.grid.addWidget(widget, 1, i)
            self.grid.setColumnStretch(i, 1)

    def fill_roles(self, roles: dict[str, str], bot: bool) -> None:
        role = self.role.currentData()
        self.role.clear()
        self.role.addItem(text("quests.reward_none"), None)
        for rid, name in sorted(roles.items(), key=lambda x: x[1].lower()):
            self.role.addItem(name, rid)
        self.role.setCurrentIndex(max(self.role.findData(role), 0))
        self.role.setEnabled(bot)

    def values(self) -> list[dict[str, Any]]:
        rewards: list[dict[str, Any]] = []
        if self.role.currentData():
            rewards.append({"kind": "role", "role_id": self.role.currentData()})
        if self.item.text().strip():
            rewards.append(
                {"kind": "item", "item": self.item.text().strip(), "count": self.item_count.value()}
            )
        if self.xp.value():
            rewards.append({"kind": "xp", "levels": self.xp.value()})
        if self.level_xp.value():
            rewards.append({"kind": "level_xp", "amount": self.level_xp.value()})
        return rewards

    def clear(self) -> None:
        self.item.clear()
        for spin in (self.xp, self.level_xp):
            spin.setValue(0)


class QuestsPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.data: dict = {}
        self.suggestion: int | None = None  # the idea the form is making into a quest
        self.no_bot = label(text("quests.no_bot"), "sub")
        self.column.addWidget(self.no_bot)
        self.listed, layout = card(text("quests.list"))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.column.addWidget(self.listed)
        self.claims, layout = card(text("quests.claims"))
        self.claim_rows = QVBoxLayout()
        layout.addLayout(self.claim_rows)
        self.column.addWidget(self.claims)
        self.ideas, layout = card(text("quests.ideas"))
        self.idea_rows = QVBoxLayout()
        layout.addLayout(self.idea_rows)
        self.column.addWidget(self.ideas)
        self.form = self.new_quest_form()
        self.column.addWidget(self.form)
        self.waiting, layout = card(text("quests.rewards_waiting"))
        self.reward_rows = QVBoxLayout()
        layout.addLayout(self.reward_rows)
        self.column.addWidget(self.waiting)
        from .events import EventsPart  # it uses this file's reward fields

        self.events = EventsPart(self)
        for part in self.events.cards:
            self.column.addWidget(part)
        self.levels = QVBoxLayout()
        self.column.addLayout(self.levels)
        self.column.addStretch()

    def load(self) -> None:
        self.ask("GET", "/quests", self.show_quests)
        self.events.load()

    def show_quests(self, data: dict) -> None:
        self.data = data
        roles = self.role_names()
        self.no_bot.setVisible(not data.get("bot"))
        refill(
            self.rows,
            [self.quest_row(q, roles) for q in data.get("quests") or []]
            or [label(text("quests.none"), "sub")],
        )
        claims = data.get("claims") or []
        self.claims.setVisible(bool(claims))
        refill(self.claim_rows, [self.claim_row(c) for c in claims])
        ideas = data.get("suggestions") or []
        self.ideas.setVisible(bool(ideas))
        refill(self.idea_rows, [self.idea_row(s) for s in ideas])
        owed = data.get("rewards") or []
        self.waiting.setVisible(bool(owed) and mode["technical"])
        refill(self.reward_rows, [self.reward_row(r, roles) for r in owed])
        self.fill_form_choices(roles)
        self.events.rewards.fill_roles(roles, bool(data.get("bot")))
        boxes: list[QWidget] = []
        for guild in data.get("guilds") or []:
            frame, layout = card(text("quests.levels", guild=guild["name"]))
            layout.addWidget(levels_box(guild, guild.get("roles") or [], self.save_levels(guild)))
            boxes.append(frame)
        refill(self.levels, boxes)

    def role_names(self) -> dict[str, str]:
        return {
            r["id"]: r["name"] for g in self.data.get("guilds") or [] for r in g.get("roles") or []
        }

    # ------------------------------------------------------------ rows
    def quest_row(self, q: dict, roles: dict[str, str]) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        title = f"{q['id']}. {q['title']}"
        words.addWidget(label(title, "step"))
        parts = [task_words(q)]
        if q["repeat"] != "once":
            parts.append(text(f"quests.repeat.{q['repeat']}"))
        if q.get("ends_at"):
            parts.append(text("quests.until", time=clock(q["ends_at"] - 60)))
        given = [reward_words(r, roles) for r in q.get("rewards") or []]
        parts.append(text("quests.rewards", rewards=", ".join(given) or text("quests.no_reward")))
        words.addWidget(label("  ·  ".join(parts), "sub"))
        if q.get("details"):
            words.addWidget(label(q["details"], "muted"))
        members = q.get("members") or []
        done = [m["name"] or m["discord_id"] for m in members if m.get("finished_at")]
        going = [
            f"{m['name'] or m['discord_id']} {m['progress']}/{q['count']}"
            for m in members
            if not m.get("finished_at")
        ]
        line = text("quests.joined", n=q.get("joined", 0))
        if done:
            line += "  ·  " + text("quests.done_by", names=", ".join(done))
        if going and mode["technical"]:
            line += "  ·  " + ", ".join(going)
        if q.get("saved_at"):
            line += "  ·  " + text("quests.saved", time=clock(q["saved_at"]))
        words.addWidget(label(line, "muted"))
        h.addLayout(words, 1)
        end = push(text("quests.end"))
        end.setAccessibleName(f"{text('quests.end')}: {q['title']}")
        end.clicked.connect(lambda _=False: self.end(q))
        h.addWidget(end, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def claim_row(self, c: dict) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        v = QVBoxLayout(row)
        v.setContentsMargins(12, 8, 8, 8)
        v.addWidget(label(text("quests.claim", name=c["discord_name"], title=c["title"]), "step"))
        if c.get("note"):
            v.addWidget(label(c["note"], "sub"))
        picture = label()
        picture.hide()
        v.addWidget(picture)
        buttons = QHBoxLayout()
        for key, answer, kind in (
            ("quests.approve", "approve", "primary"),
            ("quests.refuse", "refuse", ""),
        ):
            button = push(text(key), kind)
            button.setAccessibleName(f"{text(key)}: {c['discord_name']}, {c['title']}")
            button.clicked.connect(lambda _=False, a=answer: self.answer(c, a))
            buttons.addWidget(button)
        if c.get("screenshot"):
            see = push(text("quests.see_screenshot"), "plain")
            see.setAccessibleName(f"{text('quests.see_screenshot')}: {c['discord_name']}")
            see.clicked.connect(lambda: self.show_screenshot(c, picture, see))
            buttons.addWidget(see)
        buttons.addStretch()
        v.addLayout(buttons)
        return row

    def idea_row(self, s: dict) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        words.addWidget(label(s["title"], "step"))
        words.addWidget(
            label(text("quests.idea_from", name=s["discord_name"], details=s["details"]), "sub")
        )
        h.addLayout(words, 1)
        use = push(text("quests.make_it"))
        use.setAccessibleName(f"{text('quests.make_it')}: {s['title']}")
        use.clicked.connect(lambda: self.use_idea(s))
        no = push(text("quests.turn_down"))
        no.setAccessibleName(f"{text('quests.turn_down')}: {s['title']}")
        no.clicked.connect(
            lambda: self.ask("DELETE", f"/quests/suggestions/{s['id']}", self.show_quests)
        )
        h.addWidget(use, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(no, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def reward_row(self, r: dict, roles: dict[str, str]) -> QWidget:
        words = f"{r['title']}: {reward_words(json.loads(r['value']), roles)}"
        state = r["state"]
        words += f"  ·  {text(f'quests.reward_state.{state}')}"
        if r.get("answer"):
            words += f"  ·  {r['answer']}"
        return label(words, "sub")

    # ------------------------------------------------------------ the new quest form
    def new_quest_form(self) -> QFrame:
        frame, layout = card(text("quests.new"))
        grid = QGridLayout()
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.details = QLineEdit()
        self.details.setMaxLength(300)
        self.kind = QComboBox()
        self.goal = QLineEdit()
        self.goal.setMaxLength(100)
        self.count = QSpinBox()
        self.count.setRange(1, 100_000)
        self.repeat = QComboBox()
        for key in REPEATS:
            self.repeat.addItem(text(f"quests.repeat_pick.{key}"), key)
        self.ends_on = QCheckBox(text("quests.last_day"))
        self.ends = QDateEdit(QDate.currentDate().addDays(7))
        self.ends.setCalendarPopup(True)
        self.ends.setMinimumDate(QDate.currentDate())
        self.ends.setEnabled(False)
        self.ends_on.toggled.connect(self.ends.setEnabled)
        self.goal_hint = label(kind="muted")
        fields: list[tuple[str, QWidget]] = [
            ("quests.title", self.title),
            ("quests.details", self.details),
            ("quests.kind", self.kind),
            ("quests.goal", self.goal),
            ("quests.count", self.count),
            ("quests.repeat", self.repeat),
        ]
        for i, (key, widget) in enumerate(fields):
            widget.setAccessibleName(text(key))
            grid.addWidget(label(text(key), "sub"), (i // 2) * 2, i % 2)
            grid.addWidget(widget, (i // 2) * 2 + 1, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addWidget(self.goal_hint)
        when = QHBoxLayout()
        when.addWidget(self.ends_on)
        self.ends.setAccessibleName(text("quests.last_day"))
        when.addWidget(self.ends)
        when.addStretch()
        layout.addLayout(when)

        layout.addWidget(label(text("quests.rewards_title"), "step"))
        layout.addWidget(label(text("quests.rewards_hint"), "muted"))
        self.rewards = RewardFields()
        layout.addLayout(self.rewards.grid)
        self.problem = label(kind="problem")
        self.problem.hide()
        layout.addWidget(self.problem)
        self.make_button = push(text("quests.create"), "primary")
        self.make_button.clicked.connect(self.make)
        row = QHBoxLayout()
        row.addWidget(self.make_button)
        row.addStretch()
        layout.addLayout(row)
        self.kind.currentIndexChanged.connect(self.kind_changed)
        return frame

    def fill_form_choices(self, roles: dict[str, str]) -> None:
        kind = self.kind.currentData()
        self.kind.blockSignals(True)
        self.kind.clear()
        for key in self.data.get("kinds") or []:
            self.kind.addItem(text(f"quests.kind.{key}"), key)
        self.kind.setCurrentIndex(max(self.kind.findData(kind), 0))
        self.kind.blockSignals(False)
        self.rewards.fill_roles(roles, bool(self.data.get("bot")))
        self.kind_changed()

    def kind_changed(self, _index: int = 0) -> None:
        kind = self.kind.currentData() or "playtime"
        self.goal.setEnabled(kind in NEEDS_GOAL)
        self.count.setEnabled(kind not in ("advancement", "approval"))
        hint = text(f"quests.goal_hint.{kind}")
        if len(self.data.get("kinds") or KINDS) < len(KINDS):
            hint += " " + text("quests.limited_kinds")
        self.goal_hint.setText(hint)

    def use_idea(self, s: dict) -> None:
        self.suggestion = s["id"]
        self.title.setText(s["title"])
        self.details.setText(s["details"])
        self.title.setFocus()
        self.ensureWidgetVisible(self.form)

    def make(self) -> None:
        body: dict[str, Any] = {
            "title": self.title.text(),
            "details": self.details.text(),
            "kind": self.kind.currentData(),
            "goal": self.goal.text() if self.goal.isEnabled() else "",
            "count": self.count.value(),
            "repeat": self.repeat.currentData(),
            "ends": self.ends.date().toString("yyyy-MM-dd") if self.ends_on.isChecked() else None,
            "rewards": self.rewards.values(),
            "suggestion_id": self.suggestion,
        }

        def done(data: dict) -> None:
            self.problem.hide()
            self.suggestion = None
            for box in (self.title, self.details, self.goal):
                box.clear()
            self.rewards.clear()
            self.ends_on.setChecked(False)
            self.say(text("quests.made"))
            self.show_quests(data)

        def failed(exc: Exception) -> None:
            self.problem.setText(str(exc))
            self.problem.show()

        self.ask("POST", "/quests", done, failed, body=body)

    # ------------------------------------------------------------ buttons
    def end(self, q: dict) -> None:
        if self.window_.confirm(
            text("quests.end_title", title=q["title"]), text("quests.end_body"), text("quests.end")
        ):
            self.ask("DELETE", f"/quests/{q['id']}", self.show_quests)

    def answer(self, c: dict, answer: str) -> None:
        self.ask("POST", f"/quests/claims/{c['id']}", self.show_quests, body={"answer": answer})

    def show_screenshot(self, c: dict, picture, see) -> None:
        def shown(found: dict) -> None:
            image = QPixmap()
            image.loadFromData(base64.b64decode(found["image"]))
            if image.isNull():
                picture.setText(text("quests.screenshot_unreadable"))
            else:
                picture.setPixmap(
                    image.scaledToWidth(
                        min(SHOT_WIDTH, image.width()), Qt.TransformationMode.SmoothTransformation
                    )
                )
            picture.show()
            see.hide()

        self.ask("GET", f"/quests/claims/{c['id']}/screenshot", shown)

    def saved(self) -> None:
        self.say(text("discord.levels_saved"))
        self.load()

    def save_levels(self, guild: dict):
        def save(body: dict) -> None:
            agent = self.window_.agent
            self.window_.run(
                lambda: agent.call("PUT", f"/discord/guilds/{guild['id']}/levels", body),
                lambda _r: self.saved(),
                self.window_.page_problem,
            )

        return save
