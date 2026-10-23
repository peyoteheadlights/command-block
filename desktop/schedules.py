"""The Schedules page: timed restarts, backups and the rest, with add, edit,
turn on or off, Run now and delete. Coming up in the right panel shows the
next ones and follows every change made here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QFrame, QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout

from .overview import card, title_of
from .ui import Page, clock, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

KINDS = {
    "daily": ("schedules.kind_daily", "schedules.every_daily"),
    "weekly": ("schedules.kind_weekly", "schedules.every_weekly"),
    "interval": ("schedules.kind_interval", "schedules.every_interval"),
}


def when(s: dict) -> str:
    kind = KINDS.get(s.get("kind", ""))
    return text(kind[1], expr=s["expr"]) if kind else f"{s.get('kind')} {s.get('expr')}"


class SchedulesPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.tasks: list[str] = []
        self.editing: dict | None = None  # the schedule the form changes, or None for a new one
        listed, layout = card(text("schedules.list"))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.column.addWidget(listed)

        self.form, layout = card(text("schedules.add"))
        grid = QGridLayout()
        self.name = QLineEdit()
        self.name.setObjectName("scheduleName")
        self.name.setPlaceholderText(text("schedules.name_placeholder"))
        self.task = QComboBox()
        self.task.setObjectName("scheduleTask")
        self.kind = QComboBox()
        self.kind.setObjectName("scheduleKind")
        for key, (words, _every) in KINDS.items():
            self.kind.addItem(text(words), key)
        self.expr = QLineEdit()
        self.expr.setObjectName("scheduleWhen")
        self.expr.setPlaceholderText(text("schedules.when_placeholder"))
        for col, (words, widget) in enumerate(
            (
                ("schedules.name", self.name),
                ("schedules.task", self.task),
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

    def may_manage(self) -> bool:
        return self.window_.may(self.row["id"], "schedules.manage")

    def load(self) -> None:
        self.form.setVisible(self.may_manage())
        self.ask("GET", "/schedules", self.show_schedules)

    def show_schedules(self, data: dict) -> None:
        self.window_.server_screen.panel.show_schedules(data.get("schedules") or [])
        self.tasks = data.get("tasks") or []
        chosen = self.task.currentData()
        self.task.clear()
        for task in self.tasks:
            self.task.addItem(self.task_words(task), task)
        if chosen in self.tasks:
            self.task.setCurrentIndex(self.tasks.index(chosen))
        shown = [self.schedule_row(s) for s in data.get("schedules") or []]
        refill(
            self.rows,
            shown
            or [label(text("schedules.none"), "step"), label(text("schedules.none_hint"), "sub")],
        )

    def task_words(self, task: str) -> str:
        return text(f"task.{task}") if task in self.tasks and not mode["technical"] else task

    def schedule_row(self, s: dict) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        words.addWidget(label(s["name"], "step"))
        next_run = clock(s.get("next_run")) if s.get("enabled") else text("schedules.off")
        parts = [self.task_words(s["task"]), when(s), f"{text('schedules.col_next')}: {next_run}"]
        if s.get("last_result"):
            parts.append(f"{text('schedules.col_result')}: {s['last_result']}")
        words.addWidget(label("  ·  ".join(parts), "sub"))
        h.addLayout(words, 1)
        if self.may_manage():
            for key, do in (
                ("desktop.edit", lambda: self.edit(s)),
                (
                    "schedules.turn_off" if s.get("enabled") else "schedules.turn_on",
                    lambda: self.turn(s),
                ),
                ("schedules.run_now", lambda: self.run_now(s)),
                ("schedules.delete", lambda: self.delete(s)),
            ):
                button = push(text(key))
                button.setAccessibleName(f"{text(key)}: {s['name']}")
                button.clicked.connect(lambda _=False, d=do: d())
                h.addWidget(button)
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
        if s is not None:
            if s["task"] in self.tasks:
                self.task.setCurrentIndex(self.tasks.index(s["task"]))
            self.kind.setCurrentIndex(
                max(0, list(KINDS).index(s["kind"]) if s["kind"] in KINDS else 0)
            )
            self.name.setFocus()

    def save_schedule(self) -> None:
        task = self.task.currentData()
        body = {
            "name": self.name.text().strip() or self.task_words(task),
            "task": task,
            "kind": self.kind.currentData(),
            "expr": self.expr.text().strip(),
            "payload": (self.editing or {}).get("payload") or {},
            "enabled": True if self.editing is None else bool(self.editing.get("enabled")),
        }

        def done(_result) -> None:
            self.edit(None)
            self.load()

        def failed(exc: Exception) -> None:
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
        self.ask("PUT", f"/schedules/{s['id']}", lambda _r: self.load(), body=body)

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
