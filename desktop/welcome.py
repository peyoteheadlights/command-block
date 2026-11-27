"""The two first-run questions, shown to the owner once after signing in.

1. "Is this your first time making a Minecraft server?" Yes: Simple mode
   and tips. No: Technical mode, then which kinds they've made before.
2. "Who's this server for?": the default for new servers (whitelist on for
   a few friends or kids, off for a community).

"I know what I'm doing" sits in the corner from the first question on and
skips both: Technical mode, no tips, nothing switched on. The agent saves
the answers (PUT /account/welcome) and picks the mode and tips from them;
PC settings can change them after.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .ui import label, push
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

# Kinds made before, as the agent names them, and their words.
KINDS = (
    ("vanilla", "welcome.kind_vanilla"),
    ("mods", "welcome.kind_mods"),
    ("plugins", "welcome.kind_plugins"),
    ("bedrock", "welcome.kind_bedrock"),
)
PLAYED_BY = (
    ("friends", "welcome.for_friends"),
    ("kids", "welcome.for_kids"),
    ("community", "welcome.for_community"),
)


def kind_boxes(ticked: list[str]) -> dict[str, QCheckBox]:
    """The four tick boxes in plain words (the caller adds the exact names
    in small text under each)."""
    boxes = {}
    for kind, words in KINDS:
        box = QCheckBox(text(words))
        box.setChecked(kind in ticked)
        boxes[kind] = box
    return boxes


class WelcomeDialog(QDialog):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle(text("welcome.title"))
        self.setMinimumWidth(520)
        self.first_time: bool | None = None
        column = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addStretch()
        skip = push(text("welcome.skip"), "plain")
        skip.setObjectName("welcomeSkip")
        skip.clicked.connect(lambda: self.send(None, [], ""))
        top.addWidget(skip)
        column.addLayout(top)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.first_page())
        self.pages.addWidget(self.kinds_page())
        self.pages.addWidget(self.who_page())
        column.addWidget(self.pages)
        self.said = label("", "sub")
        column.addWidget(self.said)

    def first_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(text("welcome.first_question"), name="h2"))
        row = QHBoxLayout()
        for answer, words in ((True, "welcome.yes"), (False, "welcome.no")):
            button = push(text(words), "primary" if answer else "")
            button.setObjectName("welcomeYes" if answer else "welcomeNo")
            button.clicked.connect(lambda _=False, a=answer: self.answered_first(a))
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        # Said on first run (Mark, 2026-10-16): friends play only while this PC is on.
        layout.addWidget(label(text("welcome.pc_on"), "sub"))
        return page

    def kinds_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(text("welcome.kinds_question"), name="h2"))
        self.kinds = kind_boxes([])
        for kind, box in self.kinds.items():
            layout.addWidget(box)
            layout.addWidget(label(text(f"welcome.kind_{kind}_names"), "sub"))
        next_ = push(text("welcome.next"), "primary")
        next_.setObjectName("welcomeKindsNext")
        next_.clicked.connect(lambda: self.pages.setCurrentIndex(2))
        layout.addWidget(next_)
        return page

    def who_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(text("welcome.who_question"), name="h2"))
        self.who = QButtonGroup(page)
        for i, (_value, words) in enumerate(PLAYED_BY):
            choice = QRadioButton(text(words))
            self.who.addButton(choice, i)
            layout.addWidget(choice)
            layout.addWidget(label(text(words + "_hint"), "sub"))
        done = push(text("welcome.done"), "primary")
        done.setObjectName("welcomeDone")
        done.clicked.connect(self.finish)
        layout.addWidget(done)
        return page

    def answered_first(self, first_time: bool) -> None:
        self.first_time = first_time
        self.pages.setCurrentIndex(2 if first_time else 1)

    def finish(self) -> None:
        picked = self.who.checkedId()
        if picked < 0:
            self.said.setText(text("welcome.pick_one"))
            return
        kinds = [] if self.first_time else [k for k, box in self.kinds.items() if box.isChecked()]
        self.send(self.first_time, kinds, PLAYED_BY[picked][0])

    def send(self, first_time: bool | None, kinds: list[str], played_by: str) -> None:
        window = self.window_
        body = {"first_time": first_time, "kinds": kinds, "played_by": played_by}

        def saved(_answer: dict) -> None:
            # The agent picked the mode from the answers: show it.
            window.mode_picked = False
            window.run(window.agent.preferences, window.mode_loaded, lambda _e: None)
            self.accept()

        window.run(
            lambda: window.agent.call("PUT", "/account/welcome", body),
            saved,
            lambda exc: self.said.setText(str(exc)),
        )
