"""The fix buttons: what the agent says fixes a crash (agent/minecraft/
analyzer.py fix_for), done after one click and, where something changes, one
short question. Each uses a route the agent already has, so Undo appears
wherever the agent keeps one. Shared by Simple's suggestions and the crash
in Activity, with the Get help file for a crash nobody can name."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .agent import AgentError
from .words import text

if TYPE_CHECKING:
    from .window import MainWindow

GB = 1024
Say = Callable[..., None]  # say(words, undo=None): what happened, and how to undo it
CONFIDENCE = {
    "confirmed": "crashes.conf_confirmed",
    "likely": "crashes.conf_likely",
    "possible": "crashes.conf_possible",
}


def sure(confidence: str | None) -> str:
    """The analyzer's confidence in words."""
    return text(CONFIDENCE.get(confidence or "", "crashes.conf_unknown"))


def run_fix(window: MainWindow, sid: str, fix: dict, say: Say) -> None:
    """Does ``fix`` for server ``sid``; ``say`` shows what happened."""
    agent, here = window.agent, window.still_on(sid)

    def call(
        method: str,
        path: str,
        then: Callable[[Any], None],
        body: dict | None = None,
        slow: bool = False,
    ):
        window.run(
            lambda: agent.on(sid, method, path, body, slow),
            here(then),
            here(lambda exc: say(str(exc))),
        )

    kind = fix["kind"]
    if kind in ("memory_up", "memory_down"):
        call("GET", "/memory", lambda memory: change_memory(window, sid, memory, kind, say))
    elif kind == "eula":
        if window.confirm(
            text("desktop.fix.eula_title"),
            text("desktop.fix.eula_body", url="https://aka.ms/MinecraftEULA"),
            text("desktop.fix.accept"),
        ):
            call("POST", "/eula", lambda _r: say(text("desktop.fix.eula_done")))
    elif kind == "port":
        window.run(
            lambda: agent.call("GET", "/ports/suggest"),
            here(lambda found: change_port(window, sid, found.get("port"), say)),
            here(lambda exc: say(str(exc))),
        )
    elif kind == "dependency":
        call(
            "POST",
            "/mods/dependencies/plan",
            lambda plan: install(window, sid, plan, say),
            {},
            slow=True,
        )
    elif kind in ("mod_update", "mod_disable"):
        update = kind == "mod_update"
        name = fix.get("mod") or fix["filename"]
        if window.confirm(
            text(
                "desktop.fix.mod_update_title" if update else "desktop.fix.mod_off_title", mod=name
            ),
            text("desktop.fix.mod_update_body" if update else "desktop.fix.mod_off_body", mod=name),
            fix["label"],
        ):
            body = {"filename": fix["filename"]}
            if update:
                body["version_id"] = fix.get("version_id")
            path = "/mods/update" if update else "/mods/disable"

            def done(result: dict) -> None:
                if update:  # Undo puts the version from before back
                    back = "/mods/rollback"
                    undo = {
                        "mod_id": result["updated"]["mod_id"],
                        "archive_path": result["rollback_archive"],
                    }
                else:
                    back, undo = "/mods/enable", {"filename": result["mod"]["filename"]}
                say(
                    text("desktop.fix.mod_done"),
                    lambda: call("POST", back, lambda _r: say(text("desktop.fix.undone")), undo),
                )

            call("POST", path, done, body, slow=update)  # a Modrinth download can be slow
    elif kind == "java":
        if window.agent.place != "this PC":
            say(text("desktop.fix.java_elsewhere"))
            return
        window.run(
            lambda: window.pc.open_setup(),
            lambda _r: say(text("desktop.fix.java_opened")),
            lambda exc: say(str(exc)),
        )


def change_memory(window: MainWindow, sid: str, memory: dict, kind: str, say: Say) -> None:
    now = memory.get("limit_mb")
    start = (now or 2 * GB) + (GB if kind == "memory_up" else -GB)
    start = max(memory.get("min_mb") or GB, min(start, memory.get("max_mb") or start))
    total = memory.get("total_mb")
    typed = window.ask(
        text("desktop.fix.memory_title"),
        text(
            "desktop.fix.memory_body",
            now=f"{now / GB:g}" if now else text("state.unknown"),
            total=f"{total / GB:.0f}" if total else text("state.unknown"),
        ),
        text("action.save"),
        text("desktop.fix.memory_field"),
        value=f"{start / GB:g}",
    )
    if typed is None:
        return
    try:
        mb = round(float(typed.replace(",", ".")) * GB)
    except ValueError:
        say(text("desktop.fix.not_a_number"))
        return
    window.run(
        lambda: window.agent.on(sid, "PUT", "/memory", {"memory_mb": mb}),
        window.still_on(sid)(lambda _r: say(text("desktop.fix.memory_saved", gb=f"{mb / GB:g}"))),
        window.still_on(sid)(lambda exc: say(str(exc))),
    )


def change_port(window: MainWindow, sid: str, port: int | None, say: Say) -> None:
    if not port:
        say(text("desktop.fix.no_port"))
        return
    if not window.confirm(
        text("desktop.fix.port_title", port=port),
        text("desktop.fix.port_body", port=port),
        text("desktop.fix.use_port", port=port),
    ):
        return
    window.run(
        lambda: window.agent.on(sid, "PUT", "/game-settings", {"values": {"server-port": port}}),
        window.still_on(sid)(lambda _r: say(text("desktop.fix.port_done", port=port))),
        window.still_on(sid)(lambda exc: say(str(exc))),
    )


def install(
    window: MainWindow, sid: str, plan: dict, say: Say, then: Callable[[], None] | None = None
) -> None:
    """The missing mods ``plan`` names, after one question. What can't be
    installed automatically is named with why. ``then`` runs once the
    install has answered, either way."""
    items = plan.get("items") or []
    cant = "\n".join(
        f"  {u.get('name') or u['mod_id']}: {u['reason']}" for u in plan.get("unresolvable") or []
    )
    if not items:
        say("\n".join(x for x in (text("desktop.fix.deps_none"), cant) if x))
        return
    names = "\n".join(
        f"  {i.get('title') or i['mod_id']} {i.get('version_number') or ''}".rstrip() for i in items
    )
    body = text("desktop.fix.deps_body", list=names)
    if cant:
        body += f"\n\n{text('deps.not_automatic')}\n{cant}"
    if window.confirm(text("desktop.fix.deps_title"), body, text("desktop.fix.install")):
        say(text("deps.installing"))
        window.run(
            lambda: window.agent.on(sid, "POST", "/mods/dependencies/install", {}, slow=True),
            window.still_on(sid)(lambda result: say(installed(result))),
            window.still_on(sid)(lambda exc: say(str(exc))),
            then,
        )


def installed(result: dict) -> str:
    """What installing the missing mods did, mod by mod."""
    rows = result.get("results") or []
    words = []
    done = [r["title"] for r in rows if r["result"] == "installed"]
    if done:
        words.append(text("deps.installed_toast", names=", ".join(done)))
    words += [
        text("deps.failed_toast", name=r["title"], error=r.get("detail") or "")
        for r in rows
        if r["result"] == "failed"
    ]
    still = [g.get("name") or g.get("mod_id") for g in result.get("still_missing") or []]
    if still:
        words.append(text("deps.still_missing", names=", ".join(map(str, still))))
    return " ".join(words) or text("deps.all_ok")


def get_help(window: MainWindow, say: Say) -> None:
    """The Get help file: its file list first, then made and saved with
    Windows' save box. It is about the whole PC, not one server."""
    agent = window.agent

    def confirm(listing: dict) -> None:
        files = "\n".join(f"  {f['name']}" for f in listing.get("files") or [])
        if not window.confirm(
            text("gethelp.confirm_title"),
            f"{text('gethelp.confirm_body')}\n{files}\n\n{text('gethelp.never')}",
            text("gethelp.save"),
        ):
            return

        def make() -> tuple[str, bytes]:
            made = agent.call("POST", "/help-bundle", {"confirm": True})
            return made["filename"], agent.download(f"/help-bundle/{made['token']}")

        window.run(make, save, lambda exc: say(str(exc)))

    def save(made: tuple[str, bytes]) -> None:
        try:
            path = window.pc.save_file(*made)
        except AgentError as exc:
            say(str(exc))
            return
        if path:
            say(text("desktop.saved", path=path))

    window.run(lambda: agent.call("GET", "/help-bundle"), confirm, lambda exc: say(str(exc)))
