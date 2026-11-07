"""Two cards of the Settings tab: Version (what the server is, changing its
kind or version, and going back) and Bedrock players (Geyser on a Java
server).

Two honesty rules: the version shown is the one the
server's own console printed, "Unknown" until it starts. And a change says
what it will do before it does it: "Check first", then "Make the change"."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .gear import buttons
from .overview import card
from .ui import clock, field, label, push, refill
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

    if current.get("custom_jar"):
        own_jar(page, layout, caps, said)
    elif editable:
        # The type changes on its own card (Change server type, owner only).
        layout.addWidget(label(text("desktop.typechange.version_opener"), "step"))
        kind = current["type"]
        versions, loaders = QComboBox(), QComboBox()
        snapshots = QCheckBox(text("new.show_snapshots"))
        loader_box = field(text("version.loader_pick"), loaders)
        grid = QGridLayout()
        grid.addWidget(field(text("version.change_to"), versions), 0, 0)
        grid.addWidget(loader_box, 0, 1)
        layout.addLayout(grid)
        layout.addWidget(snapshots)
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
                if payload.get("source_problem"):
                    lines.append((payload["source_problem"], "problem"))
                fill_versions()
                say_found(lines)

            window.run(
                lambda: window.agent.call("GET", f"/server-types/{kind}/versions"),
                got,
                lambda e: say_found([(str(e), "problem")]),
            )

        def choice() -> dict:
            return {
                "type": kind,
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

        versions.currentIndexChanged.connect(fill_loaders)
        snapshots.toggled.connect(fill_versions)
        snapshots.hide()
        loader_box.hide()
        buttons = QHBoxLayout()
        # The list comes from the official source over the internet, so it
        # is asked for when someone wants to change, not every time.
        show = push(text("desktop.typechange.version_opener"))
        check_button = push(text("version.check"))
        change_button = push(text("version.change"), "primary")
        for widget in (versions, snapshots, check_button, change_button):
            widget.setEnabled(False)

        def open_changer() -> None:
            show.hide()
            for widget in (versions, snapshots, check_button, change_button):
                widget.setEnabled(True)
            load_versions()

        show.clicked.connect(open_changer)
        check_button.clicked.connect(check)
        change_button.clicked.connect(change)
        for widget in (show, check_button, change_button):
            buttons.addWidget(widget)
        last = current.get("last_change") or {}
        # After a type change, its card has the way back (Undo type change).
        if last.get("can_roll_back") and last.get("previous_type") in (None, current["type"]):
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
    if caps.get("changes_farms") and mode["technical"] and page.may("settings.game"):
        spawns = push(text("new.vanilla_spawns"))
        spawns.clicked.connect(
            lambda: page.ask(
                "POST",
                "/game-settings/vanilla-spawns",
                lambda _r: page.done(text("spawns.done")),
                lambda e: said.setText(str(e)),
            )
        )
        layout.addWidget(label(text("spawns.hint"), "sub"))
        row = QHBoxLayout()
        row.addWidget(spawns)
        row.addStretch()
        layout.addLayout(row)
    layout.addWidget(said)
    last = current.get("last_change") or {}
    if last.get("at"):
        what = f"{last.get('type')} {last.get('minecraft_version')}"
        layout.addWidget(
            label(text("version.last_change", what=what, when=clock(last["at"])), "muted")
        )
    return frame


def type_lines(report: dict) -> list[tuple[str, str]]:
    """What comes along and what doesn't, before a type change."""
    files, after = report.get("files") or {}, report["target"]["type_name"]
    lines = [(text("desktop.typechange.keeps", version=report["minecraft_version"]), "step")]
    if files.get("dimensions"):
        lines.append((text("desktop.typechange.dimensions", type=after), "sub"))
    for key, words_key, kind in (
        ("kept", "desktop.typechange.kept", "sub"),
        ("back", "desktop.typechange.back", "sub"),
        ("left", "desktop.typechange.left", "sub"),
        ("out", "desktop.typechange.out", "problem"),
    ):
        names = [n[1] if isinstance(n, list) else n for n in files.get(key) or []]
        if names:
            words = text(words_key, count=len(names), type=after, folder=files.get("folder"))
            lines.append((f"{words} {', '.join(names[:8])}.", kind))
    lines += [line for line in report_lines(report)[2:] if "moved_aside" not in line[0]]
    return lines


def type_card(page: SettingsPage, current: dict) -> QFrame:
    """Change server type (owner only): the world, settings and Minecraft
    version stay. In Technical mode also a copy for the other edition."""
    frame, layout = card(text("desktop.typechange.title"))
    layout.addWidget(label(text("desktop.typechange.intro"), "sub"))
    body = QVBoxLayout()
    layout.addLayout(body)
    page.ask(
        "GET",
        f"/type-change?technical={'true' if mode['technical'] else 'false'}",
        lambda found: fill_type_card(page, body, current, found),
        lambda e: body.addWidget(label(str(e), "problem")),
    )
    return frame


def fill_type_card(page: SettingsPage, layout: QVBoxLayout, current: dict, found: dict) -> None:
    window = page.window_
    said = label(kind="sub")
    if found.get("problem"):
        layout.addWidget(label(found["problem"], "notice"))
    elif found["targets"]:
        kinds = QComboBox()
        for target in found["targets"]:
            kinds.addItem(target["name"], target["id"])
        layout.addWidget(field(text("version.change_kind"), kinds))
        lines = QVBoxLayout()
        layout.addLayout(lines)
        checked: dict = {}

        def say(found_lines: list[tuple[str, str]]) -> None:
            refill(lines, [label(words, kind) for words, kind in found_lines])

        def body() -> dict:
            return {"type": kinds.currentData(), "technical": bool(mode["technical"])}

        def check() -> None:
            checked.clear()
            say([(text("version.checking"), "sub")])

            def got(report: dict) -> None:
                checked.update(report)
                say(type_lines(report))

            page.ask(
                "POST",
                "/type-change/preflight",
                got,
                lambda e: say([(str(e), "problem")]),
                body=body(),
            )

        def change() -> None:
            if not checked:
                said.setText(text("version.check_first"))
                return
            name = checked["target"]["type_name"]
            words = text("desktop.typechange.confirm_body")
            if found.get("players_online"):
                words += " " + text("update.confirm_players", count=found["players_online"])
            yes = text(
                "desktop.typechange.stop_and_change"
                if found["running"]
                else "desktop.typechange.change"
            )
            if not window.confirm(text("desktop.typechange.confirm_title", type=name), words, yes):
                return
            said.setText(text("version.changing"))
            page.ask(
                "POST",
                "/type-change",
                lambda _r: page.done(text("desktop.typechange.done", type=name)),
                lambda e: said.setText(str(e)),
                body={**body(), "stop_first": True, "confirm": True},
                slow=True,
            )

        def forget(_index: int) -> None:
            checked.clear()
            say([])

        kinds.currentIndexChanged.connect(forget)
        check_button = push(text("version.check"))
        check_button.clicked.connect(check)
        go = push(
            text(
                "desktop.typechange.stop_and_change"
                if found["running"]
                else "desktop.typechange.change"
            ),
            "primary",
        )
        go.clicked.connect(change)
        layout.addLayout(buttons(check_button, go))
    last = current.get("last_change") or {}
    if last.get("previous_type") not in (None, current["type"]):
        similar(page, layout)
        if last.get("can_roll_back"):
            undo = push(text("desktop.typechange.undo"))

            def roll_back() -> None:
                if window.confirm(
                    text("desktop.typechange.undo_title"),
                    text("version.back_body"),
                    text("desktop.typechange.undo"),
                ):
                    said.setText(text("version.going_back"))
                    page.ask(
                        "POST",
                        "/version/roll-back",
                        lambda _r: page.done(text("version.went_back")),
                        lambda e: said.setText(str(e)),
                        slow=True,
                    )

            undo.clicked.connect(roll_back)
            layout.addLayout(buttons(undo))
    layout.addWidget(said)
    if found.get("copy_to_other_edition"):
        world_copy(page, layout, current, found["copy_to_other_edition"])


def similar(page: SettingsPage, layout: QVBoxLayout) -> None:
    """After a type change: the same add-ons for the new type, then
    hand-checked look-alikes, each with its own Add button."""
    rows = QVBoxLayout()
    layout.addLayout(rows)

    def add(project: str, button) -> None:
        def failed(exc: Exception) -> None:
            button.setEnabled(True)
            button.setToolTip(str(exc))

        button.setEnabled(False)
        page.ask(
            "POST",
            "/mods/install",
            lambda _r: button.setText(text("desktop.typechange.added")),
            failed,
            body={"project": project},
            slow=True,
        )

    def show(found: dict) -> None:
        shown: list[QWidget] = []
        if found["suggestions"]:
            shown.append(label(text("desktop.typechange.similar", type=found["type"]), "step"))
        for item in found["suggestions"]:
            kind = (
                "desktop.typechange.same" if item["kind"] == "same" else "desktop.typechange.alike"
            )
            row = QFrame()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(label(text(kind, name=item["name"], old=item["for"]), "sub"), 1)
            button = push(text("desktop.typechange.add"))
            button.clicked.connect(lambda _=False, p=item["project"], b=button: add(p, b))
            line.addWidget(button)
            shown.append(row)
        shown += [label(problem, "muted") for problem in found.get("problems") or []]
        refill(rows, shown)

    page.ask("GET", "/type-change/similar", show, lambda e: refill(rows, [label(str(e), "muted")]))


def world_copy(page: SettingsPage, layout: QVBoxLayout, current: dict, offer: dict) -> None:
    """Technical: a new server of the other edition from a copy of this
    world (Chunker). The warning can't be skipped: the box must be ticked."""
    from .settings import grid

    window = page.window_
    to_java = (current["capabilities"].get("edition") or "java") == "bedrock"
    layout.addWidget(
        label(
            text("desktop.typechange.copy_java" if to_java else "desktop.typechange.copy_bedrock"),
            "step",
        )
    )
    layout.addWidget(label(offer["warning"], "notice"))
    kinds, versions = QComboBox(), QComboBox()
    for kind in window.types:
        if (kind.get("edition") or "java") == ("java" if to_java else "bedrock"):
            kinds.addItem(kind["name"], kind["id"])
    name, folder = QLineEdit(offer["name"]), QLineEdit(offer["directory"])
    name.setMaxLength(60)
    folder.setMaxLength(400)
    layout.addLayout(
        grid(
            [
                field(text("version.change_kind"), kinds),
                field(text("version.change_to"), versions),
                field(text("dup.name"), name),
                field(text("dup.folder"), folder, text("dup.folder_hint")),
            ]
        )
    )
    understood = QCheckBox(text("desktop.typechange.understood"))
    eula = QCheckBox(text("desktop.typechange.eula"))
    layout.addWidget(understood)
    layout.addWidget(eula)
    said = label(kind="sub")

    def load_versions() -> None:
        versions.clear()

        def got(payload: dict) -> None:
            for v in payload.get("versions") or []:
                if v["stable"]:
                    versions.addItem(v["minecraft"], v["minecraft"])
            if payload.get("source_problem"):
                said.setText(payload["source_problem"])

        window.run(
            lambda: window.agent.call("GET", f"/server-types/{kinds.currentData()}/versions"),
            got,
            lambda e: said.setText(str(e)),
        )

    def make() -> None:
        if not understood.isChecked():
            said.setText(text("desktop.typechange.tick_understood"))
            return
        said.setText(text("desktop.typechange.copying"))
        page.ask(
            "POST",
            "/type-change/copy",
            lambda r: page.done(text("desktop.typechange.copied", name=r["name"])),
            lambda e: said.setText(str(e)),
            body={
                "type": kinds.currentData(),
                "minecraft_version": versions.currentData() or "",
                "name": name.text(),
                "directory": folder.text(),
                "eula_accepted": eula.isChecked(),
                "understood": True,
            },
            slow=True,
        )

    kinds.currentIndexChanged.connect(lambda _i: load_versions())
    go = push(text("desktop.typechange.copy"), "primary")
    go.clicked.connect(make)
    layout.addLayout(buttons(go))
    layout.addWidget(said)
    load_versions()


def own_jar(page: SettingsPage, layout, caps: dict, said) -> None:
    """The owner's own server jar: never checked or updated by the app, so
    there is no version change, only Replace jar (owner only)."""
    window = page.window_
    layout.addWidget(label(text("custom.version_note", base=caps.get("name")), "notice"))
    if not page.may("servers.manage") or window.agent.place != "this PC":
        return

    def pick() -> None:
        path = window.pc.pick_file(text("custom.replace"), f"{text('custom.jar')} (*.jar)")
        if path:
            said.setText(text("custom.replacing"))
            page.ask(
                "POST",
                "/custom-jar",
                lambda _r: page.done(text("custom.replaced")),
                lambda e: said.setText(str(e)),
                body={"jar": path},
                slow=True,
            )

    replace = push(text("custom.replace"))
    replace.clicked.connect(pick)
    row = QHBoxLayout()
    row.addWidget(replace)
    row.addStretch()
    layout.addLayout(row)


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
