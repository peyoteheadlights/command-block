package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.Store

/**
 * A server's Store tab, like the desktop app (desktop/store.py): connected
 * or not, the store's link, its packages with prices, Open Tebex, the
 * copy-paste checklist and the Store ranks. The key is typed once and never
 * shown again: the agent keeps it.
 */
@Composable
fun StoreScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit, onSettings: () -> Unit) {
    val pages = vm.pages(serverId)
    val strings = vm.strings
    val colors = LocalColors.current
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    var key by remember { mutableStateOf("") }
    // own -> the gameplay question, never -> the never-sold list (with Mojang's warning when it changes gameplay).
    var asking by remember { mutableStateOf<String?>(null) }
    var changesGameplay by remember { mutableStateOf(false) }
    var remind by remember { mutableStateOf<List<String>>(emptyList()) }
    LaunchedEffect(serverId) { pages.loadStore() }
    val read = pages.store
    val view = read.value

    ServerFrame(
        vm, serverId,
        title = t("desktop.store.title"),
        onBack = onBack,
        refreshing = read.loading,
        onRefresh = pages::loadStore,
        overlay = {
            val never = view?.neverSold.orEmpty().joinToString("\n") { "- $it" }
            when {
                remind.isNotEmpty() -> Confirm(
                    t("desktop.store.remind_title"),
                    t("desktop.store.remind", "names" to remind.joinToString(", ")) + "\n\n" + never,
                    t("desktop.store.got_it"),
                    danger = false,
                    onConfirm = { remind = emptyList() },
                    onDismiss = { remind = emptyList() },
                )
                asking == "own" -> Confirm(
                    t("desktop.store.own_package"), t("desktop.store.gameplay_question"), t("desktop.store.gameplay_yes"),
                    danger = false,
                    onConfirm = { changesGameplay = true; asking = "never" },
                    onDismiss = { changesGameplay = false; asking = "never" },
                    dismiss = t("desktop.store.gameplay_no"),
                )
                asking == "never" -> Confirm(
                    t("desktop.store.own_package"),
                    (if (changesGameplay) view?.gameplayWarning.orEmpty() + "\n\n" else "") + t("desktop.store.never_sold") + "\n" + never,
                    t("desktop.store.got_it"),
                    danger = false,
                    onConfirm = { asking = null },
                    onDismiss = { asking = null },
                )
            }
        },
    ) {
        item { Note(t("desktop.store.hint")) }
        read.problem?.let { item { ProblemNote(it, onRetry = pages::loadStore) } }
        if (view == null) {
            if (read.problem == null) item { Note(t("mobile.common.loading")) }
            return@ServerFrame
        }
        val usable = Store.usable(view)
        item {
            Card {
                when {
                    !view.offered -> Note(view.reason.orEmpty())
                    view.needsType -> {
                        Note(t("desktop.store.needs_type"), color = colors.text)
                        Item(t("perf.open_settings"), onClick = onSettings)
                    }
                    else -> {
                        Item(t(if (view.connected) "desktop.store.connected" else "desktop.store.not_connected"))
                        view.store.link?.let { Note(t("desktop.store.link", "link" to it)) }
                        Note(t("desktop.store.buy", "command" to view.buyCommand))
                        view.offlineWarning?.let { Note(it, color = colors.danger) }
                        if (view.ranksByHand) Note(t("desktop.store.ranks_by_hand"))
                        val ranks = view.ranks.filter { it.made }.joinToString(", ") { it.name }
                        if (ranks.isNotEmpty()) Note(t("desktop.store.ranks", "ranks" to ranks))
                        Note(t("desktop.store.chat_note"), color = colors.text3)
                        Note(view.fees, color = colors.text3)
                    }
                }
                if (usable) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        PasswordField(key, { key = it.take(200) }, t("desktop.store.key"), hint = t("desktop.store.key_hint"))
                    }
                    Presses(
                        listOf(
                            Press(t("desktop.store.connect"), { pages.connectStore(key, strings) { key = "" } }, enabled = !pages.storeBusy && key.isNotBlank()),
                            Press(t("desktop.store.check"), { pages.checkStore(strings) { remind = it } }, enabled = !pages.storeBusy && view.keySaved),
                            Press(t("desktop.store.own_package"), { asking = "own" }),
                        ),
                    )
                }
                Item(t("desktop.store.open_tebex"), external = true, onClick = { openLink(context, view.openTebex) })
            }
        }
        item { SaidNote(pages.storeSaid) }
        view.packages?.let { packages ->
            item { SectionTitle(t("desktop.store.packages")) }
            item {
                Card {
                    if (packages.isEmpty()) Note(t("desktop.store.no_packages"))
                    for (p in packages) Item(Store.packageLine(view, p))
                }
            }
        }
        if (usable) {
            item { SectionTitle(t("desktop.store.checklist")) }
            item {
                Card {
                    Note(t("desktop.store.checklist_hint"))
                    Text(view.checklist, fontFamily = FontFamily.Monospace, color = colors.text, modifier = Modifier.padding(16.dp))
                    Presses(listOf(Press(t("desktop.copy"), { clipboard.setText(AnnotatedString(view.checklist)) })))
                }
            }
        }
    }
}
