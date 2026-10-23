"""The Settings tab: one page in four sections, with buttons at the top
that jump to each. Server settings (name and color, crashes, sleep when
empty, CPU cores, backups kept, start and stop limits, duplicate, remove),
Game settings (server.properties and memory), Version, and Bedrock players
on a Java server (version.py has those two).

Each card has its own Save, which sends only what changed; the agent checks
every value. Saving reloads the page."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
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
    QPlainTextEdit,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .agent import AgentError
from .overview import card
from .ui import Page, duration, field, label, push, refill, swatches
from .version import crossplay_card, version_card
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
    "pvp": ("game.pvp", "game.pvp_hint"),
    "view-distance": ("game.view_distance", "game.view_distance_hint"),
    "motd": ("game.motd", "game.motd_hint"),
    "server-port": ("game.port", "game.port_hint"),
    "allow-list": ("game.whitelist", "game.allowlist_hint"),
    "allow-cheats": ("game.cheats", "game.cheats_hint"),
    "server-name": ("game.server_name", "game.server_name_hint"),
}
GROUPS = [
    ("game.group_play", ["gamemode", "difficulty", "pvp", "allow-cheats"]),
    ("game.group_who", ["motd", "server-name", "max-players", "white-list", "allow-list"]),
    ("game.group_world", ["level-seed", "view-distance", "server-port"]),
]
CHOICES = {"peaceful", "easy", "normal", "hard", "survival", "creative", "adventure", "spectator"}


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


class SettingsPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        jumps = QHBoxLayout()
        self.headings: dict[str, QLabel] = {}
        self.jump_buttons: dict[str, Any] = {}
        self.slots: dict[str, QVBoxLayout] = {}
        for key, words in SECTIONS:
            button = push(text(words), "plain")
            button.clicked.connect(lambda _=False, k=key: self.jump(k))
            jumps.addWidget(button)
            self.jump_buttons[key] = button
        jumps.addStretch()
        self.column.addLayout(jumps)
        for key, words in SECTIONS:
            heading = label(text(words), name="title")
            self.headings[key] = heading
            self.column.addWidget(heading)
            for slot in [key] + {"server": ["manage"], "game": ["memory", "raw"]}.get(key, []):
                self.slots[slot] = QVBoxLayout()
                self.slots[slot].setSpacing(12)
                self.column.addLayout(self.slots[slot])
        self.column.addStretch()

    def jump(self, key: str) -> None:
        self.verticalScrollBar().setValue(self.headings[key].y())

    def may(self, permission: str) -> bool:
        return self.window_.may(self.row["id"], permission)

    def load(self) -> None:
        java = self.row.get("edition") != "bedrock"
        for widget in (self.headings["bedrock"], self.jump_buttons["bedrock"]):
            widget.setVisible(java)
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
        said.setProperty("kind", "problem" if rejected else "ok")
        said.setStyle(said.style())
        if not rejected:
            self.window_.refresh()  # the name and color show in the tabs
            self.load()

    def save_button(
        self, layout: QVBoxLayout, collect: Callable[[], dict], words: str = "action.save"
    ) -> QLabel:
        """A Save that sends what ``collect`` says changed, and its result line."""
        row = QHBoxLayout()
        button = push(text(words), "primary")
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
            self.cpu(data["cpu"], server.get("cpu_cores") or []),
            self.keep(data["backups"]),
            self.timing(server),
        ]
        for frame in cards:
            frame.setEnabled(editable)
        refill(self.slots["server"], cards)

    def identity(self, data: dict) -> QFrame:
        frame, layout = card(text("serverset.identity"))
        name = QLineEdit(self.row["name"])
        name.setMaxLength(60)
        name.setObjectName("serverName")
        layout.addWidget(field(text("serverset.name"), name))
        chosen = {"color": data["color"]}
        colors = QHBoxLayout()
        colors.addWidget(
            swatches(data["palette"], data["color"], lambda c: chosen.__setitem__("color", c)), 1
        )
        other = push(text("serverset.custom_color"), "plain")

        def any_color() -> None:
            picked = QColorDialog.getColor(parent=self, title=text("serverset.custom_color"))
            if picked.isValid():
                chosen["color"] = picked.name().upper()
                other.setText(f"{text('serverset.custom_color')}: {chosen['color']}")

        other.clicked.connect(any_color)
        colors.addWidget(other)
        layout.addWidget(label(text("serverset.color"), "sub"))
        layout.addLayout(colors)
        row = QHBoxLayout()
        save = push(text("serverset.save_identity"), "primary")
        said = label(kind="sub")
        row.addWidget(save)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def save_identity() -> None:
            new = name.text().strip()
            if chosen["color"] != data["color"]:
                self.ask(
                    "PUT",
                    "/color",
                    lambda _r: self.saved(said) if new == self.row["name"] else None,
                    lambda exc: said.setText(str(exc)),
                    body={"color": chosen["color"]},
                )
            if new and new != self.row["name"]:
                self.ask(
                    "PUT",
                    "/settings",
                    lambda result: self.saved(said, result),
                    lambda exc: said.setText(str(exc)),
                    body={"updates": {"server.name": new}},
                )
            elif chosen["color"] == data["color"]:
                said.setText(text("settings.nothing_changed"))

        save.clicked.connect(save_identity)
        return frame

    def crashes(self, m: dict) -> QFrame:
        frame, layout = card(text("serverset.crashes"))
        auto = QCheckBox(text("serverset.auto_restart"))
        auto.setChecked(bool(m["auto_restart"]))
        layout.addWidget(auto)
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
            if auto.isChecked() != bool(m["auto_restart"]):
                out["monitor.auto_restart"] = auto.isChecked()
            if abs(delay.value() - m["restart_delay"]) > 0.05:
                out["monitor.restart_delay"] = delay.value()
            if most.value() != m["max_crashes"]:
                out["monitor.max_crashes"] = most.value()
            return out

        self.save_button(layout, collect)
        return frame

    def sleep(self, server: dict, now: dict) -> QFrame:
        frame, layout = card(text("serverset.sleep"))
        on = QCheckBox(text("serverset.sleep_label"))
        on.setChecked(bool(server["autosleep"]))
        layout.addWidget(on)
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
        if not server["autosleep"]:
            line = text("serverset.sleep_off")
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
        layout.addWidget(label(line, "sub"))
        layout.addWidget(label(text("serverset.sleep_restart_note"), "muted"))

        def collect() -> dict:
            out: dict = {}
            if on.isChecked() != bool(server["autosleep"]):
                out["server.autosleep"] = on.isChecked()
            if minutes.value() != server["autosleep_minutes"]:
                out["server.autosleep_minutes"] = minutes.value()
            return out

        self.save_button(layout, collect)
        return frame

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
        save = push(text("action.save"), "primary")
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
        self.save_button(
            layout,
            lambda: {
                f"backups.keep_{k}": box.value()
                for k, box in boxes.items()
                if box.value() != b[f"keep_{k}"]
            },
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
        for shown, words in (
            (found.get("running"), "dup.running"),
            (found.get("crossplay"), "dup.crossplay"),
        ):
            if shown:
                layout.addWidget(label(text(words), "sub"))
        row = QHBoxLayout()
        go = push(text("dup.button"), "primary")
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
        save = push(text("game.save"), "primary")
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

            self.ask("PUT", "/game-settings", done, failed, body={"values": values})

        save.clicked.connect(save_game)
        refill(self.slots["game"], [self.banner, frame])
        refill(self.slots["raw"], [self.raw(data)] if mode["technical"] else [])

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

    # ------------------------------------------------------------ memory
    def show_memory(self, data: dict) -> None:
        """The memory limit (-Xmx only), bounded by the PC's memory as measured."""
        frame, layout = card(text("memory.title"))
        layout.addWidget(label(text("memory.label"), "step"))
        total = data.get("total_mb")
        layout.addWidget(
            label(
                text("memory.pc_has", total=gb(total)) if total else text("memory.pc_unknown"),
                "sub",
            )
        )
        editable = self.may("settings.server")
        if not data.get("takes_memory_limit"):
            words = (
                "memory.not_bedrock" if self.row.get("edition") == "bedrock" else "memory.not_used"
            )
            layout.addWidget(label(text(words), "sub"))
            refill(self.slots["memory"], [frame])
            return
        current = data.get("limit_mb")
        if total:
            row = QHBoxLayout()
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName("memorySlider")
            slider.setAccessibleName(text("memory.label"))
            step = data.get("step_mb") or 512
            slider.setRange(data["min_mb"] // step, total // step)
            start = min(total, max(data["min_mb"], current or 2048))
            slider.setValue(round(start / step))
            slider.setEnabled(editable)
            shown = label(kind="step")
            warning = label(kind="problem")
            others = [
                s for s in data.get("servers") or [] if not s.get("this") and s.get("running")
            ]

            def moved(value: int) -> None:
                mb = value * step
                shown.setText(gb(mb))
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

            slider.valueChanged.connect(moved)
            moved(slider.value())
            row.addWidget(slider, 1)
            row.addWidget(shown)
            layout.addLayout(row)
            layout.addWidget(warning)
        layout.addWidget(
            label(text("memory.now", limit=gb(current)) if current else text("memory.unset"), "sub")
        )
        if total and editable:
            save_row = QHBoxLayout()
            save = push(text("memory.save"), "primary")
            said = label(kind="sub")
            save_row.addWidget(save)
            save_row.addWidget(said, 1)
            layout.addLayout(save_row)

            def save_memory() -> None:
                mb = slider.value() * step
                if mb == current:
                    said.setText(text("settings.nothing_changed"))
                    return

                def done(result: dict) -> None:
                    self.done(
                        text("memory.saved_running" if result.get("running") else "memory.saved")
                    )

                self.ask(
                    "PUT", "/memory", done, lambda e: said.setText(str(e)), body={"memory_mb": mb}
                )

            save.clicked.connect(save_memory)
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
        refill(self.slots["version"], [version_card(self, data)])

    def show_crossplay(self, status: dict) -> None:
        refill(self.slots["bedrock"], [crossplay_card(self, status)])
