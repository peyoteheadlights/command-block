"""CurseForge: mods and modpacks, through Command Block's own relay.

CurseForge's API needs a key, and the app never holds one or asks for one.
It asks our relay instead (a small Firebase function, its address in
``mods.curseforge_relay``), which adds the key and allows only five
read-only calls: search, a mod, a mod's files, a file's download link and
fingerprints. To be let in, the agent signs in to Firebase anonymously
once: each install gets its own account, so its own limit. The refresh
token is kept in .env with the agent's other secrets. Calls wait their turn
here, at most 60 a minute (the relay's limit), 20 results a page.

Nothing CurseForge answers is saved: an installed mod's record holds only
its project and file id. A file is offered only when CurseForge marks it
approved or released, downloaded only from CurseForge's file servers and
kept only when its bytes match the sha1 CurseForge published. That is a
weaker check than Modrinth's SHA-512, so the apps say "checked (sha1)". A
mod whose author doesn't let other apps download it has no download link:
none is ever made up. The owner gets its page and a drop zone instead,
under "Needs you", and a dropped jar is matched by its fingerprint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import struct
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from .. import downloads
from ..security.paths import PathSafetyError, safe_filename
from .hangar import Pace
from .sources import NotFound, SourceError

log = logging.getLogger("msc.curseforge")

MODS, MODPACKS = 6, 4471  # CurseForge's class ids
LOADERS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
GOOD_STATUS = {4, 10}  # Approved, Released; never Rejected, Malware or Deleted
MALWARE = 6
RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}
NEEDS = {3: "required", 2: "optional"}
FILE_HOSTS = frozenset({"edge.forgecdn.net", "mediafilez.forgecdn.net"})
PAGE_SITE = "https://www.curseforge.com/"
SIGN_UP = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
REFRESH = "https://securetoken.googleapis.com/v1/token"
ENV_KEY = "MCSC_CURSEFORGE_REFRESH_TOKEN"
DOWN = "CurseForge isn't available right now."
PAGE_SIZE = 50  # the relay's most
FILE_PAGES = 4  # pages of a mod's files read to find one file
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
ID_RE = re.compile(r"^\d{1,10}$")
# What the relay lets through in a search; anything else becomes a space.
SEARCH_TEXT = re.compile(r"[^\w .,'+\-!&()]")

# The relay's limit is per install, so all servers share one pace and one sign-in.
PACE = Pace(60, 60.0)


class SignIn:
    def __init__(self) -> None:
        self.token: str | None = None
        self.expires = 0.0
        self.lock = asyncio.Lock()


SIGN_IN = SignIn()


class Blocked(SourceError):
    """The author doesn't let other apps download this file. ``need`` is
    what "Needs you" shows: the mod, its page and the file to drop."""

    def __init__(self, need: dict[str, Any]) -> None:
        super().__init__(
            f"{need['name']}'s author doesn't let other apps download it. Open it on "
            "CurseForge, download it there and drop the file here."
        )
        self.need = need


def fingerprint(data: bytes) -> int:
    """CurseForge's fingerprint: MurmurHash2 (seed 1) of the file with every
    tab, newline, carriage return and space byte taken out."""
    return murmur2(data.translate(None, b"\t\n\r "), 1)


def murmur2(data: bytes, seed: int) -> int:
    """Austin Appleby's 32-bit MurmurHash2."""
    m, length = 0x5BD1E995, len(data)
    h = (seed ^ length) & 0xFFFFFFFF
    tail = length - length % 4
    for (k,) in struct.iter_unpack("<I", data[:tail]):
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> 24
        h = (((h * m) & 0xFFFFFFFF) ^ ((k * m) & 0xFFFFFFFF)) & 0xFFFFFFFF
    rest = data[tail:]
    if rest:
        for i, byte in enumerate(rest):
            h ^= byte << (8 * i)
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    return h ^ (h >> 15)


def file_prints(path: str | Path) -> tuple[int, str]:
    """(fingerprint, sha1) of a jar, read once."""
    data = Path(path).read_bytes()
    return fingerprint(data), hashlib.sha1(data).hexdigest()


def plain_id(value: str) -> str:
    value = str(value or "").removeprefix("curseforge:")
    if not ID_RE.match(value):
        raise SourceError("That isn't a CurseForge id.")
    return value


def split_version(version_id: str) -> tuple[str, str]:
    """("238222", "4712345") from "curseforge:238222-4712345"."""
    mod, _, file = str(version_id or "").removeprefix("curseforge:").partition("-")
    return plain_id(mod), plain_id(file)


def page_of(mod: dict[str, Any]) -> str | None:
    """The mod's own CurseForge page, as CurseForge gives it. Never made up."""
    url = str((mod.get("links") or {}).get("websiteUrl") or "")
    return url if url.startswith(PAGE_SITE) else None


class CurseForgeClient:
    name = "curseforge"
    label = "CurseForge"

    def __init__(self, config):
        self.config = config

    @property
    def loaders(self) -> tuple[str, ...]:
        """The CurseForge loaders this server takes ("quilt", "fabric")."""
        server_type = getattr(self.config, "server_type", None)
        if not server_type or server_type.content != "mods":
            return ()
        return tuple(name for name in server_type.modrinth_loaders if name in LOADERS)

    # ------------------------------------------------------------------
    # signing in and asking the relay
    # ------------------------------------------------------------------
    async def _firebase(self, url: str, body: bytes, kind: str) -> dict[str, Any]:
        key = self.config.mods.firebase_web_key
        await PACE.wait()
        answer = await downloads.fetch_json(
            f"{url}?{urlencode({'key': key})}",
            method="POST",
            headers={"Content-Type": kind},
            content=body,
        )
        if not isinstance(answer, dict):
            raise downloads.DownloadError("Firebase sent an answer that couldn't be read.")
        return answer

    async def _token(self, fresh: bool = False) -> str:
        """A sign-in token for the relay, refreshed before it runs out."""
        async with SIGN_IN.lock:
            if SIGN_IN.token and not fresh and time.time() < SIGN_IN.expires - 300:
                return SIGN_IN.token
            try:
                token, refresh, lasts = await self._sign_in()
            except (downloads.DownloadError, KeyError, TypeError, ValueError) as exc:
                log.info("curseforge sign-in: %s", exc)
                raise SourceError(DOWN) from exc
            if refresh != os.environ.get(ENV_KEY):
                await self._keep(refresh)
            SIGN_IN.token, SIGN_IN.expires = token, time.time() + lasts
            return token

    async def _sign_in(self) -> tuple[str, str, int]:
        refresh = os.environ.get(ENV_KEY)
        if refresh:
            try:
                got = await self._firebase(
                    REFRESH,
                    urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode(),
                    "application/x-www-form-urlencoded",
                )
                return str(got["id_token"]), str(got["refresh_token"]), int(got["expires_in"])
            except downloads.DownloadError as exc:
                if exc.status != 400:  # 400: that sign-in is gone; make a new one
                    raise
        got = await self._firebase(
            SIGN_UP, json.dumps({"returnSecureToken": True}).encode(), "application/json"
        )
        return str(got["idToken"]), str(got["refreshToken"]), int(got["expiresIn"])

    async def _keep(self, refresh: str) -> None:
        """The refresh token goes into .env, next to the other secrets."""
        os.environ[ENV_KEY] = refresh
        path = getattr(self.config, "env_path", None)
        if path:
            from installer.setup_tool import write_env_value

            await asyncio.to_thread(write_env_value, path, ENV_KEY, refresh)

    async def _call(
        self, path: str, params: dict | None = None, prints: list | None = None
    ) -> dict[str, Any]:
        """One of the relay's five calls, CurseForge's answer as it is ("data"
        and, for lists, "pagination"). Anything but a good answer is
        "CurseForge isn't available right now"."""
        url = self.config.mods.curseforge_relay.rstrip("/") + path
        if params:
            url += f"?{urlencode(params)}"
        for attempt in (1, 2):
            token = await self._token(fresh=attempt == 2)
            await PACE.wait()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            request: dict[str, Any] = {"headers": headers}
            if prints is not None:
                headers["Content-Type"] = "application/json"
                request.update(method="POST", content=json.dumps({"fingerprints": prints}).encode())
            try:
                answer = await downloads.fetch_json(url, **request)
            except downloads.DownloadError as exc:
                if exc.status == 401 and attempt == 1:
                    continue  # the token ran out early: sign in again once
                log.info("curseforge %s: %s", path, exc)
                raise SourceError(DOWN) from exc
            if not isinstance(answer, dict) or "data" not in answer:
                raise SourceError(DOWN)
            return answer
        raise SourceError(DOWN)

    # ------------------------------------------------------------------
    # mods
    # ------------------------------------------------------------------
    async def search(
        self,
        query: str,
        minecraft_version: str | None = None,
        limit: int = 20,
        offset: int = 0,
        kind: int = MODS,
    ) -> dict[str, Any]:
        """Most popular first, with the names closest to ``query`` on top. Only
        mods with a file for this server's loader and version are offered."""
        query = SEARCH_TEXT.sub(" ", query or "")[:100].strip()
        size = max(1, min(int(limit), PAGE_SIZE))
        start = max(0, min(int(offset), 10_000 - size))
        params: dict[str, Any] = {
            "classId": kind,
            "searchFilter": query,
            "sortField": 2,  # popularity; without it the order is no order at all
            "sortOrder": "desc",
            "index": start,
            "pageSize": size,
        }
        ids = [LOADERS[name] for name in self.loaders]
        if kind == MODS and minecraft_version:
            params["gameVersion"] = minecraft_version
            if len(ids) == 1:
                params["modLoaderType"] = ids[0]
        answer = await self._call("/search", params)
        mods = [
            m
            for m in listed(answer)
            if kind != MODS or not minecraft_version or self._fits(m, minecraft_version, ids)
        ]
        total = (answer.get("pagination") or {}).get("totalCount")
        return {
            "hits": rank(query, [self._project(m) for m in mods]),
            "total": min(int(total), 10_000) if isinstance(total, int) else len(mods),
            "offset": start,
        }

    @staticmethod
    def _fits(mod: dict[str, Any], minecraft_version: str, ids: list[int]) -> bool:
        return any(
            i.get("gameVersion") == minecraft_version and i.get("modLoader") in ids
            for i in mod.get("latestFilesIndexes") or []
            if isinstance(i, dict)
        )

    async def project(self, project: str) -> dict[str, Any]:
        data = (await self._call(f"/mods/{plain_id(project)}"))["data"]
        if not isinstance(data, dict):
            raise NotFound("CurseForge doesn't have that.")
        return self._project(data)

    def _project(self, m: dict[str, Any]) -> dict[str, Any]:
        authors = [a for a in m.get("authors") or [] if isinstance(a, dict)]
        indexes = [i for i in m.get("latestFilesIndexes") or [] if isinstance(i, dict)]
        return {
            "project_id": f"curseforge:{m.get('id')}",
            "slug": f"curseforge:{m.get('id')}",
            "title": m.get("name"),
            "description": m.get("summary"),
            "author": authors[0].get("name") if authors else None,
            "downloads": int(m["downloadCount"]) if m.get("downloadCount") is not None else None,
            "categories": [c.get("name") for c in m.get("categories") or [] if isinstance(c, dict)],
            "game_versions": sorted({str(i.get("gameVersion")) for i in indexes}),
            "icon_url": (m.get("logo") or {}).get("thumbnailUrl"),
            "page": page_of(m),
            # False: the author keeps other apps from downloading its files.
            "blocked": m.get("allowModDistribution") is False,
            "source": self.name,
            "source_name": self.label,
        }

    def _offered(self, f: dict[str, Any], minecraft_version: str | None, kind: int) -> bool:
        if f.get("fileStatus") not in GOOD_STATUS or f.get("isAvailable") is False:
            return False
        if kind == MODPACKS:
            return not f.get("isServerPack")
        tags = {str(v).lower() for v in f.get("gameVersions") or []}
        if minecraft_version and minecraft_version.lower() not in tags:
            return False
        return any(name in tags for name in self.loaders)

    async def files(
        self, project: str, minecraft_version: str | None = None, index: int = 0, loader=None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pageSize": PAGE_SIZE, "index": index}
        if minecraft_version:
            params["gameVersion"] = minecraft_version
            if loader is not None:
                params["modLoaderType"] = loader
        return listed(await self._call(f"/mods/{plain_id(project)}/files", params))

    async def versions(
        self, project: str, minecraft_version: str | None = None, kind: int = MODS
    ) -> list[dict[str, Any]]:
        """The files this server can use, newest first."""
        ids = [LOADERS[name] for name in self.loaders]
        loader = ids[0] if len(ids) == 1 and kind == MODS else None
        found = [
            f
            for f in await self.files(project, minecraft_version, loader=loader)
            if self._offered(f, minecraft_version, kind)
        ]
        found.sort(key=lambda f: str(f.get("fileDate") or ""), reverse=True)
        return [version_of(f) for f in found]

    async def file(
        self, project: str, file_id: str, minecraft_version: str | None = None, loader=None
    ) -> dict[str, Any] | None:
        """One file of a mod, as CurseForge lists it (the relay has no call
        for a single file): looked for among the files for this version
        first, then a few pages of all of them."""
        wanted = int(plain_id(file_id))
        pages: list[tuple[str | None, int]] = [(minecraft_version, 0)] if minecraft_version else []
        pages += [(None, n * PAGE_SIZE) for n in range(FILE_PAGES)]
        for version, index in pages:
            found = await self.files(project, version, index, loader if version else None)
            match = next((f for f in found if f.get("id") == wanted), None)
            if match or (version is None and len(found) < PAGE_SIZE):
                return match
        return None

    async def version(self, version_id: str) -> dict[str, Any]:
        mod, file_id = split_version(version_id)
        found = await self.file(mod, file_id)
        if not found or found.get("fileStatus") not in GOOD_STATUS:
            raise NotFound("CurseForge doesn't offer that file.")
        return version_of(found)

    async def latest_for(
        self, project: str, minecraft_version: str | None, like: str | None = None, kind=MODS
    ) -> dict[str, Any] | None:
        """The newest release, or when a mod has none, its newest test build."""
        found = await self.versions(project, minecraft_version, kind)
        return next((v for v in found if v["release_type"] == "release"), None) or (
            found[0] if found else None
        )

    async def need_for(self, version: dict[str, Any]) -> dict[str, Any]:
        """What "Needs you" shows for a file the app may not download."""
        name, page = version.get("name"), None
        try:
            mod = await self.project(version["project_id"])
            name, page = mod["title"] or name, mod["page"]
        except SourceError:
            pass  # still listed; the page link comes back with the relay
        return {
            "project": version["project_id"],
            "version_id": version["version_id"],
            "name": name or version["file"].get("filename"),
            "filename": version["file"].get("filename"),
            "page": page,
        }

    async def by_fingerprint(self, prints: list[int]) -> dict[int, dict[str, Any]]:
        """The CurseForge file each fingerprint belongs to, when there is one."""
        if not prints:
            return {}
        data = (await self._call("/fingerprints", prints=sorted(set(prints))[:500]))["data"]
        matches = data.get("exactMatches") if isinstance(data, dict) else None
        found = {}
        for match in matches or []:
            file = match.get("file") if isinstance(match, dict) else None
            if isinstance(file, dict) and file.get("fileFingerprint") is not None:
                found[int(file["fileFingerprint"])] = version_of(file)
        return found

    # ------------------------------------------------------------------
    # downloads
    # ------------------------------------------------------------------
    async def spec(self, version: dict[str, Any], ext: str = ".jar", name=None):
        """The checked download of ``version``'s file, or Blocked."""
        file = version.get("file") or {}
        url, filename = file.get("url"), file.get("filename")
        if not url:
            raise Blocked(await self.need_for(version))
        parsed = urlparse(str(url))
        if not filename or parsed.scheme != "https" or parsed.hostname not in FILE_HOSTS:
            raise SourceError(
                "This file isn't on CurseForge's own servers, so it wasn't downloaded."
            )
        try:
            safe_filename(filename, {ext})
        except PathSafetyError as exc:
            raise SourceError(f"This file can't be used: {exc}") from exc
        if not file.get("sha1"):
            raise SourceError(
                "CurseForge published no checksum for this file, so it can't be checked "
                "and wasn't installed."
            )
        return downloads.FileSpec(
            url=str(url),
            name=name or filename,
            sha1=file["sha1"],
            size=int(file["size"]) if file.get("size") else None,
            allow_unverified=False,
            max_bytes=MAX_DOWNLOAD_BYTES,
        )

    async def download(self, version: dict[str, Any]) -> tuple[bytes, str, str]:
        spec = await self.spec(version)
        try:
            data, fetched = await downloads.download_bytes(spec)
        except downloads.DownloadError as exc:
            raise SourceError(str(exc)) from exc
        if not data.startswith(b"PK"):
            raise SourceError("The downloaded file isn't a mod (.jar), so it was thrown away.")
        return data, spec.name, fetched.sha256


def listed(answer: dict[str, Any]) -> list[dict[str, Any]]:
    data = answer.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def version_of(f: dict[str, Any]) -> dict[str, Any]:
    """A CurseForge file in the shape every source's versions have."""
    tags = [str(v) for v in f.get("gameVersions") or []]
    sha1 = next(
        (str(h.get("value")).lower() for h in f.get("hashes") or [] if h.get("algo") == 1), None
    )
    mod, file_id = f.get("modId"), f.get("id")
    return {
        "version_id": f"curseforge:{mod}-{file_id}",
        "project_id": f"curseforge:{mod}",
        "name": f.get("displayName"),
        "version_number": f.get("displayName") or f.get("fileName"),
        "release_type": RELEASE_TYPES.get(int(f.get("releaseType") or 0)),
        "game_versions": [v for v in tags if v[:1].isdigit()],
        "loaders": [v.lower() for v in tags if v.lower() in LOADERS],
        "downloads": f.get("downloadCount"),
        "date_published": f.get("fileDate"),
        "changelog": "",
        "dependencies": [
            {
                "project_id": f"curseforge:{d.get('modId')}",
                "version_id": None,
                "type": NEEDS[d["relationType"]],
                "name": None,
            }
            for d in f.get("dependencies") or []
            if isinstance(d, dict) and d.get("relationType") in NEEDS
        ],
        # Tagged for the game only, not servers (CurseForge's own tags).
        "client_only": "Client" in tags and "Server" not in tags,
        "status": f.get("fileStatus"),
        "file": {
            "filename": f.get("fileName"),
            "url": f.get("downloadUrl"),
            "size": f.get("fileLength"),
            "sha1": sha1,
        },
    }


def rank(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The name typed first, then names that start with it or hold it, each
    group most downloaded first: "lithium" finds Lithium, not a look-alike."""

    def plain(text: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

    wanted = plain(query)

    def place(hit: dict[str, Any]) -> tuple[int, int]:
        name = plain(hit.get("title"))
        if not wanted or wanted not in name:
            group = 3
        else:
            group = 0 if name == wanted else 1 if name.startswith(wanted) else 2
        return group, -(hit.get("downloads") or 0)

    return sorted(hits, key=place)
