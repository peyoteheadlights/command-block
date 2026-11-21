"""Sign in with Modrinth, and put a server's modpack (and own jars) on it.

Signing in: Modrinth only hands out a token to an app that shows its
client secret, and an app on people's PCs can't keep a secret. So our
second Firebase function (next to the CurseForge relay) does only that
step. The flow, like any "Sign in with ..." button:

  1. The app asks the agent to start. The agent asks the function, which
     answers with Modrinth's page to open and a signed ``state`` (only this
     install can finish it, and only within 10 minutes).
  2. The person says yes on Modrinth. Modrinth sends the browser to the
     function, which sends it on to the app: the desktop app's own address
     on that computer (127.0.0.1), or the phone app (mcsc://modrinth).
  3. The app hands the code to the agent, the agent checks the state is
     the one it started and asks the function to swap the code for a token.

The token is kept only on this PC, encrypted for the agent's Windows account
(DPAPI, ``modrinth.bin``), like the Microsoft sign-in. No API answer holds
it. It can only read who signed in, make projects and add versions.

Uploading: everything goes up as a draft. Nothing is public until the
owner submits it for review on Modrinth's own site.
"""

from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import downloads, modpackmake
from .modpack import INDEX, ModpackError
from .mods.curseforge import CurseForgeClient
from .mods.jarinfo import read_mod_jar
from .mods.sources import SourceError
from .security.paths import is_inside, safe_filename
from .security.records import read_secrets, write_secrets

if TYPE_CHECKING:
    from .core import ServerContext

log = logging.getLogger("msc.modrinthshare")

KEY_FILE = "modrinth.bin"
API = "https://api.modrinth.com"
SITE = "https://modrinth.com"
AUTHORIZE = f"{SITE}/auth/authorize?"
SETTING = "modrinth_upload"  # per server: the project, the uploads, the server project
SIGN_IN_TIME = 10 * 60.0  # the function's own limit
RETURN_TO = re.compile(r"^(pc[1-9]\d{0,4}|phone)$")
STATE_RE = re.compile(r"^[\w-]{22}\.\d{10}\.(pc\d{1,5}|phone)\.[\w-]{43}$")
CODE_RE = re.compile(r"^[\w-]{1,200}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
PROJECT_LINK = re.compile(r"^(?:(?:https://)?(?:www\.)?modrinth\.com/[a-z_]+/)?([\w-]{3,64})/?$")
KEEP_UPLOADS = 20
CHECK_EVERY = 10 * 60.0
# What Modrinth calls each loader in a modpack's dependencies.
LOADERS = {
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}
# A jar's Server / Players / Both mark, as Modrinth's two old side fields.
SIDES = {
    "both": ("required", "required"),
    "players": ("required", "unsupported"),
    "server": ("unsupported", "required"),
}
LICENSE = "LicenseRef-All-Rights-Reserved"  # the owner changes it on Modrinth before review


class ModrinthShareError(ModpackError):
    pass


# ---------------------------------------------------------------- the sign-in
class ModrinthAccount:
    """One Modrinth sign-in per PC (agent-wide, like the Microsoft one)."""

    def __init__(self, config) -> None:
        self.config = config
        self.path = Path(config.data_dir) / KEY_FILE
        self.pending: dict[str, float] = {}  # state -> when it was started

    def token(self) -> str | None:
        saved = read_secrets(self.path)
        try:
            if float(saved.get("expires_at") or 0) > time.time():
                return saved["token"] or None
        except (KeyError, ValueError):
            pass
        return None

    def status(self) -> dict[str, Any]:
        saved = read_secrets(self.path)
        signed_in = self.token() is not None
        return {
            "available": bool(self.config.mods.modrinth_sign_in),
            "signed_in": signed_in,
            "username": saved.get("username") if signed_in else None,
            # Modrinth's own words for a sign-in that ran out: sign in again.
            "ran_out": bool(saved) and not signed_in,
        }

    async def _function(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        """One call to our Sign in with Modrinth function, with this
        install's anonymous Firebase sign-in (the same one the CurseForge
        relay uses)."""
        base = self.config.mods.modrinth_sign_in
        if not base:
            raise ModrinthShareError("Sign in with Modrinth isn't set up in this copy of the app.")
        try:
            token = await CurseForgeClient(self.config)._token()
            answer = await downloads.fetch_json(
                base.rstrip("/") + path,
                method=method,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(body).encode() if body is not None else None,
            )
        except (SourceError, downloads.DownloadError) as exc:
            status = getattr(exc, "status", None)
            log.info("modrinth sign-in: %s", exc)
            if status == 503:
                raise ModrinthShareError("Sign in with Modrinth is turned off for now.") from exc
            if status in (400, 403):
                raise ModrinthShareError(
                    "Modrinth didn't take the sign-in. Sign in again."
                ) from exc
            raise ModrinthShareError(
                "Couldn't reach the sign-in service. Check this PC's internet and try again."
            ) from exc
        if not isinstance(answer, dict):
            raise ModrinthShareError("The sign-in service sent an answer that couldn't be read.")
        return answer

    async def start(self, return_to: str) -> dict[str, str]:
        """Modrinth's page to open. ``return_to``: ``pc<port>`` (the desktop
        app listens on 127.0.0.1:<port> of its own computer) or ``phone``."""
        port = return_to[2:] if return_to.startswith("pc") else "1"
        if not RETURN_TO.match(return_to or "") or int(port) > 65535:
            raise ModrinthShareError("That isn't a way back to the app.")
        got = await self._function("GET", f"/start?to={return_to}")
        url, state = str(got.get("url") or ""), str(got.get("state") or "")
        if not url.startswith(AUTHORIZE) or not STATE_RE.match(state):
            raise ModrinthShareError("The sign-in service sent an answer that couldn't be read.")
        now = time.time()
        self.pending = {s: t for s, t in self.pending.items() if now - t < SIGN_IN_TIME}
        self.pending[state] = now
        return {"url": url}

    async def finish(self, code: str, state: str) -> dict[str, Any]:
        """The code Modrinth gave the browser: swap it and keep the token."""
        started = self.pending.pop(state or "", None)
        if started is None or time.time() - started > SIGN_IN_TIME:
            raise ModrinthShareError(
                "This sign-in ran out or was started somewhere else. Sign in again."
            )
        if not CODE_RE.match(code or ""):
            raise ModrinthShareError("Modrinth didn't send a sign-in code. Sign in again.")
        got = await self._function("POST", "/swap", {"code": code, "state": state})
        token, lasts = str(got.get("access_token") or ""), got.get("expires_in")
        if not token or not isinstance(lasts, int) or lasts <= 0:
            raise ModrinthShareError("The sign-in service sent an answer that couldn't be read.")
        user = await _api("GET", "/v2/user", token)
        write_secrets(
            self.path,
            {
                "token": token,
                "username": str(user.get("username") or ""),
                "expires_at": str(time.time() + lasts),
            },
        )
        return self.status()

    def sign_out(self) -> None:
        self.pending.clear()
        self.path.unlink(missing_ok=True)


def _json_part(data: dict[str, Any]) -> tuple[None, str, str]:
    """The ``data`` part of Modrinth's multipart forms (a part, not a file)."""
    return (None, json.dumps(data), "application/json")


async def _api(method: str, path: str, token: str | None = None, files: dict | None = None) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = token
    try:
        return await downloads.fetch_json(
            API + path,
            method=method,
            headers=headers,
            files=files,
        )
    except downloads.DownloadError as exc:
        if getattr(exc, "status", None) == 401:
            raise ModrinthShareError(
                "Modrinth didn't accept the sign-in any more. Sign in with Modrinth again."
            ) from exc
        raise ModrinthShareError(f"Modrinth said no: {exc}") from exc


# ---------------------------------------------------------------- one server
def state(ctx: ServerContext) -> dict[str, Any]:
    """The "Put it on Modrinth" card."""
    saved = _saved(ctx)
    made = modpackmake.made(ctx)
    project = saved.get("project")
    server_project = saved.get("server_project")
    return {
        "account": ctx.core.modrinth.status(),
        "made": {"name": made["name"], "version": made["version"]} if made else None,
        "project": {**project, "url": f"{SITE}/modpack/{project['slug']}"} if project else None,
        "uploads": saved.get("uploads") or [],
        "own_jars": [
            {**jar, "url": f"{SITE}/mod/{jar['slug']}" if jar.get("slug") else None}
            for jar in _own_jars(ctx, saved)
        ],
        "server_project": server_project,
        "older": older(ctx),
        "address": _address(ctx),
    }


def _saved(ctx: ServerContext) -> dict[str, Any]:
    saved = ctx.db.get_setting(SETTING)
    return saved if isinstance(saved, dict) else {}


def _own_jars(ctx: ServerContext, saved: dict[str, Any]) -> list[dict[str, Any]]:
    """The own jars in the newest modpack, and the ones already put up."""
    put_up = saved.get("own_jars") or {}
    made = modpackmake.made(ctx) or {}
    return [
        {
            "path": jar["path"],
            "name": jar["name"],
            "side": jar.get("side", "both"),
            "slug": put_up.get(jar["path"]),
        }
        for jar in made.get("own_jars") or []
    ]


def _address(ctx: ServerContext) -> str | None:
    """The address the owner typed for friends (Discord settings, Address to
    share). A Modrinth Server Project needs one anyone can reach."""
    from .discord import layout

    return layout.addresses(ctx.core.db).get(ctx.server_id) or None


def _token(ctx: ServerContext) -> str:
    token = ctx.core.modrinth.token()
    if not token:
        raise ModrinthShareError("Sign in with Modrinth first.")
    return token


def _pack_facts(path: Path) -> tuple[list[str], list[str]]:
    """(Minecraft versions, loaders) from the modpack's own index."""
    with zipfile.ZipFile(path) as zf:
        index = json.loads(zf.read(INDEX))
    deps = index.get("dependencies") or {}
    loaders = [LOADERS[k] for k in deps if k in LOADERS]
    if "minecraft" not in deps or not loaders:
        raise ModrinthShareError(
            "Modrinth only takes Fabric, Quilt, Forge and NeoForge modpacks, "
            "and this one names none of them."
        )
    return [str(deps["minecraft"])], loaders


async def _check_slug(slug: str) -> None:
    if not SLUG_RE.match(slug or ""):
        raise ModrinthShareError(
            "The Modrinth address is 3 to 64 small letters, numbers, - or _, like my-modpack."
        )
    try:
        await downloads.fetch_json(f"{API}/v2/project/{slug}/check")
    except downloads.DownloadNotFound:
        return  # free
    except downloads.DownloadError as exc:
        raise ModrinthShareError(f"Modrinth said no: {exc}") from exc
    raise ModrinthShareError(f"modrinth.com/…/{slug} is taken. Pick another address.")


def _summary_ok(summary: str) -> str:
    summary = (summary or "").strip()
    if not 3 <= len(summary) <= 256:
        raise ModrinthShareError("Write a one-line summary of 3 to 256 letters.")
    return summary


async def _new_project(
    token: str, kind: str, slug: str, title: str, summary: str, sides: tuple[str, str]
) -> str:
    await _check_slug(slug)
    data = {
        "project_type": kind,
        "slug": slug,
        "title": title[:64],
        "description": summary,
        "body": summary,
        "categories": [],
        "client_side": sides[0],
        "server_side": sides[1],
        "license_id": LICENSE,
        "initial_versions": [],
        "is_draft": True,
    }
    made = await _api("POST", "/v2/project", token, files={"data": _json_part(data)})
    if not isinstance(made, dict) or not made.get("id"):
        raise ModrinthShareError("Modrinth sent an answer that couldn't be read.")
    return str(made["id"])


async def _new_version(
    token: str,
    project_id: str,
    name: str,
    number: str,
    games: list[str],
    loaders: list[str],
    path: Path,
    filename: str,
) -> str:
    data = {
        "project_id": project_id,
        "name": name[:64],
        "version_number": number,
        "changelog": "",
        "dependencies": [],
        "game_versions": games,
        "version_type": "release",
        "loaders": loaders,
        "featured": False,
        "file_parts": ["file"],
        "primary_file": "file",
    }
    with path.open("rb") as handle:  # streamed by httpx, never read whole
        made = await _api(
            "POST",
            "/v2/version",
            token,
            files={"data": _json_part(data), "file": (filename, handle, "application/zip")},
        )
    if not isinstance(made, dict) or not made.get("id"):
        raise ModrinthShareError("Modrinth sent an answer that couldn't be read.")
    return str(made["id"])


async def upload_modpack(
    ctx: ServerContext, slug: str = "", summary: str = "", user: str = "system"
) -> dict[str, Any]:
    """The newest made modpack as a new version of this server's Modrinth
    project, made (as a draft) the first time."""
    token = _token(ctx)
    made = modpackmake.made(ctx)
    if not made:
        raise ModrinthShareError("Make a modpack first.")
    path = modpackmake.file_path(ctx, made["token"])
    games, loaders = _pack_facts(path)
    saved = _saved(ctx)
    filename = safe_filename(made["filename"], {".mrpack"})
    if not saved.get("project"):
        summary = _summary_ok(summary)

    async def run(job) -> dict[str, Any]:
        project = saved.get("project")
        if not project:
            job.step("Making the project on Modrinth (a draft)")
            project_id = await _new_project(
                token, "modpack", slug, made["name"], summary, ("required", "optional")
            )
            project = {"id": project_id, "slug": slug, "title": made["name"]}
            saved["project"] = project
            ctx.db.set_setting(SETTING, saved)
        job.step("Uploading the modpack")
        version_id = await _new_version(
            token,
            project["id"],
            f"{made['name']} {made['version']}",
            made["version"],
            games,
            loaders,
            path,
            filename,
        )
        return {"version_id": version_id}

    _job, result = await ctx.core.jobs.run(
        "modrinth_upload",
        f"Putting {made['name']} on Modrinth",
        run,
        server_id=ctx.server_id,
        user=user,
    )
    uploads = [
        {"version_id": result["version_id"], "version": made["version"], "at": time.time()},
        *(saved.get("uploads") or []),
    ][:KEEP_UPLOADS]
    ctx.db.set_setting(SETTING, {**_saved(ctx), "uploads": uploads})
    ctx.core.db.audit(
        "modrinth_upload",
        user=user,
        target=ctx.server_id,
        detail=f"{made['name']} {made['version']}",
        server_id=ctx.server_id,
    )
    return state(ctx)


async def upload_own_jar(
    ctx: ServerContext, jar_path: str, slug: str, summary: str, user: str = "system"
) -> dict[str, Any]:
    """One own jar from the newest modpack as its own Modrinth mod (a
    draft), so the modpack can point at it instead of carrying it."""
    token = _token(ctx)
    saved = _saved(ctx)
    jar = next((j for j in _own_jars(ctx, saved) if j["path"] == jar_path), None)
    base = ctx.config.server_dir.resolve()
    source = base / jar_path
    if jar is None or not source.is_file() or source.is_symlink() or not is_inside(base, source):
        raise ModrinthShareError("That jar isn't one of the own files in the newest modpack.")
    if jar["slug"]:
        raise ModrinthShareError(f"{jar['name']} is already on Modrinth.")
    summary = _summary_ok(summary)
    info = read_mod_jar(source, compute_hash=False)
    loaders = list(ctx.mods.server_type.modrinth_loaders)
    minecraft = ctx.mods.server.mc_version
    if not loaders or not minecraft:
        raise ModrinthShareError(
            "This server's Minecraft version isn't known yet. Start the server once, then try again."
        )
    sides = SIDES.get(jar["side"], SIDES["both"])
    project_id = await _new_project(token, "mod", slug, jar["name"], summary, sides)
    number = re.sub(r"[^\w.+-]", "", info.version or source.stem)[:32] or "1"
    name = f"{jar['name']} {number}"
    await _new_version(token, project_id, name, number, [minecraft], loaders, source, source.name)
    saved = _saved(ctx)
    saved["own_jars"] = {**(saved.get("own_jars") or {}), jar_path: slug}
    ctx.db.set_setting(SETTING, saved)
    ctx.core.db.audit(
        "modrinth_upload_jar",
        user=user,
        target=ctx.server_id,
        detail=jar["name"],
        server_id=ctx.server_id,
    )
    return state(ctx)


# ---------------------------------------------------------------- the Server Project
async def set_server_project(ctx: ServerContext, link: str, user: str = "system") -> dict[str, Any]:
    """Remember the owner's Modrinth Server Project (made on Modrinth's
    site: the public API can't make one), so the app can warn when the
    modpack it lists is older than the server's. Empty ``link``: forget it."""
    saved = _saved(ctx)
    if not (link or "").strip():
        saved.pop("server_project", None)
    else:
        match = PROJECT_LINK.match(link.strip())
        if not match:
            raise ModrinthShareError(
                "Paste the Server Project's Modrinth link, like modrinth.com/server/my-server."
            )
        found = await read_server_project(match.group(1))
        if found is None:
            raise ModrinthShareError("That isn't a Modrinth Server Project.")
        saved["server_project"] = {
            "id": found["id"],
            "slug": found["slug"],
            "url": f"{SITE}/server/{found['slug']}",
            "listed_version_id": found["version_id"],
            "checked_at": time.time(),
        }
    ctx.db.set_setting(SETTING, saved)
    ctx.core.db.audit(
        "modrinth_server_project", user=user, target=ctx.server_id, server_id=ctx.server_id
    )
    return state(ctx)


async def read_server_project(id_or_slug: str) -> dict[str, Any] | None:
    """The project's id, slug and the modpack version it lists (Modrinth's
    v3 API; v2 doesn't know Server Projects)."""
    try:
        project = await downloads.fetch_json(f"{API}/v3/project/{id_or_slug}")
    except downloads.DownloadNotFound:
        return None
    except downloads.DownloadError as exc:
        raise ModrinthShareError(f"Modrinth said no: {exc}") from exc
    if not isinstance(project, dict) or "minecraft_java_server" not in (
        project.get("project_types") or []
    ):
        return None
    content = (project.get("minecraft_java_server") or {}).get("content") or {}
    return {
        "id": str(project.get("id") or ""),
        "slug": str(project.get("slug") or id_or_slug),
        "version_id": content.get("version_id") if content.get("kind") == "modpack" else None,
    }


async def refresh(ctx: ServerContext) -> None:
    """Read again which modpack version the Server Project lists, at most
    every 10 minutes (when the card is opened). Quiet when Modrinth is down:
    the last answer stays."""
    saved = _saved(ctx)
    project = saved.get("server_project")
    if not project or time.time() - float(project.get("checked_at") or 0) < CHECK_EVERY:
        return
    try:
        found = await read_server_project(project["id"])
    except ModrinthShareError:
        return
    project = {**project, "checked_at": time.time()}
    project["listed_version_id"] = found["version_id"] if found else None
    ctx.db.set_setting(SETTING, {**_saved(ctx), "server_project": project})


def older(ctx: ServerContext) -> dict[str, str] | None:
    """The Server Project lists one of our own earlier uploads, not the
    newest one: players pressing Play there get an old modpack. Only from
    what Modrinth said and what we uploaded (never a guess)."""
    saved = _saved(ctx)
    listed_version_id = (saved.get("server_project") or {}).get("listed_version_id")
    uploads = saved.get("uploads") or []
    if not listed_version_id or not uploads or uploads[0]["version_id"] == listed_version_id:
        return None
    listed = next((u for u in uploads[1:] if u["version_id"] == listed_version_id), None)
    if listed is None:
        return None  # not one of ours: nothing to compare
    return {"listed": listed["version"], "newest": uploads[0]["version"]}
