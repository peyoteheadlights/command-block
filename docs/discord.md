# The Discord bot

The bot runs inside the agent on your PC. It keeps one outgoing connection
to Discord, so nothing is hosted, nothing costs money and no port is opened.
It's offline whenever the PC is asleep or off, so turn on **Keep this PC
awake** (PC settings) if friends should reach it at night.

It's still being built. So far it connects, sets up a Discord server, gives
Discord roles permissions, links accounts, keeps a status message, posts
alerts with buttons, starts servers, carries chat both ways, and runs
stats, Levels, quests, events and polls, adds players to the whitelist,
shows the console in a private channel and takes `/report`s.

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
   alerts, quests, levels and events channels, a chat and status channel for each
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
`/request-start`, `/link`, `/links`, `/unlink`, `/whitelist ask`,
`/report`). For
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
  in the bell's list, and the phone apps (in Alerts and on the lock-screen
  alert, which needs the phone unlocked). The asker is told what
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

## Stats and Levels

`/stats [player]` shows a player's playtime, deaths, mobs killed, blocks
mined and advancements on that server, with no name your own linked
accounts. They come from the world's `stats/<uuid>.json` and
`advancements/<uuid>.json`, which Minecraft writes every few minutes and
when a player leaves, so the answer always says when they were last saved.
A stat that isn't in the file is left out (never shown as 0), and a mod's
own stats are listed under their own names. Bedrock keeps no such files
(`ServerType.stats_files`), so there it's joins and playtime from the
console, and it says so. The apps show the same with **Stats** on a player
on the Players and chat page.

Every Discord member with a linked account has a **Level**. XP only comes
from what Command Block measures: 10 per hour played (our own player
sessions, added up over all their linked accounts and servers), plus XP
for events attended (50 each) and quests. Nothing for Discord
messages. Level n needs 8 × n² XP: Level 5 after 20 hours, 10 after 80,
25 after 500. `/level` shows yours and your next Level role.

Level roles: "Set up this Discord server" makes Regular (5), Veteran (10)
and Legend (25). The owner, and anyone with **Quests and events** on one of
that Discord server's Minecraft servers, can change them under **Levels**
on the Discord page. A member holds only the highest Level role they've
reached; the one before is taken away. A role that gives power in Discord
(admin, kick, ban, timeouts, @everyone, Manage anything) can't be a Level
role, and one that gets such a permission later isn't handed out (the
checks say so). A role that stops being a Level role is taken back from
members. Once changed, setup leaves the Level roles alone.

Once a minute the bot works out everyone's Level and only calls Discord
for a member whose Level changed. Level-ups are posted in the levels
channel (no ping), and can be turned off. A member seen for the first time
gets their role without a post.

The **weekly leaderboard** is off until you pick a day. It's posted in the
levels channel at 18:00 on the PC's clock: the top 10 by playtime in the
last 7 days on that Discord server's Minecraft servers, and the top 10
Levels. It's remembered, so a restart doesn't post it twice.

## Quests

The owner, and anyone with **Quests and events** on a server, makes quests
on that server's **Quests and events** page in the desktop app or with
`/quest create` in Discord. Players join with `/quest join <number>` (the
number is on the board), see how far they are with `/quest progress`, and
the quests channel has one board message that edits itself (at most once a
minute, only when something changed).

What a quest can ask for, all from things Command Block can measure:

- **A stat count**, like `mined:diamond_ore` ×500, from the stats files.
- **An advancement**, like `end/kill_dragon` (once only; it counts whenever
  it was earned).
- **Hours played** or **joins**, from our own player sessions.
- **Bring items**: the player types `/quest done` while in the game, and
  the agent asks the server with `clear <name> <item> 0`, which counts and
  never takes anything.
- **Needs approval**: `/quest done` with a note and maybe a screenshot. It
  goes to the owner and moderators as an alert (with Approve / Refuse
  buttons in the alerts channel) and on the Quests and events page.

Progress counts from when a player joins the quest, added up over their
linked accounts on that server, and stats-file progress says when the file
was last saved. A quest can come back every day, week (Monday), month or
year at 00:00 on the PC's clock, and can have a last day. Bedrock servers
get hours played, joins and approval only, and the page says why.

**Rewards only come from a fixed list** (ADR: a quest never holds a
command): a Discord role, an item (up to 64), Minecraft XP (up to 30
levels) and Level XP (up to 500). The item and XP commands are built from
checked values and pass the console's validation. A player who is offline
gets them on their next join, and a reward is "sent" until the server's
answer confirms it (one without an answer is never sent twice). A Discord
role reward can't be one with power in Discord, checked again before each
give. Finishing a repeating quest again stacks the role: "Dragon Slayer ×2"
(made the first time it's needed, same colour) replaces "Dragon Slayer",
up to ×10.

Players can suggest a quest with `/quest suggest`; the owner and moderators
see the idea on the Quests and events page, where **Make it a quest** fills
the form and **No thanks** turns it down, and the player hears back in
Discord. Moderators with Quests and events also change the Level roles
there.

## Events and polls

Our own, not Discord's built-in ones. The owner, and anyone with **Quests
and events** on a server, makes an event on the Quests and events page or
with `/event create` (the start typed on the PC's clock, like
`2026-10-30 20:00`; Discord shows it in each reader's own time). It's
posted in the events channel with **Going**, **Maybe** and **Can't**
buttons, and the post edits itself as answers come in. An hour before, one
message pings only the members who said Going. Under **Event reminders**
each Discord server can send a private message to each of them instead;
anyone who blocks private messages from the server is pinged. An event can start the
server 10 minutes before (skipped in maintenance mode).

Who came is worked out from real data, by the event's kind:

- **Online 15 minutes**: from our own player sessions, added up over the
  member's linked accounts, only the minutes inside the event.
- **An advancement**: earned inside the event's time. The advancement file
  keeps when each part was earned; the agent asks Minecraft to save at the
  end and counts a minute later. Not on Bedrock (no advancement files).
- **You decide**: a suggested list (who was online during it, ticked from
  15 minutes, and who said Going, unticked) waits on the Quests and events
  page for **Approve list**.

Everyone who came gets 50 Level XP plus the event's rewards (the same
templates as quests; items wait for the next join). For 7 days after the
end, the ticks can be changed: ticking gives the rewards, unticking takes
back the Level XP and anything not given yet. Every change is in the audit
log with who made it.

A poll (`/poll create` or the page) has 2 to 5 answers and closes at a set
time. Everyone in the Discord server votes once with the buttons and can
change their vote until it closes. While it's open the post only says how
many voted, so early votes don't sway later ones; when it closes the post
shows every count and the result is posted below it.

## Whitelist, addresses, the console and reports

`/whitelist add <name>` is for Discord roles with **Players**. It sends the
normal whitelist command and says "Sent" until the server's console
confirms it, like the Players page. Anyone else can use `/whitelist ask`:
a pop-up form asks for their Minecraft name and why they'd like to join. The request shows in
the alerts channel with **Approve** and **Say no**, and on the Players page
(requests are kept 7 days, one per member, 30 per server). Approving sends
the whitelist command; the asker hears the answer where they asked.

On an offline-mode server Minecraft can't check whose account a name is, so
anyone can join with a name on the whitelist. If EasyWhitelist, Zero
Whitelist or TextWhitelist is installed there, every whitelist button
(Players page, phone, Discord) uses its command instead, and the Players
page suggests them when none is. Online-mode servers always use
Minecraft's own whitelist.

A Discord server can **take people off the whitelist when they leave** it
(a tick on the Discord page, off by default). It needs the Server Members
switch, and only works for linked accounts. A stopped server gets the change
when it's started.

**New addresses.** With an IP whitelist add-on (IPWL, or the IP Whitelist
Fabric mod) a friend whose home address changed is turned away. When the
console says so for a player the server knows (played there, linked or
whitelisted), the address is kept encrypted with a key in `.env`
(`MCSC_ADDRESS_KEY`) and only ever shown masked (`203.0.x.x`). The alert
"tried to join from a new address" has **Allow** (sends the add-on's
command) and **Ignore**. Addresses are deleted when the player is taken off
the whitelist, when the add-on is removed, or with **Remove** on the
Players page. Strangers' addresses are never kept. They're in Export
everything and never in Get help.

**The console channel** is off by default and never made by setup. The
owner picks a channel for a server on the Discord page; pick one only you
can see (the checks warn when everyone can). It's read only: nothing typed
there reaches the server. Lines are posted every 2 seconds in code blocks,
with addresses, link codes and Windows user names hidden. A busy console
keeps the newest 40 lines per post and says how many were left out.

**`/report <player> <reason>`** opens a private thread off the alerts
channel with the reason and the last 20 chat lines; the reporter is in it.
The alert has **See the report**, which adds whoever presses it (it needs
Players on that server), because a role can't be added to a private thread.
Three reports an hour per member; reports are kept 30 days.

## On the phone

The Android and iPhone apps look after what the bot brings in; setting the
bot up (the token, inviting it, channels, Discord roles, Level roles) stays
in the desktop app.

- **Alerts**: Start / Say no under a start request while it can still be
  open, for someone with Start on that server. Read marks go to the PC.
- **Settings > Discord** (owner): whether the bot is connected, what Discord
  switched off, each Discord server's checks with Check again, Remove on
  leave, and the webhook-too switch.
- **Players**: Linked accounts (with Remove and Bedrock link requests to
  approve), whitelist requests, and addresses the IP whitelist add-on
  turned away, for someone with Players.
- **Quests and events** (a server's screen, for someone with Quests and
  events): the quests, claims with their screenshot, ideas, a new quest
  (with no end date; set one in the desktop app), events with who came, and
  polls. Events and polls are made in the desktop app or with `/event` and
  `/poll`.

## The code

`agent/discord/client.py` is our own small Discord client (gateway and REST,
API v10, not discord.py, to keep weak PCs light). It stops on a rejected
token and never retries it, leaves a channel alone for 10 minutes after a
403, waits as long as Discord asks on a 429 and resumes after a drop with
growing waits. `bot.py` holds the commands and buttons, `live.py` what the status
commands, status message and presence say, `bridge.py` the chat bridge's words, `progress.py` what /stats, /level, the leaderboard and the quest board say, `layout.py` the channels, roles,
Level roles, grants and checks, `links.py` the linked accounts. The stats reader is `agent/stats.py`, Levels are `agent/levels.py`, quests `agent/quests.py`, events and polls `agent/game_events.py` and whitelist requests and addresses `agent/whitelist.py`, with no Discord inside. Without a token none of it
runs and the client isn't imported.

Tests use a fake Discord (`tests/fake_discord.py`). There's also an opt-in
set against a real test Discord server, see [testing.md](testing.md).
