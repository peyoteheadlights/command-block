"""The Schedules page: timed restarts, backups, messages, console commands
and steps in a row, with add, edit, turn on or off, Run now and delete.
Coming up in the right panel shows the next ones and follows every change
made here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .overview import card, title_of
from .parts import IconButton, Row, Switch
from .ui import Page, clock, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

KINDS = {
    "daily": ("schedules.kind_daily", "schedules.every_daily"),
    "weekly": ("schedules.kind_weekly", "schedules.every_weekly"),
    "interval": ("schedules.kind_interval", "schedules.every_interval"),
    "once": ("schedules.kind_once", "schedules.every_once"),
    "event": ("schedules.kind_event", "schedules.every_event"),
    "cron": ("schedules.kind_cron", "schedules.every_cron"),  # Technical only
}
MAX_STEPS = 10  # as the agent allows
OPTIONS = ("only_if_running", "skip_when_empty")


def when(s: dict) -> str:
    kind = KINDS.get(s.get("kind", ""))
    return text(kind[1], expr=s["expr"]) if kind else f"{s.get('kind')} {s.get('expr')}"


def steps_of(s: dict) -> list[dict]:
    """A schedule's steps, each with its task, as the form edits them."""
    payload = s.get("payload") or {}
    if s.get("task") == "steps":
        return [dict(step) for step in payload.get("steps") or []]
    return [{**{k: v for k, v in payload.items() if k not in OPTIONS}, "task": s.get("task")}]


class StepRow(QWidget):
    """One step: what to do, and its words (the message, the command, or
    the messages around a backup)."""

    def __init__(self, page: SchedulesPage) -> None:
        super().__init__()
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        self.task = QComboBox()
        self.task.setObjectName("scheduleTask")
        self.task.setAccessibleName(text("schedules.task"))
        self.words = QLineEdit()
        self.after = QLineEdit()  # only a backup has a second message
        self.after.setPlaceholderText(text("schedules.after"))
        self.after.setAccessibleName(text("schedules.after"))
        self.remove = IconButton("remove", text("schedules.remove_step"), "danger")
        self.remove.clicked.connect(lambda: page.remove_step(self))
        line.addWidget(self.task, 1)
        line.addWidget(self.words, 2)
        line.addWidget(self.after, 2)
        line.addWidget(self.remove)
        self.task.currentIndexChanged.connect(lambda _i: self.show_fields())
        self.confirm = False  # set once the person said yes to a risky command
        self.fill(page.tasks, page)

    def fill(self, tasks: list[str], page: SchedulesPage) -> None:
        chosen = self.task.currentData()
        self.task.clear()
        for task in tasks:
            # Steps are made with "Add a step"; any console command is Technical.
            if task == "steps" or (task == "command" and not mode["technical"]):
                continue
            self.task.addItem(page.task_words(task), task)
        if chosen is not None and self.task.findData(chosen) >= 0:
            self.task.setCurrentIndex(self.task.findData(chosen))
        self.show_fields()

    def show_fields(self) -> None:
        task = self.task.currentData()
        words = {
            "message": "schedules.text_message",
            "command": "schedules.text_command",
            "backup": "schedules.before",
        }.get(task)
        self.words.setVisible(words is not None)
        self.after.setVisible(task == "backup")
        if words:
            self.words.setPlaceholderText(text(words))
            self.words.setAccessibleName(text(words))

    def set(self, step: dict) -> None:
        task = str(step.get("task") or "")
        if self.task.findData(task) < 0:  # a command saved in Technical, seen in Simple
            self.task.addItem(task, task)
        self.task.setCurrentIndex(self.task.findData(task))
        self.words.setText(step.get("text") or step.get("command") or step.get("before") or "")
        self.after.setText(step.get("after") or "")
        self.confirm = bool(step.get("confirm"))

    def value(self) -> dict:
        task = self.task.currentData()
        words = self.words.text().strip()
        if task == "message":
            return {"task": task, "text": words}
        if task == "command":
            return {"task": task, "command": words, "confirm": self.confirm}
        if task == "backup":
            return {"task": task, "before": words, "after": self.after.text().strip()}
        return {"task": task}


class SchedulesPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.tasks: list[str] = []
        self.editing: dict | None = None  # the schedule the form changes, or None for a new one
        listed, layout = card(text("schedules.list"), box=False)
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.column.addWidget(listed)

        self.form, layout = card(text("schedules.add"))
        grid = QGridLayout()
        self.name = QLineEdit()
        self.name.setObjectName("scheduleName")
        self.name.setPlaceholderText(text("schedules.name_placeholder"))
        self.kind = QComboBox()
        self.kind.setObjectName("scheduleKind")
        self.expr = QLineEdit()
        self.expr.setObjectName("scheduleWhen")
        self.expr.setPlaceholderText(text("schedules.when_placeholder"))
        for col, (words, widget) in enumerate(
            (
                ("schedules.name", self.name),
                ("schedules.repeat", self.kind),
                ("schedules.when", self.expr),
            )
        ):
            widget.setAccessibleName(text(words))
            grid.addWidget(label(text(words), "sub"), 0, col)
            grid.addWidget(widget, 1, col)
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)
        layout.addWidget(label(text("schedules.when_hint"), "sub"))
        layout.addWidget(label(text("schedules.task"), "sub"))
        self.steps = QVBoxLayout()
        layout.addLayout(self.steps)
        self.add_step = push(text("schedules.add_step"), "plain")
        self.add_step.clicked.connect(lambda: self.new_step())
        self.keep_going = QCheckBox(text("schedules.keep_going"))
        self.options = {key: QCheckBox(text(f"schedules.{key}")) for key in OPTIONS}
        for widget in (self.add_step, self.keep_going, *self.options.values()):
            layout.addWidget(widget)
        self.new_step()
        # The quietest hour players were measured at (agent/playerhours.py).
        quiet = QHBoxLayout()
        self.quiet_line = label(kind="sub")
        self.quiet_use = push(text("activity.use"), "plain")
        self.quiet_use.clicked.connect(self.use_quiet)
        quiet.addWidget(self.quiet_line)
        quiet.addWidget(self.quiet_use)
        quiet.addStretch()
        layout.addLayout(quiet)
        self.quiet_hour: int | None = None
        self.problem = label(kind="problem")
        self.problem.hide()
        layout.addWidget(self.problem)
        buttons = QHBoxLayout()
        self.save = push(text("schedules.create"), "primary")
        self.save.clicked.connect(self.save_schedule)
        self.cancel = push(text("action.cancel"))
        self.cancel.clicked.connect(lambda: self.edit(None))
        self.cancel.hide()
        buttons.addWidget(self.save)
        buttons.addWidget(self.cancel)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.column.addWidget(self.form)
        self.column.addStretch()

    @property
    def task(self) -> QComboBox:
        """The first step's task, which is the whole schedule's when it has one step."""
        return self.step_rows()[0].task

    def step_rows(self) -> list[StepRow]:
        return [self.steps.itemAt(i).widget() for i in range(self.steps.count())]  # type: ignore[misc]

    def new_step(self, step: dict | None = None) -> StepRow:
        row = StepRow(self)
        if step:
            row.set(step)
        self.steps.addWidget(row)
        self.steps_changed()
        return row

    def remove_step(self, row: StepRow) -> None:
        if len(self.step_rows()) > 1:
            self.steps.removeWidget(row)
            row.deleteLater()
            self.steps_changed()

    def steps_changed(self) -> None:
        rows = self.step_rows()
        for row in rows:
            row.remove.setVisible(len(rows) > 1)
        self.add_step.setEnabled(len(rows) < MAX_STEPS)
        self.keep_going.setVisible(len(rows) > 1)

    def fill_kinds(self) -> None:
        chosen = self.kind.currentData()
        self.kind.clear()
        for key, (words, _every) in KINDS.items():
            if key != "cron" or mode["technical"] or chosen == "cron":
                self.kind.addItem(text(words), key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(chosen)))

    def may_manage(self) -> bool:
        return self.window_.may(self.row["id"], "schedules.manage")

    def load(self) -> None:
        self.form.setVisible(self.may_manage())
        self.fill_kinds()
        self.ask("GET", "/schedules", self.show_schedules)
        self.quiet_line.hide()
        self.quiet_use.hide()
        if self.may_manage():
            self.ask("GET", "/players/hours", self.show_quiet, lambda _e: None)

    def show_quiet(self, data: dict) -> None:
        self.quiet_hour = data.get("quietest_hour")
        if self.quiet_hour is None:
            return
        self.quiet_line.setText(text("activity.quiet", hour=f"{self.quiet_hour:02d}:00"))
        self.quiet_line.show()
        self.quiet_use.show()

    def use_quiet(self) -> None:
        """Put the quietest hour in When, keeping a weekday already typed."""
        if self.quiet_hour is None:
            return
        words = self.expr.text().split()
        day = words[0] + " " if len(words) == 2 and ":" not in words[0] else ""
        self.expr.setText(f"{day}{self.quiet_hour:02d}:00")

    def show_schedules(self, data: dict) -> None:
        self.window_.server_screen.panel.show_schedules(data.get("schedules") or [])
        self.tasks = data.get("tasks") or []
        for row in self.step_rows():
            row.fill(self.tasks, self)
        shown = [self.schedule_row(s) for s in data.get("schedules") or []]
        refill(
            self.rows,
            shown
            or [label(text("schedules.none"), "step"), label(text("schedules.none_hint"), "sub")],
        )

    def task_words(self, task: str) -> str:
        return text(f"task.{task}") if task in self.tasks and not mode["technical"] else task

    def schedule_row(self, s: dict) -> QFrame:
        row = Row()
        h = row.line
        words = QVBoxLayout()
        words.addWidget(label(s["name"], "step"))
        if not s.get("enabled"):
            next_run = text("schedules.off")
        elif s.get("kind") == "event":
            next_run = text("schedules.on_event")
        else:
            next_run = clock(s.get("next_run"))
        steps = steps_of(s)
        what = (
            text("schedules.steps_count", count=len(steps))
            if s["task"] == "steps"
            else self.task_words(s["task"])
        )
        said = steps[0].get("text") or steps[0].get("command") if len(steps) == 1 else None
        parts = [f"{what}: {said}" if said else what, when(s)]
        parts.append(f"{text('schedules.col_next')}: {next_run}")
        if s.get("last_result"):
            parts.append(f"{text('schedules.col_result')}: {s['last_result']}")
        words.addWidget(label("  ·  ".join(parts), "sub"))
        h.addLayout(words, 1)
        if self.may_manage():
            for key, do in (
                ("desktop.edit", lambda: self.edit(s)),
                ("schedules.run_now", lambda: self.run_now(s)),
            ):
                button = push(text(key))
                button.setAccessibleName(f"{text(key)}: {s['name']}")
                button.clicked.connect(lambda _=False, d=do: d())
                h.addWidget(button)
            # The switch turns the schedule on or off at once.
            key = "schedules.turn_off" if s.get("enabled") else "schedules.turn_on"
            turn = Switch(f"{text(key)}: {s['name']}")
            turn.setChecked(bool(s.get("enabled")))
            turn.toggled.connect(lambda _on: self.turn(s))
            h.addWidget(turn)
            delete = IconButton("remove", text("schedules.delete"), "danger")
            delete.setAccessibleName(f"{text('schedules.delete')}: {s['name']}")
            delete.clicked.connect(lambda: self.delete(s))
            h.addWidget(delete)
        return row

    # ------------------------------------------------------------ the form
    def edit(self, s: dict | None) -> None:
        self.editing = s
        self.problem.hide()
        title_of(self.form).setText(text("schedules.add") if s is None else s["name"])
        self.save.setText(text("schedules.create") if s is None else text("action.save"))
        self.cancel.setVisible(s is not None)
        self.name.setText("" if s is None else s["name"])
        self.expr.setText("" if s is None else s["expr"])
        for row in self.step_rows():
            self.steps.removeWidget(row)
            row.deleteLater()
        for step in steps_of(s) if s is not None else [{}]:
            self.new_step(step)
        payload = (s or {}).get("payload") or {}
        self.keep_going.setChecked(bool(payload.get("keep_going")))
        for key, box in self.options.items():
            box.setChecked(bool(payload.get(key)))
        if s is not None:
            if self.kind.findData(s["kind"]) < 0:
                self.kind.addItem(s["kind"], s["kind"])
            self.kind.setCurrentIndex(self.kind.findData(s["kind"]))
            self.name.setFocus()

    def save_schedule(self) -> None:
        steps = [row.value() for row in self.step_rows()]
        options = {key: box.isChecked() for key, box in self.options.items()}
        if len(steps) == 1:
            task = steps[0].pop("task")
            payload = {**steps[0], **options}
        else:
            task = "steps"
            payload = {"steps": steps, "keep_going": self.keep_going.isChecked(), **options}
        body = {
            "name": self.name.text().strip() or self.task_words(task),
            "task": task,
            "kind": self.kind.currentData(),
            "expr": self.expr.text().strip(),
            "payload": payload,
            "enabled": True if self.editing is None else bool(self.editing.get("enabled")),
        }

        def done(_result) -> None:
            self.edit(None)
            self.load()

        def failed(exc: Exception) -> None:
            # A command the console asks "are you sure?" about: ask here too.
            if getattr(exc, "status", None) == 409 and self.window_.confirm(
                text("schedules.confirm_title"), str(exc), text("action.save")
            ):
                for row in self.step_rows():
                    row.confirm = True
                self.save_schedule()
                return
            self.problem.setText(str(exc))
            self.problem.show()

        if self.editing is None:
            self.ask("POST", "/schedules", done, failed, body=body)
        else:
            self.ask("PUT", f"/schedules/{self.editing['id']}", done, failed, body=body)

    # ------------------------------------------------------------ row buttons
    def turn(self, s: dict) -> None:
        body = {k: s.get(k) for k in ("name", "task", "kind", "expr")}
        body.update(payload=s.get("payload") or {}, enabled=not s.get("enabled"))

        def refused(exc: Exception) -> None:
            self.window_.page_problem(exc)
            self.load()  # the list again, so the switch shows what is true

        self.ask("PUT", f"/schedules/{s['id']}", lambda _r: self.load(), refused, body=body)

    def run_now(self, s: dict) -> None:
        def done(result: dict) -> None:
            self.say(str(result.get("result") or ""))
            self.load()

        self.ask("POST", f"/schedules/{s['id']}/run", done)

    def delete(self, s: dict) -> None:
        if self.window_.confirm(
            text("schedules.delete_title", name=s["name"]),
            text("schedules.delete_body"),
            text("schedules.delete"),
        ):
            self.ask("DELETE", f"/schedules/{s['id']}", lambda _r: self.load())
