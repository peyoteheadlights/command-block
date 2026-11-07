package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** One step of a Diagnose run: [ok] true worked, false failed, null not
 * checked ([detail] says why). */
@Serializable
data class DiagnoseLine(val group: String, val what: String, val ok: Boolean? = null, val detail: String? = null)

/** A change Diagnose offers; a risky one ([safe] false) only by Apply all. */
@Serializable
data class DiagnoseChange(val key: String, val title: String, val why: String = "", val safe: Boolean = false)

/** What happened to a change: kept, put_back, no_help, failed or denied. */
@Serializable
data class DiagnoseDone(val title: String, val outcome: String, val detail: String = "")

/** A known problem the run recognised, and the title of the change offered for it. */
@Serializable
data class DiagnoseKnown(val id: String = "", val say: String, val link: String? = null, val change: String? = null)

/** GET /server/diagnose: the last run, or the one running (its lines so far). */
@Serializable
data class Diagnosis(
    val running: Boolean = false,
    val lines: List<DiagnoseLine> = emptyList(),
    @SerialName("sign_in") val signIn: Boolean = false,
    val changes: List<DiagnoseChange> = emptyList(),
    val done: List<DiagnoseDone> = emptyList(),
    val steps: List<String> = emptyList(),
    @SerialName("sign_in_mods") val signInMods: Boolean = false,
    val known: List<DiagnoseKnown> = emptyList(),
)

/** POST /server/diagnose: started, or a question to ask first. */
@Serializable
data class DiagnoseStarted(
    @SerialName("job_id") val jobId: String? = null,
    val ask: String? = null,
    @SerialName("ask_mods") val askMods: String? = null,
)

/** What the Diagnose server screen decides, the same as the desktop window
 * (desktop/diagnose.py): the agent does the work and keeps the list. */
object Diagnose {
    /** Apply all, Apply safe and Deny all, as the agent names them. */
    val PICKS = listOf("all", "safe", "none")

    fun mark(ok: Boolean?): String = when (ok) {
        true -> "✓"
        false -> "✗"
        null -> "?"
    }

    fun doneMark(done: DiagnoseDone): String = if (done.outcome == "kept") "✓" else "–"

    /** The lines under their headings, a new heading each time the group changes. */
    fun groups(lines: List<DiagnoseLine>): List<Pair<String, List<DiagnoseLine>>> {
        val out = mutableListOf<Pair<String, MutableList<DiagnoseLine>>>()
        for (line in lines) {
            if (out.lastOrNull()?.first != line.group) out.add(line.group to mutableListOf())
            out.last().second.add(line)
        }
        return out
    }

    /** The changes waiting for a pick: none while it still runs. */
    fun waiting(result: Diagnosis): List<DiagnoseChange> = if (result.running) emptyList() else result.changes

    fun statusKey(result: Diagnosis?): String = when {
        result == null -> "diagnose.starting"
        result.running -> "diagnose.running"
        waiting(result).isNotEmpty() -> "diagnose.offer"
        else -> "diagnose.done"
    }

    fun startBody(goAhead: Boolean, mods: Boolean?): JsonObject = buildJsonObject {
        put("go_ahead", goAhead)
        mods?.let { put("mods", it) }
    }

    fun pickBody(pick: String): JsonObject {
        require(pick in PICKS) { "unknown pick $pick" }
        return buildJsonObject { put("pick", pick) }
    }
}
