# Troubleshooting

## The desktop app or phone app cannot connect

| Check | How |
| --- | --- |
| Is the agent running? | `python -m installer.autostart status`, or look for the PowerShell window |
| Right address? | `network.host` in `config.yaml` must match the address the app connects to |
| Firewall? | Windows Firewall may prompt the first time; allow it on private networks |
| Tailscale up on both devices? | `tailscale status` on the PC, and the phone app |

`curl http://127.0.0.1:8765/api/health` on the PC itself separates "agent is
down" from "cannot reach the agent".

## "No password is configured"

You have not run `python -m installer.make_secrets`, or the agent is not
reading your `.env`. The file must be in the project folder (next to
`agent/`), and the agent must be restarted after it is created.

## Forgot the password

On the PC, open PowerShell as administrator and run:

```powershell
& "C:\Program Files\Command Block\mcsc.exe" reset-password
```

or, in a project folder: `python -m installer.reset_password`.

It asks for a new password twice and signs everyone out. This only works on
the PC itself, never over the network.

## Moving to a new PC

1. On the old PC, in the desktop app: gear → **PC settings** → **Move to a new PC** → **Export everything**.
   Tick worlds and backups if you want them. Tick passwords and keys only if
   you want them moved too; they are locked with a passphrase you choose.
2. On the new PC, run the setup file and choose **Import from another PC**
   on its first screen. It asks for the export file, where the servers
   should go, and the passphrase if passwords were included, then makes the
   new PC's certificate and start-with-Windows task.

(In a project folder: `python -m installer.import_from_pc "D:\the-export-file.zip"
--to "C:\Minecraft Servers"` with the app stopped, then `setup.ps1` once more.)

## The server will not start

Run `python -m agent.main --check`. It prints the exact command it will run and
any blocking problems. Common ones:

- **Server jar not found**: `jar` or `directory` is wrong in `config.yaml`
- **Java not found**: set `java` to the full path of `java.exe`
- **FAILED TO BIND TO PORT**: a previous `java.exe` is still running. Check
  Task Manager.
- **EULA**: set `eula=true` in `eula.txt` in the server folder

## It starts then immediately crashes

Open **Crashes** and press **Details** to read the evidence. The most common causes:

| Category | Usual fix |
| --- | --- |
| `OutOfMemoryError` | Raise `-Xmx`, or reduce view distance and heavy mods |
| `JavaVersionIncompatible` | 1.20.5+ needs Java 21; 1.17–1.20.4 needs Java 17 |
| `ModDependencyError` | Install the named dependency at the version shown |
| `MixinError` | A mod does not match this Minecraft version: update or disable it |
| `PortInUse` | A stray `java.exe` is holding 25565 |

Bisect with the mod manager: disable half the mods, start, repeat.

## Auto-restart stopped working

You hit the crash-loop guard: N crashes inside the window disables automatic
restarts on purpose, so a bad mod cannot restart-loop all night. The apps
show a banner with an **Allow restarts** button. Fix the cause first.

## TPS shows "unknown"

Nothing is answering the tick-rate command. Vanilla Fabric has none. Options:

- Minecraft 1.20.3+: set `monitor.tps_command: tick query`
- Install Carpet (`tps`) or spark (`spark tps`) and set it accordingly

The apps report unknown rather than guessing a number.

## Mod buttons are greyed out

The server must be stopped to change mods. Windows locks jars held by a running
JVM, and Fabric reads the folder once at startup.

## A mod will not install

- *"already in the mods folder"*: use Update, or confirm replacement
- *"supports 1.20.1, not 1.21.1"*: no build for your version yet
- *"SHA-512 checksum did not match"*: the download was corrupted and discarded;
  try again
- *"targets forge, not Fabric"*: wrong loader

## I removed the wrong mod

It is in `mod-trash/` in the data folder, and an archived copy is in
`mod-backups/<mod_id>/` (for a second server, under `servers/<id>/`). Nothing was deleted. Use **History** on the
mod, or copy the jar back by hand.

## Restore failed halfway

The restore puts the moved-aside folders back automatically and reports the
error. Your pre-restore safety backup is in the backup list, named `safety-…`.
Nothing is deleted during a restore: the old folders are still on disk as
`world.replaced-<timestamp>`.

## Backups are huge or slow

Set `backups.compression: store` for speed at the cost of size, or trim
`backups.include`: mods and config are small, worlds are not. Retention
settings control how many are kept.

## No Discord or email alerts

Settings → **Send test**. Then check the notification history. Typical causes:
a missing `MCSC_DISCORD_WEBHOOK`, a Gmail account password used instead of an
App Password, the event's checkbox turned off, or throttling because the same
alert fired minutes ago.

## Where the logs are

In the data folder, `C:\ProgramData\Command Block` unless
`paths.data_dir` says otherwise (`python -m agent.datafolder` prints it):

```
logs\agent.log          everything the agent did
logs\agent-errors.log   warnings and errors only
crashes\<timestamp>\    per-crash evidence (servers\<id>\crashes\ for a second server)
<server folder>\logs\latest.log   Minecraft's own log
```
