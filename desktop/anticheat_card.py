"""The anti-cheat picker on the Mods page (agent/anticheat.py): free ones
installed with the normal add-on install, paid ones bought on the seller's
page and added from a file, or your own jar. The Protection card's
anti-cheat line opens this page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .overview import card
from .ui import label, push, refill
from .words import text

if TYPE_CHECKING:
    from .mods import ModsPage


class AntiCheatCard:
    """Shown on Java servers; says so when none runs on the server's type."""

    def __init__(self, page: ModsPage) -> None:
        self.page = page
        self.frame, layout = card(text("anticheat.title"))
        layout.addWidget(label(text("anticheat.intro"), "sub"))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.frame.setVisible(False)

    def show(self, row: dict) -> None:
        java = row.get("edition") != "bedrock"
        if not java:
            self.frame.setVisible(False)
        else:
            self.page.ask("GET", "/anticheat", self.fill, lambda _e: None)

    def fill(self, data: dict) -> None:
        if not data.get("supported"):
            refill(self.rows, [label(text("anticheat.none_here"), "sub")])
            self.frame.setVisible(True)
            return
        page, names = self.page, ", ".join(data.get("installed") or [])
        may_install = bool(data.get("may_install"))
        rows: list[QWidget] = []
        if len(data.get("installed") or []) > 1:
            rows.append(label(text("anticheat.two", names=names), "step"))
        elif names:
            rows.append(label(text("anticheat.in_now", names=names)))
        else:
            rows.append(label(text("anticheat.nothing_in")))
        rows.append(label(text("anticheat.cpu"), "sub"))
        if not may_install:
            rows.append(label(text("anticheat.no_install"), "sub"))

        rows.append(label(text("anticheat.free"), name="h2"))
        for item in data.get("free") or []:
            words = [text(f"anticheat.about.{item['id']}")]
            if item.get("warning"):
                words.append(text("anticheat.fabric_warning"))
            button = None
            if item.get("on"):
                words.append(text("anticheat.is_in", name=item["name"]))
            elif may_install:
                button = push(text("anticheat.add", name=item["name"]))
                button.clicked.connect(
                    lambda _=False, p=item["project"]: page.install_hit({"slug": p})
                )
            rows.append(_row("\n".join(words), button))

        if data.get("paid"):
            rows.append(label(text("anticheat.paid"), name="h2"))
            rows.append(label(text("anticheat.buy_note"), "sub"))
        for item in data.get("paid") or []:
            price = item.get("price")
            words = [
                text(f"anticheat.about.{item['id']}"),
                text("anticheat.price", price=price, day=item["checked"])
                if price
                else text("anticheat.price_unknown"),
            ]
            if item.get("on"):
                words.append(text("anticheat.is_in", name=item["name"]))
            buttons = [push(text("anticheat.seller"))]
            buttons[0].clicked.connect(lambda _=False, u=item["link"]: page.window_.pc.open_link(u))
            if item.get("setup"):
                buttons.append(push(text("anticheat.setup")))
                buttons[-1].clicked.connect(
                    lambda _=False, u=item["setup"]: page.window_.pc.open_link(u)
                )
            if may_install and data.get("may_add_own_jar") and not item.get("on"):
                buttons.append(push(text("anticheat.from_file")))
                buttons[-1].clicked.connect(page.pick_jar)
            rows.append(_row("\n".join(words), *buttons))
        if data.get("paid"):
            rows.append(label(text("anticheat.prices_note"), "sub"))

        rows.append(label(text("anticheat.custom"), name="h2"))
        own = may_install and bool(data.get("may_add_own_jar"))
        button = push(text("anticheat.from_file")) if own else None
        if button:
            button.clicked.connect(page.pick_jar)
        about = text("anticheat.custom_about")
        if not own:
            about += "\n" + text("anticheat.no_own_jar")
        rows.append(_row(about, button))
        refill(self.rows, rows)
        self.frame.setVisible(True)


def _row(words: str, *buttons) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 4, 0, 4)
    h.addWidget(label(words), 1)
    for button in buttons:
        if button is not None:
            h.addWidget(button)
    return row
