# Security

## Assumptions

- The agent is reachable only over your tailnet, over HTTPS. It binds to
  one address, and that address isn't routable from the internet. It serves
  the API and the live feed only, no web pages (`/` is one plain page saying
  to use the apps).
- The Minecraft PC is trusted: anyone with a login there can already do
  everything the agent can.
- The threat model: someone on your network, a malicious web page in another
  browser tab, a hostile file name from Modrinth, and your own mistakes.

## Authentication

- Passwords are stored as scrypt with N=2^15, r=8, p=3 (one of OWASP's
  Password Storage Cheat Sheet settings: 32 MiB, about 0.23 s on a 4-core
  test machine, so under a second on a weak mini PC) and a per-password
  salt. The password itself never touches disk, logs, or the database. Each
  stored hash records how it was made, so a PBKDF2 hash from before still
  works and is re-made with scrypt the next time that person signs in. A
  hash asking for more than N=2^17 is refused, so a hash from another PC
  can't make this one run out of memory.
- Signing in returns a random 256-bit session token; only its SHA-256 is
  stored, so a stolen database yields no usable tokens.
- Sessions expire after 12 hours by default, and can be rotated or revoked.
- **Keep me signed in** (for a PC you own) makes a session that lasts
  `security.remember_days` (90 by default) from its **last use**. The
  desktop app keeps its token in Windows Credential Manager. The phone app's sign-in (it names its device and asks to be kept)
  has no time limit: it ends only when it is signed out, its account is
  removed, or that account's password changes. The token stays in the
  phone's Keystore or Keychain, behind the phone's own lock. The desktop app's Signed-in devices lists every signed-in device (name,
  account, when it signed in, last used, address) with a **Sign out**
  button for each. Signing a device out deletes its session at once;
  `DELETE /api/sessions/<id>` takes the listed id (the first 16 hex digits of
  the token's hash), never the token, and an account can only sign out its
  own devices unless it is the owner.
- The installer's **Open Command Block** signs the desktop app in once
  without the password. The installer writes only the code's SHA-256 and an
  expiry 10 minutes away to `one-time-sign-in.json` in the data folder, and
  leaves the code itself in the app's own settings under the Windows
  account that ran the setup. `POST /api/auth/once` accepts it only from a
  connection that really came from 127.0.0.1 (never a forwarded header),
  only once (the file is deleted and the hash is remembered in the
  database) and only before the expiry; then it is the normal sign-in.
- A long-lived API token is supported for scripts, compared in constant time.
- Failed attempts are counted per user and per address. Five failures locks
  sign-in for 15 minutes, with `Retry-After` in the response. Helpers are
  locked out per account, the same way.
- All API requests are rate limited (120/minute per address by default),
  and again per signed-in account. Rejected requests don't count, so
  retrying doesn't extend the block.
- Helper passwords are hashed exactly like the owner's, in the `accounts`
  table. A sign-in for an unknown name still runs a full-strength hash, so
  the time taken doesn't reveal which names exist.
- The owner's password can't be changed over the network. It is reset on
  the PC with `python -m installer.reset_password`, which asks twice, writes
  only the hash to `.env`, never shows or logs the password, and deletes
  every session on that PC. A running agent notices `.env` changed and
  uses the new hash at once. `tests/test_helpers.py` checks no route
  reaches it.

## Shared accounts (Your PCs)

An owner with several PCs has one account on all of them (`agent/yourpcs.py`,
`agent/security/records.py`). There is no cloud and no main PC: each PC
keeps a full copy and sends changes straight to the others.

- **Where the owner lives.** The owner's name and hash move from `.env`
  into the database at the first start, next to the moderators. `.env`
  keeps the first-run value; a new hash there (the reset tool, the
  installer run again) counts as a reset on that PC.
- **Signed records.** The owner, each moderator, each PC's permissions for
  that moderator, each sign-in and each PC are rows of `sync_records`, each
  signed with HMAC-SHA256. The key is shared by the joined PCs and kept in
  `account-keys.bin` in the data folder, encrypted with DPAPI for the
  agent's Windows account. A row that fails its check is never used (the
  sign-in it was for is refused) and the owner gets an alert. A moderator or
  sign-in row in the ordinary tables must match its signed record. When
  the key file can't be opened (the data folder moved to another account or
  PC), a new key is made, the rows are signed again and the owner is told:
  anything able to delete that file can already run as the agent.
- **The database file** is in the private data folder. On Windows the
  agent checks at each start that no shared group (Users, Everyone, ...)
  can open it, and alerts when one can.
- **Joining.** PC A's join link is its pairing link plus a random code
  that works once, for 10 minutes (only its SHA-256 is kept). The new PC
  checks A's certificate against the link's fingerprint before sending
  anything, then sends the code, the owner's name and password (counted and
  locked out like a sign-in), its own address and certificate fingerprint,
  and its sync key. A only takes it in when an owner device that was signed
  in **before** the request presses Allow, so someone who just learned the
  password can't join their own PC. Every owner device is alerted.
- **Syncing.** PCs call each other's normal HTTPS port, pinned to the
  certificate in the other PC's record, with a header naming the PC and its
  sync key (a random 32-byte key; the others keep only its SHA-256). The
  `/api/sync/*` routes take nothing else. Newest wins by a hybrid logical
  clock. A removed moderator, sign-in or PC stays removed. Each PC's
  permissions for a moderator are only ever written by that PC.
- **Removing a PC** puts a "removed" marker on its record: every PC that
  hears refuses its sync key (410) at once, and the removed PC goes back to
  its own owner account with a new key.
- **A reset on a joined PC** works on that PC only. The other PCs show it
  and take it only when an owner device signed in to one of them before the
  reset confirms it. Then every owner sign-in made before the change ends.
- **Sign out everywhere** ends every sign-in on every PC.
- **The Discord bot across PCs** (`agent/discord/botpc.py`). The Bot PC's
  token is in a signed record, encrypted (AES-GCM) with a key made from the
  account's signing key, so it can only be read on the owner's PCs; it goes
  only PC to PC over the pinned connection, and no route sends it back.
  A **Bot link** is a random 32-byte key per PC made by the Bot PC, kept in
  its `account-keys.bin`; the other PC keeps only its SHA-256. The other PC
  takes a Bot link call only from the PC its record names as the Bot PC,
  still in Your PCs, and only what Discord can do
  (`permissions.BOT_LINK_CALLS`: a slash command or button from the bot's
  own list, a chat line, someone leaving Discord, unlinking an account, the
  leaderboard's numbers). It is never a sign-in: the API refuses it. A
  passed-on command is checked on that PC against the Discord member's
  roles, like one from Discord itself. Each PC's own news for the bot (its
  servers, Levels XP, linked names, wake key) is only ever written by that
  PC.

## Secrets

Secrets are read from environment variables via `.env`:
`MCSC_ADMIN_PASSWORD_HASH`, `MCSC_API_TOKEN`, `MCSC_DISCORD_WEBHOOK`,
`MCSC_DISCORD_BOT_TOKEN`, `MCSC_SMTP_USERNAME`, `MCSC_SMTP_PASSWORD`.

The Discord bot's token is pasted in the desktop app and written straight to
`.env`; no route sends it back. A Discord member using the bot gets a sign-in
of kind `discord` built from their Discord roles' grants, checked by the same
`permissions.check` as every route. It never holds a moderator's account
extras or anything owner-only (see [discord.md](discord.md)).

A rented server's Pterodactyl client API key (`agent/rented.py`) is saved
in `rented-keys.bin` in the data folder, encrypted with DPAPI for the
agent's Windows account and readable only by SYSTEM, Administrators and
that account, like the separate Windows user's password. It's sent only
to that panel, as a bearer token, and dropped on any redirect. No route
answers with it, and it's never logged. Remove key deletes it. The app
asks for a client key, never the panel password or the Application (admin)
API, and points out when the key belongs to the main panel account rather
than a limited sub-user.

A Tebex store's Game Server secret key (`agent/store.py`) is saved the
same way, in `store-keys.bin`. It's sent only to Tebex's Game Server API
(`plugin.tebex.io`, the `/information` and `/packages` reads), in the
`X-Tebex-Secret` header, dropped on any redirect, and written into the
Tebex plugin's own `config.yml`, where the plugin reads it. No route
answers with it, it's never logged, and moderators can't reach the store.

Two files leave the PC on purpose, and both are built to hold no secrets
unless asked:

- **Export everything** (Move to a new PC) holds settings, data and server
  folders. `.env`'s values, helpers' password hashes, phone subscriptions
  and `server.properties`' RCON and management passwords go in only when
  "Passwords and keys" is ticked, and then only inside `secrets.bin`,
  encrypted with AES-256-GCM under a key derived from a passphrase with
  scrypt (n=2^15, r=8, p=1). The passphrase is never stored. Without it, the
  plain parts have those passwords blanked.
- **Get help** holds this app's logs, the install logs, a fresh `--check`
  report and version facts. Never `.env`, `config.yaml`, the database,
  certificates, worlds, backups or Minecraft's own logs (they hold players'
  addresses). Every line is passed through redaction first: the actual
  secret values in use, password hashes, bearer tokens, webhook URLs and
  `password=`-style pairs are cut. The file list is shown before it's made.

`tests/test_first_run_safety.py` builds both with known secret values and
fails if any of them is found inside.

None of these appear in `config.yaml`, in source, or in the apps, and
`.env` is gitignored. Setup makes `.env` readable only by SYSTEM,
Administrators and the account that runs the app (`icacls`). If that
fails, setup says so as a failed step with what to do, rather than
carrying on, and `--check` reports who can open the file. The settings API reports only whether a secret is
configured, never its value.

## Command injection

There's no endpoint that runs a shell command. Minecraft is launched with an
argument list and no shell, from a fixed working directory, using values
that only come from `config.yaml`.

The Players page's buttons (whitelist, operator, kick, ban, unban) never
take a command from the app. The API takes an action name from a fixed
list and a player name that must be a Minecraft name (`^\.?[A-Za-z0-9_]{1,16}$`,
Floodgate's `.` allowed); kick and ban take an optional reason limited to
one short line of letters, numbers and basic punctuation. The command is
built from a fixed template and then passes the same validation as anything
typed in the console (`agent/minecraft/commands.py`). Kick and ban need
`confirm: true`. See `agent/minecraft/playeractions.py`.

The chat box is the same story. A message sent from the Chat page becomes a
`say` command and passes exactly the same validation
(`agent/minecraft/chat.py` calls `agent/minecraft/commands.py`), with a
narrower character set still: letters and digits (of any language) and
basic punctuation, up to 220 characters, no newline, carriage return or
other control or invisible character, so one message can never become two
commands. Only the chat box (and the Discord bridge through it) may send
letters outside ASCII (`validate(any_letters=True)`, `commands.any_language`);
symbols, `§` and every command typed in the console keep the ASCII set. Chat shown in the apps is read from the server's
own console output; nothing is reconstructed from what this app sent.

The Discord chat bridge sends Discord messages through that same chat `say`,
never from `agent/discord/`. Before that, `@` becomes "at", so target
selectors (`@a`, `@e`, `@p`, `@r`, `@s`, `@n`) never expand, and lines that
look like a command or a console line are refused. Each line starts with
"[Discord]", which no other `say` may start with, so players can tell a
Discord member from the owner.

Auto-sleep adds no new way to run anything either: it stops a server through
the same code path as a scheduled stop.

Keeping the PC awake runs no program either. It calls Windows'
`SetThreadExecutionState` directly through `ctypes` from the agent's own
thread, and reads the battery (psutil) and the lid setting
(`powrprof.dll`, read only). The apps can only turn it on or off.

The folder picker (`GET /api/folders`, owner only) lists folder names inside
one folder, and whether each holds a `level.dat`. It never lists files or
reads their contents, and it follows no links. Whatever folder is picked is
checked again by the feature that uses it: an off-PC copy folder must exist,
be writable, not be a system folder and not overlap the servers or the
app's data (`agent/backups/offsite.py`); a world folder must hold a
`level.dat`.

A world brought in as a `.zip` is read through the same zip-slip check every
extraction uses (`check_archive_member`) before anything is written; a zip
with a path that would land outside the server folder is refused whole.

### The one program the agent runs that isn't Minecraft

Forge, NeoForge and Quilt publish an installer rather than a server jar: it
has to be run once so it can write out the libraries and, for Forge and
NeoForge, the `win_args.txt` the server is launched from. `agent/servertypes/
install.py` is the only place that does it, and the rules are:

- the command is built in that module as a list (`[java, "-jar",
  <installer>, *arguments]`), never a string, never through a shell. The
  arguments are fixed per type in its capabilities (`installer_args`):
  `--installServer` for Forge and NeoForge, and `install server <minecraft>
  <loader> --download-server --install-dir=.` for Quilt. The only values
  filled in are the Minecraft and loader versions, after `check_version`
  and after matching the list the official source returned;
- the installer's file name is generated by this app from the plan it built
  out of the official source's answer, never taken from a request, and the
  resolved path is checked to be inside that server's own folder;
- the working directory is fixed to the server folder, `stdin` is
  `DEVNULL`, and output is captured into a timestamped file under the
  server's own `install-log` folder rather than a console;
- it is killed after 20 minutes, and a non-zero exit is an error with the
  log's path, not a silent success;
- the version is checked against the list the official source returned
  before anything is downloaded, and a version string that isn't a version
  never reaches a URL or a file name (`check_version`).

`tests/test_command_isolation.py` pins all of this down, and also keeps a
list of every file in the agent that starts a program at all: a new one
fails that test until it is added on purpose.

### Diagnose server's test player, after a click

"Diagnose server" joins the server with a test player: Mineflayer (Java) and
bedrock-protocol (Bedrock), Node.js libraries (MIT), run by the Node.js that
ships with the app in `tools/diagnose`. The release build downloads node.exe
and the npm packages pinned in `bot/package-lock.json`
(`scripts/build_bot.py`), checks each against its pinned checksum (Node's
SHA-256, npm's sha512), and unpacks them itself, so npm and package install
scripts never run; nothing is downloaded when it runs. The one package npm
takes from GitHub (prismarine-xbox-services, which has no checksum to check)
is kept in `bot/vendor` instead, at the commit the lock file names.
`agent/joinbot.py` is the only place that starts it, and only inside a run
someone started with `server.diagnose` (owner only for now), or a "Sign in
with Microsoft" the owner clicked (`microsoft.account`, owner only):

- the command is a list (`[node.exe, join.js]`), never through a shell. What
  to do goes in on stdin as one JSON line, never on the command line (which
  other programs on the PC can read). Host and port come from the server's own
  settings, the name is the fixed `CommandBlock` or the owner's signed-in
  account, the chat line is made by the agent;
- only its JSON lines on stdout are read, and it is killed after 4 minutes
  (15 minutes while a sign-in waits for its code to be typed). It runs as the
  agent's user, joins only the server's own address, and exits when the join
  is done.

### The owner's Microsoft sign-in

Online-mode Java servers and Bedrock servers can only be joined with a real
account, so Diagnose server uses the owner's own, after they sign in once with
a device code (`agent/microsoft.py`). The test player (prismarine-auth) talks
to Microsoft directly. The tokens it gets back are kept only on this PC, in
`microsoft.bin`, encrypted with DPAPI for the agent's Windows account like the
panel and store keys, and handed to the test player on stdin for each join; it
never writes them to disk itself. They are never sent to Firebase, Discord or
the phones, and no API answer holds them (`GET /api/microsoft` gives only the
Java name, the gamertag and, during a sign-in, the code to type). Sign out
deletes the file. A join that would kick the owner out (they're playing as
that account) asks first.

After a run, Diagnose offers changes only for problems that run saw
(`agent/diagnosefix.py`): Safe (restart, 1 GB more memory when the PC has
room) and Risky (accept the EULA, whitelist or unban the owner's own
account). Nothing changes until someone picks Apply all, Apply safe or Deny
all; Apply all is owner only when a risky change is listed. The picked
changes run through the safe-change routine (one backup of the files they
touch, one Undo), the joins run again, and a change that didn't help is put
back. A change tried once is never offered again in that run. Nothing
deletes a world, a mod or a backup.

The other ways in (`agent/waysin.py`) only ask whether the game port answers:
from this PC on its home network and Tailscale addresses, from the owner's
other PC (Your PCs, `POST /api/sync/ping`, sync key only, home network and
Tailscale addresses only, never a name), and from the internet through
mcsrvstat.us for the address the owner typed. Two more changes come from
them: "Turn Tailscale back on" (safe; `tailscale up` with the desktop app's
flags, only when Tailscale is signed in but switched off, `tailscale down`
puts it back) and "Let port N through Windows Firewall" (risky; only when the
port answers on this PC but not from outside, and no rule of ours is there).
The firewall rule is house rule 2's Diagnose exception: `agent/firewall.py`
runs `System32\netsh.exe` with a fixed argument list as Administrator
(Windows asks first; No means not made), and the rule's name is made from the
server's id, protocol and port, so nothing typed reaches it. Router ports
aren't opened (UPnP comes later): the list says which port to forward.

The join with all mods (`agent/fullmods.py`) runs the real Minecraft game
through HeadlessMC (MIT, `agent/headlessmc.py`), only after the owner said
yes in a Diagnose they started (house rule 2's Diagnose exception). Its
Windows launcher is downloaded from HeadlessMC's GitHub releases at a pinned
version, checked against a pinned SHA-256; GitHub's paths stay closed to it
outside that one download. It downloads the game, its mod loader and Java
itself. The command is a list, never a shell; the version, the loader and
the address come from the server's own settings. Everything it keeps is
inside `headlessmc/` in the agent's data folder (its XDG folders point
there), and the game it starts is stopped with it when a try ends. Its
Microsoft sign-in is HeadlessMC's own (Mark's pick): HeadlessMC keeps it in
its own file in that folder, not encrypted by us; Sign out deletes it. Only
its name and the sign-in page reach the apps (`GET /api/headlessmc`,
`microsoft.account`, owner only). The two changes it offers, another version
of the mod the game won't start with (safe, from the site the mod came from
through the normal update, checksum included) or turning that mod off
(risky), stop the server while they're made and go through the safe-change
routine with a backup of the mods folder. Nothing is deleted.

### Chunker world conversion, after a click

Making a Java copy of a Bedrock server (or a Bedrock copy of a Java one)
runs Chunker (Hive Games, MIT, github.com/HiveGamesOSS/Chunker), the
open-source world converter. The release build builds its command-line jar
from source at a pinned tag and commit (`scripts/build_chunker.py`) and ships
it as `tools/chunker-cli.jar`; nothing is downloaded when it runs.
`agent/servertypes/chunker.py` is the only place that runs it, and only
after the owner's click (`servers.manage`, which no moderator can have):

- the command is a list (`[java, -Xmx<size>, "-jar", <shipped jar>, "-i",
  <folder>, "-o", <folder>, "-f", <format>]`), never a string, never through
  a shell. Both folders are made by this app inside its own data folder. The
  format is built from a version checked against the pinned Chunker's own
  list of versions, so nothing from a request reaches it as text;
- Chunker reads a copy of the world, so the original server is only read,
  and the result becomes a new server, which is removed again if anything
  fails;
- `stdin` is `DEVNULL`, output is captured into the original server's
  `install-log` folder, it gets a memory limit of a quarter of the PC's
  memory (1 to 4 GB), and it is killed after 3 hours. A run that doesn't
  leave a `level.dat` is an error with the log's path, even when Chunker
  exits with 0.

### The separate Windows user

With "Run servers as a separate Windows user" on (the default for new
installs), Minecraft, Chunker and the loader installer run as the hidden
local account `CommandBlockServer` instead of the owner, so a harmful mod or
plugin can't read the owner's files, the app's data folder or its keys
(ADR 0007). `agent/serveruser.py` `start()` is the one place that starts
them, and:

- it asks the starter service (below) over its named pipe, with the same
  console pipes as before. Python's own process call is wrapped only while
  `start()` runs, for that one asyncio task; every other program the app
  starts is unchanged;
- when the setting is on and the account, its password or the service is
  missing or refused, nothing starts and the app says "The separate
  Windows user is missing" with Repair. There is no code path that falls back to the owner;
- before a start it gives the account change rights (`icacls ... /grant
  CommandBlockServer:(OI)(CI)M`) on that program's own folder only (the
  server folder, or Chunker's work folder), and read-and-run on the Java
  folder. Backups, the data folder and the owner's profile get nothing.
  The agent owns those folders, so this needs no Administrator;
- the started process's access list gives the agent's own account only
  what it uses (stop, CPU cores, reading memory and CPU use).

The starter service (`agent/starter.py`, `CommandBlockStarter`, runs
`mcsc.exe starter-service` as SYSTEM). At boot the agent runs from a task
with nobody signed in, and from there Windows can't set up a program
started with `CreateProcessWithLogonW` (it stops with 0xC0000142), so a
small service does the starting (the fallback ADR 0007 planned). The agent
itself keeps the owner's normal rights. The service:

- only answers on `\\.\pipe\CommandBlockStarter`, made with
  `FILE_FLAG_FIRST_PIPE_INSTANCE` and `PIPE_REJECT_REMOTE_CLIENTS`, owned by
  SYSTEM, open to SYSTEM and the owner's account only (the account the
  installer ran as, which the startup task runs the agent as). It checks the
  asking process's account again before reading the request, so
  `CommandBlockServer` (a harmful mod) can't use it. The agent opens the pipe
  "identification only" (so the other end can't act as the owner) and
  checks it's owned by SYSTEM before sending anything;
- only ever signs in as `CommandBlockServer` (the name is in the code, not
  the request), with the password only it can read. Nothing in the request
  picks the account, the environment or which of its own handles the
  program gets: only the three console pipe ends, copied from the asking
  process itself, are passed on (`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`).
  Requests are checked (`check_request`): full paths, no NUL characters,
  normal priority classes only (never realtime);
- starts the program on a window station and desktop of its own
  (`CommandBlockServers\Default`) that only SYSTEM and the account may use,
  never on SYSTEM's shared service desktop;
- hands the agent a process handle with only the agent's rights, and stops
  the program again if it can't;
- costs nothing while it waits (one blocked thread), and is stopped while
  an update replaces the program folder.

Not checked on purpose: which program is started. The owner could run any
program as themselves anyway, and the account has fewer rights than the
owner, so a list of allowed programs would protect nothing.

The installer (Administrator) makes the account with Windows' own calls
(`NetUserAdd`, `LsaAddAccountRights`; no program is run, so the password is
never on a command line): password never expires, Remote Desktop and
network sign-in are denied, and it is left off the sign-in screen. A
keyboard sign-in stays allowed because the starter service signs in that
way (`LogonUserW`, interactive); nobody knows the password. The password is
40 random characters, saved only for the starter service:
`Program Files\Command Block Starter\starter.json` (SYSTEM and
Administrators only, encrypted with DPAPI for this PC), never in the data
folder, and never logged, printed or sent by the API. The agent never has
it. Settings' Turn on, Repair and Turn off run `mcsc server-user
on|repair|off` as Administrator (Windows asks); Repair also restarts the
service so it reads the new password. Turn off and removing the app delete
the service, its folder, the account and its profile folder.

### Updating the app itself

The second exception. The desktop app shows "Version X is out" and, for the
owner (`app.update`; helpers never have it), an **Install update** button
behind a confirmation that says who is playing. Nothing the app sends
becomes part of a command or a file name: the request carries no body at
all. What happens:

1. **The agent checks the release** (`agent/updates.py`). It reads this
   repo's latest release from `api.github.com/repos/<repo>/releases/latest`
   (only this repo's release paths are on the download allow-list), then
   fetches that version's `update.json` and `update.json.sig`. The
   signature is checked **first**, with the Ed25519 public key built into
   the app (`agent/signing.py`, `UPDATE_PUBLIC_KEY`). A file that isn't
   signed, is signed with any other key, or names another product, repo,
   version or file name is refused (`agent/signing.py: verify`). A copy
   built with no key built in never installs updates by itself; it says
   so and links the release page.
2. **It downloads the setup file** named in the signed file, from that
   release only, as `<name>.exe.download` (the agent never writes a file
   ending in `.exe`, see `agent/security/paths.py`), and checks its size
   and SHA-256 against the signed values. A mismatch deletes it.
3. **It takes a backup** of `config.yaml`, `.env` and the database into
   `updates/backup-<version>`, and writes `updates/request.json` holding
   only the version.
4. **It asks Windows to run one fixed task**: `schtasks /Run /TN
   "Command Block updater"`, an argument list with no
   values from anywhere (`tests/test_command_isolation.py` pins the exact
   list). The installer made that task; it runs `mcsc.exe apply-update`
   from Program Files, which only Administrators can change.
5. **`apply-update` trusts nothing but the version number**
   (`installer/apply_update.py`). It builds the file name itself, fetches
   and verifies the signed `update.json` again, reads the downloaded bytes
   once, checks their SHA-256, writes exactly those bytes into a folder next
   to the program folder that only Administrators can change, and starts
   that copy with fixed arguments (`--update --quiet --from-app
   --program-dir <program folder>`). A file swapped after the check is
   never the one that runs. That setup unpacks the program it carries into
   the same Administrators-only folder (`installer/bootstrap.py:
   unpack_parent`), not the person's temporary folder, which the agent's
   own account could change.
6. **The setup updates as it would by hand**: a restore point first, then
   the new files, then it checks the agent answers. If anything fails, it
   puts the restore point back by itself and says so. The outcome is
   written to `updates/last-update.json`, which the desktop app shows once,
   and an `update_finished` or `update_failed` alert is sent.

The desktop app runs from the program folder, so it has to be shut first.
Once step 4 has started the task, the agent sends `update_closing_app`
over the live feed and the app on that PC closes itself (an app on another
PC ignores it); the setup also ends any `CommandBlock.exe` still open
before it swaps the folder. When the update is done, put back, or refused
by `apply-update`, the fixed task "Command Block reopen" runs, which starts
`CommandBlock.exe --after-update` in the person's own Windows session with
normal rights. The app opens only if it was open when the update closed it.

Tests: `tests/test_updates.py` refuses an unsigned release, a bad signature,
a signature from another key, a signed file naming another version or file,
and a setup file that fails its checksum (each before anything runs).

These two (the loader installer and the update) are the only programs the
apps can cause to run besides the Minecraft server itself, and both are
listed in `tests/test_command_isolation.py`'s allow-list.

### What the desktop app does on its PC

The desktop app (`desktop/`) talks to the agent only through its API. On
the PC it runs on, this is everything it does (`desktop/pc.py`, with the
command isolation test covering it):

- Keeps "Keep me signed in" in Windows Credential Manager, never in a file.
- Keeps its own small settings in `HKCU\Software\Command Block`, and the
  Run entry that opens it hidden when Windows starts.
- Reads the Windows text size setting.
- Shows a notification-area icon and Windows alerts. For alerts with
  buttons (Open, and Restart for a crash or a slow server) it registers its
  name and icon for Windows under
  `HKCU\Software\Classes\AppUserModelId\CommandBlock.Desktop`, removed
  when the app is removed. Restart is the same API call as the app's own
  Restart button, with the same permission.
- Opens links in the browser, only `https://` ones: a link can come from
  the agent, maybe on someone else's PC, and on Windows a path there would
  run a program. The one exception is a live map add-on's page, which is
  plain `http://` and is opened only at a Tailscale address
  (100.64.0.0/10). The app never opens a router port for a map.
- When the agent isn't running, turns its startup task back on and runs it
  (`schtasks /Change ... /ENABLE` and `/Run`, fixed argument lists).
- In the Tailscale step, after a click: downloads, checks and installs
  Tailscale and runs `tailscale up` or `tailscale logout` (see Downloads).
  PC settings' Tailscale card opens the same step.
- When a crash was caused by the wrong Java, after a click on "Install
  Java" and only on the agent's own PC: opens Command Block setup
  (`setup.exe` from the program folder, no arguments) so the person can use
  its "Java for your servers" step. The agent never runs it.
- Closes itself for an update and is opened again afterwards (see
  Updating the app itself).
- Uses Windows' own save box and file and folder pickers. It writes only
  the file the person chose in the save box (a backup, a world, the Get
  help file, the export for a new PC), and reads only the file or folder they picked or dropped on
  the window. A world folder picked on a PC other than the agent's is
  zipped into a temporary folder there, sent, and the zip deleted; on the
  agent's PC the agent reads the folder itself.
- Puts a pairing link or a moderator's invite on the clipboard when Copy is
  pressed.
- Wake-on-LAN, set up in Technical mode only. The agent records the PC's
  wired adapter (its MAC and broadcast address, read from Windows' own
  adapter list, no program run). Pressing "Wake <PC>" sends the wake
  signal (6 x `FF` and the MAC 16 times, UDP port 9) to that one recorded
  PC only, and asks the helping PC, if there is one. Nothing changes a
  Windows setting: the checklist says what to tick.
- "Help wake <PC>", after a tick, on another PC at home: listens for wake
  requests on its own Tailscale address only (UDP 47391). A request must
  carry a wake key whose SHA-256 is in the list the agent gives that PC
  (refreshed every 5 minutes, so a signed-out device's key stops working),
  and it wakes at most once every 30 seconds, only the recorded PC. Each
  device's wake key is made by the agent, kept on that device, and only its
  hash is kept by the agent.
- On a moderator's PC (a copy for connecting only, no agent): the same
  Tailscale install and sign-in, after a click, for that PC only; opens the
  owner's Tailscale share link (only `https://login.tailscale.com/...`);
  and once a day asks GitHub for the latest release. It offers only a
  newer one and installs it only after "Update now": the signed
  `update.json` is checked with the built-in key, the setup file is
  downloaded as `.exe.download` and refused unless its SHA-256 and size
  match, then the checked bytes are written as the setup file in that
  person's own data folder and run for them with fixed arguments
  (`--update --quiet --from-app --program-dir <program folder>`, no
  admin). There is no privilege boundary to cross: the setup runs as the
  same person whose folder it is in (`desktop/self_update.py`).

### Installing on a moderator's PC

`setup.exe` starts without Administrator rights. "Connect to someone else's
PC" installs only the desktop app, for that Windows user: the program in
`%LOCALAPPDATA%\Programs\Command Block`, install logs in
`%LOCALAPPDATA%\Command Block`, a Start menu folder, an optional desktop
shortcut, an optional `HKCU` Run entry, and an `HKCU` "Installed apps"
entry. No task, firewall rule or service. Anything else (running servers,
updating or removing the owner's copy) starts the setup again with
Administrator rights (`ShellExecute` "runas" on the same setup file, with
the same arguments). Removing a moderator's copy signs that device out on
the owner's PC when it answers, deletes the saved sign-in from Credential
Manager and the app's own settings, and leaves Tailscale installed.

### Tailscale share links (Technical)

The owner can save a Tailscale OAuth client (id and secret) or an API
access token once, so "Make share link" asks Tailscale's API for a
one-time share invite for this PC (`agent/tailscale_share.py`). The key is
checked with Tailscale before it is kept, kept in the data folder's
`secrets/` like the Firebase key (owner-only folder), never sent back by
the API, and only sent to `api.tailscale.com`. "Remove key" deletes it and
links to Tailscale's admin page to revoke it. An API token runs out after
1 to 90 days; the agent alerts a week before. An OAuth client should get
only the device-invites scope and read access to devices.

### Bedrock servers: `bedrock_server.exe`

A Bedrock server is a Windows program, not a jar, so for this server type
the "Minecraft itself" that `agent/minecraft/process.py` launches is Mojang's
`bedrock_server.exe` from the download (the download-and-run
exception). The rules:

- it is downloaded only from Mojang (the address Mojang's download links
  service names, on `www.minecraft.net/bedrockdedicatedserver/`), through the
  downloader's host and path allow-list, and only after the person has
  ticked that they accept Mojang's EULA and Privacy Policy. Mojang publishes
  no checksum, so it is recorded as **unverified**; its SHA-256 goes into the
  install log and is compared with any earlier download of the same version;
- the zip is checked before anything is written: every entry passes the
  zip-slip check, links are refused, its unpacked size is capped, and it must
  hold the program;
- the launch command is a list with exactly one item, the program's full
  path: `[<server folder>/bedrock_server.exe]`. No shell, no arguments (it
  reads `server.properties` itself). The file name must be one of the two
  names Mojang uses (`bedrock_server.exe`, or `bedrock_server` on Linux) and
  resolve inside the server's own folder, or the server isn't started
  (`EXE_NAMES` and `launch_problem`). `server.jar` can't be changed from the
  apps (it isn't in `SETTABLE_PREFIXES`);
- `tests/test_command_isolation.py` checks the command's shape and that a
  name other than Mojang's, or a path outside the folder, is refused.

### The owner's own server jar, and servers brought in

In Technical mode the owner can pick their own server jar (a fork they
trust) and say which type it's based on (`agent/servertypes/custom.py`).
It is copied into the server's folder and launched the same way as any
server jar (`java -jar <jar>`), only after the owner's own click on Start.
Only the owner can set or replace one: those routes need `servers.manage`,
which no moderator can be given. The app never downloads, checks or
updates it: the record says `custom_jar: true`, the version check and the
newer-version notice skip it, a version change is refused, and it is never
shown as checked. A jar on the safety notes' bad-file list is refused.

"Bring a server in" (`agent/bringin.py`) copies a server folder or zip into
a new folder; the original is never changed. Its start scripts are read as
text to find the loader and are never run; a zip passes the zip-slip check,
may hold no links and must fit on the disk. A Forge or NeoForge pack that
ships only its installer gets the official installer from the loader's
Maven (the loader installer exception above), never the one in the zip. An
unknown kind comes in only as the owner's own jar, with the same warning
and tick.

#### The signing key

The private half of the update key never goes in this repo. It is a GitHub
Actions secret (`MCSC_UPDATE_SIGNING_KEY`) that only the release workflow's
"Sign the update file" job reads, plus the maintainer's offline copy. That job runs
in the `release` environment and installs nothing but the app's own
hash-locked libraries, so no build or test tool (PyInstaller, pytest,
and what they pull in) ever runs while the key is loaded
(`tests/test_release.py` checks this). `docs/releases.md` has how to make it.

**If the private key leaks** (or is lost): make a new pair with
`make-update-key.cmd` (type `REPLACE` when it asks), which puts the new
private key in the GitHub secret and writes the new public key into
`agent/signing.py`; commit that;
release a new version and tell people to **install that one by hand** from
the release page, because copies with the old key will refuse anything
signed with the new one (that is the point). Delete the old secret. Until
then, someone holding the old key could sign an update the old copies
accept, but they would also need to publish it as a release of this repo.

#### What isn't covered yet

The setup file itself isn't code-signed (Authenticode), so Windows
SmartScreen warns "Unknown publisher" on a manual download. The update
signature above covers in-app updates either way. `docs/installation.md`
has the path to code signing.

Console commands pass through `agent/minecraft/commands.py`: single line,
length capped, restricted character set, first token must look like a
Minecraft command, and shell metacharacters (`` ` ``, `;`, `&&`, `||`, `$(`,
newlines, NUL) are rejected. `MinecraftServer.send_command` also refuses
anything containing a newline, so one call can't become two commands.

## Path traversal and arbitrary writes

`agent/security/paths.py` is the only way file names from outside reach
disk. It enforces a single path component, no separators, no `..`, no
Windows reserved device names, an extension allow-list, and a blocklist for
executable extensions. Every resolved path is checked to be inside its base
directory; symlinks and NTFS reparse points are refused rather than
followed.

Backup restore also validates every member of a zip before extracting, so a
crafted archive can't write outside the server folder (zip-slip). The check
is `check_archive_member` in `paths.py`, shared with modpack import and
server duplication: an absolute path, a drive letter, `..` that climbs out,
or a link (a zip entry marked as a symlink) is refused.

**Game settings** write only `server.properties` in the server's own folder,
through a temporary file and a rename, after every known key's value has
passed its check. A copy of the old file is kept as a safety backup first.

**Duplicating a server** writes only inside the new folder, which passes
`check_new_server_folder` (below). It reads only the source server's own
folder, never follows links out of it, and leaves the agent's own folders
behind if they sit inside it. A running source is told to save and pause
saving (`save-all flush`, `save-off`, then `save-on`), so the world is never
copied while Minecraft is writing it; a server that is starting or stopping
is refused. A copy that fails its check is deleted again.

**Modpack import** (`agent/modpack.py`) refuses the whole pack if any
override path would land outside the server folder or is a link, and
refuses any mod whose path isn't a safe single file name inside the folder.
Client-only folders and files with executable extensions in the overrides
are skipped. An uploaded pack is kept in the data folder under a random
token, at most 1 GB, and cleared after 24 hours if not imported.

Two routes take a folder path: adding a server that already exists (`POST
/api/servers`) and creating a new one (`POST /api/new-server`). The second
goes through `check_new_server_folder`, which requires an absolute path
whose parent exists, and a folder that is either absent or empty, never a
drive root, the home folder, a system folder, this app's own folder or
another server's. Creating a server writes `server.properties` and
`eula.txt` and installs the software there; it starts nothing.

For a server that already exists: `check_server_folder` requires an existing, absolute, real
folder (no symlink or reparse point) that holds the named `.jar`, is not a
drive root, the home folder or a system folder, and does not overlap the
agent's own folders or another server's. Registering a folder changes nothing
in it and starts nothing. The Java program and any launch command are never
taken from the apps: a new server uses the first server's `java`, and
`raw_command` is config-file only. Removing a server only takes it off the
list; its folder is never deleted.

The CPU core limit (`server.cpu_cores`) is a list of core numbers, checked
against the cores the PC really has. The agent applies it with the operating
system's own call on the process it started (psutil), never with a command,
and adds only the `-XX:ActiveProcessorCount=<n>` JVM flag it builds itself.

## The data folder

The default data folder (`C:\ProgramData\Command Block`) holds
the database, with its sign-in sessions, and the HTTPS private key. A folder
made inside ProgramData lets every account on the PC read it, so on every
start the agent checks the folder's real permissions and, if other accounts
can open it, makes it private to the agent's account, SYSTEM and
Administrators. `python -m agent.main --check` shows the result under Storage
("Data folder access"), read from the folder itself, in any Windows language.
A folder you set in `paths.data_dir` is left as you set it, and `--check`
tells you if other accounts can read it.

## Minecraft's rules (the EULA)

For a Bedrock server there is no `eula.txt`; instead the Bedrock server is
not downloaded until the person ticks that they accept Mojang's EULA and
Privacy Policy (`POST /api/bedrock/terms` or the New server panel's box).
Who ticked it and when is recorded and in the audit log.

`eula.txt` is written with `eula=false` unless the person has ticked the box
themselves. `POST /api/servers/{id}/eula` is the only thing that writes
`eula=true`, and only from their own tick: the agent never accepts Mojang's
rules on somebody's behalf, and a new server is refused outright without it.

## Permissions

Every REST route declares the permission it needs (`server.control`,
`backups.restore`, `servers.manage`, …, listed in
`agent/security/permissions.py`), and a test fails if a route is added without
one. WebSocket messages are checked the same way.

There are two roles. The **owner** (the account in `.env`, and the API
token) holds every permission. A **moderator** ("helper" in the code for
now) gets permissions per server, picked by the owner from three
presets (Start and stop, Look after it, Run it) or one switch at a time.
Some permissions belong to no server (`account_permissions` in
`/api/auth/me`); everything that changes the PC itself (PC settings,
moderators, updates, export, Get help, the security page, which shows
sign-in addresses) is the owner's alone. A moderator is either limited to
some servers, and every per-server route is refused for the others
(`server_access`), or "All servers", which also gives them the owner's
chosen permissions on each new server. The server list and jobs leave out
servers they can't see, and the live connection carries none of their
events. A permission added later is the owner's alone until it is added to
a preset on purpose. `tests/test_helpers.py` and `tests/test_moderators.py`
walk the routes as a moderator and expect 403 wherever their permissions
don't allow it. Making players operators needs `players.op` (an operator can
type any command in the game, so it is the console by another door).

A moderator joins with an **invite**: the owner adds them by name and the
agent makes a link and QR code, good once, for 24 hours, where they choose
their own password. Only the owner can reset it, which signs them out
everywhere and makes a new invite. Wrong invite links count towards a
lockout for the address that tried them, never for everyone.

The PC's own settings and its alert log are the owner's alone, to read as
well as to change; a moderator's screens read each server's settings. The
live connection sends a moderator every
event of their servers, including ones the Activity page shows; the phones
hide the Console, Chat, Activity and Crashes pages from moderators without
"See the Console".

## Downloads

Everything the agent downloads (server software, loader installers, mods
and plugins, Geyser and Floodgate) goes through `agent/downloads.py`, which:

- allows HTTPS only, to an allow-list of hosts (Mojang, FabricMC, QuiltMC,
  Forge, NeoForged, PaperMC, PurpurMC, Modrinth, Hangar, CurseForge's file
  servers and our CurseForge relay, GeyserMC). On hosts that serve other
  people's files or other pages (GitHub; Mojang's Bedrock hosts; Hangar;
  Google's sign-in hosts; the relay; CurseForge's file servers), only the
  listed paths are allowed (`GITHUB_PATHS`, `SHARED_HOST_PATHS`). One more GitHub repo's release list and files are allowed
  only while an add-on from a pasted link to it is read, added or updated
  (`downloads.allow_releases`); any other pasted link is never fetched. The host is
  re-checked on every redirect hop, and redirects are followed by hand
  rather than by the HTTP client, so a redirect can't leave the list;
- allows a rented server's Pterodactyl panel only after the owner ticked
  "Let Command Block talk to this panel", only inside that panel's own
  calls (`downloads.allow_panel`), only HTTPS on port 443, and only its
  client API (`/api/client`). The console uses the `wss://` address the
  panel gives with a short-lived token;
- caps the size of a list (20 MB) and of a file (300 MB), and stops reading
  rather than filling the disk;
- writes to `<name>.part` and renames it into place only after the file has
  been checked, so a failed download leaves nothing behind;
- verifies the strongest checksum the source publishes (SHA-512, SHA-256,
  SHA-1 or MD5 in that order) and deletes the file on a mismatch.

Where a source publishes no checksum (the Fabric launcher jar is one), the
file is recorded as **unverified** and the apps say so, rather than
the result being presented as checked (see `docs/honesty.md`). Mod downloads
additionally refuse anything Modrinth or Hangar hasn't published a checksum
for. A plugin Hangar only links to on another site is shown as a link, never
downloaded. A file from a GitHub release is checked against the SHA-256
GitHub lists when there is one, and like a dropped file it is shown as "Not
checked by Modrinth".

CurseForge is reached only through our relay, which holds CurseForge's key
(the app never has one) and allows five read-only calls. The agent's
anonymous Firebase sign-in token goes only to the relay: it is dropped from
any redirect. CurseForge files are checked against CurseForge's sha1 and
shown as "Checked (sha1)"; a file CurseForge flags as malware is refused
even when dropped in by hand, and a mod whose author keeps it from other
apps is never downloaded from a made-up link.

Modpack mods are stricter still: each one must be on `cdn.modrinth.com`
(a CurseForge pack's: on CurseForge's file servers, with CurseForge's sha1)
and must come with a SHA-512 in the pack; anything else blocks the import
before a single file is downloaded. Each file is checked against that
SHA-512 (`allow_unverified=False`), and a mismatch stops the import: a new
server is removed again, and an existing one is put back from the backup
taken first.

Version lists (never files) are kept as saved copies in the data folder
(`agent/savedlists.py`), written only after the answer was read correctly.
When a site is down the saved list is shown with its age; a file picked
from it is still downloaded from the official site and checked as above.

The signed notes file (`data/catalog.json`, published as `catalog.json` on
the `data` pre-release, `agent/notes.py`) is read at start and once a day,
from this repo's release path only. It is signed with its own Ed25519 data
key, separate from the update key, and refused as a whole if the
signature, serial (it only goes up), size (256 KB), shape or any link
(https only) is wrong. It can only add warnings, known bad file hashes
(installs of those are refused) and newer Java rows; links in it are shown,
never fetched. Its known problems for Diagnose server
(`agent/knownproblems.py`) are a short pattern (200 characters, must
compile, only ever searched with, on text cut to 2000 characters), a plain
sentence and the name of a change the app already has; data can't add a
new kind of change, and each change still checks the server and asks first. Until the real data key exists the built-in key is a test key
whose private half was never kept, so every copy refuses every file and
says "Couldn't check for safety notes".

Nothing downloaded is executed by the agent, with the documented exceptions
under Command injection: the loader installer, Chunker (shipped with the
app, never downloaded), Mojang's Bedrock server program, the owner's own server jar (picked by the owner, never
downloaded), and an update of this app, which runs only after its signature and
checksum check out.

Bedrock add-ons (`.mcpack`, `.mcaddon`, `.mctemplate`) are only unpacked,
never run: each zip entry passes the zip-slip check, links are refused,
sizes are capped, and each pack's `manifest.json` must name a valid UUID
and version before it is written into `behavior_packs/` or `resource_packs/`
(`agent/addons.py`).

The installer downloads one more thing, on the PC with you present: Java
(Eclipse Temurin) from Adoptium's API over HTTPS, checked against the
SHA-256 Adoptium publishes before it is installed (`installer/java_setup.py`).

The desktop app downloads Tailscale's Windows setup in its Tailscale step,
after a click, from `pkgs.tailscale.com/stable/tailscale-setup-*` only.
Tailscale publishes no checksum next to the "latest" file, so it is checked
by its signature instead: Windows must say the signature is valid and the
signer is "Tailscale Inc.", or nothing runs. Then `msiexec` installs it as
administrator (one UAC prompt). The step also runs `tailscale up
--unattended` and `tailscale logout`, fixed argument lists, and only ever
opens a sign-in page on `login.tailscale.com` (`desktop/pc.py`). The agent
moves onto the Tailscale address only once `tailscale status` says it is
connected (`agent/tailscale_watch.py`).

## The Protection card

Each server's Overview has a Protection card. It only reports what it read
from the server's own files (server.properties, the player lists, Paper's
YAML files, the Java options) and never says a server is "safe": a line it
can't check says it doesn't know (the router port until the app opens it
itself). Each one-click fix goes through the same safe change as Game
settings (backup, change, check, one Undo) and needs its own permission,
checked on the server (`agent/protection.py`, `FIXES`). The Log4Shell fix
downloads Mojang's settings file with its SHA-1 and adds the launch flag to
the Java options, where it can be taken off.

Two watchers send alerts (`agent/watchers.py`): ops, whitelist or ban
changes made outside the app (a bracketed console line such as
"[Steve: Made Bob a server operator]", or a file edited while the server was
off), and the chat spam rule (several new names saying the same line turn
the whitelist on with Minecraft's own command). The failed sign-in alert
counts the tries from the same rows the lockout reads.

Help has a read-only fractureiser check (Technical, owner only): it looks for
the known files, startup entries and Run key values and lists what it
checked. It deletes nothing, and finding nothing isn't a clean bill.

## Join protection

Join protection (`agent/joinguard.py`, off by default, Java only) moves the
server to 127.0.0.1 on a private port for that run (`server-ip` in
server.properties and `--port` on the start line, both put back when it
starts without it) and listens on the server's own port in a thread of the
agent. It reads only the handshake, a server list ping or Login Start
(each packet at most 8 KB, all of it within 5 seconds), then copies bytes
both ways without reading them. It never sees passwords or the game.

- Limits per address (IPv6 per /64), automatic blocks (1 hour doubling to
  24 hours) and blocks the owner adds are checked before anything is read.
- A PROXY header is believed only from this PC (127.0.0.1 or ::1, where
  playit's agent connects from). The guard sends one to the server only
  when the type's add-on and its setting are on disk, since a server that
  doesn't expect one turns every player away.
- Without the add-on the server sees every player as 127.0.0.1, so
  `prevent-proxy-connections` is off for that run and `ban-ip` from the
  app's console becomes a guard block (a server ban on 127.0.0.1 would shut
  everyone out). An operator's in-game `/ban-ip` still reaches the server:
  a hand check.
- It fails open: a check that throws lets that player in, a stopped guard
  thread is started again within seconds, and a port it can't take means
  the server starts direct. Each sends a "Join protection stepped aside"
  alert.
- "Start when someone joins" uses the same listener while auto-sleep has
  stopped a server; a login starts it once through the normal start path.

## Web surface

- Bearer tokens, not cookies, so CSRF doesn't apply.
- CORS is off by default: the desktop and phone apps aren't browsers, so
  they don't need it.
- The agent serves no web pages, so its `Content-Security-Policy` is
  `default-src 'none'` (nothing may load or run, not even on the plain page
  at `/`) with `frame-ancestors 'none'`, `form-action 'none'` and
  `base-uri 'none'`; `X-Frame-Options: DENY`, `X-Content-Type-Options:
  nosniff` and a strict referrer policy are set too.
- The WebSocket authenticates in its first message rather than the query
  string, so tokens don't end up in logs. A browser page can't use it
  without a token, and it is read-only: actions go through the
  audited REST API.

## Phone alerts

The phone apps' lock-screen alerts work like this: the agent wakes the
Android app through Firebase and the iPhone app through Apple's push
service (`api.push.apple.com` only, or its sandbox for development builds).
Neither message carries any words, only "New alert" and the alert's
number; the app then reads the alert from the PC over Tailscale with its
own sign-in. The Firebase key and the Apple key (`.p8`, its Key ID and team
ID) are kept only in the data folder's `phone-alerts\` folder, readable by
this account and administrators, put there by
`python -m installer.setup_phone_alerts` and removed with `--remove`. The
Apple sign-in token is signed with that key on the PC and never leaves it
except as Apple's bearer token. An iPhone's push address must be hex, and
an address Apple answers is gone (410) is deleted.

## Auditing

Every privileged action is written to `audit_log` with the time, user,
source address, action, target and result. Routes write their own, more
specific entries; any other signed-in POST, PUT or DELETE gets a plain one
from a middleware in `agent/main.py`, refused ones (403) included, so a
helper's every attempt is recorded under their name. The Security page shows it. Mod
changes also get a record in `mod_history` with checksums and source URLs.

## Transport

- HTTPS is mandatory in practice: the agent refuses to serve plain HTTP on
  any address other than `127.0.0.1`, and exits with an explanation rather
  than starting insecurely.
- The certificate is real: either Let's Encrypt via Tailscale, or a local CA
  you trust once per device. No permanent click-through warnings.
- Startup is gated on TLS validity: a missing, unparseable, expired or
  mismatched certificate stops the agent rather than being worked around.
- HSTS is sent over HTTPS only, never on loopback.
- HTTP, if enabled at all, is a redirect-only listener that refuses anything
  except `GET`/`HEAD` and serves no API or data.
- The desktop and phone apps use `wss://` whenever the agent uses HTTPS,
  and the socket authenticates in its first message.
- Certificate verification is never disabled: the agent's own HTTPS
  self-check loads the CA bundle and verifies.
