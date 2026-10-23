"""The gear's screen and "?": sections listed on the left, one shown on the
right. The gear has PC settings, Updates, Moderators (moderators.py),
Signed-in devices and Your account; a moderator sees only the last two.

Every value comes from the agent and every change goes to it, which checks
the account may do it. The look (light, dark, follow Windows) is the one
thing kept on this PC."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .agent import AgentError
from .fixes import get_help
from .overview import card
from .tailscale_step import QrCode
from .ui import clock, field, label, push, refill, size
from .words import has, mode, text

if TYPE_CHECKING:
    from .window import MainWindow

# Where Tailscale lists this PC, with its Share button.
TAILSCALE_MACHINES = "https://login.tailscale.com/admin/machines"
THRESHOLDS = [
    ("thresholds.cpu_percent", "appset.cpu_alert"),
    ("thresholds.ram_percent", "appset.ram_alert"),
    ("thresholds.disk_free_gb", "appset.disk_alert"),
    ("thresholds.tps_min", "appset.tps_alert"),
    ("thresholds.mspt_max", "appset.mspt_alert"),
    ("notifications.min_interval_seconds", "appset.repeat_alert"),
]


def said(widget: QLabel, words: str, kind: str = "ok") -> None:
    widget.setText(words)
    widget.setProperty("kind", kind)
    widget.setStyle(widget.style())
    widget.setVisible(bool(words))


def three(value: Any, yes: str, no: str, unknown: str = "value.unknown") -> str:
    """Words for True, False, or not known (None)."""
    return text(yes if value is True else no if value is False else unknown)


def yes_no(value: Any) -> str:
    return three(value, "value.yes", "value.no")


def rows(pairs: list[tuple[str, str]]) -> QGridLayout:
    """Facts as two columns: what, then its value."""
    layout = QGridLayout()
    layout.setHorizontalSpacing(16)
    for i, (what, value) in enumerate(pairs):
        layout.addWidget(label(what, "sub"), i, 0)
        shown = label(value)
        shown.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(shown, i, 1)
    layout.setColumnStretch(1, 1)
    return layout


def buttons(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch()
    return row


def link_row(window: MainWindow, link: str) -> QHBoxLayout:
    """A link to read or copy: the pairing link, an invite."""
    shown = QLineEdit(link)
    shown.setReadOnly(True)
    shown.setAccessibleName(text("pair.link"))
    copy = push(text("desktop.copy"))

    def copied() -> None:
        QGuiApplication.clipboard().setText(link)
        copy.setText(text("desktop.copied"))

    copy.clicked.connect(copied)
    row = QHBoxLayout()
    row.addWidget(shown, 1)
    row.addWidget(copy)
    return row


class Section(QScrollArea):
    """One section of a list screen. load() runs each time it shows."""

    WORDS = ""

    def __init__(self, window: MainWindow, slots: list[str]) -> None:
        super().__init__()
        self.window_ = window
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(4, 0, 4, 0)
        self.column.setSpacing(12)
        self.column.addWidget(label(text(self.WORDS), name="title"))
        self.note = label()
        self.note.hide()
        self.column.addWidget(self.note)
        self.slots: dict[str, QVBoxLayout] = {}
        for slot in slots:
            self.slots[slot] = QVBoxLayout()
            self.slots[slot].setSpacing(12)
            self.column.addLayout(self.slots[slot])
        self.column.addStretch()
        self.setWidget(body)

    def allowed(self) -> bool:
        return True

    def owner(self) -> bool:
        return self.window_.role != "helper"

    def load(self) -> None:
        pass

    def say(self, words: str, kind: str = "ok") -> None:
        said(self.note, words, kind)

    def problem(self, exc: Exception) -> None:
        self.say(str(exc), "problem")

    def fill(self, slot: str, *widgets: QWidget) -> None:
        refill(self.slots[slot], list(widgets))

    def call(
        self,
        method: str,
        path: str,
        done: Callable[[Any], None],
        failed: Callable[[Exception], None] | None = None,
        body: dict | None = None,
        slow: bool = False,
    ) -> None:
        agent = self.window_.agent
        self.window_.run(lambda: agent.call(method, path, body, slow), done, failed or self.problem)

    def then_reload(self, words: str) -> Callable[[Any], None]:
        def done(_result: Any) -> None:
            self.load()
            self.say(words)

        return done


class ListScreen(QWidget):
    """The sections on the left, the chosen one on the right. It opens on
    the section seen last."""

    def __init__(self, window: MainWindow, name: str, sections: dict[str, Section]) -> None:
        super().__init__()
        self.NAME = name
        self.window_ = window
        self.sections = sections
        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(20)
        self.list = QListWidget()
        self.list.setObjectName("sections")
        self.list.setAccessibleName(text("desktop.tool.help") if name == "Help" else name)
        self.list.setFixedWidth(220)
        self.pages = QStackedWidget()
        for key, section in sections.items():
            item = QListWidgetItem(text(section.WORDS))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.list.addItem(item)
            self.pages.addWidget(section)
        self.list.currentRowChanged.connect(self.picked)
        outer.addWidget(self.list)
        outer.addWidget(self.pages, 1)

    def load(self) -> None:
        """Shows the sections this account may use, on the one seen last."""
        for i, section in enumerate(self.sections.values()):
            self.list.item(i).setHidden(not section.allowed())
        keys = list(self.sections)
        wanted = self.window_.pc.setting(f"section:{self.NAME}")
        if wanted not in keys or not self.sections[wanted].allowed():
            wanted = next(k for k, s in self.sections.items() if s.allowed())
        if self.list.currentRow() == keys.index(wanted):
            self.picked(keys.index(wanted))
        else:
            self.list.setCurrentRow(keys.index(wanted))

    def picked(self, index: int) -> None:
        if index < 0:
            return
        key = list(self.sections)[index]
        self.window_.pc.set_setting(f"section:{self.NAME}", key)
        self.pages.setCurrentIndex(index)
        section = self.sections[key]
        section.say("")
        section.load()

    @property
    def section(self) -> str:
        return list(self.sections)[self.pages.currentIndex()]


# ------------------------------------------------------------ PC settings
class PCSettings(Section):
    WORDS = "desktop.tool.pc_settings"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(
            window,
            [
                "look",
                "alerts",
                "awake",
                "maintenance",
                "tailscale",
                "wake",
                "export",
                "help",
                "startup",
            ],
        )

    def allowed(self) -> bool:
        return self.owner()

    def load(self) -> None:
        self.fill("look", self.look_card())
        self.fill("help", self.help_card())
        self.fill("startup", self.startup_card())
        agent = self.window_.agent

        def read() -> tuple[dict, dict | None]:
            try:
                power = agent.call("GET", "/power")
            except AgentError:
                power = None
            return agent.call("GET", "/settings"), power

        self.window_.run(read, self.show_settings, self.problem)
        self.call("GET", "/export", lambda sizes: self.fill("export", self.export_card(sizes)))
        if agent.place == "this PC":
            self.call("GET", "/tailscale", self.show_tailscale)
        else:
            frame, layout = card(text("security.tailscale"))
            layout.addWidget(label(text("desktop.tailscale.not_here", pc=agent.place), "sub"))
            self.fill("tailscale", frame)
        self.fill("wake")
        if mode["technical"]:  # never suggested in Simple
            self.window_.run(lambda: self.wake_state(agent), self.show_wake, self.problem)

    @staticmethod
    def wake_state(agent) -> tuple[dict, dict | None]:
        setup = agent.call("GET", "/wake/setup")
        return setup, None if agent.place == "this PC" else agent.call("GET", "/wake")

    def show_wake(self, found: tuple[dict, dict | None]) -> None:
        from .wake_setup import helper_card, setup_card

        setup, here = found
        cards = [setup_card(self, setup)]
        if here is not None and setup.get("on"):
            cards.append(helper_card(self, here))
        self.fill("wake", *cards)

    def show_settings(self, found: tuple[dict, dict | None]) -> None:
        data, power = found
        self.fill("alerts", self.alerts_card(data))
        self.fill("awake", self.awake_card(data, power) if power else QWidget())
        self.fill("maintenance", self.maintenance_card(data))

    # -------------------------------------------------------- look and detail
    def look_card(self) -> QFrame:
        frame, layout = card(text("appset.appearance"))
        window = self.window_
        looks = QButtonGroup(frame)
        row = QHBoxLayout()
        now = window.pc.setting("look") or "system"
        for key, words in (
            ("light", "theme.light"),
            ("dark", "theme.dark"),
            ("system", "desktop.look.system"),
        ):
            choice = QRadioButton(text(words))
            choice.setChecked(key == now)
            choice.toggled.connect(lambda on, k=key: window.set_look(k) if on else None)
            looks.addButton(choice)
            row.addWidget(choice)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label(text("appset.detail"), name="h2"))
        details = QButtonGroup(frame)
        for technical, words in ((False, "mode.simple"), (True, "mode.technical")):
            hint = "mode.technical_hint" if technical else "mode.simple_hint"
            choice = QRadioButton(f"{text(words)}: {text(hint)}")
            choice.setChecked(mode["technical"] == technical)
            choice.toggled.connect(lambda on, t=technical: self.set_mode(t) if on else None)
            details.addButton(choice)
            layout.addWidget(choice)
        return frame

    def set_mode(self, technical: bool) -> None:
        self.window_.set_mode(technical)
        self.load()  # Technical opens the extra details here too

    # -------------------------------------------------------- alerts
    def alerts_card(self, data: dict) -> QFrame:
        config, secrets = data["config"], data["secrets"]
        notes = config["notifications"]
        pending: dict[str, Any] = {}
        frame, layout = card(text("appset.alerts"))
        result = label()
        result.hide()

        def switch(key: str, words: str, on: Any) -> QCheckBox:
            box = QCheckBox(words)
            box.setChecked(bool(on))
            box.toggled.connect(lambda ticked: pending.__setitem__(key, ticked))
            return box

        def test(channel: str) -> QWidget:
            button = push(text("appset.send_test"))

            def sent(answer: dict) -> None:
                said(
                    result,
                    text("appset.test_sent" if answer.get("sent") else "appset.test_failed"),
                    "ok" if answer.get("sent") else "problem",
                )

            button.clicked.connect(
                lambda: self.call(
                    "POST",
                    f"/notifications/test?channel={channel}",
                    sent,
                    lambda e: said(result, str(e), "problem"),
                )
            )
            return button

        for key, words, configured, missing in (
            ("discord", "appset.discord", "discord_webhook_configured", "appset.discord_missing"),
            ("email", "appset.email", "smtp_configured", "appset.email_missing"),
        ):
            box = switch(f"notifications.{key}_enabled", text(words), notes.get(f"{key}_enabled"))
            layout.addLayout(buttons(box, test(key)))
            if not secrets.get(configured):
                layout.addWidget(label(text(missing), "muted"))
        # Phones: each turns lock-screen alerts on in its own app.
        layout.addWidget(label(text("desktop.push.phones"), "muted"))
        layout.addLayout(buttons(test("push")))
        layout.addWidget(label(text("desktop.device_alerts.shared_hint"), "muted"))

        more = push(text("appset.alert_details"), "plain")
        more.setCheckable(True)
        details = QWidget()
        inner = QVBoxLayout(details)
        inner.setContentsMargins(0, 0, 0, 0)
        numbers = QGridLayout()
        for i, (key, words) in enumerate(THRESHOLDS):
            section, name = key.split(".")
            spin = QDoubleSpinBox()
            spin.setRange(0, 100_000)
            spin.setDecimals(1)
            spin.setValue(float(config[section][name]))
            spin.valueChanged.connect(lambda value, k=key: pending.__setitem__(k, value))
            numbers.addWidget(field(text(words), spin), i // 3, i % 3)
        inner.addLayout(numbers)
        inner.addWidget(label(text("appset.which_events"), "step"))
        events = QGridLayout()
        for i, (key, on) in enumerate(notes["events"].items()):
            words = text(f"alert_event.{key}") if has(f"alert_event.{key}") else key
            events.addWidget(switch(f"notifications.events.{key}", words, on), i // 3, i % 3)
        inner.addLayout(events)
        details.setVisible(mode["technical"])
        more.setChecked(mode["technical"])
        more.toggled.connect(details.setVisible)
        layout.addLayout(buttons(more))
        layout.addWidget(details)

        save = push(text("appset.save_alerts"), "primary")

        def saved(answer: dict) -> None:
            for key in answer.get("applied") or {}:
                pending.pop(key, None)
            rejected = list((answer.get("rejected") or {}).values())
            said(
                result,
                " ".join(rejected) or text("settings.saved"),
                "problem" if rejected else "ok",
            )

        def send() -> None:
            if not pending:
                said(result, text("settings.nothing_changed"), "sub")
                return
            self.call(
                "PUT",
                "/settings",
                saved,
                lambda e: said(result, str(e), "problem"),
                body={"updates": dict(pending)},
            )

        save.clicked.connect(send)
        layout.addLayout(buttons(save))
        layout.addWidget(result)
        layout.addWidget(label(text("appset.secrets_note"), "muted"))
        return frame

    # -------------------------------------------------------- keep awake, maintenance
    def awake_card(self, data: dict, power: dict) -> QFrame:
        frame, layout = card(text("awake.title"))
        box = QCheckBox(text("awake.label"))
        box.setChecked(bool((data["config"].get("power") or {}).get("keep_awake")))
        box.toggled.connect(
            lambda on: self.call(
                "PUT",
                "/settings",
                self.then_reload(text("awake.turned_on" if on else "awake.turned_off")),
                body={"updates": {"power.keep_awake": on}},
            )
        )
        layout.addWidget(box)
        if not power.get("supported"):
            now = text("awake.unsupported")
        elif not power.get("enabled"):
            now = text("awake.off_now")
        elif power.get("active"):
            now = text("awake.active", names=", ".join(power.get("running") or []))
        else:
            now = text("awake.idle")
        layout.addWidget(label(now, "sub"))
        if power.get("problem"):
            layout.addWidget(label(power["problem"], "problem"))
        sleeps = [text(f"awake.still_{key}") for key in power.get("still_sleeps") or []]
        if sleeps:
            layout.addWidget(label(text("awake.still_title") + "\n" + "\n".join(sleeps), "notice"))
        battery = power.get("battery") or {}
        if battery.get("present") and mode["technical"]:
            source = "awake.plugged_in" if battery.get("plugged_in") else "awake.on_battery"
            percent = battery.get("percent")
            layout.addWidget(
                label(
                    text(
                        "awake.battery",
                        percent=text("value.unknown") if percent is None else percent,
                        source=text(source),
                    ),
                    "sub",
                )
            )
        return frame

    def maintenance_card(self, data: dict) -> QFrame:
        frame, layout = card(text("appset.maintenance"))
        box = QCheckBox(text("appset.maintenance_label"))
        box.setChecked(bool((data["config"].get("maintenance") or {}).get("enabled")))
        box.toggled.connect(
            lambda on: self.call(
                "POST",
                "/maintenance",
                self.then_reload(text("appset.maintenance_on" if on else "appset.maintenance_off")),
                body={"enabled": on},
            )
        )
        layout.addWidget(box)
        return frame

    # -------------------------------------------------------- Tailscale
    def show_tailscale(self, state: dict) -> None:
        frame, layout = card(text("security.tailscale"))
        connected = state.get("connected")
        layout.addWidget(
            label(
                text("desktop.ts.unknown")
                if connected is None
                else text("security.ts_connected" if connected else "security.ts_disconnected"),
                "ok" if connected else "problem",
            )
        )
        if state.get("error") and not connected:
            layout.addWidget(label(state["error"], "sub"))
        if state.get("account"):
            layout.addWidget(label(text("desktop.ts.account", account=state["account"])))
        if state.get("address"):
            address = label(text("desktop.ts.address", address=state["address"]))
            address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(address)
        step = self.window_.tailscale_step
        again = push(text("desktop.ts.reconnect"), "" if connected else "primary")
        again.clicked.connect(lambda: self.window_.show_screen("Tailscale"))
        other = push(text("desktop.tailscale.other_account"))
        other.clicked.connect(lambda: step.open_with(step.different_account))
        share = push(text("desktop.ts.share"))
        share.clicked.connect(lambda: self.window_.pc.open_link(TAILSCALE_MACHINES))
        layout.addLayout(buttons(again, other, share))
        layout.addWidget(label(text("desktop.ts.share_hint"), "muted"))
        self.fill("tailscale", frame)

    # -------------------------------------------------------- move to a new PC
    def export_card(self, sizes: dict) -> QFrame:
        def total(key: str) -> str:
            return size(sum(s.get(key) or 0 for s in sizes.get("servers") or []))

        frame, layout = card(text("export.title"))
        layout.addWidget(label(text("export.lead"), "sub"))
        always = QCheckBox(text("export.always", size=total("files")))
        always.setChecked(True)
        always.setEnabled(False)
        worlds = QCheckBox(text("export.worlds", size=total("worlds")))
        worlds.setChecked(True)
        backups = QCheckBox(text("export.backups", size=total("backups")))
        secrets = QCheckBox(text("export.secrets"))
        phrase = QLineEdit()
        phrase.setEchoMode(QLineEdit.EchoMode.Password)
        phrase_field = field(text("export.passphrase"), phrase, text("export.passphrase_hint"))
        phrase_field.hide()
        secrets.toggled.connect(phrase_field.setVisible)
        for widget in (always, worlds, backups, secrets, phrase_field):
            layout.addWidget(widget)
        start = push(text("export.button"), "primary")
        result = label()
        result.hide()
        start.clicked.connect(
            lambda: self.export(
                {
                    "worlds": worlds.isChecked(),
                    "backups": backups.isChecked(),
                    "secrets": secrets.isChecked(),
                    "passphrase": phrase.text() if secrets.isChecked() else None,
                },
                start,
                result,
                phrase,
            )
        )
        layout.addLayout(buttons(start))
        layout.addWidget(result)
        layout.addWidget(label(text("export.other_side"), "muted"))
        return frame

    def export(self, body: dict, start: QWidget, result: QLabel, phrase: QLineEdit) -> None:
        """Asks where to save, then the agent writes the file as a job, and
        it is downloaded there as it comes (it can be gigabytes)."""
        window, agent = self.window_, self.window_.agent
        path = window.pc.save_path("command-block-export.zip")
        if not path:
            return
        start.setEnabled(False)
        said(result, text("export.working"), "sub")

        def work() -> int:
            job_id = agent.call("POST", "/export", body)["job_id"]
            job = agent.call("GET", f"/jobs/{job_id}")
            while job["state"] in ("running", "queued"):
                time.sleep(1)
                job = agent.call("GET", f"/jobs/{job_id}")
            made = job.get("result") or {}
            if job["state"] != "succeeded" or not made.get("token"):
                raise AgentError(job.get("message") or text("export.failed"))
            name = quote(made.get("filename") or "export.zip")
            agent.download_to(f"/export/{made['token']}?filename={name}", path)
            return made.get("size_bytes") or 0

        def done(written: int) -> None:
            phrase.clear()
            said(
                result,
                text("export.done", size=size(written)) + " " + text("desktop.saved", path=path),
            )

        window.run(
            work,
            done,
            lambda e: said(result, str(e), "problem"),
            lambda: start.setEnabled(True),
        )

    # -------------------------------------------------------- Get help, startup
    def help_card(self) -> QFrame:
        frame, layout = card(text("gethelp.title"))
        layout.addWidget(label(text("gethelp.lead"), "sub"))
        result = label()
        result.hide()
        make = push(text("gethelp.button"))
        make.clicked.connect(
            lambda: get_help(self.window_, lambda words: said(result, words, "sub"))
        )
        guide = push(text("page.getting_started"), "plain")
        guide.clicked.connect(lambda: self.window_.open_list("Help", "guide"))
        layout.addLayout(buttons(make, guide))
        layout.addWidget(result)
        return frame

    def startup_card(self) -> QFrame:
        frame, layout = card(text("appset.startup"))
        layout.addWidget(label(text("appset.startup_hint"), "sub"))
        check = push(text("appset.startup_check"))
        report = QVBoxLayout()

        def show(found: dict) -> None:
            refill(report, [self.startup_report(found)])

        def failed(exc: Exception) -> None:
            refill(report, [label(str(exc), "problem")])

        check.clicked.connect(lambda: self.call("GET", "/system/startup", show, failed))
        layout.addLayout(buttons(check))
        layout.addLayout(report)
        return frame

    def startup_report(self, r: dict) -> QWidget:
        task, runtime, last = (
            r.get("task") or {},
            r.get("runtime") or {},
            r.get("last_startup") or {},
        )
        started = last.get("started_at")
        fully = any(e.get("event") == "controller_initialized" for e in last.get("events") or [])
        pairs = [
            (
                text("startup.registered"),
                three(r.get("registered"), "startup.found", "startup.not_found"),
            ),
            (
                text("startup.mode"),
                text({"boot": "startup.at_boot", "logon": "startup.at_logon"}[task["mode"]])
                if task.get("mode") in ("boot", "logon")
                else "—",
            ),
            (text("startup.points_here"), yes_no(r.get("points_to_current_app"))),
            (text("startup.last_run"), runtime.get("last_run_time") or text("value.unknown")),
            (
                text("startup.last_start"),
                text("startup.last_start_value", when=started, by=last.get("launched_by"))
                if started
                else text("startup.none"),
            ),
            (text("startup.initialised"), yes_no(fully) if started else "—"),
        ]
        if mode["technical"]:
            pairs += [
                (text("startup.mechanism"), r.get("mechanism") or "—"),
                (text("startup.command"), task.get("command") or "—"),
                (text("startup.folder"), task.get("working_directory") or "—"),
                (text("startup.last_result"), runtime.get("last_result") or text("value.unknown")),
            ]
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        verdict = r.get("verdict", "")
        column.addWidget(
            label(
                text(f"startup.{verdict.replace(' ', '_')}"),
                "ok" if verdict == "registered correctly" else "problem",
            )
        )
        column.addLayout(rows(pairs))
        for problem in r.get("problems") or []:
            column.addWidget(label(problem, "problem"))
        if mode["technical"] and r.get("startup_log"):
            column.addWidget(label(r["startup_log"], "address"))
        return box


# ------------------------------------------------------------ Updates
class Updates(Section):
    WORDS = "desktop.gear.updates"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, ["old", "update"])

    def allowed(self) -> bool:
        return self.owner()

    def load(self) -> None:
        self.call("GET", "/updates/old-copy", lambda found: self.show_old(found.get("old_copy")))
        self.call("GET", "/updates", self.show_update)

    def show_update(self, status: dict) -> None:
        frame, layout = card(text("update.title"))
        latest = status.get("latest") or {}
        if status.get("available"):
            layout.addWidget(label(text("update.available", version=latest.get("version")), "step"))
            notes = [
                line.strip().lstrip("-*# ")
                for line in (latest.get("notes") or "").splitlines()
                if line.strip()
            ][:30]
            if notes:
                layout.addWidget(label(text("update.whats_new"), "sub"))
                layout.addWidget(label("\n".join(f"•  {line}" for line in notes)))
            if status.get("cannot_install_reason"):
                layout.addWidget(label(status["cannot_install_reason"], "sub"))
        elif status.get("checked_at"):
            layout.addWidget(label(text("update.up_to_date", version=status.get("current"))))
        else:
            layout.addWidget(label(text("update.current", version=status.get("current"))))
        if status.get("error"):
            layout.addWidget(label(text("update.check_failed", reason=status["error"]), "problem"))
        layout.addWidget(
            label(
                text("update.checked", when=clock(status["checked_at"]))
                if status.get("checked_at")
                else text("update.checks_daily" if status.get("checking") else "update.checks_off"),
                "muted",
            )
        )
        last = status.get("last_result") or {}
        if last.get("ok") is True:
            layout.addWidget(label(text("update.last_ok", version=last.get("version") or ""), "ok"))
        elif last.get("ok") is False:
            words = text("update.last_failed", version=last.get("version") or "")
            if last.get("rolled_back") is not None:
                back = three(last["rolled_back"], "update.rolled_back", "update.not_rolled_back")
                words = f"{words} {back}"
            layout.addWidget(label(words, "problem"))
            if last.get("message"):
                layout.addWidget(label(last["message"], "sub"))
        install = push(text("update.install"), "primary")
        install.setVisible(bool(status.get("available") and status.get("can_install")))
        install.clicked.connect(lambda: self.install(latest.get("version") or ""))
        check = push(text("update.check_now"))

        def checked(now: dict) -> None:
            self.show_update(now)
            if now.get("error"):
                self.say(now["error"], "problem")

        check.clicked.connect(lambda: self.call("POST", "/updates/check", checked))
        page = push(text("update.release_page"), "plain")
        page.setVisible(bool(status.get("available") and latest.get("page")))
        page.clicked.connect(lambda: self.window_.pc.open_link(latest.get("page") or ""))
        layout.addLayout(buttons(install, check, page))
        self.fill("update", frame)

    def install(self, version: str) -> None:
        def asked(check: dict) -> None:
            players = check.get("players_online")
            body = [text("update.confirm_body")]
            if players is None:
                body.append(text("update.confirm_players_unknown"))
            elif players > 0:
                body.append(text("update.confirm_players", count=players))
            if self.window_.confirm(
                text("update.confirm_title", version=version),
                " ".join(body),
                text("update.install_anyway" if players else "update.install"),
            ):
                self.call(
                    "POST", "/updates/install", lambda _r: self.say(text("desktop.update.started"))
                )

        self.call("GET", "/updates/preflight", asked)

    def show_old(self, old: dict | None) -> None:
        pc = self.window_.pc
        if not old or not old.get("offer") or pc.setting(f"old_copy_hidden:{old['path']}"):
            self.fill("old")
            return
        frame, layout = card(text("oldcopy.title"))
        layout.addWidget(label(text("oldcopy.body", path=old["path"])))
        layout.addWidget(label(text("oldcopy.keeps"), "sub"))
        if old.get("problems"):
            layout.addWidget(label("\n".join([text("oldcopy.cant"), *old["problems"]]), "problem"))
        remove = push(text("oldcopy.remove"), "danger")
        remove.setVisible(not old.get("problems"))

        def removed(result: dict) -> None:
            self.load()
            self.say(
                text("oldcopy.removed")
                if result.get("folder_removed")
                else text("oldcopy.removed_kept", count=len(result.get("kept") or []))
            )

        def ask() -> None:
            if self.window_.confirm(
                text("oldcopy.confirm_title"),
                text("oldcopy.confirm_body", path=old["path"]),
                text("oldcopy.remove"),
            ):
                self.call("POST", "/updates/old-copy/remove", removed)

        remove.clicked.connect(ask)
        later = push(text("oldcopy.not_now"), "plain")

        def hide() -> None:
            pc.set_setting(f"old_copy_hidden:{old['path']}", True)
            self.fill("old")

        later.clicked.connect(hide)
        layout.addLayout(buttons(remove, later))
        self.fill("old", frame)


# ------------------------------------------------------------ Signed-in devices
class Devices(Section):
    WORDS = "desktop.gear.devices"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, ["devices", "pair", "facts", "audit"])

    def load(self) -> None:
        self.call("GET", "/sessions", self.show_devices)
        self.call("GET", "/pairing", self.show_pairing, lambda _e: self.fill("pair"))
        if self.owner() and mode["technical"]:
            self.call("GET", "/security", self.show_facts)
            self.call("GET", "/security/audit?limit=100", self.show_audit)
        else:
            self.fill("facts")
            self.fill("audit")

    def show_devices(self, found: dict) -> None:
        devices = found.get("sessions") or []
        frame, layout = card(text("security.sessions", count=len(devices)))
        table = QGridLayout()
        table.setHorizontalSpacing(16)
        heads = ["security.col_device", "security.col_user", "security.col_signed_in"]
        heads += ["security.col_last_used"] + (["security.col_from"] if mode["technical"] else [])
        for col, words in enumerate(heads):
            table.addWidget(label(text(words), "sub"), 0, col)
        for row, device in enumerate(devices, start=1):
            name = (device.get("label") or text("security.device_unknown"))[:40]
            tags = [text("security.this_device")] if device.get("current") else []
            if device.get("remember"):
                kept = device.get("expires_at") is None
                tags.append(text("security.until_signed_out" if kept else "security.kept"))
            cells = [
                " · ".join([name, *tags]),
                device.get("user") or "",
                clock(device.get("created_at")),
                clock(device.get("last_used")),
            ] + ([device.get("source_ip") or "—"] if mode["technical"] else [])
            for col, value in enumerate(cells):
                table.addWidget(label(value), row, col)
            out = push(text("security.device_out"))
            out.setAccessibleName(f"{text('security.device_out')} {name}")
            out.clicked.connect(lambda _=False, d=device: self.sign_out(d))
            table.addWidget(out, row, len(cells))
        table.setColumnStretch(0, 1)
        if not devices:
            layout.addWidget(label(text("security.no_sessions"), "sub"))
        layout.addLayout(table)
        if found.get("remember_days"):
            layout.addWidget(
                label(text("security.kept_note", days=f"{found['remember_days']:g}"), "muted")
            )
        if self.owner() and mode["technical"]:
            rotate = push(text("security.rotate"))
            rotate.clicked.connect(self.rotate)
            everyone = push(text("security.revoke"), "danger")
            everyone.clicked.connect(self.sign_everyone_out)
            layout.addLayout(buttons(rotate, everyone))
        self.fill("devices", frame)

    def sign_out(self, device: dict) -> None:
        window = self.window_
        if device.get("current"):
            window.sign_out()
            return
        name = device.get("label") or text("security.device_unknown")
        if window.confirm(
            text("security.device_out_title", device=name),
            text("security.device_out_body"),
            text("security.device_out"),
        ):
            self.call(
                "DELETE",
                f"/sessions/{quote(device['id'], safe='')}",
                self.then_reload(text("mobile.account.signed_out_device", device=name)),
            )

    def rotate(self) -> None:
        window = self.window_

        def rotated(result: dict) -> None:
            place = window.agent.place
            if window.pc.saved_sign_in(place):
                window.pc.save_sign_in(place, result["token"])
            window.agent.token = result["token"]
            self.say(text("security.rotated"))

        self.call("POST", "/auth/rotate", rotated)

    def sign_everyone_out(self) -> None:
        window = self.window_
        if window.confirm(
            text("security.revoke_title"), text("security.revoke_body"), text("security.revoke")
        ):
            self.call("POST", "/security/revoke-sessions", lambda _r: window.signed_out())

    def show_pairing(self, code: dict) -> None:
        frame, layout = card(text("desktop.pair.title"))
        if not code.get("ok"):
            layout.addWidget(label(text("pair.not_ready")))
            layout.addWidget(label(code.get("reason") or "", "sub"))
            self.fill("pair", frame)
            return
        layout.addWidget(label(text("desktop.pair.words"), "sub"))
        qr = QrCode()
        qr.setAccessibleName(text("pair.qr_label"))
        qr.show_rows(code.get("qr") or [])
        layout.addWidget(qr)
        layout.addLayout(link_row(self.window_, code.get("url") or ""))
        layout.addWidget(label(text("pair.no_secret"), "muted"))
        layout.addWidget(label(text("pair.rescan"), "muted"))
        if mode["technical"]:
            layout.addLayout(
                rows(
                    [
                        (text("pair.fingerprint"), code.get("fingerprint") or "—"),
                        (text("pair.api"), str(code.get("api_version"))),
                        (text("mobile.settings.pc_address"), code.get("address_url") or "—"),
                    ]
                )
            )
        self.fill("pair", frame)

    def show_facts(self, data: dict) -> None:
        tls, tailscale = data.get("tls") or {}, data.get("tailscale") or {}
        auth = data.get("authentication") or {}
        https_off = data.get("https_enabled") is False
        days = tls.get("days_remaining")
        pairs = [
            (
                text("security.https"),
                text("security.https_off_note")
                if https_off
                else text(
                    "security.issued_by",
                    issuer=tls.get("issuer") or text("security.unknown_issuer"),
                )
                if tls.get("parsed")
                else tls.get("parse_error") or text("security.cert_unreadable"),
            ),
        ]
        if not https_off:
            pairs += [
                (
                    text("security.expiry"),
                    f"{round(days)} {text('security.days_left')}"
                    if days is not None
                    else text("security.expiry_unreadable"),
                ),
                (text("security.key_match"), yes_no(tls.get("key_matches_certificate"))),
                (
                    text("security.fingerprint"),
                    tls.get("fingerprint") or text("security.cert_unreadable"),
                ),
            ]
        connected = tailscale.get("connected")
        pairs += [
            (
                text("security.tailscale"),
                text("value.unknown")
                if connected is None
                else text("security.ts_connected" if connected else "security.ts_disconnected"),
            ),
            (
                text("security.auth"),
                text("security.auth_on" if auth.get("enabled") else "security.auth_off")
                + " · "
                + text("security.auth_note", hours=auth.get("session_hours")),
            ),
            (
                text("security.failed"),
                f"{data.get('failed_logins_24h')} ({text('security.failed_note')})",
            ),
        ]
        frame, layout = card(text("page.security"))
        layout.addLayout(rows(pairs))
        layout.addWidget(label(text("security.bind", address=data.get("bind_address")), "muted"))
        self.fill("facts", frame)

    def show_audit(self, found: dict) -> None:
        entries = found.get("entries") or []
        frame, layout = card(text("security.audit"))
        if not entries:
            layout.addWidget(label(text("security.audit_none"), "sub"))
            self.fill("audit", frame)
            return
        heads = ["when", "user", "action", "target", "result"]
        table = QTableWidget(len(entries), len(heads))
        table.setHorizontalHeaderLabels([text(f"security.col_{h}") for h in heads])
        table.setAccessibleName(text("security.audit"))
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setMinimumHeight(300)
        for row, entry in enumerate(entries):
            ok = entry.get("result") == "ok"
            values = [
                clock(entry.get("ts")),
                entry.get("user") or "—",
                entry.get("action") or "",
                entry.get("target") or "—",
                text("security.result_ok") if ok else str(entry.get("result")),
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(value))
        table.resizeColumnsToContents()
        layout.addWidget(table)
        self.fill("audit", frame)


# ------------------------------------------------------------ Your account
class Account(Section):
    WORDS = "account.title"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, ["me", "alerts"])

    def load(self) -> None:
        window = self.window_
        self.call("GET", "/account/alerts", self.show_alerts)
        frame, layout = card(text("account.title"))
        name = window.user or text("value.unknown")
        if self.owner():
            layout.addWidget(label(text("account.owner", name=name)))
            layout.addWidget(label(text("guide.password_text"), "sub"))
            layout.addWidget(label("python -m installer.reset_password", "address"))
        else:
            layout.addWidget(label(text("account.helper", name=name)))
            boxes = []
            for words in ("account.current", "account.new", "mobile.invite.repeat"):
                box = QLineEdit()
                box.setEchoMode(QLineEdit.EchoMode.Password)
                boxes.append(box)
                layout.addWidget(field(text(words), box))
            layout.addWidget(label(text("mobile.account.forgot"), "muted"))
            change = push(text("account.change"), "primary")
            change.clicked.connect(lambda: self.change(*boxes))
            layout.addLayout(buttons(change))
        out = push(text("action.sign_out"))
        out.clicked.connect(window.sign_out)
        layout.addLayout(buttons(out))
        self.fill("me", frame)

    def change(self, current: QLineEdit, new: QLineEdit, again: QLineEdit) -> None:
        if new.text() != again.text():
            self.say(text("mobile.invite.mismatch"), "problem")
            return

        def changed(_result: Any) -> None:
            for box in (current, new, again):
                box.clear()
            self.say(text("account.changed"))

        self.call(
            "POST",
            "/account/password",
            changed,
            body={"current": current.text(), "new": new.text()},
        )

    def show_alerts(self, found: dict) -> None:
        """This device's alerts: shown on this PC or not, and the shared list
        or its own choice (kept with this sign-in on the agent)."""
        pc = self.window_.pc
        frame, layout = card(text("desktop.device_alerts.title"))
        here = QCheckBox(text("desktop.device_alerts.show_here"))
        here.setChecked(pc.setting("show_alerts") not in (False, "false"))
        here.toggled.connect(lambda on: pc.set_setting("show_alerts", on))
        layout.addWidget(here)
        own = QCheckBox(text("desktop.device_alerts.own"))
        own.setChecked(bool(found.get("own")))
        layout.addWidget(own)
        layout.addWidget(label(text("desktop.device_alerts.own_hint"), "muted"))
        events = QWidget()
        grid = QGridLayout(events)
        grid.setContentsMargins(0, 0, 0, 0)
        boxes: dict[str, QCheckBox] = {}
        for i, (key, on) in enumerate((found.get("events") or {}).items()):
            box = QCheckBox(text(f"alert_event.{key}") if has(f"alert_event.{key}") else key)
            box.setChecked(bool(on))
            boxes[key] = box
            grid.addWidget(box, i // 3, i % 3)
        events.setEnabled(own.isChecked())
        own.toggled.connect(events.setEnabled)
        layout.addWidget(events)
        save = push(text("appset.save_alerts"), "primary")
        save.clicked.connect(
            lambda: self.call(
                "PUT",
                "/account/alerts",
                self.then_reload(text("settings.saved")),
                body={
                    "own": own.isChecked(),
                    "events": [key for key, box in boxes.items() if box.isChecked()],
                },
            )
        )
        layout.addLayout(buttons(save))
        self.fill("alerts", frame)
