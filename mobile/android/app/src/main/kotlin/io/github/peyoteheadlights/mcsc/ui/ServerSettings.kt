package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.CpuCores
import io.github.peyoteheadlights.mcsc.core.Crossplay
import io.github.peyoteheadlights.mcsc.core.Duplicate
import io.github.peyoteheadlights.mcsc.core.FolderPicker
import io.github.peyoteheadlights.mcsc.core.Internet
import io.github.peyoteheadlights.mcsc.core.Playit
import io.github.peyoteheadlights.mcsc.core.Memory
import io.github.peyoteheadlights.mcsc.core.RawText
import io.github.peyoteheadlights.mcsc.core.RemoveServer
import io.github.peyoteheadlights.mcsc.core.ServerColors
import io.github.peyoteheadlights.mcsc.core.ServerForm
import io.github.peyoteheadlights.mcsc.core.VersionChange

/**
 * Server settings: how Command Block runs this server (Game settings are
 * the game's own rules). Name and color, what happens after a crash,
 * sleep when empty, backups kept, and the start and stop limits. Save
 * sends only what changed; a refused value says why under its field.
 * Someone who may only look sees it all read-only.
 */
@Composable
fun ServerSettingsScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit, onRemoved: () -> Unit, onFiles: () -> Unit = {}) {
    val setup = vm.pages(serverId).setup
    val strings = vm.strings
    val canEdit = ServerForm.editable(vm.me)
    var asking by remember { mutableStateOf<SetupAsk?>(null) }
    LaunchedEffect(serverId) {
        setup.clearSaid()
        setup.loadSettings()
        setup.loadMemory()
        setup.loadVersion()
        setup.loadCrossplay()
        setup.loadInternet()
        setup.loadDdns()
        setup.loadPlayit()
        if (Duplicate.allowed(vm.me)) setup.loadCopyOptions()
    }

    ServerFrame(
        vm, serverId,
        title = t("page.server_settings"),
        onBack = onBack,
        refreshing = setup.settings.loading,
        onRefresh = {
            setup.loadSettings()
            setup.loadMemory()
            setup.loadVersion()
            setup.loadCrossplay()
            setup.loadInternet()
            setup.loadDdns()
            setup.loadPlayit()
        },
        overlay = {
            when (asking) {
                SetupAsk.CHANGE -> setup.report?.value?.let { checked ->
                    Confirm(
                        t("version.confirm_title", "name" to VersionChange.target(checked)), t("version.confirm_body"), t("version.confirm_button"),
                        danger = VersionChange.danger(checked),
                        onConfirm = {
                            asking = null
                            setup.changeVersion(strings)
                        },
                        onDismiss = { asking = null },
                    )
                }
                SetupAsk.ROLL_BACK -> Confirm(
                    t("version.back_title"), t("version.back_body"), t("version.back_button"), danger = false,
                    onConfirm = {
                        asking = null
                        setup.rollBack(strings)
                    },
                    onDismiss = { asking = null },
                )
                SetupAsk.CROSSPLAY_ON -> Confirm(
                    t("cross.on_title"), Crossplay.confirm(strings, setup.crossPort), t("cross.on_button"), danger = false,
                    onConfirm = {
                        asking = null
                        setup.setCrossplay(true, strings)
                    },
                    onDismiss = { asking = null },
                )
                SetupAsk.REMOVE -> Confirm(
                    t("serverset.remove_title", "name" to (vm.servers.list.firstOrNull { it.id == serverId }?.name ?: serverId)),
                    t("serverset.remove_body"), t("serverset.remove_confirm"), danger = true,
                    onConfirm = {
                        asking = null
                        setup.removeServer(strings, onRemoved)
                    },
                    onDismiss = { asking = null },
                )
                SetupAsk.MCSRVSTAT -> Confirm(
                    t("address.test"),
                    t("address.test_ask", "address" to (setup.internet.value?.let(Internet::address) ?: "")),
                    t("address.test"),
                    danger = false,
                    onConfirm = {
                        asking = null
                        setup.testInternet(true, strings)
                    },
                    onDismiss = { asking = null },
                )
                SetupAsk.PLAYIT_MCSRVSTAT -> Confirm(
                    t("address.test"),
                    t("address.test_ask", "address" to (setup.playit.value?.let(Playit::javaAddress) ?: "")),
                    t("address.test"),
                    danger = false,
                    onConfirm = {
                        asking = null
                        setup.playitCall(strings) { it.testPlayit(serverId, true) }
                    },
                    onDismiss = { asking = null },
                )
                SetupAsk.PLAYIT_STOP -> Confirm(
                    t("playit.stop"),
                    t("playit.stop_body"),
                    t("playit.stop"),
                    danger = true,
                    onConfirm = {
                        asking = null
                        setup.playitCall(strings) { it.playitAction(serverId, "stop") }
                    },
                    onDismiss = { asking = null },
                )
                SetupAsk.REMOVE_DUCKDNS, SetupAsk.REMOVE_NOIP -> {
                    val kind = if (asking == SetupAsk.REMOVE_DUCKDNS) "duckdns" else "noip"
                    val saved = setup.ddns.value?.let { if (kind == "duckdns") it.duckdns else it.noip }
                    Confirm(
                        t("ddns.remove"),
                        t("ddns.remove_body", "name" to (saved?.name ?: "")),
                        t("ddns.remove"),
                        danger = false,
                        onConfirm = {
                            asking = null
                            setup.ddnsCall("ddns", strings) { it.removeDdns(kind) }
                        },
                        onDismiss = { asking = null },
                    )
                }
                null -> Unit
            }
        },
    ) {
        val read = setup.settings
        val settings = read.value
        item { Note(t("mobile.serverset.intro"), color = LocalColors.current.text, modifier = Modifier.padding(top = 8.dp)) }
        if (!canEdit && vm.me != null) item { Note(t("mobile.serverset.read_only"), color = LocalColors.current.text3) }
        read.problem?.let { problem -> item { ProblemNote(problem, onRetry = setup::loadSettings) } }
        if (settings == null) {
            if (read.problem == null) item { Note(t("mobile.common.loading")) }
            return@ServerFrame
        }

        item { SectionTitle(t("address.title")) }
        item { Card { AddressCard(vm, setup, serverId) { asking = it } } }
        item { SectionTitle(t("serverset.identity")) }
        item {
            Card {
                SettingField(
                    label = t("serverset.name"),
                    kind = "text",
                    value = setup.edits[ServerForm.NAME] ?: ServerForm.initial(settings, ServerForm.NAME),
                    locked = !canEdit,
                    problem = setup.fieldProblems[ServerForm.NAME],
                ) { setup.edit(ServerForm.NAME, it.take(ServerForm.NAME_LIMIT)) }
                ColorPicker(
                    palette = settings.palette.map { it.id to it.hex },
                    current = setup.color ?: settings.color,
                    locked = !canEdit,
                    onPick = setup::pickColor,
                )
            }
        }
        for (group in ServerForm.GROUPS) {
            if (!ServerForm.shown(group, vm.technical, vm.me, vm.servers.list.firstOrNull { it.id == serverId }?.edition)) continue
            item { SectionTitle(t(group.titleKey)) }
            item {
                Card {
                    for (field in group.fields) {
                        SettingField(
                            label = t(field.labelKey),
                            hint = field.hintKey?.let { key ->
                                t(key, "minutes" to ServerForm.initial(settings, "monitor.crash_window_minutes"))
                            },
                            kind = field.kind,
                            value = setup.edits[field.key] ?: ServerForm.initial(settings, field.key),
                            locked = !canEdit,
                            problem = setup.fieldProblems[field.key],
                        ) { setup.edit(field.key, it) }
                    }
                    if (group.titleKey == "serverset.sleep") Note(ServerForm.sleepLine(strings, settings))
                    group.noteKey?.let { Note(t(it), color = LocalColors.current.text3) }
                }
            }
        }
        if (canEdit) {
            item {
                Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Button(onClick = { setup.saveSettings(strings) }, enabled = setup.busy == null) {
                        Text(t(if (setup.busy == "settings") "action.saving" else "mobile.serverset.save"))
                    }
                }
            }
        }
        item { SaidNote(setup.said) }
        if (FolderPicker.allowed(vm.me)) {
            item { SectionTitle(t("files.title")) }
            item { Card { Item(t("files.open"), subtitle = t("files.hint"), onClick = onFiles) } }
        }
        item { SectionTitle(t("serverset.cpu")) }
        item { Card { CoresCard(vm, setup, canEdit) } }
        item { SectionTitle(t("version.title")) }
        item { Card { VersionCard(vm, setup, canEdit) { asking = it } } }
        item { SectionTitle(t("cross.title")) }
        item { Card { CrossplayCard(vm, setup, canEdit) { asking = it } } }
        item { SectionTitle(t("memory.title")) }
        item { Card { MemoryCard(vm, setup, canEdit, bedrock = vm.servers.list.firstOrNull { it.id == serverId }?.edition == "bedrock") } }
        setup.memory.value?.let { memory ->
            if (RawText.allowed(vm.me, vm.technical)) {
                item {
                    Card {
                        val saved = memory.jvmArgs.joinToString(" ")
                        var typed by remember(saved) { mutableStateOf(saved) }
                        RawEditor(
                            vm,
                            title = t("mobile.raw.args_title"),
                            hint = t("mobile.raw.args_hint"),
                            text = typed,
                            onText = { typed = it },
                            changes = { RawText.argChanges(memory.jvmArgs, RawText.args(typed)) },
                            busy = setup.busy != null,
                            onSave = { setup.saveJavaOptions(RawText.args(typed), strings) },
                        )
                        HideableNote(vm, "memory.raw_warning")
                        setup.argsBefore?.let { before ->
                            Presses(listOf(Press(t("changes.undo"), { setup.saveJavaOptions(before, strings, undoing = true) }, enabled = setup.busy == null)))
                        }
                    }
                }
            }
        }
        if (Duplicate.allowed(vm.me)) {
            item { SectionTitle(t("dup.title")) }
            item { Card { DuplicateCard(vm, setup, serverId) } }
        }
        if (RemoveServer.allowed(vm.me)) {
            item { SectionTitle(t("serverset.remove")) }
            item { Card { RemoveCard(vm, setup) { asking = it } } }
        }
    }
}

/**
 * Server address: "Let friends join from the internet" (owner only), what
 * the router did, the two outside tests (mcsrvstat.us after asking once,
 * or this phone over mobile data), port forwarding by hand, and playit.gg
 * when the internet provider blocks it. Home and Tailscale addresses are
 * on the Overview's "How friends join".
 */
@Composable
private fun AddressCard(vm: AppViewModel, setup: SetupPages, serverId: String, ask: (SetupAsk) -> Unit) {
    val colors = LocalColors.current
    val context = LocalContext.current
    val read = setup.internet
    val owner = vm.me?.isOwner == true
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalArrangement = Gap) {
        read.problem?.let { Text(it.text(vm.strings), color = colors.danger) }
        val info = read.value ?: return@Column
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(t("address.internet_label"), color = colors.text, modifier = Modifier.weight(1f))
            Switch(
                checked = info.on,
                onCheckedChange = { setup.setInternet(it, vm.strings) },
                enabled = owner && setup.busy == null,
            )
        }
        if (setup.busy == "internet") Text(t("address.busy"), color = colors.text2)
        for (line in Internet.lines(vm.strings, info, owner)) Text(line.text, color = colors.tone(line.tone))
        val address = info.address
        val port = info.port
        if (Internet.testable(info) && address != null && port != null) {
            if (owner) {
                Button(onClick = { if (info.mcsrvstatOk) setup.testInternet(false, vm.strings) else ask(SetupAsk.MCSRVSTAT) }, enabled = setup.busy == null) {
                    Text(t("address.test"))
                }
            }
            val mobile = onMobileDataOnly(context)
            val bedrock = vm.servers.list.firstOrNull { it.id == serverId }?.edition == "bedrock"
            OutlinedButton(onClick = { setup.phoneTest(address, port, bedrock, vm.strings) }, enabled = mobile && setup.busy == null) {
                Text(t("address.test_phone_button"))
            }
            if (!mobile) Text(t("address.wifi_off"), color = colors.text2)
        }
        for (line in Internet.nameLines(vm.strings, info)) Text(line, color = colors.text2)
        val steps = Internet.forwardSteps(vm.strings, info, vm.technical)
        if (steps.isNotEmpty()) {
            Text(t("address.forward_title"), color = colors.text)
            for (step in steps) Text(step, color = colors.text2)
        }
        NamesPart(vm, setup, owner, ask)
        PlayitPart(vm, setup, serverId, owner, ask)
    }
}

/**
 * playit.gg (agent/playit.py): link the program on the server's PC, the
 * tunnel to make on playit.gg, its address and test. Installing it is done
 * from the desktop app on that PC.
 */
@Composable
private fun PlayitPart(vm: AppViewModel, setup: SetupPages, serverId: String, owner: Boolean, ask: (SetupAsk) -> Unit) {
    val colors = LocalColors.current
    val context = LocalContext.current
    setup.playit.problem?.let { Text(it.text(vm.strings), color = colors.danger) }
    val state = setup.playit.value ?: return
    if (!state.supported) return // a plain Bedrock server goes through WaterdogPE, later
    val step = Playit.step(vm.strings, state, owner)
    val free = setup.busy == null
    Text(t("playit.title"), color = colors.text)
    for (line in step.lines) Text(line.text, color = colors.tone(line.tone))
    when (step.button) {
        "use" -> Button(onClick = { setup.playitCall(vm.strings) { it.playitAction(serverId, "use") } }, enabled = free) { Text(t("playit.use")) }
        "link" -> Button(onClick = { openLink(context, state.claim?.url.orEmpty()) }) { Text(t("playit.link_open")) }
        "reconnect" -> Button(onClick = { setup.playitCall(vm.strings) { it.playitAction(serverId, "reconnect") } }, enabled = free) { Text(t("playit.reconnect")) }
        "make" -> Button(onClick = { openLink(context, state.newTunnel) }) { Text(t("playit.make_open")) }
    }
    if (step.checkAgain) OutlinedButton(onClick = { setup.loadPlayit() }) { Text(t("playit.check")) }
    if (owner && state.on && state.phase == "running" && Playit.javaAddress(state) != null) {
        OutlinedButton(
            onClick = { if (state.mcsrvstatOk) setup.playitCall(vm.strings) { it.testPlayit(serverId, false) } else ask(SetupAsk.PLAYIT_MCSRVSTAT) },
            enabled = free,
        ) { Text(t("address.test")) }
    }
    if (setup.busy == "playit") Text(t("address.busy"), color = colors.text2)
    if (owner && state.on) OutlinedButton(onClick = { ask(SetupAsk.PLAYIT_STOP) }, enabled = free) { Text(t("playit.stop")) }
}

/**
 * A name for the internet address (agent/ddns.py): DuckDNS, with No-IP next
 * to it. The names are this PC's, so every server shows the same ones.
 */
@Composable
private fun NamesPart(vm: AppViewModel, setup: SetupPages, owner: Boolean, ask: (SetupAsk) -> Unit) {
    val colors = LocalColors.current
    val context = LocalContext.current
    val state = setup.ddns.value ?: return
    var open by remember { mutableStateOf<String?>(null) }
    // One form open at a time, so one set of fields.
    var name by remember(open) { mutableStateOf("") }
    var user by remember(open) { mutableStateOf("") }
    var secret by remember(open) { mutableStateOf("") }
    Text(t("ddns.title"), color = colors.text)
    Text(t("ddns.note"), color = colors.text2)
    for (kind in listOf("duckdns", "noip")) {
        val saved = if (kind == "duckdns") state.duckdns else state.noip
        if (saved != null) {
            val date = { at: Double -> java.text.DateFormat.getDateInstance().format(java.util.Date((at * 1000).toLong())) }
            for (line in Internet.savedLines(vm.strings, kind, saved, date)) Text(line.text, color = colors.tone(line.tone))
            if (!owner) continue
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (kind == "noip") {
                    // One tap to No-IP's hostname page (its Confirm button is there), then "I confirmed it".
                    Button(onClick = { openLink(context, state.noipSite) }) { Text(t("ddns.noip_open")) }
                    OutlinedButton(onClick = { setup.ddnsCall("ddns", vm.strings) { it.noIpConfirmed() } }, enabled = setup.busy == null) {
                        Text(t("ddns.noip_confirmed"))
                    }
                }
                OutlinedButton(
                    onClick = { ask(if (kind == "duckdns") SetupAsk.REMOVE_DUCKDNS else SetupAsk.REMOVE_NOIP) },
                    enabled = setup.busy == null,
                ) { Text(t("ddns.remove")) }
            }
            continue
        }
        if (!owner) continue
        if (open != kind) {
            OutlinedButton(onClick = { open = kind }) { Text(t("ddns.${kind}_use")) }
            continue
        }
        Text(t("ddns.${kind}_steps"), color = colors.text2)
        OutlinedButton(onClick = { openLink(context, if (kind == "duckdns") state.duckdnsSite else state.noipSite) }) {
            Text(t("ddns.${kind}_open"))
        }
        OutlinedTextField(name, { name = it.take(253) }, label = { Text(t("ddns.${kind}_name")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        if (kind == "noip") {
            OutlinedTextField(user, { user = it.take(100) }, label = { Text(t("ddns.noip_username")) }, singleLine = true, modifier = Modifier.fillMaxWidth())
        }
        OutlinedTextField(
            secret,
            { secret = it.take(200) },
            label = { Text(t(if (kind == "duckdns") "ddns.duckdns_token" else "ddns.noip_password")) },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        if (setup.busy == "ddns") Text(t("ddns.checking"), color = colors.text2)
        setup.said?.let { if (!it.good) Text(it.text, color = colors.danger) }
        Button(
            onClick = {
                setup.ddnsCall("ddns", vm.strings, done = { open = null }) {
                    if (kind == "duckdns") it.saveDuckDns(name.trim(), secret.trim()) else it.saveNoIp(name.trim(), user.trim(), secret)
                }
            },
            enabled = setup.busy == null && name.isNotBlank() && secret.isNotEmpty() && (kind == "duckdns" || user.isNotBlank()),
        ) { Text(t("ddns.save")) }
    }
}

/** Wi-Fi off and mobile data on, so the phone asks from outside the home, like a friend. */
private fun onMobileDataOnly(context: android.content.Context): Boolean {
    val manager = context.getSystemService(android.net.ConnectivityManager::class.java) ?: return false
    @Suppress("DEPRECATION") // allNetworks: every network, the VPN's underlying ones too (Tailscale)
    val all = manager.allNetworks.mapNotNull(manager::getNetworkCapabilities)
    val wifi = all.any { it.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) }
    val cellular = all.any { it.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) }
    return cellular && !wifi
}

/** Which CPU cores the server may use, who else uses each, and what the
 * running server really got; or why this PC can't limit them. */
@Composable
private fun CoresCard(vm: AppViewModel, setup: SetupPages, canEdit: Boolean) {
    val colors = LocalColors.current
    val cpu = setup.settings.value?.cpu ?: return
    val count = cpu.logicalCores
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        if (!cpu.supported || count == null || count == 0) {
            Text(cpu.unsupportedReason ?: t("serverset.cpu_unreadable"), color = colors.text2)
            return@Column
        }
        val all = setup.everyCore()
        val picked = setup.pickedCores()
        val shared = CpuCores.sharedBy(cpu)
        SettingField(t("serverset.all_cores", "count" to count), "bool", all.toString(), locked = !canEdit, problem = null) {
            setup.setAllCores(it == "true")
        }
        if (!all) {
            for (core in 0 until count) {
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = core in picked, onCheckedChange = { setup.toggleCore(core) }, enabled = canEdit)
                    Column {
                        Text(t("serverset.core", "n" to core + 1), color = colors.text)
                        shared[core]?.let { Text(t("serverset.core_shared", "names" to it.joinToString(", ")), color = colors.text2) }
                    }
                }
            }
        }
        Text(CpuCores.nowLine(vm.strings, cpu), color = colors.text2, modifier = Modifier.padding(top = 8.dp))
        cpu.status.problems.firstOrNull()?.let { Text(it, color = colors.danger, modifier = Modifier.padding(top = 4.dp)) }
        if (canEdit) {
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { setup.saveCores(vm.strings) }, enabled = setup.busy == null) {
                    Text(t(if (setup.busy == "cores") "action.saving" else "action.save"))
                }
                OutlinedButton(onClick = { setup.pickHalf(count) }, enabled = setup.busy == null) { Text(t("serverset.half_cores")) }
            }
        }
    }
}

/** The memory slider: bounded by the PC's memory, with the warning when
 * every running server together may use more than the PC has. */
@Composable
private fun MemoryCard(vm: AppViewModel, setup: SetupPages, canEdit: Boolean, bedrock: Boolean) {
    val colors = LocalColors.current
    val read = setup.memory
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        read.problem?.let { Text(it.text(vm.strings), color = colors.danger) }
        val memory = read.value ?: return@Column
        Text(t("memory.label"), color = colors.text)
        Text(memory.totalMb?.let { t("memory.pc_has", "total" to Memory.gb(it)) } ?: t("memory.pc_unknown"), color = colors.text2)
        if (!Memory.slider(memory)) {
            if (!memory.takesMemoryLimit) Text(Memory.noSlider(vm.strings, memory, bedrock), color = colors.text2, modifier = Modifier.padding(top = 4.dp))
            return@Column
        }
        val chosen = setup.memoryChoice ?: Memory.start(memory)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Slider(
                value = chosen.toFloat(),
                onValueChange = setup::slideMemory,
                valueRange = memory.minMb.toFloat()..(memory.totalMb ?: memory.minMb).toFloat(),
                enabled = canEdit,
                modifier = Modifier.weight(1f),
            )
            Text(Memory.gb(chosen), color = colors.text, modifier = Modifier.padding(start = 8.dp))
        }
        Text(Memory.nowLine(vm.strings, memory), color = colors.text2)
        Memory.warning(vm.strings, memory, chosen)?.let { Text(it, color = colors.warning, modifier = Modifier.padding(top = 4.dp)) }
        if (canEdit) {
            Button(onClick = { setup.saveMemory(vm.strings) }, enabled = setup.busy == null, modifier = Modifier.padding(top = 8.dp)) {
                Text(t(if (setup.busy == "memory") "action.saving" else "memory.save"))
            }
        }
    }
}

/** One setting: a switch, or a box for a number or words. */
@Composable
fun SettingField(
    label: String,
    kind: String,
    value: String,
    locked: Boolean,
    problem: String?,
    hint: String? = null,
    onChange: (String) -> Unit,
) {
    val colors = LocalColors.current
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        if (kind == "bool") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = colors.text, modifier = Modifier.weight(1f))
                Switch(checked = value == "true", onCheckedChange = { onChange(it.toString()) }, enabled = !locked)
            }
        } else {
            OutlinedTextField(
                value = value,
                onValueChange = onChange,
                label = { Text(label) },
                singleLine = true,
                readOnly = locked,
                enabled = !locked,
                isError = problem != null,
                keyboardOptions = KeyboardOptions(keyboardType = if (kind == "text") KeyboardType.Text else KeyboardType.Number),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        problem?.let { Text(it, color = colors.danger, modifier = Modifier.padding(top = 4.dp)) }
        hint?.takeIf { it.isNotBlank() }?.let { Text(it, color = colors.text2, modifier = Modifier.padding(top = 4.dp)) }
    }
}

/** The palette as dots, and a box for any color typed as #RRGGBB. The
 * preview is the server's badge, so its text is checked to stay readable. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun ColorPicker(palette: List<Pair<String, String>>, current: String?, locked: Boolean, onPick: (String) -> Unit) {
    val colors = LocalColors.current
    var typed by remember(current) { mutableStateOf(current.orEmpty()) }
    val typedColor = ServerForm.color(typed)
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        Text(t("serverset.color"), color = colors.text)
        FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            for ((id, hex) in palette) {
                val chosen = hex.equals(current, ignoreCase = true)
                val name = t("color.$id")
                Box(
                    Modifier
                        .size(36.dp)
                        .background(hexColor(hex) ?: colors.neutral, CircleShape)
                        .border(if (chosen) 3.dp else 1.dp, if (chosen) colors.text else colors.border, CircleShape)
                        .semantics { contentDescription = name; selected = chosen }
                        .clickable(enabled = !locked) { onPick(hex) },
                )
            }
        }
        OutlinedTextField(
            value = typed,
            onValueChange = { text ->
                typed = text.take(7)
                ServerForm.color(typed)?.let(onPick)
            },
            label = { Text(t("serverset.custom_color")) },
            singleLine = true,
            readOnly = locked,
            enabled = !locked,
            isError = typed.isNotBlank() && typedColor == null,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        )
        Text(
            t(if (typed.isNotBlank() && typedColor == null) "mobile.serverset.color_bad" else "mobile.serverset.color_hint"),
            color = if (typed.isNotBlank() && typedColor == null) colors.danger else colors.text2,
            modifier = Modifier.padding(top = 4.dp),
        )
        ServerColors.rgb(current)?.let { rgb ->
            val fill = ServerColors.fillWithText(rgb)
            Box(
                Modifier.padding(top = 8.dp).background(hexColor(ServerColors.hex(fill.fill)) ?: colors.neutral, RoundedCornerShape(8.dp))
                    .padding(horizontal = 12.dp, vertical = 4.dp),
            ) { Text(current.orEmpty().uppercase(), color = hexColor(ServerColors.hex(fill.text)) ?: colors.text) }
        }
    }
}
