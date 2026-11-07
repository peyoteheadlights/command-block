"""/diagnose in Discord, against the fake Discord: who may run it, the
summary it edits in, and what stays in the apps."""

from __future__ import annotations

from agent import diagnose
from agent.discord import bot as discord_bot
from agent.security import permissions as P
from tests.fake_discord import wait_for
from tests.test_discord import (  # noqa: F401  (agent and fake are fixtures)
    MOD_ROLE_OF_OWNER,
    agent,
    core_of,
    fake,
    run,
    set_up,
)
from tests.test_discord_start import grant


async def finished_run(out, job, fixes=None):
    out.line("Join from this PC as CommandBlock", "Log in", True)
    out.line("Home network", "From this PC to 192.168.1.20:25565", False, "No answer.")
    out.line("Internet", "From the internet", None, "Not checked: no address.")
    out.result["known"] = [{"id": "x", "say": "The server ran out of memory.", "link": None}]
    out.result["changes"] = [{"key": "memory", "title": "More memory", "why": "", "safe": True}]
    return out.finish()


def test_diagnose_needs_its_own_permission_and_edits_in_a_summary(agent, fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(diagnose, "run", finished_run)
    monkeypatch.setattr(discord_bot, "DIAGNOSE_EVERY", 0.05)
    set_up(agent)
    assert run(fake, "diagnose") == ["Your Discord roles don't allow that on this server."]
    grant(agent, P.SERVER_DIAGNOSE)
    token = fake.command("diagnose", roles=[MOD_ROLE_OF_OWNER])
    words = wait_for(lambda: (r := fake.replies(token)) and r[-1].startswith("Diagnose") and r)
    assert words[0].startswith("Testing Test Server")
    summary = words[-1]
    assert "✅ Join from this PC as CommandBlock: worked" in summary
    assert "❌ Home network: failed" in summary
    assert "❔ Internet: not checked" in summary
    assert "- The server ran out of memory." in summary
    assert "1 change may fix it" in summary
    assert "192.168" not in summary  # details stay in the apps
    ctx = core_of(agent).servers["test"]
    assert ctx.diagnosis["by"].endswith("(Discord)")


def test_a_running_diagnose_or_a_playing_owner_refuses(agent, fake, monkeypatch):  # noqa: F811
    set_up(agent)
    grant(agent, P.SERVER_DIAGNOSE)
    ctx = core_of(agent).servers["test"]
    ctx.diagnosis = {"running": True}
    assert "already running" in run(fake, "diagnose", roles=[MOD_ROLE_OF_OWNER])[-1]
    ctx.diagnosis = None
    monkeypatch.setattr(diagnose, "owner_playing", lambda ctx: "Steve")
    assert "kick them out" in run(fake, "diagnose", roles=[MOD_ROLE_OF_OWNER])[-1]


def test_still_running_after_the_wait_says_so():
    assert "Still testing" in discord_bot.diagnose_words("Test", {"running": True})
