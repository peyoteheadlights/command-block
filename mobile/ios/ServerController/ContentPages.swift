import Core
import Foundation
import Observation
import UIKit

/// A remove waiting for "yes", with what else needs the mod.
struct RemoveAsk: Identifiable {
    let mod: Mod
    let impact: ModImpact

    var id: String { mod.filename }
}

/// The kept versions of one mod, shown to roll back to.
struct VersionsAsk: Identifiable {
    let mod: Mod
    var versions: PageRead<ModVersions>

    var id: String { mod.filename }
}

/// Search results so far, and whether more can be asked for.
struct Found {
    let query: String
    let hits: [ModrinthHit]
    let total: Int?

    var more: Bool { total.map { hits.count < $0 } ?? false }
}

/// A sheet the add-ons screen shows: one at a time.
enum ContentSheet: String, Identifiable {
    case updateAll, project, missing, modpack, packUpdate, make

    var id: String { rawValue }
}

/// Everything the Mods / Plugins / Packs and Game settings screens show for
/// one server (mirrors ContentPages.kt). Every button is the agent's own
/// route with the person's own sign-in, so the agent decides what they may
/// do and explains a refusal.
@MainActor
@Observable
final class ContentPages {
    let serverId: String
    @ObservationIgnored private let client: () -> AgentClient?
    @ObservationIgnored private let noted: (AgentError) -> Void

    init(serverId: String, client: @escaping () -> AgentClient?, noted: @escaping (AgentError) -> Void) {
        self.serverId = serverId
        self.client = client
        self.noted = noted
    }

    /// "toggle:file", "remove:file", "update_all", ... while that button works.
    private(set) var busy: String? = nil
    var said: Said? = nil
    var sheet: ContentSheet? = nil

    // MARK: - Installed

    private(set) var mods = PageRead<ModList>()
    private(set) var history = PageRead<ModHistory>()
    private(set) var scan = PageRead<JarScanReport>()
    private(set) var deep = PageRead<DeepScanReport>()
    private var watchingDeep = false
    private(set) var dependencies: DependencyReport? = nil
    var removing: RemoveAsk? = nil
    var updating: Mod? = nil
    var versions: VersionsAsk? = nil

    /// The agent's label as the list last said; Unknown when it couldn't be read.
    var serverLabel: String? {
        mods.problem != nil ? "unknown" : mods.value?.label
    }

    /// The buttons the agent takes now ("change" while mods may change).
    var serverCan: [String] {
        mods.problem != nil ? [] : mods.value?.can ?? []
    }

    func loadMods() async {
        guard let client = client() else { return }
        mods.loading = true
        do {
            mods = PageRead(value: try await client.mods(serverId: serverId))
        } catch let problem as AgentError {
            noted(problem)
            // Never the old list as if current: Unknown until the PC answers.
            mods = PageRead(problem: problem)
            dependencies = nil
            return
        } catch {
            mods.loading = false
            return
        }
        // The list still shows without these.
        dependencies = try? await client.dependencies(serverId: serverId)
        do {
            history = PageRead(value: try await client.modHistory(serverId: serverId))
        } catch let problem as AgentError {
            history = PageRead(problem: problem)
        } catch {}
        do {
            scan = PageRead(value: try await client.jarScan(serverId: serverId))
        } catch let problem as AgentError {
            scan = PageRead(problem: problem)
        } catch {}
        do {
            showDeep(PageRead(value: try await client.deepScan(serverId: serverId)))
        } catch let problem as AgentError {
            deep = PageRead(problem: problem)
        } catch {}
    }

    /// Shows the deep scan, and asks again every 2 s while one is running.
    private func showDeep(_ read: PageRead<DeepScanReport>) {
        deep = read
        guard read.value?.running != nil, !watchingDeep, let client = client() else { return }
        watchingDeep = true
        Task { [weak self] in
            while let self, self.deep.value?.running != nil {
                try? await Task.sleep(for: .seconds(2))
                do {
                    self.deep = PageRead(value: try await client.deepScan(serverId: self.serverId))
                } catch let problem as AgentError {
                    self.deep = PageRead(problem: problem)
                } catch {}
            }
            self?.watchingDeep = false
            // A Known bad file may have gone to quarantine.
            await self?.loadMods()
        }
    }

    func runDeepScan(_ scope: String, _ scanners: [String], _ filename: String?, _ strings: Strings) async {
        await work("deep_scan", strings) { [weak self] client, id in
            let report = try await client.runDeepScan(serverId: id, scope: scope, scanners: scanners, filename: filename)
            self?.showDeep(PageRead(value: report))
            return nil
        }
    }

    /// True once the key is saved, so the field can be emptied.
    func saveVirusTotalKey(_ key: String, _ strings: Strings) async -> Bool {
        var saved = false
        await work("vt_key", strings) { [weak self] client, id in
            let report = try await client.saveVirusTotalKey(serverId: id, key: key.trimmingCharacters(in: .whitespacesAndNewlines))
            self?.showDeep(PageRead(value: report))
            saved = true
            return nil
        }
        return saved
    }

    func removeVirusTotalKey(_ strings: Strings) async {
        await work("vt_key", strings) { [weak self] client, id in
            self?.showDeep(PageRead(value: try await client.removeVirusTotalKey(serverId: id)))
            return nil
        }
    }

    func deepQuarantine(_ item: DeepScanFound, _ strings: Strings) async {
        await work("deep:\(item.id)", strings, reload: loadMods) { [weak self] client, id in
            self?.deep = PageRead(value: try await client.deepScanQuarantine(serverId: id, root: item.root, path: item.path))
            return nil
        }
    }

    func scanJars(_ strings: Strings) async {
        await work("jar_scan", strings, reload: loadMods) { client, id in
            _ = try await client.scanJars(serverId: id)
            return nil
        }
    }

    func putBack(_ id: String, _ filename: String, _ strings: Strings) async {
        await work("put_back:\(id)", strings, reload: loadMods) { client, serverId in
            _ = try await client.putBackJar(serverId: serverId, id: id)
            return Said(text: strings.t("jarscan.undone", ["name": filename]), good: true)
        }
    }

    func scanOptions(
        _ strings: Strings,
        weekly: Bool? = nil,
        onChange: Bool? = nil,
        beforeStart: Bool? = nil,
        askFirst: Bool? = nil,
        updateHoldDays: Int? = nil
    ) async {
        await work("jar_scan_options", strings) { [weak self] client, id in
            let report = try await client.jarScanOptions(
                serverId: id, weekly: weekly, onChange: onChange, beforeStart: beforeStart, askFirst: askFirst,
                updateHoldDays: updateHoldDays
            )
            self?.scan = PageRead(value: report)
            return nil
        }
    }

    /// The Server / Players / Both filter ("all": every mod).
    var sideFilter = "all"
    /// "On all servers": every server's marks; nil while it's off.
    private(set) var everywhere: PageRead<ModSides>? = nil

    func showEverywhere(_ on: Bool) async {
        guard on, let client = client() else {
            everywhere = nil
            return
        }
        everywhere = PageRead(loading: true)
        do {
            everywhere = PageRead(value: try await client.modSides())
        } catch let problem as AgentError {
            noted(problem)
            everywhere = PageRead(problem: problem)
        } catch {
            everywhere = nil
        }
    }

    func changedOk(_ filename: String, _ strings: Strings) async {
        await work("changed_ok:\(filename)", strings) { [weak self] client, id in
            self?.scan = PageRead(value: try await client.jarChangedOk(serverId: id, filename: filename))
            return nil
        }
    }

    func deepOptions(
        _ strings: Strings,
        beforeStart: Bool? = nil,
        schedule: String? = nil,
        time: String? = nil,
        day: String? = nil
    ) async {
        await work("deep_scan_options", strings) { [weak self] client, id in
            let report = try await client.deepScanOptions(
                serverId: id, beforeStart: beforeStart, schedule: schedule, time: time, day: day
            )
            self?.showDeep(PageRead(value: report))
            return nil
        }
    }

    func setEnabled(_ mod: Mod, _ on: Bool, _ strings: Strings) async {
        await work("toggle:\(mod.filename)", strings, reload: loadMods) { client, id in
            _ = try await client.setModEnabled(serverId: id, filename: mod.filename, enabled: on)
            return Said(text: strings.t(on ? "mods.turned_on" : "mods.turned_off", ["name": mod.name]), good: true)
        }
    }

    /// Asks the agent what else needs `mod`; nothing changes yet.
    func askRemove(_ mod: Mod, _ strings: Strings) async {
        await work("remove:\(mod.filename)", strings) { [weak self] client, id in
            self?.removing = RemoveAsk(mod: mod, impact: try await client.modImpact(serverId: id, filename: mod.filename))
            return nil
        }
    }

    func remove(_ ask: RemoveAsk, _ strings: Strings) async {
        removing = nil
        await work("remove:\(ask.mod.filename)", strings, reload: loadMods) { client, id in
            _ = try await client.removeMod(serverId: id, filename: ask.mod.filename)
            return Said(text: strings.t("mods.removed_toast", ["name": ask.mod.name]), good: true)
        }
    }

    func update(_ mod: Mod, _ strings: Strings) async {
        updating = nil
        await work("update:\(mod.filename)", strings, reload: loadMods) { client, id in
            let done = try await client.updateMod(serverId: id, filename: mod.filename, versionId: mod.updateAvailable?.versionId)
            return Said(text: strings.t("mods.updated_toast", ["version": done.updated?.version ?? strings.t("value.unknown")]), good: true)
        }
    }

    func showVersions(_ mod: Mod) async {
        guard let client = client() else { return }
        versions = VersionsAsk(mod: mod, versions: PageRead(loading: true))
        var read = PageRead<ModVersions>()
        do {
            read = PageRead(value: try await client.modVersions(serverId: serverId, modId: mod.modId))
        } catch let problem as AgentError {
            noted(problem)
            read = PageRead(problem: problem)
        } catch {}
        if versions?.mod == mod {
            versions?.versions = read
        }
    }

    func rollBack(_ mod: Mod, _ version: ModVersion, _ strings: Strings) async {
        guard Content.canRollBack(version) else { return }
        versions = nil
        await work("rollback:\(mod.filename)", strings, reload: loadMods) { client, id in
            let done = try await client.rollBackMod(serverId: id, modId: mod.modId, archivePath: version.archivePath)
            let name = done.restored?.version ?? version.version ?? strings.t("value.unknown")
            return Said(text: strings.t("mods.rolled_back", ["version": name]), good: true)
        }
    }

    /// Stop from "Stop the server first"; the list then says whether it stopped.
    func stop(_ strings: Strings) async {
        await work("stop", strings, reload: loadMods) { client, id in
            _ = try await client.serverAction(serverId: id, action: "stop")
            return Said(text: strings.t("mobile.server.done_stop"), good: true)
        }
    }

    /// The server started or stopped: the list says again whether changes work.
    func onStateChanged() {
        guard mods.value != nil, busy == nil else { return }
        Task { await loadMods() }
    }

    // MARK: - Update all

    private(set) var plan: PageRead<UpdateAllPlan>? = nil
    var ticked: Set<String> = []

    /// The live feed's latest word on a task of this server.
    private(set) var task: Job? = nil

    func onTask(_ job: Job) {
        task = job
    }

    /// Asks Modrinth (through the PC) what Update all would change.
    func openUpdateAll() async {
        guard let client = client() else { return }
        plan = PageRead(loading: true)
        sheet = .updateAll
        do {
            let read = try await client.updateAllPlan(serverId: serverId)
            ticked = UpdateAll.ticked(read)
            plan = PageRead(value: read)
        } catch let problem as AgentError {
            noted(problem)
            plan = PageRead(problem: problem)
        } catch {
            plan = nil
        }
    }

    /// "Change these" on the Still fit? card: Update all's list, filled from the card.
    func openFit(_ fit: ModFit) {
        let read = StillFit.plan(fit)
        ticked = UpdateAll.ticked(read)
        plan = PageRead(value: read)
        sheet = .updateAll
    }

    func hideFit(_ strings: Strings) async {
        await work("hide_fit", strings, reload: loadMods) { client, id in
            try await client.hideFit(serverId: id)
            return nil
        }
    }

    func updateAll(_ strings: Strings) async {
        guard let read = plan?.value else { return }
        let body = UpdateAll.body(read, ticked: ticked)
        sheet = nil
        plan = nil
        task = nil
        await work("update_all", strings, reload: loadMods) { client, id in
            Said(text: UpdateAll.done(strings, try await client.updateAll(serverId: id, body)), good: true)
        }
    }

    /// A sheet closed without its button: nothing it read is kept, and a
    /// modpack read but not imported is forgotten on the PC.
    func sheetClosed() {
        sheet = nil
        plan = nil
        project = nil
        missingPlan = nil
        cancelModpack()
        cancelPackUpdate()
        makeOptions = nil
    }

    // MARK: - Modrinth

    private(set) var found: PageRead<Found>? = nil
    private(set) var project: PageRead<ModProject>? = nil
    private(set) var missingPlan: PageRead<DependencyPlan>? = nil
    /// Counts searches, so an answer for an older query is dropped.
    @ObservationIgnored private var searchRead = 0

    func search(_ query: String) async {
        guard let client = client() else { return }
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        searchRead += 1
        let mine = searchRead
        found = PageRead(loading: true)
        var read = PageRead<Found>()
        do {
            let page = try await client.searchMods(serverId: serverId, query: q)
            read = PageRead(value: Found(query: q, hits: page.hits, total: page.total))
        } catch let problem as AgentError {
            noted(problem)
            read = PageRead(problem: problem)
        } catch {}
        if mine == searchRead { found = read }
    }

    func searchMore() async {
        guard let client = client(), let now = found?.value, now.more, found?.loading != true else { return }
        searchRead += 1
        let mine = searchRead
        found = PageRead(value: now, loading: true)
        var read = PageRead<Found>(value: now)
        do {
            let page = try await client.searchMods(serverId: serverId, query: now.query, offset: now.hits.count)
            let fresh = page.hits.filter { hit in !now.hits.contains { $0.slug == hit.slug } }
            read = PageRead(value: Found(query: now.query, hits: now.hits + fresh, total: page.total))
        } catch let problem as AgentError {
            noted(problem)
            read.problem = problem
        } catch {}
        if mine == searchRead { found = read }
    }

    /// Reads a project's versions that fit this server, for the install question.
    func openProject(_ hit: ModrinthHit) async {
        await openProject(slug: hit.slug)
    }

    func openProject(slug: String) async {
        guard let client = client() else { return }
        project = PageRead(loading: true)
        sheet = .project
        do {
            project = PageRead(value: try await client.modProject(serverId: serverId, slug: slug))
        } catch let problem as AgentError {
            noted(problem)
            project = PageRead(problem: problem)
        } catch {
            project = nil
        }
    }

    func install(withDependencies: Bool, _ strings: Strings) async {
        guard let read = project?.value, let version = Install.latest(read) else { return }
        sheet = nil
        project = nil
        await work("install:\(read.project.slug)", strings, reload: loadMods) { client, id in
            let done = try await client.installMod(serverId: id, slug: read.project.slug, versionId: version.versionId, withDependencies: withDependencies)
            let name = done.installed?.name ?? read.project.title
            let number = done.installed?.version ?? version.versionNumber ?? strings.t("value.unknown")
            return Said(text: strings.t("mobile.content.installed", ["name": name, "version": number]), good: true)
        }
    }

    /// What "Install the missing ones" would download; nothing is yet.
    func planMissing() async {
        guard let client = client() else { return }
        missingPlan = PageRead(loading: true)
        sheet = .missing
        do {
            missingPlan = PageRead(value: try await client.planDependencies(serverId: serverId))
        } catch let problem as AgentError {
            noted(problem)
            missingPlan = PageRead(problem: problem)
        } catch {
            missingPlan = nil
        }
    }

    func installMissing(_ strings: Strings) async {
        sheet = nil
        missingPlan = nil
        await work("missing", strings, reload: loadMods) { client, id in
            let results = try await client.installDependencies(serverId: id).results
            return Said(text: Install.missingDone(strings, results), good: Install.allWorked(results))
        }
    }

    // MARK: - Files

    private(set) var modpack: ModpackInspected? = nil

    /// A picked file no site lists, waiting for "Add it anyway": the agent's warning and its SHA-512.
    struct Unknown {
        let file: PickedFile
        let warning: String
        let sha512: String?
    }

    var unknown: Unknown? = nil

    func uploadMod(_ file: PickedFile, _ strings: Strings, confirm: Bool = false) async {
        var waiting = false
        defer { if !waiting { try? FileManager.default.removeItem(at: file.url) } }
        await work("upload", strings, reload: loadMods) { [weak self] client, id in
            let done: ModUploaded
            do {
                done = try await client.uploadMod(serverId: id, file: file, confirm: confirm)
            } catch AgentError.failed(_, _, _, let problems) where problems["confirm"] != nil {
                waiting = true
                if let old = self?.unknown, old.file.url != file.url { self?.throwAway(old) }
                self?.unknown = Unknown(file: file, warning: problems["confirm"] ?? "", sha512: problems["sha512"])
                return nil
            }
            return Said(text: strings.t("mobile.content.added", ["name": done.installed?.name ?? file.name]), good: true)
        }
    }

    /// No after all: the picked copy on the phone is thrown away.
    func throwAway(_ ask: Unknown) {
        try? FileManager.default.removeItem(at: ask.file.url)
    }

    /// Reads the modpack on the PC; nothing changes until Import.
    func inspectModpack(_ file: PickedFile, _ strings: Strings) async {
        defer { try? FileManager.default.removeItem(at: file.url) }
        await work("modpack", strings) { [weak self] client, id in
            self?.modpack = try await client.inspectModpack(serverId: id, file: file)
            self?.sheet = .modpack
            return nil
        }
    }

    /// Modpacks found by name on Modrinth and CurseForge; picking one reads it like a file.
    private(set) var packsFound: ModSearch? = nil

    func findPacks(_ query: String, _ strings: Strings) async {
        let query = query.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty else { return }
        await work("find_pack", strings) { [weak self] client, _ in
            self?.packsFound = try await client.searchModpacks(query: query)
            return nil
        }
    }

    func readFound(_ hit: ModrinthHit, _ strings: Strings) async {
        await work("modpack", strings) { [weak self] client, id in
            self?.modpack = try await client.fetchModpack(hit, serverId: id)
            self?.packsFound = nil
            self?.sheet = .modpack
            return nil
        }
    }

    /// No import after all: the PC forgets the pack it read.
    func cancelModpack() {
        guard let token = modpack?.token else { return }
        modpack = nil
        guard let client = client() else { return }
        Task { try? await client.forgetModpack(token: token) }
    }

    func importModpack(_ strings: Strings) async {
        guard let read = modpack else { return }
        sheet = nil
        modpack = nil
        task = nil
        await work("modpack", strings, reload: loadMods) { client, id in
            // Its own jars were listed in the question just answered.
            let done = try await client.importModpack(serverId: id, token: read.token, ownJars: !read.pack.ownJars.isEmpty)
            return Said(text: strings.t("mobile.content.modpack_done", ["name": done.name.isEmpty ? read.pack.name : done.name]), good: true)
        }
    }

    // MARK: - Server / Players / Both

    func setSide(_ filename: String, name: String, side: String, _ strings: Strings) async {
        await work("side:" + filename, strings, reload: loadMods) { client, id in
            try await client.setModSide(serverId: id, filename: filename, side: side)
            return Said(text: strings.t("sides.marked_" + side, ["name": name]), good: true)
        }
    }

    func uploadForPlayers(_ file: PickedFile, _ strings: Strings) async {
        defer { try? FileManager.default.removeItem(at: file.url) }
        await work("players", strings, reload: loadMods) { client, id in
            try await client.uploadForPlayers(serverId: id, file: file)
            return Said(text: strings.t("sides.players_added", ["name": file.name]), good: true)
        }
    }

    func fetchForPlayers(_ filename: String, _ strings: Strings) async {
        await work("fetch:" + filename, strings, reload: loadMods) { client, id in
            try await client.fetchForPlayers(serverId: id, filename: filename)
            return Said(text: strings.t("sides.players_added", ["name": filename]), good: true)
        }
    }

    func setPlayerOnlyOptions(_ options: PlayerOnlyOptions, _ strings: Strings) async {
        await work("options", strings, reload: loadMods) { client, id in
            try await client.setPlayerOnlyOptions(serverId: id, options: options)
            return Said(text: strings.t("sides.options_saved"), good: true)
        }
    }

    func removeForPlayers(_ filename: String, _ strings: Strings) async {
        await work("remove:" + filename, strings, reload: loadMods) { client, id in
            try await client.removeForPlayers(serverId: id, filename: filename)
            return nil
        }
    }

    // MARK: - Linked modpack

    private(set) var packUpdate: ModpackUpdatePreview? = nil

    /// Gets the new version; nothing changes until Modpack update is pressed.
    func previewPackUpdate(_ versionId: String?, _ strings: Strings) async {
        await work("modpack", strings) { [weak self] client, id in
            self?.packUpdate = try await client.previewModpackUpdate(serverId: id, versionId: versionId)
            self?.sheet = .packUpdate
            return nil
        }
    }

    func cancelPackUpdate() {
        guard let token = packUpdate?.token else { return }
        packUpdate = nil
        guard let client = client() else { return }
        Task { try? await client.forgetModpack(token: token) }
    }

    func updatePack(usePackConfigs: Bool, _ strings: Strings) async {
        guard let read = packUpdate else { return }
        sheet = nil
        packUpdate = nil
        await work("modpack", strings, reload: loadMods) { client, id in
            try await client.updateModpack(
                serverId: id, token: read.token, usePackConfigs: usePackConfigs, ownJars: !read.changes.newOwnJars.isEmpty
            )
            return Said(text: strings.t("linked.updated", ["name": read.pack.name, "version": read.changes.toVersion]), good: true)
        }
    }

    func unlinkPack(_ strings: Strings) async {
        await work("unlink", strings, reload: loadMods) { client, id in
            try await client.unlinkModpack(serverId: id)
            return nil
        }
    }

    // MARK: - Make a modpack

    /// Set while the Make a modpack question shows.
    private(set) var makeOptions: ModpackMakeOptions? = nil
    /// The modpack made last, with Share and Post in Discord.
    private(set) var made: ModpackMade? = nil
    /// The downloaded modpack while the share sheet shows.
    var sharing: Downloaded? = nil

    func askMakeModpack(_ strings: Strings) async {
        await work("make", strings) { [weak self] client, id in
            self?.makeOptions = try await client.makeModpackOptions(serverId: id)
            self?.sheet = .make
            return nil
        }
    }

    /// A title-screen picture was uploaded while the Make a modpack sheet shows.
    private(set) var backgroundSet = false

    func uploadTitleBackground(_ file: PickedFile, _ strings: Strings) async {
        defer { try? FileManager.default.removeItem(at: file.url) }
        await work("background", strings) { [weak self] client, id in
            let options = try await client.uploadTitleBackground(serverId: id, file: file)
            self?.backgroundSet = options.titleScreen.backgroundSet
            return nil
        }
    }

    func makeModpack(_ answers: MakeModpack, _ strings: Strings) async {
        sheet = nil
        makeOptions = nil
        backgroundSet = false
        await work("make", strings) { [weak self] client, id in
            self?.made = try await client.makeModpack(serverId: id, answers)
            await self?.loadLink() // the modpack's download link, if it's on
            await self?.loadModrinth() // "Upload Friends 2"
            return nil
        }
    }

    /// Downloads the modpack into the app's temporary folder and hands it to
    /// the share sheet (`sharing`); it never stays here.
    func shareModpack(_ strings: Strings) async {
        guard let made = made else { return }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        let name = (made.filename as NSString).lastPathComponent
        let file = folder.appendingPathComponent(name.isEmpty ? "modpack.mrpack" : name)
        await work("share", strings) { [weak self] client, id in
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                _ = try await client.downloadModpack(serverId: id, made: made, to: file)
            } catch {
                try? FileManager.default.removeItem(at: folder)
                throw error
            }
            self?.sharing = Downloaded(name: file.lastPathComponent, file: file)
            return nil
        }
    }

    /// The share sheet closed: the copy in the app's own folder goes.
    func finishSharing() {
        guard let done = sharing else { return }
        sharing = nil
        try? FileManager.default.removeItem(at: done.file.deletingLastPathComponent())
    }

    func postModpack(_ strings: Strings) async {
        guard let made = made else { return }
        await work("discord", strings) { client, id in
            let posted = try await client.postModpack(serverId: id, token: made.token)
            return Said(text: strings.t(posted.asLink ? "make.posted_link" : "make.posted", ["count": posted.posted]), good: true)
        }
    }

    // MARK: - Download link

    /// The download link and the server resource pack, once read.
    private(set) var link: DownloadLink? = nil
    /// Set while "Use a link…" asks for the pack's link and SHA-1.
    var askingPackLink = false

    /// Read on its own, not as a "work": the mods list may be loading at the same time.
    func loadLink() async {
        guard let client = client() else { return }
        do {
            link = try await client.downloadLink(serverId: serverId)
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    func switchLink(_ on: Bool, _ strings: Strings) async {
        await work("link", strings) { [weak self] client, id in
            self?.link = try await client.setDownloadLink(serverId: id, on: on)
            return nil
        }
    }

    func newLink(_ strings: Strings) async {
        await work("link", strings) { [weak self] client, id in
            self?.link = try await client.newDownloadLink(serverId: id)
            return nil
        }
    }

    func uploadPack(_ file: PickedFile, _ strings: Strings) async {
        defer { try? FileManager.default.removeItem(at: file.url) }
        let required = link?.resourcePack?.required ?? false
        await work("pack", strings) { [weak self] client, id in
            self?.link = try await client.uploadResourcePack(serverId: id, file: file, required: required)
            return nil
        }
    }

    func setPackLink(_ url: String, _ sha1: String, _ strings: Strings) async {
        askingPackLink = false
        let required = link?.resourcePack?.required ?? false
        await work("pack", strings) { [weak self] client, id in
            self?.link = try await client.setResourcePackLink(serverId: id, url: url, sha1: sha1, required: required)
            return nil
        }
    }

    func setPackRequired(_ required: Bool, _ strings: Strings) async {
        await work("pack", strings) { [weak self] client, id in
            self?.link = try await client.setResourcePackRequired(serverId: id, required: required)
            return nil
        }
    }

    func removePack(_ strings: Strings) async {
        await work("pack", strings) { [weak self] client, id in
            self?.link = try await client.removeResourcePack(serverId: id)
            return nil
        }
    }

    // MARK: - Bedrock packs

    private(set) var addons = PageRead<AddonList>()
    /// Set after a pack change while the server runs: "Restart now" shows.
    var restartOffered = false

    func loadAddons() async {
        guard let client = client() else { return }
        addons.loading = true
        do {
            addons = PageRead(value: try await client.addons(serverId: serverId))
        } catch let problem as AgentError {
            noted(problem)
            addons = PageRead(problem: problem)
        } catch {
            addons.loading = false
        }
    }

    func setAddonsRequired(_ on: Bool, _ strings: Strings) async {
        await work("required", strings, reload: loadAddons) { [weak self] client, id in
            let done = try await client.setAddonsRequired(serverId: id, required: on)
            self?.restartOffered = done.restartNeeded == true
            return Said(text: strings.t(on ? "addons.required_on" : "addons.required_off"), good: true)
        }
    }

    func setAddon(_ addon: Addon, _ on: Bool, _ strings: Strings) async {
        await work("pack:\(addon.uuid)", strings, reload: loadAddons) { [weak self] client, id in
            let done = try await client.setAddonEnabled(serverId: id, uuid: addon.uuid, enabled: on)
            self?.restartOffered = done.restartNeeded == true
            return Said(text: strings.t(on ? "addons.turned_on" : "addons.turned_off", ["name": addon.name]), good: true)
        }
    }

    func removeAddon(_ addon: Addon, _ strings: Strings) async {
        await work("pack:\(addon.uuid)", strings, reload: loadAddons) { [weak self] client, id in
            let done = try await client.removeAddon(serverId: id, uuid: addon.uuid)
            self?.restartOffered = done.restartNeeded == true
            return Said(text: strings.t("addons.removed", ["name": addon.name]), good: true)
        }
    }

    func uploadAddon(_ file: PickedFile, _ strings: Strings) async {
        defer { try? FileManager.default.removeItem(at: file.url) }
        await work("upload", strings, reload: loadAddons) { [weak self] client, id in
            let done = try await client.uploadAddon(serverId: id, file: file)
            self?.restartOffered = done.restartNeeded == true
            let names = done.installed.map(\.name).joined(separator: ", ")
            return Said(text: strings.t("addons.installed_done", ["name": names.isEmpty ? file.name : names]), good: true)
        }
    }

    /// Restart now, after a pack change or a game settings save.
    func restart(_ strings: Strings, reload: @escaping () async -> Void) async {
        restartOffered = false
        gameRestart = false
        await work("restart", strings, reload: reload) { client, id in
            _ = try await client.serverAction(serverId: id, action: "restart")
            return Said(text: strings.t("mobile.server.done_restart"), good: true)
        }
    }

    // MARK: - Game settings

    private(set) var game = PageRead<GameSettings>()
    /// What the person typed or picked, by key, as text.
    var edits: [String: String] = [:]
    /// Each refused value's reason, by key, from the last Save.
    private(set) var fieldProblems: [String: String] = [:]
    /// Saved while running: "Restart now" shows next to the words.
    private(set) var gameRestart = false

    func loadGame() async {
        guard let client = client() else { return }
        game.loading = true
        do {
            game = PageRead(value: try await client.gameSettings(serverId: serverId))
            edits = [:]
            fieldProblems = [:]
        } catch let problem as AgentError {
            noted(problem)
            game = PageRead(problem: problem)
        } catch {
            game.loading = false
        }
    }

    func edit(_ key: String, _ value: String) {
        edits[key] = value
        fieldProblems[key] = nil
    }

    // MARK: - Game rules

    /// Read from the running server only when the screen asks (one question per rule).
    private(set) var rules = PageRead<GameRules>()
    /// Changed values by rule name, waiting for Save rules.
    var ruleEdits: [String: JSONValue] = [:]
    /// What the last change was before, for Undo; empty: nothing to undo.
    private(set) var rulesBefore: [String: JSONValue] = [:]
    private(set) var rulesSaid: String? = nil
    /// Rules of a preset this server doesn't have, from the last change.
    private(set) var rulesSkipped: [String] = []

    func loadRules() async {
        guard let client = client() else { return }
        rules.loading = true
        do {
            rules = PageRead(value: try await client.gameRules(serverId: serverId))
            ruleEdits = [:]
        } catch let problem as AgentError {
            noted(problem)
            rules = PageRead(problem: problem)
        } catch {
            rules.loading = false
        }
    }

    func editRule(_ name: String, _ value: JSONValue) {
        let said = rules.value?.rules.first(where: { $0.name == name })?.value
        ruleEdits[name] = said == value ? nil : value
    }

    /// Saves `changed` (Save rules or Undo), or applies `preset`.
    func saveRules(_ strings: Strings, changed: [String: JSONValue]? = nil, preset: String? = nil) async {
        guard let client = client(), busy == nil else { return }
        busy = "rules"
        defer { busy = nil }
        do {
            let done = try await client.saveGameRules(serverId: serverId, rules: changed ?? ruleEdits, preset: preset)
            if let current = rules.value {
                rules = PageRead(value: GameRules(running: current.running, rules: done.rules, presets: current.presets))
            }
            ruleEdits = [:]
            rulesBefore = done.before
            rulesSkipped = done.skipped
            rulesSaid = done.before.isEmpty
                ? strings.t("gamerules.no_change") : strings.t("gamerules.saved", ["n": done.before.count])
        } catch let problem as AgentError {
            noted(problem)
            rulesSaid = problem.message(strings)
            rulesSkipped = []
        } catch {}
    }

    // MARK: - Server icon

    /// The server icon as base64 PNG; nil while unknown or when there is none.
    private(set) var icon: String? = nil

    func loadIcon() async {
        guard let client = client() else { return }
        do {
            icon = try await client.serverIcon(serverId: serverId)
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    /// `png` is already 64 x 64 (IconCard makes it). Undo is the server's
    /// own Undo, like every change the agent backs up first.
    func saveIcon(_ png: String, _ strings: Strings) async {
        await work("icon", strings, reload: { await self.loadGame() }) { client, id in
            let done = try await client.saveServerIcon(serverId: id, png: png)
            self.icon = png
            let restart = done.running ? " " + strings.t("motd.icon_restart") : ""
            return Said(text: strings.t("motd.icon_saved") + restart, good: true)
        }
    }

    func removeIcon(_ strings: Strings) async {
        await work("icon", strings, reload: { await self.loadGame() }) { client, id in
            _ = try await client.removeServerIcon(serverId: id)
            self.icon = nil
            return Said(text: strings.t("motd.icon_removed"), good: true)
        }
    }

    /// The value a field shows now: what was typed, else what the file says.
    func shown(_ field: GameSetting) -> String {
        edits[field.key] ?? GameForm.initial(field)
    }

    /// True when Save would send something.
    var gameChanged: Bool {
        guard let settings = game.value else { return false }
        return !GameForm.changed(settings, edits).isEmpty
    }

    func saveGame(_ strings: Strings) async {
        guard let settings = game.value, let client = client(), busy == nil else { return }
        let values = GameForm.changed(settings, edits)
        busy = "game"
        said = nil
        do {
            let done = try await client.saveGameSettings(serverId: serverId, values: values)
            said = Said(text: GameForm.saved(strings, done), good: true)
            gameRestart = done.running && done.nothingChanged != true
            busy = nil
            await loadGame()
        } catch let problem as AgentError {
            noted(problem)
            if case .failed(_, _, _, let problems) = problem, !problems.isEmpty {
                fieldProblems = problems
                said = Said(text: strings.t("mobile.game.fix_marked"), good: false)
            } else {
                said = Said(text: problem.message(strings), good: false)
            }
            busy = nil
        } catch {
            busy = nil
        }
    }

    /// Saves the whole file as typed in Technical mode. A refused value
    /// leaves the file as it was, and its reason is shown.
    func saveGameText(_ text: String, _ strings: Strings) async {
        guard let client = client(), busy == nil else { return }
        busy = "game_text"
        said = nil
        do {
            let done = try await client.saveGameSettingsText(serverId: serverId, text: text)
            said = Said(text: GameForm.saved(strings, done), good: true)
            gameRestart = done.running && done.nothingChanged != true
            busy = nil
            await loadGame()
        } catch let problem as AgentError {
            noted(problem)
            if case .failed(_, _, _, let problems) = problem, !problems.isEmpty {
                said = Said(text: problems.keys.sorted().compactMap { key in problems[key].map { "\(key): \($0)" } }.joined(separator: " "), good: false)
            } else {
                said = Said(text: problem.message(strings), good: false)
            }
            busy = nil
        } catch {
            busy = nil
        }
    }

    // MARK: - Doing

    /// One button at a time: says what it did (or the agent's reason it
    /// didn't), then reads the screen again.
    // MARK: - The anti-cheat picker

    private(set) var antiCheat: AntiCheat? = nil

    func loadAntiCheat() async {
        guard let client = client() else { return }
        do {
            antiCheat = try await client.antiCheat(serverId: serverId)
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    // MARK: - Far view

    private(set) var farView: FarView? = nil

    func loadFarView() async {
        guard let client = client() else { return }
        do {
            farView = try await client.farView(serverId: serverId)
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    func chooseFarView(_ choice: String, _ strings: Strings) async {
        await work("far_view", strings, reload: { await self.loadFarView(); await self.loadMods() }) { client, id in
            _ = try await client.chooseFarView(serverId: id, choice: choice)
            return Said(text: strings.t("farview.changed"), good: true)
        }
    }

    func saveFarView(unvisited: Bool, loadAhead: Bool, radius: Int, _ strings: Strings) async {
        let world = farView?.loadAhead?.world ?? ""
        await work("far_view", strings) { client, id in
            self.farView = try await client.farViewOptions(serverId: id, unvisited: unvisited, loadAhead: loadAhead, radius: radius, world: world)
            return Said(text: strings.t("farview.saved"), good: true)
        }
    }

    func rebuildFarView(_ strings: Strings) async {
        await work("far_view", strings) { client, id in
            let done = try await client.rebuildFarView(serverId: id)
            return Said(text: strings.t("farview.rebuilt", ["files": String(done.files), "size": Display.bytes(strings, done.sizeBytes)]), good: true)
        }
    }

    // MARK: - Put it on Modrinth

    private(set) var modrinth: ModrinthShare? = nil
    /// Asking the Modrinth address and summary: for the modpack (`jar` nil) or one own jar.
    var askingModrinth: ModrinthAsk? = nil
    /// Set while "Paste its link…" asks for the Server Project's link.
    var askingServerLink = false

    func loadModrinth() async {
        guard let client = client() else { return }
        do {
            modrinth = try await client.modrinth(serverId: serverId)
        } catch let problem as AgentError {
            noted(problem)
        } catch {}
    }

    /// Opens Modrinth's page in Safari; it comes back as mcsc://modrinth (AppModel.open).
    func signInModrinth(_ strings: Strings) async {
        await work("modrinth", strings) { client, _ in
            let started = try await client.modrinthSignIn()
            if let url = URL(string: started.url) { await UIApplication.shared.open(url) }
            return Said(text: strings.t("modrinth.waiting"), good: true)
        }
    }

    func finishModrinth(_ url: URL, _ strings: Strings) async {
        guard let back = ModrinthCards.cameBack(url) else {
            said = Said(text: strings.t("modrinth.said_no"), good: false)
            return
        }
        await work("modrinth", strings) { [weak self] client, id in
            _ = try await client.modrinthFinish(code: back.code, state: back.state)
            self?.modrinth = try await client.modrinth(serverId: id)
            return Said(text: strings.t("modrinth.welcome"), good: true)
        }
    }

    func signOutModrinth(_ strings: Strings) async {
        await work("modrinth", strings) { [weak self] client, id in
            _ = try await client.modrinthSignOut()
            self?.modrinth = try await client.modrinth(serverId: id)
            return nil
        }
    }

    func uploadToModrinth(_ slug: String, _ summary: String, _ strings: Strings) async {
        let jar = askingModrinth?.jar
        askingModrinth = nil
        await work("modrinth", strings) { [weak self] client, id in
            if let jar = jar {
                self?.modrinth = try await client.modrinthOwnJar(serverId: id, path: jar.path, slug: slug, summary: summary)
            } else {
                self?.modrinth = try await client.modrinthUpload(serverId: id, slug: slug, summary: summary)
            }
            return Said(text: strings.t("modrinth.uploaded"), good: true)
        }
    }

    func setServerProject(_ link: String, _ strings: Strings) async {
        askingServerLink = false
        await work("modrinth", strings) { [weak self] client, id in
            self?.modrinth = try await client.modrinthServerProject(serverId: id, link: link)
            return nil
        }
    }

    private func work(
        _ name: String,
        _ strings: Strings,
        reload: (() async -> Void)? = nil,
        _ call: (AgentClient, String) async throws -> Said?
    ) async {
        guard let client = client(), busy == nil else { return }
        busy = name
        said = nil
        do {
            if let done = try await call(client, serverId) {
                said = done
            }
        } catch let problem as AgentError {
            noted(problem)
            said = Said(text: problem.message(strings), good: false)
        } catch {
            // Reading the picked file on the phone failed: said as such.
            said = Said(text: strings.t("mobile.error.failed", ["reason": error.localizedDescription]), good: false)
        }
        busy = nil
        await reload?()
    }
}

/// The Modrinth address and summary being asked: the modpack, or one own `jar`.
struct ModrinthAsk: Identifiable {
    let jar: ModrinthJar?
    let name: String
    var id: String { jar?.path ?? "modpack" }
}
