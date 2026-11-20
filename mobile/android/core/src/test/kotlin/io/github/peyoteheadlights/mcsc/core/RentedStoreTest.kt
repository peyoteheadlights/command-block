package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Rented servers, the Store tab and the Bedrock switch on the phones (11c part 8). */
class RentedStoreTest {
    private val token = "secret-session-token-1234567890"
    private val strings = Strings(Shared.strings)

    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), { token }, systemTrust = TestAgent.trustingNothing())

    @Test
    fun `the rented list reads servers and panels, and the key is never sent back`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"servers": [{"id": "r-1", "kind": "pterodactyl", "name": "Survival",
                "host": "panel.sparkedhost.us", "panel_id": "p1", "state": "UNKNOWN", "state_reason": "The panel refused the key.",
                "resources": null, "discord": "status"}], "panels": [{"id": "p1", "host": "panel.sparkedhost.us", "address": "https://panel.sparkedhost.us"}]}"""))
            val list = c.rented()
            assertEquals("/api/rented", agent.server.takeRequest().path)
            val row = list.servers.single()
            assertEquals("p1", row.panelId)
            assertEquals("status", row.discord)
            assertEquals("Unknown: The panel refused the key.", Rented.stateLine(strings, row))
            assertEquals("Memory Unknown, CPU Unknown, disk Unknown", Rented.usage(strings, row.resources))

            agent.server.enqueue(agent.json("""{"address": "https://panel.x", "host": "panel.x",
                "servers": [{"identifier": "a1b2", "name": "Survival", "node": "n", "server_owner": true}], "full_account": true}"""))
            val found = c.checkPanel(" panel.x ", " key ")
            val sent = agent.server.takeRequest()
            assertEquals("/api/rented/check", sent.path)
            val body = sent.body.readUtf8()
            assertTrue(body, body.contains("\"allow_host\":true") && body.contains("\"address\":\"panel.x\"") && body.contains("\"key\":\"key\""))
            assertTrue(found.fullAccount)
            assertEquals("a1b2", found.servers.single().identifier)
        }
    }

    @Test
    fun `kill asks with confirm, and the console reads after the last line`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"result": "REQUESTED", "detail": "The panel was asked."}"""))
            c.rentedPower("r 1", "kill", confirm = true)
            val sent = agent.server.takeRequest()
            assertEquals("/api/rented/r%201/power", sent.path)
            assertEquals("""{"signal":"kill","confirm":true}""", sent.body.readUtf8())

            agent.server.enqueue(agent.json("""{"lines": [{"n": 4, "text": "Done"}], "last": 4, "problem": null}"""))
            val console = c.rentedConsole("r-1", 3)
            assertEquals("/api/rented/r-1/console?after=3", agent.server.takeRequest().path)
            assertEquals(4, console.last)
            assertNull(console.problem)
        }
    }

    @Test
    fun `power buttons follow the state, and nothing works while it's Unknown`() {
        assertTrue(Rented.enabled(States.OFFLINE, "start"))
        assertFalse(Rented.enabled(States.OFFLINE, "stop"))
        assertTrue(Rented.enabled(States.ONLINE, "kill"))
        assertFalse(Rented.enabled(States.ONLINE, "start"))
        for (action in Rented.POWER + "kill") assertFalse(action, Rented.enabled(States.UNKNOWN, action))
        assertFalse(Rented.mayConnect(Me("mod", role = "moderator")))
        assertTrue(Rented.mayConnect(Me("mark")))
        for (key in Rented.NEEDS_THIS_PC) assertTrue(key, key in Shared.strings)
        assertTrue(Shared.text("links.json").contains("\"aternos\": \"${Rented.ATERNOS}\""))
    }

    @Test
    fun `a backup still being made says so, a failed one says failed`() {
        assertEquals("Nightly: the host is still making it", Rented.backupLine(strings, RentedBackup(name = "Nightly")))
        val done = RentedBackup(name = "Nightly", bytes = 2048, completedAt = "2026-11-20T10:15:00+00:00", succeeded = false)
        assertEquals("Nightly, 2.0 KB, 2026-11-20 10:15 (failed)", Rented.backupLine(strings, done))
    }

    @Test
    fun `the Store shows only for the owner in Technical on a Java community server`() {
        val row = ServerRow("s", "S", edition = "java", playedBy = "community")
        val owner = Me("mark")
        assertTrue(Store.shown(row, owner, technical = true))
        assertFalse(Store.shown(row, owner, technical = false))
        assertFalse(Store.shown(row, Me("mod", role = "moderator"), technical = true))
        assertFalse(Store.shown(row.copy(test = true), owner, technical = true))
        assertFalse(Store.shown(row.copy(playedBy = "kids"), owner, technical = true))
        assertFalse(Store.shown(row.copy(edition = "bedrock"), owner, technical = true))
    }

    @Test
    fun `the store view reads packages, and connect sends only the key`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"offered": true, "reason": null, "needs_type": false, "connected": true,
                "installed": {"tebex": true, "luckperms": true}, "ranks": [{"id": "supporter", "name": "Supporter", "made": true}],
                "store": {"name": "Shop", "link": "https://shop.tebex.io", "currency": "EUR"},
                "packages": [{"id": "1", "name": "Supporter", "price": 5.0, "rank": "supporter"}, {"id": "2", "name": "Kit", "price": 4.99, "rank": null}],
                "checklist": "1. ...", "remind": ["Kit"]}"""))
            val view = c.connectStore("survival", " tbx-key ")
            val sent = agent.server.takeRequest()
            assertEquals("/api/servers/survival/store/connect", sent.path)
            assertEquals("""{"key":"tbx-key"}""", sent.body.readUtf8())
            assertTrue(Store.usable(view))
            assertEquals("✓ Supporter  5 EUR", Store.packageLine(view, view.packages!![0]))
            assertEquals("Kit  4.99 EUR", Store.packageLine(view, view.packages!![1]))
            assertEquals(listOf("Kit"), view.remind)
        }
    }

    @Test
    fun `the Bedrock switch is the owner's, on Bedrock only`() {
        val group = ServerForm.GROUPS.single { it.bedrockOwner }
        assertEquals(listOf("server.bedrock_auto_update"), group.fields.map { it.key })
        val owner = Me("mark")
        assertTrue(ServerForm.shown(group, technical = false, me = owner, edition = "bedrock"))
        assertFalse(ServerForm.shown(group, technical = false, me = owner, edition = "java"))
        assertFalse(ServerForm.shown(group, technical = false, me = Me("mod", role = "moderator"), edition = "bedrock"))
        val technicalOnly = ServerForm.GROUPS.single { it.technical }
        assertFalse(ServerForm.shown(technicalOnly, technical = false, me = owner, edition = "java"))
    }

    @Test
    fun `an Update all row says when its list was a saved copy`() {
        val row = AgentJson.decodeFromString(UpdateAllRow.serializer(), """{"filename": "a.jar", "saved": "Saved list from 3 days ago."}""")
        assertEquals("Saved list from 3 days ago.", row.saved)
    }
}
