"""Players and chat: who is online, everyone seen, Minecraft's own lists
(whitelist or allowlist, operators, banned) with each player's buttons, and
the in-game chat with a box to send as "Server".

A button sends the normal Minecraft command. It shows as "Sent" until the
server's console confirms it, then "Done" (or what the server said instead).
Who is whitelisted, an operator or banned comes from Minecraft's own files,
never from what was sent.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import quote

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .overview import card, title_of
from .ui import Page, clock, duration, label, push, refill
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

ACTION_WORDS = {
    "whitelist_add": "players.do_whitelist_add",
    "whitelist_remove": "players.do_whitelist_remove",
    "op": "players.do_op",
    "deop": "players.do_deop",
    "kick": "players.do_kick",
    "ban": "players.do_ban",
    "pardon": "players.do_pardon",
    "address_allow": "whitelist.do_address_allow",
    "address_remove": "whitelist.do_address_remove",
}
# Bedrock calls its whitelist the allowlist (the glossary's words).
ALLOWLIST = {
    "players.do_whitelist_add": "desktop.players.do_allowlist_add",
    "players.do_whitelist_remove": "desktop.players.do_allowlist_remove",
    "players.whitelist": "desktop.players.allowlist",
    "players.whitelist_empty": "desktop.players.allowlist_empty",
    "players.tag_whitelisted": "desktop.players.tag_allowlisted",
    "whitelist.title": "whitelist.title_allowlist",
}
STATE_WORDS = {
    "sent": "players.state_sent",
    "done": "players.state_done",
    "unchanged": "players.state_unchanged",
    "failed": "players.state_failed",
    "no_answer": "players.state_no_answer",
}
# Actions that ask first, with a reason the player sees.
CONFIRM = {
    "kick": ("players.kick_title", "players.kick_body"),
    "ban": ("players.ban_title", "players.ban_body"),
}
OPERATOR = {"op", "deop"}  # the owner's alone: an operator can type any command
# A Java name, or an Xbox gamertag (letters, numbers and single spaces).
NAMES = {
    "java": re.compile(r"^\.?[A-Za-z0-9_]{1,16}$"),
    "bedrock": re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]| (?! )){0,19}$"),
}


class PlayersPage(Page):
    POLL_MS = 1000
    POLLS = 22  # then the line stays "Sent" and says so

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.data: dict = {}
        self.lines: dict[str, QLabel] = {}  # action id -> its line under "Sent to the server"
        self.stats: dict[tuple[str, str], str] = {}  # (server, player) -> their stats, while open
        self.notice = label(text("players.start_first"), "notice")
        self.notice.hide()  # until the agent says the server is off
        self.column.addWidget(self.notice)
        self.action_card, layout = card(text("players.recent_actions"))
        self.action_rows = QVBoxLayout()
        layout.addLayout(self.action_rows)
        self.action_card.hide()
        self.column.addWidget(self.action_card)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.online_card, layout = card("")
        self.online_title = title_of(self.online_card)
        self.online = QVBoxLayout()
        layout.addLayout(self.online)
        layout.addStretch()
        top.addWidget(self.online_card, 1)
        chat, layout = card(text("desktop.page.chat"))
        self.chat_note = label(text("chat.not_read"), "sub")
        self.chat_note.hide()  # Bedrock only
        layout.addWidget(self.chat_note)
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setMaximumBlockCount(200)
        self.chat.setAccessibleName(text("chat.log_label"))
        self.chat.setMinimumHeight(220)
        layout.addWidget(self.chat, 1)
        send_row = QHBoxLayout()
        send_row.addWidget(label(text("chat.as_server"), "sub"))
        self.message = QLineEdit()
        self.message.setObjectName("chatMessage")
        self.message.setMaxLength(220)
        self.message.setPlaceholderText(text("chat.placeholder"))
        self.message.setAccessibleName(text("chat.input_label"))
        self.message.returnPressed.connect(self.send_chat)
        send_row.addWidget(self.message, 1)
        self.send = push(text("chat.send"), "primary")
        self.send.clicked.connect(self.send_chat)
        send_row.addWidget(self.send)
        layout.addLayout(send_row)
        self.chat_hint = label(kind="sub")
        layout.addWidget(self.chat_hint)
        top.addWidget(chat, 1)
        self.chat_card = chat
        self.column.addLayout(top)

        self.add_card, layout = card(text("players.add_title"))
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setObjectName("addName")
        self.name.setAccessibleName(text("players.add_name"))
        self.name.setPlaceholderText(text("players.add_name"))
        row.addWidget(self.name, 1)
        self.add_buttons = QHBoxLayout()
        row.addLayout(self.add_buttons)
        layout.addLayout(row)
        self.add_problem = label(kind="problem")
        self.add_problem.hide()
        layout.addWidget(self.add_problem)
        layout.addWidget(label(text("players.add_hint"), "sub"))
        self.column.addWidget(self.add_card)

        everyone, layout = card(text("players.everyone"))
        self.everyone = QVBoxLayout()
        layout.addLayout(self.everyone)
        self.column.addWidget(everyone)

        lists = QHBoxLayout()
        lists.setSpacing(12)
        self.list_cards: dict[str, tuple[QFrame, QVBoxLayout]] = {}
        for key in ("whitelist", "ops", "banned"):
            frame, layout = card("")
            rows = QVBoxLayout()
            layout.addLayout(rows)
            layout.addStretch()
            self.list_cards[key] = (frame, rows)
            lists.addWidget(frame, 1)
        self.column.addLayout(lists)
        self.lists_note = label(kind="sub")
        self.column.addWidget(self.lists_note)

        # Linked accounts (agent/discord/links.py): shown once there's one.
        self.links_card, layout = card(text("discord.links.title"))
        self.link_rows = QVBoxLayout()
        layout.addLayout(self.link_rows)
        self.links_card.hide()
        self.column.addWidget(self.links_card)
        # Whitelist requests, new addresses and the add-on note (agent/whitelist.py).
        self.help_card, layout = card(text("whitelist.title"))
        self.help_rows = QVBoxLayout()
        layout.addLayout(self.help_rows)
        self.help_card.hide()
        self.column.addWidget(self.help_card)
        self.column.addWidget(label(text("players.no_ip"), "sub"))
        self.column.addStretch()

    # ------------------------------------------------------------ words
    def words(self, key: str, **values) -> str:
        if self.data.get("edition") == "bedrock":
            key = ALLOWLIST.get(key, key)
        return text(key, **values)

    def may(self, permission: str) -> bool:
        return self.window_.may(self.row["id"], permission)

    # ------------------------------------------------------------ loading
    def load(self) -> None:
        self.ask("GET", "/players", self.show_players)
        if self.may("console.read"):
            self.ask("GET", "/chat?lines=200", self.show_chat, lambda _e: None)
        self.chat_card.setVisible(self.may("console.read"))
        if self.may("players.manage"):
            self.ask("GET", "/discord-links", self.show_links, lambda _e: None)
            self.ask("GET", "/whitelist", self.show_help, lambda _e: None)
        else:
            self.links_card.hide()
            self.help_card.hide()

    def show_links(self, data: dict) -> None:
        rows: list[QWidget] = []
        for asked in data.get("requests") or []:
            rows.append(self.link_request(asked, data.get("players") or []))
        for linked in data.get("links") or []:
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 0, 0, 0)
            words = text("discord.links.row", name=linked["name"], member=linked["discord_name"])
            if "level" in linked:
                words += "  ·  " + text(
                    "discord.links.level", level=linked["level"], xp=linked["xp"]
                )
            if mode["technical"]:
                words += f"  ({linked['uuid']})"
            h.addWidget(label(words), 1)
            remove = push(text("discord.links.remove"), "plain")
            remove.clicked.connect(
                lambda _=False, u=linked["uuid"]: self.ask(
                    "DELETE", f"/discord-links/{u}", self.show_links
                )
            )
            h.addWidget(remove)
            rows.append(line)
        refill(self.link_rows, rows)
        self.links_card.setVisible(bool(rows))

    def show_help(self, data: dict) -> None:
        """Requests to join, addresses an IP whitelist add-on turned away,
        and what an offline-mode server uses for its whitelist."""
        title_of(self.help_card).setText(self.words("whitelist.title"))
        rows: list[QWidget] = []
        if data.get("online_mode") is False and data.get("suggested"):
            words = (
                text("whitelist.addon_used", addon=data["addon"])
                if data.get("addon")
                else text("whitelist.no_addon", addons=", ".join(data["suggested"]))
            )
            rows.append(label(words, "notice" if not data.get("addon") else "sub"))
        for form in data.get("forms") or []:
            rows.append(self.form_row(form))
        addresses = data.get("addresses") or []
        for address in addresses:
            rows.append(self.address_row(address))
        if addresses:
            rows.append(label(text("whitelist.addresses_hint", addon=data["ip_addon"]), "muted"))
        refill(self.help_rows, rows)
        self.help_card.setVisible(bool(rows))
        if data.get("action"):
            self.sent(data)

    def form_row(self, form: dict) -> QWidget:
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        words = text(
            "whitelist.asks", member=form["discord_name"], name=form["name"], why=form["why"]
        )
        h.addWidget(label(words), 1)
        for verdict, key, style in (
            ("approve", "discord.links.approve", "primary"),
            ("refuse", "discord.links.refuse", "plain"),
        ):
            button = push(text(key), style)
            button.setAccessibleName(f"{text(key)}: {form['name']}")
            button.clicked.connect(
                lambda _=False, v=verdict: self.ask(
                    "POST", f"/whitelist/forms/{form['id']}", self.show_help, body={"answer": v}
                )
            )
            h.addWidget(button)
        return line

    def address_row(self, address: dict) -> QWidget:
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        words = text(
            f"whitelist.address_{address['state']}",
            name=address["name"],
            masked=address["masked"],
            time=clock(address["seen_at"]),
        )
        h.addWidget(label(words), 1)
        path = f"/whitelist/addresses/{address['id']}"
        choices = [("allow", "whitelist.allow", "primary"), ("ignore", "whitelist.ignore", "plain")]
        if address["state"] != "new":
            choices = []
        for verdict, key, style in choices:
            button = push(text(key), style)
            button.setAccessibleName(f"{text(key)}: {address['name']} {address['masked']}")
            button.clicked.connect(
                lambda _=False, v=verdict: self.ask(
                    "POST", path, self.show_help, body={"answer": v}
                )
            )
            h.addWidget(button)
        remove = push(text("whitelist.remove"), "plain")
        remove.setAccessibleName(
            f"{text('whitelist.remove')}: {address['name']} {address['masked']}"
        )
        remove.clicked.connect(lambda: self.ask("DELETE", path, self.show_help))
        h.addWidget(remove)
        return line

    def link_request(self, asked: dict, players: list[dict]) -> QWidget:
        """A Bedrock player's link request: pick who they are, then approve."""
        line = QWidget()
        column = QVBoxLayout(line)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(
            label(
                text("discord.links.asks", member=asked["discord_name"], name=asked["name"]), "step"
            )
        )
        h = QHBoxLayout()
        who = QComboBox()
        who.setAccessibleName(text("discord.links.pick"))
        for p in players:
            who.addItem(p["name"], p["uuid"])
        if players:
            match = who.findText(asked["name"])
            who.setCurrentIndex(max(match, 0))
        h.addWidget(who, 1)
        approve = push(text("discord.links.approve"), "primary")
        approve.setEnabled(bool(players))

        def answer(verdict: str) -> None:
            self.ask(
                "POST",
                f"/discord-links/requests/{asked['id']}",
                self.show_links,
                body={"answer": verdict, "uuid": who.currentData()},
            )

        approve.clicked.connect(lambda: answer("approve"))
        refuse = push(text("discord.links.refuse"), "plain")
        refuse.clicked.connect(lambda: answer("refuse"))
        h.addWidget(approve)
        h.addWidget(refuse)
        column.addLayout(h)
        if not players:
            column.addWidget(label(text("discord.links.no_players"), "sub"))
        return line

    def show_players(self, data: dict) -> None:
        self.data = data
        running = bool(data.get("running"))
        self.notice.setVisible(not running and self.may("players.manage"))
        lists = data.get("lists") or {}
        self.sets = {
            key: {
                p["name"].lower()
                for p in (lists.get(key) or {}).get("players") or []
                if p.get("name")
            }
            for key in ("whitelist", "ops", "banned")
        }
        refill(self.action_rows, [self.action_line(a) for a in data.get("actions") or []])
        self.action_card.setVisible(bool(data.get("actions")))

        unknown = text("state.unknown")
        count, most = data.get("online_count"), data.get("max_players")
        # The count is None until the agent has read the list: Unknown, never
        # the length of a list that may be old.
        self.online_title.setText(
            text(
                "players.online",
                count=unknown if count is None else count,
                max=unknown if most is None else most,
            )
        )
        online = data.get("online") or []
        refill(
            self.online,
            [self.player_row(p["username"], self.online_info(p), True, p) for p in online]
            or [label(text("players.nobody"), "sub")],
        )
        names = {p["username"] for p in online}
        known = data.get("known") or []
        refill(
            self.everyone,
            [
                self.player_row(p["username"], self.known_info(p), p["username"] in names)
                for p in known
            ]
            or [label(text("players.none"), "sub"), label(text("players.none_hint"), "sub")],
        )
        self.show_add(running)
        self.show_lists(lists)
        self.chat_note.setVisible((self.row.get("capabilities") or {}).get("reads_chat") is False)
        self.show_chat_box()

    def online_info(self, p: dict) -> str:
        parts = [text("players.col_session") + " " + duration(p.get("session_seconds"))]
        if mode["technical"] and p.get("uuid"):
            parts.append(p["uuid"])
        return "  ·  ".join(parts)

    def known_info(self, p: dict) -> str:
        return "  ·  ".join(
            [
                f"{text('players.col_first')} {clock(p.get('first_seen')) or text('time.never')}",
                f"{text('players.col_last')} {clock(p.get('last_seen')) or text('time.never')}",
                f"{text('players.col_sessions')} {p.get('sessions', 0)}",
                f"{text('players.col_total')} {duration(p.get('total_seconds_live'))}",
            ]
        )

    def player_row(self, name: str, info: str, online: bool, player: dict | None = None) -> QFrame:
        row = QFrame()
        row.setProperty("kind", "row")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 8, 8, 8)
        words = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(label(name, "step"))
        lower = name.lower()
        tags = [
            ("players.bedrock", (player or {}).get("edition") == "bedrock"),
            ("players.tag_whitelisted", lower in self.sets["whitelist"]),
            ("players.tag_op", lower in self.sets["ops"]),
            ("players.tag_banned", lower in self.sets["banned"]),
        ]
        for key, shown in tags:
            if shown:
                chip = label(self.words(key), "chip")
                chip.setWordWrap(False)
                top.addWidget(chip)
        top.addStretch()
        words.addLayout(top)
        words.addWidget(label(info, "sub"))
        shown = self.stats.get((self.row["id"], name))
        if shown:
            words.addWidget(label(shown, "sub"))
        h.addLayout(words, 1)
        stats = push(text("players.stats"), "plain")
        stats.setAccessibleName(f"{text('players.stats')}: {name}")
        stats.clicked.connect(lambda _=False: self.toggle_stats(name))
        h.addWidget(stats)
        for button in self.buttons(name, online):
            h.addWidget(button)
        return row

    def toggle_stats(self, name: str) -> None:
        """Shows a player's stats under their row, or hides them again."""
        key = (self.row["id"], name)
        if self.stats.pop(key, None) is None:

            def show(found: dict) -> None:
                self.stats[key] = stats_words(found)
                self.show_players(self.data)

            self.ask("GET", f"/players/{quote(name)}/stats", show)
        else:
            self.show_players(self.data)

    def buttons(self, name: str, online: bool) -> list[QPushButton]:
        lower, sets = name.lower(), self.sets
        kinds = [
            "whitelist_remove" if lower in sets["whitelist"] else "whitelist_add",
            "deop" if lower in sets["ops"] else "op",
        ]
        if online:
            kinds.append("kick")
        if self.data.get("bans") is not False:  # Bedrock servers have no ban list
            kinds.append("pardon" if lower in sets["banned"] else "ban")
        return [b for b in (self.button(k, lambda n=name: n) for k in kinds) if b]

    def button(self, kind: str, name) -> QPushButton | None:
        """``name`` is called when it's clicked, so the add box reads its field then."""
        if not self.may("players.op" if kind in OPERATOR else "players.manage"):
            return None
        button = push(self.words(ACTION_WORDS[kind]))  # kick and ban ask first
        button.setEnabled(bool(self.data.get("running")))
        button.clicked.connect(lambda _=False: self.act(kind, name(), button))
        return button

    def show_add(self, running: bool) -> None:
        refill(self.add_buttons, [])
        kinds = ["whitelist_add", "op"] + (["ban"] if self.data.get("bans") is not False else [])
        for kind in kinds:
            button = self.button(kind, self.checked_name)
            if button:
                if kind == "whitelist_add":
                    button.setProperty("kind", "primary")
                self.add_buttons.addWidget(button)
        self.add_card.setVisible(self.add_buttons.count() > 0)
        bedrock = self.data.get("edition") == "bedrock"
        self.name.setMaxLength(20 if bedrock else 17)
        self.name.setPlaceholderText("Steve Gamer" if bedrock else "Alex")

    def checked_name(self) -> str:
        """The add box's name, or "" (with a note) when it isn't a valid name."""
        name = self.name.text().strip()
        bedrock = self.data.get("edition") == "bedrock"
        if not NAMES["bedrock" if bedrock else "java"].match(name):
            self.add_problem.setText(
                text("players.bad_gamertag" if bedrock else "players.bad_name")
            )
            self.add_problem.show()
            self.name.setFocus()
            return ""
        self.add_problem.hide()
        return name

    def show_lists(self, lists: dict) -> None:
        titles = {
            "whitelist": ("players.whitelist", "players.whitelist_empty", "whitelist_remove"),
            "ops": ("players.ops", "players.ops_empty", "deop"),
            "banned": ("players.banned", "players.banned_empty", "pardon"),
        }
        files = []
        for key, (frame, rows) in self.list_cards.items():
            title, empty, remove = titles[key]
            title_of(frame).setText(self.words(title))
            found = lists.get(key)
            frame.setVisible(found is not None)  # Bedrock has no ban list
            if found is None:
                continue
            if found.get("players") is None:
                refill(rows, [label(found.get("reason") or text("state.unknown"), "sub")])
                continue
            files.append(found.get("file") or "")
            shown: list[QWidget] = []
            for p in found["players"]:
                line = QWidget()
                h = QHBoxLayout(line)
                h.setContentsMargins(0, 0, 0, 0)
                if p.get("name"):
                    who = p["name"] + (f"  ({p['reason']})" if p.get("reason") else "")
                    h.addWidget(label(who), 1)
                    button = self.button(remove, lambda n=p["name"]: n)
                    if button:
                        h.addWidget(button)
                else:
                    # Bedrock names players by xuid only until they're seen joining.
                    unseen = label(p.get("uuid") or "", "address")
                    unseen.setToolTip(text("players.xuid_only"))
                    h.addWidget(unseen, 1)
                shown.append(line)
            refill(rows, shown or [label(self.words(empty), "sub")])
        note = self.words("players.whitelist_note")
        if mode["technical"] and files:
            note += "  " + text("players.from_file", file=", ".join(f for f in files if f))
        self.lists_note.setText(note)

    # ------------------------------------------------------------ actions
    def action_words(self, action: dict) -> str:
        words = (
            f"{self.words(ACTION_WORDS[action['kind']])} {action['name']}: "
            f"{text(STATE_WORDS.get(action.get('state', 'sent'), 'players.state_sent'))}"
        )
        if mode["technical"] and action.get("message"):
            words += f"  ({action['message']})"
        return words

    def action_line(self, action: dict) -> QLabel:
        line = label(self.action_words(action))
        self.lines[action["id"]] = line
        return line

    def act(self, kind: str, name: str, button: QPushButton) -> None:
        if not name:
            return
        reason = ""
        if kind in CONFIRM:
            title, body = CONFIRM[kind]
            answer = self.window_.ask(
                text(title, name=name),
                text(body, name=name),
                text(ACTION_WORDS[kind]),
                text("players.reason"),
            )
            if answer is None:
                return
            reason = answer.strip()
        button.setEnabled(False)
        button.setText(text("players.sending"))

        def failed(exc: Exception) -> None:
            button.setText(self.words(ACTION_WORDS[kind]))
            button.setEnabled(True)
            self.window_.page_problem(exc)

        self.ask(
            "POST",
            "/players/actions",
            self.sent,
            failed,
            body={
                "action": kind,
                "name": name,
                "reason": reason or None,
                "confirm": kind in CONFIRM,
            },
        )

    def sent(self, result: dict) -> None:
        action = result["action"]
        self.action_rows.insertWidget(0, self.action_line(action))
        self.action_card.show()
        self.follow(action["id"])

    def follow(self, action_id: str) -> None:
        """Ask once a second until the console confirms it (or 22 tries)."""
        timer = QTimer(self)
        timer.setInterval(self.POLL_MS)
        tries, sid = [0], self.row["id"]

        def check() -> None:
            tries[0] += 1
            if tries[0] > self.POLLS or self.row.get("id") != sid:
                timer.stop()  # gave up, or another server is open now
                return
            self.ask("GET", f"/players/actions/{action_id}", answered, lambda _e: timer.stop())

        def answered(result: dict) -> None:
            action = result["action"]
            line = self.lines.get(action_id)
            if isinstance(line, QLabel):
                line.setText(self.action_words(action))
            if action.get("state") != "sent":
                timer.stop()
                if action.get("state") == "done":
                    self.load()

        timer.timeout.connect(check)
        timer.start()

    # ------------------------------------------------------------ chat
    def show_chat(self, data: dict) -> None:
        self.chat.setPlainText("\n".join(self.chat_words(m) for m in data.get("messages") or []))
        if not data.get("messages"):
            self.chat.setPlaceholderText(f"{text('chat.empty')}. {text('chat.empty_hint')}")
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def chat_words(self, message: dict) -> str:
        who = text("chat.server") if message.get("kind") == "server" else message.get("name") or ""
        said = message.get("text", "")
        if message.get("kind") == "action":
            said = f"• {said}"
        return f"{clock(message.get('ts'))}  {who}: {said}"

    def live(self, event: dict) -> None:
        if event.get("type") == "chat" and self.may("console.read"):
            self.chat.appendPlainText(self.chat_words(event.get("data") or {}))

    def show_chat_box(self) -> None:
        running = self.row.get("state") == "ONLINE"
        allowed = self.may("chat.send")
        for widget in (self.message, self.send):
            widget.setEnabled(running and allowed)
            widget.setVisible(allowed)
        self.chat_hint.setText(text("chat.hint") if running else text("chat.offline"))
        self.chat_hint.setVisible(allowed)

    def send_chat(self) -> None:
        words = self.message.text().strip()
        if not words:
            return
        self.send.setEnabled(False)

        def done(_result) -> None:
            self.message.clear()

        self.ask(
            "POST",
            "/chat",
            done,
            body={"message": words},
            always=self.show_chat_box,
        )


def stats_words(found: dict) -> str:
    """Only the numbers the agent found, then where they came from."""
    values = found.get("values") or {}
    parts = [
        f"{text(f'players.stat.{key}')} "
        + (duration(values[key]) if key == "playtime" else f"{values[key]:,}")
        for key in ("playtime", "deaths", "mobs", "blocks", "advancements", "joins")
        if key in values
    ]
    parts += [f"{x['name']} {x['value']:,}" for x in found.get("other") or []]
    if found.get("source") == "console":
        return "  ·  ".join([*parts, text("players.stats_console")])
    if found.get("saved_at") is None:
        return text("players.stats_none")
    return "  ·  ".join([*parts, text("players.stats_saved", time=clock(found["saved_at"]))])
