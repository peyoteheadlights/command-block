"""The Backups page: Back up now, every backup with both restores (only the
world, or everything in it), the second copy off this PC, and bringing a
world in or taking it home. Files and folders are picked with Windows' own
boxes, which show the PC the app runs on: on a moderator's PC a world folder
is zipped there and sent, and the second copy folder (a folder on the
server's PC) can only be chosen on the server's PC."""

from __future__ import annotations

import shutil
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .overview import card
from .parts import IconButton, Row
from .ui import Page, clock, duration, label, push, refill, size
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

KINDS = {
    "manual": "backups.kind_manual",
    "scheduled": "backups.kind_scheduled",
    "safety": "backups.kind_safety",
    "season": "season.kind",
}
COPY = {
    "ok": "offsite.copied",
    "failed": "offsite.failed",
    "unreachable": "offsite.unreachable",
    "missing": "offsite.missing",
    "none": "offsite.not_copied",
}
WORLD_FILES = (".zip", ".mcworld", ".mctemplate")
RUNNING = {"ONLINE", "STARTING", "STOPPING", "RESTARTING"}


def loss(point: dict) -> str:
    """What going back to this point would lose, in plain words."""
    since = text("world.since", duration=duration(point.get("age_seconds")))
    played = point.get("played_seconds")
    if played is None:
        return f"{since} · {text('world.play_unknown')}"
    if played < 60:
        return f"{since} · {text('world.no_play')}"
    return f"{since} · {text('world.play_lost', duration=duration(played))}"


def is_world(path: Path) -> bool:
    """What can be dropped on the window to bring a world in."""
    if path.is_dir():
        return (path / "level.dat").is_file()  # Java and Bedrock worlds both have one
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                if "pack.mcmeta" in zf.namelist():
                    return False  # a datapack, which the Mods page adds
        except (OSError, zipfile.BadZipFile):
            pass  # the world import says what's wrong with it
    return path.suffix.lower() in WORLD_FILES


def zipped(folder: Path, into: Path) -> Path:
    """The folder as a zip with the folder itself at the top, as the
    agent's upload expects."""
    made = shutil.make_archive(
        str(into / "world"), "zip", root_dir=folder.parent, base_dir=folder.name
    )
    return Path(made)


class BackupsPage(Page):
    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.backups: list[dict] = []
        self.timeline: dict = {}
        self.offsite: dict = {}
        top = QHBoxLayout()
        self.back_up = push(text("action.back_up_now"), "primary")
        self.back_up.clicked.connect(self.make_backup)
        self.save_world = push("")
        self.save_world.clicked.connect(
            lambda: self.ask(
                "POST", "/worlds/save", lambda _r: self.say(text("backups.save_sent")), self.said
            )
        )
        top.addWidget(self.back_up)
        top.addWidget(self.save_world)
        top.addStretch()
        self.column.addLayout(top)

        listed, layout = card(text("backups.list"), box=False)
        self.stop_first = label(kind="problem")
        layout.addWidget(self.stop_first)
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        self.keeping = label(kind="sub")
        layout.addWidget(self.keeping)
        self.column.addWidget(listed)

        # Left out of backups (agent/backups/manager.py size_preview): map
        # tiles and other folders the owner picks, with what a backup weighs.
        self.leftout_card, layout = card(text("leftout.title"))
        layout.addWidget(label(text("leftout.lead"), "sub"))
        self.leftout_size = label()
        layout.addWidget(self.leftout_size)
        self.leftout_rows = QVBoxLayout()
        layout.addLayout(self.leftout_rows)
        row = QHBoxLayout()
        self.leftout_path = QLineEdit()
        self.leftout_path.setPlaceholderText(text("leftout.path_hint"))
        self.leftout_path.setAccessibleName(text("leftout.title"))
        self.leftout_add = push(text("leftout.leave_out"))
        self.leftout_add.clicked.connect(lambda: self.leave_out(self.leftout_path.text()))
        row.addWidget(self.leftout_path, 1)
        row.addWidget(self.leftout_add)
        layout.addLayout(row)
        self.measure = push(text("leftout.measure"), "plain")
        self.measure.clicked.connect(self.load_size)
        row = QHBoxLayout()
        row.addWidget(self.measure)
        row.addStretch()
        layout.addLayout(row)
        self.column.addWidget(self.leftout_card)
        self.size_data: dict = {}

        self.offsite_card, layout = card(text("offsite.title"))
        layout.addWidget(label(text("offsite.lead"), "sub"))
        self.offsite_words = label()
        self.offsite_folder = label(kind="muted")
        self.offsite_elsewhere = label(text("desktop.backups.offsite_elsewhere"), "sub")
        buttons = QHBoxLayout()
        self.choose = push("")
        self.choose.clicked.connect(self.choose_offsite)
        self.turn_off = push(text("offsite.turn_off"), "plain")
        self.turn_off.clicked.connect(lambda: self.set_offsite(""))
        buttons.addWidget(self.choose)
        buttons.addWidget(self.turn_off)
        buttons.addStretch()
        for widget in (self.offsite_words, self.offsite_folder, self.offsite_elsewhere):
            layout.addWidget(widget)
        layout.addLayout(buttons)
        self.column.addWidget(self.offsite_card)
        # Until the agent answers, nothing says what may be done.
        for hidden in (self.offsite_elsewhere, self.choose, self.turn_off, self.stop_first):
            hidden.hide()

        self.import_card, layout = card(text("import.title"))
        self.import_lead = label(kind="sub")
        self.import_stop = label(text("import.stop_first"), "problem")
        self.import_stop.hide()
        layout.addWidget(self.import_lead)
        layout.addWidget(self.import_stop)
        buttons = QHBoxLayout()
        self.choose_zip = push("")
        self.choose_zip.clicked.connect(self.pick_zip)
        self.choose_folder = push(text("import.choose_folder"))
        self.choose_folder.clicked.connect(self.pick_folder)
        buttons.addWidget(self.choose_zip)
        buttons.addWidget(self.choose_folder)
        buttons.addStretch()
        layout.addLayout(buttons)
        layout.addWidget(label(text("desktop.backups.drop"), "sub"))
        self.saves_title = label(text("import.from_saves"), "step")
        self.saves = QVBoxLayout()
        layout.addWidget(self.saves_title)
        layout.addLayout(self.saves)
        self.column.addWidget(self.import_card)

        self.home_card, layout = card(text("download.title"))
        self.home_lead = label(kind="sub")
        layout.addWidget(self.home_lead)
        self.take_home = push(text("download.button"))
        self.take_home.clicked.connect(self.download_world)
        layout.addWidget(self.take_home)
        self.column.addWidget(self.home_card)

        # New season (agent/seasons.py): a fresh world, the old one kept.
        self.season_card, layout = card(text("season.title"))
        layout.addWidget(label(text("season.lead"), "sub"))
        row = QHBoxLayout()
        self.seed = QLineEdit()
        self.seed.setMaxLength(32)
        self.seed.setPlaceholderText(text("season.seed_hint"))
        self.seed.setAccessibleName(text("season.seed"))
        row.addWidget(label(text("season.seed"), "sub"))
        row.addWidget(self.seed, 1)
        layout.addLayout(row)
        self.announce = QCheckBox(text("season.announce"))
        self.announce.setChecked(True)
        layout.addWidget(self.announce)
        self.season_button = push(text("season.button"))
        self.season_button.clicked.connect(self.new_season)
        row = QHBoxLayout()
        row.addWidget(self.season_button)
        row.addStretch()
        layout.addLayout(row)
        self.column.addWidget(self.season_card)

        # Clean-up (agent/cleanup.py): shown first, moved to a bin, Undo.
        self.cleanup_card, layout = card(text("cleanup.title"))
        layout.addWidget(label(text("cleanup.lead", days=7), "sub"))
        self.find_button = push(text("cleanup.look"))
        self.find_button.clicked.connect(self.load_cleanup)
        self.cleanup_rows = QVBoxLayout()
        self.cleanup_note = label(kind="sub")
        self.to_bin = push(text("cleanup.move"), "primary")
        self.to_bin.clicked.connect(self.clean_up)
        self.bin_words = label(kind="sub")
        self.undo_last = push(text("cleanup.undo_last"), "plain")
        self.undo_last.clicked.connect(lambda: self.cleanup_undo(self.bin[0]["batch"]))
        self.empty_bin = push(text("cleanup.empty"), "plain")
        self.empty_bin.clicked.connect(self.cleanup_empty)
        row = QHBoxLayout()
        row.addWidget(self.find_button)
        row.addWidget(self.to_bin)
        row.addStretch()
        layout.addLayout(self.cleanup_rows)
        layout.addWidget(self.cleanup_note)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.bin_words, 1)
        row.addWidget(self.undo_last)
        row.addWidget(self.empty_bin)
        layout.addLayout(row)
        self.column.addWidget(self.cleanup_card)
        self.picks: dict[str, QCheckBox] = {}
        self.bin: list[dict] = []

        self.worlds_card, layout = card(text("backups.worlds"))
        self.worlds = QVBoxLayout()
        layout.addLayout(self.worlds)
        self.column.addWidget(self.worlds_card)
        self.column.addStretch()

    # ------------------------------------------------------------ loading
    def may(self, permission: str) -> bool:
        return self.window_.may(self.row["id"], permission)

    @property
    def here(self) -> bool:
        """The app runs on the server's PC, so its folders are the agent's."""
        return self.window_.agent.place == "this PC"

    @property
    def bedrock(self) -> bool:
        return self.row.get("edition") == "bedrock"

    @property
    def running(self) -> bool:
        """From the agent's timeline once read: the row can be a moment old."""
        if "server_running" in self.timeline:
            return bool(self.timeline["server_running"])
        return self.row.get("state") in RUNNING

    def load(self) -> None:
        technical = mode["technical"]
        self.timeline = {}  # it may be another server's
        self.back_up.setVisible(self.may("backups.create"))
        # Another server's backup or download may have left these greyed out.
        self.back_up.setEnabled(True)
        self.take_home.setEnabled(True)
        self.save_world.setText(text("backups.save_world"))
        self.save_world.setVisible(technical and self.may("server.control"))
        replace = self.may("world.replace")
        self.import_card.setVisible(replace)
        self.import_lead.setText(text("import.lead_bedrock" if self.bedrock else "import.lead"))
        self.choose_zip.setText(
            text("import.choose_mcworld" if self.bedrock else "import.choose_zip")
        )
        self.home_card.setVisible(self.may("backups.download"))
        self.season_card.setVisible(replace)
        self.season_button.setEnabled(True)
        self.home_lead.setText(text("download.lead_bedrock" if self.bedrock else "download.lead"))
        self.worlds_card.setVisible(technical)
        self.saves_title.hide()
        refill(self.saves, [])
        self.leftout_card.setVisible(self.may("settings.server"))
        self.leftout_path.setVisible(technical)
        self.leftout_add.setVisible(technical)
        self.leftout_size.setText("")
        refill(self.leftout_rows, [])
        self.size_data = {}
        if self.leftout_card.isVisibleTo(self):
            self.load_size()
        self.cleanup_card.setVisible(self.may("backups.delete"))
        self.show_cleanup({})

        def offsite_first(data: dict) -> None:
            # The list shows each backup's second copy, so the folder comes first.
            self.show_offsite(data)
            self.load_list()

        def offsite_failed(exc: Exception) -> None:
            self.said(exc)
            self.load_list()

        self.ask("GET", "/backups/offsite", offsite_first, offsite_failed)
        if technical:
            self.ask("GET", "/worlds", self.show_worlds)
        # Single-player's saves on the server's PC; elsewhere they'd be the wrong PC's.
        if replace and self.here and not self.bedrock:
            self.ask("GET", "/world/saves", self.show_saves)

    def changed(self) -> None:
        self.load_list()

    def load_list(self) -> None:
        def got_timeline(timeline: dict) -> None:
            self.timeline = timeline
            self.ask("GET", "/backups", self.show_backups)

        self.ask("GET", "/world/timeline", got_timeline)

    def show_backups(self, data: dict) -> None:
        self.backups = data.get("backups") or []
        points = {p["backup_id"]: p for p in self.timeline.get("points") or []}
        busy = self.timeline.get("blocked_by")
        self.stop_first.setText(text("world.busy", job=busy) if busy else text("world.stop_first"))
        self.stop_first.setVisible(
            bool(points) and not self.timeline.get("can_restore") and self.may("backups.restore")
        )
        refill(
            self.rows,
            [self.backup_row(b, points.get(b["id"])) for b in self.backups]
            or [label(text("backups.none"), "step"), label(text("backups.none_hint"), "sub")],
        )
        for button in (self.choose_zip, self.choose_folder):
            button.setEnabled(not self.running)
        self.import_stop.setVisible(self.running)
        kept = data.get("retention")
        self.keeping.setText(text("backups.keeping", **kept) if kept else "")

    def backup_row(self, b: dict, point: dict | None) -> QFrame:
        row = Row()
        h = row.line
        words = QVBoxLayout()
        made = clock(b.get("created_at"))
        words.addWidget(label(b["name"] if mode["technical"] else made, "step"))
        copy = b.get("copy") or {"state": "none"}
        parts = [
            b.get("label") or (text(KINDS[b["kind"]]) if b.get("kind") in KINDS else b.get("kind")),
            size(b.get("size_bytes")),
        ]
        if self.offsite.get("directory") or copy["state"] != "none":
            state = text(COPY.get(copy["state"], "state.unknown"), reason=copy.get("reason") or "")
            parts.append(f"{text('offsite.col')}: {state}")
        if mode["technical"]:
            parts.insert(0, made)
        if b.get("kept") and b.get("kind") != "season":
            parts.append(text("backups.kept"))
        if b.get("status") != "ok":
            parts.append(text("backup.unchecked"))
        words.addWidget(label("  ·  ".join(str(p) for p in parts if p), "sub"))
        if point:
            words.addWidget(label(loss(point), "sub"))
        h.addLayout(words, 1)
        if point and self.may("backups.restore"):
            if point.get("checked"):
                restore = push(text("world.title"))
                restore.setAccessibleName(f"{text('world.title')}: {made}")
                restore.setEnabled(bool(self.timeline.get("can_restore")))
                restore.clicked.connect(lambda: self.restore_world(point))
                h.addWidget(restore)
            else:
                h.addWidget(label(text("world.not_offered"), "muted"))
        more = IconButton("more", text("desktop.more"))
        more.setAccessibleName(f"{text('desktop.more')}: {made}")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        actions: list[tuple[str, Callable[[], None]]] = [("backups.verify", lambda: self.verify(b))]
        if self.may("backups.download"):
            actions.append(("backups.download", lambda: self.download(b)))
        if (
            self.offsite.get("directory")
            and b.get("status") == "ok"
            and copy["state"] != "ok"
            and self.may("backups.create")
        ):
            actions.append(("offsite.copy_now", lambda: self.copy(b)))
        if self.may("backups.restore"):
            actions.append(("desktop.backups.restore_all", lambda: self.restore_all(b)))
        whole = "/" not in (b.get("includes") or "")  # not a backup of single files
        if b.get("status") == "ok" and whole and self.may("servers.manage"):
            season = b.get("kind") == "season"
            key = "season.open" if season else "backups.open_as_server"
            actions.append((key, lambda: self.open_as_server(b, season)))
        if b.get("status") == "ok" and b.get("kind") not in ("manual", "season"):
            if self.may("backups.delete"):
                key = "backups.unkeep" if b.get("kept") else "backups.keep"
                actions.append((key, lambda: self.keep(b, not b.get("kept"))))
        if self.may("backups.delete"):
            actions.append(("backups.delete", lambda: self.delete(b)))
        for key, do in actions:
            menu.addAction(text(key)).triggered.connect(lambda _=False, d=do: d())
        more.setMenu(menu)
        h.addWidget(more)
        return row

    def show_worlds(self, data: dict) -> None:
        rows = []
        for w in data.get("worlds") or []:
            last = w.get("last_backup")
            facts = [
                size(w.get("size_bytes")) if w.get("exists") else text("backups.world_missing"),
                f"{text('backups.col_modified')}: {clock(w.get('modified')) or '-'}",
                f"{text('backups.col_last_backup')}: "
                + (
                    f"{last['name']} ({clock(last.get('created_at'))})"
                    if last
                    else text("time.never")
                ),
            ]
            rows.append(label(f"{w['label']}  ·  {'  ·  '.join(facts)}"))
        refill(self.worlds, rows)

    def show_saves(self, data: dict) -> None:
        rows = []
        for world in (data.get("worlds") or [])[:8]:
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(label(world["folder"]), 1)
            h.addWidget(label(clock(world.get("modified")), "muted"))
            use = push(text("import.use"))
            use.setAccessibleName(f"{text('import.use')}: {world['folder']}")
            use.clicked.connect(lambda _=False, p=world["path"]: self.bring_in(p))
            h.addWidget(use)
            rows.append(line)
        self.saves_title.setVisible(bool(rows))
        refill(self.saves, rows)

    def said(self, exc: Exception) -> None:
        self.say(str(exc))

    def run_here(self, work: Callable[[], Any], done: Callable[[Any], None], always=None) -> None:
        """Like ask(), for work that isn't one JSON call (files)."""
        here = self.window_.still_on(self.row["id"])
        self.window_.run(work, here(done), here(self.said), always)

    # ------------------------------------------------------------ backups
    def make_backup(self) -> None:
        self.back_up.setEnabled(False)
        self.say(text("backups.started"))

        def done(result: dict) -> None:
            check = result.get("verification")
            self.say(
                text("backups.verified_toast", name=result["name"], entries=check["entries"])
                if check
                else text("backup.done", name=result["name"])
            )
            self.load_list()
            self.window_.load_server_parts()

        self.ask(
            "POST",
            "/backups",
            done,
            self.said,
            body={},
            always=lambda: self.back_up.setEnabled(True),
            slow=True,
        )

    def verify(self, b: dict) -> None:
        self.say(text("backups.checking"))
        self.ask(
            "GET",
            f"/backups/{b['id']}/verify",
            lambda r: self.say(
                text("backups.verify_ok")
                if r.get("ok")
                else text("backups.verify_failed", reason=r.get("reason"))
            ),
            self.said,
            slow=True,
        )

    def download(self, b: dict) -> None:
        path = self.window_.pc.save_path(b["name"])
        if not path:
            return
        sid, agent = self.row["id"], self.window_.agent
        self.say(text("backups.preparing"))
        self.run_here(
            lambda: agent.download_to(f"/servers/{sid}/backups/{b['id']}/download", path),
            lambda _r: self.say(text("desktop.saved", path=path)),
        )

    def copy(self, b: dict) -> None:
        self.say(text("offsite.copying"))

        def done(result: dict) -> None:
            copy = result.get("copy") or {}
            self.say(
                text("offsite.copy_done")
                if copy.get("state") == "ok"
                else text("offsite.copy_failed", reason=copy.get("reason") or text("state.unknown"))
            )
            self.load_list()

        self.ask("POST", f"/backups/{b['id']}/copy", done, self.said, slow=True)

    def delete(self, b: dict) -> None:
        if not self.window_.confirm(
            text("backups.delete_title", name=b["name"]),
            text("backups.delete_body"),
            text("backups.delete_confirm"),
        ):
            return

        def done(_r) -> None:
            self.say(text("backups.deleted"))
            self.load_list()

        self.ask("DELETE", f"/backups/{b['id']}", done, self.said)

    # ------------------------------------------------------------ seasons
    def new_season(self) -> None:
        if not self.window_.confirm(
            text("season.confirm_title", name=self.row["name"]),
            text("season.confirm_body"),
            text("season.button"),
        ):
            return
        self.season_button.setEnabled(False)
        self.say(text("season.starting"))
        body = {
            "seed": self.seed.text().strip(),
            "announce": self.announce.isChecked(),
            "confirm": True,
        }
        self.ask(
            "POST",
            "/world/new-season",
            lambda result: self.restored(text("season.done"), result),
            self.said,
            body=body,
            always=lambda: self.season_button.setEnabled(True),
            slow=True,
        )

    def keep(self, b: dict, keep: bool) -> None:
        def done(_r) -> None:
            self.say(text("backups.kept_toast" if keep else "backups.unkept_toast"))
            self.load_list()

        self.ask("POST", f"/backups/{b['id']}/keep", done, self.said, body={"keep": keep})

    def open_as_server(self, b: dict, season: bool = False) -> None:
        """A backup (or an old season) as a server of its own: Duplicate,
        with the backup's world, settings and add-ons. The server it came
        from stays as it is."""
        made = time.strftime("%Y-%m-%d", time.localtime(b.get("created_at") or 0))
        key = "season.copy_name" if season else "backups.copy_name"
        name = text(key, name=self.row["name"], date=made)[:60]

        def suggested(found: dict) -> None:
            body = {
                "name": name,
                "directory": str(Path(found["directory"]).parent / name),
                "world": "fresh",
                "from_backup": b["id"],
            }
            self.ask(
                "POST",
                "/duplicate",
                lambda _r: self.say(text("dup.started", name=name)),
                self.said,
                body=body,
            )

        self.ask("GET", "/duplicate", suggested, self.said)

    # ------------------------------------------------------------ restoring
    def restored(self, words: str, result: dict) -> None:
        """After a restore or an import: when the agent made a backup of how
        it was first, the header's Undo goes back."""
        undo = result.get("undo") or result.get("safety_backup")
        self.say(words, (lambda: self.window_.move_change("undo")) if undo else None)
        self.load_list()
        self.window_.load_server_parts()

    def restore_world(self, point: dict) -> None:
        body = text("world.confirm_body", loss=loss(point)) + "\n\n" + text("world.confirm_safety")
        if not self.window_.confirm(
            text("world.confirm_title", when=clock(point.get("created_at"))),
            body,
            text("world.confirm_action"),
        ):
            return
        self.say(text("world.working"))
        self.ask(
            "POST",
            "/world/undo",
            lambda r: self.restored(text("desktop.backups.world_done"), r),
            self.said,
            body={"backup_id": point["backup_id"], "confirm": True},
            slow=True,
        )

    def restore_all(self, b: dict) -> None:
        """The whole backup: mods, settings and all. The agent stops the
        server first, and it's started again after when it was running."""
        again = self.row.get("state") == "ONLINE"

        def ask_first(preview: dict) -> None:
            steps = "\n".join(f"• {step}" for step in preview.get("will_happen") or [])
            question = text(
                "backups.restore_question", name=b["name"], when=clock(b.get("created_at"))
            )
            if not self.window_.confirm(
                text("backups.restore_title"),
                f"{question}\n\n{steps}",
                text("backups.restore_confirm"),
            ):
                return
            self.say(text("backups.restoring"))
            self.ask(
                "POST",
                f"/backups/{b['id']}/restore",
                lambda r: self.restored(
                    text("backups.restored", name=r.get("restored") or b["name"]), r
                ),
                self.said,
                body={"confirm": True, "start_after": again},
                slow=True,
            )

        self.ask(
            "POST",
            f"/backups/{b['id']}/restore",
            ask_first,
            self.said,
            body={"confirm": False, "start_after": again},
        )

    # ------------------------------------------------------------ second copy
    def show_offsite(self, data: dict) -> None:
        self.offsite = data
        folder = data.get("directory")
        if not folder:
            words = text("offsite.off")
        elif data.get("unavailable"):
            words = text("offsite.folder_unavailable", folder=folder, reason=data["unavailable"])
        else:
            words = text("offsite.folder_ok", folder=folder)
        self.offsite_words.setText(words)
        self.offsite_folder.setText(data.get("subfolder") or "")
        self.offsite_folder.setVisible(mode["technical"] and bool(data.get("subfolder")))
        owner = self.window_.role in (None, "owner")  # pc.settings is the owner's alone
        self.choose.setText(text("offsite.change" if folder else "offsite.choose"))
        self.choose.setAccessibleName(f"{text('offsite.title')}: {self.choose.text()}")
        self.choose.setVisible(owner and self.here)
        self.turn_off.setVisible(owner and bool(folder))
        self.offsite_elsewhere.setText(
            text("desktop.backups.offsite_agent_only", pc=self.window_.agent.place)
            if self.window_.agent_only
            else text("desktop.backups.offsite_elsewhere")
        )
        self.offsite_elsewhere.setVisible(owner and not self.here)

    def choose_offsite(self) -> None:
        folder = self.window_.pc.pick_folder(text("offsite.pick_title"))
        if folder:
            self.set_offsite(folder)

    def set_offsite(self, folder: str) -> None:
        def done(_r) -> None:
            self.say(text("offsite.saved" if folder else "offsite.turned_off"))
            self.ask("GET", "/backups/offsite", self.show_offsite)
            self.load_list()

        self.ask("PUT", "/backups/offsite", done, self.said, body={"directory": folder})

    # ------------------------------------------------------------ bringing a world in
    def pick_zip(self) -> None:
        kinds = f"{text('desktop.backups.worlds_kind')} ({' '.join('*' + s for s in WORLD_FILES)})"
        path = self.window_.pc.pick_file(text("import.title"), kinds)
        if path:
            self.bring_in(path)

    def pick_folder(self) -> None:
        path = self.window_.pc.pick_folder(text("import.pick_title"))
        if path:
            self.bring_in(path)

    def take(self, paths: list[str]) -> None:
        """A world dropped on the window."""
        self.bring_in(paths[0])

    def bring_in(self, path: str) -> None:
        """A world zip or folder, from a picker, single-player's saves or a
        drop: read it first, then ask before it replaces anything."""
        if self.running:
            self.say(text("import.stop_first"))
            return
        sid, agent, place = self.row["id"], self.window_.agent, Path(path)
        if place.is_dir() and not is_world(place):
            # Before zipping a big folder that the agent would only refuse.
            self.say(text("desktop.backups.not_a_world"))
            return
        self.say(text("import.reading"))
        if place.is_dir() and self.here:
            # The agent reads the folder itself: nothing is copied.
            self.ask(
                "POST",
                "/world/import/folder",
                self.confirm_import,
                self.said,
                body={"path": path},
                slow=True,
            )
            return

        def send() -> dict:
            if not place.is_dir():
                return agent.upload(f"/servers/{sid}/world/import/upload", path)
            folder = Path(tempfile.mkdtemp(prefix="command-block-"))
            try:
                return agent.upload(
                    f"/servers/{sid}/world/import/upload", str(zipped(place, folder))
                )
            finally:
                shutil.rmtree(folder, ignore_errors=True)

        self.run_here(send, self.confirm_import)

    def confirm_import(self, read: dict) -> None:
        world, token = read["world"], read["token"]
        name = world.get("name") or world["folder_name"]
        unknown = text("state.unknown")
        lines = [
            f"{text('import.name')}: {name}",
            f"{text('import.version')}: {world.get('version') or unknown}",
            f"{text('import.size')}: "
            + text("import.size_value", size=size(world.get("size_bytes")), files=world["files"]),
            f"{text('import.last_played')}: {clock(world.get('last_played')) or unknown}",
        ]
        if world.get("read_problem"):
            lines.append(text("import.read_problem", problem=world["read_problem"]))
        if read.get("edition_matches") is False:
            lines.append(
                text("import.is_bedrock" if world.get("edition") == "bedrock" else "import.is_java")
            )
        if read.get("newer_than_server") is True:
            lines.append(
                text("import.newer", world=world.get("version"), server=read.get("server_version"))
            )
        if read.get("level_name"):
            lines.append(text("import.bedrock_level", name=read["level_name"]))
        lines.append(text("import.confirm_body"))
        self.say("")
        if not self.window_.confirm(
            text("import.confirm_title", name=name),
            "\n\n".join(lines),
            text("import.confirm_action"),
        ):
            self.ask("DELETE", f"/world/import/{token}", lambda _r: None, lambda _e: None)
            return
        self.say(text("import.working"))
        self.ask(
            "POST",
            "/world/import",
            lambda r: self.restored(text("desktop.backups.import_done"), r),
            self.said,
            body={"token": token, "confirm": True},
            slow=True,
        )

    # ------------------------------------------------------------ taking it home
    def download_world(self) -> None:
        self.take_home.setEnabled(False)
        self.say(text("download.packing"))
        sid, agent = self.row["id"], self.window_.agent

        def packed(result: dict) -> None:
            path = self.window_.pc.save_path(result["filename"])
            if not path:
                self.say("")
                self.take_home.setEnabled(True)
                return
            route = f"/servers/{sid}/world/download/{result['token']}"
            self.run_here(
                lambda: agent.download_to(f"{route}?filename={quote(result['filename'])}", path),
                lambda _r: self.say(text("desktop.saved", path=path)),
                lambda: self.take_home.setEnabled(True),
            )

        def failed(exc: Exception) -> None:
            self.said(exc)
            self.take_home.setEnabled(True)

        self.ask("POST", "/world/download", packed, failed, slow=True)

    # ------------------------------------------------------------ left out of backups
    def load_size(self) -> None:
        self.measure.setEnabled(False)
        self.leftout_size.setText(text("leftout.measuring"))
        self.ask(
            "GET",
            "/backups/size",
            self.show_size,
            self.said,
            always=lambda: self.measure.setEnabled(True),
        )

    def show_size(self, data: dict) -> None:
        self.size_data = data
        self.leftout_size.setText(
            text(
                "leftout.size",
                size=size(data.get("total_bytes")),
                free=size(data.get("free_bytes")),
            )
        )
        rows: list[QWidget] = []
        for item in data.get("excluded") or []:
            rows.append(self.leftout_row(item["path"], size(item.get("size_bytes")), False))
        for tip in data.get("suggested") or []:
            words = text(
                "leftout.suggest", name=tip["name"], path=tip["path"], size=size(tip["size_bytes"])
            )
            rows.append(self.leftout_row(tip["path"], words, True))
        if not data.get("excluded"):
            rows.insert(0, label(text("leftout.none"), "sub"))
        refill(self.leftout_rows, rows)

    def leftout_row(self, path: str, words: str, add: bool) -> QWidget:
        line = QWidget()
        h = QHBoxLayout(line)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(label(words if add else f"{path}  ·  {words}"), 1)
        button = push(text("leftout.leave_out" if add else "leftout.put_back"), "plain")
        button.setAccessibleName(f"{button.text()}: {path}")
        button.clicked.connect(
            lambda: (
                self.leave_out(path)
                if add
                else self.save_excludes(
                    [e["path"] for e in self.size_data.get("excluded") or [] if e["path"] != path]
                )
            )
        )
        h.addWidget(button)
        return line

    def leave_out(self, path: str) -> None:
        path = path.strip()
        if not path:
            return
        now = [e["path"] for e in self.size_data.get("excluded") or []]
        self.save_excludes([*now, path])

    def save_excludes(self, paths: list[str]) -> None:
        def done(result: dict) -> None:
            refused = (result.get("rejected") or {}).get("backups.exclude")
            if refused:
                self.say(refused)
                return
            self.leftout_path.clear()
            self.say(text("leftout.saved"))
            self.load_size()

        self.ask("PUT", "/settings", done, self.said, body={"updates": {"backups.exclude": paths}})

    # ------------------------------------------------------------ clean-up
    def load_cleanup(self) -> None:
        self.find_button.setEnabled(False)
        self.ask(
            "GET",
            "/cleanup",
            self.show_cleanup,
            self.said,
            always=lambda: self.find_button.setEnabled(True),
        )

    def show_cleanup(self, data: dict) -> None:
        items = data.get("items")
        self.picks = {}
        rows: list[QWidget] = []
        for item in items or []:
            words = [text(f"cleanup.kind.{item['kind']}"), size(item.get("size_bytes"))]
            words.insert(1 if mode["technical"] else 2, item["path"])
            if item.get("modified"):
                words.append(clock(item["modified"]))
            box = QCheckBox("  ·  ".join(words))
            # Logs and crash reports are ticked; the bigger things are the owner's pick.
            box.setChecked(item["kind"] in ("old_log", "crash_report"))
            self.picks[item["path"]] = box
            rows.append(box)
        if items is not None and not items:
            rows.append(label(text("cleanup.nothing"), "sub"))
        refill(self.cleanup_rows, rows)
        note = "" if data.get("dimensions_checked", True) else text("cleanup.dimensions_unknown")
        self.cleanup_note.setText(note)
        self.cleanup_note.setVisible(bool(note))
        self.to_bin.setVisible(bool(items))
        self.bin = data.get("bin") or []
        if self.bin:
            when = time.strftime("%Y-%m-%d", time.localtime(min(b["empties_at"] for b in self.bin)))
            total = sum(b.get("size_bytes") or 0 for b in self.bin)
            self.bin_words.setText(text("cleanup.bin", size=size(total), when=when))
        for widget in (self.bin_words, self.undo_last, self.empty_bin):
            widget.setVisible(bool(self.bin))

    def clean_up(self) -> None:
        paths = [path for path, box in self.picks.items() if box.isChecked()]
        if not paths:
            return
        self.to_bin.setEnabled(False)

        def done(result: dict) -> None:
            words = text(
                "cleanup.done",
                count=len(result.get("moved") or []),
                size=size(result.get("size_bytes")),
                days=result.get("bin_days"),
            )
            if result.get("skipped"):
                words += " " + text("cleanup.skipped", count=len(result["skipped"]))
            batch = result.get("batch")
            self.say(words, (lambda: self.cleanup_undo(batch)) if batch else None)
            self.load_cleanup()

        self.ask(
            "POST",
            "/cleanup",
            done,
            self.said,
            body={"paths": paths},
            always=lambda: self.to_bin.setEnabled(True),
        )

    def cleanup_undo(self, batch: str) -> None:
        def done(result: dict) -> None:
            left = result.get("left_in_bin") or []
            self.say(text("cleanup.left", count=len(left)) if left else text("cleanup.undone"))
            self.load_cleanup()

        self.ask("POST", "/cleanup/undo", done, self.said, body={"batch": batch})

    def cleanup_empty(self) -> None:
        if not self.window_.confirm(
            text("cleanup.empty_title"), text("cleanup.empty_body"), text("cleanup.empty")
        ):
            return

        def done(result: dict) -> None:
            self.say(text("cleanup.emptied", size=size(result.get("freed_bytes"))))
            self.load_cleanup()

        self.ask("POST", "/cleanup/empty", done, self.said, body={})
