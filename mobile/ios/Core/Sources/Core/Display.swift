import Foundation

/// How a state reads: the same words and tones in every app.
public enum Tone: Equatable, Sendable {
    case success
    case warning
    case danger
    case neutral
}

public struct StateWords: Equatable, Sendable {
    public let key: String
    public let tone: Tone
    public let busy: Bool

    public init(key: String, tone: Tone, busy: Bool = false) {
        self.key = key
        self.tone = tone
        self.busy = busy
    }
}

/// What the server screen offers, and which need a second tap.
public struct QuickAction: Equatable, Hashable, Sendable {
    public let action: String
    public let labelKey: String
    public let confirm: Bool
    public let danger: Bool

    public init(action: String, labelKey: String, confirm: Bool, danger: Bool = false) {
        self.action = action
        self.labelKey = labelKey
        self.confirm = confirm
        self.danger = danger
    }
}

/// Turning what the agent said into words, with the shared rules:
/// a value the agent didn't measure reads "Unknown", never 0 or a guess, and
/// nothing is shown as current when it came from an earlier read.
public enum Display {
    /// Keyed by the agent's label (agent/minecraft/state.py `controls`).
    private static let STATES: [String: StateWords] = [
        "online": StateWords(key: "state.online", tone: .success),
        "not_answering": StateWords(key: "state.not_answering", tone: .danger),
        "offline": StateWords(key: "state.offline", tone: .neutral),
        "starting": StateWords(key: "state.starting", tone: .warning, busy: true),
        "stopping": StateWords(key: "state.stopping", tone: .warning, busy: true),
        "restarting": StateWords(key: "state.restarting", tone: .warning, busy: true),
        "crashed": StateWords(key: "state.crashed", tone: .danger),
        "unknown": StateWords(key: "state.unknown", tone: .neutral),
    ]

    /// Rented servers send the panel's state (ONLINE), not a label: same words.
    public static func state(_ label: String?) -> StateWords {
        if let label = label, let words = STATES[label.lowercased()] {
            return words
        }
        return StateWords(key: "state.unknown", tone: .neutral)
    }

    /// "3d 4h", "2h 10m", "5m", "40s": duration in desktop/ui.py.
    public static func duration(_ strings: Strings, _ seconds: Double) -> String {
        let whole = seconds.isFinite ? seconds.rounded(.down) : 0
        let s = Swift.max(Int64(0), Int64(Swift.max(0, Swift.min(whole, 9.0e15))))
        let d = s / 86400
        let h = (s % 86400) / 3600
        let m = (s % 3600) / 60
        if d > 0 { return strings.t("time.days_hours", ["d": d, "h": h]) }
        if h > 0 { return strings.t("time.hours_minutes", ["h": h, "m": m]) }
        if m > 0 { return strings.t("time.minutes", ["m": m]) }
        return strings.t("time.seconds", ["s": s])
    }

    /// "5m ago", or "Never" when there is no time.
    public static func ago(_ strings: Strings, _ ts: Double?, now: Double) -> String {
        guard let ts = ts, ts > 0 else { return strings.t("time.never") }
        return strings.t("time.ago", ["duration": duration(strings, now - ts)])
    }

    /// "3 of 20", "3", or Unknown when the player list wasn't read.
    public static func players(_ strings: Strings, _ online: Int?, _ max: Int?) -> String {
        guard let online = online else { return strings.t("value.unknown") }
        if let max = max {
            return strings.t("servers.players_of", ["n": online, "max": max])
        }
        return String(online)
    }

    /// "Up for 2h 10m" while online; nothing for other states.
    public static func uptime(_ strings: Strings, _ state: String?, _ uptime: Double?) -> String? {
        guard state == States.ONLINE, let uptime = uptime else { return nil }
        return strings.t("hero.up_for", ["duration": duration(strings, uptime)])
    }

    /// The note about changes waiting for a restart, and the one button it
    /// offers: "Restart when nobody's on", or Cancel once that is set. Nil
    /// when nothing waits. No button when the account can't control the server.
    public static func restartWaiting(
        _ strings: Strings, _ status: ServerStatus?, canControl: Bool
    ) -> (words: String, action: QuickAction?)? {
        guard let status = status else { return nil }
        if status.restartWhenEmpty {
            let cancel = QuickAction(action: "cancel-restart-when-empty", labelKey: "action.cancel", confirm: false)
            return (strings.t("restart.when_empty_set"), canControl ? cancel : nil)
        }
        if status.restartWaiting.isEmpty { return nil }
        let what = status.restartWaiting.map { strings.t("restart.what.\($0)") }.joined(separator: ", ")
        let later = QuickAction(action: "restart-when-empty", labelKey: "action.restart_when_empty", confirm: false)
        return (strings.t("restart.waiting", ["what": what]), canControl ? later : nil)
    }

    private static let ACTIONS = [
        QuickAction(action: "restart", labelKey: "action.restart", confirm: true),
        QuickAction(action: "stop", labelKey: "action.stop", confirm: true, danger: true),
        // A server left running by a crashed agent: no console to save with.
        QuickAction(action: "force_stop", labelKey: "action.force_stop", confirm: true, danger: true),
        QuickAction(action: "start", labelKey: "action.start", confirm: false),
    ]

    /// The buttons the server page shows: the ones the agent says it takes
    /// now (`can`), never worked out here. Starting asks nothing; stopping
    /// and restarting ask first, because players are thrown off. Nothing is
    /// offered to an account that may not control the server.
    public static func actions(_ can: [String], canControl: Bool) -> [QuickAction] {
        canControl ? ACTIONS.filter { can.contains($0.action) } : []
    }
}

// MARK: - One server's screens (mirrors Display.kt)

extension Display {
    /// "Online now (3 of 20)"; without "of" when the maximum isn't known,
    /// and "Players not known" until the agent has established the list
    /// (never the length of a list that may be old).
    public static func onlineNow(_ strings: Strings, _ count: Int?, _ max: Int?) -> String {
        guard let count = count else { return strings.t("overview.players_unknown") }
        guard let max = max else { return strings.t("mobile.players.online_count", ["count": count]) }
        return strings.t("players.online", ["count": count, "max": max])
    }

    /// How long someone has been playing; Unknown when the join wasn't
    /// seen (the agent then says 0, which wasn't measured).
    public static func playingFor(_ strings: Strings, _ player: OnlinePlayer) -> String {
        guard player.sessionStarted != nil, let seconds = player.sessionSeconds else { return strings.t("value.unknown") }
        return duration(strings, seconds)
    }

    /// A crash's exit code, or Unknown (never "nil").
    public static func exitCode(_ strings: Strings, _ code: Int?) -> String {
        code.map { String($0) } ?? strings.t("value.unknown")
    }

    /// The likely cause in words; the analyzer's own name in Technical
    /// words or when the app has no words for it (causeText in crashes.js).
    public static func cause(_ strings: Strings, _ category: String?) -> String {
        guard let category = category, !category.isEmpty, category != "Unknown" else { return strings.t("crashes.cause_unknown") }
        let key = "cause.\(category)"
        if strings.technical || !strings.has(key) { return category }
        let text = strings.t(key)
        return text.prefix(1).uppercased() + text.dropFirst()
    }

    private static let CONFIDENCE: [String: StateWords] = [
        "confirmed": StateWords(key: "crashes.conf_confirmed", tone: .danger),
        "likely": StateWords(key: "crashes.conf_likely", tone: .warning),
        "possible": StateWords(key: "crashes.conf_possible", tone: .neutral),
    ]

    public static func confidence(_ name: String?) -> StateWords {
        name.flatMap { CONFIDENCE[$0] } ?? StateWords(key: "crashes.conf_unknown", tone: .neutral)
    }

    private static let ACTION_STATES: [String: StateWords] = [
        PlayerActions.SENT: StateWords(key: "players.state_sent", tone: .warning, busy: true),
        PlayerActions.DONE: StateWords(key: "players.state_done", tone: .success),
        PlayerActions.UNCHANGED: StateWords(key: "players.state_unchanged", tone: .neutral),
        PlayerActions.FAILED: StateWords(key: "players.state_failed", tone: .danger),
        PlayerActions.NO_ANSWER: StateWords(key: "players.state_no_answer", tone: .warning),
    ]

    /// What became of a player button. "Sent, not confirmed" is said as
    /// such; an unknown state reads Unknown, never "Done".
    public static func actionState(_ state: String?) -> StateWords {
        state.flatMap { ACTION_STATES[$0] } ?? StateWords(key: "value.unknown", tone: .neutral)
    }

    public static func eventTone(_ level: String?) -> Tone {
        switch level {
        case "error": return .danger
        case "warn": return .warning
        case "success": return .success
        default: return .neutral
        }
    }

    /// Events Simple words leave out.
    private static let INTERNAL: Set<String> = [
        "state", "console", "metrics", "job", "chat", "plain", "text",
        "server_start_requested", "server_stop_requested",
        "backup_started", "restore_stopping", "backup_retention",
        "tps_detection_started", "tps_command_detected", "tps_detection_failed",
        "server_changed", "data_folder_moved", "data_folder_not_moved",
    ]

    public static func worthShowing(_ type: String, technical: Bool) -> Bool {
        technical || !INTERNAL.contains(type)
    }

    /// 24-hour "14:05:09" in the phone's time zone (fmt.clock).
    public static func clock(_ ts: Double, zone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let parts = calendar.dateComponents([.hour, .minute, .second], from: Date(timeIntervalSince1970: ts))
        return String(format: "%02d:%02d:%02d", parts.hour ?? 0, parts.minute ?? 0, parts.second ?? 0)
    }

    /// "2026-10-10 14:05:09" in the phone's time zone.
    public static func dateTime(_ ts: Double, zone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let parts = calendar.dateComponents([.year, .month, .day], from: Date(timeIntervalSince1970: ts))
        let day = String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
        return day + " " + clock(ts, zone: zone)
    }
}
