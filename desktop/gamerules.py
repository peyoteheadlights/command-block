"""Settings > Game settings > Game rules: every rule the running server
lists, with its value as the server says it, and a few presets.

Rules change at once (no restart). Reading them sends the server one
question per rule, so the card asks only when the Game settings section is
opened, not each time the Settings page loads.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from .overview import card
from .parts import Switch
from .ui import label, push, refill
from .words import text

if TYPE_CHECKING:
    from .settings import SettingsPage


class RulesCard(QWidget):
    def __init__(self, page: SettingsPage) -> None:
        super().__init__()
        self.page = page
        self.frame, layout = card(text("gamerules.title"), words=text("gamerules.hint"))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        self.said = label(kind="sub")
        layout.addWidget(self.said)
        tools = QHBoxLayout()
        self.presets = QComboBox()
        self.presets.setAccessibleName(text("gamerules.presets"))
        self.use = push(text("gamerules.use"), "plain")
        self.use.clicked.connect(lambda: self.put({"preset": self.presets.currentData()}))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(text("gamerules.filter"))
        self.filter.setAccessibleName(text("gamerules.filter"))
        self.filter.textChanged.connect(self.show_matching)
        tools.addWidget(label(text("gamerules.presets"), "sub"))
        tools.addWidget(self.presets, 1)
        tools.addWidget(self.use)
        tools.addWidget(self.filter, 1)
        layout.addLayout(tools)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        layout.addLayout(self.rows)
        self.save = push(text("gamerules.save"), "tonal")
        self.save.clicked.connect(self.save_changed)
        layout.addWidget(self.save)
        # name -> (row, read the control, the value the server said)
        self.controls: dict[str, tuple[QWidget, Callable[[], bool | int], bool | int]] = {}

    def load(self) -> None:
        self.said.setText(text("gamerules.reading"))
        self.page.ask("GET", "/gamerules", self.fill, lambda exc: self.said.setText(str(exc)))

    def fill(self, data: dict) -> None:
        rules = data.get("rules") or []
        may = self.page.may("settings.game")
        if not data.get("running"):
            self.said.setText(text("gamerules.stopped"))
        elif not rules:
            self.said.setText(text("gamerules.none"))
        else:
            self.said.setText("")
        self.presets.clear()
        for preset in data.get("presets") or []:
            self.presets.addItem(text(f"presets.{preset}"), preset)
        shown = bool(rules)
        for widget in (self.presets, self.use, self.filter, self.save):
            widget.setVisible(shown)
            widget.setEnabled(may)
        self.controls = {}
        rows = []
        for rule in rules:
            value = rule["value"]
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label(rule["name"]), 1)
            control: QWidget
            read: Callable[[], bool | int] | None
            if isinstance(value, bool):
                switch = control = Switch(rule["name"])
                switch.setChecked(value)
                read = switch.isChecked
            elif isinstance(value, int):
                number = control = QSpinBox()
                number.setRange(-(2**31), 2**31 - 1)
                number.setValue(value)
                number.setAccessibleName(rule["name"])
                read = number.value
            else:  # a value we can't edit safely: shown as the server said it
                control = label(str(value), "sub")
                read = None
            control.setEnabled(may)
            row.addWidget(control)
            rows.append(line)
            if read is not None:
                self.controls[rule["name"]] = (line, read, value)
        refill(self.rows, rows)
        self.show_matching()

    def show_matching(self) -> None:
        needle = self.filter.text().lower()
        for name, (line, _read, _value) in self.controls.items():
            line.setVisible(needle in name.lower())

    def save_changed(self) -> None:
        changed = {
            name: read() for name, (_line, read, value) in self.controls.items() if read() != value
        }
        if not changed:
            self.said.setText(text("settings.nothing_changed"))
            return
        self.put({"rules": changed})

    def put(self, body: dict) -> None:
        def done(result: dict) -> None:
            before = result.get("before") or {}
            self.fill({"running": True, "rules": result.get("rules"), "presets": self.preset_ids()})
            words = (
                text("gamerules.saved", n=len(before)) if before else text("gamerules.no_change")
            )
            if result.get("skipped"):
                words += " " + text("gamerules.skipped", names=", ".join(result["skipped"]))
            self.said.setText(words)
            self.page.say(words, (lambda: self.put({"rules": before})) if before else None)

        self.page.ask("PUT", "/gamerules", done, lambda exc: self.said.setText(str(exc)), body=body)

    def preset_ids(self) -> list[str]:
        return [self.presets.itemData(i) for i in range(self.presets.count())]
