# Security

## Reporting a problem

Please report security problems **privately**, not in a public issue:
use GitHub's private vulnerability reporting on this repository
(**Security** tab, then **Report a vulnerability**).

Say what you found, how to reproduce it, and which version you used (shown
in the desktop app under ? > About, or at `/api/version`). You'll get an answer in the same
place. Please give a reasonable time to fix it before telling anyone else.

## Which versions get fixes

Only the newest release. The app tells you when a new one is out and can
install it for you, so updating is the fix.

| Version | Gets security fixes |
|---|---|
| Newest release | Yes |
| Anything older | No, update first |

## What is in scope

The agent, the desktop app, the phone apps, the installer and the in-app updater. How the
app is designed to keep your PC safe is in [docs/security.md](docs/security.md).

Also the Command Block Companion mod and plugin (`companion/`). What it
protects against, and what it can't, is in
[companion/THREAT-MODEL.md](companion/THREAT-MODEL.md); it checks that a
player has the right modpack and is not an anti-cheat. Only its newest
release gets fixes.

## Checking a download

Every setup file and Companion jar on the releases page has a `.sha256`
file, and GitHub keeps a signed record of where it was built:

```
gh attestation verify <file> --repo peyoteheadlights/command-block
```
