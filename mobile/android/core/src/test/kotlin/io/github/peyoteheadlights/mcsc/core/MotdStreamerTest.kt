package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MotdStreamerTest {
    private val token = "secret-session-token-1234567890"

    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), { token }, systemTrust = TestAgent.trustingNothing())

    @Test
    fun `colour codes become pieces the way the server list draws them`() {
        val pieces = Motd.pieces("§6§lGold\n§rplain§cred")
        assertEquals(listOf("Gold\n", "plain", "red"), pieces.map { it.text })
        assertEquals(0xFFAA00, pieces[0].color)
        assertTrue(pieces[0].bold)
        assertEquals(Motd.PLAIN, pieces[1].color)
        assertFalse(pieces[1].bold)
        // A colour ends bold, as in Java Edition.
        assertFalse(Motd.pieces("§lA§cB")[1].bold)
    }

    @Test
    fun `streamer mode hides what the shared cases say`() {
        val streamer = Streamer(Shared.text("streamer.json"))
        assertTrue(streamer.cases.isNotEmpty())
        for ((shown, hidden) in streamer.cases) assertEquals(shown, hidden, streamer.hide(shown))
    }

    @Test
    fun `the server icon goes to the agent's route as base64`() = runBlocking {
        TestAgent().use { agent ->
            agent.server.enqueue(agent.json("""{"png": null}"""))
            assertNull(client(agent).serverIcon("s"))
            assertEquals("/api/servers/s/server-icon", agent.server.takeRequest().path)
            agent.server.enqueue(agent.json("""{"ok": true, "icon": true, "running": true}"""))
            assertTrue(client(agent).saveServerIcon("s", "iVBORw0K").running)
            val put = agent.server.takeRequest()
            assertEquals("PUT", put.method)
            assertEquals("""{"png":"iVBORw0K"}""", put.body.readUtf8())
            agent.server.enqueue(agent.json("""{"ok": true, "icon": false, "running": false}"""))
            assertFalse(client(agent).removeServerIcon("s").icon)
            assertEquals("DELETE", agent.server.takeRequest().method)
        }
    }
}
