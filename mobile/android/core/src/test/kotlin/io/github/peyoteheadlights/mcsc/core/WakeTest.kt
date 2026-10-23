package io.github.peyoteheadlights.mcsc.core

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WakeTest {
    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), token = { "t" })

    private val withHelper = """{"on": true, "pc": "GAMING-PC", "mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255",
        "helper": {"address": "127.0.0.1", "port": 47391}, "router_url": null, "has_key": false}"""

    @Test
    fun `the wake signal is six FF bytes and the MAC sixteen times`() {
        val packet = Wake.magicPacket("aa-bb-cc-dd-ee-ff")
        assertEquals(102, packet.size)
        assertArrayEquals(ByteArray(6) { 0xFF.toByte() }, packet.copyOfRange(0, 6))
        assertArrayEquals(byteArrayOf(0xAA.toByte(), 0xBB.toByte(), 0xCC.toByte(), 0xDD.toByte(), 0xEE.toByte(), 0xFF.toByte()), packet.copyOfRange(96, 102))
        assertTrue(runCatching { Wake.magicPacket("not a mac") }.isFailure)
    }

    @Test
    fun `with a helping PC the phone asks for its own key once`() = runBlocking {
        TestAgent().use { agent ->
            agent.server.enqueue(agent.json(withHelper))
            agent.server.enqueue(agent.json("""{"key": "secret-key"}"""))
            agent.server.enqueue(agent.json(withHelper.replace("\"has_key\": false", "\"has_key\": true")))
            val c = client(agent)
            val kept = Wake.keep(c, null)
            assertEquals("secret-key", kept.key)
            assertEquals("/api/wake", agent.server.takeRequest().path)
            assertEquals("/api/wake/key", agent.server.takeRequest().path)
            // Next time the agent knows the key: no new one.
            assertEquals("secret-key", Wake.keep(c, kept).key)
            assertEquals(3, agent.server.requestCount)
            assertTrue(kept.offered(canSignal = false))
        }
    }

    @Test
    fun `without a helping PC there's no key and an iPhone-like phone has no Wake`() = runBlocking {
        TestAgent().use { agent ->
            agent.server.enqueue(agent.json("""{"on": true, "pc": "PC", "mac": "AA:BB:CC:DD:EE:FF", "has_key": false}"""))
            val kept = Wake.keep(client(agent), KeptWake(WakeInfo(), "old"))
            assertNull(kept.key)
            assertFalse(kept.offered(canSignal = false))
            assertTrue(kept.offered(canSignal = true))
            assertFalse(KeptWake(WakeInfo(on = false, mac = "AA:BB:CC:DD:EE:FF")).offered(canSignal = true))
        }
    }

    @Test
    fun `the helping PC gets the key and the home network the signal`() {
        DatagramSocket(0, InetAddress.getByName("127.0.0.1")).use { listener ->
            listener.soTimeout = 5000
            val kept = KeptWake(
                WakeInfo(on = true, mac = "AA:BB:CC:DD:EE:FF", broadcast = "127.0.0.1", helper = WakeHelper("127.0.0.1", listener.localPort)),
                key = "secret-key",
            )
            Wake.send(kept, signal = true, wakePort = listener.localPort)
            val got = mutableListOf<String>()
            repeat(2) {
                val buffer = DatagramPacket(ByteArray(256), 256)
                listener.receive(buffer)
                got += if (buffer.length == 102) "signal" else String(buffer.data, 0, buffer.length)
            }
            assertEquals(setOf("signal", "CBWAKE1 secret-key"), got.toSet())
        }
    }

    @Test
    fun `alerts for this device follow the shared list or its own choice`() = runBlocking {
        TestAgent().use { agent ->
            agent.server.enqueue(agent.json("""{"own": false, "events": {"server_crashed": true, "player_joined": false}}"""))
            agent.server.enqueue(agent.json("""{"own": true, "events": {"server_crashed": true, "player_joined": true}}"""))
            val c = client(agent)
            assertFalse(c.deviceAlerts().own)
            val chosen = c.chooseDeviceAlerts(true, setOf("server_crashed", "player_joined"))
            assertTrue(chosen.own && chosen.events.getValue("player_joined"))
            agent.server.takeRequest()
            val put = agent.server.takeRequest()
            assertEquals("PUT", put.method)
            val body = Json.parseToJsonElement(put.body.readUtf8()).jsonObject
            assertTrue(body.getValue("own").jsonPrimitive.boolean)
            assertEquals(listOf("player_joined", "server_crashed"), body.getValue("events").jsonArray.map { it.jsonPrimitive.content })
        }
    }
}
