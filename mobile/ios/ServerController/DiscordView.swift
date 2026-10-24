import Core
import Observation
import SwiftUI

/// Settings > Discord bot (owner only): what the PC measured, and its two
/// switches (mirrors Discord.kt).
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

    /// The webhook too, while the bot posts the alerts (each one comes twice).
    func webhookToo(_ on: Bool) async {
        await work("webhook") { [weak self] client in
            _ = try await client.savePcSettings(["notifications.webhook_with_bot": .bool(on)])
            self?.status = PageRead(value: try await client.discord())
            return "settings.saved"
        }
    }

    /// One button at a time; `call` gives the key of the words to say once it worked.
    private func work(_ name: String, _ call: (AgentClient) async throws -> String) async {
        guard let client = client(), busy == nil else { return }
        busy = name
        said = nil
        do {
            said = Said(text: strings.t(try await call(client)), good: true)
        } catch let problem as AgentError {
            noted(problem)
            said = Said(text: problem.message(strings), good: false)
        } catch {}
        busy = nil
    }
}

/// Whether the bot is connected (measured by the PC, never assumed), what
/// Discord switched off, and each Discord server's checks. Setting it up
/// (the token, inviting it, channels, Discord roles) stays in the desktop
/// app, where pasting a token and picking channels is easier.
struct DiscordView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme

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
        }
        if status.tokenSet {
            Section {
                Button(model.t(pages.busy == "check" ? "discord.checking" : "discord.check_again")) {
                    Task { await pages.check() }
                }
                .disabled(busy)
            }
        }
        Section {
            Hint(text: model.t("mobile.discord.on_pc"), palette: palette)
        }
    }
}
