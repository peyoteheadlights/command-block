package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.AddServer
import io.github.peyoteheadlights.mcsc.core.Display
import io.github.peyoteheadlights.mcsc.core.ServerListing
import io.github.peyoteheadlights.mcsc.core.ServerRow
import io.github.peyoteheadlights.mcsc.core.States

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServersScreen(
    vm: AppViewModel,
    onOpen: (String) -> Unit,
    onAdd: () -> Unit,
    onRented: (String) -> Unit = {},
    onConnectRented: () -> Unit = {},
) {
    val ui = vm.servers
    val colors = LocalColors.current
    val now = rememberNow()
    var search by rememberSaveable { mutableStateOf("") }
    var stopping by remember { mutableStateOf<List<String>?>(null) }
    var grouping by remember { mutableStateOf<ServerRow?>(null) }
    val grouped = ServerListing.grouped(ui.list, search)
    val shownIds = grouped.flatMap { (_, rows) -> rows.map { it.id } }
    val owner = vm.me?.isOwner != false
    // Rented servers: an agent without them answers 404 and none show.
    LaunchedEffect(vm.me?.user) { vm.rented.load() }
    Binder(vm, current = null) {
        Scaffold(
            containerColor = colors.sheet,
            topBar = {
                Bar(t("mobile.servers.title"), actions = {
                    if (vm.live) {
                        // On the band, so in the band's own text color.
                        Text(t("mobile.servers.live"), color = colors.bandText, modifier = Modifier.padding(end = 16.dp))
                    }
                    if (AddServer.allowed(vm.me)) {
                        IconButton(onClick = onAdd) { Icon(Icons.Filled.Add, contentDescription = t("mobile.add.title")) }
                    }
                })
            },
        ) { padding ->
            PullToRefreshBox(
                isRefreshing = ui.refreshing,
                onRefresh = {
                    vm.refreshAll()
                    vm.rented.load()
                },
                modifier = Modifier.padding(padding).fillMaxSize(),
            ) {
                LazyColumn(contentPadding = ScreenPadding, verticalArrangement = Gap, modifier = Modifier.fillMaxSize()) {
                    ui.problem?.let { problem -> item { ProblemNote(problem, onRetry = { vm.refreshServers() }) } }
                    if (ui.problem?.key == "mobile.error.unreachable" || vm.wake.waking || vm.wake.awake) item { WakeCard(vm) }
                    if (ui.list.size >= 2) {
                        item {
                            OutlinedTextField(
                                value = search,
                                onValueChange = { search = it },
                                label = { Text(t("servers.search")) },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                            )
                        }
                        item {
                            Row(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlinedButton(onClick = { vm.allServers("start", shownIds) }, enabled = shownIds.isNotEmpty()) {
                                    Text(t("servers.start_all"))
                                }
                                OutlinedButton(onClick = { stopping = shownIds }, enabled = shownIds.isNotEmpty()) {
                                    Text(t("servers.stop_all"))
                                }
                            }
                        }
                    }
                    items(ui.said) { said -> Note(said.text(LocalStrings.current)) }
                    if (search.isNotBlank() && grouped.isEmpty() && ui.list.isNotEmpty()) {
                        item { Note(t("servers.no_match", "search" to search.trim())) }
                    }
                    if (ui.loaded && ui.list.isEmpty()) {
                        item {
                            Note(t(if (vm.me?.isOwner == false) "mobile.servers.helper_none" else "servers.none"))
                        }
                    }
                    for ((group, rows) in grouped) {
                        if (grouped.size > 1 || group.isNotEmpty()) {
                            item(key = "group:$group") { SectionTitle(group.ifEmpty { t("servers.no_group") }) }
                        }
                        val ids = rows.map { it.id }
                        items(rows, key = { it.id }) { row ->
                            // Read before the last failed attempt: not current, so not shown as if it were.
                            // Each server's box has its edge and buttons in its own color.
                            McscTheme(vm.shared, themeName(vm.theme), serverColor = row.color) {
                                ServerCard(
                                    if (ui.problem != null) row.copy(state = States.UNKNOWN, playersOnline = null) else row,
                                    arrange = if (!owner) null else { how ->
                                        when (how) {
                                            "group" -> grouping = row
                                            else -> vm.moveServer(row.id, ids, up = how == "up")
                                        }
                                    },
                                ) { onOpen(row.id) }
                            }
                        }
                    }
                    rentedSection(vm, onOpen = onRented, onConnect = onConnectRented)
                    ui.checkedAt?.let { at ->
                        item { Note(t("mobile.servers.checked", "when" to Display.ago(vm.strings, at, now))) }
                    }
                    if (ui.loaded) item { Note(t("mobile.servers.more_on_pc"), color = colors.text3) }
                }
            }
        }
        stopping?.let { ids ->
            val running = ui.list.count { it.id in ids && it.state != States.OFFLINE }
            Confirm(
                title = t("servers.stop_all_title", "n" to running.toString()),
                body = t("servers.stop_all_body"),
                confirm = t("servers.stop_all"),
                danger = true,
                onConfirm = {
                    stopping = null
                    vm.allServers("stop", ids)
                },
                onDismiss = { stopping = null },
            )
        }
        grouping?.let { row -> GroupDialog(row, onSave = { vm.groupServer(row.id, it) }, onDismiss = { grouping = null }) }
    }
}

/** "Put in a group": servers with the same group name are listed together. */
@Composable
private fun GroupDialog(row: ServerRow, onSave: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf(row.group.orEmpty()) }
    val colors = LocalColors.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(t("servers.group_title")) },
        text = {
            Column {
                Text(t("servers.group_ask"))
                OutlinedTextField(
                    value = name,
                    onValueChange = { if (it.length <= 40) name = it },
                    label = { Text(t("servers.group_title")) },
                    singleLine = true,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                onSave(name)
                onDismiss()
            }) { Text(t("action.save")) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(t("mobile.common.cancel")) } },
        containerColor = colors.float,
        titleContentColor = colors.text,
        textContentColor = colors.text2,
    )
}

@Composable
private fun ServerCard(row: ServerRow, arrange: ((String) -> Unit)?, onClick: () -> Unit) {
    val colors = LocalColors.current
    var menu by remember { mutableStateOf(false) }
    Card {
        Item(
            title = row.name,
            subtitle = listOfNotNull(row.typeName, row.minecraftVersion).joinToString(" ").ifBlank { null },
            onClick = onClick,
            leading = { ServerDot(row.color) },
            trailing = if (arrange == null) null else {
                {
                    Box {
                        IconButton(onClick = { menu = true }) {
                            Icon(Icons.Filled.MoreVert, contentDescription = t("servers.more", "name" to row.name))
                        }
                        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                            for ((how, words) in listOf("up" to "servers.move_up", "down" to "servers.move_down", "group" to "servers.group")) {
                                DropdownMenuItem(text = { Text(t(words)) }, onClick = {
                                    menu = false
                                    arrange(how)
                                })
                            }
                        }
                    }
                }
            },
        )
        Row(
            Modifier.fillMaxWidth().padding(start = 42.dp, end = 16.dp, bottom = 14.dp),
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StateText(row.state, row.answering?.ok)
            if (row.state == States.ONLINE) {
                Text(
                    t("servers.players") + ": " + Display.players(LocalStrings.current, row.playersOnline, row.maxPlayers),
                    color = colors.text2,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

