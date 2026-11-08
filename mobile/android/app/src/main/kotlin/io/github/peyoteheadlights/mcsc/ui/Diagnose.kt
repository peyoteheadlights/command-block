package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.AgentClient
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.Diagnose
import io.github.peyoteheadlights.mcsc.core.DiagnoseStarted
import io.github.peyoteheadlights.mcsc.core.Diagnosis
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Diagnose server on the phone: the same run as the desktop window
 * (desktop/diagnose.py). The agent does the work and keeps the list; this
 * reads it once a second while it runs. The Microsoft sign-ins stay in the
 * desktop app, so a join that needs one says so.
 */
class DiagnosePage(
    val serverId: String,
    private val scope: CoroutineScope,
    private val client: () -> AgentClient?,
    private val noted: (AgentException) -> Unit,
) {
    var result: Diagnosis? by mutableStateOf(null)
        private set

    /** A question the agent asked before starting (the owner is playing, or a mod server). */
    var asking: DiagnoseStarted? by mutableStateOf(null)
        private set
    var problem: Message? by mutableStateOf(null)
        private set
    var busy: Boolean by mutableStateOf(false)
        private set
    var notStarted: Boolean by mutableStateOf(false)
        private set

    private var goAhead = false
    private var mods: Boolean? = null

    fun load() = work { c -> result = c.diagnosis(serverId) }

    fun start() = work { c ->
        notStarted = false
        val answer = c.startDiagnose(serverId, goAhead, mods)
        if (answer.ask != null || answer.askMods != null) {
            asking = answer
        } else {
            goAhead = false
            mods = null
            result = c.diagnosis(serverId)
        }
    }

    /** The answer to [asking]: yes or no. */
    fun answer(yes: Boolean) {
        val question = asking ?: return
        asking = null
        if (question.askMods != null) {
            mods = yes // no still runs the other joins, like the desktop app
            start()
        } else if (yes) {
            goAhead = true
            start()
        } else {
            notStarted = true
        }
    }

    /** Apply all ("all"), Apply safe ("safe") or Deny all ("none"). */
    fun pick(pick: String) = work { c ->
        c.diagnoseChanges(serverId, pick)
        result = c.diagnosis(serverId)
    }

    private fun work(block: suspend (AgentClient) -> Unit) {
        val c = client() ?: return
        busy = true
        problem = null
        scope.launch {
            try {
                block(c)
            } catch (e: AgentException) {
                noted(e)
                problem = Message.of(e)
            }
            busy = false
        }
    }
}

@Composable
fun DiagnoseScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit) {
    val page = vm.pages(serverId).diagnose
    val result = page.result
    val running = result?.running == true
    LaunchedEffect(serverId) { page.load() }
    LaunchedEffect(running) {
        while (running) {
            delay(1000)
            page.load()
        }
    }
    val waiting = result?.let { Diagnose.waiting(it) }.orEmpty()
    ServerFrame(
        vm, serverId,
        title = t("action.diagnose"),
        onBack = onBack,
        refreshing = false,
        onRefresh = page::load,
        overlay = {
            page.asking?.let { question ->
                Confirm(
                    title = t("action.diagnose"),
                    body = question.askMods ?: question.ask.orEmpty(),
                    confirm = t(if (question.askMods != null) "diagnose.mods_yes" else "diagnose.go_ahead"),
                    dismiss = if (question.askMods != null) t("mobile.diagnose.mods_no") else null,
                    danger = false,
                    onConfirm = { page.answer(true) },
                    onDismiss = { page.answer(false) },
                )
            }
        },
    ) {
        item {
            Card(Modifier.padding(top = 8.dp)) {
                val status = when {
                    page.notStarted -> "diagnose.not_started"
                    result == null -> "mobile.diagnose.intro"
                    else -> Diagnose.statusKey(result)
                }
                Note(t(status))
                if (!running && waiting.isEmpty()) {
                    Button(
                        onClick = page::start,
                        enabled = !page.busy,
                        modifier = Modifier.fillMaxWidth().padding(16.dp),
                    ) { Text(t(if (result == null) "action.diagnose" else "diagnose.again")) }
                }
            }
        }
        page.problem?.let { problem -> item { ProblemNote(problem) } }
        if (result == null) return@ServerFrame
        if (result.signIn) item { Note(t("mobile.diagnose.sign_in")) }
        if (result.signInMods) item { Note(t("mobile.diagnose.sign_in_mods")) }
        for ((group, lines) in Diagnose.groups(result.lines)) {
            item { SectionTitle(group) }
            item {
                Card { for (line in lines) Item("${Diagnose.mark(line.ok)}  ${line.what}", subtitle = line.detail) }
            }
        }
        if (result.known.isNotEmpty()) {
            item { SectionTitle(t("diagnose.known")) }
            item {
                Card {
                    for (known in result.known) {
                        val more = listOfNotNull(known.change?.let { t("diagnose.known_change", "change" to it) }, known.link)
                        Item(known.say, subtitle = more.joinToString("\n").ifEmpty { null })
                    }
                }
            }
        }
        if (result.done.isNotEmpty()) {
            item { SectionTitle(t("diagnose.made")) }
            item { Card { for (done in result.done) Item("${Diagnose.doneMark(done)}  ${done.title}", subtitle = done.detail) } }
        }
        if (result.steps.isNotEmpty()) {
            item { SectionTitle(t("diagnose.yourself")) }
            item { Card { for (step in result.steps) Item(step) } }
        }
        for (safe in listOf(true, false)) {
            val group = waiting.filter { it.safe == safe }
            if (group.isEmpty()) continue
            item { SectionTitle(t(if (safe) "diagnose.safe" else "diagnose.risky")) }
            item { Card { for (change in group) Item(change.title, subtitle = change.why.ifEmpty { null }) } }
        }
        if (waiting.isNotEmpty()) {
            item {
                Row(
                    Modifier.fillMaxWidth().padding(16.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    for (pick in Diagnose.PICKS) {
                        val enabled = !page.busy && (pick != "safe" || waiting.any { it.safe })
                        val label = t("diagnose.apply_$pick")
                        if (pick == "all") {
                            Button(onClick = { page.pick(pick) }, enabled = enabled, modifier = Modifier.weight(1f)) { Text(label) }
                        } else {
                            OutlinedButton(onClick = { page.pick(pick) }, enabled = enabled, modifier = Modifier.weight(1f)) { Text(label) }
                        }
                    }
                }
            }
        }
    }
}
