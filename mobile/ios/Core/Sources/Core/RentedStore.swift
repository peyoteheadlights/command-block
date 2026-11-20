import Foundation

// Rented servers on a host's Pterodactyl panel (agent/rented.py) and a
// server's Store tab (agent/store.py). Mirrors the Android core's Rented.kt
// and Store.kt.

/// Memory, CPU and disk as the host's panel last said; nil when it didn't.
public struct RentedResources: Decodable, Equatable, Sendable {
    public var memoryBytes: Int64?
    public var cpuPercent: Double?
    public var diskBytes: Int64?

    enum CodingKeys: String, CodingKey {
        case memoryBytes = "memory_bytes"
        case cpuPercent = "cpu_percent"
        case diskBytes = "disk_bytes"
    }

    public init(memoryBytes: Int64? = nil, cpuPercent: Double? = nil, diskBytes: Int64? = nil) {
        self.memoryBytes = memoryBytes
        self.cpuPercent = cpuPercent
        self.diskBytes = diskBytes
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        memoryBytes = c.maybe(.memoryBytes)
        cpuPercent = c.maybe(.cpuPercent)
        diskBytes = c.maybe(.diskBytes)
    }
}

/// One rented server on a host's panel.
public struct RentedServer: Decodable, Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var host: String
    public var panelId: String
    /// ONLINE, OFFLINE, STARTING, STOPPING or UNKNOWN (the panel didn't answer).
    public var state: String
    public var stateReason: String?
    public var resources: RentedResources?
    /// off, status or control.
    public var discord: String

    enum CodingKeys: String, CodingKey {
        case id, name, host, state, resources, discord
        case panelId = "panel_id"
        case stateReason = "state_reason"
    }

    public init(id: String, name: String = "", host: String = "", panelId: String = "", state: String = States.UNKNOWN,
                stateReason: String? = nil, resources: RentedResources? = nil, discord: String = "off") {
        self.id = id
        self.name = name
        self.host = host
        self.panelId = panelId
        self.state = state
        self.stateReason = stateReason
        self.resources = resources
        self.discord = discord
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = c.value(.name, "")
        host = c.value(.host, "")
        panelId = c.value(.panelId, "")
        state = c.value(.state, States.UNKNOWN)
        stateReason = c.maybe(.stateReason)
        resources = c.maybe(.resources)
        discord = c.value(.discord, "off")
    }
}

/// A connected panel, for Remove key (the owner's only; never the key).
public struct RentedPanel: Decodable, Equatable, Sendable {
    public var id: String
    public var host: String
}

public struct RentedList: Decodable, Equatable, Sendable {
    public var servers: [RentedServer]
    public var panels: [RentedPanel]

    enum CodingKeys: String, CodingKey { case servers, panels }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        servers = c.value(.servers, [])
        panels = c.value(.panels, [])
    }
}

/// One server the key can see on the panel, before anything is saved.
public struct PanelServer: Decodable, Equatable, Identifiable, Sendable {
    public var identifier: String
    public var name: String
    public var id: String { identifier }
}

public struct PanelCheck: Decodable, Equatable, Sendable {
    public var host: String
    public var servers: [PanelServer]
    /// The main account's key: it can do everything that account can.
    public var fullAccount: Bool

    enum CodingKeys: String, CodingKey {
        case host, servers
        case fullAccount = "full_account"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        host = c.value(.host, "")
        servers = c.value(.servers, [])
        fullAccount = c.value(.fullAccount, false)
    }
}

public struct RentedAdded: Decodable, Equatable, Sendable {
    public var added: [RentedServer]
}

public struct RentedDone: Decodable, Equatable, Sendable {
    public var result: String
    public var detail: String
}

public struct KeyRemoved: Decodable, Equatable, Sendable {
    public var removed: [String]
}

public struct RentedLine: Decodable, Equatable, Identifiable, Sendable {
    public var n: Int
    public var text: String
    public var id: Int { n }
}

public struct RentedConsole: Decodable, Equatable, Sendable {
    public var lines: [RentedLine]
    public var last: Int
    public var problem: String?
}

public struct RentedBackup: Decodable, Equatable, Sendable {
    public var id: String?
    public var name: String?
    public var bytes: Int64?
    public var completedAt: String?
    /// Nil while the host is still making it.
    public var succeeded: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, bytes, succeeded
        case completedAt = "completed_at"
    }

    public init(id: String? = nil, name: String? = nil, bytes: Int64? = nil, completedAt: String? = nil, succeeded: Bool? = nil) {
        self.id = id
        self.name = name
        self.bytes = bytes
        self.completedAt = completedAt
        self.succeeded = succeeded
    }
}

public struct RentedBackups: Decodable, Equatable, Sendable {
    public var backups: [RentedBackup]
    public var limit: Int?
}

/// What the rented server screens show, worked out without SwiftUI.
public enum Rented {
    /// Aternos forbids other apps controlling its servers: only its site opens (shared/links.json).
    public static let ATERNOS = "https://aternos.org/servers/"
    public static let POWER = ["start", "stop", "restart"]
    public static let DISCORD = ["off", "status", "control"]
    /// The agent keeps this many console lines (agent/rented.py).
    public static let CONSOLE_LINES = 500
    /// What needs this PC, greyed out on a rented server (desktop/rented.py).
    public static let NEEDS_THIS_PC = ["backups_here", "wake", "join_protection", "change_type", "addons"]
        .map { "desktop.rented.local.\($0)" }

    /// Connecting a panel and Remove key are the owner's.
    public static func mayConnect(_ me: Me?) -> Bool { me?.isOwner == true }

    /// Start only when it's off; the rest only when it isn't; nothing while Unknown.
    public static func enabled(_ state: String, _ action: String) -> Bool {
        if state == States.UNKNOWN { return false }
        return action == "start" ? state == States.OFFLINE : state != States.OFFLINE
    }

    /// "Unknown: the panel refused the key", never the old state.
    public static func stateLine(_ strings: Strings, _ server: RentedServer) -> String {
        let words = strings.t(Display.state(server.state).key)
        if server.state == States.UNKNOWN, let reason = server.stateReason, !reason.isEmpty {
            return words + ": " + reason
        }
        return words
    }

    public static func usage(_ strings: Strings, _ r: RentedResources?) -> String {
        strings.t("desktop.rented.usage", [
            "memory": Display.bytes(strings, r?.memoryBytes),
            "cpu": r?.cpuPercent.map { "\(Int($0.rounded())) %" } ?? strings.t("value.unknown"),
            "disk": Display.bytes(strings, r?.diskBytes),
        ])
    }

    public static func backupLine(_ strings: Strings, _ b: RentedBackup) -> String {
        let name = b.name ?? ""
        guard let ok = b.succeeded else { return strings.t("desktop.rented.backup_making", ["name": name]) }
        let when = String((b.completedAt ?? "").prefix(16)).replacingOccurrences(of: "T", with: " ")
        let words = strings.t("desktop.rented.backup_row", ["name": name, "size": Display.bytes(strings, b.bytes), "when": when])
        return ok ? words : words + " " + strings.t("desktop.rented.backup_failed")
    }
}

// MARK: - Store

public struct StoreRank: Decodable, Equatable, Sendable {
    public var name: String
    public var made: Bool
}

/// What the last check read from Tebex; never the key.
public struct StoreFacts: Decodable, Equatable, Sendable {
    public var name: String?
    public var link: String?
    public var currency: String?

    public init(name: String? = nil, link: String? = nil, currency: String? = nil) {
        self.name = name
        self.link = link
        self.currency = currency
    }
}

/// One package on Tebex; `rank` when it matches a Store rank.
public struct StorePackage: Decodable, Equatable, Sendable {
    public var name: String
    public var price: Double?
    public var rank: String?

    public init(name: String, price: Double? = nil, rank: String? = nil) {
        self.name = name
        self.price = price
        self.rank = rank
    }
}

/// The Store tab. The answer never holds the key.
public struct StoreView: Decodable, Equatable, Sendable {
    public var offered: Bool
    public var reason: String?
    public var needsType: Bool
    public var connected: Bool
    public var keySaved: Bool
    public var offlineWarning: String?
    public var ranksByHand: Bool
    public var ranks: [StoreRank]
    public var store: StoreFacts
    /// Nil until "Check my store" read them.
    public var packages: [StorePackage]?
    public var buyCommand: String
    public var checklist: String
    public var neverSold: [String]
    public var gameplayWarning: String
    public var fees: String
    public var openTebex: String
    /// After a check: packages the app didn't make, seen for the first time.
    public var remind: [String]

    enum CodingKeys: String, CodingKey {
        case offered, reason, connected, ranks, store, packages, checklist, fees, remind
        case needsType = "needs_type"
        case keySaved = "key_saved"
        case offlineWarning = "offline_warning"
        case ranksByHand = "ranks_by_hand"
        case buyCommand = "buy_command"
        case neverSold = "never_sold"
        case gameplayWarning = "gameplay_warning"
        case openTebex = "open_tebex"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        offered = c.value(.offered, false)
        reason = c.maybe(.reason)
        needsType = c.value(.needsType, false)
        connected = c.value(.connected, false)
        keySaved = c.value(.keySaved, false)
        offlineWarning = c.maybe(.offlineWarning)
        ranksByHand = c.value(.ranksByHand, false)
        ranks = c.value(.ranks, [])
        store = c.value(.store, StoreFacts())
        packages = c.maybe(.packages)
        buyCommand = c.value(.buyCommand, "/store")
        checklist = c.value(.checklist, "")
        neverSold = c.value(.neverSold, [])
        gameplayWarning = c.value(.gameplayWarning, "")
        fees = c.value(.fees, "")
        openTebex = c.value(.openTebex, "")
        remind = c.value(.remind, [])
    }
}

/// The Store tab's rules, as the desktop app has them (desktop/store.py).
public enum Store {
    /// Only the owner, in Technical, on a Java server for a community that isn't a Test server.
    public static func shown(_ row: ServerRow?, me: Me?, technical: Bool) -> Bool {
        guard let row = row, technical, me?.isOwner == true else { return false }
        return row.playedBy == "community" && !row.test && (row.edition ?? "java") == "java"
    }

    /// Whether the key box and buttons work: offered and on a type that takes the plugin.
    public static func usable(_ view: StoreView) -> Bool { view.offered && !view.needsType }

    /// "Supporter  4.99 EUR", with ✓ when the package matches a Store rank.
    public static func packageLine(_ view: StoreView, _ p: StorePackage) -> String {
        let price = p.price.map { n -> String in
            let plain = n == n.rounded() && n.isFinite ? String(Int64(n)) : String(n)
            return (plain + " " + (view.store.currency ?? "")).trimmingCharacters(in: .whitespaces)
        } ?? ""
        let mark = p.rank != nil ? "✓ " : ""
        return (mark + p.name + "  " + price).trimmingCharacters(in: .whitespaces)
    }
}

// MARK: - Routes

extension AgentClient {
    private func rentedPath(_ id: String) -> String { "/api/rented/" + AgentClient.pathSegment(id) }

    private func panelBody(_ address: String, _ key: String, _ servers: [String] = []) throws -> Data {
        // allow_host: the owner ticked "Let Command Block talk to this panel".
        try AgentClient.encode([
            "address": JSONValue.string(address.trimmingCharacters(in: .whitespacesAndNewlines)),
            "key": .string(key.trimmingCharacters(in: .whitespacesAndNewlines)),
            "allow_host": .bool(true),
            "servers": .array(servers.map { .string($0) }),
        ])
    }

    /// Rented servers this account may see; panels only for the owner.
    public func rented() async throws -> RentedList {
        try await call("GET", "/api/rented", as: RentedList.self)
    }

    /// The panel's servers for this key, before anything is saved.
    public func checkPanel(address: String, key: String) async throws -> PanelCheck {
        try await call("POST", "/api/rented/check", body: try panelBody(address, key), slow: true, as: PanelCheck.self)
    }

    public func connectPanel(address: String, key: String, servers: [String]) async throws -> RentedAdded {
        try await call("POST", "/api/rented/panels", body: try panelBody(address, key, servers), slow: true, as: RentedAdded.self)
    }

    /// Remove key: the key is deleted and the panel's servers leave the list.
    public func removePanelKey(_ panelId: String) async throws -> KeyRemoved {
        try await call("DELETE", "/api/rented/panels/" + AgentClient.pathSegment(panelId), as: KeyRemoved.self)
    }

    public func rentedStatus(_ id: String) async throws -> RentedServer {
        try await call("GET", rentedPath(id) + "/status", as: RentedServer.self)
    }

    /// start, stop, restart, or kill with `confirm` (it can damage the world).
    public func rentedPower(_ id: String, signal: String, confirm: Bool = false) async throws -> RentedDone {
        try await call("POST", rentedPath(id) + "/power",
                       body: try AgentClient.encode(["signal": JSONValue.string(signal), "confirm": .bool(confirm)]), as: RentedDone.self)
    }

    public func rentedConsole(_ id: String, after: Int) async throws -> RentedConsole {
        try await call("GET", rentedPath(id) + "/console?after=\(after)", as: RentedConsole.self)
    }

    public func rentedCommand(_ id: String, command: String) async throws -> RentedDone {
        try await call("POST", rentedPath(id) + "/command",
                       body: try AgentClient.encode(["command": command.trimmingCharacters(in: .whitespacesAndNewlines)]), as: RentedDone.self)
    }

    public func rentedBackups(_ id: String) async throws -> RentedBackups {
        try await call("GET", rentedPath(id) + "/backups", as: RentedBackups.self)
    }

    public func makeRentedBackup(_ id: String) async throws -> RentedDone {
        try await call("POST", rentedPath(id) + "/backups", body: Data("{}".utf8), as: RentedDone.self)
    }

    /// off, status or control: what the Discord bot may do with it.
    public func rentedDiscord(_ id: String, choice: String) async throws -> RentedServer {
        try await call("PUT", rentedPath(id) + "/discord", body: try AgentClient.encode(["choice": choice]), as: RentedServer.self)
    }

    public func store(serverId: String) async throws -> StoreView {
        try await call("GET", "/api/servers/\(AgentClient.pathSegment(serverId))/store", as: StoreView.self)
    }

    /// Checks the key with Tebex, then installs the plugin and LuckPerms (backup first, one Undo).
    public func connectStore(serverId: String, key: String) async throws -> StoreView {
        try await call("POST", "/api/servers/\(AgentClient.pathSegment(serverId))/store/connect",
                       body: try AgentClient.encode(["key": key.trimmingCharacters(in: .whitespacesAndNewlines)]), slow: true, as: StoreView.self)
    }

    public func checkStore(serverId: String) async throws -> StoreView {
        try await call("POST", "/api/servers/\(AgentClient.pathSegment(serverId))/store/check", body: Data("{}".utf8), slow: true, as: StoreView.self)
    }
}
