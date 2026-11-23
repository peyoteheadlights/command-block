import Core
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// A step waiting for "yes".
private struct ContentConfirm: Identifiable {
    let title: String
    let body: String
    let confirm: String
    var danger = true
    let run: () -> Void

    var id: String { title }
}

/// Which file the Files picker is choosing.
private enum Picking {
    case jar, modpack, pack, players, resourcePack
}

/// "Who needs it?" for one mod; `ready`: the server is stopped, so Players (a move) is offered.
private struct SideAsk: Identifiable {
    let mod: Mod
    let ready: Bool

    var id: String { mod.filename }
}

/// Mods, Plugins or Packs, named for the server type (mirrors
/// ContentScreen.kt). Java servers: what's installed with on/off, Update,
/// Versions (Roll back) and Remove, Update all, the missing dependencies,
/// Modrinth search, a .jar or modpack from the phone, and the change
/// history. They change only while the server is stopped, so while it runs
/// the screen offers Stop, which asks first. Bedrock servers: their packs,
/// which change while it runs too and then offer Restart now.
struct ContentView: View {
    let serverId: String

    @Environment(AppModel.self) private var model
    @State private var asking: ContentConfirm?
    @State private var picking: Picking?
    @State private var choosingSide: SideAsk?
    @State private var query = ""
    @State private var packQuery = ""
    @State private var web: WebLink?

    private var content: ContentPages { model.pages(serverId).content }
    private var row: ServerRow? { model.servers.first(where: { $0.id == serverId }) }

    var body: some View {
        @Bindable var content = self.content
        let kind = Content.kind(row)
        ServerFrame(serverId: serverId, title: model.t((kind ?? .mods).titleKey)) { palette in
            ScrollView {
                TimelineView(.periodic(from: .now, by: 30)) { context in
                    VStack(alignment: .leading, spacing: 12) {
                        switch kind {
                        case nil:
                            ProgressView().frame(maxWidth: .infinity)
                        case .some(ContentKind.none):
                            EmptyCard(title: model.t("mobile.content.none_for_type"), palette: palette)
                        case .some(.packs):
                            packs(content, palette)
                        case .some(let kind):
                            mods(content, kind, palette, now: context.date.timeIntervalSince1970)
                        }
                    }
                    .padding(16)
                    .frame(maxWidth: 640)
                    .frame(maxWidth: .infinity)
                }
                .sheet(item: $web) { page in
                    SafariView(url: page.url).ignoresSafeArea()
                }
            }
            .refreshable { await reload(kind) }
            .alert(
                pending(content)?.title ?? "",
                isPresented: Binding(get: { pending(content) != nil }, set: { shown in
                    if !shown {
                        asking = nil
                        content.removing = nil
                        content.updating = nil
                    }
                }),
                presenting: pending(content),
                actions: { ask in
                    Button(ask.confirm, role: ask.danger ? .destructive : nil, action: ask.run)
                    Button(model.t("mobile.common.cancel"), role: .cancel) {}
                },
                message: { ask in Text(verbatim: ask.body) }
            )
            .confirmationDialog(
                model.t("sides.who_title", ["name": choosingSide.map { $0.mod.name.isEmpty ? $0.mod.filename : $0.mod.name } ?? ""]),
                isPresented: Binding(get: { choosingSide != nil }, set: { if !$0 { choosingSide = nil } }),
                titleVisibility: .visible,
                presenting: choosingSide
            ) { ask in
                // Players moves the jar out of the mods folder, so only while the server is stopped.
                ForEach(Sides.picks(ask.mod).filter { $0 != "players" || ask.ready }, id: \.self) { side in
                    Button(model.t("sides.mark_" + side)) {
                        let strings = model.strings
                        let name = ask.mod.name.isEmpty ? ask.mod.filename : ask.mod.name
                        Task { await content.setSide(ask.mod.filename, name: name, side: side, strings) }
                    }
                }
                Button(model.t("mobile.common.cancel"), role: .cancel) {}
            }
            .sheet(item: $content.versions) { ask in
                VersionsSheet(content: content, ask: ask, palette: palette)
            }
            .fileImporter(
                isPresented: Binding(get: { picking != nil }, set: { if !$0 { picking = nil } }),
                allowedContentTypes: [.item]
            ) { result in
                let what = picking
                picking = nil
                if case .success(let url) = result, let what = what {
                    picked(url, what, kind ?? .mods)
                }
            }
        }
        .sheet(item: $content.sheet, onDismiss: { content.sheetClosed() }) { sheet in
            NavigationStack {
                sheetBody(content, sheet)
            }
        }
        .sheet(item: $content.sharing, onDismiss: { content.finishSharing() }) { done in
            ShareSheet(file: done.file)
        }
        .task(id: serverId) {
            content.said = nil
            await reload(kind)
        }
    }

    private func reload(_ kind: ContentKind?) async {
        switch kind {
        case .packs: await content.loadAddons()
        case .mods, .plugins: await content.loadMods()
        default: break
        }
    }

    /// The question showing now: one asked here, a Remove or an Update.
    private func pending(_ content: ContentPages) -> ContentConfirm? {
        if let asking = asking { return asking }
        let strings = model.strings
        if let ask = content.removing {
            let needed = ask.impact.dependents.map { d in
                strings.t("mods.needed_by", ["name": d.name, "requires": d.requires ?? strings.t("value.unknown")])
            }
            var lines = needed.isEmpty ? [strings.t("mods.not_needed")] : needed
            if let warning = ask.impact.warning { lines.append(warning) }
            lines.append(strings.t("mods.remove_kept"))
            return ContentConfirm(
                title: strings.t("mods.remove_title", ["name": ask.mod.name]),
                body: lines.joined(separator: "\n"),
                confirm: strings.t("mods.remove")
            ) {
                Task { await content.remove(ask, strings) }
            }
        }
        if let mod = content.updating {
            let body = Content.change(strings, mod.version, mod.updateAvailable?.latestVersion) + "\n" + strings.t("mods.update_archived")
            return ContentConfirm(
                title: strings.t("mods.update_title", ["name": mod.name]),
                body: body,
                confirm: strings.t("mods.update"),
                danger: false
            ) {
                Task { await content.update(mod, strings) }
            }
        }
        return nil
    }

    // MARK: - Doing the same job

    /// The same mod twice, or mods that do the same job: which one to turn
    /// off when the facts say. Turn off has Undo like the mod's own row.
    @ViewBuilder
    private func sameJobCard(_ content: ContentPages, _ list: ModList, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        let manage = model.can(Permissions.MODS_MANAGE)
        SectionTitle(text: model.t("same.title"), palette: palette)
        CardBox(palette: palette) {
            ForEach(list.overlaps) { overlap in
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: overlap.say).foregroundStyle(palette.textPrimary)
                    Hint(text: overlap.why, palette: palette)
                    if manage {
                        ForEach(SameJob.turnOff(overlap)) { row in
                            if let mod = list.mods.first(where: { $0.filename == row.filename && $0.enabled }) {
                                Text(verbatim: "\(row.name) \(row.version)").foregroundStyle(palette.textPrimary)
                                Presses(presses: [
                                    Press(title: model.t("mods.turn_off"), enabled: ready) {
                                        Task { await content.setEnabled(mod, false, strings) }
                                    },
                                ], palette: palette)
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - Linked modpack

    /// The modpack this server came from: Modpack update and Unlink.
    @ViewBuilder
    private func linkedPackCard(_ content: ContentPages, _ pack: LinkedModpack, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        SectionTitle(text: model.t("linked.title"), palette: palette)
        CardBox(palette: palette) {
            ForEach(LinkedPacks.lines(strings, pack), id: \.self) { line in
                Text(verbatim: line).foregroundStyle(palette.textPrimary)
            }
            if model.can(Permissions.MODS_MANAGE) {
                Presses(presses: (pack.update != nil && model.can(Permissions.SETTINGS_SERVER) ? [
                    Press(title: model.t("linked.update"), enabled: ready) {
                        Task { await content.previewPackUpdate(pack.update?.versionId, strings) }
                    },
                ] : []) + [
                    Press(title: model.t("linked.unlink"), danger: true, enabled: ready) {
                        asking = ContentConfirm(
                            title: strings.t("linked.unlink_title", ["name": pack.name]),
                            body: strings.t("linked.unlink_body"),
                            confirm: strings.t("linked.unlink")
                        ) {
                            Task { await content.unlinkPack(strings) }
                        }
                    },
                ], palette: palette)
            }
        }
    }

    // MARK: - Make a modpack

    /// Make a modpack from this server, then share it through the share
    /// sheet or have the Discord bot post it.
    @ViewBuilder
    private func makeModpackCard(_ content: ContentPages, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        SectionTitle(text: model.t("make.title"), palette: palette)
        CardBox(palette: palette) {
            Text(verbatim: model.t("make.intro")).foregroundStyle(palette.textSecondary)
            if let made = content.made {
                ForEach(MadeModpacks.lines(strings, made), id: \.self) { line in
                    Text(verbatim: line).foregroundStyle(palette.textPrimary)
                }
            }
            Presses(presses: [
                Press(title: model.t("make.start"), enabled: ready) {
                    Task { await content.askMakeModpack(strings) }
                },
            ] + (content.made == nil ? [] : [
                Press(title: model.t("make.share"), enabled: ready) {
                    Task { await content.shareModpack(strings) }
                },
            ]) + (content.made?.discord == true ? [
                Press(title: model.t("make.discord"), enabled: ready) {
                    Task { await content.postModpack(strings) }
                },
            ] : []), palette: palette)
            if content.made != nil {
                Text(verbatim: model.t("make.guide_title")).foregroundStyle(palette.textPrimary)
                Text(verbatim: model.t("make.guide")).foregroundStyle(palette.textSecondary)
            }
        }
    }

    // MARK: - Download link

    /// The download link (on, off, New link, Copy) and the server resource pack.
    @ViewBuilder
    private func downloadLinkCard(_ content: ContentPages, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        let owner = model.can(Permissions.PC_SETTINGS)
        let game = model.can(Permissions.SETTINGS_GAME)
        SectionTitle(text: model.t("link.title"), palette: palette)
        CardBox(palette: palette) {
            Text(verbatim: model.t("link.intro")).foregroundStyle(palette.textSecondary)
            if let link = content.link {
                ForEach(DownloadLinks.lines(strings, link, owner: owner), id: \.self) { line in
                    Text(verbatim: line).foregroundStyle(palette.textPrimary)
                }
                Presses(presses: [
                    Press(title: model.t(link.on ? "link.turn_off" : "link.turn_on"), enabled: ready && owner && (link.on || link.whyNot == nil)) {
                        Task { await content.switchLink(!link.on, strings) }
                    },
                ] + (link.on && owner ? [
                    Press(title: model.t("link.new"), enabled: ready) {
                        Task { await content.newLink(strings) }
                    },
                ] : []) + (link.modpackUrl.map { url in [
                    Press(title: model.t("link.copy"), enabled: true) {
                        UIPasteboard.general.string = url
                        content.said = Said(text: strings.t("link.copied"), good: true)
                    },
                ] } ?? []), palette: palette)
                Text(verbatim: model.t("pack.title")).foregroundStyle(palette.textPrimary)
                Text(verbatim: model.t("pack.intro")).foregroundStyle(palette.textSecondary)
                ForEach(DownloadLinks.packLines(strings, link), id: \.self) { line in
                    Text(verbatim: line).foregroundStyle(palette.textPrimary)
                }
                if let pack = link.resourcePack {
                    Toggle(model.t("pack.required"), isOn: Binding(
                        get: { pack.required },
                        set: { on in Task { await content.setPackRequired(on, strings) } }
                    ))
                    .disabled(!ready || !game)
                }
                Presses(presses: [
                    Press(title: model.t("pack.pick"), enabled: ready && game) { picking = .resourcePack },
                    Press(title: model.t("pack.use_link"), enabled: ready && game) { content.askingPackLink = true },
                ] + (link.resourcePack == nil ? [] : [
                    Press(title: model.t("pack.remove"), enabled: ready && game) {
                        Task { await content.removePack(strings) }
                    },
                ]), palette: palette)
                Text(verbatim: model.t("pack.tailscale_only")).foregroundStyle(palette.textSecondary)
            }
        }
        .task(id: serverId) { await content.loadLink() }
        .sheet(isPresented: Binding(get: { content.askingPackLink }, set: { content.askingPackLink = $0 })) {
            NavigationStack { PackLinkSheet(content: content) }
        }
    }

    // MARK: - The anti-cheat picker

    /// Free from Modrinth, paid from the seller's page, or your own jar.
    @ViewBuilder
    private func antiCheatCard(_ content: ContentPages, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        Group {
            if let picker = content.antiCheat {
                let ownJar = picker.mayInstall && picker.mayAddOwnJar
                SectionTitle(text: model.t("anticheat.title"), palette: palette)
                CardBox(palette: palette) {
                    Text(verbatim: model.t("anticheat.intro")).foregroundStyle(palette.textSecondary)
                    Text(verbatim: AntiCheats.status(strings, picker)).foregroundStyle(palette.textPrimary)
                    if picker.supported {
                        Text(verbatim: model.t("anticheat.cpu")).foregroundStyle(palette.textSecondary)
                        if !picker.mayInstall {
                            Text(verbatim: model.t("anticheat.no_install")).foregroundStyle(palette.textSecondary)
                        }
                        Text(verbatim: model.t("anticheat.free")).foregroundStyle(palette.textPrimary)
                        ForEach(picker.free) { item in
                            ForEach(AntiCheats.free(strings, item), id: \.self) { line in
                                Text(verbatim: line).foregroundStyle(palette.textPrimary)
                            }
                            if !item.on && picker.mayInstall {
                                Presses(presses: [
                                    Press(title: strings.t("anticheat.add", ["name": item.name]), enabled: ready) {
                                        Task { await content.openProject(slug: item.project) }
                                    },
                                ], palette: palette)
                            }
                        }
                        if !picker.paid.isEmpty {
                            Text(verbatim: model.t("anticheat.paid")).foregroundStyle(palette.textPrimary)
                            Text(verbatim: model.t("anticheat.buy_note")).foregroundStyle(palette.textSecondary)
                            ForEach(picker.paid) { item in
                                ForEach(AntiCheats.paid(strings, item), id: \.self) { line in
                                    Text(verbatim: line).foregroundStyle(palette.textPrimary)
                                }
                                Presses(presses: [
                                    Press(title: model.t("anticheat.seller")) {
                                        if let url = URL(string: item.link) { web = WebLink(url: url) }
                                    },
                                ] + (item.setup.flatMap(URL.init(string:)).map { url in [
                                    Press(title: model.t("anticheat.setup")) { web = WebLink(url: url) },
                                ] } ?? []) + (ownJar && !item.on ? [
                                    Press(title: model.t("anticheat.from_file"), enabled: ready) { picking = .jar },
                                ] : []), palette: palette)
                            }
                            Text(verbatim: model.t("anticheat.prices_note")).foregroundStyle(palette.textSecondary)
                        }
                        Text(verbatim: model.t("anticheat.custom")).foregroundStyle(palette.textPrimary)
                        Text(verbatim: model.t("anticheat.custom_about")).foregroundStyle(palette.textSecondary)
                        if ownJar {
                            Presses(presses: [
                                Press(title: model.t("anticheat.from_file"), enabled: ready) { picking = .jar },
                            ], palette: palette)
                        } else {
                            Text(verbatim: model.t("anticheat.no_own_jar")).foregroundStyle(palette.textSecondary)
                        }
                    }
                }
            }
        }
        .task(id: serverId) { await content.loadAntiCheat() }
    }

    // MARK: - Put it on Modrinth

    /// Sign in with Modrinth, upload the modpack and own jars, the Server Project helper.
    @ViewBuilder
    private func modrinthCard(_ content: ContentPages, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        let owner = model.can(Permissions.PC_SETTINGS)
        SectionTitle(text: model.t("modrinth.title"), palette: palette)
        CardBox(palette: palette) {
            Text(verbatim: model.t("modrinth.intro")).foregroundStyle(palette.textSecondary)
            if let share = content.modrinth {
                let account = share.account
                let signedIn = account?.signedIn == true
                ForEach(ModrinthCards.lines(strings, share, owner: owner), id: \.self) { line in
                    Text(verbatim: line).foregroundStyle(palette.textPrimary)
                }
                let waiting = share.ownJars.first(where: { $0.url == nil })
                let upload = share.made.map { strings.t("modrinth.upload_named", ["name": $0.name, "version": $0.version]) } ?? model.t("modrinth.upload")
                Presses(presses: (owner && account?.available == true && !signedIn ? [
                    Press(title: model.t("modrinth.sign_in"), enabled: ready) {
                        Task { await content.signInModrinth(strings) }
                    },
                ] : []) + [
                    Press(title: upload, enabled: ready && signedIn && share.made != nil) {
                        if share.project != nil {
                            Task { await content.uploadToModrinth("", "", strings) }
                        } else {
                            content.askingModrinth = ModrinthAsk(jar: nil, name: share.made?.name ?? "")
                        }
                    },
                ] + (waiting.map { jar in [
                    Press(title: model.t("modrinth.own_jar"), enabled: ready && signedIn) {
                        content.askingModrinth = ModrinthAsk(jar: jar, name: jar.name)
                    },
                ] } ?? []) + (owner && signedIn ? [
                    Press(title: model.t("modrinth.sign_out"), enabled: ready) {
                        Task { await content.signOutModrinth(strings) }
                    },
                ] : []), palette: palette)
                Text(verbatim: model.t("modrinth.review_note")).foregroundStyle(palette.textSecondary)
                Text(verbatim: model.t("modrinth.server_title")).foregroundStyle(palette.textPrimary)
                Text(verbatim: model.t("modrinth.server_intro")).foregroundStyle(palette.textSecondary)
                ForEach(ModrinthCards.serverLines(strings, share), id: \.self) { line in
                    Text(verbatim: line).foregroundStyle(palette.textPrimary)
                }
                if share.address != nil {
                    Text(verbatim: ModrinthCards.steps(strings, share)).foregroundStyle(palette.textSecondary)
                    Presses(presses: [
                        Press(title: model.t("modrinth.server_paste"), enabled: ready) { content.askingServerLink = true },
                    ], palette: palette)
                }
            }
        }
        .task(id: serverId) { await content.loadModrinth() }
        .task(id: model.modrinthReturn) {
            guard let url = model.modrinthReturn else { return }
            model.modrinthReturn = nil
            await content.finishModrinth(url, strings)
        }
        .sheet(item: Binding(get: { content.askingModrinth }, set: { content.askingModrinth = $0 })) { ask in
            NavigationStack { ModrinthProjectSheet(content: content, ask: ask) }
        }
        .sheet(isPresented: Binding(get: { content.askingServerLink }, set: { content.askingServerLink = $0 })) {
            NavigationStack { ServerProjectSheet(content: content) }
        }
    }

    // MARK: - Still fit?

    /// After a mod or version change: the ones to change (one Update all), the
    /// ones with no version that fits (Turn off) and the ones nothing said about.
    @ViewBuilder
    private func stillFitCard(_ content: ContentPages, _ list: ModList, _ fit: ModFit, _ kind: ContentKind, ready: Bool, _ palette: Palette) -> some View {
        let strings = model.strings
        let manage = model.can(Permissions.MODS_MANAGE)
        SectionTitle(text: model.t("fit.title"), palette: palette)
        CardBox(palette: palette) {
            Text(verbatim: StillFit.lead(strings, fit, plugins: kind == .plugins))
                .foregroundStyle(palette.textPrimary)
            ForEach(fit.needsChange) { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: row.name).foregroundStyle(palette.textPrimary)
                    Hint(text: Content.change(strings, row.from, row.to) + "\n" + row.why, palette: palette)
                }
            }
            if !fit.noFit.isEmpty {
                Text(verbatim: model.t("fit.no_fit")).foregroundStyle(palette.textPrimary)
            }
            ForEach(fit.noFit) { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: row.name).foregroundStyle(palette.textPrimary)
                    Hint(text: row.why, palette: palette)
                    if manage, let mod = list.mods.first(where: { $0.filename == row.filename && $0.enabled }) {
                        Presses(presses: [
                            Press(title: model.t("mods.turn_off"), enabled: ready) {
                                Task { await content.setEnabled(mod, false, strings) }
                            },
                        ], palette: palette)
                    }
                }
            }
            if !fit.cantTell.isEmpty {
                Text(verbatim: model.t("fit.cant_tell")).foregroundStyle(palette.textPrimary)
            }
            ForEach(fit.cantTell) { row in
                VStack(alignment: .leading, spacing: 2) {
                    Text(verbatim: row.name).foregroundStyle(palette.textPrimary)
                    Hint(text: row.why, palette: palette)
                }
            }
            if manage {
                Presses(presses: (fit.needsChange.isEmpty ? [] : [
                    Press(title: model.t("fit.change"), enabled: ready) { content.openFit(fit) },
                ]) + [
                    Press(title: model.t("fit.hide"), enabled: true) { Task { await content.hideFit(strings) } },
                ], palette: palette)
            }
        }
    }

    // MARK: - Mods and plugins

    @ViewBuilder
    private func mods(_ content: ContentPages, _ kind: ContentKind, _ palette: Palette, now: Double) -> some View {
        let read = content.mods
        let state = content.serverState
        let changes = Content.changes(canManage: model.can(Permissions.MODS_MANAGE), serverState: state)
        let idle = content.busy == nil
        let strings = model.strings

        changesCard(content, changes, state, palette)
        taskNote(content, palette)
        SaidRow(said: content.said, palette: palette)

        if let problem = read.problem {
            Hint(text: model.t("mobile.content.unknown"), palette: palette)
            ProblemCard(problem: problem, palette: palette) { await content.loadMods() }
        }
        if let list = read.value {
            if list.storedSeparately {
                Hint(text: model.t("mods.stored_separately"), palette: palette)
            }
            if let pack = list.modpack {
                linkedPackCard(content, pack, ready: changes == .ready && idle, palette)
            }
            if model.can(Permissions.MODS_MANAGE) {
                makeModpackCard(content, ready: idle, palette)
                downloadLinkCard(content, ready: idle, palette)
                modrinthCard(content, ready: idle, palette)
            }
            antiCheatCard(content, ready: idle, palette)
            if let fit = list.fit {
                stillFitCard(content, list, fit, kind, ready: changes == .ready && idle, palette)
            }
            if !list.overlaps.isEmpty {
                sameJobCard(content, list, ready: changes == .ready && idle, palette)
            }
            if changes == .ready {
                Presses(presses: [
                    Press(
                        title: model.t(UpdateAll.offered(list) ? "mobile.content.update_all" : "mobile.content.check_updates"),
                        enabled: idle && !list.mods.isEmpty
                    ) {
                        Task { await content.openUpdateAll() }
                    },
                ], palette: palette)
            }

            let missing = Content.missing(content.dependencies)
            // Unread is said as such, never shown as "nothing missing".
            if content.dependencies == nil {
                Hint(text: model.t("mobile.content.missing_unknown"), palette: palette)
            }
            if !missing.isEmpty {
                SectionTitle(text: model.t("mobile.content.missing_title"), palette: palette)
                CardBox(palette: palette) {
                    ForEach(missing, id: \.modId) { dep in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(verbatim: dep.name.isEmpty ? dep.modId : dep.name)
                                .foregroundStyle(palette.textPrimary)
                            if let why = dep.reason ?? dep.rangeText {
                                Hint(text: why, palette: palette)
                            }
                        }
                    }
                    if changes == .ready {
                        Presses(presses: [
                            Press(title: model.t("mobile.content.install_missing"), enabled: idle) {
                                Task { await content.planMissing() }
                            },
                        ], palette: palette)
                    }
                }
            }

            SectionTitle(
                text: kind == .plugins ? model.t("page.plugins") + " · \(list.mods.count)" : strings.tn("mods.installed", count: list.mods.count),
                palette: palette
            )
            if list.mods.isEmpty {
                EmptyCard(title: model.t("mods.none"), hint: changes == .ready ? model.t("mods.none_hint") : nil, palette: palette)
            }
            ForEach(list.mods) { mod in
                modCard(content, mod, changes, palette)
            }
            if !list.oddSetups.isEmpty {
                CardBox(palette: palette) {
                    Text(verbatim: model.t("sides.odd_title")).foregroundStyle(palette.tone(.warning))
                    ForEach(list.oddSetups, id: \.text) { odd in
                        Text(verbatim: "• " + odd.text).font(.footnote).foregroundStyle(palette.tone(.warning))
                    }
                }
            }
            if kind != .packs {
                playersOnly(content, list.playerOnly, list.playerOnlyOptions, kind, changes, palette)
            }

            if changes == .ready {
                SectionTitle(text: model.t("mobile.content.search_title"), palette: palette)
                searchCard(content, list.minecraftVersion, palette)
                found(content, palette)
                addFiles(content, palette)
            }

            SectionTitle(text: model.t("mods.change_history"), palette: palette)
            if let problem = content.history.problem {
                Hint(text: problem.message(strings), palette: palette)
            } else if let rows = content.history.value?.history {
                if rows.isEmpty {
                    EmptyCard(title: model.t("mods.no_history"), palette: palette)
                }
                ForEach(Array(rows.prefix(50).enumerated()), id: \.offset) { _, row in
                    historyCard(row, palette, now: now)
                }
            }
        } else if read.problem == nil {
            ProgressView().frame(maxWidth: .infinity)
        }
    }

    /// Why changes are off now, with Stop right here while the server runs.
    @ViewBuilder
    private func changesCard(_ content: ContentPages, _ changes: Changes, _ state: String?, _ palette: Palette) -> some View {
        switch changes {
        case .ready:
            EmptyView()
        case .notAllowed:
            Hint(text: model.t("mobile.content.not_allowed"), palette: palette)
        case .unknown:
            Text(model.t("mobile.content.state_unknown"))
                .foregroundStyle(palette.tone(.warning))
        case .stopFirst:
            CardBox(palette: palette) {
                Text(model.t("mobile.content.stop_first"))
                    .foregroundStyle(palette.textPrimary)
                if Content.canStopHere(state, canControl: model.canControl) {
                    let strings = model.strings
                    Presses(presses: [
                        Press(title: model.t(content.busy == "stop" ? "action.stopping" : "action.stop"), danger: true, enabled: content.busy == nil) {
                            asking = ContentConfirm(
                                title: strings.t("mobile.content.stop_confirm_title"),
                                body: strings.t("mobile.content.stop_confirm_body"),
                                confirm: strings.t("action.stop")
                            ) {
                                Task { await content.stop(strings) }
                            }
                        },
                    ], palette: palette)
                } else {
                    Hint(text: model.t("mods.running_notice", ["state": model.t(Display.state(state).key)]), palette: palette)
                }
            }
        }
    }

    /// A running install, Update all or modpack import, with the step the live feed last said.
    @ViewBuilder
    private func taskNote(_ content: ContentPages, _ palette: Palette) -> some View {
        if let busy = content.busy,
           busy == "update_all" || busy == "modpack" || busy == "missing" || busy.hasPrefix("install:") || busy.hasPrefix("update:") {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                if let step = UpdateAll.progress(content.task, serverId: serverId) {
                    Text(model.t("mobile.content.update_all_working", ["step": step]))
                } else {
                    Text(model.t("mobile.content.working"))
                }
            }
            .foregroundStyle(palette.textPrimary)
        }
    }

    private func modCard(_ content: ContentPages, _ mod: Mod, _ changes: Changes, _ palette: Palette) -> some View {
        let strings = model.strings
        let status = Content.status(mod)
        let idle = content.busy == nil
        let ready = changes == .ready && idle
        var presses: [Press] = []
        if changes != .notAllowed {
            presses.append(Press(title: model.t(mod.enabled ? "mods.turn_off" : "mods.turn_on"), enabled: ready) {
                Task { await content.setEnabled(mod, !mod.enabled, strings) }
            })
            if mod.updateAvailable != nil {
                presses.append(Press(title: model.t("mods.update"), enabled: ready) { content.updating = mod })
            }
            if !mod.modId.isEmpty {
                presses.append(Press(title: model.t("mobile.content.versions"), enabled: idle) {
                    Task { await content.showVersions(mod) }
                })
            }
            presses.append(Press(title: model.t("mods.remove"), danger: true, enabled: ready) {
                Task { await content.askRemove(mod, strings) }
            })
            if !Sides.picks(mod).isEmpty {
                presses.append(Press(title: model.t("sides.who"), enabled: idle) {
                    choosingSide = SideAsk(mod: mod, ready: ready)
                })
            }
        }
        return CardBox(palette: palette) {
            Text(verbatim: mod.name.isEmpty ? mod.filename : mod.name)
                .font(.headline)
                .foregroundStyle(palette.textPrimary)
            Text(verbatim: (mod.version.isEmpty ? model.t("value.unknown") : mod.version) + " · " + model.t(status.key))
                .font(.footnote)
                .foregroundStyle(status.tone == .neutral ? palette.textSecondary : palette.tone(status.tone))
            ForEach(Content.problems(mod), id: \.self) { problem in
                Text(verbatim: problem)
                    .font(.footnote)
                    .foregroundStyle(palette.tone(mod.status == "error" ? .danger : .warning))
            }
            if let newer = Content.newer(strings, mod) {
                Text(verbatim: newer)
                    .font(.footnote)
                    .foregroundStyle(palette.accentInk)
            }
            if mod.addedByYou == true {
                Text(verbatim: model.t("linked.added_by_you"))
                    .font(.footnote)
                    .foregroundStyle(palette.textSecondary)
            }
            if let side = Sides.words(strings, mod) {
                Text(verbatim: side)
                    .font(.footnote)
                    .foregroundStyle(palette.textSecondary)
            }
            if model.technical {
                Hint(text: Content.technical(strings, mod), palette: palette)
            }
            Presses(presses: presses, palette: palette)
        }
    }

    /// The player-only folder: mods, resource packs and shader packs the server never loads.
    @ViewBuilder
    private func playersOnly(
        _ content: ContentPages, _ files: [PlayerFile], _ options: PlayerOnlyOptions, _ kind: ContentKind, _ changes: Changes, _ palette: Palette
    ) -> some View {
        let manage = changes != .notAllowed
        let idle = content.busy == nil
        if manage || !files.isEmpty {
            SectionTitle(text: model.t("sides.players_title"), palette: palette)
            CardBox(palette: palette) {
                Hint(text: model.t("sides.players_intro"), palette: palette)
                if files.isEmpty {
                    Text(verbatim: model.t("sides.players_none")).foregroundStyle(palette.textSecondary)
                }
                ForEach(files) { file in
                    let name = file.name.isEmpty ? file.filename : file.name
                    Text(verbatim: name).font(.headline).foregroundStyle(palette.textPrimary)
                    Text(verbatim: [file.version, model.t(file.kind == "mod" ? "sides.kind_mod" : "sides.kind_pack"), file.filename]
                        .filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.footnote)
                        .foregroundStyle(palette.textSecondary)
                    if file.changed {
                        Text(verbatim: model.t("sides.changed")).font(.footnote).foregroundStyle(palette.tone(.warning))
                    }
                    if file.missing {
                        Text(verbatim: model.t("sides.missing")).font(.footnote).foregroundStyle(palette.tone(.warning))
                        if manage && file.site == "modrinth" {
                            Presses(presses: [
                                Press(title: model.t("sides.get_again"), enabled: idle) {
                                    let strings = model.strings
                                    Task { await content.fetchForPlayers(file.filename, strings) }
                                },
                            ], palette: palette)
                        }
                    } else if manage {
                        Presses(presses: (file.kind == "mod" && kind == .mods ? [
                            Press(title: model.t("sides.move_to_server"), enabled: changes == .ready && idle) {
                                let strings = model.strings
                                Task { await content.setSide(file.filename, name: name, side: "both", strings) }
                            },
                        ] : []) + [
                            Press(title: model.t("mods.remove"), danger: true, enabled: idle) {
                                let strings = model.strings
                                asking = ContentConfirm(
                                    title: strings.t("sides.remove_title", ["name": name]),
                                    body: strings.t("sides.remove_body"),
                                    confirm: strings.t("mods.remove")
                                ) {
                                    Task { await content.removeForPlayers(file.filename, strings) }
                                }
                            },
                        ], palette: palette)
                    }
                }
                if content.busy == "players" {
                    Hint(text: model.t("mobile.content.reading"), palette: palette)
                }
                if manage {
                    Presses(presses: [
                        Press(title: model.t("sides.players_add"), enabled: idle) { picking = .players },
                    ], palette: palette)
                }
                if model.can(Permissions.SETTINGS_SERVER) {
                    Toggle(model.t("sides.option_backup"), isOn: Binding(get: { options.backup }, set: { on in
                        var changed = options
                        changed.backup = on
                        let strings = model.strings
                        Task { await content.setPlayerOnlyOptions(changed, strings) }
                    }))
                    .disabled(!idle)
                    Toggle(model.t("sides.option_skip_listed"), isOn: Binding(get: { options.skipListed }, set: { on in
                        var changed = options
                        changed.skipListed = on
                        let strings = model.strings
                        Task { await content.setPlayerOnlyOptions(changed, strings) }
                    }))
                    .disabled(!idle || !options.backup)
                }
            }
        }
    }

    private func searchCard(_ content: ContentPages, _ minecraft: String?, _ palette: Palette) -> some View {
        let unknown = model.t("value.unknown")
        let searching = content.found?.loading == true
        return CardBox(palette: palette) {
            TextField(model.t("mods.search_label"), text: $query)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.search)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .onChange(of: query) { if query.count > 100 { query = String(query.prefix(100)) } }
                .onSubmit { Task { await content.search(query) } }
            Hint(text: model.t("mobile.content.search_hint", [
                "type": row?.typeName ?? unknown, "version": minecraft ?? unknown,
            ]), palette: palette)
            Presses(presses: [
                Press(title: model.t(searching ? "mods.searching" : "mods.search"), enabled: !query.trimmingCharacters(in: .whitespaces).isEmpty) {
                    Task { await content.search(query) }
                },
            ], palette: palette)
        }
    }

    @ViewBuilder
    private func found(_ content: ContentPages, _ palette: Palette) -> some View {
        if let read = content.found {
            if let problem = read.problem {
                ProblemRow(text: problem.message(model.strings), palette: palette)
            }
            if let found = read.value {
                if found.hits.isEmpty {
                    Hint(text: model.t("mods.nothing_found"), palette: palette)
                }
                ForEach(found.hits) { hit in
                    hitCard(content, hit, palette)
                }
                if found.more {
                    Presses(presses: [
                        Press(title: model.t("mobile.content.more"), enabled: !read.loading) {
                            Task { await content.searchMore() }
                        },
                    ], palette: palette)
                }
            }
        }
    }

    private func hitCard(_ content: ContentPages, _ hit: ModrinthHit, _ palette: Palette) -> some View {
        var presses = [
            Press(title: model.t("mods.install"), enabled: content.busy == nil) {
                Task { await content.openProject(hit) }
            },
        ]
        if let page = Install.page(hit.page) {
            presses.append(Press(title: model.t("mobile.content.open_page")) { web = WebLink(url: page) })
        }
        let lines: [String?] = [hit.description.map { String($0.prefix(160)) }, Install.downloads(model.strings, hit.downloads)]
        let details = lines.compactMap { $0 }
        return CardBox(palette: palette) {
            HStack(alignment: .top, spacing: 12) {
                ModIcon(icons: model.icons, url: hit.iconUrl, palette: palette)
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: hit.title.isEmpty ? hit.slug : hit.title)
                        .font(.headline)
                        .foregroundStyle(palette.textPrimary)
                    ForEach(details, id: \.self) { line in
                        Hint(text: line, palette: palette)
                    }
                }
            }
            Presses(presses: presses, palette: palette)
        }
    }

    /// A .jar from the phone, or a Modrinth modpack imported into this server.
    @ViewBuilder
    private func addFiles(_ content: ContentPages, _ palette: Palette) -> some View {
        let busy = content.busy
        SectionTitle(text: model.t("mobile.content.add_file"), palette: palette)
        CardBox(palette: palette) {
            Text(model.t("mobile.content.add_file_hint"))
                .foregroundStyle(palette.textPrimary)
            if busy == "upload" || busy == "modpack" {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Hint(text: model.t("mobile.content.reading"), palette: palette)
                }
            }
            Presses(presses: [
                Press(title: model.t("mobile.content.add_file"), enabled: busy == nil) { picking = .jar },
                Press(title: model.t("mobile.content.modpack_choose"), enabled: busy == nil) { picking = .modpack },
            ], palette: palette)
            Hint(text: model.t("pack.into_intro"), palette: palette)
            TextField(model.t("pack.find_placeholder"), text: Binding(get: { packQuery }, set: { packQuery = String($0.prefix(100)) }))
                .textFieldStyle(.roundedBorder)
            Presses(presses: [
                Press(title: model.t(busy == "find_pack" ? "mods.searching" : "pack.find"),
                      enabled: busy == nil && !packQuery.trimmingCharacters(in: .whitespaces).isEmpty) {
                    let strings = model.strings
                    let query = packQuery
                    Task { await content.findPacks(query, strings) }
                },
            ], palette: palette)
            if let found = content.packsFound {
                ForEach(found.problems, id: \.self) { Hint(text: $0, palette: palette) }
                Hint(text: model.t(found.hits.isEmpty ? "mods.nothing_found" : "pack.find_pick"), palette: palette)
                ForEach(found.hits) { hit in
                    Button {
                        let strings = model.strings
                        Task { await content.readFound(hit, strings) }
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(hit.title).foregroundStyle(palette.textPrimary)
                            Hint(text: Modpacks.about(model.strings, hit), palette: palette)
                        }
                    }
                    .disabled(busy != nil)
                }
            }
        }
    }

    private func historyCard(_ row: ModHistoryRow, _ palette: Palette, now: Double) -> some View {
        let strings = model.strings
        var details = [strings.t("mobile.content.history_by", [
            "when": Display.ago(strings, row.ts, now: now), "who": row.user ?? strings.t("value.unknown"),
        ])]
        if row.oldVersion != nil || row.newVersion != nil {
            details.append(Content.change(strings, row.oldVersion, row.newVersion))
        }
        var result = strings.t(row.result == "ok" ? "mobile.content.result_ok" : "mobile.content.result_failed")
        if row.result != "ok", let detail = row.detail {
            result += ": " + detail
        }
        return CardBox(palette: palette) {
            Text(verbatim: Content.action(strings, row) + ": " + Content.historyName(strings, row))
                .foregroundStyle(palette.textPrimary)
            Hint(text: details.joined(separator: " · "), palette: palette)
            Text(verbatim: result)
                .font(.footnote)
                .foregroundStyle(palette.tone(Content.historyTone(row)))
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: - Bedrock packs

    @ViewBuilder
    private func packs(_ content: ContentPages, _ palette: Palette) -> some View {
        let read = content.addons
        let changes = Content.packChanges(canManage: model.can(Permissions.MODS_MANAGE))
        let strings = model.strings
        let idle = content.busy == nil
        if changes == .notAllowed {
            Hint(text: model.t("mobile.content.not_allowed"), palette: palette)
        }
        SaidRow(said: content.said, palette: palette)
        if content.restartOffered && read.value?.running == true && model.canControl {
            CardBox(palette: palette) {
                Text(model.t("addons.restart"))
                    .foregroundStyle(palette.textPrimary)
                Presses(presses: [
                    Press(title: model.t("action.restart_now"), enabled: idle) {
                        asking = ContentConfirm(
                            title: strings.t("confirm.restart_title"),
                            body: strings.t("confirm.restart_body"),
                            confirm: strings.t("action.restart"),
                            danger: false
                        ) {
                            Task { await content.restart(strings, reload: { await content.loadAddons() }) }
                        }
                    },
                ], palette: palette)
            }
        }
        if let problem = read.problem {
            Hint(text: model.t("mobile.content.unknown"), palette: palette)
            ProblemCard(problem: problem, palette: palette) { await content.loadAddons() }
        }
        if let list = read.value {
            if let world = list.world {
                Hint(text: model.t("mobile.content.pack_world", ["world": world]), palette: palette)
            } else {
                Text(model.t("addons.no_world"))
                    .foregroundStyle(palette.tone(.warning))
            }
            if list.running && changes == .ready {
                Hint(text: model.t("addons.running"), palette: palette)
            }
            SectionTitle(text: strings.tn("addons.installed", count: list.addons.count), palette: palette)
            if list.addons.isEmpty {
                EmptyCard(title: model.t("addons.none"), hint: model.t("addons.none_hint"), palette: palette)
            }
            ForEach(list.addons) { addon in
                packCard(content, addon, canTurn: list.world != nil, changes, palette)
            }
            if changes == .ready {
                SectionTitle(text: model.t("addons.add_title"), palette: palette)
                CardBox(palette: palette) {
                    Text(model.t("addons.add_lead"))
                        .foregroundStyle(palette.textPrimary)
                    if content.busy == "upload" {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Hint(text: model.t("addons.installing"), palette: palette)
                        }
                    }
                    Presses(presses: [
                        Press(title: model.t("mobile.content.add_pack"), enabled: idle) { picking = .pack },
                    ], palette: palette)
                }
            }
        } else if read.problem == nil {
            ProgressView().frame(maxWidth: .infinity)
        }
    }

    private func packCard(_ content: ContentPages, _ addon: Addon, canTurn: Bool, _ changes: Changes, _ palette: Palette) -> some View {
        let strings = model.strings
        let idle = content.busy == nil
        let details = [
            model.t(Content.packKind(addon)),
            addon.version.isEmpty ? model.t("value.unknown") : addon.version,
            model.t(addon.enabled ? "addons.on" : "addons.off"),
        ]
        return CardBox(palette: palette) {
            Text(verbatim: addon.name.isEmpty ? addon.folder : addon.name)
                .font(.headline)
                .foregroundStyle(palette.textPrimary)
            Hint(text: details.joined(separator: " · "), palette: palette)
            if let description = addon.description, !description.isEmpty {
                Text(verbatim: String(description.prefix(200)))
                    .font(.footnote)
                    .foregroundStyle(palette.textPrimary)
            }
            if model.technical {
                Hint(text: "\(addon.uuid) · \(addon.folder)", palette: palette)
            }
            if changes == .ready {
                Presses(presses: [
                    Press(title: model.t(addon.enabled ? "addons.turn_off" : "addons.turn_on"), enabled: idle && canTurn) {
                        Task { await content.setAddon(addon, !addon.enabled, strings) }
                    },
                    Press(title: model.t("addons.remove"), danger: true, enabled: idle) {
                        asking = ContentConfirm(
                            title: strings.t("addons.remove_title", ["name": addon.name]),
                            body: strings.t("addons.remove_body"),
                            confirm: strings.t("addons.remove")
                        ) {
                            Task { await content.removeAddon(addon, strings) }
                        }
                    },
                ], palette: palette)
            }
        }
    }

    // MARK: - Files

    /// Copies the picked file into the app's own temporary folder (the
    /// picker's copy can only be read for a moment), then sends it; the copy
    /// is deleted once sent.
    private func picked(_ url: URL, _ what: Picking, _ kind: ContentKind) {
        let content = self.content
        let strings = model.strings
        let name = url.lastPathComponent
        let fits: Bool
        let expected: String
        switch what {
        case .jar:
            fits = Content.accepts(kind, name)
            expected = Content.JAR
        case .modpack:
            fits = Content.accepts(kind, name, modpack: true)
            expected = Content.MODPACK
        case .pack:
            fits = Content.accepts(.packs, name)
            expected = Content.PACK_FILES.joined(separator: ", ")
        case .players:
            fits = Sides.accepts(name)
            expected = ".jar, .zip"
        case .resourcePack:
            fits = name.lowercased().hasSuffix(".zip")
            expected = ".zip"
        }
        guard fits else {
            content.said = Said(text: strings.t("mobile.content.wrong_file", ["kind": expected]), good: false)
            return
        }
        Task {
            let file: PickedFile
            do {
                file = try await Task.detached { try PickedCopy.make(url) }.value
            } catch {
                content.said = Said(text: strings.t("mobile.error.failed", ["reason": error.localizedDescription]), good: false)
                return
            }
            switch what {
            case .jar: await content.uploadMod(file, strings)
            case .modpack: await content.inspectModpack(file, strings)
            case .pack: await content.uploadAddon(file, strings)
            case .players: await content.uploadForPlayers(file, strings)
            case .resourcePack: await content.uploadPack(file, strings)
            }
        }
    }

    // MARK: - Sheets

    @ViewBuilder
    private func sheetBody(_ content: ContentPages, _ sheet: ContentSheet) -> some View {
        switch sheet {
        case .updateAll:
            UpdateAllSheet(content: content)
        case .project:
            InstallSheet(content: content)
        case .missing:
            MissingSheet(content: content)
        case .modpack:
            ModpackSheet(content: content)
        case .packUpdate:
            PackUpdateSheet(content: content)
        case .make:
            MakeModpackSheet(content: content)
        }
    }
}

/// The picked file's own copy in the app's temporary folder.
enum PickedCopy {
    static func make(_ url: URL) throws -> PickedFile {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("picked-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let copy = folder.appendingPathComponent(url.lastPathComponent)
        try FileManager.default.copyItem(at: url, to: copy)
        return PickedFile(name: url.lastPathComponent, url: copy)
    }
}

/// A Modrinth picture from the phone's cache, or a plain square while there is none.
private struct ModIcon: View {
    let icons: IconCache
    let url: String?
    let palette: Palette

    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image = image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                palette.sunken
            }
        }
        .frame(width: 40, height: 40)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .accessibilityHidden(true)
        .task(id: url) {
            image = nil
            if let data = await icons.load(url) {
                image = UIImage(data: data)
            }
        }
    }
}

// MARK: - Questions

/// A read the sheet waits on: Loading, or the problem in the agent's words.
private struct SheetLoading<T>: View {
    let read: PageRead<T>

    @Environment(AppModel.self) private var model

    var body: some View {
        if let problem = read.problem {
            Text(problem.message(model.strings)).foregroundStyle(.red)
        } else if read.value == nil {
            HStack(spacing: 8) {
                ProgressView()
                Text(model.t("mobile.common.loading"))
            }
        }
    }
}

/// The kept copies of one mod, each with Roll back (which asks first).
private struct VersionsSheet: View {
    let content: ContentPages
    let ask: VersionsAsk
    let palette: Palette

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var using: ModVersion?

    private func details(_ version: ModVersion, _ strings: Strings) -> String {
        let parts: [String?] = [
            Display.dateTime(strings, version.createdAt),
            version.isCurrent ? model.t("mods.current") : nil,
            version.exists ? nil : model.t("mods.file_missing"),
        ]
        return parts.compactMap { $0 }.joined(separator: " · ")
    }

    var body: some View {
        let strings = model.strings
        let canChange = Content.changes(canManage: model.can(Permissions.MODS_MANAGE), serverState: content.serverState) == .ready
        let read = content.versions?.versions ?? ask.versions
        NavigationStack {
            List {
                SheetLoading(read: read)
                if let list = read.value?.versions {
                    if list.isEmpty {
                        Text(model.t("mods.no_versions"))
                    }
                    ForEach(list) { version in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(verbatim: version.version ?? version.filename)
                            Text(verbatim: details(version, strings))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            if canChange && Content.canRollBack(version) {
                                Button(model.t("mods.rollback")) { using = version }
                                    .buttonStyle(.borderless)
                            }
                        }
                        .frame(minHeight: 44, alignment: .leading)
                    }
                }
            }
            .navigationTitle(strings.t("mods.versions_title", ["name": ask.mod.name]))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(model.t("mobile.common.cancel")) { dismiss() }
                }
            }
            .alert(
                strings.t("mods.rollback_title", ["version": using.map { $0.version ?? $0.filename } ?? ""]),
                isPresented: Binding(get: { using != nil }, set: { if !$0 { using = nil } }),
                presenting: using,
                actions: { version in
                    Button(model.t("mods.rollback")) {
                        Task { await content.rollBack(ask.mod, version, strings) }
                    }
                    Button(model.t("mobile.common.cancel"), role: .cancel) {}
                },
                message: { _ in Text(model.t("mods.rollback_body")) }
            )
        }
    }
}

/// The ticked list, old version to new, then one Update all.
private struct UpdateAllSheet: View {
    @Bindable var content: ContentPages

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let strings = model.strings
        let read = content.plan ?? PageRead(loading: true)
        let plan = read.value
        let count = content.ticked.count
        let blocked = plan.flatMap { UpdateAll.blocked(strings, $0) }
        List {
            if plan == nil {
                if read.problem == nil {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text(model.t("mobile.content.checking"))
                    }
                } else {
                    SheetLoading(read: read)
                }
            }
            if let plan = plan {
                Section {
                    if !plan.updates.isEmpty {
                        Text(model.t("mobile.content.update_all_lead"))
                    } else if plan.unchecked.isEmpty {
                        Text(model.t("mobile.content.update_all_none"))
                    }
                    if let blocked = blocked {
                        Text(verbatim: blocked).foregroundStyle(.orange)
                    }
                }
                Section {
                    ForEach(plan.updates) { row in
                        Toggle(isOn: Binding(
                            get: { content.ticked.contains(row.filename) },
                            set: { on in
                                if on { content.ticked.insert(row.filename) } else { content.ticked.remove(row.filename) }
                            }
                        )) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(verbatim: row.name.isEmpty ? row.filename : row.name)
                                Text(verbatim: Content.change(strings, row.installedVersion, row.latestVersion))
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                                if !row.enabled {
                                    Text(model.t("mobile.content.update_all_off"))
                                        .font(.footnote)
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
                if !plan.unchecked.isEmpty {
                    Section {
                        ForEach(Array(plan.unchecked.enumerated()), id: \.offset) { _, u in
                            Text(model.t("mobile.content.update_all_unchecked", ["name": u.name, "reason": u.reason]))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle(model.t("mobile.content.update_all"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(model.t("mobile.common.cancel")) { dismiss() }
            }
            if let plan = plan, !plan.updates.isEmpty {
                ToolbarItem(placement: .confirmationAction) {
                    Button(strings.tn("mobile.content.update_all_go", count: count)) {
                        Task { await content.updateAll(strings) }
                    }
                    .disabled(count == 0 || blocked != nil)
                }
            }
        }
    }
}

/// What installing a Modrinth project would add, with its dependencies.
private struct InstallSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var web: WebLink?
    @State private var withDependencies = true

    var body: some View {
        let strings = model.strings
        let read = content.project ?? PageRead(loading: true)
        let project = read.value
        let version = project.flatMap { Install.latest($0) }
        List {
            SheetLoading(read: read)
            if let project = project {
                if let version = version {
                    Section {
                        ForEach(Install.facts(strings, project, version), id: \.self) { fact in
                            Text(verbatim: fact)
                        }
                        Text(model.t("mods.install_checked"))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    let required = Install.required(version)
                    if !required.isEmpty {
                        Toggle(strings.tn("mods.install_deps", count: required.count), isOn: $withDependencies)
                    }
                } else {
                    Text(model.t("mods.no_build"))
                }
                if let page = Install.page(project.project.page) {
                    Button(model.t("mobile.content.open_page")) { web = WebLink(url: page) }
                }
            }
        }
        .sheet(item: $web) { page in
            SafariView(url: page.url).ignoresSafeArea()
        }
        .navigationTitle(model.t("mods.install_title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(model.t("mobile.common.cancel")) { dismiss() }
            }
            if version != nil {
                ToolbarItem(placement: .confirmationAction) {
                    Button(model.t("mods.install_confirm")) {
                        let deps = withDependencies
                        Task { await content.install(withDependencies: deps, strings) }
                    }
                }
            }
        }
    }
}

/// What "Install the missing ones" would download, and what it can't.
private struct MissingSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var web: WebLink?

    private func line(_ item: PlannedDependency, _ strings: Strings) -> String {
        let parts: [String?] = [item.title ?? item.modId, item.versionNumber, item.size.map { Display.bytes(strings, $0) }]
        return parts.compactMap { $0 }.joined(separator: " · ")
    }

    var body: some View {
        let strings = model.strings
        let read = content.missingPlan ?? PageRead(loading: true)
        let plan = read.value
        List {
            SheetLoading(read: read)
            if let plan = plan {
                if !plan.items.isEmpty {
                    Section(strings.tn("mobile.content.missing_plan", count: plan.items.count)) {
                        ForEach(Array(plan.items.enumerated()), id: \.offset) { _, item in
                            Text(verbatim: line(item, strings))
                        }
                    }
                }
                ForEach(Array(plan.unresolvable.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(model.t("mobile.content.cant_install", ["name": item.name ?? item.modId, "reason": item.reason]))
                            .foregroundStyle(.orange)
                        if let page = Install.page(item.page) {
                            Button(model.t("mobile.content.get_it")) { web = WebLink(url: page) }
                            .buttonStyle(.borderless)
                        }
                    }
                }
            }
        }
        .sheet(item: $web) { page in
            SafariView(url: page.url).ignoresSafeArea()
        }
        .navigationTitle(model.t("mobile.content.install_missing"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(model.t("mobile.common.cancel")) { dismiss() }
            }
            if let plan = plan, !plan.items.isEmpty {
                ToolbarItem(placement: .confirmationAction) {
                    Button(model.t("mods.install_confirm")) {
                        Task { await content.installMissing(strings) }
                    }
                }
            }
        }
    }
}

/// What Modpack update changes, then Modpack update.
private struct PackUpdateSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var usePackConfigs = false

    var body: some View {
        let strings = model.strings
        if let read = content.packUpdate {
            let problems = read.changes.problems
            List {
                if !problems.isEmpty {
                    Section(model.t("pack.cant_import")) {
                        ForEach(problems, id: \.self) { problem in
                            Text(verbatim: problem).foregroundStyle(.red)
                        }
                    }
                } else {
                    Section {
                        ForEach(LinkedPacks.changes(strings, read.changes), id: \.self) { line in
                            Text(verbatim: line)
                        }
                        if !read.changes.configsChangedByYou.isEmpty {
                            Toggle(model.t("linked.use_pack_configs"), isOn: $usePackConfigs)
                        }
                    }
                    Section {
                        ForEach(LinkedPacks.steps(strings, read), id: \.self) { step in
                            Text(verbatim: step)
                        }
                    }
                }
            }
            .navigationTitle(strings.t("linked.update_title", ["name": read.pack.name, "version": read.changes.toVersion]))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(model.t("mobile.common.cancel")) { dismiss() }
                }
                if problems.isEmpty {
                    ToolbarItem(placement: .confirmationAction) {
                        Button(model.t("linked.update")) {
                            Task { await content.updatePack(usePackConfigs: usePackConfigs, strings) }
                        }
                    }
                }
            }
        }
    }
}

/// Name, version, "Leave out server-only mods", the players' loader on a
/// plugin server, the config files to put in, and "Required to join"
/// (greyed until the join check exists).
private struct MakeModpackSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var version = ""
    @State private var leaveOut = true
    @State private var loader = "fabric"
    @State private var configs: Set<String> = []
    @State private var required = false

    var body: some View {
        let strings = model.strings
        if let options = content.makeOptions {
            Form {
                Section {
                    TextField(model.t("make.name"), text: $name)
                    TextField(model.t("make.version"), text: $version)
                    if !options.playersLoaders.isEmpty {
                        Picker(model.t("make.loader"), selection: $loader) {
                            ForEach(options.playersLoaders, id: \.id) { choice in
                                Text(verbatim: choice.name).tag(choice.id)
                            }
                        }
                    }
                }
                Section {
                    Toggle(model.t("make.leave_out"), isOn: $leaveOut)
                    Text(verbatim: model.t("make.leave_out_hint")).font(.footnote).foregroundStyle(.secondary)
                }
                Section {
                    // Greyed until the join check (the Companion mod) exists.
                    Toggle(model.t("make.required"), isOn: $required).disabled(!options.requiredToJoin)
                    if !options.requiredToJoin {
                        Text(verbatim: model.t("make.required_later")).font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section(model.t(options.configs.isEmpty ? "make.no_configs" : "make.configs")) {
                    ForEach(options.configs, id: \.path) { config in
                        Toggle(config.path, isOn: Binding(
                            get: { configs.contains(config.path) },
                            set: { on in if on { configs.insert(config.path) } else { configs.remove(config.path) } }
                        ))
                    }
                }
            }
            .navigationTitle(model.t("make.title"))
            .navigationBarTitleDisplayMode(.inline)
            .onAppear {
                name = options.name
                version = options.version
                loader = options.playersLoaders.first?.id ?? "fabric"
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(model.t("mobile.common.cancel")) { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(model.t("make.title")) {
                        let answers = MakeModpack(
                            name: String(name.trimmingCharacters(in: .whitespaces).prefix(64)),
                            version: String(version.trimmingCharacters(in: .whitespaces).prefix(32)),
                            leaveOutServerOnly: leaveOut,
                            configs: options.configs.map(\.path).filter { configs.contains($0) },
                            playersLoader: loader
                        )
                        Task { await content.makeModpack(answers, strings) }
                    }
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || version.isEmpty)
                }
            }
        }
    }
}

/// A resource pack someone else hosts: its link, and its SHA-1 if the site gives one.
/// The Modrinth address and one-line summary of a new project.
private struct ModrinthProjectSheet: View {
    let content: ContentPages
    let ask: ModrinthAsk

    @Environment(AppModel.self) private var model
    @State private var slug = ""
    @State private var summary = ""

    var body: some View {
        let strings = model.strings
        Form {
            if ask.jar != nil { Text(verbatim: model.t("modrinth.own_jar_pick")) }
            TextField(model.t("modrinth.slug_prompt"), text: $slug)
                .textInputAutocapitalization(.never)
            TextField(model.t("modrinth.summary_prompt"), text: $summary)
        }
        .onAppear { slug = ModrinthCards.slug(ask.name) }
        .navigationTitle(ask.jar?.name ?? model.t("modrinth.title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(model.t("mobile.common.cancel")) { content.askingModrinth = nil }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button(model.t("modrinth.upload")) {
                    let s = String(slug.trimmingCharacters(in: .whitespaces).prefix(64))
                    let line = String(summary.trimmingCharacters(in: .whitespaces).prefix(256))
                    Task { await content.uploadToModrinth(s, line, strings) }
                }
                .disabled(slug.isEmpty || summary.trimmingCharacters(in: .whitespaces).count < 3)
            }
        }
    }
}

private struct ServerProjectSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @State private var link = ""

    var body: some View {
        let strings = model.strings
        Form {
            TextField(model.t("modrinth.server_paste_prompt"), text: $link)
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)
        }
        .navigationTitle(model.t("modrinth.server_title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(model.t("mobile.common.cancel")) { content.askingServerLink = false }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button(model.t("modrinth.server_paste")) {
                    let value = String(link.trimmingCharacters(in: .whitespaces).prefix(200))
                    Task { await content.setServerProject(value, strings) }
                }
            }
        }
    }
}

private struct PackLinkSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @State private var url = ""
    @State private var sha1 = ""

    var body: some View {
        let strings = model.strings
        Form {
            TextField(model.t("pack.link_prompt"), text: $url)
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)
            TextField(model.t("pack.sha1_prompt"), text: $sha1)
                .textInputAutocapitalization(.never)
        }
        .navigationTitle(model.t("pack.title"))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button(model.t("mobile.common.cancel")) { content.askingPackLink = false }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button(model.t("pack.use_link")) {
                    let link = String(url.trimmingCharacters(in: .whitespaces).prefix(1000))
                    let hash = String(sha1.trimmingCharacters(in: .whitespaces).prefix(40))
                    Task { await content.setPackLink(link, hash, strings) }
                }
                .disabled(url.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
    }
}

/// What importing a modpack here would do, then Import.
private struct ModpackSheet: View {
    let content: ContentPages

    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let strings = model.strings
        let unknown = strings.t("value.unknown")
        if let inspected = content.modpack {
            let pack = inspected.pack
            let loader = [pack.typeName, pack.loaderVersion].compactMap { $0 }.joined(separator: " ")
            List {
                Section {
                    Text(verbatim: model.t("pack.minecraft") + ": " + (pack.minecraftVersion ?? unknown))
                    Text(verbatim: model.t("pack.loader") + ": " + (loader.isEmpty ? unknown : loader))
                    Text(verbatim: model.t("pack.files") + ": " + model.t("pack.files_value", [
                        "count": pack.downloadCount, "size": Display.bytes(strings, pack.downloadBytes),
                    ]))
                    if inspected.versionChange {
                        Text(model.t("pack.version_change", ["type": pack.typeName ?? unknown, "version": pack.minecraftVersion ?? unknown]))
                            .foregroundStyle(.orange)
                    }
                }
                if !pack.problems.isEmpty {
                    Section(model.t("pack.cant_import")) {
                        ForEach(pack.problems, id: \.self) { problem in
                            Text(verbatim: problem).foregroundStyle(.red)
                        }
                    }
                }
                if !pack.clientOnly.isEmpty {
                    Text(model.t("pack.client_only", ["names": pack.clientOnly.joined(separator: ", ")]))
                }
                if !pack.skippedOverrides.isEmpty {
                    Text(model.t("pack.skipped_extras", ["names": pack.skippedOverrides.joined(separator: ", ")]))
                }
                ForEach(Modpacks.notes(strings, pack), id: \.self) { Text(verbatim: $0).foregroundStyle(.orange) }
                if pack.canImport {
                    Section(model.t("mobile.content.modpack_failed_note")) {
                        ForEach(Modpacks.steps(strings, inspected), id: \.self) { step in
                            Text(verbatim: step)
                        }
                    }
                }
                Section(model.t("pack.all_files", ["count": pack.files.count])) {
                    ForEach(Array(pack.files.prefix(200).enumerated()), id: \.offset) { _, file in
                        let status = Modpacks.fileStatus(file)
                        Text(verbatim: "\(file.name) · \(model.t(status.key))" + (file.reason.map { " (\($0))" } ?? ""))
                            .font(.footnote)
                            .foregroundStyle(status.tone == .danger ? Color.red : Color.secondary)
                    }
                }
            }
            .navigationTitle(strings.t("pack.import_title", ["name": pack.name.isEmpty ? unknown : pack.name]))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(model.t("mobile.common.cancel")) { dismiss() }
                }
                if pack.canImport {
                    ToolbarItem(placement: .confirmationAction) {
                        Button(model.t("pack.import")) {
                            Task { await content.importModpack(strings) }
                        }
                    }
                }
            }
        }
    }
}
