package io.github.peyoteheadlights.mcsc.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.Community
import io.github.peyoteheadlights.mcsc.core.EventsPage
import io.github.peyoteheadlights.mcsc.core.GameEvent
import io.github.peyoteheadlights.mcsc.core.QuestForm
import io.github.peyoteheadlights.mcsc.core.QuestsPage

/** A button waiting for "yes": end a quest or call off an event. */
private data class Ending(val kind: String, val id: Long, val title: String)

/**
 * Quests and events for the owner and people with "Quests and events": the
 * quests going on (joined in Discord), claims to approve with their
 * screenshot, ideas from /quest suggest, a new quest, then the events with
 * who came, and the polls. Events and polls are made in the desktop app or
 * in Discord (/event, /poll); here they're looked after.
 */
@Composable
fun QuestsScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit) {
    val quests = vm.pages(serverId).quests
    var ending by remember { mutableStateOf<Ending?>(null) }
    // The idea "Make it" copies into the form.
    var form by remember(serverId) { mutableStateOf(QuestForm()) }
    LaunchedEffect(serverId) { quests.load() }

    ServerFrame(
        vm, serverId,
        title = t("page.quests"),
        onBack = onBack,
        refreshing = false,
        onRefresh = quests::load,
        overlay = {
            ending?.let { e ->
                Confirm(
                    title = t("${e.kind}_title", "title" to e.title),
                    body = t("${e.kind}_body"),
                    confirm = t(e.kind),
                    danger = true,
                    onConfirm = {
                        ending = null
                        if (e.kind == "quests.end") quests.end(e.id) else quests.callOff(e.id)
                    },
                    onDismiss = { ending = null },
                )
            }
        },
    ) {
        quests.problem?.let { item { ProblemNote(it) } }
        val page = quests.page.value
        when {
            quests.page.problem != null -> item { ProblemNote(quests.page.problem!!, quests::load) }
            page == null -> item { Note(t("mobile.common.loading")) }
            else -> {
                questsPart(vm, quests, page, onEnd = { ending = Ending("quests.end", it.id, it.title) }, onUse = { idea ->
                    form = form.copy(title = idea.title, details = idea.details, suggestionId = idea.id)
                })
                item { NewQuest(quests, page, form, onChange = { form = it }) }
            }
        }
        quests.events.problem?.let { item { ProblemNote(it, quests::load) } }
        quests.events.value?.let { events ->
            eventsPart(vm, quests, events, page, onCallOff = { ending = Ending("game_events.call_off", it.id, it.title) })
        }
    }
}

private fun LazyListScope.questsPart(
    vm: AppViewModel,
    quests: QuestPages,
    page: QuestsPage,
    onEnd: (io.github.peyoteheadlights.mcsc.core.Quest) -> Unit,
    onUse: (io.github.peyoteheadlights.mcsc.core.QuestIdea) -> Unit,
) {
    val roles = page.roleNames
    if (!page.bot) item { Note(t("quests.no_bot"), color = LocalColors.current.text3) }
    item { SectionTitle(t("quests.list")) }
    if (page.quests.isEmpty()) item { Card { Item(t("quests.none")) } }
    items(page.quests, key = { "q${it.id}" }) { quest ->
        Card {
            Item("${quest.id}. ${quest.title}", subtitle = Community.questLine(vm.strings, quest, roles))
            if (quest.details.isNotBlank()) Note(quest.details, color = LocalColors.current.text3)
            Note(Community.membersLine(vm.strings, quest), color = LocalColors.current.text3)
            Buttons(listOf(Press(t("quests.end"), { onEnd(quest) }, danger = true)), quests.busy != null)
        }
    }
    if (page.claims.isNotEmpty()) item { SectionTitle(t("quests.claims")) }
    items(page.claims, key = { "c${it.id}" }) { claim ->
        Card {
            Item(t("quests.claim", "name" to claim.discordName, "title" to claim.title), subtitle = claim.note?.takeIf { it.isNotBlank() })
            quests.screenshot?.takeIf { it.first == claim.id }?.let { (_, bytes) -> Screenshot(bytes) }
            val presses = listOfNotNull(
                Press(t("quests.approve"), { quests.answerClaim(claim.id, true) }),
                Press(t("quests.refuse"), { quests.answerClaim(claim.id, false) }),
                claim.screenshot?.takeIf { quests.screenshot?.first != claim.id }?.let {
                    Press(t("quests.see_screenshot"), { quests.showScreenshot(claim.id) })
                },
            )
            Buttons(presses, quests.busy != null)
        }
    }
    if (page.suggestions.isNotEmpty()) item { SectionTitle(t("quests.ideas")) }
    items(page.suggestions, key = { "i${it.id}" }) { idea ->
        Card {
            Item(idea.title, subtitle = t("quests.idea_from", "name" to idea.discordName, "details" to idea.details))
            Buttons(
                listOf(
                    Press(t("quests.make_it"), { onUse(idea) }),
                    Press(t("quests.turn_down"), { quests.turnDown(idea.id) }),
                ),
                quests.busy != null,
            )
        }
    }
}

/** A claim's screenshot, made smaller than the phone's screen before it is drawn. */
@Composable
private fun Screenshot(bytes: ByteArray) {
    val image = remember(bytes) {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (bounds.outWidth / sample > SHOT_WIDTH) sample *= 2
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
    }
    if (image == null) {
        Note(t("quests.screenshot_unreadable"), color = LocalColors.current.danger)
    } else {
        Image(image, contentDescription = t("quests.see_screenshot"), modifier = Modifier.fillMaxWidth().padding(16.dp))
    }
}

/** Wide enough for any phone screen; the full picture stays on the PC. */
private const val SHOT_WIDTH = 1080

@Composable
private fun NewQuest(quests: QuestPages, page: QuestsPage, form: QuestForm, onChange: (QuestForm) -> Unit) {
    val colors = LocalColors.current
    val kinds = page.kinds.ifEmpty { listOf("playtime") }
    val kind = form.kind.takeIf { it in kinds } ?: kinds.first()
    val roles = page.roleNames.entries.sortedBy { it.value.lowercase() }
    Column {
        SectionTitle(t("quests.new"))
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Field(t("quests.title"), form.title, 80) { onChange(form.copy(title = it)) }
                Field(t("quests.details"), form.details, 300) { onChange(form.copy(details = it)) }
                Picker(t("quests.kind"), t("quests.kind.$kind"), kinds.map { it to t("quests.kind.$it") }) { onChange(form.copy(kind = it)) }
                Text(Community.goalHint(LocalStrings.current, kind, page.kinds), color = colors.text3)
                if (Community.needsGoal(kind)) Field(t("quests.goal"), form.goal, 100) { onChange(form.copy(goal = it)) }
                if (Community.needsCount(kind)) Count(t("quests.count"), form.count, 1..100_000) { onChange(form.copy(count = it)) }
                Picker(t("quests.repeat"), t("quests.repeat_pick.${form.repeat}"), Community.REPEATS.map { it to t("quests.repeat_pick.$it") }) {
                    onChange(form.copy(repeat = it))
                }
                Text(t("quests.rewards_title"), color = colors.text)
                Text(t("quests.rewards_hint"), color = colors.text3)
                if (page.bot && roles.isNotEmpty()) {
                    val none = "" to t("quests.reward_none")
                    Picker(
                        t("quests.reward_role"),
                        form.roleId?.let { page.roleNames[it] } ?: none.second,
                        listOf(none) + roles.map { it.key to it.value },
                    ) { onChange(form.copy(roleId = it.ifEmpty { null })) }
                }
                Field(t("quests.reward_item"), form.item, 100) { onChange(form.copy(item = it)) }
                if (form.item.isNotBlank()) Count(t("quests.reward_item_count"), form.itemCount, 1..64) { onChange(form.copy(itemCount = it)) }
                Count(t("quests.reward_xp"), form.xpLevels, 0..30) { onChange(form.copy(xpLevels = it)) }
                Count(t("quests.reward_level_xp"), form.levelXp, 0..500) { onChange(form.copy(levelXp = it)) }
                quests.formProblem?.let { Text(it.text(), color = colors.danger) }
                Button(
                    onClick = { quests.make(form.copy(kind = kind)) { onChange(QuestForm()) } },
                    enabled = quests.busy == null && form.title.isNotBlank(),
                ) { Text(t("quests.create")) }
                Text(t("mobile.quests.last_day"), color = colors.text3)
            }
        }
    }
}

@Composable
private fun Field(label: String, value: String, max: Int, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = { onChange(it.take(max)) },
        label = { Text(label) },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
}

/** A whole number kept inside [range]; an empty box reads as the lowest. */
@Composable
private fun Count(label: String, value: Int, range: IntRange, onChange: (Int) -> Unit) {
    var typed by rememberSaveable(label) { mutableStateOf(value.toString()) }
    LaunchedEffect(value) { if (typed.toIntOrNull() != value) typed = value.toString() }
    OutlinedTextField(
        value = typed,
        onValueChange = { text ->
            typed = text.filter(Char::isDigit).take(6)
            onChange((typed.toIntOrNull() ?: range.first).coerceIn(range))
        },
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth(),
    )
}

private fun LazyListScope.eventsPart(
    vm: AppViewModel,
    quests: QuestPages,
    events: EventsPage,
    page: QuestsPage?,
    onCallOff: (GameEvent) -> Unit,
) {
    val roles = page?.roleNames.orEmpty()
    item { SectionTitle(t("game_events.list")) }
    if (events.events.isEmpty()) item { Card { Item(t("game_events.none")) } }
    items(events.events, key = { "e${it.id}" }) { event ->
        Card {
            Item(event.title, subtitle = Community.eventLine(vm.strings, event, roles))
            if (event.details.isNotBlank()) Note(event.details, color = LocalColors.current.text3)
            Note(Community.answersLine(vm.strings, event), color = LocalColors.current.text3)
            if (Community.canCallOff(event)) {
                Buttons(listOf(Press(t("game_events.call_off"), { onCallOff(event) }, danger = true)), quests.busy != null)
            }
            if (event.state in Community.ATTENDANCE_STATES) WhoCame(vm, quests, event)
        }
    }
    item { SectionTitle(t("game_events.polls")) }
    if (events.polls.isEmpty()) item { Card { Item(t("game_events.no_polls")) } }
    items(events.polls, key = { "p${it.id}" }) { poll ->
        Card {
            Item(poll.question, subtitle = Community.pollCounts(poll))
            Note(Community.pollState(vm.strings, poll), color = LocalColors.current.text3)
            if (poll.open) Buttons(listOf(Press(t("game_events.close_poll"), { quests.closePoll(poll.id) })), quests.busy != null)
        }
    }
    item { Note(t("mobile.quests.make_events"), color = LocalColors.current.text3) }
}

/** Who came: ticks, changeable for 7 days after the end; Approve for a list to OK. */
@Composable
private fun WhoCame(vm: AppViewModel, quests: QuestPages, event: GameEvent) {
    val hint = if (event.state == "approving") "game_events.approve_hint" else "game_events.fix_hint"
    Note(t(if (event.attendance.isEmpty()) "game_events.nobody" else hint), color = LocalColors.current.text3)
    for (person in event.attendance) {
        Tick(
            Community.personLine(vm.strings, person),
            checked = person.attended,
            enabled = event.fixable && quests.busy == null,
        ) { quests.fix(event.id, person.discordId, it) }
    }
    if (event.state == "approving") {
        Buttons(listOf(Press(t("game_events.approve"), { quests.approve(event.id) })), quests.busy != null)
    }
}

/** The card's buttons, all waiting while one of them works. */
@Composable
private fun Buttons(presses: List<Press>, busy: Boolean) = Presses(presses.map { it.copy(enabled = !busy) })
