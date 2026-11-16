"""Command Block's look as a Qt style sheet, over Qt's Fusion style.

The colors are the ones the phone apps use (``shared/theme.json``), so
every app looks the same. Qt
style sheets have no ``var()``, so the sheet below is filled in from them.
The installer uses the same look.

The page is plain paper in every server: a server's color goes on its tab,
the band behind the sheet, the buttons and the edges. Where no server is
open, the app icon's copper takes its place.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent import colors

SHARED = Path(__file__).resolve().parent.parent / "shared"
FONTS = Path(__file__).resolve().parent / "fonts"
RADIUS = {"radius-sm": "8px", "radius": "8px", "radius-lg": "14px"}
COPPER = "#C8794A"
HEADING = '"Archivo", "Segoe UI Variable Display", "Segoe UI", sans-serif'
# What a server's color (or the copper) sets. The rest stays the theme's.
ACCENT = ("accent", "accent-text", "accent-hover", "accent-hover-text", "accent-ink", "accent-ring")
SERVER = ("accent-band", "accent-edge", "tab-fill", "tab-text")


def load_fonts() -> None:
    """Archivo, the heading font, from the app's own folder (no download).
    Qt keeps one font list per program, so this only works once."""
    from PySide6.QtGui import QFontDatabase  # here, so the colors load without Qt

    if "Archivo" not in QFontDatabase.families():
        for file in sorted(FONTS.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(file))


def _qt(value) -> str:
    """theme.json alphas are 0 to 1; Qt's are 0 to 255."""
    if isinstance(value, dict):
        r, g, b = (int(value["rgb"][i : i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r}, {g}, {b}, {round(value['alpha'] * 255)})"
    return value


def _theme(dark: bool) -> dict:
    return json.loads((SHARED / "theme.json").read_text(encoding="utf-8"))[
        "dark" if dark else "light"
    ]


def tokens(dark: bool, server_color: str | None = None) -> dict[str, str]:
    """The theme's colors, with a server's color on the accents, the band
    and the edges when one is given (copper when not)."""
    theme = _theme(dark)
    mode = "dark" if dark else "light"
    out = {**{k: _qt(v) for k, v in theme.items()}, **RADIUS}
    out["tab-fill"], out["tab-text"] = out["tab-neutral"], out["text-primary"]
    tint = colors.derive_from(server_color, theme, mode) if server_color else None
    keys = ACCENT + SERVER if tint else ACCENT
    tint = tint or colors.derive_from(COPPER, theme, mode) or {}
    out.update({k: tint[k] for k in keys})
    out["accent-soft"] = _qt({"rgb": tint["accent-edge"], "alpha": 0.2 if dark else 0.13})
    out["paper"] = out["sheet"] if dark else out["surface"]
    # Edges take the color too. accent-ring is that color made dark (or
    # light) enough to see as a line: 3 to 1 against the paper.
    out["border"] = _qt({"rgb": out["accent-ring"], "alpha": 0.5})
    out["border-strong"] = out["accent-ring"]
    # The band is stronger than the phones' (their part of the redesign
    # comes later), and the words on it are moved until they read on it.
    band = colors.mix(_rgb(out["accent-band"]), _rgb(out["accent-edge"]), 0.28)
    out["band"] = colors.to_hex(band)
    out["band-text"] = colors.to_hex(colors.readable(_rgb(out["text-primary"]), [band]))
    return out


def _rgb(value: str) -> colors.RGB:
    found = colors.rgb(value)
    assert found is not None, value  # theme.json and derive_from only give hex here
    return found


def style_sheet(t: dict[str, str], scale: float = 1.0) -> str:
    """The whole window's look. Object names and the ``kind`` property pick
    the variants (``.btn.primary``). ``scale``
    is Windows' text size (1.0 to 2.25): every font grows with it."""
    sheet = _sheet(t)
    if scale == 1.0:
        return sheet
    return re.sub(r"(\d+(?:\.\d+)?)pt", lambda m: f"{float(m[1]) * scale:g}pt", sheet)


def row_sheet(t: dict[str, str]) -> str:
    """The buttons of one server's box on All servers, in that server's
    color (``t`` are its tokens). Everything else stays the window's."""
    return f"""
QPushButton {{ border: 1px solid {t["border-strong"]}; }}
QPushButton:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton[kind="primary"] {{ background: {t["accent"]}; color: {t["accent-text"]};
                               border: 1px solid {t["accent-ring"]}; }}
QPushButton[kind="primary"]:hover {{ background: {t["accent-hover"]}; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton[kind="plain"] {{ color: {t["accent-ink"]}; border: 1px solid transparent; }}
QPushButton[kind="plain"]:hover {{ background: {t["accent-soft"]}; }}
"""


def _tick(t: dict[str, str]) -> str:
    """The tick mark's picture, white or black like the words on a filled
    button. A style sheet can only take it from a file."""
    name = "tick-white.svg" if t["accent-text"] == "#FFFFFF" else "tick-black.svg"
    return (FONTS.parent / "images" / name).as_posix()


def _sheet(t: dict[str, str]) -> str:
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt;
     color: {t["text-primary"]}; }}
QWidget#desk {{ background: {t["desk"]}; }}
QFrame#sheet {{ background: {t["paper"]}; border-radius: 12px;
                border: 1px solid {t["border-strong"]}; }}
QLabel {{ background: transparent; }}
QLabel#appName, QLabel#title, QLabel#h1, QLabel#h2, QTabBar#binder, QToolButton#pageTab {{
    font-family: {HEADING}; }}
QLabel#appName {{ font-size: 15pt; font-weight: 600; }}
QLabel#appVersion, QLabel[kind="sub"] {{ color: {t["text-secondary"]}; }}
QLabel#title {{ font-size: 13pt; font-weight: 600; }}
QLabel#intro {{ color: {t["text-secondary"]}; }}
QLabel[kind="muted"] {{ color: {t["text-tertiary"]}; font-size: 9pt; }}
QLabel[kind="problem"] {{ color: {t["danger"]}; }}
QLabel[kind="ok"] {{ color: {t["success"]}; }}
QLabel[kind="step"] {{ font-weight: 600; }}
QLabel[kind="address"] {{ font-family: "Cascadia Mono", Consolas, monospace; }}
QLabel[kind="notice"] {{ background: {t["surface"]}; border: 1px solid {t["border"]};
                         border-radius: {t["radius"]}; padding: 8px 12px; }}
QLabel[kind="bad"] {{ background: {t["danger-soft"]}; border-radius: {t["radius"]};
                      padding: 8px 12px; }}
QLabel[kind="good"] {{ background: {t["success-soft"]}; border-radius: {t["radius"]};
                       padding: 8px 12px; }}

QPushButton {{ background: {t["surface"]}; border: 1px solid {t["border-strong"]};
               border-radius: {t["radius"]}; padding: 5px 14px; min-height: 20px; font-weight: 500; }}
QPushButton:hover {{ background: {t["surface-sunken"]}; }}
QPushButton:pressed {{ background: {t["surface-selected"]}; }}
QPushButton:focus {{ border: 2px solid {t["accent-ring"]}; padding: 4px 13px; }}
QPushButton:disabled {{ color: {t["text-tertiary"]}; background: {t["surface-sunken"]}; }}
QPushButton:checked {{ background: {t["accent-soft"]}; color: {t["accent-ink"]};
                       border: 1px solid {t["accent-ring"]}; font-weight: 600; }}
QPushButton[kind="primary"] {{ background: {t["accent"]}; color: {t["accent-text"]};
                               border: 1px solid {t["accent-ring"]}; min-width: 96px; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t["accent-hover"]}; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton[kind="primary"]:disabled {{ background: {t["accent-soft"]}; color: {t["text-tertiary"]};
                                        border-color: transparent; }}
QPushButton[kind="danger"] {{ background: {t["danger"]}; color: {t["on-danger"]}; border: 1px solid {t["danger"]};
                              font-weight: 600; }}
QPushButton[kind="tonal"] {{ background: {t["accent-soft"]}; border: 1px solid transparent;
                             color: {t["accent-ink"]}; font-weight: 600; }}
QPushButton[kind="tonal"]:hover {{ border: 1px solid {t["accent-ring"]}; }}
QPushButton[kind="tonal"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton[kind="plain"] {{ background: transparent; border: 1px solid transparent;
                             color: {t["accent-ink"]}; padding: 5px 8px; }}
QPushButton[kind="plain"]:hover {{ background: {t["accent-soft"]}; }}
QPushButton[kind="dashed"] {{ background: transparent; border: 1px dashed {t["border-strong"]}; }}
QPushButton[kind="dashed"]:hover {{ background: {t["accent-soft"]}; }}
QPushButton[kind="dashed"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton[kind="swatch"] {{ border-radius: 11px; min-width: 22px; max-width: 22px;
                              min-height: 22px; max-height: 22px; padding: 0;
                              border: 1px solid {t["border-strong"]}; }}
QPushButton[kind="swatch"]:checked {{ border: 3px solid {t["text-primary"]}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {t["surface"]}; border: 1px solid {t["border-strong"]};
             border-radius: {t["radius"]}; padding: 5px 8px; min-height: 20px;
             selection-background-color: {t["accent"]}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {t["accent-ring"]}; padding: 4px 7px; }}

QListWidget {{ background: {t["surface"]}; border: 1px solid {t["border"]};
               border-radius: {t["radius"]}; padding: 4px; }}
QListWidget::item {{ padding: 8px; border-bottom: 1px solid {t["border"]}; }}
QListWidget::item:selected {{ background: {t["accent-soft"]}; color: {t["text-primary"]}; }}

QFrame[kind="choice"] {{ background: {t["surface"]}; border: 1px solid {t["border"]};
                         border-radius: {t["radius"]}; }}
QFrame[kind="row"] {{ background: {t["surface-sunken"]}; border: 0; border-radius: {t["radius"]}; }}
QFrame[kind="choice"][selected="true"] {{ background: {t["accent-soft"]};
                                          border: 1px solid {t["accent-ring"]}; }}
QFrame[kind="line"] {{ background: {t["border"]}; max-height: 1px; min-height: 1px; }}
QRadioButton, QCheckBox {{ background: transparent; spacing: 8px; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; background: {t["surface"]};
                           border: 1px solid {t["border-strong"]}; }}
QRadioButton::indicator:checked {{ background: {t["accent-text"]}; border: 5px solid {t["accent"]};
                                   width: 6px; height: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; background: {t["surface"]};
                        border: 1px solid {t["border-strong"]}; }}
QCheckBox::indicator:hover {{ border: 1px solid {t["accent-ring"]}; }}
QCheckBox::indicator:checked {{ background: {t["accent"]}; border-color: {t["accent-ring"]};
                                image: url({_tick(t)}); }}

QProgressBar {{ background: {t["surface-sunken"]}; border: 0; border-radius: 4px;
                max-height: 8px; min-height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {t["accent"]}; border-radius: 4px; }}

QPlainTextEdit {{ background: {t["console-bg"]}; border: 1px solid {t["border"]};
                  border-radius: {t["radius"]}; font-family: "Cascadia Mono", Consolas, monospace;
                  font-size: 9pt; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QDialog, QMessageBox {{ background: {t["paper"]}; }}
QToolTip {{ background: {t["surface"]}; border: 1px solid {t["border-strong"]}; }}

QTabBar#binder {{ font-weight: 500; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {t["radius"]};
               padding: 4px 8px; }}
QToolButton:hover {{ background: {t["surface-hover"]}; }}
QToolButton:focus {{ border: 2px solid {t["accent-ring"]}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle {{ background: {t["neutral"]}; border-radius: 4px; min-width: 24px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0;
                                                                                       background: none; }}
QToolButton#pageTab {{ color: {t["band-text"]}; border: 2px solid transparent; border-radius: 9px;
                       padding: 0 10px; min-height: 32px; font-weight: 500; }}
QToolButton#pageTab:hover {{ background: transparent; }}
QToolButton#pageTab:checked {{ color: {t["text-primary"]}; font-weight: 600; }}
QToolButton#pageTab:focus {{ border: 2px solid {t["accent-ring"]}; }}
QFrame#band {{ background: {t["band"]}; border-radius: {t["radius-lg"]}; }}
QFrame#strip {{ background: {t["paper"]}; border: 1px solid {t["border-strong"]}; border-radius: 10px; }}
QFrame#strip QToolButton {{ padding: 3px 5px; }}
QFrame#strip QToolButton#close:hover {{ background: {t["danger"]}; }}
QFrame#gap {{ background: {t["border-strong"]}; max-width: 1px; min-width: 1px; }}
QLabel[kind="big"] {{ font-size: 19pt; font-weight: 600; }}
QLabel[kind="group"] {{ color: {t["text-secondary"]}; font-size: 9pt; font-weight: 600; }}
QFrame#newServer {{ border: 1px dashed {t["border-strong"]}; border-radius: 12px; }}
QFrame#panel {{ border: 0 solid {t["border-strong"]}; border-left-width: 1px; }}
QFrame#panel[under="true"] {{ border-left-width: 0; border-top-width: 1px; }}
QLabel#h1 {{ font-size: 25.5pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QPushButton[big="true"] {{ font-size: 12pt; min-width: 110px; min-height: 30px; }}
QToolButton#tool {{ background: {t["paper"]}; border: 1px solid {t["border-strong"]};
                    border-radius: 10px; padding: 0; min-width: 34px; max-width: 34px;
                    min-height: 34px; max-height: 34px; }}
QToolButton#tool:hover {{ background: {t["surface-sunken"]}; }}
QToolButton#tool:pressed {{ background: {t["surface-selected"]}; }}
QToolButton#tool:focus {{ border: 2px solid {t["accent-ring"]}; }}
QToolButton#tool[kind="accent"] {{ background: {t["accent"]}; border-color: {t["accent-ring"]}; }}
QToolButton#tool[kind="accent"]:hover {{ background: {t["accent-hover"]}; }}
QToolButton#tool[kind="accent"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QToolButton#tool[kind="danger"] {{ background: transparent; border-color: transparent; }}
QToolButton#tool[kind="danger"]:hover {{ background: {t["danger"]}; }}
QToolButton#tool[kind="danger"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton#seg {{ background: transparent; border: 2px solid transparent; border-radius: 7px;
                   padding: 2px 14px; min-height: 20px; font-weight: 500; }}
QPushButton#seg:checked {{ font-weight: 600; }}
QPushButton#seg:focus {{ border: 2px solid {t["accent-ring"]}; padding: 2px 14px; }}
QLineEdit#bigNumber {{ font-size: 25.5pt; font-weight: 600; background: transparent;
                       border: 2px solid transparent; padding: 0 4px; min-height: 0; }}
QLineEdit#bigNumber:hover {{ background: {t["surface-sunken"]}; }}
QLineEdit#bigNumber:focus {{ background: {t["paper"]}; border: 2px solid {t["accent-ring"]};
                             padding: 0 4px; }}
"""
