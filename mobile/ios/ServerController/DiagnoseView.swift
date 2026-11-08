import Core
import Observation
import SwiftUI

/// Diagnose server on the phone (mirrors Diagnose.kt): the same run as the
/// desktop window (desktop/diagnose.py). The agent does the work and keeps
/// the list; this reads it once a second while it runs. The Microsoft
/// sign-ins stay in the desktop app, so a join that needs one says so.
@MainActor
@Observable
final class DiagnosePage {
    let serverId: String
    @ObservationIgnored private let client: () -> AgentClient?
    @ObservationIgnored private let noted: (AgentError) -> Void

    init(serverId: String, client: @escaping () -> AgentClient?, noted: @escaping (AgentError) -> Void) {
        self.serverId = serverId
        self.client = client
        self.noted = noted
    }

    private(set) var result: Diagnosis? = nil
    /// A question the agent asked before starting (the owner is playing, or a mod server).
    private(set) var asking: DiagnoseStarted? = nil
    private(set) var problem: AgentError? = nil
    private(set) var busy = false
    private(set) var notStarted = false
    @ObservationIgnored private var goAhead = false
    @ObservationIgnored private var mods: Bool? = nil

    func load() async {
        await work { client in self.result = try await client.diagnosis(serverId: self.serverId) }
    }

    func start() async {
        await work { client in
            self.notStarted = false
            let answer = try await client.startDiagnose(serverId: self.serverId, goAhead: self.goAhead, mods: self.mods)
            if answer.ask != nil || answer.askMods != nil {
                self.asking = answer
            } else {
                self.goAhead = false
                self.mods = nil
                self.result = try await client.diagnosis(serverId: self.serverId)
            }
        }
    }

    /// The answer to `asking`: yes or no.
    func answer(_ yes: Bool) async {
        guard let question = asking else { return }
        asking = nil
        if question.askMods != nil {
            mods = yes  // no still runs the other joins, like the desktop app
            await start()
        } else if yes {
            goAhead = true
            await start()
        } else {
            notStarted = true
        }
    }

    /// Apply all ("all"), Apply safe ("safe") or Deny all ("none").
    func pick(_ pick: String) async {
        await work { client in
            _ = try await client.diagnoseChanges(serverId: self.serverId, pick: pick)
            self.result = try await client.diagnosis(serverId: self.serverId)
        }
    }

    private func work(_ block: (AgentClient) async throws -> Void) async {
        guard let client = client() else { return }
        busy = true
        problem = nil
        defer { busy = false }
        do {
            try await block(client)
        } catch let failed as AgentError {
            noted(failed)
            problem = failed
        } catch {}
    }
}

struct DiagnoseView: View {
    let serverId: String

    @Environment(AppModel.self) private var model

    var body: some View {
        let page = model.pages(serverId).diagnose
        let result = page.result
        let running = result?.running == true
        let waiting = result.map(Diagnose.waiting) ?? []
        ServerFrame(serverId: serverId, title: model.t("action.diagnose")) { palette in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    CardBox(palette: palette) {
                        Text(model.t(status(page)))
                            .foregroundStyle(palette.textSecondary)
                        if !running && waiting.isEmpty {
                            Button(model.t(result == nil ? "action.diagnose" : "diagnose.again")) {
                                Task { await page.start() }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(page.busy)
                        }
                    }
                    if let problem = page.problem {
                        ProblemRow(text: problem.message(model.strings), palette: palette)
                    }
                    if let result = result {
                        details(result, waiting, page, palette)
                    }
                }
                .padding(16)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .refreshable { await page.load() }
            .alert(
                model.t("action.diagnose"),
                isPresented: Binding(get: { page.asking != nil }, set: { _ in }),
                presenting: page.asking,
                actions: { question in
                    let mods = question.askMods != nil
                    Button(model.t(mods ? "diagnose.mods_yes" : "diagnose.go_ahead")) { Task { await page.answer(true) } }
                    Button(model.t(mods ? "mobile.diagnose.mods_no" : "mobile.common.cancel"), role: .cancel) {
                        Task { await page.answer(false) }
                    }
                },
                message: { question in Text(verbatim: question.askMods ?? question.ask ?? "") }
            )
        }
        .task(id: serverId) { await page.load() }
        .task(id: running) {
            guard running else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                await page.load()
            }
        }
    }

    private func status(_ page: DiagnosePage) -> String {
        if page.notStarted { return "diagnose.not_started" }
        guard let result = page.result else { return "mobile.diagnose.intro" }
        return Diagnose.statusKey(result)
    }

    @ViewBuilder
    private func details(_ result: Diagnosis, _ waiting: [DiagnoseChange], _ page: DiagnosePage, _ palette: Palette) -> some View {
        if result.signIn { Hint(text: model.t("mobile.diagnose.sign_in"), palette: palette) }
        if result.signInMods { Hint(text: model.t("mobile.diagnose.sign_in_mods"), palette: palette) }
        ForEach(Array(Diagnose.groups(result.lines).enumerated()), id: \.offset) { _, group in
            SectionTitle(text: group.group, palette: palette)
            CardBox(palette: palette) {
                ForEach(Array(group.lines.enumerated()), id: \.offset) { _, line in
                    row("\(Diagnose.mark(line.ok))  \(line.what)", line.detail, palette, danger: line.ok == false)
                }
            }
        }
        if !result.known.isEmpty {
            SectionTitle(text: model.t("diagnose.known"), palette: palette)
            CardBox(palette: palette) {
                ForEach(Array(result.known.enumerated()), id: \.offset) { _, known in
                    let more = [known.change.map { model.t("diagnose.known_change", ["change": $0]) }, known.link]
                        .compactMap { $0 }
                    row(known.say, more.isEmpty ? nil : more.joined(separator: "\n"), palette)
                }
            }
        }
        if !result.done.isEmpty {
            SectionTitle(text: model.t("diagnose.made"), palette: palette)
            CardBox(palette: palette) {
                ForEach(Array(result.done.enumerated()), id: \.offset) { _, done in
                    row("\(Diagnose.doneMark(done))  \(done.title)", done.detail, palette)
                }
            }
        }
        if !result.steps.isEmpty {
            SectionTitle(text: model.t("diagnose.yourself"), palette: palette)
            CardBox(palette: palette) {
                ForEach(Array(result.steps.enumerated()), id: \.offset) { _, step in row(step, nil, palette) }
            }
        }
        ForEach([true, false], id: \.self) { safe in
            let group = waiting.filter { $0.safe == safe }
            if !group.isEmpty {
                SectionTitle(text: model.t(safe ? "diagnose.safe" : "diagnose.risky"), palette: palette)
                CardBox(palette: palette) {
                    ForEach(Array(group.enumerated()), id: \.offset) { _, change in
                        row(change.title, change.why.isEmpty ? nil : change.why, palette)
                    }
                }
            }
        }
        if !waiting.isEmpty {
            HStack(spacing: 8) {
                ForEach(Diagnose.picks, id: \.self) { pick in
                    let button = Button(model.t("diagnose.apply_\(pick)")) { Task { await page.pick(pick) } }
                        .disabled(page.busy || (pick == "safe" && !waiting.contains { $0.safe }))
                        .frame(maxWidth: .infinity)
                    if pick == "all" {
                        button.buttonStyle(.borderedProminent)
                    } else {
                        button.buttonStyle(.bordered)
                    }
                }
            }
            .padding(.top, 8)
        }
    }

    private func row(_ title: String, _ detail: String?, _ palette: Palette, danger: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(verbatim: title)
                .foregroundStyle(danger ? palette.danger : palette.textPrimary)
            if let detail = detail, !detail.isEmpty {
                Text(verbatim: detail)
                    .font(.footnote)
                    .foregroundStyle(palette.textSecondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}
