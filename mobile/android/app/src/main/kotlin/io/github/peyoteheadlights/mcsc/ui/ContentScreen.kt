package io.github.peyoteheadlights.mcsc.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.Addon
import io.github.peyoteheadlights.mcsc.core.Changes
import io.github.peyoteheadlights.mcsc.core.Content
import io.github.peyoteheadlights.mcsc.core.ContentKind
import io.github.peyoteheadlights.mcsc.core.Display
import io.github.peyoteheadlights.mcsc.core.DownloadLinks
import io.github.peyoteheadlights.mcsc.core.IconCache
import io.github.peyoteheadlights.mcsc.core.Install
import io.github.peyoteheadlights.mcsc.core.Mod
import io.github.peyoteheadlights.mcsc.core.ModFit
import io.github.peyoteheadlights.mcsc.core.ModList
import io.github.peyoteheadlights.mcsc.core.ModVersion
import io.github.peyoteheadlights.mcsc.core.SameJob
import io.github.peyoteheadlights.mcsc.core.Modpacks
import io.github.peyoteheadlights.mcsc.core.AntiCheats
import io.github.peyoteheadlights.mcsc.core.LinkedModpack
import io.github.peyoteheadlights.mcsc.core.LinkedPacks
import io.github.peyoteheadlights.mcsc.core.MadeModpacks
import io.github.peyoteheadlights.mcsc.core.MakeModpack
import io.github.peyoteheadlights.mcsc.core.ModrinthCards
import io.github.peyoteheadlights.mcsc.core.ModrinthHit
import io.github.peyoteheadlights.mcsc.core.ModpackMakeOptions
import io.github.peyoteheadlights.mcsc.core.PlayerFile
import io.github.peyoteheadlights.mcsc.core.PlayerOnlyOptions
import io.github.peyoteheadlights.mcsc.core.Sides
import io.github.peyoteheadlights.mcsc.core.Permissions
import io.github.peyoteheadlights.mcsc.core.States
import io.github.peyoteheadlights.mcsc.core.StillFit
import io.github.peyoteheadlights.mcsc.core.Tone
import io.github.peyoteheadlights.mcsc.core.UpdateAll
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** A question waiting for "yes": its title, words and button. */
private data class ContentAsk(val title: String, val body: String, val confirm: String, val danger: Boolean, val run: () -> Unit)

/** The server's state for these screens: Unknown after a failed read, never a guess. */
private fun stateFor(vm: AppViewModel, serverId: String, failed: Boolean): String? {
    val ui = vm.server[serverId]
    if (failed || ui?.problem != null) return States.UNKNOWN
    return ui?.status?.state ?: vm.servers.list.firstOrNull { it.id == serverId }?.state
}

/**
 * Mods, Plugins or Packs, named for the server type. Java servers: what's
 * installed with on/off, Update, Versions (Roll back) and Remove, Update
 * all, the missing dependencies, Modrinth search, a .jar or modpack from
 * the phone, and the change history. They change only while the server is
 * stopped, so while it runs the screen offers Stop, which asks first.
 * Bedrock servers: their packs, which change while it runs too and then
 * offer Restart now.
 */
@Composable
fun ContentScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit) {
    val content = vm.pages(serverId).content
    val kind = Content.kind(vm.servers.list.firstOrNull { it.id == serverId })
    var asking by remember { mutableStateOf<ContentAsk?>(null) }
    val strings = vm.strings

    LaunchedEffect(serverId, kind) {
        content.clearSaid()
        when (kind) {
            ContentKind.PACKS -> content.loadAddons()
            ContentKind.MODS, ContentKind.PLUGINS -> content.loadMods()
            else -> Unit
        }
    }
    val reload = { if (kind == ContentKind.PACKS) content.loadAddons() else content.loadMods() }

    ServerFrame(
        vm, serverId,
        title = t((kind ?: ContentKind.MODS).titleKey),
        onBack = onBack,
        refreshing = false,
        onRefresh = {
            vm.refreshServer(serverId)
            reload()
        },
        overlay = {
            asking?.let { ask ->
                Confirm(ask.title, ask.body, ask.confirm, danger = ask.danger, onConfirm = {
                    asking = null
                    ask.run()
                }, onDismiss = { asking = null })
            }
            ContentDialogs(vm, content) { asking = it }
        },
    ) {
        when (kind) {
            null -> item { Note(t("mobile.common.loading")) }
            ContentKind.NONE -> item { Card(Modifier.padding(top = 8.dp)) { Item(t("mobile.content.none_for_type")) } }
            ContentKind.PACKS -> packsList(vm, serverId, content) { asking = it }
            else -> modsList(vm, serverId, content, kind) { asking = it }
        }
    }
}

// ---------------------------------------------------------------- mods and plugins

private fun LazyListScope.modsList(vm: AppViewModel, serverId: String, content: ContentPages, kind: ContentKind, ask: (ContentAsk) -> Unit) {
    val read = content.mods
    val state = stateFor(vm, serverId, read.problem != null)
    val changes = Content.changes(vm.canDo(Permissions.MODS_MANAGE), state)
    val busy = content.busy
    val idle = busy == null

    item { ChangesCard(vm, serverId, changes, state, ask) }
    item { TaskNote(content, serverId) }
    item { SaidNote(content.said) }

    val list = read.value
    read.problem?.let { problem ->
        item { Note(t("mobile.content.unknown"), color = LocalColors.current.text2) }
        item { ProblemNote(problem, onRetry = content::loadMods) }
    }
    if (list == null) {
        if (read.problem == null) item { Note(t("mobile.common.loading")) }
        return
    }
    if (list.storedSeparately) item { Note(t("mods.stored_separately"), color = LocalColors.current.text2) }
    list.modpack?.let { pack -> item { LinkedPackCard(vm, content, pack, changes == Changes.READY && idle) } }
    if (vm.canDo(Permissions.MODS_MANAGE)) item { MakeModpackCard(vm, content, idle) }
    if (vm.canDo(Permissions.MODS_MANAGE)) item { DownloadLinkCard(vm, content, idle) }
    if (vm.canDo(Permissions.MODS_MANAGE)) item { ModrinthCard(vm, content, idle) }
    item { AntiCheatCard(vm, content, idle) }
    list.fit?.let { fit -> item { StillFitCard(vm, content, list, fit, kind, changes == Changes.READY && idle) } }
    if (list.overlaps.isNotEmpty()) item { SameJobCard(vm, content, list, changes == Changes.READY && idle) }

    if (changes == Changes.READY) {
        item {
            Presses(
                listOf(
                    Press(
                        t(if (UpdateAll.offered(list)) "mobile.content.update_all" else "mobile.content.check_updates"),
                        content::openUpdateAll,
                        enabled = idle && list.mods.isNotEmpty(),
                    ),
                ),
            )
        }
    }

    val missing = Content.missing(content.dependencies)
    // Unread is said as such, never shown as "nothing missing".
    if (content.dependencies == null) item { Note(t("mobile.content.missing_unknown"), color = LocalColors.current.text3) }
    if (missing.isNotEmpty()) {
        item { SectionTitle(t("mobile.content.missing_title")) }
        item {
            Card {
                for (dep in missing) Item(dep.name.ifBlank { dep.modId }, subtitle = dep.reason ?: dep.rangeText)
                if (changes == Changes.READY) {
                    Presses(listOf(Press(t("mobile.content.install_missing"), content::planMissing, enabled = idle)))
                }
            }
        }
    }

    item {
        SectionTitle(
            if (kind == ContentKind.PLUGINS) t("page.plugins") + " · " + list.mods.size
            else vm.strings.tn("mods.installed", list.mods.size),
        )
    }
    if (list.mods.isEmpty()) item { Card { Item(t("mods.none"), subtitle = t("mods.none_hint").takeIf { changes == Changes.READY }) } }
    items(list.mods, key = { it.filename }) { mod -> ModCard(vm, content, mod, changes, ask) }
    if (list.oddSetups.isNotEmpty()) {
        item {
            Card {
                Note(t("sides.odd_title"), color = LocalColors.current.warning)
                for (odd in list.oddSetups) Note("• ${odd.text}", color = LocalColors.current.warning)
            }
        }
    }
    if (kind != ContentKind.PACKS) playersOnly(vm, content, list.playerOnly, list.playerOnlyOptions, kind, changes, ask)

    if (changes == Changes.READY) {
        item { SectionTitle(t("mobile.content.search_title")) }
        item { SearchCard(vm, content, list.minecraftVersion, vm.server[serverId]?.status?.serverTypeName) }
        foundList(vm, content)
        item { AddFiles(vm, content, kind) }
    }

    item { SectionTitle(t("mods.change_history")) }
    val rows = content.history.value?.history
    when {
        content.history.problem != null -> item { Note(content.history.problem!!.text(), color = LocalColors.current.text3) }
        rows == null -> Unit
        rows.isEmpty() -> item { Card { Item(t("mods.no_history")) } }
        else -> items(rows.take(50)) { row ->
            val colors = LocalColors.current
            val now = rememberNow()
            Card {
                Item(
                    "${Content.action(vm.strings, row)}: ${Content.historyName(vm.strings, row)}",
                    subtitle = listOfNotNull(
                        t("mobile.content.history_by", "when" to Display.ago(vm.strings, row.ts, now), "who" to (row.user ?: t("value.unknown"))),
                        Content.change(vm.strings, row.oldVersion, row.newVersion).takeIf { row.oldVersion != null || row.newVersion != null },
                    ).joinToString(" · "),
                )
                Note(
                    t(if (row.result == "ok") "mobile.content.result_ok" else "mobile.content.result_failed") +
                        (row.detail?.takeIf { row.result != "ok" }?.let { ": $it" } ?: ""),
                    color = colors.tone(Content.historyTone(row)),
                )
            }
        }
    }
}

/** Why changes are off now, with Stop right here while the server runs. */
@Composable
private fun ChangesCard(vm: AppViewModel, serverId: String, changes: Changes, state: String?, ask: (ContentAsk) -> Unit) {
    val colors = LocalColors.current
    val strings = vm.strings
    when (changes) {
        Changes.READY -> Unit
        Changes.NOT_ALLOWED -> Note(t("mobile.content.not_allowed"), color = colors.text3, modifier = Modifier.padding(top = 8.dp))
        Changes.UNKNOWN -> Note(t("mobile.content.state_unknown"), color = colors.warning, modifier = Modifier.padding(top = 8.dp))
        Changes.STOP_FIRST -> Card(Modifier.padding(top = 8.dp)) {
            Note(t("mobile.content.stop_first"), color = colors.text, modifier = Modifier.padding(top = 10.dp))
            if (Content.canStopHere(state, vm.canControl())) {
                val busy = vm.server[serverId]?.busyAction
                Presses(
                    listOf(
                        Press(t(if (busy == "stop") "action.stopping" else "action.stop"), {
                            ask(ContentAsk(strings.t("mobile.content.stop_confirm_title"), strings.t("mobile.content.stop_confirm_body"), strings.t("action.stop"), true) {
                                vm.act(serverId, "stop")
                            })
                        }, danger = true, enabled = busy == null),
                    ),
                )
            } else {
                Note(t("mods.running_notice", "state" to t(Display.state(state).key)), color = colors.text2)
            }
        }
    }
}

/** A running install, Update all or modpack import, with the step the live feed last said. */
@Composable
private fun TaskNote(content: ContentPages, serverId: String) {
    val busy = content.busy ?: return
    val long = busy == "update_all" || busy == "modpack" || busy == "missing" || busy.startsWith("install:") || busy.startsWith("update:")
    if (!long) return
    val step = UpdateAll.progress(content.task, serverId)
    Note(
        if (step != null) t("mobile.content.update_all_working", "step" to step) else t("mobile.content.working"),
        color = LocalColors.current.text,
    )
}

@Composable
private fun ModCard(vm: AppViewModel, content: ContentPages, mod: Mod, changes: Changes, ask: (ContentAsk) -> Unit) {
    val colors = LocalColors.current
    val strings = vm.strings
    val status = Content.status(mod)
    val idle = content.busy == null
    var choosing by remember { mutableStateOf(false) }
    Card {
        Item(
            mod.name.ifBlank { mod.filename },
            subtitle = listOf(mod.version.ifBlank { t("value.unknown") }, t(status.key)).joinToString(" · "),
        )
        for (problem in Content.problems(mod)) Note(problem, color = if (mod.status == "error") colors.danger else colors.warning)
        Content.newer(strings, mod)?.let { Note(it, color = colors.accentInk) }
        if (mod.addedByYou == true) Note(t("linked.added_by_you"), color = colors.text3)
        Sides.words(strings, mod)?.let { Note(it, color = colors.text3) }
        if (vm.technical) Note(Content.technical(strings, mod), color = colors.text3)
        if (changes != Changes.NOT_ALLOWED) {
            val ready = changes == Changes.READY && idle
            Presses(
                listOfNotNull(
                    Press(t(if (mod.enabled) "mods.turn_off" else "mods.turn_on"), { content.setEnabled(mod, !mod.enabled, strings) }, enabled = ready),
                    Press(t("mods.update"), { content.askUpdate(mod) }, enabled = ready).takeIf { mod.updateAvailable != null },
                    Press(t("mobile.content.versions"), { content.showVersions(mod) }, enabled = idle).takeIf { mod.modId.isNotBlank() },
                    Press(t("mods.remove"), { content.askRemove(mod, strings) }, danger = true, enabled = ready),
                    Press(t("sides.who"), { choosing = true }, enabled = idle).takeIf { Sides.picks(mod).isNotEmpty() },
                ),
            )
        }
    }
    if (choosing) {
        val ready = changes == Changes.READY && idle
        Question(t("sides.who_title", "name" to mod.name.ifBlank { mod.filename }), confirm = null, onConfirm = {}, onDismiss = { choosing = false }) {
            for (side in Sides.picks(mod)) {
                // Players moves the jar out of the mods folder, so the server must be stopped.
                val allowed = side != "players" || ready
                Item(
                    t("sides.mark_$side"),
                    subtitle = t("mobile.content.stop_first").takeIf { !allowed },
                    onClick = if (allowed) { { choosing = false; content.setSide(mod.filename, mod.name.ifBlank { mod.filename }, side, strings) } } else null,
                )
            }
        }
    }
}

/** The player-only folder: mods, resource packs and shader packs the server never loads. */
private fun LazyListScope.playersOnly(
    vm: AppViewModel,
    content: ContentPages,
    files: List<PlayerFile>,
    options: PlayerOnlyOptions,
    kind: ContentKind,
    changes: Changes,
    ask: (ContentAsk) -> Unit,
) {
    val manage = changes != Changes.NOT_ALLOWED
    if (files.isEmpty() && !manage) return
    item { SectionTitle(t("sides.players_title")) }
    item {
        val strings = vm.strings
        val idle = content.busy == null
        val pick = rememberFilePicker { file ->
            if (Sides.accepts(file.name)) content.uploadForPlayers(file, strings)
            else content.tell(Said(strings.t("mobile.content.wrong_file", "kind" to ".jar / .zip"), good = false))
        }
        Card {
            Note(t("sides.players_intro"), color = LocalColors.current.text2)
            if (files.isEmpty()) Note(t("sides.players_none"))
            for (file in files) {
                val name = file.name.ifBlank { file.filename }
                Item(name, subtitle = listOf(file.version, t(if (file.kind == "mod") "sides.kind_mod" else "sides.kind_pack"), file.filename).filter { it.isNotBlank() }.joinToString(" · "))
                if (file.changed) Note(t("sides.changed"), color = LocalColors.current.warning)
                if (file.missing) {
                    Note(t("sides.missing"), color = LocalColors.current.warning)
                    if (manage && file.site == "modrinth") {
                        Presses(listOf(Press(t("sides.get_again"), { content.fetchForPlayers(file.filename, strings) }, enabled = idle)))
                    }
                    continue
                }
                if (manage) {
                    Presses(
                        listOfNotNull(
                            Press(t("sides.move_to_server"), { content.setSide(file.filename, name, "both", strings) }, enabled = changes == Changes.READY && idle)
                                .takeIf { file.kind == "mod" && kind == ContentKind.MODS },
                            Press(t("mods.remove"), {
                                ask(ContentAsk(strings.t("sides.remove_title", "name" to name), strings.t("sides.remove_body"), strings.t("mods.remove"), true) {
                                    content.removeForPlayers(file.filename, strings)
                                })
                            }, danger = true, enabled = idle),
                        ),
                    )
                }
            }
            if (content.busy == "players") Note(t("mobile.content.reading"))
            if (manage) Presses(listOf(Press(t("sides.players_add"), pick, enabled = idle)))
            if (vm.canDo(Permissions.SETTINGS_SERVER)) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(horizontal = 8.dp)) {
                    Checkbox(checked = options.backup, enabled = idle, onCheckedChange = { content.setPlayerOnlyOptions(options.copy(backup = it), strings) })
                    Text(t("sides.option_backup"))
                }
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(horizontal = 8.dp)) {
                    Checkbox(
                        checked = options.skipListed,
                        enabled = idle && options.backup,
                        onCheckedChange = { content.setPlayerOnlyOptions(options.copy(skipListed = it), strings) },
                    )
                    Text(t("sides.option_skip_listed"))
                }
            }
        }
    }
}

@Composable
private fun SearchCard(vm: AppViewModel, content: ContentPages, minecraft: String?, typeName: String?) {
    var query by rememberSaveable { mutableStateOf("") }
    Card {
        Column(Modifier.padding(16.dp)) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it.take(100) },
                label = { Text(t("mods.search_label")) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { content.search(query) }),
                modifier = Modifier.fillMaxWidth(),
            )
            Text(
                t("mobile.content.search_hint", "type" to (typeName ?: t("value.unknown")), "version" to (minecraft ?: t("value.unknown"))),
                color = LocalColors.current.text3,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        Presses(listOf(Press(t(if (content.found?.loading == true) "mods.searching" else "mods.search"), { content.search(query) }, enabled = query.isNotBlank())))
    }
}

private fun LazyListScope.foundList(vm: AppViewModel, content: ContentPages) {
    val read = content.found ?: return
    read.problem?.let { problem -> item { ProblemNote(problem) } }
    val found = read.value ?: return
    if (found.hits.isEmpty()) item { Note(t("mods.nothing_found")) }
    items(found.hits, key = { "hit:" + it.slug }) { hit ->
        val context = LocalContext.current
        Card {
            Item(
                hit.title.ifBlank { hit.slug },
                subtitle = listOfNotNull(hit.description?.take(160), Install.downloads(vm.strings, hit.downloads)).joinToString("\n"),
                leading = { ModIcon(vm.icons, hit.iconUrl) },
            )
            Presses(
                listOfNotNull(
                    Press(t("mods.install"), { content.openProject(hit) }, enabled = content.busy == null),
                    Install.page(hit.page)?.let { page -> Press(t("mobile.content.open_page"), { openLink(context, page) }) },
                ),
            )
        }
    }
    if (found.more) item { Presses(listOf(Press(t("mobile.content.more"), content::searchMore, enabled = !read.loading))) }
}

/** A Modrinth icon from the phone's cache, or a plain square while there is none. */
@Composable
private fun ModIcon(icons: IconCache, url: String?) {
    val image by produceState<ImageBitmap?>(null, url) {
        val bytes = icons.load(url) ?: return@produceState
        value = withContext(Dispatchers.Default) {
            runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() }.getOrNull()
        }
    }
    val shape = RoundedCornerShape(8.dp)
    val loaded = image
    if (loaded != null) {
        Image(loaded, contentDescription = null, modifier = Modifier.size(40.dp).clip(shape))
    } else {
        Box(Modifier.size(40.dp).background(LocalColors.current.neutral, shape))
    }
}

/** A .jar from the phone, or a Modrinth modpack imported into this server. */
@Composable
private fun AddFiles(vm: AppViewModel, content: ContentPages, kind: ContentKind) {
    val strings = vm.strings
    val pickJar = rememberFilePicker { file ->
        if (Content.accepts(kind, file.name)) content.uploadMod(file, strings)
        else content.tell(Said(strings.t("mobile.content.wrong_file", "kind" to Content.JAR), good = false))
    }
    val pickPack = rememberFilePicker { file ->
        if (Content.accepts(kind, file.name, modpack = true)) content.inspectModpack(file, strings)
        else content.tell(Said(strings.t("mobile.content.wrong_file", "kind" to Content.MODPACK), good = false))
    }
    val busy = content.busy
    Column {
        SectionTitle(t("mobile.content.add_file"))
        Card {
            Note(t("mobile.content.add_file_hint"), color = LocalColors.current.text, modifier = Modifier.padding(top = 10.dp))
            if (busy == "upload" || busy == "modpack") Note(t("mobile.content.reading"))
            Presses(
                listOf(
                    Press(t("mobile.content.add_file"), pickJar, enabled = busy == null),
                    Press(t("mobile.content.modpack_choose"), pickPack, enabled = busy == null),
                ),
            )
            Note(t("pack.into_intro"), color = LocalColors.current.text3)
            var packQuery by rememberSaveable { mutableStateOf("") }
            OutlinedTextField(
                value = packQuery,
                onValueChange = { packQuery = it.take(100) },
                label = { Text(t("pack.find_placeholder")) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
            )
            Presses(listOf(Press(t(if (busy == "find_pack") "mods.searching" else "pack.find"), { content.findPacks(packQuery, strings) }, enabled = busy == null && packQuery.isNotBlank())))
            content.packsFound?.let { found ->
                for (problem in found.problems) Note(problem)
                Note(t(if (found.hits.isEmpty()) "mods.nothing_found" else "pack.find_pick"))
                for (hit in found.hits) {
                    Item(hit.title, subtitle = Modpacks.about(strings, hit), onClick = { content.readFound(hit, strings) })
                }
            }
        }
    }
}

// ---------------------------------------------------------------- Bedrock packs

private fun LazyListScope.packsList(vm: AppViewModel, serverId: String, content: ContentPages, ask: (ContentAsk) -> Unit) {
    val read = content.addons
    val changes = Content.packChanges(vm.canDo(Permissions.MODS_MANAGE))
    val strings = vm.strings
    val idle = content.busy == null
    if (changes == Changes.NOT_ALLOWED) item { Note(t("mobile.content.not_allowed"), color = LocalColors.current.text3, modifier = Modifier.padding(top = 8.dp)) }
    item { SaidNote(content.said) }
    val list = read.value
    if (content.restartOffered && list?.running == true && vm.canControl()) {
        item {
            Card {
                Note(t("addons.restart"), color = LocalColors.current.text, modifier = Modifier.padding(top = 10.dp))
                Presses(
                    listOf(
                        Press(t("action.restart_now"), {
                            ask(ContentAsk(strings.t("confirm.restart_title"), strings.t("confirm.restart_body"), strings.t("action.restart"), false) {
                                content.restarted()
                                vm.act(serverId, "restart")
                            })
                        }),
                    ),
                )
            }
        }
    }
    read.problem?.let { problem ->
        item { Note(t("mobile.content.unknown")) }
        item { ProblemNote(problem, onRetry = content::loadAddons) }
    }
    if (list == null) {
        if (read.problem == null) item { Note(t("mobile.common.loading")) }
        return
    }
    when {
        list.world == null -> item { Note(t("addons.no_world"), color = LocalColors.current.warning) }
        else -> item { Note(t("mobile.content.pack_world", "world" to list.world)) }
    }
    if (list.running && changes == Changes.READY) item { Note(t("addons.running"), color = LocalColors.current.text3) }
    item { SectionTitle(vm.strings.tn("addons.installed", list.addons.size)) }
    if (list.addons.isEmpty()) item { Card { Item(t("addons.none"), subtitle = t("addons.none_hint")) } }
    items(list.addons, key = { it.uuid }) { addon ->
        Card {
            Item(
                addon.name.ifBlank { addon.folder },
                subtitle = listOf(t(Content.packKind(addon)), addon.version.ifBlank { t("value.unknown") }, t(if (addon.enabled) "addons.on" else "addons.off")).joinToString(" · "),
            )
            addon.description?.takeIf { it.isNotBlank() }?.let { Note(it.take(200)) }
            if (vm.technical) Note("${addon.uuid} · ${addon.folder}", color = LocalColors.current.text3)
            if (changes == Changes.READY) {
                Presses(
                    listOf(
                        Press(t(if (addon.enabled) "addons.turn_off" else "addons.turn_on"), { content.setAddon(addon, !addon.enabled, strings) }, enabled = idle && list.world != null),
                        Press(t("addons.remove"), { ask(removePack(vm, content, addon)) }, danger = true, enabled = idle),
                    ),
                )
            }
        }
    }
    if (changes == Changes.READY) item { AddPack(vm, content) }
}

private fun removePack(vm: AppViewModel, content: ContentPages, addon: Addon): ContentAsk {
    val strings = vm.strings
    return ContentAsk(strings.t("addons.remove_title", "name" to addon.name), strings.t("addons.remove_body"), strings.t("addons.remove"), true) {
        content.removeAddon(addon, strings)
    }
}

@Composable
private fun AddPack(vm: AppViewModel, content: ContentPages) {
    val strings = vm.strings
    val pick = rememberFilePicker { file ->
        if (Content.accepts(ContentKind.PACKS, file.name)) content.uploadAddon(file, strings)
        else content.tell(Said(strings.t("mobile.content.wrong_file", "kind" to Content.PACK_FILES.joinToString(", ")), good = false))
    }
    Column {
        SectionTitle(t("addons.add_title"))
        Card {
            Note(t("addons.add_lead"), color = LocalColors.current.text, modifier = Modifier.padding(top = 10.dp))
            if (content.busy == "upload") Note(t("addons.installing"))
            Presses(listOf(Press(t("mobile.content.add_pack"), pick, enabled = content.busy == null)))
        }
    }
}

// ---------------------------------------------------------------- questions

/** Remove, Update, Versions, Update all, install, the missing ones and a modpack: each asks first. */
@Composable
private fun ContentDialogs(vm: AppViewModel, content: ContentPages, ask: (ContentAsk) -> Unit) {
    val strings = vm.strings
    content.removing?.let { r ->
        val needed = r.impact.dependents.map { d -> strings.t("mods.needed_by", "name" to d.name, "requires" to (d.requires ?: strings.t("value.unknown"))) }
        val body = (needed.ifEmpty { listOf(strings.t("mods.not_needed")) } + listOfNotNull(r.impact.warning, strings.t("mods.remove_kept"))).joinToString("\n")
        Confirm(strings.t("mods.remove_title", "name" to r.mod.name), body, strings.t("mods.remove"), danger = true,
            onConfirm = { content.remove(strings) }, onDismiss = content::cancelRemove)
    }
    content.updating?.let { mod ->
        val body = Content.change(strings, mod.version, mod.updateAvailable?.latestVersion) + "\n" + strings.t("mods.update_archived")
        Confirm(strings.t("mods.update_title", "name" to mod.name), body, strings.t("mods.update"), danger = false,
            onConfirm = { content.update(strings) }, onDismiss = content::cancelUpdate)
    }
    content.versions?.let { v -> VersionsDialog(vm, content, v, ask) }
    content.plan?.let { UpdateAllDialog(vm, content) }
    content.project?.let { InstallDialog(vm, content) }
    content.missingPlan?.let { MissingDialog(vm, content) }
    content.modpack?.let { ModpackDialog(vm, content) }
    content.packUpdate?.let { PackUpdateDialog(vm, content) }
    content.makeOptions?.let { options -> MakeModpackDialog(vm, content, options) }
    if (content.askingPackLink) PackLinkDialog(vm, content)
    content.askingModrinth?.let { ask -> ModrinthProjectDialog(vm, content, ask) }
    if (content.askingServerLink) ServerProjectDialog(vm, content)
}

/** A sheet-like question: a title, scrolling words, and one button plus Cancel. */
@Composable
private fun Question(title: String, confirm: String?, enabled: Boolean = true, onConfirm: () -> Unit, onDismiss: () -> Unit, body: @Composable () -> Unit) {
    val colors = LocalColors.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Column(Modifier.verticalScroll(rememberScrollState())) { body() } },
        confirmButton = {
            if (confirm != null) {
                TextButton(onClick = onConfirm, enabled = enabled, colors = ButtonDefaults.textButtonColors(contentColor = colors.accentInk)) { Text(confirm) }
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(t("mobile.common.cancel")) } },
        containerColor = colors.float,
        titleContentColor = colors.text,
        textContentColor = colors.text2,
    )
}

@Composable
private fun Loading(read: Read<*>) {
    val problem = read.problem
    when {
        problem != null -> Text(problem.text(), color = LocalColors.current.danger)
        read.value == null -> Text(t("mobile.common.loading"))
    }
}

@Composable
private fun VersionsDialog(vm: AppViewModel, content: ContentPages, v: VersionsAsk, ask: (ContentAsk) -> Unit) {
    val strings = vm.strings
    val canChange = Content.changes(vm.canDo(Permissions.MODS_MANAGE), stateFor(vm, content.serverId, content.mods.problem != null)) == Changes.READY
    Question(strings.t("mods.versions_title", "name" to v.mod.name), confirm = null, onConfirm = {}, onDismiss = content::closeVersions) {
        Loading(v.versions)
        val list = v.versions.value?.versions ?: return@Question
        if (list.isEmpty()) Text(t("mods.no_versions"))
        for (version in list) VersionRow(vm, version, canChange) {
            val name = version.version ?: version.filename
            ask(ContentAsk(strings.t("mods.rollback_title", "version" to name), strings.t("mods.rollback_body"), strings.t("mods.rollback"), false) {
                content.rollBack(version, strings)
            })
        }
    }
}

@Composable
private fun VersionRow(vm: AppViewModel, version: ModVersion, canChange: Boolean, onUse: () -> Unit) {
    val colors = LocalColors.current
    Column(Modifier.padding(vertical = 6.dp)) {
        Text(version.version ?: version.filename, color = colors.text)
        Text(
            listOfNotNull(
                Display.dateTime(vm.strings, version.createdAt),
                t("mods.current").takeIf { version.isCurrent },
                t("mods.file_missing").takeIf { !version.exists },
            ).joinToString(" · "),
        )
        if (canChange && Content.canRollBack(version)) {
            TextButton(onClick = onUse) { Text(t("mods.rollback")) }
        }
    }
}

/** After a mod or version change: the ones to change (one Update all), the
 * ones with no version that fits (Turn off) and the ones nothing said about. */
@Composable
private fun StillFitCard(vm: AppViewModel, content: ContentPages, list: ModList, fit: ModFit, kind: ContentKind?, ready: Boolean) {
    val strings = vm.strings
    SectionTitle(t("fit.title"))
    Card {
        Note(StillFit.lead(strings, fit, kind == ContentKind.PLUGINS))
        for (row in fit.needsChange) Item(row.name, subtitle = "${Content.change(strings, row.from, row.to)}\n${row.why}")
        if (fit.noFit.isNotEmpty()) Note(t("fit.no_fit"), color = LocalColors.current.text)
        for (row in fit.noFit) {
            Item(row.name, subtitle = row.why)
            val mod = list.mods.firstOrNull { it.filename == row.filename && it.enabled }
            if (mod != null && vm.canDo(Permissions.MODS_MANAGE)) {
                Presses(listOf(Press(t("mods.turn_off"), { content.setEnabled(mod, false, strings) }, enabled = ready)))
            }
        }
        if (fit.cantTell.isNotEmpty()) Note(t("fit.cant_tell"), color = LocalColors.current.text)
        for (row in fit.cantTell) Item(row.name, subtitle = row.why)
        if (vm.canDo(Permissions.MODS_MANAGE)) {
            Presses(
                listOfNotNull(
                    Press(t("fit.change"), { content.openFit(fit) }, enabled = ready).takeIf { fit.needsChange.isNotEmpty() },
                    Press(t("fit.hide"), { content.hideFit(strings) }),
                ),
            )
        }
    }
}

/** The same mod twice, or mods that do the same job: which one to turn off
 * when the facts say. Turn off has Undo like the mod's own row. */
/** The modpack this server came from: Modpack update and Unlink. */
@Composable
private fun LinkedPackCard(vm: AppViewModel, content: ContentPages, pack: LinkedModpack, ready: Boolean) {
    val strings = vm.strings
    var unlinking by remember { mutableStateOf(false) }
    SectionTitle(t("linked.title"))
    Card {
        for (line in LinkedPacks.lines(strings, pack)) Note(line)
        if (vm.canDo(Permissions.MODS_MANAGE)) {
            Presses(
                listOfNotNull(
                    Press(t("linked.update"), { content.previewPackUpdate(pack.update?.versionId, strings) }, enabled = ready)
                        .takeIf { pack.update != null && vm.canDo(Permissions.SETTINGS_SERVER) },
                    Press(t("linked.unlink"), { unlinking = true }, enabled = ready),
                ),
            )
        }
    }
    if (unlinking) {
        Confirm(strings.t("linked.unlink_title", "name" to pack.name), strings.t("linked.unlink_body"), strings.t("linked.unlink"), danger = true,
            onConfirm = { unlinking = false; content.unlinkPack(strings) }, onDismiss = { unlinking = false })
    }
}

@Composable
private fun PackUpdateDialog(vm: AppViewModel, content: ContentPages) {
    val read = content.packUpdate ?: return
    val strings = vm.strings
    var usePackConfigs by remember(read.token) { mutableStateOf(false) }
    val problems = read.changes.problems
    Question(
        strings.t("linked.update_title", "name" to read.pack.name, "version" to read.changes.toVersion),
        confirm = strings.t("linked.update").takeIf { problems.isEmpty() },
        onConfirm = { content.updatePack(usePackConfigs, strings) },
        onDismiss = content::cancelPackUpdate,
    ) {
        if (problems.isNotEmpty()) {
            Text(t("pack.cant_import"), color = LocalColors.current.danger)
            for (p in problems) Text("• $p", color = LocalColors.current.danger)
            return@Question
        }
        for (line in LinkedPacks.changes(strings, read.changes)) Text(line, modifier = Modifier.padding(top = 4.dp))
        if (read.changes.configsChangedByYou.isNotEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                Checkbox(checked = usePackConfigs, onCheckedChange = { usePackConfigs = it })
                Text(t("linked.use_pack_configs"))
            }
        }
        for (step in LinkedPacks.steps(strings, read)) Text("• $step", modifier = Modifier.padding(top = 2.dp))
    }
}

/** Make a modpack from this server, then share it through the phone's
 * share menu or have the Discord bot post it. */
@Composable
private fun MakeModpackCard(vm: AppViewModel, content: ContentPages, idle: Boolean) {
    val strings = vm.strings
    val context = LocalContext.current
    SectionTitle(t("make.title"))
    Card {
        Note(t("make.intro"))
        val made = content.made
        if (made != null) {
            for (line in MadeModpacks.lines(strings, made)) Note(line, color = LocalColors.current.text)
        }
        Presses(
            listOfNotNull(
                Press(t("make.start"), { content.askMakeModpack(strings) }, enabled = idle),
                Press(t("make.share"), {
                    content.shareModpack(sharedFolder(context, "modpack"), strings) { shareFile(context, it, strings.t("make.share")) }
                }, enabled = idle).takeIf { made != null },
                Press(t("make.discord"), { content.postModpack(strings) }, enabled = idle).takeIf { made?.discord == true },
            ),
        )
        if (made != null) {
            Note(t("make.guide_title"), color = LocalColors.current.text)
            Note(t("make.guide"))
        }
    }
}

/** The download link (on, off, New link, Copy) and the server resource pack. */
@Composable
private fun DownloadLinkCard(vm: AppViewModel, content: ContentPages, idle: Boolean) {
    val strings = vm.strings
    val clipboard = LocalClipboardManager.current
    LaunchedEffect(content.serverId) { content.loadLink() }
    val link = content.link ?: return
    val owner = vm.canDo(Permissions.PC_SETTINGS)
    val game = vm.canDo(Permissions.SETTINGS_GAME)
    val pick = rememberFilePicker { file ->
        if (file.name.lowercase().endsWith(".zip")) content.uploadPack(file, strings)
        else content.tell(Said(strings.t("mobile.content.wrong_file", "kind" to ".zip"), good = false))
    }
    SectionTitle(t("link.title"))
    Card {
        Note(t("link.intro"))
        for (line in DownloadLinks.lines(strings, link, owner)) Note(line, color = LocalColors.current.text)
        Presses(
            listOfNotNull(
                Press(t(if (link.on) "link.turn_off" else "link.turn_on"), { content.switchLink(!link.on, strings) },
                    enabled = idle && owner && (link.on || link.whyNot == null)),
                Press(t("link.new"), { content.newLink(strings) }, enabled = idle).takeIf { link.on && owner },
                link.modpackUrl?.let { url ->
                    Press(t("link.copy"), {
                        clipboard.setText(AnnotatedString(url))
                        content.tell(Said(strings.t("link.copied"), good = true))
                    })
                },
            ),
        )
        Note(t("pack.title"), color = LocalColors.current.text)
        Note(t("pack.intro"))
        for (line in DownloadLinks.packLines(strings, link)) Note(line, color = LocalColors.current.text)
        link.resourcePack?.let { pack ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = pack.required, onCheckedChange = { content.setPackRequired(it, strings) }, enabled = idle && game)
                Text(t("pack.required"))
            }
        }
        Presses(
            listOfNotNull(
                Press(t("pack.pick"), pick, enabled = idle && game),
                Press(t("pack.use_link"), { content.askingPackLink = true }, enabled = idle && game),
                Press(t("pack.remove"), { content.removePack(strings) }, enabled = idle && game).takeIf { link.resourcePack != null },
            ),
        )
        Note(t("pack.tailscale_only"))
    }
}

/** Sign in with Modrinth, upload the modpack and own jars, the Server Project helper. */
@Composable
private fun ModrinthCard(vm: AppViewModel, content: ContentPages, idle: Boolean) {
    val strings = vm.strings
    val context = LocalContext.current
    LaunchedEffect(content.serverId) { content.loadModrinth() }
    LaunchedEffect(vm.modrinthReturn) {
        vm.modrinthReturn?.let {
            vm.modrinthReturn = null
            content.finishModrinth(it, strings)
        }
    }
    val share = content.modrinth ?: return
    val owner = vm.canDo(Permissions.PC_SETTINGS)
    val account = share.account
    var showSteps by remember { mutableStateOf(false) }
    SectionTitle(t("modrinth.title"))
    Card {
        Note(t("modrinth.intro"))
        for (line in ModrinthCards.lines(strings, share, owner)) Note(line, color = LocalColors.current.text)
        val made = share.made
        Presses(
            listOfNotNull(
                Press(t("modrinth.sign_in"), { content.signInModrinth({ openLink(context, it) }, strings) }, enabled = idle)
                    .takeIf { owner && account.available && !account.signedIn },
                Press(
                    if (made != null) strings.t("modrinth.upload_named", "name" to made.name, "version" to made.version) else t("modrinth.upload"),
                    {
                        if (share.project != null) content.uploadToModrinth("", "", strings)
                        else content.askingModrinth = ModrinthAsk(null, made?.name ?: "")
                    },
                    enabled = idle && account.signedIn && made != null,
                ),
                Press(t("modrinth.own_jar"), {
                    share.ownJars.firstOrNull { it.url == null }?.let { content.askingModrinth = ModrinthAsk(it, it.name) }
                }, enabled = idle && account.signedIn).takeIf { share.ownJars.any { it.url == null } },
                Press(t("modrinth.sign_out"), { content.signOutModrinth(strings) }, enabled = idle).takeIf { owner && account.signedIn },
            ),
        )
        Note(t("modrinth.review_note"))
        Note(t("modrinth.server_title"), color = LocalColors.current.text)
        Note(t("modrinth.server_intro"))
        for (line in ModrinthCards.serverLines(strings, share)) Note(line, color = LocalColors.current.text)
        if (share.address != null) {
            Presses(
                listOf(
                    Press(t("modrinth.server_how"), { showSteps = true }),
                    Press(t("modrinth.server_paste"), { content.askingServerLink = true }, enabled = idle),
                ),
            )
        }
    }
    if (showSteps) {
        Question(strings.t("modrinth.server_title"), confirm = null, onConfirm = {}, onDismiss = { showSteps = false }) {
            Text(ModrinthCards.steps(strings, share))
        }
    }
}

/** The anti-cheat picker: free from Modrinth, paid from the seller's page, or your own jar. */
@Composable
private fun AntiCheatCard(vm: AppViewModel, content: ContentPages, idle: Boolean) {
    val strings = vm.strings
    val context = LocalContext.current
    LaunchedEffect(content.serverId) { content.loadAntiCheat() }
    val pickJar = rememberFilePicker { file -> content.uploadMod(file, strings) }
    val picker = content.antiCheat ?: return
    SectionTitle(t("anticheat.title"))
    Card {
        Note(t("anticheat.intro"))
        Note(AntiCheats.status(strings, picker), color = LocalColors.current.text)
        if (!picker.supported) return@Card
        Note(t("anticheat.cpu"))
        if (!picker.mayInstall) Note(t("anticheat.no_install"))
        Note(t("anticheat.free"), color = LocalColors.current.text)
        for (item in picker.free) {
            for (line in AntiCheats.free(strings, item)) Note(line, color = LocalColors.current.text)
            if (!item.on && picker.mayInstall) {
                Presses(listOf(Press(strings.t("anticheat.add", "name" to item.name), { content.openProject(ModrinthHit(slug = item.project, title = item.name)) }, enabled = idle)))
            }
        }
        val ownJar = picker.mayInstall && picker.mayAddOwnJar
        if (picker.paid.isNotEmpty()) {
            Note(t("anticheat.paid"), color = LocalColors.current.text)
            Note(t("anticheat.buy_note"))
            for (item in picker.paid) {
                for (line in AntiCheats.paid(strings, item)) Note(line, color = LocalColors.current.text)
                Presses(
                    listOfNotNull(
                        Press(t("anticheat.seller"), { openLink(context, item.link) }),
                        item.setup?.let { setup -> Press(t("anticheat.setup"), { openLink(context, setup) }) },
                        Press(t("anticheat.from_file"), pickJar, enabled = idle).takeIf { ownJar && !item.on },
                    ),
                )
            }
            Note(t("anticheat.prices_note"))
        }
        Note(t("anticheat.custom"), color = LocalColors.current.text)
        Note(t("anticheat.custom_about"))
        if (ownJar) Presses(listOf(Press(t("anticheat.from_file"), pickJar, enabled = idle)))
        else Note(t("anticheat.no_own_jar"))
    }
}

/** The Modrinth address and one-line summary of a new project. */
@Composable
private fun ModrinthProjectDialog(vm: AppViewModel, content: ContentPages, ask: ModrinthAsk) {
    val strings = vm.strings
    var slug by remember { mutableStateOf(ModrinthCards.slug(ask.name)) }
    var summary by remember { mutableStateOf("") }
    Question(
        ask.jar?.name ?: strings.t("modrinth.title"),
        confirm = strings.t("modrinth.upload"),
        enabled = slug.isNotBlank() && summary.trim().length >= 3,
        onConfirm = { content.uploadToModrinth(slug.trim(), summary.trim(), strings) },
        onDismiss = { content.askingModrinth = null },
    ) {
        if (ask.jar != null) Note(t("modrinth.own_jar_pick"))
        OutlinedTextField(value = slug, onValueChange = { slug = it.take(64) }, label = { Text(t("modrinth.slug_prompt")) }, singleLine = true)
        OutlinedTextField(value = summary, onValueChange = { summary = it.take(256) }, label = { Text(t("modrinth.summary_prompt")) })
    }
}

@Composable
private fun ServerProjectDialog(vm: AppViewModel, content: ContentPages) {
    val strings = vm.strings
    var link by remember { mutableStateOf("") }
    Question(
        strings.t("modrinth.server_title"),
        confirm = strings.t("modrinth.server_paste"),
        onConfirm = { content.setServerProject(link.trim(), strings) },
        onDismiss = { content.askingServerLink = false },
    ) {
        OutlinedTextField(value = link, onValueChange = { link = it.take(200) }, label = { Text(t("modrinth.server_paste_prompt")) }, singleLine = true)
    }
}

@Composable
private fun PackLinkDialog(vm: AppViewModel, content: ContentPages) {
    val strings = vm.strings
    var url by remember { mutableStateOf("") }
    var sha1 by remember { mutableStateOf("") }
    Question(
        strings.t("pack.title"),
        confirm = strings.t("pack.use_link"),
        enabled = url.isNotBlank(),
        onConfirm = { content.setPackLink(url.trim(), sha1.trim(), strings) },
        onDismiss = { content.askingPackLink = false },
    ) {
        OutlinedTextField(value = url, onValueChange = { url = it.take(1000) }, label = { Text(t("pack.link_prompt")) }, singleLine = true)
        OutlinedTextField(value = sha1, onValueChange = { sha1 = it.take(40) }, label = { Text(t("pack.sha1_prompt")) }, singleLine = true)
    }
}

@Composable
private fun MakeModpackDialog(vm: AppViewModel, content: ContentPages, options: ModpackMakeOptions) {
    val strings = vm.strings
    var name by remember(options) { mutableStateOf(options.name) }
    var version by remember(options) { mutableStateOf(options.version) }
    var leaveOut by remember(options) { mutableStateOf(true) }
    var loader by remember(options) { mutableStateOf(options.playersLoaders.firstOrNull()?.id ?: "fabric") }
    var configs by remember(options) { mutableStateOf(setOf<String>()) }
    Question(
        strings.t("make.title"),
        confirm = strings.t("make.title"),
        enabled = name.isNotBlank() && version.isNotBlank(),
        onConfirm = { content.makeModpack(MakeModpack(name.trim(), version.trim(), leaveOut, configs.toList(), loader), strings) },
        onDismiss = content::cancelMakeModpack,
    ) {
        OutlinedTextField(value = name, onValueChange = { name = it.take(64) }, label = { Text(t("make.name")) }, singleLine = true)
        OutlinedTextField(value = version, onValueChange = { version = it.take(32) }, label = { Text(t("make.version")) }, singleLine = true)
        if (options.playersLoaders.isNotEmpty()) {
            Text(t("make.loader"), modifier = Modifier.padding(top = 6.dp))
            for (choice in options.playersLoaders) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    RadioButton(selected = loader == choice.id, onClick = { loader = choice.id })
                    Text(choice.name)
                }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
            Checkbox(checked = leaveOut, onCheckedChange = { leaveOut = it })
            Text(t("make.leave_out"))
        }
        Text(t("make.leave_out_hint"))
        Row(verticalAlignment = Alignment.CenterVertically) {
            // Greyed until the join check (the Companion mod) exists.
            Checkbox(checked = false, onCheckedChange = null, enabled = options.requiredToJoin.available)
            Text(t("make.required"))
        }
        if (!options.requiredToJoin.available) Text(t("make.required_later"))
        Text(t(if (options.configs.isEmpty()) "make.no_configs" else "make.configs"), modifier = Modifier.padding(top = 6.dp))
        for (config in options.configs) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(
                    checked = config.path in configs,
                    onCheckedChange = { on -> configs = if (on) configs + config.path else configs - config.path },
                )
                Text(config.path)
            }
        }
    }
}

@Composable
private fun SameJobCard(vm: AppViewModel, content: ContentPages, list: ModList, ready: Boolean) {
    val strings = vm.strings
    SectionTitle(t("same.title"))
    Card {
        for (overlap in list.overlaps) {
            Item(overlap.say, subtitle = overlap.why)
            if (!vm.canDo(Permissions.MODS_MANAGE)) continue
            for (row in SameJob.turnOff(overlap)) {
                val mod = list.mods.firstOrNull { it.filename == row.filename && it.enabled } ?: continue
                Item("${row.name} ${row.version}")
                Presses(listOf(Press(t("mods.turn_off"), { content.setEnabled(mod, false, strings) }, enabled = ready)))
            }
        }
    }
}

@Composable
private fun UpdateAllDialog(vm: AppViewModel, content: ContentPages) {
    val read = content.plan ?: return
    val strings = vm.strings
    val plan = read.value
    val count = content.ticked.size
    val blocked = plan?.let { UpdateAll.blocked(strings, it) }
    Question(
        strings.t("mobile.content.update_all"),
        confirm = if (plan != null && plan.updates.isNotEmpty()) strings.tn("mobile.content.update_all_go", count) else null,
        enabled = count > 0 && blocked == null,
        onConfirm = { content.updateAll(strings) },
        onDismiss = content::closeUpdateAll,
    ) {
        if (read.loading) Text(t("mobile.content.checking"))
        Loading(read)
        plan ?: return@Question
        if (plan.updates.isNotEmpty()) Text(t("mobile.content.update_all_lead")) else if (plan.unchecked.isEmpty()) Text(t("mobile.content.update_all_none"))
        blocked?.let { Text(it, color = LocalColors.current.warning, modifier = Modifier.padding(top = 6.dp)) }
        for (row in plan.updates) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                Checkbox(checked = row.filename in content.ticked, onCheckedChange = { on -> content.tick(row.filename, on) })
                Column {
                    Text(row.name.ifBlank { row.filename }, color = LocalColors.current.text)
                    Text(Content.change(strings, row.installedVersion, row.latestVersion))
                    if (!row.enabled) Text(t("mobile.content.update_all_off"), color = LocalColors.current.text3)
                }
            }
        }
        for (u in plan.unchecked) Text(t("mobile.content.update_all_unchecked", "name" to u.name, "reason" to u.reason), color = LocalColors.current.text3, modifier = Modifier.padding(top = 6.dp))
    }
}

@Composable
private fun InstallDialog(vm: AppViewModel, content: ContentPages) {
    val read = content.project ?: return
    val strings = vm.strings
    val project = read.value
    val version = project?.let { Install.latest(it) }
    var withDeps by remember(project) { mutableStateOf(true) }
    val context = LocalContext.current
    Question(
        strings.t("mods.install_title"),
        confirm = strings.t("mods.install_confirm").takeIf { version != null },
        onConfirm = { content.install(withDeps, strings) },
        onDismiss = content::closeProject,
    ) {
        Loading(read)
        project ?: return@Question
        if (version == null) {
            Text(t("mods.no_build"))
            return@Question
        }
        for (fact in Install.facts(strings, project, version)) Text(fact, modifier = Modifier.padding(top = 4.dp))
        Text(t("mods.install_checked"), modifier = Modifier.padding(top = 4.dp))
        val required = Install.required(version)
        if (required.isNotEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                Checkbox(checked = withDeps, onCheckedChange = { withDeps = it })
                Text(strings.tn("mods.install_deps", required.size))
            }
        }
        Install.page(project.project.page)?.let { page ->
            TextButton(onClick = { openLink(context, page) }) { Text(t("mobile.content.open_page")) }
        }
    }
}

@Composable
private fun MissingDialog(vm: AppViewModel, content: ContentPages) {
    val read = content.missingPlan ?: return
    val strings = vm.strings
    val plan = read.value
    val context = LocalContext.current
    Question(
        strings.t("mobile.content.install_missing"),
        confirm = strings.t("mods.install_confirm").takeIf { plan != null && plan.items.isNotEmpty() },
        onConfirm = { content.installMissing(strings) },
        onDismiss = content::closeMissing,
    ) {
        Loading(read)
        plan ?: return@Question
        if (plan.items.isNotEmpty()) Text(strings.tn("mobile.content.missing_plan", plan.items.size))
        for (item in plan.items) {
            Text(
                "• " + listOfNotNull(item.title ?: item.modId, item.versionNumber, item.size?.let { Display.bytes(strings, it) }).joinToString(" · "),
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        for (item in plan.unresolvable) {
            Text(
                t("mobile.content.cant_install", "name" to (item.name ?: item.modId), "reason" to item.reason),
                color = LocalColors.current.warning,
                modifier = Modifier.padding(top = 6.dp),
            )
            Install.page(item.page)?.let { page -> TextButton(onClick = { openLink(context, page) }) { Text(t("mobile.content.get_it")) } }
        }
    }
}

@Composable
private fun ModpackDialog(vm: AppViewModel, content: ContentPages) {
    val inspected = content.modpack ?: return
    val strings = vm.strings
    val pack = inspected.pack
    val unknown = strings.t("value.unknown")
    Question(
        strings.t("pack.import_title", "name" to pack.name.ifBlank { unknown }),
        confirm = strings.t("pack.import").takeIf { pack.canImport },
        onConfirm = { content.importModpack(strings) },
        onDismiss = content::cancelModpack,
    ) {
        Text(t("pack.minecraft") + ": " + (pack.minecraftVersion ?: unknown))
        Text(t("pack.loader") + ": " + listOfNotNull(pack.typeName, pack.loaderVersion).joinToString(" ").ifBlank { unknown })
        Text(t("pack.files") + ": " + t("pack.files_value", "count" to pack.downloadCount, "size" to Display.bytes(strings, pack.downloadBytes)))
        if (inspected.into?.versionChange == true) {
            Text(t("pack.version_change", "type" to (pack.typeName ?: unknown), "version" to (pack.minecraftVersion ?: unknown)), color = LocalColors.current.warning)
        }
        if (pack.problems.isNotEmpty()) {
            Text(t("pack.cant_import"), color = LocalColors.current.danger, modifier = Modifier.padding(top = 6.dp))
            for (p in pack.problems) Text("• $p", color = LocalColors.current.danger)
        }
        if (pack.clientOnly.isNotEmpty()) Text(t("pack.client_only", "names" to pack.clientOnly.joinToString(", ")), modifier = Modifier.padding(top = 6.dp))
        if (pack.skippedOverrides.isNotEmpty()) Text(t("pack.skipped_extras", "names" to pack.skippedOverrides.joinToString(", ")), modifier = Modifier.padding(top = 6.dp))
        for (note in Modpacks.notes(strings, pack)) Text(note, color = LocalColors.current.warning, modifier = Modifier.padding(top = 6.dp))
        if (pack.canImport) {
            Text(t("mobile.content.modpack_failed_note"), modifier = Modifier.padding(top = 8.dp))
            for (step in Modpacks.steps(strings, inspected)) Text("• $step", modifier = Modifier.padding(top = 2.dp))
        }
        Text(t("pack.all_files", "count" to pack.files.size), color = LocalColors.current.text, modifier = Modifier.padding(top = 8.dp))
        for (file in pack.files.take(200)) {
            val status = Modpacks.fileStatus(file)
            Text(
                "${file.name} · ${t(status.key)}" + (file.reason?.let { " ($it)" } ?: ""),
                color = if (status.tone == Tone.DANGER) LocalColors.current.danger else LocalColors.current.text2,
            )
        }
    }
}
