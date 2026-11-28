"""The Far view card on the Mods page (agent/farview.py): Off, Distant
Horizons or Voxy, with what each adds here; "Build land nobody has
visited" (DH), "Load the world ahead at night" (Chunky), and in Technical
mode "Rebuild far view"."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QSpinBox, QVBoxLayout, QWidget

from .anticheat_card import _row
from .overview import card
from .ui import label, push, refill, size
from .words import mode, text

if TYPE_CHECKING:
    from .mods import ModsPage


class FarViewCard:
    def __init__(self, page: ModsPage) -> None:
        self.page = page
        self.frame, layout = card(text("farview.title"))
        layout.addWidget(label(text("farview.intro"), "sub"))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.frame.setVisible(False)

    def show(self, row: dict) -> None:
        if row.get("edition") == "bedrock":
            self.frame.setVisible(False)
        else:
            self.page.ask("GET", "/far-view", self.fill, lambda _e: None)

    def fill(self, data: dict) -> None:
        page = self.page
        if not data.get("supported"):
            refill(self.rows, [label(text("farview.none_here"), "sub")])
            self.frame.setVisible(True)
            return
        may, now = bool(data.get("may_change")), data.get("choice") or "off"
        rows: list[QWidget] = []
        names = {c["id"]: c["name"] for c in data.get("choices") or []}
        rows.append(
            label(
                text("farview.is_on", name=names[now]) if now in names else text("farview.is_off")
            )
        )
        rows.append(label(text("farview.stop_first" if may else "farview.no_change"), "sub"))
        for item in data.get("choices") or []:
            words = [text(f"farview.about.{item['id']}")]
            if not item.get("available"):
                words.append(item.get("why") or "")
            elif item["id"] != now:
                if item.get("server"):
                    words.append(text("farview.server_gets", names=", ".join(item["server"])))
                if item.get("players"):
                    words.append(text("farview.players_get", names=", ".join(item["players"])))
                if not item.get("server") and not item.get("players"):
                    words.append(text("farview.all_here"))
            button = None
            if may and item.get("available") and item["id"] != now:
                button = push(text("farview.use", name=item["name"]))
                button.clicked.connect(lambda _=False, c=item["id"]: self.choose(c))
            rows.append(_row("\n".join(words), button))
        if now != "off" and may:
            off = push(text("farview.turn_off"))
            off.clicked.connect(lambda _=False: self.choose("off"))
            rows.append(_row(text("farview.off_note"), off))

        load = data.get("load_ahead") or {}
        unvisited = QCheckBox(text("farview.unvisited"))
        unvisited.setChecked(bool(data.get("unvisited")))
        unvisited.setEnabled(may)
        if now == "distanthorizons":
            rows += [unvisited, label(text("farview.unvisited_hint"), "sub")]
        ahead = QCheckBox(text("farview.load_ahead"))
        ahead.setChecked(bool(load.get("on")))
        ahead.setEnabled(may and bool(load.get("chunky")))
        radius = QSpinBox()
        radius.setRange(100, 30000)
        radius.setSingleStep(500)
        radius.setValue(int(load.get("radius") or 2000))
        radius.setEnabled(may)
        rows += [ahead, label(text("farview.load_ahead_hint", night=load.get("night", "")), "sub")]
        if not load.get("chunky"):
            add = push(text("farview.add_chunky")) if may else None
            if add:
                add.clicked.connect(lambda _=False: page.install_hit({"slug": "chunky"}))
            rows.append(_row(text("farview.needs_chunky"), add))
        elif load.get("running"):
            rows.append(label(text("farview.loading_now")))
        rows.append(_row(text("farview.radius"), radius))
        if may:
            save = push(text("farview.save"))
            save.clicked.connect(
                lambda _=False: self.save(
                    unvisited.isChecked(), ahead.isChecked(), radius.value(), load.get("world", "")
                )
            )
            rows.append(_row("", save))
        rows.append(label(text("farview.backups_note"), "sub"))
        if mode["technical"] and may:
            rebuild = push(text("farview.rebuild"))
            rebuild.clicked.connect(lambda _=False: self.rebuild())
            rows.append(_row(text("farview.rebuild_hint"), rebuild))
        refill(self.rows, rows)
        self.frame.setVisible(True)

    def choose(self, choice: str) -> None:
        def done(_r) -> None:
            self.page.say(text("farview.changed"))
            self.page.after_change()
            self.show(self.page.row)

        self.page.ask("POST", "/far-view", done, self.page.said, body={"choice": choice}, slow=True)

    def save(self, unvisited: bool, ahead: bool, radius: int, world: str) -> None:
        body = {"unvisited": unvisited, "load_ahead": ahead, "radius": radius, "world": world}

        def done(data: dict) -> None:
            self.page.say(text("farview.saved"))
            self.fill(data)

        self.page.ask("POST", "/far-view/options", done, self.page.said, body=body)

    def rebuild(self) -> None:
        window = self.page.window_
        if not window.confirm(
            text("farview.rebuild"), text("farview.rebuild_ask"), text("farview.rebuild")
        ):
            return

        def done(r: dict) -> None:
            self.page.say(text("farview.rebuilt", files=r["files"], size=size(r.get("size_bytes"))))

        self.page.ask("POST", "/far-view/rebuild", done, self.page.said, slow=True)
