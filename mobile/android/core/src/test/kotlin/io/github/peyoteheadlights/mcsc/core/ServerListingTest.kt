package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerListingTest {
    private val token = "secret-session-token-1234567890"

    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), { token }, systemTrust = TestAgent.trustingNothing())

    private fun row(id: String, group: String = "") = ServerRow(id = id, name = id.replaceFirstChar { it.uppercase() }, group = group)

    @Test
    fun `groups come in list order, servers without one last`() {
        val rows = listOf(row("a"), row("b", "Friends"), row("c"), row("d", "Friends"))
        assertEquals(
            listOf("Friends" to listOf("b", "d"), "" to listOf("a", "c")),
            ServerListing.grouped(rows, "").map { (name, list) -> name to list.map { it.id } },
        )
        // No groups at all: one heading-less list. The search looks at names and groups.
        assertEquals(listOf("" to listOf("a")), ServerListing.grouped(listOf(row("a"), row("c")), "A").map { it.first to it.second.map { r -> r.id } })
        assertEquals(listOf("b", "d"), ServerListing.grouped(rows, "friend").single().second.map { it.id })
        assertTrue(ServerListing.grouped(rows, "zzz").isEmpty())
    }

    @Test
    fun `moving is one place among the shown servers`() {
        val order = listOf("a", "hidden", "b", "c")
        assertEquals(listOf("b", "a", "hidden", "c"), ServerListing.moved(order, listOf("a", "b", "c"), "b", up = true))
        assertEquals(listOf("hidden", "b", "a", "c"), ServerListing.moved(order, listOf("a", "b", "c"), "a", up = false))
        assertNull(ServerListing.moved(order, listOf("a", "b", "c"), "a", up = true))
        assertNull(ServerListing.moved(order, listOf("a", "b", "c"), "c", up = false))
    }

    @Test
    fun `completions are the server's own names for the first word`() {
        val names = listOf("gamemode", "gamerule", "give", "say")
        assertEquals(listOf("gamemode", "gamerule"), ServerListing.completions(names, "GAM"))
        assertEquals(emptyList<String>(), ServerListing.completions(names, "gamerule "))
        assertEquals(emptyList<String>(), ServerListing.completions(names, "say"))
        assertEquals(emptyList<String>(), ServerListing.completions(names, ""))
    }

    @Test
    fun `start all, game rules and older logs use the agent's routes`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"servers": [{"id": "a", "name": "A", "result": "failed", "detail": "Port busy"}]}"""))
            assertEquals("Port busy", c.allServers("start", listOf("a")).servers.single().detail)
            val start = agent.server.takeRequest()
            assertEquals("/api/servers/start-all", start.path)
            assertEquals("""{"ids":["a"]}""", start.body.readUtf8())

            agent.server.enqueue(agent.json("""{"ok": true}"""))
            c.saveServerGroup("a", "Friends")
            val group = agent.server.takeRequest()
            assertEquals("PUT /api/servers/a/group", "${group.method} ${group.path}")
            assertEquals("""{"group":"Friends"}""", group.body.readUtf8())

            agent.server.enqueue(agent.json("""{"running": true, "rules": [{"name": "keepInventory", "value": false}, {"name": "spawnRadius", "value": 10}], "presets": ["friendly"]}"""))
            val rules = c.gameRules("a")
            assertEquals(JsonPrimitive(false), rules.rules[0].value)
            assertEquals(10, rules.rules[1].value.content.toInt())
            agent.server.takeRequest()
            agent.server.enqueue(agent.json("""{"rules": [], "before": {"keepInventory": false}, "skipped": ["doinsomnia"]}"""))
            val saved = c.saveGameRules("a", buildJsonObject { put("keepInventory", true) })
            assertEquals(listOf("doinsomnia"), saved.skipped)
            assertEquals("""{"rules":{"keepInventory":true}}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json("""{"name": "2026-11-19-1.log.gz", "lines": ["Done"], "cut": false}"""))
            assertEquals(listOf("Done"), c.logFile("a", "2026-11-19-1.log.gz").lines)
            assertEquals("/api/servers/a/logs/files/2026-11-19-1.log.gz", agent.server.takeRequest().path)
        }
    }
}
