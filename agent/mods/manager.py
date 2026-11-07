"""The mod and plugin manager.

Mods (Fabric, Quilt, Forge, NeoForge) live in mods/; plugins (Paper,
Purpur) live in plugins/; datapacks in the world's datapacks/. Which one,
which loaders are accepted and which sites' listings fit come from the
server type's capabilities (agent/servertypes). Add-ons come from Modrinth,
Hangar, CurseForge or a GitHub release, all through the same calls
(agent/mods/sources.py), or from a file. Everything that touches those
folders goes through this class, and every write is:

  * confined to the mods folder by agent.security.paths
  * limited to .jar (and .jar.disabled) files, and .zip datapacks
  * recorded in mod_history with actor, source URL and SHA-256
  * reversible - removals go to a trash folder, updates keep the old jar

Nothing here executes a downloaded file. The only program that ever reads a
mod jar is the Minecraft server itself, when the operator starts it.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from .. import savedlists
from ..events import Event, EventBus
from ..minecraft.properties import FILENAME, FormError, PropertiesFile, world_folder
from ..security.paths import (
    PathSafetyError,
    assert_not_symlink,
    is_inside,
    safe_filename,
    safe_join,
)
from . import checks
from .curseforge import Blocked, CurseForgeClient, file_prints
from .dependencies import PLATFORM_IDS, DependencyResolver
from .github import GitHubReleases
from .hangar import HangarClient
from .jarinfo import (
    DISABLED_SUFFIX,
    ModInfo,
    range_satisfies,
    read_library_mods,
    read_mod_jar,
    sha256_file,
    wrong_loader_reason,
)
from .modrinth import ModrinthClient
from .sources import LABELS, AddonSource, SourceError, split

log = logging.getLogger("msc.mods")

JAR_EXT = {".jar"}
# What can be added from a file or a link: mods and plugins, and datapacks.
FILE_EXT = {".jar", ".zip"}
MAX_FILE_BYTES = 300 * 1024 * 1024
# A jar Modrinth, Hangar and CurseForge don't list (agent/mods/addfrom.py).
# Never "scanned" or "checked": nothing can tell what a jar will do.
UNKNOWN_FILE = (
    "Modrinth, Hangar and CurseForge don't list this file, so the app can't tell what it "
    "is or who made it. Add it only if you trust where it came from."
)
# A jar with one of these is a mod or plugin (agent/mods/jarinfo.py reads them).
MOD_FILES = (
    "fabric.mod.json",
    "quilt.mod.json",
    "META-INF/mods.toml",
    "META-INF/neoforge.mods.toml",
    "mcmod.info",
    "plugin.yml",
    "paper-plugin.yml",
)
# How deep required plugins of required plugins are added.
MAX_NEEDS_DEPTH = 6
NO_UPDATES = "Added from a file, so there's no list to check it against for updates."
# Mods CurseForge's authors don't let other apps download: their page and a
# drop zone, until the owner drops the file in (see Blocked).
NEEDS_YOU = "curseforge_needs_you"
BLOCKED_UPDATE = "{version} is out, but its author doesn't let other apps download it."
# How each site's files are checked, for the one weaker than the rest.
CHECKED_WITH = {"curseforge": "sha1"}
# How many mods Update all asks Modrinth about at the same time.
PLAN_CHECKS_AT_ONCE = 6


class ModError(RuntimeError):
    """Operator-visible mod management problem."""


class AlreadyHere(ModError):
    """A needed mod turned out to be installed already, from another site."""


def checked(source: str | None) -> dict[str, Any]:
    """Who checked a file, and how when it's the weaker sha1: only a site
    that lists the very file has looked at it."""
    listed = source in ("modrinth", "hangar", "curseforge")
    return {
        "checked_by": LABELS[source] if listed and source else None,
        "checked_with": CHECKED_WITH.get(source or ""),
    }


def refuse_known_bad(data: bytes, filename: str) -> None:
    """Refuse a file the signed safety notes list as known bad (agent/notes.py)."""
    from .. import notes

    bad = notes.known_bad(data)
    if bad:
        raise ModError(
            f"{filename} is on the safety notes' list of known bad files ({bad['why']}), "
            "so it wasn't installed."
        )


def is_datapack(data: bytes) -> bool:
    """A datapack, not a mod or plugin that happens to carry pack.mcmeta."""
    if datapack_problem(data):
        return False
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
    return not any(name in names for name in MOD_FILES)


def datapack_problem(data: bytes) -> str | None:
    """None when ``data`` is a datapack Minecraft can load (a zip with
    pack.mcmeta and data/ at its top), else why not, in plain words. Only
    read, never unpacked: the zip goes into the world as it is."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "pack.mcmeta" not in names:
                inside = any(n.endswith("/pack.mcmeta") for n in names)
                return (
                    "Its pack.mcmeta is in a folder inside the zip, so Minecraft won't load it. "
                    "Zip the files inside that folder instead."
                    if inside
                    else "It has no mod, plugin or datapack information inside."
                )
            if not any(n.startswith("data/") for n in names):
                return (
                    "It's a resource pack. Players add those in their own game, not on the server."
                )
            if zf.getinfo("pack.mcmeta").file_size > 64 * 1024:
                return "Its pack.mcmeta is too big to be real."
            meta = json.loads(zf.read("pack.mcmeta").decode("utf-8-sig"))
    except (zipfile.BadZipFile, ValueError, UnicodeDecodeError):
        return "Its pack.mcmeta can't be read."
    if not isinstance(meta, dict) or not isinstance(meta.get("pack"), dict):
        return "Its pack.mcmeta can't be read."
    return None


class ModManager:
    def __init__(self, config, bus: EventBus, db, server, backups=None):
        self.config = config
        self.bus = bus
        self.db = db
        self.server = server
        self.backups = backups
        self.modrinth = ModrinthClient(config)
        self.hangar = HangarClient(config)
        self.github = GitHubReleases(config)
        self.curseforge = CurseForgeClient(config)
        self.deps = DependencyResolver(self)
        self._cache: tuple[float, list[ModInfo]] | None = None

    # ------------------------------------------------------------------
    # the server's type
    # ------------------------------------------------------------------
    @property
    def server_type(self):
        return self.config.server_type

    @property
    def word(self) -> str:
        """ "mod" or "plugin", for messages."""
        return "plugin" if self.server_type.content == "plugins" else "mod"

    def _require_content(self) -> None:
        if not self.server_type.has_content:
            raise ModError(
                f"{self.server_type.name} servers don't use mods or plugins. Change the "
                "server type in Server settings to add some."
            )

    def _check_loader(self, info: ModInfo, discarded: bool = True) -> None:
        """Refuse a jar built for another loader, in plain words."""
        reason = wrong_loader_reason(info, self.server_type.accepts, self.server_type.name)
        if reason:
            raise ModError(reason + (" It wasn't installed." if discarded else ""))

    # ------------------------------------------------------------------
    # where add-ons come from
    # ------------------------------------------------------------------
    def site(self, name: str) -> AddonSource:
        """The add-on source called ``name``, when this server can use it."""
        if name == "hangar" and not self.server_type.hangar_platform:
            raise ModError(f"Hangar has plugins for Paper servers, not {self.server_type.name}.")
        if name == "curseforge" and not self.curseforge.loaders:
            raise ModError(
                f"CurseForge here has mods for mod loaders, not {self.server_type.name}."
            )
        sites: dict[str, AddonSource] = {
            "modrinth": self.modrinth,
            "hangar": self.hangar,
            "github": self.github,
            "curseforge": self.curseforge,
        }
        if name not in sites:
            raise ModError("That add-on source isn't known.")
        return sites[name]

    def record_of(self, mod: ModInfo) -> dict[str, Any]:
        return self.db.get_setting(f"mod_source:{mod.mod_id}") or {}

    def origin(self, mod: ModInfo) -> tuple[str, str | None]:
        """Where ``mod`` came from and its project there. Add-ons noted
        before Hangar was added have no "source": they are Modrinth's."""
        record = self.record_of(mod)
        source = record.get("source") or "modrinth"
        if source == "modrinth":
            return source, record.get("modrinth_project") or mod.modrinth_project or mod.mod_id
        return source, record.get("project")

    def remember(self, mod_id: str, source: str, version: dict[str, Any] | None = None) -> None:
        """Note where an add-on came from, for its updates."""
        record: dict[str, Any] = {"source": source}
        if version:
            record.update(
                project=version.get("project_id"),
                version_id=version.get("version_id"),
                version=version.get("version_number"),
            )
            if source == "modrinth":
                record["modrinth_project"] = version.get("project_id")
        self.db.set_setting(f"mod_source:{mod_id}", record)

    async def match_on_hangar(self, mod: ModInfo) -> None:
        """A jar that came from nowhere the app knows, looked up on Hangar
        by its SHA-256 once, so a plugin found there gets its updates."""
        try:
            project = await self.hangar.by_hash(mod.sha256)
        except SourceError:
            return  # asked again next time
        if project:
            self.remember(mod.mod_id, "hangar", {"project_id": f"hangar:{project}"})
        else:  # still asked on Modrinth by its id, as before
            self.db.set_setting(f"mod_source:{mod.mod_id}", {"hangar_checked": True})

    async def match_on_curseforge(self, mods: list[ModInfo]) -> None:
        """Jars that came from nowhere the app knows, looked up on CurseForge
        by fingerprint, all in one call, once. A match counts only when its
        sha1 is the jar's too, so a mod is never taken for another."""
        fresh = [mod for mod in mods if not self.record_of(mod)]
        if not fresh or not self.curseforge.loaders:
            return
        prints = await asyncio.to_thread(lambda: [file_prints(mod.path) for mod in fresh])
        try:
            found = await self.curseforge.by_fingerprint([p for p, _ in prints])
        except SourceError:
            return  # asked again next time
        for mod, (print_, sha1) in zip(fresh, prints, strict=True):
            version = found.get(print_)
            if version and version["file"]["sha1"] == sha1:
                self.remember(mod.mod_id, "curseforge", version)
            else:  # still asked on Modrinth by its id, as before
                self.db.set_setting(f"mod_source:{mod.mod_id}", {"curseforge_checked": True})

    def needs_you(self) -> list[dict[str, Any]]:
        return self.db.get_setting(NEEDS_YOU) or []

    def need_you(self, need: dict[str, Any]) -> None:
        """List a mod under "Needs you" (once)."""
        rest = [n for n in self.needs_you() if n["project"] != need["project"]]
        self.db.set_setting(NEEDS_YOU, [*rest, need])

    def done_needing(self, project: str) -> bool:
        """Take ``project`` off "Needs you"; whether it was there."""
        before = self.needs_you()
        rest = [n for n in before if n["project"] != project]
        self.db.set_setting(NEEDS_YOU, rest)
        return len(rest) != len(before)

    def source_of(self, mod: ModInfo) -> str | None:
        """The source the app installed ``mod`` from, None for a jar it
        didn't add (copied into the folder by hand). Never a guess."""
        record = self.record_of(mod)
        return record.get("source") or ("modrinth" if record.get("modrinth_project") else None)

    # ------------------------------------------------------------------
    # directories
    # ------------------------------------------------------------------
    @property
    def mods_dir(self) -> Path:
        path = self.config.mods_dir
        if not self.config.server_dir_configured:
            raise ModError(
                "The Minecraft server folder isn't set. Set server.directory in config/config.yaml."
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def backup_dir(self) -> Path:
        path = self.config.mod_backup_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def trash_dir(self) -> Path:
        path = self.config.mod_trash_dir
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def scan(self, use_cache: bool = False, max_age: float = 10.0) -> list[ModInfo]:
        if use_cache and self._cache and time.time() - self._cache[0] < max_age:
            return self._cache[1]
        if not self.server_type.has_content:
            return []
        mods: list[ModInfo] = []
        for entry in sorted(self.mods_dir.iterdir()):
            if not entry.is_file() or entry.is_symlink():
                continue
            name = entry.name
            if not (name.endswith(".jar") or name.endswith(".jar" + DISABLED_SUFFIX)):
                continue
            mods.append(read_mod_jar(entry))
        self._cache = (time.time(), mods)
        return mods

    def provided(self, mods: list[ModInfo]) -> tuple[dict[str, ModInfo], bool]:
        """Every mod id the server loads, mapped to what brings it: the jars
        in ``mods`` (an enabled copy first), the mods they carry inside, and
        the mods that come with the server software (its libraries folder).

        The second value is False when a needed id wasn't found and the
        libraries folder isn't there yet (Fabric makes it on the first
        start), so whether that id is really missing can't be told.
        """
        index: dict[str, ModInfo] = {}
        for mod in sorted(mods, key=lambda m: not m.enabled):
            for carried in (mod, *mod.bundled):
                index.setdefault(carried.mod_id, carried)
        needed = {d.mod_id for m in mods if m.enabled for d in m.dependencies} - PLATFORM_IDS
        if self.server_type.content != "mods" or needed <= index.keys():
            return index, True
        libraries = self.config.server_dir / "libraries"
        if not libraries.is_dir():
            return index, False
        for carried in read_library_mods(libraries):
            index.setdefault(carried.mod_id, carried)
        return index, True

    def platform_versions(self) -> dict[str, str | None]:
        """The versions the server's console reported, by the id mods use to
        depend on them. Java and other loaders' ids aren't known."""
        return {
            "minecraft": self.server.mc_version,
            self.server_type.loader_mod_id or "": self.server.loader_version,
        }

    def list_installed(self, use_cache: bool = False) -> list[dict[str, Any]]:
        mods = self.scan(use_cache=use_cache)
        issues = self.check_all(mods)
        by_file = {i["filename"]: i for i in issues["per_mod"]}
        result = []
        for mod in mods:
            row = mod.to_dict()
            row["issues"] = by_file.get(mod.filename, {}).get("issues", [])
            row["status"] = by_file.get(mod.filename, {}).get("status", "ok")
            latest = self.db.query_one(
                "SELECT version, created_at FROM mod_versions WHERE server_id = ? AND mod_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (self.server.server_id, mod.mod_id),
            )
            row["has_history"] = bool(latest)
            source = self.source_of(mod)
            row["source"] = source
            row["source_name"] = LABELS.get(source or "")
            # Only a site that lists the file has looked at it; a file or a
            # GitHub release was checked by nobody.
            row.update(checked(source))
            update = self.db.get_setting(f"mod_update:{mod.mod_id}")
            if update and update.get("installed_version") == mod.version:
                row["update_available"] = update
            else:
                row["update_available"] = None
            result.append(row)
        return result

    def find(self, filename: str) -> ModInfo:
        name = safe_filename(filename)
        for mod in self.scan():
            if mod.filename == name:
                return mod
        raise ModError(f"{filename} isn't in the {self.server_type.content} folder.")

    def find_by_id(self, mod_id: str) -> ModInfo | None:
        for mod in self.scan():
            if mod.mod_id == mod_id:
                return mod
        return None

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def check_all(self, mods: list[ModInfo] | None = None) -> dict[str, Any]:
        """Dependency, duplicate and compatibility checks across the folder.

        Limits are stated openly: this reads declared metadata only. Two mods
        can still conflict at runtime with nothing in their metadata to say so,
        and this checker cannot see that.
        """
        mods = mods if mods is not None else self.scan()
        enabled = [m for m in mods if m.enabled]
        installed, software_known = self.provided(enabled)
        mc_version = self.server.mc_version
        loader_version = self.server.loader_version

        per_mod: list[dict[str, Any]] = []
        problems: list[dict[str, Any]] = checks.duplicate_ids(enabled)
        for mod in mods:
            issues = checks.jar_issues(mod, self.server_type)
            if not mod.enabled:
                per_mod.append(
                    {
                        "filename": mod.filename,
                        "mod_id": mod.mod_id,
                        "status": "disabled",
                        "issues": issues,
                    }
                )
                continue
            if mod.environment == "client":
                issues.append(checks.client_only_issue(mod))
            else:
                issues += checks.dependency_issues(
                    mod, installed, self.platform_versions(), software_known
                )
                issues += checks.breaks_issues(mod, installed)
            per_mod.append(
                {
                    "filename": mod.filename,
                    "mod_id": mod.mod_id,
                    "status": checks.status_of(issues),
                    "issues": issues,
                }
            )
            problems.extend(
                {**i, "mod": mod.name, "filename": mod.filename}
                for i in issues
                if i["severity"] == "error"
            )

        return {
            "per_mod": per_mod,
            "problems": problems,
            "minecraft_version": mc_version,
            "fabric_loader": loader_version,
            "loader_version": loader_version,
            "loader_name": self.server_type.loader_name,
            "checked_at": time.time(),
            "claim": (
                "No problems found in what the files say"
                if not problems
                else f"{len(problems)} problem(s) found in what the files say"
            ),
            "claim_note": (
                "This only reads what each file says it needs. Nothing was run or tested, "
                "so two files can still clash in ways neither one mentions."
            ),
            "limitations": [
                "Only what each file declares is checked ("
                + ", ".join(self.server_type.metadata_files)
                + ").",
                f"{self.word.capitalize()}s can still clash with nothing declared in their files.",
                "The Minecraft and loader versions are read from the server console, so they're "
                "only known after the server has started once.",
            ],
        }

    def missing_dependencies(self) -> list[dict[str, Any]]:
        checks = self.check_all()
        return [p for p in checks["problems"] if p["kind"] == "missing_dependency"]

    # ------------------------------------------------------------------
    # history
    # ------------------------------------------------------------------
    def compatibility_verdict(
        self, modrinth_version: dict | None, mod: ModInfo | None = None
    ) -> dict[str, Any]:
        """How confident we are that a mod will work here.

        Four verdicts, and none of them is "it works":

          verified_metadata  the file's own information declares this
                             Minecraft version, and Modrinth agrees
          likely             Modrinth lists this Minecraft version, but the
                             file's own information could not confirm it
          unknown            the server's Minecraft version has not been
                             observed yet, so nothing can be compared
          incompatible       a declared requirement is not met

        Listing on Modrinth is a publisher's claim, not a test result, so it
        never produces better than "likely" on its own.
        """
        mc_version = self.server.mc_version
        if not mc_version:
            return {
                "verdict": "unknown",
                "detail": (
                    "The server's Minecraft version hasn't been seen yet. It's read from "
                    "the console the first time the server starts, so this can't be checked yet."
                ),
            }
        declared = None
        mc_range = mod.minecraft_range if mod else None
        if mc_range:
            syntax = next(
                (d.syntax for d in (mod.dependencies if mod else []) if d.mod_id == "minecraft"),
                "fabric",
            )
            declared = range_satisfies(mc_version, mc_range, syntax)
        listed = None
        if modrinth_version:
            listed = mc_version in (modrinth_version.get("game_versions") or [])
        if declared is False:
            return {
                "verdict": "incompatible",
                "detail": f"This {self.word} says it needs Minecraft {mc_range}, "
                f"but this server runs {mc_version}.",
            }
        if declared is True and listed is not False:
            return {
                "verdict": "verified_metadata",
                "detail": f"This {self.word} says it works with {mc_range}, which "
                f"includes {mc_version}. That's what the file says, not a test.",
            }
        if listed:
            return {
                "verdict": "likely",
                "detail": f"Modrinth lists this build for Minecraft {mc_version}, but the "
                f"file's own information doesn't confirm it. That's what the "
                f"author says, not a test.",
            }
        return {
            "verdict": "unknown",
            "detail": "Neither the file's own information nor Modrinth confirms this "
            "Minecraft version.",
        }

    def record(
        self,
        action: str,
        user: str,
        mod: ModInfo | None = None,
        *,
        mod_id: str | None = None,
        mod_name: str | None = None,
        old_version: str | None = None,
        new_version: str | None = None,
        source: str | None = None,
        sha256: str | None = None,
        result: str = "ok",
        detail: str | None = None,
    ) -> int:
        return self.db.insert(
            "mod_history",
            {
                "server_id": self.server.server_id,
                "ts": time.time(),
                "user": user,
                "action": action,
                "mod_id": mod_id or (mod.mod_id if mod else None),
                "mod_name": mod_name or (mod.name if mod else None),
                "old_version": old_version,
                "new_version": new_version or (mod.version if mod else None),
                "source": source,
                "sha256": sha256 or (mod.sha256 if mod else None),
                "result": result,
                "detail": detail,
            },
        )

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM mod_history WHERE server_id = ? ORDER BY ts DESC LIMIT ?",
            (self.server.server_id, limit),
        )

    # ------------------------------------------------------------------
    # archive / versions
    # ------------------------------------------------------------------
    def archive(self, path: Path, mod: ModInfo, source: str | None = None) -> Path:
        """Copy a jar into mod-backups/<mod_id>/<timestamp>-<filename>."""
        folder = self.backup_dir / (mod.mod_id or "unknown")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base_name = path.name.replace(DISABLED_SUFFIX, "")
        target = folder / f"{stamp}-{base_name}"
        # Two archives can land in the same second (pre-update then post-update,
        # or archive-then-rollback). Never let the second one overwrite the
        # first: an archive that silently changed content would defeat the
        # SHA-256 check that rollback relies on.
        counter = 1
        while target.exists():
            target = folder / f"{stamp}-{counter}-{base_name}"
            counter += 1
        shutil.copy2(path, target)
        self.db.insert(
            "mod_versions",
            {
                "server_id": self.server.server_id,
                "mod_id": mod.mod_id,
                "version": mod.version,
                "filename": base_name,
                "archive_path": str(target),
                "sha256": mod.sha256 or sha256_file(target),
                "source": source,
                "created_at": time.time(),
            },
        )
        return target

    def versions_for(self, mod_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM mod_versions WHERE server_id = ? AND mod_id = ? ORDER BY created_at DESC",
            (self.server.server_id, mod_id),
        )
        current = self.find_by_id(mod_id)
        for row in rows:
            row["is_current"] = bool(current and current.version == row["version"])
            row["exists"] = Path(row["archive_path"]).is_file()
        return rows

    # ------------------------------------------------------------------
    # write operations
    # ------------------------------------------------------------------
    def require_server_offline(self, action: str) -> None:
        if self.server.state.value == "RESTART_PENDING":
            # The countdown could restart the server while a jar is half-written.
            raise ModError(f"An automatic restart is counting down. Cancel it before you {action}.")
        if self.server.running:
            raise ModError(
                f"The server is {self.server.state.value.lower()}. Stop it before you {action}: "
                f"it keeps {self.word} files open while it runs, and changing them mid-game "
                "can damage the world."
            )

    async def _emit(self, type_: str, message: str, level: str = "info", **data) -> None:
        await self.bus.publish(Event(type=type_, message=message, level=level, data=data))

    def preview_install(self, mod_id_or_slug: str) -> dict[str, Any]:
        existing = self.find_by_id(mod_id_or_slug)
        return {"already_installed": existing.to_dict() if existing else None}

    async def install_from_site(
        self,
        project: str,
        user: str,
        version_id: str | None = None,
        minecraft_version: str | None = None,
        allow_replace: bool = False,
        install_dependencies: bool = False,
        depth: int = 0,
    ) -> dict[str, Any]:
        """Download and install a mod or plugin from Modrinth, or from
        Hangar or CurseForge when ``project`` is "hangar:<id>" or
        "curseforge:<id>". Returns what changed. A CurseForge mod its author
        keeps from other apps is put under "Needs you" (Blocked)."""
        self._require_content()
        self.require_server_offline(f"install a {self.word}")
        mc_version = minecraft_version or self.server.mc_version
        kind = self.server_type.name
        source, plain = split(project)
        if source == "github":
            raise ModError("Add a file from GitHub with Add from a link.")
        site = self.site(source)

        version: dict[str, Any] | None
        if version_id:
            version = await site.version(version_id)
        else:
            version = await site.latest_for(plain, mc_version)
        if not version:
            raise ModError(
                f"There's no {kind} version of '{project}'"
                + (f" for Minecraft {mc_version}." if mc_version else ".")
            )
        if mc_version and mc_version not in version["game_versions"]:
            raise ModError(
                f"{version['version_number']} is for Minecraft "
                f"{', '.join(version['game_versions'][:6])}, not {mc_version}. "
                "Pick another version."
            )
        if not self.server_type.accepts_any([loader.lower() for loader in version["loaders"]]):
            raise ModError(
                f"That file is for {', '.join(version['loaders'])}, not {kind}, "
                "so it wasn't installed."
            )

        filename = version["file"]["filename"]
        target = safe_join(self.mods_dir, filename, allowed_extensions=JAR_EXT)
        if target.exists() and not allow_replace:
            raise ModError(
                f"{filename} is already in the {self.server_type.content} folder, so nothing "
                "was changed. Use Update, or confirm that you want to replace it."
            )

        try:
            data, filename, sha256 = await site.download(version)
        except Blocked as exc:
            self.need_you(exc.need)
            raise
        refuse_known_bad(data, filename)
        twin = next((m for m in self.scan() if m.sha256 == sha256 and m.path != str(target)), None)
        if twin:
            # The same file from the other site, or under another name.
            raise ModError(
                f"That file is already installed as {twin.filename}, so nothing was changed."
            )
        temp = self.mods_dir / f".{filename}.part"
        temp.write_bytes(data)
        try:
            info = read_mod_jar(temp, compute_hash=False)
            self._check_loader(info)
            here = depth and self.find_by_id(info.mod_id)
            if here:  # a needed mod already brought by a jar from elsewhere
                raise AlreadyHere(f"{info.mod_id} is already installed as {here.filename}.")
            replaced = None
            if target.exists():
                assert_not_symlink(target)
                old = read_mod_jar(target)
                self.archive(target, old, source="replaced-on-install")
                replaced = old.to_dict()
                target.unlink()
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

        installed = read_mod_jar(target)
        installed.sha256 = sha256
        self.archive(target, installed, source=version["file"]["url"])
        self.record(
            "install",
            user,
            installed,
            new_version=installed.version,
            source=version["file"]["url"],
            sha256=sha256,
            detail=f"{LABELS[source]} {version['version_number']} ({version['release_type']})",
        )
        self.remember(
            installed.mod_id, source, {**version, "project_id": version.get("project_id") or plain}
        )
        self.done_needing(version.get("project_id") or plain)
        self._cache = None
        await self._emit(
            "mod_installed",
            f"Installed {installed.name} {installed.version}",
            level="success",
            mod=installed.to_dict(),
            source=source,
        )

        report: dict[str, Any] = {
            "installed": installed.to_dict(),
            # Writing a jar into the folder is not the same as the server
            # loading it. That is only known once the server has started and
            # reported it, so it stays unverified until then.
            "loaded_by_minecraft": "not verified",
            "loaded_detail": (
                f"The file is in the {self.server_type.content} folder. Start the server "
                "to find out whether it loads."
            ),
            "compatibility": self.compatibility_verdict(version, installed),
            "replaced": replaced,
            "sha256": sha256,
            "source": version["file"]["url"],
            "release_type": version["release_type"],
            **checked(source),
            "dependencies_required": [],
            "dependencies_installed": [],
            # Hangar's: the ones not on Hangar (a link the owner clicks; never
            # downloaded) and the optional ones (listed, not added).
            "dependencies_links": [
                {"name": d.get("name"), "url": d["url"], "required": d["type"] == "required"}
                for d in version.get("dependencies") or []
                if d.get("url") and not d.get("project_id")
            ],
            "dependencies_optional": [
                {"name": d.get("name"), "project": d["project_id"]}
                for d in version.get("dependencies") or []
                if d["type"] == "optional" and d.get("project_id")
            ],
            "needs_you": [],
        }

        if source in ("hangar", "curseforge"):
            needs = [
                d
                for d in version.get("dependencies") or []
                if d["type"] == "required" and d.get("project_id") and not self._has(d)
            ]
            if install_dependencies:
                done = await self._add_needs(needs, user, depth)
                report["dependencies_installed"] = [r for r in done if r["result"] == "installed"]
                report["dependencies_failed"] = [r for r in done if r["result"] == "failed"]
                report["needs_you"] = [r["need"] for r in done if r["result"] == "needs_you"]
                needs = [d for d in needs if not self._has(d)]
            report["dependencies_required"] = [
                {"mod_id": d["project_id"], "title": d.get("name"), "installed": False}
                for d in needs
            ]
        # Modrinth's are planned from this version's metadata and installed
        # through the same verified path; see dependencies.py.
        elif install_dependencies:
            outcome = await self.deps.install(None, user)
            report["dependencies_installed"] = [
                r for r in outcome["results"] if r["result"] == "installed"
            ]
            report["dependencies_failed"] = [
                r for r in outcome["results"] if r["result"] == "failed"
            ]
            report["dependencies_required"] = [
                {"mod_id": g["mod_id"], "title": g["name"], "installed": False}
                for g in outcome["still_missing"]
            ]
        else:
            report["dependencies_required"] = [
                {
                    "mod_id": g["mod_id"],
                    "title": g["name"],
                    "installed": False,
                    "range_text": g["range_text"],
                }
                for g in self.deps.analyse()["items"]
                if g["status"] == "missing" and g["kind"] == "required"
            ]
        return report

    def _has(self, need: dict[str, Any]) -> bool:
        """Whether a required add-on is here already: added from that
        project, or a jar of that name."""
        name = str(need.get("name") or "").lower()
        site = split(need["project_id"])[0]
        return any(
            mod.mod_id.lower() == name or self.origin(mod) == (site, need["project_id"])
            for mod in self.scan()
        )

    async def _add_needs(
        self, needs: list[dict[str, Any]], user: str, depth: int
    ) -> list[dict[str, Any]]:
        """Add each required Hangar plugin or CurseForge mod, with a version
        that fits this server, and theirs in turn. One that fails is
        reported, not hidden; one its author keeps from other apps waits
        under "Needs you" while the rest go ahead."""
        results: list[dict[str, Any]] = []
        for need in needs:
            title = need.get("name") or need["project_id"]
            if self._has(need):  # added for another one just now
                continue
            if depth >= MAX_NEEDS_DEPTH:
                results.append(
                    {
                        "title": title,
                        "result": "failed",
                        "detail": "The chain of needs is too long.",
                    }
                )
                continue
            try:
                got = await self.install_from_site(
                    need["project_id"], user, install_dependencies=True, depth=depth + 1
                )
            except AlreadyHere:
                continue
            except Blocked as exc:
                results.append({"title": exc.need["name"], "result": "needs_you", "need": exc.need})
                continue
            except (ModError, SourceError, PathSafetyError) as exc:
                results.append({"title": title, "result": "failed", "detail": str(exc)})
                continue
            installed = got["installed"]
            results.append(
                {
                    "title": installed.get("name") or title,
                    "result": "installed",
                    "version": installed["version"],
                }
            )
            results += got["dependencies_installed"] + got.get("dependencies_failed", [])
            results += [
                {"title": n["name"], "result": "needs_you", "need": n} for n in got["needs_you"]
            ]
        return results

    async def install_local_file(
        self,
        filename: str,
        data: bytes,
        user: str,
        allow_replace: bool = False,
        source: str = "file",
        version: dict[str, Any] | None = None,
        ask_first: bool = False,
    ) -> dict[str, Any]:
        """Install a jar or datapack from the PC, or from a GitHub link
        (``source`` and ``version`` say where it came from). Same rules as
        a Modrinth install; it is read to check it fits this server first.
        ``ask_first``: a jar no site lists. Every check runs, then it stops
        with the warning to confirm, and nothing is written."""
        self.require_server_offline(f"install a {self.word}")
        safe_filename(filename, FILE_EXT)
        if not data.startswith(b"PK"):
            raise ModError("That file isn't a .jar or .zip file.")
        if len(data) > MAX_FILE_BYTES:
            raise ModError("That file is bigger than the 300 MB limit.")
        refuse_known_bad(data, filename)
        if is_datapack(data):
            return await self._install_datapack(filename, data, user, allow_replace, source)
        self._require_content()
        if filename.lower().endswith(".zip"):
            raise ModError(
                f"{filename} can't be added. "
                + (datapack_problem(data) or f"It's a {self.word}, so its name has to end in .jar.")
            )
        target = safe_join(self.mods_dir, filename, allowed_extensions=JAR_EXT)
        if target.exists() and not allow_replace:
            raise ModError(f"{filename} is already there, so nothing was changed.")
        temp = self.mods_dir / f".{filename}.part"
        temp.write_bytes(data)
        try:
            info = read_mod_jar(temp, compute_hash=True)
            self._check_loader(info)
            if ask_first:
                raise FormError(
                    {"confirm": UNKNOWN_FILE, "sha512": hashlib.sha512(data).hexdigest()}
                )
            if target.exists():
                old = read_mod_jar(target)
                self.archive(target, old, source="replaced-on-upload")
                target.unlink()
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        installed = read_mod_jar(target)
        where = (version or {}).get("file", {}).get("url") or "upload"
        self.archive(target, installed, source="uploaded" if source == "file" else where)
        self.record("install", user, installed, source=where, detail=f"Added {filename}")
        self.remember(installed.mod_id, source, version)
        if source == "curseforge" and version:
            self.done_needing(version["project_id"])
        self._cache = None
        await self._emit(
            "mod_installed",
            f"Installed {installed.name} {installed.version} from {LABELS[source]}",
            level="success",
            mod=installed.to_dict(),
            source=source,
        )
        return {
            "installed": {**installed.to_dict(), "kind": self.word},
            "source": source,
            **checked(source),
            "loaded_by_minecraft": "not verified",
            "loaded_detail": (
                f"The file is in the {self.server_type.content} folder. Start the server "
                "to find out whether it loads."
            ),
        }

    @property
    def datapacks_dir(self) -> Path:
        """The main world's datapacks folder (Paper's split layout keeps
        its datapacks there too)."""
        props = self.config.server_dir / FILENAME
        values = PropertiesFile.read(props).values() if props.is_file() else {}
        return world_folder(self.config.server_dir, values, self.server_type) / "datapacks"

    async def _install_datapack(
        self, filename: str, data: bytes, user: str, allow_replace: bool, source: str
    ) -> dict[str, Any]:
        if self.server_type.edition != "java":
            raise ModError(f"{self.server_type.name} servers don't use Java datapacks.")
        folder = self.datapacks_dir
        if not filename.lower().endswith(".zip"):
            raise ModError(f"{filename} is a datapack, so its name has to end in .zip.")
        folder.mkdir(parents=True, exist_ok=True)
        target = safe_join(folder, filename, allowed_extensions={".zip"})
        if target.exists() and not allow_replace:
            raise ModError(f"{filename} is already in the world's datapacks folder.")
        temp = folder / f".{filename}.part"
        temp.write_bytes(data)
        temp.replace(target)
        name = Path(filename).stem
        self.record(
            "install",
            user,
            mod_id=name.lower(),
            mod_name=name,
            source="upload" if source == "file" else source,
            sha256=hashlib.sha256(data).hexdigest(),
            detail=f"Datapack {filename} into {folder.parent.name}/datapacks",
        )
        await self._emit(
            "mod_installed", f"Added the datapack {name}", level="success", source=source
        )
        return {
            "installed": {"filename": filename, "name": name, "kind": "datapack"},
            "source": source,
            "checked_by": None,
            "loaded_by_minecraft": "not verified",
            "loaded_detail": (
                f"The datapack is in {folder.parent.name}/datapacks. Minecraft turns it on "
                "when the server starts; /datapack list shows whether it did."
            ),
        }

    def removal_impact(self, filename: str) -> dict[str, Any]:
        """What breaks if this mod goes away."""
        mod = self.find(filename)
        dependents = []
        for other in self.scan():
            if other.filename == mod.filename or not other.enabled:
                continue
            for dep in other.dependencies:
                if dep.mod_id == mod.mod_id and dep.kind == "depends":
                    dependents.append(
                        {
                            "name": other.name,
                            "mod_id": other.mod_id,
                            "filename": other.filename,
                            "requires": dep.version_range,
                        }
                    )
        return {
            "mod": mod.to_dict(),
            "dependents": dependents,
            "warning": (
                f"{len(dependents)} other {self.word}(s) say they need {mod.mod_id}. "
                "Removing it may stop the server from starting."
            )
            if dependents
            else None,
        }

    async def remove(self, filename: str, user: str, backup: bool = True) -> dict[str, Any]:
        self.require_server_offline(f"remove a {self.word}")
        mod = self.find(filename)
        path = Path(mod.path)
        assert_not_symlink(path)
        if not is_inside(self.mods_dir, path):
            raise PathSafetyError(f"That file isn't inside the {self.server_type.content} folder.")
        archived = None
        if backup:
            archived = str(self.archive(path, mod, source="removed"))
        stamp = time.strftime("%Y%m%d-%H%M%S")
        trash_target = self.trash_dir / f"{stamp}-{path.name}"
        shutil.move(str(path), str(trash_target))
        self.record(
            "remove",
            user,
            mod,
            old_version=mod.version,
            source="local",
            detail=f"Moved to {trash_target.name}",
        )
        self._cache = None
        await self._emit(
            "mod_removed", f"Removed {mod.name} {mod.version}", level="warn", mod=mod.to_dict()
        )
        return {"removed": mod.to_dict(), "archived": archived, "trash": str(trash_target)}

    async def set_enabled(self, filename: str, enabled: bool, user: str) -> dict[str, Any]:
        self.require_server_offline(f"turn a {self.word} on or off")
        mod = self.find(filename)
        path = Path(mod.path)
        assert_not_symlink(path)
        if enabled and mod.enabled:
            return {"mod": mod.to_dict(), "changed": False}
        if not enabled and not mod.enabled:
            return {"mod": mod.to_dict(), "changed": False}
        if enabled:
            new_name = path.name[: -len(DISABLED_SUFFIX)]
        else:
            new_name = path.name + DISABLED_SUFFIX
        target = self.mods_dir / new_name
        if not is_inside(self.mods_dir, target):
            raise PathSafetyError(
                f"Refusing to write outside the {self.server_type.content} folder."
            )
        if target.exists():
            raise ModError(f"{new_name} is already in the {self.server_type.content} folder.")
        path.rename(target)
        self._cache = None
        updated = read_mod_jar(target)
        action = "enable" if enabled else "disable"
        self.record(action, user, updated, detail=f"{path.name} -> {new_name}")
        await self._emit(
            f"mod_{action}d",
            f"{'Enabled' if enabled else 'Disabled'} {updated.name}",
            mod=updated.to_dict(),
        )
        return {"mod": updated.to_dict(), "changed": True}

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------
    def _target_version(self) -> str | None:
        """The Minecraft version add-on updates are for: the one the console
        reported or, right after a version change, the one being set up (so
        the updates offered fit the new version, not the old one)."""
        pending = self.server.pending_version or {}
        return pending.get("minecraft_version") or self.server.mc_version

    async def check_updates(
        self, minecraft_version: str | None = None, background: bool = False
    ) -> list[dict[str, Any]]:
        """Ask the site each mod or plugin came from (turned-off ones too,
        so their updates are offered; they stay off) and save what it says
        for the Mods list. Returns the updates found. The ``background``
        check every few hours leaves CurseForge out: our relay is asked
        only when someone presses Check or opens Update all."""
        found, _ = await self._check(minecraft_version or self._target_version(), not background)
        return [entry for _, entry in found]

    async def _check(
        self, mc_version: str | None, relay: bool = True
    ) -> tuple[list[tuple[ModInfo, dict[str, Any]]], list[tuple[ModInfo, str]]]:
        """Each mod with a newer version and its saved update, and each mod
        its site couldn't be asked about with the reason. Several are asked
        at once."""
        mods = self.scan()
        if relay:
            await self.match_on_curseforge(mods)
        asking = asyncio.Semaphore(PLAN_CHECKS_AT_ONCE)

        async def ask(
            mod: ModInfo,
        ) -> tuple[str, dict[str, Any] | None | Exception, str | None]:
            async with asking:
                if not self.record_of(mod) and self.server_type.hangar_platform:
                    await self.match_on_hangar(mod)
                source, project = self.origin(mod)
                if source == "file" or not project:
                    return "", SourceError(NO_UPDATES), None
                if source == "curseforge" and not relay:
                    return project, SourceError("Checked when you press Check."), None
                with savedlists.watch() as used:
                    try:
                        site = self.site(source)
                        latest = await site.latest_for(project, mc_version, like=mod.filename)
                    except (SourceError, ModError) as exc:
                        return project, exc, None
                # "From 3 days ago, couldn't reach Modrinth." when the
                # answer is the site's saved list.
                return project, latest, savedlists.problem(used)[1]

        answers = await asyncio.gather(*(ask(mod) for mod in mods))
        found: list[tuple[ModInfo, dict[str, Any]]] = []
        unchecked: list[tuple[ModInfo, str]] = []
        for mod, (project, latest, saved) in zip(mods, answers, strict=True):
            entry = None
            if isinstance(latest, Exception):
                unchecked.append((mod, str(latest)))
                continue
            if latest:
                entry = self._note_update(mod, project, latest, saved)
            if latest and entry and entry["source"] == "curseforge" and not latest["file"]["url"]:
                # Its author keeps CurseForge files from other apps: no
                # Update button for it, only where to get it.
                self.db.set_setting(f"mod_update:{mod.mod_id}", None)
                need = await self.curseforge.need_for(latest)
                reason = BLOCKED_UPDATE.format(version=entry["latest_version"])
                where = (
                    f" Get it from {need['page']}" if need["page"] else " Get it from CurseForge."
                )
                unchecked.append((mod, reason + where))
                continue
            if entry:
                found.append((mod, entry))
            elif saved:
                # A saved list is never "up to date": nobody checked just now.
                unchecked.append((mod, f"{saved} The saved list shows nothing newer."))
        if any(saved is None and not isinstance(latest, Exception) for _, latest, saved in answers):
            self.db.set_setting("mod_updates_checked_at", time.time())
        return found, unchecked

    def _note_update(
        self, mod: ModInfo, project: str, latest: dict[str, Any], saved: str | None = None
    ) -> dict[str, Any] | None:
        """Save what the site said about ``mod`` for the Mods list; returns
        the update, or None when it is up to date. ``saved`` says how old
        the list was when it came from a saved copy."""
        record = self.record_of(mod)
        if (
            not latest["version_number"]
            or latest["version_number"] == mod.version
            or self._is_installed_file(mod, latest)
            # A release tag ("v1.3") rarely matches the jar's own version.
            or (
                record.get("source") == "github"
                and latest["version_id"] == record.get("version_id")
            )
        ):
            if not saved:
                self.db.set_setting(f"mod_update:{mod.mod_id}", None)
            return None
        entry = {
            "mod_id": mod.mod_id,
            "name": mod.name,
            "filename": mod.filename,
            "installed_version": mod.version,
            "latest_version": latest["version_number"],
            "version_id": latest["version_id"],
            "release_type": latest.get("release_type"),
            "game_versions": latest.get("game_versions", []),
            "project": project,
            "source": self.origin(mod)[0],
            "changelog": (latest.get("changelog") or "")[:400],
            "saved": saved,
        }
        self.db.set_setting(f"mod_update:{mod.mod_id}", entry)
        return entry

    @staticmethod
    def _is_installed_file(mod: ModInfo, version: dict[str, Any]) -> bool:
        """Is ``version``'s file the very jar that is installed? Modrinth's
        version name often differs from the one inside the jar (Sodium's
        "mc1.21.1-0.6.0-fabric" is "0.6.0+mc1.21.1" in the jar), so the
        names alone would offer the same file again and again."""
        file = version.get("file") or {}
        for algorithm in ("sha512", "sha256", "sha1"):
            if file.get(algorithm):
                with open(mod.path, "rb") as jar:
                    digest = hashlib.file_digest(jar, algorithm).hexdigest()
                return digest == file[algorithm].lower()
        return False

    async def update_plan(self) -> dict[str, Any]:
        """What "Update all" would change: every mod or plugin, turned off
        ones too, that its site has a newer version of. One that can't be
        checked (its site is down, or it came from a file) is listed in
        ``unchecked`` with the reason, never left out as if it were up to date. What it finds is saved for the
        Mods list too, as a check for updates would."""
        found, unchecked = await self._check(self._target_version())

        def row(mod: ModInfo) -> dict[str, Any]:
            return {"mod_id": mod.mod_id, "name": mod.name or mod.mod_id, "filename": mod.filename}

        keys = ("installed_version", "latest_version", "version_id", "project", "source", "saved")
        return {
            "updates": [
                {**row(mod), "enabled": mod.enabled, **{key: entry[key] for key in keys}}
                for mod, entry in found
            ],
            "unchecked": [{**row(mod), "reason": reason} for mod, reason in unchecked],
            "checked_at": self.db.get_setting("mod_updates_checked_at"),
        }

    async def update(
        self, filename: str, user: str, version_id: str | None = None
    ) -> dict[str, Any]:
        self.require_server_offline(f"update a {self.word}")
        mod = self.find(filename)
        old_path = Path(mod.path)
        source, project = self.origin(mod)
        if source == "file" or not project:
            raise ModError(
                f"{mod.name} was added from a file, so there's nothing to update it from."
            )
        site = self.site(source)
        version: dict[str, Any] | None
        if version_id:
            version = await site.version(version_id)
        else:
            version = await site.latest_for(project, self._target_version(), like=mod.filename)
        if not version:
            raise ModError(
                f"{LABELS[source]} has no newer {self.server_type.name} version of {mod.name}."
            )
        if self._is_installed_file(mod, version):
            self.db.set_setting(f"mod_update:{mod.mod_id}", None)
            raise ModError(
                f"{mod.name} is already this version ({version['version_number']}), "
                "so nothing was changed."
            )
        data, new_filename, sha256 = await site.download(version)
        refuse_known_bad(data, new_filename)
        archived = self.archive(old_path, mod, source="pre-update")
        target = safe_join(self.mods_dir, new_filename, allowed_extensions=JAR_EXT)
        if not mod.enabled:
            # A turned-off mod is updated, and stays off.
            target = target.with_name(target.name + DISABLED_SUFFIX)
        temp = self.mods_dir / f".{new_filename}.part"
        temp.write_bytes(data)
        try:
            probe = read_mod_jar(temp, compute_hash=False)
            self._check_loader(probe, discarded=False)
            old_path.unlink()
            temp.replace(target)
        except Exception:
            temp.unlink(missing_ok=True)
            if not old_path.exists():  # put the old jar back
                shutil.copy2(archived, old_path)
            raise
        installed = read_mod_jar(target)
        installed.sha256 = sha256
        self.archive(target, installed, source=version["file"]["url"])
        self.record(
            "update",
            user,
            installed,
            old_version=mod.version,
            new_version=installed.version,
            source=version["file"]["url"],
            sha256=sha256,
        )
        self.remember(
            installed.mod_id,
            source,
            {**version, "project_id": version.get("project_id") or project},
        )
        self.db.set_setting(f"mod_update:{installed.mod_id}", None)
        self._cache = None
        await self._emit(
            "mod_updated",
            f"Updated {installed.name}: {mod.version} to {installed.version}",
            level="success",
            mod=installed.to_dict(),
            previous=mod.version,
        )
        return {
            "updated": installed.to_dict(),
            "previous_version": mod.version,
            "rollback_archive": str(archived),
            "loaded_by_minecraft": "not verified",
            "loaded_detail": (
                "The new file is in place. Start the server to find out whether it loads. "
                "If the server won't start, go back to the old version from Versions."
            ),
        }

    async def rollback(self, mod_id: str, archive_path: str, user: str) -> dict[str, Any]:
        self.require_server_offline(f"go back to an older {self.word}")
        row = self.db.query_one(
            "SELECT * FROM mod_versions WHERE server_id = ? AND mod_id = ? AND archive_path = ?",
            (self.server.server_id, mod_id, archive_path),
        )
        if not row:
            raise ModError(f"That saved version isn't in this server's {self.word} history.")
        source = Path(row["archive_path"])
        if not source.is_file():
            raise ModError("The saved copy of that version is missing from the backups folder.")
        if not is_inside(self.backup_dir, source):
            raise PathSafetyError(f"That path is outside the saved {self.word} folder.")
        if row.get("sha256"):
            actual = sha256_file(source)
            if actual != row["sha256"]:
                raise ModError("The saved copy has changed since it was saved, so it wasn't used.")

        current = self.find_by_id(mod_id)
        previous_version = current.version if current else None
        if current:
            current_path = Path(current.path)
            self.archive(current_path, current, source="pre-rollback")
            current_path.unlink()
        target = safe_join(self.mods_dir, row["filename"], allowed_extensions=JAR_EXT)
        shutil.copy2(source, target)
        restored = read_mod_jar(target)
        self.record(
            "rollback",
            user,
            restored,
            old_version=previous_version,
            new_version=restored.version,
            source=str(source),
        )
        self._cache = None
        await self._emit(
            "mod_rolled_back",
            f"Rolled {restored.name} back to {restored.version}",
            level="warn",
            mod=restored.to_dict(),
            previous=previous_version,
        )
        return {"restored": restored.to_dict(), "previous_version": previous_version}

    async def close(self) -> None:
        await self.modrinth.close()
