import Core
import SwiftUI

/// Every server this account may see: its color, what it's doing and who's
/// playing, with when the PC was last read. If the last read failed, the
/// names stay but what they were doing reads Unknown.
struct ServersListView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme
    @State private var search = ""
    @State private var stopping: [String]? = nil
    @State private var grouping: ServerRow? = nil
    @State private var groupName = ""

    var body: some View {
        @Bindable var model = model
        let grouped = ServerListing.grouped(model.servers, search: model.servers.count >= 2 ? search : "")
        let shownIds = grouped.flatMap { $0.rows.map(\.id) }
        NavigationStack(path: $model.serversPath) {
            List {
                Section {
                    statusLine
                }
                if model.serversLoaded && model.servers.isEmpty {
                    Section {
                        Text(model.me?.isOwner == false ? model.t("mobile.servers.helper_none") : model.t("mobile.servers.none"))
                            .foregroundStyle(.secondary)
                    }
                } else if !model.serversLoaded {
                    Section {
                        HStack(spacing: 12) {
                            ProgressView()
                            Text(model.t("mobile.common.loading"))
                        }
                        .frame(minHeight: 44)
                    }
                }
                if model.servers.count >= 2 {
                    Section {
                        HStack {
                            Button(model.t("servers.start_all")) {
                                Task { await model.allServers("start", ids: shownIds) }
                            }
                            .buttonStyle(.bordered)
                            Button(model.t("servers.stop_all")) { stopping = shownIds }
                                .buttonStyle(.bordered)
                        }
                        .disabled(shownIds.isEmpty)
                        ForEach(model.serversSaid, id: \.self) { said in
                            Text(said).font(.footnote).foregroundStyle(.secondary)
                        }
                        if !search.isEmpty && shownIds.isEmpty {
                            Text(model.t("servers.no_match", ["search": search])).foregroundStyle(.secondary)
                        }
                    }
                }
                ForEach(grouped.indices, id: \.self) { index in
                    let group = grouped[index]
                    Section {
                        ForEach(group.rows) { row in
                            NavigationLink(value: ServerRoute.server(row.id)) {
                                ServerRowView(row: row, fresh: model.serversFresh)
                            }
                            .contextMenu { arrangeMenu(row, shown: group.rows.map(\.id)) }
                        }
                    } header: {
                        if grouped.count > 1 || !group.name.isEmpty {
                            Text(group.name.isEmpty ? model.t("servers.no_group") : group.name)
                        }
                    } footer: {
                        if index == grouped.count - 1 {
                            Text(model.t("mobile.servers.more_on_pc"))
                        }
                    }
                }
            }
            .refreshable {
                await model.refreshServers()
            }
            .scrollContentBackground(.hidden)
            .background(model.basePalette(colorScheme).sheet)
            .binder(current: nil)
            .navigationTitle(model.t("mobile.servers.title"))
            .searchable(text: $search, prompt: model.t("servers.search"))
            .confirmationDialog(
                model.t("servers.stop_all_title", [
                    "n": model.servers.filter { (stopping ?? []).contains($0.id) && $0.state != States.OFFLINE }.count,
                ]),
                isPresented: Binding(get: { stopping != nil }, set: { if !$0 { stopping = nil } }),
                titleVisibility: .visible
            ) {
                Button(model.t("servers.stop_all"), role: .destructive) {
                    let ids = stopping ?? []
                    stopping = nil
                    Task { await model.allServers("stop", ids: ids) }
                }
            } message: {
                Text(model.t("servers.stop_all_body"))
            }
            .alert(
                model.t("servers.group_title"),
                isPresented: Binding(get: { grouping != nil }, set: { if !$0 { grouping = nil } })
            ) {
                TextField(model.t("servers.group_title"), text: $groupName)
                Button(model.t("action.save")) {
                    if let row = grouping {
                        let name = String(groupName.prefix(40))
                        Task { await model.groupServer(row.id, group: name) }
                    }
                    grouping = nil
                }
                Button(model.t("mobile.common.cancel"), role: .cancel) { grouping = nil }
            } message: {
                Text(model.t("servers.group_ask"))
            }
            .toolbar {
                if AddServer.allowed(model.me) {
                    ToolbarItem(placement: .primaryAction) {
                        NavigationLink(value: ServerRoute.add) {
                            Image(systemName: "plus")
                        }
                        .accessibilityLabel(model.t("mobile.add.title"))
                    }
                }
            }
            .navigationDestination(for: ServerRoute.self) { route in
                switch route {
                case .server(let id):
                    ServerView(serverId: id)
                case .page(let id, .console):
                    ConsoleView(serverId: id)
                case .page(let id, .chat):
                    ChatView(serverId: id)
                case .page(let id, .players):
                    PlayersView(serverId: id)
                case .page(let id, .performance):
                    PerformanceView(serverId: id)
                case .page(let id, .backups):
                    BackupsView(serverId: id)
                case .page(let id, .world):
                    WorldRestoreView(serverId: id)
                case .page(let id, .schedules):
                    SchedulesView(serverId: id)
                case .page(let id, .events):
                    EventsView(serverId: id)
                case .page(let id, .crashes):
                    CrashesView(serverId: id)
                case .crash(let id, let crashId):
                    CrashView(serverId: id, crashId: crashId)
                case .content(let id):
                    ContentView(serverId: id)
                case .game(let id):
                    GameSettingsView(serverId: id)
                case .settings(let id):
                    ServerSettingsView(serverId: id)
                case .quests(let id):
                    QuestsView(serverId: id)
                case .diagnose(let id):
                    DiagnoseView(serverId: id)
                case .add:
                    AddServerView()
                }
            }
        }
    }

    /// The owner's Move up, Move down and Put in a group (long press).
    @ViewBuilder
    private func arrangeMenu(_ row: ServerRow, shown: [String]) -> some View {
        if model.me?.isOwner != false {
            Button(model.t("servers.move_up")) {
                Task { await model.moveServer(row.id, shown: shown, up: true) }
            }
            Button(model.t("servers.move_down")) {
                Task { await model.moveServer(row.id, shown: shown, up: false) }
            }
            Button(model.t("servers.group")) {
                groupName = row.group ?? ""
                grouping = row
            }
        }
    }

    private var statusLine: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let problem = model.serversProblem {
                Label(problem.message(model.strings), systemImage: "exclamationmark.triangle")
                    .foregroundStyle(model.basePalette(colorScheme).tone(.warning))
            }
            if model.serversProblem == .unreachable || model.waking || model.wakeFailed {
                wake
            }
            TimelineView(.periodic(from: .now, by: 30)) { context in
                HStack(spacing: 8) {
                    if model.live {
                        Image(systemName: "dot.radiowaves.left.and.right")
                            .foregroundStyle(model.basePalette(colorScheme).tone(.success))
                            .accessibilityHidden(true)
                        Text(model.t("mobile.servers.live"))
                        Text(hiding: "·")
                            .accessibilityHidden(true)
                    }
                    Text(model.t("mobile.servers.checked", [
                        "when": Display.ago(model.strings, model.serversCheckedAt, now: context.date.timeIntervalSince1970),
                    ]))
                }
                .font(.footnote)
                .foregroundStyle(.secondary)
                .accessibilityElement(children: .combine)
            }
        }
    }

    /// "Wake <PC>" when the PC doesn't answer, for an iPhone allowed to wake
    /// it: asks the helping PC, or opens the router's wake page.
    @ViewBuilder
    private var wake: some View {
        if let kept = model.keptWake, kept.offered {
            let pc = kept.info.pc.isEmpty ? (model.pairing?.host ?? "") : kept.info.pc
            if model.waking {
                Label(model.t("desktop.wake.waking", ["pc": pc]), systemImage: "power")
                    .font(.footnote)
            } else {
                if model.wakeFailed {
                    Text(model.t("desktop.wake.failed", ["pc": pc]))
                        .font(.footnote)
                        .foregroundStyle(model.basePalette(colorScheme).tone(.danger))
                }
                Button(model.t("desktop.wake.button", ["pc": pc])) {
                    model.wakePC()
                }
                .buttonStyle(.borderedProminent)
                .frame(minHeight: 44)
            }
        }
    }
}

/// One server in the list.
struct ServerRowView: View {
    let row: ServerRow
    /// The last read worked; otherwise state and players read Unknown.
    let fresh: Bool

    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let state = fresh ? row.state : States.UNKNOWN
        let players: Int? = fresh ? row.playersOnline : nil
        let words = Display.state(state, answering: fresh ? row.answering?.ok : nil)
        HStack(spacing: 12) {
            ColorBadge(hex: row.color, size: 16)
            VStack(alignment: .leading, spacing: 4) {
                Text(row.name)
                    .font(.headline)
                HStack(spacing: 6) {
                    Text(model.t(words.key))
                        .foregroundStyle(model.basePalette(colorScheme).tone(words.tone))
                    Text(hiding: "·")
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                    Image(systemName: "person.2")
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                    Text(Display.players(model.strings, players, row.maxPlayers))
                        .foregroundStyle(.secondary)
                }
                .font(.subheadline)
            }
        }
        .frame(minHeight: 44)
        .accessibilityElement(children: .combine)
    }
}
