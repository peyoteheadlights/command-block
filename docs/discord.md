# The Discord bot

The bot runs inside the agent on your PC. It keeps one outgoing connection
to Discord, so nothing is hosted, nothing costs money and no port is opened.
It's offline whenever the PC is asleep or off, so turn on **Keep this PC
awake** (PC settings) if friends should reach it at night.

It's still being built. So far it connects, sets up a Discord server, gives
Discord roles permissions, links accounts, keeps a status message, posts
alerts with buttons, starts servers and carries chat both ways. `/status`,
`/players`, `/ip`, `/start`, `/request-start`, `/link`, `/links` and
`/unlink` work; stats, Levels, quests and events come next.

## Setting it up

Everything is on the desktop app's gear → **Discord**.

1. Open Discord's developer portal (the button on the page), press **New
   Application**, name it and press **Create**.
2. **Bot** → **Reset Token** → **Copy**.
3. Optional: on the same page turn on **Server Members Intent** and
   **Message Content Intent**. The bot works without them; the page lists
   what stays off. The bot only asks Discord for Message Content when it's
   on here, because asking for a switch that's off would stop it.
4. Paste the token on the Discord page and press **Save**. It's written to
   `.env` as `MCSC_DISCORD_BOT_TOKEN`, never to `config.yaml`, the logs or
   the Get help file.
5. Press **Invite the bot**, pick your Discord server, **Authorize**. The
   invite only asks for what the bot uses: see and post in channels, embed
   links, read history, add reactions, manage roles and channels, and
   private threads.
6. Tick which Minecraft servers that Discord server is for and press **Set
   up this Discord server**. It adds a "Command Block" category with
   alerts, quests and events channels, a chat and status channel for each
   Minecraft server, and the Discord roles "Minecraft mod", "Regular",
   "Veteran" and "Legend". It only adds what's missing and finds its own
   channels and roles by id, so it never deletes or renames anything.

The first time it connects the bot names itself "Command Block" with the
logo. You can change both on the page (Discord allows about two name
changes an hour).

One Discord server can be for all your Minecraft servers, or each can have
its own. A command used in a channel tied to a Minecraft server acts on that
server; anywhere else it asks which one when there are several.

## The checks

The page checks, against Discord itself, that the token works, which
switches are on, that the bot has Manage Roles and Manage Channels, that its
role is above every Discord role it hands out, and that it can see and post
in each channel it uses (and add reactions in the chat channels). Each problem says what to fix. A rejected token or
a role problem also shows in Recommendations and Getting started, and
`python -m agent --check` asks Discord whether the token works.

## Who may do what

Everyone in a Discord server set up for a Minecraft server can use the
self-service commands there (`/status`, `/players`, `/ip`,
`/request-start`, `/link`, `/links`, `/unlink`). For
the rest, give a Discord role a permission level per Minecraft server on the
Discord page: the same levels and switches as moderators. A Discord role can
never get more than a moderator could, and never anything owner-only. Each
command is checked on the agent like an API route (a sign-in of kind
`discord`, see `agent/security/permissions.py`), limited to 8 a minute per
member, and written to the audit log with the member's name.

The new "Quests and events" permission is in no level: tick it for a
moderator or a Discord role on purpose.

## Status, alerts and starting

- `/status`, `/players` and `/ip` only say what the agent measured: a player
  count nobody checked says Unknown, a stopped server says offline. `/ip`
  shows the **Address to share** you typed on the Discord page (like your
  No-IP name), or else your home network and Tailscale addresses. The
  internet address is never guessed.
- Each server's status channel has one message the bot edits when something
  changes (checked once a minute). The line under the bot's name says how
  many are playing.
- Once an alerts channel is picked, alerts go there and the webhook stops,
  unless you tick **Also send alerts to the webhook**. A server can have its
  own alerts channel ("Alerts: <server>"). Alerts about the PC itself
  (sign-ins, certificate, updates, Tailscale) never go to Discord.
- Alerts have buttons: Kick and Ban on player alerts, Restart on crashes and
  a slow game, Back up on a failed backup. Each checks the presser's Discord
  role and asks "Are you sure?" first.
- `/start` works for Discord roles with Start, only while the server is
  stopped, with 5 minutes between starts from Discord. If the server refuses
  to start, Discord only hears that it didn't (the reason can name folders
  on the PC); the app shows why.
- `/request-start` (a start request) is for everyone else: only while the
  server is stopped, one open request per server, 10 minutes between
  requests, 15 minutes to answer. The owner and moderators with Start get
  Start / Say no on the Discord alert, the desktop app's Windows alert and
  in the bell's list (phones in a later part). The asker is told what
  happened. Requests are kept in memory, so restarting the agent drops an
  open one (`agent/startrequests.py`).

## The chat bridge

Each Minecraft server's chat channel and its game chat are joined.

- **Game to Discord:** chat, `/me`, messages you sent as "Server", joins,
  leaves, deaths and advancements. Deaths and advancements are read from
  the console's own lines (vanilla's wording) and only count for a player
  who's online. Lines are gathered for a second and posted together. The
  bot never pings anyone and Discord formatting in game chat shows as
  typed. Link codes never reach Discord.
- **Discord to game:** `[Discord] Sam: hi`, sent with `tellraw`. Letters of
  any language stay, emoji go, mentions become plain names, formatting
  marks go, and it's one line of at most 220 characters. A message that
  starts like a command (`/`) or a console line (`[12:00:00`, `[Server]`,
  `<Name>`) gets a 🚫 reaction and stays out of the game. Each member can
  send 5 messages in 10 seconds; the rest are dropped. Bots and webhooks
  are left out, and nothing goes in while the server isn't online.

Discord to game needs the **Message Content** switch; without it the bridge
is game to Discord only and the Discord page says so. A Bedrock server
doesn't print chat to its console, so there only joins and leaves reach
Discord (the page says that too); Discord messages still reach the game.

The `tellraw` text is JSON our code writes, with every character outside a
small safe set written as a `\u` escape, so the command still passes the
console check's character set (house rule 2). It's built and sent in
`agent/minecraft/chat.py`; nothing in `agent/discord/` reaches the console.

## Linking accounts

`/link <Minecraft name>` gives a 6-letter code only you can see. Type it in
the game chat within 10 minutes, as that player, and you're linked. It only
counts when exactly that player says it (the same anchored chat matching as
the chat page), it works once, and the code line never reaches the chat log.
A member can link up to 3 Minecraft accounts; an account belongs to one
member. Accounts are kept by the uuid the server printed (Floodgate's for
Bedrock players on Java).

A Bedrock server doesn't show chat to Command Block, so there `/link` sends
a link request instead. It shows on the server's **Players and chat** page
(and as an alert) for the owner and moderators with Players, who pick the
player from the server's recent players and press **Approve** or **Say no**.

Linked accounts are on the Players and chat page, where they can be
removed. They're in Export everything and never in Get help.

## The code

`agent/discord/client.py` is our own small Discord client (gateway and REST,
API v10, not discord.py, to keep weak PCs light). It stops on a rejected
token and never retries it, leaves a channel alone for 10 minutes after a
403, waits as long as Discord asks on a 429 and resumes after a drop with
growing waits. `bot.py` holds the commands and buttons, `live.py` what the status
commands, status message and presence say, `bridge.py` the chat bridge's words, `layout.py` the channels, roles,
grants and checks, `links.py` the linked accounts. Without a token none of it
runs and the client isn't imported.

Tests use a fake Discord (`tests/fake_discord.py`). There's also an opt-in
set against a real test Discord server, see [testing.md](testing.md).
