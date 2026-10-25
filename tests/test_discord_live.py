"""Live checks against a real test Discord server, for what the fake can't
prove: logging in, registering commands, the checks, and "Set up this
Discord server" making only what's missing.

Skipped unless both are set (on your PC, or a CI job with secrets):

    set CB_TEST_DISCORD_TOKEN=<the test bot's token>
    set CB_TEST_DISCORD_SERVER=<the test Discord server's id>
    python -m pytest tests/test_discord_live.py -v

Use a bot and a Discord server made only for this. The test deletes the
channels and roles setup made at the end, so the Discord server is left as
it was. (The bot itself never deletes anything; only this test does.)
Slash commands and buttons can't be pressed by a bot, so those stay covered
by the fake Discord and the hand-checks.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from agent.database.db import Database
from agent.discord import client, layout
from agent.discord.bot import COMMANDS, command_list

TOKEN = os.environ.get("CB_TEST_DISCORD_TOKEN", "")
GUILD = os.environ.get("CB_TEST_DISCORD_SERVER", "")

pytestmark = pytest.mark.skipif(
    not (TOKEN and GUILD), reason="set CB_TEST_DISCORD_TOKEN and CB_TEST_DISCORD_SERVER"
)


def test_the_real_discord_accepts_the_bot(tmp_path):
    asyncio.run(live(tmp_path))


async def live(tmp_path) -> None:
    seen: dict[str, dict] = {}
    rest = client.Rest(TOKEN)

    async def on_event(name: str, data: dict) -> None:
        seen.setdefault(name, data)

    gateway = client.Gateway(rest, TOKEN, client.GUILDS, on_event)
    running = asyncio.create_task(gateway.run())
    made_channels: list[str] = []
    made_roles: list[str] = []
    try:
        for _ in range(300):
            if "GUILD_CREATE" in seen:
                break
            assert gateway.state not in ("token_rejected", "intents_refused"), gateway.detail
            await asyncio.sleep(0.1)
        assert gateway.state == "connected", gateway.detail
        bot_id = seen["READY"]["user"]["id"]
        app = await rest.request("GET", "/applications/@me")

        registered = await rest.request(
            "PUT", f"/applications/{app['id']}/guilds/{GUILD}/commands", command_list({"s": "Test"})
        )
        assert {c["name"] for c in registered} == set(COMMANDS)

        before_channels = {
            c["id"]: c["name"] for c in await rest.request("GET", f"/guilds/{GUILD}/channels")
        }
        before_roles = {
            r["id"]: r["name"] for r in await rest.request("GET", f"/guilds/{GUILD}/roles")
        }
        db = Database(tmp_path / "live.db")
        first = await layout.set_up(rest, db, GUILD, {"s": "Test"})
        made_channels = [r["channel_id"] for r in layout.channels(db, GUILD)]
        made_roles = list(layout.our_roles(db, GUILD).values())
        assert first["made"], "setup made nothing on an empty test server"
        assert (await layout.set_up(rest, db, GUILD, {"s": "Test"}))["made"] == []
        after_channels = {
            c["id"]: c["name"] for c in await rest.request("GET", f"/guilds/{GUILD}/channels")
        }
        after_roles = {
            r["id"]: r["name"] for r in await rest.request("GET", f"/guilds/{GUILD}/roles")
        }
        # Nothing that was there before was renamed or deleted.
        assert all(after_channels.get(i) == name for i, name in before_channels.items())
        assert all(after_roles.get(i) == name for i, name in before_roles.items())

        report = await layout.inspect(rest, db, GUILD, bot_id, {"s": "Test"})
        print("checks:", report["problems"])
        assert all(p["id"] != "channel_gone" for p in report["problems"])
    finally:
        # Put the test Discord server back as it was.
        for channel in reversed(made_channels):
            try:
                await rest.request("DELETE", f"/channels/{channel}")
            except client.DiscordError:
                pass
        for role in made_roles:
            try:
                await rest.request("DELETE", f"/guilds/{GUILD}/roles/{role}")
            except client.DiscordError:
                pass
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        await rest.close()
