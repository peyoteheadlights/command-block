import Core
import PhotosUI
import SwiftUI

/// Game settings: the PC page's fields in its groups, each with its hint
/// (mirrors GameSettings.kt). The seed is shown but locked once the world
/// exists; someone who may not edit settings sees them all read-only. Save
/// sends only what changed, and a refused value (a port in use, a number
/// out of range) says why under the field. Saved while running, it offers
/// Restart now, which asks first.
struct GameSettingsView: View {
    let serverId: String

    @Environment(AppModel.self) private var model
    @State private var restarting = false

    private var content: ContentPages { model.pages(serverId).content }

    var body: some View {
        let content = self.content
        ServerFrame(serverId: serverId, title: model.t("page.game_settings")) { palette in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    page(content, palette)
                }
                .padding(16)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .refreshable { await content.loadGame() }
            .alert(model.t("confirm.restart_title"), isPresented: $restarting) {
                Button(model.t("action.restart")) {
                    let strings = model.strings
                    Task { await content.restart(strings, reload: { await content.loadGame() }) }
                }
                Button(model.t("mobile.common.cancel"), role: .cancel) {}
            } message: {
                Text(model.t("confirm.restart_body"))
            }
        }
        .task(id: serverId) {
            content.said = nil
            await content.loadGame()
            await content.loadIcon()
            await content.loadRules()
        }
    }

    @ViewBuilder
    private func page(_ content: ContentPages, _ palette: Palette) -> some View {
        let canEdit = model.can(Permissions.SETTINGS_GAME)
        let strings = model.strings
        let read = content.game
        Text(model.t("game.intro"))
            .foregroundStyle(palette.textPrimary)
        if !canEdit && model.me != nil {
            Hint(text: model.t("mobile.game.read_only"), palette: palette)
        }
        if let problem = read.problem {
            ProblemCard(problem: problem, palette: palette) { await content.loadGame() }
        }
        if let settings = read.value {
            if !settings.exists {
                EmptyCard(title: model.t("game.no_file"), palette: palette)
            }
            if settings.restartNeeded {
                Text(model.t("game.restart_needed"))
                    .foregroundStyle(palette.tone(.warning))
            } else if settings.running && canEdit {
                Hint(text: model.t("game.after_restart"), palette: palette)
            }
            ForEach(GameForm.groups(settings), id: \.titleKey) { group in
                SectionTitle(text: model.t(group.titleKey), palette: palette)
                CardBox(palette: palette) {
                    ForEach(group.fields) { field in
                        GameField(
                            field: field,
                            value: content.shown(field),
                            locked: GameForm.locked(field, canEdit: canEdit),
                            problem: content.fieldProblems[field.key],
                            palette: palette
                        ) { content.edit(field.key, $0) }
                    }
                }
            }
            if settings.icon != nil {
                SectionTitle(text: model.t("motd.icon"), palette: palette)
                CardBox(palette: palette) {
                    IconCard(content: content, canEdit: canEdit, palette: palette)
                }
            }
            SectionTitle(text: model.t("gamerules.title"), palette: palette)
            Hint(text: model.t("gamerules.hint"), palette: palette)
            CardBox(palette: palette) {
                RulesCard(content: content, canEdit: canEdit, palette: palette)
            }
            if canEdit {
                Button {
                    Task { await content.saveGame(strings) }
                } label: {
                    Text(model.t(content.busy == "game" ? "action.saving" : "game.save"))
                        .frame(minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .tint(palette.accent)
                .disabled(content.busy != nil || !content.gameChanged)
                Hint(text: model.t("game.copy_kept"), palette: palette)
            }
            if canEdit && RawText.allowed(model.me, technical: model.technical, permission: Permissions.SETTINGS_GAME) && settings.exists {
                SectionTitle(text: model.t("game.raw_title"), palette: palette)
                CardBox(palette: palette) {
                    RawEditorView(
                        title: model.t("game.raw_label"),
                        hint: model.t("game.raw_hint", ["file": settings.file]),
                        saved: settings.text,
                        changes: { RawText.propertyChanges(settings.text, $0) },
                        busy: content.busy != nil,
                        palette: palette
                    ) { typed in Task { await content.saveGameText(typed, strings) } }
                }
            }
            SaidRow(said: content.said, palette: palette)
            if content.gameRestart && model.canControl {
                Presses(presses: [
                    Press(title: model.t("action.restart_now"), enabled: content.busy == nil) { restarting = true },
                ], palette: palette)
            }
        } else if read.problem == nil {
            ProgressView().frame(maxWidth: .infinity)
        }
    }
}

/// One setting: a switch, choices, or a box for a number or words.
private struct GameField: View {
    let field: GameSetting
    let value: String
    let locked: Bool
    let problem: String?
    let palette: Palette
    let change: (String) -> Void

    @Environment(AppModel.self) private var model

    var body: some View {
        let strings = model.strings
        let label = GameForm.label(strings, field)
        VStack(alignment: .leading, spacing: 6) {
            switch field.kind {
            case "bool":
                Toggle(label, isOn: Binding(get: { value == "true" }, set: { change($0 ? "true" : "false") }))
                    .disabled(locked)
                    .foregroundStyle(palette.textPrimary)
            case "choice":
                Picker(label, selection: Binding(get: { value }, set: { change($0) })) {
                    // The file's own value stays listed even when it isn't one of the choices.
                    if !field.choices.contains(value) {
                        Text(hiding: value).tag(value)
                    }
                    ForEach(field.choices, id: \.self) { choice in
                        Text(GameForm.choiceLabel(strings, choice)).tag(choice)
                    }
                }
                .disabled(locked)
                .foregroundStyle(palette.textPrimary)
            default:
                Text(label)
                    .foregroundStyle(palette.textPrimary)
                if Motd.keys.contains(field.key) {
                    MotdField(field: field, value: value, locked: locked, palette: palette, change: change)
                } else {
                TextField(label, text: Binding(get: { value }, set: { typed in
                    change(field.maxLength.map { String(typed.prefix($0)) } ?? typed)
                }))
                .textFieldStyle(.roundedBorder)
                .keyboardType(field.kind == "int" || field.kind == "port" ? .numberPad : .default)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .disabled(locked)
                }
            }
            if let problem = problem {
                Text(hiding: problem)
                    .font(.footnote)
                    .foregroundStyle(palette.danger)
            }
            if let fileProblem = GameForm.fileProblem(strings, field) {
                Text(hiding: fileProblem)
                    .font(.footnote)
                    .foregroundStyle(palette.tone(.warning))
            }
            let hint = GameForm.hint(strings, field)
            if !hint.isEmpty {
                Hint(text: hint, palette: palette)
            }
        }
        .frame(minHeight: 44, alignment: .leading)
    }
}

/// The MOTD builder: the words (two lines on Java), a preview on the server
/// list's dark background, and buttons that add Minecraft's colour codes
/// (mirrors MotdField in GameSettings.kt).
private struct MotdField: View {
    let field: GameSetting
    let value: String
    let locked: Bool
    let palette: Palette
    let change: (String) -> Void

    @Environment(AppModel.self) private var model

    var body: some View {
        TextField(model.t("game.motd"), text: Binding(get: { value }, set: { typed in
            let lines = typed.split(separator: "\n", omittingEmptySubsequences: false).prefix(field.lines).joined(separator: "\n")
            change(field.maxLength.map { String(lines.prefix($0)) } ?? lines)
        }), axis: .vertical)
        .lineLimit(1...max(1, field.lines))
        .textFieldStyle(.roundedBorder)
        .autocorrectionDisabled()
        .textInputAutocapitalization(.never)
        .disabled(locked)
        Text(preview)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(Color(red: 0x1D / 255, green: 0x1D / 255, blue: 0x1D / 255), in: RoundedRectangle(cornerRadius: 6))
            .accessibilityLabel(model.t("motd.preview"))
        if !locked {
            Text(model.t("motd.colors"))
                .font(.footnote)
                .foregroundStyle(palette.textSecondary)
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(36)), count: 8), alignment: .leading, spacing: 6) {
                ForEach(Motd.colors.indices, id: \.self) { index in
                    let color = Motd.colors[index]
                    Button {
                        change(value + "\u{00A7}\(color.code)")
                    } label: {
                        Circle().fill(Self.color(color.rgb)).frame(width: 32, height: 32)
                    }
                    .accessibilityLabel(model.t("motd.color_\(color.code)"))
                }
            }
            HStack {
                ForEach(Motd.styles.indices, id: \.self) { index in
                    let style = Motd.styles[index]
                    Button(model.t(style.key)) { change(value + "\u{00A7}\(style.code)") }
                        .buttonStyle(.bordered)
                        .font(.footnote)
                }
            }
        }
    }

    private var preview: AttributedString {
        var out = AttributedString()
        for piece in Motd.pieces(value) {
            var part = AttributedString(piece.text)
            part.foregroundColor = Self.color(piece.color)
            var font = Font.body
            if piece.bold { font = font.bold() }
            if piece.italic { font = font.italic() }
            part.font = font
            if piece.underline { part.underlineStyle = .single }
            if piece.strike { part.strikethroughStyle = .single }
            out += part
        }
        return out
    }

    static func color(_ rgb: Int) -> Color {
        Color(red: Double((rgb >> 16) & 0xFF) / 255, green: Double((rgb >> 8) & 0xFF) / 255, blue: Double(rgb & 0xFF) / 255)
    }
}

/// The server icon: what it is now, Pick a picture (any picture: cut to a
/// square from its middle and made 64 x 64 PNG here), and Remove.
private struct IconCard: View {
    let content: ContentPages
    let canEdit: Bool
    let palette: Palette

    @Environment(AppModel.self) private var model
    @State private var picked: PhotosPickerItem?
    @State private var unreadable = false

    var body: some View {
        let strings = model.strings
        let shown = content.icon.flatMap { Data(base64Encoded: $0) }.flatMap { UIImage(data: $0) }
        HStack(spacing: 12) {
            if let shown {
                Image(uiImage: shown)
                    .interpolation(.none)
                    .resizable()
                    .frame(width: 64, height: 64)
                    .accessibilityLabel(model.t("motd.icon"))
            }
            Hint(text: model.t("motd.icon_hint"), palette: palette)
        }
        if unreadable {
            Text(model.t("motd.icon_unreadable"))
                .font(.footnote)
                .foregroundStyle(palette.danger)
        }
        if canEdit {
            HStack {
                PhotosPicker(model.t("motd.icon_pick"), selection: $picked, matching: .images)
                    .disabled(content.busy != nil)
                if shown != nil {
                    Button(model.t("motd.icon_remove")) { Task { await content.removeIcon(strings) } }
                        .disabled(content.busy != nil)
                }
            }
            .onChange(of: picked) { _, item in
                guard let item else { return }
                Task {
                    let data = try? await item.loadTransferable(type: Data.self)
                    let png = data.flatMap { UIImage(data: $0) }.flatMap(Self.iconPng)
                    unreadable = png == nil
                    picked = nil
                    if let png { await content.saveIcon(png, strings) }
                }
            }
        }
    }

    /// A picture cut to a square from its middle, made 64 x 64, as base64 PNG.
    static func iconPng(_ picture: UIImage) -> String? {
        let side = min(picture.size.width, picture.size.height)
        guard side > 0 else { return nil }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let small = UIGraphicsImageRenderer(size: CGSize(width: 64, height: 64), format: format).image { _ in
            let scale = 64 / side
            let width = picture.size.width * scale
            let height = picture.size.height * scale
            picture.draw(in: CGRect(x: (64 - width) / 2, y: (64 - height) / 2, width: width, height: height))
        }
        return small.pngData()?.base64EncodedString()
    }
}

/// Game rules: read from the running server, changed at once (no restart).
struct RulesCard: View {
    let content: ContentPages
    let canEdit: Bool
    let palette: Palette

    @Environment(AppModel.self) private var model

    var body: some View {
        let strings = model.strings
        let read = content.rules
        VStack(alignment: .leading, spacing: 8) {
            if let problem = read.problem {
                ProblemCard(problem: problem, palette: palette) { await content.loadRules() }
            } else if let data = read.value {
                if !data.running {
                    Hint(text: model.t("gamerules.stopped"), palette: palette)
                } else if data.rules.isEmpty {
                    Hint(text: model.t("gamerules.none"), palette: palette)
                }
            } else {
                Hint(text: model.t("gamerules.reading"), palette: palette)
            }
            if let said = content.rulesSaid {
                HStack {
                    Text(said).foregroundStyle(palette.textSecondary)
                    Spacer()
                    if canEdit && !content.rulesBefore.isEmpty {
                        Button(model.t("changes.undo")) {
                            let before = content.rulesBefore
                            Task { await content.saveRules(strings, changed: before) }
                        }
                    }
                }
            }
            if !content.rulesSkipped.isEmpty {
                Hint(text: model.t("gamerules.skipped", ["names": content.rulesSkipped.joined(separator: ", ")]), palette: palette)
            }
            let rules = read.value?.rules ?? []
            if canEdit && !rules.isEmpty {
                Menu(model.t("gamerules.presets")) {
                    ForEach(read.value?.presets ?? [], id: \.self) { preset in
                        Button(model.t("presets.\(preset)")) {
                            Task { await content.saveRules(strings, changed: [:], preset: preset) }
                        }
                    }
                }
                .disabled(content.busy != nil)
            }
            ForEach(rules) { rule in
                row(rule, content.ruleEdits[rule.name] ?? rule.value)
            }
            if canEdit && !rules.isEmpty {
                Button {
                    Task { await content.saveRules(strings) }
                } label: {
                    Text(model.t(content.busy == "rules" ? "action.saving" : "gamerules.save"))
                }
                .buttonStyle(.borderedProminent)
                .tint(palette.accent)
                .disabled(content.busy != nil || content.ruleEdits.isEmpty)
            }
        }
    }

    @ViewBuilder
    private func row(_ rule: GameRule, _ value: JSONValue) -> some View {
        switch value {
        case .bool(let on):
            Toggle(rule.name, isOn: Binding(get: { on }, set: { content.editRule(rule.name, .bool($0)) }))
                .disabled(!canEdit)
        case .number(let number):
            HStack {
                Text(rule.name)
                Spacer()
                TextField(rule.name, text: Binding(
                    get: { String(Int(number)) },
                    set: { typed in if let n = Int(typed) { content.editRule(rule.name, .number(Double(n))) } }
                ))
                .keyboardType(.numberPad)
                .multilineTextAlignment(.trailing)
                .frame(maxWidth: 110)
                .disabled(!canEdit)
            }
        case .string(let said):
            // A value the app can't edit safely: shown as the server said it.
            HStack {
                Text(rule.name)
                Spacer()
                Text(hiding: said).foregroundStyle(palette.textSecondary)
            }
        default:
            EmptyView()
        }
    }
}
