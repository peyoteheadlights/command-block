# Command Block Companion: protocol

How the Companion's two halves talk, and the two files they read. The
code that reads and writes each part is named next to it; the tests in
`common/src/test/` check the byte layouts below. Anything not described
here is refused.

Numbers are big-endian (like Java's `DataOutputStream`). `u8`, `u16`,
`i32`, `i64` are 1, 2, 4 and 8 bytes. `text(n)` is a `u16` byte length
then that many UTF-8 bytes, at most `n` bytes. Every packet starts with a
`u8` version (1 today) and must end exactly where its layout ends:
left-over bytes, a wrong version or a count over its limit refuse the
whole packet (`BadInput`), never part of it.

## Channels

| Channel | Direction | Carries |
|---|---|---|
| `commandblock:check` | server to player, player to server | Challenge, Answer |
| `commandblock:offer` | server to player | Offer |

On Fabric and NeoForge each packet is one Minecraft byte array: a VarInt
length, then the bytes, at most `CheckPayload.MAX_BYTES` (128 132 bytes,
a challenge with 4000 hashes). Paper hands plugins the raw packet, so the
plugin adds and strips that VarInt length itself (`CompanionPlugin.frame`
and `unframe`, at most 3 length bytes).

## The join check, step by step

1. A player starts joining. While Minecraft is still "configuring" them
   (before they are in the world), the server asks the per-address limit
   (`Gate`: `handshakes_per_ip_per_minute` checks a minute from one
   address; IPv6 counts per /64). Joins from the server's own PC
   (127.0.0.1, ::1) are never counted: behind Join protection everyone
   arrives from there, and the join guard limits each real address
   itself. Over the limit: refused when the modpack is required,
   otherwise not checked.
2. A player whose game doesn't listen on `commandblock:check` has no
   Companion: judged as "no answer".
3. Otherwise the server sends the Offer (only when the download link is
   on), then a fresh Challenge, and waits `timeout_seconds` from that
   moment (the join steps before it, like a resource pack prompt, don't
   count).
4. The first Answer counts; later ones are ignored. No answer in time, or
   an answer that doesn't parse: "no answer".
5. `Checker.judge` passes the player when every modpack file was loaded
   and every spot proof matches. Otherwise the player is refused with
   what to get (required), or let in and shown the chat line (not
   required).
6. Required modpacks only: a new challenge at a random time between
   `recheck_minutes[0]` and `[1]` while they play, with the same timeout.

Each check writes one line to the server log, which the app reads for
the Players page (`agent/minecraft/console.py`, `COMPANION_RE`):

```
Companion check: <player> passed|failed, modpack <version or ->
```

Paper puts `[CommandBlockCompanion] ` in front.

## Challenge (server to player)

`Challenge.encode` / `decode`.

| Field | Type | Limit |
|---|---|---|
| version | `u8` | 1 |
| nonce | 16 bytes | fresh from `SecureRandom` for every challenge |
| file count | `u16` | at most 4000 |
| hashes | count x 32 bytes | SHA-256 of each modpack jar, in the settings file's order |
| spot count | `u8` | at most 8 |
| spots | count x (`u16` file index, `i64` offset, `i32` length) | index below the file count, offset 0 or more, length 1 to 65 536 |

A spot asks for the proof of one byte range of one modpack file. The
server only picks files whose own copy still has the modpack's hash, and
ranges inside the file.

## Answer (player to server)

`Answer.encode` / `decode`, made by `Prover.answer`.

| Field | Type | Limit |
|---|---|---|
| version | `u8` | 1 |
| nonce | 16 bytes | the challenge's, sent back |
| modpack version | `text(32)` | from the players file, `""` when unknown |
| file count | `u16` | at most 4000, must equal the challenge's |
| loaded | count x `u8` | 1 if the game loaded a jar with that exact hash, else 0 |
| extra mods | `u8` | 1 if the game loaded any jar not in the challenge's list |
| proof count | `u8` | at most 8, must equal the challenge's spot count |
| proofs | count x 32 bytes | SHA-256 of nonce + the file's bytes in that spot's range |

Flags other than 0 and 1 are refused. "Loaded" means the loader's own
list of loaded jars inside the game's `mods` folder (`LoadedJars`), not
what is on disk. The answer never carries the names or hashes of the
player's own mods: only yes/no per file the server named, and one yes/no
for "anything else".

## Offer (server to player)

`Offer.encode` / `decode`.

| Field | Type | Limit |
|---|---|---|
| version | `u8` | 1 |
| name | `text(256)` | not blank, no control characters (the app allows 64 characters, up to 4 bytes each) |
| version | `text(32)` | `[A-Za-z0-9_.+-]{1,32}` |
| link | `text(512)` | `http` or `https`, printable ASCII, a host, no user name or password |

The offer never makes the Companion do anything by itself. It only adds
an "Update to ..." button to the disconnect and pause screens when the
check found the player behind. See "Updates" below.

## The server's settings file

Written by the app each time the owner makes a modpack
(`agent/companion.py`, `write`), read by `Settings.parse`. Where:
`config/commandblock-companion.json` on Fabric and NeoForge servers,
`plugins/CommandBlockCompanion/config.json` on Paper. At most 2 MiB.
Every object must have exactly these keys, no more and no fewer.

```json
{
 "format": 1,
 "required": true,
 "modpack": {"name": "Friends", "version": "3"},
 "files": [
  {"path": "mods/sodium.jar", "name": "Sodium", "sha256": "<64 hex>", "size": 1234567,
   "on_server": "player-only/sodium.jar"}
 ],
 "download_link": "http://100.64.0.1:25590/<token>/<file>.mrpack",
 "chat_line": {"on": true, "text": "This server uses the modpack Friends 3. ..."},
 "check": {"timeout_seconds": 10, "recheck_minutes": [10, 30], "spot_checks": 3,
           "handshakes_per_ip_per_minute": 6}
}
```

| Field | Rule |
|---|---|
| `format` | 1. A newer format is refused (the check then stays off) |
| `modpack.name` | text up to 128, not blank |
| `modpack.version` | `[A-Za-z0-9_.+-]{1,32}`: English letters and digits only, the same rule everywhere (`Settings.versionOk`, and the app's `VERSION_RE`) |
| `files` | at most 4000 |
| `files[].path` | `mods/<one file name>.jar`, no folders, no characters Windows refuses |
| `files[].sha256` | 64 lowercase hex |
| `files[].size` | 0 to 1 GiB |
| `files[].on_server` | the server's own copy, from the server folder: up to 4 folders deep, never `..`, or null. Read only when it stays inside the server folder and isn't a link |
| `download_link` | null, or `http(s)://` and printable ASCII up to 500 |
| `chat_line.text` | up to 1024 |
| `check.timeout_seconds` | 1 to 60 |
| `check.recheck_minutes` | two numbers 1 to 1440, the first not above the second |
| `check.spot_checks` | 0 to 8 |
| `check.handshakes_per_ip_per_minute` | 1 to 60 |

A missing or refused file turns the check off and says why in the log.

## The players file

Put in the modpack by the app (`agent/companion.py`, `players_file`) as
`client-overrides/config/commandblock-companion-client.json`, so it ends
up in the player's `config/`. Read leniently: anything odd in one part
turns only that part off, never crashes the game.

```json
{
 "format": 1,
 "modpack": {"name": "Friends", "version": "3"},
 "files": ["mods/sodium.jar", "mods/own-tweaks.jar"],
 "title_screen": {"name": "Survival", "address": "play.example.net:25570", "flavor_text": "Dragons!"}
}
```

- `modpack.version` is what the Answer says (`Prover.packVersion`).
- `files` are the modpack's own jars: the only files an update may ever
  replace or remove. When an update goes in, this list is rewritten to
  the modpack's files already in place (only those the old list named)
  plus the ones the player ticked and the update added
  (`Plan.playersFile`), so a modpack can never claim a player's own mod,
  not even an identical copy, nor a file the player chose not to add.
- `title_screen` (`TitleSettings.parse`): the join button's address (a
  host name or IPv4 address, maybe `:port`) and the flavor text (up to
  60 characters, no control characters, no `§` colour codes). Each may
  be null. The icon and background are PNG files next to it, checked
  before Minecraft decodes them (`Pictures`: at most 256 KiB / 4 MiB and
  4096 pixels a side).

## Updates

The "Update to ..." button only shows when the offer's modpack name is
the one in place (or there is none yet), and a new join forgets the last
server's offer (`Updates.joining`), so another server can't offer to swap
a friend's modpack for its own. When the update is got ready, the
server's address is written to `.commandblock-companion/offered-by.txt`;
the title screen moves server list entries to a new address only when
that address is the old one (`TitleExtras.followAddress`).

Only after the player presses "Update to ..." and then "Download" on the
list (`UpdateScreen`):

1. The `.mrpack` is downloaded from the offer's link, at most 1 GiB, into
   `.commandblock-companion/` in the game folder (`Install.fetchPack`).
2. `Plan.read` works out the list: mods from Modrinth's or CurseForge's
   own download servers (`cdn.modrinth.com`, `edge.forgecdn.net`,
   `mediafilez.forgecdn.net`, https, no other port) checked by the
   modpack's SHA-512; own jars inside the modpack only when their
   SHA-256 is on the server's list from the last check, and unticked;
   removals only of files the modpack in place lists as its own. Each
   file name once: `client-overrides/` wins over `overrides/`, and both
   over the download list, like a launcher. Files go only straight into
   `mods/` (`Swap.inside`).
3. `Install.prepare` downloads the ticked files into a waiting folder,
   every one checked: downloads by SHA-512 and then their SHA-256 must
   be on the server's list from the last check (the `.mrpack` comes over
   plain http, so a changed one can't add any other Modrinth or
   CurseForge file); own jars by SHA-256 again. One bad file throws the
   whole update away. Redirects are followed only within the same site.
4. When the game closes, `Swap` (a small Java program started from the
   Companion's own jar with the game's own Java) moves the old files to
   `previous/` and the new ones in, or undoes every move if one fails.
   "Go back" swaps the previous set back the same way. If the swap
   changes nothing, the next update screen says why (`Swap.lastFailure`).

Nothing downloaded is ever run.
