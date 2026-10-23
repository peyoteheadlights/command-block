"""Removing the app (Windows "Installed apps", or the setup's Remove button).

It stops the app (any running Minecraft server is stopped cleanly through
it first), then removes the startup task, the update task, the firewall
rules, the Start Menu shortcuts, the "Installed apps" entry and the program
folder. Your data folder (settings, database, backups, logs) is kept unless
you tick "Also delete my settings and backups". Minecraft server folders
are never touched either way.

A copy for connecting only (a moderator's PC) has no agent: removing it
signs this device out on the owner's PC when it answers, forgets the saved
sign-in, and removes the app, its shortcuts, its start-with-Windows entry
and its install logs. Tailscale is separate and stays.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent import appinfo

from . import layout
from .bootstrap import UPDATE_FOLDER_SUFFIX
from .installlog import InstallLog
from .system import System

# A file the app was still closing can stay locked for a moment after it
# stopped: try the program folder again a few times before giving up.
REMOVE_TRIES = 5


def uninstall(
    program_dir: Path,
    data_root: Path,
    remove_data: bool = False,
    system: System | None = None,
    setup_exe: Path | None = None,
) -> dict[str, Any]:
    """``setup_exe`` is the setup copy this is running from, when it runs
    from a temporary copy of the program folder's setup.exe (bootstrap.py)."""
    system = system or System()
    connect_only = bool((appinfo.install_record(program_dir) or {}).get("connect_only"))
    remove_data = remove_data or connect_only  # it holds only install logs
    log = InstallLog(layout.logs_dir(data_root)) if not remove_data else None
    done: list[str] = []
    problems: list[str] = []
    notes: list[str] = []

    def note(event: str, **fields: Any) -> None:
        if log:
            log.write(event, **fields)

    note("uninstall_started", program_dir=str(program_dir), remove_data=remove_data)
    if connect_only:
        try:
            done_here, notes = system.sign_out_here()
            done += done_here
        except Exception as exc:
            problems.append(f"the saved sign-in: {exc}")
        removals: tuple[tuple[str, Callable[[], None]], ...] = (
            ("app", system.close_app),
            (
                "shortcuts, start-with-Windows and Installed apps entries",
                system.remove_user_entries,
            ),
        )
        notes.append("Tailscale is separate and stays installed.")
    else:
        port = _stop_agent(system, data_root, log, done, problems)
        removals = (
            ("startup task", system.remove_startup),
            ("update task", system.remove_updater_task),
            ("Start menu shortcuts", system.remove_shortcuts),
            ("Installed apps entry", system.remove_uninstall_entry),
            ("Windows alerts entry", system.remove_alerts_entry),
            ("firewall rules", lambda: _remove_firewall(system, port)),
        )
    for name, action in removals:
        try:
            action()
            done.append(f"closed the {name}" if name == "app" else f"removed the {name}")
        except Exception as exc:
            problems.append(f"the {name}: {exc}")
    for folder in (program_dir, program_dir.with_name(program_dir.name + ".previous")):
        if folder.exists() and (folder / "install.json").is_file():
            for attempt in range(REMOVE_TRIES):
                if attempt:
                    time.sleep(1)
                shutil.rmtree(folder, ignore_errors=True)
                if not folder.exists():
                    break
            left = [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []
            if not folder.exists():
                done.append(f"removed {folder}")
            elif (
                setup_exe is not None
                and folder == program_dir
                and left
                and all(p.name.lower() == layout.SETUP_COPY for p in left)
            ):
                # Only the setup.exe that started this is left, held open
                # until it exits: a copy of the setup removes it then.
                system.remove_after_exit(setup_exe, folder)
                done.append(f"removed {folder} (its setup.exe goes when the setup closes)")
            else:
                problems.append(f"some files in {folder} were in use and are removed on restart")
    # The last update's checked setup file (installer/apply_update.py).
    staging = program_dir.with_name(program_dir.name + UPDATE_FOLDER_SUFFIX)
    if staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)
        done.append(f"removed {staging}")
    if remove_data and data_root.is_dir():
        inside = servers_inside(data_root)
        if inside:
            # Minecraft server folders are never removed, so neither is the
            # data folder that holds one.
            problems.append(
                f"{data_root} was kept because it holds a Minecraft server folder "
                f"({inside[0]}). Move or delete it yourself."
            )
        else:
            shutil.rmtree(data_root, ignore_errors=True)
            done.append(f"removed {data_root}")
    note("uninstall_finished", done=done, problems=problems)
    return {
        "ok": not problems,
        "done": done,
        "problems": problems,
        "notes": notes,
        "kept": None if remove_data else str(data_root),
        "log": str(log.path) if log else None,
    }


def _stop_agent(
    system: System, data_root: Path, log: InstallLog | None, done: list[str], problems: list[str]
) -> int:
    """Stop the agent (its running Minecraft servers cleanly first). Returns
    its port, for the firewall rules."""
    system.host, port, tls, token = _settings(data_root)
    if log:
        log.hide(token)
    if system.agent_answers(port, tls):
        if token:
            try:
                for row in system.api(port, tls, token, "GET", "/servers")["servers"]:
                    if row.get("state") in ("running", "starting"):
                        system.api(port, tls, token, "POST", f"/servers/{row['id']}/server/stop")
                        done.append(f"stopped {row['name']}")
            except Exception as exc:
                problems.append(f"couldn't stop the Minecraft servers first: {exc}")
        system.stop_agent(port, tls)
        done.append("stopped the server panel")
    return port


def _remove_firewall(system: System, port: int) -> None:
    system.firewall(Path(__file__).resolve().parent / "firewall.ps1", port, 0, remove=True)


def servers_inside(data_root: Path, folder: Path | None = None) -> list[str]:
    """The server folders in config.yaml that are inside ``folder`` (the
    data folder when not given)."""
    try:
        from agent.config import Config

        config = Config.load(layout.config_path(data_root), layout.env_path(data_root))
        folders = [config.for_server(i).server_dir for i in config.server_ids]
    except Exception:
        return []
    root = (folder or data_root).resolve()
    found = []
    for folder in folders:
        try:
            folder.resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        found.append(str(folder))
    return found


def _settings(data_root: Path) -> tuple[str, int, bool, str]:
    from .setup_tool import read_env
    from .system import read_address

    host, port, tls = read_address(layout.config_path(data_root))
    return host, port, tls, read_env(layout.env_path(data_root)).get("MCSC_API_TOKEN", "")
