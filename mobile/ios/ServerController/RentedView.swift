import Core
import Observation
import SwiftUI

/// Rented servers on a host's Pterodactyl panel, like the desktop app
/// (desktop/rented.py): the list on All servers, one server's screen and
/// Connect a rented server (owner only). The agent holds the key and talks
/// to the panel; the phone only calls /api/rented. Mirrors Rented.kt.
@MainActor
@Observable
final class RentedPages {
    @ObservationIgnored private let client: () -> AgentClient?
    @ObservationIgnored private let noted: (AgentError) -> Void
    @ObservationIgnored private let strings: Strings

    init(client: @escaping () -> AgentClient?, noted: @escaping (AgentError) -> Void, strings: Strings) {
        self.client = client
        self.noted = noted
        self.strings = strings
    }

    private(set) var list = PageRead<RentedList>()
    private(set) var busy: String? = nil
    private(set) var said: Said? = nil
    // The open server's console and backups.
    private(set) var console: [RentedLine] = []
    private(set) var consoleProblem: String? = nil
    private(set) var backups = PageRead<RentedBackups>()
    @ObservationIgnored private var openId: String? = nil
    @ObservationIgnored private var lastLine = 0
    /// What "Find its servers" found, before anything is saved.
    private(set) var found: PanelCheck? = nil

    func server(_ id: String) -> RentedServer? { list.value?.servers.first(where: { $0.id == id }) }

    func load() async {
        guard let client = client() else { return }
        list.loading = true
        do {
            list = PageRead(value: try await client.rented())
        } catch let problem as AgentError {
            noted(problem)
            list.problem = problem
            list.loading = false
        } catch {
            list.loading = false
        }
    }

    /// A fresh console and backups list when another server opens.
    func open(_ id: String) async {
        if id != openId {
            openId = id
            console = []
            consoleProblem = nil
            lastLine = 0
            backups = PageRead()
            said = nil
        }
        await loadBackups(id)
    }

    /// The agent asks the panel every 20 s; this reads its answer.
    func refresh(_ id: String) async {
        guard let client = client() else { return }
        do {
            replace(try await client.rentedStatus(id))
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    private func replace(_ row: RentedServer) {
        guard var current = list.value else { return }
        current.servers = current.servers.map { $0.id == row.id ? row : $0 }
        list.value = current
    }

    func readConsole(_ id: String) async {
        guard let client = client() else { return }
        do {
            let got = try await client.rentedConsole(id, after: lastLine)
            guard id == openId else { return }
            console = Array((console + got.lines).suffix(Rented.CONSOLE_LINES))
            lastLine = got.last
            consoleProblem = got.problem
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    func loadBackups(_ id: String) async {
        guard let client = client() else { return }
        backups.loading = true
        do {
            backups = PageRead(value: try await client.rentedBackups(id))
        } catch let problem as AgentError {
            noted(problem)
            backups.problem = problem
            backups.loading = false
        } catch {
            backups.loading = false
        }
    }

    func power(_ id: String, _ signal: String, confirm: Bool = false) async {
        await work(signal) { client in
            let done = try await client.rentedPower(id, signal: signal, confirm: confirm)
            await self.refresh(id)
            return done.detail
        }
    }

    /// True once it was sent, to empty the box.
    func send(_ id: String, _ command: String) async -> Bool {
        var sent = false
        await work("command") { client in
            let done = try await client.rentedCommand(id, command: command)
            sent = true
            return done.detail
        }
        return sent
    }

    func makeBackup(_ id: String) async {
        await work("backup") { client in
            let done = try await client.makeRentedBackup(id)
            await self.loadBackups(id)
            return done.detail
        }
    }

    func discord(_ id: String, _ choice: String) async {
        await work("discord") { client in
            self.replace(try await client.rentedDiscord(id, choice: choice))
            return self.strings.t("settings.saved")
        }
    }

    /// True once the key is gone, to leave the screen.
    func removeKey(_ panelId: String) async -> Bool {
        var removed = false
        await work("remove") { client in
            _ = try await client.removePanelKey(panelId)
            await self.load()
            removed = true
            return self.strings.t("mobile.rented.key_removed")
        }
        return removed
    }

    func forget() {
        found = nil
        said = nil
    }

    func check(_ address: String, _ key: String) async {
        await work("check") { client in
            self.found = try await client.checkPanel(address: address, key: key)
            return ""
        }
    }

    /// True once the servers are added, to leave the screen.
    func connect(_ address: String, _ key: String, _ picked: [String]) async -> Bool {
        var added = false
        await work("add") { client in
            let done = try await client.connectPanel(address: address, key: key, servers: picked)
            await self.load()
            self.found = nil
            added = true
            return self.strings.tn("mobile.rented.added", count: done.added.count)
        }
        return added
    }

    /// One button at a time; `call` gives the words to say once it worked.
    private func work(_ name: String, _ call: (AgentClient) async throws -> String) async {
        guard let client = client(), busy == nil else { return }
        busy = name
        said = nil
        do {
            let words = try await call(client)
            said = words.isEmpty ? nil : Said(text: words, good: true)
        } catch let problem as AgentError {
            noted(problem)
            said = Said(text: problem.message(strings), good: false)
        } catch {}
        busy = nil
    }
}

/// "Rented servers" on All servers: each with its host and state, and Connect for the owner.
struct RentedSection: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let rows = model.rented.list.value?.servers ?? []
        let owner = Rented.mayConnect(model.me)
        if !rows.isEmpty || owner {
            Section(model.t("desktop.rented.heading_list")) {
                ForEach(rows) { row in
                    NavigationLink(value: ServerRoute.rented(row.id)) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(hiding: row.name)
                            Text(hiding: Rented.stateLine(model.strings, row) + " · " + row.host)
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                if owner {
                    NavigationLink(model.t("desktop.rented.connect"), value: ServerRoute.connectRented)
                }
            }
        }
    }
}

/// One rented server: status, Start/Stop/Restart/Kill, console, the host's backups.
struct RentedView: View {
    let id: String

    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.dismiss) private var dismiss
    @State private var killing = false
    @State private var removing = false
    @State private var command = ""

    var body: some View {
        let pages = model.rented
        let palette = model.basePalette(colorScheme)
        let me = model.me?.forServer(id)
        let may = { (p: String) in me?.can(p) == true }
        let busy = pages.busy != nil
        Form {
            if let problem = pages.list.problem {
                Section {
                    Text(problem.message(model.strings)).foregroundStyle(.red)
                    Button(model.t("mobile.common.retry")) { Task { await pages.load() } }
                }
            }
            if let row = pages.server(id) {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(hiding: model.t("desktop.rented.heading", ["name": row.name, "host": row.host]))
                        Hint(text: Rented.stateLine(model.strings, row), palette: palette)
                        Hint(text: Rented.usage(model.strings, row.resources), palette: palette)
                    }
                    if may(Permissions.SERVER_CONTROL) {
                        HStack {
                            ForEach(Rented.POWER, id: \.self) { action in
                                Button(model.t("action.\(action)")) { Task { await pages.power(id, action) } }
                                    .disabled(busy || !Rented.enabled(row.state, action))
                            }
                            Button(model.t("desktop.rented.kill"), role: .destructive) { killing = true }
                                .disabled(busy || !Rented.enabled(row.state, "kill"))
                        }
                        .buttonStyle(.bordered)
                    }
                    SaidRow(said: pages.said, palette: palette)
                }
                if may(Permissions.CONSOLE_READ) {
                    Section(model.t("desktop.rented.console")) {
                        if let problem = pages.consoleProblem {
                            Text(problem).foregroundStyle(palette.danger)
                        }
                        if pages.console.isEmpty {
                            Text(model.t("mobile.common.loading")).foregroundStyle(.secondary)
                        }
                        ForEach(pages.console.suffix(200)) { line in
                            Text(hiding: line.text).font(.footnote.monospaced())
                        }
                        if may(Permissions.CONSOLE_SEND) {
                            TextField(model.t("desktop.rented.command"), text: $command)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .submitLabel(.send)
                                .onSubmit(sendCommand)
                            Button(model.t("desktop.rented.send"), action: sendCommand)
                                .disabled(busy || command.trimmingCharacters(in: .whitespaces).isEmpty)
                        }
                    }
                }
                Section(model.t("desktop.rented.backups")) {
                    if let problem = pages.backups.problem {
                        Text(problem.message(model.strings)).foregroundStyle(palette.danger)
                    } else if let b = pages.backups.value {
                        if b.backups.isEmpty { Text(model.t("desktop.rented.no_backups")).foregroundStyle(.secondary) }
                        ForEach(b.backups.indices, id: \.self) { i in
                            Text(hiding: Rented.backupLine(model.strings, b.backups[i]))
                        }
                        if let limit = b.limit { Hint(text: model.t("desktop.rented.backup_limit", ["n": limit]), palette: palette) }
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                    if may(Permissions.BACKUPS_CREATE) {
                        Button(model.t("desktop.rented.make_backup")) { Task { await pages.makeBackup(id) } }
                            .disabled(busy)
                    }
                }
                Section(model.t("desktop.rented.needs_pc")) {
                    ForEach(Rented.NEEDS_THIS_PC, id: \.self) { key in
                        Text(model.t(key)).foregroundStyle(.secondary)
                    }
                    Text(model.t("desktop.rented.later")).foregroundStyle(.secondary)
                }
                if Rented.mayConnect(model.me) {
                    Section {
                        Picker(model.t("desktop.rented.discord"), selection: Binding(
                            get: { row.discord },
                            set: { choice in Task { await pages.discord(id, choice) } }
                        )) {
                            ForEach(Rented.DISCORD, id: \.self) { Text(model.t("desktop.rented.discord.\($0)")).tag($0) }
                        }
                        .disabled(busy)
                        Button(model.t("desktop.rented.remove_key"), role: .destructive) { removing = true }
                            .disabled(busy)
                    }
                }
            } else if pages.list.loading {
                ProgressView().frame(maxWidth: .infinity)
            } else {
                Text(model.t("mobile.rented.gone")).foregroundStyle(.secondary)
            }
        }
        .navigationTitle(pages.server(id)?.name ?? model.t("desktop.rented.heading_list"))
        .navigationBarTitleDisplayMode(.inline)
        .refreshable {
            await pages.refresh(id)
            await pages.loadBackups(id)
        }
        // Only while this screen is open: console every 2 s, status every 10 s.
        .task(id: id) {
            if pages.list.value == nil { await pages.load() }
            await pages.open(id)
            var ticks = 0
            while !Task.isCancelled {
                if may(Permissions.CONSOLE_READ) { await pages.readConsole(id) }
                if ticks % 5 == 0 { await pages.refresh(id) }
                ticks += 1
                try? await Task.sleep(for: .seconds(2))
            }
        }
        .alert(model.t("desktop.rented.kill_title", ["name": pages.server(id)?.name ?? id]), isPresented: $killing) {
            Button(model.t("desktop.rented.kill"), role: .destructive) { Task { await pages.power(id, "kill", confirm: true) } }
            Button(model.t("mobile.common.cancel"), role: .cancel) {}
        } message: {
            Text(model.t("desktop.rented.kill_warning"))
        }
        .alert(model.t("desktop.rented.remove_key_title", ["host": pages.server(id)?.host ?? ""]), isPresented: $removing) {
            Button(model.t("desktop.rented.remove_key"), role: .destructive) {
                guard let panel = pages.server(id)?.panelId else { return }
                Task { if await pages.removeKey(panel) { dismiss() } }
            }
            Button(model.t("mobile.common.cancel"), role: .cancel) {}
        } message: {
            Text(model.t("desktop.rented.remove_key_words"))
        }
    }

    private func sendCommand() {
        let typed = command
        guard !typed.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        Task { if await model.rented.send(id, typed) { command = "" } }
    }
}

/// Connect a rented server: panel address and client API key, the owner's tick, then pick its servers.
struct ConnectPanelView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @State private var address = ""
    @State private var key = ""
    @State private var allow = false
    @State private var picked: Set<String> = []

    var body: some View {
        let pages = model.rented
        let palette = model.basePalette(colorScheme)
        let busy = pages.busy != nil
        Form {
            Section {
                Text(model.t("desktop.rented.connect_intro"))
                TextField(model.t("desktop.rented.address"), text: $address, prompt: Text("https://panel.example.com"))
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField(model.t("desktop.rented.key"), text: $key)
                Hint(text: model.t("desktop.rented.key_hint"), palette: palette)
                Toggle(model.t("desktop.rented.allow"), isOn: $allow)
                Button(model.t("desktop.rented.look")) {
                    picked = []
                    Task { await pages.check(address, key) }
                }
                .disabled(busy || !allow || address.isEmpty || key.isEmpty)
                SaidRow(said: pages.said, palette: palette)
            }
            if let found = pages.found {
                Section {
                    if found.fullAccount {
                        Text(model.t("desktop.rented.full_account")).foregroundStyle(palette.danger)
                    }
                    Text(model.t(found.servers.isEmpty ? "desktop.rented.none_on_panel" : "desktop.rented.pick"))
                    ForEach(found.servers) { server in
                        Toggle(server.name, isOn: Binding(
                            get: { picked.contains(server.identifier) },
                            set: { on in if on { picked.insert(server.identifier) } else { picked.remove(server.identifier) } }
                        ))
                    }
                    Button(model.t("desktop.rented.add")) {
                        let ids = found.servers.map(\.identifier).filter { picked.contains($0) }
                        Task { if await pages.connect(address, key, ids) { dismiss() } }
                    }
                    .disabled(busy || picked.isEmpty)
                }
            }
            Section {
                Text(model.t("desktop.rented.aternos_note")).foregroundStyle(.secondary)
                Button(model.t("desktop.rented.aternos")) {
                    if let url = URL(string: Rented.ATERNOS) { openURL(url) }
                }
            }
        }
        .navigationTitle(model.t("desktop.rented.connect"))
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { pages.forget() }
    }
}
