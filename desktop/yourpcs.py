"""Settings > Your PCs: the owner's PCs that share one account
(agent/yourpcs.py). It lists them with whether each has every change, adds a
PC (a join link that works once, for 10 minutes), joins this PC to another
PC's account, answers "Allow <PC> to join your account?" and password
resets made on another PC, and removes a PC.

The words never say a change reached a PC unless the agent knows it did.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QGridLayout, QLineEdit

from .gear import Section, buttons, link_row
from .overview import card
from .tailscale_step import QrCode
from .ui import clock, field, label, push
from .words import text


def reach_words(pc: dict[str, Any]) -> str:
    """One PC's line: up to date, sending, or out of reach since when."""
    reach = pc.get("reach") or {}
    state, name = reach.get("state"), pc.get("name") or ""
    if state == "this":
        return text("desktop.pcs.this")
    if state == "unreachable":
        words = text("desktop.pcs.unreachable", pc=name, time=clock(reach.get("since")))
        if reach.get("waiting"):
            words += " " + text("desktop.pcs.saved_here", pc=name)
        return words
    if state == "sending":
        return text("desktop.pcs.sending")
    if state == "ok":
        return text("desktop.pcs.up_to_date")
    return text("desktop.pcs.not_yet")


class YourPCs(Section):
    WORDS = "desktop.pcs.title"

    def __init__(self, window) -> None:
        super().__init__(window, ["asks", "list", "add", "join"])

    def allowed(self) -> bool:
        # A moderator's app learns the PCs too (All servers), without this page.
        return self.owner()

    def load(self) -> None:
        self.call("GET", "/pcs", self.show_pcs)

    def show_pcs(self, found: dict) -> None:
        self.window_.pcs_loaded(found)
        self.show_asks(found)
        frame, layout = card(text("desktop.pcs.list"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        for row, pc in enumerate(found.get("pcs") or []):
            grid.addWidget(label(pc.get("name") or ""), row, 0)
            grid.addWidget(label(reach_words(pc), "sub"), row, 1)
            if not pc.get("this") and found.get("joined"):
                out = push(text("desktop.pcs.remove"), "danger")
                out.setAccessibleName(f"{text('desktop.pcs.remove')} {pc.get('name')}")
                out.clicked.connect(lambda _=False, p=pc: self.remove(p))
                grid.addWidget(out, row, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        if not found.get("joined"):
            layout.addWidget(label(text("desktop.pcs.alone"), "muted"))
        self.fill("list", frame)
        self.show_add()
        self.show_join(found)

    def show_asks(self, found: dict) -> None:
        cards = []
        for ask in found.get("joins") or []:
            frame, layout = card(text("desktop.pcs.allow_title", pc=ask.get("name") or ""))
            layout.addWidget(
                label(text("desktop.pcs.allow_words", time=clock(ask.get("created_at"))))
            )
            allow = push(text("desktop.pcs.allow"), "primary")
            allow.clicked.connect(lambda _=False, a=ask: self.answer(a, "allow"))
            refuse = push(text("desktop.pcs.refuse"))
            refuse.clicked.connect(lambda _=False, a=ask: self.answer(a, "refuse"))
            layout.addLayout(buttons(allow, refuse))
            cards.append(frame)
        for reset in found.get("resets") or []:
            frame, layout = card(text("desktop.pcs.reset_title", pc=reset.get("name") or ""))
            layout.addWidget(label(text("desktop.pcs.reset_words", pc=reset.get("name") or "")))
            confirm = push(text("desktop.pcs.reset_confirm"), "primary")
            confirm.clicked.connect(lambda _=False, r=reset: self.confirm_reset(r))
            layout.addLayout(buttons(confirm))
            cards.append(frame)
        self.fill("asks", *cards)

    def show_add(self) -> None:
        frame, layout = card(text("desktop.pcs.add"))
        layout.addWidget(label(text("desktop.pcs.add_words"), "sub"))
        make = push(text("desktop.pcs.make_link"))

        def made(found: dict) -> None:
            qr = QrCode()
            qr.setAccessibleName(text("pair.qr_label"))
            qr.show_rows(found.get("qr") or [])
            layout.addWidget(qr)
            layout.addLayout(link_row(self.window_, found.get("url") or ""))
            layout.addWidget(
                label(text("desktop.pcs.link_until", time=clock(found.get("expires_at"))), "muted")
            )
            make.hide()

        make.clicked.connect(lambda: self.call("POST", "/pcs/link", made))
        layout.addLayout(buttons(make))
        self.fill("add", frame)

    def show_join(self, found: dict) -> None:
        if found.get("joined"):
            self.fill("join")
            return
        frame, layout = card(text("desktop.pcs.join"))
        if found.get("joining"):
            layout.addWidget(label(text("desktop.pcs.waiting")))
            self.fill("join", frame)
            return
        layout.addWidget(label(text("desktop.pcs.join_words"), "sub"))
        link, name, password = QLineEdit(), QLineEdit(), QLineEdit()
        link.setObjectName("join_link")
        password.setEchoMode(QLineEdit.EchoMode.Password)
        for box, words in (
            (link, "desktop.pcs.join_link"),
            (name, "login.username"),
            (password, "login.password"),
        ):
            box.setAccessibleName(text(words))
            layout.addWidget(field(text(words), box))
        go = push(text("desktop.pcs.join_button"), "primary")
        go.clicked.connect(
            lambda: self.call(
                "POST",
                "/pcs/join",
                self.then_reload(text("desktop.pcs.waiting")),
                body={"link": link.text(), "username": name.text(), "password": password.text()},
            )
        )
        layout.addLayout(buttons(go))
        self.fill("join", frame)

    def answer(self, ask: dict, answer: str) -> None:
        words = "desktop.pcs.allowed" if answer == "allow" else "desktop.pcs.refused"
        self.call(
            "POST",
            f"/pcs/joins/{ask['id']}/{answer}",
            self.then_reload(text(words, pc=ask.get("name") or "")),
        )

    def confirm_reset(self, reset: dict) -> None:
        self.call(
            "POST",
            f"/pcs/resets/{reset['pc']}/confirm",
            lambda _r: self.window_.signed_out(text("desktop.pcs.reset_done")),
        )

    def remove(self, pc: dict) -> None:
        name = pc.get("name") or ""
        if self.window_.confirm(
            text("desktop.pcs.remove_title", pc=name),
            text("desktop.pcs.remove_body", pc=name),
            text("desktop.pcs.remove"),
        ):
            self.call(
                "DELETE", f"/pcs/{pc['id']}", self.then_reload(text("desktop.pcs.removed", pc=name))
            )
