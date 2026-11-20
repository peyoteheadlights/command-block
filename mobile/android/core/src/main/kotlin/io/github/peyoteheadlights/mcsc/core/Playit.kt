package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** A tunnel playit's program reports. */
@Serializable
data class PlayitTunnel(
    val address: String = "",
    val destination: String = "",
    val disabled: Boolean = false,
    val reason: String? = null,
)

/** One tunnel this server needs: Java on its port, Bedrock on Geyser's. */
@Serializable
data class PlayitWay(
    val kind: String = "java",
    @SerialName("local_port") val localPort: Int = 0,
    val tunnel: PlayitTunnel? = null,
)

@Serializable
data class PlayitClaim(val url: String = "", val state: String = "", val detail: String? = null)

@Serializable
data class PlayitTest(val works: Boolean? = null, val by: String = "", val address: String = "")

/** GET /api/servers/{id}/playit (agent/playit.py). Never holds playit's key. */
@Serializable
data class PlayitState(
    val on: Boolean = false,
    val phase: String? = null,
    val problem: String? = null,
    val down: Boolean = false,
    val claim: PlayitClaim? = null,
    val ways: List<PlayitWay> = emptyList(),
    val supported: Boolean = true,
    val proxy: String = "none",
    @SerialName("new_tunnel") val newTunnel: String = "",
    val site: String = "",
    val test: PlayitTest? = null,
    @SerialName("mcsrvstat_ok") val mcsrvstatOk: Boolean = false,
    @SerialName("stop_problem") val stopProblem: String? = null,
)

/**
 * playit.gg on the phones: the same words and next step as the desktop
 * app's playit part. Phones can't install it: that's on the server's PC.
 */
object Playit {
    /** What to say, and the one main button: use, link, reconnect, make or none. */
    data class Step(val lines: List<Internet.Line>, val button: String?, val checkAgain: Boolean)

    fun step(strings: Strings, s: PlayitState, owner: Boolean): Step {
        val out = mutableListOf<Internet.Line>()
        fun say(key: String, tone: Tone = Tone.NEUTRAL, vararg args: Pair<String, Any?>) {
            out += Internet.Line(strings.t(key, *args), tone)
        }
        val claim = s.claim
        if (!s.on) {
            say("playit.note")
            if (s.phase == "not_installed") {
                if (owner) say("playit.not_here")
                return Step(out, null, false)
            }
            return Step(out, if (owner) "use" else null, false)
        }
        when {
            claim?.state == "waiting" -> {
                say("playit.link")
                return Step(out, "link", true)
            }
            claim != null -> say("playit.link_${claim.state}", Tone.DANGER, "detail" to claim.detail.orEmpty())
            s.phase == "waiting_for_secret" -> say("playit.not_linked", Tone.DANGER)
            s.phase != "running" -> {
                out += Internet.Line(strings.t("playit.stopped", "phase" to s.phase, "problem" to s.problem.orEmpty()).trim(), Tone.DANGER)
                return Step(out, if (owner && s.phase != "not_installed") "reconnect" else null, false)
            }
            else -> {
                for (way in s.ways) {
                    val tunnel = way.tunnel
                    if (tunnel != null) {
                        say("playit.join_${way.kind}", Tone.SUCCESS, "address" to tunnel.address, "port" to way.localPort)
                        if (tunnel.disabled) say("playit.disabled", Tone.DANGER, "reason" to tunnel.reason.orEmpty())
                    } else {
                        val proxy = strings.t(if (s.proxy == "v2") "playit.proxy_v2" else "playit.proxy_none")
                        say("playit.make_tunnel", Tone.NEUTRAL, "kind" to strings.t("playit.kind_${way.kind}"), "port" to way.localPort, "proxy" to proxy)
                    }
                }
                s.test?.works?.let { works ->
                    val by = strings.t("address.by_mcsrvstat")
                    say(if (works) "playit.works" else "playit.fails", if (works) Tone.SUCCESS else Tone.DANGER, "address" to s.test.address, "by" to by)
                }
                val missing = s.ways.any { it.tunnel == null }
                return Step(out, if (owner && missing) "make" else null, missing)
            }
        }
        return Step(out, if (owner) "use" else null, false)
    }

    /** The Java tunnel's address, for the outside test. */
    fun javaAddress(s: PlayitState): String? =
        s.ways.firstOrNull { it.kind == "java" }?.tunnel?.takeIf { !it.disabled }?.address
}
