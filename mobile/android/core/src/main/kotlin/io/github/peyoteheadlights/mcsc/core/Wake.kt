package io.github.peyoteheadlights.mcsc.core

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** What GET /api/wake answers (agent/wake.py for_device). */
@Serializable
data class WakeInfo(
    val on: Boolean = false,
    val pc: String = "",
    val mac: String? = null,
    val broadcast: String? = null,
    val helper: WakeHelper? = null,
    @SerialName("router_url") val routerUrl: String? = null,
    @SerialName("has_key") val hasKey: Boolean = false,
)

@Serializable
data class WakeHelper(val address: String, val port: Int)

@Serializable
data class WakeKey(val key: String)

/** What the phone keeps, while the PC is awake, to wake it once it sleeps:
 * the agent's answer and this phone's own wake key (only when there's a
 * helping PC, which checks it). */
@Serializable
data class KeptWake(val info: WakeInfo, val key: String? = null) {
    /** Whether "Wake <PC>" is offered. [canSignal]: this phone can send the
     * wake signal itself (Android, on the home network). */
    fun offered(canSignal: Boolean): Boolean =
        info.on && (helper != null || info.routerUrl != null || (canSignal && info.mac != null))

    /** The helping PC, only when this phone has a key for it. */
    val helper: WakeHelper? get() = info.helper.takeIf { key != null }
}

/**
 * Wake-on-LAN from a phone (the desktop app's desktop/wake.py, rule for rule).
 * A wake signal is six 0xFF bytes and the PC's MAC 16 times, sent as a
 * broadcast: it only works from the home network. From anywhere else the
 * phone sends its wake key over Tailscale to the helping PC, which sends
 * the signal; or the owner's router has a wake page.
 */
object Wake {
    const val WAKE_PORT = 9
    private val PREFIX = "CBWAKE1 ".toByteArray()

    fun magicPacket(mac: String): ByteArray {
        val hex = mac.replace(":", "").replace("-", "")
        require(hex.length == 12 && hex.all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }) { "Not a MAC address: $mac" }
        val raw = ByteArray(6) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        return ByteArray(6) { 0xFF.toByte() } + (0 until 16).flatMap { raw.toList() }.toByteArray()
    }

    fun helperRequest(key: String): ByteArray = PREFIX + key.toByteArray()

    /** While the PC answers: what to keep, asking for this phone's own key
     * when there's a helping PC and the agent doesn't know one for it yet. */
    suspend fun keep(client: AgentClient, before: KeptWake?): KeptWake {
        val info = client.wakeInfo()
        var key = before?.key
        if (info.helper != null && !(info.hasKey && key != null)) key = client.wakeKey().key
        return KeptWake(info, key.takeIf { info.helper != null })
    }

    /** Sends what [kept] allows: the signal on this network when [signal]
     * (does nothing useful away from home, and harms nothing), and the
     * request to the helping PC. Blocking; call it off the main thread. */
    fun send(kept: KeptWake, signal: Boolean, wakePort: Int = WAKE_PORT) {
        DatagramSocket().use { socket ->
            socket.broadcast = true
            val mac = kept.info.mac
            if (signal && mac != null) {
                val packet = magicPacket(mac)
                for (target in setOfNotNull(kept.info.broadcast, "255.255.255.255")) {
                    runCatching { socket.send(DatagramPacket(packet, packet.size, InetAddress.getByName(target), wakePort)) }
                }
            }
            val helper = kept.helper
            val key = kept.key
            if (helper != null && key != null) {
                val request = helperRequest(key)
                socket.send(DatagramPacket(request, request.size, InetAddress.getByName(helper.address), helper.port))
            }
        }
    }
}
