package io.github.peyoteheadlights.mcsc.core

import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

/**
 * "Let friends join from the internet" on the phones: the words for what
 * the agent says (agent/internet.py), and the phone's own outside test.
 * The same rules as the desktop app's Server address card.
 */
object Internet {
    data class Line(val text: String, val tone: Tone)

    /** "1.2.3.4:25565", or null while the router gave no public address. */
    fun address(info: InternetReach): String? =
        info.address?.let { if (info.port != null) "$it:${info.port}" else it }

    fun lines(strings: Strings, info: InternetReach, owner: Boolean): List<Line> {
        val out = mutableListOf<Line>()
        val full = address(info).orEmpty()
        if (!owner) out += Line(strings.t("address.owner_only"), Tone.NEUTRAL)
        when {
            info.reason == "off" -> out += Line(strings.t("address.internet_note", "port" to info.port), Tone.NEUTRAL)
            info.reason == "not_running" -> out += Line(strings.t("address.not_running"), Tone.NEUTRAL)
            info.busy -> out += Line(strings.t("address.busy"), Tone.NEUTRAL)
            info.reason == "tested" -> {
                val by = strings.t(if (info.testedBy == "phone") "address.by_phone" else "address.by_mcsrvstat")
                out += if (info.works == true) {
                    Line(strings.t("address.works", "address" to full, "by" to by), Tone.SUCCESS)
                } else {
                    Line(strings.t("address.fails", "address" to full, "by" to by), Tone.DANGER)
                }
            }
            info.reason == "shared" || info.reason == "private" -> out += Line(strings.t("address.${info.reason}"), Tone.DANGER)
            info.reason == "no_router" || info.reason == "refused" ->
                out += Line(strings.t("address.${info.reason}", "detail" to info.detail.orEmpty()), Tone.DANGER)
            full.isNotEmpty() -> {
                out += Line(strings.t("address.open", "address" to full), Tone.NEUTRAL)
                out += Line(strings.t("address.not_known"), Tone.NEUTRAL)
            }
        }
        return out
    }

    /** The port forwarding steps: after the router said no or a failed test, and always in Technical. */
    fun forwardSteps(strings: Strings, info: InternetReach, technical: Boolean): List<String> {
        val failed = info.reason == "no_router" || info.reason == "refused" || (info.reason == "tested" && info.works == false)
        if (!failed && !(technical && info.reason != "shared" && info.reason != "private")) return emptyList()
        val local = info.localAddress ?: "?"
        return info.ports.map { strings.t("address.forward_step", "protocol" to it.protocol, "port" to it.port, "local" to local) } +
            strings.t("address.forward_help")
    }

    /** "Friends can also type myserver.duckdns.org:25565", once an outside test passed. */
    fun nameLines(strings: Strings, info: InternetReach): List<String> =
        if (info.reason == "tested" && info.works == true && info.port != null) {
            info.names.map { strings.t("ddns.join", "name" to it.name, "port" to info.port) }
        } else {
            emptyList()
        }

    /** A saved name's lines: the name, why it stopped, and No-IP's confirm date. */
    fun savedLines(strings: Strings, kind: String, name: DdnsName, date: (Double) -> String): List<Line> {
        val out = mutableListOf(Line(strings.t("ddns.${kind}_saved", "name" to name.name), Tone.NEUTRAL))
        name.problem?.let { out += Line(strings.t("ddns.stopped", "problem" to it), Tone.DANGER) }
        name.confirmBy?.let {
            out += Line(strings.t("ddns.noip_confirm_by", "date" to date(it)), if (name.confirmSoon) Tone.DANGER else Tone.NEUTRAL)
        }
        return out
    }

    /** The outside tests make sense once the router gave a public address. */
    fun testable(info: InternetReach): Boolean =
        address(info) != null && info.reason in setOf("not_tested", "not_checked", "tested")
}

/**
 * The phone's own ping, over mobile data, like a friend's game would ask:
 * Java's server list ping, or Bedrock's NetherNet join page then RakNet.
 * True only when the server answered like a Minecraft server.
 */
object OutsidePing {
    fun java(host: String, port: Int, timeoutMs: Int = 5000): Boolean = try {
        Socket().use { socket ->
            socket.connect(InetSocketAddress(host, port), timeoutMs)
            socket.soTimeout = timeoutMs
            val name = host.toByteArray()
            val body = ByteArrayOutputStream().apply {
                write(0)
                writeVarInt(-1) // protocol -1: just asking
                writeVarInt(name.size)
                write(name)
                write(port shr 8 and 0xFF)
                write(port and 0xFF)
                writeVarInt(1) // next: status
            }.toByteArray()
            val out = ByteArrayOutputStream().apply {
                writeVarInt(body.size)
                write(body)
                write(1)
                write(0) // the status request
            }
            socket.getOutputStream().write(out.toByteArray())
            val input = DataInputStream(socket.getInputStream())
            val length = input.readVarInt()
            length in 1..(1 shl 20) && input.readVarInt() == 0 && input.readVarInt() > 0 && input.read() == '{'.code
        }
    } catch (e: Exception) {
        false
    }

    fun bedrock(host: String, port: Int, timeoutMs: Int = 5000): Boolean = netherNet(host, port, timeoutMs) || rakNet(host, port, timeoutMs)

    /** Bedrock 1.26.50+: a small TCP web page on the game port. */
    private fun netherNet(host: String, port: Int, timeoutMs: Int): Boolean = try {
        val where = if (':' in host) "[$host]" else host
        val page = URL("http://$where:$port/v1/join").openConnection() as HttpURLConnection
        page.connectTimeout = timeoutMs
        page.readTimeout = timeoutMs
        try {
            page.responseCode == 200 && page.inputStream.bufferedReader().readText().contains("\"version\"")
        } finally {
            page.disconnect()
        }
    } catch (e: Exception) {
        false
    }

    private val MAGIC = byteArrayOf(
        0x00, 0xFF.toByte(), 0xFF.toByte(), 0x00, 0xFE.toByte(), 0xFE.toByte(), 0xFE.toByte(), 0xFE.toByte(),
        0xFD.toByte(), 0xFD.toByte(), 0xFD.toByte(), 0xFD.toByte(), 0x12, 0x34, 0x56, 0x78,
    )

    /** Older Bedrock and Geyser: RakNet's "unconnected ping" over UDP. */
    private fun rakNet(host: String, port: Int, timeoutMs: Int): Boolean = try {
        DatagramSocket().use { socket ->
            socket.soTimeout = timeoutMs
            val ping = ByteArray(33)
            ping[0] = 0x01
            MAGIC.copyInto(ping, 9)
            socket.send(DatagramPacket(ping, ping.size, InetAddress.getByName(host), port))
            val reply = DatagramPacket(ByteArray(2048), 2048)
            socket.receive(reply)
            reply.length > 0 && reply.data[0] == 0x1C.toByte()
        }
    } catch (e: Exception) {
        false
    }

    private fun ByteArrayOutputStream.writeVarInt(number: Int) {
        var value = number
        while (true) {
            if (value and 0x7F.inv() == 0) {
                write(value)
                return
            }
            write(value and 0x7F or 0x80)
            value = value ushr 7
        }
    }

    private fun DataInputStream.readVarInt(): Int {
        var value = 0
        for (shift in 0 until 35 step 7) {
            val byte = readUnsignedByte()
            value = value or (byte and 0x7F shl shift)
            if (byte and 0x80 == 0) return value
        }
        error("varint too long")
    }
}
