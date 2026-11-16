"""The binder's own parts, drawn by hand: the desk behind it, the server
tabs across the top, the sheet of paper, and a server's page list with the
marker that slides to the open page.

A Qt style sheet can't give each tab its own color or move a marker, so
these paint themselves with QPainter (like Graphics2D in Java). The colors
all come from ``theme.tokens``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent import colors

from . import icons
from .parts import animated, glide

ROUND = 10  # the tabs' top corners, and the open tab's feet
EDGE = 1.5  # the line around the paper


def _painter(widget: QWidget) -> QPainter:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    return painter


class Desk(QWidget):
    """What is behind the binder. In a see-through window that is the
    person's own desktop, so this paints almost nothing; over Remote
    Desktop it is the theme's plain grey (the style sheet's ``#desk``)."""

    def __init__(self, see_through: bool) -> None:
        super().__init__()
        self.see_through = see_through
        self.band: QWidget | None = None
        self.setObjectName("desk")
        if not see_through:
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def paintEvent(self, event) -> None:
        if not self.see_through or self.band is None:
            return
        painter = _painter(self)
        band = QRectF(self.band.geometry())
        # The row above the band looks empty but takes clicks, so the window
        # can be dragged by it: Windows lets a click fall through a pixel
        # only when nothing at all is painted there.
        painter.fillRect(QRectF(0, 0, self.width(), band.top()), QColor(0, 0, 0, 1))
        around = self.layout()
        room = around.contentsMargins().bottom() if around else 0
        for step in range(room):  # a soft shadow, darker toward the band
            grow = room - step
            painter.setBrush(QColor(0, 0, 0, 2 + step // 2))
            painter.drawRoundedRect(
                band.adjusted(-grow, -grow / 2, grow, grow), 14 + grow, 14 + grow
            )


class BinderTabs(QTabBar):
    """The server tabs: All servers, one per server in its own color, and
    "+". Still a QTabBar, so the arrow keys, Narrator and the scroll arrows
    work as before; only the drawing is ours.

    ``looks`` has each server's colors by its id, ``running`` the ids with a
    green dot. The open tab takes the window's band color, so it runs into
    the band under it.
    """

    ICONS = {"all": "servers", "new": "plus"}
    LEAD = ROUND  # room left of the first tab for the open tab's foot
    GAP, PAD, DOT, ICON = 3, 18, 8, 16

    def __init__(self) -> None:
        super().__init__()
        self.look: dict[str, str] = {}
        self.looks: dict[str, dict[str, str]] = {}
        self.running: set[str] = set()
        self.none_open = False  # the screen shown belongs to no tab (PC settings, Help)
        # The top view's pick: a little brighter, and with Ctrl+Tab ringed.
        self.lit, self.lit_ring = -1, False
        # Tabs come up under the mouse: the one it is on most, the ones next
        # to it a little. ``rise`` has each tab's extra height right now.
        self.under = -1
        self.rise: dict[int, float] = {}
        self._rise_from: dict[int, float] = {}
        self._rising = glide(self, 300, self._rise_to)
        self.setMouseTracking(True)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self._hover(self.tabAt(event.position().toPoint()))

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hover(-1)

    def _hover(self, index: int) -> None:
        if index == self.under:
            return
        self.under = index
        self._rising.stop()
        self._rise_from = dict(self.rise)
        if animated(self):
            self._rising.start()
        else:
            self.rise = {}

    def _rise_to(self, at: float) -> None:
        ends = (
            {self.under: 10.0, self.under - 1: 5.0, self.under + 1: 5.0} if self.under >= 0 else {}
        )
        self.rise = {}
        for index in ends.keys() | self._rise_from.keys():
            was = self._rise_from.get(index, 0.0)
            self.rise[index] = was + (ends.get(index, 0.0) - was) * at
        self.update()

    def refit(self) -> None:
        """Measures the tabs again. Qt sizes a tab when it is added, which
        is before it knows which server the tab is for."""
        self.setIconSize(self.iconSize())

    def heights(self) -> tuple[int, int]:
        """A tab and the open tab, which stands a little taller. They grow
        with Windows' text size."""
        tab = max(28, self.fontMetrics().height() + 10)
        return tab, tab + 6

    def _font(self, open_: bool) -> QFont:
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold if open_ else QFont.Weight.Medium)
        return font

    def tabSizeHint(self, index: int) -> QSize:
        which = self.tabData(index)
        words = self.tabText(index)
        wide = 2 * self.PAD + self.GAP + (self.LEAD if index == 0 else 0)
        if words:  # measured in the open tab's weight, so opening a tab moves nothing
            wide += QFontMetrics(self._font(True)).horizontalAdvance(words)
        if which in self.ICONS:
            wide += self.ICON + (8 if words else 0)
        elif which in self.running:
            wide += self.DOT + 8
        return QSize(wide, self.heights()[1] + 6)

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def paintEvent(self, event) -> None:
        if not self.look:
            return
        painter = _painter(self)
        open_ = -1 if self.none_open else self.currentIndex()
        for index in range(self.count()):
            if index != open_:
                self.paint_tab(painter, index, False)
        if open_ >= 0:
            self.paint_tab(painter, open_, True)

    def look_of_tab(self, index: int) -> dict[str, str]:
        # All servers and "+" stay neutral, whichever server is open.
        return self.looks.get(self.tabData(index)) or {
            "tab-fill": self.look["tab-neutral"],
            "tab-text": self.look["text-primary"],
        }

    def fill_of(self, index: int, color: str = "") -> QColor:
        """A tab's color: its server's, unless the open tab's ``color`` is given."""
        fill = QColor(color or self.look_of_tab(index)["tab-fill"])
        return fill.lighter(108) if index == self.lit else fill

    def paint_tab(self, painter: QPainter, index: int, open_: bool) -> None:
        which = self.tabData(index)
        look = self.look_of_tab(index)
        fill = self.fill_of(index, self.look["band"] if open_ else "")
        ink = self.look["band-text"] if open_ else look["tab-text"]
        rect = self.tabRect(index)
        tall = max(self.heights()[open_], self.heights()[0] + round(self.rise.get(index, 0.0)))
        left = rect.left() + (self.LEAD if index == 0 else 0)
        right, bottom = rect.right() + 1 - self.GAP, float(self.height())
        top = bottom - tall
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        # Round at the top only: the rest of the rounding is below the bar.
        painter.drawRoundedRect(QRectF(left, top, right - left, tall + ROUND), ROUND, ROUND)
        if open_:
            # Two small feet curve the open tab out into the band.
            for at, start, sweep in ((left, 270, 90), (right, 270, -90)):
                side = -1 if sweep > 0 else 1
                foot = QPainterPath(QPointF(at, bottom))
                foot.lineTo(at + side * ROUND, bottom)
                circle = QRectF(
                    min(at, at + side * 2 * ROUND), bottom - 2 * ROUND, 2 * ROUND, 2 * ROUND
                )
                foot.arcTo(circle, start, sweep)
                foot.closeSubpath()
                painter.drawPath(foot)
        if (self.hasFocus() and open_) if self.lit < 0 else (self.lit_ring and index == self.lit):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(self.look["accent-ring"]), 2))
            painter.setClipRect(QRectF(left, top, right - left, tall))
            painter.drawRoundedRect(QRectF(left + 2, top + 2, right - left - 4, tall + ROUND), 8, 8)
            painter.setClipping(False)
        x = left + self.PAD
        middle = top + tall / 2
        if which in self.ICONS:
            size = self.ICON
            picture = icons.icon(self.ICONS[which], ink, size)
            picture.paint(painter, QRect(int(x), int(middle - size / 2), size, size))
            x += size + 8
        elif which in self.running:
            # The dot stays green on any tab color, so it gets a pale ring.
            ring = QColor(self.look["paper"])
            ring.setAlphaF(0.7)
            painter.setPen(QPen(ring, 2))
            painter.setBrush(QColor(self.look["success"]))
            painter.drawEllipse(QPointF(x + self.DOT / 2, middle), self.DOT / 2, self.DOT / 2)
            x += self.DOT + 8
        painter.setPen(QColor(ink))
        painter.setFont(self._font(open_))
        painter.drawText(
            QRectF(x, top, right - x, tall),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.tabText(index),
        )


class Sheet(QFrame):
    """The page: paper lying on the band, with the edges of two more pages
    showing under it. A server's page list goes in its left margin, and the
    screens go in ``inside``. ``bare`` leaves the paper out (sign-in and the
    whole-window notices bring their own small sheet)."""

    INSET = 12  # between the band's left edge and the paper, with no page list
    PAGES = ((6, 5, 0.55), (3, 3, 0.8))  # (in from the sides, out below, share of paper)

    def __init__(self) -> None:
        super().__init__()
        self.look: dict[str, str] = {}
        self.bare = False
        self.page_list: QWidget | None = None
        self.row = QHBoxLayout(self)
        self.row.setSpacing(0)
        holder = QWidget()
        self.inside = QVBoxLayout(holder)
        self.inside.setContentsMargins(2, 2, 2, 2)  # inside the paper's edge line
        self.row.addWidget(holder, 1)
        self.arrange()

    def set_list(self, page_list: QWidget) -> None:
        self.page_list = page_list
        self.row.insertWidget(0, page_list)
        self.arrange()

    def listed(self) -> bool:
        return self.page_list is not None and not self.page_list.isHidden()

    def arrange(self) -> None:
        """Room for the paper's left edge, and under it for the page edges."""
        self.row.setContentsMargins(0 if self.listed() else self.INSET, 0, 0, self.PAGES[0][1] + 1)
        self.update()

    def paper(self) -> QRectF:
        margins = self.row.contentsMargins()
        # The page list's tab reaches 2 px over the paper's edge line, so
        # the open page's tab and the paper read as one piece.
        left = self.page_list.width() - 2 if self.listed() and self.page_list else margins.left()
        return QRectF(left, 0, self.width() - left, self.height() - margins.bottom()).adjusted(
            EDGE / 2, EDGE / 2, -EDGE / 2, -EDGE / 2
        )

    def paintEvent(self, event) -> None:
        if self.bare or not self.look:
            return
        painter = _painter(self)
        paper = self.paper()
        white, band = colors.rgb(self.look["paper"]), colors.rgb(self.look["band"])
        assert white and band
        for inward, below, share in self.PAGES:
            painter.setBrush(QColor(colors.to_hex(colors.mix(band, white, share))))
            painter.drawRoundedRect(paper.adjusted(inward, 0, -inward, below), 12, 12)
        painter.setBrush(QColor(self.look["paper"]))
        painter.setPen(QPen(QColor(self.look["border-strong"]), EDGE))
        painter.drawRoundedRect(paper, 12, 12)


class _Tabs(QWidget):
    """The page list's buttons, and the marker painted behind the open
    page's button (or on its way there, at ``sliding``)."""

    def __init__(self) -> None:
        super().__init__()
        self.color = ""
        self.open: QAbstractButton | None = None
        self.sliding: float | None = None

    def paintEvent(self, event) -> None:
        if self.open is None or not self.color:
            return
        top = self.open.y() if self.sliding is None else self.sliding
        marker = QRectF(self.open.x(), top, self.width() - self.open.x(), self.open.height())
        painter = _painter(self)
        painter.setBrush(QColor(self.color))
        # Round on the left only: the right side runs into the paper.
        painter.setClipRect(marker)
        painter.drawRoundedRect(marker.adjusted(0, 0, 9, 0), 9, 9)


class PageList(QScrollArea):
    """A server's pages, listed down the sheet's left edge with flat text.
    The open page's tab is a piece of the paper, and it slides to the page
    that is clicked. With big text the list scrolls instead of cutting off.
    """

    LEFT, TOP, GAP = 16, 20, 3
    SLIDE_MS = 460

    def __init__(self, animated: Callable[[], bool]) -> None:
        super().__init__()
        self.animated = animated
        self.look: dict[str, str] = {}
        self.setObjectName("pageList")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.tabs = _Tabs()
        self.column = QVBoxLayout(self.tabs)
        self.column.setContentsMargins(self.LEFT, self.TOP, 0, 0)
        self.column.setSpacing(self.GAP)
        self.column.addStretch()
        self.setWidget(self.tabs)
        self.group = QButtonGroup(self)
        self.icons: dict[QToolButton, str] = {}
        # A spring: a little past the page, then back (the design's curve).
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        curve.addCubicBezierSegment(QPointF(0.3, 1.36), QPointF(0.44, 1), QPointF(1, 1))
        self.slide = QVariantAnimation(self)
        self.slide.setDuration(self.SLIDE_MS)
        self.slide.setEasingCurve(curve)
        self.slide.valueChanged.connect(self._slide_to)
        self.slide.finished.connect(lambda: self._slide_to(None))

    def fill(self, pages: list[tuple[str, str, str]], open_page: Callable[[str], None]) -> None:
        """The buttons, top to bottom: (page, its words, its icon) each."""
        self.slide.stop()
        self.tabs.open = None
        for button in self.group.buttons():
            self.group.removeButton(button)
            button.setParent(None)
        self.icons.clear()
        for at, (page, words, icon) in enumerate(pages):
            button = QToolButton()
            button.setObjectName("pageTab")
            button.setText(" " + words)
            button.setAccessibleName(words)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setProperty("page", page)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, p=page: open_page(p))
            self.group.addButton(button)
            self.icons[button] = icon
            self.column.insertWidget(at, button)
        self.set_look(self.look)
        for button in self.group.buttons():
            button.ensurePolished()  # so its size counts the style sheet's font
        wide = max((b.sizeHint().width() for b in self.group.buttons()), default=0)
        self.setFixedWidth(max(184, wide + self.LEFT))

    def set_look(self, look: dict[str, str]) -> None:
        self.look = look
        if not look:
            return
        self.tabs.color = look["paper"]
        for button, name in self.icons.items():
            button.setIcon(icons.icon(name, look["band-text"], on=look["accent-ring"]))
        self.tabs.update()

    def mark(self, page: str | None) -> None:
        """Checks ``page``'s button and brings the marker to it. A page with
        no button (Activity) leaves none checked and no marker."""
        self.group.setExclusive(False)
        target = None
        for button in self.group.buttons():
            button.setChecked(button.property("page") == page)
            if button.isChecked():
                target = button
        self.group.setExclusive(True)
        self.slide.stop()
        was, self.tabs.open = self.tabs.open, target
        if target is not None and was is not None and self.animated() and self.isVisible():
            self.column.activate()  # the buttons' places, even before the next paint
            at = self.tabs.sliding  # still on its way from an earlier click
            self.slide.setStartValue(float(was.y() if at is None else at))
            self.slide.setEndValue(float(target.y()))
            self.slide.start()
        else:
            self._slide_to(None)
        if target is not None:
            self.ensureWidgetVisible(target)

    def _slide_to(self, y: float | None) -> None:
        self.tabs.sliding = y
        self.tabs.update()
