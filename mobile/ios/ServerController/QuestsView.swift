import Core
import ImageIO
import SwiftUI
import UIKit

/// A button waiting for "yes": end a quest or call off an event.
private struct Ending: Identifiable {
    let kind: String
    let id: Int64
    let title: String
}

/// Quests and events for the owner and people with "Quests and events"
/// (mirrors Quests.kt): the quests going on (joined in Discord), claims to
/// approve with their screenshot, ideas from /quest suggest, a new quest,
/// then the events with who came, and the polls. Events and polls are made
/// in the desktop app or in Discord (/event, /poll); here they're looked after.
struct QuestsView: View {
    let serverId: String

    @Environment(AppModel.self) private var model
    @State private var ending: Ending?
    // The idea "Make it" copies into the form.
    @State private var form = QuestForm()

    private var quests: QuestPages { model.pages(serverId).quests }

    var body: some View {
        let quests = self.quests
        ServerFrame(serverId: serverId, title: model.t("page.quests")) { palette in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let problem = quests.problem {
                        ProblemRow(text: problem, palette: palette)
                    }
                    if let problem = quests.page.problem {
                        ProblemCard(problem: problem, palette: palette) { await quests.load() }
                    } else if let page = quests.page.value {
                        questsPart(quests, page, palette)
                        NewQuestCard(quests: quests, page: page, form: $form, palette: palette)
                    } else {
                        ProgressView().frame(maxWidth: .infinity)
                    }
                    if let problem = quests.events.problem {
                        ProblemCard(problem: problem, palette: palette) { await quests.load() }
                    } else if let events = quests.events.value {
                        eventsPart(quests, events, roles: quests.page.value?.roleNames ?? [:], palette)
                    }
                }
                .padding(16)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .refreshable { await quests.load() }
            .alert(
                ending.map { model.t("\($0.kind)_title", ["title": $0.title]) } ?? "",
                isPresented: Binding(get: { ending != nil }, set: { if !$0 { ending = nil } }),
                presenting: ending,
                actions: { e in
                    Button(model.t(e.kind), role: .destructive) {
                        let strings = model.strings
                        Task {
                            if e.kind == "quests.end" { await quests.end(e.id, strings) } else { await quests.callOff(e.id, strings) }
                        }
                    }
                    Button(model.t("mobile.common.cancel"), role: .cancel) {}
                },
                message: { e in Text(model.t("\(e.kind)_body")) }
            )
        }
        .task(id: serverId) { await quests.load() }
    }

    // MARK: - Quests

    @ViewBuilder
    private func questsPart(_ quests: QuestPages, _ page: QuestsPage, _ palette: Palette) -> some View {
        let roles = page.roleNames
        let busy = quests.busy != nil
        if !page.bot {
            Hint(text: model.t("quests.no_bot"), palette: palette)
        }
        SectionTitle(text: model.t("quests.list"), palette: palette)
        if page.quests.isEmpty {
            EmptyCard(title: model.t("quests.none"), palette: palette)
        }
        ForEach(page.quests) { quest in
            CardBox(palette: palette) {
                Text(verbatim: "\(quest.id). \(quest.title)")
                    .font(.headline)
                    .foregroundStyle(palette.textPrimary)
                Hint(text: Community.questLine(model.strings, quest, roles: roles), palette: palette)
                if !quest.details.trimmingCharacters(in: .whitespaces).isEmpty {
                    Hint(text: quest.details, palette: palette)
                }
                Hint(text: Community.membersLine(model.strings, quest), palette: palette)
                Presses(presses: [
                    Press(title: model.t("quests.end"), danger: true, enabled: !busy) {
                        ending = Ending(kind: "quests.end", id: quest.id, title: quest.title)
                    },
                ], palette: palette)
            }
        }
        if !page.claims.isEmpty {
            SectionTitle(text: model.t("quests.claims"), palette: palette)
        }
        ForEach(page.claims) { claim in
            CardBox(palette: palette) {
                Text(model.t("quests.claim", ["name": claim.discordName, "title": claim.title]))
                    .foregroundStyle(palette.textPrimary)
                if let note = claim.note, !note.isEmpty {
                    Hint(text: note, palette: palette)
                }
                if let shot = quests.screenshot, shot.claimId == claim.id {
                    Screenshot(data: shot.data, palette: palette)
                }
                Presses(presses: claimPresses(quests, claim), palette: palette)
            }
        }
        if !page.suggestions.isEmpty {
            SectionTitle(text: model.t("quests.ideas"), palette: palette)
        }
        ForEach(page.suggestions) { idea in
            CardBox(palette: palette) {
                Text(verbatim: idea.title).foregroundStyle(palette.textPrimary)
                Hint(text: model.t("quests.idea_from", ["name": idea.discordName, "details": idea.details]), palette: palette)
                Presses(presses: [
                    Press(title: model.t("quests.make_it"), enabled: !busy) {
                        form.title = idea.title
                        form.details = idea.details
                        form.suggestionId = idea.id
                    },
                    Press(title: model.t("quests.turn_down"), enabled: !busy) { run { await $0.turnDown(idea.id, $1) } },
                ], palette: palette)
            }
        }
    }

    private func claimPresses(_ quests: QuestPages, _ claim: QuestClaim) -> [Press] {
        let busy = quests.busy != nil
        var presses = [
            Press(title: model.t("quests.approve"), enabled: !busy) { run { await $0.answerClaim(claim.id, approve: true, $1) } },
            Press(title: model.t("quests.refuse"), enabled: !busy) { run { await $0.answerClaim(claim.id, approve: false, $1) } },
        ]
        if claim.screenshot != nil && quests.screenshot?.claimId != claim.id {
            presses.append(Press(title: model.t("quests.see_screenshot"), enabled: !busy) { run { await $0.showScreenshot(claim.id, $1) } })
        }
        return presses
    }

    // MARK: - Events and polls

    @ViewBuilder
    private func eventsPart(_ quests: QuestPages, _ events: EventsPage, roles: [String: String], _ palette: Palette) -> some View {
        let busy = quests.busy != nil
        SectionTitle(text: model.t("game_events.list"), palette: palette)
        if events.events.isEmpty {
            EmptyCard(title: model.t("game_events.none"), palette: palette)
        }
        ForEach(events.events) { event in
            CardBox(palette: palette) {
                Text(verbatim: event.title)
                    .font(.headline)
                    .foregroundStyle(palette.textPrimary)
                Hint(text: Community.eventLine(model.strings, event, roles: roles), palette: palette)
                if !event.details.trimmingCharacters(in: .whitespaces).isEmpty {
                    Hint(text: event.details, palette: palette)
                }
                Hint(text: Community.answersLine(model.strings, event), palette: palette)
                if Community.canCallOff(event) {
                    Presses(presses: [
                        Press(title: model.t("game_events.call_off"), danger: true, enabled: !busy) {
                            ending = Ending(kind: "game_events.call_off", id: event.id, title: event.title)
                        },
                    ], palette: palette)
                }
                if Community.ATTENDANCE_STATES.contains(event.state) {
                    whoCame(quests, event, palette)
                }
            }
        }
        SectionTitle(text: model.t("game_events.polls"), palette: palette)
        if events.polls.isEmpty {
            EmptyCard(title: model.t("game_events.no_polls"), palette: palette)
        }
        ForEach(events.polls) { poll in
            CardBox(palette: palette) {
                Text(verbatim: poll.question).foregroundStyle(palette.textPrimary)
                Hint(text: Community.pollCounts(poll), palette: palette)
                Hint(text: Community.pollState(model.strings, poll), palette: palette)
                if poll.open {
                    Presses(presses: [
                        Press(title: model.t("game_events.close_poll"), enabled: !busy) { run { await $0.closePoll(poll.id, $1) } },
                    ], palette: palette)
                }
            }
        }
        Hint(text: model.t("mobile.quests.make_events"), palette: palette)
    }

    /// Who came: ticks, changeable for 7 days after the end; Approve for a list to OK.
    @ViewBuilder
    private func whoCame(_ quests: QuestPages, _ event: GameEvent, _ palette: Palette) -> some View {
        let hint = event.state == "approving" ? "game_events.approve_hint" : "game_events.fix_hint"
        Hint(text: model.t(event.attendance.isEmpty ? "game_events.nobody" : hint), palette: palette)
        ForEach(event.attendance) { person in
            Toggle(Community.personLine(model.strings, person), isOn: Binding(
                get: { person.attended },
                set: { on in run { await $0.fix(event.id, person.discordId, attended: on, $1) } }
            ))
            .disabled(!event.fixable || quests.busy != nil)
        }
        if event.state == "approving" {
            Presses(presses: [
                Press(title: model.t("game_events.approve"), enabled: quests.busy == nil) { run { await $0.approve(event.id, $1) } },
            ], palette: palette)
        }
    }

    private func run(_ call: @escaping (QuestPages, Strings) async -> Void) {
        let quests = self.quests
        let strings = model.strings
        Task { await call(quests, strings) }
    }
}

/// A claim's screenshot, made smaller than the phone's screen before it is drawn.
private struct Screenshot: View {
    let data: Data
    let palette: Palette

    @Environment(AppModel.self) private var model
    @State private var image: UIImage?
    @State private var unreadable = false

    /// Wide enough for any phone screen; the full picture stays on the PC.
    private static let SHOT_SIZE = 1080

    var body: some View {
        Group {
            if let image = image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
                    .accessibilityLabel(model.t("quests.see_screenshot"))
            } else if unreadable {
                Text(model.t("quests.screenshot_unreadable")).foregroundStyle(palette.danger)
            }
        }
        .task(id: data) {
            image = Screenshot.small(data)
            unreadable = image == nil
        }
    }

    static func small(_ data: Data) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: SHOT_SIZE,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary).map { UIImage(cgImage: $0) }
    }
}

/// A new quest. The agent checks every value again and says what's wrong.
private struct NewQuestCard: View {
    let quests: QuestPages
    let page: QuestsPage
    @Binding var form: QuestForm
    let palette: Palette

    @Environment(AppModel.self) private var model

    var body: some View {
        let kinds = page.kinds.isEmpty ? ["playtime"] : page.kinds
        let kind = kinds.contains(form.kind) ? form.kind : kinds[0]
        let roles = page.roleNames.sorted { $0.value.lowercased() < $1.value.lowercased() }
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle(text: model.t("quests.new"), palette: palette)
            CardBox(palette: palette) {
                field("quests.title", $form.title, 80)
                field("quests.details", $form.details, 300)
                Picker(model.t("quests.kind"), selection: Binding(get: { kind }, set: { form.kind = $0 })) {
                    ForEach(kinds, id: \.self) { Text(model.t("quests.kind.\($0)")).tag($0) }
                }
                Hint(text: Community.goalHint(model.strings, kind: kind, kinds: page.kinds), palette: palette)
                if Community.needsGoal(kind) {
                    field("quests.goal", $form.goal, 100)
                }
                if Community.needsCount(kind) {
                    count("quests.count", $form.count, 1...100_000)
                }
                Picker(model.t("quests.repeat"), selection: $form.repeatEvery) {
                    ForEach(Community.REPEATS, id: \.self) { Text(model.t("quests.repeat_pick.\($0)")).tag($0) }
                }
                Text(model.t("quests.rewards_title")).foregroundStyle(palette.textPrimary)
                Hint(text: model.t("quests.rewards_hint"), palette: palette)
                if page.bot && !roles.isEmpty {
                    Picker(model.t("quests.reward_role"), selection: $form.roleId) {
                        Text(model.t("quests.reward_none")).tag(String?.none)
                        ForEach(roles, id: \.key) { role in Text(verbatim: role.value).tag(Optional(role.key)) }
                    }
                }
                field("quests.reward_item", $form.item, 100)
                if !form.item.trimmingCharacters(in: .whitespaces).isEmpty {
                    count("quests.reward_item_count", $form.itemCount, 1...64)
                }
                count("quests.reward_xp", $form.xpLevels, 0...30)
                count("quests.reward_level_xp", $form.levelXp, 0...500)
                if let problem = quests.formProblem {
                    Text(problem).foregroundStyle(palette.danger)
                }
                Button(model.t("quests.create")) {
                    var sent = form
                    sent.kind = kind
                    let strings = model.strings
                    Task {
                        if await quests.make(sent, strings) { form = QuestForm() }
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(palette.accentInk)
                .disabled(quests.busy != nil || form.title.trimmingCharacters(in: .whitespaces).isEmpty)
                Hint(text: model.t("mobile.quests.last_day"), palette: palette)
            }
        }
    }

    private func field(_ key: String, _ text: Binding<String>, _ max: Int) -> some View {
        TextField(model.t(key), text: Binding(get: { text.wrappedValue }, set: { text.wrappedValue = String($0.prefix(max)) }))
            .textFieldStyle(.roundedBorder)
    }

    /// A whole number kept inside `range`.
    private func count(_ key: String, _ value: Binding<Int>, _ range: ClosedRange<Int>) -> some View {
        Stepper(value: value, in: range) {
            HStack {
                Text(model.t(key)).foregroundStyle(palette.textPrimary)
                Spacer()
                TextField(
                    model.t(key),
                    value: Binding(get: { value.wrappedValue }, set: { value.wrappedValue = Swift.min(Swift.max($0, range.lowerBound), range.upperBound) }),
                    format: .number
                )
                .keyboardType(.numberPad)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 90)
            }
        }
    }
}
