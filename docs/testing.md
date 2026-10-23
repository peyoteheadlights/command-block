# Testing

```powershell
pip install -r requirements-dev.lock
python -m pytest -q
```

About 380 tests, a little over a minute.

## Lint, format and type check

Settings live in `pyproject.toml`.

```powershell
python -m ruff check .            # add --fix for the safe fixes
python -m ruff format .
python -m mypy
```

To run these before every commit: `pip install pre-commit`, then
`pre-commit install`.

## Pinned versions

`pyproject.toml` lists what the project needs: `[project.dependencies]` for
the app, and the `dev` and `build` extras for the tests and the installer
build. `requirements.lock`, `requirements-dev.lock` and
`requirements-build.lock` pin the exact versions (with hashes) that CI tests,
for every platform and Python 3.11+. Everything (setup.ps1, CI, the
installer) installs from a lock with `--require-hashes`, so a PC runs what CI
tested. After changing a dependency in `pyproject.toml`, regenerate all
three:

```powershell
uv pip compile pyproject.toml --universal --python-version 3.11 --generate-hashes -o requirements.lock
uv pip compile pyproject.toml --extra dev --universal --python-version 3.11 --generate-hashes -o requirements-dev.lock
uv pip compile pyproject.toml --extra build --universal --python-version 3.11 --generate-hashes -o requirements-build.lock
```

`tests/test_dependencies.py` fails if a lock is missing a package
`pyproject.toml` asks for.

## Security scans and coverage

CI's "Known security problems in dependencies" job runs `pip-audit` on the
lock files, and the CodeQL workflow scans the Python and JavaScript (results
under the repository's Security tab). Keep GitHub's Dependabot **alerts** on
and Dependabot **pull requests** off; update the locks as in "Pinned
versions" above.

One of the test runs prints the coverage per file
(`python -m pytest --cov=agent --cov=installer --cov-report=term:skip-covered`).
There is no minimum; it shows which code no test reaches.

## What is safe about the tests

They never touch a real Minecraft server. Each test builds a throwaway server
folder in a temporary directory, and `server.raw_command` points at
`tests/fixtures/fake_server.py`: a small Python program that imitates Fabric's
console output, responds to `stop`, and can be told to crash, hang or fail at
startup with environment variables.

That means the crash path, the force-kill path and the auto-restart path are
all exercised for real, with real processes, without a JVM and without risking
a world.

## Coverage by file

| File | What it proves |
| --- | --- |
| `test_process.py` | Start reaches ONLINE and parses versions; a graceful stop is **never** reported as a crash; a crash is detected with its exit code; a startup failure is distinguished from a crash; restart works; force-kill after a stop timeout; auto-restart with recovery; crash-loop protection engages; commands refused when offline; newline injection refused; preflight catches a missing jar; the console buffer stays bounded |
| `test_validation.py` | Command validation accepts real commands and rejects newline injection, shell separators, backticks and `$( )`; dangerous commands require confirmation; the crash analyzer identifies OOM, dependency errors, mixin failures, Java mismatches, port conflicts and EULA, and **admits when it does not know**; path safety rejects traversal, executables, reserved names and symlinks; schedule expressions parse and bad ones are refused |
| `test_mods.py` | Metadata is read from real generated jars; Forge and corrupt jars are flagged; missing dependencies, version mismatches, duplicate mod ids and declared incompatibilities are detected; enable/disable renames correctly; removal archives and trashes instead of deleting; dependents are warned about; mod writes are refused while the server runs; uploads reject non-jars, executables and traversal; rollback restores and refuses a tampered archive; downloads verify SHA-512 and reject wrong hosts, plain HTTP, missing checksums and non-archives |
| `test_backups.py` | Archives are created and verify; damage and deletion are detected; `session.lock` is skipped; restore makes a safety backup and puts files back; unverifiable backups are refused; zip-slip is blocked; manual backups survive retention |
| `test_api.py` | Every data and action route rejects unauthenticated calls; login, rotation, logout and revocation behave; five failures cause a lockout with `Retry-After`; API tokens work and near-miss tokens do not; dangerous commands need confirmation through the API; shell injection through the API is refused; settings updates are limited to an allow-list; restore returns a plan before acting; executable uploads are refused; security headers are present; the WebSocket demands auth in its first message |
| `test_notifications.py` | A failing webhook never raises and is recorded; failures publish an event; missing config is "skipped" not "failed"; disabled events are not sent; repeat alerts are throttled; crash notifications carry the analysis; health reports unknown TPS instead of guessing; thresholds publish warnings; players accumulate playtime and UUIDs |

| `test_https.py` | Certificates are generated with the right SANs and a browser-acceptable lifetime; renewal keeps the same CA; a mismatched key, an unreadable file and a missing file are each detected; expiry tiers work; the agent refuses to start HTTPS with a bad certificate; plain HTTP is refused on a non-loopback address; a **real** TLS server is started and driven by a client that verifies against the CA; an untrusted client is **rejected** (proving verification is on); HSTS appears over HTTPS but not on loopback; authentication and dangerous-command confirmation still hold over HTTPS; a real `wss://` socket authenticates and streams; the HTTP listener redirects GET and refuses POST |
| `test_no_fabrication.py` | Every unknown stays unknown: state before checking, TPS with no provider, players with no evidence, versions before detection, Java when unparseable, crash cause when nothing matches, compatibility without a Minecraft version, Tailscale without the daemon, certificate without a file, backups that fail verification, and installs that have not been loaded |
| `test_command_isolation.py` | No `shell=True`, `os.system`, `eval` or `exec` anywhere in the codebase; every subprocess call uses an argument list; no execution-style route exists; `send_command` touches only stdin; twelve OS-command shapes are rejected; a newline cannot smuggle a second command; launch arguments come only from config; a disabled mod is **actually not loaded** by a running server; rollback after a failed startup brings the server back online; user restarts do not count towards the crash limit; five crashes stop automatic restarts |
| `test_reliability.py` | Migrations are idempotent; WAL is active; eight concurrent writers produce no corruption and pass `PRAGMA integrity_check`; the database survives an abrupt close; simultaneous crash events are all recorded; pruning bounds growth; the console buffer stays at 2000 lines with 50,000 pushed through and never reaches the database; websockets reconnect without leaking subscriber queues; a revoked token cannot reconnect; the socket cannot perform actions |
| `test_game_settings.py` | A change rewrites only its own lines and keeps comments, unknown keys, order, escapes, Windows line endings and bytes that aren't UTF-8; bad values are refused per field and nothing is written; the seed locks once the world exists; a key the file doesn't set is reported as not set, not as its default; the port is checked against other servers; a save keeps a safety backup with undo; a running server reports that the change waits for a restart |
| `test_player_actions.py` | Each button builds the plain Minecraft command; names and reasons that could smuggle a second command are refused before the console; every built command passes console validation; an action is "sent" until the fake server's console confirms it, then "done"; "nothing changed" and "failed" are reported as such; no answer is never reported as done; kick and ban need confirming; lists come from Minecraft's files |
| `test_duplicate.py` | A copy has the same jar, mods, config and game settings, its own free port and color; a fresh world leaves the world folders behind and clears the seed; links are never followed out of the source; nothing is written outside the new folder; unsafe folders are refused; a starting server is refused; a copy that fails its check is removed again; a running source is saved and paused for the copy |
| `test_modpack.py` | A pack is described before anything happens; a missing or bad SHA-512, a host other than Modrinth's CDN, plain HTTP and unsafe paths are refused and block the import; zip-slip paths and links in the overrides block it; client-only files are never downloaded; a new server gets the pack's mods and files; a SHA-512 mismatch leaves no server or folder behind; importing into a server moves its mods aside, and a mismatch puts everything back |
| `test_quality_of_life.py` | Auto-sleep is off until a server turns it on and **never** stops one while the player count is Unknown or above zero; it waits out the set time, warns in the game first, and the stop is a planned stop, not a crash; a change that holds the server postpones it; world undo always takes a fresh backup before restoring, refuses while the server runs, refuses a backup that did not check out, and leaves the world untouched when the archive is damaged; "play lost" is unknown when no session was recorded; chat is parsed from what the console printed and a player cannot fake the server's own line; a chat send becomes a `say` command and passes console validation, and newlines and backticks are refused; the checklist ticks only from measured state, is finished for good once done, and is dismissed per server |
| `test_join_info.py` | Only private addresses of adapters that are up are listed; no adapter means no address; Tailscale's own report is passed through; internet reachability is always "not known" |

## Running a subset

```powershell
python -m pytest tests/test_mods.py -q
python -m pytest -k "crash or restart" -v
```

## Adding a test

`tests/conftest.py` gives you a `config` fixture (a complete throwaway server
folder) and `make_config(**overrides)` for variations, for example
`make_config(**{"monitor.max_crashes": 2})`.
