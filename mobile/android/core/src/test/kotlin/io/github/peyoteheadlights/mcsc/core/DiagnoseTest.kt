package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnoseTest {
    private val token = "secret-session-token-1234567890"

    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), { token }, systemTrust = TestAgent.trustingNothing())

    @Test
    fun `lines go under a new heading each time the group changes`() {
        val lines = listOf(
            DiagnoseLine("Join from this PC", "Log in", true),
            DiagnoseLine("Join from this PC", "Arrive in the world", false, "Kicked"),
            DiagnoseLine("Home network", "From this PC", null, "Not checked"),
        )
        val groups = Diagnose.groups(lines)
        assertEquals(listOf("Join from this PC", "Home network"), groups.map { it.first })
        assertEquals(2, groups[0].second.size)
        assertEquals(listOf("✓", "✗", "?"), lines.map { Diagnose.mark(it.ok) })
    }

    @Test
    fun `changes wait for a pick only once the run is done`() {
        val change = DiagnoseChange("restart", "Restart the server", "", safe = true)
        assertEquals("diagnose.starting", Diagnose.statusKey(null))
        assertEquals("diagnose.running", Diagnose.statusKey(Diagnosis(running = true, changes = listOf(change))))
        assertTrue(Diagnose.waiting(Diagnosis(running = true, changes = listOf(change))).isEmpty())
        assertEquals("diagnose.offer", Diagnose.statusKey(Diagnosis(changes = listOf(change))))
        assertEquals("diagnose.done", Diagnose.statusKey(Diagnosis()))
        assertThrows(IllegalArgumentException::class.java) { Diagnose.pickBody("everything") }
    }

    @Test
    fun `the run is started, read and picked through the agent`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("null"))
            assertNull(c.diagnosis("s"))
            assertEquals("/api/servers/s/server/diagnose", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"job_id": null, "ask_mods": "Join with all mods too?"}"""))
            assertEquals("Join with all mods too?", c.startDiagnose("s", goAhead = false, mods = null).askMods)
            assertEquals("""{"go_ahead":false}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json("""{"job_id": "j"}"""))
            assertEquals("j", c.startDiagnose("s", goAhead = true, mods = false).jobId)
            assertEquals("""{"go_ahead":true,"mods":false}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json("""{"running": false, "started_at": 1.0, "finished_at": 2.0, "by": "admin", "round": 1, "mods": false, "lines": [{"group": "Home network", "what": "From this PC", "ok": false, "detail": "No answer", "port": 25565, "outside": false}], "changes": [{"key": "firewall", "title": "Let Minecraft through the firewall", "why": "Nothing answered", "safe": false}], "done": [{"title": "Restart", "safe": true, "outcome": "no_help", "detail": "Put back"}], "steps": ["Open port 25565 on the router"], "known": [{"id": "oom", "say": "The server ran out of memory.", "link": null, "change": "Give it 1 GB more memory"}], "sign_in": true, "sign_in_mods": false}"""))
            val result = c.diagnosis("s")!!
            assertEquals("Let Minecraft through the firewall", Diagnose.waiting(result).single().title)
            assertEquals("Give it 1 GB more memory", result.known.single().change)
            assertEquals("–", Diagnose.doneMark(result.done.single()))
            assertTrue(result.signIn)
            agent.server.takeRequest()

            agent.server.enqueue(agent.json("""{"job_id": "k"}"""))
            c.diagnoseChanges("s", "safe")
            val sent = agent.server.takeRequest()
            assertEquals("/api/servers/s/server/diagnose/changes", sent.path)
            assertEquals("""{"pick":"safe"}""", sent.body.readUtf8())
        }
    }
}
