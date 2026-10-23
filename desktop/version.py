"""Two cards of the Settings tab: Version (what the server is, changing its
kind or version, and going back) and Bedrock players (Geyser on a Java
server).

Two honesty rules: the version shown is the one the
server's own console printed, "Unknown" until it starts. And a change says
what it will do before it does it: "Check first", then "Make the change"."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QSpinBox

from .overview import card
from .ui import clock, field, label, push
from .words import mode, text

if TYPE_CHECKING:
    from .settings import SettingsPage


def facts(rows: list[tuple[str, object]]) -> QGridLayout:
    """Name and value pairs; a value nobody knows says Unknown."""
    layout = QGridLayout()
    layout.setHorizontalSpacing(16)
    for i, (name, value) in enumerate(rows):
        layout.addWidget(label(name, "sub"), i, 0)
        layout.addWidget(label(text("state.unknown") if value in (None, "") else str(value)), i, 1)
    layout.setColumnStretch(1, 1)
    return layout


def report_lines(report: dict) -> list[tuple[str, str]]:
    """What "Check first" found, as (words, kind) lines, before anything changes."""
    now, after, java = report["current"], report["target"], report.get("java") or {}
    lines = [
        (
            f"{text('version.now')}: "
            + (
                f"{now.get('type_name')} {now.get('minecraft_version')}"
                if now.get("observed")
                else text("state.unknown")
            ),
            "sub",
        ),
        (f"{text('version.after')}: {after['type_name']} {after['minecraft_version']}", "step"),
    ]
    if report.get("java_required"):
        lines.append((f"{text('version.java_needed')}: Java {report['java_required']}", "sub"))
    if mode["technical"] and java.get("detail"):
        lines.append((f"{text('version.java_here')}: {java['detail']}", "sub"))
    if report.get("downgrade_warning"):
        lines.append((text("version.downgrade"), "problem"))
    if report.get("type_change"):
        if report.get("gentle_type_change"):
            lines.append((text("version.type_change_gentle"), "sub"))
        else:
            words = text(
                "version.type_change", **{"from": now.get("type_name"), "to": after["type_name"]}
            )
            lines.append((words, "problem"))
    if java.get("verdict") == "incompatible":
        lines.append((java.get("detail") or "", "problem"))
    elif java.get("verdict") == "unknown":
        lines.append((text("version.java_unknown"), "sub"))
    if report.get("crossplay") and not report.get("crossplay_available"):
        lines.append((text("version.crossplay_lost"), "problem"))
    for key, note, kind in (
        ("not_supporting", "version.not_supporting_note", "problem"),
        ("moved_aside", "version.moved_aside_note", "problem"),
        ("unknown_support", "version.unknown_support_note", "sub"),
    ):
        found = report.get(key) or []
        if found:
            words = text(f"version.{key}", count=len(found), version=after.get("minecraft_version"))
            if key != "unknown_support":
                words += f" {', '.join(found[:8])}."
            lines.append((f"{words} {text(note)}", kind))
    lines.append((text("version.backup_first"), "sub"))
    return lines


def version_card(page: SettingsPage, current: dict) -> QFrame:
    frame, layout = card(text("version.title"))
    caps, window = current["capabilities"], page.window_
    rows: list[tuple[str, object]] = [
        (text("version.kind"), caps.get("name")),
        (text("version.minecraft"), current.get("minecraft_version")),
    ]
    if current.get("loader_name"):
        rows.append((current["loader_name"], current.get("loader_version")))
    if mode["technical"]:
        rows.append((text("version.seen_in"), current.get("minecraft_version_source")))
    layout.addLayout(facts(rows))
    if not current.get("minecraft_version"):
        layout.addWidget(label(text("version.not_seen_yet"), "sub"))
    if current.get("detected_type") and current["detected_type"] != current["type"]:
        words = text("version.type_disagrees", console=current["detected_type"], set=caps["name"])
        layout.addWidget(label(words, "problem"))
    editable = page.may("settings.server")
    pending = current.get("pending_version")
    if pending:
        version = pending.get("minecraft_version")
        layout.addWidget(label(text("version.pending", version=version), "notice"))
        layout.addWidget(label(text("version.pending_note"), "sub"))
        if caps.get("modrinth"):
            found = label(kind="sub")
            look = push(text("version.updates_check", version=version))

            def show_updates(answer: dict) -> None:
                updates = answer.get("updates") or []
                lines = [
                    text(
                        "version.updates_found" if updates else "version.updates_none",
                        count=len(updates),
                        version=version,
                    )
                ] + [
                    f"{u['name']}: {u.get('installed_version') or '?'} → {u['latest_version']}"
                    for u in updates
                ]
                found.setText("\n".join(lines))

            look.clicked.connect(
                lambda: page.ask(
                    "GET",
                    "/mods/updates?refresh=true",
                    show_updates,
                    lambda e: found.setText(str(e)),
                )
            )
            row = QHBoxLayout()
            row.addWidget(look)
            row.addStretch()
            layout.addLayout(row)
            layout.addWidget(found)
    said = label(kind="sub")
    if current.get("eula_required") and editable:
        layout.addWidget(label(text("version.eula_needed"), "problem"))
        agree = push(text("version.eula_accept"), "primary")
        agree.clicked.connect(
            lambda: page.ask(
                "POST",
                "/eula",
                lambda _r: page.done(text("version.eula_done")),
                lambda e: said.setText(str(e)),
            )
        )
        layout.addWidget(agree)

    if editable:
        layout.addWidget(label(text("version.change_opener"), "step"))
        edition = caps.get("edition") or "java"
        kinds = QComboBox()
        for kind in window.types:
            if (kind.get("edition") or "java") == edition:
                kinds.addItem(kind["name"], kind["id"])
        kinds.setCurrentIndex(max(0, kinds.findData(current["type"])))
        versions, loaders = QComboBox(), QComboBox()
        snapshots = QCheckBox(text("new.show_snapshots"))
        loader_box = field(text("version.loader_pick"), loaders)
        grid = QGridLayout()
        grid.addWidget(field(text("version.change_kind"), kinds), 0, 0)
        grid.addWidget(field(text("version.change_to"), versions), 0, 1)
        grid.addWidget(loader_box, 0, 2)
        layout.addLayout(grid)
        layout.addWidget(snapshots)
        layout.addWidget(
            label(
                text("version.only_bedrock" if edition == "bedrock" else "version.only_java"),
                "muted",
            )
        )
        found_lines = QGridLayout()
        layout.addLayout(found_lines)
        listed: dict = {}
        checked: dict = {}

        def say_found(lines: list[tuple[str, str]]) -> None:
            while found_lines.count():
                old = found_lines.takeAt(0).widget()
                if old is not None:
                    old.setParent(None)
            for i, (words, kind) in enumerate(lines):
                found_lines.addWidget(label(words, kind), i, 0)

        def fill_versions() -> None:
            keep = versions.currentData()
            versions.clear()
            for v in listed.get("versions") or []:
                if v["stable"] or snapshots.isChecked():
                    words = (
                        v["minecraft"]
                        if v["stable"]
                        else text("new.snapshot_option", version=v["minecraft"])
                    )
                    versions.addItem(words, v["minecraft"])
            versions.setCurrentIndex(max(0, versions.findData(keep or listed.get("latest_stable"))))

        def fill_loaders() -> None:
            checked.clear()
            say_found([])
            entry: dict = next(
                (
                    v
                    for v in listed.get("versions") or []
                    if v["minecraft"] == versions.currentData()
                ),
                {},
            )
            loaders.clear()
            for loader in entry.get("loaders") or []:
                loaders.addItem(loader, loader)
            loader_box.setVisible(loaders.count() > 0 and mode["technical"])

        def load_versions() -> None:
            listed.clear()
            versions.clear()
            say_found([(text("status.loading"), "sub")])

            def got(payload: dict) -> None:
                listed.update(payload)
                snapshots.setVisible(bool(payload.get("snapshots")))
                lines = (
                    [(text("new.bedrock_versions"), "sub")] if payload.get("latest_only") else []
                )
                fill_versions()
                say_found(lines)

            window.run(
                lambda: window.agent.call("GET", f"/server-types/{kinds.currentData()}/versions"),
                got,
                lambda e: say_found([(str(e), "problem")]),
            )

        def choice() -> dict:
            return {
                "type": kinds.currentData(),
                "minecraft_version": versions.currentData(),
                "loader_version": loaders.currentData(),
            }

        def check() -> None:
            def got(report: dict) -> None:
                checked.clear()
                checked.update(report)
                say_found(report_lines(report))

            page.ask(
                "POST",
                "/version/preflight",
                got,
                lambda e: say_found([(str(e), "problem")]),
                body=choice(),
            )

        def change() -> None:
            if not checked:
                said.setText(text("version.check_first"))
                return
            after = checked["target"]
            name = f"{after['type_name']} {after['minecraft_version']}"
            if not window.confirm(
                text("version.confirm_title", name=name),
                text("version.confirm_body"),
                text("version.confirm_button"),
            ):
                return

            def done(result: dict) -> None:
                notes = [text("version.changed", version=name)]
                if result.get("verified") is False:
                    notes.append(text("new.unverified"))
                if result.get("crossplay_turned_off"):
                    notes.append(text("version.crossplay_off"))
                page.done(" ".join(notes))

            said.setText(text("version.changing"))
            page.ask(
                "POST",
                "/version",
                done,
                lambda e: said.setText(str(e)),
                body={**choice(), "confirm": True},
                slow=True,
            )

        kinds.currentIndexChanged.connect(load_versions)
        versions.currentIndexChanged.connect(fill_loaders)
        snapshots.toggled.connect(fill_versions)
        snapshots.hide()
        loader_box.hide()
        buttons = QHBoxLayout()
        # The list comes from the official source over the internet, so it
        # is asked for when someone wants to change, not every time.
        show = push(text("version.change_opener"))
        check_button = push(text("version.check"))
        change_button = push(text("version.change"), "primary")
        for widget in (kinds, versions, snapshots, check_button, change_button):
            widget.setEnabled(False)

        def open_changer() -> None:
            show.hide()
            for widget in (kinds, versions, snapshots, check_button, change_button):
                widget.setEnabled(True)
            load_versions()

        show.clicked.connect(open_changer)
        check_button.clicked.connect(check)
        change_button.clicked.connect(change)
        for widget in (show, check_button, change_button):
            buttons.addWidget(widget)
        last = current.get("last_change") or {}
        if last.get("can_roll_back"):
            back = push(text("version.back"))

            def roll_back() -> None:
                if window.confirm(
                    text("version.back_title"),
                    text("version.back_body"),
                    text("version.back_button"),
                ):
                    said.setText(text("version.going_back"))
                    page.ask(
                        "POST",
                        "/version/roll-back",
                        lambda _r: page.done(text("version.went_back")),
                        lambda e: said.setText(str(e)),
                        slow=True,
                    )

            back.clicked.connect(roll_back)
            buttons.addWidget(back)
        buttons.addStretch()
        layout.addLayout(buttons)
    layout.addWidget(said)
    last = current.get("last_change") or {}
    if last.get("at"):
        what = f"{last.get('type')} {last.get('minecraft_version')}"
        layout.addWidget(
            label(text("version.last_change", what=what, when=clock(last["at"])), "muted")
        )
    return frame


def crossplay_card(page: SettingsPage, status: dict) -> QFrame:
    """Bedrock players on a Java server: switching it on puts Geyser and
    Floodgate into the server's files, so the server is stopped first. The
    address is only ever this PC's measured Tailscale address."""
    frame, layout = card(text("cross.title"))
    if not status.get("available"):
        layout.addWidget(
            label(status.get("unavailable_reason") or text("cross.not_for_type"), "sub")
        )
        layout.addWidget(label(text("cross.switch_type"), "sub"))
        return frame
    layout.addWidget(label(text("cross.what"), "sub"))
    if status.get("missing_mod"):
        layout.addWidget(label(text("cross.needs_mod", mod=status["missing_mod"]), "notice"))
    port = QSpinBox()
    port.setRange(1024, 65535)
    port.setValue(status.get("port") or status.get("suggested_port") or status["default_port"])
    if status.get("enabled"):
        layout.addLayout(
            facts(
                [
                    (text("cross.address"), status.get("address")),
                    (
                        text("cross.ready"),
                        text("cross.ready_yes" if status.get("ready") else "cross.files_missing"),
                    ),
                    (
                        text("cross.names"),
                        text("cross.names_value", prefix=status.get("username_prefix")),
                    ),
                ]
            )
        )
        if status.get("address_unknown"):
            layout.addWidget(label(status["address_unknown"], "sub"))
    else:
        layout.addWidget(field(text("cross.port"), port, text("cross.port_hint")))
    said = label(kind="sub")
    on = not status.get("enabled")
    turn = push(text("cross.turn_on" if on else "cross.turn_off"), "primary" if on else "")
    turn.setEnabled(page.may("settings.server"))

    def switch() -> None:
        if on and not page.window_.confirm(
            text("cross.on_title"),
            text("cross.on_body", port=port.value()),
            text("cross.on_button"),
        ):
            return
        said.setText(text("cross.turning_on" if on else "cross.turning_off"))
        page.ask(
            "PUT",
            "/crossplay",
            lambda _r: page.done(text("cross.on_done" if on else "cross.off_done")),
            lambda e: said.setText(str(e)),
            body={"enabled": on, "port": port.value() if on else None},
            slow=True,
        )

    turn.clicked.connect(switch)
    row = QHBoxLayout()
    row.addWidget(turn)
    row.addWidget(label(text("cross.stopped_first"), "muted"), 1)
    layout.addLayout(row)
    layout.addWidget(said)
    layout.addWidget(label(text("cross.differences"), "step"))
    for line in status.get("differences") or []:
        layout.addWidget(label(f"• {line}", "sub"))
    return frame
