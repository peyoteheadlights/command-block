"""Hangar: PaperMC's own plugin site, as a source next to Modrinth.

Only Hangar's public API (``hangar.papermc.io/api/v1``) is used, without a
key, through the safe downloader (agent/downloads.py), which sends the
app's own User-Agent. Hangar asks for at most 20 requests every 5 seconds,
so every call waits its turn here. Version lists and project pages are kept
as saved copies (agent/savedlists.py); searches are not.

Only public projects and versions are offered, for the server type's
Hangar platform (PAPER). A file is downloaded only from Hangar's own file
host and only when its bytes match the SHA-256 Hangar published. A version
whose file lives on another site is never downloaded: its link is shown.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from .. import downloads, savedlists
from ..security.paths import PathSafetyError, safe_filename
from .sources import NotFound, SourceError

log = logging.getLogger("msc.hangar")

API = "https://hangar.papermc.io/api/v1"
SITE = "https://hangar.papermc.io"
FILE_HOST = "hangarcdn.papermc.io"
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
DOWN = "Hangar isn't available right now."
ID_RE = re.compile(r"^\d{1,12}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Pace:
    """At most ``count`` requests in any ``seconds``: each call books the
    next free slot before it waits, so calls made at once never share one."""

    def __init__(self, count: int = 20, seconds: float = 5.0) -> None:
        self.seconds = seconds
        self.booked: deque[float] = deque(maxlen=count)

    async def wait(self) -> None:
        now = time.monotonic()
        full = len(self.booked) == self.booked.maxlen
        start = max(now, self.booked[0] + self.seconds) if full else now
        self.booked.append(start)
        if start > now:
            await asyncio.sleep(start - now)


# Hangar's limit is per PC, so all servers share one.
PACE = Pace()


def plain_id(value: str) -> str:
    """A Hangar project or version id ("31"), or a refusal."""
    value = str(value or "").removeprefix("hangar:")
    if not ID_RE.match(value):
        raise SourceError("That isn't a Hangar id.")
    return value


class HangarClient:
    name = "hangar"
    label = "Hangar"

    def __init__(self, config):
        self.config = config

    @property
    def platform(self) -> str:
        return self.config.server_type.hangar_platform or "PAPER"

    async def _get(self, path: str, params: dict | None = None, saved: bool = False) -> Any:
        url = f"{API}{path}" + (f"?{urlencode(params)}" if params else "")
        await PACE.wait()
        try:
            if saved:
                return await savedlists.fetch_json(url, lambda d: isinstance(d, dict))
            data = await downloads.fetch_json(url)
        except downloads.DownloadNotFound as exc:
            raise NotFound("Hangar doesn't have that.") from exc
        except downloads.DownloadError as exc:
            log.info("hangar %s: %s", path, exc)
            raise SourceError(DOWN) from exc
        if not isinstance(data, dict):
            raise SourceError(DOWN)
        return data

    # ------------------------------------------------------------------
    async def search(
        self, query: str, minecraft_version: str | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "query": query or "",
            "platform": self.platform,
            "limit": max(1, min(int(limit), 25)),
            "offset": max(0, int(offset)),
        }
        if minecraft_version:
            params["version"] = minecraft_version
        data = await self._get("/projects", params)
        hits = [self._project(p) for p in data.get("result") or [] if _public(p)]
        return {
            "hits": hits,
            "total": (data.get("pagination") or {}).get("count", len(hits)),
            "offset": params["offset"],
        }

    async def project(self, project: str) -> dict[str, Any]:
        data = await self._get(f"/projects/{plain_id(project)}", saved=True)
        if not _public(data):
            raise NotFound("Hangar doesn't have that.")
        return self._project(data)

    def _project(self, p: dict[str, Any]) -> dict[str, Any]:
        space = p.get("namespace") or {}
        return {
            "project_id": f"hangar:{p.get('id')}",
            "slug": f"hangar:{p.get('id')}",
            "title": p.get("name"),
            "description": p.get("description"),
            "author": space.get("owner"),
            "downloads": (p.get("stats") or {}).get("downloads"),
            "categories": [p["category"]] if p.get("category") else [],
            "loaders": ["paper"],
            "game_versions": (p.get("supportedPlatforms") or {}).get(self.platform) or [],
            "server_side": "required",
            "client_side": "unsupported",
            "license": (p.get("license") or {}).get("name"),
            "icon_url": p.get("avatarUrl"),
            "page": f"{SITE}/{quote(str(space.get('owner') or ''))}/"
            f"{quote(str(space.get('slug') or ''))}",
            "source": self.name,
            "source_name": self.label,
        }

    async def versions(
        self, project: str, minecraft_version: str | None = None, channel: str | None = None
    ) -> list[dict[str, Any]]:
        """Public versions for this platform, newest first."""
        params: dict[str, Any] = {"platform": self.platform, "limit": 25, "offset": 0}
        if minecraft_version:
            params["platformVersion"] = minecraft_version
        if channel:
            params["channel"] = channel
        data = await self._get(f"/projects/{plain_id(project)}/versions", params, saved=True)
        found = [v for v in data.get("result") or [] if _public(v)]
        found.sort(key=lambda v: str(v.get("createdAt") or ""), reverse=True)
        return [self._version(v) for v in found]

    async def version(self, version_id: str) -> dict[str, Any]:
        data = await self._get(f"/versions/{plain_id(version_id)}")
        if not _public(data):
            raise NotFound("Hangar doesn't have that.")
        return self._version(data)

    async def latest_for(
        self, project: str, minecraft_version: str | None, like: str | None = None
    ) -> dict[str, Any] | None:
        """The newest Release channel version: a test build is never
        installed or offered as an update by itself."""
        found = await self.versions(project, minecraft_version, channel="Release")
        return found[0] if found else None

    def _version(self, v: dict[str, Any]) -> dict[str, Any]:
        here = (v.get("downloads") or {}).get(self.platform) or {}
        info = here.get("fileInfo") or {}
        channel = str((v.get("channel") or {}).get("name") or "")
        needs = (v.get("pluginDependencies") or {}).get(self.platform) or []
        return {
            "version_id": f"hangar:{v.get('id')}",
            "project_id": f"hangar:{v.get('projectId')}",
            "name": v.get("name"),
            "version_number": v.get("name"),
            "release_type": "release" if channel == "Release" else channel.lower() or None,
            "game_versions": (v.get("platformDependencies") or {}).get(self.platform) or [],
            "loaders": ["paper"] if here else [],
            "downloads": (v.get("stats") or {}).get("totalDownloads"),
            "date_published": v.get("createdAt"),
            "changelog": (v.get("description") or "")[:1000],
            "dependencies": [
                {
                    "project_id": f"hangar:{d['projectId']}" if d.get("projectId") else None,
                    "version_id": None,
                    "type": "required" if d.get("required") else "optional",
                    "name": d.get("name"),
                    # Not on Hangar: only ever shown as a link.
                    "url": d.get("externalUrl"),
                }
                for d in needs
                if isinstance(d, dict)
            ],
            "file": {
                "filename": info.get("name"),
                "url": here.get("downloadUrl"),
                "size": info.get("sizeBytes"),
                "sha256": info.get("sha256Hash"),
                "external_url": here.get("externalUrl"),
            },
        }

    async def by_hash(self, sha256: str) -> str | None:
        """The Hangar project id of a file with this SHA-256, or None."""
        if not SHA256_RE.match(sha256):
            return None
        try:
            found = await self._get(f"/versions/hash/{sha256}")
        except NotFound:
            return None
        return str(found["id"]) if _public(found) and found.get("id") else None

    # ------------------------------------------------------------------
    async def download(self, version: dict[str, Any]) -> tuple[bytes, str, str]:
        file = version.get("file") or {}
        url, filename = file.get("url"), file.get("filename")
        if not url and file.get("external_url"):
            raise SourceError(
                "This plugin's file isn't on Hangar, so it wasn't downloaded. Get it from "
                f"{file['external_url']} and drop it here."
            )
        parsed = urlparse(str(url or ""))
        if not filename or parsed.scheme != "https" or parsed.hostname != FILE_HOST:
            raise SourceError("This file isn't on Hangar's own servers, so it wasn't downloaded.")
        try:
            safe_filename(filename, {".jar"})
        except PathSafetyError as exc:
            raise SourceError(f"This file can't be used: {exc}") from exc
        if not file.get("sha256"):
            raise SourceError(
                "Hangar published no checksum for this file, so it can't be checked "
                "and wasn't installed."
            )
        spec = downloads.FileSpec(
            url=str(url),
            name=filename,
            sha256=file["sha256"],
            size=int(file["size"]) if file.get("size") else None,
            allow_unverified=False,
            max_bytes=MAX_DOWNLOAD_BYTES,
        )
        try:
            data, fetched = await downloads.download_bytes(spec)
        except downloads.DownloadError as exc:
            raise SourceError(str(exc)) from exc
        if not data.startswith(b"PK"):
            raise SourceError("The downloaded file isn't a plugin (.jar), so it was thrown away.")
        return data, filename, fetched.sha256


def _public(item: Any) -> bool:
    return isinstance(item, dict) and item.get("visibility") == "public"
