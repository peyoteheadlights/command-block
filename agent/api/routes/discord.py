"""The Discord bot's settings (agent/discord), owner only (discord.manage):
the token, the connection and its checks, "Set up this Discord server",
which channel is used for what, and what each Discord role may do.

The token goes to .env and is never sent back."""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request

from ...discord import layout
from ...security import permissions as P
from ...security.auth import AuthError, Principal
from ...security.permissions import DISCORD_MANAGE, SERVER_PERMISSIONS, require
from ..deps import audit, get_core
from ..responses import DiscordLevels, DiscordSetupDone, DiscordStatus
from .models import (
    DiscordAddress,
    DiscordGuildOptions,
    DiscordLevelSettings,
    DiscordProfile,
    DiscordRoleGrants,
    DiscordSetup,
    DiscordTie,
    DiscordToken,
)

router = APIRouter(prefix="/discord")

OFF = {"token_set": False, "state": "off"}


def status_of(core) -> dict:
    return core.discord.status() if core.discord is not None else OFF


def bot_of(core):
    if core.discord is None or core.discord.gateway.state != "connected":
        raise HTTPException(status_code=409, detail="The bot has to be connected first.")
    return core.discord


async def discord_call(work):
    """Runs a call to Discord, turning its refusal into a 400 with its words."""
    from ...discord.client import DiscordError

    try:
        return await work
    except DiscordError as exc:
        raise HTTPException(status_code=400, detail=f"Discord said: {exc}") from None


def guild_of(bot, guild_id: str) -> str:
    if guild_id not in bot.guilds:
        raise HTTPException(status_code=404, detail="The bot isn't in that Discord server.")
    return guild_id


@router.get("", response_model=DiscordStatus)
async def discord_status(
    principal: Principal = Depends(require(DISCORD_MANAGE)), core=Depends(get_core)
):
    return status_of(core)


@router.put("/token", response_model=DiscordStatus)
async def set_token(
    payload: DiscordToken,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    await core.set_discord_token(payload.token)
    audit(core, request, "discord_token", detail="set")
    return status_of(core)


@router.delete("/token", response_model=DiscordStatus)
async def remove_token(
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    await core.set_discord_token(None)
    audit(core, request, "discord_token", detail="removed")
    return status_of(core)


@router.post("/check", response_model=DiscordStatus)
async def check_again(
    principal: Principal = Depends(require(DISCORD_MANAGE)), core=Depends(get_core)
):
    await bot_of(core).check()
    return status_of(core)


@router.put("/profile", response_model=DiscordStatus)
async def set_profile(
    payload: DiscordProfile,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    from ...discord.bot import logo

    bot = bot_of(core)
    picture = None
    if payload.avatar == "logo":
        picture = logo()
    elif payload.avatar:
        try:
            picture = base64.b64decode(payload.avatar, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="That picture couldn't be read.") from None
        if not (picture.startswith(b"\x89PNG") or picture.startswith(b"\xff\xd8")):
            raise HTTPException(status_code=400, detail="Use a PNG or JPEG picture.")
        if len(picture) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Use a picture under 8 MB.")
    await discord_call(bot.set_profile(payload.name, picture))
    audit(core, request, "discord_profile", detail=payload.name)
    return status_of(core)


@router.post("/guilds/{guild_id}/setup", response_model=DiscordSetupDone)
async def set_up(
    guild_id: str,
    payload: DiscordSetup,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    bot = bot_of(core)
    unknown = [sid for sid in payload.servers if sid not in core.servers]
    if unknown:
        raise HTTPException(
            status_code=404, detail=f"There is no server with the id '{unknown[0]}'"
        )
    made = await discord_call(bot.set_up(guild_of(bot, guild_id), payload.servers))
    audit(core, request, "discord_setup", target=guild_id, detail=", ".join(made["made"]))
    return {"made": made["made"], "status": status_of(core)}


@router.put("/guilds/{guild_id}/channels", response_model=DiscordStatus)
async def tie_channel(
    guild_id: str,
    payload: DiscordTie,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    bot = bot_of(core)
    guild_of(bot, guild_id)
    if payload.kind in (*layout.SERVER_CHANNELS, *layout.OPT_IN_CHANNELS) and not payload.server_id:
        raise HTTPException(status_code=400, detail="Pick the Minecraft server for this channel.")
    if payload.server_id and payload.server_id not in core.servers:
        raise HTTPException(
            status_code=404, detail=f"There is no server with the id '{payload.server_id}'"
        )
    layout.tie(core.db, guild_id, payload.kind, payload.server_id, payload.channel_id)
    audit(core, request, "discord_channel", target=f"{payload.kind}:{payload.server_id}")
    await discord_call(bot.refresh(guild_id))
    return status_of(core)


@router.put("/guilds/{guild_id}/options", response_model=DiscordStatus)
async def set_options(
    guild_id: str,
    payload: DiscordGuildOptions,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    """Per Discord server choices. Taking people off the whitelist when they
    leave needs Discord to say who left, which the bot only asks for while
    one Discord server uses it, so the bot logs in again to ask."""
    from ...discord import client

    bot = bot_of(core)
    guild_of(bot, guild_id)
    layout.set_guild_options(core.db, guild_id, remove_on_leave=payload.remove_on_leave)
    audit(
        core,
        request,
        "discord_options",
        target=guild_id,
        detail=f"remove_on_leave={payload.remove_on_leave}",
    )
    if bot.wants_members() != bool(bot.gateway.intents & client.GUILD_MEMBERS):
        await bot.gateway.relogin()
    return status_of(core)


@router.put("/guilds/{guild_id}/roles/{role_id}", response_model=DiscordStatus)
async def grant_role(
    guild_id: str,
    role_id: str,
    payload: DiscordRoleGrants,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    bot = bot_of(core)
    guild_of(bot, guild_id)
    if not role_id.isdigit():
        raise HTTPException(status_code=404, detail="There's no such Discord role.")
    for sid, wanted in payload.grants.items():
        if sid not in core.servers:
            raise HTTPException(status_code=404, detail=f"There is no server with the id '{sid}'")
        if set(wanted) - SERVER_PERMISSIONS:
            raise HTTPException(status_code=400, detail="A Discord role can't be given that.")
    for sid, wanted in payload.grants.items():
        layout.set_grant(core.db, guild_id, role_id, payload.role_name, sid, wanted)
    audit(core, request, "discord_role", target=f"{payload.role_name or role_id}")
    return status_of(core)


def may_change_levels(core, principal: Principal, guild_id: str) -> None:
    """The owner, or someone with "Quests and events" on one of the
    Minecraft servers this Discord server is for."""
    for sid in layout.servers_of(core.db, guild_id):
        try:
            P.check(principal, P.QUESTS_MANAGE, sid)
            return
        except AuthError:
            continue
    raise HTTPException(status_code=403, detail="Your account isn't allowed to do that.")


@router.get("/guilds/{guild_id}/levels", response_model=DiscordLevels)
async def get_levels(
    guild_id: str,
    principal: Principal = Depends(require(P.QUESTS_MANAGE)),
    core=Depends(get_core),
):
    bot = bot_of(core)
    may_change_levels(core, principal, guild_of(bot, guild_id))
    return bot.levels_of(guild_id)


@router.put("/guilds/{guild_id}/levels", response_model=DiscordLevels)
async def set_levels(
    guild_id: str,
    payload: DiscordLevelSettings,
    request: Request,
    principal: Principal = Depends(require(P.QUESTS_MANAGE)),
    core=Depends(get_core),
):
    """Level-up messages on or off, the weekly leaderboard's day, and (when
    given) which Discord role goes with which Level."""
    bot = bot_of(core)
    may_change_levels(core, principal, guild_of(bot, guild_id))
    if payload.roles is not None:
        wanted = sorted((r.level, r.role_id) for r in payload.roles)
        if len({at for at, _ in wanted}) != len(wanted) or len({r for _, r in wanted}) != len(
            wanted
        ):
            raise HTTPException(status_code=400, detail="Each Level and each role only once.")
        found = {
            r["id"]: r
            for r in await discord_call(bot.rest.request("GET", f"/guilds/{guild_id}/roles"))
        }
        ours = layout.our_roles(core.db, guild_id)
        for _at, role_id in wanted:
            if role_id not in found:
                raise HTTPException(status_code=404, detail="There's no such Discord role.")
            refused = layout.level_role_refused(guild_id, found[role_id], ours)
            if refused:
                raise HTTPException(status_code=400, detail=refused)
        layout.set_level_roles(core.db, guild_id, wanted)
    # Saved before waiting on Discord, so all of it is saved at once.
    layout.set_level_settings(
        core.db, guild_id, announce=payload.announce, leaderboard_day=payload.leaderboard_day
    )
    if payload.roles is not None:
        await discord_call(bot.check(guild_id))
        bot.resync_levels()
    audit(core, request, "discord_levels", target=guild_id)
    return bot.levels_of(guild_id)


@router.put("/addresses", response_model=DiscordStatus)
async def set_address(
    payload: DiscordAddress,
    request: Request,
    principal: Principal = Depends(require(DISCORD_MANAGE)),
    core=Depends(get_core),
):
    """The address /ip and the status message show for a server (empty: none)."""
    if payload.server_id not in core.servers:
        raise HTTPException(
            status_code=404, detail=f"There is no server with the id '{payload.server_id}'"
        )
    layout.set_address(core.db, payload.server_id, payload.address)
    audit(core, request, "discord_address", target=payload.server_id)
    return status_of(core)
