"""Do the other mods still fit? The "Still fit?" card.

After a mod or plugin is updated, rolled back, added or removed, and after
a Minecraft or loader version change, this looks at what the mods and the
sites say and lists:

- ``needs_change``: mods whose version doesn't fit any more, each with a
  newer version that does (the card's ticked list; "Change these" sends
  them to Update all),
- ``no_fit``: mods that don't fit, with no version to change to,
- ``cant_tell``: mods nothing said anything about either way.

Only declared facts count (Mark, 2026-11-06): the ranges in each jar,
its "breaks" list, the site saying a version is built for this Minecraft
version and loader, and the site's "incompatible" links. Nothing said is
"can't tell", never "fits". It changes nothing.

On a weak mini PC: all Modrinth mods are asked in two calls (by the sha1
the store saved when the file came in), other sites once per mod that
needs it, and only after the last of several quick changes (Update all
updates one mod at a time).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..events import Event
from .dependencies import PLATFORM_IDS, describe_range, pretty_name
from .jarinfo import ModInfo, range_satisfies
from .manager import ModError
from .modrinth import ModrinthError
from .sources import LABELS, SourceError
from .store import hashes_of

if TYPE_CHECKING:
    from ..core import ServerContext

log = logging.getLogger("msc.mods.fit")

SETTING = "mod_fit"
# The events that start a check, and how long after the last one it runs.
MOD_EVENTS = ("mod_installed", "mod_updated", "mod_rolled_back", "mod_removed")
VERSION_EVENTS = ("version_changed", "version_rolled_back")
WAIT = 3.0


def empty(about: str = "") -> dict[str, Any]:
    return {"about": about, "needs_change": [], "no_fit": [], "cant_tell": []}


def shown(result: dict[str, Any] | None) -> bool:
    return bool(result) and any(result[key] for key in ("needs_change", "no_fit", "cant_tell"))


# ---------------------------------------------------------------- when
class Later:
    """Waits for the last of several quick changes, then checks once."""

    def __init__(self, ctx: ServerContext):
        self.ctx = ctx
        self.task: asyncio.Task | None = None
        self.names: list[str] = []
        self.ids: set[str] = set()
        self.everything = False

    def heard(self, event: Event) -> None:
        data = event.data or {}
        if event.type in VERSION_EVENTS:
            self.everything = True
            if data.get("minecraft_version"):
                self.names.append(f"Minecraft {data['minecraft_version']}")
        elif event.type in MOD_EVENTS and data.get("mod"):
            mod = data["mod"]
            self.names.append(f"{mod.get('name') or mod.get('mod_id')} {mod.get('version')}")
            self.ids.add(str(mod.get("mod_id")))
        else:
            return
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = asyncio.create_task(self._run(), name=f"mod-fit-{self.ctx.server_id}")

    async def _run(self) -> None:
        await asyncio.sleep(WAIT)
        names, ids = self.names, None if self.everything else self.ids
        self.names, self.ids, self.everything = [], set(), False
        about = names[0] if len(names) == 1 else "these changes"
        await refresh(self.ctx, about, ids)

    def stop(self) -> None:
        if self.task:
            self.task.cancel()


async def refresh(ctx: ServerContext, about: str, mod_ids: set[str] | None) -> None:
    """Check, save the answer for the Mods page and tell the apps. A check
    that fails is logged; the card is only cleared, never made up."""
    try:
        result = await check_fit(ctx, about, mod_ids)
    except Exception:
        log.exception("the Still fit? check failed")
        return
    result["checked_at"] = time.time()
    ctx.db.set_setting(SETTING, result if shown(result) else None)
    count = len(result["needs_change"])
    await ctx.bus.publish(
        Event(
            type="mod_fit",
            level="warn" if shown(result) else "info",
            message=(
                f"{count} {ctx.mods.word}{'' if count == 1 else 's'} need a different version "
                f"to go with {about}"
                if count
                else f"Checked the other {ctx.mods.word}s after {about}"
            ),
            data={"shown": shown(result)},
        )
    )


# ---------------------------------------------------------------- check
async def check_fit(
    ctx: ServerContext, about: str, mod_ids: set[str] | None = None
) -> dict[str, Any]:
    """``mod_ids`` None: everything (after a version change); otherwise the
    mods just changed, checked against the mods they state ranges with."""
    mods = ctx.mods
    result = empty(about)
    minecraft = mods._target_version()
    if not minecraft or not mods.server_type.has_content:
        return result  # nothing to check against yet
    result["minecraft_version"] = minecraft
    installed = [m for m in mods.scan() if m.enabled]
    index, _ = mods.provided(installed)
    tops = {id(m) for m in installed}
    # A range one jar states on another, as (who states it, on whom, range).
    links: list[tuple[ModInfo, ModInfo, Any, bool]] = []
    for mod in installed:
        for dep in mod.dependencies:
            other = index.get(dep.mod_id)
            if (
                dep.kind == "depends"
                and dep.mod_id not in PLATFORM_IDS
                and other
                and id(other) in tops
            ):
                links.append((mod, other, dep, False))
        for bad in mod.breaks:
            other = index.get(bad.mod_id)
            if other and id(other) in tops:
                links.append((mod, other, bad, True))

    linked: set[str] = set()
    changed = {m.mod_id for m in installed if mod_ids is None or m.mod_id in mod_ids}
    if mod_ids is not None:
        linked = {
            m.mod_id
            for a, b, _, _ in links
            if a.mod_id in mod_ids or b.mod_id in mod_ids
            for m in (a, b)
        }
        checking = [m for m in installed if m.mod_id in linked]
    else:
        checking = installed

    platform = {
        **mods.platform_versions(),
        "minecraft": minecraft,
    }
    pending = mods.server.pending_version or {}
    if pending.get("loader_version") and mods.server_type.loader_mod_id:
        platform[mods.server_type.loader_mod_id] = pending["loader_version"]

    wrong: dict[str, list[str]] = {}
    unsure: dict[str, list[str]] = {}
    jar_ok: set[str] = set()
    for mod in checking:
        for dep in mod.dependencies:
            # Minecraft and the loader only change in a version change.
            target = platform.get(dep.mod_id)
            if mod_ids is not None or dep.kind != "depends" or not target:
                continue
            ok = range_satisfies(target, dep.version_range, dep.syntax)
            if ok is False:
                wrong.setdefault(mod.mod_id, []).append(
                    f"{pretty_name(dep.mod_id).capitalize()} {target} isn't in its range "
                    f"({describe_range(dep.version_range)})"
                )
            elif ok and dep.mod_id == "minecraft":
                jar_ok.add(mod.mod_id)
    for stater, other, dep, breaks in links:
        if mod_ids is not None and stater.mod_id not in linked:
            continue
        ok = range_satisfies(other.version, dep.version_range, dep.syntax)
        # The one to change: the one that states the range, unless it was
        # just changed on purpose, then the other one.
        to_change = other if stater.mod_id in changed and mod_ids is not None else stater
        if mod_ids is not None and to_change.mod_id in mod_ids:
            continue
        said = (
            f"{stater.name} {'breaks with' if breaks else 'needs'} {other.name} "
            + describe_range(dep.version_range)
        )
        if (breaks and ok is not False) or (not breaks and ok is False):
            wrong.setdefault(to_change.mod_id, []).append(f"{said}, and {other.version} is here")
        elif ok is None:
            unsure.setdefault(to_change.mod_id, []).append(
                f"Can't read {stater.name}'s range for {other.name} ({dep.version_range})"
            )

    to_ask = [
        m
        for m in checking
        if (mod_ids is None or m.mod_id in wrong)
        and not (mod_ids is not None and m.mod_id in mod_ids)
    ]
    sites = await _ask_sites(mods, to_ask, minecraft)

    def row(mod: ModInfo, why: str) -> dict[str, Any]:
        return {
            "mod_id": mod.mod_id,
            "name": mod.name or mod.mod_id,
            "filename": mod.filename,
            "why": why,
        }

    for mod in checking:
        if mod_ids is not None and mod.mod_id in mod_ids:
            continue
        site = sites.get(mod.mod_id, {})
        problems = list(wrong.get(mod.mod_id, []))
        if site.get("listed") is False:
            problems.append(f"{site['label']} doesn't list {mod.version} for Minecraft {minecraft}")
        if not problems:
            if unsure.get(mod.mod_id):
                result["cant_tell"].append(row(mod, unsure[mod.mod_id][0]))
            elif mod_ids is None and not (mod.mod_id in jar_ok or site.get("listed")):
                result["cant_tell"].append(
                    row(
                        mod,
                        site.get("error")
                        or f"Neither it nor its site says it works with Minecraft {minecraft}",
                    )
                )
            continue
        why = problems[0]
        if site.get("error"):
            result["cant_tell"].append(row(mod, f"{why}. {site['error']}"))
            continue
        candidate = site.get("candidate")
        if not candidate:
            result["no_fit"].append(
                row(
                    mod,
                    f"{why}. "
                    + (
                        f"No version for Minecraft {minecraft} yet"
                        if site.get("label")
                        else "It was added from a file, so there's no other version to pick"
                    ),
                )
            )
            continue
        doubt = _candidate_doubt(mod, candidate, links, installed, mods)
        if doubt:
            result["cant_tell"].append(row(mod, f"{why}. {doubt}"))
            continue
        result["needs_change"].append(
            {
                **row(mod, why),
                "from": mod.version,
                "to": candidate.get("version_number") or "",
                "version_id": candidate["version_id"],
            }
        )
    return result


def _candidate_doubt(
    mod: ModInfo, candidate: dict[str, Any], links, installed: list[ModInfo], mods
) -> str | None:
    """Why the site's newest fitting version might still not fit, from what
    the other mods state about this one and the site's "incompatible"
    links; None when nothing says so."""
    number = candidate.get("version_number") or ""
    for stater, other, dep, breaks in links:
        if other is not mod:
            continue
        ok = range_satisfies(number, dep.version_range, dep.syntax)
        if (ok is True and breaks) or (ok is False and not breaks) or ok is None:
            return (
                f"Can't tell if {number} fits {stater.name}'s range "
                f"({describe_range(dep.version_range)})"
            )
    projects = {mods.origin(m)[1]: m for m in installed if m is not mod}
    for dep in candidate.get("dependencies") or []:
        other = projects.get(dep.get("project_id"))
        if dep.get("type") == "incompatible" and other:
            return f"Its site says {number} doesn't work with {other.name}"
    return None


async def _ask_sites(mods, wanted: list[ModInfo], minecraft: str) -> dict[str, dict[str, Any]]:
    """For each mod: ``label`` (its site), ``listed`` (True or False when
    the site says whether the installed file is for this Minecraft version,
    None when it doesn't say), ``candidate`` (the newest fitting version
    that isn't the installed file) and ``error`` (the site couldn't be
    asked). A mod from a file gets an empty answer."""
    found: dict[str, dict[str, Any]] = {}
    on_modrinth: dict[str, ModInfo] = {}
    others: list[tuple[ModInfo, str, str]] = []
    for mod in wanted:
        source, project = mods.origin(mod)
        if source == "file" or not project:
            continue
        found[mod.mod_id] = {"label": LABELS.get(source, source)}
        if source == "modrinth":
            on_modrinth[_hashes(mods, mod)["sha1"]] = mod
        else:
            others.append((mod, source, project))

    if on_modrinth:
        hashes = list(on_modrinth)
        try:
            now = await mods.modrinth.installed_versions(hashes)
            newest = await mods.modrinth.fitting_versions(hashes, minecraft)
        except ModrinthError as exc:
            for mod in on_modrinth.values():
                found[mod.mod_id]["error"] = str(exc)
        else:
            for sha1, mod in on_modrinth.items():
                answer = found[mod.mod_id]
                if sha1 in now:
                    answer["listed"] = minecraft in now[sha1]["game_versions"] and bool(
                        set(now[sha1]["loaders"]) & set(mods.modrinth.loaders)
                    )
                fit = newest.get(sha1)
                if fit and fit["file"].get("sha1") != sha1:
                    answer["candidate"] = fit
                if fit and fit["file"].get("sha1") == sha1:
                    answer["listed"] = True

    for mod, source, project in others:
        answer = found[mod.mod_id]
        try:
            site = mods.site(source)
            versions = (
                await site.versions(project, minecraft)
                if hasattr(site, "versions")
                else [
                    v for v in [await site.latest_for(project, minecraft, like=mod.filename)] if v
                ]
            )
        except (SourceError, ModError) as exc:
            answer["error"] = str(exc)
            continue
        hashes = _hashes(mods, mod)
        mine = [v for v in versions if _same_file(hashes, v)]
        if hasattr(site, "versions"):
            answer["listed"] = bool(mine)
        elif mine:
            answer["listed"] = True
        releases = [v for v in versions if v.get("release_type") == "release"] or versions
        if releases and not _same_file(hashes, releases[0]):
            answer["candidate"] = releases[0]
    return found


def _hashes(mods, mod: ModInfo) -> dict[str, Any]:
    """The installed file's hashes as the store saved them when it came in;
    read from the file only for a jar that never went through the store."""
    row = mod.sha256 and mods.store.db.query_one(
        "SELECT sha256, sha1, sha512 FROM stored_files WHERE sha256 = ?", (mod.sha256,)
    )
    return dict(row) if row else hashes_of(mod.path)


def _same_file(hashes: dict[str, Any], version: dict[str, Any]) -> bool:
    """Is ``version``'s file the installed one? Told by a hash the site
    published, never by the version's name."""
    file = version.get("file") or {}
    for algorithm in ("sha512", "sha256", "sha1"):
        if file.get(algorithm) and hashes.get(algorithm):
            return str(file[algorithm]).lower() == hashes[algorithm]
    return False
