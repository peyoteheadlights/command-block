"""The gear's Discord: the bot's token and connection, inviting it, its name
and picture, and each Discord server it's in (its checks, "Set up this
Discord server", which channel does what, and what each Discord role may do
on each Minecraft server). Owner only; the agent checks that too.

What a Discord role may do uses the moderators' level and switch rows
(moderators.py), so both look and work the same."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from .gear import Section, buttons, link_row
from .moderators import Moderators
from .overview import card
from .ui import clock, label, push
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

PORTAL = "https://discord.com/developers/applications"
STEPS = 6  # discord.steps.1 to .6
SERVER_STEPS = 3
KINDS = ("alerts", "status", "chat", "quests", "events")
PER_SERVER = ("status", "chat")
# Shared, and a server may have its own instead (None: the shared one).
OWN_IF_WANTED = ("alerts",)


class Discord(Moderators):
    WORDS = "discord.title"

    def __init__(self, window: MainWindow) -> None:
        Section.__init__(
            self, window, ["status", "token", "invite", "profile", "sharing", "guilds"]
        )
        self.data: dict = {}  # the moderators' catalogue and the servers (GET /accounts)
        self.state: dict = {}
        self.role_names: dict[str, str] = {}

    def allowed(self) -> bool:
        return self.owner()

    def load(self) -> None:
        agent = self.window_.agent
        self.window_.run(
            lambda: (agent.call("GET", "/discord"), agent.call("GET", "/accounts")),
            self.show_state,
            self.problem,
        )

    def show_state(self, found: tuple[dict, dict]) -> None:
        self.state, self.data = found
        state = self.state
        self.fill("status", self.status_card(state))
        self.fill("token", self.token_card(state))
        connected = state.get("state") == "connected"
        self.fill("invite", *([self.invite_card(state)] if connected else []))
        self.fill("profile", *([self.profile_card(state)] if connected else []))
        self.fill("sharing", *([self.sharing_card(state)] if connected else []))
        self.fill("guilds", *[self.guild_card(g) for g in state.get("guilds") or []])

    def done(self, words: str):
        def shown(state: dict) -> None:
            self.state = state
            self.show_state((state, self.data))
            self.say(words)

        return shown

    # ------------------------------------------------------------ the bot
    def status_card(self, state: dict) -> QFrame:
        frame, layout = card(text("discord.bot"))
        now = state.get("state", "off")
        line = label(
            text(f"discord.state.{now}", name=state.get("name") or ""),
            "ok" if now == "connected" else "problem" if now != "off" else "sub",
        )
        layout.addWidget(line)
        if now == "connected" and state.get("connected_at"):
            layout.addWidget(label(text("discord.since", time=clock(state["connected_at"])), "sub"))
        elif state.get("detail"):
            layout.addWidget(label(state["detail"], "sub"))
        for feature in state.get("features_off") or []:
            layout.addWidget(label(text(f"discord.off.{feature}"), "sub"))
        if mode["technical"] and now == "connected":
            for switch, on in (state.get("switches") or {}).items():
                words = (
                    "value.unknown"
                    if on is None
                    else "discord.switch_on"
                    if on
                    else "discord.switch_off"
                )
                layout.addWidget(
                    label(f"{text(f'discord.switch.{switch}')}: {text(words)}", "muted")
                )
        layout.addWidget(label(text("discord.pc_on"), "muted"))
        awake = push(text("discord.keep_awake"), "plain")
        awake.clicked.connect(lambda: self.window_.open_list("PC settings", "pc"))
        layout.addLayout(buttons(awake))
        return frame

    def token_card(self, state: dict) -> QFrame:
        frame, layout = card(text("discord.token_title"))
        if not state.get("token_set") or state.get("state") == "token_rejected":
            for i in range(1, STEPS + 1):
                layout.addWidget(label(text(f"discord.steps.{i}"), "step"))
            portal = push(text("discord.portal"))
            portal.clicked.connect(lambda: self.window_.pc.open_link(PORTAL))
            layout.addLayout(buttons(portal))
        token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText(text("discord.token_placeholder"))
        token.setAccessibleName(text("discord.token_title"))
        save = push(text("discord.token_save"), "primary")

        def send() -> None:
            if token.text().strip():
                self.call(
                    "PUT",
                    "/discord/token",
                    self.done(text("discord.token_saved")),
                    body={"token": token.text().strip()},
                )

        save.clicked.connect(send)
        row = [save]
        if state.get("token_set"):
            remove = push(text("discord.token_remove"), "danger")
            remove.clicked.connect(self.remove_token)
            row.append(remove)
        layout.addWidget(token)
        layout.addLayout(buttons(*row))
        layout.addWidget(label(text("discord.token_kept"), "muted"))
        return frame

    def remove_token(self) -> None:
        if self.window_.confirm(
            text("discord.token_remove"),
            text("discord.token_remove_body"),
            text("discord.token_remove"),
        ):
            self.call("DELETE", "/discord/token", self.done(text("discord.token_removed")))

    def invite_card(self, state: dict) -> QFrame:
        frame, layout = card(text("discord.invite_title"))
        for i in range(1, SERVER_STEPS + 1):
            layout.addWidget(label(text(f"discord.server_steps.{i}"), "step"))
        url = state.get("invite_url") or ""
        invite = push(text("discord.invite"), "primary" if not state.get("guilds") else "")
        invite.clicked.connect(lambda: self.window_.pc.open_link(url))
        layout.addLayout(buttons(invite))
        if mode["technical"]:
            layout.addLayout(link_row(self.window_, url))
        return frame

    def profile_card(self, state: dict) -> QFrame:
        frame, layout = card(text("discord.profile_title"))
        name = QLineEdit(state.get("name") or "")
        name.setMaxLength(32)
        name.setAccessibleName(text("discord.profile_name"))
        save = push(text("discord.profile_save"))
        save.clicked.connect(lambda: self.profile({"name": name.text().strip()}))
        picture = push(text("discord.profile_picture"))
        picture.clicked.connect(self.pick_picture)
        logo = push(text("discord.profile_logo"), "plain")
        logo.clicked.connect(lambda: self.profile({"avatar": "logo"}))
        layout.addWidget(name)
        layout.addLayout(buttons(save, picture, logo))
        layout.addWidget(label(text("discord.profile_hint"), "muted"))
        return frame

    def profile(self, body: dict[str, Any]) -> None:
        self.call("PUT", "/discord/profile", self.done(text("discord.profile_saved")), body=body)

    def pick_picture(self) -> None:
        path = self.window_.pc.pick_file(
            text("discord.profile_picture"), "Pictures (*.png *.jpg *.jpeg)"
        )
        if path:
            data = Path(path).read_bytes()
            self.profile({"avatar": base64.b64encode(data).decode()})

    def sharing_card(self, state: dict) -> QFrame:
        """What /ip shows for each server, and whether the webhook keeps
        sending once the bot posts the alerts."""
        frame, layout = card(text("discord.sharing_title"))
        saved = state.get("addresses") or {}
        grid = QGridLayout()
        for i, s in enumerate(self.data.get("servers") or []):
            words = text("discord.address", server=s["name"])
            grid.addWidget(label(words), i, 0)
            address = QLineEdit(saved.get(s["id"], ""))
            address.setMaxLength(253)
            address.setPlaceholderText("play.example.com")
            address.setAccessibleName(words)
            grid.addWidget(address, i, 1)
            save = push(text("discord.address_save"))
            save.setAccessibleName(f"{text('discord.address_save')}: {words}")
            save.clicked.connect(
                lambda _c=False, sid=s["id"], box=address: self.call(
                    "PUT",
                    "/discord/addresses",
                    self.done(text("discord.address_saved")),
                    body={"server_id": sid, "address": box.text().strip() or None},
                )
            )
            grid.addWidget(save, i, 2)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addWidget(label(text("discord.address_hint"), "muted"))
        both = QCheckBox(text("discord.webhook_with_bot"))
        both.setChecked(bool(state.get("webhook_with_bot")))
        both.toggled.connect(
            lambda on: self.call(
                "PUT",
                "/settings",
                lambda _r: self.say(text("settings.saved")),
                body={"updates": {"notifications.webhook_with_bot": on}},
            )
        )
        layout.addWidget(both)
        layout.addWidget(label(text("discord.alerts_hint"), "muted"))
        return frame

    # ------------------------------------------------------------ a Discord server
    def guild_card(self, guild: dict) -> QFrame:
        gid = guild["id"]
        frame, layout = card(guild["name"])
        report = guild.get("report") or {}
        problems = report.get("problems")
        if problems is None:
            layout.addWidget(label(text("discord.checking"), "sub"))
        elif problems:
            for p in problems:
                values = {
                    "guild": guild["name"],
                    **p,
                    "kind": text(f"discord.kind.{p.get('kind', 'alerts')}"),
                }
                layout.addWidget(
                    label("✗ " + text(f"discord.problem.{p['id']}", **values), "problem")
                )
        else:
            layout.addWidget(label("✓ " + text("discord.all_good"), "ok"))
        again = push(text("discord.check_again"), "plain")
        again.clicked.connect(
            lambda: self.call("POST", "/discord/check", self.done(text("discord.checked")))
        )
        layout.addLayout(buttons(again))

        # Which Minecraft servers it's for, then the one button.
        layout.addWidget(label(text("discord.for_servers"), "step"))
        servers = self.data.get("servers") or []
        tied = set(guild.get("servers") or [])
        ticks: dict[str, QCheckBox] = {}
        for s in servers:
            tick = QCheckBox(s["name"])
            tick.setChecked(s["id"] in tied or (not tied and len(servers) == 1))
            ticks[s["id"]] = tick
            layout.addWidget(tick)
        setup = push(text("discord.set_up"), "primary" if not tied else "")
        setup.clicked.connect(
            lambda: self.call(
                "POST",
                f"/discord/guilds/{gid}/setup",
                lambda made: self.done(
                    text("discord.set_up_done", made=", ".join(made["made"]))
                    if made["made"]
                    else text("discord.set_up_nothing")
                )(made["status"]),
                body={"servers": [sid for sid, t in ticks.items() if t.isChecked()]},
                slow=True,
            )
        )
        layout.addLayout(buttons(setup))
        layout.addWidget(label(text("discord.set_up_hint"), "muted"))
        if tied and report.get("channels") is not None:
            layout.addWidget(self.channels(gid, report, servers, tied))
            layout.addWidget(self.roles(guild, report, servers))
            layout.addWidget(self.levels(guild, report))
        return frame

    def levels(self, guild: dict, report: dict) -> QWidget:
        return levels_box(
            guild,
            report.get("roles") or [],
            lambda body: self.call(
                "PUT",
                f"/discord/guilds/{guild['id']}/levels",
                self.then_reload(text("discord.levels_saved")),
                body=body,
            ),
        )

    def channels(self, gid: str, report: dict, servers: list[dict], tied: set[str]) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.addWidget(label(text("discord.channels"), "step"), 0, 0, 1, 2)
        current = {(t["kind"], t["server_id"]): t["channel_id"] for t in report.get("tied") or []}
        rows: list[tuple[str, str, str]] = [
            (k, "", text(f"discord.kind.{k}")) for k in KINDS if k not in PER_SERVER
        ]
        for s in servers:
            if s["id"] in tied:
                rows += [
                    (k, s["id"], f"{text(f'discord.kind.{k}')}: {s['name']}")
                    for k in (*PER_SERVER, *OWN_IF_WANTED)
                ]
        no_chat = set(self.state.get("no_game_chat") or [])
        i = 0
        for kind, sid, words in rows:
            i += 1
            grid.addWidget(label(words), i, 0)
            pick = QComboBox()
            pick.setAccessibleName(words)
            shared = sid and kind in OWN_IF_WANTED
            pick.addItem(text("discord.same_alerts" if shared else "discord.no_channel"), None)
            for channel in report.get("channels") or []:
                pick.addItem(f"#{channel['name']}", channel["id"])
            pick.setCurrentIndex(max(pick.findData(current.get((kind, sid))), 0))
            pick.activated.connect(
                lambda _i, p=pick, k=kind, s=sid: self.call(
                    "PUT",
                    f"/discord/guilds/{gid}/channels",
                    self.done(text("discord.channel_saved")),
                    body={"kind": k, "server_id": s, "channel_id": p.currentData()},
                )
            )
            grid.addWidget(pick, i, 1)
            if kind == "chat" and sid in no_chat:
                i += 1
                name = next((s["name"] for s in servers if s["id"] == sid), sid)
                grid.addWidget(label(text("discord.no_game_chat", server=name), "muted"), i, 1)
        grid.setColumnStretch(1, 1)
        return box

    def roles(self, guild: dict, report: dict, servers: list[dict]) -> QWidget:
        """Each Discord role with a permission level, then a picker to give
        one more. The rows are the moderators' own."""
        gid = guild["id"]
        box = QWidget()
        column = QGridLayout(box)
        column.setContentsMargins(0, 8, 0, 0)
        column.addWidget(label(text("discord.roles"), "step"), 0, 0, 1, 4)
        column.addWidget(label(text("discord.roles_hint"), "muted"), 1, 0, 1, 4)
        grants = guild.get("grants") or {}
        roles = report.get("roles") or []
        self.role_names.update({r["id"]: r["name"] for r in roles})
        row = 2
        for role in roles:
            if role["id"] not in grants:
                continue
            column.addWidget(label(self.role_name(role, gid), "step"), row, 0, 1, 4)
            row += 1
            held = grants[role["id"]]
            for s in servers:
                fake_mod = {"username": f"{gid}/{role['id']}", "all_servers": False}
                self.server_row(
                    column, row, fake_mod, s["id"], s["name"], set(held.get(s["id"]) or [])
                )
                row += 2
        add = QComboBox()
        add.setAccessibleName(text("discord.add_role"))
        add.addItem(text("discord.add_role"), None)
        for role in roles:
            if role["id"] not in grants:
                add.addItem(self.role_name(role, gid), role["id"])

        def picked(_index: int) -> None:
            if add.currentData():
                level = sorted(self.level_set())
                body = {"grants": {sid: level for sid in guild.get("servers") or []}}
                self.save(f"{gid}/{add.currentData()}", body)

        add.activated.connect(picked)
        column.addWidget(add, row, 0, 1, 2)
        return box

    @staticmethod
    def role_name(role: dict, gid: str) -> str:
        return text("discord.everyone") if role["id"] == gid else role["name"]

    def level_set(self) -> set[str]:
        catalogue = self.data["permissions"]
        default = catalogue["default_level"]
        return next(set(x["permissions"]) for x in catalogue["levels"] if x["id"] == default)

    # The moderators' rows call these with "<guild>/<role>" as the name.
    def save(self, name: str, body: dict) -> None:
        gid, role = name.split("/", 1)
        self.call(
            "PUT",
            f"/discord/guilds/{gid}/roles/{role}",
            self.done(text("discord.role_saved")),
            body={"role_name": self.role_names.get(role, ""), "grants": body.get("grants") or {}},
        )


def levels_box(guild: dict, roles: list[dict], save: Callable[[dict], None]) -> QWidget:
    """Level-ups on or off, the weekly leaderboard's day, and which Discord
    role goes with which Level, saved together. On the owner's Discord page
    and, for moderators with Quests and events, the Quests and events page."""
    gid = guild["id"]
    saved = guild.get("levels") or {}
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 8, 0, 0)
    grid.addWidget(label(text("discord.levels"), "step"), 0, 0, 1, 3)
    grid.addWidget(label(text("discord.levels_hint"), "muted"), 1, 0, 1, 3)
    announce = QCheckBox(text("discord.level_ups"))
    announce.setChecked(bool(saved.get("announce", True)))
    grid.addWidget(announce, 2, 0, 1, 3)
    grid.addWidget(label(text("discord.leaderboard")), 3, 0)
    day = QComboBox()
    day.setAccessibleName(text("discord.leaderboard"))
    day.addItem(text("discord.leaderboard_off"), None)
    for i in range(7):  # Monday first, in the PC's language
        day.addItem(QLocale().dayName(i + 1), i)
    day.setCurrentIndex(max(day.findData(saved.get("leaderboard_day")), 0))
    grid.addWidget(day, 3, 1, 1, 2)
    grid.addWidget(label(text("discord.leaderboard_hint"), "muted"), 4, 0, 1, 3)
    roles = [r for r in roles if r["id"] != gid]
    rows: list[tuple[QSpinBox, QComboBox]] = []
    lines = [5]  # the grid line the next row goes on

    def add_row(level: int, role_id: str | None) -> None:
        at = lines[0]
        lines[0] += 1
        number = QSpinBox()
        number.setRange(1, 1000)
        number.setValue(level)
        number.setPrefix(text("discord.level_role") + " ")
        number.setAccessibleName(text("discord.level_role"))
        pick = QComboBox()
        pick.setAccessibleName(f"{text('discord.level_role')} {level}")
        for role in roles:
            pick.addItem(role["name"], role["id"])
        pick.setCurrentIndex(max(pick.findData(role_id), 0))
        grid.addWidget(number, at, 0)
        grid.addWidget(pick, at, 1)
        remove = push(text("discord.level_remove"), "plain")
        remove.setAccessibleName(
            f"{text('discord.level_remove')}: {text('discord.level_role')} {level}"
        )
        entry = (number, pick)
        rows.append(entry)

        def gone() -> None:
            rows.remove(entry)
            for widget in (number, pick, remove):
                widget.hide()
                widget.deleteLater()

        remove.clicked.connect(gone)
        grid.addWidget(remove, at, 2)

    for row in saved.get("roles") or []:
        add_row(row["level"], row["role_id"])
    add = push(text("discord.level_add"), "plain")
    add.clicked.connect(lambda: add_row(max((n.value() for n, _ in rows), default=0) + 5, None))
    save_button = push(text("discord.level_save"), "primary")

    def send() -> None:
        body: dict[str, Any] = {
            "announce": announce.isChecked(),
            "leaderboard_day": day.currentData(),
        }
        wanted = sorted(
            (n.value(), p.currentData()) for n, p in rows if p.currentData() is not None
        )
        # Only sent when changed: once changed, setup leaves Level roles alone.
        if wanted != sorted((r["level"], r["role_id"]) for r in saved.get("roles") or []):
            body["roles"] = [{"level": at, "role_id": rid} for at, rid in wanted]
        save(body)

    save_button.clicked.connect(send)
    grid.addLayout(buttons(add, save_button), 100, 0, 1, 3)
    grid.setColumnStretch(1, 1)
    return box
