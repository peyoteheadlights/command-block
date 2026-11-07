"""GitHub releases, for add-ons an owner adds from a link.

A link like ``github.com/<owner>/<repo>/releases/tag/<tag>`` is read with
GitHub's public releases API (no key). The owner picks one of the release's
.jar or .zip files; nothing is picked for them. Only that repo's release list
and files may be read, and only while this module asks (downloads.allow_releases).

Updates come from the repo's newest full release (not a pre-release), and
only when it has exactly one file named like the installed one with other
numbers ("MyPlugin-1.2.jar" and "MyPlugin-1.3.jar"). Anything else is
reported, never guessed.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from .. import downloads, savedlists
from ..security.paths import PathSafetyError, safe_filename
from .jarinfo import DISABLED_SUFFIX
from .sources import NotFound, SourceError

API = "https://api.github.com"
FILE_TYPES = {".jar", ".zip"}
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
DOWN = "GitHub isn't available right now."
NAME = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
REPO = r"[A-Za-z0-9._-]{1,100}"
LINK_RE = re.compile(
    rf"^/(?P<repo>{NAME}/{REPO})(?:/releases(?:/tag/(?P<tag>[^/]+)|/latest|"
    r"/download/(?P<file_tag>[^/]+)/[^/]+)?)?/?$"
)
VERSION_ID_RE = re.compile(rf"^(?P<repo>{NAME}/{REPO})/(?P<asset>\d{{1,15}})$")


def read_link(url: str) -> tuple[str, str | None] | None:
    """(repo, tag) from a github.com link to a repo or one of its releases;
    no tag means its newest release. None for any other link."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("github.com", "www.github.com"):
        return None
    found = LINK_RE.match(parsed.path)
    if not found:
        return None
    tag = found["tag"] or found["file_tag"]
    return found["repo"], unquote(tag) if tag else None


def shape(filename: str) -> str:
    """A file name with its numbers taken out, to find the same file in a
    newer release."""
    return re.sub(r"\d+", "0", filename.removesuffix(DISABLED_SUFFIX).lower())


class GitHubReleases:
    name = "github"
    label = "GitHub"

    def __init__(self, config=None):
        self.config = config

    async def _get(self, repo: str, path: str, saved: bool = False) -> Any:
        url = f"{API}/repos/{repo}/releases{path}"
        with downloads.allow_releases(repo):
            try:
                if saved:
                    return await savedlists.fetch_json(url, lambda d: isinstance(d, dict))
                return await downloads.fetch_json(url)
            except downloads.DownloadNotFound as exc:
                raise NotFound("GitHub has no release there.") from exc
            except downloads.DownloadError as exc:
                raise SourceError(DOWN) from exc

    async def release(self, repo: str, tag: str | None = None) -> dict[str, Any]:
        """The release (its newest full one without a tag) and its files."""
        path = f"/tags/{tag}" if tag else "/latest"
        data = await self._get(repo, path, saved=tag is None)
        if not isinstance(data, dict) or data.get("draft"):
            raise NotFound("GitHub has no release there.")
        files = [
            self._version(repo, data, asset)
            for asset in data.get("assets") or []
            if isinstance(asset, dict)
            and str(asset.get("name") or "").lower().endswith(tuple(FILE_TYPES))
        ]
        return {
            "repo": repo,
            "tag": data.get("tag_name"),
            "name": data.get("name") or data.get("tag_name"),
            "prerelease": bool(data.get("prerelease")),
            "page": data.get("html_url"),
            "files": files,
        }

    def _version(self, repo: str, release: dict[str, Any], asset: dict[str, Any]) -> dict:
        digest = str(asset.get("digest") or "")
        tag = release.get("tag_name") or _tag_of(asset)
        return {
            "version_id": f"{repo}/{asset.get('id')}",
            "project_id": repo,
            "name": release.get("name") or tag,
            "version_number": tag,
            "release_type": "beta" if release.get("prerelease") else "release",
            "game_versions": [],
            "loaders": [],
            "downloads": asset.get("download_count"),
            "date_published": release.get("published_at"),
            "changelog": (release.get("body") or "")[:1000],
            "dependencies": [],
            "file": {
                "filename": asset.get("name"),
                "url": asset.get("browser_download_url"),
                "size": asset.get("size"),
                # GitHub has listed a checksum for files uploaded since 2025.
                "sha256": digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
            },
        }

    async def version(self, version_id: str) -> dict[str, Any]:
        """One release file, by "owner/repo/<file id>"."""
        found = VERSION_ID_RE.match(version_id)
        if not found:
            raise SourceError("That isn't a GitHub release file.")
        asset = await self._get(found["repo"], f"/assets/{found['asset']}")
        return self._version(found["repo"], {}, asset)

    async def latest_for(
        self, project: str, minecraft_version: str | None, like: str | None = None
    ) -> dict[str, Any] | None:
        release = await self.release(project)
        same = [f for f in release["files"] if like and shape(f["file"]["filename"]) == shape(like)]
        if len(same) != 1:
            raise SourceError(
                f"The newest release on GitHub ({release['tag']}) has no single file like "
                f"{like}, so pick one with Add from a link."
            )
        return same[0]

    async def download(self, version: dict[str, Any]) -> tuple[bytes, str, str]:
        file = version.get("file") or {}
        repo, filename = str(version.get("project_id") or ""), file.get("filename")
        try:
            safe_filename(str(filename or ""), FILE_TYPES)
        except PathSafetyError as exc:
            raise SourceError(f"This file can't be used: {exc}") from exc
        spec = downloads.FileSpec(
            url=str(file.get("url") or ""),
            name=str(filename),
            sha256=file.get("sha256"),
            size=int(file["size"]) if file.get("size") else None,
            max_bytes=MAX_DOWNLOAD_BYTES,
        )
        with downloads.allow_releases(repo):
            try:
                data, fetched = await downloads.download_bytes(spec)
            except downloads.DownloadError as exc:
                raise SourceError(str(exc)) from exc
        if not data.startswith(b"PK"):
            raise SourceError(
                "The downloaded file isn't a .jar or .zip file, so it was thrown away."
            )
        return data, str(filename), fetched.sha256


def _tag_of(asset: dict[str, Any]) -> str | None:
    """The tag in a release file's address (.../releases/download/<tag>/<name>)."""
    parts = urlparse(str(asset.get("browser_download_url") or "")).path.split("/")
    return unquote(parts[-2]) if len(parts) > 2 and parts[-3] == "download" else None
