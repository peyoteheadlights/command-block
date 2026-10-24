package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import io.github.peyoteheadlights.mcsc.core.AgentClient
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.Community
import io.github.peyoteheadlights.mcsc.core.Display
import io.github.peyoteheadlights.mcsc.core.DiscordStatus
import io.github.peyoteheadlights.mcsc.core.Strings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Settings > Discord bot (owner only): what the PC measured, and its two switches. */
class DiscordPages(
    private val scope: CoroutineScope,
    private val client: () -> AgentClient?,
    private val noted: (AgentException) -> Unit,
) {
    var status: Read<DiscordStatus> by mutableStateOf(Read())
        private set
    var busy: String? by mutableStateOf(null)
        private set
    var said: Said? by mutableStateOf(null)
        private set

    fun load() {
        val c = client() ?: return
        status = status.copy(loading = true)
        scope.launch {
            status = try { Read(c.discord()) } catch (e: AgentException) {
                noted(e)
                status.copy(problem = Message.of(e), loading = false)
            }
        }
    }

    /** Asks Discord again: the token, the switches, each Discord server's roles and channels. */
    fun check(strings: Strings) = work("check", strings) {
        status = Read(it.checkDiscord())
        strings.t("discord.checked")
    }

    fun removeOnLeave(guildId: String, on: Boolean, strings: Strings) = work("leave:$guildId", strings) {
        status = Read(it.setRemoveOnLeave(guildId, on))
        strings.t("settings.saved")
    }

    /** The webhook too, while the bot posts the alerts (each one comes twice). */
    fun webhookToo(on: Boolean, strings: Strings) = work("webhook", strings) {
        it.savePcSettings(buildJsonObject { put("notifications.webhook_with_bot", on) })
        status = Read(it.discord())
        strings.t("settings.saved")
    }

    /** One button at a time; [call] gives the words to say once it worked. */
    private fun work(name: String, strings: Strings, call: suspend (AgentClient) -> String) {
        val c = client() ?: return
        if (busy != null) return
        busy = name
        said = null
        scope.launch {
            said = try {
                Said(call(c), good = true)
            } catch (e: AgentException) {
                noted(e)
                Said(Message.of(e).text(strings), good = false)
            } finally {
                busy = null
            }
        }
    }
}

/**
 * Whether the bot is connected (measured by the PC, never assumed), what
 * Discord switched off, and each Discord server's checks. Setting it up
 * (the token, inviting it, channels, Discord roles) stays in the desktop
 * app, where pasting a token and picking channels is easier.
 */
@Composable
fun DiscordScreen(vm: AppViewModel, onBack: () -> Unit) {
    val pages = vm.discord
    val colors = LocalColors.current
    val strings = vm.strings
    val now = rememberNow()
    LaunchedEffect(Unit) { pages.load() }
    val status = pages.status.value

    Scaffold(containerColor = colors.sheet, topBar = { Bar(t("discord.title"), onBack) }) { padding ->
        LazyColumn(contentPadding = ScreenPadding, modifier = Modifier.padding(padding).fillMaxSize()) {
            pages.status.problem?.let { item { ProblemNote(it, onRetry = pages::load) } }
            item { SaidNote(pages.said) }
            if (status == null) {
                if (pages.status.problem == null) item { Note(t("mobile.common.loading")) }
            } else {
                item { SectionTitle(t("discord.bot")) }
                item {
                    Card {
                        val connected = status.state == "connected"
                        Item(
                            Community.botState(strings, status),
                            subtitle = status.connectedAt?.takeIf { connected }?.let { t("discord.since", "time" to Display.ago(strings, it, now)) }
                                ?: status.detail.takeIf { it.isNotBlank() },
                        )
                        for (feature in status.featuresOff) Note(t("discord.off.$feature"))
                        if (strings.technical && connected) {
                            for ((switch, on) in status.switches) {
                                val words = when (on) {
                                    null -> "value.unknown"
                                    true -> "discord.switch_on"
                                    false -> "discord.switch_off"
                                }
                                Note("${t("discord.switch.$switch")}: ${t(words)}", color = colors.text3)
                            }
                        }
                        Note(t("discord.pc_on"), color = colors.text3)
                        if (connected) {
                            Tick(t("discord.webhook_with_bot"), checked = status.webhookWithBot, enabled = pages.busy == null) {
                                pages.webhookToo(it, strings)
                            }
                            Note(t("discord.alerts_hint"), color = colors.text3)
                        }
                    }
                }
                items(status.guilds, key = { it.id }) { guild ->
                    SectionTitle(guild.name)
                    Card {
                        val problems = Community.problems(strings, guild)
                        when {
                            problems == null -> Item(t("discord.checking"))
                            problems.isEmpty() -> Item("✓ " + t("discord.all_good"))
                            else -> problems.forEach { Note("✗ $it", color = colors.danger) }
                        }
                        Tick(
                            t("discord.remove_on_leave"),
                            checked = guild.removeOnLeave,
                            subtitle = t("discord.remove_on_leave_hint"),
                            enabled = pages.busy == null,
                        ) { pages.removeOnLeave(guild.id, it, strings) }
                    }
                }
                if (status.tokenSet) {
                    item {
                        Card {
                            Item(
                                t(if (pages.busy == "check") "discord.checking" else "discord.check_again"),
                                onClick = if (pages.busy == null) ({ pages.check(strings) }) else null,
                            )
                        }
                    }
                }
                item { Note(t("mobile.discord.on_pc"), color = colors.text3) }
            }
        }
    }
}
