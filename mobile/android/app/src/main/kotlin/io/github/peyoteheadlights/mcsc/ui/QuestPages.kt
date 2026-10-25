package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import io.github.peyoteheadlights.mcsc.core.AgentClient
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.Community
import io.github.peyoteheadlights.mcsc.core.EventsPage
import io.github.peyoteheadlights.mcsc.core.QuestForm
import io.github.peyoteheadlights.mcsc.core.QuestsPage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * One server's Quests and events: the quests and their board, "done"
 * claims to approve, ideas from /quest suggest, a new quest, the events
 * with their attendance, and the polls. The agent checks every value and
 * the "Quests and events" permission again.
 */
class QuestPages(
    val serverId: String,
    private val scope: CoroutineScope,
    private val client: () -> AgentClient?,
    private val noted: (AgentException) -> Unit,
) {
    var page: Read<QuestsPage> by mutableStateOf(Read())
        private set
    var events: Read<EventsPage> by mutableStateOf(Read())
        private set

    /** "end:3", "claim:4", "make" while that button works. */
    var busy: String? by mutableStateOf(null)
        private set
    var problem: Message? by mutableStateOf(null)
        private set

    /** What the agent said was wrong with the new quest, kept by the form. */
    var formProblem: Message? by mutableStateOf(null)
        private set

    /** The one claim screenshot shown (claim id and the picture's bytes);
     * showing another lets this one go, so a long session keeps one picture. */
    var screenshot: Pair<Long, ByteArray>? by mutableStateOf(null)
        private set

    fun load() {
        val c = client() ?: return
        page = page.copy(loading = true)
        events = events.copy(loading = true)
        scope.launch {
            page = try { Read(c.quests(serverId)) } catch (e: AgentException) {
                noted(e)
                page.copy(problem = Message.of(e), loading = false)
            }
            events = try { Read(c.questEvents(serverId)) } catch (e: AgentException) {
                noted(e)
                events.copy(problem = Message.of(e), loading = false)
            }
        }
    }

    /** [done] runs once the agent made it, to empty the form. */
    fun make(form: QuestForm, done: () -> Unit) = quests("make") { c ->
        formProblem = null
        try {
            c.makeQuest(serverId, Community.questBody(form)).also { done() }
        } catch (e: AgentException.Failed) {
            formProblem = Message.of(e)
            null
        }
    }

    fun end(questId: Long) = quests("end:$questId") { it.endQuest(serverId, questId) }

    fun answerClaim(claimId: Long, approve: Boolean) = quests("claim:$claimId") { it.answerClaim(serverId, claimId, approve) }

    fun turnDown(ideaId: Long) = quests("idea:$ideaId") { it.turnDownIdea(serverId, ideaId) }

    fun showScreenshot(claimId: Long) = work("shot:$claimId") { c ->
        val shot = c.claimScreenshot(serverId, claimId)
        val bytes = runCatching { java.util.Base64.getDecoder().decode(shot.image) }.getOrNull()
        if (bytes == null) problem = Message("quests.screenshot_unreadable") else screenshot = claimId to bytes
    }

    fun callOff(eventId: Long) = events("event:$eventId") { it.callOffEvent(serverId, eventId) }

    fun approve(eventId: Long) = events("event:$eventId") { it.approveAttendance(serverId, eventId) }

    fun fix(eventId: Long, discordId: String, attended: Boolean) =
        events("fix:$eventId:$discordId") { it.fixAttendance(serverId, eventId, discordId, attended) }

    fun closePoll(pollId: Long) = events("poll:$pollId") { it.closePoll(serverId, pollId) }

    private fun quests(name: String, call: suspend (AgentClient) -> QuestsPage?) = work(name) { c ->
        call(c)?.let { page = Read(it) }
    }

    private fun events(name: String, call: suspend (AgentClient) -> EventsPage) = work(name) { c ->
        events = Read(call(c))
    }

    /** One button at a time; what went wrong stays in [problem]. */
    private fun work(name: String, call: suspend (AgentClient) -> Unit) {
        val c = client() ?: return
        if (busy != null) return
        busy = name
        problem = null
        scope.launch {
            try {
                call(c)
            } catch (e: AgentException) {
                noted(e)
                problem = Message.of(e)
            } finally {
                busy = null
            }
        }
    }
}
