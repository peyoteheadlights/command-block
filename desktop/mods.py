"""The Mods page, called Plugins or Add-ons by the server's type: the
installed list with Turn off, Update, Versions and Remove, "Needs you" (the
CurseForge mods to download by hand), the mods other mods need, Update all,
Modrinth search (and Hangar's on plugin servers, CurseForge's on mod
servers), a modpack import (from a file or found on CurseForge), .jar and
datapack files from this PC and GitHub release links, the suggestions
("For your players" in both modes; "Good to have" and "Often used with
your mods" in Technical), the live map's link, Copy to another server, each
add-on's guide and Edit settings (desktop/editor.py).
A Bedrock server's add-ons (behavior and resource packs) are on
their own page, AddonsPage. Changes wait until the server is stopped, as on
the agent; nothing here stops it without a click."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .agent import AgentError
from .editor import GuideWindow, pick_file
from .fixes import install
from .overview import card, title_of
from .ui import Page, clock, label, push, refill, size
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

STATUS = {
    "ok": "mods.status_ok",
    "warn": "mods.status_warn",
    "error": "mods.status_error",
}
DEPENDENCY = ("missing_dependency", "dependency_version")
ATTENTION = ("missing", "incompatible", "disabled", "platform_incompatible")
STOPPED = ("OFFLINE", "CRASHED")
PACK_FILES = (".mcpack", ".mcaddon", ".mctemplate")
SEARCH = 10  # results asked of Modrinth at a time
ACTIONS = ("install", "upload", "remove", "update", "rollback", "enable", "disable")
# Plugin servers' suggestion groups, in the order shown.
GROUPS = {
    "starter": "desktop.mods.group_starter",
    "protect": "desktop.mods.group_protect",
    "bedrock": "desktop.mods.group_bedrock",
    "maps": "desktop.mods.group_maps",
    "voice": "desktop.mods.group_voice",
}


def takes(row: dict, paths: list[str]) -> bool:
    """Whether these files, dropped on the window, are the server's add-ons:
    .jar mods or plugins and .zip datapacks, one .mrpack, or Bedrock packs."""
    kinds = {Path(p).suffix.lower() for p in paths}
    caps = row.get("capabilities") or {}
    if caps.get("addons"):
        return kinds <= set(PACK_FILES)
    if caps.get("mods"):
        return kinds <= {".jar", ".zip"} or (len(paths) == 1 and kinds == {".mrpack"})
    return False


def action(name: str) -> str:
    """A history row's action in words ("Turned off"), or as the agent wrote it."""
    key = f"mobile.content.action_{name}"
    return text(key) if name in ACTIONS else name


def pack_lines(pack: dict) -> list[str]:
    """What a modpack holds, read before anything is downloaded, and why it
    can't be imported when it can't."""
    name = f"{pack['name']} {pack.get('version') or ''}".strip()
    loader = " ".join(x for x in (pack.get("type_name"), pack.get("loader_version")) if x)
    lines = [
        f"{text('pack.name')}: {name}",
        f"{text('pack.minecraft')}: {pack.get('minecraft_version') or text('state.unknown')}",
        f"{text('pack.loader')}: {loader or text('state.unknown')}",
        f"{text('pack.files')}: "
        + text("pack.files_value", count=pack["download_count"], size=size(pack["download_bytes"])),
    ]
    refused = [
        f"{f['name']}: {f.get('reason') or ''}"
        for f in pack.get("files") or []
        if f["status"] == "refused"
    ]
    if pack.get("client_only"):
        lines.append(text("pack.client_only", names=", ".join(pack["client_only"])))
    if pack.get("needs_you"):
        lines.append(text("desktop.pack.needs_you", names=names(pack["needs_you"], "name")))
    if pack.get("skipped_overrides"):
        lines.append(text("pack.skipped_extras", names=", ".join(pack["skipped_overrides"])))
    if not pack.get("can_import"):
        return [text("pack.cant_import"), *pack.get("problems", []), *refused]
    return lines + refused


def names(items: list[dict], key: str = "title") -> str:
    return ", ".join(str(i.get(key) or i.get("name") or "") for i in items)


class ModsPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.data: dict = {}
        self.guides: dict[str, dict] = {}  # by the add-on's file name
        self.query = ""
        self.offset = 0
        self.searches = 0  # the newest search, so an older answer is dropped

        self.stop_row = QWidget()
        h = QHBoxLayout(self.stop_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.stop_words = label(kind="problem")
        self.stop = push(text("action.stop"))
        self.stop.clicked.connect(lambda: window.server_action(self.row["id"], "stop"))
        h.addWidget(self.stop_words, 1)
        h.addWidget(self.stop)
        self.column.addWidget(self.stop_row)
        self.problems = label(kind="problem")
        self.column.addWidget(self.problems)

        # CurseForge mods whose authors keep them from other apps: their
        # page, and the window takes the file when it's dropped.
        self.needs_you_card, layout = card("")
        self.needs_you_words = label(kind="sub")
        self.needs_you_rows = QVBoxLayout()
        layout.addWidget(self.needs_you_words)
        layout.addLayout(self.needs_you_rows)
        self.column.addWidget(self.needs_you_card)

        self.missing_card, layout = card("")
        self.missing_words = label(kind="sub")
        self.missing_rows = QVBoxLayout()
        self.install_missing = push("", "primary")
        self.install_missing.clicked.connect(self.plan_missing)
        layout.addWidget(self.missing_words)
        layout.addLayout(self.missing_rows)
        line = QHBoxLayout()
        line.addWidget(self.install_missing)
        line.addStretch()
        layout.addLayout(line)
        self.column.addWidget(self.missing_card)

        self.list_card, layout = card("")
        self.needs = label(kind="sub")
        buttons = QHBoxLayout()
        self.update_all = push("")
        self.update_all.clicked.connect(self.plan_update_all)
        self.add_file = push("")
        self.add_file.clicked.connect(self.pick_jar)
        self.copy = push("")
        self.copy.clicked.connect(self.copy_to)
        self.maps = QHBoxLayout()
        buttons.addWidget(self.update_all)
        buttons.addWidget(self.add_file)
        buttons.addWidget(self.copy)
        buttons.addLayout(self.maps)
        buttons.addStretch()
        self.link_row = QWidget()
        line = QHBoxLayout(self.link_row)
        line.setContentsMargins(0, 0, 0, 0)
        self.link_box = QLineEdit()
        self.link_box.setMaxLength(500)
        self.link_box.returnPressed.connect(self.read_link)
        self.read = push("")
        self.read.clicked.connect(self.read_link)
        line.addWidget(self.link_box, 1)
        line.addWidget(self.read)
        self.rows = QVBoxLayout()
        layout.addWidget(self.needs)
        layout.addLayout(buttons)
        layout.addWidget(self.link_row)
        layout.addLayout(self.rows)
        drop_hint = label(kind="sub")
        layout.addWidget(drop_hint)
        self.column.addWidget(self.list_card)

        # What friends add to their own game; never put on the server.
        self.players_card, layout = card("")
        players_intro = label(kind="sub")
        self.players_rows = QVBoxLayout()
        layout.addWidget(players_intro)
        layout.addLayout(self.players_rows)
        self.column.addWidget(self.players_card)

        # Technical: the hand-checked lists, each checked against this server.
        self.ideas_card, layout = card("")
        self.ideas_rows = QVBoxLayout()
        layout.addLayout(self.ideas_rows)
        self.column.addWidget(self.ideas_card)

        self.search_card, layout = card("")
        line = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setMaxLength(100)
        self.search_box.returnPressed.connect(self.search)
        go = push(text("mods.search"), "primary")
        go.clicked.connect(self.search)
        line.addWidget(self.search_box, 1)
        line.addWidget(go)
        self.search_hint = label(kind="sub")
        self.hits_widget = QWidget()
        self.hits = QVBoxLayout(self.hits_widget)
        self.hits.setContentsMargins(0, 0, 0, 0)
        self.more = push("", "plain")
        self.more.clicked.connect(lambda: self.search(more=True))
        layout.addLayout(line)
        layout.addWidget(self.search_hint)
        layout.addWidget(self.hits_widget)
        layout.addWidget(self.more)
        self.column.addWidget(self.search_card)

        self.pack_card, layout = card("")
        pack_intro = label(kind="sub")
        layout.addWidget(pack_intro)
        choose = push("")
        choose.clicked.connect(self.pick_pack)
        layout.addWidget(choose)
        line = QHBoxLayout()
        self.pack_box = QLineEdit()
        self.pack_box.setMaxLength(100)
        self.pack_box.returnPressed.connect(self.find_pack)
        find = push("")
        find.clicked.connect(self.find_pack)
        line.addWidget(self.pack_box, 1)
        line.addWidget(find)
        layout.addLayout(line)
        self.column.addWidget(self.pack_card)

        self.checks_card, layout = card(text("mods.about_checks"))
        self.checks = label(kind="sub")
        layout.addWidget(self.checks)
        self.column.addWidget(self.checks_card)

        self.history_card, layout = card("")
        self.history = QVBoxLayout()
        layout.addLayout(self.history)
        self.column.addWidget(self.history_card)
        self.column.addStretch()
        for hidden in (
            self.stop_row,
            self.problems,
            self.needs_you_card,
            self.missing_card,
            self.more,
            self.copy,
            self.players_card,
            self.ideas_card,
        ):
            hidden.hide()
        # Words that differ between Simple and Technical, set again on each load.
        self.wording: list[tuple[Callable[[str], None], str]] = [
            (title_of(self.missing_card).setText, "deps.title"),
            (self.update_all.setText, "mobile.content.update_all"),
            (self.add_file.setText, "mobile.content.add_file"),
            (self.link_box.setPlaceholderText, "desktop.mods.link_placeholder"),
            (self.link_box.setAccessibleName, "desktop.mods.link_placeholder"),
            (self.read.setText, "desktop.mods.link_read"),
            (drop_hint.setText, "desktop.mods.drop"),
            (title_of(self.search_card).setText, "mobile.content.search_title"),
            (self.search_box.setPlaceholderText, "mods.search_placeholder"),
            (self.search_box.setAccessibleName, "mods.search_label"),
            (self.more.setText, "mobile.content.more"),
            (title_of(self.pack_card).setText, "pack.into_title"),
            (pack_intro.setText, "pack.into_intro"),
            (choose.setText, "mobile.content.modpack_choose"),
            (self.pack_box.setPlaceholderText, "desktop.mods.pack_find_placeholder"),
            (self.pack_box.setAccessibleName, "desktop.mods.pack_find_placeholder"),
            (find.setText, "desktop.mods.pack_find"),
            (title_of(self.needs_you_card).setText, "desktop.mods.needs_you"),
            (self.needs_you_words.setText, "desktop.mods.needs_you_intro"),
            (title_of(self.history_card).setText, "mods.change_history"),
            (self.copy.setText, "desktop.mods.copy"),
            (title_of(self.players_card).setText, "desktop.mods.for_players"),
            (players_intro.setText, "desktop.mods.for_players_intro"),
            (title_of(self.ideas_card).setText, "desktop.mods.good_to_have"),
        ]

    # ------------------------------------------------------------ loading
    def may(self, permission: str) -> bool:
        return self.window_.may(self.row["id"], permission)

    @property
    def can_change(self) -> bool:
        """Mods change only while the server is stopped, and only for an
        account allowed to; until /mods answers, the state isn't known."""
        return self.may("mods.manage") and self.data.get("server_state") in STOPPED

    def load(self) -> None:
        for set_words, key in self.wording:
            set_words(text(key))
        self.data = {}
        self.query, self.offset = "", 0
        self.searches += 1
        self.search_box.clear()
        refill(self.hits, [])
        self.more.hide()
        manage = self.may("mods.manage")
        for widget in (self.search_card, self.add_file, self.update_all, self.link_row):
            widget.setVisible(manage)
        caps = self.row.get("capabilities") or {}
        if caps.get("hangar"):
            title_of(self.search_card).setText(text("desktop.mods.search_both"))
        if caps.get("curseforge"):
            title_of(self.search_card).setText(text("desktop.mods.search_curseforge"))
        self.link_box.clear()
        self.pack_card.setVisible(manage and self.may("settings.server"))
        self.checks_card.setVisible(mode["technical"])
        self.missing_card.hide()
        self.needs.setText(text("deps.checking"))
        self.after_change()
        technical = "true" if mode["technical"] else "false"
        self.ask(
            "GET",
            f"/suggestions?technical={technical}",
            self.show_ideas,
            lambda _e: None,
            slow=True,
        )
        self.guides = {}
        self.ask("GET", "/guides", self.show_guides, lambda _e: None)
        self.targets: list[dict] = []
        self.copy.hide()
        if manage:
            self.ask("GET", "/mods/copy", self.show_targets, lambda _e: None)

    def show_guides(self, found: dict) -> None:
        self.guides = {g["filename"]: g for g in found.get("guides") or []}
        if self.data:
            self.show_list(self.data)

    def changed(self) -> None:
        # The feed says something changed: the list, not Modrinth's names.
        self.ask("GET", "/mods", self.show_list)
        self.ask("GET", "/mods/history?limit=15", self.show_history, lambda _e: None)

    def after_change(self) -> None:
        self.changed()
        self.ask("GET", "/mods/dependencies", self.show_needs, self.needs_failed, slow=True)

    def said(self, exc: Exception) -> None:
        self.say(str(exc))

    def show_list(self, data: dict) -> None:
        self.data = data
        mods = data.get("mods") or []
        plugins = self.row.get("content") == "plugins"
        title_of(self.list_card).setText(
            f"{text('page.plugins')} · {len(mods)}"
            if plugins
            else text(
                "mods.installed.one" if len(mods) == 1 else "mods.installed.other", count=len(mods)
            )
        )
        state = data.get("server_state")
        stopped = state in STOPPED
        self.stop_words.setText(text("mobile.content.stop_first"))
        self.stop_row.setVisible(not stopped and self.may("mods.manage"))
        self.stop.setVisible(state == "ONLINE" and self.may("server.control"))
        others = [
            p["detail"] for p in data.get("problems") or [] if p.get("kind") not in DEPENDENCY
        ]
        self.problems.setText(
            "\n".join(
                [
                    text(
                        "mods.problems.one" if len(others) == 1 else "mods.problems.other",
                        count=len(others),
                    )
                ]
                + [f"• {p}" for p in others[:6]]
            )
        )
        self.problems.setVisible(bool(others))
        # The search results' Install buttons too: the server may have stopped since.
        for button in [
            self.add_file,
            self.read,
            self.install_missing,
            *self.hits_widget.findChildren(QPushButton),
        ]:
            button.setEnabled(self.can_change)
        self.update_all_ready()
        needs = data.get("needs_you") or []
        self.needs_you_card.setVisible(bool(needs))
        refill(self.needs_you_rows, [self.need_row(n) for n in needs])
        refill(
            self.rows,
            [self.mod_row(m) for m in mods]
            or [label(text("mods.none"), "step"), label(text("mods.none_hint"), "sub")],
        )
        self.search_hint.setText(
            text(
                "mobile.content.search_hint",
                type=self.row.get("type_name") or text("state.unknown"),
                version=data.get("minecraft_version") or text("state.unknown"),
            )
        )
        limits = " ".join(data.get("limitations") or [])
        self.checks.setText(
            f"{data.get('claim') or ''}. {data.get('claim_note') or ''}\n\n"
            + text("mods.limits", limits=limits)
        )

    def mod_row(self, mod: dict) -> QWidget:
        row = QWidget()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        words.addWidget(label(mod.get("name") or mod["filename"], "step"))
        status = STATUS.get(mod.get("status") or "", "state.unknown")
        parts = [mod.get("version") or text("state.unknown")]
        parts.append(text(status if mod.get("enabled") else "mods.status_off"))
        if mode["technical"]:
            parts += [mod.get("mod_id"), mod.get("minecraft_range"), mod.get("source_name")]
        words.addWidget(label("  ·  ".join(p for p in parts if p), "sub"))
        if mod.get("source") in ("file", "github"):
            words.addWidget(label(text("desktop.mods.not_checked"), "muted"))
        elif mod.get("checked_with") == "sha1":
            words.addWidget(label(text("desktop.mods.checked_sha1"), "muted"))
        for issue in (mod.get("issues") or [])[:2]:
            words.addWidget(
                label(issue["detail"], "problem" if issue.get("severity") == "error" else "sub")
            )
        newer = mod.get("update_available")
        if newer:
            words.addWidget(label(text("mods.update_to", version=newer["latest_version"]), "ok"))
        h.addLayout(words, 1)
        name = mod.get("name") or mod["filename"]
        if self.may("mods.manage"):
            turn = push(text("mods.turn_off" if mod.get("enabled") else "mods.turn_on"))
            turn.setAccessibleName(f"{turn.text()}: {name}")
            turn.setEnabled(self.can_change)
            turn.clicked.connect(lambda: self.turn(mod["filename"], not mod.get("enabled")))
            h.addWidget(turn)
            if newer:
                update = push(text("mods.update"), "primary")
                update.setAccessibleName(f"{update.text()}: {name}")
                update.setEnabled(self.can_change)
                update.clicked.connect(lambda: self.update_mod(mod))
                h.addWidget(update)
        guide = self.guides.get(mod["filename"])
        if guide:
            read = push(text("desktop.guide.open"), "plain")
            read.setAccessibleName(f"{read.text()}: {name}")
            read.clicked.connect(lambda: GuideWindow(self, guide).open())
            h.addWidget(read)
        more = QToolButton()
        more.setText("⋯")
        more.setAccessibleName(f"{text('desktop.more')}: {name}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        actions: list[tuple[str, Callable[[], None]]] = []
        if mod.get("mod_id"):
            actions.append(("mobile.content.versions", lambda: self.versions(mod)))
        if self.may("mods.manage"):
            actions.append(
                ("desktop.files.edit_settings", lambda: pick_file(self, mod["filename"], name))
            )
        if self.may("mods.manage") and self.can_change:
            actions.append(("mods.remove", lambda: self.remove(mod)))
        for key, do in actions:
            menu.addAction(text(key)).triggered.connect(lambda _=False, d=do: d())
        more.setMenu(menu)
        more.setVisible(bool(actions))
        h.addWidget(more)
        return row

    def need_row(self, need: dict) -> QWidget:
        row = QWidget()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        name = need.get("name") or need.get("filename") or ""
        words.addWidget(label(name, "step"))
        words.addWidget(
            label(text("desktop.mods.needs_you_file", file=need.get("filename")), "sub")
        )
        h.addLayout(words, 1)
        if need.get("page"):
            open_it = push(text("desktop.mods.open_curseforge"), "primary")
            open_it.setAccessibleName(f"{open_it.text()}: {name}")
            open_it.clicked.connect(lambda: self.window_.pc.open_link(need["page"]))
            h.addWidget(open_it)
        if self.may("mods.manage"):
            drop = push(text("desktop.mods.needs_you_remove"), "plain")
            drop.setAccessibleName(f"{drop.text()}: {name}")
            drop.clicked.connect(
                lambda: self.ask(
                    "POST",
                    "/mods/needs-you/dismiss",
                    lambda _r: self.changed(),
                    self.said,
                    body={"project": need["project"]},
                )
            )
            h.addWidget(drop)
        return row

    def show_history(self, data: dict) -> None:
        rows = []
        for h in (data.get("history") or [])[:15]:
            change = ""
            if h.get("old_version") or h.get("new_version"):
                change = f"  {h.get('old_version') or '-'} → {h.get('new_version') or '-'}"
            result = (
                "" if h.get("result") == "ok" else f"  ·  {text('mobile.content.result_failed')}"
            )
            rows.append(
                label(
                    f"{clock(h.get('ts'))}  ·  {h.get('user') or '-'}  ·  {action(h['action'])}  "
                    f"{h.get('mod_name') or h.get('mod_id') or ''}{change}{result}",
                    "sub",
                )
            )
        refill(self.history, rows or [label(text("mods.no_history"), "sub")])

    # ------------------------------------------------------------ what mods need
    def show_needs(self, report: dict) -> None:
        items = report.get("items") or []
        attention = [
            i for i in items if i.get("kind") == "required" and i.get("status") in ATTENTION
        ]
        missing = [i for i in attention if i.get("status") == "missing"]
        lookup = report.get("lookup_error") or ""
        self.needs.setText(lookup or ("" if attention else text("deps.all_ok")))
        self.needs.setVisible(bool(self.needs.text()))
        self.missing_card.setVisible(bool(attention))
        count = len(missing)
        self.missing_words.setText(
            text("deps.missing.one" if count == 1 else "deps.missing.other", count=count)
            if count
            else text("deps.problems")
        )
        rows = []
        for item in attention:
            needed_by = ", ".join(dict.fromkeys(r["name"] for r in item.get("required_by") or []))
            status = f"deps.status_{item['status']}"
            facts = [text("deps.required_by", names=needed_by)]
            if item.get("range_text"):
                facts.append(text("deps.version", range=item["range_text"]))
            if item.get("reason"):
                facts.append(item["reason"])
            rows.append(label(f"{item['name']}  ·  {text(status)}  ·  {' '.join(facts)}"))
        refill(self.missing_rows, rows)
        self.install_missing.setText(text("deps.install_missing", n=count))
        self.install_missing.setVisible(bool(count) and self.may("mods.manage"))
        self.install_missing.setEnabled(self.can_change)

    def needs_failed(self, exc: Exception) -> None:
        self.needs.setText(f"{text('deps.check_failed')} {exc}")
        self.needs.show()

    def plan_missing(self) -> None:
        sid = self.row["id"]

        def refresh() -> None:  # once the install has answered, if still on this server
            if self.window_.server_id == sid:
                self.after_change()

        self.say(text("deps.planning"))
        self.ask(
            "POST",
            "/mods/dependencies/plan",
            lambda plan: install(self.window_, sid, plan, self.say, refresh),
            self.said,
            body={},
            slow=True,
        )

    # ------------------------------------------------------------ one mod
    def turn(self, filename: str, on: bool) -> None:
        def done(result: dict) -> None:
            mod = result["mod"]
            name = mod.get("name") or mod["filename"]
            self.say(
                text("mods.turned_on" if on else "mods.turned_off", name=name),
                lambda: self.turn(mod["filename"], not on),
            )
            self.after_change()

        self.ask(
            "POST",
            "/mods/enable" if on else "/mods/disable",
            done,
            self.said,
            body={"filename": filename},
        )

    def update_mod(self, mod: dict) -> None:
        newer = mod["update_available"]
        name = mod.get("name") or mod["filename"]
        if not self.window_.confirm(
            text("mods.update_title", name=name),
            f"{mod.get('version')} → {newer['latest_version']}\n\n{text('mods.update_archived')}",
            text("mods.update"),
        ):
            return

        def done(result: dict) -> None:
            back = {
                "mod_id": result["updated"]["mod_id"],
                "archive_path": result["rollback_archive"],
            }
            self.say(
                text("mods.updated_toast", version=result["updated"]["version"]),
                lambda: self.ask(
                    "POST",
                    "/mods/rollback",
                    lambda r: self.rolled_back(r),
                    self.said,
                    body=back,
                ),
            )
            self.after_change()

        self.say(text("mobile.content.working"))
        self.ask(
            "POST",
            "/mods/update",
            done,
            self.said,
            body={"filename": mod["filename"], "version_id": newer["version_id"]},
            slow=True,
        )

    def rolled_back(self, result: dict) -> None:
        self.say(text("mods.rolled_back", version=result["restored"].get("version")))
        self.after_change()

    def versions(self, mod: dict) -> None:
        name = mod.get("name") or mod["filename"]

        def choose(data: dict) -> None:
            kept = [v for v in data.get("versions") or [] if not v["is_current"] and v["exists"]]
            if not kept:
                self.say(text("mods.no_versions"))
                return
            if not self.can_change:
                lines = [f"{v.get('version') or '-'}  ·  {clock(v['created_at'])}" for v in kept]
                self.say(f"{text('mods.versions_title', name=name)}: {', '.join(lines)}")
                return
            picked = self.window_.pick(
                text("mods.versions_title", name=name),
                text("mods.rollback_body"),
                [f"{v.get('version') or '-'}  ·  {clock(v['created_at'])}" for v in kept],
            )
            if picked is None:
                return
            v = kept[picked]
            self.ask(
                "POST",
                "/mods/rollback",
                self.rolled_back,
                self.said,
                body={"mod_id": mod["mod_id"], "archive_path": v["archive_path"]},
            )

        self.ask("GET", f"/mods/versions/{quote(mod['mod_id'], safe='')}", choose, self.said)

    def remove(self, mod: dict) -> None:
        name = mod.get("name") or mod["filename"]

        def ask_first(impact: dict) -> None:
            lines = [impact["warning"]] if impact.get("warning") else []
            lines += [
                text("mods.needed_by", name=d["name"], requires=d.get("requires") or "")
                for d in impact.get("dependents") or []
            ] or [text("mods.not_needed")]
            lines.append(text("mods.remove_kept"))
            if not self.window_.confirm(
                text("mods.remove_title", name=name), "\n\n".join(lines), text("mods.remove")
            ):
                return

            def done(_r) -> None:
                self.say(text("mods.removed_toast", name=name))
                self.after_change()

            self.ask("POST", "/mods/remove", done, self.said, body={"filename": mod["filename"]})

        self.ask("GET", f"/mods/impact?filename={quote(mod['filename'])}", ask_first, self.said)

    # ------------------------------------------------------------ Update all
    def update_all_ready(self) -> None:
        self.update_all.setEnabled(self.can_change and bool(self.data.get("mods")))

    def plan_update_all(self) -> None:
        """Greyed out from the press until it's over: asked, cancelled or done."""
        self.update_all.setEnabled(False)
        self.say(text("mobile.content.checking"))

        def failed(exc: Exception) -> None:
            self.said(exc)
            self.update_all_ready()

        self.ask("GET", "/mods/update-all", self.confirm_update_all, failed, slow=True)

    def confirm_update_all(self, plan: dict) -> None:
        updates = plan.get("updates") or []
        unchecked = [
            text("mobile.content.update_all_unchecked", name=u["name"], reason=u["reason"])
            for u in plan.get("unchecked") or []
        ]
        if not updates:
            # Never "up to date" while something couldn't be checked just now.
            self.say("\n".join(unchecked or [text("mobile.content.update_all_none")]))
            self.update_all_ready()
            return
        self.say("")
        choices = [
            (
                f"{u['name']}   {u['installed_version']} → {u['latest_version']}"
                + ("" if u["enabled"] else f"   ({text('mobile.content.update_all_off')})")
                + (f"   ({u['saved']})" if u.get("saved") else ""),
                bool(u["enabled"]),
            )
            for u in updates
        ]
        ticked = self.window_.tick(
            text("mobile.content.update_all"),
            "\n\n".join([text("mobile.content.update_all_lead"), *unchecked]),
            choices,
            text("mods.update"),
        )
        if not ticked:
            self.update_all_ready()
            return
        wanted = [
            {"filename": updates[i]["filename"], "version_id": updates[i]["version_id"]}
            for i in ticked
        ]

        def done(result: dict) -> None:
            count = len(result.get("updated") or [])
            backup = (result.get("safety_backup") or {}).get("name") or text("state.unknown")
            self.say(
                text(
                    "mobile.content.update_all_done.one"
                    if count == 1
                    else "mobile.content.update_all_done.other",
                    count=count,
                    backup=backup,
                ),
                (lambda: self.window_.move_change("undo")) if result.get("undo") else None,
            )
            self.window_.load_server_parts()

        self.say(text("mobile.content.working"))
        self.ask(
            "POST",
            "/mods/update-all",
            done,
            self.said,
            body={"mods": wanted},
            always=self.after_change,
            slow=True,
        )

    # ------------------------------------------------------------ Modrinth
    def search(self, more: bool = False) -> None:
        self.searches += 1
        mine = self.searches
        if not more:
            self.query, self.offset = self.search_box.text().strip(), 0
            refill(self.hits, [label(text("mods.searching"), "sub")])
        self.more.hide()
        offset = self.offset

        def found(data: dict) -> None:
            if mine != self.searches:
                return  # another search (or another server) since
            hits = data.get("hits") or []
            if not more:
                refill(self.hits, [label(p, "problem") for p in data.get("problems") or []])
            for hit in hits:
                self.hits.addWidget(self.hit_row(hit))
            if not more and not hits:
                self.hits.addWidget(label(text("mods.nothing_found"), "sub"))
            self.offset = offset + len(hits)
            self.more.setVisible(len(hits) == SEARCH and self.offset < (data.get("total") or 0))

        self.ask(
            "GET",
            f"/mods/search?q={quote(self.query)}&limit={SEARCH}&offset={offset}",
            found,
            lambda exc: refill(self.hits, [label(str(exc), "problem")]),
        )

    def hit_row(self, hit: dict) -> QWidget:
        row = QWidget()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        title = hit.get("title") or hit.get("slug") or ""
        words.addWidget(label(title, "step"))
        words.addWidget(label((hit.get("description") or "")[:140], "sub"))
        if hit.get("downloads") is not None:
            count = f"{hit['downloads']:,}"
            words.addWidget(
                label(
                    text("desktop.mods.downloads_on", count=count, site=hit["source_name"])
                    if hit.get("source_name")
                    else text("mobile.content.downloads", count=count),
                    "muted",
                )
            )
        if hit.get("look_alike"):
            words.addWidget(
                label(text("desktop.mods.look_alike", name=hit["look_alike"]), "problem")
            )
        h.addLayout(words, 1)
        if hit.get("blocked"):
            # Its author keeps it from other apps: its page, never a download.
            words.addWidget(label(text("desktop.mods.blocked_hint"), "sub"))
            if hit.get("page"):
                open_it = push(text("desktop.mods.open_curseforge"))
                open_it.setAccessibleName(f"{open_it.text()}: {title}")
                open_it.clicked.connect(lambda: self.window_.pc.open_link(hit["page"]))
                h.addWidget(open_it)
            return row
        get = push(text("mods.install"))
        get.setAccessibleName(f"{text('mods.install')}: {title}")
        get.setEnabled(self.can_change)
        get.setToolTip("" if self.can_change else text("mods.stop_first"))
        get.clicked.connect(lambda: self.install_hit(hit))
        h.addWidget(get)
        return row

    def install_hit(self, hit: dict) -> None:
        slug = hit.get("slug") or hit.get("project_id") or ""

        def ask_first(detail: dict) -> None:
            versions = detail.get("versions") or []
            if not versions:
                self.say(text("mods.no_build"))
                return
            latest, project = versions[0], detail["project"]
            lines = [
                text(
                    "mods.install_what",
                    name=project.get("title"),
                    version=latest.get("version_number"),
                ),
                text("mods.install_size", size=size((latest.get("file") or {}).get("size"))),
                text(
                    "mods.install_minecraft", versions=", ".join(latest.get("game_versions") or [])
                ),
            ]
            if mode["technical"]:
                lines += [
                    text("mods.install_file", file=(latest.get("file") or {}).get("filename")),
                    text("mods.install_side", side=project.get("server_side")),
                    text(
                        "mods.install_licence",
                        licence=project.get("license") or text("state.unknown"),
                    ),
                ]
            hangar = slug.startswith("hangar:")
            checked = {"hangar": "desktop.mods.checked_hangar"}
            checked["curseforge"] = "desktop.mods.checked_curseforge"
            lines.append(text(checked.get(slug.partition(":")[0], "mods.install_checked")))
            deps = latest.get("dependencies") or []
            # Hangar's needs that live elsewhere are only ever a link to click.
            lines += [
                text("desktop.mods.needs_link", name=d.get("name"), url=d["url"])
                for d in deps
                if d.get("type") == "required" and d.get("url") and not d.get("project_id")
            ]
            optional = [d.get("name") for d in deps if hangar and d.get("type") == "optional"]
            if optional:
                lines.append(text("desktop.mods.works_with", names=", ".join(map(str, optional))))
            needed = [d for d in deps if d.get("type") == "required" and d.get("project_id")]
            body = "\n".join(lines)
            if needed:
                count = len(needed)
                ticked = self.window_.tick(
                    text("mods.install_title"),
                    body,
                    [
                        (
                            text(
                                "mods.install_deps.one"
                                if count == 1
                                else "mods.install_deps.other",
                                count=count,
                            ),
                            True,
                        )
                    ],
                    text("mods.install_confirm"),
                )
                if ticked is None:
                    return
                with_needed = bool(ticked)
            else:
                if not self.window_.confirm(
                    text("mods.install_title"), body, text("mods.install_confirm")
                ):
                    return
                with_needed = False

            def done(result: dict) -> None:
                got = result["installed"]
                words = [
                    text("mods.installed_toast", name=got.get("name"), version=got.get("version"))
                ]
                if any(not d.get("installed") for d in result.get("dependencies_required") or []):
                    words.append(text("mods.deps_missing"))
                if result.get("needs_you"):
                    words.append(text("desktop.mods.needs_you_added"))
                self.say(" ".join(words))
                self.after_change()

            self.say(text("mobile.content.working"))
            self.ask(
                "POST",
                "/mods/install",
                done,
                self.said,
                body={
                    "project": slug,
                    "version_id": latest.get("version_id"),
                    "install_dependencies": with_needed,
                },
                slow=True,
            )

        self.ask("GET", f"/mods/project/{quote(slug, safe='')}", ask_first, self.said, slow=True)

    # ------------------------------------------------------------ suggestions
    def show_ideas(self, found: dict) -> None:
        maps = found.get("maps") or []
        shown: list[QWidget] = []
        for item in maps:
            if item.get("link"):
                button = push(text("desktop.mods.map", name=item["name"]))
                button.setToolTip(item["link"])
                button.clicked.connect(lambda _=False, u=item["link"]: self.window_.pc.open_link(u))
                shown.append(button)
            else:
                shown.append(label(f"{item['name']}: {item.get('problem') or ''}", "muted"))
        refill(self.maps, shown)
        players = found.get("for_your_players") or []
        refill(self.players_rows, [self.player_row(p) for p in players])
        self.players_card.setVisible(bool(players))
        rows: list[QWidget] = [
            label(line, "muted") for line in (found.get("problem"), found.get("stale")) if line
        ]
        plugins = self.row.get("content") == "plugins"
        good = found.get("good_to_have") or []
        groups = [g for g in GROUPS if any(i["group"] == g for i in good)] if plugins else []
        for group in groups:
            rows.append(label(text(GROUPS[group]), "step"))
            rows += [self.idea_row(i) for i in good if i["group"] == group]
        rows += [self.idea_row(i) for i in good if i["group"] not in groups]
        with_yours = found.get("often_used_with") or []
        if with_yours:
            rows.append(label(text("desktop.mods.often_used_with"), "step"))
            rows += [self.idea_row(i) for i in with_yours]
        refill(self.ideas_rows, rows)
        self.ideas_card.setVisible(mode["technical"] and len(rows) > 0)

    def idea_row(self, item: dict) -> QWidget:
        row = QWidget()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        words.addWidget(label(item["name"], "step"))
        words.addWidget(label((item.get("description") or "")[:140], "sub"))
        facts = [
            text(
                "desktop.mods.changes_gameplay"
                if item["changes_gameplay"]
                else "desktop.mods.same_gameplay"
            )
        ]
        if item["place"] == "both":
            facts.append(text("desktop.mods.players_too"))
        if item.get("release_type") and item["release_type"] != "release":
            facts.append(item["release_type"])
        count = item.get("downloads")
        if count:
            facts.append(
                text(
                    "desktop.mods.downloads_read",
                    count=f"{count['count']:,}",
                    site=count["source"],
                    when=clock(count["read_at"]),
                )
            )
        words.addWidget(label("  ·  ".join(facts), "muted"))
        h.addLayout(words, 1)
        if self.may("mods.manage"):
            add = push(text("desktop.typechange.add"))
            add.setAccessibleName(f"{add.text()}: {item['name']}")
            add.setEnabled(self.can_change)
            add.setToolTip("" if self.can_change else text("mods.stop_first"))
            add.clicked.connect(
                lambda: self.install_hit({"slug": item["project"], "title": item["name"]})
            )
            h.addWidget(add)
        return row

    def player_row(self, item: dict) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        words = QVBoxLayout()
        words.addWidget(label(item["name"], "step"))
        words.addWidget(
            label(
                text(
                    "desktop.mods.players_also"
                    if item.get("also_on_server")
                    else "desktop.mods.players_only"
                ),
                "sub",
            )
        )
        h.addLayout(words, 1)
        if item.get("page"):
            page = push(text("desktop.mods.open_page"))
            page.setAccessibleName(f"{page.text()}: {item['name']}")
            page.clicked.connect(lambda: self.window_.pc.open_link(item["page"]))
            h.addWidget(page)
        return row

    # ------------------------------------------------------------ copy to another server
    def show_targets(self, found: dict) -> None:
        self.targets = found.get("targets") or []
        self.copy.setVisible(bool(self.targets) and bool(self.data.get("mods")))

    def copy_to(self) -> None:
        mods = self.data.get("mods") or []
        names = [t["name"] for t in self.targets]
        picked = self.window_.pick(
            text("desktop.mods.copy"), text("desktop.mods.copy_where"), names
        )
        if picked is None:
            return
        target = self.targets[picked]
        ticked = self.window_.tick(
            text("desktop.mods.copy"),
            text("desktop.mods.copy_which", server=target["name"]),
            [(m.get("name") or m["filename"], False) for m in mods],
            text("desktop.mods.copy_check"),
        )
        if not ticked:
            return
        body = {"target": target["id"], "filenames": [mods[i]["filename"] for i in ticked]}

        def confirm(plan: dict) -> None:
            coming = "\n".join(f"  {c['name']} {c.get('version') or ''}" for c in plan["coming"])
            cant = "\n".join(f"  {c['name']}: {c['why']}" for c in plan["cant_come"])
            lines = [text("desktop.mods.copy_coming", server=target["name"]), coming or "  -"]
            if cant:
                lines += ["", text("desktop.mods.copy_cant"), cant]
            if plan.get("settings_note"):
                lines += ["", plan["settings_note"]]
            if not plan["coming"]:
                self.window_.confirm(
                    text("desktop.mods.copy"), "\n".join(lines), text("action.close")
                )
                return
            lines += ["", text("desktop.mods.copy_backup", server=target["name"])]
            if self.window_.confirm(
                text("desktop.mods.copy"), "\n".join(lines), text("desktop.mods.copy_go")
            ):
                self.say(text("mobile.content.working"))
                self.ask("POST", "/mods/copy", copied, self.said, body=body, slow=True)

        def copied(result: dict) -> None:
            agent, sid = self.window_.agent, target["id"]
            self.say(
                text("desktop.mods.copy_done", count=len(result["copied"]), server=target["name"]),
                lambda: self.window_.run(
                    lambda: agent.on(sid, "POST", "/changes/undo", {}, slow=True),
                    lambda _r: self.say(text("desktop.fix.undone")),
                    self.said,
                ),
            )

        self.ask("POST", "/mods/copy/check", confirm, self.said, body=body, slow=True)

    # ------------------------------------------------------------ files from this PC
    def run_here(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[AgentError], None] | None = None,
    ) -> None:
        here = self.window_.still_on(self.row["id"])
        self.window_.run(work, here(done), here(failed or self.said))

    def sure_unknown(self, name: str, problems: dict) -> bool:
        """A jar no site lists: the agent's warning and its hash, then the person decides."""
        body = f"{name}\n\n{problems['confirm']}"
        if problems.get("sha512"):
            body += "\n\n" + text("desktop.mods.unknown_hash", hash=problems["sha512"])
        return self.window_.confirm(
            text("desktop.mods.unknown_title"), body, text("desktop.mods.unknown_add")
        )

    def take(self, paths: list[str]) -> None:
        """Files dropped on the window: .jar files, or one modpack."""
        if paths[0].lower().endswith(".mrpack"):
            self.bring_pack(paths[0])
        else:
            self.add_files(paths)

    def pick_jar(self) -> None:
        path = self.window_.pc.pick_file(
            text("mobile.content.add_file"), f"{text('page.mods')} (*.jar *.zip)"
        )
        if path:
            self.add_files([path])

    def add_files(self, paths: list[str], added: list[dict] | None = None, sure=False) -> None:
        """One file at a time, so a file no site lists can be asked about.
        No check here: a drop opens the page before /mods answers, and the
        agent says "stop the server first" itself."""
        added = added or []
        if not paths:
            self.added(added)
            return
        sid, agent = self.row["id"], self.window_.agent
        path, rest = paths[0], paths[1:]

        def failed(exc: AgentError) -> None:
            if exc.problems.get("confirm") and not sure:
                if self.sure_unknown(Path(path).name, exc.problems):
                    self.add_files(paths, added, sure=True)
                else:
                    self.add_files(rest, added)
                return
            self.added(added, f"{Path(path).name}: {exc}")  # the ones before it stay added

        self.say(text("mobile.content.reading"))
        self.run_here(
            lambda: agent.upload(f"/servers/{sid}/mods/upload?confirm={str(sure).lower()}", path),
            lambda result: self.add_files(rest, [*added, result]),
            failed,
        )

    def added(self, results: list[dict], problem: str = "") -> None:
        """What was added from files or a link, and Undo for the last one."""
        packs = [r["installed"] for r in results if r["installed"].get("kind") == "datapack"]
        jars = [r["installed"] for r in results if r["installed"].get("kind") != "datapack"]
        words = [text("mobile.content.added", name=names(jars, "name"))] if jars else []
        words += [text("desktop.mods.datapack_added", name=p["name"]) for p in packs]
        if any(not r.get("checked_by") for r in results):
            words.append(f"{text('desktop.mods.not_checked')}.")
        undo = results[-1].get("undo") if results else None
        self.say(
            " ".join([*words, problem]),
            (lambda: self.window_.move_change("undo")) if undo and not problem else None,
        )
        self.after_change()

    def read_link(self) -> None:
        url = self.link_box.text().strip()
        if not url:
            return

        def offer(read: dict) -> None:
            if read["kind"] != "github":
                self.say(read.get("message") or "")
                return
            files = read.get("files") or []
            if not files:
                self.say(text("desktop.mods.link_none"))
                return
            picked = self.window_.pick(
                text("desktop.mods.link_pick", name=read.get("name") or read.get("repo")),
                text("desktop.mods.link_pick_body"),
                [f"{f['filename']}  ·  {size(f.get('size'))}" for f in files],
            )
            if picked is None:
                return
            version_id = files[picked]["version_id"]

            def failed(exc: Exception) -> None:
                problems = getattr(exc, "problems", {})
                if not problems.get("confirm"):
                    self.said(exc)
                elif self.sure_unknown(files[picked]["filename"], problems):
                    send(True)
                else:
                    self.say("")

            def send(sure: bool) -> None:
                self.say(text("mobile.content.working"))
                self.ask(
                    "POST",
                    "/mods/link/add",
                    lambda result: self.added([result]),
                    failed,
                    body={"version_id": version_id, "confirm": sure},
                    slow=True,
                )

            send(False)

        self.ask("POST", "/mods/link", offer, self.said, body={"url": url}, slow=True)

    def pick_pack(self) -> None:
        path = self.window_.pc.pick_file(
            text("pack.into_title"), f"{text('pack.choose')} (*.mrpack *.zip)"
        )
        if path:
            self.bring_pack(path)

    def bring_pack(self, path: str) -> None:
        """Read the pack first; nothing is downloaded or changed until Import."""
        sid, agent = self.row["id"], self.window_.agent
        self.say(text("pack.reading"))
        self.run_here(
            lambda: agent.upload(f"/modpacks/inspect?server_id={quote(sid)}", path),
            self.confirm_pack,
        )

    def find_pack(self) -> None:
        """A CurseForge modpack by name: pick one, then it's read like a file."""
        query = self.pack_box.text().strip()
        if not query:
            return
        sid, agent = self.row["id"], self.window_.agent

        def offer(found: dict) -> None:
            hits = found.get("hits") or []
            if not hits:
                self.say(text("mods.nothing_found"))
                return
            picked = self.window_.pick(
                text("desktop.mods.pack_find"),
                text("desktop.mods.pack_pick"),
                [f"{h.get('title')}  ·  {h.get('downloads') or 0:,}" for h in hits],
            )
            if picked is None:
                return
            body = {"project": hits[picked]["project_id"], "server_id": sid}
            self.say(text("pack.reading"))
            self.run_here(
                lambda: agent.call("POST", "/modpacks/curseforge", body, slow=True),
                self.confirm_pack,
            )

        self.say(text("mods.searching"))
        self.run_here(
            lambda: agent.call("GET", f"/modpacks/curseforge/search?q={quote(query)}&limit=10"),
            offer,
        )

    def confirm_pack(self, read: dict) -> None:
        pack, token, into = read["pack"], read["token"], read.get("into") or {}
        agent = self.window_.agent

        def forget() -> None:  # the uploaded pack, when it isn't imported
            self.window_.run(
                lambda: agent.call("DELETE", f"/modpacks/{token}"), lambda _r: None, lambda _e: None
            )

        lines = pack_lines(pack)
        if not pack.get("can_import"):
            self.say("\n".join(lines))
            forget()
            return
        steps = [text("pack.step_stop"), text("pack.step_backup")]
        if into.get("version_change"):
            steps.append(
                text(
                    "pack.step_version",
                    type=pack.get("type_name"),
                    version=pack.get("minecraft_version"),
                )
            )
        steps += [text("pack.step_aside"), text("pack.step_files", count=pack["download_count"])]
        body = "\n".join(lines) + "\n\n" + "\n".join(f"• {s}" for s in steps)
        self.say("")
        if not self.window_.confirm(
            text("pack.import_title", name=pack["name"]), body, text("pack.import")
        ):
            forget()
            return

        def done(result: dict) -> None:
            self.say(
                text("pack.imported", name=pack["name"]),
                (lambda: self.window_.move_change("undo")) if result.get("undo") else None,
            )
            self.after_change()
            self.window_.load_server_parts()

        self.say(text("pack.importing"))

        def failed(exc: Exception) -> None:
            self.said(exc)
            forget()

        self.ask(
            "POST", "/modpack", done, failed, body={"token": token, "confirm": True}, slow=True
        )


class AddonsPage(Page):
    """A Bedrock server's behavior and resource packs. The agent checks each
    file before anything is written and turns the pack on in the world; the
    server reads its packs when it starts."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.state = label(kind="sub")
        self.column.addWidget(self.state)
        self.list_card, layout = card("")
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.column.addWidget(self.list_card)
        self.add_card, layout = card("")
        lead, choose, drop_hint = label(kind="sub"), push(""), label(kind="sub")
        choose.clicked.connect(self.pick)
        for widget in (lead, choose, drop_hint):
            layout.addWidget(widget)
        self.wording: list[tuple[Callable[[str], None], str]] = [
            (title_of(self.add_card).setText, "addons.add_title"),
            (lead.setText, "addons.add_lead"),
            (choose.setText, "addons.choose"),
            (drop_hint.setText, "desktop.mods.drop_packs"),
        ]
        self.column.addWidget(self.add_card)
        self.column.addStretch()
        self.world = False

    def load(self) -> None:
        for set_words, key in self.wording:
            set_words(text(key))
        self.add_card.setVisible(self.window_.may(self.row["id"], "mods.manage"))
        self.ask("GET", "/addons", self.show_packs)

    def show_packs(self, data: dict) -> None:
        packs = data.get("addons") or []
        self.world = bool(data.get("world"))
        notes = []
        if data.get("running"):
            notes.append(text("addons.running"))
        if not self.world:
            notes.append(text("addons.no_world"))
        self.state.setText("\n".join(notes))
        self.state.setVisible(bool(notes))
        title_of(self.list_card).setText(
            text(
                "addons.installed.one" if len(packs) == 1 else "addons.installed.other",
                count=len(packs),
            )
        )
        refill(
            self.rows,
            [self.pack_row(p) for p in packs]
            or [label(text("addons.none"), "step"), label(text("addons.none_hint"), "sub")],
        )

    def pack_row(self, pack: dict) -> QWidget:
        row = QWidget()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        words.addWidget(label(pack["name"], "step"))
        kind = text(
            "addons.kind_resource" if pack.get("kind") == "resource" else "addons.kind_behavior"
        )
        parts = [
            kind,
            pack.get("version"),
            text("addons.on" if pack.get("enabled") else "addons.off"),
        ]
        words.addWidget(label("  ·  ".join(p for p in parts if p), "sub"))
        if pack.get("description"):
            words.addWidget(label(pack["description"], "sub"))
        if mode["technical"]:
            words.addWidget(label(f"{pack['uuid']}  ·  {pack.get('folder')}", "muted"))
        h.addLayout(words, 1)
        if self.window_.may(self.row["id"], "mods.manage"):
            on = bool(pack.get("enabled"))
            turn = push(text("addons.turn_off" if on else "addons.turn_on"))
            turn.setAccessibleName(f"{turn.text()}: {pack['name']}")
            turn.setEnabled(self.world)
            turn.clicked.connect(lambda: self.turn(pack, not on))
            remove = push(text("addons.remove"), "plain")
            remove.setAccessibleName(f"{remove.text()}: {pack['name']}")
            remove.clicked.connect(lambda: self.remove(pack))
            h.addWidget(turn)
            h.addWidget(remove)
        return row

    def done(self, words: str, result: dict) -> None:
        self.say(f"{words} {text('addons.restart')}" if result.get("restart_needed") else words)
        self.load()

    def turn(self, pack: dict, on: bool) -> None:
        self.ask(
            "PUT",
            f"/addons/{pack['uuid']}",
            lambda r: self.done(
                text("addons.turned_on" if on else "addons.turned_off", name=pack["name"]), r
            ),
            lambda exc: self.say(str(exc)),
            body={"enabled": on},
        )

    def remove(self, pack: dict) -> None:
        if not self.window_.confirm(
            text("addons.remove_title", name=pack["name"]),
            text("addons.remove_body"),
            text("addons.remove"),
        ):
            return
        self.ask(
            "DELETE",
            f"/addons/{pack['uuid']}",
            lambda r: self.done(text("addons.removed", name=pack["name"]), r),
            lambda exc: self.say(str(exc)),
        )

    def pick(self) -> None:
        kinds = f"{text('page.addons')} ({' '.join('*' + s for s in PACK_FILES)})"
        path = self.window_.pc.pick_file(text("addons.add_title"), kinds)
        if path:
            self.take([path])

    def take(self, paths: list[str]) -> None:
        sid, agent = self.row["id"], self.window_.agent

        def send() -> tuple[list[dict], bool, str]:
            packs: list[dict] = []
            restart = False
            for path in paths:
                try:
                    answer = agent.upload(f"/servers/{sid}/addons/upload", path)
                except AgentError as exc:  # the ones before it stay added
                    return packs, restart, f"{Path(path).name}: {exc}"
                packs += answer.get("installed") or []
                restart = restart or bool(answer.get("restart_needed"))
            return packs, restart, ""

        def sent(result: tuple[list[dict], bool, str]) -> None:
            packs, restart, problem = result
            words = [text("addons.installed_done", name=names(packs, "name"))] if packs else []
            self.done(" ".join([*words, problem]), {"restart_needed": restart and bool(packs)})

        here = self.window_.still_on(sid)
        self.say(text("addons.installing"))
        self.window_.run(send, here(sent), here(lambda exc: self.say(str(exc))))
