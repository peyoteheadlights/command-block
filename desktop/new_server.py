"""The "+" tab, with three ways in:

- New server: which Minecraft, which kind (the comparison table, built from
  what each type says about itself), which version, its name, folder,
  color and memory, then Minecraft's rules. Nothing is downloaded before
  the box is ticked.
- From a modpack: the pack is read first (the same summary as importing
  one into a server), then a new server is made from it.
- A server already on this PC: put on the list; nothing in its folder changes.

A new server opens once it's made. It is never started by itself."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .mods import pack_lines
from .overview import card
from .ui import field, label, push, refill, swatches
from .words import mode, text

if TYPE_CHECKING:
    from .window import MainWindow

WAYS = ["add.way_new", "add.way_modpack", "add.way_have"]


def known(value: Any) -> bool:
    return value not in (None, "")


class NewServerScreen(QScrollArea):
    NAME = "New server"

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        self.options: dict = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(12)
        column.addWidget(label(text("page.add_server"), name="title"))
        ways = QHBoxLayout()
        ways.setSpacing(0)
        self.way_group = QButtonGroup(self)
        self.panels = QStackedWidget()
        for i, words in enumerate(WAYS):
            button = push(text(words), "segment")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, n=i: self.panels.setCurrentIndex(n))
            self.way_group.addButton(button, i)
            ways.addWidget(button)
        ways.addStretch()
        column.addLayout(ways)
        self.said = label(kind="problem")
        self.said.hide()
        column.addWidget(self.said)
        self.new_panel = QWidget()
        self.steps = QVBoxLayout(self.new_panel)
        self.steps.setContentsMargins(0, 0, 0, 0)
        self.steps.setSpacing(12)
        self.pack_panel = QWidget()
        self.pack_column = QVBoxLayout(self.pack_panel)
        self.pack_column.setContentsMargins(0, 0, 0, 0)
        self.have_panel = QWidget()
        self.have_column = QVBoxLayout(self.have_panel)
        self.have_column.setContentsMargins(0, 0, 0, 0)
        for panel in (self.new_panel, self.pack_panel, self.have_panel):
            self.panels.addWidget(panel)
        column.addWidget(self.panels)
        column.addStretch()
        self.setWidget(body)

    def load(self) -> None:
        """Each time the tab opens: a fresh start, with the PC's choices."""
        self.said.hide()
        self.way_group.button(0).setChecked(True)
        self.panels.setCurrentIndex(0)
        for layout in (self.steps, self.pack_column, self.have_column):
            refill(layout, [])
        self.window_.run(
            lambda: self.window_.agent.call("GET", "/new-server/options"), self.show, self.failed
        )

    def failed(self, exc: Exception) -> None:
        self.said.setText(str(exc))
        self.said.show()

    def show(self, options: dict) -> None:  # type: ignore[override]
        self.options = options
        self.choice: dict = {
            "type": None,
            "minecraft": None,
            "loader": None,
            "color": options["color"],
        }
        refill(self.steps, [self.edition_card(), self.kinds_card()])
        self.version_slot = QVBoxLayout()
        self.details_slot = QVBoxLayout()
        self.steps.addLayout(self.version_slot)
        self.steps.addLayout(self.details_slot)
        refill(self.pack_column, [self.pack_card()])
        refill(self.have_column, [self.have_card()])

    # ------------------------------------------------------------ helpers
    def folder_field(self, words: str, hint: str, placeholder: str) -> tuple[QWidget, QLineEdit]:
        """A folder box, with Windows' own picker when the agent is on this
        PC (a moderator's PC would show the wrong PC's folders)."""
        box = QLineEdit()
        box.setMaxLength(400)
        box.setPlaceholderText(placeholder)
        if self.window_.agent.place != "this PC":
            return field(words, box, hint), box
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(box, 1)
        browse = push(text("desktop.browse"))

        def pick() -> None:
            found = self.window_.pc.pick_folder(words)
            if found:
                box.setText(found)

        browse.clicked.connect(pick)
        h.addWidget(browse)
        made = field(words, row, hint)
        box.setAccessibleName(words)
        return made, box

    def colors(self) -> QWidget:
        return swatches(
            self.options["palette"],
            self.choice["color"],
            lambda c: self.choice.__setitem__("color", c),
        )

    def java_link(self) -> QWidget:
        link = push(text("new.java_get"), "plain")
        link.clicked.connect(
            lambda: self.window_.pc.open_link(self.options.get("java_download") or "")
        )
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(link)
        h.addStretch()
        return row

    def created(self, result: dict, words: str) -> None:
        notes = [words]
        if result.get("verified") is False:
            notes.append(text("new.unverified"))
        self.window_.open_new(result["server_id"], " ".join(notes))

    # ------------------------------------------------------------ new server
    def edition_card(self) -> QFrame:
        frame, layout = card(text("new.step_edition"))
        self.java = QRadioButton(f"{text('new.java')}: {text('new.java_hint')}")
        bedrock = QRadioButton(f"{text('new.bedrock')}: {text('new.bedrock_hint')}")
        self.java.setChecked(True)
        layout.addWidget(self.java)
        layout.addWidget(bedrock)
        self.java.toggled.connect(self.edition_changed)
        return frame

    def edition_changed(self, java: bool) -> None:
        self.kinds.setVisible(java)
        refill(self.version_slot, [])
        refill(self.details_slot, [])
        self.choice["type"] = None
        if not java:
            found = next((t for t in self.options["types"] if t.get("edition") == "bedrock"), None)
            if found:
                self.choose(found)

    def kinds_card(self) -> QFrame:
        """The comparison table of the Java kinds: it describes, and never
        claims a number nobody measured."""
        self.kinds, layout = card(text("new.step_kind"))
        layout.addWidget(label(text("new.kind_hint"), "sub"))
        recommended = self.options["recommended"]
        columns = [
            (
                "new.col_kind",
                lambda t: (
                    t["name"] + (f"  ({text('new.recommended')})" if t["id"] == recommended else "")
                ),
            ),
            ("new.col_best_for", lambda t: text(f"types.{t['id']}.best")),
            (
                "new.col_content",
                lambda t: (
                    text(
                        "new.content_plugins"
                        if t.get("content") == "plugins"
                        else "new.content_mods"
                    )
                    if t.get("content")
                    else text("new.content_none")
                ),
            ),
            ("new.col_modrinth", lambda t: text("value.yes" if t.get("modrinth") else "value.no")),
            (
                "new.col_crossplay",
                lambda t: text("value.yes" if t.get("crossplay") else "value.no"),
            ),
            ("new.col_ease", lambda t: text(f"new.ease_{t['ease']}")),
        ]
        if mode["technical"]:
            columns += [
                ("new.col_loader", lambda t: t.get("loader_name") or text("new.content_none")),
                ("new.col_metadata", lambda t: ", ".join(t.get("metadata_files") or []) or "-"),
                ("new.col_source", lambda t: t.get("version_source") or "-"),
            ]
        table = QGridLayout()
        table.setHorizontalSpacing(14)
        table.setVerticalSpacing(8)
        for col, (words, _cell) in enumerate(columns):
            table.addWidget(label(text(words), "muted"), 0, col)
        self.choose_buttons: dict[str, QPushButton] = {}
        java = [t for t in self.options["types"] if t.get("edition") != "bedrock"]
        for row, kind in enumerate(java, start=1):
            for col, (_words, cell) in enumerate(columns):
                table.addWidget(label(cell(kind), "step" if col == 0 else ""), row, col)
            button = push(text("new.choose"))
            button.setAccessibleName(f"{text('new.choose')} {kind['name']}")
            button.clicked.connect(lambda _=False, k=kind: self.choose(k))
            self.choose_buttons[kind["id"]] = button
            table.addWidget(button, row, len(columns))
        for col in range(1, len(columns)):
            table.setColumnStretch(col, 1)
        layout.addLayout(table)
        name = next((t["name"] for t in self.options["types"] if t["id"] == recommended), "")
        layout.addWidget(label(text("new.rec_why", name=name), "sub"))
        return self.kinds

    def choose(self, kind: dict) -> None:
        self.choice.update(type=kind, minecraft=None, loader=None)
        for kind_id, button in self.choose_buttons.items():
            picked = kind_id == kind["id"]
            button.setText(text("new.chosen" if picked else "new.choose"))
            button.setProperty("kind", "primary" if picked else "")
            button.setStyle(button.style())
        refill(self.version_slot, [label(text("status.loading"), "sub")])
        refill(self.details_slot, [])
        window = self.window_
        window.run(
            lambda: window.agent.call("GET", f"/server-types/{kind['id']}/versions"),
            lambda payload: self.show_versions(kind, payload),
            lambda exc: refill(self.version_slot, [label(str(exc), "problem")]),
        )

    def show_versions(self, kind: dict, payload: dict) -> None:
        if self.choice["type"] is not kind:
            return  # another kind was chosen meanwhile
        frame, layout = card(text("new.step_version"))
        if payload.get("latest_only"):
            layout.addWidget(label(text("new.bedrock_versions"), "sub"))
        if payload.get("source_problem"):
            layout.addWidget(label(payload["source_problem"], "problem"))
        versions, loaders = QComboBox(), QComboBox()
        versions.setObjectName("newVersion")
        snapshots = QCheckBox(text("new.show_snapshots"))
        java_note = label(kind="problem")
        java_note.hide()
        layout.addWidget(
            field(
                text("new.version"),
                versions,
                text("new.version_hint", source=payload.get("type_name") or ""),
            )
        )
        if payload.get("snapshots"):
            layout.addWidget(snapshots)
        loader_box = field(
            text("new.loader_version", loader=payload.get("loader_name") or ""),
            loaders,
            text("new.loader_hint"),
        )
        loader_box.setVisible(bool(payload.get("picks_loader")) and mode["technical"])
        layout.addWidget(loader_box)
        layout.addWidget(java_note)
        get_java = self.java_link()
        get_java.hide()
        layout.addWidget(get_java)
        listed = payload.get("versions") or []

        def picked() -> None:
            entry: dict = next((v for v in listed if v["minecraft"] == versions.currentData()), {})
            loaders.clear()
            for loader in entry.get("loaders") or []:
                loaders.addItem(loader, loader)
            self.choice.update(minecraft=versions.currentData(), loader=loaders.currentData())
            have = (self.options.get("java") or {}).get("version_major")
            need = entry.get("java_required")
            old = have is not None and need is not None and have < need
            java_note.setVisible(bool(old))
            get_java.setVisible(bool(old))
            if old:
                java_note.setText(
                    text("new.java_old", version=entry["minecraft"], need=need, have=have)
                )

        def fill() -> None:
            versions.clear()
            for v in listed:
                if v["stable"] or snapshots.isChecked():
                    words = (
                        v["minecraft"]
                        if v["stable"]
                        else text("new.snapshot_option", version=v["minecraft"])
                    )
                    versions.addItem(words, v["minecraft"])
            versions.setCurrentIndex(max(0, versions.findData(payload.get("latest_stable"))))
            picked()

        versions.currentIndexChanged.connect(lambda _i: picked())
        loaders.currentIndexChanged.connect(
            lambda _i: self.choice.update(loader=loaders.currentData())
        )
        snapshots.toggled.connect(lambda _on: fill())
        fill()
        refill(self.version_slot, [frame])
        refill(self.details_slot, [self.details_card(kind), self.rules_card(kind)])

    def details_card(self, kind: dict) -> QFrame:
        frame, layout = card(text("new.step_details"))
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.name.setObjectName("newName")
        folder, self.folder = self.folder_field(
            text("new.folder"), text("new.folder_hint"), "C:\\Minecraft\\Survival"
        )
        row = QHBoxLayout()
        row.addWidget(field(text("add.name"), self.name), 1)
        row.addWidget(folder, 1)
        layout.addLayout(row)
        layout.addWidget(label(text("serverset.color"), "sub"))
        layout.addWidget(self.colors())
        self.memory: QSpinBox | None = None
        if kind.get("edition") == "bedrock":
            port = (self.options.get("bedrock") or {}).get("port")
            layout.addWidget(
                label(
                    text("new.bedrock_port_hint", port=port)
                    if known(port)
                    else text("new.bedrock_no_port"),
                    "sub",
                )
            )
        else:
            self.memory = QSpinBox()
            self.memory.setRange(1024, self.options.get("total_mb") or 65536)
            self.memory.setSingleStep(512)
            self.memory.setSuffix(" MB")
            self.memory.setValue(self.options.get("memory_mb") or 2048)
            layout.addWidget(
                field(text("new.memory"), self.memory, self.options.get("memory_reason") or "")
            )
            layout.addWidget(label(text("new.port_hint", port=self.options.get("port")), "muted"))
        return frame

    def rules_card(self, kind: dict) -> QFrame:
        """Minecraft's rules (and for Bedrock, Mojang's Privacy Policy too)."""
        bedrock = kind.get("edition") == "bedrock"
        frame, layout = card(text("new.step_rules"))
        layout.addWidget(
            label(text("new.bedrock_terms_intro" if bedrock else "new.eula_intro"), "sub")
        )
        links = QHBoxLayout()
        terms = self.options.get("bedrock") or {}
        pages = [
            ("new.eula_link", terms.get("eula_url") if bedrock else self.options.get("eula_url"))
        ]
        if bedrock:
            pages.append(("new.privacy_link", terms.get("privacy_url")))
        for words, url in pages:
            link = push(text(words), "plain")
            link.clicked.connect(lambda _=False, u=url: self.window_.pc.open_link(u or ""))
            links.addWidget(link)
        links.addStretch()
        layout.addLayout(links)
        self.eula = QCheckBox(text("new.bedrock_tick" if bedrock else "new.eula_tick"))
        self.eula.setObjectName("newEula")
        layout.addWidget(self.eula)
        java = self.options.get("java") or {}
        if not bedrock and java and not known(java.get("version_major")):
            layout.addWidget(label(text("new.java_unknown"), "problem"))
            layout.addWidget(self.java_link())
        row = QHBoxLayout()
        create = push(text("new.create"), "primary")
        said = label(text("new.create_hint"), "sub")
        row.addWidget(create)
        row.addWidget(said, 1)
        layout.addLayout(row)

        def make() -> None:
            name, folder = self.name.text().strip(), self.folder.text().strip()
            if not self.choice["minecraft"]:
                said.setText(text("new.need_version"))
            elif not name or not folder:
                said.setText(text("add.need_name_and_folder"))
            elif not self.eula.isChecked():
                said.setText(text("new.need_bedrock_terms" if bedrock else "new.need_eula"))
            else:
                create.setEnabled(False)
                said.setText(text("new.creating"))
                body = {
                    "name": name,
                    "directory": folder,
                    "type": kind["id"],
                    "minecraft_version": self.choice["minecraft"],
                    "loader_version": self.choice["loader"],
                    "memory_mb": self.memory.value() if self.memory else None,
                    "color": self.choice["color"],
                    "eula_accepted": True,
                }
                window = self.window_
                window.run(
                    lambda: window.agent.call("POST", "/new-server", body, slow=True),
                    lambda result: self.created(result, text("new.created", name=result["name"])),
                    lambda exc: said.setText(str(exc)),
                    lambda: create.setEnabled(True),
                )

        create.clicked.connect(make)
        return frame

    # ------------------------------------------------------------ from a modpack
    def pack_card(self) -> QFrame:
        frame, layout = card(text("pack.new_title"))
        layout.addWidget(label(text("pack.new_intro"), "sub"))
        choose = push(text("pack.choose"), "primary")
        row = QHBoxLayout()
        row.addWidget(choose)
        row.addStretch()
        layout.addLayout(row)
        self.pack_slot = QVBoxLayout()
        layout.addLayout(self.pack_slot)
        choose.clicked.connect(self.pick_pack)
        return frame

    def take(self, paths: list[str]) -> None:
        """A modpack dropped on the "+" tab."""
        self.way_group.button(1).setChecked(True)
        self.panels.setCurrentIndex(1)
        self.read_pack(paths[0])

    def pick_pack(self) -> None:
        path = self.window_.pc.pick_file(
            text("pack.new_title"), f"{text('pack.choose')} (*.mrpack)"
        )
        if path:
            self.read_pack(path)

    def read_pack(self, path: str) -> None:
        refill(self.pack_slot, [label(text("pack.reading"), "sub")])
        window = self.window_
        window.run(
            lambda: window.agent.upload("/modpacks/inspect", path),
            self.show_pack,
            lambda exc: refill(self.pack_slot, [label(str(exc), "problem")]),
        )

    def show_pack(self, read: dict) -> None:
        pack, token, window = read["pack"], read["token"], self.window_
        shown = [label(line, "sub") for line in pack_lines(pack)]
        if not pack.get("can_import"):
            window.run(
                lambda: window.agent.call("DELETE", f"/modpacks/{token}"),
                lambda _r: None,
                lambda _e: None,
            )
            refill(self.pack_slot, shown)
            return
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit(pack["name"][:60])
        name.setMaxLength(60)
        name.setObjectName("packName")
        folder, folder_box = self.folder_field(
            text("pack.folder"), text("pack.folder_hint"), "C:\\Minecraft\\Modpack"
        )
        row = QHBoxLayout()
        row.addWidget(field(text("add.name"), name), 1)
        row.addWidget(folder, 1)
        layout.addLayout(row)
        eula = QCheckBox(text("pack.eula"))
        eula.setObjectName("packEula")
        read_them = push(text("pack.eula_link"), "plain")
        read_them.clicked.connect(lambda: window.pc.open_link(self.options.get("eula_url") or ""))
        tick = QHBoxLayout()
        tick.addWidget(eula)
        tick.addWidget(read_them)
        tick.addStretch()
        layout.addLayout(tick)
        create = push(text("pack.create"), "primary")
        said = label(text("pack.create_hint"), "sub")
        buttons = QHBoxLayout()
        buttons.addWidget(create)
        buttons.addWidget(said, 1)
        layout.addLayout(buttons)

        def make() -> None:
            if not name.text().strip() or not folder_box.text().strip():
                said.setText(text("add.need_name_and_folder"))
                return
            if not eula.isChecked():
                said.setText(text("pack.need_eula"))
                return
            create.setEnabled(False)
            said.setText(text("pack.creating"))
            body = {
                "name": name.text().strip(),
                "directory": folder_box.text().strip(),
                "eula_accepted": True,
            }
            window.run(
                lambda: window.agent.call("POST", f"/modpacks/{token}/new-server", body, slow=True),
                lambda result: self.created(result, text("pack.created", name=result["name"])),
                lambda exc: said.setText(str(exc)),
                lambda: create.setEnabled(True),
            )

        create.clicked.connect(make)
        refill(self.pack_slot, [*shown, form])

    # ------------------------------------------------------------ already on this PC
    def have_card(self) -> QFrame:
        frame, layout = card(text("add.existing"))
        layout.addWidget(label(text("add.existing_hint"), "sub"))
        name = QLineEdit()
        name.setMaxLength(60)
        name.setObjectName("haveName")
        folder, folder_box = self.folder_field(text("add.folder"), "", "C:\\Minecraft\\Creative")
        row = QHBoxLayout()
        row.addWidget(field(text("add.name"), name), 1)
        row.addWidget(folder, 1)
        layout.addLayout(row)
        kinds = QComboBox()
        for kind in self.options["types"]:
            kinds.addItem(kind["name"], kind["id"])
        kinds.setCurrentIndex(max(0, kinds.findData("fabric")))
        layout.addWidget(field(text("add.kind"), kinds, text("add.kind_hint")))
        layout.addWidget(label(text("serverset.color"), "sub"))
        layout.addWidget(self.colors())
        jar = QLineEdit()
        jar.setMaxLength(180)
        jar.setPlaceholderText("fabric-server-launch.jar")
        if mode["technical"]:
            layout.addWidget(field(text("add.jar"), jar, text("add.jar_hint")))
        buttons = QHBoxLayout()
        add = push(text("add.submit"), "primary")
        said = label(kind="sub")
        buttons.addWidget(add)
        buttons.addWidget(said, 1)
        layout.addLayout(buttons)

        def add_it() -> None:
            if not name.text().strip() or not folder_box.text().strip():
                said.setText(text("add.need_name_and_folder"))
                return
            add.setEnabled(False)
            said.setText(text("add.adding"))
            body = {
                "name": name.text().strip(),
                "directory": folder_box.text().strip(),
                "jar": jar.text().strip(),
                "type": kinds.currentData(),
                "color": self.choice["color"],
            }
            window = self.window_

            def done(result: dict) -> None:
                words = [
                    text("add.added", name=result["server"]["name"]),
                    *(result.get("warnings") or []),
                ]
                window.open_new(result["server"]["id"], " ".join(words))

            window.run(
                lambda: window.agent.call("POST", "/servers", body),
                done,
                lambda exc: said.setText(str(exc)),
                lambda: add.setEnabled(True),
            )

        add.clicked.connect(add_it)
        return frame
