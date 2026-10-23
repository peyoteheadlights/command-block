"""PC settings' Wake-on-LAN cards (Technical only): the setup for the PC the
agent runs on, and "Help wake <PC>" on another PC at home. desktop/wake.py
sends the signal; agent/wake.py keeps the setup. Nothing here changes a
Windows setting: the checklist says what to tick, the person ticks it."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QFrame, QLineEdit

from .gear import PCSettings, buttons
from .overview import card
from .ui import clock, field, label, push
from .wake import PORT
from .words import text

CHECKLIST = (
    "desktop.wake.check_adapter",
    "desktop.wake.check_advanced",
    "desktop.wake.check_bios",
    "desktop.wake.check_sleep",
)
NOW = {
    True: "desktop.wake.now_cable",
    False: "desktop.wake.now_wifi",
    None: "desktop.wake.now_unknown",
}


def setup_card(page: PCSettings, state: dict) -> QFrame:
    frame, layout = card(text("desktop.wake.title"))
    layout.addWidget(label(text("desktop.wake.lead"), "sub"))
    now = state.get("now")
    if now is None:
        layout.addWidget(label(text("desktop.wake.no_network"), "problem"))
    else:
        layout.addWidget(
            label(
                text(NOW[now.get("wired")], adapter=now["adapter"], mac=now["mac"]),
                "problem" if now.get("wired") is False else "sub",
            )
        )

    def change(body: dict, words: str) -> None:
        page.call("PUT", "/wake/setup", page.then_reload(words), body=body)

    if not state.get("on"):
        start = push(text("desktop.wake.set_up"), "primary")
        start.clicked.connect(lambda: change({"on": True}, text("desktop.wake.turned_on")))
        layout.addWidget(start)
        return frame
    layout.addWidget(label(text("desktop.wake.checklist"), "step"))
    for i, item in enumerate(CHECKLIST, 1):
        layout.addWidget(label(f"{i}. " + text(item)))
    layout.addWidget(label(text("desktop.wake.never_changes"), "muted"))

    layout.addWidget(label(text("desktop.wake.away"), "step"))
    helper = state.get("helper")
    layout.addWidget(label(text("desktop.wake.helper_how"), "sub"))
    if helper:
        layout.addWidget(label(text("desktop.wake.helper_is", **helper)))
        stop = push(text("desktop.wake.helper_stop"), "plain")
        stop.clicked.connect(
            lambda: page.call(
                "DELETE", "/wake/helper", page.then_reload(text("desktop.wake.helper_stopped"))
            )
        )
        layout.addWidget(stop)
    layout.addWidget(label(text("desktop.wake.router_how"), "sub"))
    layout.addWidget(label(text("desktop.wake.router_fritz"), "muted"))
    router = QLineEdit(state.get("router_url") or "")
    router.setPlaceholderText("https://")
    layout.addWidget(field(text("desktop.wake.router_page"), router))
    save = push(text("desktop.wake.router_save"))
    save.clicked.connect(
        lambda: change({"router_url": router.text().strip() or None}, text("settings.saved"))
    )
    layout.addWidget(save)

    layout.addWidget(label(text("desktop.wake.try_title"), "step"))
    if state.get("tested_at"):
        layout.addWidget(label(text("desktop.wake.tested", time=clock(state["tested_at"])), "ok"))
    elif state.get("trying_since"):
        layout.addWidget(label(text("desktop.wake.trying"), "sub"))
    try_it = push(text("desktop.wake.try"), "primary")
    try_it.clicked.connect(
        lambda: page.call("POST", "/wake/try", page.then_reload(text("desktop.wake.trying")))
    )
    off = push(text("desktop.wake.turn_off"), "danger")
    off.clicked.connect(lambda: change({"on": False}, text("desktop.wake.turned_off")))
    layout.addLayout(buttons(try_it, off))
    return frame


def helper_card(page: PCSettings, info: dict) -> QFrame:
    """On another PC at home: this PC sends the wake signal for the PC the
    agent runs on, when asked over Tailscale with a valid key."""
    window = page.window_
    name = info.get("pc") or window.agent.place
    frame, layout = card(text("desktop.wake.help_title", pc=name))
    box = QCheckBox(text("desktop.wake.help", pc=name))
    box.setChecked(bool(window.pc.setting("help_wake")))
    layout.addWidget(box)
    layout.addWidget(label(text("desktop.wake.help_hint"), "muted"))
    result = label()
    result.hide()
    layout.addWidget(result)

    def turned(on: bool) -> None:
        if not on:
            window.pc.set_setting("help_wake", "")
            window.sync_helper()
            page.call("DELETE", "/wake/helper", lambda _s: None)
            return

        def untick(words: str) -> None:
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
            result.setText(words)
            result.show()

        address = window.pc.tailscale_address()
        if not address:
            untick(text("desktop.wake.help_no_tailscale"))
            return

        def done(_state: dict) -> None:
            window.pc.set_setting("help_wake", address)
            window.sync_helper()

        page.call(
            "PUT",
            "/wake/helper",
            done,
            lambda exc: untick(str(exc)),
            body={"address": address, "port": PORT},
        )

    box.toggled.connect(turned)
    return frame
