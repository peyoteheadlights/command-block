import Core
import Observation
import SwiftUI

/// Settings > Discord bot (owner only): the token, inviting the bot, and each
/// Discord server's setup, channels, reminders and Levels, like the desktop
/// app (mirrors Discord.kt).
@MainActor
@Observable
final class DiscordPages {
    @ObservationIgnored private let client: () -> AgentClient?
    @ObservationIgnored private let noted: (AgentError) -> Void
    @ObservationIgnored private let strings: Strings

    init(client: @escaping () -> AgentClient?, noted: @escaping (AgentError) -> Void, strings: Strings) {
        self.client = client
        self.noted = noted
        self.strings = strings
    }

    private(set) var status = PageRead<DiscordStatus>()
    private(set) var busy: String? = nil
    private(set) var said: Said? = nil

    func load() async {
        guard let client = client() else { return }
        status.loading = true
        do {
            status = PageRead(value: try await client.discord())
        } catch let problem as AgentError {
            noted(problem)
            status.problem = problem
            status.loading = false
        } catch {
            status.loading = false
        }
    }

    /// Asks Discord again: the token, the switches, each Discord server's roles and channels.
    func check() async {
        await work("check") { [weak self] client in
            self?.status = PageRead(value: try await client.checkDiscord())
            return "discord.checked"
        }
    }

    func removeOnLeave(_ guildId: String, _ on: Bool) async {
        await work("leave:\(guildId)") { [weak self] client in
            self?.status = PageRead(value: try await client.setRemoveOnLeave(guildId: guildId, on: on))
            return "settings.saved"
        }
    }

    /// True once the PC has it, to empty the box.
    func saveToken(_ token: String) async -> Bool {
        var saved = false
        await work("token") { [weak self] client in
            self?.status = PageRead(value: try await client.setDiscordToken(token))
            saved = true
            return "discord.token_saved"
        }
        return saved
    }

    func removeToken() async {
        await work("token") { [weak self] client in
            self?.status = PageRead(value: try await client.removeDiscordToken())
            return "discord.token_removed"
        }
    }

    func setUp(_ guildId: String, _ servers: [String]) async {
        var made: [String] = []
        await work("setup:\(guildId)", values: { ["made": made.joined(separator: ", ")] }) { [weak self] client in
            let done = try await client.setUpDiscord(guildId: guildId, servers: servers)
            self?.status = PageRead(value: done.status)
            made = done.made
            return made.isEmpty ? "discord.set_up_nothing" : "discord.set_up_done"
        }
    }

    func tie(_ guildId: String, _ row: ChannelRow, _ channelId: String?) async {
        await work("channel:\(guildId)") { [weak self] client in
            self?.status = PageRead(value: try await client.tieChannel(guildId: guildId, body: Community.channelBody(row, channelId: channelId)))
            return "discord.channel_saved"
        }
    }

    func reminders(_ guildId: String, _ how: String) async {
        await work("reminders:\(guildId)") { [weak self] client in
            self?.status = PageRead(value: try await client.setReminders(guildId: guildId, how: how))
            return "settings.saved"
        }
    }

    func levels(_ guildId: String, _ body: [String: JSONValue]) async {
        await work("levels:\(guildId)") { [weak self] client in
            _ = try await client.setLevels(guildId: guildId, body: body)
            self?.status = PageRead(value: try await client.discord())
            return "discord.levels_saved"
        }
    }

    /// The webhook too, while the bot posts the alerts (each one comes twice).
    func webhookToo(_ on: Bool) async {
        await work("webhook") { [weak self] client in
            _ = try await client.savePcSettings(["notifications.webhook_with_bot": .bool(on)])
            self?.status = PageRead(value: try await client.discord())
            return "settings.saved"
        }
    }

    /// One button at a time; `call` gives the key of the words to say once it worked.
    private func work(_ name: String, values: () -> [String: CustomStringConvertible] = { [:] },
                      _ call: (AgentClient) async throws -> String) async {
        guard let client = client(), busy == nil else { return }
        busy = name
        said = nil
        do {
            let key = try await call(client)
            said = Said(text: strings.t(key, values()), good: true)
        } catch let problem as AgentError {
            noted(problem)
            said = Said(text: problem.message(strings), good: false)
        } catch {}
        busy = nil
    }
}

/// Whether the bot is connected (measured by the PC, never assumed), what
/// Discord switched off, the bot's token and invite, and each Discord
/// server's checks, setup, channels, reminders and Levels. What each Discord
/// role may do stays in the desktop app.
struct DiscordView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.openURL) private var openURL
    @State private var removing = false

    var body: some View {
        let pages = model.discord
        let palette = model.basePalette(colorScheme)
        TimelineView(.periodic(from: .now, by: 30)) { context in
            Form {
                if let problem = pages.status.problem {
                    Section {
                        Text(problem.message(model.strings)).foregroundStyle(.red)
                        Button(model.t("mobile.common.retry")) { Task { await pages.load() } }
                    }
                }
                if pages.said != nil {
                    Section { SaidRow(said: pages.said, palette: palette) }
                }
                if let status = pages.status.value {
                    content(pages, status, palette, now: context.date.timeIntervalSince1970)
                } else if pages.status.problem == nil {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
        }
        .navigationTitle(model.t("discord.title"))
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await pages.load() }
        .task { await pages.load() }
        .alert(model.t("discord.token_remove"), isPresented: $removing) {
            Button(model.t("discord.token_remove"), role: .destructive) { Task { await pages.removeToken() } }
            Button(model.t("mobile.common.cancel"), role: .cancel) {}
        } message: {
            Text(model.t("discord.token_remove_body"))
        }
    }

    @ViewBuilder
    private func content(_ pages: DiscordPages, _ status: DiscordStatus, _ palette: Palette, now: Double) -> some View {
        let connected = status.state == "connected"
        let busy = pages.busy != nil
        Section(model.t("discord.bot")) {
            VStack(alignment: .leading, spacing: 4) {
                Text(Community.botState(model.strings, status)).foregroundStyle(palette.textPrimary)
                if connected, let since = status.connectedAt {
                    Hint(text: model.t("discord.since", ["time": Display.ago(model.strings, since, now: now)]), palette: palette)
                } else if !status.detail.isEmpty {
                    Hint(text: status.detail, palette: palette)
                }
            }
            ForEach(status.featuresOff, id: \.self) { feature in
                Text(model.t("discord.off.\(feature)")).foregroundStyle(palette.textPrimary)
            }
            if model.technical && connected {
                ForEach(status.switches.keys.sorted(), id: \.self) { name in
                    let on = status.switches[name] ?? nil
                    let words = on.map { $0 ? "discord.switch_on" : "discord.switch_off" } ?? "value.unknown"
                    Hint(text: model.t("discord.switch.\(name)") + ": " + model.t(words), palette: palette)
                }
            }
            Hint(text: model.t("discord.pc_on"), palette: palette)
            if connected {
                Toggle(model.t("discord.webhook_with_bot"), isOn: Binding(
                    get: { status.webhookWithBot },
                    set: { on in Task { await pages.webhookToo(on) } }
                ))
                .disabled(busy)
                Hint(text: model.t("discord.alerts_hint"), palette: palette)
            }
        }
        TokenSection(pages: pages, status: status, palette: palette, onRemove: { removing = true })
        if connected, let invite = status.inviteUrl, let url = URL(string: invite) {
            Section(model.t("discord.invite_title")) {
                ForEach(1...3, id: \.self) { i in Text(model.t("discord.server_steps.\(i)")).foregroundStyle(palette.textPrimary) }
                Button(model.t("discord.invite")) { openURL(url) }
            }
        }
        let servers = model.servers.map { ($0.id, $0.name) }
        ForEach(status.guilds) { guild in
            Section(guild.name) {
                if let problems = Community.problems(model.strings, guild) {
                    if problems.isEmpty {
                        Label(model.t("discord.all_good"), systemImage: "checkmark.circle")
                            .foregroundStyle(palette.tone(.success))
                    }
                    ForEach(problems, id: \.self) { problem in
                        Label(problem, systemImage: "xmark.circle").foregroundStyle(palette.danger)
                    }
                } else {
                    Text(model.t("discord.checking")).foregroundStyle(palette.textSecondary)
                }
            }
            GuildSetup(pages: pages, guild: guild, servers: servers, palette: palette)
            if !guild.servers.isEmpty && guild.problems != nil {
                Section(model.t("discord.channels")) {
                    ForEach(Community.channelRows(model.strings, guild, servers: servers)) { row in
                        Picker(row.label, selection: Binding(
                            get: { row.channelId ?? "" },
                            set: { id in Task { await pages.tie(guild.id, row, id.isEmpty ? nil : id) } }
                        )) {
                            Text(model.t(Community.noChannel(row))).tag("")
                            ForEach(guild.channels, id: \.id) { channel in Text(verbatim: "#" + channel.name).tag(channel.id) }
                        }
                        .disabled(busy)
                    }
                    Hint(text: model.t("discord.console_hint"), palette: palette)
                }
                Section {
                    Picker(model.t("discord.reminders"), selection: Binding(
                        get: { guild.reminders },
                        set: { how in Task { await pages.reminders(guild.id, how) } }
                    )) {
                        ForEach(Community.REMINDERS, id: \.self) { Text(model.t("discord.reminders.\($0)")).tag($0) }
                    }
                    .disabled(busy)
                    Hint(text: model.t("discord.reminders_hint"), palette: palette)
                    Toggle(isOn: Binding(
                        get: { guild.removeOnLeave },
                        set: { on in Task { await pages.removeOnLeave(guild.id, on) } }
                    )) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(model.t("discord.remove_on_leave"))
                            Hint(text: model.t("discord.remove_on_leave_hint"), palette: palette)
                        }
                    }
                    .disabled(busy)
                }
                LevelSettings(pages: pages, guild: guild, palette: palette)
            }
        }
        if status.tokenSet {
            Section {
                Button(model.t(pages.busy == "check" ? "discord.checking" : "discord.check_again")) {
                    Task { await pages.check() }
                }
                .disabled(busy)
            }
        }
    }
}

/// The bot's token: the developer portal's steps until it works, then Save or Remove.
private struct TokenSection: View {
    let pages: DiscordPages
    let status: DiscordStatus
    let palette: Palette
    let onRemove: () -> Void

    @Environment(AppModel.self) private var model
    @Environment(\.openURL) private var openURL
    @State private var token = ""

    var body: some View {
        Section(model.t("discord.token_title")) {
            if !status.tokenSet || status.state == "token_rejected" {
                ForEach(1...6, id: \.self) { i in Text(model.t("discord.steps.\(i)")).foregroundStyle(palette.textPrimary) }
                if let url = URL(string: Community.DEVELOPER_PORTAL) {
                    Button(model.t("discord.portal")) { openURL(url) }
                }
            }
            SecureField(model.t("discord.token_title"), text: Binding(get: { token }, set: { token = String($0.prefix(120)) }))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Hint(text: model.t("mobile.discord.token_hint"), palette: palette)
            Button(model.t("discord.token_save")) {
                let typed = token
                Task { if await pages.saveToken(typed) { token = "" } }
            }
            .disabled(pages.busy != nil || token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            if status.tokenSet {
                Button(model.t("discord.token_remove"), role: .destructive, action: onRemove).disabled(pages.busy != nil)
            }
        }
    }
}

/// Which Minecraft servers this Discord server is for, and the one button.
private struct GuildSetup: View {
    let pages: DiscordPages
    let guild: DiscordGuild
    let servers: [(String, String)]
    let palette: Palette

    @Environment(AppModel.self) private var model
    @State private var ticked: Set<String>? = nil

    var body: some View {
        let ids = servers.map { $0.0 }
        let chosen = ticked ?? Set(Community.setupServers(guild, servers: ids))
        Section {
            Text(model.t("discord.for_servers")).foregroundStyle(palette.textPrimary)
            ForEach(servers.indices, id: \.self) { i in
                let server = servers[i]
                Toggle(server.1, isOn: Binding(
                    get: { chosen.contains(server.0) },
                    set: { on in ticked = on ? chosen.union([server.0]) : chosen.subtracting([server.0]) }
                ))
                .disabled(pages.busy != nil)
            }
            Button(model.t("discord.set_up")) {
                let picked = ids.filter { chosen.contains($0) }
                Task { await pages.setUp(guild.id, picked) }
            }
            .disabled(pages.busy != nil || chosen.isEmpty)
            Hint(text: model.t("discord.set_up_hint"), palette: palette)
        }
        .onChange(of: guild.servers) { ticked = nil }
    }
}

/// Level-ups on or off, the weekly leaderboard's day, and which Discord role goes with which Level.
private struct LevelSettings: View {
    let pages: DiscordPages
    let guild: DiscordGuild
    let palette: Palette

    @Environment(AppModel.self) private var model
    @State private var announce = true
    @State private var day: Int? = nil
    @State private var rows: [LevelRow] = []

    /// Monday first, as the agent counts them (0 to 6).
    private var days: [String] {
        let names = DateFormatter().weekdaySymbols ?? []
        return names.count == 7 ? Array(names[1...]) + [names[0]] : (0..<7).map { "\($0)" }
    }

    var body: some View {
        let roles = Community.levelRoleChoices(guild)
        Section(model.t("discord.levels")) {
            Hint(text: model.t("discord.levels_hint"), palette: palette)
            Toggle(model.t("discord.level_ups"), isOn: $announce)
            Picker(model.t("discord.leaderboard"), selection: $day) {
                Text(model.t("discord.leaderboard_off")).tag(Int?.none)
                ForEach(Array(days.enumerated()), id: \.offset) { i, name in Text(verbatim: name).tag(Optional(i)) }
            }
            Hint(text: model.t("discord.leaderboard_hint"), palette: palette)
            ForEach(rows.indices, id: \.self) { i in
                let row = self.row(i)
                Stepper(value: row.level, in: 1...1000) {
                    Text(model.t("discord.level_role") + " \(row.wrappedValue.level)").foregroundStyle(palette.textPrimary)
                }
                Picker(model.t("discord.level_role"), selection: row.roleId) {
                    Text(model.t("value.unknown")).tag(String?.none)
                    ForEach(roles, id: \.id) { role in Text(verbatim: role.name).tag(Optional(role.id)) }
                }
                Button(model.t("discord.level_remove"), role: .destructive) { rows.remove(at: i) }
            }
            Button(model.t("discord.level_add")) { rows.append(LevelRow(level: (rows.map(\.level).max() ?? 0) + 5, roleId: nil)) }
            Button(model.t("discord.level_save")) {
                let body = Community.levelsBody(guild, announce: announce, day: day, rows: rows)
                Task { await pages.levels(guild.id, body) }
            }
            .disabled(pages.busy != nil)
        }
        .task(id: guild.levels) {
            announce = guild.levels.announce
            day = guild.levels.leaderboardDay
            rows = guild.levels.roles.map { LevelRow(level: $0.level, roleId: $0.roleId) }
        }
    }

    /// Row `i`, safe while a removed row's last redraw still asks for it.
    private func row(_ i: Int) -> Binding<LevelRow> {
        Binding(
            get: { i < rows.count ? rows[i] : LevelRow(level: 1, roleId: nil) },
            set: { if i < rows.count { rows[i] = $0 } }
        )
    }
}
