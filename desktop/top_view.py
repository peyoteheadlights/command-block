"""The top view: the binder tips back and moves down, so its top edge is
in the middle of the window and every server's tab stands one step behind
the one before, big enough to hit easily.

Qt can't tip a live widget back, so while the view is open this lies over
the whole window and draws pictures of the band and the tool strip, tipped
with ``motion.Eye``, and the tabs standing behind them. The real binder
under it is drawn at no opacity meanwhile, and clicks here are handed on
to the real tab or button.

It opens when the mouse rests on the empty part of the top row or above
it, never from a tab or a button, and with Ctrl+Tab. Leaving the top part
of the window closes it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QWidget
from shiboken6 import isValid

from .motion import Eye, onto
from .parts import animated, glide, ink

if TYPE_CHECKING:
    from .window import MainWindow


class TopView(QWidget):
    TILT, STAND = 36.0, 30.0  # degrees: the binder back, and each tab back up from it
    DOWN = 250  # how far the binder moves down
    STEP = 70  # from one server's divider to the next
    KEEP = 120  # the mouse may go this far under the binder's top edge
    GROW = 40 / 28  # the tabs get bigger
    LEAD = 72  # and start further in from the left
    WAIT_MS, MOVE_MS = 150, 620

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window_ = window
        self.at = 0.0  # 0: the binder as it is; 1: tipped all the way back
        self.open_ = False  # where it is going
        self.keys = False  # opened with Ctrl+Tab: only the keyboard closes it
        self.pick = -1  # the tab under the mouse, or picked with the keys
        self.pressed: object = None
        self.focus_was: QWidget | None = None
        self.veil: QGraphicsOpacityEffect | None = None
        self.band_picture = QPixmap()
        self.strip_picture = QPixmap()
        self.setMouseTracking(True)
        self.hide()
        self.wait = QTimer(self)
        self.wait.setSingleShot(True)
        self.wait.setInterval(self.WAIT_MS)
        self.wait.timeout.connect(self.open)
        self.move_ = glide(self, self.MOVE_MS, self._tip_to)
        self.move_.finished.connect(self._arrived)
        # Mouse tracking: Qt then sends moves with no button held too.
        for watched in (window.centralWidget(), window.topbar, window.binder):
            watched.setMouseTracking(True)
            watched.installEventFilter(self)
        window.installEventFilter(self)

    # ------------------------------------------------------------ opening
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        window = self.window_
        if watched is window:
            if kind in (QEvent.Type.Resize, QEvent.Type.WindowDeactivate):
                self.shut()
        elif kind == QEvent.Type.MouseMove and not event.buttons():  # type: ignore[attr-defined]
            if self._opens(event.globalPosition().toPoint()):  # type: ignore[attr-defined]
                if not self.wait.isActive():
                    self.wait.start()
            else:
                self.wait.stop()
        elif kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
            self.wait.stop()
        return False

    def _opens(self, on_screen: QPoint) -> bool:
        """Whether the mouse is where the top view opens from: above the
        top row or on its empty part, never on a tab or a button. A move a
        tab doesn't use is passed on to the row it is on, so it comes by
        here more than once: what counts is the part right under the mouse."""
        window = self.window_
        if self.open_ or not window.binder.isVisible() or not animated(window):
            return False
        pos = window.mapFromGlobal(on_screen)
        under = window.childAt(pos)
        if under is window.centralWidget():
            return pos.y() < self._top()
        return under is window.topbar

    def open(self, keys: bool = False) -> None:
        window = self.window_
        if self.open_ or not window.binder.isVisible():
            return
        self.open_, self.keys = True, keys
        self.pick = window.binder.currentIndex() if keys else -1
        self.focus_was = window.focusWidget()
        self.setGeometry(window.rect())
        self.veil = QGraphicsOpacityEffect(window.centralWidget())
        self.veil.setOpacity(0.0)
        window.centralWidget().setGraphicsEffect(self.veil)
        self.show()
        self.raise_()
        if keys:
            self.setFocus()
        self._go(1.0)

    def close_(self) -> None:
        self.open_ = self.keys = False
        self.wait.stop()
        if self.isVisible():
            self._go(0.0)

    def shut(self) -> None:
        """Closed at once, with no move."""
        self.open_ = self.keys = False
        self.wait.stop()
        self.move_.stop()
        if self.isVisible():
            self._tip_to(0.0)
            self._arrived()

    def _go(self, to: float) -> None:
        self.move_.stop()
        if animated(self.window_) and self.at != to:
            self.move_.setStartValue(self.at)
            self.move_.setEndValue(to)
            self.move_.start()
        else:
            self._tip_to(to)
            self._arrived()

    def _tip_to(self, at: float) -> None:
        self.at = at
        # New pictures each step, so a server picked here shows while the
        # binder comes back up. The real binder is drawn for a moment for it.
        window = self.window_
        if self.veil is not None:
            self.veil.setEnabled(False)
        self.band_picture = window.band.grab()
        self.strip_picture = window.strip.grab()
        if self.veil is not None:
            self.veil.setEnabled(True)
        self.update()

    def _arrived(self) -> None:
        if self.open_:
            return
        self.hide()
        self.veil = None
        # Qt deletes the old one.
        self.window_.centralWidget().setGraphicsEffect(None)  # type: ignore[arg-type]
        if self.focus_was is not None and isValid(self.focus_was):
            self.focus_was.setFocus()
        self.focus_was = None

    # ------------------------------------------------------------ picking
    def step(self, by: int) -> None:
        """Ctrl+Tab and Ctrl+Shift+Tab: the next or the previous tab. Letting
        go of Ctrl opens it."""
        count = self.window_.binder.count()
        if not self.window_.binder.isVisible() or not count:
            return
        if not self.open_:
            self.open(keys=True)
        elif not self.keys:
            self.keys = True
            self.pick = self.window_.binder.currentIndex()
            self.setFocus()
        self.pick = (self.pick + by) % count
        self.update()

    def choose(self, index: int) -> None:
        window = self.window_
        if 0 <= index < window.binder.count():
            window.binder_clicked(index)  # the open tab, from the bell's or the gear's screen
            window.binder.setCurrentIndex(index)
        self.close_()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.choose(self.pick)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.step(-1 if key == Qt.Key.Key_Left else 1)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Control and self.keys:
            self.choose(self.pick)
        else:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if self.keys:
            self.close_()

    def mouseMoveEvent(self, event) -> None:
        if self.keys or not self.open_:
            return
        pos = event.position()
        if pos.y() > self._top() + self._down(1.0) + self.KEEP:
            self.close_()
            return
        under = self.tab_at(pos)
        if under != self.pick:
            self.pick = under
            self.update()

    def leaveEvent(self, event) -> None:
        if not self.keys:
            self.close_()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        index = self.tab_at(pos)
        self.pressed = index if index >= 0 else self.button_at(pos)
        if self.pressed is None:
            # A press on nothing moves the window, as it does on the top row.
            self.shut()
            if self.window_.frameless:
                self.window_.windowHandle().startSystemMove()

    def mouseReleaseEvent(self, event) -> None:
        pressed, self.pressed = self.pressed, None
        pos = event.position()
        if isinstance(pressed, int):
            if pressed == self.tab_at(pos):
                self.choose(pressed)
        elif isinstance(pressed, QAbstractButton) and pressed is self.button_at(pos):
            self.close_()
            pressed.click()

    # ------------------------------------------------------------ where things are
    def _top(self) -> float:
        """The band's top edge, in the window: the line the binder tips about."""
        window = self.window_
        return float(window.band.mapTo(window, window.band.rect().topLeft()).y())

    def _down(self, at: float) -> float:
        return min(self.DOWN, 0.32 * self.height()) * at

    def _tipped(self, x: float, y: float, z: float) -> QPointF:
        """A point of the binder on the screen. ``y`` is down from the top
        line along the binder, ``z`` out of it toward the person. Tipping
        back by an angle turns (y, z) by it, like turning a point around
        the x axis."""
        tilt = math.radians(self.TILT * self.at)
        eye = Eye(QPointF(self.width() / 2, 0.08 * self.height()), 1800)
        return eye.see(
            x,
            self._top() + self._down(self.at) + y * math.cos(tilt) + z * math.sin(tilt),
            -y * math.sin(tilt) + z * math.cos(tilt),
        )

    def _in_window(self, part: QWidget) -> QRectF:
        return QRectF(part.rect()).translated(part.mapTo(self.window_, part.rect().topLeft()))

    def _step(self) -> float:
        """Between two dividers. Less than STEP when there are so many
        servers that the last one would be above the window."""
        count = self.window_.binder.count()
        room = self._top() + self._down(1.0) - 40 * self.GROW
        return min(self.STEP, room / max(1, count - 1) / math.sin(math.radians(self.TILT)))

    def _depth(self, index: int) -> int:
        """How many steps back a tab stands: the open one is in front, then
        the ones to its right, then around to the ones on its left."""
        binder = self.window_.binder
        return (index - binder.currentIndex()) % max(1, binder.count())

    def _grow(self) -> float:
        binder = self._in_window(self.window_.binder)
        strip = self._in_window(self.window_.strip)
        fits = (strip.left() - binder.left() - self.LEAD - 12) / max(1.0, binder.width())
        return 1 + (max(1.0, min(self.GROW, fits)) - 1) * self.at

    def tab_onto(self, index: int) -> QTransform:
        """Draws tab ``index``, given in the tab bar's own coordinates,
        where it stands in the top view."""
        binder = self.window_.binder
        bar = self._in_window(binder)
        rect = QRectF(binder.tabRect(index))
        grow = self._grow()
        stand = math.radians(self.STAND * self.at)
        back = -self._step() * self._depth(index) * self.at

        def spot(x: float, y: float) -> QPointF:
            up = (bar.height() - y) * grow  # above the line the tabs stand on
            return self._tipped(
                bar.left() + self.LEAD * self.at + x * grow,
                -up * math.cos(stand),
                back - up * math.sin(stand),
            )

        corners = [
            spot(x, y)
            for x, y in (
                (rect.left(), rect.top()),
                (rect.right(), rect.top()),
                (rect.right(), rect.bottom()),
                (rect.left(), rect.bottom()),
            )
        ]
        return onto(rect, corners)

    def flat_onto(self, part: QWidget) -> QTransform:
        """Draws the band or the tool strip, given from its own top left
        corner, tipped back with the binder."""
        rect = self._in_window(part)
        above = rect.top() - self._top()
        corners = [
            self._tipped(x, y, 0)
            for x, y in (
                (rect.left(), above),
                (rect.right(), above),
                (rect.right(), above + rect.height()),
                (rect.left(), above + rect.height()),
            )
        ]
        return onto(QRectF(part.rect()), corners)

    def tab_at(self, pos: QPointF) -> int:
        binder = self.window_.binder
        front_first = sorted(range(binder.count()), key=self._depth)
        for index in front_first:
            back, ok = self.tab_onto(index).inverted()
            if ok and QRectF(binder.tabRect(index)).contains(back.map(pos)):
                return index
        return -1

    def button_at(self, pos: QPointF) -> QAbstractButton | None:
        strip = self.window_.strip
        back, ok = self.flat_onto(strip).inverted()
        found = strip.childAt(back.map(pos).toPoint()) if ok else None
        return found if isinstance(found, QAbstractButton) else None

    # ------------------------------------------------------------ drawing
    def paintEvent(self, event) -> None:
        window = self.window_
        look = window.look
        binder = window.binder
        painter = QPainter(self)
        # Almost nothing, but enough that Windows gives this the mouse.
        painter.fillRect(
            self.rect(), QColor(0, 0, 0, 1) if window.see_through else ink(look["desk"])
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._paint_top(painter)
        binder.lit, binder.lit_ring = self.pick, self.keys
        for index in sorted(range(binder.count()), key=self._depth, reverse=True):
            painter.setTransform(self.tab_onto(index))
            if self._depth(index):
                # The top edge of this tab's own divider, a little wider than the tab.
                foot = QRectF(binder.tabRect(index)).adjusted(-20, 0, 20, 0)
                foot.setTop(foot.bottom() - 4)
                painter.setOpacity(self.at)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(binder.fill_of(index))
                painter.drawRoundedRect(foot, 2, 2)
                painter.setOpacity(1.0)
            binder.paint_tab(
                painter, index, index == binder.currentIndex() and not binder.none_open
            )
        binder.lit = -1
        self._paint_shadow(painter)
        for part, picture in ((window.band, self.band_picture), (window.strip, self.strip_picture)):
            painter.setTransform(self.flat_onto(part))
            painter.drawPixmap(QRectF(part.rect()), picture, QRectF(picture.rect()))

    def _paint_top(self, painter: QPainter) -> None:
        """The top of the pages, seen from above: paper edges, and across
        them each server's divider in its color, where its tab stands."""
        window = self.window_
        look = window.look
        binder = window.binder
        band = self._in_window(window.band).adjusted(10, 0, -10, 0)
        step = self._step()
        deep = step * (binder.count() - 1) + 24

        def line(back: float) -> list[QPointF]:
            return [self._tipped(band.left(), 0, -back), self._tipped(band.right(), 0, -back)]

        painter.setOpacity(self.at)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink(look["paper"]))
        near, far = line(0), line(deep)
        painter.drawPolygon(QPolygonF([*near, far[1], far[0]]))
        painter.setOpacity(self.at * 0.45)
        painter.setPen(QPen(ink(look["neutral"]), 1))
        for edge in range(1, int(deep // 14) + 1):
            painter.drawLine(*line(edge * 14))
        painter.setPen(QPen(ink(look["neutral"]), 2))
        painter.setOpacity(self.at)
        for depth in range(1, binder.count()):
            painter.drawLine(*line(step * depth * self.at))
        painter.setOpacity(1.0)

    def _paint_shadow(self, painter: QPainter) -> None:
        """A soft shadow under the tipped band, darker toward it."""
        band = QRectF(self.window_.band.rect())
        painter.setTransform(self.flat_onto(self.window_.band))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, round(5 * self.at)))
        for grow in range(4, 28, 4):
            painter.drawRoundedRect(band.adjusted(-grow / 2, 0, grow / 2, grow), 14, 14)
