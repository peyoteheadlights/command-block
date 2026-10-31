"""New season: a fresh world with the same settings, add-ons and whitelist.

One safe change (agent/safechange.py): the server stops, a full backup is
made and checked, the world folders go, and server.properties gets the new
seed ("" lets Minecraft pick one). The backup is then marked ``season``,
which the clean-up never deletes, and it is this change's Undo. An old
season opens again as its own server through Duplicate (agent/duplicate.py,
``season_backup``).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import TYPE_CHECKING, Any

from .events import Event
from .minecraft.properties import FILENAME, PropertiesFile, world_folders
from .safechange import SafeChange, run_safe_change
from .worldimport import _move_aside, _put_back

if TYPE_CHECKING:
    from .core import ServerContext
    from .jobs import JobHandle

MAX_SEED = 32


class SeasonError(RuntimeError):
    """A new season can't start as asked. Written for people."""


def check_seed(value: str | None) -> str:
    seed = (value or "").strip()
    if len(seed) > MAX_SEED or not seed.isprintable() or "=" in seed:
        raise SeasonError(f"A seed is a number or a word of up to {MAX_SEED} letters.")
    return seed


async def new_season(
    ctx: ServerContext, *, seed: str | None, announce: bool, user: str = "system"
) -> dict[str, Any]:
    seed = check_seed(seed)
    base = ctx.config.server_dir
    folders = sorted(world_folders(base, ctx.config.server_type))
    existing = [name for name in folders if (base / name).exists()]
    if not existing:
        raise SeasonError(f"{ctx.name} has no world yet, so there's no season to end.")
    ctx.require_free()
    moved: list[str] = []
    title = "Start a new season"

    async def change(job: JobHandle | None) -> dict[str, Any]:
        moved.extend(await asyncio.to_thread(_move_aside, base, existing))
        try:
            props = PropertiesFile.read(base / FILENAME)
            props.set("level-seed", seed)
            props.write(base / FILENAME)
        except BaseException:
            await asyncio.to_thread(_put_back, base, moved)
            raise
        return {"seed": seed or None}

    async def check(result: dict[str, Any]) -> tuple[bool, str]:
        left = [name for name in existing if (base / name).exists()]
        if left:
            return False, f"{', '.join(left)} is still there"
        if (PropertiesFile.read(base / FILENAME).get("level-seed") or "") != seed:
            return False, "the seed wasn't saved"
        return True, "the old world is in its backup; the new one is made on the next start"

    plan = SafeChange(
        title=title,
        change=change,
        check=check,
        start_after=ctx.server.running,
        backup_name="season",
        backup_note=f"Season up to {time.strftime('%Y-%m-%d')}",
        # A full backup, so an old season opens with its own settings and
        # add-ons too; Undo puts back only the world and the seed.
        undo_scope=[*existing, FILENAME],
        undo_on_change_error=False,
    )

    async def run(job: JobHandle | None) -> dict[str, Any]:
        result = await run_safe_change(ctx.server, ctx.backups, plan, job=job, user=user)
        ctx.core.db.execute(
            "UPDATE backups SET kind = 'season' WHERE id = ?", (result["safety_backup"]["id"],)
        )
        for retired in moved:
            await asyncio.to_thread(shutil.rmtree, retired, True)
        return result

    created, result = await ctx.core.jobs.run(
        "new_season", f"{title} on {ctx.name}", run, server_id=ctx.server_id, risky=True, user=user
    )
    ctx.db.audit("new_season", user=user, detail=f"seed {seed or 'random'}")
    await ctx.bus.publish(
        Event(
            type="new_season",
            level="success",
            message=f"A new season started on {ctx.name}. The old world is kept as a backup.",
            # quiet: the owner unticked "Tell Discord", so no alert goes out.
            data={"undo": result.get("undo"), "quiet": not announce},
        )
    )
    return {"job_id": created.id, **result}
