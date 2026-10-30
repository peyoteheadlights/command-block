package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId

/** The Discord bot's screens: start requests, the bot, Linked accounts,
 * whitelist requests, quests, events and polls (agent/api/routes/). */
class CommunityTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val strings = Strings(Shared.strings)
    private val token = "secret-session-token-1234567890"

    private fun client(agent: TestAgent) =
        AgentClient(agent.pairing(), { token }, {}, AgentLog { }, systemTrust = TestAgent.trustingNothing())

    private fun alert(event: String, ts: Double, server: String? = "survival") =
        AppAlert(1, ts, server, event, "Survival: start request", "Sam asks to start Survival.", "overview", "/#survival/overview")

    // What GET /quests sends (agent/quests.py), with a role from the bot.
    private val quests = json.decodeFromString(
        QuestsPage.serializer(),
        """{"quests": [{"id": 3, "title": "Diamonds", "details": "", "kind": "stat", "goal": "mined diamond_ore",
              "count": 20, "repeat": "weekly", "ends_at": null, "period": "2026-W43", "joined": 2, "saved_at": 1761300000,
              "rewards": [{"kind": "role", "role_id": "123456789"}, {"kind": "item", "item": "minecraft:golden_apple", "count": 2}],
              "members": [{"discord_id": "11", "name": "Sam", "progress": 20, "finished_at": 1761300000},
                          {"discord_id": "12", "name": null, "progress": 4, "finished_at": null}]}],
            "claims": [], "suggestions": [], "rewards": [], "kinds": ["playtime", "joins", "approval"], "bot": true,
            "guilds": [{"id": "999", "name": "Friends", "roles": [{"id": "123456789", "name": "Miner"}], "levels": {}}]}""",
    )

    @Test
    fun `Start and Say no show on a start request that can still be open, for someone who may start`() {
        val now = 10_000.0
        assertTrue(Community.answersStartRequest(alert("start_request", now - 60), now, mayStart = true))
        assertFalse(Community.answersStartRequest(alert("start_request", now - 60), now, mayStart = false))
        // Past 15 minutes the agent has closed it: no dead buttons.
        assertFalse(Community.answersStartRequest(alert("start_request", now - 901), now, mayStart = true))
        assertFalse(Community.answersStartRequest(alert("server_crashed", now - 60), now, mayStart = true))
        assertFalse(Community.answersStartRequest(alert("start_request", now - 60, server = null), now, mayStart = true))
    }

    @Test
    fun `a quest says what to do, how often and what it gives, with real role names`() {
        val quest = quests.quests.single()
        val line = Community.questLine(strings, quest, quests.roleNames)
        assertTrue(line, line.contains("diamond ore"))
        assertTrue(line, line.contains("Miner"))
        assertTrue(line, line.contains("golden apple"))
        assertTrue(line, line.contains(strings.t("quests.repeat.weekly")))
        // A role the bot hasn't read yet is Unknown, never a guess.
        val unknown = Community.rewardWords(strings, quest.rewards.first(), emptyMap())
        assertTrue(unknown, unknown.contains(strings.t("value.unknown")))
    }

    @Test
    fun `the members line names who finished, and Technical says how far the rest are`() {
        val quest = quests.quests.single()
        val simple = Community.membersLine(strings, quest)
        assertTrue(simple, simple.contains("Sam"))
        assertFalse(simple, simple.contains("4/20"))
        val technical = Strings(Shared.strings).apply { this.technical = true }
        assertTrue(Community.membersLine(technical, quest).contains("12 4/20"))
        // Progress from the stats files says when they were saved.
        assertTrue(simple.contains(strings.t("quests.saved", "time" to Display.dateTime(strings, 1761300000.0))))
    }

    @Test
    fun `a new quest sends only what its kind uses and only the rewards picked`() {
        val body = Community.questBody(
            QuestForm(title = " Sleep well ", kind = "approval", goal = "ignored", count = 9, roleId = "123456789", xpLevels = 99),
        )
        assertEquals("Sleep well", body["title"]!!.jsonPrimitive.content)
        assertEquals("", body["goal"]!!.jsonPrimitive.content)
        assertEquals(1, body["count"]!!.jsonPrimitive.content.toInt())
        val rewards = body["rewards"]!!.jsonArray.map { it.jsonObject }
        assertEquals(listOf("role", "xp"), rewards.map { it["kind"]!!.jsonPrimitive.content })
        // Kept inside the template's limits; the agent checks again.
        assertEquals("30", rewards[1]["levels"]!!.jsonPrimitive.content)
        val stat = Community.questBody(QuestForm(title = "Dig", kind = "stat", goal = "mined stone", count = 500, suggestionId = 7))
        assertEquals("mined stone", stat["goal"]!!.jsonPrimitive.content)
        assertEquals("500", stat["count"]!!.jsonPrimitive.content)
        assertEquals("7", stat["suggestion_id"]!!.jsonPrimitive.content)
        assertTrue(stat["rewards"]!!.jsonArray.isEmpty())
    }

    @Test
    fun `a quest can have a last day, typed like the desktop's`() {
        assertEquals("2026-12-31", Community.questBody(QuestForm(title = "x", lastDay = " 2026-12-31 "))["ends"]!!.jsonPrimitive.content)
        assertEquals(JsonNull, Community.questBody(QuestForm(title = "x"))["ends"])
        assertTrue(Community.lastDayOk("") && Community.lastDayOk("2026-01-05"))
        assertFalse(Community.lastDayOk("31/12/2026") || Community.lastDayOk("2026-1-5"))
    }

    @Test
    fun `a Community event and a poll are sent with their times on the phone's clock`() {
        val cairo = ZoneId.of("Africa/Cairo")
        val now = 1_761_840_000.0
        val form = Community.eventForm(now, cairo)
        assertEquals(Community.showWhen(now + 86_400, cairo), form.starts)
        val body = Community.eventBody(
            form.copy(title = " Dragon night ", kind = "online", goal = "ignored", startServer = true, xpLevels = 5),
            cairo,
        )!!
        assertEquals("Dragon night", body["title"]!!.jsonPrimitive.content)
        assertEquals("", body["goal"]!!.jsonPrimitive.content)
        assertEquals(now + 86_400, body["starts_at"]!!.jsonPrimitive.content.toDouble(), 0.0)
        assertEquals(now + 93_600, body["ends_at"]!!.jsonPrimitive.content.toDouble(), 0.0)
        assertEquals("true", body["start_server"]!!.jsonPrimitive.content)
        assertEquals("xp", body["rewards"]!!.jsonArray[0].jsonObject["kind"]!!.jsonPrimitive.content)
        assertEquals("end/kill_dragon", Community.eventBody(form.copy(kind = "advancement", goal = "end/kill_dragon"), cairo)!!["goal"]!!.jsonPrimitive.content)
        // A time that can't be read sends nothing.
        assertNull(Community.eventBody(form.copy(starts = "tomorrow"), cairo))
        val poll = Community.pollBody(
            Community.pollForm(now, cairo).copy(question = "Map?", answers = listOf(" Desert ", "", "Ocean", "", "")),
            cairo,
        )!!
        assertEquals(listOf("Desert", "Ocean"), poll["answers"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(now + 86_400, poll["closes_at"]!!.jsonPrimitive.content.toDouble(), 0.0)
        assertNull(Community.pollBody(PollForm(question = "Map?", closes = "soon"), cairo))
    }

    // A Discord server as GET /discord sends it (agent/discord/bot.py status).
    private val guild = json.decodeFromString(
        DiscordGuild.serializer(),
        """{"id": "999", "name": "Friends", "servers": ["survival"], "options": {"remove_on_leave": false, "reminders": "private"},
            "levels": {"announce": true, "leaderboard_day": null, "roles": [{"level": 5, "role_id": "50", "name": "Regular"}]},
            "report": {"problems": [], "roles": [{"id": "50", "name": "Regular"}, {"id": "999", "name": "@everyone"}],
                       "channels": [{"id": "70", "name": "general"}],
                       "tied": [{"kind": "levels", "server_id": "", "channel_id": "70", "name": "general"}]}}""",
    )

    @Test
    fun `the channel pickers list the shared channels, then each server's own`() {
        val rows = Community.channelRows(strings, guild, listOf("survival" to "Survival", "creative" to "Creative"))
        assertEquals(listOf("alerts", "quests", "levels", "events", "status", "chat", "alerts", "console"), rows.map { it.kind })
        assertEquals("70", rows.first { it.kind == "levels" }.channelId)
        assertEquals("${strings.t("discord.kind.chat")}: Survival", rows.first { it.kind == "chat" }.label)
        assertTrue(rows.none { it.serverId == "creative" }) // not set up for this Discord server
        assertEquals("discord.same_alerts", Community.noChannel(rows.last { it.kind == "alerts" }))
        val body = Community.channelBody(rows.first { it.kind == "chat" }, null)
        assertEquals("""{"kind":"chat","server_id":"survival","channel_id":null}""", body.toString())
        assertEquals("private", guild.reminders)
    }

    @Test
    fun `Level settings send the Level roles only when they changed`() {
        val same = Community.levelsBody(guild, announce = false, day = 4, rows = listOf(LevelRow(5, "50")))
        assertEquals("""{"announce":false,"leaderboard_day":4}""", same.toString())
        val changed = Community.levelsBody(guild, announce = true, day = null, rows = listOf(LevelRow(20, "50"), LevelRow(30, null)))
        assertEquals("""[{"level":20,"role_id":"50"}]""", changed["roles"].toString())
        assertEquals(listOf("50"), Community.levelRoleChoices(guild).map { it.id }) // never @everyone
        assertEquals(listOf("survival"), Community.setupServers(guild, listOf("survival", "creative")))
        assertEquals(listOf("solo"), Community.setupServers(guild.copy(servers = emptyList()), listOf("solo")))
    }

    @Test
    fun `a server that can't check every kind says so`() {
        val hint = Community.goalHint(strings, "playtime", quests.kinds)
        assertTrue(hint.endsWith(strings.t("quests.limited_kinds")))
        assertFalse(Community.goalHint(strings, "playtime", Community.KINDS).contains(strings.t("quests.limited_kinds")))
    }

    @Test
    fun `events show attendance once it is counted, and only coming ones can be called off`() {
        val page = json.decodeFromString(
            EventsPage.serializer(),
            """{"events": [{"id": 1, "title": "Dragon night", "details": "", "kind": "approval", "goal": "",
                  "starts_at": 1761300000, "ends_at": 1761307200, "start_server": true, "rewards": [],
                  "state": "approving", "answers": {"going": 3, "maybe": 1, "no": 0},
                  "going": [], "attendance": [{"discord_id": "11", "discord_name": "Sam", "minutes": 40,
                  "attended": true, "how": "suggested", "fixed_by": null}], "fixable": true}],
                "polls": [{"id": 2, "question": "Next map?", "answers": ["Desert", "Snow"], "votes": [2, 5],
                  "total": 7, "closes_at": 1761400000, "closed_at": null, "open": true}],
                "kinds": ["online", "approval"], "bot": true}""",
        )
        val event = page.events.single()
        assertTrue(event.state in Community.ATTENDANCE_STATES)
        assertFalse(Community.canCallOff(event))
        assertTrue(Community.answersLine(strings, event).contains("3"))
        assertTrue(Community.eventLine(strings, event, emptyMap()).contains(strings.t("game_events.starts_server")))
        assertEquals("Desert: 2 · Snow: 5", Community.pollCounts(page.polls.single()))
        val person = event.attendance.single()
        assertFalse(Community.personLine(strings, person).contains(strings.t("game_events.how.suggested", "by" to "")))
    }

    @Test
    fun `a Discord server's check reads as plain problems, and an unchecked one says nothing yet`() {
        val status = json.decodeFromString(
            DiscordStatus.serializer(),
            """{"token_set": true, "state": "connected", "name": "Command Block", "switches": {"message_content": true, "members": null},
                "guilds": [{"id": "999", "name": "Friends", "servers": ["survival"], "options": {"remove_on_leave": true},
                  "report": {"problems": [{"id": "role_too_low", "role": "Legend"}], "checked_at": 1}},
                  {"id": "998", "name": "New", "servers": [], "options": {}, "report": null}]}""",
        )
        val (checked, unchecked) = status.guilds
        assertTrue(checked.removeOnLeave)
        assertFalse(unchecked.removeOnLeave)
        val line = Community.problems(strings, checked)!!.single()
        assertTrue(line, line.contains("Legend") && line.contains("Friends"))
        assertNull(Community.problems(strings, unchecked))
        assertNull(status.switches["members"])
        assertTrue(Community.botState(strings, status).contains("Command Block"))
    }

    @Test
    fun `an offline-mode server names the whitelist add-on it uses, or suggests some`() {
        val help = WhitelistHelp(onlineMode = false, suggested = listOf("EasyWhitelist", "TextWhitelist"))
        assertTrue(Community.whitelistNote(strings, help)!!.contains("EasyWhitelist"))
        assertTrue(Community.whitelistNote(strings, help.copy(addon = "EasyWhitelist"))!!.contains("EasyWhitelist"))
        assertNull(Community.whitelistNote(strings, help.copy(onlineMode = true)))
        assertNull(Community.whitelistNote(strings, help.copy(onlineMode = null)))
    }

    @Test
    fun `a Bedrock link request picks the player of the same name first`() {
        val request = LinkRequest(1, "Steve", "sam")
        val players = listOf(RecentPlayer("Alex", "a"), RecentPlayer("Steve", "s"))
        assertEquals("s", Community.likelyPlayer(request, players)!!.uuid)
        assertEquals("a", Community.likelyPlayer(LinkRequest(2, "Nobody", "x"), players)!!.uuid)
        assertNull(Community.likelyPlayer(request, emptyList()))
    }

    @Test
    fun `the screens call the agent's own routes`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"answer": "started"}"""))
            assertEquals("started", c.answerStartRequest("my server", start = true).answer)
            val start = agent.server.takeRequest()
            assertEquals("/api/servers/my%20server/start-request", start.path)
            assertEquals("""{"answer":"start"}""", start.body.readUtf8())

            agent.server.enqueue(agent.json("""{"read_up_to": 41}"""))
            assertEquals(41L, c.markAlertsRead(41).readUpTo)
            assertEquals("""{"up_to":41}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json("""{"links": [], "requests": [], "players": [], "reads_chat": false}"""))
            c.answerLinkRequest("survival", 5, approve = true, uuid = "abc")
            val link = agent.server.takeRequest()
            assertEquals("/api/servers/survival/discord-links/requests/5", link.path)
            assertEquals("""{"answer":"approve","uuid":"abc"}""", link.body.readUtf8())

            agent.server.enqueue(agent.json("""{"events": [], "polls": [], "kinds": [], "bot": false}"""))
            c.fixAttendance("survival", 1, "11", attended = false)
            val fix = agent.server.takeRequest()
            assertEquals("PUT", fix.method)
            assertEquals("/api/servers/survival/quests/events/1/attendance/11", fix.path)

            agent.server.enqueue(agent.json("""{"made": ["levels"], "status": {"token_set": true, "state": "connected"}}"""))
            assertEquals(listOf("levels"), c.setUpDiscord("999", listOf("survival")).made)
            val setup = agent.server.takeRequest()
            assertEquals("/api/discord/guilds/999/setup", setup.path)
            assertEquals("""{"servers":["survival"]}""", setup.body.readUtf8())

            agent.server.enqueue(agent.json("""{"token_set": true, "state": "connected"}"""))
            c.setReminders("999", "private")
            val reminders = agent.server.takeRequest()
            assertEquals("PUT", reminders.method)
            assertEquals("""{"reminders":"private"}""", reminders.body.readUtf8())

            agent.server.enqueue(agent.json("""{"token_set": true, "state": "connecting"}"""))
            c.setDiscordToken(" a.b.c ")
            val token = agent.server.takeRequest()
            assertEquals("/api/discord/token", token.path)
            assertEquals("""{"token":"a.b.c"}""", token.body.readUtf8())

            agent.server.enqueue(agent.json("""{"events": [], "polls": [], "kinds": [], "bot": true}"""))
            c.makePoll("survival", Community.pollBody(PollForm(question = "Map?", answers = listOf("A", "B"), closes = "2026-10-31 20:00"), ZoneId.of("UTC"))!!)
            assertEquals("/api/servers/survival/quests/polls", agent.server.takeRequest().path)

            // A request that ended (answered elsewhere, or expired) comes back as the agent's words.
            agent.server.enqueue(agent.json("""{"detail": "There's no start request open for Survival."}""", 404))
            val gone = assertThrows(AgentException.Failed::class.java) { runBlocking { c.answerStartRequest("survival", start = false) } }
            assertEquals("There's no start request open for Survival.", gone.reason)
        }
    }

    @Test
    fun `every word the community screens use is in the table`() {
        val keys = listOf("mobile.start_request.started", "mobile.start_request.said_no", "page.quests", "mobile.quests.last_day_hint", "mobile.quests.when_hint", "mobile.discord.hint", "mobile.discord.token_hint") +
            Community.KINDS.map { "quests.kind.$it" } + Community.REPEATS.map { "quests.repeat_pick.$it" } +
            Community.EVENT_KINDS.map { "game_events.kind.$it" } + Community.REMINDERS.map { "discord.reminders.$it" } +
            (Community.SHARED_CHANNELS + Community.SERVER_CHANNELS).map { "discord.kind.$it" }
        for (key in keys) assertTrue(key, strings.has(key))
    }
}
