"""The Discord bot built into the agent (Phase 11).

client.py talks to Discord (our own small client, ADR 0003) and is only
imported once a bot token is set. bot.py is the bot itself; links.py, the
Linked accounts; layout.py, the Discord servers' channels, roles and
Discord role grants. Only client.py imports httpx or websockets for Discord.
"""
