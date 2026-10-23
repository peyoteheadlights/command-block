"""The bell's Notifications screen: the newest 200 alerts, newest first.
Opening one goes to its server, on the page it's about."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .ui import clock, label
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

KEPT = 200  # as many as the agent keeps

# The agent's alert page (agent/notifications/dispatcher.py ALERT_PAGES) to
# the desktop app's page. Anything else opens the server's Overview.
PAGES = {
    "backups": "Backups",
    "mods": "Mods",
    "players": "Players and chat",
    "performance": "Performance",
    "game-settings": "Settings",
}
# Alerts whose Windows alert has a Restart button (for someone who may).
RESTART_FOR = {"server_crashed", "crash_loop", "low_tps", "high_mspt"}
# About the PC, not one server: the gear's section for each.
PC_PAGES = {"security": "devices", "helpers": "moderators", "app-settings": "pc"}


class NotificationsScreen(QWidget):
    NAME = "Notifications"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        heading = label(text("mobile.alerts.title"))
        heading.setObjectName("title")
        outer.addWidget(heading)
        self.empty = label(text("mobile.alerts.empty"), "sub")
        outer.addWidget(self.empty)
        self.list = QListWidget()
        self.list.setAccessibleName(text("mobile.alerts.title"))
        self.list.setWordWrap(True)
        self.list.itemActivated.connect(
            lambda item: window.open_alert(item.data(Qt.ItemDataRole.UserRole))
        )
        self.list.itemClicked.connect(
            lambda item: window.open_alert(item.data(Qt.ItemDataRole.UserRole))
        )
        outer.addWidget(self.list, 1)
        self.fill([], {})

    def fill(self, alerts: list[dict], names: dict[str, str]) -> None:
        self.list.clear()
        for alert in alerts:
            where = names.get(alert.get("server_id") or "", text("mobile.alerts.pc"))
            item = QListWidgetItem(
                f"{clock(alert.get('ts'))}  {where}\n{alert['title']}\n{alert['body']}"
            )
            item.setData(Qt.ItemDataRole.UserRole, alert)
            self.list.addItem(item)
        self.list.setCurrentRow(-1)
        self.empty.setVisible(not alerts)
        self.list.setVisible(bool(alerts))

    def items(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]
