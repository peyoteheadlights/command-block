import XCTest
@testable import Core

/// Mods, Plugins, Packs and Game settings: the screens' rules, the words,
/// and the agent's answers (mirrors ContentTest.kt).
final class ContentTests: XCTestCase {
    private let token = "secret-session-token-1234567890"
    private let s = Strings(Shared.strings)

    override func setUp() {
        super.setUp()
        StubAgent.reset()
    }

    private func client() -> AgentClient {
        let value = token
        return AgentClient(
            pairing: Pairing(host: "localhost", port: 8765, fingerprint: String(repeating: "ab", count: 32), apiVersion: 1),
            token: { value },
            configuration: StubAgent.configuration()
        )
    }

    private func body(_ index: Int) -> String {
        String(data: StubAgent.requests[index].body, encoding: .utf8) ?? ""
    }

    private func path(_ index: Int) -> String {
        let url = StubAgent.requests[index].request.url
        return (url?.path ?? "") + (url?.query.map { "?" + $0 } ?? "")
    }

    private func method(_ index: Int) -> String {
        StubAgent.requests[index].request.httpMethod ?? ""
    }

    private func mod(enabled: Bool = true, status: String = "ok", issues: [ModIssue] = []) -> Mod {
        Mod(filename: "a.jar", modId: "a", name: "Alpha", version: "1.0", enabled: enabled, status: status, issues: issues)
    }

    private func json(_ value: [String: JSONValue]) -> String {
        String(data: (try? AgentClient.encode(value)) ?? Data(), encoding: .utf8) ?? ""
    }

    // MARK: - The screen's words

    func testTheScreenIsNamedForWhatTheServerTypeTakes() {
        let row = ServerRow(id: "s", name: "S")
        XCTAssertNil(Content.kind(nil))
        var mods = row
        mods.content = "mods"
        XCTAssertEqual(Content.kind(mods), .mods)
        var plugins = row
        plugins.content = "plugins"
        XCTAssertEqual(Content.kind(plugins), .plugins)
        var bedrock = row
        bedrock.edition = "bedrock"
        bedrock.capabilities = Capabilities(addons: true)
        XCTAssertEqual(Content.kind(bedrock), .packs)
        XCTAssertEqual(Content.kind(row), ContentKind.none)
        XCTAssertEqual(s.t(ContentKind.mods.titleKey), "Mods")
        XCTAssertEqual(s.t(ContentKind.plugins.titleKey), "Plugins")
        XCTAssertEqual(s.t(ContentKind.packs.titleKey), "Packs")
    }

    func testEachModSaysWhetherItWorksAndWhyNot() {
        let missing = ModIssue(kind: "missing_dependency", severity: "error", detail: "Alpha needs Fabric API, which isn't installed.")
        let version = ModIssue(kind: "version_mismatch", severity: "warn", detail: "Alpha is made for Minecraft 1.20.")
        let note = ModIssue(kind: "unverified", severity: "info", detail: "Checked from the file only.")
        let broken = mod(status: "error", issues: [version, note, missing])
        XCTAssertEqual(Content.status(broken).key, "mods.status_error")
        XCTAssertEqual(Content.status(broken).tone, .danger)
        XCTAssertEqual(Content.problems(broken), [missing.detail, version.detail])
        XCTAssertEqual(Content.status(mod(enabled: false, status: "disabled")).key, "mods.status_off")
        XCTAssertEqual(Content.status(mod(status: "something new")).key, "value.unknown")
    }

    func testANewerVersionAndTechnicalDetailsAreShownInTheSharedWords() {
        var newer = mod()
        newer.updateAvailable = ModUpdate(latestVersion: "2.0")
        newer.minecraftRange = ">=1.21"
        XCTAssertEqual(Content.newer(s, newer), "Update: 2.0")
        XCTAssertNil(Content.newer(s, mod()))
        XCTAssertEqual(Content.technical(s, newer), "a · a.jar · For Minecraft >=1.21")
        XCTAssertEqual(Content.change(s, "1.0", "2.0"), "1.0 → 2.0")
        XCTAssertEqual(Content.change(s, nil, "2.0"), "Unknown → 2.0")
    }

    // MARK: - Who may change what, and when

    func testOnlySomeoneAllowedSeesChangeButtonsAndTheyWorkOnlyWhileStopped() {
        XCTAssertEqual(Content.changes(canManage: false, serverState: States.OFFLINE), .notAllowed)
        XCTAssertEqual(Content.changes(canManage: true, serverState: States.OFFLINE), .ready)
        XCTAssertEqual(Content.changes(canManage: true, serverState: States.CRASHED), .ready)
        for state in [States.ONLINE, States.STARTING, States.STOPPING, States.RESTART_PENDING] {
            XCTAssertEqual(Content.changes(canManage: true, serverState: state), .stopFirst, state)
        }
        // A state not known is never taken to be stopped.
        XCTAssertEqual(Content.changes(canManage: true, serverState: nil), .unknown)
        XCTAssertEqual(Content.changes(canManage: true, serverState: States.UNKNOWN), .unknown)
        // Bedrock packs change while running too (a restart is offered after).
        XCTAssertEqual(Content.packChanges(canManage: true), .ready)
        XCTAssertEqual(Content.packChanges(canManage: false), .notAllowed)
        // "Stop the server first" offers Stop only while it would work.
        XCTAssertTrue(Content.canStopHere(States.ONLINE, canControl: true))
        XCTAssertFalse(Content.canStopHere(States.ONLINE, canControl: false))
        XCTAssertFalse(Content.canStopHere(States.STOPPING, canControl: true))
    }

    func testOnlyAKeptVersionThatIsThereAndNotInUseCanBeRolledBackTo() {
        let kept = ModVersion(version: "1.0", archivePath: "p", exists: true)
        XCTAssertTrue(Content.canRollBack(kept))
        var current = kept
        current.isCurrent = true
        XCTAssertFalse(Content.canRollBack(current))
        var gone = kept
        gone.exists = false
        XCTAssertFalse(Content.canRollBack(gone))
    }

    func testHistoryRowsSayWhatHappenedInWords() {
        let row = ModHistoryRow(ts: 1, user: "admin", action: "rollback", modName: "Alpha", result: "ok")
        XCTAssertEqual(Content.action(s, row), "Rolled back")
        XCTAssertEqual(Content.action(s, ModHistoryRow(action: "something_new")), "something_new")
        XCTAssertEqual(Content.historyTone(ModHistoryRow(result: "failed")), .danger)
        XCTAssertEqual(Content.historyName(s, ModHistoryRow(action: "update_all", detail: "Alpha, Beta")), "Alpha, Beta")
        XCTAssertEqual(Content.action(s, ModHistoryRow(action: "update_all")), "Update all")
    }

    func testOnlyTheFilesEachScreenTakesAreSent() {
        XCTAssertTrue(Content.accepts(.mods, "Sodium-0.6.JAR"))
        XCTAssertFalse(Content.accepts(.mods, "pack.mcpack"))
        XCTAssertTrue(Content.accepts(.packs, "skins.mcaddon"))
        XCTAssertFalse(Content.accepts(.packs, "mod.jar"))
        XCTAssertTrue(Content.accepts(.mods, "Pack 1.2.mrpack", modpack: true))
    }

    func testMissingDependenciesAreTheRequiredModsThatArentThere() {
        let report = DependencyReport(items: [
            DependencyItem(modId: "fabric-api", kind: "required", status: "missing"),
            DependencyItem(modId: "minecraft", kind: "required", platform: true, status: "platform_incompatible"),
            DependencyItem(modId: "modmenu", kind: "optional", status: "missing"),
            DependencyItem(modId: "cloth", kind: "required", status: "satisfied"),
        ])
        XCTAssertEqual(Content.missing(report).map(\.modId), ["fabric-api"])
        XCTAssertEqual(Content.missing(nil), [])
    }

    // MARK: - Modrinth

    func testTheInstallConfirmationListsVersionSizeLicenceAndSides() {
        let project = ModProject(
            project: ModrinthProject(slug: "sodium", title: "Sodium", license: "LGPL-3.0", serverSide: "unsupported", clientSide: "required"),
            versions: [
                ModrinthVersion(
                    versionId: "v1", versionNumber: "0.6.0", gameVersions: ["1.21.1"],
                    dependencies: [ModrinthDependency(projectId: "P1", type: "required"), ModrinthDependency(projectId: "P2", type: "optional")],
                    file: ModrinthFile(filename: "sodium.jar", size: 2048)
                ),
            ]
        )
        let version = Install.latest(project)!
        XCTAssertEqual(Install.required(version).map(\.projectId), ["P1"])
        XCTAssertEqual(Install.facts(s, project, version), [
            "Sodium 0.6.0", "Size: 2.0 KB", "For Minecraft 1.21.1", "Licence: LGPL-3.0",
            "Needed on the server: No", "Needed in the game: Yes",
        ])
        XCTAssertEqual(Install.downloads(s, 1_234_567), "1.2M downloads")
        XCTAssertEqual(Install.downloads(s, nil), "Unknown")
        XCTAssertEqual(Install.page("https://modrinth.com/mod/sodium")?.absoluteString, "https://modrinth.com/mod/sodium")
        XCTAssertNil(Install.page("https://example.com/mod/sodium"))
    }

    func testTheMissingOnesSayWhatHappenedToEachInTheAgentsThreeAnswers() {
        let results = [
            DependencyResult(title: "Fabric API", result: "installed"),
            DependencyResult(title: "Cloth", result: "skipped", detail: "Already installed"),
            DependencyResult(title: "Iris", result: "failed", detail: "Modrinth stopped answering."),
        ]
        XCTAssertEqual(
            Install.missingDone(s, results),
            "Done: Fabric API (Worked), Cloth (Already there), Iris (Failed: Modrinth stopped answering.)"
        )
        XCTAssertFalse(Install.allWorked(results))
        XCTAssertTrue(Install.allWorked(Array(results.prefix(2))))
    }

    // MARK: - Update all

    private let plan = UpdateAllPlan(updates: [
        UpdateAllRow(modId: "a", name: "Alpha", filename: "a.jar", enabled: true, installedVersion: "1.0", latestVersion: "2.0", versionId: "va"),
        UpdateAllRow(modId: "b", name: "Beta", filename: "b.jar.disabled", enabled: false, installedVersion: "1.0", latestVersion: "1.1", versionId: "vb"),
        UpdateAllRow(modId: "c", name: "Gamma", filename: "c.jar", enabled: true, installedVersion: "3", latestVersion: "4", versionId: nil),
    ])

    func testUpdateAllTicksTheOnesThatAreOnAndLeavesTurnedOffOnesForThePerson() throws {
        XCTAssertEqual(UpdateAll.ticked(plan), ["a.jar", "c.jar"])
        let body = String(data: try AgentClient.encode(UpdateAll.body(plan, ticked: ["c.jar", "b.jar.disabled"])), encoding: .utf8)
        XCTAssertEqual(body, #"{"mods":[{"filename":"b.jar.disabled","version_id":"vb"},{"filename":"c.jar","version_id":null}]}"#)
        var newer = mod()
        newer.updateAvailable = ModUpdate(latestVersion: "2.0")
        XCTAssertTrue(UpdateAll.offered(ModList(mods: [newer])))
        XCTAssertFalse(UpdateAll.offered(ModList(mods: [mod()])))
        XCTAssertFalse(UpdateAll.offered(nil))
    }

    func testTheSameJobCardOffersTurnOffOnThePicksOthersOrOnEachOneWithNoPick() throws {
        let list = try JSONDecoder().decode(ModList.self, from: Data(#"{"mods": [], "server_state": "OFFLINE", "overlaps": [{"kind": "same_mod", "why": "Sodium is here 2 times.", "keep": "new.jar", "say": "Keep 0.5.2, turn off 0.5.1", "mods": [{"filename": "old.jar", "name": "Sodium", "version": "0.5.1", "turn_off": true, "needed_by": ""}, {"filename": "new.jar", "name": "Sodium", "version": "0.5.2", "turn_off": false, "needed_by": ""}]}, {"kind": "same_job", "why": "They all draw a web map of the world.", "keep": "", "say": "These do the same job. Keep the one you use.", "mods": [{"filename": "dynmap.jar", "name": "Dynmap", "version": "3.7", "turn_off": false, "needed_by": "Dynmap Addon"}, {"filename": "squaremap.jar", "name": "squaremap", "version": "1.2", "turn_off": false, "needed_by": ""}]}]}"#.utf8))
        XCTAssertEqual(list.overlaps.count, 2)
        XCTAssertEqual(list.overlaps[0].say, "Keep 0.5.2, turn off 0.5.1")
        XCTAssertEqual(SameJob.turnOff(list.overlaps[0]).map(\.filename), ["old.jar"])
        XCTAssertEqual(SameJob.turnOff(list.overlaps[1]).map(\.filename), ["squaremap.jar"])
        XCTAssertEqual(ModList().overlaps, [])
    }

    func testServerPlayersAndBothSayWhereTheMarkCameFrom() throws {
        let list = try JSONDecoder().decode(ModList.self, from: Data(#"{"mods": [{"filename": "a.jar", "side": "both", "side_from": "unknown", "loader": "forge"}, {"filename": "b.jar", "side": "server", "side_from": "modrinth"}, {"filename": "p.jar", "side": "server", "side_from": "file", "loader": "paper"}], "server_state": "OFFLINE", "player_only": [{"filename": "map.jar", "name": "Minimap", "kind": "mod", "size_bytes": 10, "changed": true}, {"filename": "f.zip", "missing": true, "site": "modrinth"}], "player_only_options": {"backup": true, "skip_listed": true}, "odd_setups": [{"filename": "a.jar", "text": "Alpha needs Beta."}]}"#.utf8))
        let a = list.mods[0], b = list.mods[1], plugin = list.mods[2]
        XCTAssertEqual(Sides.words(s, a), "For: Both (not sure, the mod doesn't say)")
        XCTAssertEqual(Sides.words(s, b), "For: Server (from Modrinth)")
        XCTAssertNil(Sides.words(s, plugin))
        XCTAssertEqual(Sides.picks(a), ["server", "players"])
        XCTAssertEqual(Sides.picks(b), ["players", "both"])
        XCTAssertEqual(Sides.picks(plugin), [])
        XCTAssertEqual(list.playerOnly.first?.name, "Minimap")
        XCTAssertEqual(list.playerOnly.map(\.changed), [true, false])
        XCTAssertEqual(list.playerOnly.map(\.missing), [false, true])
        XCTAssertEqual(list.playerOnlyOptions, PlayerOnlyOptions(backup: true, skipListed: true))
        XCTAssertEqual(ModList().playerOnlyOptions, PlayerOnlyOptions())
        XCTAssertEqual(list.oddSetups.first?.text, "Alpha needs Beta.")
        XCTAssertTrue(Sides.accepts("Faithful.ZIP"))
        XCTAssertFalse(Sides.accepts("pack.mrpack"))
    }

    func testALinkedModpacksCardAndModpackUpdatesQuestion() throws {
        let list = try JSONDecoder().decode(ModList.self, from: Data(#"{"mods": [{"filename": "mine.jar", "added_by_you": true}], "server_state": "OFFLINE", "modpack": {"name": "Test Pack", "site": "Modrinth", "version": "1.2.0", "update": {"version_id": "v2", "version": "1.3.0"}, "added_by_you": 1}}"#.utf8))
        var pack = try XCTUnwrap(list.modpack)
        XCTAssertEqual(LinkedPacks.lines(s, pack), ["Made from Test Pack 1.2.0 on Modrinth.", "Version 1.3.0 is out.", "1 mod was added by you. Modpack update keeps it."])
        pack.update = LinkedModpackUpdate(versionId: "cf:1-2")
        pack.addedByYou = 0
        XCTAssertEqual(LinkedPacks.lines(s, pack)[1], "A new version is out.")
        XCTAssertEqual(list.mods.first?.addedByYou, true)
        let preview = try JSONDecoder().decode(ModpackUpdatePreview.self, from: Data(#"{"token": "t", "pack": {"name": "Test Pack", "type_name": "Fabric"}, "changes": {"to_version": "1.3.0", "added": ["ferrite.jar"], "removed": [], "newer": [{"name": "l2.jar", "from": "l.jar", "to": "l2.jar"}], "added_by_you": ["mine.jar"], "configs_changed_by_you": [], "version_change": true, "minecraft_version": "1.21.1", "problems": []}}"#.utf8))
        XCTAssertEqual(LinkedPacks.changes(s, preview.changes), ["Newer: l.jar → l2.jar", "Added: ferrite.jar", "Kept, added by you: mine.jar"])
        XCTAssertEqual(LinkedPacks.steps(s, preview).last, "The server is set up as Fabric 1.21.1 first.")
        XCTAssertNil(ModList().modpack)
    }

    func testAnotherOwnersModpackListsItsOwnJarsAndThePluginsToGet() throws {
        let pack = try JSONDecoder().decode(Modpack.self, from: Data(#"{"name": "P", "own_jars": ["mods/a.jar"], "plugins_to_get": ["EssentialsX"], "can_import": true}"#.utf8))
        let notes = Modpacks.notes(s, pack)
        XCTAssertEqual(notes.first, "Get these plugins yourself: EssentialsX")
        XCTAssertTrue(notes[1].hasPrefix("Not checked by a site: mods/a.jar."), notes[1])
        XCTAssertEqual(Modpacks.notes(s, Modpack()), [])
        let changes = try JSONDecoder().decode(ModpackChanges.self, from: Data(#"{"new_own_jars": ["mods/a.jar"]}"#.utf8))
        XCTAssertTrue(LinkedPacks.changes(s, changes).first?.hasPrefix("Not checked by a site") == true)
    }

    func testTheStillFitCardReadsTheAgentsAnswerAndChangesTheseAsOneUpdateAll() throws {
        let list = try JSONDecoder().decode(ModList.self, from: Data(#"{"mods": [], "server_state": "OFFLINE", "fit": {"about": "Create 6.0", "needs_change": [{"mod_id": "r", "name": "Rails", "filename": "r.jar", "why": "Rails needs Create below 6", "from": "1.0", "to": "2.0", "version_id": "R2"}], "no_fit": [{"mod_id": "g", "name": "Gamma", "filename": "g.jar", "why": "No version for Minecraft 1.21.4 yet"}], "cant_tell": []}}"#.utf8))
        let fit = try XCTUnwrap(list.fit)
        XCTAssertEqual(StillFit.lead(s, fit, plugins: false), "1 mod needs a different version to go with Create 6.0.")
        var none = fit
        none.needsChange = []
        XCTAssertEqual(StillFit.lead(s, none, plugins: true), "Some plugins may not go with Create 6.0.")
        let plan = StillFit.plan(fit)
        XCTAssertEqual(UpdateAll.ticked(plan), ["r.jar"])
        let body = String(data: try AgentClient.encode(UpdateAll.body(plan, ticked: UpdateAll.ticked(plan))), encoding: .utf8)
        XCTAssertEqual(body, #"{"mods":[{"filename":"r.jar","version_id":"R2"}]}"#)
        XCTAssertEqual(fit.noFit.first?.why, "No version for Minecraft 1.21.4 yet")
        XCTAssertNil(ModList().fit)
    }

    func testUpdateAllSaysWhyItCantStartAndWhatItDid() {
        var running = plan
        running.serverRunning = true
        XCTAssertEqual(UpdateAll.blocked(s, running), s.t("mobile.content.stop_first"))
        var busy = plan
        busy.blockedBy = "Backing up"
        XCTAssertEqual(UpdateAll.blocked(s, busy), "Wait for Backing up to finish first.")
        XCTAssertNil(UpdateAll.blocked(s, plan))
        let done = UpdateAllResult(updated: [UpdateAllDone(name: "Alpha", previousVersion: "1.0", version: "2.0")], safetyBackup: SafetyBackup(name: "before-update-all.zip"))
        XCTAssertEqual(UpdateAll.done(s, done), "Updated 1. The backup made first is before-update-all.zip.")
        let job = Job(id: "j", kind: "mods", state: "running", step: "Updating Alpha (1 of 2)", serverId: "s")
        XCTAssertEqual(UpdateAll.progress(job, serverId: "s"), "Updating Alpha (1 of 2)")
        XCTAssertNil(UpdateAll.progress(job, serverId: "other"))
        var finished = job
        finished.state = "succeeded"
        XCTAssertNil(UpdateAll.progress(finished, serverId: "s"))
    }

    // MARK: - Modpacks

    func testAModpackImportListsItsStepsWithTheVersionChangeOnlyWhenNeeded() {
        let inspected = ModpackInspected(
            token: "t",
            pack: Modpack(name: "Pack", minecraftVersion: "1.21.1", typeName: "Fabric", downloadCount: 3, canImport: true),
            versionChange: true
        )
        let steps = Modpacks.steps(s, inspected)
        XCTAssertEqual(steps.count, 5)
        XCTAssertEqual(steps[2], s.t("pack.step_version", ["type": "Fabric", "version": "1.21.1"]))
        var same = inspected
        same.versionChange = false
        XCTAssertEqual(Modpacks.steps(s, same).count, 4)
        XCTAssertEqual(Modpacks.fileStatus(ModpackFile(status: "client_only")).key, "pack.status_client")
        XCTAssertEqual(Modpacks.fileStatus(ModpackFile(status: "refused")).tone, .danger)
    }

    // MARK: - Game settings

    private func setting(_ key: String, _ kind: String, _ value: JSONValue?, set: Bool? = nil, fallback: String = "", readOnly: String? = nil) -> GameSetting {
        GameSetting(key: key, kind: kind, value: value, set: set ?? (value != nil), minecraftDefault: fallback, readOnly: readOnly)
    }

    private var settings: GameSettings {
        GameSettings(exists: true, fields: [
            setting("difficulty", "choice", nil, fallback: "easy"),
            setting("max-players", "int", .number(20), fallback: "20"),
            setting("pvp", "bool", .bool(true), fallback: "true"),
            setting("white-list", "bool", nil, fallback: "false"),
            setting("motd", "text", .string("Hi"), fallback: "A Minecraft Server"),
            setting("level-seed", "seed", .string("123"), fallback: "", readOnly: "world_exists"),
            setting("server-port", "port", .number(25565), fallback: "25565"),
        ])
    }

    func testGameSettingsAreGroupedAsOnThePCWithEachFieldsHint() {
        let groups = GameForm.groups(settings)
        XCTAssertEqual(groups.map(\.titleKey), ["game.group_play", "game.group_who", "game.group_world"])
        XCTAssertEqual(groups[0].fields.map(\.key), ["difficulty", "pvp"])
        XCTAssertEqual(groups[1].fields.map(\.key), ["motd", "max-players", "white-list"])
        let difficulty = settings.fields[0]
        XCTAssertEqual(GameForm.initial(difficulty), "easy")
        XCTAssertEqual(GameForm.choiceLabel(s, "easy"), "Easy")
        XCTAssertEqual(GameForm.hint(s, difficulty), s.t("game.difficulty_hint") + " " + s.t("game.not_set", ["value": "Easy"]))
        XCTAssertEqual(GameForm.initial(settings.fields[2]), "true")
        XCTAssertEqual(GameForm.initial(settings.fields[3]), "false")
        XCTAssertEqual(GameForm.initial(settings.fields[1]), "20")
        // A value the file has but Minecraft won't take is shown as the file has it, never
        // replaced by the default, and isn't written back unless it is changed.
        var bad = setting("view-distance", "int", nil, set: true, fallback: "10")
        bad.problem = "not a number"
        bad.raw = "ten"
        XCTAssertEqual(GameForm.initial(bad), "ten")
        let one = GameSettings(exists: true, fields: [bad], running: false, restartNeeded: false)
        XCTAssertTrue(GameForm.changed(one, ["view-distance": "ten"]).isEmpty)
        XCTAssertTrue(GameForm.fileProblem(s, bad)?.hasSuffix("not a number") == true)
        XCTAssertEqual(GameForm.label(s, setting("allow-list", "bool", nil)), "Allowlist")
    }

    func testTheSeedIsLockedOnceTheWorldExistsAndEverythingForSomeoneWhoMayNotEdit() {
        let seed = settings.fields.first { $0.key == "level-seed" }!
        XCTAssertTrue(GameForm.locked(seed, canEdit: true))
        XCTAssertEqual(GameForm.hint(s, seed), s.t("game.seed_locked"))
        XCTAssertFalse(GameForm.locked(settings.fields[0], canEdit: true))
        XCTAssertTrue(GameForm.locked(settings.fields[0], canEdit: false))
    }

    func testSaveSendsOnlyWhatChangedTypedAsTheAgentTakesIt() {
        let edits = [
            "difficulty": "hard", "max-players": "20", "pvp": "false", "motd": "Hi",
            "level-seed": "999", "server-port": "25570", "white-list": "false",
        ]
        XCTAssertEqual(json(GameForm.changed(settings, edits)), #"{"difficulty":"hard","pvp":false,"server-port":25570}"#)
        XCTAssertEqual(GameForm.changed(settings, [:]), [:])
        XCTAssertEqual(json(GameForm.changed(settings, ["max-players": "lots"])), #"{"max-players":"lots"}"#)
        XCTAssertEqual(GameForm.saved(s, GameSettingsSaved(keys: ["pvp"], running: true)), s.t("game.saved_running"))
        XCTAssertEqual(GameForm.saved(s, GameSettingsSaved(nothingChanged: true)), s.t("settings.nothing_changed"))
    }

    // MARK: - Icons

    func testAnIconFromAnywhereButModrinthsOwnAddressIsNeverLoaded() async {
        let icons = IconCache(directory: nil)
        XCTAssertTrue(icons.allowed("https://cdn.modrinth.com/data/abc/icon.png"))
        XCTAssertFalse(icons.allowed("http://cdn.modrinth.com/data/abc/icon.png"))
        XCTAssertFalse(icons.allowed("https://evil.example/icon.png"))
        let evil = await icons.load("https://evil.example/icon.png")
        XCTAssertNil(evil)
        let none = await icons.load(nil)
        XCTAssertNil(none)
    }

    func testAnIconIsLoadedOnceThenKept() async {
        let icons = IconCache(directory: nil, configuration: StubAgent.configuration(), hosts: ["localhost"], httpsOnly: false)
        StubAgent.enqueue(StubAgent.Answer(status: 200, headers: ["Content-Type": "image/png", "Cache-Control": "max-age=86400"], body: Data([1, 2, 3])))
        let first = await icons.load("http://localhost/data/abc/icon.png")
        let second = await icons.load("http://localhost/data/abc/icon.png")
        XCTAssertEqual(first, Data([1, 2, 3]))
        XCTAssertEqual(second, Data([1, 2, 3]))
        XCTAssertEqual(StubAgent.requests.count, 1)
    }

    func testAnIconTooBigIsNotLoaded() async {
        let icons = IconCache(directory: nil, configuration: StubAgent.configuration(), hosts: ["localhost"], httpsOnly: false)
        StubAgent.enqueue(StubAgent.Answer(status: 200, headers: ["Content-Type": "image/png"], body: Data(count: IconCache.MAX_ICON_BYTES + 1)))
        let huge = await icons.load("http://localhost/data/abc/huge.png")
        XCTAssertNil(huge)
    }

    // MARK: - The agent

    func testAFoundModpackSaysItsSiteLoaderVersionDownloadsAndWhatItHolds() async throws {
        let c = client()
        StubAgent.json(#"{"hits": [{"project_id": "curseforge:9", "slug": "curseforge:9", "title": "Cozy", "source": "curseforge", "source_name": "CurseForge", "loaders": ["Forge"]}, {"project_id": "xyz", "slug": "xyz", "title": "Pack", "downloads": 1234, "source_name": "Modrinth", "loaders": ["Fabric"], "minecraft_version": "1.21.10", "server_side": "unsupported", "disclosures": ["Shows ads"]}], "total": 2, "problems": ["Modrinth isn't available right now."]}"#)
        let found = try await c.searchModpacks(query: "cozy pack")
        XCTAssertEqual(found.problems, ["Modrinth isn't available right now."])
        XCTAssertEqual(path(0), "/api/modpacks/search?q=cozy%20pack&limit=20")
        XCTAssertEqual(
            Modpacks.about(s, found.hits[1]),
            "Modrinth  ·  Fabric 1.21.10  ·  1,234 downloads  ·  Made for players' games, not servers  ·  Shows ads"
        )
        StubAgent.json(#"{"token": "t", "pack": {"name": "Cozy", "can_import": true}}"#)
        let read = try await c.fetchModpack(found.hits[0], serverId: nil)
        XCTAssertEqual(read.pack.name, "Cozy")
        XCTAssertEqual(path(1), "/api/modpacks/curseforge")
        XCTAssertEqual(body(1), #"{"project":"curseforge:9","server_id":""}"#)
    }

    func testARequiredModpackAndItsChatLineAreSentWhenTheCompanionIsOnTheServer() async throws {
        let c = client()
        StubAgent.json(#"{"name": "Friends", "version": "2", "configs": [], "players_loaders": [], "discord": false, "discord_limit_bytes": 1, "required_to_join": {"available": true, "on": true}, "chat_line": {"on": false, "text": "Get {name}"}}"#)
        let options = try await c.makeModpackOptions(serverId: "s")
        XCTAssertTrue(options.requiredToJoin && options.requiredOn)
        XCTAssertEqual(options.chatLine, ChatLine(on: false, text: "Get {name}"))

        StubAgent.json(#"{"ok": true, "token": "t1", "filename": "Friends 2.mrpack", "files": 1, "size_bytes": 1}"#)
        _ = try await c.makeModpack(serverId: "s", MakeModpack(name: "Friends", version: "2", required: true, chatLine: ChatLine(on: false, text: "Get {name}")))
        XCTAssertEqual(
            body(1),
            #"{"chat_line":{"on":false,"text":"Get {name}"},"configs":[],"leave_out_server_only":true,"name":"Friends","players_loader":"fabric","required":true,"version":"2"}"#
        )
    }

    func testTheTitleScreenIsReadAndSentWithTheModpack() async throws {
        let c = client()
        StubAgent.json(#"{"name": "Friends", "version": "2", "required_to_join": {"available": true, "on": false}, "title_screen": {"button": {"on": true, "address": "play.example.net"}, "flavor_text": {"on": false, "text": ""}, "background": {"on": false}, "background_set": true}}"#)
        let options = try await c.makeModpackOptions(serverId: "s")
        XCTAssertEqual(options.titleScreen.button, TitleScreen.Button(on: true, address: "play.example.net"))
        XCTAssertTrue(options.titleScreen.backgroundSet)

        StubAgent.json(#"{"ok": true, "token": "t1", "filename": "Friends 2.mrpack", "files": 1, "size_bytes": 1}"#)
        let screen = TitleScreen(button: .init(on: true, address: "play.example.net"), flavorText: .init(on: true, text: "Dragons!"), background: .init(on: true))
        _ = try await c.makeModpack(serverId: "s", MakeModpack(name: "Friends", version: "2", titleScreen: screen))
        XCTAssertTrue(body(1).contains(#""title_screen":{"background":{"on":true},"button":{"address":"play.example.net","on":true},"flavor_text":{"on":true,"text":"Dragons!"}}"#), body(1))
    }

    func testMakeAModpackSendsTheAnswersThenDownloadsItAndPostsItInDiscord() async throws {
        let c = client()
        StubAgent.json(#"{"name": "Friends", "version": "2", "configs": [{"path": "config/a.toml", "size_bytes": 9}], "players_loaders": [{"id": "fabric", "name": "Fabric"}], "discord": true, "discord_limit_bytes": 10485760, "required_to_join": {"available": false, "needs_fabric": true}}"#)
        let options = try await c.makeModpackOptions(serverId: "s")
        XCTAssertEqual(options.configs.first?.path, "config/a.toml")
        XCTAssertFalse(options.requiredToJoin)
        XCTAssertTrue(options.needsFabric)
        XCTAssertEqual(options.chatLine, ChatLine())
        XCTAssertEqual(path(0), "/api/servers/s/modpack/make")

        StubAgent.json(#"{"ok": true, "token": "t1", "filename": "Friends 2.mrpack", "files": 3, "size_bytes": 2048, "own_files": ["overrides/mods/x.jar"], "trust_note": "Only share it with people you trust.", "get_yourself": [{"name": "Secret Mod", "page": null}], "plugins_to_get": [], "left_out": ["Ledger"], "discord": true}"#)
        let made = try await c.makeModpack(serverId: "s", MakeModpack(name: "Friends", version: "2", configs: ["config/a.toml"]))
        XCTAssertEqual(method(1), "POST")
        XCTAssertEqual(
            body(1),
            #"{"configs":["config/a.toml"],"leave_out_server_only":true,"name":"Friends","players_loader":"fabric","required":false,"version":"2"}"#
        )
        let lines = MadeModpacks.lines(s, made)
        XCTAssertTrue(lines[0].hasPrefix("Friends 2.mrpack is ready"), lines[0])
        XCTAssertTrue(lines[1].contains("people you trust"))
        XCTAssertTrue(lines.contains { $0.hasSuffix("Secret Mod") })
        XCTAssertTrue(lines.contains { $0.hasSuffix("Ledger") })

        let zip = Data([80, 75, 3, 4])
        StubAgent.enqueue(StubAgent.Answer(status: 200, headers: ["Content-Type": "application/zip"], body: zip))
        let out = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mrpack")
        defer { try? FileManager.default.removeItem(at: out) }
        XCTAssertEqual(try await c.downloadModpack(serverId: "s", made: made, to: out), 4)
        XCTAssertEqual(StubAgent.requests[2].request.url?.path, "/api/servers/s/modpack/made/t1")

        StubAgent.json(#"{"ok": true, "posted": 1}"#)
        XCTAssertEqual(try await c.postModpack(serverId: "s", token: "t1").posted, 1)
        XCTAssertEqual(path(3), "/api/servers/s/modpack/made/discord")
        XCTAssertEqual(body(3), #"{"token":"t1"}"#)
    }

    func testTheDownloadLinkTurnsOnAndTheResourcePackIsSetByLinkAndRemoved() async throws {
        let c = client()
        StubAgent.json(#"{"on": false, "why_not": null, "listening": false, "modpack_url": null, "speed_cap_bytes": 4194304, "resource_pack": null, "max_pack_bytes": 262144000}"#)
        let off = try await c.downloadLink(serverId: "s")
        XCTAssertEqual(path(0), "/api/servers/s/download-link")
        XCTAssertEqual(DownloadLinks.packLines(s, off), ["No resource pack."])
        XCTAssertTrue(DownloadLinks.lines(s, off, owner: false).first?.contains("owner") == true)

        let on = #"{"on": true, "listening": true, "modpack_url": "http://100.64.0.1:25590/abc/modpack.mrpack", "speed_cap_bytes": 4194304, "resource_pack": {"source": "file", "filename": "Textures.zip", "url": "http://100.64.0.1:25590/abc/resource-pack.zip", "sha1": "aa", "size_bytes": 2048, "required": true, "served": true}, "max_pack_bytes": 262144000, "restart_needed": true}"#
        StubAgent.json(on)
        let link = try await c.setDownloadLink(serverId: "s", on: true)
        XCTAssertEqual(method(1), "POST")
        XCTAssertEqual(body(1), #"{"on":true}"#)
        XCTAssertTrue(DownloadLinks.lines(s, link, owner: true)[0].hasSuffix("/abc/modpack.mrpack"))
        let pack = DownloadLinks.packLines(s, link)
        XCTAssertTrue(pack[0].hasPrefix("Textures.zip") && pack[0].contains("served by the download link"), pack[0])
        XCTAssertTrue(pack.last?.hasPrefix("Restart the server") == true)

        StubAgent.json(on)
        _ = try await c.setResourcePackLink(serverId: "s", url: "https://packs.example.com/t.zip", sha1: "", required: false)
        XCTAssertEqual(path(2), "/api/servers/s/resource-pack")
        XCTAssertEqual(body(2), #"{"required":false,"sha1":"","url":"https://packs.example.com/t.zip"}"#)

        StubAgent.json(on)
        _ = try await c.setResourcePackRequired(serverId: "s", required: true)
        XCTAssertEqual(body(3), #"{"required":true}"#)

        StubAgent.json(#"{"on": true, "listening": true, "speed_cap_bytes": 1, "max_pack_bytes": 1}"#)
        XCTAssertNil(try await c.removeResourcePack(serverId: "s").resourcePack)
        XCTAssertEqual(method(4), "DELETE")

        StubAgent.json(on)
        _ = try await c.newDownloadLink(serverId: "s")
        XCTAssertEqual(path(5), "/api/servers/s/download-link/new")
    }

    func testSignInWithModrinthComesBackAsAnMcscLinkAndTheModpackGoesUp() async throws {
        let c = client()
        StubAgent.json(#"{"url": "https://modrinth.com/auth/authorize?state=s1"}"#)
        XCTAssertEqual(try await c.modrinthSignIn().url, "https://modrinth.com/auth/authorize?state=s1")
        XCTAssertEqual(path(0), "/api/modrinth/sign-in")
        XCTAssertEqual(body(0), #"{"return_to":"phone"}"#)

        let back = ModrinthCards.cameBack(URL(string: "mcsc://modrinth?state=x.1.phone.y&code=abc")!)
        XCTAssertEqual(back?.code, "abc")
        XCTAssertEqual(back?.state, "x.1.phone.y")
        XCTAssertNil(ModrinthCards.cameBack(URL(string: "mcsc://modrinth?state=x&error=access_denied")!))
        XCTAssertNil(ModrinthCards.cameBack(URL(string: "mcsc://server/abc?code=1&state=2")!))

        StubAgent.json(#"{"available": true, "signed_in": true, "username": "mark", "ran_out": false}"#)
        XCTAssertTrue(try await c.modrinthFinish(code: "abc", state: "x.1.phone.y").signedIn)
        XCTAssertEqual(body(1), #"{"code":"abc","state":"x.1.phone.y"}"#)

        let card = #"{"account": {"available": true, "signed_in": true, "username": "mark", "ran_out": false}, "made": {"name": "Friends", "version": "2"}, "project": {"id": "p1", "slug": "friends", "title": "Friends", "url": "https://modrinth.com/modpack/friends"}, "uploads": [{"version_id": "v2", "version": "2", "at": 1.0}], "own_jars": [{"path": "mods/a.jar", "name": "A", "side": "both", "slug": "a", "url": "https://modrinth.com/mod/a"}], "server_project": {"id": "s", "slug": "s", "url": "https://modrinth.com/server/s"}, "older": {"listed": "1", "newest": "2"}, "address": "play.example.org"}"#
        StubAgent.json(card)
        let share = try await c.modrinthUpload(serverId: "s", slug: "friends", summary: "Our pack")
        XCTAssertEqual(path(2), "/api/servers/s/modrinth/upload")
        let lines = ModrinthCards.lines(s, share, owner: true)
        XCTAssertEqual(lines[0], "Signed in to Modrinth as mark.")
        XCTAssertTrue(lines[1].contains("https://modrinth.com/modpack/friends"))
        XCTAssertTrue(ModrinthCards.serverLines(s, share)[1].contains("version 1"))
        XCTAssertTrue(ModrinthCards.steps(s, share).contains("play.example.org"))
        XCTAssertEqual(ModrinthCards.slug("Our Friends' Pack!"), "our-friends-pack")

        StubAgent.json(card)
        _ = try await c.modrinthOwnJar(serverId: "s", path: "mods/a.jar", slug: "a", summary: "A mod")
        XCTAssertEqual(body(3), #"{"path":"mods/a.jar","slug":"a","summary":"A mod"}"#)
    }

    func testFarViewListsWhatEachChoiceAddsAndSaysWhyOneCant() async throws {
        let c = client()
        StubAgent.json(#"{"supported": true, "choice": "off", "choices": [{"id": "distanthorizons", "name": "Distant Horizons", "available": true, "server": ["Distant Horizons"], "players": []}, {"id": "voxy", "name": "Voxy", "available": false, "why": "Voxy has nothing for NeoForge on Minecraft 1.21.4 yet.", "server": [], "players": []}], "unvisited": false, "load_ahead": {"on": false, "radius": 2000, "world": "", "chunky": false, "running": false, "night": "1:00-6:00", "main_world": "world"}, "may_change": true}"#)
        let card = try await c.farView(serverId: "s")
        XCTAssertEqual(path(0), "/api/servers/s/far-view")
        XCTAssertEqual(FarViews.status(s, card), "Far view is off.")
        XCTAssertEqual(FarViews.lines(s, card.choices[0], now: card.choice)[1], "The server gets: Distant Horizons")
        XCTAssertEqual(FarViews.lines(s, card.choices[1], now: card.choice)[1], "Voxy has nothing for NeoForge on Minecraft 1.21.4 yet.")
        XCTAssertEqual(card.loadAhead?.night, "1:00-6:00")
        StubAgent.json(#"{"ok": true, "choice": "voxy", "removed": [], "added": ["voxy.jar"]}"#)
        let done = try await c.chooseFarView(serverId: "s", choice: "voxy")
        XCTAssertEqual(done.added, ["voxy.jar"])
        XCTAssertEqual(body(1), #"{"choice":"voxy"}"#)
    }

    func testTheAntiCheatPickerNeverShowsAGuessedPriceAndWarnsAboutTwo() async throws {
        let c = client()
        StubAgent.json(#"{"supported": true, "installed": ["Grim", "Vulcan"], "free": [{"id": "grim", "name": "Grim", "project": "grimac", "on": true, "warning": true}], "paid": [{"id": "vulcan", "name": "Vulcan", "on": true, "link": "https://www.spigotmc.org/resources/83626/", "setup": null, "price": null}, {"id": "polar", "name": "Polar", "on": false, "link": "https://polar.top", "setup": "https://docs.polar.top/getting-started.html", "price": "19.99 USD", "checked": "14 November 2026"}], "may_install": true, "may_add_own_jar": false}"#)
        let picker = try await c.antiCheat(serverId: "s")
        XCTAssertEqual(path(0), "/api/servers/s/anticheat")
        XCTAssertTrue(AntiCheats.status(s, picker).hasPrefix("Two anti-cheats are in: Grim, Vulcan."))
        XCTAssertTrue(AntiCheats.free(s, picker.free[0]).contains { $0.contains("Try it with a friend first") })
        XCTAssertEqual(AntiCheats.paid(s, picker.paid[0])[1], "Price: see the seller's page.")
        XCTAssertEqual(AntiCheats.paid(s, picker.paid[1])[1], "Price: 19.99 USD (checked 14 November 2026).")
    }

    func testTheInstalledListUpdateAllAndHistoryAreRead() async throws {
        let c = client()
        StubAgent.json(#"{"mods": [{"filename": "a.jar", "path": "C:\\s\\mods\\a.jar", "mod_id": "a", "name": "Alpha", "version": "1.0", "enabled": true, "status": "warn", "issues": [{"kind": "version_mismatch", "severity": "warn", "detail": "Made for 1.20"}], "has_history": true, "update_available": {"mod_id": "a", "latest_version": "2.0", "version_id": "va"}}], "problems": [], "minecraft_version": null, "server_state": "OFFLINE", "updates_checked_at": null}"#)
        let list = try await c.mods(serverId: "s")
        XCTAssertEqual(list.mods.first?.updateAvailable?.latestVersion, "2.0")
        XCTAssertNil(list.minecraftVersion)
        XCTAssertEqual(path(0), "/api/servers/s/mods")

        StubAgent.json(#"{"updates": [{"mod_id": "b", "name": "Beta", "filename": "b.jar.disabled", "enabled": false, "installed_version": "1.0", "latest_version": "1.1", "version_id": "vb", "project": "beta"}], "unchecked": [{"mod_id": "c", "name": "Gamma", "filename": "c.jar", "reason": "Modrinth didn't answer."}], "server_running": false, "blocked_by": null, "checked_at": 5.0}"#)
        let plan = try await c.updateAllPlan(serverId: "s")
        XCTAssertEqual(plan.updates.first?.enabled, false)
        XCTAssertEqual(plan.unchecked.first?.reason, "Modrinth didn't answer.")

        StubAgent.json(#"{"ok": true, "job_id": "j", "updated": [{"mod_id": "b", "name": "Beta", "filename": "b-1.1.jar.disabled", "previous_version": "1.0", "version": "1.1"}], "safety_backup": {"id": 3, "name": "before-update-all.zip"}, "undo": {"kind": "restore_backup", "backup_id": 3, "backup": "before-update-all.zip"}}"#)
        let done = try await c.updateAll(serverId: "s", UpdateAll.body(plan, ticked: ["b.jar.disabled"]))
        XCTAssertEqual(done.updated.first?.version, "1.1")
        XCTAssertEqual(done.safetyBackup?.name, "before-update-all.zip")
        XCTAssertEqual(method(2), "POST")
        XCTAssertEqual(path(2), "/api/servers/s/mods/update-all")
        XCTAssertEqual(body(2), #"{"mods":[{"filename":"b.jar.disabled","version_id":"vb"}]}"#)

        StubAgent.json(#"{"history": [{"id": 9, "server_id": "s", "ts": 10.0, "user": "admin", "action": "update_all", "mod_id": null, "mod_name": null, "old_version": null, "new_version": null, "source": null, "sha256": null, "result": "failed", "detail": "Updating Beta failed"}]}"#)
        let history = try await c.modHistory(serverId: "s")
        XCTAssertEqual(history.history.first?.result, "failed")
        XCTAssertEqual(path(3), "/api/servers/s/mods/history?limit=100")
    }

    func testTurningOnRemovingRollingBackAndInstallingSendWhatTheDesktopAppSends() async throws {
        let c = client()
        StubAgent.json(#"{"ok": true, "mod": {"filename": "a.jar.disabled", "mod_id": "a", "name": "Alpha", "version": "1.0", "enabled": false}, "changed": true}"#)
        let off = try await c.setModEnabled(serverId: "s", filename: "a.jar", enabled: false)
        XCTAssertEqual(off.mod?.enabled, false)
        XCTAssertEqual(path(0), "/api/servers/s/mods/disable")
        XCTAssertEqual(body(0), #"{"filename":"a.jar"}"#)

        StubAgent.json(#"{"mod": {}, "dependents": [{"name": "Beta", "mod_id": "b", "filename": "b.jar", "requires": ">=1.0"}], "warning": "Beta needs it."}"#)
        let impact = try await c.modImpact(serverId: "s", filename: "my mod.jar")
        XCTAssertEqual(impact.dependents.first?.name, "Beta")
        XCTAssertEqual(StubAgent.requests[1].request.url?.absoluteString.hasSuffix("/api/servers/s/mods/impact?filename=my%20mod.jar"), true)

        StubAgent.json(#"{"versions": [{"id": 1, "version": "1.0", "filename": "a.jar", "archive_path": "C:\\kept\\a.jar", "created_at": 3.0, "is_current": false, "exists": true}]}"#)
        let kept = try await c.modVersions(serverId: "s", modId: "a").versions[0]
        StubAgent.json(#"{"ok": true, "restored": {"filename": "a.jar", "name": "Alpha", "version": "1.0"}, "previous_version": "2.0"}"#)
        let back = try await c.rollBackMod(serverId: "s", modId: "a", archivePath: kept.archivePath)
        XCTAssertEqual(back.previousVersion, "2.0")
        XCTAssertEqual(body(3), #"{"archive_path":"C:\\kept\\a.jar","mod_id":"a"}"#)

        StubAgent.json(#"{"hits": [{"project_id": "P", "slug": "sodium", "title": "Sodium", "description": "Fast", "downloads": 5, "icon_url": "https://cdn.modrinth.com/i.png", "page": "https://modrinth.com/mod/sodium"}], "total": 1, "offset": 0}"#)
        let found = try await c.searchMods(serverId: "s", query: "so dium")
        XCTAssertEqual(found.hits.first?.iconUrl, "https://cdn.modrinth.com/i.png")
        XCTAssertEqual(StubAgent.requests[4].request.url?.absoluteString.hasSuffix("/api/servers/s/mods/search?q=so%20dium&limit=20&offset=0"), true)

        StubAgent.json(#"{"ok": true, "installed": {"filename": "sodium.jar", "name": "Sodium", "version": "0.6"}, "dependencies_required": [{"mod_id": "fabric-api", "title": "Fabric API", "installed": false}], "dependencies_installed": []}"#)
        let installed = try await c.installMod(serverId: "s", slug: "sodium", versionId: "v1", withDependencies: false)
        XCTAssertEqual(installed.dependenciesRequired.first?.title, "Fabric API")
        XCTAssertEqual(body(5), #"{"install_dependencies":false,"project":"sodium","version_id":"v1"}"#)

        StubAgent.json(#"{"items": [], "unresolvable": [{"mod_id": "x", "name": "X", "required_by": ["Alpha"], "reason": "Not found on Modrinth. Install it manually.", "page": null}], "skipped": [], "minecraft_version": "1.21.1"}"#)
        let missing = try await c.planDependencies(serverId: "s")
        XCTAssertEqual(missing.unresolvable.first?.name, "X")
        XCTAssertEqual(path(6), "/api/servers/s/mods/dependencies/plan")
    }

    func testAFilePickedOnThePhoneIsSentAsAnUploadForm() async throws {
        let c = client()
        let jar = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jar")
        try Data([80, 75, 3, 4]).write(to: jar)
        defer { try? FileManager.default.removeItem(at: jar) }
        StubAgent.json(#"{"ok": true, "installed": {"filename": "e.jar", "name": "Epsilon", "version": "3.0"}}"#)
        let added = try await c.uploadMod(serverId: "s", file: PickedFile(name: "e.jar", url: jar))
        XCTAssertEqual(added.installed?.name, "Epsilon")
        XCTAssertEqual(path(0), "/api/servers/s/mods/upload?confirm=true")
        let type = StubAgent.requests[0].request.value(forHTTPHeaderField: "Content-Type") ?? ""
        XCTAssertTrue(type.hasPrefix("multipart/form-data; boundary="), type)

        StubAgent.json(#"{"token": "tok", "pack": {"name": "Pack", "version": "1.2", "summary": null, "minecraft_version": "1.21.1", "type": "fabric", "type_name": "Fabric", "loader_version": "0.16.5", "files": [{"name": "lithium.jar", "path": "mods/lithium.jar", "size": 10, "status": "download", "reason": null, "env_server": "required"}], "download_count": 1, "download_bytes": 10, "client_only": [], "refused": [], "overrides": 0, "overrides_bytes": 0, "skipped_overrides": [], "problems": [], "can_import": true}, "into": {"version_change": false, "running": false}}"#)
        let pack = try await c.inspectModpack(serverId: "s", file: PickedFile(name: "p.mrpack", url: jar))
        XCTAssertEqual(pack.token, "tok")
        XCTAssertFalse(pack.versionChange)
        XCTAssertEqual(path(1), "/api/modpacks/inspect?server_id=s")
        StubAgent.json(#"{"ok": true, "job_id": "j", "modpack": {"name": "Pack"}, "version_change": false, "undo": null}"#)
        let imported = try await c.importModpack(serverId: "s", token: "tok")
        XCTAssertEqual(imported.name, "Pack")
        XCTAssertEqual(body(2), #"{"confirm":true,"own_jars":false,"token":"tok"}"#)
    }

    func testTheUploadFormHoldsTheFileUnderTheFieldTheAgentReads() throws {
        let jar = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".jar")
        let form = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".form")
        try Data("PK-bytes".utf8).write(to: jar)
        defer {
            try? FileManager.default.removeItem(at: jar)
            try? FileManager.default.removeItem(at: form)
        }
        try AgentClient.writeForm(PickedFile(name: "e\".jar", url: jar), boundary: "B", to: form)
        let text = String(data: try Data(contentsOf: form), encoding: .utf8) ?? ""
        XCTAssertEqual(
            text,
            "--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"e_.jar\"\r\nContent-Type: application/octet-stream\r\n\r\nPK-bytes\r\n--B--\r\n"
        )
    }

    func testBedrockPacksAndGameSettingsAreReadAndSaved() async throws {
        let c = client()
        StubAgent.json(#"{"addons": [{"uuid": "u1", "version": "1.0.0", "name": "Skins", "description": null, "kind": "resource", "folder": "resource_packs/skins", "enabled": true}], "world": "Bedrock level", "running": true, "restart_needed": true}"#)
        let packs = try await c.addons(serverId: "s")
        XCTAssertEqual(packs.world, "Bedrock level")
        XCTAssertEqual(Content.packKind(packs.addons[0]), "addons.kind_resource")
        StubAgent.json(#"{"ok": true, "installed": [], "restart_needed": true}"#)
        let toggled = try await c.setAddonEnabled(serverId: "s", uuid: "u1", enabled: false)
        XCTAssertEqual(toggled.restartNeeded, true)
        XCTAssertEqual(method(1), "PUT")
        XCTAssertEqual(body(1), #"{"enabled":false}"#)

        StubAgent.json(#"{"file": "C:\\s\\server.properties", "exists": true, "modified_at": 1.0, "world_exists": true, "world_folder": "world", "fields": [{"key": "max-players", "kind": "int", "value": 20, "raw": "20", "set": true, "problem": null, "minecraft_default": "20", "choices": [], "min": 1, "max": 10000, "max_length": 200, "read_only": null}], "text": "", "running": false, "restart_needed": false}"#)
        let game = try await c.gameSettings(serverId: "s")
        XCTAssertEqual(GameForm.initial(game.fields[0]), "20")
        StubAgent.json(#"{"detail": "Some values can't be used.", "problems": {"server-port": "Another program is using port 80."}}"#, status: 400)
        do {
            _ = try await c.saveGameSettings(serverId: "s", values: ["server-port": .number(80)])
            XCTFail("A refused port was saved")
        } catch AgentError.failed(_, _, _, let problems) {
            XCTAssertEqual(problems["server-port"], "Another program is using port 80.")
        }
        XCTAssertEqual(method(3), "PUT")
        XCTAssertEqual(body(3), #"{"values":{"server-port":80}}"#)
    }
}
