"""The main window without Windows' frame, so only the binder shows on the
person's own desktop.

Qt can drop the title bar (``FramelessWindowHint``) and show the desktop
through whatever isn't painted (``WA_TranslucentBackground``). Windows then
stops helping with a window like that, so this file puts back what a title
bar gives: moving, resizing from the edges, snapping, and the minimize,
maximize and close buttons. Over Remote Desktop a see-through window draws
slowly, so there the window keeps its own buttons but is plain grey behind
the binder.
"""

from __future__ import annotations

import ctypes
import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QMargins, QPoint, QSize, Qt
from PySide6.QtWidgets import QMainWindow, QToolButton, QWidget

from . import icons
from .words import text

if TYPE_CHECKING:
    from .pc import WindowsPC

FRAMELESS = True  # False gives the window Windows' own title bar back
ROOM = QMargins(12, 8, 12, 12)  # around the binder, for its shadow
GRIP = 6  # how far in from the binder's edge the mouse resizes
LEFT, RIGHT, TOP, BOTTOM = (
    Qt.Edge.LeftEdge,
    Qt.Edge.RightEdge,
    Qt.Edge.TopEdge,
    Qt.Edge.BottomEdge,
)
CURSORS = {
    LEFT: Qt.CursorShape.SizeHorCursor,
    RIGHT: Qt.CursorShape.SizeHorCursor,
    TOP: Qt.CursorShape.SizeVerCursor,
    BOTTOM: Qt.CursorShape.SizeVerCursor,
    TOP | LEFT: Qt.CursorShape.SizeFDiagCursor,
    BOTTOM | RIGHT: Qt.CursorShape.SizeFDiagCursor,
    TOP | RIGHT: Qt.CursorShape.SizeBDiagCursor,
    BOTTOM | LEFT: Qt.CursorShape.SizeBDiagCursor,
}

WORDS = {
    "minimize": "desktop.window.minimize",
    "maximize": "desktop.window.maximize",
    "restore": "desktop.window.restore",
    "close": "action.close",
}

if sys.platform == "win32":
    from ctypes import wintypes

    # Windows' own numbers (winuser.h).
    GWL_STYLE = -16
    SNAP_STYLES = (
        0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000
    )  # caption, thick frame, min, max
    WM_GETMINMAXINFO, WM_NCCALCSIZE = 0x0024, 0x0083
    FRAME_CHANGED = 0x0001 | 0x0002 | 0x0004 | 0x0020  # no size, move or order change
    NEAREST_SCREEN = 2

    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]

    class MinMaxInfo(ctypes.Structure):
        _fields_ = [
            ("reserved", wintypes.POINT),
            ("max_size", wintypes.POINT),
            ("max_position", wintypes.POINT),
            ("min_track", wintypes.POINT),
            ("max_track", wintypes.POINT),
        ]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("monitor", wintypes.RECT),
            ("work", wintypes.RECT),
            ("flags", wintypes.DWORD),
        ]

    def add_snap_styles(hwnd: int) -> None:
        """Qt leaves these styles off a frameless window, and Windows looks
        at them before it snaps one (Win+arrow keys, dragging to a screen
        edge) or plays the minimize animation."""
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        if style & SNAP_STYLES != SNAP_STYLES:
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | SNAP_STYLES)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, FRAME_CHANGED)

    def frame_message(window: FramelessWindow, address: int) -> bool:
        """Answers the two messages that keep the frame those styles bring
        from showing. True when the message is dealt with."""
        msg = wintypes.MSG.from_address(address)
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            return True  # the whole window is ours: no title bar, no border
        if msg.message == WM_GETMINMAXINFO:
            # Maximized means exactly the screen's work area. Windows would
            # put a window with a frame 8 px over every edge.
            info = MinMaxInfo.from_address(msg.lParam)
            found = MonitorInfo()
            found.size = ctypes.sizeof(MonitorInfo)
            screen = user32.MonitorFromWindow(msg.hWnd, NEAREST_SCREEN)
            user32.GetMonitorInfoW(screen, ctypes.byref(found))
            info.max_position.x = found.work.left - found.monitor.left
            info.max_position.y = found.work.top - found.monitor.top
            info.max_size.x = found.work.right - found.work.left
            info.max_size.y = found.work.bottom - found.work.top
            scale = window.devicePixelRatioF()
            least = window.minimumSizeHint().expandedTo(window.minimumSize())
            info.min_track.x = round(least.width() * scale)
            info.min_track.y = round(least.height() * scale)
            return True
        return False


class Grip(QWidget):
    """An unseen strip along one edge or corner of the binder. Pressing it
    hands the resize to Windows from that edge."""

    def __init__(self, window: FramelessWindow, edges: Qt.Edge) -> None:
        super().__init__(window)
        self.edges = edges
        self.setCursor(CURSORS[edges])

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().windowHandle().startSystemResize(self.edges)


class FramelessWindow(QMainWindow):
    """A main window that is its own title bar. The window that builds on
    it puts ``window_buttons`` somewhere, says where it can be dragged
    (``can_drag``) and calls ``show_frame_look`` when the colors change."""

    def __init__(self, pc: WindowsPC) -> None:
        super().__init__()
        self.look: dict[str, str] = {}
        self.frameless = FRAMELESS
        self.see_through = FRAMELESS and not pc.remote_desktop()
        self.grips: list[Grip] = []
        self.window_buttons: dict[str, QToolButton] = {}
        if not self.frameless:
            return
        # Still a minimize and maximize box, so a click on the taskbar
        # button minimizes the window.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        if self.see_through:
            # Before the window exists: Windows then draws only what we paint.
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.grips = [Grip(self, edges) for edges in CURSORS]
        for name, pressed in (
            ("minimize", self.showMinimized),
            ("maximize", self.toggle_maximized),
            ("close", self.close),
        ):
            button = QToolButton()
            button.setObjectName(name)
            button.setIconSize(QSize(20, 20))
            button.setAutoRaise(True)  # so the close button's icon changes under the mouse
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.clicked.connect(pressed)
            self.window_buttons[name] = button

    def show_frame_look(self, look: dict[str, str]) -> None:
        """The window buttons' icons and names, in the look's colors."""
        full = self.isMaximized()
        for name, button in self.window_buttons.items() if look else ():
            shape = "restore" if name == "maximize" and full else name
            words = text(WORDS[shape])
            button.setToolTip(words)
            button.setAccessibleName(words)
            hover = look["on-danger"] if name == "close" else ""
            button.setIcon(icons.icon(shape, look["text-primary"], 20, hover=hover))

    def tab_to_window_buttons_last(self) -> None:
        """Tab reaches the window's buttons after everything else, so a
        screen starts on its own first field and not on minimize. Call it
        once the window's screens are built."""
        last: QWidget = self.previousInFocusChain() or self
        # Qt only orders widgets that can take the keyboard.
        while last is not self and last.focusPolicy() == Qt.FocusPolicy.NoFocus:
            last = last.previousInFocusChain() or self
        for button in self.window_buttons.values():
            if button is not last:
                QWidget.setTabOrder(last, button)
            last = button

    def toggle_maximized(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def can_drag(self, pos: QPoint) -> bool:
        """Whether a press here, that no button or field took, moves the window."""
        return True

    # ------------------------------------------------------------ events
    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self.frameless and event.button() == Qt.MouseButton.LeftButton and self.can_drag(pos):
            # Windows moves the window from here on, which is what makes
            # dragging it to a screen edge snap.
            self.windowHandle().startSystemMove()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.frameless and self.can_drag(event.position().toPoint()):
            self.toggle_maximized()
        else:
            super().mouseDoubleClickEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if self.frameless and event.type() == QEvent.Type.WindowStateChange:
            self.place_frame()
            self.show_frame_look(self.look)  # maximize turns into restore

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.place_frame()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.frameless and sys.platform == "win32":
            add_snap_styles(int(self.winId()))

    def nativeEvent(self, kind, message):
        if self.frameless and sys.platform == "win32" and frame_message(self, int(message)):
            return True, 0
        return False, 0

    def place_frame(self) -> None:
        """Room around the binder for its shadow (none when maximized), and
        the grips along the binder's edges."""
        desk = self.centralWidget()
        around = desk.layout() if desk is not None else None
        if not self.frameless or around is None:
            return
        full = self.isMaximized()
        around.setContentsMargins(QMargins() if full and self.see_through else ROOM)
        binder = around.contentsRect().translated(desk.pos())
        g = GRIP
        across, down = binder.width() - 4 * g, binder.height() - 4 * g
        left, top = binder.left(), binder.top()
        right, bottom = binder.right() + 1 - g, binder.bottom() + 1 - g
        places = {
            LEFT: (left, top + 2 * g, g, down),
            RIGHT: (right, top + 2 * g, g, down),
            TOP: (left + 2 * g, top, across, g),
            BOTTOM: (left + 2 * g, bottom, across, g),
            TOP | LEFT: (left, top, 2 * g, 2 * g),
            TOP | RIGHT: (right - g, top, 2 * g, 2 * g),
            BOTTOM | LEFT: (left, bottom - g, 2 * g, 2 * g),
            BOTTOM | RIGHT: (right - g, bottom - g, 2 * g, 2 * g),
        }
        for grip in self.grips:
            grip.setGeometry(*places[grip.edges])
            grip.setVisible(not full)
            grip.raise_()
