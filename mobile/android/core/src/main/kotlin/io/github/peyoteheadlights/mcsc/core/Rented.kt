package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Memory, CPU and disk as the host's panel last said; null when it didn't. */
@Serializable
data class RentedResources(
    @SerialName("memory_bytes") val memoryBytes: Long? = null,
    @SerialName("cpu_percent") val cpuPercent: Double? = null,
    @SerialName("disk_bytes") val diskBytes: Long? = null,
)

/** One rented server on a host's Pterodactyl panel (agent/rented.py). */
@Serializable
data class RentedServer(
    val id: String,
    val name: String = "",
    val host: String = "",
    @SerialName("panel_id") val panelId: String = "",
    /** ONLINE, OFFLINE, STARTING, STOPPING or UNKNOWN (the panel didn't answer). */
    val state: String = States.UNKNOWN,
    @SerialName("state_reason") val stateReason: String? = null,
    val resources: RentedResources? = null,
    @SerialName("checked_at") val checkedAt: Double? = null,
    /** off, status or control. */
    val discord: String = "off",
)

/** A connected panel, for Remove key (the owner's only; never the key). */
@Serializable
data class RentedPanel(val id: String, val host: String = "", val address: String = "")

@Serializable
data class RentedList(
    val servers: List<RentedServer> = emptyList(),
    val panels: List<RentedPanel> = emptyList(),
)

/** One server the key can see on the panel, before anything is saved. */
@Serializable
data class PanelServer(val identifier: String, val name: String = "")

@Serializable
data class PanelCheck(
    val host: String = "",
    val servers: List<PanelServer> = emptyList(),
    /** The main account's key: it can do everything that account can. */
    @SerialName("full_account") val fullAccount: Boolean = false,
)

@Serializable
data class RentedAdded(val added: List<RentedServer> = emptyList())

@Serializable
data class RentedDone(val result: String = "", val detail: String = "")

@Serializable
data class RentedLine(val n: Int, val text: String = "")

@Serializable
data class RentedConsole(
    val lines: List<RentedLine> = emptyList(),
    val last: Int = 0,
    val problem: String? = null,
)

@Serializable
data class RentedBackup(
    val id: String? = null,
    val name: String? = null,
    val bytes: Long? = null,
    @SerialName("completed_at") val completedAt: String? = null,
    /** Null while the host is still making it. */
    val succeeded: Boolean? = null,
)

@Serializable
data class RentedBackups(
    val backups: List<RentedBackup> = emptyList(),
    val limit: Int? = null,
)

/** What the rented server screens show, worked out without Android. */
object Rented {
    /** Aternos forbids other apps controlling its servers: only its site opens (shared/links.json). */
    const val ATERNOS = "https://aternos.org/servers/"

    /** Kill and the rest, as the desktop app has them. */
    val POWER = listOf("start", "stop", "restart")
    val DISCORD = listOf("off", "status", "control")

    /** The agent keeps this many console lines (agent/rented.py). */
    const val CONSOLE_LINES = 500

    /** What needs this PC, greyed out on a rented server (desktop/rented.py). */
    val NEEDS_THIS_PC = listOf("backups_here", "wake", "join_protection", "change_type", "addons")
        .map { "desktop.rented.local.$it" }

    /** Connecting a panel and Remove key are the owner's. */
    fun mayConnect(me: Me?): Boolean = me?.isOwner == true

    /** Start only when it's off; the rest only when it isn't; nothing while Unknown. */
    fun enabled(state: String, action: String): Boolean = when {
        state == States.UNKNOWN -> false
        action == "start" -> state == States.OFFLINE
        else -> state != States.OFFLINE
    }

    /** "Unknown: the panel refused the key", never the old state. */
    fun stateLine(strings: Strings, server: RentedServer): String {
        val words = strings.t(Display.state(server.state).key)
        val reason = server.stateReason
        return if (server.state == States.UNKNOWN && !reason.isNullOrBlank()) "$words: $reason" else words
    }

    fun usage(strings: Strings, r: RentedResources?): String = strings.t(
        "desktop.rented.usage",
        "memory" to Display.bytes(strings, r?.memoryBytes),
        "cpu" to (r?.cpuPercent?.let { "${Math.round(it)} %" } ?: strings.t("value.unknown")),
        "disk" to Display.bytes(strings, r?.diskBytes),
    )

    fun backupLine(strings: Strings, b: RentedBackup): String {
        val name = b.name.orEmpty()
        val ok = b.succeeded ?: return strings.t("desktop.rented.backup_making", "name" to name)
        val words = strings.t(
            "desktop.rented.backup_row",
            "name" to name,
            "size" to Display.bytes(strings, b.bytes),
            "when" to b.completedAt.orEmpty().take(16).replace("T", " "),
        )
        return if (ok) words else words + " " + strings.t("desktop.rented.backup_failed")
    }
}
