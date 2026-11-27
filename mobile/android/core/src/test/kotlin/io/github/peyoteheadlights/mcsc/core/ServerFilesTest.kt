package io.github.peyoteheadlights.mcsc.core

import java.io.ByteArrayOutputStream
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Server files, the graphs as CSV, and the Technical settings group (11c part 4b). */
class ServerFilesTest {
    private val token = "secret-session-token-1234567890"

    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), { token }, systemTrust = TestAgent.trustingNothing())

    @Test
    fun `a folder lists folders and files, and paths inside it use slashes`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"path": "plugins", "parent": "", "entries": [
                {"name": "Thing", "folder": true, "size": null, "changed": 1.0},
                {"name": "a b.yml", "folder": false, "size": 12, "changed": 2.0}], "truncated": false}"""))
            val found = c.serverFiles("survival", "plugins")
            assertEquals("/api/servers/survival/files?path=plugins", agent.server.takeRequest().path)
            assertEquals("", found.parent)
            assertNull(found.entries[0].size)
            assertEquals("plugins/a b.yml", found.inside(found.entries[1].name))
            assertEquals("x.txt", ServerFiles(path = "").inside("x.txt"))
        }
    }

    @Test
    fun `a download is streamed as it is, an upload names the folder`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("").setHeader("Content-Type", "application/octet-stream").setBody("hello"))
            val out = ByteArrayOutputStream()
            assertEquals(5L, c.downloadServerFile("survival", "plugins/notes.txt", out))
            assertEquals("hello", out.toString())
            assertEquals("/api/servers/survival/files/download?path=plugins%2Fnotes.txt", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"ok": true, "path": "plugins/x.yml", "name": "x.yml", "size": 2}"""))
            val saved = c.uploadServerFile("survival", "plugins", PickedFile("x.yml", 2) { "a\n".byteInputStream() })
            assertEquals("x.yml", saved.name)
            val sent = agent.server.takeRequest()
            assertEquals("/api/servers/survival/files/upload?path=plugins", sent.path)
            assertTrue(sent.body.readUtf8().contains("filename=\"x.yml\""))
        }
    }

    @Test
    fun `the graphs come as csv text`() = runBlocking {
        TestAgent().use { agent ->
            agent.server.enqueue(agent.json("""{"filename": "Survival-performance.csv", "text": "time,pc_cpu_percent\n"}"""))
            val csv = client(agent).performanceCsv("survival", 24)
            assertEquals("Survival-performance.csv", csv.filename)
            assertEquals("/api/servers/survival/performance/csv?hours=24", agent.server.takeRequest().path)
        }
    }

    @Test
    fun `exit codes and priority are a Technical group`() {
        val group = ServerForm.GROUPS.single { it.technical }
        assertEquals(listOf("monitor.ignored_exit_codes", "server.low_priority"), group.fields.map { it.key })
    }
}
