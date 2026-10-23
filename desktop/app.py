"""Start the desktop app: find the agent on this PC from its pairing file,
then open the window on the sign-in screen."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import icons
from .agent import AgentClient, AgentError, local_pairing
from .connect import ConnectWindow, agent_for, read_link
from .pc import WindowsPC
from .window import MainWindow


def warn(words: str) -> None:
    QMessageBox(QMessageBox.Icon.Warning, "Command Block", words).exec()


def open_after_update(pc: WindowsPC, argv: list[str]) -> bool:
    """The update closed the app (MainWindow.close_for_update): open it
    again only if it was open then, shown or hidden as it was."""
    was = pc.setting("reopen_after_update")
    if not was:
        return False
    pc.set_setting("reopen_after_update", "")
    if was == "hidden":
        argv.append("--hidden")
    return True


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Command Block")
    app.setWindowIcon(icons.tray_icon())
    # Closing the window keeps the app in the notification area; Quit ends it.
    app.setQuitOnLastWindowClosed(False)
    pc = WindowsPC()
    if "--after-update" in sys.argv and not open_after_update(pc, sys.argv):
        return 0
    pairing = local_pairing()
    windows: list = []

    def open_window(agent: AgentClient) -> None:
        window = MainWindow(agent, pc)
        windows.append(window)
        if "--hidden" not in sys.argv:  # Windows starting it (the Run entry) keeps it hidden
            window.show()

    try:
        if pairing is not None:
            # The agent on this PC always listens on 127.0.0.1, even without Tailscale.
            open_window(
                AgentClient.connect(
                    "127.0.0.1",
                    pairing["port"],
                    pairing.get("fingerprint"),
                    pairing.get("tls", True),
                )
            )
        else:
            problem = ""
            if pc.setting("pc_link"):  # someone else's PC, connected to before
                try:
                    open_window(agent_for(read_link(pc.setting("pc_link"))))
                    return app.exec()
                except AgentError as exc:
                    # Its certificate changed, say: ask for a new link here,
                    # and keep the old one until a new one works.
                    problem = str(exc)
            # No agent here (or it hasn't written its pairing file yet).
            first = ConnectWindow(pc, open_window)
            first.say(problem)
            windows.append(first)
            first.show()
    except AgentError as exc:
        warn(str(exc))
        return 1
    return app.exec()


def run() -> None:
    """Runs the app, then ends the process at once. Not sys.exit: a backup or
    restore can hold a thread for minutes waiting for the agent's answer, and
    Python would wait for it before closing. The agent finishes it either way."""
    code = main()
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    run()
