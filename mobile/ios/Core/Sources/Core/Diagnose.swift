import Foundation

/// One step of a Diagnose run: `ok` true worked, false failed, nil not
/// checked (`detail` says why).
public struct DiagnoseLine: Decodable, Equatable, Sendable {
    public var group: String
    public var what: String
    public var ok: Bool?
    public var detail: String?

    enum CodingKeys: String, CodingKey { case group, what, ok, detail }

    public init(group: String, what: String, ok: Bool?, detail: String? = nil) {
        self.group = group
        self.what = what
        self.ok = ok
        self.detail = detail
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        group = c.value(.group, "")
        what = c.value(.what, "")
        ok = c.maybe(.ok)
        detail = c.maybe(.detail)
    }
}

/// A change Diagnose offers; a risky one (`safe` false) only by Apply all.
public struct DiagnoseChange: Decodable, Equatable, Sendable {
    public var key: String
    public var title: String
    public var why: String
    public var safe: Bool

    enum CodingKeys: String, CodingKey { case key, title, why, safe }

    public init(key: String, title: String, why: String = "", safe: Bool) {
        self.key = key
        self.title = title
        self.why = why
        self.safe = safe
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = c.value(.key, "")
        title = c.value(.title, "")
        why = c.value(.why, "")
        safe = c.value(.safe, false)
    }
}

/// What happened to a change: kept, put_back, no_help, failed or denied.
public struct DiagnoseDone: Decodable, Equatable, Sendable {
    public var title: String
    public var outcome: String
    public var detail: String

    enum CodingKeys: String, CodingKey { case title, outcome, detail }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = c.value(.title, "")
        outcome = c.value(.outcome, "")
        detail = c.value(.detail, "")
    }
}

/// A known problem the run recognised, and the title of the change offered for it.
public struct DiagnoseKnown: Decodable, Equatable, Sendable {
    public var say: String
    public var link: String?
    public var change: String?

    enum CodingKeys: String, CodingKey { case say, link, change }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        say = c.value(.say, "")
        link = c.maybe(.link)
        change = c.maybe(.change)
    }
}

/// GET /server/diagnose: the last run, or the one running (its lines so far).
public struct Diagnosis: Decodable, Equatable, Sendable {
    public var running: Bool
    public var lines: [DiagnoseLine]
    public var signIn: Bool
    public var changes: [DiagnoseChange]
    public var done: [DiagnoseDone]
    public var steps: [String]
    public var signInMods: Bool
    public var known: [DiagnoseKnown]

    enum CodingKeys: String, CodingKey {
        case running, lines, changes, done, steps, known
        case signIn = "sign_in"
        case signInMods = "sign_in_mods"
    }

    public init(running: Bool = false, lines: [DiagnoseLine] = [], changes: [DiagnoseChange] = []) {
        self.running = running
        self.lines = lines
        self.changes = changes
        signIn = false
        done = []
        steps = []
        signInMods = false
        known = []
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        running = c.value(.running, false)
        lines = c.value(.lines, [])
        signIn = c.value(.signIn, false)
        changes = c.value(.changes, [])
        done = c.value(.done, [])
        steps = c.value(.steps, [])
        signInMods = c.value(.signInMods, false)
        known = c.value(.known, [])
    }
}

/// POST /server/diagnose: started, or a question to ask first.
public struct DiagnoseStarted: Decodable, Equatable, Sendable {
    public var jobId: String?
    public var ask: String?
    public var askMods: String?

    enum CodingKeys: String, CodingKey {
        case ask
        case jobId = "job_id"
        case askMods = "ask_mods"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobId = c.maybe(.jobId)
        ask = c.maybe(.ask)
        askMods = c.maybe(.askMods)
    }
}

/// What the Diagnose server screen decides, the same as the desktop window
/// (desktop/diagnose.py) and Diagnose.kt: the agent does the work and keeps the list.
public enum Diagnose {
    /// Apply all, Apply safe and Deny all, as the agent names them.
    public static let picks = ["all", "safe", "none"]

    public static func mark(_ ok: Bool?) -> String {
        switch ok {
        case .some(true): return "✓"
        case .some(false): return "✗"
        case .none: return "?"
        }
    }

    public static func doneMark(_ done: DiagnoseDone) -> String { done.outcome == "kept" ? "✓" : "–" }

    /// The lines under their headings, a new heading each time the group changes.
    public static func groups(_ lines: [DiagnoseLine]) -> [(group: String, lines: [DiagnoseLine])] {
        var out: [(group: String, lines: [DiagnoseLine])] = []
        for line in lines {
            if out.last?.group != line.group {
                out.append((group: line.group, lines: []))
            }
            out[out.count - 1].lines.append(line)
        }
        return out
    }

    /// The changes waiting for a pick: none while it still runs.
    public static func waiting(_ result: Diagnosis) -> [DiagnoseChange] { result.running ? [] : result.changes }

    public static func statusKey(_ result: Diagnosis?) -> String {
        guard let result = result else { return "diagnose.starting" }
        if result.running { return "diagnose.running" }
        return waiting(result).isEmpty ? "diagnose.done" : "diagnose.offer"
    }
}

extension AgentClient {
    private struct DiagnoseForm: Encodable {
        let goAhead: Bool
        let mods: Bool?
        enum CodingKeys: String, CodingKey {
            case mods
            case goAhead = "go_ahead"
        }
    }

    /// The last Diagnose server run, or the one running; nil before the first.
    public func diagnosis(serverId: String) async throws -> Diagnosis? {
        try await call("GET", diagnosePath(serverId), as: Diagnosis?.self)
    }

    public func startDiagnose(serverId: String, goAhead: Bool, mods: Bool?) async throws -> DiagnoseStarted {
        let body = try AgentClient.encode(DiagnoseForm(goAhead: goAhead, mods: mods))
        return try await call("POST", diagnosePath(serverId), body: body, as: DiagnoseStarted.self)
    }

    /// Apply all ("all"), Apply safe ("safe") or Deny all ("none").
    public func diagnoseChanges(serverId: String, pick: String) async throws -> DiagnoseStarted {
        precondition(Diagnose.picks.contains(pick), "unknown pick \(pick)")
        let body = try AgentClient.encode(["pick": pick])
        return try await call("POST", diagnosePath(serverId) + "/changes", body: body, as: DiagnoseStarted.self)
    }

    private func diagnosePath(_ id: String) -> String {
        "/api/servers/" + AgentClient.pathSegment(id) + "/server/diagnose"
    }
}
