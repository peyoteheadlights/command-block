"""The "Put it on Modrinth" card on the Mods page (agent/modrinthshare.py):
Sign in with Modrinth, upload the newest modpack as a draft, put own jars
up as their own mods, and the Modrinth Server Project helper.

Signing in: Modrinth's page opens in the browser and, once the person says
yes there, sends the browser back to this computer: http://127.0.0.1:<port>
/modrinth with a code. ``CodeCatcher`` is that address, open only while a
sign-in waits (at most 10 minutes). Qt note: QTcpServer is like Java's
ServerSocket, but it tells us about each new connection with a signal
instead of a blocking accept(), so no thread waits here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import QHBoxLayout, QInputDialog

from .overview import card
from .ui import label, push
from .words import text

if TYPE_CHECKING:
    from .mods import ModsPage, VanillaModsPage

WAIT_MS = 10 * 60 * 1000  # the sign-in service's own limit
PAGE = (
    "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
    "Content-Security-Policy: default-src 'none'\r\nConnection: close\r\n\r\n"
    "<!doctype html><title>Command Block</title><p>{words}</p>"
)
NOT_FOUND = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


class CodeCatcher:
    """Waits on 127.0.0.1 for the browser coming back from Modrinth, once."""

    def __init__(self, got) -> None:
        self.got = got  # called with {"code", "state"} or {"error"}
        self.server = QTcpServer()
        self.server.newConnection.connect(self._connection)
        self.server.listen(QHostAddress.SpecialAddress.LocalHost, 0)
        self.port = self.server.serverPort()
        self.timer = QTimer(singleShot=True, interval=WAIT_MS)
        self.timer.timeout.connect(self.stop)
        self.timer.start()

    def _connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: self._read(s))

    def _read(self, socket) -> None:
        if not socket.canReadLine():
            return
        parts = bytes(socket.readLine()).decode("latin-1").split()
        url = urlparse(parts[1] if len(parts) == 3 and parts[0] == "GET" else "")
        if url.path != "/modrinth":
            socket.write(NOT_FOUND.encode())
            socket.disconnectFromHost()
            return
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        words = text("modrinth.browser_done" if query.get("code") else "modrinth.browser_failed")
        socket.write(PAGE.format(words=words).encode())
        socket.disconnectFromHost()
        self.stop()
        self.got(query)

    def stop(self) -> None:
        self.timer.stop()
        self.server.close()


class ModrinthCard:
    """Shown on Java servers to accounts that may manage mods. Signing in
    and out is the owner's (PC settings)."""

    def __init__(self, page: ModsPage | VanillaModsPage) -> None:
        self.page = page
        self.data: dict = {}
        self.catcher: CodeCatcher | None = None
        self.frame, layout = card(text("modrinth.title"))
        layout.addWidget(label(text("modrinth.intro"), "sub"))
        self.account = label()
        layout.addWidget(self.account)
        self.sign_in = push(text("modrinth.sign_in"), "primary")
        self.sign_in.clicked.connect(self.start_sign_in)
        self.sign_out = push(text("modrinth.sign_out"))
        self.sign_out.clicked.connect(
            lambda: self.call("POST", "/modrinth/sign-out", lambda _r: self.load())
        )
        line = QHBoxLayout()
        for button in (self.sign_in, self.sign_out):
            line.addWidget(button)
        line.addStretch()
        layout.addLayout(line)

        self.words = label()
        layout.addWidget(self.words)
        self.upload = push(text("modrinth.upload"))
        self.upload.clicked.connect(self.upload_pack)
        self.own_jar = push(text("modrinth.own_jar"))
        self.own_jar.clicked.connect(self.upload_jar)
        line = QHBoxLayout()
        for button in (self.upload, self.own_jar):
            line.addWidget(button)
        line.addStretch()
        layout.addLayout(line)
        layout.addWidget(label(text("modrinth.review_note"), "sub"))

        layout.addWidget(label(text("modrinth.server_title"), name="h2"))
        layout.addWidget(label(text("modrinth.server_intro"), "sub"))
        self.server_words = label()
        layout.addWidget(self.server_words)
        self.how = push(text("modrinth.server_how"))
        self.how.clicked.connect(self.show_how)
        self.paste = push(text("modrinth.server_paste"))
        self.paste.clicked.connect(self.paste_link)
        line = QHBoxLayout()
        for button in (self.how, self.paste):
            line.addWidget(button)
        line.addStretch()
        layout.addLayout(line)
        self.frame.setVisible(False)

    def show(self, row: dict) -> None:
        java = row.get("edition") != "bedrock" and self.page.may("mods.manage")
        self.frame.setVisible(java)
        if java:
            self.load()

    def load(self) -> None:
        self.page.ask("GET", "/modrinth", self.fill, lambda _e: None)

    def call(self, method: str, path: str, done, body: dict | None = None) -> None:
        """A PC-wide call (the sign-in isn't per server)."""
        agent = self.page.window_.agent
        self.page.run_here(lambda: agent.call(method, path, body), done, self.failed)

    def fill(self, data: dict) -> None:
        self.data = data
        account, owner = data["account"], self.page.may("pc.settings")
        if not account.get("available"):
            words = text("modrinth.not_available")
        elif account.get("signed_in"):
            words = text("modrinth.signed_in", name=account.get("username") or "?")
        else:
            words = text("modrinth.ran_out" if account.get("ran_out") else "modrinth.signed_out")
        if not owner:
            words += "\n" + text("modrinth.owner_only")
        self.account.setText(words)
        signed_in = bool(account.get("signed_in"))
        self.sign_in.setVisible(owner and bool(account.get("available")) and not signed_in)
        self.sign_out.setVisible(owner and signed_in)

        lines = []
        project, made = data.get("project"), data.get("made")
        if project:
            uploads = data.get("uploads") or []
            newest = uploads[0]["version"] if uploads else "-"
            lines.append(text("modrinth.project", url=project["url"], version=newest))
        if not made:
            lines.append(text("modrinth.make_first"))
        for jar in data.get("own_jars") or []:
            if jar.get("url"):
                lines.append(text("modrinth.jar_up", name=jar["name"], url=jar["url"]))
        self.words.setText("\n".join(lines))
        self.upload.setEnabled(signed_in and bool(made))
        if made:
            self.upload.setText(
                text("modrinth.upload_named", name=made["name"], version=made["version"])
            )
        waiting = [j for j in data.get("own_jars") or [] if not j.get("url")]
        self.own_jar.setVisible(bool(waiting))
        self.own_jar.setEnabled(signed_in)

        server, older = data.get("server_project"), data.get("older")
        if not data.get("address"):
            server_lines = [text("modrinth.server_no_address")]
        elif server:
            server_lines = [text("modrinth.server_listed", url=server["url"])]
        else:
            server_lines = []
        if older:
            server_lines.append(text("modrinth.server_older", **older))
        self.server_words.setText("\n".join(server_lines))
        self.how.setVisible(bool(data.get("address")))
        self.paste.setVisible(bool(data.get("address")))

    def failed(self, exc: Exception) -> None:
        self.page.said(exc)
        self.load()

    # ------------------------------------------------------------ signing in
    def start_sign_in(self) -> None:
        if self.catcher:
            self.catcher.stop()
        self.catcher = CodeCatcher(self.came_back)
        body = {"return_to": f"pc{self.catcher.port}"}

        def opened(started: dict) -> None:
            self.page.window_.pc.open_link(started["url"])
            self.page.say(text("modrinth.waiting"))

        self.call("POST", "/modrinth/sign-in", opened, body)

    def came_back(self, query: dict) -> None:
        self.catcher = None
        if not query.get("code"):
            self.page.say(text("modrinth.said_no"))
            return
        body = {"code": query["code"], "state": query.get("state", "")}

        def done(_account: dict) -> None:
            self.page.say(text("modrinth.welcome"))
            self.load()

        self.call("POST", "/modrinth/sign-in/finish", done, body)

    # ------------------------------------------------------------ uploading
    def ask_project(self, title: str, default: str) -> dict | None:
        """The Modrinth address and one-line summary of a new project."""
        slug, ok = QInputDialog.getText(
            self.page, title, text("modrinth.slug_prompt"), text=default
        )
        if not ok:
            return None
        summary, ok = QInputDialog.getText(self.page, title, text("modrinth.summary_prompt"))
        if not ok:
            return None
        return {"slug": slug.strip(), "summary": summary.strip()}

    def uploaded(self, data: dict) -> None:
        self.page.say(text("modrinth.uploaded"))
        self.fill(data)

    def upload_pack(self) -> None:
        body: dict | None = {}
        if not self.data.get("project"):
            name = (self.data.get("made") or {}).get("name", "")
            body = self.ask_project(text("modrinth.title"), _slug(name))
            if body is None:
                return
        self.page.say(text("modrinth.uploading"))
        self.page.ask("POST", "/modrinth/upload", self.uploaded, self.failed, body=body, slow=True)

    def upload_jar(self) -> None:
        waiting = [j for j in self.data.get("own_jars") or [] if not j.get("url")]
        names = [j["name"] for j in waiting]
        name, ok = QInputDialog.getItem(
            self.page, text("modrinth.own_jar"), text("modrinth.own_jar_pick"), names, 0, False
        )
        if not ok:
            return
        jar = waiting[names.index(name)]
        body = self.ask_project(jar["name"], _slug(jar["name"]))
        if body is None:
            return
        self.page.say(text("modrinth.uploading"))
        body["path"] = jar["path"]
        self.page.ask("POST", "/modrinth/own-jar", self.uploaded, self.failed, body=body, slow=True)

    # ------------------------------------------------------------ the Server Project
    def show_how(self) -> None:
        project = self.data.get("project") or {}
        uploads = self.data.get("uploads") or []
        steps = text(
            "modrinth.server_steps",
            address=self.data.get("address") or "",
            project=project.get("title") or text("modrinth.your_modpack"),
            version=uploads[0]["version"] if uploads else "-",
        )
        self.page.window_.confirm(text("modrinth.server_title"), steps, text("action.close"))

    def paste_link(self) -> None:
        link, ok = QInputDialog.getText(
            self.page, text("modrinth.server_title"), text("modrinth.server_paste_prompt")
        )
        if ok:
            self.page.ask(
                "POST",
                "/modrinth/server-project",
                self.fill,
                self.failed,
                body={"link": link.strip()},
            )


def _slug(name: str) -> str:
    """A first try at the Modrinth address, which the person can change."""
    out = "".join(c if c.isascii() and c.isalnum() else "-" for c in name.lower())
    return "-".join(part for part in out.split("-") if part)[:64]
