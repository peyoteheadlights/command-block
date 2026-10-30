import XCTest
@testable import Core

/// The Discord bot's screens: start requests, the bot, Linked accounts,
/// whitelist requests, quests, events and polls (mirrors CommunityTest.kt).
final class CommunityTests: XCTestCase {
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

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    private func alert(_ event: String, _ ts: Double, server: String? = "survival") -> AppAlert {
        AppAlert(id: 1, ts: ts, serverId: server, event: event, title: "Survival: start request",
                 body: "Sam asks to start Survival.", page: "overview", url: "/#survival/overview")
    }

    // What GET /quests sends (agent/quests.py), with a role from the bot.
    private func quests() throws -> QuestsPage {
        try decode(QuestsPage.self, """
        {"quests": [{"id": 3, "title": "Diamonds", "details": "", "kind": "stat", "goal": "mined diamond_ore",
          "count": 20, "repeat": "weekly", "ends_at": null, "period": "2026-W43", "joined": 2, "saved_at": 1761300000,
          "rewards": [{"kind": "role", "role_id": "123456789"}, {"kind": "item", "item": "minecraft:golden_apple", "count": 2}],
          "members": [{"discord_id": "11", "name": "Sam", "progress": 20, "finished_at": 1761300000},
                      {"discord_id": "12", "name": null, "progress": 4, "finished_at": null}]}],
        "claims": [], "suggestions": [], "rewards": [], "kinds": ["playtime", "joins", "approval"], "bot": true,
        "guilds": [{"id": "999", "name": "Friends", "roles": [{"id": "123456789", "name": "Miner"}], "levels": {}}]}
        """)
    }

    func testStartAndSayNoShowOnAStartRequestThatCanStillBeOpenForSomeoneWhoMayStart() {
        let now = 10_000.0
        XCTAssertTrue(Community.answersStartRequest(alert("start_request", now - 60), now: now, mayStart: true))
        XCTAssertFalse(Community.answersStartRequest(alert("start_request", now - 60), now: now, mayStart: false))
        // Past 15 minutes the agent has closed it: no dead buttons.
        XCTAssertFalse(Community.answersStartRequest(alert("start_request", now - 901), now: now, mayStart: true))
        XCTAssertFalse(Community.answersStartRequest(alert("server_crashed", now - 60), now: now, mayStart: true))
        XCTAssertFalse(Community.answersStartRequest(alert("start_request", now - 60, server: nil), now: now, mayStart: true))
    }

    func testAQuestSaysWhatToDoHowOftenAndWhatItGivesWithRealRoleNames() throws {
        let page = try quests()
        let quest = try XCTUnwrap(page.quests.first)
        let line = Community.questLine(s, quest, roles: page.roleNames)
        XCTAssertTrue(line.contains("diamond ore"), line)
        XCTAssertTrue(line.contains("Miner"), line)
        XCTAssertTrue(line.contains("golden apple"), line)
        XCTAssertTrue(line.contains(s.t("quests.repeat.weekly")), line)
        // A role the bot hasn't read yet is Unknown, never a guess.
        let unknown = Community.rewardWords(s, quest.rewards[0], roles: [:])
        XCTAssertTrue(unknown.contains(s.t("value.unknown")), unknown)
    }

    func testTheMembersLineNamesWhoFinishedAndTechnicalSaysHowFarTheRestAre() throws {
        let quest = try XCTUnwrap(try quests().quests.first)
        let simple = Community.membersLine(s, quest)
        XCTAssertTrue(simple.contains("Sam"), simple)
        XCTAssertFalse(simple.contains("4/20"), simple)
        let technical = Strings(Shared.strings)
        technical.technical = true
        XCTAssertTrue(Community.membersLine(technical, quest).contains("12 4/20"))
        // Progress from the stats files says when they were saved.
        XCTAssertTrue(simple.contains(s.t("quests.saved", ["time": Display.dateTime(s, 1761300000)])))
    }

    func testANewQuestSendsOnlyWhatItsKindUsesAndOnlyTheRewardsPicked() {
        var form = QuestForm()
        form.title = " Sleep well "
        form.kind = "approval"
        form.goal = "ignored"
        form.count = 9
        form.roleId = "123456789"
        form.xpLevels = 99
        let body = Community.questBody(form)
        XCTAssertEqual(body["title"], .string("Sleep well"))
        XCTAssertEqual(body["goal"], .string(""))
        XCTAssertEqual(body["count"], .number(1))
        guard case .array(let rewards)? = body["rewards"] else { return XCTFail("no rewards") }
        XCTAssertEqual(rewards.count, 2)
        XCTAssertEqual(rewards.first, .object(["kind": .string("role"), "role_id": .string("123456789")]))
        // Kept inside the template's limits; the agent checks again.
        XCTAssertEqual(rewards.last, .object(["kind": .string("xp"), "levels": .number(30)]))

        var stat = QuestForm()
        stat.title = "Dig"
        stat.kind = "stat"
        stat.goal = "mined stone"
        stat.count = 500
        stat.suggestionId = 7
        let dig = Community.questBody(stat)
        XCTAssertEqual(dig["goal"], .string("mined stone"))
        XCTAssertEqual(dig["count"], .number(500))
        XCTAssertEqual(dig["suggestion_id"], .number(7))
        XCTAssertEqual(dig["rewards"], .array([]))
    }

    func testAQuestCanHaveALastDayTypedLikeTheDesktops() {
        var form = QuestForm()
        form.title = "x"
        XCTAssertEqual(Community.questBody(form)["ends"], .null)
        form.lastDay = " 2026-12-31 "
        XCTAssertEqual(Community.questBody(form)["ends"], .string("2026-12-31"))
        XCTAssertTrue(Community.lastDayOk("") && Community.lastDayOk("2026-01-05"))
        XCTAssertFalse(Community.lastDayOk("31/12/2026") || Community.lastDayOk("2026-1-5"))
    }

    func testACommunityEventAndAPollAreSentWithTheirTimesOnThePhonesClock() throws {
        let cairo = try XCTUnwrap(TimeZone(identifier: "Africa/Cairo"))
        let now = 1_761_840_000.0
        var form = Community.eventForm(now: now, zone: cairo)
        XCTAssertEqual(form.starts, Community.showWhen(now + 86_400, zone: cairo))
        form.title = " Dragon night "
        form.goal = "ignored"
        form.startServer = true
        form.xpLevels = 5
        let body = try XCTUnwrap(Community.eventBody(form, zone: cairo))
        XCTAssertEqual(body["title"], .string("Dragon night"))
        XCTAssertEqual(body["goal"], .string(""))
        XCTAssertEqual(body["starts_at"], .number(now + 86_400))
        XCTAssertEqual(body["ends_at"], .number(now + 93_600))
        XCTAssertEqual(body["start_server"], .bool(true))
        XCTAssertEqual(body["rewards"], .array([.object(["kind": .string("xp"), "levels": .number(5)])]))
        var dragon = form
        dragon.kind = "advancement"
        dragon.goal = "end/kill_dragon"
        XCTAssertEqual(Community.eventBody(dragon, zone: cairo)?["goal"], .string("end/kill_dragon"))
        // A time that can't be read sends nothing.
        form.starts = "tomorrow"
        XCTAssertNil(Community.eventBody(form, zone: cairo))

        var poll = Community.pollForm(now: now, zone: cairo)
        poll.question = "Map?"
        poll.answers = [" Desert ", "", "Ocean", "", ""]
        let sent = try XCTUnwrap(Community.pollBody(poll, zone: cairo))
        XCTAssertEqual(sent["answers"], .array([.string("Desert"), .string("Ocean")]))
        XCTAssertEqual(sent["closes_at"], .number(now + 86_400))
        poll.closes = "soon"
        XCTAssertNil(Community.pollBody(poll, zone: cairo))
    }

    // A Discord server as GET /discord sends it (agent/discord/bot.py status).
    private func guild(servers: String = "[\"survival\"]") throws -> DiscordGuild {
        try decode(DiscordGuild.self, """
        {"id": "999", "name": "Friends", "servers": \(servers), "options": {"remove_on_leave": false, "reminders": "private"},
         "levels": {"announce": true, "leaderboard_day": null, "roles": [{"level": 5, "role_id": "50", "name": "Regular"}]},
         "report": {"problems": [], "roles": [{"id": "50", "name": "Regular"}, {"id": "999", "name": "@everyone"}],
                    "channels": [{"id": "70", "name": "general"}],
                    "tied": [{"kind": "levels", "server_id": "", "channel_id": "70", "name": "general"}]}}
        """)
    }

    func testTheChannelPickersListTheSharedChannelsThenEachServersOwn() throws {
        let guild = try self.guild()
        let rows = Community.channelRows(s, guild, servers: [("survival", "Survival"), ("creative", "Creative")])
        XCTAssertEqual(rows.map(\.kind), ["alerts", "quests", "levels", "events", "status", "chat", "alerts", "console"])
        XCTAssertEqual(rows.first { $0.kind == "levels" }?.channelId, "70")
        XCTAssertEqual(rows.first { $0.kind == "chat" }?.label, s.t("discord.kind.chat") + ": Survival")
        XCTAssertFalse(rows.contains { $0.serverId == "creative" }) // not set up for this Discord server
        XCTAssertEqual(Community.noChannel(try XCTUnwrap(rows.last { $0.kind == "alerts" })), "discord.same_alerts")
        let chat = try XCTUnwrap(rows.first { $0.kind == "chat" })
        XCTAssertEqual(Community.channelBody(chat, channelId: nil),
                       ["kind": .string("chat"), "server_id": .string("survival"), "channel_id": .null])
        XCTAssertEqual(guild.reminders, "private")
    }

    func testLevelSettingsSendTheLevelRolesOnlyWhenTheyChanged() throws {
        let guild = try self.guild()
        let same = Community.levelsBody(guild, announce: false, day: 4, rows: [LevelRow(level: 5, roleId: "50")])
        XCTAssertEqual(same, ["announce": .bool(false), "leaderboard_day": .number(4)])
        let changed = Community.levelsBody(guild, announce: true, day: nil, rows: [LevelRow(level: 20, roleId: "50"), LevelRow(level: 30, roleId: nil)])
        XCTAssertEqual(changed["roles"], .array([.object(["level": .number(20), "role_id": .string("50")])]))
        XCTAssertEqual(Community.levelRoleChoices(guild).map(\.id), ["50"]) // never @everyone
        XCTAssertEqual(Community.setupServers(guild, servers: ["survival", "creative"]), ["survival"])
        XCTAssertEqual(Community.setupServers(try self.guild(servers: "[]"), servers: ["solo"]), ["solo"])
    }

    func testAServerThatCantCheckEveryKindSaysSo() throws {
        let hint = Community.goalHint(s, kind: "playtime", kinds: try quests().kinds)
        XCTAssertTrue(hint.hasSuffix(s.t("quests.limited_kinds")))
        XCTAssertFalse(Community.goalHint(s, kind: "playtime", kinds: Community.KINDS).contains(s.t("quests.limited_kinds")))
    }

    func testEventsShowAttendanceOnceItIsCountedAndOnlyComingOnesCanBeCalledOff() throws {
        let page = try decode(EventsPage.self, """
        {"events": [{"id": 1, "title": "Dragon night", "details": "", "kind": "approval", "goal": "",
          "starts_at": 1761300000, "ends_at": 1761307200, "start_server": true, "rewards": [],
          "state": "approving", "answers": {"going": 3, "maybe": 1, "no": 0},
          "going": [], "attendance": [{"discord_id": "11", "discord_name": "Sam", "minutes": 40,
          "attended": true, "how": "suggested", "fixed_by": null}], "fixable": true}],
        "polls": [{"id": 2, "question": "Next map?", "answers": ["Desert", "Snow"], "votes": [2, 5],
          "total": 7, "closes_at": 1761400000, "closed_at": null, "open": true}],
        "kinds": ["online", "approval"], "bot": true}
        """)
        let event = try XCTUnwrap(page.events.first)
        XCTAssertTrue(Community.ATTENDANCE_STATES.contains(event.state))
        XCTAssertFalse(Community.canCallOff(event))
        XCTAssertTrue(Community.answersLine(s, event).contains("3"))
        XCTAssertTrue(Community.eventLine(s, event, roles: [:]).contains(s.t("game_events.starts_server")))
        XCTAssertEqual(Community.pollCounts(try XCTUnwrap(page.polls.first)), "Desert: 2 · Snow: 5")
        let person = try XCTUnwrap(event.attendance.first)
        XCTAssertFalse(Community.personLine(s, person).contains(s.t("game_events.how.suggested", ["by": ""])))
    }

    func testADiscordServersCheckReadsAsPlainProblemsAndAnUncheckedOneSaysNothingYet() throws {
        let status = try decode(DiscordStatus.self, """
        {"token_set": true, "state": "connected", "name": "Command Block", "switches": {"message_content": true, "members": null},
         "guilds": [{"id": "999", "name": "Friends", "servers": ["survival"], "options": {"remove_on_leave": true},
           "report": {"problems": [{"id": "role_too_low", "role": "Legend"}], "checked_at": 1}},
           {"id": "998", "name": "New", "servers": [], "options": {}, "report": null}]}
        """)
        XCTAssertEqual(status.guilds.count, 2)
        let checked = status.guilds[0], unchecked = status.guilds[1]
        XCTAssertTrue(checked.removeOnLeave)
        XCTAssertFalse(unchecked.removeOnLeave)
        let lines = try XCTUnwrap(Community.problems(s, checked))
        XCTAssertEqual(lines.count, 1)
        XCTAssertTrue(lines[0].contains("Legend") && lines[0].contains("Friends"), lines[0])
        XCTAssertNil(Community.problems(s, unchecked))
        XCTAssertEqual(status.switches["message_content"], .some(true))
        XCTAssertEqual(status.switches["members"], .some(nil))
        XCTAssertTrue(Community.botState(s, status).contains("Command Block"))
    }

    func testAnOfflineModeServerNamesTheWhitelistAddOnItUsesOrSuggestsSome() {
        var help = WhitelistHelp(onlineMode: false, suggested: ["EasyWhitelist", "TextWhitelist"])
        XCTAssertTrue(Community.whitelistNote(s, help)?.contains("EasyWhitelist") == true)
        help.addon = "EasyWhitelist"
        XCTAssertTrue(Community.whitelistNote(s, help)?.contains("EasyWhitelist") == true)
        help.onlineMode = true
        XCTAssertNil(Community.whitelistNote(s, help))
        help.onlineMode = nil
        XCTAssertNil(Community.whitelistNote(s, help))
    }

    func testABedrockLinkRequestPicksThePlayerOfTheSameNameFirst() {
        let request = LinkRequest(id: 1, name: "Steve", discordName: "sam")
        let players = [RecentPlayer(name: "Alex", uuid: "a"), RecentPlayer(name: "Steve", uuid: "s")]
        XCTAssertEqual(Community.likelyPlayer(request, players)?.uuid, "s")
        XCTAssertEqual(Community.likelyPlayer(LinkRequest(id: 2, name: "Nobody", discordName: "x"), players)?.uuid, "a")
        XCTAssertNil(Community.likelyPlayer(request, []))
    }

    func testTheScreensCallTheAgentsOwnRoutes() async throws {
        let c = client()
        StubAgent.json("{\"answer\": \"started\"}")
        let started = try await c.answerStartRequest(serverId: "my server", start: true)
        XCTAssertEqual(started.answer, "started")
        XCTAssertEqual(StubAgent.requests[0].request.url?.absoluteString.hasSuffix("/api/servers/my%20server/start-request"), true)
        XCTAssertEqual(body(0), "{\"answer\":\"start\"}")

        StubAgent.json("{\"read_up_to\": 41}")
        let read = try await c.markAlertsRead(upTo: 41)
        XCTAssertEqual(read.readUpTo, 41)
        XCTAssertEqual(body(1), "{\"up_to\":41}")

        StubAgent.json("{\"links\": [], \"requests\": [], \"players\": [], \"reads_chat\": false}")
        _ = try await c.answerLinkRequest(serverId: "survival", requestId: 5, approve: true, uuid: "abc")
        XCTAssertEqual(StubAgent.requests[2].request.url?.path, "/api/servers/survival/discord-links/requests/5")
        XCTAssertEqual(body(2), "{\"answer\":\"approve\",\"uuid\":\"abc\"}")

        StubAgent.json("{\"events\": [], \"polls\": [], \"kinds\": [], \"bot\": false}")
        _ = try await c.fixAttendance(serverId: "survival", eventId: 1, discordId: "11", attended: false)
        XCTAssertEqual(StubAgent.requests[3].request.httpMethod, "PUT")
        XCTAssertEqual(StubAgent.requests[3].request.url?.path, "/api/servers/survival/quests/events/1/attendance/11")

        StubAgent.json("{\"made\": [\"levels\"], \"status\": {\"token_set\": true, \"state\": \"connected\"}}")
        let setup = try await c.setUpDiscord(guildId: "999", servers: ["survival"])
        XCTAssertEqual(setup.made, ["levels"])
        XCTAssertEqual(StubAgent.requests[4].request.url?.path, "/api/discord/guilds/999/setup")
        XCTAssertEqual(body(4), "{\"servers\":[\"survival\"]}")

        StubAgent.json("{\"token_set\": true, \"state\": \"connected\"}")
        _ = try await c.setReminders(guildId: "999", how: "private")
        XCTAssertEqual(StubAgent.requests[5].request.httpMethod, "PUT")
        XCTAssertEqual(body(5), "{\"reminders\":\"private\"}")

        StubAgent.json("{\"token_set\": true, \"state\": \"connecting\"}")
        _ = try await c.setDiscordToken(" a.b.c ")
        XCTAssertEqual(StubAgent.requests[6].request.url?.path, "/api/discord/token")
        XCTAssertEqual(body(6), "{\"token\":\"a.b.c\"}")

        var poll = PollForm()
        poll.question = "Map?"
        poll.answers = ["A", "B"]
        poll.closes = "2026-10-31 20:00"
        StubAgent.json("{\"events\": [], \"polls\": [], \"kinds\": [], \"bot\": true}")
        _ = try await c.makePoll(serverId: "survival", body: try XCTUnwrap(Community.pollBody(poll, zone: try XCTUnwrap(TimeZone(identifier: "UTC")))))
        XCTAssertEqual(StubAgent.requests[7].request.url?.path, "/api/servers/survival/quests/polls")

        // A request that ended (answered elsewhere, or expired) comes back as the agent's words.
        StubAgent.json("{\"detail\": \"There's no start request open for Survival.\"}", status: 404)
        do {
            _ = try await c.answerStartRequest(serverId: "survival", start: false)
            XCTFail("a closed start request was answered")
        } catch AgentError.failed(let status, let reason, _, _) {
            XCTAssertEqual(status, 404)
            XCTAssertEqual(reason, "There's no start request open for Survival.")
        }
    }

    func testEveryWordTheCommunityScreensUseIsInTheTable() {
        var keys: [String] = ["mobile.start_request.started", "mobile.start_request.said_no", "page.quests", "mobile.quests.last_day_hint",
                              "mobile.quests.when_hint", "mobile.discord.hint", "mobile.discord.token_hint"]
        keys += Community.KINDS.map { "quests.kind.\($0)" }
        keys += Community.REPEATS.map { "quests.repeat_pick.\($0)" }
        keys += Community.EVENT_KINDS.map { "game_events.kind.\($0)" }
        keys += Community.REMINDERS.map { "discord.reminders.\($0)" }
        keys += (Community.SHARED_CHANNELS + Community.SERVER_CHANNELS).map { "discord.kind.\($0)" }
        for key in keys { XCTAssertTrue(s.has(key), key) }
    }
}
