"""The Diagnose server window: starts a run and shows its list as it grows.

The agent does the work (agent/diagnose.py) and keeps the list; this window
asks for it once a second while the run lasts, then stops asking.

When the run finds changes that may fix what failed, they are listed Safe
and Risky with Apply all, Apply safe and Deny all (agent/diagnosefix.py).
Applying runs the joins again; the list then also says what each change did.
What only the owner can do (a router port) is listed under "Do it yourself".

At the top: the owner's Microsoft account, which online-mode and Bedrock
joins use, with Sign in with Microsoft (a code to type on Microsoft's page,
agent/microsoft.py) or Sign out. On a mod server the window first asks
whether to run the join with all mods too (agent/fullmods.py); when yes,
HeadlessMC's own sign-in shows under it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from .agent import AgentError
from .ui import label, push
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

MARKS = {True: "✓", False: "✗", None: "?"}


class DiagnoseWindow(QDialog):
    def __init__(self, window: MainWindow, server_id: str, name: str) -> None:
        super().__init__(window)
        self.window_ = window
        self.server_id = server_id
        self.setWindowTitle(text("diagnose.title", name=name))
        self.setMinimumWidth(520)
        column = QVBoxLayout(self)
        self.microsoft = AccountRow(self, "/microsoft", "microsoft", self.signed_in)
        column.addLayout(self.microsoft.row)
        # HeadlessMC's own sign-in, shown once the join with all mods is on.
        self.headlessmc = AccountRow(self, "/headlessmc", "headlessmc", self.signed_in)
        column.addLayout(self.headlessmc.row)
        self.go_ahead = False
        self.mods: bool | None = None
        self.status = label(text("diagnose.starting"), "sub")
        column.addWidget(self.status)
        self.list = QVBoxLayout()
        column.addLayout(self.list)
        column.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.picks = {}
        for pick in ("all", "safe", "none"):
            button = push(text(f"diagnose.apply_{pick}"), "" if pick == "all" else "plain")
            button.clicked.connect(lambda _=False, pick=pick: self.apply(pick))
            button.hide()
            row.addWidget(button)
            self.picks[pick] = button
        self.again = push(text("diagnose.again"))
        self.again.clicked.connect(self.start)
        self.again.hide()
        row.addWidget(self.again)
        close = push(text("action.close"), "plain")
        close.clicked.connect(self.close)
        row.addWidget(close)
        column.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.ask)
        self.microsoft.ask()
        self.start()

    def start(self) -> None:
        self.again.hide()
        self.status.setText(text("diagnose.starting"))
        agent, sid = self.window_.agent, self.server_id
        form = {"go_ahead": self.go_ahead, "mods": self.mods}
        self.window_.run(
            lambda: agent.call("POST", f"/servers/{sid}/server/diagnose", form),
            self.started,
            self.failed,
        )

    def apply(self, pick: str) -> None:
        for button in self.picks.values():
            button.hide()
        self.again.hide()
        self.status.setText(text("diagnose.running"))
        agent, sid = self.window_.agent, self.server_id
        self.window_.run(
            lambda: agent.call("POST", f"/servers/{sid}/server/diagnose/changes", {"pick": pick}),
            lambda _answer: self.ask() if pick == "none" else self.timer.start(),
            self.failed,
        )

    def started(self, answer: dict) -> None:
        if answer.get("ask_mods"):
            # A mod server: the join with all mods is long, so it asks first.
            self.mods = self.window_.confirm(
                text("action.diagnose"), answer["ask_mods"], text("diagnose.mods_yes")
            )
            if self.mods:
                self.headlessmc.ask()
            self.start()
            return
        if not answer.get("ask"):
            self.timer.start()
            return
        # The owner is playing, and a join with their account kicks them out.
        if self.window_.confirm(text("action.diagnose"), answer["ask"], text("diagnose.go_ahead")):
            self.go_ahead = True
            self.start()
        else:
            self.status.setText(text("diagnose.not_started"))
            self.again.show()

    def signed_in(self) -> None:
        """A sign-in just finished: run the joins that waited for it."""
        if not self.timer.isActive():
            self.start()

    def ask(self) -> None:
        agent, sid = self.window_.agent, self.server_id
        self.window_.run(
            lambda: agent.call("GET", f"/servers/{sid}/server/diagnose"), self.show_result
        )

    def failed(self, exc: AgentError) -> None:
        self.timer.stop()
        self.status.setText(str(exc))
        self.again.show()

    def show_result(self, result: dict | None) -> None:
        if not result:
            return
        if result.get("mods") and not self.headlessmc.account.isVisible():
            self.headlessmc.ask()
        while self.list.count():
            old = self.list.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        group = None
        for line in result.get("lines") or []:
            if line["group"] != group:
                group = line["group"]
                self.add(label(group, name="h2"))
            words = f"{MARKS[line['ok']]}  {line['what']}"
            if line.get("detail"):
                words += f"\n     {line['detail']}"
            kind = "problem" if line["ok"] is False else ("muted" if line["ok"] is None else "")
            self.add(label(words, kind))
        if result.get("done"):
            self.add(label(text("diagnose.made"), name="h2"))
        for done in result.get("done") or []:
            mark = "✓" if done["outcome"] == "kept" else "–"
            self.add(label(f"{mark}  {done['title']}\n     {done['detail']}", "muted"))
        if result.get("steps"):
            self.add(label(text("diagnose.yourself"), name="h2"))
        for step in result.get("steps") or []:
            self.add(label(f"•  {step}"))
        changes = [] if result.get("running") else result.get("changes") or []
        for safe in (True, False):
            group = [c for c in changes if c["safe"] is safe]
            if group:
                self.add(label(text("diagnose.safe" if safe else "diagnose.risky"), name="h2"))
            for change in group:
                self.add(label(f"•  {change['title']}\n     {change['why']}"))
        if result.get("running"):
            self.status.setText(text("diagnose.running"))
            return
        self.timer.stop()
        self.status.setText(text("diagnose.offer" if changes else "diagnose.done"))
        for button in self.picks.values():
            button.setVisible(bool(changes))
        self.picks["safe"].setEnabled(any(c["safe"] for c in changes))
        self.again.setVisible(not changes)

    def add(self, widget: QLabel) -> None:
        self.list.addWidget(widget)
        widget.show()  # added after the window opened: Qt keeps it hidden otherwise

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.microsoft.signing.stop()
        self.headlessmc.signing.stop()
        super().closeEvent(event)


class AccountRow:
    """One sign-in at the top of the window: the owner's Microsoft account
    (``/microsoft``, the test player's joins) or HeadlessMC's
    (``/headlessmc``, the join with all mods). Sign in shows a code to type
    on Microsoft's page; the row asks every 2 seconds until it's done."""

    def __init__(self, dialog: DiagnoseWindow, path: str, words: str, done) -> None:
        self.dialog, self.path, self.words, self.done = dialog, path, words, done
        self.row = QHBoxLayout()
        self.account = label("", "sub")
        self.account.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.row.addWidget(self.account, 1)
        self.open_page = push(text("microsoft.open_page"), "plain")
        self.open_page.clicked.connect(self.open)
        self.copy_code = push(text("microsoft.copy_code"), "plain")
        self.copy_code.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.code or ""))
        self.sign_in = push(text(f"{words}.sign_in"))
        self.sign_in.clicked.connect(self.start_sign_in)
        self.sign_out = push(text("microsoft.sign_out"), "plain")
        self.sign_out.clicked.connect(self.do_sign_out)
        self.account.hide()
        for button in (self.open_page, self.copy_code, self.sign_in, self.sign_out):
            button.hide()
            self.row.addWidget(button)
        self.code: str | None = None
        self.url = ""
        self.signing = QTimer(dialog)
        self.signing.setInterval(2000)
        self.signing.timeout.connect(self.ask)

    def ask(self) -> None:
        agent = self.dialog.window_.agent
        self.dialog.window_.run(lambda: agent.call("GET", self.path), self.show, self.hide)

    def hide(self, _exc: AgentError) -> None:
        self.signing.stop()
        for widget in (self.account, self.open_page, self.copy_code, self.sign_in, self.sign_out):
            widget.hide()

    def show(self, status: dict) -> None:
        self.code, self.url = status.get("code"), status.get("url") or ""
        waiting = bool(self.url) and not status.get("signed_in")
        if status.get("signed_in"):
            names = [status.get(n) for n in ("java_name", "gamertag", "name") if status.get(n)]
            words = text(f"{self.words}.signed_in", names=", ".join(names) or "?")
        elif waiting:
            words = text(f"{self.words}.type_code", url=self.url, code=self.code or "")
        else:
            words = status.get("error") or text(f"{self.words}.signed_out")
        self.account.setText(words)
        self.account.show()
        self.sign_out.setVisible(bool(status.get("signed_in")))
        self.sign_in.setVisible(not status.get("signed_in") and not waiting)
        self.open_page.setVisible(waiting)
        self.copy_code.setVisible(waiting and bool(self.code))
        if waiting:
            self.signing.start()
        else:
            if self.signing.isActive() and status.get("signed_in"):
                self.done()
            self.signing.stop()

    def start_sign_in(self) -> None:
        self.sign_in.setEnabled(False)
        self.account.setText(text(f"{self.words}.asking"))
        agent = self.dialog.window_.agent
        self.dialog.window_.run(
            lambda: agent.call("POST", f"{self.path}/sign-in", {}),
            self.show,
            lambda exc: self.account.setText(str(exc)),
            lambda: self.sign_in.setEnabled(True),
        )

    def open(self) -> None:
        if self.url.startswith("https://"):
            QDesktopServices.openUrl(QUrl(self.url))

    def do_sign_out(self) -> None:
        agent = self.dialog.window_.agent
        self.dialog.window_.run(lambda: agent.call("POST", f"{self.path}/sign-out", {}), self.show)
