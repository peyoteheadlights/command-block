import Foundation

// The Mods / Plugins / Packs and Game settings screens' rules. Mirrors
// mobile/android/core's Content.kt rule for rule.

/// What a server's add-ons are called, by its type (glossary words).
public enum ContentKind: Equatable, Sendable {
    case mods
    case plugins
    case packs
    /// Vanilla takes neither, so it has no add-ons screen.
    case none

    public var titleKey: String {
        switch self {
        case .mods, .none: return "page.mods"
        case .plugins: return "page.plugins"
        case .packs: return "mobile.content.packs"
        }
    }
}

/// Whether the add-ons screen's change buttons work now.
public enum Changes: Equatable, Sendable {
    /// The account may only look (a helper without mods.manage).
    case notAllowed
    /// Mods change only while the server is stopped.
    case stopFirst
    /// The server's state isn't known (the PC can't be reached).
    case unknown
    case ready
}

/// The Mods / Plugins / Packs screen's rules (mods.js and addons.js).
public enum Content {
    /// Mods, Plugins or Packs; nil while the server list isn't read yet.
    public static func kind(_ row: ServerRow?) -> ContentKind? {
        guard let row = row else { return nil }
        if row.capabilities?.addons == true { return .packs }
        if row.content == "plugins" { return .plugins }
        if row.content == "mods" || row.capabilities?.mods == true { return .mods }
        return ContentKind.none
    }

    /// Whether the server screen links to it: vanilla's only makes a modpack.
    public static func shown(_ row: ServerRow?) -> Bool {
        guard let kind = kind(row) else { return false }
        return kind != ContentKind.none || row?.edition != "bedrock"
    }

    /// Which change buttons a mod or plugin row offers. Only someone with
    /// mods.manage sees any; they work only while the server is stopped (or
    /// crashed); a state not known is not guessed
    /// to be stopped.
    public static func changes(canManage: Bool, serverState: String?) -> Changes {
        if !canManage { return .notAllowed }
        guard let state = serverState, state != States.UNKNOWN else { return .unknown }
        if state == States.OFFLINE || state == States.CRASHED { return .ready }
        return .stopFirst
    }

    /// Bedrock packs change while the server runs too; they take effect
    /// after a restart, which the screen then offers.
    public static func packChanges(canManage: Bool) -> Changes {
        canManage ? .ready : .notAllowed
    }

    /// "Stop the server first" offers Stop only while Stop would work.
    public static func canStopHere(_ serverState: String?, canControl: Bool) -> Bool {
        canControl && serverState == States.ONLINE
    }

    public static func status(_ mod: Mod) -> StateWords {
        if !mod.enabled { return StateWords(key: "mods.status_off", tone: .neutral) }
        switch mod.status {
        case "ok": return StateWords(key: "mods.status_ok", tone: .success)
        case "warn": return StateWords(key: "mods.status_warn", tone: .warning)
        case "error": return StateWords(key: "mods.status_error", tone: .danger)
        default: return StateWords(key: "value.unknown", tone: .neutral)
        }
    }

    /// Each problem's reason (a missing dependency, the wrong Minecraft
    /// version or server type), worst first; notes for information left out.
    public static func problems(_ mod: Mod) -> [String] {
        let worth = mod.issues.filter { $0.severity == "error" || $0.severity == "warn" }
        let errors = worth.filter { $0.severity == "error" }
        let warnings = worth.filter { $0.severity != "error" }
        return (errors + warnings).map(\.detail).filter { !$0.isEmpty }
    }

    /// Technical words add the mod's id, file and Minecraft versions.
    public static func technical(_ strings: Strings, _ mod: Mod) -> String {
        var parts: [String] = []
        if !mod.modId.isEmpty { parts.append(mod.modId) }
        parts.append(mod.filename)
        if let range = mod.minecraftRange, !range.isEmpty {
            parts.append(strings.t("mods.install_minecraft", ["versions": range]))
        }
        return parts.joined(separator: " · ")
    }

    /// "Update: 2.0" when Modrinth had a newer one at the last check.
    public static func newer(_ strings: Strings, _ mod: Mod) -> String? {
        mod.updateAvailable.map { strings.t("mods.update_to", ["version": $0.latestVersion]) }
    }

    /// "1.0 → 2.0"; Unknown for a side the agent didn't say.
    public static func change(_ strings: Strings, _ old: String?, _ new: String?) -> String {
        let unknown = strings.t("value.unknown")
        let from = (old?.isEmpty == false ? old : nil) ?? unknown
        let to = (new?.isEmpty == false ? new : nil) ?? unknown
        return "\(from) → \(to)"
    }

    private static let actions: Set<String> = [
        "install", "upload", "remove", "update", "rollback", "enable", "disable", "side", "players_add", "players_remove",
        "quarantine", "quarantine_undo",
    ]

    /// What a history row did, in words; the agent's own name for an action the app doesn't know.
    public static func action(_ strings: Strings, _ row: ModHistoryRow) -> String {
        if row.action == "update_all" { return strings.t("mobile.content.update_all") }
        return actions.contains(row.action) ? strings.t("mobile.content.action_" + row.action) : row.action
    }

    public static func historyTone(_ row: ModHistoryRow) -> Tone {
        row.result == "ok" ? .success : .danger
    }

    /// The mod a history row is about; Update all is about several.
    public static func historyName(_ strings: Strings, _ row: ModHistoryRow) -> String {
        row.modName ?? row.modId ?? (row.action == "update_all" ? (row.detail ?? "") : strings.t("value.unknown"))
    }

    /// Only a kept version whose file is still there and isn't in use can
    /// be rolled back to.
    public static func canRollBack(_ version: ModVersion) -> Bool {
        version.exists && !version.isCurrent
    }

    /// The required dependencies still missing: what "Install the missing ones" fetches.
    public static func missing(_ report: DependencyReport?) -> [DependencyItem] {
        (report?.items ?? []).filter { !$0.platform && $0.kind == "required" && $0.status == "missing" }
    }

    /// A Bedrock pack's kind in words.
    public static func packKind(_ addon: Addon) -> String {
        addon.kind == "resource" ? "addons.kind_resource" : "addons.kind_behavior"
    }

    /// The files each screen picks.
    public static let JAR = ".jar"
    public static let PACK_FILES = [".mcpack", ".mcaddon", ".mctemplate"]
    public static let MODPACK = ".mrpack"

    /// Whether a picked file's name is one the screen sends; the agent
    /// checks again and explains anything else in its own words.
    public static func accepts(_ kind: ContentKind, _ name: String, modpack: Bool = false) -> Bool {
        let lower = name.lowercased()
        if modpack { return lower.hasSuffix(MODPACK) }
        if kind == .packs { return PACK_FILES.contains { lower.hasSuffix($0) } }
        return lower.hasSuffix(JAR)
    }
}

/// Modrinth search results and the install confirmation (installMod in mods.js).
public enum Install {
    /// The version that would be installed: the newest that fits.
    public static func latest(_ project: ModProject) -> ModrinthVersion? {
        project.versions.first
    }

    public static func required(_ version: ModrinthVersion) -> [ModrinthDependency] {
        version.dependencies.filter { $0.type == "required" }
    }

    private static let sides: Set<String> = ["required", "optional", "unsupported"]

    /// What the confirmation lists before anything is downloaded.
    public static func facts(_ strings: Strings, _ project: ModProject, _ version: ModrinthVersion) -> [String] {
        let unknown = strings.t("value.unknown")
        var out = [
            strings.t("mods.install_what", ["name": project.project.title, "version": version.versionNumber ?? unknown]),
            strings.t("mods.install_size", ["size": Display.bytes(strings, version.file?.size)]),
        ]
        if !version.gameVersions.isEmpty {
            out.append(strings.t("mods.install_minecraft", ["versions": version.gameVersions.joined(separator: ", ")]))
        }
        out.append(strings.t("mods.install_licence", ["licence": project.project.license ?? unknown]))
        out.append(strings.t("mobile.content.side_server", ["side": side(strings, project.project.serverSide)]))
        out.append(strings.t("mobile.content.side_client", ["side": side(strings, project.project.clientSide)]))
        return out
    }

    private static func side(_ strings: Strings, _ value: String?) -> String {
        guard let value = value, sides.contains(value) else { return strings.t("value.unknown") }
        return strings.t("mobile.content.side_" + value)
    }

    /// "1.2M downloads"; Unknown when Modrinth didn't say.
    public static func downloads(_ strings: Strings, _ count: Int64?) -> String {
        guard let count = count else { return strings.t("value.unknown") }
        let short: String
        if count >= 1_000_000 {
            short = Display.fixed(Double(count) / 1_000_000, 1) + "M"
        } else if count >= 1_000 {
            short = Display.fixed(Double(count) / 1_000, 1) + "K"
        } else {
            short = String(count)
        }
        return strings.t("mobile.content.downloads", ["count": short])
    }

    /// Only Modrinth's own pages open, in the in-app browser.
    public static func page(_ url: String?) -> URL? {
        guard let url = url, url.hasPrefix("https://modrinth.com/") else { return nil }
        return URL(string: url)
    }

    /// What "Install the missing ones" did with each: installed, already there, or failed and why.
    public static func missingDone(_ strings: Strings, _ results: [DependencyResult]) -> String {
        let words = results.map { r -> String in
            let said: String
            switch r.result {
            case "installed": said = strings.t("mobile.content.result_ok")
            case "skipped": said = strings.t("mobile.content.result_skipped")
            default:
                let detail = r.detail.flatMap { $0.isEmpty ? nil : ": " + $0 } ?? ""
                said = strings.t("mobile.content.result_failed") + detail
            }
            return "\(r.title) (\(said))"
        }
        return strings.t("mobile.content.missing_done", ["results": words.joined(separator: ", ")])
    }

    public static func allWorked(_ results: [DependencyResult]) -> Bool {
        results.allSatisfy { $0.result == "installed" || $0.result == "skipped" }
    }
}

/// The "Doing the same job" rows.
public enum SameJob {
    /// The mods that get a Turn off button: with a pick, the others; with
    /// none, each one no other mod needs, so the owner keeps the one they use.
    public static func turnOff(_ overlap: Overlap) -> [OverlapMod] {
        overlap.mods.filter { $0.neededBy.isEmpty && (overlap.keep.isEmpty || $0.turnOff) }
    }
}

/// Update all's list and what is sent.
/// Server / Players / Both: the words under a mod and the picks offered.
public enum Sides {
    public static let all = ["server", "players", "both"]
    private static let plugins: Set<String> = ["paper", "bukkit"]
    private static let from: Set<String> = ["you", "unknown", "modpack", "file"]
    private static let sites = ["modrinth": "Modrinth", "curseforge": "CurseForge"]

    /// "For: Both (not sure, the mod doesn't say)"; nil for plugins, which only run on the server.
    public static func words(_ strings: Strings, _ mod: Mod) -> String? {
        guard let side = mod.side, !plugins.contains(mod.loader ?? "") else { return nil }
        let words = strings.t("sides.for", ["side": strings.t("sides." + side)])
        guard let source = mod.sideFrom else { return words }
        let said = from.contains(source)
            ? strings.t("sides.from_" + source)
            : strings.t("sides.from_site", ["site": sites[source] ?? source])
        return words + " " + said
    }

    /// The marks a mod can be changed to. Players stays offered while the jar
    /// is still in the mods folder, since picking it moves the jar out.
    public static func picks(_ mod: Mod) -> [String] {
        guard let side = mod.side, !plugins.contains(mod.loader ?? "") else { return [] }
        return all.filter { $0 == "players" || $0 != side }
    }

    /// The Server / Players / Both filter; "all" keeps every mod.
    public static let filters = ["all"] + all

    public static func shown(_ mods: [Mod], _ filter: String) -> [Mod] {
        mods.filter { filter == "all" || $0.side == filter }
    }

    public static func shown(_ found: ModSides, _ filter: String) -> [ModSideRow] {
        found.mods.filter { filter == "all" || $0.side == filter }
    }

    /// "On: Survival, Creative (not sure)", the servers a mod is on with this mark.
    public static func servers(_ strings: Strings, _ row: ModSideRow) -> String {
        let names = row.servers.map { $0.sideFrom == "unknown" ? $0.name + " " + strings.t("sides.not_sure") : $0.name }
        return strings.t("sides.on_servers", ["servers": names.joined(separator: ", ")])
    }

    /// A file the Players only list takes: a mod (.jar) or a pack (.zip).
    public static func accepts(_ name: String) -> Bool {
        let lower = name.lowercased()
        return lower.hasSuffix(".jar") || lower.hasSuffix(".zip")
    }
}

/// The linked modpack card and Modpack update's question.
public enum LinkedPacks {
    public static func lines(_ strings: Strings, _ pack: LinkedModpack) -> [String] {
        var lines = [strings.t("linked.from", ["name": pack.name, "version": pack.version, "site": pack.site])]
        if let update = pack.update {
            lines.append(update.version.isEmpty
                ? strings.t("linked.update_ready_unnamed")
                : strings.t("linked.update_ready", ["version": update.version]))
        }
        if pack.addedByYou > 0 { lines.append(strings.tn("linked.yours", count: pack.addedByYou)) }
        return lines
    }

    public static func changes(_ strings: Strings, _ changes: ModpackChanges) -> [String] {
        [
            ("linked.newer", changes.newer.map { "\($0.from) → \($0.to)" }),
            ("linked.added", changes.added),
            ("linked.removed", changes.removed),
            ("linked.kept", changes.addedByYou),
            ("linked.configs", changes.configsChangedByYou),
            ("pack.own_jars", changes.newOwnJars),
        ].filter { !$0.1.isEmpty }.map { strings.t($0.0, ["names": $0.1.joined(separator: ", ")]) }
    }

    public static func steps(_ strings: Strings, _ preview: ModpackUpdatePreview) -> [String] {
        var steps = [strings.t("pack.step_stop"), strings.t("linked.step_one_backup")]
        if preview.changes.versionChange {
            steps.append(strings.t("pack.step_version", [
                "type": preview.pack.typeName ?? strings.t("value.unknown"),
                "version": preview.changes.minecraftVersion,
            ]))
        }
        return steps
    }
}

/// The "Still fit?" card: its first line, and "Change these" as an Update all.
public enum StillFit {
    public static func lead(_ strings: Strings, _ fit: ModFit, plugins: Bool) -> String {
        let word = plugins ? "plugin" : "mod"
        let count = fit.needsChange.count
        return count > 0
            ? strings.tn("fit.lead", count: count, ["word": word, "about": fit.about])
            : strings.t("fit.lead_none", ["word": word, "about": fit.about])
    }

    /// The mods to change, shown and sent the way Update all's own list is (all ticked).
    public static func plan(_ fit: ModFit) -> UpdateAllPlan {
        UpdateAllPlan(updates: fit.needsChange.map {
            UpdateAllRow(modId: $0.modId, name: $0.name, filename: $0.filename, installedVersion: $0.from, latestVersion: $0.to, versionId: $0.versionId)
        })
    }
}

public enum UpdateAll {
    /// Offered when the last check found a newer version of any mod.
    public static func offered(_ list: ModList?) -> Bool {
        list?.mods.contains { $0.updateAvailable != nil } == true
    }

    /// Ticked at first: every one that is on. Turned-off ones are listed
    /// but left for the person to tick.
    public static func ticked(_ plan: UpdateAllPlan) -> Set<String> {
        Set(plan.updates.filter(\.enabled).map(\.filename))
    }

    /// The ticked ones, in the plan's order, with the version the plan showed.
    public static func body(_ plan: UpdateAllPlan, ticked: Set<String>) -> UpdateAllBody {
        UpdateAllBody(mods: plan.updates.filter { ticked.contains($0.filename) }.map {
            UpdateAllBody.Item(filename: $0.filename, versionId: $0.versionId)
        })
    }

    /// Why it can't start now, or nil when it can.
    public static func blocked(_ strings: Strings, _ plan: UpdateAllPlan) -> String? {
        if plan.serverRunning { return strings.t("mobile.content.stop_first") }
        if let job = plan.blockedBy { return strings.t("world.busy", ["job": job]) }
        return nil
    }

    public static func done(_ strings: Strings, _ result: UpdateAllResult) -> String {
        strings.tn("mobile.content.update_all_done", count: result.updated.count, ["backup": result.safetyBackup?.name ?? strings.t("value.unknown")])
    }

    /// The step a running Update all is on, from the live feed.
    public static func progress(_ job: Job?, serverId: String) -> String? {
        guard let job = job, job.state == "running", job.serverId == serverId, job.kind == "mods" else { return nil }
        return job.step
    }
}

/// A modpack import's steps, as the confirmation lists them.
public enum Modpacks {
    /// What another owner's pack asks of you: plugins to get by hand, and its
    /// own jars no site checked. Shown in the question; a yes sends own_jars.
    public static func notes(_ strings: Strings, _ pack: Modpack) -> [String] {
        [("pack.plugins_to_get", pack.pluginsToGet), ("pack.own_jars", pack.ownJars)]
            .filter { !$0.1.isEmpty }.map { strings.t($0.0, ["names": $0.1.joined(separator: ", ")]) }
    }

    public static func steps(_ strings: Strings, _ inspected: ModpackInspected) -> [String] {
        let pack = inspected.pack
        let unknown = strings.t("value.unknown")
        var out = [strings.t("pack.step_stop"), strings.t("pack.step_backup")]
        if inspected.versionChange {
            out.append(strings.t("pack.step_version", ["type": pack.typeName ?? unknown, "version": pack.minecraftVersion ?? unknown]))
        }
        out.append(strings.t("pack.step_aside"))
        out.append(strings.t("pack.step_files", ["count": pack.downloadCount]))
        return out
    }

    /// Where a found modpack is read from.
    public static func site(_ hit: ModrinthHit) -> String {
        hit.source == "curseforge" ? "curseforge" : "modrinth"
    }

    /// A found modpack on one line under its name: site, loader and newest Minecraft
    /// version, downloads, and what Modrinth was told it holds.
    public static func about(_ strings: Strings, _ hit: ModrinthHit) -> String {
        let madeFor = (hit.loaders + [hit.minecraftVersion].compactMap { $0 }).joined(separator: " ")
        var parts = [hit.sourceName ?? "", madeFor]
        if let downloads = hit.downloads {
            let grouped = NumberFormatter()
            grouped.numberStyle = .decimal
            grouped.locale = Locale(identifier: "en_US")
            parts.append(strings.t("pack.downloads", ["count": grouped.string(from: NSNumber(value: downloads)) ?? "\(downloads)"]))
        }
        if hit.serverSide == "unsupported" { parts.append(strings.t("pack.players_only")) }
        return (parts + hit.disclosures).filter { !$0.isEmpty }.joined(separator: "  ·  ")
    }

    public static func fileStatus(_ file: ModpackFile) -> StateWords {
        switch file.status {
        case "download": return StateWords(key: "pack.status_download", tone: .success)
        case "client_only": return StateWords(key: "pack.status_client", tone: .neutral)
        default: return StateWords(key: "pack.status_refused", tone: .danger)
        }
    }
}

/// The Game settings form (game-settings.js): which fields, in which
/// groups, their words, and what Save sends.
public enum GameForm {
    /// Each key's label and hint. Bedrock's allow-list shares the whitelist's label.
    public static let LABELS: [String: (label: String, hint: String)] = [
        "difficulty": ("game.difficulty", "game.difficulty_hint"),
        "gamemode": ("game.gamemode", "game.gamemode_hint"),
        "max-players": ("game.max_players", "game.max_players_hint"),
        "level-seed": ("game.seed", "game.seed_hint"),
        "white-list": ("game.whitelist", "game.whitelist_hint"),
        "pvp": ("game.pvp", "game.pvp_hint"),
        "view-distance": ("game.view_distance", "game.view_distance_hint"),
        "motd": ("game.motd", "game.motd_hint"),
        "server-port": ("game.port", "game.port_hint"),
        "allow-list": ("mobile.game.allowlist", "game.allowlist_hint"),
        "allow-cheats": ("game.cheats", "game.cheats_hint"),
        "server-name": ("game.server_name", "game.server_name_hint"),
    ]

    private static let choiceWords: Set<String> = ["peaceful", "easy", "normal", "hard", "survival", "creative", "adventure", "spectator"]

    /// How the game plays, who can join, then the technical bits.
    public static let ORDER: [(title: String, keys: [String])] = [
        ("game.group_play", ["gamemode", "difficulty", "pvp", "allow-cheats"]),
        ("game.group_who", ["motd", "server-name", "max-players", "white-list", "allow-list"]),
        ("game.group_world", ["level-seed", "view-distance", "server-port"]),
    ]

    public struct Group: Equatable, Sendable {
        public let titleKey: String
        public let fields: [GameSetting]
    }

    /// The groups with the fields this server's file has, in order.
    public static func groups(_ settings: GameSettings) -> [Group] {
        ORDER.compactMap { entry in
            let fields = entry.keys.compactMap { key in settings.fields.first { $0.key == key } }
            return fields.isEmpty ? nil : Group(titleKey: entry.title, fields: fields)
        }
    }

    public static func label(_ strings: Strings, _ field: GameSetting) -> String {
        LABELS[field.key].map { strings.t($0.label) } ?? field.key
    }

    public static func choiceLabel(_ strings: Strings, _ value: String) -> String {
        choiceWords.contains(value) ? strings.t("game." + value) : value
    }

    /// The value the form starts with, as text: what the file says (as
    /// written, when Minecraft wouldn't accept it), otherwise what Minecraft
    /// uses then (an empty seed means a random one). Booleans are "true" or
    /// "false".
    public static func initial(_ field: GameSetting) -> String {
        // A value Minecraft wouldn't accept is shown as the file has it (its problem is
        // said next to it), never swapped for the default.
        if field.set && field.problem != nil { return field.raw ?? "" }
        let shown = field.set ? field.value?.text : nil
        if field.kind == "seed" { return shown ?? "" }
        return shown ?? field.minecraftDefault
    }

    /// Locked: the seed once the world exists, or everything for someone
    /// who may not edit settings.
    public static func locked(_ field: GameSetting, canEdit: Bool) -> Bool {
        !canEdit || field.readOnly != nil
    }

    /// The line under a field: its hint (or why the seed is locked), and
    /// what Minecraft uses when the file doesn't set it.
    public static func hint(_ strings: Strings, _ field: GameSetting) -> String {
        var notes: [String] = []
        if field.readOnly == "world_exists" {
            notes.append(strings.t("game.seed_locked"))
        } else if let hint = LABELS[field.key]?.hint {
            notes.append(strings.t(hint))
        }
        if !field.set {
            let fallback: String
            if field.kind == "choice" {
                fallback = choiceLabel(strings, field.minecraftDefault)
            } else if field.minecraftDefault.isEmpty {
                fallback = strings.t("game.blank")
            } else {
                fallback = field.minecraftDefault
            }
            notes.append(strings.t("game.not_set", ["value": fallback]))
        }
        return notes.filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// Why the file's own value couldn't be used, or nil.
    public static func fileProblem(_ strings: Strings, _ field: GameSetting) -> String? {
        field.problem.map { strings.t("game.file_problem", ["problem": $0]) }
    }

    /// Only what changed, as the agent takes it: on and off as true or
    /// false, numbers as numbers when they read as one (the agent checks and
    /// explains anything else), words as typed. Locked fields never.
    public static func changed(_ settings: GameSettings, _ edits: [String: String]) -> [String: JSONValue] {
        var out: [String: JSONValue] = [:]
        for field in settings.fields where field.readOnly == nil {
            guard let edit = edits[field.key], edit != initial(field) else { continue }
            switch field.kind {
            case "bool":
                out[field.key] = .bool(edit == "true")
            case "int", "port":
                let trimmed = edit.trimmingCharacters(in: .whitespaces)
                if let number = Int64(trimmed) {
                    out[field.key] = .number(Double(number))
                } else {
                    out[field.key] = .string(trimmed)
                }
            default:
                out[field.key] = .string(edit)
            }
        }
        return out
    }

    /// What to say after Save. Running: it waits for a restart, offered next to it.
    public static func saved(_ strings: Strings, _ result: GameSettingsSaved) -> String {
        if result.nothingChanged == true { return strings.t("settings.nothing_changed") }
        return strings.t(result.running ? "game.saved_running" : "game.saved")
    }
}

/// Modrinth's icons for the search results, loaded straight from Modrinth
/// (never through the PC) into the phone's own cache, so scrolling back
/// doesn't download them again. Only https addresses on Modrinth's own
/// hosts are loaded; anything else is no icon.
public final class IconCache: @unchecked Sendable {
    public static let MODRINTH_HOSTS: Set<String> = ["cdn.modrinth.com"]
    public static let MAX_ICON_BYTES = 1024 * 1024

    private let session: URLSession
    private let hosts: Set<String>
    private let httpsOnly: Bool
    private let memory = NSCache<NSURL, NSData>()

    /// `directory` holds the disk cache (the app's Caches folder, which iOS
    /// may clear); nil keeps icons in memory only.
    public init(
        directory: URL?,
        configuration: URLSessionConfiguration = .default,
        hosts: Set<String> = IconCache.MODRINTH_HOSTS,
        httpsOnly: Bool = true,
        kept: Int = 120
    ) {
        let config = configuration
        config.urlCache = URLCache(memoryCapacity: 4 * 1024 * 1024, diskCapacity: 20 * 1024 * 1024, directory: directory)
        config.requestCachePolicy = .returnCacheDataElseLoad
        config.httpCookieStorage = nil
        config.httpShouldSetCookies = false
        // A redirect could lead off Modrinth's own address: never followed.
        self.session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
        self.hosts = hosts
        self.httpsOnly = httpsOnly
        memory.countLimit = kept
    }

    public func allowed(_ url: String?) -> Bool {
        guard let url = url, let parsed = URL(string: url), let host = parsed.host else { return false }
        return (!httpsOnly || parsed.scheme == "https") && hosts.contains(host)
    }

    /// The icon's bytes, from memory, the disk cache or Modrinth; nil when
    /// it isn't allowed or couldn't be loaded.
    public func load(_ url: String?) async -> Data? {
        guard let url = url, allowed(url), let parsed = URL(string: url) else { return nil }
        if let kept = memory.object(forKey: parsed as NSURL) {
            return kept as Data
        }
        guard let answer = try? await session.bytes(from: parsed),
              let http = answer.1 as? HTTPURLResponse, (200...299).contains(http.statusCode),
              http.expectedContentLength <= Int64(IconCache.MAX_ICON_BYTES)
        else { return nil }
        // Read no more than the limit, even when the size wasn't said.
        var data = Data()
        do {
            for try await byte in answer.0 {
                data.append(byte)
                if data.count > IconCache.MAX_ICON_BYTES { return nil }
            }
        } catch {
            return nil
        }
        memory.setObject(data as NSData, forKey: parsed as NSURL)
        return data
    }
}

/// Refuses every redirect, so an icon is only ever read from where it was asked for.
private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

/// What a made modpack says on the Make a modpack card: ready, the trust
/// note when own files are inside, and what isn't inside and why.
public enum MadeModpacks {
    public static func lines(_ strings: Strings, _ made: ModpackMade) -> [String] {
        var lines = [strings.t("make.made", ["name": made.filename, "size": Display.bytes(strings, made.sizeBytes)])]
        if made.trustNote != nil { lines.append(strings.t("make.trust")) }
        lines += [
            ("make.get_yourself", made.getYourself.map(\.name)),
            ("make.plugins_to_get", made.pluginsToGet),
            ("make.left_out", made.leftOut),
        ].filter { !$0.1.isEmpty }.map { strings.t($0.0, ["names": $0.1.joined(separator: ", ")]) }
        return lines
    }
}

/// What the Download link card says: the modpack's link and the speed cap
/// while it's on, why it can't run, and the resource pack. `owner`: may turn it on.
public enum DownloadLinks {
    public static func lines(_ strings: Strings, _ link: DownloadLink, owner: Bool) -> [String] {
        var lines: [String] = []
        if link.on, !link.listening, let why = link.whyNot {
            lines.append(strings.t("link.not_listening", ["why": why]))
        } else if link.on {
            lines.append(link.modpackUrl.map { strings.t("link.modpack", ["url": $0]) } ?? strings.t("link.no_modpack"))
            lines.append(strings.t("link.speed", ["speed": Display.bytes(strings, link.speedCapBytes)]))
            lines.append(strings.t("link.new_hint"))
        } else if let why = link.whyNot {
            lines.append(why)
        }
        if !owner { lines.append(strings.t("link.owner_only")) }
        return lines
    }

    public static func packLines(_ strings: Strings, _ link: DownloadLink) -> [String] {
        guard let pack = link.resourcePack else { return [strings.t("pack.none")] }
        let name = pack.filename ?? ""
        let size = Display.bytes(strings, pack.sizeBytes)
        var lines: [String]
        if !pack.served {
            lines = [strings.t("pack.outside", ["url": pack.url ?? ""])]
        } else if pack.url != nil {
            lines = [strings.t("pack.file", ["name": name, "size": size])]
        } else {
            lines = [strings.t("pack.file_off", ["name": name, "size": size])]
        }
        if pack.sha1 == nil { lines.append(strings.t("pack.no_sha1")) }
        if link.restartNeeded { lines.append(strings.t("pack.restart")) }
        return lines
    }
}

/// What the "Put it on Modrinth" card says. `owner`: may sign in and out.
public enum ModrinthCards {
    public static func lines(_ strings: Strings, _ share: ModrinthShare, owner: Bool) -> [String] {
        var lines: [String] = []
        if let account = share.account {
            if !account.available {
                lines.append(strings.t("modrinth.not_available"))
            } else if account.signedIn {
                lines.append(strings.t("modrinth.signed_in", ["name": account.username ?? "?"]))
            } else {
                lines.append(strings.t(account.ranOut ? "modrinth.ran_out" : "modrinth.signed_out"))
            }
        }
        if !owner { lines.append(strings.t("modrinth.owner_only")) }
        if let project = share.project {
            lines.append(strings.t("modrinth.project", ["url": project.url, "version": share.uploads.first?.version ?? "-"]))
        }
        if share.made == nil { lines.append(strings.t("modrinth.make_first")) }
        for jar in share.ownJars {
            if let url = jar.url { lines.append(strings.t("modrinth.jar_up", ["name": jar.name, "url": url])) }
        }
        return lines
    }

    public static func serverLines(_ strings: Strings, _ share: ModrinthShare) -> [String] {
        var lines: [String] = []
        if share.address == nil {
            lines.append(strings.t("modrinth.server_no_address"))
        } else if let project = share.serverProject {
            lines.append(strings.t("modrinth.server_listed", ["url": project.url]))
        }
        if let older = share.older {
            lines.append(strings.t("modrinth.server_older", ["listed": older.listed, "newest": older.newest]))
        }
        return lines
    }

    public static func steps(_ strings: Strings, _ share: ModrinthShare) -> String {
        let title = share.project?.title ?? ""
        return strings.t("modrinth.server_steps", [
            "address": share.address ?? "",
            "project": title.isEmpty ? strings.t("modrinth.your_modpack") : title,
            "version": share.uploads.first?.version ?? "-",
        ])
    }

    /// A first try at the Modrinth address, which the person can change.
    public static func slug(_ name: String) -> String {
        let dashed = String(name.lowercased().map { $0.isASCII && ($0.isLetter || $0.isNumber) ? $0 : "-" })
        return String(dashed.split(separator: "-").joined(separator: "-").prefix(64))
    }

    /// (code, state) from mcsc://modrinth?state=..&code=.., or nil when
    /// Modrinth sent no code (the person said no) or it isn't that link.
    public static func cameBack(_ url: URL) -> (code: String, state: String)? {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme?.lowercased() == DeepLink.scheme, parts.host == "modrinth",
              let code = parts.queryItems?.first(where: { $0.name == "code" })?.value,
              let state = parts.queryItems?.first(where: { $0.name == "state" })?.value
        else { return nil }
        return (code, state)
    }
}

/// What the anti-cheat picker says above its buttons.
public enum AntiCheats {
    public static func status(_ strings: Strings, _ picker: AntiCheat) -> String {
        let names = picker.installed.joined(separator: ", ")
        if !picker.supported { return strings.t("anticheat.none_here") }
        if picker.installed.count > 1 { return strings.t("anticheat.two", ["names": names]) }
        if !picker.installed.isEmpty { return strings.t("anticheat.in_now", ["names": names]) }
        return strings.t("anticheat.nothing_in")
    }

    public static func free(_ strings: Strings, _ item: AntiCheatFree) -> [String] {
        var lines = [strings.t("anticheat.about." + item.id)]
        if item.warning { lines.append(strings.t("anticheat.fabric_warning")) }
        if item.on { lines.append(strings.t("anticheat.is_in", ["name": item.name])) }
        return lines
    }

    /// Never a guessed price: no checked price sends people to the seller's page.
    public static func paid(_ strings: Strings, _ item: AntiCheatPaid) -> [String] {
        var lines = [strings.t("anticheat.about." + item.id)]
        lines.append(item.price.map { strings.t("anticheat.price", ["price": $0, "day": item.checked ?? ""]) } ?? strings.t("anticheat.price_unknown"))
        if item.on { lines.append(strings.t("anticheat.is_in", ["name": item.name])) }
        return lines
    }
}

/// What the Far view card says.
public enum FarViews {
    public static func status(_ strings: Strings, _ card: FarView) -> String {
        if !card.supported { return strings.t("farview.none_here") }
        if let on = card.choices.first(where: { $0.id == card.choice }) {
            return strings.t("farview.is_on", ["name": on.name])
        }
        return strings.t("farview.is_off")
    }

    /// A choice's lines: what it is, then why it can't, or what it adds here.
    public static func lines(_ strings: Strings, _ item: FarViewChoice, now: String) -> [String] {
        var lines = [strings.t("farview.about." + item.id)]
        if !item.available {
            if let why = item.why { lines.append(why) }
        } else if item.id != now {
            if item.server.isEmpty && item.players.isEmpty { lines.append(strings.t("farview.all_here")) }
            if !item.server.isEmpty { lines.append(strings.t("farview.server_gets", ["names": item.server.joined(separator: ", ")])) }
            if !item.players.isEmpty { lines.append(strings.t("farview.players_get", ["names": item.players.joined(separator: ", ")])) }
        }
        return lines
    }
}
