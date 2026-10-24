package io.github.peyoteheadlights.mcsc.push

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import io.github.peyoteheadlights.mcsc.R
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.Community
import io.github.peyoteheadlights.mcsc.graph
import io.github.peyoteheadlights.mcsc.ui.Message
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Start or Say no, pressed on a start request's lock-screen alert. Sent
 * with the app's own sign-in, like the button in the app; the alert then
 * says what happened, in the PC's words when it said no (already answered,
 * the 15 minutes are up, or this account may not start that server).
 */
class StartRequestReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val serverId = intent.getStringExtra(EXTRA_SERVER) ?: return
        val id = intent.getIntExtra(EXTRA_ALERT, 0)
        val start = intent.getBooleanExtra(EXTRA_START, false)
        val app = context.applicationContext
        val graph = app.graph
        val client = graph.client() ?: return
        // Android gives a receiver about 10 seconds once it says it isn't done.
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val words = withTimeoutOrNull(9_000) {
                    try {
                        Community.answered(graph.strings, client.answerStartRequest(serverId, start).answer)
                    } catch (e: AgentException) {
                        graph.prefs.lastProblem = e.key
                        Message.of(e).text(graph.strings)
                    }
                } ?: graph.strings.t("mobile.error.unreachable")
                Alerts.show(app, id, graph.strings.t("alert_event.start_requests"), words, serverId)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        private const val EXTRA_SERVER = "server"
        private const val EXTRA_ALERT = "alert"
        private const val EXTRA_START = "start"

        /** The Start or Say no button, for the alert shown as [id]. */
        fun action(context: Context, id: Int, serverId: String, start: Boolean): NotificationCompat.Action {
            val intent = Intent(context, StartRequestReceiver::class.java)
                .putExtra(EXTRA_SERVER, serverId)
                .putExtra(EXTRA_ALERT, id)
                .putExtra(EXTRA_START, start)
            val press = PendingIntent.getBroadcast(
                context,
                id * 2 + if (start) 1 else 0,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val words = context.graph.strings.t(if (start) "desktop.start_request.start" else "desktop.start_request.no")
            return NotificationCompat.Action.Builder(R.drawable.ic_notification, words, press)
                // Starting a server from a locked phone would let anyone holding it do it.
                .setAuthenticationRequired(true)
                .build()
        }
    }
}
