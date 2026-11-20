"""Plain Bedrock through playit.gg: WaterdogPE in front of the server.

playit's guide (support-docs, "Setting up a Minecraft Bedrock server with
playit") says a Bedrock Dedicated Server can't reach a playit "Minecraft
Bedrock (NetherNet)" tunnel by itself. WaterdogPE (GPL-2.0,
github.com/WaterdogPE/WaterdogPE), a Bedrock proxy, talks to playit's
NetherNet signaling and passes players on to the server, which moves to
another port (19142 in playit's settings). WaterdogPE checks every player's
Xbox sign-in, so the server itself runs with online-mode and its allow list
off (playit's guide: it won't start otherwise).

What the owner does, once: makes the NetherNet tunnel on playit.gg, opens its
"Configuration help", WaterdogPE tab, and pastes the config.yml box here. That
box holds the tunnel's private token, which only playit's page shows. It goes
into WaterdogPE's own config.yml in the agent's data folder and nowhere else
(never logged, never sent back by the API).

House rule 2: WaterdogPE is downloaded and run only after the owner's click
("Use playit.gg for this Bedrock server"); then it runs with the server, as
the same Windows user, and stops with it. Its NetherNet support is only in
WaterdogPE's rolling "latest" build, so that build is downloaded, checked
against the checksum GitHub lists for it.

The allow list (Mark's pick, 2026-11-20): the server can't keep it any more,
so the Companion's WaterdogPE plugin does (companion/waterdog in the repo,
released with the Companion). When the server's allow list was on, the
plugin turns away anyone not on its allowlist.json right after WaterdogPE
signed them in, before they reach the server. The agent tells it which file
in ``plugins/commandblockcompanion/config.json`` on every start.

WaterdogPE's own anonymous error reports and bStats statistics are turned
off (PRIVACY.md: Command Block sends nothing about the owner's server).
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from . import downloads, serveruser
from .events import Event
from .minecraft import properties

if TYPE_CHECKING:
    from .core import AgentCore, ServerContext

SETTING = "waterdog"  # {server_id: {"listen", "media", "server_port", "list_on", "before"}}
REPO = "WaterdogPE/WaterdogPE"
TAG = "latest"  # the rolling build; the numbered releases have no NetherNet
JAR = "Waterdog.jar"
MIN_JAVA = 17  # playit's guide
MAX_BOX = 64 * 1024
REGISTERED = "Registered with the external signaling service"
# The Companion plugin's last line when it loads (AllowList.READY). WaterdogPE
# loads plugins before it registers with playit, so this always comes first.
# A plugin that failed to load only gets a line in WaterdogPE's log, and
# WaterdogPE carries on: without this line it is stopped (never open by mistake).
PLUGIN_READY = "Command Block allow list ready"
# The Companion's release that carries the WaterdogPE plugin (companion-release.yml).
COMPANION_VERSION = "0.1.0"
COMPANION_TAG = f"companion-v{COMPANION_VERSION}"
PLUGIN_JAR = f"commandblock-companion-waterdog-{COMPANION_VERSION}.jar"
# WaterdogPE makes a plugin's folder from its name in lower case.
PLUGIN_CONFIG = Path("plugins") / "commandblockcompanion" / "config.json"
PLUGIN_FORMAT = 1
# Always set, whatever the box says: nothing about the owner's server leaves
# the PC, every player's Xbox sign-in is checked and encrypted, and names keep
# their spaces (the plugin matches them against allowlist.json).
FORCED = {
    "enable_statistics": False,
    "enable_error_reporting": False,
    "online_mode": True,
    "upstream_encryption": True,
    "replace_username_spaces": False,
}
STOP_WAIT = 15.0
# What playit's guide says the server needs behind WaterdogPE, plus LAN
# visibility off: with online-mode off, the server must not offer itself to
# the home network without WaterdogPE's sign-in check.
# Bedrock's own defaults, for a key server.properties didn't have before.
MISSING = {
    "online-mode": "true",
    "allow-list": "false",
    "enable-lan-visibility": "true",
    "server-portv6": "19133",
}
SERVER_KEYS = ("server-port", "server-portv6", "online-mode", "allow-list", "enable-lan-visibility")


def server_values(port: int) -> dict[str, str]:
    """What server.properties must say behind WaterdogPE (SERVER_KEYS)."""
    return {
        "server-port": str(port),
        "server-portv6": str(port + 1),
        "online-mode": "false",
        "allow-list": "false",
        "enable-lan-visibility": "false",
    }


class WaterdogError(RuntimeError):
    """Said to the owner as it is."""


# ---------------------------------------------------------------- playit's box
def _port_of(address: Any) -> int | None:
    try:
        port = int(str(address).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def read_box(text: str) -> dict[str, Any]:
    """playit's config.yml box -> {"config", "listen", "media", "server_port"}.
    Raises WaterdogError, in words, when it isn't that box."""
    if not text or not text.strip():
        raise WaterdogError("Paste the config.yml box from playit's Configuration help.")
    if len(text) > MAX_BOX:
        raise WaterdogError("That's far longer than playit's config.yml box.")
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WaterdogError(
            f"That isn't playit's config.yml box (it can't be read: {exc})."
        ) from exc
    if not isinstance(config, dict) or not isinstance(config.get("nethernet"), dict):
        raise WaterdogError(
            "That isn't the WaterdogPE config.yml box: it has no nethernet section. "
            "Copy it from the WaterdogPE tab of the tunnel's Configuration help."
        )
    listener = config.get("listener")
    servers = config.get("servers")
    if not isinstance(listener, dict) or not isinstance(servers, dict) or len(servers) != 1:
        raise WaterdogError("The box needs its listener and servers sections, with one server.")
    listen = _port_of(listener.get("host"))
    target = next(iter(servers.values()))
    address = str(target.get("address") or "") if isinstance(target, dict) else ""
    host = address.rsplit(":", 1)[0]
    server_port = _port_of(address)
    if listen is None or server_port is None:
        raise WaterdogError("The box's listener host and server address need a port each.")
    if host not in ("127.0.0.1", "localhost"):
        raise WaterdogError(
            f"The box sends players to {host}, not to this PC (127.0.0.1). "
            "Copy the box again from playit."
        )
    if listen in (server_port, server_port + 1):
        raise WaterdogError("The box uses the same port for WaterdogPE and the server.")
    return {
        "config": config,
        "listen": listen,
        "media": media_port(config, listen),
        "server_port": server_port,
    }


def media_port(config: dict[str, Any], listen: int) -> int:
    """NetherNet's UDP port, worked out the way WaterdogPE does
    (NetherNetSettings.resolveWebrtcPort): its own setting, or the listener's
    port when RakNet is off, else the next one no server in the box uses."""
    nethernet = config.get("nethernet") or {}
    try:
        own = int(nethernet.get("webrtc_port") or 0)
    except (TypeError, ValueError):
        own = 0
    if own > 0:
        return own
    transport = str((config.get("listener") or {}).get("transport") or "both").lower()
    if transport == "nethernet":
        return listen
    taken = {
        _port_of(s.get("address"))
        for s in (config.get("servers") or {}).values()
        if isinstance(s, dict)
    }
    port = listen + 1
    while port in taken:
        port += 1
    return port


# ---------------------------------------------------------------- one PC's WaterdogPEs
class Waterdog:
    """WaterdogPE for each Bedrock server that uses it (one at a time per port)."""

    def __init__(self, core: AgentCore) -> None:
        self.core = core
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.state: dict[str, str] = {}  # server id -> starting, registered, stopped
        self.tails: dict[str, collections.deque[str]] = {}
        self._readers: dict[str, asyncio.Task] = {}

    def saved(self) -> dict[str, Any]:
        return self.core.db.get_setting(SETTING, {}) or {}

    def of(self, ctx: ServerContext) -> dict[str, Any] | None:
        return self.saved().get(ctx.server_id)

    def _put(self, ctx: ServerContext, entry: dict[str, Any] | None) -> None:
        found = self.saved()
        if entry is None:
            found.pop(ctx.server_id, None)
        else:
            found[ctx.server_id] = entry
        self.core.db.set_setting(SETTING, found)

    def folder(self, ctx: ServerContext) -> Path:
        return Path(self.core.config.data_dir) / "waterdog" / ctx.server_id

    def status(self, ctx: ServerContext) -> dict[str, Any] | None:
        """For the playit part of the Server address card; never the token."""
        entry = self.of(ctx)
        if entry is None:
            return None
        running = ctx.server.running
        state = self.state.get(ctx.server_id) if running else "server_off"
        return {
            "state": state or "stopped",
            "listen_port": entry["listen"],
            "media_port": entry["media"],
            "server_port": entry["server_port"],
            "list_on": bool(entry.get("list_on")),
        }

    # ------------------------------------------------------------ the owner's buttons
    async def use(self, ctx: ServerContext, box: str, user: str) -> dict[str, Any]:
        """Download WaterdogPE, write its config from playit's box and move the
        server to the box's port. The server must be stopped."""
        from . import gamesettings

        if ctx.config.server_type.edition != "bedrock":
            raise WaterdogError("WaterdogPE is only for Bedrock servers.")
        # A second click (two apps open, a slow network) would read the moved
        # server.properties and lose the allow list and the old port.
        if self.of(ctx) is not None:
            raise WaterdogError(
                "WaterdogPE is already set up. Stop using it first to paste a new box."
            )
        if ctx.server.running:
            raise WaterdogError("Stop the server first: its port changes.")
        found = read_box(box)
        await asyncio.to_thread(check_java, ctx.config.server.java or "java")
        self._check_ports(ctx, found)
        path = ctx.config.server_dir / properties.FILENAME
        if not path.is_file():
            raise WaterdogError("server.properties isn't there yet. Start the server once.")
        folder = self.folder(ctx)
        await download(folder, REPO, TAG, JAR, JAR)
        await download(folder / "plugins", downloads.REPO, COMPANION_TAG, PLUGIN_JAR, PLUGIN_JAR)
        await asyncio.to_thread(_write_config, folder, {**found["config"], **FORCED})

        edited = properties.PropertiesFile.read(path)
        before = {key: edited.get(key) for key in (*SERVER_KEYS, "server-udp-ports")}
        port = found["server_port"]
        wanted = server_values(port)
        # server-udp-ports set to the game port's number (11c part 0b) moves with it.
        old_port = (before.get("server-port") or "").strip()
        if old_port and (before.get("server-udp-ports") or "").strip() == old_port:
            wanted["server-udp-ports"] = str(port)
        changed = {key: value for key, value in wanted.items() if edited.set(key, value)}
        if changed:
            await gamesettings.write_properties(
                ctx, edited, changed, sorted(changed), user, "Moving the server behind WaterdogPE"
            )
        self._put(
            ctx,
            {
                "listen": found["listen"],
                "media": found["media"],
                "server_port": port,
                "list_on": (before.get("allow-list") or "").strip().lower() == "true",
                # Only what we changed, to put back on Stop.
                "before": {key: before.get(key) for key in changed},
                "set": changed,
            },
        )
        internet_off = False
        if ctx.config.server.internet:
            # The router must not open the server's port: it lets anyone in
            # without a sign-in now. playit does the job instead.
            ctx.config.set("server.internet", False)
            ctx.config.save()
            internet_off = True
        return {"internet_off": internet_off}

    def _check_ports(self, ctx: ServerContext, found: dict[str, Any]) -> None:
        from .gamesettings import port_problem
        from .ports import is_free

        current = self.core.ports.port_of(ctx).port
        problem = (
            port_problem(ctx, found["server_port"]) if found["server_port"] != current else None
        )
        if problem:
            raise WaterdogError(problem)
        used = self.core.ports.used(exclude=ctx.server_id)
        mine = {current, current + 1}  # the server's own ports, free once it moves
        for port in {found["listen"], found["media"]}:
            owner = used.get(("udp", port)) or used.get(("tcp", port))
            if owner:
                raise WaterdogError(
                    f"The server '{self.core.servers[owner].name}' already uses port {port}, "
                    "which WaterdogPE needs."
                )
            if port not in mine and not is_free(port, "udp"):
                raise WaterdogError(f"Another program on this PC is using port {port}.")

    async def stop_using(self, ctx: ServerContext, user: str) -> None:
        """WaterdogPE stops and its folder (with playit's token) goes; the
        server gets back the settings we changed, unless someone changed them since."""
        from . import gamesettings

        entry = self.of(ctx)
        if entry is None:
            return
        if ctx.server.running:
            raise WaterdogError("Stop the server first: its port changes back.")
        await self.stop(ctx)
        path = ctx.config.server_dir / properties.FILENAME
        if path.is_file():
            current = properties.PropertiesFile.read(path)
            before, made = entry.get("before") or {}, entry.get("set") or {}
            back = {
                key: old if old is not None else MISSING[key]
                for key, old in before.items()
                # Changed by someone since: theirs now. A key the file didn't
                # have gets Bedrock's own default.
                if current.get(key) == made.get(key) and (old is not None or key in MISSING)
            }
            # Never left without the Xbox sign-in check, nor its allow list.
            back["online-mode"] = before.get("online-mode") or MISSING["online-mode"]
            back["allow-list"] = "true" if entry.get("list_on") else back.get("allow-list", "false")
            if back:
                await gamesettings.set_values(
                    ctx, back, "Taking the server out of WaterdogPE", user
                )
        await asyncio.to_thread(shutil.rmtree, self.folder(ctx), True)
        self._put(ctx, None)

    def list_path(self, ctx: ServerContext) -> Path | None:
        """The allowlist.json the plugin keeps, or None when everyone may join."""
        entry = self.of(ctx) or {}
        return ctx.config.server_dir / "allowlist.json" if entry.get("list_on") else None

    async def set_list(self, ctx: ServerContext, on: bool) -> bool:
        """Turn the allow list on or off behind WaterdogPE: the plugin keeps
        it, the server's own stays off. The plugin reads its settings for
        every join, so it counts at once. False when WaterdogPE isn't used."""
        entry = self.of(ctx)
        if entry is None:
            return False
        self._put(ctx, {**entry, "list_on": on})
        if ctx.server_id in self.processes:
            try:
                await asyncio.to_thread(_write_plugin_config, self.folder(ctx), self.list_path(ctx))
            except OSError as exc:  # the old settings would stay: never open by mistake
                await self.stop(ctx)
                await self._said(
                    ctx,
                    f"The allow list add-on's settings couldn't be written ({exc}), so "
                    "WaterdogPE was stopped. Restart the server to try again.",
                )
        return True

    # ------------------------------------------------------------ with the server
    def before_launch(self, ctx: ServerContext) -> str | None:
        """Before every start: server.properties says what WaterdogPE needs
        again (an Undo or a hand edit may have changed it): the box's port,
        LAN visibility, allow list and online mode off. An allow list turned
        on since becomes the plugin's to keep. Returns a line for the console."""
        entry = self.of(ctx)
        path = ctx.config.server_dir / properties.FILENAME
        if entry is None or not path.is_file():
            return None
        edited = properties.PropertiesFile.read(path)
        if (edited.get("allow-list") or "").strip().lower() == "true":
            self._put(ctx, {**entry, "list_on": True})
        wanted = server_values(entry["server_port"])
        changed = [f"{key}={value}" for key, value in wanted.items() if edited.set(key, value)]
        if not changed:
            return None
        edited.write(path)
        return (
            f"[agent] set {', '.join(changed)}: players come in through WaterdogPE, which "
            "checks their Xbox sign-in, and the agent keeps the allow list. "
            "Stop using WaterdogPE to change these."
        )

    async def start(self, ctx: ServerContext) -> None:
        """On server_started: run WaterdogPE next to it."""
        if self.of(ctx) is None or ctx.server_id in self.processes:
            return
        from .winproc import NO_WINDOW

        folder = self.folder(ctx)
        if not (folder / JAR).is_file() or not (folder / "plugins" / PLUGIN_JAR).is_file():
            await self._said(
                ctx, "WaterdogPE isn't downloaded. Use playit.gg for this server again."
            )
            return
        try:
            await asyncio.to_thread(_write_plugin_config, folder, self.list_path(ctx))
        except OSError as exc:  # an older config.json would stay: never open by mistake
            await self._said(
                ctx,
                f"The allow list add-on's settings couldn't be written ({exc}), so WaterdogPE "
                "wasn't started and friends can't join through playit.gg.",
            )
            return
        java = ctx.config.server.java or "java"
        argv = [java, "-Xms256M", "-Xmx1G", "-jar", JAR]
        self.state[ctx.server_id] = "starting"
        self.tails[ctx.server_id] = collections.deque(maxlen=200)
        try:
            process = await serveruser.start(
                ctx.config,
                argv,
                cwd=folder,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=NO_WINDOW,
            )
        except (
            OSError,
            ValueError,
            serveruser.ServerUserMissing,
            serveruser.FolderRightsFailed,
        ) as exc:
            self.state[ctx.server_id] = "stopped"
            await self._said(ctx, f"WaterdogPE didn't start: {exc}")
            return
        self.processes[ctx.server_id] = process
        self._readers[ctx.server_id] = asyncio.create_task(
            self._read(ctx, process), name=f"waterdog-{ctx.server_id}"
        )

    async def _read(self, ctx: ServerContext, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        tail = self.tails[ctx.server_id]
        ready = False
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            tail.append(line)
            if PLUGIN_READY in line:
                ready = True
            elif REGISTERED in line and not ready:
                # The allow list plugin didn't load: players would reach the
                # server with no allow list. Stopped before anyone can join.
                if self.processes.get(ctx.server_id) is process:
                    self.processes.pop(ctx.server_id, None)
                self.state[ctx.server_id] = "stopped"
                if process.returncode is None:
                    process.kill()
                await process.wait()
                await self._said(
                    ctx,
                    "The allow list add-on didn't load, so WaterdogPE was stopped and friends "
                    "can't join through playit.gg. Update Command Block, then Stop using "
                    "WaterdogPE and set it up again.",
                )
                return
            elif REGISTERED in line:
                self.state[ctx.server_id] = "registered"
        await process.wait()
        if self.processes.get(ctx.server_id) is process:  # not our stop
            self.processes.pop(ctx.server_id, None)
            self.state[ctx.server_id] = "stopped"
            await self._said(
                ctx,
                f"WaterdogPE stopped (exit code {process.returncode}), so friends can't join "
                "through playit.gg. Restart the server to start it again.",
            )

    async def stop(self, ctx: ServerContext) -> None:
        """On server_stopped / crashed: WaterdogPE's own "end", then a kill."""
        process = self.processes.pop(ctx.server_id, None)
        self.state[ctx.server_id] = "stopped"
        if process is None or process.returncode is not None:
            return
        try:
            assert process.stdin is not None
            process.stdin.write(b"end\n")
            await process.stdin.drain()
            await asyncio.wait_for(process.wait(), STOP_WAIT)
        except (OSError, TimeoutError, AssertionError):
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _said(self, ctx: ServerContext, words: str) -> None:
        await ctx.bus.publish(Event(type="waterdog_stopped", level="error", message=words))

    async def shutdown(self) -> None:
        for server_id in list(self.processes):
            ctx = self.core.servers.get(server_id)
            if ctx:
                await self.stop(ctx)


# ---------------------------------------------------------------- files
def check_java(java: str) -> None:
    from .minecraft.java import detect_java

    found = detect_java(java)
    if (found.version_major or 0) < MIN_JAVA:
        have = f"Java {found.version_major}" if found.version_major else "no Java"
        raise WaterdogError(
            f"WaterdogPE needs Java {MIN_JAVA} or newer, and this PC has {have}. "
            "Install Java from adoptium.net (Temurin), then try again."
        )


async def download(folder: Path, repo: str, tag: str | None, filename: str, name: str) -> Path:
    """One file of a GitHub release (WaterdogPE's newest build, or the
    Companion's plugin), checked against the checksum GitHub lists for it.
    Refused when GitHub lists none (nothing unchecked is run)."""
    from .mods.github import GitHubReleases
    from .mods.sources import SourceError

    try:
        release = await GitHubReleases().release(repo, tag)
    except SourceError as exc:
        raise WaterdogError(f"{filename} couldn't be found on GitHub: {exc}") from exc
    file = next((f["file"] for f in release["files"] if f["file"]["filename"] == filename), None)
    if not file or not file.get("sha256"):
        raise WaterdogError(f"GitHub lists no checked {filename} in {repo}'s {tag} release.")
    spec = downloads.FileSpec(
        url=file["url"],
        name=name,
        sha256=file["sha256"],
        size=int(file["size"]) if file.get("size") else None,
        allow_unverified=False,
    )
    try:
        with downloads.allow_releases(repo):
            fetched = await downloads.download(spec, folder)
    except downloads.DownloadError as exc:
        raise WaterdogError(f"{filename} couldn't be downloaded: {exc}") from exc
    return fetched.path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.part")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _write_config(folder: Path, config: dict[str, Any]) -> None:
    """WaterdogPE fills in every setting the box leaves out on its first start."""
    _write(folder / "config.yml", yaml.safe_dump(config, sort_keys=False))


def _write_plugin_config(folder: Path, allow_list: Path | None) -> None:
    """The Companion plugin's settings: which allowlist.json to keep, or none."""
    body = {"format": PLUGIN_FORMAT, "allow_list": str(allow_list) if allow_list else None}
    _write(folder / PLUGIN_CONFIG, json.dumps(body))
