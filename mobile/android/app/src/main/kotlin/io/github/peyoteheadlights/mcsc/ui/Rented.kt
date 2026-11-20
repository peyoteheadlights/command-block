package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.AgentClient
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.PanelCheck
import io.github.peyoteheadlights.mcsc.core.Permissions
import io.github.peyoteheadlights.mcsc.core.Rented
import io.github.peyoteheadlights.mcsc.core.RentedBackups
import io.github.peyoteheadlights.mcsc.core.RentedLine
import io.github.peyoteheadlights.mcsc.core.RentedList
import io.github.peyoteheadlights.mcsc.core.RentedServer
import io.github.peyoteheadlights.mcsc.core.States
import io.github.peyoteheadlights.mcsc.core.Strings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Rented servers on a host's Pterodactyl panel, like the desktop app
 * (desktop/rented.py): the list on All servers, one server's screen, and
 * Connect a rented server (owner only). The agent holds the key and talks
 * to the panel; the phone only calls /api/rented.
 */
class RentedPages(
    private val scope: CoroutineScope,
    private val client: () -> AgentClient?,
    private val noted: (AgentException) -> Unit,
) {
    var list: Read<RentedList> by mutableStateOf(Read())
        private set
    var busy: String? by mutableStateOf(null)
        private set
    var said: Said? by mutableStateOf(null)
        private set

    // The open server's console and backups.
    var console: List<RentedLine> by mutableStateOf(emptyList())
        private set
    var consoleProblem: String? by mutableStateOf(null)
        private set
    var backups: Read<RentedBackups> by mutableStateOf(Read())
        private set
    private var openId: String? = null
    private var lastLine = 0

    /** What "Find its servers" found, before anything is saved. */
    var found: PanelCheck? by mutableStateOf(null)
        private set

    fun server(id: String): RentedServer? = list.value?.servers?.firstOrNull { it.id == id }

    fun load() {
        val c = client() ?: return
        list = list.copy(loading = true)
        scope.launch {
            list = try { Read(c.rented()) } catch (e: AgentException) {
                noted(e)
                list.copy(problem = Message.of(e), loading = false)
            }
        }
    }

    /** A fresh console and backups list when another server opens. */
    fun open(id: String) {
        if (id != openId) {
            openId = id
            console = emptyList()
            consoleProblem = null
            lastLine = 0
            backups = Read()
            said = null
        }
        loadBackups(id)
    }

    /** The agent asks the panel every 20 s; this reads its answer. */
    fun refresh(id: String) {
        val c = client() ?: return
        scope.launch {
            try {
                replace(c.rentedStatus(id))
            } catch (e: AgentException) {
                noted(e)
            }
        }
    }

    private fun replace(row: RentedServer) {
        val current = list.value ?: return
        list = list.copy(value = current.copy(servers = current.servers.map { if (it.id == row.id) row else it }))
    }

    fun readConsole(id: String) {
        val c = client() ?: return
        scope.launch {
            try {
                val got = c.rentedConsole(id, lastLine)
                if (id != openId) return@launch
                console = (console + got.lines).takeLast(Rented.CONSOLE_LINES)
                lastLine = got.last
                consoleProblem = got.problem
            } catch (e: AgentException) {
                noted(e)
            }
        }
    }

    fun loadBackups(id: String) {
        val c = client() ?: return
        backups = backups.copy(loading = true)
        scope.launch {
            backups = try { Read(c.rentedBackups(id)) } catch (e: AgentException) {
                noted(e)
                backups.copy(problem = Message.of(e), loading = false)
            }
        }
    }

    fun power(id: String, signal: String, strings: Strings, confirm: Boolean = false) = work(signal, strings) {
        val done = it.rentedPower(id, signal, confirm)
        refresh(id)
        done.detail
    }

    fun send(id: String, command: String, strings: Strings, done: () -> Unit) = work("command", strings) {
        val sent = it.rentedCommand(id, command)
        done()
        sent.detail
    }

    fun makeBackup(id: String, strings: Strings) = work("backup", strings) {
        val done = it.makeRentedBackup(id)
        loadBackups(id)
        done.detail
    }

    fun discord(id: String, choice: String, strings: Strings) = work("discord", strings) {
        replace(it.rentedDiscord(id, choice))
        strings.t("settings.saved")
    }

    fun removeKey(panelId: String, strings: Strings, done: () -> Unit) = work("remove", strings) {
        it.removePanelKey(panelId)
        load()
        done()
        strings.t("mobile.rented.key_removed")
    }

    fun forget() {
        found = null
        said = null
    }

    fun check(address: String, key: String, strings: Strings) = work("check", strings) {
        found = it.checkPanel(address, key)
        ""
    }

    fun connect(address: String, key: String, picked: List<String>, strings: Strings, done: () -> Unit) = work("add", strings) {
        val added = it.connectPanel(address, key, picked)
        load()
        found = null
        done()
        strings.tn("mobile.rented.added", added.added.size)
    }

    /** One button at a time; [call] gives the words to say once it worked. */
    private fun work(name: String, strings: Strings, call: suspend (AgentClient) -> String) {
        val c = client() ?: return
        if (busy != null) return
        busy = name
        said = null
        scope.launch {
            said = try {
                call(c).takeIf { it.isNotBlank() }?.let { Said(it, good = true) }
            } catch (e: AgentException) {
                noted(e)
                Said(Message.of(e).text(strings), good = false)
            } finally {
                busy = null
            }
        }
    }
}

/** "Rented servers" on All servers: each with its host and state, and Connect for the owner. */
fun LazyListScope.rentedSection(vm: AppViewModel, onOpen: (String) -> Unit, onConnect: () -> Unit) {
    val rows = vm.rented.list.value?.servers.orEmpty()
    val owner = Rented.mayConnect(vm.me)
    if (rows.isEmpty() && !owner) return
    item(key = "rented") { SectionTitle(t("desktop.rented.heading_list")) }
    items(rows, key = { "rented:${it.id}" }) { row ->
        Card {
            Item(
                row.name,
                subtitle = listOfNotNull(row.host, row.stateReason?.takeIf { row.state == States.UNKNOWN }).joinToString(" · "),
                trailing = { StateText(row.state) },
                onClick = { onOpen(row.id) },
            )
        }
    }
    if (owner) item(key = "rented:connect") { Card { Item(t("desktop.rented.connect"), onClick = onConnect) } }
}

/** One rented server: status, Start/Stop/Restart/Kill, console, the host's backups. */
@Composable
fun RentedScreen(vm: AppViewModel, id: String, onBack: () -> Unit) {
    val pages = vm.rented
    val strings = vm.strings
    val colors = LocalColors.current
    val me = vm.me?.forServer(id)
    val may = { p: String -> me?.can(p) == true }
    val owner = Rented.mayConnect(vm.me)
    var asking by remember { mutableStateOf<String?>(null) }
    var command by remember { mutableStateOf("") }
    LaunchedEffect(id) {
        if (pages.list.value == null) pages.load()
        pages.open(id)
        var ticks = 0
        // Only while this screen is open: console every 2 s, status every 10 s.
        while (true) {
            if (may(Permissions.CONSOLE_READ)) pages.readConsole(id)
            if (ticks % 5 == 0) pages.refresh(id)
            ticks++
            delay(2_000)
        }
    }
    val row = pages.server(id)
    when (asking) {
        "kill" -> Confirm(
            t("desktop.rented.kill_title", "name" to (row?.name ?: id)), t("desktop.rented.kill_warning"), t("desktop.rented.kill"),
            danger = true,
            onConfirm = { asking = null; pages.power(id, "kill", strings, confirm = true) },
            onDismiss = { asking = null },
        )
        "remove" -> row?.let {
            Confirm(
                t("desktop.rented.remove_key_title", "host" to it.host), t("desktop.rented.remove_key_words"), t("desktop.rented.remove_key"),
                danger = true,
                onConfirm = { asking = null; pages.removeKey(it.panelId, strings, onBack) },
                onDismiss = { asking = null },
            )
        }
    }
    Scaffold(containerColor = colors.sheet, topBar = { Bar(row?.name ?: t("desktop.rented.heading_list"), onBack) }) { padding ->
        LazyColumn(contentPadding = ScreenPadding, verticalArrangement = Gap, modifier = Modifier.padding(padding).fillMaxSize()) {
            pages.list.problem?.let { item { ProblemNote(it, onRetry = pages::load) } }
            if (row == null) {
                item { Note(t(if (pages.list.loading) "mobile.common.loading" else "mobile.rented.gone")) }
                return@LazyColumn
            }
            item {
                Card {
                    Item(t("desktop.rented.heading", "name" to row.name, "host" to row.host), subtitle = Rented.stateLine(strings, row))
                    Note(Rented.usage(strings, row.resources))
                    if (may(Permissions.SERVER_CONTROL)) {
                        Presses(
                            Rented.POWER.map { action ->
                                Press(t("action.$action"), { pages.power(id, action, strings) }, enabled = pages.busy == null && Rented.enabled(row.state, action))
                            } + Press(t("desktop.rented.kill"), { asking = "kill" }, danger = true, enabled = pages.busy == null && Rented.enabled(row.state, "kill")),
                        )
                    }
                }
            }
            item { SaidNote(pages.said) }
            if (may(Permissions.CONSOLE_READ)) {
                item { SectionTitle(t("desktop.rented.console")) }
                item {
                    Card {
                        pages.consoleProblem?.let { Note(it, color = colors.danger) }
                        if (pages.console.isEmpty()) Note(t("mobile.common.loading"))
                        for (line in pages.console.takeLast(SHOWN_LINES)) {
                            Text(line.text, fontFamily = FontFamily.Monospace, color = colors.text, modifier = Modifier.padding(horizontal = 16.dp))
                        }
                        if (may(Permissions.CONSOLE_SEND)) {
                            val sendIt = { if (command.isNotBlank()) pages.send(id, command, strings) { command = "" } }
                            OutlinedTextField(
                                value = command,
                                onValueChange = { command = it.take(1000) },
                                label = { Text(t("desktop.rented.command")) },
                                singleLine = true,
                                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                                keyboardActions = KeyboardActions(onSend = { sendIt() }),
                                modifier = Modifier.fillMaxWidth().padding(16.dp),
                            )
                            Presses(listOf(Press(t("desktop.rented.send"), sendIt, enabled = pages.busy == null && command.isNotBlank())))
                        }
                    }
                }
            }
            item { SectionTitle(t("desktop.rented.backups")) }
            item {
                Card {
                    val backups = pages.backups
                    backups.problem?.let { Note(it.text(strings), color = colors.danger) }
                    backups.value?.let { b ->
                        if (b.backups.isEmpty()) Note(t("desktop.rented.no_backups"))
                        for (one in b.backups) Item(Rented.backupLine(strings, one))
                        b.limit?.let { Note(t("desktop.rented.backup_limit", "n" to it.toString()), color = colors.text3) }
                    } ?: run { if (backups.problem == null) Note(t("mobile.common.loading")) }
                    if (may(Permissions.BACKUPS_CREATE)) {
                        Presses(listOf(Press(t("desktop.rented.make_backup"), { pages.makeBackup(id, strings) }, enabled = pages.busy == null)))
                    }
                }
            }
            item { SectionTitle(t("desktop.rented.needs_pc")) }
            item {
                Card {
                    for (key in Rented.NEEDS_THIS_PC) Note(t(key), color = colors.text3)
                    Note(t("desktop.rented.later"), color = colors.text3)
                }
            }
            if (owner) {
                item { SectionTitle(t("desktop.rented.discord")) }
                item {
                    Card {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Picker(
                                t("desktop.rented.discord"),
                                t("desktop.rented.discord.${row.discord}"),
                                Rented.DISCORD.map { it to vm.strings.t("desktop.rented.discord.$it") },
                            ) { if (pages.busy == null) pages.discord(id, it, strings) }
                        }
                        Presses(listOf(Press(t("desktop.rented.remove_key"), { asking = "remove" }, danger = true, enabled = pages.busy == null)))
                    }
                }
            }
        }
    }
}

/** The phone shows at most this many console lines; the agent keeps 500. */
private const val SHOWN_LINES = 200

/** Connect a rented server: panel address and client API key, the owner's tick, then pick its servers. */
@Composable
fun ConnectPanelScreen(vm: AppViewModel, onBack: () -> Unit) {
    val pages = vm.rented
    val strings = vm.strings
    val colors = LocalColors.current
    val context = LocalContext.current
    var address by remember { mutableStateOf("") }
    var key by remember { mutableStateOf("") }
    var allow by remember { mutableStateOf(false) }
    var picked by remember { mutableStateOf(setOf<String>()) }
    LaunchedEffect(Unit) { pages.forget() }
    val found = pages.found
    Scaffold(containerColor = colors.sheet, topBar = { Bar(t("desktop.rented.connect"), onBack) }) { padding ->
        LazyColumn(contentPadding = ScreenPadding, verticalArrangement = Gap, modifier = Modifier.padding(padding).fillMaxSize()) {
            item { Note(t("desktop.rented.connect_intro"), color = colors.text) }
            item {
                Card {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        OutlinedTextField(
                            value = address,
                            onValueChange = { address = it.take(300) },
                            label = { Text(t("desktop.rented.address")) },
                            placeholder = { Text("https://panel.example.com") },
                            singleLine = true,
                            keyboardOptions = KeyboardOptions(keyboardType = androidx.compose.ui.text.input.KeyboardType.Uri),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        PasswordField(key, { key = it.take(200) }, t("desktop.rented.key"), hint = t("desktop.rented.key_hint"))
                    }
                    Tick(t("desktop.rented.allow"), checked = allow) { allow = it }
                    Presses(
                        listOf(
                            Press(
                                t("desktop.rented.look"),
                                { picked = emptySet(); pages.check(address, key, strings) },
                                enabled = pages.busy == null && allow && address.isNotBlank() && key.isNotBlank(),
                            ),
                        ),
                    )
                }
            }
            item { SaidNote(pages.said) }
            if (found != null) {
                if (found.fullAccount) item { Note(t("desktop.rented.full_account"), color = colors.danger) }
                item {
                    Card {
                        if (found.servers.isEmpty()) Note(t("desktop.rented.none_on_panel"))
                        else Note(t("desktop.rented.pick"))
                        for (server in found.servers) {
                            Tick(server.name, checked = server.identifier in picked) {
                                picked = if (it) picked + server.identifier else picked - server.identifier
                            }
                        }
                        Presses(
                            listOf(
                                Press(
                                    t("desktop.rented.add"),
                                    { pages.connect(address, key, found.servers.map { it.identifier }.filter { it in picked }, strings, onBack) },
                                    enabled = pages.busy == null && picked.isNotEmpty(),
                                ),
                            ),
                        )
                    }
                }
            }
            item {
                Card {
                    Note(t("desktop.rented.aternos_note"))
                    Item(t("desktop.rented.aternos"), external = true, onClick = { openLink(context, Rented.ATERNOS) })
                }
            }
        }
    }
}
