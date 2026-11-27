"""Streamer mode: addresses, folder paths and keys are hidden on screen, so
the app can be shown on a stream or a screen share.

What counts as one of those is in shared/streamer.json, which the phone apps
read too. Nothing is changed on the agent: only what this screen draws.

It works from one place instead of every page: while it is on, a filter on
the whole app looks at each label and text box as it is shown or redrawn and
covers what matches. Labels get the words swapped for dots; a box someone can
type in keeps its text (saving must never save the dots) and shows dots like
a password box; the pairing QR code is blurred.
"""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsBlurEffect,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from .theme import SHARED

_DATA = json.loads((SHARED / "streamer.json").read_text(encoding="utf-8"))
HIDDEN: str = _DATA["hidden"]
PATTERN = re.compile("|".join(f"(?:{p})" for p in _DATA["patterns"]))
TAG = re.compile(r"(<[^>]*>)")
MARK = "streamerCovered"  # set on what this module changed, so it can undo it

state = {"on": False}


def hide(words: str) -> str:
    """``words`` with every address, path and key swapped for dots. In rich
    text only the words between tags change, so links keep working."""
    if "<" in words and ">" in words:
        return "".join(
            piece if piece.startswith("<") else PATTERN.sub(HIDDEN, piece)
            for piece in TAG.split(words)
        )
    return PATTERN.sub(HIDDEN, words)


def shown(words: str) -> str:
    """What a line of text should show now: hidden while streamer mode is on."""
    return hide(words) if state["on"] else words


def cover(widget: QObject) -> None:
    """Hide what one widget shows, if it shows anything to hide."""
    if isinstance(widget, QLabel):
        before = widget.text()
        after = hide(before)
        if after != before:
            widget.setText(after)
    elif isinstance(widget, QLineEdit):
        if widget.isReadOnly() or not widget.isEnabled():
            before = widget.text()
            if hide(before) != before:
                widget.setText(hide(before))
        elif (
            widget.echoMode() == QLineEdit.EchoMode.Normal and hide(widget.text()) != widget.text()
        ):
            widget.setEchoMode(QLineEdit.EchoMode.Password)
            widget.setProperty(MARK, True)
    elif isinstance(widget, QPlainTextEdit):
        before = widget.toPlainText()
        if hide(before) == before:
            return
        if widget.isReadOnly():
            widget.setPlainText(hide(before))
        else:
            blur(widget)
    elif type(widget).__name__ == "QrCode":
        blur(widget)  # type: ignore[arg-type]


def blur(widget: QWidget) -> None:
    if widget.graphicsEffect() is None:
        effect = QGraphicsBlurEffect(widget)
        effect.setBlurRadius(24)
        widget.setGraphicsEffect(effect)
        widget.setProperty(MARK, True)


class _Watcher(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.Show or (
            kind == QEvent.Type.Paint and isinstance(watched, (QLabel, QLineEdit))
        ):
            cover(watched)
        return False


_watcher = _Watcher()


def turn(on: bool) -> None:
    """Start or stop covering. Turning it off takes the blur and the dots
    off text boxes; labels show their own words again when their page reloads."""
    app = QApplication.instance()
    if app is None or on == state["on"]:
        return
    state["on"] = on
    if on:
        app.installEventFilter(_watcher)
        for widget in QApplication.allWidgets():
            # A widget Qt already deleted can come back as some other type.
            if isinstance(widget, QWidget) and widget.isVisible():
                cover(widget)
        return
    app.removeEventFilter(_watcher)
    for widget in QApplication.allWidgets():
        if isinstance(widget, QWidget) and widget.property(MARK):
            widget.setProperty(MARK, False)
            if isinstance(widget, QLineEdit):
                widget.setEchoMode(QLineEdit.EchoMode.Normal)
            else:
                widget.setGraphicsEffect(None)  # type: ignore[arg-type]
