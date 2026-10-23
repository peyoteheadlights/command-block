"""Line icons (24x24, stroked) and the server badge.

Drawn with QtSvg in the color asked for, so they follow the theme and the
server's tint.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from agent import colors

IMAGES = Path(__file__).resolve().parent / "images"

DOTS = {"red": "#D93025", "yellow": "#F2B600"}

SHAPES = {
    "overview": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/>',
    "servers": '<rect x="3.5" y="4" width="17" height="6.5" rx="1.5"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="1.5"/><path d="M7 7.25h.01M7 16.75h.01"/>',
    "players": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.6 6.5-5.6s5.7 2 6.5 5.6"/><path d="M15.5 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.6c1.8.7 3 2.6 3.5 5.4"/>',
    "console": '<rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="M7 9.5l3 2.5-3 2.5"/><path d="M12.5 15h4.5"/>',
    "backups": '<rect x="3" y="4" width="18" height="5" rx="1.2"/><path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9"/><path d="M10 13h4"/>',
    "mods": '<path d="M12 2.8l8.5 4.7v9L12 21.2 3.5 16.5v-9z"/><path d="M3.8 7.6 12 12l8.2-4.4"/><path d="M12 12v9"/>',
    "schedules": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "performance": '<path d="M3 12.5h4l2.6-7 4.8 13 2.6-6h4"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/><circle cx="12" cy="12" r="6.2"/>',
    "help": '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4"/><path d="M12 16.8h.01"/>',
    "bell": '<path d="M12 3.5a5.5 5.5 0 0 1 5.5 5.5v3l1.5 3H5l1.5-3V9A5.5 5.5 0 0 1 12 3.5z"/><path d="M9.8 18a2.2 2.2 0 0 0 4.4 0"/>',
    "undo": '<path d="M4 10.5h9.5a5 5 0 1 1 0 10H8"/><path d="M7.5 6 4 10.5 7.5 15"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
}


def icon(name: str, color: str, size: int = 18) -> QIcon:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{SHAPES[name]}</svg>"
    )
    picture = QPixmap(size * 2, size * 2)  # drawn at twice the size for high-DPI screens
    picture.fill(Qt.GlobalColor.transparent)
    picture.setDevicePixelRatio(2)
    painter = QPainter(picture)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QIcon(picture)


def paint_badge(painter: QPainter, rect, color: str, letter: str) -> None:
    fill, text = colors.fill_with_text(colors.rgb(color) or (60, 60, 67))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors.to_hex(fill)))
    radius = rect.height() * 0.22
    painter.drawRoundedRect(rect, radius, radius)
    font = painter.font()
    font.setPixelSize(max(1, int(rect.height() * 0.5)))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(colors.to_hex(text)))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)


def badge_icon(color: str, letter: str, size: int = 18) -> QIcon:
    """A server's icon: its first letter on its color."""
    picture = QPixmap(size * 2, size * 2)
    picture.fill(Qt.GlobalColor.transparent)
    picture.setDevicePixelRatio(2)
    painter = QPainter(picture)
    paint_badge(painter, QRectF(0, 0, size, size), color, letter)
    painter.end()
    return QIcon(picture)


class Badge(QWidget):
    """The big server icon on the header card."""

    def __init__(self, size: int) -> None:
        super().__init__()
        self.color, self.letter = "#8E8E93", ""
        self.setFixedSize(size, size)

    def show_server(self, color: str, letter: str) -> None:
        self.color, self.letter = color, letter
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_badge(painter, QRectF(self.rect()), self.color, self.letter)


def tray_icon(dot: str | None = None) -> QIcon:
    """The app's icon for the notification area, with a red dot while a
    server is down after a crash, or a yellow one while setup is unfinished."""
    picture = QPixmap(str(IMAGES / "icon-192.png")).scaled(
        64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    if dot:
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#FFFFFF"))
        painter.setBrush(QColor(DOTS[dot]))
        painter.drawEllipse(QRectF(36, 36, 26, 26))
        painter.end()
    return QIcon(picture)
