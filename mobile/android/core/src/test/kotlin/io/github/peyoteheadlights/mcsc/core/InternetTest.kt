package io.github.peyoteheadlights.mcsc.core

import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class InternetTest {
    private val strings = Strings(Shared.strings)
    private val json = Json { ignoreUnknownKeys = true }

    private fun reach(text: String) = json.decodeFromString(InternetReach.serializer(), text)

    @Test
    fun anOpenPortIsNotKnownUntilTested() {
        val info = reach("""{"on": true, "known": false, "reason": "not_tested", "address": "1.2.3.4", "port": 25565,
            "ports": [{"protocol": "TCP", "port": 25565}], "local_address": "192.168.1.20"}""")
        val lines = Internet.lines(strings, info, owner = true).map { it.text }
        assertEquals(listOf(strings.t("address.open", "address" to "1.2.3.4:25565"), strings.t("address.not_known")), lines)
        assertTrue(Internet.testable(info))
        assertEquals(emptyList<String>(), Internet.forwardSteps(strings, info, technical = false))
    }

    @Test
    fun noRouterGivesTheForwardingStepsWithThisPcsAddress() {
        val info = reach("""{"on": true, "reason": "no_router", "port": 25565,
            "ports": [{"protocol": "TCP", "port": 25565}], "local_address": "192.168.1.20"}""")
        assertEquals(Tone.DANGER, Internet.lines(strings, info, owner = true).single().tone)
        val steps = Internet.forwardSteps(strings, info, technical = false)
        assertEquals(strings.t("address.forward_step", "protocol" to "TCP", "port" to 25565, "local" to "192.168.1.20"), steps.first())
        assertFalse(Internet.testable(info))
    }

    @Test
    fun aTestedAddressSaysWhoTestedIt() {
        val info = reach("""{"on": true, "known": true, "works": true, "tested_by": "phone", "reason": "tested",
            "address": "1.2.3.4", "port": 25565}""")
        val line = Internet.lines(strings, info, owner = false).last()
        assertEquals(Tone.SUCCESS, line.tone)
        assertTrue(line.text.contains(strings.t("address.by_phone")))
    }

    @Test
    fun theJavaPingNeedsAMinecraftAnswer() {
        ServerSocket(0).use { server ->
            thread {
                server.accept().use { socket ->
                    val input = DataInputStream(socket.getInputStream())
                    input.readFully(ByteArray(input.read())) // the handshake
                    input.readFully(ByteArray(2)) // the status request
                    val text = """{"version":{"name":"1.21"}}""".toByteArray()
                    val body = byteArrayOf(0, text.size.toByte()) + text
                    socket.getOutputStream().write(byteArrayOf(body.size.toByte()) + body)
                }
            }
            assertTrue(OutsidePing.java("127.0.0.1", server.localPort))
        }
        val closed = ServerSocket(0).use { it.localPort }
        assertFalse(OutsidePing.java("127.0.0.1", closed, timeoutMs = 1000))
    }

    @Test
    fun theBedrockPingTakesARakNetPong() {
        DatagramSocket(0, InetAddress.getByName("127.0.0.1")).use { server ->
            thread {
                val ping = DatagramPacket(ByteArray(64), 64)
                server.receive(ping)
                val pong = byteArrayOf(0x1C) + ByteArray(40)
                server.send(DatagramPacket(pong, pong.size, ping.socketAddress))
            }
            assertTrue(OutsidePing.bedrock("127.0.0.1", server.localPort, timeoutMs = 2000))
        }
    }
}
