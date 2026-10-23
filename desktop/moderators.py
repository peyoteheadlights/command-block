"""The gear's Moderators: friends' own sign-ins, with what each may do on
each server. The same levels and switches as the phone apps, from the
agent's catalogue (/api/accounts), so the apps keep no copy of the rules.
Risky switches show only in Technical, with their warning."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .gear import TAILSCALE_MACHINES, Section, buttons, link_row, said
from .overview import card
from .tailscale_step import QrCode
from .ui import clock, field, label, push
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

APP_DOWNLOAD = "https://github.com/peyoteheadlights/command-block/releases"
SEEING = "server.view"  # comes with anything ticked; not a switch
# PC-wide permissions' words: (switch, hint).
PC_WORDS = {"agent.start": ("desktop.mod.pc_agent_start", "desktop.mod.pc_agent_start_hint")}


def level_of(permissions: set[str], catalogue: dict) -> str:
    if not permissions:
        return "none"
    for level in catalogue["levels"]:
        if set(level["permissions"]) == permissions:
            return level["id"]
    return "custom"


def level(name: str, catalogue: dict) -> set[str]:
    return next((set(x["permissions"]) for x in catalogue["levels"] if x["id"] == name), set())


def toggle(permissions: set[str], permission: str, on: bool, catalogue: dict) -> set[str]:
    """Ticking a switch brings what it needs (Make ops needs Players);
    unticking one takes away what needs it. Nothing ticked hides the server."""
    needs = catalogue["needs"]
    now = set(permissions)
    if on:
        now |= {permission, *needs.get(permission, [])}
    else:
        now -= {permission} | {k for k, v in needs.items() if permission in v}
    now.discard(SEEING)
    return now | {SEEING} if now else set()


def switch_words(permission: str) -> str:
    return text(f"mobile.mod.switch_{permission.replace('.', '_')}")


def summary(permissions: set[str], catalogue: dict) -> str:
    """A custom set as its switches' names, else nothing (the level says it)."""
    if level_of(permissions, catalogue) != "custom":
        return ""
    names = [
        switch_words(p) for g in catalogue["groups"] for p in g["permissions"] if p in permissions
    ]
    if len(names) > 3:
        return text("mobile.mod.summary_more", list=", ".join(names[:3]), count=len(names) - 3)
    return ", ".join(names)


class Moderators(Section):
    WORDS = "page.helpers"

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window, ["invite", "list", "add", "key"])
        self.data: dict = {}
        self.key: dict = {}  # the Tailscale key for share links (Technical)

    def allowed(self) -> bool:
        return self.owner()

    def load(self) -> None:
        self.call("GET", "/accounts", self.show_list)
        self.fill("key")
        if mode["technical"]:
            self.call("GET", "/tailscale/share-key", self.show_key)

    def show_list(self, data: dict) -> None:
        self.data = data
        cards: list[QWidget] = [label(text("mobile.mod.lead"), "sub")]
        cards += [self.moderator_card(h) for h in data["helpers"]]
        if not data["helpers"]:
            cards.append(label(text("mobile.mod.none"), "sub"))
        self.fill("list", *cards)
        self.fill("add", self.add_card())

    def save(self, name: str, body: dict) -> None:
        self.call(
            "PUT",
            f"/accounts/{quote(name, safe='')}",
            self.then_reload(text("mobile.mod.saved")),
            body=body,
        )

    # -------------------------------------------------------- one moderator
    def moderator_card(self, mod: dict) -> QFrame:
        name = mod["username"]
        frame, layout = card(name)
        if mod.get("invite_waiting"):
            invite = mod.get("invite") or {}
            words = text("mobile.mod.invite_waiting")
            if invite.get("expires_at"):
                words += ". " + text("mobile.mod.invite_expires", time=clock(invite["expires_at"]))
            layout.addWidget(label(words, "sub"))
        else:
            layout.addWidget(
                label(
                    text("mobile.mod.last_used", time=clock(mod["last_used"]))
                    if mod.get("last_used")
                    else text("mobile.mod.never_used"),
                    "sub",
                )
            )
        every = QCheckBox(text("mobile.mod.all_servers"))
        every.setChecked(bool(mod.get("all_servers")))
        every.setToolTip(text("mobile.mod.all_servers_hint"))
        every.toggled.connect(lambda on: self.save(name, self.all_servers(on, mod)))
        layout.addWidget(every)
        layout.addWidget(label(text("mobile.mod.all_servers_hint"), "muted"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        places = [(a["id"], a["name"], set(a["permissions"])) for a in mod["access"]]
        if mod.get("all_servers"):
            new = set((mod.get("new_server") or {}).get("permissions") or [])
            places.append((None, text("mobile.mod.new_servers"), new))
        for row, (sid, where, held) in enumerate(places):
            self.server_row(grid, row * 2, mod, sid, where, held)
        layout.addLayout(grid)
        if mode["technical"]:  # waking the PC is Technical only, like Wake-on-LAN
            for permission in self.data["permissions"].get("pc") or []:
                words, hint = PC_WORDS.get(permission, (permission, ""))
                box = QCheckBox(text(words) if hint else words)
                box.setChecked(permission in (mod.get("pc") or []))
                box.toggled.connect(
                    lambda on, p=permission: self.save(
                        name, {"pc": sorted(set(mod.get("pc") or []) ^ {p})}
                    )
                )
                layout.addWidget(box)
                if hint:
                    layout.addWidget(label(text(hint), "muted"))
        actions = []
        if mod.get("invite_waiting"):
            again = push(text("mobile.mod.invite_new"))
            again.setToolTip(text("mobile.mod.invite_new_hint"))
            again.clicked.connect(lambda: self.invite(name, False))
            cancel = push(text("mobile.mod.invite_cancel"), "plain")
            cancel.clicked.connect(
                lambda: self.call(
                    "DELETE",
                    f"/accounts/{quote(name, safe='')}/invite",
                    self.then_reload(text("mobile.mod.invite_cancelled")),
                )
            )
            actions += [again, cancel]
        reset = push(text("mobile.mod.reset"))
        reset.clicked.connect(lambda: self.reset(name))
        remove = push(text("mobile.mod.remove"), "danger")
        remove.clicked.connect(lambda: self.remove(name))
        layout.addLayout(buttons(*actions, reset, remove))
        return frame

    def server_row(
        self, grid: QGridLayout, row: int, mod: dict, sid: str | None, where: str, held: set[str]
    ) -> None:
        """A server (or New servers, ``sid`` None): its level, and its
        switches under it once "Change permissions" is pressed."""
        catalogue, name = self.data["permissions"], mod["username"]
        grid.addWidget(label(where), row, 0)
        levels = QComboBox()
        levels.setAccessibleName(f"{text('mobile.mod.level')}: {where}")
        now = level_of(held, catalogue)
        if now == "custom":
            levels.addItem(text("mobile.mod.level_custom"), "custom")
        for each in [x["id"] for x in catalogue["levels"]] + ([] if sid is None else ["none"]):
            levels.addItem(text(f"mobile.mod.level_{each}"), each)
        levels.setCurrentIndex(levels.findData(now))

        def picked(index: int) -> None:
            chosen = levels.itemData(index)
            if chosen != "custom" and chosen != now:
                self.save(name, self.body(sid, level(chosen, catalogue)))

        levels.activated.connect(picked)
        grid.addWidget(levels, row, 1)
        hidden = sum(1 for p in held if p in catalogue["technical"]) if not mode["technical"] else 0
        note = summary(held, catalogue)
        if hidden:
            note = " ".join(x for x in (note, text("mobile.mod.more_technical", count=hidden)) if x)
        grid.addWidget(label(note, "muted"), row, 2)
        change = push(text("mobile.mod.change"), "plain")
        grid.addWidget(change, row, 3)
        switches = self.switches(mod, sid, held)
        switches.hide()
        change.clicked.connect(lambda: switches.setVisible(not switches.isVisible()))
        grid.addWidget(switches, row + 1, 0, 1, 4)
        grid.setColumnStretch(2, 1)

    def switches(self, mod: dict, sid: str | None, held: set[str]) -> QWidget:
        catalogue, name = self.data["permissions"], mod["username"]
        box = QFrame()
        box.setProperty("kind", "row")
        column = QVBoxLayout(box)
        chosen = set(held)
        ticks: dict[str, QCheckBox] = {}
        warn = label(text("mobile.mod.warn"), "problem")

        def sync() -> None:
            for permission, tick in ticks.items():
                tick.blockSignals(True)
                tick.setChecked(permission in chosen)
                tick.blockSignals(False)
            warn.setVisible(any(p in catalogue["warn"] for p in chosen))

        def flip(permission: str, on: bool) -> None:
            nonlocal chosen
            chosen = toggle(chosen, permission, on, catalogue)
            sync()

        for group in catalogue["groups"]:
            shown = [
                p
                for p in group["permissions"]
                if mode["technical"] or p not in catalogue["technical"]
            ]
            if not shown:
                continue
            column.addWidget(label(text(f"mobile.mod.group_{group['id']}"), "step"))
            for permission in shown:
                tick = QCheckBox(switch_words(permission))
                tick.toggled.connect(lambda on, p=permission: flip(p, on))
                ticks[permission] = tick
                column.addWidget(tick)
        column.addWidget(warn)
        sync()
        if sid is None:
            save = push(text("mobile.mod.save"), "primary")
            save.clicked.connect(lambda: self.save(name, {"new_server": sorted(chosen)}))
            column.addLayout(buttons(save))
            return box
        here = push(text("mobile.mod.save_here"), "primary")
        here.clicked.connect(lambda: self.save(name, self.body(sid, chosen)))
        some = push(text("mobile.mod.save_for"))
        some.clicked.connect(lambda: self.save_for(name, chosen))
        every = push(text("mobile.mod.apply_all"))
        every.clicked.connect(
            lambda: self.save(
                name,
                {
                    "grants": {s["id"]: sorted(chosen) for s in self.data["servers"]},
                    **({"new_server": sorted(chosen)} if mod.get("all_servers") else {}),
                },
            )
        )
        column.addLayout(buttons(here, some, every))
        return box

    @staticmethod
    def body(sid: str | None, permissions: set[str]) -> dict:
        if sid is None:
            return {"new_server": sorted(permissions)}
        return {"grants": {sid: sorted(permissions)}}

    def save_for(self, name: str, chosen: set[str]) -> None:
        servers = self.data["servers"]
        picked = self.window_.tick(
            text("mobile.mod.pick_servers"),
            text("mobile.mod.change"),
            [(s["name"], False) for s in servers],
            text("mobile.mod.save"),
        )
        if not picked:
            return
        self.save(name, {"grants": {servers[i]["id"]: sorted(chosen) for i in picked}})

    def all_servers(self, on: bool, mod: dict) -> dict:
        """On: each new server gives what most of their servers have now (the
        default level if none). Off: they keep the servers they have."""
        if not on:
            return {"all_servers": False}
        seen = Counter(frozenset(a["permissions"]) for a in mod["access"] if a["permissions"])
        catalogue = self.data["permissions"]
        usual = seen.most_common(1)[0][0] if seen else level(catalogue["default_level"], catalogue)
        return {"all_servers": True, "new_server": sorted(usual)}

    def reset(self, name: str) -> None:
        if self.window_.confirm(
            text("mobile.mod.reset_title", name=name),
            text("mobile.mod.reset_body"),
            text("mobile.mod.reset"),
        ):
            self.invite(name, True)

    def invite(self, name: str, forget: bool) -> None:
        def made(invite: dict) -> None:
            self.load()
            self.show_invite(invite, name)

        self.call(
            "POST",
            f"/accounts/{quote(name, safe='')}/invite",
            made,
            body={"forget_password": forget},
        )

    def remove(self, name: str) -> None:
        if self.window_.confirm(
            text("helpers.remove_title", name=name),
            text("mobile.mod.remove_body"),
            text("mobile.mod.remove"),
        ):
            self.call(
                "DELETE",
                f"/accounts/{quote(name, safe='')}",
                self.then_reload(text("mobile.mod.removed", name=name)),
            )

    # -------------------------------------------------------- adding and inviting
    def add_card(self) -> QFrame:
        catalogue, servers = self.data["permissions"], self.data["servers"]
        frame, layout = card(text("mobile.mod.add"))
        name = QLineEdit()
        name.setObjectName("moderatorName")
        name.setMaxLength(32)
        layout.addWidget(field(text("mobile.mod.name"), name, text("mobile.mod.name_hint")))
        layout.addWidget(label(text("mobile.mod.servers"), "step"))
        every = QCheckBox(text("mobile.mod.all_servers"))
        every.setChecked(True)
        layout.addWidget(every)
        picks = QWidget()
        column = QVBoxLayout(picks)
        column.setContentsMargins(20, 0, 0, 0)
        ticks = []
        for server in servers:
            tick = QCheckBox(server["name"])
            ticks.append(tick)
            column.addWidget(tick)
        picks.hide()
        every.toggled.connect(lambda on: picks.setVisible(not on))
        layout.addWidget(picks)
        layout.addWidget(label(text("mobile.mod.level"), "step"))
        group = QButtonGroup(frame)
        for each in catalogue["levels"]:
            key = f"mobile.mod.level_{each['id']}"
            choice = QRadioButton(f"{text(key)}: {text(key + '_hint')}")
            choice.setProperty("level", each["id"])
            choice.setChecked(each["id"] == catalogue["default_level"])
            group.addButton(choice)
            layout.addWidget(choice)
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(
            field(text("desktop.mod.password"), password, text("desktop.mod.password_hint"))
        )
        result = label()
        result.hide()
        add = push(text("mobile.mod.add"), "primary")

        def send() -> None:
            chosen = [s["id"] for s, t in zip(servers, ticks, strict=True) if t.isChecked()]
            if not every.isChecked() and not chosen:
                said(result, text("mobile.mod.pick_a_server"), "problem")
                return
            who = name.text().strip()
            picked = group.checkedButton()
            body: dict[str, Any] = {
                "username": who,
                "password": password.text() or None,
                "servers": None if every.isChecked() else chosen,
                "permissions": sorted(level(str(picked.property("level")), catalogue))
                if picked
                else None,
            }

            def added(answer: dict) -> None:
                self.load()
                if answer.get("invite"):
                    self.show_invite(answer["invite"], who)
                else:
                    self.say(text("helpers.added", name=who))

            self.call("POST", "/accounts", added, lambda e: said(result, str(e), "problem"), body)

        add.clicked.connect(send)
        layout.addLayout(buttons(add))
        layout.addWidget(result)
        return frame

    def show_invite(self, invite: dict, name: str) -> None:
        """The invite once, at the top: its QR code and link to copy, with the
        Tailscale share link the owner may add (it goes in the message)."""
        frame, layout = card(text("mobile.mod.invite_title", name=name))
        if not invite.get("ok") or not invite.get("url"):
            reason = invite.get("reason") or text("value.unknown")
            layout.addWidget(label(text("mobile.mod.no_code", reason=reason), "problem"))
        else:
            layout.addWidget(label(text("mobile.mod.invite_lead", name=name), "sub"))
            qr = QrCode()
            qr.setAccessibleName(text("mobile.mod.invite_title", name=name))
            qr.show_rows(invite.get("qr") or [])
            layout.addWidget(qr, 0, Qt.AlignmentFlag.AlignLeft)
            layout.addLayout(link_row(self.window_, invite["url"]))
            layout.addWidget(
                label(
                    text("mobile.mod.invite_expires", time=clock(invite.get("expires_at"))),
                    "muted",
                )
            )
            layout.addWidget(label(text("mobile.mod.invite_tailscale", name=name)))
            share = QLineEdit()
            share.setObjectName("shareLink")
            layout.addWidget(field(text("desktop.mod.share_link"), share))
            open_share = push(text("desktop.ts.share"), "plain")
            open_share.clicked.connect(lambda: self.window_.pc.open_link(TAILSCALE_MACHINES))
            make = push(text("desktop.share.make"))
            make.setVisible(bool(mode["technical"] and self.key.get("saved")))
            make.clicked.connect(
                lambda: self.call(
                    "POST", "/tailscale/share-link", lambda made: share.setText(made["url"])
                )
            )
            copy = push(text("desktop.mod.copy"), "primary")
            copied = label()
            copied.hide()

            def copy_message() -> None:
                message = text(
                    "mobile.mod.invite_message", name=name, app=APP_DOWNLOAD, link=invite["url"]
                )
                if share.text().strip():
                    message += "\n\n" + text("desktop.mod.share_line", share=share.text().strip())
                QGuiApplication.clipboard().setText(message)
                said(copied, text("desktop.mod.copied", name=name))

            copy.clicked.connect(copy_message)
            layout.addLayout(buttons(copy, make, open_share))
            layout.addWidget(copied)
        done = push(text("desktop.tailscale.finish"))
        done.clicked.connect(lambda: self.fill("invite"))
        layout.addLayout(buttons(done))
        self.fill("invite", frame)

    # -------------------------------------------------------- the Tailscale key
    def show_key(self, key: dict) -> None:
        """Technical: a Tailscale key saved once, so invites get their share
        link made for them (agent/tailscale_share.py)."""
        self.key = key
        frame, layout = card(text("desktop.share.title"))
        if key.get("saved"):
            token = key.get("kind") == "token"
            words = text("desktop.share.saved_token" if token else "desktop.share.saved_oauth")
            if key.get("expires_at"):
                words += " " + text("desktop.share.runs_out", time=clock(key["expires_at"]))
            layout.addWidget(label(words, "problem" if key.get("expiring") else "sub"))
            remove = push(text("desktop.share.remove"), "danger")

            def removed(answer: dict) -> None:
                self.show_key(answer)
                page = answer.get("revoke_page")
                if page:
                    self.say(text("desktop.share.removed"))
                    revoke = push(text("desktop.share.revoke"))
                    revoke.clicked.connect(lambda: self.window_.pc.open_link(page))
                    self.slots["key"].addWidget(revoke)

            remove.clicked.connect(lambda: self.call("DELETE", "/tailscale/share-key", removed))
            layout.addLayout(buttons(remove))
            self.fill("key", frame)
            return
        layout.addWidget(label(text("desktop.share.lead"), "sub"))
        kinds = QButtonGroup(frame)
        oauth = QRadioButton(text("desktop.share.oauth"))
        oauth.setChecked(True)
        token = QRadioButton(text("desktop.share.token"))
        for choice in (oauth, token):
            kinds.addButton(choice)
            layout.addWidget(choice)
        client_id = QLineEdit()
        client_id.setObjectName("clientId")
        secret = QLineEdit()
        secret.setObjectName("shareSecret")
        secret.setEchoMode(QLineEdit.EchoMode.Password)
        id_field = field(text("desktop.share.client_id"), client_id)
        layout.addWidget(id_field)
        layout.addWidget(field(text("desktop.share.secret"), secret))
        hint = label(text("desktop.share.oauth_hint"), "muted")
        layout.addWidget(hint)

        def picked() -> None:
            id_field.setVisible(oauth.isChecked())
            hint.setText(
                text(
                    "desktop.share.oauth_hint" if oauth.isChecked() else "desktop.share.token_hint"
                )
            )

        oauth.toggled.connect(lambda _on: picked())
        save = push(text("desktop.share.save"), "primary")

        def saved(answer: dict) -> None:
            secret.clear()
            self.show_key(answer)
            self.say(text("desktop.share.done"))

        save.clicked.connect(
            lambda: self.call(
                "PUT",
                "/tailscale/share-key",
                saved,
                body={
                    "kind": "oauth" if oauth.isChecked() else "token",
                    "secret": secret.text(),
                    "client_id": client_id.text() if oauth.isChecked() else None,
                },
            )
        )
        layout.addLayout(buttons(save))
        self.fill("key", frame)
