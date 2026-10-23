"""The "?" screen: Getting started (the few things to know, each with a
button to where it's done) and About (version, licences, the Mojang
disclaimer and a link to the full help online)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from agent import __version__ as APP_VERSION

from .gear import Section, buttons
from .overview import card
from .theme import SHARED
from .ui import label, push
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

REPO = "https://github.com/peyoteheadlights/command-block"
# (title, words, button, where it goes, what the account needs). Where:
# a server's page, or a gear section after "gear:".
STEPS = [
    ("guide.start", "guide.start_text", "guide.start_button", "Overview", None),
    ("guide.friends", "guide.friends_text", "guide.friends_button", "Overview", None),
    ("guide.world", "guide.world_text", "desktop.guide.world_button", "Backups", "world.replace"),
    ("guide.backups", "guide.backups_text", "guide.backups_button", "Backups", None),
    ("guide.alerts", "guide.alerts_text", "desktop.guide.alerts_button", "gear:pc", "owner"),
    ("guide.helpers", "guide.helpers_text", "guide.helpers_button", "gear:moderators", "owner"),
    ("guide.trouble", "guide.trouble_text", "desktop.guide.trouble_button", "Activity", None),
]


class Guide(Section):
    WORDS = "page.getting_started"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, ["steps"])

    def may(self, need: str | None) -> bool:
        window = self.window_
        if need == "owner":
            return self.owner()
        return need is None or any(window.may(r["id"], need) for r in window.servers)

    def load(self) -> None:
        cards: list[QWidget] = [label(text("guide.lead"), "sub")]
        steps = [s for s in STEPS if self.may(s[4])]
        for number, (title, words, button, where, _need) in enumerate(steps, start=1):
            frame, layout = card(f"{number}. {text(title)}")
            layout.addWidget(label(text(words), "sub"))
            go = push(text(button))
            go.clicked.connect(lambda _=False, w=where: self.go(w))
            layout.addLayout(buttons(go))
            cards.append(frame)
        if self.owner():
            frame, layout = card(text("guide.password"))
            layout.addWidget(label(text("guide.password_text"), "sub"))
            layout.addWidget(label("python -m installer.reset_password", "address"))
            cards.append(frame)
        self.fill("steps", *cards)

    def go(self, where: str) -> None:
        window = self.window_
        if where.startswith("gear:"):
            window.open_list("PC settings", where.removeprefix("gear:"))
            return
        server = window.server_id or next((r["id"] for r in window.servers), None)
        if server is None:
            window.show_screen("All servers")
            return
        window.go_to_server(server)
        if where == "Activity":
            window.open_activity(crashes=True)
        else:
            window.open_page(where)


class About(Section):
    WORDS = "desktop.about.title"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, ["about", "licences"])

    def load(self) -> None:
        window = self.window_
        frame, layout = card(text("app.name"))
        layout.addWidget(label(text("mobile.about.version", version=APP_VERSION)))
        agent = window.agent_version
        if agent and agent != APP_VERSION:
            layout.addWidget(
                label(text("desktop.about.agent", pc=window.agent.place, version=agent), "sub")
            )
        layout.addWidget(label(text("mobile.about.free"), "sub"))
        layout.addWidget(label(text("mobile.about.disclaimer"), "muted"))
        links = [
            ("desktop.about.docs", f"{REPO}#readme"),
            ("mobile.about.source", REPO),
            ("mobile.about.privacy", f"{REPO}/blob/main/PRIVACY.md"),
            ("mobile.about.report", f"{REPO}/issues/new?template=bug_report.yml"),
        ]
        shown = []
        for words, url in links:
            button = push(text(words), "plain")
            button.clicked.connect(lambda _=False, u=url: window.pc.open_link(u))
            shown.append(button)
        layout.addLayout(buttons(*shown))
        layout.addWidget(label(text("mobile.about.forms_hint"), "muted"))
        self.fill("about", frame)
        licences, inside = card(text("mobile.about.licenses"))
        inside.addWidget(label(text("mobile.about.licenses_lead"), "sub"))
        try:
            notices = (SHARED.parent / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
        except OSError as exc:
            notices = str(exc)
        notes = label(notices)
        notes.setTextFormat(Qt.TextFormat.PlainText)
        inside.addWidget(notes)
        self.fill("licences", licences)
