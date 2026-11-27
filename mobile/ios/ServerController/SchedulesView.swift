import Core
import SwiftUI

/// What runs on its own at set times: a form to add one, then each schedule
/// with when it runs next and how its last run went, Turn on or off, Run now
/// and Delete. Only someone allowed to manage schedules gets the buttons.
struct SchedulesView: View {
    let serverId: String

    @Environment(AppModel.self) private var model
    @State private var deleting: Schedule?

    private var world: WorldPages { model.pages(serverId).world }

    var body: some View {
        let world = self.world
        ServerFrame(serverId: serverId, title: model.t("page.schedules")) { palette in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    content(world, palette)
                }
                .padding(16)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .refreshable { await world.loadSchedules() }
            .alert(
                deleting.map { model.t("schedules.delete_title", ["name": $0.name]) } ?? "",
                isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                presenting: deleting,
                actions: { schedule in
                    Button(model.t("schedules.delete"), role: .destructive) {
                        let strings = model.strings
                        Task { await world.deleteSchedule(schedule, strings) }
                    }
                    Button(model.t("mobile.common.cancel"), role: .cancel) {}
                },
                message: { _ in Text(model.t("schedules.delete_body")) }
            )
        }
        .task(id: serverId) { await world.loadSchedules() }
    }

    @ViewBuilder
    private func content(_ world: WorldPages, _ palette: Palette) -> some View {
        let strings = model.strings
        let canManage = model.can(Permissions.SCHEDULES_MANAGE)
        let busy = world.busy
        SaidRow(said: world.said, palette: palette)
        if let problem = world.schedules.problem {
            ProblemCard(problem: problem, palette: palette) { await world.loadSchedules() }
        } else if let list = world.schedules.value {
            // Above the list ("Add one above").
            if canManage {
                AddScheduleForm(world: world, tasks: list.tasks, palette: palette)
            }
            SectionTitle(text: model.t("schedules.list"), palette: palette)
            if list.schedules.isEmpty {
                EmptyCard(title: model.t("schedules.none"), hint: model.t("schedules.none_hint"), palette: palette)
            }
            ForEach(list.schedules) { schedule in
                CardBox(palette: palette) {
                    Text(verbatim: schedule.name)
                        .font(.headline)
                        .foregroundStyle(palette.textPrimary)
                    Hint(
                        text: Schedules.summary(strings, schedule, tasks: list.tasks) + " · " + Schedules.whenText(strings, schedule),
                        palette: palette
                    )
                    Text(verbatim: model.t("schedules.col_next") + ": " + Schedules.next(strings, schedule))
                        .font(.footnote)
                        .foregroundStyle(palette.textPrimary)
                    Hint(text: model.t("schedules.col_result") + ": " + (schedule.lastResult ?? "—"), palette: palette)
                    if canManage {
                        Presses(presses: [
                            Press(title: model.t(schedule.enabled ? "schedules.turn_off" : "schedules.turn_on"), enabled: busy == nil) {
                                Task { await world.toggle(schedule, strings) }
                            },
                            Press(title: model.t("schedules.run_now"), enabled: busy == nil) {
                                Task { await world.runNow(schedule, strings) }
                            },
                            Press(title: model.t("schedules.delete"), danger: true, enabled: busy == nil) {
                                deleting = schedule
                            },
                        ], palette: palette)
                    }
                }
            }
        } else {
            ProgressView().frame(maxWidth: .infinity)
        }
    }
}

/// Name, the steps, how often and when; Create sends it.
private struct AddScheduleForm: View {
    let world: WorldPages
    let tasks: [String]
    let palette: Palette

    @Environment(AppModel.self) private var model
    @State private var name = ""
    @State private var steps: [Schedules.Step] = []
    @State private var kind = Schedules.KINDS[0]
    @State private var expr = ""
    @State private var keepGoing = false
    @State private var onlyIfRunning = false
    @State private var skipWhenEmpty = false

    var body: some View {
        let adding = world.busy == "add"
        let choices = Schedules.stepTasks(tasks, technical: model.strings.technical)
        let shown = steps.isEmpty ? [Schedules.Step(task: choices.first ?? "")] : steps
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle(text: model.t("schedules.add"), palette: palette)
            CardBox(palette: palette) {
                TextField(model.t("schedules.name_placeholder"), text: $name)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel(model.t("schedules.name"))
                    .onChange(of: name) {
                        if name.count > Schedules.NAME_LENGTH { name = String(name.prefix(Schedules.NAME_LENGTH)) }
                    }
                ForEach(shown.indices, id: \.self) { index in
                    stepFields(index, shown, choices)
                }
                Button(model.t("schedules.add_step")) { steps = shown + [Schedules.Step(task: choices.first ?? "")] }
                    .disabled(shown.count >= Schedules.MAX_STEPS)
                if shown.count > 1 {
                    Toggle(model.t("schedules.keep_going"), isOn: $keepGoing)
                }
                Toggle(model.t("schedules.only_if_running"), isOn: $onlyIfRunning)
                Toggle(model.t("schedules.skip_when_empty"), isOn: $skipWhenEmpty)
                Picker(model.t("schedules.repeat"), selection: $kind) {
                    ForEach(Schedules.kinds(technical: model.strings.technical), id: \.self) { option in
                        Text(model.t(Schedules.kindKey(option))).tag(option)
                    }
                }
                .pickerStyle(.menu)
                TextField(model.t("schedules.when_placeholder"), text: $expr)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityLabel(model.t("schedules.when"))
                    .onChange(of: expr) {
                        if expr.count > Schedules.EXPR_LENGTH { expr = String(expr.prefix(Schedules.EXPR_LENGTH)) }
                    }
                Hint(text: model.t("schedules.when_hint"), palette: palette)
                Button(model.t(adding ? "action.saving" : "schedules.create")) {
                    let strings = model.strings
                    let sent = (name, shown, kind, expr, keepGoing, onlyIfRunning, skipWhenEmpty)
                    let body: (Bool) -> ScheduleBody = { confirm in
                        Schedules.newBody(
                            strings, name: sent.0, steps: sent.1, kind: sent.2, expr: sent.3, tasks: tasks,
                            keepGoing: sent.4, onlyIfRunning: sent.5, skipWhenEmpty: sent.6, confirm: confirm
                        )
                    }
                    Task {
                        if await world.addSchedule(body, strings) { emptied() }
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(palette.accent)
                .disabled(adding || world.busy != nil || shown.contains { $0.task.isEmpty } || expr.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .alert(
            model.t("schedules.confirm_title"),
            isPresented: Binding(get: { world.scheduleDanger != nil }, set: { _ in }),
            actions: {
                Button(model.t("action.save"), role: .destructive) {
                    let strings = model.strings
                    Task { if await world.answerDanger(true, strings) { emptied() } }
                }
                Button(model.t("mobile.common.cancel"), role: .cancel) {
                    let strings = model.strings
                    Task { _ = await world.answerDanger(false, strings) }
                }
            },
            message: { Text(verbatim: world.scheduleDanger ?? "") }
        )
    }

    private func emptied() {
        name = ""
        expr = ""
        steps = []
    }

    /// One step: what it does, its words, and Remove when there are more.
    @ViewBuilder
    private func stepFields(_ index: Int, _ shown: [Schedules.Step], _ choices: [String]) -> some View {
        let step = shown[index]
        let edit = { (change: (inout Schedules.Step) -> Void) in
            var next = shown
            change(&next[index])
            steps = next
        }
        Picker(
            model.t("schedules.task"),
            selection: Binding(get: { step.task }, set: { value in edit { $0.task = value } })
        ) {
            ForEach(choices, id: \.self) { option in
                Text(Schedules.taskName(model.strings, option, tasks: tasks)).tag(option)
            }
        }
        .pickerStyle(.menu)
        if let key = Schedules.wordsKey(step.task) {
            TextField(model.t(key), text: Binding(get: { step.words }, set: { value in edit { $0.words = value } }))
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel(model.t(key))
        }
        if step.task == "backup" {
            TextField(model.t("schedules.after"), text: Binding(get: { step.after }, set: { value in edit { $0.after = value } }))
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel(model.t("schedules.after"))
        }
        if shown.count > 1 {
            Button(model.t("schedules.remove_step"), role: .destructive) {
                var next = shown
                next.remove(at: index)
                steps = next
            }
        }
    }
}
