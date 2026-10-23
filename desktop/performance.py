"""The Performance page (Technical only): the numbers now, graphs over a
chosen time, storage and network, how the server's speed is read, and the
health checks. It asks the agent for new samples only while it is open."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from .graphs import Graph
from .overview import card
from .ui import Page, clock, label, push, refill
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

RANGES = (1, 6, 24, 168)  # hours
TPS_CHOICES = ("auto", "tick query", "tps", "spark tps", "custom", "off")
TPS_WORDS = ("tps.opt_auto", "tps.opt_tick", "tps.opt_carpet", "tps.opt_spark", "tps.opt_custom")


def words(key: str, **values) -> str:
    return text(key, technical=True, **values)


def size(mb: float | None) -> str:
    if mb is None:
        return words("state.unknown")
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def gb(value: float | None) -> str:
    return words("state.unknown") if value is None else f"{value:.1f} GB"


class PerformancePage(Page):
    REFRESH_MS = 30_000  # the agent's samples are a minute apart

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.hours = 6
        top = QHBoxLayout()
        top.addWidget(label(words("perf.range"), "sub"))
        self.ranges = QButtonGroup(self)
        for hours in RANGES:
            button = push(words(f"perf.range_{hours}"), "segment")
            button.setCheckable(True)
            button.setChecked(hours == self.hours)
            button.clicked.connect(lambda _=False, h=hours: self.pick_range(h))
            self.ranges.addButton(button)
            top.addWidget(button)
        top.addStretch()
        self.column.addLayout(top)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title in (
            ("pc_cpu", "perf.pc_cpu"),
            ("server_cpu", "perf.server_cpu"),
            ("server_memory", "perf.server_memory"),
            ("pc_memory", "perf.pc_memory"),
            ("disk", "perf.disk_free"),
        ):
            frame, layout = card(words(title))
            value, note = label(name="h1"), label(kind="muted")
            layout.addWidget(value)
            layout.addWidget(note)
            self.tiles[key] = (value, note)
            tiles.addWidget(frame, 1)
        self.column.addLayout(tiles)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.graphs: dict[str, Graph] = {}
        self.empty: dict[str, QLabel] = {}
        for i, (key, title, unit, top_value) in enumerate(
            (
                ("cpu_percent", "perf.chart_cpu", "%", 100.0),
                ("proc_ram_mb", "perf.chart_memory", "GB", 4.0),
                ("tps", "perf.chart_speed", "", 20.0),
                ("players", "perf.chart_players", "", 4.0),
                ("mspt", "perf.chart_mspt", "ms", 50.0),
                ("ram_used_mb", "perf.chart_pc_memory", "GB", 8.0),
            )
        ):
            frame, layout = card(words(title))
            graph = Graph(words(title), unit, top_value)
            graph.setMinimumHeight(150)
            layout.addWidget(graph)
            empty = label(kind="muted")
            layout.addWidget(empty)
            self.graphs[key], self.empty[key] = graph, empty
            grid.addWidget(frame, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.column.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(12)
        storage, layout = card(words("perf.storage"))
        self.storage = QGridLayout()
        layout.addLayout(self.storage)
        layout.addStretch()
        row.addWidget(storage, 1)
        network, layout = card(words("perf.network"))
        self.network = QGridLayout()
        layout.addLayout(self.network)
        layout.addStretch()
        row.addWidget(network, 1)
        self.column.addLayout(row)

        row = QHBoxLayout()
        row.setSpacing(12)
        speed, layout = card(words("perf.speed_readings"))
        self.speed = QVBoxLayout()
        layout.addLayout(self.speed)
        buttons = QHBoxLayout()
        self.change = push(words("tps.change"))
        self.change.clicked.connect(self.change_command)
        self.detect = push(words("tps.detect_again"))
        self.detect.clicked.connect(self.detect_again)
        buttons.addWidget(self.change)
        buttons.addWidget(self.detect)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addStretch()
        row.addWidget(speed, 1)
        health, layout = card(words("perf.health"))
        self.health = QVBoxLayout()
        layout.addLayout(self.health)
        layout.addStretch()
        row.addWidget(health, 1)
        self.column.addLayout(row)
        self.column.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(self.REFRESH_MS)
        self.timer.timeout.connect(self.reload)
        self.tps: dict = {}

    def showEvent(self, event) -> None:
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def reload(self) -> None:
        if self.isVisible() and not self.window().isMinimized():
            self.load()

    def changed(self) -> None:
        # The timer brings new samples; an event may be about reading TPS.
        self.ask("GET", "/tps", self.show_tps)

    def pick_range(self, hours: int) -> None:
        self.hours = hours
        self.ask("GET", f"/performance?hours={hours}", self.show_performance)

    def style_graphs(self, look: dict[str, str]) -> None:
        colors = {
            "line": look["accent"],
            "text": look["text-secondary"],
            "border": look["text-tertiary"],
            "plot": look["surface-sunken"],
        }
        for graph in self.graphs.values():
            graph.colors = colors
            graph.update()

    # ------------------------------------------------------------ loading
    def load(self) -> None:
        self.style_graphs(self.window_.look)
        self.ask("GET", f"/performance?hours={self.hours}", self.show_performance, self.unknown)
        self.ask("GET", "/tps", self.show_tps)
        self.ask("GET", "/health/server", self.show_health)
        sid = self.row["id"]
        self.change.setVisible(self.window_.may(sid, "settings.server"))
        self.detect.setVisible(self.window_.may(sid, "server.control"))

    def unknown(self, exc: Exception) -> None:
        """No answer: nothing is shown as if it were measured."""
        for value, note in self.tiles.values():
            value.setText(words("state.unknown"))
            note.setText(str(exc))
        for graph in self.graphs.values():
            graph.show_points([], 0, 0, 60)

    def show_performance(self, data: dict) -> None:
        c = data.get("current") or {}
        limit = data.get("memory_limit_mb")

        def pct(v: float | None) -> str:
            return words("state.unknown") if v is None else f"{v:.0f}%"

        server_memory = size(c.get("proc_ram_mb")) if c.get("proc_ram_mb") is not None else None
        tiles = {
            "pc_cpu": (pct(c.get("cpu_percent")), words("perf.pc_cpu_note")),
            "server_cpu": (pct(c.get("process_cpu_percent")), words("perf.server_cpu_note")),
            "server_memory": (
                server_memory or words("state.unknown"),
                words("perf.of_limit", limit=size(limit))
                if server_memory and limit
                else words("perf.not_running_note"),
            ),
            "pc_memory": (pct(c.get("ram_percent")), words("perf.pc_memory_note")),
            "disk": (
                gb(c.get("disk_free_gb")),
                c.get("disk_unknown_reason") or words("perf.on_server_drive"),
            ),
        }
        for key, (value, note) in tiles.items():
            self.tiles[key][0].setText(value)
            self.tiles[key][1].setText(note)

        series = data.get("series") or {}
        points = series.get("points") or []
        end = time.time()
        start = end - float(series.get("hours") or self.hours) * 3600
        # Longer than this with no sample means the agent wasn't running.
        gap = max(float(series.get("bucket_seconds") or 60) * 2.5, data["sample_interval"] * 3)
        bedrock = (self.row.get("capabilities") or {}).get("speed") is False
        tops = {
            "cpu_percent": 100.0,
            "proc_ram_mb": (limit or 4096) / 1024,
            "tps": 20.0,
            "players": float(max([4] + [p.get("players_max") or 0 for p in points])),
            "mspt": 50.0,
            "ram_used_mb": max([8192] + [p.get("ram_total_mb") or 0 for p in points]) / 1024,
        }
        empties = {
            "proc_ram_mb": "perf.memory_empty",
            "tps": "perf.speed_not_bedrock" if bedrock else "perf.speed_empty",
            "mspt": "perf.speed_not_bedrock" if bedrock else "perf.speed_empty",
            "players": "perf.players_empty",
        }
        for key, graph in self.graphs.items():
            graph.top = tops[key]
            scale = 1 / 1024 if key in ("proc_ram_mb", "ram_used_mb") else 1
            if key == "proc_ram_mb" and limit:
                graph.guide = (limit / 1024, words("perf.limit", limit=size(limit)))
            elif key == "mspt":
                graph.guide = (50.0, words("perf.mspt_budget"))
            values = [(p["ts"], None if p.get(key) is None else p[key] * scale) for p in points]
            graph.show_points(values, start, end, gap)
            measured = any(v is not None for _t, v in values)
            self.empty[key].setText(
                "" if measured else words(empties.get(key, "overview.not_measured"))
            )
            self.empty[key].setVisible(not measured)

        storage = data.get("storage") or {}
        rows = [
            ("perf.storage_worlds", gb(storage.get("worlds_gb"))),
            ("perf.storage_backups", gb(storage.get("backups_gb"))),
            ("perf.storage_mods", gb(storage.get("mods_gb"))),
            ("perf.storage_logs", gb(storage.get("logs_gb"))),
            ("perf.storage_other", gb(storage.get("other_gb"))),
            (
                "perf.storage_free",
                words("perf.unknown_because", reason=storage["free_unknown_reason"])
                if storage.get("free_gb") is None and storage.get("free_unknown_reason")
                else gb(storage.get("free_gb")),
            ),
        ]
        self.fill_grid(self.storage, rows)

        def rate(v: float | None) -> str:
            return words("state.unknown") if v is None else f"{v:.2f} MB/s"

        self.fill_grid(
            self.network,
            [
                ("perf.net_in", rate(c.get("net_recv_mb_s"))),
                ("perf.net_out", rate(c.get("net_sent_mb_s"))),
            ],
        )

    def fill_grid(self, grid: QGridLayout, rows: list[tuple[str, str]]) -> None:
        while grid.count():
            old = grid.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        for i, (key, value) in enumerate(rows):
            grid.addWidget(label(words(key), "sub"), i, 0)
            grid.addWidget(label(value, "address"), i, 1)

    # ------------------------------------------------------------ speed readings
    def show_tps(self, status: dict) -> None:
        self.tps = status
        if status.get("mode") == "unsupported":
            refill(self.speed, [label(status.get("message") or "", "sub")])
            self.change.hide()
            self.detect.hide()
            return
        mode = {"manual": "tps.manual", "disabled": "tps.off"}.get(
            status["mode"],
            "tps.auto_remembered" if status.get("detection") == "remembered" else "tps.auto",
        )
        command = status.get("command") or words(
            "tps.manual_silent" if status["mode"] == "manual" else "tps.none"
        )
        when = status.get("last_reading_at")
        lines = [
            f"{words('tps.status')}: {words('tps.state_' + status['state'])}",
            f"{words('tps.command')}: {command}",
            f"{words('tps.detection')}: {words(mode)}",
            f"{words('tps.last_reading')}: {clock(when) if when else words('tps.none_yet')}",
        ]
        shown = [label(line) for line in lines]
        if status["state"] in ("unavailable", "disabled") and status.get("message"):
            shown.append(label(status["message"], "sub"))
            shown += [
                label(f"{t.get('command')}: {t.get('detail')}", "sub")
                for t in status.get("tried") or []
            ]
        refill(self.speed, shown)
        self.change.setText(
            words("tps.configure" if status["state"] == "unavailable" else "tps.change")
        )
        online = status.get("server_state") == "ONLINE"
        self.detect.setVisible(
            status["mode"] == "auto" and self.window_.may(self.row["id"], "server.control")
        )
        self.detect.setEnabled(online)
        self.detect.setToolTip("" if online else words("tps.needs_online"))

    def detect_again(self) -> None:
        self.detect.setEnabled(False)
        self.detect.setText(words("tps.detecting"))

        def always() -> None:
            self.detect.setText(words("tps.detect_again"))

        self.ask("POST", "/tps/detect", self.show_tps, always=always)

    def change_command(self) -> None:
        names = [words(key) for key in TPS_WORDS] + [words("tps.opt_off")]
        picked = self.window_.pick(words("tps.dialog_title"), words("tps.dialog_body"), names)
        if picked is None:
            return
        command = TPS_CHOICES[picked]
        if command == "custom":
            typed = self.window_.ask(
                words("tps.dialog_title"),
                words("tps.dialog_body"),
                words("action.save"),
                words("tps.custom_label"),
            )
            if not typed or not typed.strip():
                return
            command = typed.strip()
        self.ask("PUT", "/tps", self.show_tps, body={"command": command})

    # ------------------------------------------------------------ health checks
    def show_health(self, health: dict) -> None:
        overall = {
            "ALL CHECKS VERIFIED": ("good", "health.all_ok"),
            "PARTIALLY VERIFIED": ("notice", "health.partial"),
            "ATTENTION NEEDED": ("bad", "health.attention"),
        }.get(health.get("overall", ""), ("notice", ""))
        head = label(words(overall[1]) if overall[1] else health.get("overall", ""), overall[0])
        shown = [head]
        if health.get("note"):
            shown.append(label(health["note"], "sub"))
        for check in health.get("checks") or []:
            value = check.get("value")
            line = f"{check.get('name')}: {words('state.unknown') if value is None else value}"
            detail = check.get("detail") or ""
            if check.get("threshold") is not None:
                detail += f" ({words('health.limit', limit=check['threshold'])})"
            if check.get("source"):
                detail += f" [{check['source']}]"
            shown.append(label(line, "step"))
            if detail:
                shown.append(label(detail.strip(), "sub"))
        refill(self.health, shown)
