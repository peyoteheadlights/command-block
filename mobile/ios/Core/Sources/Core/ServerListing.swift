import Foundation

/// Start all / Stop all's answer: what happened to each server.
public struct AllServersResult: Decodable, Equatable, Sendable {
    public let id: String
    public let name: String
    /// started, already, stopped, stopping or failed.
    public let result: String
    public let detail: String?
}

public struct AllServersAction: Decodable, Equatable, Sendable {
    public let servers: [AllServersResult]
}

/// A command the running server's own help lists, for the console box.
public struct CommandUsage: Decodable, Equatable, Sendable {
    public let name: String
    public let usage: String
}

public struct CommandList: Decodable, Equatable, Sendable {
    public let commands: [CommandUsage]
}

/// One of Minecraft's own log files in the server's logs folder.
public struct LogFile: Decodable, Equatable, Identifiable, Sendable {
    public let name: String
    public let size: Int64
    public let changed: Double
    public var id: String { name }
}

public struct LogFiles: Decodable, Equatable, Sendable {
    public let files: [LogFile]
}

public struct LogFileText: Decodable, Equatable, Sendable {
    public let name: String
    public let lines: [String]
    /// The file goes on after these lines.
    public let cut: Bool
}

/// One entry of a folder in the server's folder (Server files).
public struct ServerFileEntry: Decodable, Equatable, Identifiable, Sendable {
    public let name: String
    public let folder: Bool
    /// Bytes; nil for a folder.
    public let size: Int64?
    public let changed: Double
    public var id: String { name }
}

/// A folder in the server's folder; `path` is relative with "/", "" for the server folder itself.
public struct ServerFiles: Decodable, Equatable, Sendable {
    public let path: String
    public let parent: String?
    public let entries: [ServerFileEntry]
    public let truncated: Bool

    public init(path: String, parent: String? = nil, entries: [ServerFileEntry] = [], truncated: Bool = false) {
        self.path = path
        self.parent = parent
        self.entries = entries
        self.truncated = truncated
    }

    /// The path of `name` inside this folder.
    public func inside(_ name: String) -> String { path.isEmpty ? name : path + "/" + name }
}

public struct ServerFileUploaded: Decodable, Equatable, Sendable {
    public let path: String
    public let name: String
    public let size: Int64
}

/// The graphs' points as a CSV file's text.
public struct PerformanceCsv: Decodable, Equatable, Sendable {
    public let filename: String
    public let text: String
}

/// One game rule as the running server said it.
public struct GameRule: Decodable, Equatable, Identifiable, Sendable {
    public let name: String
    public let value: JSONValue
    public var id: String { name }
}

public struct GameRules: Decodable, Equatable, Sendable {
    /// False: the server is stopped, so no rule was read.
    public let running: Bool
    public let rules: [GameRule]
    public let presets: [String]

    public init(running: Bool, rules: [GameRule], presets: [String]) {
        self.running = running
        self.rules = rules
        self.presets = presets
    }
}

public struct GameRulesSaved: Decodable, Equatable, Sendable {
    public let rules: [GameRule]
    /// What each changed rule was before, sent back for Undo.
    public let before: [String: JSONValue]
    /// Rules this server doesn't have, left out.
    public let skipped: [String]
}

/// All servers' search, groups and order, the same way as the desktop app
/// (mirrors ServerListing.kt).
public enum ServerListing {
    public static func matches(_ row: ServerRow, _ search: String) -> Bool {
        let needle = search.trimmingCharacters(in: .whitespaces).lowercased()
        return needle.isEmpty || "\(row.name) \(row.group ?? "")".lowercased().contains(needle)
    }

    /// Headings and their servers, in list order. One heading "" when nobody made groups.
    public static func grouped(_ rows: [ServerRow], search: String) -> [(name: String, rows: [ServerRow])] {
        let shown = rows.filter { matches($0, search) }
        var names: [String] = []
        for row in shown {
            if let group = row.group, !group.isEmpty, !names.contains(group) { names.append(group) }
        }
        if names.isEmpty { return shown.isEmpty ? [] : [("", shown)] }
        return (names + [""]).map { name in (name, shown.filter { ($0.group ?? "") == name }) }
            .filter { !$0.rows.isEmpty }
    }

    /// The whole order after moving `id` one place up (or down) among `shown`,
    /// or nil when it's already first or last.
    public static func moved(order: [String], shown: [String], id: String, up: Bool) -> [String]? {
        guard let from = shown.firstIndex(of: id) else { return nil }
        let at = from + (up ? -1 : 1)
        guard shown.indices.contains(at) else { return nil }
        var rest = order.filter { $0 != id }
        guard let neighbour = rest.firstIndex(of: shown[at]) else { return nil }
        rest.insert(id, at: neighbour + (up ? 0 : 1))
        return rest
    }

    /// The server's own command names that start with what's typed, while
    /// only the first word is typed.
    public static func completions(_ names: [String], typed: String, most: Int = 6) -> [String] {
        let word = String(typed.drop(while: { $0 == " " }))
        if word.isEmpty || word.contains(" ") { return [] }
        return Array(names.filter {
            $0.lowercased().hasPrefix(word.lowercased()) && $0.lowercased() != word.lowercased()
        }.prefix(most))
    }
}

extension AgentClient {
    /// Start all or Stop all (`action` "start" or "stop") for `ids`, the
    /// servers shown. The agent skips the ones this account may not control.
    public func allServers(action: String, ids: [String]) async throws -> AllServersAction {
        guard ["start", "stop"].contains(action) else { throw NotAnAction(action: action) }
        let body = try AgentClient.encode(["ids": JSONValue.array(ids.map(JSONValue.string))])
        return try await call("POST", "/api/servers/\(action)-all", body: body, timeout: AgentClient.actionTimeout, as: AllServersAction.self)
    }

    /// The order of All servers, first to last (the same on every app).
    public func saveServerOrder(ids: [String]) async throws {
        let body = try AgentClient.encode(["ids": JSONValue.array(ids.map(JSONValue.string))])
        _ = try await call("PUT", "/api/servers/order", body: body, as: Ok.self)
    }

    /// The heading a server is under on All servers; empty takes it out of its group.
    public func saveServerGroup(serverId: String, group: String) async throws {
        let body = try AgentClient.encode(["group": JSONValue.string(group)])
        _ = try await call("PUT", "/api/servers/\(AgentClient.pathSegment(serverId))/group", body: body, as: Ok.self)
    }

    /// Every game rule as the running server says it; none while it's stopped.
    public func gameRules(serverId: String) async throws -> GameRules {
        try await call("GET", "/api/servers/\(AgentClient.pathSegment(serverId))/gamerules", timeout: AgentClient.actionTimeout, as: GameRules.self)
    }

    /// Changes `rules` (name to true/false or a number), or applies `preset`.
    public func saveGameRules(serverId: String, rules: [String: JSONValue], preset: String? = nil) async throws -> GameRulesSaved {
        var fields: [String: JSONValue] = ["rules": .object(rules)]
        if let preset { fields["preset"] = .string(preset) }
        let body = try AgentClient.encode(fields)
        return try await call(
            "PUT", "/api/servers/\(AgentClient.pathSegment(serverId))/gamerules",
            body: body, timeout: AgentClient.actionTimeout, as: GameRulesSaved.self
        )
    }

    /// The commands the running server's help lists; empty while it's stopped.
    public func consoleCommands(serverId: String) async throws -> CommandList {
        try await call("GET", "/api/servers/\(AgentClient.pathSegment(serverId))/console/commands", as: CommandList.self)
    }

    /// Minecraft's own log files (logs/), newest first.
    public func logFiles(serverId: String) async throws -> LogFiles {
        try await call("GET", "/api/servers/\(AgentClient.pathSegment(serverId))/logs/files", as: LogFiles.self)
    }

    /// One of them, read-only.
    public func logFile(serverId: String, name: String) async throws -> LogFileText {
        try await call(
            "GET", "/api/servers/\(AgentClient.pathSegment(serverId))/logs/files/" + AgentClient.pathSegment(name),
            as: LogFileText.self
        )
    }

    /// One folder of the server's folder (owner only).
    public func serverFiles(serverId: String, folder: String) async throws -> ServerFiles {
        try await call(
            "GET", "/api/servers/\(AgentClient.pathSegment(serverId))/files?path=" + AgentClient.pathSegment(folder),
            as: ServerFiles.self
        )
    }

    /// Downloads one file of the server's folder into `destination`, as a backup's zip is.
    public func downloadServerFile(serverId: String, file: String, to destination: URL) async throws -> Int64 {
        try await downloadZip(
            "/api/servers/\(AgentClient.pathSegment(serverId))/files/download?path=" + AgentClient.pathSegment(file),
            to: destination, type: "application/octet-stream", progress: { _, _ in }
        )
    }

    /// Puts `file` into `folder` of the server's folder; never over a file that's there.
    public func uploadServerFile(serverId: String, folder: String, file: PickedFile) async throws -> ServerFileUploaded {
        try await upload(
            "/api/servers/\(AgentClient.pathSegment(serverId))/files/upload?path=" + AgentClient.pathSegment(folder),
            file, as: ServerFileUploaded.self
        )
    }

    /// The graphs' points for `hours` as CSV text, for a spreadsheet.
    public func performanceCsv(serverId: String, hours: Int) async throws -> PerformanceCsv {
        let range = Swift.min(Swift.max(hours, 1), 168)
        return try await call("GET", "/api/servers/\(AgentClient.pathSegment(serverId))/performance/csv?hours=\(range)", as: PerformanceCsv.self)
    }
}
