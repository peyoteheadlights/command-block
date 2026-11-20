import Core
import SwiftUI
import UIKit

/// A server's Store tab, like the desktop app (desktop/store.py): connected
/// or not, the store's link, its packages with prices, Open Tebex, the
/// copy-paste checklist and the Store ranks. The key is typed once and
/// never shown again: the agent keeps it. Mirrors Store.kt.
struct StoreScreen: View {
    let serverId: String

    @Environment(AppModel.self) private var model
    @Environment(\.openURL) private var openURL
    @State private var key = ""
    @State private var askingGameplay = false
    @State private var neverSold: String? = nil
    @State private var remind: [String] = []

    var body: some View {
        let pages = model.pages(serverId)
        let strings = model.strings
        ServerFrame(serverId: serverId, title: model.t("desktop.store.title")) { palette in
            List {
                Text(model.t("desktop.store.hint")).foregroundStyle(.secondary)
                if let problem = pages.store.problem {
                    ProblemCard(problem: problem, palette: palette) { await pages.loadStore() }
                } else if let view = pages.store.value {
                    content(pages, view, strings, palette)
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .refreshable { await pages.loadStore() }
        }
        .task(id: serverId) { await pages.loadStore() }
        .alert(model.t("desktop.store.own_package"), isPresented: $askingGameplay) {
            Button(model.t("desktop.store.gameplay_yes")) { neverSold = (pages.store.value?.gameplayWarning ?? "") + "\n\n" }
            Button(model.t("desktop.store.gameplay_no"), role: .cancel) { neverSold = "" }
        } message: {
            Text(model.t("desktop.store.gameplay_question"))
        }
        .alert(model.t("desktop.store.own_package"), isPresented: Binding(get: { neverSold != nil }, set: { if !$0 { neverSold = nil } })) {
            Button(model.t("desktop.store.got_it")) { neverSold = nil }
        } message: {
            Text((neverSold ?? "") + model.t("desktop.store.never_sold") + "\n" + list(pages.store.value?.neverSold ?? []))
        }
        .alert(model.t("desktop.store.remind_title"), isPresented: Binding(get: { !remind.isEmpty }, set: { if !$0 { remind = [] } })) {
            Button(model.t("desktop.store.got_it")) { remind = [] }
        } message: {
            Text(model.t("desktop.store.remind", ["names": remind.joined(separator: ", ")]) + "\n\n" + list(pages.store.value?.neverSold ?? []))
        }
    }

    private func list(_ items: [String]) -> String { items.map { "- " + $0 }.joined(separator: "\n") }

    @ViewBuilder
    private func content(_ pages: ServerPages, _ view: StoreView, _ strings: Strings, _ palette: Palette) -> some View {
        let usable = Store.usable(view)
        Section {
            if !view.offered {
                Text(view.reason ?? "").foregroundStyle(.secondary)
            } else if view.needsType {
                Text(model.t("desktop.store.needs_type"))
                NavigationLink(model.t("perf.open_settings"), value: ServerRoute.settings(serverId))
            } else {
                Text(model.t(view.connected ? "desktop.store.connected" : "desktop.store.not_connected"))
                if let link = view.store.link { Hint(text: model.t("desktop.store.link", ["link": link]), palette: palette) }
                Hint(text: model.t("desktop.store.buy", ["command": view.buyCommand]), palette: palette)
                if let warning = view.offlineWarning { Text(warning).foregroundStyle(palette.danger) }
                if view.ranksByHand { Hint(text: model.t("desktop.store.ranks_by_hand"), palette: palette) }
                let ranks = view.ranks.filter(\.made).map(\.name).joined(separator: ", ")
                if !ranks.isEmpty { Hint(text: model.t("desktop.store.ranks", ["ranks": ranks]), palette: palette) }
                Hint(text: model.t("desktop.store.chat_note"), palette: palette)
                Hint(text: view.fees, palette: palette)
            }
            if usable {
                SecureField(model.t("desktop.store.key"), text: $key)
                Hint(text: model.t("desktop.store.key_hint"), palette: palette)
                Button(model.t("desktop.store.connect")) {
                    let typed = key
                    Task { if await pages.connectStore(typed, strings) { key = "" } }
                }
                .disabled(pages.storeBusy || key.trimmingCharacters(in: .whitespaces).isEmpty)
                Button(model.t("desktop.store.check")) {
                    Task { remind = await pages.checkStore(strings) }
                }
                .disabled(pages.storeBusy || !view.keySaved)
                Button(model.t("desktop.store.own_package")) { askingGameplay = true }
            }
            if let url = URL(string: view.openTebex) {
                Button(model.t("desktop.store.open_tebex")) { openURL(url) }
            }
            SaidRow(said: pages.storeSaid, palette: palette)
        }
        if let packages = view.packages {
            Section(model.t("desktop.store.packages")) {
                if packages.isEmpty { Text(model.t("desktop.store.no_packages")).foregroundStyle(.secondary) }
                ForEach(packages.indices, id: \.self) { i in Text(hiding: Store.packageLine(view, packages[i])) }
            }
        }
        if usable {
            Section(model.t("desktop.store.checklist")) {
                Hint(text: model.t("desktop.store.checklist_hint"), palette: palette)
                Text(view.checklist).font(.footnote.monospaced()).textSelection(.enabled)
                Button(model.t("desktop.copy")) { UIPasteboard.general.string = view.checklist }
            }
        }
    }
}
