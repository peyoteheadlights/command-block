package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

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

            // A request that ended (answered elsewhere, or expired) comes back as the agent's words.
            agent.server.enqueue(agent.json("""{"detail": "There's no start request open for Survival."}""", 404))
            val gone = assertThrows(AgentException.Failed::class.java) { runBlocking { c.answerStartRequest("survival", start = false) } }
            assertEquals("There's no start request open for Survival.", gone.reason)
        }
    }

    @Test
    fun `every word the community screens use is in the table`() {
        val keys = listOf("mobile.start_request.started", "mobile.start_request.said_no", "page.quests", "mobile.quests.last_day", "mobile.quests.make_events", "mobile.discord.hint", "mobile.discord.on_pc") +
            Community.KINDS.map { "quests.kind.$it" } + Community.REPEATS.map { "quests.repeat_pick.$it" }
        for (key in keys) assertTrue(key, strings.has(key))
    }
}
