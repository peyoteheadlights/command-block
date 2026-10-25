import Foundation

/// The Discord bot's routes the phone uses (Phase 11). Mirrors the Android
/// core's AgentClient; the agent checks each route's permission.
extension AgentClient {
    /// Every alert up to `upTo` read, on every device of this account.
    public func markAlertsRead(upTo: Int64) async throws -> AlertsRead {
        try await call("POST", "/api/alerts/read", body: try AgentClient.encode(["up_to": upTo]), as: AlertsRead.self)
    }

    /// Start (the Start button's own start) or Say no to the server's open start request.
    public func answerStartRequest(serverId: String, start: Bool) async throws -> StartRequestAnswered {
        try await call("POST", room(serverId) + "/start-request", body: try AgentClient.encode(["answer": start ? "start" : "no"]), as: StartRequestAnswered.self)
    }

    /// The bot's state and each Discord server's check (owner only).
    public func discord() async throws -> DiscordStatus {
        try await call("GET", "/api/discord", as: DiscordStatus.self)
    }

    public func checkDiscord() async throws -> DiscordStatus {
        try await call("POST", "/api/discord/check", body: Data("{}".utf8), slow: true, as: DiscordStatus.self)
    }

    public func setRemoveOnLeave(guildId: String, on: Bool) async throws -> DiscordStatus {
        try await call("PUT", "/api/discord/guilds/\(AgentClient.pathSegment(guildId))/options",
                       body: try AgentClient.encode(["remove_on_leave": on]), as: DiscordStatus.self)
    }

    public func discordLinks(serverId: String) async throws -> DiscordLinks {
        try await call("GET", room(serverId) + "/discord-links", as: DiscordLinks.self)
    }

    public func unlink(serverId: String, uuid: String) async throws -> DiscordLinks {
        try await call("DELETE", room(serverId) + "/discord-links/" + AgentClient.pathSegment(uuid), as: DiscordLinks.self)
    }

    /// A Bedrock player's link request: approve as `uuid` (one of the recent players), or refuse.
    public func answerLinkRequest(serverId: String, requestId: Int64, approve: Bool, uuid: String?) async throws -> DiscordLinks {
        let body: [String: JSONValue] = ["answer": .string(approve ? "approve" : "refuse"), "uuid": uuid.map(JSONValue.string) ?? .null]
        return try await call("POST", room(serverId) + "/discord-links/requests/\(requestId)", body: try AgentClient.encode(body), as: DiscordLinks.self)
    }

    public func whitelistHelp(serverId: String) async throws -> WhitelistHelp {
        try await call("GET", room(serverId) + "/whitelist", as: WhitelistHelp.self)
    }

    /// Approve sends the whitelist command: "Sent" until the console says so.
    public func answerWhitelistForm(serverId: String, formId: Int64, approve: Bool) async throws -> WhitelistHelp {
        try await call("POST", room(serverId) + "/whitelist/forms/\(formId)",
                       body: try AgentClient.encode(["answer": approve ? "approve" : "refuse"]), as: WhitelistHelp.self)
    }

    public func answerAddress(serverId: String, addressId: Int64, allow: Bool) async throws -> WhitelistHelp {
        try await call("POST", room(serverId) + "/whitelist/addresses/\(addressId)",
                       body: try AgentClient.encode(["answer": allow ? "allow" : "ignore"]), as: WhitelistHelp.self)
    }

    public func removeAddress(serverId: String, addressId: Int64) async throws -> WhitelistHelp {
        try await call("DELETE", room(serverId) + "/whitelist/addresses/\(addressId)", as: WhitelistHelp.self)
    }

    public func quests(serverId: String) async throws -> QuestsPage {
        try await call("GET", room(serverId) + "/quests", as: QuestsPage.self)
    }

    /// A new quest, its body from `Community.questBody`.
    public func makeQuest(serverId: String, form: QuestForm) async throws -> QuestsPage {
        try await call("POST", room(serverId) + "/quests", body: try AgentClient.encode(Community.questBody(form)), as: QuestsPage.self)
    }

    public func endQuest(serverId: String, questId: Int64) async throws -> QuestsPage {
        try await call("DELETE", room(serverId) + "/quests/\(questId)", as: QuestsPage.self)
    }

    public func answerClaim(serverId: String, claimId: Int64, approve: Bool) async throws -> QuestsPage {
        try await call("POST", room(serverId) + "/quests/claims/\(claimId)",
                       body: try AgentClient.encode(["answer": approve ? "approve" : "refuse"]), as: QuestsPage.self)
    }

    public func claimScreenshot(serverId: String, claimId: Int64) async throws -> QuestScreenshot {
        try await call("GET", room(serverId) + "/quests/claims/\(claimId)/screenshot", as: QuestScreenshot.self)
    }

    public func turnDownIdea(serverId: String, ideaId: Int64) async throws -> QuestsPage {
        try await call("DELETE", room(serverId) + "/quests/suggestions/\(ideaId)", as: QuestsPage.self)
    }

    public func questEvents(serverId: String) async throws -> EventsPage {
        try await call("GET", room(serverId) + "/quests/events", as: EventsPage.self)
    }

    public func callOffEvent(serverId: String, eventId: Int64) async throws -> EventsPage {
        try await call("DELETE", room(serverId) + "/quests/events/\(eventId)", as: EventsPage.self)
    }

    /// The suggested attendance list is right: the rewards go out.
    public func approveAttendance(serverId: String, eventId: Int64) async throws -> EventsPage {
        try await call("POST", room(serverId) + "/quests/events/\(eventId)/approve", body: Data("{}".utf8), as: EventsPage.self)
    }

    public func fixAttendance(serverId: String, eventId: Int64, discordId: String, attended: Bool) async throws -> EventsPage {
        try await call("PUT", room(serverId) + "/quests/events/\(eventId)/attendance/" + AgentClient.pathSegment(discordId),
                       body: try AgentClient.encode(["attended": attended]), as: EventsPage.self)
    }

    public func closePoll(serverId: String, pollId: Int64) async throws -> EventsPage {
        try await call("DELETE", room(serverId) + "/quests/polls/\(pollId)", as: EventsPage.self)
    }

    /// PageAPI.swift's `server(_:)` is private to that file.
    private func room(_ id: String) -> String { "/api/servers/" + AgentClient.pathSegment(id) }
}
