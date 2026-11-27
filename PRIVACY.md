# Privacy

Command Block runs on your own PC. It has no accounts, no
analytics and no tracking, and the person who made it receives nothing
from it.

It doesn't send information to other computers unless you, or whoever
installed or runs it, asked it to. Everything it does contact is listed
here.

## What it contacts, and when

| What | When | What is sent |
|---|---|---|
| **GitHub** (`api.github.com`, this repository's releases) | Once a day to see if a new version is out (on by default; `updates.check: false` in `config.yaml` turns it off), when you select **Check now**, and when you install an update | An ordinary web request. Nothing about you or your servers |
| **Mojang / Microsoft**, **Fabric**, **Quilt**, **Forge**, **NeoForge**, **PaperMC**, **Purpur**, **GeyserMC** | When you make a server, change its version or add crossplay | Which version to download |
| **Modrinth** | When you search for, add or update mods, and every 12 hours to see if your mods have updates (on by default; `mods.update_check_hours: 0` in `config.yaml` turns it off) | Your search words, or which mods you have, and the Minecraft version |
| **Hangar** (`hangar.papermc.io`, PaperMC's plugin site) | On Paper and Purpur servers: when you search for, add or update plugins, with the same update check as Modrinth, and once for each plugin jar the app didn't add, to look it up by its checksum | Your search words, which plugins you have (as checksums or project numbers), and the Minecraft version |
| **CurseForge**, through Command Block's relay (a Firebase function, `europe-west1-command-block-d4526.cloudfunctions.net`) | On mod servers: when you search for, add or update mods, read or import a CurseForge modpack, and once for the mod jars the app didn't add, to look them up by fingerprint. Never in the 12-hour update check. The first time, the agent signs in to Firebase anonymously (no name, no email) | Searching CurseForge goes through Command Block's relay, which sees which mods you search for: your search words, which mods and files you ask about (as numbers or fingerprints) and the Minecraft version. The relay keeps nothing; Google's servers see your IP address as for any request. Mod files then come straight from CurseForge's own file servers (`forgecdn.net`) |
| **Spiget** (`api.spiget.org`, a public copy of SpigotMC's plugin list) | At most once a day, when someone opens the Anti-cheat card on a Paper, Purpur or Leaf server | Which paid anti-cheat's price to read (Vulcan's SpigotMC number). Nothing about your server |
| **GitHub** (one add-on's repository) | When you paste a GitHub release link to add a mod or plugin, and with the update check for an add-on added that way | Which repository and release |
| **Adoptium** (Java) | When you ask the setup or the apps to get Java | Which Java version to download |
| **Discord** | Only if you set up Discord alerts | The alert text (for example "Server crashed") to the webhook you entered |
| **Your email server** | Only if you set up email alerts | The alert text to the addresses you entered |
| **Google Firebase Cloud Messaging** (Android) and **Apple Push Notification service** (iPhone) | Only if the owner set up lock-screen alerts for the phone app, and only to phones that turned them on | A wake-up with an alert number and no words in it. The phone then reads the alert from your PC |
| **Your other PCs** (Your PCs) | Only after you join two of your own PCs into one account: when an account changes, and when a PC starts | The owner and moderator accounts (names and password hashes, never passwords), which servers each moderator was given, and the signed-in devices; with the Discord bot, its token (encrypted), its Discord settings, and each PC's server names, Levels XP, linked Minecraft names and wake details. Straight from PC to PC over your own network. No server in between |
| **Tailscale** (`pkgs.tailscale.com`, `api.tailscale.com`) | When you select Install Tailscale in the desktop app, and when you make a share link for a moderator with a Tailscale key you saved | Which Tailscale setup to download, or the key and this PC's Tailscale address, to make the link |
| **mcsrvstat.us** (`api.mcsrvstat.us`) | When you run Diagnose server and the server has an internet address (one you typed in Discord settings, or the one your router gave after "Let friends join from the internet"), and after "Let friends join from the internet" opens the router port, only once you said yes to it | That address and port, so mcsrvstat.us can ping it from the internet |
| **DuckDNS** (`www.duckdns.org`) | Only after you save a DuckDNS name in Server address, then every 30 minutes and when your internet address changes | The name, your DuckDNS token and (from the request itself) your internet address |
| **No-IP** (`dynupdate.no-ip.com`) | Only after you save a No-IP name in Server address, then when your internet address changes (or once a day if your router doesn't say it) | The name, your No-IP username and password, your internet address, and the app's name, version and the project's contact email (No-IP asks every update program for these) |
| **playit.gg** (`api.playit.gg`, `github.com/playit-cloud`) | Only after you select Use playit.gg: the desktop app downloads playit's setup from its GitHub releases, and the agent links playit's program to your playit account | A one-time link code, and the key playit sends back, which goes straight to playit's program on this PC (Command Block doesn't keep it). After that, playit's own program connects to playit, and friends' game traffic goes through playit's servers (playit's own privacy policy applies) |
| **WaterdogPE** (`github.com/WaterdogPE`, our own GitHub releases), for a plain Bedrock server through playit.gg | Only after you select Set up WaterdogPE: the agent downloads WaterdogPE's newest build and the Companion's WaterdogPE plugin, each checked against GitHub's SHA-256 | Nothing is sent by Command Block. WaterdogPE itself talks to playit's signaling service with the tunnel token you pasted (kept only in WaterdogPE's own settings file on this PC). WaterdogPE's anonymous error reports and bStats statistics are turned off |
| **Your home router** (UPnP, on your own network) | When "Let friends join from the internet" is on: when the server starts and stops, and when you turn it on or off | Asks it to send the game ports to this PC and to stop again, and reads the internet address it has. Nothing leaves your home network |
| **GitHub** (HeadlessMC), **Mojang / Microsoft**, the mod loader's site and **Adoptium** | Only when you say yes to Diagnose server's join with all mods, or sign in for it | Downloads of HeadlessMC, the Minecraft game, its mod loader and Java. Signing in for it sends your Microsoft account sign-in to Microsoft, and HeadlessMC keeps it on this PC (in the agent's data folder) until you sign out |

Each of these services has its own privacy policy, and sees your PC's
internet address like any website you visit.

## What stays on your PC

Your worlds, backups, settings, passwords (stored only as salted hashes),
logs and players' names stay on your PC. The agent is reached through
your own Tailscale network; this app never sends it anywhere else.

Players' internet addresses aren't saved, with one exception: on a server
with an IP whitelist add-on, the address it turned away a known player
from is kept encrypted, shown only in part (`203.0.x.x`), and deleted when
the player is taken off the whitelist. With Join protection on, the
addresses it blocks are kept until the block ends or you remove it, and its
log of who joined keeps only part of each address (`203.0.x.x`), for the
last 500 joins. If you pick a Discord channel for a
server's console, its lines are posted there with addresses hidden.

**Get help** saves a file of logs and checks on your PC. It is never sent
by itself: you choose who to give it to. It never contains passwords, keys
or worlds.

## The phone apps

The Android and iPhone apps talk only to your PC, over your Tailscale
network, apart from the mod pictures described below. They have no accounts, no ads, no analytics and no tracking, and
send nothing to the person who made them.

- Your sign-in is kept in the phone's secure storage (the Android keystore
  or the iPhone keychain), behind the phone's own unlock. It is never sent
  anywhere but your PC, and is not included in phone backups.
- The camera is used only to read the pairing code from your PC's screen.
  The picture is read on the phone and never stored or sent.
- **Lock-screen alerts** (if turned on) give Google (Android) or Apple
  (iPhone) a push address for the phone, and they deliver wake-ups from
  your PC to it. A wake-up
  holds only an alert number. Turning the setting off, signing out, or the
  PC signing the phone out removes the address from your PC.
- **Copy details for a problem report** puts versions and recent request
  results on the clipboard, never a password, sign-in code or address. You
  choose where to paste it.
- **Mod pictures**: when you search Modrinth for mods or plugins, the
  results' pictures are loaded by the phone straight from Modrinth
  (`cdn.modrinth.com`) and kept in the phone's cache. Modrinth sees the
  phone's internet address, as for any website. The search itself goes
  through your PC.
- The apps open other websites (Tailscale, GitHub's forms, a mod's
  Modrinth page, this policy) in the phone's browser. Nothing of your
  sign-in goes with them.

## Questions

Open an issue on
[GitHub](https://github.com/peyoteheadlights/command-block/issues),
or see [SECURITY.md](SECURITY.md) to report a security problem privately.
