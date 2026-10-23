# Desktop app

The Command Block window for Windows (`CommandBlock.exe`). It is built with
PySide6 Qt Widgets and only talks to the agent through its API, the same API
the phone apps use.

- `app.py` starts it: it reads `pairing.json` (written by the agent next to
  `config.yaml`) and connects to the agent on 127.0.0.1. On a PC with no
  agent, `connect.py` sets up Tailscale first, then takes the owner's share
  link and the pairing link or invite. `self_update.py` is that PC's own
  daily update check.
- `agent.py` is the connection. It pins the agent's certificate by its
  SHA-256 fingerprint instead of turning certificate checks off.
- `window.py` has the main window: sign-in, All servers and the binder tabs.
  Anything that calls the agent runs off the window's thread so the window
  never freezes. `server_tab.py` is one server's tab: header card, page tabs
  and the right panel. `overview.py` is its Overview in Simple and Technical,
  and `graphs.py` draws the graphs.
- Each server page is a `Page` (`ui.py`) that asks the agent only while it
  is open: `players.py` (Players and chat), `console.py`, `performance.py`,
  `schedules.py`, `activity.py` (every event, and the crashes with what
  the crash analyzer found) and `backups.py` (backups, Restore world, and
  bringing a world in or taking it home; a world dropped on the window goes
  there too) and `mods.py` (Mods or Plugins: the installed list, Modrinth
  search, missing mods, Update all, modpacks and .jar files dropped on the
  window; a Bedrock server's add-ons) and `settings.py` (Settings: server
  settings, game settings and memory; `version.py` has the version and
  Bedrock players sections). `fixes.py` has the crash fix buttons and the
  Get help file.
- `new_server.py` is the "+" tab: a new server, one from a modpack, or one
  already on the PC.
- `gear.py` has the gear's screen, its sections listed on the left: PC
  settings, Updates, Signed-in devices and Your account, with Moderators in
  `moderators.py`. `wake_setup.py` has PC settings' Wake-on-LAN cards, and
  `wake.py` sends the wake signal and runs "Help wake <PC>". `help.py` is
  "?": Getting started and About. The look
  (light, dark or follow Windows) is the one setting kept on this PC.
- `feed.py` is the live feed: one websocket to the agent that keeps the
  window current, and `alerts.py` is the bell's list of alerts.
- `words.py` and `theme.py` read the words and colors from the top-level
  `shared/`, so the desktop app, the phone apps and the installer look and
  read the same. The server's color tints the window with the same sums as
  the phone apps (`agent/colors.py`). The app's own words start with
  `desktop.` in `shared/strings.json`.
- `icons.py` draws the line icons and the server badges; `images/` holds the
  app's icon (`python scripts/make_icons.py` makes it from `docs/images/logo.png`).
- `pc.py` is everything the app asks of Windows itself. Tests swap it for
  `tests/fake_pc.py`.
- `api-routes.txt` lists every agent route the app calls. A test checks that
  each one exists.

Run it from a checkout with `python -m desktop.app` while the agent is
running. `python scripts/desktop_shots.py` saves pictures of the screens in
light and dark without touching your real agent.
