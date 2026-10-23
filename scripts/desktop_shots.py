"""Pictures of the desktop app's screens, in light and dark, to check the design.

    python scripts/desktop_shots.py            (writes ui-shots/desktop/*.png)

It runs the real agent in this process with two pretend Minecraft servers
(the same ones the tests use), so nothing on this PC is changed. On a
machine with no screen it draws off-screen.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "ui-shots" / "desktop"
PASSWORD = "pictures only, not a real password"


def main() -> int:
    if sys.platform not in ("win32", "darwin") and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from fastapi.testclient import TestClient
    from PySide6.QtCore import QThreadPool
    from PySide6.QtWidgets import QApplication, QPushButton

    from agent.main import create_app
    from agent.security.auth import hash_password
    from desktop.agent import AgentClient
    from desktop.window import MainWindow
    from tests.conftest import build_multi_config
    from tests.fake_pc import FakePC

    os.environ["MCSC_ADMIN_USERNAME"] = "admin"
    os.environ["MCSC_ADMIN_PASSWORD_HASH"] = hash_password(PASSWORD, rounds=1000)
    os.environ.pop("MCSC_API_TOKEN", None)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    OUT.mkdir(parents=True, exist_ok=True)

    def settle(seconds: float = 1.0) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def snap(window: MainWindow, name: str, dark: bool) -> None:
        settle()
        path = OUT / f"{name}-{'dark' if dark else 'light'}.png"
        window.grab().save(str(path))
        print(f"wrote {path.relative_to(ROOT)}")

    with tempfile.TemporaryDirectory() as folder:
        config = build_multi_config(Path(folder))
        config.set("security.rate_limit_requests", 100_000)  # light and dark in one minute
        with TestClient(create_app(config)) as http:
            from agent.events import Event

            for kind, server, words in (
                ("server_crashed", "survival", "The server stopped unexpectedly (exit code 1)"),
                ("backup_completed", "creative", "Backup finished: 212 MB"),
            ):
                core = http.app.state.core
                core.notifier.record_app_alert(Event(type=kind, message=words, server_id=server))
            # An hour of made-up samples for the graphs: pictures only.
            core = http.app.state.core
            first = next(iter(core.servers))
            now = time.time()
            for step in range(360):  # one every 10 seconds, as the agent samples
                minute = step // 6
                running = minute < 40
                core.db.insert(
                    "metrics",
                    {
                        "server_id": first,
                        "ts": now - step * 10,
                        "cpu_percent": 20 + minute % 7 * 3,
                        "tps": 19.2 + minute % 5 * 0.2 if running else None,
                        "mspt": 14 + minute % 6 * 2 if running else None,
                        "proc_ram_mb": 1800 + minute * 10 if running else None,
                    },
                )
            # Mods, with Modrinth pretend (tests' fake): pictures only.
            from tests.test_dependency_resolution import FakeModrinth
            from tests.test_mods import make_jar

            folder = core.servers[first].config.mods_dir
            folder.mkdir(exist_ok=True)
            make_jar(folder / "lithium.jar", "lithium", "0.12.1", name="Lithium")
            make_jar(folder / "ferrite.jar.disabled", "ferritecore", "6.0.3", name="FerriteCore")
            make_jar(
                folder / "waystones.jar", "waystones", "21.1.4", {"balm": "*"}, name="Waystones"
            )
            core.servers[first].mods.modrinth = FakeModrinth(
                {
                    "lithium": {
                        "project_id": "gvQqBUqZ",
                        "title": "Lithium",
                        "versions": [("0.13.0", [])],
                    },
                    "ferritecore": {
                        "project_id": "uXXizFIs",
                        "title": "FerriteCore",
                        "versions": [("7.0.0", [])],
                    },
                    "waystones": {
                        "project_id": "LOpKHB2A",
                        "title": "Waystones",
                        "versions": [("21.1.4", [])],
                    },
                    "balm": {
                        "project_id": "MBAkmtvl",
                        "title": "Balm",
                        "versions": [("21.0.20", [])],
                    },
                }
            )
            for dark in (False, True):
                window = MainWindow(AgentClient(http), FakePC(), dark=dark)
                window.show()
                snap(window, "01-sign-in", dark)
                window.agent.sign_in("admin", PASSWORD, False, "Pictures")
                window.signed_in(False)
                snap(window, "02-all-servers", dark)
                window.binder.setCurrentIndex(1)
                for technical in (False, True):
                    window.set_mode(technical)
                    snap(window, f"03-server-{'technical' if technical else 'simple'}", dark)
                window.resize(900, 760)
                snap(window, "04-server-narrow", dark)
                window.set_mode(False)
                window.resize(1280, 800)
                window.alerts_loaded(window.agent.alerts(0)["alerts"], pop=False)
                window.show_screen("Notifications")
                snap(window, "05-alerts", dark)
                # The pages, with the first server running.
                sid = window.servers[0]["id"]
                window.agent.server_action(sid, "start")
                for _ in range(150):
                    if window.agent.status(sid).get("state") == "ONLINE":
                        break
                    settle(0.2)
                for words in (
                    "fakejoin Alex",
                    "fakejoin Sam",
                    "fakechat Alex anyone up for the nether?",
                ):
                    window.agent.on(
                        sid, "POST", "/server/command", {"command": words, "confirm": True}
                    )
                window.refresh()
                settle()
                window.go_to_server(sid)
                for page, name, technical in (
                    ("Players and chat", "07-players", False),
                    ("Console", "08-console", True),
                    ("Performance", "09-performance", True),
                    ("Schedules", "10-schedules", False),
                ):
                    window.set_mode(technical)
                    window.open_page(page)
                    settle(2)
                    snap(window, name, dark)
                window.set_mode(False)
                window.open_activity()
                settle(2)
                snap(window, "11-activity", dark)
                window.agent.on(
                    sid, "POST", "/server/command", {"command": "crash", "confirm": True}
                )
                settle(3)
                window.open_activity(crashes=True)
                settle(2)
                for found in window.server_screen.page.findChildren(QPushButton):
                    if found.text() == "Details":
                        found.click()
                        break
                snap(window, "12-crashes", dark)
                try:
                    window.agent.server_action(sid, "stop")
                except Exception:
                    pass  # it may still be down after the crash
                # Backups, with the server off so Restore world is offered.
                window.agent.back_up(sid)
                window.agent.back_up(sid)
                for technical, name in ((False, "13-backups"), (True, "14-backups-technical")):
                    window.set_mode(technical)
                    window.open_page("Backups")
                    settle(2)
                    snap(window, name, dark)
                window.agent.on(sid, "GET", "/mods/update-all", slow=True)  # notes what's newer
                for technical, name in ((False, "15-mods"), (True, "16-mods-technical")):
                    window.set_mode(technical)
                    window.open_page("Mods")
                    page = window.server_screen.page
                    page.search_box.setText("lith")  # type: ignore[attr-defined]
                    page.search()  # type: ignore[attr-defined]
                    settle(2)
                    snap(window, name, dark)
                # Settings, the + tab, the gear's sections and "?".
                for technical, name in ((False, "17-settings"), (True, "18-settings-technical")):
                    window.set_mode(technical)
                    window.open_page("Settings")
                    settle(2)
                    snap(window, name, dark)
                window.set_mode(False)
                window.show_screen("New server")
                settle(2)
                snap(window, "19-new-server", dark)
                if not dark:  # one moderator to list, made once
                    window.agent.call(
                        "POST",
                        "/accounts",
                        {"username": "sam", "password": PASSWORD, "servers": [sid]},
                    )
                for screen, section, name in (
                    ("PC settings", "pc", "20-pc-settings"),
                    ("PC settings", "updates", "21-updates"),
                    ("PC settings", "moderators", "22-moderators"),
                    ("PC settings", "devices", "23-signed-in-devices"),
                    ("PC settings", "account", "24-your-account"),
                    ("Help", "guide", "25-getting-started"),
                    ("Help", "about", "26-about"),
                ):
                    window.open_list(screen, section)
                    settle(2)
                    snap(window, name, dark)
                window.agent_down()
                snap(window, "06-agent-not-running", dark)
                window.quit_app()
                QThreadPool.globalInstance().waitForDone(5000)  # before the agent stops
    return 0


if __name__ == "__main__":
    sys.exit(main())
