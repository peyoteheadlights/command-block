package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

// The Discord bot's community features on the phone (Phase 11): start
// requests, the bot's state, Linked accounts, whitelist requests, quests,
// events and polls. The agent's routes: agent/api/routes/server.py
// (start-request), discord.py, discord_links.py, whitelist.py, quests.py,
// events.py. Mirrors the desktop app's desktop/discord.py, players.py,
// quests.py and events.py, which say the same things.

// ------------------------------------------------------------ start requests
@Serializable
data class StartRequestAnswered(val answer: String)

// ------------------------------------------------------------ the bot (owner)
@Serializable
data class GuildReport(
    /** Null until the bot has checked this Discord server. */
    val problems: List<JsonObject>? = null,
    @SerialName("checked_at") val checkedAt: Double? = null,
)

@Serializable
data class DiscordGuild(
    val id: String,
    val name: String,
    val servers: List<String> = emptyList(),
    val options: JsonObject = JsonObject(emptyMap()),
    val report: GuildReport? = null,
) {
    val removeOnLeave: Boolean get() = (options["remove_on_leave"] as? JsonPrimitive)?.contentOrNull == "true"
}

@Serializable
data class DiscordStatus(
    @SerialName("token_set") val tokenSet: Boolean = false,
    /** off, connecting, connected, token_rejected, intents_refused or stopped. */
    val state: String = "off",
    val detail: String = "",
    @SerialName("connected_at") val connectedAt: Double? = null,
    val name: String? = null,
    /** message_content and members: on, off, or null until read from Discord. */
    val switches: Map<String, Boolean?> = emptyMap(),
    @SerialName("features_off") val featuresOff: List<String> = emptyList(),
    @SerialName("webhook_with_bot") val webhookWithBot: Boolean = false,
    val guilds: List<DiscordGuild> = emptyList(),
)

// ------------------------------------------------------------ Linked accounts
@Serializable
data class LinkedAccount(
    val uuid: String,
    val name: String,
    @SerialName("discord_id") val discordId: String,
    @SerialName("discord_name") val discordName: String,
    /** The member's Level across all their linked accounts, once the agent says. */
    val level: Int? = null,
    val xp: Int? = null,
)

@Serializable
data class LinkRequest(
    val id: Long,
    val name: String,
    @SerialName("discord_name") val discordName: String,
)

@Serializable
data class RecentPlayer(val name: String, val uuid: String)

@Serializable
data class DiscordLinks(
    val links: List<LinkedAccount> = emptyList(),
    /** Bedrock players waiting for approval. */
    val requests: List<LinkRequest> = emptyList(),
    /** Recent players to pick from. */
    val players: List<RecentPlayer> = emptyList(),
    @SerialName("reads_chat") val readsChat: Boolean = true,
)

// ------------------------------------------------------------ whitelist help
@Serializable
data class WhitelistForm(
    val id: Long,
    val name: String,
    val why: String = "",
    @SerialName("discord_name") val discordName: String,
)

@Serializable
data class SeenAddress(
    val id: Long,
    val name: String,
    /** Partly hidden: the agent never sends the whole address. */
    val masked: String,
    /** new, allowed or ignored. */
    val state: String,
    @SerialName("seen_at") val seenAt: Double,
)

@Serializable
data class WhitelistHelp(
    /** Null when server.properties isn't there. */
    @SerialName("online_mode") val onlineMode: Boolean? = null,
    val addon: String? = null,
    val suggested: List<String> = emptyList(),
    @SerialName("ip_addon") val ipAddon: String? = null,
    val forms: List<WhitelistForm> = emptyList(),
    val addresses: List<SeenAddress> = emptyList(),
    /** The whitelist command an answer sent ("Sent" until the console says so). */
    val action: PlayerAction? = null,
)

// ------------------------------------------------------------ quests
@Serializable
data class QuestMember(
    @SerialName("discord_id") val discordId: String,
    val name: String? = null,
    val progress: Int = 0,
    @SerialName("finished_at") val finishedAt: Double? = null,
)

@Serializable
data class Quest(
    val id: Long,
    val title: String,
    val details: String = "",
    val kind: String,
    val goal: String? = null,
    val count: Int = 1,
    val repeat: String = "once",
    @SerialName("ends_at") val endsAt: Double? = null,
    val rewards: List<JsonObject> = emptyList(),
    val joined: Int = 0,
    /** When the stats files were saved; null unless the progress comes from them. */
    @SerialName("saved_at") val savedAt: Double? = null,
    val members: List<QuestMember> = emptyList(),
)

@Serializable
data class QuestClaim(
    val id: Long,
    val title: String,
    @SerialName("discord_name") val discordName: String,
    val note: String? = null,
    /** Null for none; the picture itself is asked for separately. */
    val screenshot: String? = null,
)

@Serializable
data class QuestIdea(
    val id: Long,
    @SerialName("discord_name") val discordName: String,
    val title: String,
    val details: String = "",
)

@Serializable
data class RoleName(val id: String, val name: String)

@Serializable
data class QuestGuild(val id: String, val name: String, val roles: List<RoleName> = emptyList())

@Serializable
data class QuestsPage(
    val quests: List<Quest> = emptyList(),
    val claims: List<QuestClaim> = emptyList(),
    val suggestions: List<QuestIdea> = emptyList(),
    /** The quest kinds this server can check. */
    val kinds: List<String> = emptyList(),
    /** The Discord bot is connected (needed for Discord roles). */
    val bot: Boolean = false,
    val guilds: List<QuestGuild> = emptyList(),
) {
    /** Every Discord role's name, by id, from every Discord server set up for this server. */
    val roleNames: Map<String, String> get() = guilds.flatMap { it.roles }.associate { it.id to it.name }
}

@Serializable
data class QuestScreenshot(val type: String, val image: String)

// ------------------------------------------------------------ events and polls
@Serializable
data class EventAnswers(val going: Int = 0, val maybe: Int = 0, val no: Int = 0)

@Serializable
data class Attendance(
    @SerialName("discord_id") val discordId: String,
    @SerialName("discord_name") val discordName: String,
    val minutes: Int = 0,
    val attended: Boolean = false,
    /** measured, suggested or fixed. */
    val how: String = "measured",
    @SerialName("fixed_by") val fixedBy: String? = null,
)

@Serializable
data class GameEvent(
    val id: Long,
    val title: String,
    val details: String = "",
    /** advancement, online or approval. */
    val kind: String,
    val goal: String? = null,
    @SerialName("starts_at") val startsAt: Double,
    @SerialName("ends_at") val endsAt: Double,
    @SerialName("start_server") val startServer: Boolean = false,
    val rewards: List<JsonObject> = emptyList(),
    /** coming, on, counting, approving, ended or cancelled. */
    val state: String,
    val answers: EventAnswers = EventAnswers(),
    val attendance: List<Attendance> = emptyList(),
    /** Attendance can still be fixed (7 days after the end). */
    val fixable: Boolean = false,
)

@Serializable
data class Poll(
    val id: Long,
    val question: String,
    val answers: List<String> = emptyList(),
    val votes: List<Int> = emptyList(),
    val total: Int = 0,
    @SerialName("closes_at") val closesAt: Double,
    val open: Boolean = false,
)

@Serializable
data class EventsPage(
    val events: List<GameEvent> = emptyList(),
    val polls: List<Poll> = emptyList(),
    val kinds: List<String> = emptyList(),
    val bot: Boolean = false,
)

/** The new quest form, as typed. The agent checks every value again. */
data class QuestForm(
    val title: String = "",
    val details: String = "",
    val kind: String = "playtime",
    val goal: String = "",
    val count: Int = 1,
    val repeat: String = "once",
    val roleId: String? = null,
    val item: String = "",
    val itemCount: Int = 1,
    val xpLevels: Int = 0,
    val levelXp: Int = 0,
    /** The /quest suggest idea it was made from, answered once made. */
    val suggestionId: Long? = null,
)

/** What the community screens say and send, the same as the desktop app. */
object Community {
    /** Seconds a start request waits for an answer (agent/startrequests.py LIFE). */
    const val START_REQUEST_LIFE = 900.0
    const val START_REQUEST = "start_request"

    val KINDS = listOf("stat", "advancement", "playtime", "joins", "items", "approval")
    val REPEATS = listOf("once", "daily", "weekly", "monthly", "yearly")
    private val NEEDS_GOAL = setOf("stat", "advancement", "items")
    private val NO_COUNT = setOf("advancement", "approval")
    /** The event states attendance is shown in. */
    val ATTENDANCE_STATES = setOf("counting", "approving", "ended")

    /** Start / Say no on an alert: a start request young enough to be
     * open still, for someone who may start that server. */
    fun answersStartRequest(alert: AppAlert, now: Double, mayStart: Boolean): Boolean =
        mayStart && alert.event == START_REQUEST && alert.serverId != null && now - alert.ts < START_REQUEST_LIFE

    fun answered(strings: Strings, answer: String): String =
        strings.t(if (answer == "started") "mobile.start_request.started" else "mobile.start_request.said_no")

    /** "minecraft:diamond_ore" as "diamond ore". */
    fun short(name: String): String = name.removePrefix("minecraft:").replace('_', ' ')

    fun taskWords(strings: Strings, quest: Quest): String {
        var goal = quest.goal.orEmpty()
        if (quest.kind == "stat") {
            val section = goal.substringBefore(' ')
            val name = goal.substringAfter(' ', "")
            goal = "${short(section)}: ${short(name)}"
        }
        return strings.t("quests.task.${quest.kind}", "count" to quest.count, "goal" to short(goal))
    }

    fun rewardWords(strings: Strings, reward: JsonObject, roles: Map<String, String>): String {
        fun value(key: String) = (reward[key] as? JsonPrimitive)?.contentOrNull.orEmpty()
        return strings.t(
            "quests.reward.${value("kind")}",
            "role" to (roles[value("role_id")] ?: strings.t("value.unknown")),
            "item" to short(value("item")),
            "count" to value("count"),
            "levels" to value("levels"),
            "amount" to value("amount"),
        )
    }

    /** The quest's line under its title: what to do, how often, until when, its rewards. */
    fun questLine(strings: Strings, quest: Quest, roles: Map<String, String>): String {
        val parts = mutableListOf(taskWords(strings, quest))
        if (quest.repeat != "once") parts += strings.t("quests.repeat.${quest.repeat}")
        quest.endsAt?.let { parts += strings.t("quests.until", "time" to Display.dateTime(strings, it - 60)) }
        val given = quest.rewards.map { rewardWords(strings, it, roles) }
        parts += strings.t("quests.rewards", "rewards" to (given.joinToString(", ").ifEmpty { strings.t("quests.no_reward") }))
        return parts.joinToString(" · ")
    }

    /** Who joined and who finished; Technical also says how far each is. */
    fun membersLine(strings: Strings, quest: Quest): String {
        val done = quest.members.filter { it.finishedAt != null }.map { it.name ?: it.discordId }
        var line = strings.t("quests.joined", "n" to quest.joined)
        if (done.isNotEmpty()) line += " · " + strings.t("quests.done_by", "names" to done.joinToString(", "))
        if (strings.technical) {
            val going = quest.members.filter { it.finishedAt == null }.map { "${it.name ?: it.discordId} ${it.progress}/${quest.count}" }
            if (going.isNotEmpty()) line += " · " + going.joinToString(", ")
        }
        quest.savedAt?.let { line += " · " + strings.t("quests.saved", "time" to Display.dateTime(strings, it)) }
        return line
    }

    fun needsGoal(kind: String): Boolean = kind in NEEDS_GOAL

    fun needsCount(kind: String): Boolean = kind !in NO_COUNT

    /** The hint under the kind; a server that can't check every kind says why. */
    fun goalHint(strings: Strings, kind: String, kinds: List<String>): String {
        var hint = strings.t("quests.goal_hint.$kind")
        if (kinds.size < KINDS.size) hint += " " + strings.t("quests.limited_kinds")
        return hint
    }

    /** POST /quests: only the fields the kind uses, and only the rewards picked. */
    fun questBody(form: QuestForm): JsonObject {
        val rewards = buildList<JsonElement> {
            form.roleId?.let { add(buildJsonObject { put("kind", "role"); put("role_id", it) }) }
            form.item.trim().takeIf { it.isNotEmpty() }?.let { item ->
                add(buildJsonObject { put("kind", "item"); put("item", item); put("count", form.itemCount.coerceIn(1, 64)) })
            }
            if (form.xpLevels > 0) add(buildJsonObject { put("kind", "xp"); put("levels", form.xpLevels.coerceIn(1, 30)) })
            if (form.levelXp > 0) add(buildJsonObject { put("kind", "level_xp"); put("amount", form.levelXp.coerceIn(1, 500)) })
        }
        return buildJsonObject {
            put("title", form.title.trim())
            put("details", form.details.trim())
            put("kind", form.kind)
            put("goal", if (needsGoal(form.kind)) form.goal.trim() else "")
            put("count", if (needsCount(form.kind)) form.count.coerceIn(1, 100_000) else 1)
            put("repeat", form.repeat)
            put("ends", JsonNull)
            put("rewards", JsonArray(rewards))
            put("suggestion_id", form.suggestionId?.let(::JsonPrimitive) ?: JsonNull)
        }
    }

    fun eventLine(strings: Strings, event: GameEvent, roles: Map<String, String>): String {
        val parts = mutableListOf(
            strings.t("game_events.time", "start" to Display.dateTime(strings, event.startsAt), "end" to Display.dateTime(strings, event.endsAt)),
            strings.t("game_events.counts.${event.kind}", "goal" to short(event.goal.orEmpty())),
        )
        val given = listOf(strings.t("game_events.attend_xp")) + event.rewards.map { rewardWords(strings, it, roles) }
        parts += strings.t("quests.rewards", "rewards" to given.joinToString(", "))
        if (event.startServer) parts += strings.t("game_events.starts_server")
        return parts.joinToString(" · ")
    }

    fun answersLine(strings: Strings, event: GameEvent): String =
        strings.t("game_events.answers", "going" to event.answers.going, "maybe" to event.answers.maybe, "no" to event.answers.no) +
            " · " + strings.t("game_events.state.${event.state}")

    fun personLine(strings: Strings, person: Attendance): String {
        var words = strings.t("game_events.person", "name" to person.discordName, "minutes" to person.minutes)
        if (strings.technical) words += " · " + strings.t("game_events.how.${person.how}", "by" to (person.fixedBy ?: ""))
        return words
    }

    fun canCallOff(event: GameEvent): Boolean = event.state == "coming" || event.state == "on"

    fun pollCounts(poll: Poll): String =
        poll.answers.zip(poll.votes).joinToString(" · ") { (answer, n) -> "$answer: $n" }

    fun pollState(strings: Strings, poll: Poll): String =
        if (poll.open) strings.t("game_events.poll_open", "n" to poll.total, "time" to Display.dateTime(strings, poll.closesAt))
        else strings.t("game_events.poll_closed", "n" to poll.total)

    /** The bot's state line. */
    fun botState(strings: Strings, status: DiscordStatus): String =
        strings.t("discord.state.${status.state}", "name" to (status.name ?: ""))

    /** What a Discord server's check found, one line each; empty when all is well. */
    fun problems(strings: Strings, guild: DiscordGuild): List<String>? = guild.report?.problems?.map { p ->
        val values = p.mapValues { (_, v) -> (v as? JsonPrimitive)?.contentOrNull.orEmpty() }
        val kind = strings.t("discord.kind.${values["kind"] ?: "alerts"}")
        val pairs = (values + mapOf("guild" to guild.name, "kind" to kind)).map { it.key to it.value }
        strings.t("discord.problem.${values["id"].orEmpty()}", *pairs.toTypedArray())
    }

    /** What an offline-mode server uses for its whitelist, or null to say nothing. */
    fun whitelistNote(strings: Strings, help: WhitelistHelp): String? = when {
        help.onlineMode != false || help.suggested.isEmpty() -> null
        help.addon != null -> strings.t("whitelist.addon_used", "addon" to help.addon)
        else -> strings.t("whitelist.no_addon", "addons" to help.suggested.joinToString(", "))
    }

    fun addressLine(strings: Strings, address: SeenAddress): String = strings.t(
        "whitelist.address_${address.state}",
        "name" to address.name,
        "masked" to address.masked,
        "time" to Display.dateTime(strings, address.seenAt),
    )

    fun linkLine(strings: Strings, link: LinkedAccount): String {
        var words = strings.t("discord.links.row", "name" to link.name, "member" to link.discordName)
        if (link.level != null) words += " · " + strings.t("discord.links.level", "level" to link.level, "xp" to (link.xp ?: 0))
        if (strings.technical) words += " (${link.uuid})"
        return words
    }

    /** The recent player a Bedrock link request most likely means: the same name, else the first. */
    fun likelyPlayer(request: LinkRequest, players: List<RecentPlayer>): RecentPlayer? =
        players.firstOrNull { it.name == request.name } ?: players.firstOrNull()
}
