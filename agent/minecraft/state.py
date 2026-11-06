from __future__ import annotations

from enum import Enum


class ServerState(str, Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    STOPPING = "STOPPING"
    CRASHED = "CRASHED"
    RESTARTING = "RESTARTING"
    # Crashed, with an automatic restart counting down. Nothing but
    # "restart now" or "cancel" may leave this state; a normal start is refused.
    RESTART_PENDING = "RESTART_PENDING"
    UNKNOWN = "UNKNOWN"


class ExitReason(str, Enum):
    USER_STOP = "user_stop"  # operator pressed STOP / RESTART
    SCHEDULED_STOP = "scheduled_stop"  # a scheduled task asked for it
    CLEAN_EXIT = "clean_exit"  # server shut itself down cleanly
    CRASH = "crash"  # died while running
    STARTUP_FAILURE = "startup_failure"  # died before "Done (..)!"
    FORCE_KILLED = "force_killed"  # graceful stop timed out / kill requested
    UNKNOWN = "unknown"


NORMAL_REASONS = {ExitReason.USER_STOP, ExitReason.SCHEDULED_STOP, ExitReason.CLEAN_EXIT}


def controls(
    state: ServerState,
    answering: dict | None = None,
    busy: str | None = None,
    left_behind: bool = False,
) -> dict:
    """What every app shows for a server and which buttons the agent takes
    right now, worked out once here so no app decides it on its own.

    ``label`` is the end of a ``state.*`` word in shared/strings.json.
    ``can`` lists what the agent accepts now: start, stop, force_stop,
    restart, and change (version, type, mods: only while it is off)."""
    if state is ServerState.ONLINE and (answering or {}).get("problem"):
        label = "not_answering"  # kept while a check is skipped, e.g. during a backup
    elif state is ServerState.RESTART_PENDING:
        label = "crashed"  # the countdown is shown beside it
    else:
        label = state.value.lower()
    if left_behind:
        # Found running from before the agent started: no console to stop it with.
        return {"label": label, "can": ["force_stop"]}
    can = []
    if state in (ServerState.OFFLINE, ServerState.CRASHED) and not busy:
        can += ["start", "change"]
    if state in (ServerState.STARTING, ServerState.ONLINE):
        can.append("stop")
    if state is ServerState.ONLINE and not busy:
        can.append("restart")
    return {"label": label, "can": can}
