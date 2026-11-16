# Command Block Companion: threat model

Written for the first release, with the security review of 2026-11-16.
STRIDE style: who could attack, what they could try, what stops it, and
which test shows it. The byte layouts are in [PROTOCOL.md](PROTOCOL.md).

## The one honest limit

The check runs inside the player's own game, on a computer the player
controls. A modified game can hash the real modpack files and report them
while it loads something else. No check inside the player's game can stop
that. So the Companion proves "this player has the right modpack" for
honest players. It is never called protection or anti-cheat. The real
gates stay the whitelist, Join protection and a server-side anti-cheat
(Grim, suggested in the app). Mods that add blocks, items or entities
enforce themselves: NeoForge and Fabric API refuse a player whose
registries don't match the server's.

## What we protect

1. **The player's PC:** nothing the server sends may run, write outside
   the game's `mods/` and the Companion's own folder, or remove the
   player's own mods.
2. **The player's privacy:** the server learns only yes/no for the files
   it names, the modpack version and "has other mods: yes/no".
3. **The owner's server, often a weak mini PC:** joins must not let anyone
   make it hash files or hold memory without limit.
4. **The owner's choice:** "Required to join" must fail closed: no answer
   means not in.

## Who might attack

- **A lying player**, with a modified game or their own client.
- **A malicious server** the player joins, pushing a bad modpack or
  probing the player's mods.
- **A man in the middle** between the player and the server, or between
  the player and the download link.
- **A flood**: many joins from many addresses.
- **A bad file**: a broken or hand-edited settings or players file.

## Threats and what stops them

| # | STRIDE | Threat | What stops it | Shown by |
|---|---|---|---|---|
| 1 | Spoofing | Replaying an old good answer | A fresh 128-bit nonce per challenge, sent back and compared in constant time | `CheckerTest.anOldAnswerIsUselessForANewChallenge` |
| 2 | Spoofing | A game that only knows the modpack's hash list | Random spot checks: the hash of nonce + a random byte range of a random file, fresh each time | `CheckerTest.aGameThatOnlyKnowsTheHashesFails`, `aFakedSpotProofFails` |
| 3 | Spoofing | Swapping mods after joining | Rechecks at random times while playing (required modpacks) | `CompanionServer.tick` |
| 4 | Tampering | Oversized or malformed packets | Strict parsers: every read checks length, counts are capped before lists are made, left-over bytes refused; payload codecs read at most `MAX_BYTES` | `ChallengeTest`, `AnswerTest`, `OfferTest` (including random-bytes fuzz loops), `FrameTest` |
| 5 | Tampering | A bad settings file | Exact keys only, every value range-checked, `on_server` kept inside the server folder and never a link; a bad file turns the check off with a log line | `SettingsTest` |
| 6 | Tampering | A malicious modpack writing outside `mods/` | Every file name goes through `Swap.inside`: one plain `.jar` name straight inside the folder, no `..`, no hidden names | `UpdateTest.pathsThatLeadOutOfTheModsFolderAreIgnored`, `aPlanWithABadNameChangesNothing` |
| 7 | Tampering | A malicious modpack pulling files from anywhere | Only Modrinth's and CurseForge's own download servers (https, default port), checked by SHA-512; own jars only when their SHA-256 is on the server's list from the check, unticked, with "Only add this if you trust the owner" | `UpdateTest.onlyModrinthAndCurseForgeDownloadServersCount`, `anOwnJarNotOnTheServersListIsNeverOffered`, `aFileThatDoesntMatchItsHashIsThrownAway` |
| 8 | Tampering | A malicious modpack replacing or later removing the player's own mods | Only the modpack in place says which files are its own; the new players file lists only files the update really brought | `UpdateTest.aNewModpackCantClaimAPlayersOwnFile`, `theNewPlayersFileListsOnlyFilesTheUpdateBrings`, `aLaterUpdateNeverRemovesAFileTheOldModpackOnlyClaimed` (fixed in this review) |
| 9 | Tampering | Half an update going in | Downloads land in a waiting folder; one bad file throws it all away; the swap undoes every move if one fails; the old set is kept for "Go back" | `UpdateTest.aSwapThatCantFinishUndoesEverything`, `installWaitsThenSwapPutsItInAndGoBackUndoesIt` |
| 10 | Elevation | Anything downloaded running | Nothing is run. The only program started is `Swap`, from the Companion's own jar with the game's own Java, after the game closes | `Swap.startAfterExit` |
| 11 | Elevation | A picture that crashes or eats the game's memory | PNG signature, file size and pixel size checked before Minecraft decodes it | `PicturesTest` |
| 12 | Info disclosure | The player's mod list leaking | The answer is yes/no per file the server named plus one "other mods" flag; never names, paths or other hashes | `CheckerTest.theAnswerNeverCarriesThePlayersOwnModHashes` |
| 13 | Denial of service | Making a weak server hash all day | At most `handshakes_per_ip_per_minute` checks a minute per address, IPv6 per /64; at most 10 000 addresses remembered; old ones swept at most once a second | `GateTest` (the /64, memory and speed tests were added in this review) |
| 14 | Denial of service | A player who never answers | `timeout_seconds` (1 to 60), then judged as no answer | `JoinCheck.tick` |
| 15 | Repudiation | "I wasn't refused" / unseen attacks | One log line per check, read into Recent activity; 3 failures from one player in a day alert the owner | `agent/minecraft/console.py`, `tests/test_companion_checks.py` |

## Known risks we accept, or fix later

| # | Risk | Why it's accepted for now | Later |
|---|---|---|---|
| A | A faked game passes (the honest limit) | Nothing inside the player's game can stop it | Server-side anti-cheat |
| B | **A different server's update repoints the join button and server list.** A server the player joins can offer "Update to" its own modpack in the same game folder. If the player presses Update and Download, the new players file's title address replaces the old one, and the title screen moves server list entries with the old address to the new one (meant for an owner changing address) | The player must press two buttons, the list shows the modpack's name and every change, and "Go back" restores the files | **Recommended next:** only offer an update whose modpack name matches the one in place, and only move server list entries when the update was offered by the server at the old address |
| C | **The `.mrpack` itself isn't pinned by a hash.** The download link is plain http. Today it listens only on the owner's Tailscale network (WireGuard-encrypted), so only someone in that network could change it on the way. Mod files from Modrinth and CurseForge are still checked by SHA-512 and own jars by the server's list, but a changed modpack could swap which Modrinth files it names or change the title address | Not reachable from the internet yet | **Before the download link opens to the internet:** send the `.mrpack`'s SHA-256 in the Offer (over the game connection) and check it after the download |
| D | A server can test whether a player loaded a jar it names by hash (up to 4000 a check), for example a known cheat client build | That is what the check is; it can't list the player's mods. Said in `PRIVACY.md` | None planned |
| E | Offline-mode servers: no encryption and no verified names, so a man in the middle can change challenges and answers, and two joins with one name can make one of them time out (the Paper plugin keeps joining checks by player id) | Offline mode already lets anyone use any name; the whitelist and Join protection are the gates | Key Paper's joining checks by connection |
| F | Paper waits on its own joining thread for each answer, up to `timeout_seconds`. Many joins that never answer could hold those threads | Bounded by the per-address limit and Join protection | Measure on the mini PC |
| G | Not required: a player refused the check but never reaching the world leaves a small chat-line entry in memory | Bounded by the per-address limit (6 a minute by default) | None planned |
| H | The title button pings the address the modpack names, which could be a LAN address | It sends only Minecraft's own status handshake | None planned |
| I | The server hashes its own copies of the modpack's files once at start, on the server thread | Not a security problem; a slower start with a big modpack | Measure on the mini PC |

## Supply chain

- Open source in this repository, built only in CI
  (`.github/workflows/companion.yml`), never on a PC.
- Gradle dependency verification (`companion/gradle/verification-metadata.xml`):
  every library and plugin jar is checked by SHA-256 before the build
  uses it. The Gradle wrapper jar is checked against Gradle's published
  checksums.
- CodeQL for Java and an OSV dependency scan on every pull request.
- Releases (`.github/workflows/companion-release.yml`): each jar comes with
  a `.sha256` file and a signed SLSA build provenance (GitHub artifact
  attestation), and is published on GitHub and Modrinth from the same
  build. How to check one: [SECURITY.md](../SECURITY.md).
- No obfuscation: the strength comes from fresh challenges, not hidden code.

## Standards measured against

- **OWASP ASVS** (the parts that apply): input validation (V5: strict
  parsers, allow-lists, size limits), communication (V9: https for mod
  downloads; see risk C), configuration (V14: pinned dependencies,
  verified build).
- **OpenSSF Scorecard**: pinned actions by commit, least-privilege
  workflow tokens, CodeQL, dependency scanning, signed releases.
- **SLSA build level 2**: hosted build, signed provenance.
- **EU Cyber Resilience Act**: mostly exempt as free non-commercial open
  source; its basics are covered by `SECURITY.md` (contact and supported
  versions), security fixes in new releases, and GitHub's dependency
  graph (Insights, Dependency graph, Export SBOM) as the list of
  dependencies.

## Review log

- **2026-11-16, first security review** (before the first release).
  Read: every file in `common/`, `fabric/`, `neoforge/`, `paper/` and
  `client-screens/`, and the app side (`agent/companion.py`,
  `agent/downloadlink.py`, the log parser).
  Fixed: threat 8 (a modpack could claim the player's own mods) and
  threat 13 (IPv6 counted per address, memory without a cap, a sweep on
  every call). Open: risks B and C above, both recommended as the next
  Companion work.
