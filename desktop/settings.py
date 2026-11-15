"""The Settings tab: four sections, picked with the control at the top, one
shown at a time. Server settings (name and color, crashes, sleep when
empty, CPU cores, backups kept, start and stop limits, duplicate, remove),
Game settings (server.properties, memory and the folder's other config
files, opened in desktop/editor.py), Version, and Bedrock players
on a Java server (version.py has those two).

A switch, a slider, the name and the color save as soon as they change; the
other fields have their card's Save, which sends only what changed. The agent
checks every value, and the page is read again from it after each save."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .agent import AgentError
from .editor import edit_file
from .overview import card
from .parts import BigNumber, Box, InfoDot, Segmented, SnapSlider, Switch
from .ui import Page, duration, field, label, push, refill, swatches
from .version import crossplay_card, type_card, version_card
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

SECTIONS = [
    ("server", "page.server_settings"),
    ("game", "page.game_settings"),
    ("version", "version.title"),
    ("bedrock", "cross.title"),
]
# Game settings: each key's words, and the order the form shows them in.
LABELS = {
    "difficulty": ("game.difficulty", "game.difficulty_hint"),
    "gamemode": ("game.gamemode", "game.gamemode_hint"),
    "max-players": ("game.max_players", "game.max_players_hint"),
    "level-seed": ("game.seed", "game.seed_hint"),
    "white-list": ("game.whitelist", "game.whitelist_hint"),
    "online-mode": ("game.online_mode", "game.online_mode_hint"),
    "pvp": ("game.pvp", "game.pvp_hint"),
    "view-distance": ("game.view_distance", "game.view_distance_hint"),
    "simulation-distance": ("game.simulation_distance", "game.simulation_distance_hint"),
    "motd": ("game.motd", "game.motd_hint"),
    "server-port": ("game.port", "game.port_hint"),
    "allow-list": ("game.whitelist", "game.allowlist_hint"),
    "allow-cheats": ("game.cheats", "game.cheats_hint"),
    "server-name": ("game.server_name", "game.server_name_hint"),
}
GROUPS = [
    ("game.group_play", ["gamemode", "difficulty", "pvp", "allow-cheats"]),
    (
        "game.group_who",
        ["motd", "server-name", "max-players", "white-list", "allow-list", "online-mode"],
    ),
    ("game.group_world", ["level-seed", "view-distance", "simulation-distance", "server-port"]),
]
CHOICES = {"peaceful", "easy", "normal", "hard", "survival", "creative", "adventure", "spectator"}


# Join protection's limits and what happened to a join, by the agent's ids.
LIMIT_WORDS = {
    "connections": "serverset.join_connections",
    "new_names": "serverset.join_new_names",
    "pings": "serverset.join_pings",
    "slow_rate": "serverset.join_slow_rate",
}
RESULT_WORDS = {
    "in": "serverset.join_result_in",
    "too_fast": "serverset.join_result_too_fast",
    "known_only": "serverset.join_result_known_only",
    "in_line": "serverset.join_result_in_line",
    "asleep": "serverset.join_result_asleep",
}


def choice_words(value: str) -> str:
    return text(f"game.{value}") if value in CHOICES else value


def gb(mb: float) -> str:
    return f"{mb / 1024:.{1 if mb % 1024 else 0}f} GB"


def cores(numbers: list[int]) -> str:
    """Cores as people count them, from 1, with runs shortened: "1-4, 7"."""
    runs: list[str] = []
    for n in sorted(c + 1 for c in numbers):
        if runs and runs[-1].split("-")[-1] == str(n - 1):
            runs[-1] = f"{runs[-1].split('-')[0]}-{n}"
        else:
            runs.append(str(n))
    return ", ".join(runs)


def grid(fields: list[QWidget], columns: int = 2) -> QGridLayout:
    layout = QGridLayout()
    layout.setHorizontalSpacing(14)
    for i, widget in enumerate(fields):
        layout.addWidget(widget, i // columns, i % columns)
    for col in range(columns):
        layout.setColumnStretch(col, 1)
    return layout


def number(value: float, low: float, high: float, decimals: int = 0) -> QSpinBox | QDoubleSpinBox:
    box = QDoubleSpinBox() if decimals else QSpinBox()
    if isinstance(box, QDoubleSpinBox):
        box.setDecimals(decimals)
    box.setRange(low, high)  # type: ignore[arg-type]
    box.setValue(value)  # type: ignore[arg-type]
    return box


def switch_row(words: QLabel, name: str, hint: str = "") -> tuple[QHBoxLayout, Switch]:
    """One on/off setting in one row: its words, an info dot for the
    sentence that explains it, and the switch. ``name`` is what Narrator
    reads for the switch."""
    row = QHBoxLayout()
    words.setWordWrap(False)
    row.addWidget(words)
    if hint:
        row.addWidget(InfoDot(hint))
    row.addStretch(1)
    switch = Switch(name)
    row.addWidget(switch)
    return row, switch


def switch_card(title: str, name: str, hint: str = "") -> tuple[Box, QVBoxLayout, Switch]:
    """A card that is one on/off setting: the switch sits on the heading's row."""
    frame, layout = card(title)
    top = layout.takeAt(0)  # the heading's row, as card() made it
    heading = frame.findChild(QLabel, "h2")
    assert top is not None and heading is not None
    top.layout().removeWidget(heading)
    row, switch = switch_row(heading, name, hint)
    layout.insertLayout(0, row)
    return frame, layout, switch


def saves_when_let_go(slider: SnapSlider, save: Callable[[int], None]) -> None:
    """Calls ``save`` with the slider's value when the mouse lets it go, or
    a moment after the last arrow key: not at every step on the way."""
    wait = QTimer(slider)
    wait.setSingleShot(True)
    wait.setInterval(500)
    wait.timeout.connect(lambda: save(slider.value()))
    slider.sliderReleased.connect(lambda: save(slider.value()))
    slider.valueChanged.connect(lambda _v: None if slider.isSliderDown() else wait.start())


def slider_card(
    title: str, hint: str, slider: SnapSlider, unit: str, low: str, high: str
) -> tuple[Box, QVBoxLayout, BigNumber]:
    """A card for one slider: the heading and its info dot, the value big on
    the right (it is also the field to type in), the slider, and the two
    ends of its range under it."""
    frame, layout = card(title, words=hint)
    top = layout.itemAt(0).layout()
    assert isinstance(top, QHBoxLayout)
    big = BigNumber(slider.accessibleName())
    top.addWidget(big)
    if unit:
        top.addWidget(label(unit, "sub"), 0, Qt.AlignmentFlag.AlignBottom)
    layout.addWidget(slider)
    ends = QHBoxLayout()
    ends.addWidget(label(low, "sub"))
    ends.addStretch()
    ends.addWidget(label(high, "sub"))
    layout.addLayout(ends)
    return frame, layout, big


class SettingsPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.sections = Segmented(
            [(key, text(words)) for key, words in SECTIONS], "server", self.jump
        )
        self.column.addWidget(self.sections)
        self.section: dict[str, QWidget] = {}
        self.slots: dict[str, QVBoxLayout] = {}
        for key, _words in SECTIONS:
            holder = self.section[key] = QWidget()
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)  # a card brings its own room for its shadow
            if key == "game":
                # The two sliders side by side, above the form.
                sliders = QHBoxLayout()
                for slot in ("memory", "view"):
                    self.slots[slot] = QVBoxLayout()
                    sliders.addLayout(self.slots[slot], 1)
                column.addLayout(sliders)
            for slot in [key] + {"server": ["manage"], "game": ["raw", "files"]}.get(key, []):
                self.slots[slot] = QVBoxLayout()
                self.slots[slot].setSpacing(4)
                column.addLayout(self.slots[slot])
            holder.setVisible(key == "server")
            self.column.addWidget(holder)
        self.column.addStretch()

    def jump(self, key: str) -> None:
        """Shows one of the four sections."""
        self.sections.choose(key)
        for name, holder in self.section.items():
            holder.setVisible(name == key)
        self.verticalScrollBar().setValue(0)

    def may(self, permission: str) -> bool:
        return self.window_.may(self.row["id"], permission)

    def load(self) -> None:
        java = self.row.get("edition") != "bedrock"
        self.sections.buttons["bedrock"].setVisible(java)
        if not java and self.sections.key() == "bedrock":
            self.jump("server")
        refill(self.slots["bedrock"], [])
        self.ask("GET", "/settings", self.show_server)
        self.ask("GET", "/game-settings", self.show_game, self.game_failed)
        self.ask("GET", "/memory", self.show_memory, lambda exc: self.problem("memory", exc))
        self.window_.with_types(
            lambda: self.ask(
                "GET", "/version", self.show_version, lambda e: self.problem("version", e)
            )
        )
        if java:
            self.ask(
                "GET", "/crossplay", self.show_crossplay, lambda exc: self.problem("bedrock", exc)
            )
        if self.may("servers.manage"):
            self.ask("GET", "/duplicate", self.show_manage, lambda exc: self.show_manage(None, exc))
        else:
            refill(self.slots["manage"], [])
        refill(self.slots["files"], [])
        if self.may("mods.manage"):
            self.ask("GET", "/config-files", self.show_files, lambda e: self.problem("files", e))

    def changed(self) -> None:
        """Only the line about restarting follows the live feed: reloading the
        forms would throw away what someone is typing."""
        self.ask("GET", "/game-settings", self.show_banner, lambda _e: None)

    def done(self, words: str) -> None:
        """After a change: the page again from the agent, and what happened on top."""
        self.load()
        self.say(words)
        self.verticalScrollBar().setValue(0)

    def problem(self, slot: str, exc: Exception) -> None:
        refill(self.slots[slot], [label(str(exc), "problem")])

    def saved(self, said: QLabel, result: dict | None = None) -> None:
        """After a Save: the agent's reasons for anything it refused, or Saved."""
        rejected = list(((result or {}).get("rejected") or {}).values())
        said.setText(" ".join(rejected) if rejected else text("settings.saved"))
        said.show()
        said.setProperty("kind", "problem" if rejected else "ok")
        said.setStyle(said.style())
        if not rejected:
            self.window_.refresh()  # the name and color show in the tabs
            self.load()

    def applies(self, switch: Switch, key: str, layout: QVBoxLayout) -> None:
        """Makes ``switch`` save ``key`` as soon as it is flipped. If the
        agent says no, the switch goes back and the reason shows under it:
        it never stays on something that was not saved."""
        said = label(kind="problem")
        said.hide()
        layout.addWidget(said)
        quiet = [False]

        def back(words: str) -> None:
            quiet[0] = True
            switch.setChecked(not switch.isChecked())
            quiet[0] = False
            said.setText(words)
            said.show()

        def done(result: dict) -> None:
            rejected = list((result.get("rejected") or {}).values())
            if rejected:
                back(" ".join(rejected))
            else:
                self.load()

        def flip(on: bool) -> None:
            if not quiet[0]:
                self.ask(
                    "PUT",
                    "/settings",
                    done,
                    lambda exc: back(str(exc)),
                    body={"updates": {key: on}},
                )

        switch.toggled.connect(flip)

    def save_button(
        self, layout: QVBoxLayout, collect: Callable[[], dict], words: str = "action.save"
    ) -> QLabel:
        """A Save that sends what ``collect`` says changed, and its result line."""
        row = QHBoxLayout()
        button = push(text(words), "tonal")  # the one filled button is the header's Start
        said = label(kind="sub")
        row.addWidget(button)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def save() -> None:
            updates = collect()
            if not updates:
                said.setText(text("settings.nothing_changed"))
                return
            self.ask(
                "PUT",
                "/settings",
                lambda result: self.saved(said, result),
                lambda exc: said.setText(str(exc)),
                body={"updates": updates},
            )

        button.clicked.connect(save)
        return said

    # ------------------------------------------------------------ server settings
    def show_server(self, data: dict) -> None:
        server, editable = data["server"], self.may("settings.server")
        cards = [
            self.identity(data),
            self.crashes(data["monitor"]),
            self.sleep(server, data.get("sleep") or {}),
            *([self.join(server, data["join"])] if data.get("join") else []),
            self.cpu(data["cpu"], server.get("cpu_cores") or []),
            self.keep(data["backups"]),
            self.timing(server),
        ]
        if server.get("test") and mode["technical"] and self.window_.role != "helper":
            cards.insert(1, self.build_folder(server))
        if self.row.get("edition") == "bedrock" and self.window_.role != "helper":
            cards.insert(3, self.bedrock_updates(server))
        for frame in cards:
            frame.setEnabled(editable)
        refill(self.slots["server"], cards)

    def bedrock_updates(self, server: dict) -> QFrame:
        """Bedrock players' games update themselves, then can't join an older
        server. Off by default, and only the owner sees it."""
        frame, layout, on = switch_card(
            text("serverset.bedrock_update"),
            text("serverset.bedrock_update_label"),
            text("serverset.bedrock_update_note"),
        )
        on.setChecked(bool(server.get("bedrock_auto_update")))
        self.applies(on, "server.bedrock_auto_update", layout)
        return frame

    def identity(self, data: dict) -> QFrame:
        """The name and the color save as soon as they change: the name on
        Enter or when the keyboard leaves the field, a color on its click."""
        frame, layout = card(text("serverset.identity"))
        name = QLineEdit(self.row["name"])
        name.setMaxLength(60)
        name.setObjectName("serverName")
        name.setAccessibleName(text("serverset.name"))
        layout.addWidget(name)
        said = label(kind="sub")
        said.hide()  # until there is something to say
        sent = [self.row["name"]]  # Enter also takes the keyboard away: send it once

        def problem(exc: Exception) -> None:
            said.setText(str(exc))
            said.show()

        def save_name() -> None:
            new = name.text().strip()
            if new and new != sent[0]:
                sent[0] = new
                self.ask(
                    "PUT",
                    "/settings",
                    lambda result: self.saved(said, result),
                    problem,
                    body={"updates": {"server.name": new}},
                )

        def save_color(color: str) -> None:
            if color != data["color"]:
                self.ask(
                    "PUT", "/color", lambda _r: self.saved(said), problem, body={"color": color}
                )

        name.editingFinished.connect(save_name)
        colors = QHBoxLayout()
        colors.addWidget(swatches(data["palette"], data["color"], save_color), 1)
        other = push(text("serverset.custom_color"), "plain")

        def any_color() -> None:
            picked = QColorDialog.getColor(parent=self, title=text("serverset.custom_color"))
            if picked.isValid():
                save_color(picked.name().upper())

        other.clicked.connect(any_color)
        colors.addWidget(other)
        layout.addLayout(colors)
        layout.addWidget(said)
        return frame

    def crashes(self, m: dict) -> QFrame:
        frame, layout, auto = switch_card(
            text("serverset.auto_restart"),
            text("serverset.auto_restart"),
            text("serverset.auto_restart_hint"),
        )
        auto.setChecked(bool(m["auto_restart"]))
        delay = number(m["restart_delay"], 0, 3600, 1)
        most = number(m["max_crashes"], 1, 1000)
        layout.addLayout(
            grid(
                [
                    field(text("serverset.restart_delay"), delay),
                    field(
                        text("serverset.max_crashes"),
                        most,
                        text(
                            "serverset.max_crashes_hint", minutes=f"{m['crash_window_minutes']:g}"
                        ),
                    ),
                ]
            )
        )

        def collect() -> dict:
            out: dict = {}
            if abs(delay.value() - m["restart_delay"]) > 0.05:
                out["monitor.restart_delay"] = delay.value()
            if most.value() != m["max_crashes"]:
                out["monitor.max_crashes"] = most.value()
            return out

        self.save_button(layout, collect)
        self.applies(auto, "monitor.auto_restart", layout)
        return frame

    def sleep(self, server: dict, now: dict) -> QFrame:
        frame, layout, on = switch_card(
            text("serverset.sleep"), text("serverset.sleep_label"), text("serverset.sleep_label")
        )
        on.setChecked(bool(server["autosleep"]))
        minutes = number(server["autosleep_minutes"], 1, 1440)
        layout.addLayout(
            grid(
                [
                    field(
                        text("serverset.sleep_minutes"),
                        minutes,
                        text("serverset.sleep_minutes_hint"),
                    )
                ]
            )
        )
        # Only what was measured: the count, and what it is waiting for.
        # Nothing while it is off: the switch says that.
        if not server["autosleep"]:
            line = ""
        elif not now:
            line = text("value.unknown")
        elif now.get("list_answered") is False:
            line = text("serverset.sleep_no_answer")
        elif now.get("players_online") is None:
            line = text("serverset.sleep_unknown_players")
        elif now["players_online"] > 0:
            line = text("serverset.sleep_players", count=now["players_online"])
        elif now.get("stops_in") is not None:
            line = text("serverset.sleep_countdown", duration=duration(now["stops_in"]))
        else:
            line = text("serverset.sleep_waiting")
        if line:
            layout.addWidget(label(line, "sub"))
        layout.addWidget(label(text("serverset.sleep_restart_note"), "muted"))

        def collect() -> dict:
            out: dict = {}
            if minutes.value() != server["autosleep_minutes"]:
                out["server.autosleep_minutes"] = minutes.value()
            return out

        self.save_button(layout, collect)
        self.applies(on, "server.autosleep", layout)
        return frame

    def join(self, server: dict, now: dict) -> QFrame:
        """Join protection (agent/joinguard.py): one switch in Simple, and in
        Technical the size, limits, home-only, Launch mode, blocks and log."""
        frame, layout, on = switch_card(
            text("serverset.join"), text("serverset.join_label"), text("serverset.join_note")
        )
        on.setChecked(bool(server.get("join_protection")))
        if now.get("why_not") == "start_command":
            line = text("serverset.join_start_command")
        elif now.get("restart_needed"):
            line = text("serverset.join_restart")
        elif now.get("running"):
            line = text("serverset.join_running", blocked=now.get("blocked_today", 0))
        elif server.get("join_protection"):
            line = text("serverset.join_next")
        else:
            line = ""  # off: the switch says that
        if line:
            layout.addWidget(label(line, "sub"))
        self.applies(on, "server.join_protection", layout)
        row, wake = switch_row(
            label(text("serverset.wake_label")),
            text("serverset.wake_label"),
            text("serverset.wake_note"),
        )
        wake.setChecked(bool(server.get("start_on_join")))
        layout.addLayout(row)
        self.applies(wake, "server.start_on_join", layout)
        if self.row.get("content"):  # the anti-cheat picker is on the Mods page
            anticheat = push(text("desktop.protect.pick_anticheat"))
            anticheat.clicked.connect(lambda: self.window_.open_page("Mods"))
            layout.addWidget(anticheat, 0, Qt.AlignmentFlag.AlignLeft)
        size, home, limits = QComboBox(), QCheckBox(text("serverset.join_home_only")), {}
        if mode["technical"]:
            for value, words in (
                ("", "serverset.join_size_auto"),
                ("small", "serverset.join_small"),
                ("big", "serverset.join_big"),
            ):
                size.addItem(text(words), value)
            size.setCurrentIndex(max(0, size.findData(server.get("join_size") or "")))
            home.setChecked(bool(server.get("join_home_only")))
            used = now.get("limits") or {}
            for key in LIMIT_WORDS:
                limits[key] = number(used.get(key, 1), 1, 10000)
            layout.addLayout(
                grid(
                    [
                        field(
                            text("serverset.join_size"),
                            size,
                            text("serverset.join_size_hint", size=now.get("size", "")),
                        ),
                        *(field(text(LIMIT_WORDS[k]), box) for k, box in limits.items()),
                    ]
                )
            )
            layout.addWidget(home)
        details = QVBoxLayout()
        layout.addLayout(details)

        def collect() -> dict:
            out: dict = {}
            if mode["technical"]:
                if size.currentData() != (server.get("join_size") or ""):
                    out["server.join_size"] = size.currentData()
                if home.isChecked() != bool(server.get("join_home_only")):
                    out["server.join_home_only"] = home.isChecked()
                used = now.get("limits") or {}
                picked = {k: box.value() for k, box in limits.items() if box.value() != used.get(k)}
                if picked:
                    out["server.join_limits"] = {**(server.get("join_limits") or {}), **picked}
            return out

        if mode["technical"]:  # Simple has only the two switches: nothing to Save
            self.save_button(layout, collect)
            self.ask(
                "GET",
                "/join-protection",
                lambda data: refill(details, self.join_details(data)),
                lambda exc: refill(details, [label(str(exc), "problem")]),
            )
        return frame

    def join_details(self, data: dict) -> list[QWidget]:
        """Technical: Launch mode, the block list with expiry and the
        name-to-address log (addresses masked)."""
        status, shown = data["status"], []
        launch = QWidget()
        rows = QVBoxLayout(launch)
        rows.setContentsMargins(0, 8, 0, 0)
        rows.addWidget(label(text("serverset.launch"), "step"))
        if status.get("launch_until"):
            rows.addWidget(
                label(
                    text(
                        "serverset.launch_on",
                        duration=duration(status["launch_until"] - time.time()),
                    ),
                    "sub",
                )
            )
            end = push(text("serverset.launch_end"))
            end.clicked.connect(
                lambda: self.ask(
                    "DELETE",
                    "/join-protection/launch",
                    lambda _r: self.done(text("serverset.launch_ended")),
                )
            )
            rows.addWidget(end)
        elif not status.get("can_launch"):
            rows.addWidget(label(text("serverset.launch_not_set_up"), "muted"))
        else:
            hours, players = number(24, 1, 168), number(100, 1, 100000)
            rows.addLayout(
                grid(
                    [
                        field(text("serverset.launch_hours"), hours),
                        field(text("serverset.launch_players"), players),
                    ]
                )
            )
            go = push(text("serverset.launch_start"))
            go.clicked.connect(
                lambda: self.ask(
                    "POST",
                    "/join-protection/launch",
                    lambda _r: self.done(text("serverset.launch_started")),
                    body={"hours": hours.value(), "players": players.value()},
                )
            )
            rows.addWidget(go)
        rows.addWidget(label(text("serverset.launch_note"), "muted"))
        shown.append(launch)

        blocks = QWidget()
        rows = QVBoxLayout(blocks)
        rows.setContentsMargins(0, 8, 0, 0)
        rows.addWidget(label(text("serverset.join_blocks"), "step"))
        for block in data.get("blocks") or []:
            row = QHBoxLayout()
            until = (
                text("serverset.join_block_forever")
                if block["until"] is None
                else text(
                    "serverset.join_block_until", duration=duration(block["until"] - time.time())
                )
            )
            row.addWidget(label(f"{block['address']}  ·  {until}", "sub"), 1)
            remove = push(text("serverset.join_unblock"), "plain")
            remove.clicked.connect(
                lambda _=False, a=block["address"]: self.ask(
                    "DELETE",
                    "/join-protection/blocks",
                    lambda _r: self.done(text("serverset.join_unblocked")),
                    body={"address": a},
                )
            )
            row.addWidget(remove)
            rows.addLayout(row)
        if not data.get("blocks"):
            rows.addWidget(label(text("serverset.join_no_blocks"), "muted"))
        add_row = QHBoxLayout()
        address = QLineEdit()
        address.setPlaceholderText(text("serverset.join_block_hint"))
        add = push(text("serverset.join_block"))
        add.clicked.connect(
            lambda: self.ask(
                "POST",
                "/join-protection/blocks",
                lambda _r: self.done(text("serverset.join_blocked")),
                body={"address": address.text().strip()},
            )
        )
        add_row.addWidget(address, 1)
        add_row.addWidget(add)
        rows.addLayout(add_row)
        shown.append(blocks)

        log = QWidget()
        rows = QVBoxLayout(log)
        rows.setContentsMargins(0, 8, 0, 0)
        rows.addWidget(label(text("serverset.join_log"), "step"))
        for entry in (data.get("log") or [])[:20]:
            rows.addWidget(
                label(
                    text(
                        "serverset.join_log_row",
                        name=entry["name"],
                        address=entry["address"],
                        result=text(
                            RESULT_WORDS.get(entry["result"], "serverset.join_result_other")
                        ),
                        when=duration(time.time() - entry["ts"]),
                    ),
                    "sub",
                )
            )
        if not data.get("log"):
            rows.addWidget(label(text("serverset.join_no_log"), "muted"))
        shown.append(log)

        playit = QWidget()
        rows = QVBoxLayout(playit)
        rows.setContentsMargins(0, 8, 0, 0)
        rows.addWidget(label(text("serverset.join_address"), "step"))
        row = QHBoxLayout()
        typed = QLineEdit(status.get("shared_address") or "")
        typed.setMaxLength(253)
        typed.setPlaceholderText("example.joinmc.link")
        save = push(text("action.save"))
        sid = self.row["id"]
        save.clicked.connect(
            lambda: self.window_.run(
                lambda: self.window_.agent.call(
                    "PUT",
                    "/discord/addresses",
                    {"server_id": sid, "address": typed.text().strip() or None},
                ),
                lambda _r: self.done(text("serverset.join_address_saved")),
                self.window_.page_problem,
            )
        )
        row.addWidget(typed, 1)
        row.addWidget(save)
        rows.addLayout(row)
        rows.addWidget(label(text("serverset.join_address_hint"), "muted"))
        shown.append(playit)
        return shown

    def cpu(self, info: dict, chosen: list[int]) -> QFrame:
        """Which cores this server may use. What it really uses is read back
        from the running server; the boxes are only the setting."""
        frame, layout = card(text("serverset.cpu"))
        count = info.get("logical_cores")
        if not info.get("supported") or not count:
            layout.addWidget(
                label(info.get("unsupported_reason") or text("serverset.cpu_unreadable"), "sub")
            )
            return frame
        shared: dict[int, list[str]] = {}
        for other in info.get("others") or []:
            for core in other.get("cores") or []:
                shared.setdefault(core, []).append(other["name"])
        every = QCheckBox(text("serverset.all_cores", count=count))
        every.setChecked(not chosen)
        layout.addWidget(every)
        boxes = []
        holder = QWidget()
        boxes_grid = QGridLayout(holder)
        boxes_grid.setContentsMargins(0, 0, 0, 0)
        for core in range(count):
            words = text("serverset.core", n=core + 1)
            if core in shared:
                words += f" ({text('serverset.core_shared', names=', '.join(shared[core]))})"
            box = QCheckBox(words)
            box.setChecked(core in chosen)
            boxes.append(box)
            boxes_grid.addWidget(box, core // 4, core % 4)
        holder.setVisible(bool(chosen))
        every.toggled.connect(lambda on: holder.setVisible(not on))
        layout.addWidget(holder)
        status = info.get("status") or {}
        applied = status.get("applied")
        if applied:
            words = text(
                "serverset.cores_now",
                cores=text("serverset.every_core") if len(applied) == count else cores(applied),
            )
        else:
            words = text("serverset.cores_unknown", reason=status.get("applied_reason") or "")
        layout.addWidget(label(words, "sub"))
        for found in (status.get("problems") or [])[:1]:
            layout.addWidget(label(found, "problem"))
        row = QHBoxLayout()
        save = push(text("action.save"), "tonal")
        half = push(text("serverset.half_cores"), "plain")
        said = label(kind="sub")
        for widget in (save, half):
            row.addWidget(widget)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def pick_half() -> None:
            every.setChecked(False)
            for i, box in enumerate(boxes):
                box.setChecked(i < (count + 1) // 2)

        def save_cores() -> None:
            picked = [] if every.isChecked() else [i for i, b in enumerate(boxes) if b.isChecked()]
            if not every.isChecked() and not picked:
                said.setText(text("serverset.pick_a_core"))
                return

            def done(result: dict) -> None:
                if result.get("rejected"):
                    self.saved(said, result)
                    return
                live = result.get("cpu_cores")
                self.done(
                    text("serverset.cores_not_applied", reason=live.get("reason"))
                    if live and not live.get("ok")
                    else text("serverset.cores_applied" if live else "serverset.cores_next_start")
                )

            self.ask(
                "PUT",
                "/settings",
                done,
                lambda exc: said.setText(str(exc)),
                body={"updates": {"server.cpu_cores": picked}},
            )

        half.clicked.connect(pick_half)
        save.clicked.connect(save_cores)
        return frame

    def keep(self, b: dict) -> QFrame:
        frame, layout = card(text("serverset.keep_label"), "backup")
        boxes = {
            kind: number(b[f"keep_{kind}"], 0, 1000) for kind in ("daily", "weekly", "monthly")
        }
        layout.addLayout(
            grid([field(text(f"serverset.keep_{k}"), box) for k, box in boxes.items()], 3)
        )

        def collect() -> dict:
            return {
                f"backups.keep_{k}": box.value()
                for k, box in boxes.items()
                if box.value() != b[f"keep_{k}"]
            }

        self.save_button(layout, collect)
        row, on_stop = switch_row(
            label(text("serverset.backup_on_stop")),
            text("serverset.backup_on_stop"),
            text("serverset.backup_on_stop_hint"),
        )
        on_stop.setChecked(bool(b.get("on_stop")))
        layout.addLayout(row)
        self.applies(on_stop, "backups.on_stop", layout)
        return frame

    def build_folder(self, server: dict) -> QFrame:
        """A Test server's build folder: its jars are copied in at each start."""
        frame, layout = card(text("test.build_title"))
        layout.addWidget(label(text("test.build_lead"), "sub"))
        box = QLineEdit(server.get("build_folder") or "")
        box.setMaxLength(400)
        box.setPlaceholderText("C:\\dev\\myplugin\\build\\libs")
        box.setAccessibleName(text("test.build_title"))
        row = QHBoxLayout()
        row.addWidget(box, 1)
        if self.window_.agent.place == "this PC":
            browse = push(text("desktop.browse"))
            browse.clicked.connect(
                lambda: box.setText(
                    self.window_.pc.pick_folder(text("test.build_title")) or box.text()
                )
            )
            row.addWidget(browse)
        layout.addLayout(row)
        self.save_button(
            layout,
            lambda: (
                {"server.build_folder": box.text().strip()}
                if box.text().strip() != (server.get("build_folder") or "")
                else {}
            ),
        )
        return frame

    def timing(self, s: dict) -> QFrame:
        frame, layout = card(text("serverset.timing"))
        start = number(s["start_timeout"], 10, 3600)
        stop = number(s["stop_timeout"], 5, 3600)
        layout.addLayout(
            grid(
                [
                    field(text("serverset.start_timeout"), start),
                    field(
                        text("serverset.stop_timeout"), stop, text("serverset.stop_timeout_hint")
                    ),
                ]
            )
        )
        if mode["technical"]:
            folder = label(text("serverset.folder", folder=s.get("directory") or "-"), "address")
            folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(folder)
        self.save_button(
            layout,
            lambda: {
                key: box.value()
                for key, box in (("server.start_timeout", start), ("server.stop_timeout", stop))
                if box.value() != round(s[key.split(".")[1]])
            },
        )
        return frame

    # ------------------------------------------------------------ duplicate, remove
    def show_manage(self, found: dict | None, exc: Exception | None = None) -> None:
        refill(self.slots["manage"], [self.duplicate(found, exc), self.remove()])

    def duplicate(self, found: dict | None, exc: Exception | None) -> QFrame:
        """A new server with the same software, add-ons and game settings. The
        copy runs as a job; its tab appears when the copy is checked."""
        frame, layout = card(text("dup.title"))
        if found is None:
            layout.addWidget(label(str(exc), "problem"))
            return frame
        layout.addWidget(label(text("dup.intro"), "sub"))
        name = QLineEdit(found["name"])
        name.setMaxLength(60)
        folder = QLineEdit(found["directory"])
        folder.setMaxLength(400)
        layout.addLayout(
            grid(
                [
                    field(text("dup.name"), name),
                    field(text("dup.folder"), folder, text("dup.folder_hint")),
                ]
            )
        )
        layout.addWidget(label(text("dup.world"), "sub"))
        copy = QRadioButton(f"{text('dup.world_copy')}: {text('dup.world_copy_hint')}")
        fresh = QRadioButton(f"{text('dup.world_fresh')}: {text('dup.world_fresh_hint')}")
        copy.setChecked(True)
        layout.addWidget(copy)
        layout.addWidget(fresh)
        test = QCheckBox(text("test.copy_as_test"))
        test.setVisible(mode["technical"])
        layout.addWidget(test)
        for shown, words in (
            (found.get("running"), "dup.running"),
            (found.get("crossplay"), "dup.crossplay"),
        ):
            if shown:
                layout.addWidget(label(text(words), "sub"))
        row = QHBoxLayout()
        go = push(text("dup.button"), "tonal")
        said = label(text("dup.after"), "sub")
        row.addWidget(go)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def start() -> None:
            if not name.text().strip() or not folder.text().strip():
                said.setText(text("add.need_name_and_folder"))
                return
            go.setEnabled(False)
            said.setText(text("dup.starting"))
            self.ask(
                "POST",
                "/duplicate",
                lambda _r: said.setText(text("dup.started", name=name.text().strip())),
                lambda e: said.setText(str(e)),
                body={
                    "name": name.text().strip(),
                    "directory": folder.text().strip(),
                    "world": "copy" if copy.isChecked() else "fresh",
                    "test": test.isChecked(),
                },
                always=lambda: go.setEnabled(True),
            )

        go.clicked.connect(start)
        return frame

    def remove(self) -> QFrame:
        frame, layout = card(text("serverset.remove"))
        last = len(self.window_.servers) < 2
        layout.addWidget(
            label(text("serverset.remove_last" if last else "serverset.remove_hint"), "sub")
        )
        button = push(text("serverset.remove_button"))
        button.setEnabled(not last)
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        name = self.row["name"]

        def remove() -> None:
            if not self.window_.confirm(
                text("serverset.remove_title", name=name),
                text("serverset.remove_body"),
                text("serverset.remove_confirm"),
            ):
                return
            sid, window = self.row["id"], self.window_
            window.run(
                lambda: window.agent.call("DELETE", f"/servers/{sid}"),
                lambda _r: window.server_removed(text("serverset.removed", name=name)),
                window.page_problem,
            )

        button.clicked.connect(remove)
        return frame

    # ------------------------------------------------------------ game settings
    def game_failed(self, exc: Exception) -> None:
        self.problem("game", exc)
        refill(self.slots["raw"], [])

    def show_banner(self, data: dict) -> None:
        banner = getattr(self, "banner", None)
        if banner is None:
            return
        banner.setVisible(bool(data.get("running")))
        banner.setText(
            text("game.restart_needed" if data.get("restart_needed") else "game.after_restart")
        )

    def show_game(self, data: dict) -> None:
        self.banner = label(kind="notice")
        self.show_banner(data)
        frame, layout = card(text("game.title"))
        frame.setEnabled(self.may("settings.game"))
        layout.addWidget(label(text("game.intro" if data.get("exists") else "game.no_file"), "sub"))
        fields = {f["key"]: f for f in data.get("fields") or []}
        view = fields.get("view-distance")
        if view and view["kind"] == "int" and not view.get("problem"):
            del fields["view-distance"]  # it has its own card, with the slider
            refill(self.slots["view"], [self.view_distance(view)])
        else:
            refill(self.slots["view"], [])
        reads: dict[str, tuple[Callable[[], Any], Any, dict]] = {}
        errors: dict[str, QLabel] = {}
        for group, keys in GROUPS:
            shown = [fields[k] for k in keys if k in fields and k in LABELS]
            if not shown:
                continue
            layout.addWidget(label(text(group), "step"))
            boxes = []
            for f in shown:
                widget, read, first = self.control(f)
                reads[f["key"]] = (read, first, f)
                words, hint = self.field_words(f)
                box = field(words, widget, hint)
                errors[f["key"]] = label(kind="problem")
                if f.get("problem"):
                    errors[f["key"]].setText(text("game.file_problem", problem=f["problem"]))
                errors[f["key"]].setVisible(bool(f.get("problem")))
                box.layout().addWidget(errors[f["key"]])  # type: ignore[union-attr]
                boxes.append(box)
            layout.addLayout(grid(boxes, 3))
        row = QHBoxLayout()
        save = push(text("game.save"), "tonal")
        said = label(text("game.copy_kept"), "sub")
        row.addWidget(save)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def save_game() -> None:
            for error in errors.values():
                error.hide()
            values = {
                key: read()
                for key, (read, first, f) in reads.items()
                if not f.get("read_only") and str(read()) != str(first)
            }
            if not values:
                said.setText(text("settings.nothing_changed"))
                return

            def failed(exc: Exception) -> None:
                problems = exc.problems if isinstance(exc, AgentError) else {}
                for key, reason in problems.items():
                    if key in errors:
                        errors[key].setText(reason)
                        errors[key].show()
                said.setText(text("game.fix_marked") if problems else str(exc))

            def done(result: dict) -> None:
                self.done(text("game.saved_running" if result.get("running") else "game.saved"))
                if (result.get("changed") or {}).get("online-mode") == "false":
                    self.ask("GET", "/protection", self.offer_login, lambda _e: None)

            self.ask("PUT", "/game-settings", done, failed, body={"values": values})

        save.clicked.connect(save_game)
        refill(self.slots["game"], [self.banner, frame])
        refill(self.slots["raw"], [self.raw(data)] if mode["technical"] else [])

    def view_distance(self, f: dict) -> QFrame:
        """View distance as a slider, in chunks. It saves when let go, the
        same way the form's Save does (the agent keeps a copy of the file)."""
        words, hint = self.field_words(f)
        first = int(f["value"] if f.get("set") else f["minecraft_default"])
        slider = SnapSlider(words)
        slider.setObjectName("viewSlider")
        slider.setRange(f["min"], f["max"])
        slider.setValue(first)
        frame, layout, big = slider_card(words, hint, slider, "", str(f["min"]), str(f["max"]))
        frame.setEnabled(self.may("settings.game"))
        big.show_value(str(first))
        slider.valueChanged.connect(lambda value: big.show_value(str(value)))
        big.entered.connect(lambda typed: slider.setValue(round(typed)))
        said = label(kind="problem")
        said.hide()
        layout.addWidget(said)

        def failed(exc: Exception) -> None:
            problems = exc.problems if isinstance(exc, AgentError) else {}
            said.setText(problems.get("view-distance") or str(exc))
            said.show()

        def save(value: int) -> None:
            if value == first and f.get("set"):
                return
            self.ask(
                "PUT",
                "/game-settings",
                lambda r: self.done(
                    text("game.saved_running" if r.get("running") else "game.saved")
                ),
                failed,
                body={"values": {"view-distance": str(value)}},
            )

        saves_when_let_go(slider, save)
        return frame

    def offer_login(self, card: dict) -> None:
        """Offline mode was just turned on: offer this type's login add-on
        (never added without this click)."""
        lines = [line for line in card.get("lines") or [] if line["id"] == "not_protected"]
        login = next((a for line in lines for a in line.get("addons") or []), None)
        if login is None:
            return
        name = login["name"]
        if self.window_.confirm(
            text("protect.title"),
            text("desktop.game.offer_login", name=name),
            text("desktop.protect.add", name=name),
        ):
            self.window_.install_addon(login["project"])

    def field_words(self, f: dict) -> tuple[str, str]:
        words, hint = LABELS[f["key"]]
        notes = [text("game.seed_locked" if f.get("read_only") == "world_exists" else hint)]
        if not f.get("set"):
            default = f.get("minecraft_default")
            if f["kind"] == "bool":
                shown = text("game.on" if default == "true" else "game.off")
            else:
                shown = (
                    choice_words(str(default))
                    if f["kind"] == "choice"
                    else default or text("game.blank")
                )
            notes.append(text("game.not_set", value=shown))
        name = text(words) + (f"  ({f['key']})" if mode["technical"] else "")
        return name, " ".join(notes)

    def control(self, f: dict) -> tuple[QWidget, Callable[[], Any], Any]:
        """The control for one key, how to read it, and what it showed first."""
        shown = f["value"] if f.get("set") and not f.get("problem") else None
        if f["kind"] == "choice":
            first = shown if shown is not None else f["minecraft_default"]
            combo = QComboBox()
            for choice in f["choices"]:
                combo.addItem(choice_words(choice), choice)
            combo.setCurrentIndex(max(0, combo.findData(first)))
            return combo, combo.currentData, first
        if f["kind"] == "bool":
            first = shown if shown is not None else f["minecraft_default"] == "true"
            box = QCheckBox(text("game.on"))
            box.setChecked(bool(first))
            box.toggled.connect(lambda on: box.setText(text("game.on" if on else "game.off")))
            box.setText(text("game.on" if first else "game.off"))
            return box, box.isChecked, bool(first)
        if f["kind"] in ("int", "port"):
            first = shown if shown is not None else int(f["minecraft_default"])
            spin = number(first, f["min"], f["max"])
            return spin, lambda: str(spin.value()), str(first)
        first = (
            shown if shown is not None else ("" if f["kind"] == "seed" else f["minecraft_default"])
        )
        line = QLineEdit(first)
        line.setMaxLength(f.get("max_length") or 150)
        line.setReadOnly(bool(f.get("read_only")))
        return line, line.text, first

    def raw(self, data: dict) -> QFrame:
        """Technical: the whole file as text, for every other key."""
        frame, layout = card(text("game.raw_title"))
        frame.setEnabled(self.may("settings.game"))
        layout.addWidget(label(text("game.raw_hint", file=data.get("file") or ""), "sub"))
        area = QPlainTextEdit(data.get("text") or "")
        area.setAccessibleName(text("game.raw_label"))
        area.setMinimumHeight(260)
        layout.addWidget(area)
        row = QHBoxLayout()
        save = push(text("game.save_raw"))
        said = label(kind="sub")
        row.addWidget(save)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def done(result: dict) -> None:
            if result.get("nothing_changed"):
                said.setText(text("settings.nothing_changed"))
                return
            self.done(text("game.saved_running" if result.get("running") else "game.saved"))

        save.clicked.connect(
            lambda: self.ask(
                "PUT",
                "/game-settings/raw",
                done,
                lambda exc: said.setText(str(exc)),
                body={"text": area.toPlainText()},
            )
        )
        return frame

    # ------------------------------------------------------------ other files
    def show_files(self, data: dict) -> None:
        """The server folder's other config files (server.properties is above)."""
        frame, layout = card(text("desktop.files.title"))
        layout.addWidget(label(text("desktop.files.intro"), "sub"))
        find = QLineEdit()
        find.setPlaceholderText(text("desktop.files.find"))
        find.setAccessibleName(text("desktop.files.find"))
        listed = QListWidget()
        listed.setAccessibleName(text("desktop.files.title"))
        listed.setMinimumHeight(180)
        for row in data.get("files") or []:
            item = QListWidgetItem(row["path"])
            item.setData(Qt.ItemDataRole.UserRole, row["path"])
            item.setFlags(item.flags() if not row["too_big"] else Qt.ItemFlag.NoItemFlags)
            listed.addItem(item)

        def only(words: str) -> None:
            for i in range(listed.count()):
                item = listed.item(i)
                item.setHidden(words.lower() not in item.text().lower())

        find.textChanged.connect(only)
        edit = push(text("desktop.files.edit"))

        def open_chosen() -> None:
            item = listed.currentItem()
            if item is not None:
                edit_file(self, item.data(Qt.ItemDataRole.UserRole))

        edit.clicked.connect(open_chosen)
        listed.itemDoubleClicked.connect(lambda _item: open_chosen())
        layout.addWidget(find)
        layout.addWidget(listed)
        if data.get("truncated"):
            layout.addWidget(label(text("desktop.files.truncated"), "sub"))
        layout.addWidget(edit, 0, Qt.AlignmentFlag.AlignLeft)
        refill(self.slots["files"], [frame])

    # ------------------------------------------------------------ memory
    def show_memory(self, data: dict) -> None:
        """The memory limit (-Xmx only), bounded by the PC's memory as measured."""
        total = data.get("total_mb")
        editable = self.may("settings.server")
        current = data.get("limit_mb")
        if not data.get("takes_memory_limit") or not total:
            frame, layout = card(text("memory.title"), "memory.label")
            if not total:
                words = "memory.pc_unknown"
            elif self.row.get("edition") == "bedrock":
                words = "memory.not_bedrock"
            else:
                words = "memory.not_used"
            layout.addWidget(label(text(words), "sub"))
            if not data.get("takes_memory_limit"):
                refill(self.slots["memory"], [frame])
                return
        else:
            step = data.get("step_mb") or 512
            slider = SnapSlider(text("memory.label"))
            slider.setObjectName("memorySlider")
            slider.setRange(data["min_mb"] // step, total // step)
            slider.setTickInterval(max(1, 1024 // step))  # a mark at every GB
            slider.setValue(round(min(total, max(data["min_mb"], current or 2048)) / step))
            slider.setEnabled(editable)
            frame, layout, big = slider_card(
                text("memory.title"),
                f"{text('memory.label')}. {text('memory.pc_has', total=gb(total))}",
                slider,
                "GB",
                gb(data["min_mb"]),
                gb(total),
            )
            big.setEnabled(editable)
            warning = label(kind="problem")
            said = label(kind="problem")
            said.hide()
            others = [
                s for s in data.get("servers") or [] if not s.get("this") and s.get("running")
            ]

            def moved(value: int) -> None:
                mb = value * step
                big.show_value(f"{mb / 1024:g}")
                together = sum(s.get("limit_mb") or 0 for s in others) + mb
                over = together > total
                warning.setVisible(over)
                if over and others:
                    warning.setText(
                        text(
                            "memory.over_together",
                            names=", ".join(s["name"] for s in others),
                            total=gb(together),
                            pc=gb(total),
                        )
                    )
                elif over:
                    warning.setText(text("memory.over_alone", total=gb(together), pc=gb(total)))

            def save_memory(value: int) -> None:
                mb = value * step
                if mb == current:
                    return

                def done(result: dict) -> None:
                    self.done(
                        text("memory.saved_running" if result.get("running") else "memory.saved")
                    )

                def failed(exc: Exception) -> None:
                    said.setText(str(exc))
                    said.show()

                self.ask("PUT", "/memory", done, failed, body={"memory_mb": mb})

            slider.valueChanged.connect(moved)
            moved(slider.value())
            big.entered.connect(lambda typed: slider.setValue(round(typed * 1024 / step)))
            saves_when_let_go(slider, save_memory)
            layout.addWidget(warning)
            layout.addWidget(said)
            if not current:  # the slider only shows where it starts: say nothing is set
                layout.addWidget(label(text("memory.unset"), "sub"))
        if mode["technical"]:
            args = QLineEdit(" ".join(data.get("jvm_args") or []))
            args.setEnabled(editable)
            layout.addWidget(field(text("memory.raw_args"), args, text("memory.raw_hint")))
            if editable:
                self.save_button(
                    layout,
                    lambda: (
                        {"server.jvm_args": args.text().split()}
                        if args.text().split() != (data.get("jvm_args") or [])
                        else {}
                    ),
                    "memory.save_raw",
                )
        refill(self.slots["memory"], [frame])

    # ------------------------------------------------------------ version and Bedrock players
    def show_version(self, data: dict) -> None:
        cards = [version_card(self, data)]
        java = data["capabilities"].get("edition") != "bedrock"
        if self.may("servers.manage") and (java or mode["technical"]):
            cards.append(type_card(self, data))
        refill(self.slots["version"], cards)

    def show_crossplay(self, status: dict) -> None:
        refill(self.slots["bedrock"], [crossplay_card(self, status)])
