package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.AgentClient
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.ChannelRow
import io.github.peyoteheadlights.mcsc.core.Community
import io.github.peyoteheadlights.mcsc.core.DiscordGuild
import io.github.peyoteheadlights.mcsc.core.Display
import io.github.peyoteheadlights.mcsc.core.DiscordStatus
import io.github.peyoteheadlights.mcsc.core.LevelRow
import io.github.peyoteheadlights.mcsc.core.Strings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Settings > Discord bot (owner only): the token, inviting the bot, and each
 * Discord server's setup, channels, reminders and Levels, like the desktop app. */
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

    /** [done] empties the box once the PC has it. */
    fun saveToken(token: String, strings: Strings, done: () -> Unit) = work("token", strings) {
        status = Read(it.setDiscordToken(token))
        done()
        strings.t("discord.token_saved")
    }

    fun removeToken(strings: Strings) = work("token", strings) {
        status = Read(it.removeDiscordToken())
        strings.t("discord.token_removed")
    }

    fun setUp(guildId: String, servers: List<String>, strings: Strings) = work("setup:$guildId", strings) {
        val done = it.setUpDiscord(guildId, servers)
        status = Read(done.status)
        if (done.made.isEmpty()) strings.t("discord.set_up_nothing") else strings.t("discord.set_up_done", "made" to done.made.joinToString(", "))
    }

    fun tie(guildId: String, row: ChannelRow, channelId: String?, strings: Strings) = work("channel:$guildId", strings) {
        status = Read(it.tieChannel(guildId, Community.channelBody(row, channelId)))
        strings.t("discord.channel_saved")
    }

    fun reminders(guildId: String, how: String, strings: Strings) = work("reminders:$guildId", strings) {
        status = Read(it.setReminders(guildId, how))
        strings.t("settings.saved")
    }

    fun levels(guildId: String, body: JsonObject, strings: Strings) = work("levels:$guildId", strings) {
        it.setLevels(guildId, body)
        status = Read(it.discord())
        strings.t("discord.levels_saved")
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
 * Discord switched off, the bot's token and invite, and each Discord
 * server's checks, setup, channels, reminders and Levels. What each Discord
 * role may do stays in the desktop app.
 */
@Composable
fun DiscordScreen(vm: AppViewModel, onBack: () -> Unit) {
    val pages = vm.discord
    val colors = LocalColors.current
    val strings = vm.strings
    val now = rememberNow()
    val context = LocalContext.current
    var removing by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { pages.load() }
    val status = pages.status.value
    val servers = vm.servers.list.map { it.id to it.name }

    if (removing) {
        Confirm(
            title = t("discord.token_remove"),
            body = t("discord.token_remove_body"),
            confirm = t("discord.token_remove"),
            danger = true,
            onConfirm = { removing = false; pages.removeToken(strings) },
            onDismiss = { removing = false },
        )
    }
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
                item { TokenCard(pages, status, strings, onRemove = { removing = true }, open = { openLink(context, it) }) }
                if (status.state == "connected") {
                    status.inviteUrl?.let { url ->
                        item {
                            SectionTitle(t("discord.invite_title"))
                            Card {
                                for (i in 1..3) Note(t("discord.server_steps.$i"))
                                Item(t("discord.invite"), onClick = { openLink(context, url) })
                            }
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
                    }
                    GuildSetup(pages, guild, servers, strings)
                    if (guild.servers.isNotEmpty() && guild.report?.problems != null) {
                        Card {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                                Text(t("discord.channels"), color = colors.text)
                                for (row in Community.channelRows(strings, guild, servers)) ChannelPicker(pages, guild, row, strings)
                                Note(t("discord.console_hint"), color = colors.text3)
                            }
                        }
                        Card {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                                Picker(
                                    t("discord.reminders"),
                                    t("discord.reminders.${guild.reminders}"),
                                    Community.REMINDERS.map { it to t("discord.reminders.$it") },
                                ) { if (pages.busy == null) pages.reminders(guild.id, it, strings) }
                                Text(t("discord.reminders_hint"), color = colors.text3)
                            }
                            Tick(
                                t("discord.remove_on_leave"),
                                checked = guild.removeOnLeave,
                                subtitle = t("discord.remove_on_leave_hint"),
                                enabled = pages.busy == null,
                            ) { pages.removeOnLeave(guild.id, it, strings) }
                        }
                        LevelSettings(pages, guild, strings)
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
            }
        }
    }
}

/** The bot's token: the developer portal's steps until it works, then Save or Remove. */
@Composable
private fun TokenCard(pages: DiscordPages, status: DiscordStatus, strings: Strings, onRemove: () -> Unit, open: (String) -> Unit) {
    var token by remember { mutableStateOf("") }
    SectionTitle(t("discord.token_title"))
    Card {
        if (!status.tokenSet || status.state == "token_rejected") {
            for (i in 1..6) Note(t("discord.steps.$i"))
            Item(t("discord.portal"), onClick = { open(Community.DEVELOPER_PORTAL) })
        }
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            PasswordField(token, { token = it.take(120) }, t("discord.token_title"), hint = t("mobile.discord.token_hint"))
            Presses(
                listOfNotNull(
                    Press(t("discord.token_save"), { pages.saveToken(token, strings) { token = "" } }, enabled = pages.busy == null && token.isNotBlank()),
                    if (status.tokenSet) Press(t("discord.token_remove"), onRemove, danger = true, enabled = pages.busy == null) else null,
                ),
            )
        }
    }
}

/** Which Minecraft servers this Discord server is for, and the one button. */
@Composable
private fun GuildSetup(pages: DiscordPages, guild: DiscordGuild, servers: List<Pair<String, String>>, strings: Strings) {
    var ticked by remember(guild.id, guild.servers) { mutableStateOf(Community.setupServers(guild, servers.map { it.first }).toSet()) }
    Card {
        Note(t("discord.for_servers"))
        for ((id, name) in servers) {
            Tick(name, checked = id in ticked, enabled = pages.busy == null) { ticked = if (it) ticked + id else ticked - id }
        }
        Item(
            t("discord.set_up"),
            subtitle = t("discord.set_up_hint"),
            onClick = if (pages.busy == null && ticked.isNotEmpty()) ({ pages.setUp(guild.id, servers.map { it.first }.filter { it in ticked }, strings) }) else null,
        )
    }
}

@Composable
private fun ChannelPicker(pages: DiscordPages, guild: DiscordGuild, row: ChannelRow, strings: Strings) {
    val channels = guild.report?.channels.orEmpty()
    val none = "" to t(Community.noChannel(row))
    Picker(
        row.label,
        channels.firstOrNull { it.id == row.channelId }?.let { "#${it.name}" } ?: none.second,
        listOf(none) + channels.map { it.id to "#${it.name}" },
    ) { if (pages.busy == null) pages.tie(guild.id, row, it.ifEmpty { null }, strings) }
}

/** Level-ups on or off, the weekly leaderboard's day, and which Discord role goes with which Level. */
@Composable
private fun LevelSettings(pages: DiscordPages, guild: DiscordGuild, strings: Strings) {
    val colors = LocalColors.current
    val saved = guild.levels
    var announce by remember(guild.id, saved) { mutableStateOf(saved.announce) }
    var day by remember(guild.id, saved) { mutableStateOf(saved.leaderboardDay) }
    var rows by remember(guild.id, saved) { mutableStateOf(saved.roles.map { LevelRow(it.level, it.roleId) }) }
    val roles = Community.levelRoleChoices(guild)
    val days = java.time.DayOfWeek.entries.map { it.getDisplayName(java.time.format.TextStyle.FULL, java.util.Locale.getDefault()) }
    SectionTitle(t("discord.levels"))
    Card {
        Note(t("discord.levels_hint"), color = colors.text3)
        Tick(t("discord.level_ups"), checked = announce) { announce = it }
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Picker(
                t("discord.leaderboard"),
                day?.let { days[it] } ?: t("discord.leaderboard_off"),
                listOf("" to t("discord.leaderboard_off")) + days.mapIndexed { i, name -> "$i" to name },
            ) { day = it.toIntOrNull() }
            Text(t("discord.leaderboard_hint"), color = colors.text3)
            rows.forEachIndexed { i, row ->
                LevelNumber(t("discord.level_role"), row.level) { n -> rows = rows.toMutableList().also { it[i] = row.copy(level = n) } }
                Picker(
                    "${t("discord.level_role")} ${row.level}",
                    roles.firstOrNull { it.id == row.roleId }?.name ?: t("value.unknown"),
                    roles.map { it.id to it.name },
                ) { id -> rows = rows.toMutableList().also { it[i] = row.copy(roleId = id) } }
                Presses(listOf(Press(t("discord.level_remove"), { rows = rows.filterIndexed { j, _ -> j != i } })))
            }
            Presses(
                listOf(
                    Press(t("discord.level_add"), { rows = rows + LevelRow((rows.maxOfOrNull { it.level } ?: 0) + 5, null) }),
                    Press(t("discord.level_save"), { pages.levels(guild.id, Community.levelsBody(guild, announce, day, rows), strings) }, enabled = pages.busy == null),
                ),
            )
        }
    }
}

/** A Level from 1 to 1000; an empty box reads as 1. */
@Composable
private fun LevelNumber(label: String, value: Int, onChange: (Int) -> Unit) {
    var typed by remember { mutableStateOf(value.toString()) }
    // Only a new value from outside (a row removed above) replaces the text,
    // so the box can be emptied while typing.
    if ((typed.toIntOrNull() ?: 1).coerceIn(1, 1000) != value) typed = value.toString()
    OutlinedTextField(
        value = typed,
        onValueChange = { text ->
            typed = text.filter(Char::isDigit).take(4)
            onChange((typed.toIntOrNull() ?: 1).coerceIn(1, 1000))
        },
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth(),
    )
}
