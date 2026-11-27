"""Moves with depth: a page turning in, and the arithmetic for drawing a
flat thing tipped back (the top view uses it too).

Every move asks ``parts.animated`` first, so with Windows' "Show
animations" off, or over Remote Desktop, things are just there at once.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QStackedWidget, QWidget

from .parts import animated, glide, ink, look_of

TURN_MS = 560


class Eye:
    """Where a point in space lands on the screen, seen from ``far`` pixels
    in front of ``at``. Things further away (z below 0) come out smaller
    and nearer to ``at``, which is all perspective is."""

    def __init__(self, at: QPointF, far: float) -> None:
        self.at, self.far = at, far

    def see(self, x: float, y: float, z: float) -> QPointF:
        scale = self.far / (self.far - z)
        return QPointF(
            self.at.x() + (x - self.at.x()) * scale, self.at.y() + (y - self.at.y()) * scale
        )


def onto(flat: QRectF, corners: list[QPointF]) -> QTransform:
    """The painter transform that draws ``flat`` on the four ``corners``
    (top left, top right, bottom right, bottom left)."""
    square = [flat.topLeft(), flat.topRight(), flat.bottomRight(), flat.bottomLeft()]
    found = QTransform.quadToQuad(QPolygonF(square), QPolygonF(corners))
    return found if isinstance(found, QTransform) else QTransform()


class Turn(QWidget):
    """A page turning in from the page list's edge. It lies over the page
    for half a second and draws a picture of it, a little turned and
    see-through at first. Clicks go through to the real page under it."""

    ANGLE, SLIDE = 9.0, 12.0  # degrees about the left edge, and pixels to the left
    running: Turn | None = None

    def __init__(self, stack: QStackedWidget) -> None:
        super().__init__(stack)
        self.page = stack.currentWidget()
        self.at = 0.0
        self.picture = QPixmap()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(stack.rect())
        self.move_ = glide(self, TURN_MS, self._turn_to)
        self.move_.finished.connect(self.stop)
        # Qt deletes a turn with its window, without calling stop.
        self.destroyed.connect(lambda: self._forget())
        self._turn_to(0.0)
        self.show()
        self.raise_()
        self.move_.start()

    def _turn_to(self, at: float) -> None:
        self.at = at
        # A new picture each step, so what loads meanwhile shows too. Taken
        # here and not while painting: Qt can't paint two widgets at once.
        self.picture = self.page.grab()
        self.update()

    def stop(self) -> None:
        self.move_.stop()
        self._forget()
        self.hide()
        self.deleteLater()

    def _forget(self) -> None:
        if Turn.running is self:
            Turn.running = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), ink(look_of(self)["paper"]))
        left = 1 - self.at
        flat = QRectF(self.rect())
        eye = Eye(flat.center(), 1400)
        angle = math.radians(self.ANGLE * left)
        shift = -self.SLIDE * left
        wide, tall = flat.width(), flat.height()
        corners = [
            eye.see(shift + x * math.cos(angle), y, -x * math.sin(angle))
            for x, y in ((0, 0), (wide, 0), (wide, tall), (0, tall))
        ]
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self.at)
        painter.setTransform(onto(flat, corners))
        painter.drawPixmap(0, 0, self.picture)


def turn(stack: QStackedWidget) -> None:
    """Call after ``stack`` got a new current page. One page turns at a
    time: a new turn ends the one before."""
    if Turn.running is not None:
        Turn.running.stop()
    if stack.isVisible() and stack.currentWidget() is not None and animated(stack):
        Turn.running = Turn(stack)
