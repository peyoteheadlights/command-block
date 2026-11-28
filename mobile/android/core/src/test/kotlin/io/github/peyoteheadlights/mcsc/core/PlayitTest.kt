package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlayitTest {
    private val strings = Strings(Shared.strings)
    private val json = Json { ignoreUnknownKeys = true }

    private fun state(text: String) = json.decodeFromString(PlayitState.serializer(), text)

    @Test
    fun notInstalledTellsTheOwnerToInstallItOnThePc() {
        val step = Playit.step(strings, state("""{"on": false, "phase": "not_installed", "proxy": "none", "new_tunnel": "x", "site": "y"}"""), owner = true)
        assertNull(step.button)
        assertEquals(strings.t("playit.not_here"), step.lines.last().text)
    }

    @Test
    fun aMissingTunnelSaysHowToMakeItAndAFoundOneShowsItsAddress() {
        val missing = state("""{"on": true, "phase": "running", "proxy": "v2", "new_tunnel": "x", "site": "y",
            "ways": [{"kind": "java", "local_port": 25565, "tunnel": null}]}""")
        val step = Playit.step(strings, missing, owner = true)
        assertEquals("make", step.button)
        assertTrue(step.checkAgain)
        assertTrue(step.lines.last().text.contains("25565"))
        val found = state("""{"on": true, "phase": "running", "proxy": "none", "new_tunnel": "x", "site": "y",
            "ways": [{"kind": "java", "local_port": 25565, "tunnel": {"address": "cool.gl.joinmc.link", "destination": "127.0.0.1:25565"}}]}""")
        val shown = Playit.step(strings, found, owner = true)
        assertNull(shown.button)
        assertEquals(strings.t("playit.join_java", "address" to "cool.gl.joinmc.link", "port" to 25565), shown.lines[0].text)
        assertEquals("cool.gl.joinmc.link", Playit.javaAddress(found))
    }

    @Test
    fun aStoppedProgramOffersReconnect() {
        val step = Playit.step(strings, state("""{"on": true, "phase": "error", "problem": "down", "proxy": "none", "new_tunnel": "x", "site": "y"}"""), owner = true)
        assertEquals("reconnect", step.button)
        assertEquals(Tone.DANGER, step.lines[0].tone)
    }

    @Test
    fun aPlainBedrockServerAsksForPlayitsBoxFirst() {
        val bedrock = state("""{"on": true, "phase": "running", "proxy": "none", "new_tunnel": "x", "site": "y",
            "plain_bedrock": true, "waterdog": null, "ways": []}""")
        val step = Playit.step(strings, bedrock, owner = true)
        assertEquals("waterdog", step.button)
        assertEquals(strings.t("playit.bedrock_note"), step.lines.single().text)
        assertNull(Playit.step(strings, bedrock, owner = false).button)
    }

    @Test
    fun behindWaterdogTheNetherNetTunnelAndTheAllowListShow() {
        val set = state("""{"on": true, "phase": "running", "proxy": "none", "new_tunnel": "x", "site": "y",
            "plain_bedrock": true,
            "waterdog": {"state": "registered", "listen_port": 19132, "media_port": 19132, "server_port": 19142, "list_on": true},
            "ways": [{"kind": "nethernet", "local_port": 19132, "tunnel": {"address": "a.tun.ply.gg", "destination": "127.0.0.1:19132"}}]}""")
        val step = Playit.step(strings, set, owner = true)
        assertNull(step.button)
        assertEquals(strings.t("playit.join_nethernet", "address" to "a.tun.ply.gg", "port" to 19132), step.lines[0].text)
        assertEquals(strings.t("playit.waterdog_registered", "port" to 19142), step.lines[1].text)
        assertEquals(strings.t("playit.waterdog_list_on"), step.lines[2].text)
        assertNull(Playit.javaAddress(set)) // mcsrvstat.us can't test NetherNet
    }
}
