import Foundation

// What the Mods / Plugins / Packs and Game settings screens read. Mirrors
// mobile/android/core's ContentModels.kt. As in Pages.swift, only the fields
// the app uses are read, and a value the agent can leave out is nil here and
// shown as Unknown.

/// Something wrong with one mod, in the agent's words.
public struct ModIssue: Decodable, Equatable, Sendable {
    public var kind: String
    public var severity: String
    public var detail: String

    enum CodingKeys: String, CodingKey { case kind, severity, detail }

    public init(kind: String = "", severity: String = "info", detail: String = "") {
        self.kind = kind
        self.severity = severity
        self.detail = detail
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = c.value(.kind, "")
        severity = c.value(.severity, "info")
        detail = c.value(.detail, "")
    }
}

/// A newer version found on Modrinth at the agent's last check.
public struct ModUpdate: Decodable, Equatable, Sendable {
    public var latestVersion: String
    public var versionId: String?
    /// Update all waits until this time, as the version is very new.
    public var heldUntil: Double?

    enum CodingKeys: String, CodingKey {
        case latestVersion = "latest_version"
        case versionId = "version_id"
        case heldUntil = "held_until"
    }

    public init(latestVersion: String, versionId: String? = nil, heldUntil: Double? = nil) {
        self.latestVersion = latestVersion
        self.versionId = versionId
        self.heldUntil = heldUntil
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        latestVersion = c.value(.latestVersion, "")
        versionId = c.maybe(.versionId)
        heldUntil = c.maybe(.heldUntil)
    }
}

/// One mod or plugin in the server's folder.
public struct Mod: Decodable, Equatable, Identifiable, Sendable {
    public var filename: String
    public var modId: String
    public var name: String
    public var version: String
    public var enabled: Bool
    public var description: String?
    public var minecraftRange: String?
    /// ok, warn or error while on; disabled while off.
    public var status: String?
    public var issues: [ModIssue]
    public var updateAvailable: ModUpdate?
    /// On a linked modpack's server: true when the modpack didn't put it there.
    public var addedByYou: Bool?
    public var loader: String?
    /// server, players or both: who needs it.
    public var side: String?
    /// Where `side` came from: you, modpack, modrinth, curseforge, file or unknown.
    public var sideFrom: String?

    public var id: String { filename }

    enum CodingKeys: String, CodingKey {
        case filename, name, version, enabled, description, status, issues, loader, side
        case modId = "mod_id"
        case sideFrom = "side_from"
        case minecraftRange = "minecraft_range"
        case updateAvailable = "update_available"
        case addedByYou = "added_by_you"
    }

    public init(
        filename: String, modId: String = "", name: String = "", version: String = "", enabled: Bool = true,
        description: String? = nil, minecraftRange: String? = nil, status: String? = nil, issues: [ModIssue] = [],
        updateAvailable: ModUpdate? = nil, loader: String? = nil, side: String? = nil, sideFrom: String? = nil
    ) {
        self.filename = filename
        self.modId = modId
        self.name = name
        self.version = version
        self.enabled = enabled
        self.description = description
        self.minecraftRange = minecraftRange
        self.status = status
        self.issues = issues
        self.updateAvailable = updateAvailable
        self.loader = loader
        self.side = side
        self.sideFrom = sideFrom
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filename = try c.decode(String.self, forKey: .filename)
        modId = c.value(.modId, "")
        name = c.value(.name, "")
        version = c.value(.version, "")
        enabled = c.value(.enabled, true)
        description = c.maybe(.description)
        minecraftRange = c.maybe(.minecraftRange)
        status = c.maybe(.status)
        issues = c.each(.issues)
        updateAvailable = c.maybe(.updateAvailable)
        addedByYou = c.maybe(.addedByYou)
        loader = c.maybe(.loader)
        side = c.maybe(.side)
        sideFrom = c.maybe(.sideFrom)
    }
}

public struct ModList: Decodable, Equatable, Sendable {
    public var mods: [Mod]
    /// Nil until the server has started once.
    public var minecraftVersion: String?
    public var serverState: String
    /// The server's drive can't link to the shared store, so its jars are copies.
    public var storedSeparately: Bool
    /// The "Still fit?" card after a mod or version change; nil: nothing to show.
    public var fit: ModFit?
    /// The same mod twice, or mods that do the same job.
    public var overlaps: [Overlap]
    /// The modpack this server came from; nil: not linked.
    public var modpack: LinkedModpack?
    /// The player-only folder: kept for the modpack, never loaded by the server.
    public var playerOnly: [PlayerFile] = []
    public var playerOnlyOptions = PlayerOnlyOptions()
    /// A mod needs one that sits on the other side.
    public var oddSetups: [OddSetup] = []

    enum CodingKeys: String, CodingKey {
        case mods, fit, overlaps, modpack
        case playerOnly = "player_only"
        case playerOnlyOptions = "player_only_options"
        case oddSetups = "odd_setups"
        case minecraftVersion = "minecraft_version"
        case serverState = "server_state"
        case storedSeparately = "stored_separately"
    }

    public init(mods: [Mod] = [], minecraftVersion: String? = nil, serverState: String = States.UNKNOWN, storedSeparately: Bool = false, fit: ModFit? = nil, overlaps: [Overlap] = []) {
        self.mods = mods
        self.minecraftVersion = minecraftVersion
        self.serverState = serverState
        self.storedSeparately = storedSeparately
        self.fit = fit
        self.overlaps = overlaps
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mods = c.each(.mods)
        minecraftVersion = c.maybe(.minecraftVersion)
        serverState = c.value(.serverState, States.UNKNOWN)
        storedSeparately = c.value(.storedSeparately, false)
        fit = c.maybe(.fit)
        overlaps = c.each(.overlaps)
        modpack = c.maybe(.modpack)
        playerOnly = c.each(.playerOnly)
        playerOnlyOptions = c.value(.playerOnlyOptions, PlayerOnlyOptions())
        oddSetups = c.each(.oddSetups)
    }
}

/// A file in the player-only folder.
public struct PlayerFile: Decodable, Equatable, Identifiable, Sendable {
    public var filename: String
    public var name: String
    public var version: String
    /// mod or pack (a resource pack or shader pack).
    public var kind: String
    /// Not the file the app first saw: changed outside it.
    public var changed: Bool = false
    /// The app added it, but it's gone ("Get it again" when `site` is modrinth).
    public var missing: Bool = false
    public var site: String?

    public var id: String { filename }

    enum CodingKeys: String, CodingKey { case filename, name, version, kind, changed, missing, site }

    public init(filename: String, name: String = "", version: String = "", kind: String = "") {
        self.filename = filename
        self.name = name
        self.version = version
        self.kind = kind
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filename = try c.decode(String.self, forKey: .filename)
        name = c.value(.name, "")
        version = c.value(.version, "")
        kind = c.value(.kind, "")
        changed = c.value(.changed, false)
        missing = c.value(.missing, false)
        site = c.maybe(.site)
    }
}

/// The player-only folder's two backup options.
public struct PlayerOnlyOptions: Codable, Equatable, Sendable {
    public var backup: Bool
    /// Leave out files Modrinth lists; got again by hash after a restore.
    public var skipListed: Bool

    enum CodingKeys: String, CodingKey {
        case backup
        case skipListed = "skip_listed"
    }

    public init(backup: Bool = true, skipListed: Bool = false) {
        self.backup = backup
        self.skipListed = skipListed
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        backup = c.value(.backup, true)
        skipListed = c.value(.skipListed, false)
    }
}

public struct OddSetup: Decodable, Equatable, Sendable {
    public var filename: String
    public var text: String

    enum CodingKeys: String, CodingKey { case filename, text }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filename = c.value(.filename, "")
        text = c.value(.text, "")
    }
}

/// "On all servers": one mod with one mark, and the servers it's on.
public struct ModSideRow: Decodable, Equatable, Sendable {
    public var name: String
    public var modId: String?
    /// server, players or both.
    public var side: String
    public var servers: [ModSideServer]

    enum CodingKeys: String, CodingKey {
        case name, side, servers
        case modId = "mod_id"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        modId = c.maybe(.modId)
        side = c.value(.side, "")
        servers = c.value(.servers, [])
    }
}

public struct ModSideServer: Decodable, Equatable, Sendable {
    public var id: String
    public var name: String
    /// Like `Mod.sideFrom`; unknown: not sure. Nil for the player-only folder.
    public var sideFrom: String?

    enum CodingKeys: String, CodingKey {
        case id, name
        case sideFrom = "side_from"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        name = c.value(.name, "")
        sideFrom = c.maybe(.sideFrom)
    }
}

public struct ModSideNotRead: Decodable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var detail: String

    enum CodingKeys: String, CodingKey { case id, name, detail }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        name = c.value(.name, "")
        detail = c.value(.detail, "")
    }
}

public struct ModSides: Decodable, Equatable, Sendable {
    public var mods: [ModSideRow]
    /// A server whose mods couldn't be read: named, never left out quietly.
    public var notRead: [ModSideNotRead]

    enum CodingKeys: String, CodingKey {
        case mods
        case notRead = "not_read"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mods = c.value(.mods, [])
        notRead = c.value(.notRead, [])
    }
}

public struct LinkedModpackUpdate: Decodable, Equatable, Sendable {
    public var versionId: String
    /// Empty for CurseForge, whose names aren't kept.
    public var version: String

    enum CodingKeys: String, CodingKey {
        case version
        case versionId = "version_id"
    }

    public init(versionId: String, version: String = "") {
        self.versionId = versionId
        self.version = version
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        versionId = try c.decode(String.self, forKey: .versionId)
        version = c.value(.version, "")
    }
}

/// The modpack a server came from, for its card on the Mods page.
public struct LinkedModpack: Decodable, Equatable, Sendable {
    public var name: String
    /// Modrinth or CurseForge.
    public var site: String
    public var version: String
    public var update: LinkedModpackUpdate?
    public var addedByYou: Int

    enum CodingKeys: String, CodingKey {
        case name, site, version, update
        case addedByYou = "added_by_you"
    }

    public init(name: String = "", site: String = "", version: String = "", update: LinkedModpackUpdate? = nil, addedByYou: Int = 0) {
        self.name = name
        self.site = site
        self.version = version
        self.update = update
        self.addedByYou = addedByYou
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        site = c.value(.site, "")
        version = c.value(.version, "")
        update = c.maybe(.update)
        addedByYou = c.value(.addedByYou, 0)
    }
}

public struct NewerMod: Decodable, Equatable, Sendable {
    public var name: String
    public var from: String
    public var to: String

    enum CodingKeys: String, CodingKey { case name, from, to }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        from = c.value(.from, "")
        to = c.value(.to, "")
    }
}

/// What Modpack update changes, compared by file hash.
public struct ModpackChanges: Decodable, Equatable, Sendable {
    public var toVersion: String
    public var added: [String]
    public var removed: [String]
    public var newer: [NewerMod]
    public var addedByYou: [String]
    public var configsChangedByYou: [String]
    /// New or changed own jars: the update needs ownJars = true.
    public var newOwnJars: [String]
    public var versionChange: Bool
    public var minecraftVersion: String
    public var problems: [String]

    enum CodingKeys: String, CodingKey {
        case added, removed, newer, problems
        case toVersion = "to_version"
        case addedByYou = "added_by_you"
        case configsChangedByYou = "configs_changed_by_you"
        case newOwnJars = "new_own_jars"
        case versionChange = "version_change"
        case minecraftVersion = "minecraft_version"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        toVersion = c.value(.toVersion, "")
        added = c.value(.added, [])
        removed = c.value(.removed, [])
        newer = c.each(.newer)
        addedByYou = c.value(.addedByYou, [])
        configsChangedByYou = c.value(.configsChangedByYou, [])
        newOwnJars = c.value(.newOwnJars, [])
        versionChange = c.value(.versionChange, false)
        minecraftVersion = c.value(.minecraftVersion, "")
        problems = c.value(.problems, [])
    }
}

public struct ModpackUpdatePreview: Decodable, Equatable, Sendable {
    public var token: String
    public var pack: Modpack
    public var changes: ModpackChanges
}

/// One mod in an `Overlap`. `turnOff`: the pick isn't this one.
public struct OverlapMod: Decodable, Equatable, Identifiable, Sendable {
    public var filename: String
    public var modId: String
    public var name: String
    public var version: String
    public var turnOff: Bool
    /// An installed mod that needs this one, "" when none.
    public var neededBy: String

    public var id: String { filename }

    enum CodingKeys: String, CodingKey {
        case filename, name, version
        case modId = "mod_id"
        case turnOff = "turn_off"
        case neededBy = "needed_by"
    }

    public init(filename: String, modId: String = "", name: String = "", version: String = "", turnOff: Bool = false, neededBy: String = "") {
        self.filename = filename
        self.modId = modId
        self.name = name
        self.version = version
        self.turnOff = turnOff
        self.neededBy = neededBy
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filename = try c.decode(String.self, forKey: .filename)
        modId = c.value(.modId, "")
        name = c.value(.name, "")
        version = c.value(.version, "")
        turnOff = c.value(.turnOff, false)
        neededBy = c.value(.neededBy, "")
    }
}

/// The same mod twice ("same_mod"), mods on the notes file's same-job list
/// ("same_job") or a site saying two don't work together ("site_says").
public struct Overlap: Decodable, Equatable, Identifiable, Sendable {
    public var kind: String
    public var why: String
    /// The file to keep, "" when nothing says which.
    public var keep: String
    /// "Keep 0.5.2, turn off 0.5.1", or "Keep the one you use".
    public var say: String
    public var mods: [OverlapMod]

    public var id: String { mods.map(\.filename).joined(separator: "|") }

    enum CodingKeys: String, CodingKey {
        case kind, why, keep, say, mods
    }

    public init(kind: String = "", why: String = "", keep: String = "", say: String = "", mods: [OverlapMod] = []) {
        self.kind = kind
        self.why = why
        self.keep = keep
        self.say = say
        self.mods = mods
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = c.value(.kind, "")
        why = c.value(.why, "")
        keep = c.value(.keep, "")
        say = c.value(.say, "")
        mods = c.each(.mods)
    }
}

/// One mod on the "Still fit?" card: what the mods or its site said. For
/// one that needs a change, `from`, `to` and `versionId` are filled.
public struct FitRow: Decodable, Equatable, Identifiable, Sendable {
    public var modId: String
    public var name: String
    public var filename: String
    public var why: String
    public var from: String
    public var to: String
    public var versionId: String?

    public var id: String { filename }

    enum CodingKeys: String, CodingKey {
        case name, filename, why, from, to
        case modId = "mod_id"
        case versionId = "version_id"
    }

    public init(modId: String = "", name: String = "", filename: String, why: String = "", from: String = "", to: String = "", versionId: String? = nil) {
        self.modId = modId
        self.name = name
        self.filename = filename
        self.why = why
        self.from = from
        self.to = to
        self.versionId = versionId
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modId = c.value(.modId, "")
        name = c.value(.name, "")
        filename = try c.decode(String.self, forKey: .filename)
        why = c.value(.why, "")
        from = c.value(.from, "")
        to = c.value(.to, "")
        versionId = c.maybe(.versionId)
    }
}

public struct ModFit: Decodable, Equatable, Sendable {
    /// What changed: "Create 6.0" or "Minecraft 1.21.4".
    public var about: String
    public var needsChange: [FitRow]
    public var noFit: [FitRow]
    public var cantTell: [FitRow]

    enum CodingKeys: String, CodingKey {
        case about
        case needsChange = "needs_change"
        case noFit = "no_fit"
        case cantTell = "cant_tell"
    }

    public init(about: String = "", needsChange: [FitRow] = [], noFit: [FitRow] = [], cantTell: [FitRow] = []) {
        self.about = about
        self.needsChange = needsChange
        self.noFit = noFit
        self.cantTell = cantTell
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        about = c.value(.about, "")
        needsChange = c.each(.needsChange)
        noFit = c.each(.noFit)
        cantTell = c.each(.cantTell)
    }
}

public struct ModChanged: Decodable, Equatable, Sendable {
    public var mod: Mod?

    enum CodingKeys: String, CodingKey { case mod }

    public init(from decoder: Decoder) throws {
        mod = try decoder.container(keyedBy: CodingKeys.self).maybe(.mod)
    }
}

public struct ModDependent: Decodable, Equatable, Sendable {
    public var name: String
    public var requires: String?

    enum CodingKeys: String, CodingKey { case name, requires }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        requires = c.maybe(.requires)
    }
}

/// What else needs a mod, asked before it is removed.
public struct ModImpact: Decodable, Equatable, Sendable {
    public var dependents: [ModDependent]
    public var warning: String?

    enum CodingKeys: String, CodingKey { case dependents, warning }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dependents = c.each(.dependents)
        warning = c.maybe(.warning)
    }
}

public struct ModRef: Decodable, Equatable, Sendable {
    public var filename: String
    public var name: String
    public var version: String

    enum CodingKeys: String, CodingKey { case filename, name, version }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filename = c.value(.filename, "")
        name = c.value(.name, "")
        version = c.value(.version, "")
    }
}

public struct ModRemoved: Decodable, Equatable, Sendable {
    public var removed: ModRef?
}

public struct ModUpdated: Decodable, Equatable, Sendable {
    public var updated: ModRef?
    public var previousVersion: String?

    enum CodingKeys: String, CodingKey {
        case updated
        case previousVersion = "previous_version"
    }
}

/// A copy of a mod kept on this server, which it can roll back to.
public struct ModVersion: Decodable, Equatable, Identifiable, Sendable {
    public var version: String?
    public var filename: String
    public var archivePath: String
    public var createdAt: Double
    public var isCurrent: Bool
    /// False when the kept file has gone.
    public var exists: Bool

    public var id: String { archivePath }

    enum CodingKeys: String, CodingKey {
        case version, filename, exists
        case archivePath = "archive_path"
        case createdAt = "created_at"
        case isCurrent = "is_current"
    }

    public init(version: String? = nil, filename: String = "", archivePath: String, createdAt: Double = 0, isCurrent: Bool = false, exists: Bool = false) {
        self.version = version
        self.filename = filename
        self.archivePath = archivePath
        self.createdAt = createdAt
        self.isCurrent = isCurrent
        self.exists = exists
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        version = c.maybe(.version)
        filename = c.value(.filename, "")
        archivePath = try c.decode(String.self, forKey: .archivePath)
        createdAt = c.value(.createdAt, 0)
        isCurrent = c.value(.isCurrent, false)
        exists = c.value(.exists, false)
    }
}

public struct ModVersions: Decodable, Equatable, Sendable {
    public var versions: [ModVersion]

    enum CodingKeys: String, CodingKey { case versions }

    public init(from decoder: Decoder) throws {
        versions = try decoder.container(keyedBy: CodingKeys.self).each(.versions)
    }
}

public struct ModRolledBack: Decodable, Equatable, Sendable {
    public var restored: ModRef?
    public var previousVersion: String?

    enum CodingKeys: String, CodingKey {
        case restored
        case previousVersion = "previous_version"
    }
}

/// One change in the history: what, when, who, and whether it worked.
public struct ModHistoryRow: Decodable, Equatable, Sendable {
    public var ts: Double
    public var user: String?
    public var action: String
    public var modId: String?
    public var modName: String?
    public var oldVersion: String?
    public var newVersion: String?
    public var result: String
    public var detail: String?

    enum CodingKeys: String, CodingKey {
        case ts, user, action, result, detail
        case modId = "mod_id"
        case modName = "mod_name"
        case oldVersion = "old_version"
        case newVersion = "new_version"
    }

    public init(
        ts: Double = 0, user: String? = nil, action: String = "", modId: String? = nil, modName: String? = nil,
        oldVersion: String? = nil, newVersion: String? = nil, result: String = "", detail: String? = nil
    ) {
        self.ts = ts
        self.user = user
        self.action = action
        self.modId = modId
        self.modName = modName
        self.oldVersion = oldVersion
        self.newVersion = newVersion
        self.result = result
        self.detail = detail
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ts = c.value(.ts, 0)
        user = c.maybe(.user)
        action = c.value(.action, "")
        modId = c.maybe(.modId)
        modName = c.maybe(.modName)
        oldVersion = c.maybe(.oldVersion)
        newVersion = c.maybe(.newVersion)
        result = c.value(.result, "")
        detail = c.maybe(.detail)
    }
}

public struct ModHistory: Decodable, Equatable, Sendable {
    public var history: [ModHistoryRow]

    enum CodingKeys: String, CodingKey { case history }

    public init(from decoder: Decoder) throws {
        history = try decoder.container(keyedBy: CodingKeys.self).each(.history)
    }
}

/// Something one jar check found, or noted, in a jar: a strings key and its values.
public struct JarScanWords: Decodable, Equatable, Sendable {
    public var scanner: String?
    public var words: String
    public var values: [String: String]

    enum CodingKeys: String, CodingKey { case scanner, words, values }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        scanner = c.maybe(.scanner)
        words = c.value(.words, "")
        values = c.value(.values, [:])
    }
}

public struct JarScanRow: Decodable, Equatable, Identifiable, Sendable {
    public var filename: String
    public var findings: [JarScanWords]
    public var notes: [JarScanWords]
    public var words: String
    public var values: [String: String]
    /// New or different since the last check, and nothing in the app changed it.
    public var changedOutside: Bool

    public var id: String { filename }

    enum CodingKeys: String, CodingKey {
        case filename, findings, notes, words, values
        case changedOutside = "changed_outside"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        filename = c.value(.filename, "")
        findings = c.each(.findings)
        notes = c.each(.notes)
        words = c.value(.words, "")
        values = c.value(.values, [:])
        changedOutside = c.value(.changedOutside, false)
    }
}

public struct QuarantinedJar: Decodable, Equatable, Identifiable, Sendable {
    public var id: String
    public var filename: String
    public var at: Double
    public var findings: [JarScanWords]

    enum CodingKeys: String, CodingKey { case id, filename, at, findings }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        filename = c.value(.filename, "")
        at = c.value(.at, 0)
        findings = c.each(.findings)
    }
}

public struct JarScanSummary: Decodable, Equatable, Sendable {
    public var checked = 0
    public var jars = 0
    public var found = 0

    enum CodingKeys: String, CodingKey { case checked, jars, found }

    public init() {}

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        checked = c.value(.checked, 0)
        jars = c.value(.jars, 0)
        found = c.value(.found, 0)
    }
}

/// The jar check: each jar's result and the jars in quarantine.
public struct JarScanReport: Decodable, Equatable, Sendable {
    public var checkedAt: Double?
    public var weekly: Bool
    public var onChange: Bool
    public var beforeStart: Bool
    public var askFirst: Bool
    public var updateHoldDays: Int
    public var jars: [JarScanRow]
    public var quarantine: [QuarantinedJar]
    public var summary: JarScanSummary

    enum CodingKeys: String, CodingKey {
        case weekly, jars, quarantine, summary
        case checkedAt = "checked_at"
        case onChange = "on_change"
        case beforeStart = "before_start"
        case askFirst = "ask_first"
        case updateHoldDays = "update_hold_days"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        checkedAt = c.maybe(.checkedAt)
        weekly = c.value(.weekly, true)
        onChange = c.value(.onChange, true)
        beforeStart = c.value(.beforeStart, false)
        askFirst = c.value(.askFirst, false)
        updateHoldDays = c.value(.updateHoldDays, 3)
        jars = c.each(.jars)
        quarantine = c.each(.quarantine)
        summary = c.value(.summary, JarScanSummary())
    }
}

public struct JarScanUndone: Decodable, Equatable, Sendable {
    public var restored: String?
}

public struct DeepScanTook: Decodable, Equatable, Sendable {
    public var seconds: Double
    public var files: Int

    enum CodingKeys: String, CodingKey { case seconds, files }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        seconds = c.value(.seconds, 0)
        files = c.value(.files, 0)
    }
}

public struct DeepScanDownloaded: Decodable, Equatable, Sendable {
    public var tag: String
    public var at: Double

    enum CodingKeys: String, CodingKey { case tag, at }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tag = c.value(.tag, "")
        at = c.value(.at, 0)
    }
}

/// One check the deep scan can run, with how long it took last time.
public struct DeepScanScanner: Decodable, Equatable, Identifiable, Sendable {
    public var id: String
    public var words: String
    public var what: String
    public var speed: String
    public var took: DeepScanTook?
    public var downloaded: DeepScanDownloaded?
    /// False for an outside program until its first download.
    public var installed: Bool
    /// False while jNeedle's list can't be downloaded; the row is hidden.
    public var available: Bool
    /// Download size in bytes, only for a program not downloaded yet.
    public var size: Int?
    /// VirusTotal only: whether the owner saved a key. Nil on every other row.
    public var key: Bool?

    enum CodingKeys: String, CodingKey { case id, words, what, speed, took, downloaded, installed, available, size, key }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        words = c.value(.words, "")
        what = c.value(.what, "")
        speed = c.value(.speed, "")
        took = c.maybe(.took)
        downloaded = c.maybe(.downloaded)
        installed = c.value(.installed, true)
        available = c.value(.available, true)
        size = c.maybe(.size)
        key = c.maybe(.key)
    }
}

/// A file the deep scan listed: Known bad, or Worth a look. `root` is "server" or "java".
public struct DeepScanFound: Decodable, Equatable, Identifiable, Sendable {
    public var root: String
    public var path: String
    public var kind: String
    public var findings: [JarScanWords]
    public var state: String

    public var id: String { root + ":" + path }

    enum CodingKeys: String, CodingKey { case root, path, kind, findings, state }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        root = c.value(.root, "server")
        path = c.value(.path, "")
        kind = c.value(.kind, "")
        findings = c.each(.findings)
        state = c.value(.state, "")
    }
}

public struct DeepScanLast: Decodable, Equatable, Sendable {
    public var finished: Double
    public var files: Int
    public var skipped: Int
    public var found: [DeepScanFound]
    public var errors: [JarScanWords]

    enum CodingKeys: String, CodingKey { case finished, files, skipped, found, errors }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        finished = c.value(.finished, 0)
        files = c.value(.files, 0)
        skipped = c.value(.skipped, 0)
        found = c.each(.found)
        errors = c.each(.errors)
    }
}

public struct DeepScanRunning: Decodable, Equatable, Sendable {
    public var done: Double?
    public var total: Double?

    enum CodingKeys: String, CodingKey { case done, total }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        done = c.maybe(.done)
        total = c.maybe(.total)
    }
}

/// The deep scan: its checks, the scan running now and the last results.
public struct DeepScanReport: Decodable, Equatable, Sendable {
    public var scanners: [DeepScanScanner]
    public var running: DeepScanRunning?
    public var last: DeepScanLast?
    public var options: DeepScanOptions

    enum CodingKeys: String, CodingKey { case scanners, running, last, options }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        scanners = c.each(.scanners)
        running = c.maybe(.running)
        last = c.maybe(.last)
        options = c.value(.options, DeepScanOptions())
    }

    /// The checks the person can pick from now.
    /// VirusTotal stays listed without a key, so the owner can add one.
    public var shown: [DeepScanScanner] { scanners.filter { $0.available || $0.key != nil } }
}

/// Deep scan before each start, and the daily or weekly run. `next` is
/// when the next run starts (nil when the schedule is off).
public struct DeepScanOptions: Decodable, Equatable, Sendable {
    public var beforeStart = false
    public var schedule = "off"
    public var time = "04:00"
    public var day = "sun"
    public var next: Double?

    enum CodingKeys: String, CodingKey {
        case schedule, time, day, next
        case beforeStart = "before_start"
    }

    public init() {}

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        beforeStart = c.value(.beforeStart, false)
        schedule = c.value(.schedule, "off")
        time = c.value(.time, "04:00")
        day = c.value(.day, "sun")
        next = c.maybe(.next)
    }
}

/// A Modrinth search result. `iconUrl` is loaded straight from Modrinth.
public struct ModrinthHit: Decodable, Equatable, Identifiable, Sendable {
    public var projectId: String?
    public var slug: String
    public var title: String
    public var description: String?
    public var downloads: Int64?
    public var iconUrl: String?
    public var page: String?
    /// "modrinth" or "curseforge", and the site's name as shown.
    public var source: String?
    public var sourceName: String?
    /// Modpacks: "Fabric", "Forge"..., the newest Minecraft version, "unsupported" when it's
    /// made for players' games only, and what Modrinth was told it holds ("Shows ads").
    public var loaders: [String]
    public var minecraftVersion: String?
    public var serverSide: String?
    public var disclosures: [String]

    public var id: String { slug }

    enum CodingKeys: String, CodingKey {
        case slug, title, description, downloads, page, source, loaders, disclosures
        case projectId = "project_id"
        case iconUrl = "icon_url"
        case sourceName = "source_name"
        case minecraftVersion = "minecraft_version"
        case serverSide = "server_side"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        projectId = c.maybe(.projectId)
        slug = c.value(.slug, "")
        title = c.value(.title, "")
        description = c.maybe(.description)
        downloads = c.maybe(.downloads)
        iconUrl = c.maybe(.iconUrl)
        page = c.maybe(.page)
        source = c.maybe(.source)
        sourceName = c.maybe(.sourceName)
        loaders = c.each(.loaders)
        minecraftVersion = c.maybe(.minecraftVersion)
        serverSide = c.maybe(.serverSide)
        disclosures = c.each(.disclosures)
    }
}

public struct ModSearch: Decodable, Equatable, Sendable {
    public var hits: [ModrinthHit]
    public var total: Int?
    /// A site that couldn't be asked: "CurseForge isn't available right now."
    public var problems: [String]

    enum CodingKeys: String, CodingKey { case hits, total, problems }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hits = c.each(.hits)
        total = c.maybe(.total)
        problems = c.each(.problems)
    }
}

public struct ModrinthFile: Decodable, Equatable, Sendable {
    public var filename: String?
    public var size: Int64?

    public init(filename: String? = nil, size: Int64? = nil) {
        self.filename = filename
        self.size = size
    }
}

public struct ModrinthDependency: Decodable, Equatable, Sendable {
    public var projectId: String?
    public var type: String?

    enum CodingKeys: String, CodingKey {
        case type
        case projectId = "project_id"
    }

    public init(projectId: String? = nil, type: String? = nil) {
        self.projectId = projectId
        self.type = type
    }
}

public struct ModrinthVersion: Decodable, Equatable, Sendable {
    public var versionId: String?
    public var versionNumber: String?
    public var gameVersions: [String]
    public var dependencies: [ModrinthDependency]
    public var file: ModrinthFile?

    enum CodingKeys: String, CodingKey {
        case dependencies, file
        case versionId = "version_id"
        case versionNumber = "version_number"
        case gameVersions = "game_versions"
    }

    public init(
        versionId: String? = nil, versionNumber: String? = nil, gameVersions: [String] = [],
        dependencies: [ModrinthDependency] = [], file: ModrinthFile? = nil
    ) {
        self.versionId = versionId
        self.versionNumber = versionNumber
        self.gameVersions = gameVersions
        self.dependencies = dependencies
        self.file = file
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        versionId = c.maybe(.versionId)
        versionNumber = c.maybe(.versionNumber)
        gameVersions = c.value(.gameVersions, [])
        dependencies = c.each(.dependencies)
        file = c.maybe(.file)
    }
}

public struct ModrinthProject: Decodable, Equatable, Sendable {
    public var slug: String
    public var title: String
    public var description: String?
    public var license: String?
    public var serverSide: String?
    public var clientSide: String?
    public var iconUrl: String?
    public var page: String?

    enum CodingKeys: String, CodingKey {
        case slug, title, description, license, page
        case serverSide = "server_side"
        case clientSide = "client_side"
        case iconUrl = "icon_url"
    }

    public init(
        slug: String = "", title: String = "", description: String? = nil, license: String? = nil,
        serverSide: String? = nil, clientSide: String? = nil, iconUrl: String? = nil, page: String? = nil
    ) {
        self.slug = slug
        self.title = title
        self.description = description
        self.license = license
        self.serverSide = serverSide
        self.clientSide = clientSide
        self.iconUrl = iconUrl
        self.page = page
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = c.value(.slug, "")
        title = c.value(.title, "")
        description = c.maybe(.description)
        license = c.maybe(.license)
        serverSide = c.maybe(.serverSide)
        clientSide = c.maybe(.clientSide)
        iconUrl = c.maybe(.iconUrl)
        page = c.maybe(.page)
    }
}

/// A Modrinth project and its versions that fit this server, newest first.
public struct ModProject: Decodable, Equatable, Sendable {
    public var project: ModrinthProject
    public var versions: [ModrinthVersion]

    enum CodingKeys: String, CodingKey { case project, versions }

    public init(project: ModrinthProject, versions: [ModrinthVersion]) {
        self.project = project
        self.versions = versions
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        project = c.value(.project, ModrinthProject())
        versions = c.each(.versions)
    }
}

public struct DependencyNeeded: Decodable, Equatable, Sendable {
    public var modId: String
    public var title: String?
    public var installed: Bool

    enum CodingKeys: String, CodingKey {
        case title, installed
        case modId = "mod_id"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modId = c.value(.modId, "")
        title = c.maybe(.title)
        installed = c.value(.installed, false)
    }
}

public struct ModInstalled: Decodable, Equatable, Sendable {
    public var installed: ModRef?
    public var dependenciesRequired: [DependencyNeeded]

    enum CodingKeys: String, CodingKey {
        case installed
        case dependenciesRequired = "dependencies_required"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        installed = c.maybe(.installed)
        dependenciesRequired = c.each(.dependenciesRequired)
    }
}

public struct DependencyItem: Decodable, Equatable, Sendable {
    public var modId: String
    public var name: String
    public var kind: String
    /// Minecraft, the loader or Java rather than a mod.
    public var platform: Bool
    public var status: String?
    public var rangeText: String?
    public var reason: String?

    enum CodingKeys: String, CodingKey {
        case name, kind, platform, status, reason
        case modId = "mod_id"
        case rangeText = "range_text"
    }

    public init(modId: String, name: String = "", kind: String = "", platform: Bool = false, status: String? = nil, rangeText: String? = nil, reason: String? = nil) {
        self.modId = modId
        self.name = name
        self.kind = kind
        self.platform = platform
        self.status = status
        self.rangeText = rangeText
        self.reason = reason
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modId = try c.decode(String.self, forKey: .modId)
        name = c.value(.name, "")
        kind = c.value(.kind, "")
        platform = c.value(.platform, false)
        status = c.maybe(.status)
        rangeText = c.maybe(.rangeText)
        reason = c.maybe(.reason)
    }
}

public struct DependencyReport: Decodable, Equatable, Sendable {
    public var items: [DependencyItem]

    enum CodingKeys: String, CodingKey { case items }

    public init(items: [DependencyItem]) {
        self.items = items
    }

    public init(from decoder: Decoder) throws {
        items = try decoder.container(keyedBy: CodingKeys.self).each(.items)
    }
}

public struct PlannedDependency: Decodable, Equatable, Sendable {
    public var modId: String
    public var title: String?
    public var versionNumber: String?
    public var size: Int64?

    enum CodingKeys: String, CodingKey {
        case title, size
        case modId = "mod_id"
        case versionNumber = "version_number"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modId = c.value(.modId, "")
        title = c.maybe(.title)
        versionNumber = c.maybe(.versionNumber)
        size = c.maybe(.size)
    }
}

/// One the app can't install by itself, with a page to get it by hand.
public struct UnresolvableDependency: Decodable, Equatable, Sendable {
    public var modId: String
    public var name: String?
    public var reason: String
    public var page: String?

    enum CodingKeys: String, CodingKey {
        case name, reason, page
        case modId = "mod_id"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modId = c.value(.modId, "")
        name = c.maybe(.name)
        reason = c.value(.reason, "")
        page = c.maybe(.page)
    }
}

/// What "Install the missing ones" would download. Nothing is yet.
public struct DependencyPlan: Decodable, Equatable, Sendable {
    public var items: [PlannedDependency]
    public var unresolvable: [UnresolvableDependency]

    enum CodingKeys: String, CodingKey { case items, unresolvable }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = c.each(.items)
        unresolvable = c.each(.unresolvable)
    }
}

public struct DependencyResult: Decodable, Equatable, Sendable {
    public var title: String
    public var result: String
    public var detail: String?

    enum CodingKeys: String, CodingKey { case title, result, detail }

    public init(title: String, result: String, detail: String? = nil) {
        self.title = title
        self.result = result
        self.detail = detail
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = c.value(.title, "")
        result = c.value(.result, "")
        detail = c.maybe(.detail)
    }
}

public struct DependencyInstall: Decodable, Equatable, Sendable {
    public var results: [DependencyResult]

    enum CodingKeys: String, CodingKey { case results }

    public init(from decoder: Decoder) throws {
        results = try decoder.container(keyedBy: CodingKeys.self).each(.results)
    }
}

public struct ModUploaded: Decodable, Equatable, Sendable {
    public var installed: ModRef?
}

/// A mod Update all would change. Turned-off ones are listed, not ticked.
public struct UpdateAllRow: Decodable, Equatable, Identifiable, Sendable {
    public var modId: String
    public var name: String
    public var filename: String
    public var enabled: Bool
    public var installedVersion: String
    public var latestVersion: String
    public var versionId: String?
    /// How old the site's list was when it came from a saved copy; nil when asked just now.
    public var saved: String?
    /// Update all leaves it out until this time; Update now skips the wait.
    public var heldUntil: Double?

    public var id: String { filename }

    enum CodingKeys: String, CodingKey {
        case name, filename, enabled, saved
        case modId = "mod_id"
        case installedVersion = "installed_version"
        case latestVersion = "latest_version"
        case versionId = "version_id"
        case heldUntil = "held_until"
    }

    public init(
        modId: String = "", name: String = "", filename: String, enabled: Bool = true,
        installedVersion: String = "", latestVersion: String = "", versionId: String? = nil, saved: String? = nil,
        heldUntil: Double? = nil
    ) {
        self.modId = modId
        self.name = name
        self.filename = filename
        self.enabled = enabled
        self.installedVersion = installedVersion
        self.latestVersion = latestVersion
        self.versionId = versionId
        self.saved = saved
        self.heldUntil = heldUntil
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        modId = c.value(.modId, "")
        name = c.value(.name, "")
        filename = try c.decode(String.self, forKey: .filename)
        enabled = c.value(.enabled, true)
        installedVersion = c.value(.installedVersion, "")
        latestVersion = c.value(.latestVersion, "")
        versionId = c.maybe(.versionId)
        saved = c.maybe(.saved)
        heldUntil = c.maybe(.heldUntil)
    }
}

/// One Modrinth couldn't be asked about, with the reason.
public struct UpdateAllUnchecked: Decodable, Equatable, Sendable {
    public var name: String
    public var reason: String

    enum CodingKeys: String, CodingKey { case name, reason }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        reason = c.value(.reason, "")
    }
}

public struct UpdateAllPlan: Decodable, Equatable, Sendable {
    public var updates: [UpdateAllRow]
    public var unchecked: [UpdateAllUnchecked]
    public var serverRunning: Bool
    public var blockedBy: String?

    enum CodingKeys: String, CodingKey {
        case updates, unchecked
        case serverRunning = "server_running"
        case blockedBy = "blocked_by"
    }

    public init(updates: [UpdateAllRow] = [], unchecked: [UpdateAllUnchecked] = [], serverRunning: Bool = false, blockedBy: String? = nil) {
        self.updates = updates
        self.unchecked = unchecked
        self.serverRunning = serverRunning
        self.blockedBy = blockedBy
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        updates = c.each(.updates)
        unchecked = c.each(.unchecked)
        serverRunning = c.value(.serverRunning, false)
        blockedBy = c.maybe(.blockedBy)
    }
}

public struct UpdateAllDone: Decodable, Equatable, Sendable {
    public var name: String
    public var previousVersion: String
    public var version: String

    enum CodingKeys: String, CodingKey {
        case name, version
        case previousVersion = "previous_version"
    }

    public init(name: String, previousVersion: String, version: String) {
        self.name = name
        self.previousVersion = previousVersion
        self.version = version
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        previousVersion = c.value(.previousVersion, "")
        version = c.value(.version, "")
    }
}

public struct UpdateAllResult: Decodable, Equatable, Sendable {
    public var updated: [UpdateAllDone]
    public var safetyBackup: SafetyBackup?
    public var undo: Undo?
    /// Names Update all left out because their version is very new.
    public var held: [String]

    enum CodingKeys: String, CodingKey {
        case updated, undo, held
        case safetyBackup = "safety_backup"
    }

    public init(updated: [UpdateAllDone], safetyBackup: SafetyBackup? = nil, undo: Undo? = nil, held: [String] = []) {
        self.updated = updated
        self.safetyBackup = safetyBackup
        self.undo = undo
        self.held = held
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        updated = c.each(.updated)
        safetyBackup = c.maybe(.safetyBackup)
        undo = c.maybe(.undo)
        held = c.value(.held, [])
    }
}

public struct ModpackFile: Decodable, Equatable, Sendable {
    public var name: String
    public var size: Int64?
    /// download, client_only or refused.
    public var status: String
    public var reason: String?

    enum CodingKeys: String, CodingKey { case name, size, status, reason }

    public init(name: String = "", size: Int64? = nil, status: String = "", reason: String? = nil) {
        self.name = name
        self.size = size
        self.status = status
        self.reason = reason
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        size = c.maybe(.size)
        status = c.value(.status, "")
        reason = c.maybe(.reason)
    }
}

/// What a modpack holds, read before anything changes.
public struct Modpack: Decodable, Equatable, Sendable {
    public var name: String
    public var minecraftVersion: String?
    public var typeName: String?
    public var loaderVersion: String?
    public var files: [ModpackFile]
    public var downloadCount: Int
    public var downloadBytes: Int64
    public var clientOnly: [String]
    public var skippedOverrides: [String]
    /// Jars no site checked: importing them needs ownJars = true and "Add own .jar".
    public var ownJars: [String]
    /// From the pack's command-block.json: plugins to get by hand.
    public var pluginsToGet: [String]
    public var problems: [String]
    public var canImport: Bool

    enum CodingKeys: String, CodingKey {
        case name, files, problems
        case minecraftVersion = "minecraft_version"
        case typeName = "type_name"
        case loaderVersion = "loader_version"
        case downloadCount = "download_count"
        case downloadBytes = "download_bytes"
        case clientOnly = "client_only"
        case skippedOverrides = "skipped_overrides"
        case ownJars = "own_jars"
        case pluginsToGet = "plugins_to_get"
        case canImport = "can_import"
    }

    public init(
        name: String = "", minecraftVersion: String? = nil, typeName: String? = nil, loaderVersion: String? = nil,
        files: [ModpackFile] = [], downloadCount: Int = 0, downloadBytes: Int64 = 0, clientOnly: [String] = [],
        skippedOverrides: [String] = [], ownJars: [String] = [], pluginsToGet: [String] = [],
        problems: [String] = [], canImport: Bool = false
    ) {
        self.name = name
        self.minecraftVersion = minecraftVersion
        self.typeName = typeName
        self.loaderVersion = loaderVersion
        self.files = files
        self.downloadCount = downloadCount
        self.downloadBytes = downloadBytes
        self.clientOnly = clientOnly
        self.skippedOverrides = skippedOverrides
        self.ownJars = ownJars
        self.pluginsToGet = pluginsToGet
        self.problems = problems
        self.canImport = canImport
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        minecraftVersion = c.maybe(.minecraftVersion)
        typeName = c.maybe(.typeName)
        loaderVersion = c.maybe(.loaderVersion)
        files = c.each(.files)
        downloadCount = c.value(.downloadCount, 0)
        downloadBytes = c.value(.downloadBytes, 0)
        clientOnly = c.value(.clientOnly, [])
        skippedOverrides = c.value(.skippedOverrides, [])
        ownJars = c.value(.ownJars, [])
        pluginsToGet = c.value(.pluginsToGet, [])
        problems = c.value(.problems, [])
        canImport = c.value(.canImport, false)
    }
}

public struct ModpackInspected: Decodable, Equatable, Sendable {
    public var token: String
    public var pack: Modpack
    /// The server's software changes too (another type or Minecraft version).
    public var versionChange: Bool

    enum CodingKeys: String, CodingKey { case token, pack, into }
    enum IntoKeys: String, CodingKey { case versionChange = "version_change" }

    public init(token: String, pack: Modpack, versionChange: Bool = false) {
        self.token = token
        self.pack = pack
        self.versionChange = versionChange
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        token = try c.decode(String.self, forKey: .token)
        pack = c.value(.pack, Modpack())
        let into = try? c.nestedContainer(keyedBy: IntoKeys.self, forKey: .into)
        versionChange = into?.value(.versionChange, false) ?? false
    }
}

public struct ModpackImported: Decodable, Equatable, Sendable {
    public var name: String

    enum CodingKeys: String, CodingKey { case modpack }
    enum PackKeys: String, CodingKey { case name }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let pack = try? c.nestedContainer(keyedBy: PackKeys.self, forKey: .modpack)
        name = pack?.value(.name, "") ?? ""
    }
}

/// One Bedrock behavior or resource pack.
public struct Addon: Decodable, Equatable, Identifiable, Sendable {
    public var uuid: String
    public var name: String
    public var version: String
    public var description: String?
    /// behavior or resource.
    public var kind: String
    public var folder: String
    public var enabled: Bool

    public var id: String { uuid }

    enum CodingKeys: String, CodingKey { case uuid, name, version, description, kind, folder, enabled }

    public init(uuid: String, name: String = "", version: String = "", description: String? = nil, kind: String = "", folder: String = "", enabled: Bool = false) {
        self.uuid = uuid
        self.name = name
        self.version = version
        self.description = description
        self.kind = kind
        self.folder = folder
        self.enabled = enabled
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        uuid = try c.decode(String.self, forKey: .uuid)
        name = c.value(.name, "")
        version = c.value(.version, "")
        description = c.maybe(.description)
        kind = c.value(.kind, "")
        folder = c.value(.folder, "")
        enabled = c.value(.enabled, false)
    }
}

public struct AddonList: Decodable, Equatable, Sendable {
    public var addons: [Addon]
    /// The world the packs are on; nil before the server made one.
    public var world: String?
    /// Bedrock's "Required to join" (texturepack-required).
    public var required: Bool
    public var running: Bool

    enum CodingKeys: String, CodingKey { case addons, world, required, running }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        addons = c.each(.addons)
        world = c.maybe(.world)
        required = c.value(.required, false)
        running = c.value(.running, false)
    }
}

public struct AddonResult: Decodable, Equatable, Sendable {
    public var installed: [Addon]
    public var restartNeeded: Bool?

    enum CodingKeys: String, CodingKey {
        case installed
        case restartNeeded = "restart_needed"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        installed = c.each(.installed)
        restartNeeded = c.maybe(.restartNeeded)
    }
}

/// One setting from server.properties. `value` is typed (a word, a number,
/// true or false); nil when the file doesn't set it or can't be read.
public struct GameSetting: Decodable, Equatable, Identifiable, Sendable {
    public var key: String
    /// choice, bool, int, text, port or seed.
    public var kind: String
    public var value: JSONValue?
    public var set: Bool
    public var problem: String?
    /// The file's own text for it, as written (shown when it can't be read as a value).
    public var raw: String?
    public var minecraftDefault: String
    public var choices: [String]
    public var maxLength: Int?
    /// How many lines the value can have (the MOTD has 2).
    public var lines: Int
    /// "world_exists" for the seed once the world is made.
    public var readOnly: String?

    public var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, kind, value, set, problem, raw, choices, lines
        case minecraftDefault = "minecraft_default"
        case maxLength = "max_length"
        case readOnly = "read_only"
    }

    public init(
        key: String, kind: String = "text", value: JSONValue? = nil, set: Bool = false, problem: String? = nil,
        raw: String? = nil, minecraftDefault: String = "", choices: [String] = [], maxLength: Int? = nil,
        lines: Int = 1, readOnly: String? = nil
    ) {
        self.key = key
        self.kind = kind
        self.value = value
        self.set = set
        self.problem = problem
        self.raw = raw
        self.minecraftDefault = minecraftDefault
        self.choices = choices
        self.maxLength = maxLength
        self.lines = lines
        self.readOnly = readOnly
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        kind = c.value(.kind, "text")
        value = c.maybe(.value)
        set = c.value(.set, false)
        problem = c.maybe(.problem)
        raw = c.maybe(.raw)
        minecraftDefault = c.value(.minecraftDefault, "")
        choices = c.value(.choices, [])
        maxLength = c.maybe(.maxLength)
        lines = c.value(.lines, 1)
        readOnly = c.maybe(.readOnly)
    }
}

public struct GameSettings: Decodable, Equatable, Sendable {
    public var file: String
    public var exists: Bool
    public var fields: [GameSetting]
    public var running: Bool
    public var restartNeeded: Bool
    /// The whole file as it is now, for the Technical mode editor.
    public var text: String
    /// Whether server-icon.png is there; nil on Bedrock, which shows none.
    public var icon: Bool?

    enum CodingKeys: String, CodingKey {
        case file, exists, fields, running, text, icon
        case restartNeeded = "restart_needed"
    }

    public init(file: String = "", exists: Bool = false, fields: [GameSetting] = [], running: Bool = false,
                restartNeeded: Bool = false, text: String = "", icon: Bool? = nil) {
        self.file = file
        self.exists = exists
        self.fields = fields
        self.running = running
        self.restartNeeded = restartNeeded
        self.text = text
        self.icon = icon
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        file = c.value(.file, "")
        exists = c.value(.exists, false)
        fields = c.each(.fields)
        running = c.value(.running, false)
        restartNeeded = c.value(.restartNeeded, false)
        text = c.value(.text, "")
        icon = c.maybe(.icon)
    }
}

/// server-icon.png in base64, or nil when the server has none.
public struct ServerIcon: Decodable, Equatable, Sendable {
    public var png: String?

    public init(png: String? = nil) { self.png = png }
}

public struct ServerIconSaved: Decodable, Equatable, Sendable {
    public var icon: Bool
    public var running: Bool

    enum CodingKeys: String, CodingKey { case icon, running }

    public init(icon: Bool = false, running: Bool = false) {
        self.icon = icon
        self.running = running
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        icon = c.value(.icon, false)
        running = c.value(.running, false)
    }
}

public struct GameSettingsSaved: Decodable, Equatable, Sendable {
    public var keys: [String]
    public var nothingChanged: Bool?
    public var running: Bool
    public var restartNeeded: Bool

    enum CodingKeys: String, CodingKey {
        case keys, running
        case nothingChanged = "nothing_changed"
        case restartNeeded = "restart_needed"
    }

    public init(keys: [String] = [], nothingChanged: Bool? = nil, running: Bool = false, restartNeeded: Bool = false) {
        self.keys = keys
        self.nothingChanged = nothingChanged
        self.running = running
        self.restartNeeded = restartNeeded
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        keys = c.value(.keys, [])
        nothingChanged = c.maybe(.nothingChanged)
        running = c.value(.running, false)
        restartNeeded = c.value(.restartNeeded, false)
    }
}

// MARK: - Make a modpack

/// A config file Make a modpack can put in.
public struct ModpackConfigFile: Decodable, Equatable, Sendable {
    public var path: String
    public var sizeBytes: Int64

    enum CodingKeys: String, CodingKey {
        case path
        case sizeBytes = "size_bytes"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        sizeBytes = c.value(.sizeBytes, 0)
    }
}

public struct PlayersLoader: Decodable, Equatable, Sendable {
    public var id: String
    public var name: String
}

/// What Make a modpack offers. `playersLoaders` is empty on a mod server.
public struct ModpackMakeOptions: Decodable, Equatable, Sendable {
    public var name: String
    public var version: String
    public var configs: [ModpackConfigFile]
    public var playersLoaders: [PlayersLoader]
    public var discord: Bool
    /// The Companion mod is on the server: "Required to join" can be ticked.
    public var requiredToJoin: Bool
    /// It was ticked last time.
    public var requiredOn: Bool
    /// A vanilla server, which can't run the Companion: offer Change server type.
    public var needsFabric: Bool
    public var chatLine: ChatLine
    public var titleScreen: TitleScreen

    enum CodingKeys: String, CodingKey {
        case name, version, configs, discord
        case playersLoaders = "players_loaders"
        case requiredToJoin = "required_to_join"
        case chatLine = "chat_line"
        case titleScreen = "title_screen"
    }

    private struct Required: Decodable {
        var available: Bool?
        var on: Bool?
        var needsFabric: Bool?

        enum CodingKeys: String, CodingKey {
            case available, on
            case needsFabric = "needs_fabric"
        }
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.value(.name, "")
        version = c.value(.version, "1")
        configs = c.each(.configs)
        playersLoaders = c.each(.playersLoaders)
        discord = c.value(.discord, false)
        let required: Required? = c.maybe(.requiredToJoin)
        requiredToJoin = required?.available ?? false
        requiredOn = required?.on ?? false
        needsFabric = required?.needsFabric ?? false
        chatLine = c.maybe(.chatLine) ?? ChatLine()
        titleScreen = c.maybe(.titleScreen) ?? TitleScreen()
    }
}

/// What friends with the Companion see when Minecraft starts: the join
/// button beside Realms, the yellow flavor text and the background picture.
public struct TitleScreen: Codable, Equatable, Sendable {
    public struct Button: Codable, Equatable, Sendable {
        public var on = false
        public var address = ""
        public init(on: Bool = false, address: String = "") { self.on = on; self.address = address }
    }

    public struct Flavor: Codable, Equatable, Sendable {
        public var on = false
        public var text = ""
        public init(on: Bool = false, text: String = "") { self.on = on; self.text = text }
    }

    public struct OnOff: Codable, Equatable, Sendable {
        public var on = false
        public init(on: Bool = false) { self.on = on }
    }

    public var button: Button
    public var flavorText: Flavor
    public var background: OnOff
    /// A picture was uploaded (read only; never sent).
    public var backgroundSet: Bool

    enum CodingKeys: String, CodingKey {
        case button, background
        case flavorText = "flavor_text"
        case backgroundSet = "background_set"
    }

    public init(button: Button = Button(), flavorText: Flavor = Flavor(), background: OnOff = OnOff(), backgroundSet: Bool = false) {
        self.button = button
        self.flavorText = flavorText
        self.background = background
        self.backgroundSet = backgroundSet
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        button = c.maybe(.button) ?? Button()
        flavorText = c.maybe(.flavorText) ?? Flavor()
        background = c.maybe(.background) ?? OnOff()
        backgroundSet = c.value(.backgroundSet, false)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(button, forKey: .button)
        try c.encode(flavorText, forKey: .flavorText)
        try c.encode(background, forKey: .background)
    }
}

/// The line players without the modpack see; {name}, {version}, {link} and {get_it} are filled in.
public struct ChatLine: Codable, Equatable, Sendable {
    public var on: Bool
    public var text: String

    public init(on: Bool = true, text: String = "") {
        self.on = on
        self.text = text
    }
}

/// The person's answers to Make a modpack.
public struct MakeModpack: Encodable, Equatable, Sendable {
    public var name: String
    public var version: String
    public var leaveOutServerOnly: Bool
    public var configs: [String]
    public var playersLoader: String
    public var required: Bool
    /// nil: the agent's own wording.
    public var chatLine: ChatLine?
    public var titleScreen: TitleScreen?

    enum CodingKeys: String, CodingKey {
        case name, version, configs, required
        case leaveOutServerOnly = "leave_out_server_only"
        case playersLoader = "players_loader"
        case chatLine = "chat_line"
        case titleScreen = "title_screen"
    }

    public init(
        name: String, version: String, leaveOutServerOnly: Bool = true, configs: [String] = [],
        playersLoader: String = "fabric", required: Bool = false, chatLine: ChatLine? = nil,
        titleScreen: TitleScreen? = nil
    ) {
        self.titleScreen = titleScreen
        self.name = name
        self.version = version
        self.leaveOutServerOnly = leaveOutServerOnly
        self.configs = configs
        self.playersLoader = playersLoader
        self.required = required
        self.chatLine = chatLine
    }
}

/// A CurseForge mod its author keeps from other apps: never put in.
public struct GetYourself: Decodable, Equatable, Sendable {
    public var name: String
}

/// A made modpack, downloaded with `token`. `trustNote`: own files are inside.
public struct ModpackMade: Decodable, Equatable, Sendable {
    public var token: String
    public var filename: String
    public var sizeBytes: Int64?
    public var trustNote: String?
    public var getYourself: [GetYourself]
    public var pluginsToGet: [String]
    public var leftOut: [String]
    public var discord: Bool

    enum CodingKeys: String, CodingKey {
        case token, filename, discord
        case sizeBytes = "size_bytes"
        case trustNote = "trust_note"
        case getYourself = "get_yourself"
        case pluginsToGet = "plugins_to_get"
        case leftOut = "left_out"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        token = try c.decode(String.self, forKey: .token)
        filename = c.value(.filename, "modpack.mrpack")
        sizeBytes = c.maybe(.sizeBytes)
        trustNote = c.maybe(.trustNote)
        getYourself = c.each(.getYourself)
        pluginsToGet = c.value(.pluginsToGet, [])
        leftOut = c.value(.leftOut, [])
        discord = c.value(.discord, false)
    }
}

/// `asLink`: too big for Discord, so the bot posted the download link.
public struct ModpackPosted: Decodable, Equatable, Sendable {
    public var posted: Int
    public var asLink: Bool

    enum CodingKeys: String, CodingKey {
        case posted
        case asLink = "as_link"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        posted = c.value(.posted, 0)
        asLink = c.value(.asLink, false)
    }
}

/// The server resource pack: a zip the download link serves (`served`), or an outside link.
public struct ResourcePack: Decodable, Equatable, Sendable {
    public var source: String
    public var filename: String?
    public var url: String?
    public var sha1: String?
    public var sizeBytes: Int64?
    public var required: Bool
    public var served: Bool

    enum CodingKeys: String, CodingKey {
        case source, filename, url, sha1, required, served
        case sizeBytes = "size_bytes"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = c.value(.source, "file")
        filename = c.maybe(.filename)
        url = c.maybe(.url)
        sha1 = c.maybe(.sha1)
        sizeBytes = c.maybe(.sizeBytes)
        required = c.value(.required, false)
        served = c.value(.served, false)
    }
}

/// The Download link card. `whyNot`: why it can't be turned on (no Tailscale yet).
public struct DownloadLink: Decodable, Equatable, Sendable {
    public var on: Bool
    public var whyNot: String?
    public var listening: Bool
    public var modpackUrl: String?
    public var speedCapBytes: Int64
    public var resourcePack: ResourcePack?
    public var restartNeeded: Bool

    enum CodingKeys: String, CodingKey {
        case on, listening
        case whyNot = "why_not"
        case modpackUrl = "modpack_url"
        case speedCapBytes = "speed_cap_bytes"
        case resourcePack = "resource_pack"
        case restartNeeded = "restart_needed"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        on = c.value(.on, false)
        whyNot = c.maybe(.whyNot)
        listening = c.value(.listening, false)
        modpackUrl = c.maybe(.modpackUrl)
        speedCapBytes = c.value(.speedCapBytes, 0)
        resourcePack = c.maybe(.resourcePack)
        restartNeeded = c.value(.restartNeeded, false)
    }
}

/// Sign in with Modrinth on the PC. The token itself never reaches the phone.
public struct ModrinthAccount: Decodable, Equatable, Sendable {
    public var available: Bool
    public var signedIn: Bool
    public var username: String?
    public var ranOut: Bool

    enum CodingKeys: String, CodingKey {
        case available, username
        case signedIn = "signed_in"
        case ranOut = "ran_out"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = c.value(.available, false)
        signedIn = c.value(.signedIn, false)
        username = c.maybe(.username)
        ranOut = c.value(.ranOut, false)
    }
}

public struct ModrinthSignInStarted: Decodable, Equatable, Sendable {
    public var url: String
}

public struct ModrinthMade: Decodable, Equatable, Sendable {
    public var name: String
    public var version: String
}

public struct ModrinthOwnProject: Decodable, Equatable, Sendable {
    public var slug: String
    public var title: String?
    public var url: String
}

public struct ModrinthUpload: Decodable, Equatable, Sendable {
    public var version: String
}

/// An own jar in the newest modpack; `url` once it is on Modrinth.
public struct ModrinthJar: Decodable, Equatable, Sendable, Identifiable {
    public var path: String
    public var name: String
    public var url: String?
    public var id: String { path }
}

public struct ModrinthServerProject: Decodable, Equatable, Sendable {
    public var url: String
}

public struct ModrinthOlder: Decodable, Equatable, Sendable {
    public var listed: String
    public var newest: String
}

/// A free anti-cheat, installed from Modrinth with the normal install. `warning`: Fabric with mods players need.
public struct AntiCheatFree: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var project: String
    public var on: Bool
    public var warning: Bool

    enum CodingKeys: String, CodingKey { case id, name, project, on, warning }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        name = c.value(.name, "")
        project = c.value(.project, "")
        on = c.value(.on, false)
        warning = c.value(.warning, false)
    }
}

/// A paid anti-cheat, bought on the seller's `link`. `price`: nil when none was read; `checked`: the day it was.
public struct AntiCheatPaid: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var on: Bool
    public var link: String
    public var setup: String?
    public var price: String?
    public var checked: String?

    enum CodingKeys: String, CodingKey { case id, name, on, link, setup, price, checked }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        name = c.value(.name, "")
        on = c.value(.on, false)
        link = c.value(.link, "")
        setup = c.maybe(.setup)
        price = c.maybe(.price)
        checked = c.maybe(.checked)
    }
}

/// The anti-cheat picker. `supported`: false when none runs on this server type.
public struct AntiCheat: Decodable, Equatable, Sendable {
    public var supported: Bool
    public var installed: [String]
    public var free: [AntiCheatFree]
    public var paid: [AntiCheatPaid]
    public var mayInstall: Bool
    public var mayAddOwnJar: Bool

    enum CodingKeys: String, CodingKey {
        case supported, installed, free, paid
        case mayInstall = "may_install"
        case mayAddOwnJar = "may_add_own_jar"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        supported = c.value(.supported, false)
        installed = c.value(.installed, [])
        free = c.value(.free, [])
        paid = c.value(.paid, [])
        mayInstall = c.value(.mayInstall, false)
        mayAddOwnJar = c.value(.mayAddOwnJar, false)
    }
}

/// The "Put it on Modrinth" card. `address`: the address typed for friends.
public struct ModrinthShare: Decodable, Equatable, Sendable {
    public var account: ModrinthAccount?
    public var made: ModrinthMade?
    public var project: ModrinthOwnProject?
    public var uploads: [ModrinthUpload]
    public var ownJars: [ModrinthJar]
    public var serverProject: ModrinthServerProject?
    public var older: ModrinthOlder?
    public var address: String?

    enum CodingKeys: String, CodingKey {
        case account, made, project, uploads, older, address
        case ownJars = "own_jars"
        case serverProject = "server_project"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        account = c.maybe(.account)
        made = c.maybe(.made)
        project = c.maybe(.project)
        uploads = c.value(.uploads, [])
        ownJars = c.value(.ownJars, [])
        serverProject = c.maybe(.serverProject)
        older = c.maybe(.older)
        address = c.maybe(.address)
    }
}

/// One far-view choice. `available` false: `why` says what's missing. `server`/`players`: what it adds, by name.
public struct FarViewChoice: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var available: Bool
    public var why: String?
    public var server: [String]
    public var players: [String]

    enum CodingKeys: String, CodingKey { case id, name, available, why, server, players }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.value(.id, "")
        name = c.value(.name, "")
        available = c.value(.available, false)
        why = c.maybe(.why)
        server = c.value(.server, [])
        players = c.value(.players, [])
    }
}

/// "Load the world ahead at night" with Chunky. `running`: loading right now.
public struct FarViewLoadAhead: Decodable, Equatable, Sendable {
    public var on: Bool
    public var radius: Int
    public var world: String
    public var chunky: Bool
    public var running: Bool
    public var night: String

    enum CodingKeys: String, CodingKey { case on, radius, world, chunky, running, night }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        on = c.value(.on, false)
        radius = c.value(.radius, 2000)
        world = c.value(.world, "")
        chunky = c.value(.chunky, false)
        running = c.value(.running, false)
        night = c.value(.night, "")
    }
}

/// The Far view card: Off / Distant Horizons / Voxy. `unvisited`: DH builds land nobody visited.
public struct FarView: Decodable, Equatable, Sendable {
    public var supported: Bool
    public var choice: String
    public var choices: [FarViewChoice]
    public var unvisited: Bool
    public var loadAhead: FarViewLoadAhead?
    public var mayChange: Bool

    enum CodingKeys: String, CodingKey {
        case supported, choice, choices, unvisited
        case loadAhead = "load_ahead"
        case mayChange = "may_change"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        supported = c.value(.supported, false)
        choice = c.value(.choice, "off")
        choices = c.value(.choices, [])
        unvisited = c.value(.unvisited, false)
        loadAhead = c.maybe(.loadAhead)
        mayChange = c.value(.mayChange, false)
    }
}

public struct FarViewChanged: Decodable, Equatable, Sendable {
    public var choice: String
    public var added: [String]

    enum CodingKeys: String, CodingKey { case choice, added }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        choice = c.value(.choice, "off")
        added = c.value(.added, [])
    }
}

public struct FarViewRebuilt: Decodable, Equatable, Sendable {
    public var files: Int
    public var sizeBytes: Int64

    enum CodingKeys: String, CodingKey {
        case files
        case sizeBytes = "size_bytes"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        files = c.value(.files, 0)
        sizeBytes = c.value(.sizeBytes, 0)
    }
}
