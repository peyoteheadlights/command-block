"""The Deep scan card on the Mods page: pick the checks and what to look at,
run it, and see what it listed. Worth a look files get a Quarantine button;
Known bad ones go to quarantine on the agent (agent/mods/deepscan.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .overview import card, title_of
from .ui import clock, label, push, refill
from .words import text

if TYPE_CHECKING:
    from .mods import ModsPage

RUN = "deepscan.run"
POLL_MS = 2000


def said(words: dict) -> str:
    """One check line, led by the name of the check that said it."""
    line = text(words["words"], **words.get("values") or {})
    return f"{words['scanner']}: {line}" if words.get("scanner") else line


class DeepScanCard:
    def __init__(self, page: ModsPage) -> None:
        self.page = page
        self.frame, layout = card("")
        self.intro = label(kind="sub")
        self.scanners = QVBoxLayout()
        self.boxes: dict[str, QCheckBox] = {}
        self.picked = {"behaviour"}
        self.scope_title = label()
        self.scopes = QButtonGroup(self.frame)
        self.scope: dict[str, QRadioButton] = {}
        for key in ("jar", "mods", "folder"):
            button = QRadioButton()
            self.scopes.addButton(button)
            self.scope[key] = button
        self.scope["mods"].setChecked(True)
        self.jar = QComboBox()
        self.folder_note = label(kind="sub")
        self.run_button = push("")
        self.run_button.clicked.connect(self.run)
        self.status = label(kind="sub")
        self.rows = QVBoxLayout()
        layout.addWidget(self.intro)
        layout.addLayout(self.scanners)
        layout.addWidget(self.scope_title)
        jar_row = QHBoxLayout()
        jar_row.addWidget(self.scope["jar"])
        jar_row.addWidget(self.jar, 1)
        layout.addLayout(jar_row)
        for widget in (self.scope["mods"], self.scope["folder"], self.folder_note):
            layout.addWidget(widget)
        layout.addWidget(self.run_button)
        layout.addWidget(self.status)
        layout.addLayout(self.rows)
        self.timer = QTimer(self.frame)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.ask)

    def load(self) -> None:
        self.timer.stop()
        title_of(self.frame).setText(text("deepscan.title"))
        self.intro.setText(text("deepscan.intro"))
        self.scope_title.setText(text("deepscan.scope"))
        has_content = bool(self.page.row.get("content"))
        word = self.page.row.get("content") or "mods"
        self.scope["jar"].setText(text("deepscan.scope.jar"))
        self.scope["mods"].setText(text("deepscan.scope.mods", word=word))
        self.scope["folder"].setText(text("deepscan.scope.folder"))
        self.jar.setAccessibleName(text("deepscan.pick_jar"))
        if not has_content:
            self.scope["folder"].setChecked(True)
        self.folder_note.setText(text("deepscan.folder_note"))
        self.run_button.setText(text("deepscan.run"))
        allowed = self.page.may(RUN)
        for widget in (self.scope["jar"], self.scope["mods"], self.jar):
            widget.setVisible(allowed and has_content)
        for shown in (self.scope["folder"], self.scope_title, self.folder_note, self.run_button):
            shown.setVisible(allowed)
        self.status.setText("")
        refill(self.rows, [])
        self.ask()

    def jars(self, filenames: list[str]) -> None:
        """The jars to pick from for a one-jar scan."""
        keep = self.jar.currentText()
        self.jar.clear()
        self.jar.addItems(filenames)
        if keep in filenames:
            self.jar.setCurrentText(keep)

    def ask(self) -> None:
        self.page.ask("GET", "/mods/deepscan", self.show, lambda _e: self.timer.stop())

    # ------------------------------------------------------------ showing
    def show(self, report: dict) -> None:
        allowed = self.page.may(RUN)
        boxes = []
        self.boxes = {}
        for scanner in report.get("scanners") or []:
            box = QCheckBox(f"{text(scanner['words'])} ({text(scanner['speed'])})")
            box.setChecked(scanner["id"] in self.picked)
            box.setEnabled(allowed)
            box.toggled.connect(lambda on, key=scanner["id"]: self.tick(key, on))
            self.boxes[scanner["id"]] = box
            took = scanner.get("took")
            about = [
                text(scanner["what"]),
                text("deepscan.took", **took) if took else text("deepscan.not_timed"),
            ]
            got = scanner.get("downloaded")
            if got:
                about.append(text("deepscan.downloaded", tag=got["tag"], when=clock(got["at"])))
            boxes += [box, label("\n".join(about), "sub")]
        refill(self.scanners, boxes)
        running = report.get("running")
        self.run_button.setEnabled(allowed and not running)
        if running:
            self.status.setText(
                text(
                    "deepscan.running",
                    done=int(running.get("done") or 0),
                    total=int(running.get("total") or 0),
                )
            )
            if not self.timer.isActive():
                self.timer.start()
            return
        self.timer.stop()
        self.show_last(report.get("last"), allowed)

    def show_last(self, last: dict | None, allowed: bool) -> None:
        if not last:
            self.status.setText(text("deepscan.never_run"))
            refill(self.rows, [])
            return
        found = last.get("found") or []
        lines = [
            text(
                "deepscan.result",
                when=clock(last["finished"]),
                files=last["files"],
                count=len(found),
            )
        ]
        if last.get("skipped"):
            lines.append(text("deepscan.result_skipped", count=last["skipped"]))
        lines += [said(e) for e in last.get("errors") or []]
        self.status.setText("\n".join(lines))
        rows: list[QWidget] = []
        for item in found:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            where = item["path"]
            if item["root"] == "java":
                where = text("deepscan.in_java", where=where)
            kind = text(f"deepscan.kind.{item['kind']}")
            words = [f"{where}: {kind}"]
            words += [said(f) for f in item.get("findings") or []]
            words.append(text(f"deepscan.state.{item['state']}"))
            h.addWidget(label("\n".join(words), "sub"), 1)
            if allowed and item["kind"] == "worth_a_look" and item["state"] == "found":
                move = push(text("deepscan.quarantine"), "plain")
                move.setAccessibleName(f"{move.text()}: {item['path']}")
                move.clicked.connect(lambda _=False, i=item: self.quarantine(i))
                h.addWidget(move)
            rows.append(row)
        refill(self.rows, rows)

    # ------------------------------------------------------------ actions
    def tick(self, key: str, on: bool) -> None:
        if on:
            self.picked.add(key)
        else:
            self.picked.discard(key)

    def run(self) -> None:
        scope = next(key for key, button in self.scope.items() if button.isChecked())
        body: dict = {"scope": scope, "scanners": sorted(self.picked)}
        if scope == "jar":
            body["filename"] = self.jar.currentText()
        self.run_button.setEnabled(False)
        self.page.ask("POST", "/mods/deepscan", self.show, self.failed, body=body)

    def failed(self, exc: Exception) -> None:
        self.run_button.setEnabled(True)
        self.page.say(str(exc))

    def quarantine(self, item: dict) -> None:
        def done(report: dict) -> None:
            self.show(report)
            self.page.changed()

        body = {"root": item["root"], "path": item["path"]}
        self.page.ask("POST", "/mods/deepscan/quarantine", done, self.page.said, body=body)
