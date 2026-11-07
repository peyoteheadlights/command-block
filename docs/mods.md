# The mod manager

This is the part that can break a server badly, so it is the part with the most
safety rails.

## Mods or plugins, depending on the server

What a server takes comes from its type (`agent/servertypes/`), not from an
assumption that every server is Fabric:

| Type | Takes | Folder | What a file has to carry |
| --- | --- | --- | --- |
| Vanilla | nothing | none | the page isn't offered at all |
| Fabric | mods | `mods/` | `fabric.mod.json` |
| Quilt | mods | `mods/` | `quilt.mod.json`, or `fabric.mod.json`: Quilt loads most Fabric mods |
| Forge | mods | `mods/` | `META-INF/mods.toml` |
| NeoForge | mods | `mods/` | `META-INF/neoforge.mods.toml` |
| Paper, Purpur | plugins | `plugins/` | `plugin.yml` or `paper-plugin.yml` |
| Bedrock | packs (an **Add-ons** page of its own) | `behavior_packs/`, `resource_packs/` | `manifest.json` with a valid UUID and version |

A Bedrock server's Add-ons page takes `.mcpack`, `.mcaddon` and `.mctemplate`
files (Bedrock add-ons aren't on Modrinth, so there is no search). Each
pack's `manifest.json` is checked, every path in the file is checked to
stay inside the pack's folder, and the pack is turned on in the server's
world (`world_behavior_packs.json` / `world_resource_packs.json`). Mojang's
own packs aren't listed. Removing a pack turns it off and moves it into the
data folder's `removed-addons/`. See `agent/addons.py`.

The page names itself after the server it is showing ("Mods" or "Plugins")
and a file that belongs to a different loader is flagged by what its own
metadata says, with the file it was read from named.

## The installed mods table

| Column | Meaning |
| --- | --- |
| Mod | Display name from `fabric.mod.json`, with the mod id underneath |
| Version | The version the mod declares |
| Minecraft | The Minecraft range the mod says it supports |
| Status | `ok`, `warn`, `error`, or `disabled`: errors are explained above the table |
| Update | The newer Modrinth version, if one exists for your Minecraft version |

All of it is read straight from the jar: the agent opens it as a zip file and
parses the metadata member that type uses (JSON for Fabric and Quilt, TOML for
Forge and NeoForge, YAML for Paper plugins). It never loads or runs mod code.

Forge and NeoForge declare their Minecraft support as a Maven range
(`[1.20.1,1.21)`), Fabric as its own (`>=1.21 <1.22`). Both are read in their
own syntax; a file that declares nothing is **Unknown**, never assumed to
fit.

## Installing from Modrinth

Search, pick a mod, and you get a confirmation panel before anything is
written. It shows the file name and size, the Minecraft versions, whether the
mod works server-side, the licence, and how many required dependencies it
declares.

What happens when you confirm:

1. Compatibility is checked against the Minecraft version the server reported
2. Required dependencies are listed, never installed behind your back
3. Conflicts with what you have are checked
4. The current mods folder state is archived
5. The file is downloaded to a temporary `.part` file
6. The **SHA-512 that Modrinth published is verified** against the bytes received
7. The file is confirmed to be a real zip carrying the metadata this server's
   type uses
8. Only then is it moved into the mods folder
9. The source URL and a SHA-256 are recorded in the audit trail

If any step fails the temporary file is deleted and your mods folder is
untouched.

### The rules the downloader enforces

Every download, here and elsewhere, goes through `agent/downloads.py`, and the
rules are in `docs/security.md`. On top of them, for mods:

- HTTPS only, and only from `cdn.modrinth.com` / `cdn-raw.modrinth.com`
- `.jar` files only: `.exe`, `.bat`, `.ps1`, `.cmd`, `.dll` and friends are
  refused by name before anything is fetched
- No path separators in file names, so nothing can escape the mods folder
- A file with no published checksum is refused rather than trusted
- 300 MB ceiling per file

## Hangar, files and links

On Paper and Purpur servers the search also asks **Hangar**, PaperMC's own
plugin site, and each result says which site it's from. Hangar plugins
install, update and join Update all like Modrinth ones, with these rules
(`agent/mods/hangar.py`):

- only public projects, for the PAPER platform and this Minecraft version
- the SHA-256 Hangar published is checked, and files come only from
  `hangarcdn.papermc.io`
- required plugins on Hangar are added with a version that fits; one Hangar
  only links to on another site is shown as a link; optional ones are listed
- only the Release channel is installed by default and offered as an update
- at most 20 requests every 5 seconds, as Hangar asks
- Modrinth publishes SHA-512 and SHA-1, Hangar SHA-256, so a plugin on both
  can show twice in a search. Installing the same file twice is refused by its
  hash.

**Add a file** (or drop one on the window) takes a mod or plugin `.jar` or a
datapack `.zip`. The file is read first: a mod for another loader, a resource
pack or a zip with its `pack.mcmeta` in a folder is refused with the reason.
Datapacks go into the world's `datapacks` folder. A jar Modrinth or Hangar
lists (looked up by its hash) is treated as theirs; anything else shows
"Not checked by Modrinth" and is never updated by Update all.

**Add from a link** reads GitHub release links only (`agent/mods/github.py`):
you pick one of the release's `.jar` or `.zip` files, and updates come from
the repo's newest full release when it has one file named like yours. Any
other link (Planet Minecraft, SpigotMC, a creator's site) says "Download it
in your browser and drop the file here".

Each add is one change with a backup of the folder it writes to and one
Undo, except into an empty folder, which has nothing to put back (Remove
takes it out).

## CurseForge

On Fabric, Quilt, Forge and NeoForge servers the search also asks
**CurseForge** (`agent/mods/curseforge.py`). CurseForge's API needs a key;
the app never holds one and never asks you for one. It goes through our own
relay instead, a small Firebase function that holds the key and allows only
five read-only calls (search, a mod, a mod's files, a file's download link,
fingerprints). The agent signs in to it anonymously once; the refresh token
is kept in `.env` with the other secrets.

- results are sorted by popularity, then the name you typed is put first
  ("lithium" finds Lithium, not a look-alike), and only mods with a file for
  this server's loader and Minecraft version are shown
- only files CurseForge marks approved or released are offered
- files come only from `edge.forgecdn.net` / `mediafilez.forgecdn.net` and
  are checked against the **sha1** CurseForge publishes (it publishes no
  SHA-512). That is a weaker check than Modrinth's, so the apps say
  "Checked (sha1)"
- required mods are added too
- an author can keep other apps from downloading a mod. Such a mod gets
  **Open on CurseForge** and is listed under **Needs you** on the Mods page;
  the rest of an install goes ahead. Drop the file you downloaded on the
  window: it's matched by its CurseForge fingerprint (MurmurHash2 of the
  file without whitespace bytes), never by its name
- jars already in the folder that the app didn't add are looked up by
  fingerprint once, all in one call, when you press Check or open Update
  all; a match counts only when its sha1 matches too
- the 12-hour update check leaves CurseForge out; it's asked only when you
  search, add, press Check or open Update all
- nothing CurseForge answers is saved. An installed mod's record keeps only
  its project and file id
- when the relay is down, over its limit or turned off, the page says
  "CurseForge isn't available right now" and Modrinth keeps working

## Why the server must be stopped

Installing, removing, enabling, disabling and updating all require the server
to be OFFLINE or CRASHED. Windows locks jars that a running JVM has open, and
Fabric reads the mods folder exactly once at startup: changing it underneath a
running server produces confusing half-states. The apps disable those
buttons and says why. Paper and Purpur read `plugins/` the same way.

## Changing the kind of server

Changing a server's type (Server settings → Kind and version) checks every
installed add-on against the type you are moving to and lists the ones that
don't fit **before** anything happens. Those files are moved to the mod trash,
where the usual restore puts them back. Nothing is deleted.

## Removing a mod

Before it does anything, the apps show which installed mods declare this
one as a required dependency. Removing Fabric API with twelve mods depending on
it is a decision, not an accident.

The jar is then **copied to `mod-backups/` and moved to `mod-trash/`**. It is
never deleted outright, so a mistake costs you one click to undo.

## Enable and disable

Disabling renames `sodium.jar` to `sodium.jar.disabled`. Fabric only loads
files ending in exactly `.jar`, so the mod stops loading but the file stays put.
This is the fastest way to bisect a crash: disable half your mods, start, repeat.

## Updates and rollback

Checking for updates asks Modrinth for the newest build matching your Minecraft
version. Updating archives the current jar first, then swaps it. If the download
or the swap fails, the old jar is put straight back.

Every version the manager has ever seen is kept in `mod-backups/<mod_id>/` with
its SHA-256 recorded. **History** on any mod lists them, and **Roll back**
restores one, after re-verifying the archived file's SHA-256, so a corrupted
archive is refused rather than installed.

## Dependency and conflict checks

Run on every page load, and reported at the top of the mods page:

- a required dependency that is missing or disabled
- a dependency present but at a version outside the required range
- two enabled jars providing the same mod id
- a mod that declares it `breaks` another mod you have installed
- a Forge mod sitting in a Fabric server's mods folder

### What it cannot see

The page says this too, because it matters:

- **Only declared metadata is checked.** Two mods can corrupt each other's
  world data with nothing in either `fabric.mod.json` to suggest it.
- **Minecraft and Fabric versions are learned from the console**, so before the
  first successful start those checks are skipped rather than guessed.
- Maven-style ranges like `[1.0,2.0)` are not modelled; those are reported as
  "could not be checked" instead of being silently treated as passing.

## Modpacks

A Modrinth modpack (`.mrpack`) or a CurseForge modpack (its `.zip`, or one
found with **Find on CurseForge**) can become a new server (the **+** tab,
**From a modpack**) or be imported into the open server (Mods page,
**Import a modpack**).

A CurseForge pack lists only each mod's project and file in its
`manifest.json`. When it's read, each one is asked of CurseForge through the
relay (one call or a few per mod, at most 60 a minute, so a big pack takes a
few minutes) and kept in memory until it's imported. Mods CurseForge tags
for the game only are left out and listed for your players; mods their
authors keep from other apps go under **Needs you** after the import; a
loader this app can't set up is refused before anything is asked or changed.

Choosing the file reads it and changes nothing. The panel shows the Minecraft
version, the loader and its version, and every file, marked as downloaded,
skipped or refused:

- **Skipped**: files the pack marks as not for servers (`env.server` is
  "unsupported"), and folders only the game uses (resource packs, shader
  packs, `options.txt`) in its extra files. Files with program extensions
  (`.exe`, `.bat`, …) in the extra files are skipped too. Optional server
  files are included.
- **Refused**: a mod with no SHA-512 in the pack, one that isn't on
  Modrinth's own download site (`cdn.modrinth.com`), or one whose file name
  or folder isn't safe to write. Any refused file blocks the import, and so
  does an extra file whose path would land outside the server folder or is
  a link.

Importing downloads every mod through the safe downloader and checks it
against the pack's SHA-512, then copies the pack's extra files (`overrides/`,
then `server-overrides/`). The server type and version are set up with the
same installer as **New server** and **Change the kind or version**.

Into an existing server, the import stops the server and takes a verified
backup first. If the server isn't already on the pack's type and versions
(as its console last reported them), the version is changed first. The mods
it had are moved to the mod trash in a `<date>-before-modpack` folder, never
deleted. If anything fails, the server is put back from the backup.

## The audit trail

Every mod action is recorded with the time, who did it, the action, the mod,
the old and new versions, the source URL, the SHA-256 and the result, including
failed attempts. It is at the bottom of the Mods page and in the database table
`mod_history`.
