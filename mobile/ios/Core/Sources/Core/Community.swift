import Foundation

// The Discord bot's community features on the phone (Phase 11): start
// requests, the bot's state, Linked accounts, whitelist requests, quests,
// events and polls. Mirrors mobile/android/core's Community.kt, and says
// the same things as the desktop app's discord.py, players.py, quests.py
// and events.py.

public struct StartRequestAnswered: Decodable, Equatable, Sendable {
    /// "started" or "no".
    public var answer: String
}

public struct AlertsRead: Decodable, Equatable, Sendable {
    public var readUpTo: Int64

    enum CodingKeys: String, CodingKey { case readUpTo = "read_up_to" }
}

// MARK: - The bot (owner)

public struct DiscordGuild: Decodable, Equatable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var servers: [String]
    public var options: [String: JSONValue]
    /// What the check found, one entry per problem; nil until checked.
    public var problems: [[String: JSONValue]]?

    enum CodingKeys: String, CodingKey { case id, name, servers, options, report }
    enum ReportKeys: String, CodingKey { case problems }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = c.value(.name, id)
        servers = c.value(.servers, [])
        options = c.value(.options, [:])
        let report = try? c.nestedContainer(keyedBy: ReportKeys.self, forKey: .report)
        problems = report?.maybe(.problems)
    }

    public var removeOnLeave: Bool { options["remove_on_leave"] == .bool(true) }
}

public struct DiscordStatus: Decodable, Equatable, Sendable {
    public var tokenSet: Bool
    /// off, connecting, connected, token_rejected, intents_refused or stopped.
    public var state: String
    public var detail: String
    public var connectedAt: Double?
    public var name: String?
    /// message_content and members: on, off, or nil until read from Discord.
    public var switches: [String: Bool?]
    public var featuresOff: [String]
    public var webhookWithBot: Bool
    public var guilds: [DiscordGuild]

    enum CodingKeys: String, CodingKey {
        case state, detail, name, switches, guilds
        case tokenSet = "token_set"
        case connectedAt = "connected_at"
        case featuresOff = "features_off"
        case webhookWithBot = "webhook_with_bot"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tokenSet = c.value(.tokenSet, false)
        state = c.value(.state, "off")
        detail = c.value(.detail, "")
        connectedAt = c.maybe(.connectedAt)
        name = c.maybe(.name)
        switches = c.value(.switches, [:])
        featuresOff = c.value(.featuresOff, [])
        webhookWithBot = c.value(.webhookWithBot, false)
        guilds = c.each(.guilds)
    }
}

// MARK: - Linked accounts

public struct LinkedAccount: Decodable, Equatable, Sendable, Identifiable {
    public var uuid: String
    public var name: String
    public var discordId: String
    public var discordName: String
    /// The member's Level across all their linked accounts, once the agent says.
    public var level: Int?
    public var xp: Int?

    public var id: String { uuid }

    enum CodingKeys: String, CodingKey {
        case uuid, name, level, xp
        case discordId = "discord_id"
        case discordName = "discord_name"
    }
}

public struct LinkRequest: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var name: String
    public var discordName: String

    enum CodingKeys: String, CodingKey {
        case id, name
        case discordName = "discord_name"
    }

    public init(id: Int64, name: String, discordName: String) {
        self.id = id
        self.name = name
        self.discordName = discordName
    }
}

public struct RecentPlayer: Decodable, Equatable, Sendable, Hashable {
    public var name: String
    public var uuid: String

    public init(name: String, uuid: String) {
        self.name = name
        self.uuid = uuid
    }
}

public struct DiscordLinks: Decodable, Equatable, Sendable {
    public var links: [LinkedAccount]
    /// Bedrock players waiting for approval.
    public var requests: [LinkRequest]
    /// Recent players to pick from.
    public var players: [RecentPlayer]

    enum CodingKeys: String, CodingKey { case links, requests, players }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        links = c.each(.links)
        requests = c.each(.requests)
        players = c.each(.players)
    }
}

// MARK: - Whitelist help

public struct WhitelistForm: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var name: String
    public var why: String
    public var discordName: String

    enum CodingKeys: String, CodingKey {
        case id, name, why
        case discordName = "discord_name"
    }
}

public struct SeenAddress: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var name: String
    /// Partly hidden: the agent never sends the whole address.
    public var masked: String
    /// new, allowed or ignored.
    public var state: String
    public var seenAt: Double

    enum CodingKeys: String, CodingKey {
        case id, name, masked, state
        case seenAt = "seen_at"
    }
}

public struct WhitelistHelp: Decodable, Equatable, Sendable {
    /// nil when server.properties isn't there.
    public var onlineMode: Bool?
    public var addon: String?
    public var suggested: [String]
    public var ipAddon: String?
    public var forms: [WhitelistForm]
    public var addresses: [SeenAddress]
    /// The whitelist command an answer sent ("Sent" until the console says so).
    public var action: PlayerAction?

    enum CodingKeys: String, CodingKey {
        case addon, suggested, forms, addresses, action
        case onlineMode = "online_mode"
        case ipAddon = "ip_addon"
    }

    public init(onlineMode: Bool? = nil, addon: String? = nil, suggested: [String] = []) {
        self.onlineMode = onlineMode
        self.addon = addon
        self.suggested = suggested
        ipAddon = nil
        forms = []
        addresses = []
        action = nil
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        onlineMode = c.maybe(.onlineMode)
        addon = c.maybe(.addon)
        suggested = c.value(.suggested, [])
        ipAddon = c.maybe(.ipAddon)
        forms = c.each(.forms)
        addresses = c.each(.addresses)
        action = c.maybe(.action)
    }
}

// MARK: - Quests

public struct QuestMember: Decodable, Equatable, Sendable {
    public var discordId: String
    public var name: String?
    public var progress: Int
    public var finishedAt: Double?

    enum CodingKeys: String, CodingKey {
        case name, progress
        case discordId = "discord_id"
        case finishedAt = "finished_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        discordId = try c.decode(String.self, forKey: .discordId)
        name = c.maybe(.name)
        progress = c.value(.progress, 0)
        finishedAt = c.maybe(.finishedAt)
    }
}

public struct Quest: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var title: String
    public var details: String
    public var kind: String
    public var goal: String
    public var count: Int
    public var repeatEvery: String
    public var endsAt: Double?
    public var rewards: [[String: JSONValue]]
    public var joined: Int
    /// When the stats files were saved; nil unless the progress comes from them.
    public var savedAt: Double?
    public var members: [QuestMember]

    enum CodingKeys: String, CodingKey {
        case id, title, details, kind, goal, count, rewards, joined, members
        case repeatEvery = "repeat"
        case endsAt = "ends_at"
        case savedAt = "saved_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        title = c.value(.title, "")
        details = c.value(.details, "")
        kind = c.value(.kind, "")
        goal = c.value(.goal, "")
        count = c.value(.count, 1)
        repeatEvery = c.value(.repeatEvery, "once")
        endsAt = c.maybe(.endsAt)
        rewards = c.value(.rewards, [])
        joined = c.value(.joined, 0)
        savedAt = c.maybe(.savedAt)
        members = c.each(.members)
    }
}

public struct QuestClaim: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var title: String
    public var discordName: String
    public var note: String?
    /// nil for none; the picture itself is asked for separately.
    public var screenshot: String?

    enum CodingKeys: String, CodingKey {
        case id, title, note, screenshot
        case discordName = "discord_name"
    }
}

public struct QuestIdea: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var discordName: String
    public var title: String
    public var details: String

    enum CodingKeys: String, CodingKey {
        case id, title, details
        case discordName = "discord_name"
    }
}

public struct RoleName: Decodable, Equatable, Sendable {
    public var id: String
    public var name: String
}

public struct QuestGuild: Decodable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var roles: [RoleName]

    enum CodingKeys: String, CodingKey { case id, name, roles }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = c.value(.name, id)
        roles = c.each(.roles)
    }
}

public struct QuestsPage: Decodable, Equatable, Sendable {
    public var quests: [Quest]
    public var claims: [QuestClaim]
    public var suggestions: [QuestIdea]
    /// The quest kinds this server can check.
    public var kinds: [String]
    /// The Discord bot is connected (needed for Discord roles).
    public var bot: Bool
    public var guilds: [QuestGuild]

    enum CodingKeys: String, CodingKey { case quests, claims, suggestions, kinds, bot, guilds }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        quests = c.each(.quests)
        claims = c.each(.claims)
        suggestions = c.each(.suggestions)
        kinds = c.value(.kinds, [])
        bot = c.value(.bot, false)
        guilds = c.each(.guilds)
    }

    /// Every Discord role's name, by id, from every Discord server set up for this server.
    public var roleNames: [String: String] {
        var names: [String: String] = [:]
        for role in guilds.flatMap(\.roles) { names[role.id] = role.name }
        return names
    }
}

public struct QuestScreenshot: Decodable, Equatable, Sendable {
    public var type: String
    public var image: String
}

// MARK: - Events and polls

public struct EventAnswers: Decodable, Equatable, Sendable {
    public var going: Int
    public var maybe: Int
    public var no: Int

    enum CodingKeys: String, CodingKey { case going, maybe, no }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        going = c.value(.going, 0)
        maybe = c.value(.maybe, 0)
        no = c.value(.no, 0)
    }
}

public struct Attendance: Decodable, Equatable, Sendable, Identifiable {
    public var discordId: String
    public var discordName: String
    public var minutes: Int
    public var attended: Bool
    /// measured, suggested or fixed.
    public var how: String
    public var fixedBy: String?

    public var id: String { discordId }

    enum CodingKeys: String, CodingKey {
        case minutes, attended, how
        case discordId = "discord_id"
        case discordName = "discord_name"
        case fixedBy = "fixed_by"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        discordId = try c.decode(String.self, forKey: .discordId)
        discordName = c.value(.discordName, discordId)
        minutes = c.value(.minutes, 0)
        attended = c.value(.attended, false)
        how = c.value(.how, "measured")
        fixedBy = c.maybe(.fixedBy)
    }
}

public struct GameEvent: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var title: String
    public var details: String
    /// advancement, online or approval.
    public var kind: String
    public var goal: String
    public var startsAt: Double
    public var endsAt: Double
    public var startServer: Bool
    public var rewards: [[String: JSONValue]]
    /// coming, on, counting, approving, ended or cancelled.
    public var state: String
    public var answers: EventAnswers?
    public var attendance: [Attendance]
    /// Attendance can still be fixed (7 days after the end).
    public var fixable: Bool

    enum CodingKeys: String, CodingKey {
        case id, title, details, kind, goal, rewards, state, answers, attendance, fixable
        case startsAt = "starts_at"
        case endsAt = "ends_at"
        case startServer = "start_server"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        title = c.value(.title, "")
        details = c.value(.details, "")
        kind = c.value(.kind, "online")
        goal = c.value(.goal, "")
        startsAt = try c.decode(Double.self, forKey: .startsAt)
        endsAt = try c.decode(Double.self, forKey: .endsAt)
        startServer = c.value(.startServer, false)
        rewards = c.value(.rewards, [])
        state = c.value(.state, "coming")
        answers = c.maybe(.answers)
        attendance = c.each(.attendance)
        fixable = c.value(.fixable, false)
    }
}

public struct Poll: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var question: String
    public var answers: [String]
    public var votes: [Int]
    public var total: Int
    public var closesAt: Double
    public var open: Bool

    enum CodingKeys: String, CodingKey {
        case id, question, answers, votes, total, open
        case closesAt = "closes_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int64.self, forKey: .id)
        question = c.value(.question, "")
        answers = c.value(.answers, [])
        votes = c.value(.votes, [])
        total = c.value(.total, 0)
        closesAt = c.value(.closesAt, 0)
        open = c.value(.open, false)
    }
}

public struct EventsPage: Decodable, Equatable, Sendable {
    public var events: [GameEvent]
    public var polls: [Poll]
    public var kinds: [String]
    public var bot: Bool

    enum CodingKeys: String, CodingKey { case events, polls, kinds, bot }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        events = c.each(.events)
        polls = c.each(.polls)
        kinds = c.value(.kinds, [])
        bot = c.value(.bot, false)
    }
}

/// The new quest form, as typed. The agent checks every value again.
public struct QuestForm: Equatable, Sendable {
    public var title = ""
    public var details = ""
    public var kind = "playtime"
    public var goal = ""
    public var count = 1
    public var repeatEvery = "once"
    public var roleId: String? = nil
    public var item = ""
    public var itemCount = 1
    public var xpLevels = 0
    public var levelXp = 0
    /// The /quest suggest idea it was made from, answered once made.
    public var suggestionId: Int64? = nil

    public init() {}
}

/// What the community screens say and send, the same as the desktop app.
public enum Community {
    /// Seconds a start request waits for an answer (agent/startrequests.py LIFE).
    public static let START_REQUEST_LIFE = 900.0
    public static let START_REQUEST = "start_request"
    /// The category of a start request's lock-screen alert, with Start and Say no.
    public static let START_REQUEST_CATEGORY = "start-request"

    public static let KINDS = ["stat", "advancement", "playtime", "joins", "items", "approval"]
    public static let REPEATS = ["once", "daily", "weekly", "monthly", "yearly"]
    static let NEEDS_GOAL: Set<String> = ["stat", "advancement", "items"]
    static let NO_COUNT: Set<String> = ["advancement", "approval"]
    /// The event states attendance is shown in.
    public static let ATTENDANCE_STATES: Set<String> = ["counting", "approving", "ended"]

    /// Start / Say no on an alert: a start request young enough to be
    /// open still, for someone who may start that server.
    public static func answersStartRequest(_ alert: AppAlert, now: Double, mayStart: Bool) -> Bool {
        mayStart && alert.event == START_REQUEST && alert.serverId != nil && now - alert.ts < START_REQUEST_LIFE
    }

    public static func answered(_ strings: Strings, _ answer: String) -> String {
        strings.t(answer == "started" ? "mobile.start_request.started" : "mobile.start_request.said_no")
    }

    /// "minecraft:diamond_ore" as "diamond ore".
    public static func short(_ name: String) -> String {
        (name.hasPrefix("minecraft:") ? String(name.dropFirst("minecraft:".count)) : name).replacingOccurrences(of: "_", with: " ")
    }

    public static func taskWords(_ strings: Strings, _ quest: Quest) -> String {
        var goal = quest.goal
        if quest.kind == "stat" {
            let parts = goal.split(separator: " ", maxSplits: 1).map(String.init)
            goal = "\(short(parts.first ?? "")): \(short(parts.count > 1 ? parts[1] : ""))"
        }
        return strings.t("quests.task.\(quest.kind)", ["count": quest.count, "goal": short(goal)])
    }

    public static func rewardWords(_ strings: Strings, _ reward: [String: JSONValue], roles: [String: String]) -> String {
        func value(_ key: String) -> String { reward[key]?.text ?? "" }
        return strings.t("quests.reward.\(value("kind"))", [
            "role": roles[value("role_id")] ?? strings.t("value.unknown"),
            "item": short(value("item")),
            "count": value("count"),
            "levels": value("levels"),
            "amount": value("amount"),
        ])
    }

    /// The quest's line under its title: what to do, how often, until when, its rewards.
    public static func questLine(_ strings: Strings, _ quest: Quest, roles: [String: String]) -> String {
        var parts = [taskWords(strings, quest)]
        if quest.repeatEvery != "once" { parts.append(strings.t("quests.repeat.\(quest.repeatEvery)")) }
        if let ends = quest.endsAt { parts.append(strings.t("quests.until", ["time": Display.dateTime(strings, ends - 60)])) }
        let given = quest.rewards.map { rewardWords(strings, $0, roles: roles) }
        parts.append(strings.t("quests.rewards", ["rewards": given.isEmpty ? strings.t("quests.no_reward") : given.joined(separator: ", ")]))
        return parts.joined(separator: " · ")
    }

    /// Who joined and who finished; Technical also says how far the rest are.
    public static func membersLine(_ strings: Strings, _ quest: Quest) -> String {
        let done = quest.members.filter { $0.finishedAt != nil }.map { $0.name ?? $0.discordId }
        var line = strings.t("quests.joined", ["n": quest.joined])
        if !done.isEmpty { line += " · " + strings.t("quests.done_by", ["names": done.joined(separator: ", ")]) }
        if strings.technical {
            let going = quest.members.filter { $0.finishedAt == nil }.map { "\($0.name ?? $0.discordId) \($0.progress)/\(quest.count)" }
            if !going.isEmpty { line += " · " + going.joined(separator: ", ") }
        }
        if let saved = quest.savedAt { line += " · " + strings.t("quests.saved", ["time": Display.dateTime(strings, saved)]) }
        return line
    }

    public static func needsGoal(_ kind: String) -> Bool { NEEDS_GOAL.contains(kind) }

    public static func needsCount(_ kind: String) -> Bool { !NO_COUNT.contains(kind) }

    /// The hint under the kind; a server that can't check every kind says why.
    public static func goalHint(_ strings: Strings, kind: String, kinds: [String]) -> String {
        var hint = strings.t("quests.goal_hint.\(kind)")
        if kinds.count < KINDS.count { hint += " " + strings.t("quests.limited_kinds") }
        return hint
    }

    /// POST /quests: only the fields the kind uses, and only the rewards picked.
    public static func questBody(_ form: QuestForm) -> [String: JSONValue] {
        var rewards: [JSONValue] = []
        if let role = form.roleId {
            rewards.append(.object(["kind": .string("role"), "role_id": .string(role)]))
        }
        let item = form.item.trimmingCharacters(in: .whitespaces)
        if !item.isEmpty {
            rewards.append(.object(["kind": .string("item"), "item": .string(item), "count": .number(Double(clamp(form.itemCount, 1, 64)))]))
        }
        if form.xpLevels > 0 {
            rewards.append(.object(["kind": .string("xp"), "levels": .number(Double(clamp(form.xpLevels, 1, 30)))]))
        }
        if form.levelXp > 0 {
            rewards.append(.object(["kind": .string("level_xp"), "amount": .number(Double(clamp(form.levelXp, 1, 500)))]))
        }
        return [
            "title": .string(form.title.trimmingCharacters(in: .whitespaces)),
            "details": .string(form.details.trimmingCharacters(in: .whitespaces)),
            "kind": .string(form.kind),
            "goal": .string(needsGoal(form.kind) ? form.goal.trimmingCharacters(in: .whitespaces) : ""),
            "count": .number(Double(needsCount(form.kind) ? clamp(form.count, 1, 100_000) : 1)),
            "repeat": .string(form.repeatEvery),
            "ends": .null,
            "rewards": .array(rewards),
            "suggestion_id": form.suggestionId.map { .number(Double($0)) } ?? .null,
        ]
    }

    static func clamp(_ value: Int, _ low: Int, _ high: Int) -> Int { Swift.min(Swift.max(value, low), high) }

    public static func eventLine(_ strings: Strings, _ event: GameEvent, roles: [String: String]) -> String {
        var parts = [
            strings.t("game_events.time", ["start": Display.dateTime(strings, event.startsAt), "end": Display.dateTime(strings, event.endsAt)]),
            strings.t("game_events.counts.\(event.kind)", ["goal": short(event.goal)]),
        ]
        let given = [strings.t("game_events.attend_xp")] + event.rewards.map { rewardWords(strings, $0, roles: roles) }
        parts.append(strings.t("quests.rewards", ["rewards": given.joined(separator: ", ")]))
        if event.startServer { parts.append(strings.t("game_events.starts_server")) }
        return parts.joined(separator: " · ")
    }

    public static func answersLine(_ strings: Strings, _ event: GameEvent) -> String {
        let a = event.answers
        return strings.t("game_events.answers", ["going": a?.going ?? 0, "maybe": a?.maybe ?? 0, "no": a?.no ?? 0])
            + " · " + strings.t("game_events.state.\(event.state)")
    }

    public static func personLine(_ strings: Strings, _ person: Attendance) -> String {
        var words = strings.t("game_events.person", ["name": person.discordName, "minutes": person.minutes])
        if strings.technical { words += " · " + strings.t("game_events.how.\(person.how)", ["by": person.fixedBy ?? ""]) }
        return words
    }

    public static func canCallOff(_ event: GameEvent) -> Bool { event.state == "coming" || event.state == "on" }

    public static func pollCounts(_ poll: Poll) -> String {
        zip(poll.answers, poll.votes).map { "\($0.0): \($0.1)" }.joined(separator: " · ")
    }

    public static func pollState(_ strings: Strings, _ poll: Poll) -> String {
        poll.open
            ? strings.t("game_events.poll_open", ["n": poll.total, "time": Display.dateTime(strings, poll.closesAt)])
            : strings.t("game_events.poll_closed", ["n": poll.total])
    }

    /// The bot's state line.
    public static func botState(_ strings: Strings, _ status: DiscordStatus) -> String {
        strings.t("discord.state.\(status.state)", ["name": status.name ?? ""])
    }

    /// What a Discord server's check found, one line each; nil until checked.
    public static func problems(_ strings: Strings, _ guild: DiscordGuild) -> [String]? {
        guild.problems?.map { problem in
            var values: [String: CustomStringConvertible] = [:]
            for (key, value) in problem { values[key] = value.text ?? "" }
            values["guild"] = guild.name
            values["kind"] = strings.t("discord.kind.\(problem["kind"]?.text ?? "alerts")")
            return strings.t("discord.problem.\(problem["id"]?.text ?? "")", values)
        }
    }

    /// What an offline-mode server uses for its whitelist, or nil to say nothing.
    public static func whitelistNote(_ strings: Strings, _ help: WhitelistHelp) -> String? {
        guard help.onlineMode == false, !help.suggested.isEmpty else { return nil }
        if let addon = help.addon { return strings.t("whitelist.addon_used", ["addon": addon]) }
        return strings.t("whitelist.no_addon", ["addons": help.suggested.joined(separator: ", ")])
    }

    public static func addressLine(_ strings: Strings, _ address: SeenAddress) -> String {
        strings.t("whitelist.address_\(address.state)", [
            "name": address.name, "masked": address.masked, "time": Display.dateTime(strings, address.seenAt),
        ])
    }

    public static func linkLine(_ strings: Strings, _ link: LinkedAccount) -> String {
        var words = strings.t("discord.links.row", ["name": link.name, "member": link.discordName])
        if let level = link.level { words += " · " + strings.t("discord.links.level", ["level": level, "xp": link.xp ?? 0]) }
        if strings.technical { words += " (\(link.uuid))" }
        return words
    }

    /// The recent player a Bedrock link request most likely means: the same name, else the first.
    public static func likelyPlayer(_ request: LinkRequest, _ players: [RecentPlayer]) -> RecentPlayer? {
        players.first { $0.name == request.name } ?? players.first
    }
}
