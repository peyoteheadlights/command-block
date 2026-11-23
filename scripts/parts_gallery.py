"""Every part of the desktop app's kit on one screen, to look at and try.

    python scripts/parts_gallery.py            (opens the gallery)
    python scripts/parts_gallery.py --shots    (writes ui-shots/desktop/parts-*.png)

It is not a screen of the app and talks to no agent. The words on it are
samples, most of them the app's own; the captions are only for us.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "ui-shots" / "desktop"

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from agent import colors  # noqa: E402
from desktop import parts, theme  # noqa: E402
from desktop.ui import label, push, swatches  # noqa: E402
from desktop.words import text  # noqa: E402


class Gallery(QWidget):
    """``pc`` answers "are animations on?" (desktop/pc.py, or the tests'
    pretend one). ``look`` is where every part finds its colors."""

    def __init__(self, pc, dark: bool = False, color: str | None = None) -> None:
        super().__init__()
        self.pc, self.dark, self.color = pc, dark, color
        self.look: dict[str, str] = {}
        self.setObjectName("gallery")
        self.setWindowTitle("Command Block parts")
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(14)

        top = QHBoxLayout()
        top.addWidget(label("Parts", name="h1"))
        top.addStretch()
        self.theme = parts.Segmented(
            [("light", text("theme.light")), ("dark", text("theme.dark"))],
            "dark" if dark else "light",
            lambda key: self.show_look(key == "dark", self.color),
        )
        top.addWidget(self.theme)
        column.addLayout(top)
        palette = [{"id": id_, "hex": hex_} for id_, _name, hex_ in colors.PALETTE]
        column.addWidget(swatches(palette, color, lambda hex_: self.show_look(self.dark, hex_)))

        grid = QGridLayout()
        grid.setSpacing(10)
        column.addLayout(grid)
        column.addStretch()
        for at, (caption, fill) in enumerate(
            (
                ("Buttons", self.buttons),
                ("Icon buttons (hover for the name)", self.icon_buttons),
                ("Switch and info dot", self.switches),
                ("Slider and big number", self.slider),
                ("Segmented control", self.segmented),
                ("Search field and tick boxes", self.fields),
                ("Status marks", self.statuses),
                ("Player faces", self.faces),
            )
        ):
            box = parts.Box()
            box.column.addWidget(label(caption, "muted"))
            fill(box)
            box.column.addStretch()
            grid.addWidget(box, at // 2, at % 2)
        self.show_look(dark, color)

    def show_look(self, dark: bool, color: str | None) -> None:
        self.dark, self.color = dark, color
        self.look = theme.tokens(dark, color)
        self.setStyleSheet(
            theme.style_sheet(self.look, self.pc.text_scale())
            + f"QWidget#gallery {{ background: {self.look['paper']}; }}"
        )

    @staticmethod
    def row(box: parts.Box, *widgets: QWidget) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch()
        box.column.addLayout(row)

    def buttons(self, box: parts.Box) -> None:
        off = push(text("action.start"))
        off.setEnabled(False)
        self.row(
            box,
            push(text("action.stop"), "primary"),
            push(text("desktop.copy"), "tonal"),
            push(text("action.restart")),
            push(text("desktop.panel.see_all"), "plain"),
        )
        self.row(box, push(text("mods.remove"), "danger"), off)

    def icon_buttons(self, box: parts.Box) -> None:
        off = parts.IconButton("undo", text("changes.undo"))
        off.setEnabled(False)
        self.row(
            box,
            parts.IconButton("performance", text("page.performance")),
            parts.IconButton("plus", text("add.way_new"), "accent"),
            parts.IconButton("close", text("mods.remove"), "danger"),
            off,
        )

    def switches(self, box: parts.Box) -> None:
        for key, on in (("serverset.auto_restart", True), ("serverset.sleep_label", False)):
            row = QHBoxLayout()
            row.addWidget(label(text(key), "step"))
            row.addWidget(parts.InfoDot(text("desktop.hint.backup")))
            row.addStretch()
            switch = parts.Switch(text(key))
            switch.setChecked(on)
            row.addWidget(switch)
            box.column.addLayout(row)
            if on:
                line = QFrame()
                line.setProperty("kind", "line")
                box.column.addWidget(line)

    def slider(self, box: parts.Box) -> None:
        """Memory in steps of half a GB: the slider and the number move
        each other, the way Settings will use them."""
        name = text("memory.label")
        self.number = parts.BigNumber(name)
        self.memory = parts.SnapSlider(name)
        self.memory.setRange(2, 16)
        self.memory.setTickInterval(2)
        self.memory.valueChanged.connect(lambda steps: self.number.show_value(f"{steps / 2:g}"))
        self.number.entered.connect(lambda gb: self.memory.setValue(round(gb * 2)))
        self.memory.setValue(9)
        row = QHBoxLayout()
        row.addWidget(label(name, "step"))
        row.addStretch()
        row.addWidget(self.number)
        row.addWidget(label("GB", "sub"))
        box.column.addLayout(row)
        box.column.addWidget(self.memory)

    def segmented(self, box: parts.Box) -> None:
        self.mode = parts.Segmented(
            [("simple", text("mode.simple")), ("technical", text("mode.technical"))],
            "simple",
            lambda key: None,
        )
        ranges = [(str(hours), text(f"perf.range_{hours}")) for hours in (1, 24, 168)]
        self.row(box, self.mode)
        self.row(box, parts.Segmented(ranges, "24", lambda key: None))

    def fields(self, box: parts.Box) -> None:
        box.column.addWidget(parts.Search(text("mods.search_placeholder")))
        ticked, plain = QCheckBox("Lithium"), QCheckBox("FerriteCore")
        ticked.setChecked(True)
        self.row(box, ticked, plain)

    def statuses(self, box: parts.Box) -> None:
        self.row(
            box,
            parts.Status("on", text("state.online")),
            parts.Status("wait", text("state.starting")),
            parts.Status("bad", text("state.crashed")),
            parts.Status("off", text("state.offline")),
        )

    def faces(self, box: parts.Box) -> None:
        for name in ("Alex", "Sam"):
            self.row(box, parts.Face(name), label(name))


def main() -> int:
    shots = "--shots" in sys.argv
    if shots and sys.platform not in ("win32", "darwin") and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from desktop.pc import WindowsPC

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    theme.load_fonts()
    if not shots:
        gallery = Gallery(WindowsPC())
        gallery.resize(900, 760)
        gallery.show()
        return app.exec()
    OUT.mkdir(parents=True, exist_ok=True)
    for dark in (False, True):
        for name, color in (("copper", None), ("blue", "#1967D4"), ("amber", "#F7E10D")):
            gallery = Gallery(WindowsPC(), dark, color)
            gallery.resize(900, 760)
            gallery.show()
            app.processEvents()
            path = OUT / f"parts-{name}-{'dark' if dark else 'light'}.png"
            gallery.grab().save(str(path))
            gallery.close()
            print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
