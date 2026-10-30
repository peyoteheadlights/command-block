"""Add-on sources: the sites mods and plugins come from.

The mod manager talks to every site through the same few calls, so Modrinth
(agent/mods/modrinth.py), Hangar (hangar.py) and GitHub releases
(github.py) look the same to it. A version is a dict in Modrinth's shape
(``version_id``, ``version_number``, ``release_type``, ``game_versions``,
``loaders``, ``dependencies``, ``file``), whichever site it came from.

Ids from a site other than Modrinth carry its name in front
("hangar:31"), so the apps send them back unchanged and the agent knows
where to ask. Modrinth's own ids and slugs never hold a ":".
"""

from __future__ import annotations

from typing import Any, Protocol

# The names people know each source by. "file" is an add-on dropped in or
# picked from the PC, which no site has checked.
LABELS = {"modrinth": "Modrinth", "hangar": "Hangar", "github": "GitHub", "file": "a file"}


class SourceError(RuntimeError):
    """A site couldn't be asked or sent something unusable. Written for people."""


class NotFound(SourceError):
    """The site answered that the project or version doesn't exist."""


class AddonSource(Protocol):
    async def version(self, version_id: str) -> dict[str, Any]: ...

    async def latest_for(
        self, project: str, minecraft_version: str | None, *, like: str | None = None
    ) -> dict[str, Any] | None:
        """The newest version to install or update to. ``like`` is the
        installed file's name, for sites whose versions hold several files."""
        ...

    async def download(self, version: dict[str, Any]) -> tuple[bytes, str, str]:
        """(bytes, filename, sha256), checked against what the site published."""
        ...


def split(project: str) -> tuple[str, str]:
    """("hangar", "31") from "hangar:31"; a plain id or slug is Modrinth's."""
    site, _, rest = project.partition(":")
    return (site, rest) if rest else ("modrinth", project)
