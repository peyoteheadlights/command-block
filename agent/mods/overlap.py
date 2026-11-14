"""The same mod twice, and mods that do the same job.

Three kinds of rows for the Mods page, the Overview and the phone apps:

- ``same_mod``: two turned-on jars with the same mod id,
- ``same_job``: two installed add-ons on one hand-checked "these do the
  same job" list in the notes file (agent/notes.py ``same_job``),
- ``site_says``: two installed add-ons where the site one came from said
  that version doesn't work with the other.

Each row says which one to keep only when the facts say so (Mark,
2026-11-06): never one another installed mod needs; the same mod twice
keeps the newer one; otherwise the one with a build for this Minecraft
version, then the notes file's usual pick; otherwise no pick. It changes
nothing: Turn off is the owner's click.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .jarinfo import ModInfo, _cmp, range_satisfies
from .sources import LABELS

NO_PICK = "These do the same job. Keep the one you use."


def find(
    enabled: list[ModInfo],
    origin: Callable[[ModInfo], tuple[str, str | None]],
    record: Callable[[ModInfo], dict[str, Any]],
    groups: list[dict[str, Any]],
    minecraft: str | None,
) -> list[dict[str, Any]]:
    """``origin``: (source, project) of a jar; ``record``: what the app
    noted when it installed it (its site's "incompatible" projects);
    ``groups``: the notes file's same-job groups for this server type."""
    rows = _same_mod(enabled)
    twice = {r["mods"][0]["mod_id"] for r in rows}
    once = [m for m in enabled if m.mod_id not in twice]
    projects = {m.mod_id: str(origin(m)[1] or "").lower() for m in once}
    paired: set[frozenset[str]] = set()
    for group in groups:
        found = [m for m in once if any(_is(m, a, projects, record) for a in group["addons"])]
        if len(found) < 2:
            continue
        paired.update(frozenset((a.mod_id, b.mod_id)) for a in found for b in found)
        pick = group["usual_pick"].lower()
        usual = next((m for m in found if pick and projects[m.mod_id] == pick), None)
        rows.append(_row("same_job", found, group["why"], enabled, minecraft, usual))
    said = {m.mod_id: _incompatible(record(m)) for m in once}
    for mod in once:
        for other in once:
            pair = frozenset((mod.mod_id, other.mod_id))
            if (
                other is not mod
                and pair not in paired
                and projects[other.mod_id]
                and projects[other.mod_id] in said[mod.mod_id]
            ):
                paired.add(pair)
                site = LABELS.get(origin(mod)[0], "Its site")
                why = f"{site} says {mod.name} doesn't work with {other.name}."
                rows.append(_row("site_says", [mod, other], why, enabled, minecraft, None))
    return rows


def _is(mod: ModInfo, addon: dict[str, Any], projects: dict[str, str], record) -> bool:
    if not addon["project"]:
        # Listed by jar id alone: only a jar the app didn't install from a
        # site, so a site's add-on that shares the id is never mistaken for it.
        noted = record(mod)
        from_site = noted.get("source") not in (None, "file") or noted.get("modrinth_project")
        return not from_site and mod.mod_id.lower() in addon["ids"]
    return projects[mod.mod_id] == addon["project"].lower() or mod.mod_id.lower() in addon["ids"]


def _incompatible(record: dict[str, Any]) -> set[str]:
    return {str(p).lower() for p in record.get("incompatible") or []}


def _needed_by(mod: ModInfo, enabled: list[ModInfo], rivals: list[ModInfo]) -> str:
    """The name of an installed mod that needs this one: it depends on this
    mod's id. With two copies of one mod (``rivals`` share its id), only
    when this copy is in its range and another copy isn't."""
    for user in enabled:
        if user is mod or any(user is r for r in rivals):
            continue
        for dep in user.dependencies:
            if dep.kind != "depends" or dep.mod_id != mod.mod_id:
                continue
            if all(r.mod_id != mod.mod_id for r in rivals):
                return user.name or user.mod_id
            here = range_satisfies(mod.version, dep.version_range, dep.syntax)
            if here is True and any(
                range_satisfies(r.version, dep.version_range, dep.syntax) is False for r in rivals
            ):
                return user.name or user.mod_id
    return ""


def _has_build(mod: ModInfo, minecraft: str | None) -> bool:
    """The jar itself says it is built for this Minecraft version."""
    if not minecraft:
        return False
    return any(
        d.mod_id == "minecraft" and range_satisfies(minecraft, d.version_range, d.syntax) is True
        for d in mod.dependencies
    )


def _newest(mods: list[ModInfo]) -> ModInfo | None:
    """The one newer than every other, or None when versions can't be
    compared or two are the same."""
    best = mods[0]
    try:
        for mod in mods[1:]:
            if _cmp(mod.version, best.version) > 0:
                best = mod
        if any(m is not best and _cmp(best.version, m.version) == 0 for m in mods):
            return None
    except ValueError:
        return None
    return best


def _same_mod(enabled: list[ModInfo]) -> list[dict[str, Any]]:
    by_id: dict[str, list[ModInfo]] = {}
    for mod in enabled:
        by_id.setdefault(mod.mod_id, []).append(mod)
    rows = []
    for copies in (c for c in by_id.values() if len(c) > 1):
        needed = {id(m): _needed_by(m, enabled, [c for c in copies if c is not m]) for m in copies}
        wanted = [m for m in copies if needed[id(m)]]
        keep = wanted[0] if len(wanted) == 1 else None if wanted else _newest(copies)
        name = copies[0].name or copies[0].mod_id
        rows.append(
            {
                "kind": "same_mod",
                "why": f"{name} is here {len(copies)} times.",
                "keep": keep.filename if keep else "",
                "say": _say(keep, copies),
                "mods": [_mod(m, keep, needed[id(m)]) for m in copies],
            }
        )
    return rows


def _row(kind, found, why, enabled, minecraft, usual) -> dict[str, Any]:
    needed = {m.mod_id: _needed_by(m, enabled, [o for o in found if o is not m]) for m in found}
    free = [m for m in found if not needed[m.mod_id]]
    built = [m for m in free if _has_build(m, minecraft)]
    keep: ModInfo | None = None
    if len(free) < len(found):
        # One another mod needs stays; with two needed, nothing to pick.
        keep = next(m for m in found if needed[m.mod_id]) if len(found) - len(free) == 1 else None
    elif len(built) == 1:
        keep = built[0]
    elif usual is not None:
        keep = usual
    return {
        "kind": kind,
        "why": why,
        "keep": keep.filename if keep else "",
        "say": _say(keep, found),
        "mods": [_mod(m, keep, needed[m.mod_id]) for m in found],
    }


def _say(keep: ModInfo | None, mods: list[ModInfo]) -> str:
    if keep is None:
        return NO_PICK
    off = ", ".join(_label(m, mods) for m in mods if m is not keep)
    return f"Keep {_label(keep, mods)}, turn off {off}"


def _label(mod: ModInfo, mods: list[ModInfo]) -> str:
    """The version when every one is the same mod, otherwise its name."""
    same = len({m.mod_id for m in mods}) == 1
    return f"{mod.version or mod.filename}" if same else (mod.name or mod.mod_id)


def _mod(mod: ModInfo, keep: ModInfo | None, needed_by: str) -> dict[str, Any]:
    return {
        "filename": mod.filename,
        "mod_id": mod.mod_id,
        "name": mod.name or mod.mod_id,
        "version": mod.version,
        # Suggested to turn off: there is a pick and it isn't this one.
        "turn_off": keep is not None and mod is not keep,
        "needed_by": needed_by,
    }
