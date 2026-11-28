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

/** One tunnel this server needs: Java on its port, Bedrock on Geyser's, NetherNet on WaterdogPE's. */
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

/** WaterdogPE in front of a plain Bedrock server (agent/waterdog.py). Never the tunnel's token. */
@Serializable
data class WaterdogState(
    val state: String = "stopped",
    @SerialName("listen_port") val listenPort: Int = 0,
    @SerialName("media_port") val mediaPort: Int = 0,
    @SerialName("server_port") val serverPort: Int = 0,
    @SerialName("list_on") val listOn: Boolean = false,
)

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
    @SerialName("plain_bedrock") val plainBedrock: Boolean = false,
    val waterdog: WaterdogState? = null,
    @SerialName("internet_off") val internetOff: Boolean = false,
)

/**
 * playit.gg on the phones: the same words and next step as the desktop
 * app's playit part. Phones can't install it: that's on the server's PC.
 */
object Playit {
    /** What to say, and the one main button: use, link, reconnect, make, waterdog (paste playit's box) or none. */
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
            s.plainBedrock && s.waterdog == null -> {
                say("playit.bedrock_note")
                return Step(out, if (owner) "waterdog" else null, false)
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
                s.waterdog?.let { w ->
                    say("playit.waterdog_${w.state}", if (w.state == "stopped") Tone.DANGER else Tone.NEUTRAL, "port" to w.serverPort)
                    say(if (w.listOn) "playit.waterdog_list_on" else "playit.waterdog_list_off")
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
