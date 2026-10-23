"""Command Block's look as a Qt style sheet, over Qt's Fusion style.

The colors are the ones the phone apps use (``shared/theme.json``), so
every app looks the same. Qt
style sheets have no ``var()``, so the sheet below is filled in from them.
The installer uses the same look.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent import colors

SHARED = Path(__file__).resolve().parent.parent / "shared"
RADIUS = {"radius-sm": "6px", "radius": "8px", "radius-lg": "12px"}


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
    """The theme's colors, tinted with a server's color when one is given
    (the same tinting as the phone apps)."""
    theme = _theme(dark)
    out = {**{k: _qt(v) for k, v in theme.items()}, **RADIUS}
    out["tab-fill"], out["tab-text"] = out["tab-neutral"], out["text-primary"]
    tint = (
        colors.derive_from(server_color, theme, "dark" if dark else "light")
        if server_color
        else None
    )
    if tint:
        out.update(tint)
        out["accent-soft"] = _qt({"rgb": tint["accent-edge"], "alpha": 0.2 if dark else 0.13})
    return out


def style_sheet(t: dict[str, str], scale: float = 1.0) -> str:
    """The whole window's look. Object names and the ``kind`` property pick
    the variants (``.btn.primary``). ``scale``
    is Windows' text size (1.0 to 2.25): every font grows with it."""
    sheet = _sheet(t)
    if scale == 1.0:
        return sheet
    return re.sub(r"(\d+(?:\.\d+)?)pt", lambda m: f"{float(m[1]) * scale:g}pt", sheet)


def _sheet(t: dict[str, str]) -> str:
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt;
     color: {t["text-primary"]}; }}
QWidget#desk {{ background: {t["desk"]}; }}
QFrame#sheet {{ background: {t["sheet"]}; border-radius: {t["radius-lg"]};
                border: 1px solid {t["border"]}; }}
QLabel {{ background: transparent; }}
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
               border-radius: {t["radius"]}; padding: 5px 14px; min-height: 20px; }}
QPushButton:hover {{ background: {t["surface-sunken"]}; }}
QPushButton:focus {{ border: 2px solid {t["accent-ring"]}; padding: 4px 13px; }}
QPushButton:disabled {{ color: {t["text-tertiary"]}; background: {t["surface-sunken"]}; }}
QPushButton[kind="primary"] {{ background: {t["accent"]}; color: {t["accent-text"]};
                               border: 1px solid {t["accent"]}; min-width: 96px; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t["accent-hover"]}; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {t["accent-ring"]}; }}
QPushButton[kind="primary"]:disabled {{ background: {t["accent-soft"]}; color: {t["text-tertiary"]};
                                        border-color: transparent; }}
QPushButton[kind="danger"] {{ background: {t["danger"]}; color: #FFFFFF; border: 1px solid {t["danger"]};
                              font-weight: 600; }}
QPushButton[kind="plain"] {{ background: transparent; border: 1px solid transparent;
                             color: {t["accent-ink"]}; padding: 5px 8px; }}
QPushButton[kind="plain"]:hover {{ background: {t["accent-soft"]}; }}
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

QFrame[kind="choice"], QFrame[kind="row"] {{ background: {t["surface"]}; border: 1px solid {t["border"]};
                                             border-radius: {t["radius"]}; }}
QFrame[kind="choice"][selected="true"] {{ background: {t["accent-soft"]};
                                          border: 1px solid {t["accent-ring"]}; }}
QFrame[kind="line"] {{ background: {t["border"]}; max-height: 1px; min-height: 1px; }}
QRadioButton, QCheckBox {{ background: transparent; spacing: 8px; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px; background: {t["surface"]};
                           border: 1px solid {t["border-strong"]}; }}
QRadioButton::indicator:checked {{ background: {t["accent-text"]}; border: 5px solid {t["accent"]};
                                   width: 6px; height: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; background: {t["surface"]};
                        border: 1px solid {t["border-strong"]}; }}
QCheckBox::indicator:checked {{ background: {t["accent"]}; border-color: {t["accent"]}; }}

QProgressBar {{ background: {t["surface-sunken"]}; border: 0; border-radius: 4px;
                max-height: 8px; min-height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {t["accent"]}; border-radius: 4px; }}

QPlainTextEdit {{ background: {t["console-bg"]}; border: 1px solid {t["border"]};
                  border-radius: {t["radius"]}; font-family: "Cascadia Mono", Consolas, monospace;
                  font-size: 9pt; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QMessageBox {{ background: {t["sheet"]}; }}
QToolTip {{ background: {t["surface"]}; border: 1px solid {t["border-strong"]}; }}

QTabBar#binder::tab {{ background: {t["tab-neutral"]}; color: {t["text-secondary"]}; padding: 7px 16px;
                       margin-right: 3px; border-top-left-radius: {t["radius"]};
                       border-top-right-radius: {t["radius"]}; }}
QTabBar#binder::tab:selected {{ background: {t["accent-band"]}; color: {t["text-primary"]};
                                font-weight: 600; }}
QTabBar#binder::tab:focus {{ border: 2px solid {t["accent-ring"]}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {t["radius"]};
               padding: 4px 8px; }}
QToolButton:hover {{ background: {t["surface-hover"]}; }}
QToolButton:focus {{ border: 2px solid {t["accent-ring"]}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle {{ background: {t["border-strong"]}; border-radius: 4px; min-width: 24px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ width: 0; height: 0;
                                                                                       background: none; }}
QToolButton#pageTab {{ border-radius: 14px; padding: 5px 10px; }}
QToolButton#pageTab:checked {{ background: {t["surface"]}; font-weight: 600; }}
QFrame#band {{ background: {t["accent-band"]}; border-radius: {t["radius-lg"]};
               border-top-left-radius: 0; }}
QFrame[kind="card"] {{ background: {t["surface"]}; border: 1px solid {t["border"]};
                       border-radius: {t["radius-lg"]}; }}
QFrame#panel {{ background: {t["sheet"]}; border-radius: {t["radius-lg"]}; }}
QLabel#h1 {{ font-size: 20pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel[kind="chip"] {{ background: {t["surface-sunken"]}; color: {t["text-secondary"]};
                       border-radius: 10px; padding: 2px 10px; }}
QLabel[kind="chip"][strong="true"] {{ background: {t["accent"]}; color: {t["accent-text"]}; }}
QPushButton[big="true"] {{ font-size: 12pt; min-width: 110px; min-height: 30px; }}
QPushButton[kind="segment"]:checked {{ background: {t["accent"]}; color: {t["accent-text"]};
                                       font-weight: 600; }}
"""
