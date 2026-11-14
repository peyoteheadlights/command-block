package io.github.peyoteheadlights.mcsc.core

import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import okhttp3.Cache
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request

/** What a server's add-ons are called, by its type (glossary words). */
enum class ContentKind(val titleKey: String) {
    MODS("page.mods"),
    PLUGINS("page.plugins"),
    PACKS("mobile.content.packs"),

    /** Vanilla takes neither, so it has no add-ons screen. */
    NONE("page.mods"),
}

/** Whether the add-ons screen's change buttons work now. */
enum class Changes {
    /** The account may only look (a helper without mods.manage). */
    NOT_ALLOWED,

    /** Mods change only while the server is stopped. */
    STOP_FIRST,

    /** The server's state isn't known (the PC can't be reached). */
    UNKNOWN,
    READY,
}

/** The Mods / Plugins / Packs screen's rules (mods.js and addons.js). */
object Content {
    /** Mods, Plugins or Packs; null while the server list isn't read yet. */
    fun kind(row: ServerRow?): ContentKind? {
        row ?: return null
        return when {
            row.capabilities?.addons == true -> ContentKind.PACKS
            row.content == "plugins" -> ContentKind.PLUGINS
            row.content == "mods" || row.capabilities?.mods == true -> ContentKind.MODS
            else -> ContentKind.NONE
        }
    }

    /**
     * Which change buttons a mod or plugin row offers. Only someone with
     * mods.manage sees any; they work only while the server is stopped
     * (or crashed); a state not known is not
     * guessed to be stopped.
     */
    fun changes(canManage: Boolean, serverState: String?): Changes = when {
        !canManage -> Changes.NOT_ALLOWED
        serverState == null || serverState == States.UNKNOWN -> Changes.UNKNOWN
        serverState == States.OFFLINE || serverState == States.CRASHED -> Changes.READY
        else -> Changes.STOP_FIRST
    }

    /** Bedrock packs change while the server runs too; they take effect
     * after a restart, which the screen then offers. */
    fun packChanges(canManage: Boolean): Changes = if (canManage) Changes.READY else Changes.NOT_ALLOWED

    /** "Stop the server first" offers Stop only while Stop would work. */
    fun canStopHere(serverState: String?, canControl: Boolean): Boolean = canControl && serverState == States.ONLINE

    fun status(mod: Mod): StateWords = if (!mod.enabled) StateWords("mods.status_off", Tone.NEUTRAL) else when (mod.status) {
        "ok" -> StateWords("mods.status_ok", Tone.SUCCESS)
        "warn" -> StateWords("mods.status_warn", Tone.WARNING)
        "error" -> StateWords("mods.status_error", Tone.DANGER)
        else -> StateWords("value.unknown", Tone.NEUTRAL)
    }

    /** Each problem's reason (a missing dependency, the wrong Minecraft
     * version or server type), worst first; notes for information left out. */
    fun problems(mod: Mod): List<String> =
        mod.issues.filter { it.severity == "error" || it.severity == "warn" }
            .sortedBy { if (it.severity == "error") 0 else 1 }
            .map { it.detail }
            .filter { it.isNotBlank() }

    /** Technical words add the mod's id, file and Minecraft versions. */
    fun technical(strings: Strings, mod: Mod): String = listOfNotNull(
        mod.modId.takeIf { it.isNotBlank() },
        mod.filename,
        mod.minecraftRange?.takeIf { it.isNotBlank() }?.let { strings.t("mods.install_minecraft", "versions" to it) },
    ).joinToString(" · ")

    /** "Update: 2.0" when Modrinth had a newer one at the last check. */
    fun newer(strings: Strings, mod: Mod): String? =
        mod.updateAvailable?.let { strings.t("mods.update_to", "version" to it.latestVersion) }

    /** "1.0 → 2.0"; Unknown for a side the agent didn't say. */
    fun change(strings: Strings, old: String?, new: String?): String =
        "${old?.takeIf { it.isNotBlank() } ?: strings.t("value.unknown")} → ${new?.takeIf { it.isNotBlank() } ?: strings.t("value.unknown")}"

    private val ACTIONS = setOf("install", "upload", "remove", "update", "rollback", "enable", "disable", "side", "players_add", "players_remove")

    /** What a history row did, in words; the agent's own name for an action the app doesn't know. */
    fun action(strings: Strings, row: ModHistoryRow): String =
        when (row.action) {
            "update_all" -> strings.t("mobile.content.update_all")
            in ACTIONS -> strings.t("mobile.content.action_${row.action}")
            else -> row.action
        }

    fun historyTone(row: ModHistoryRow): Tone = if (row.result == "ok") Tone.SUCCESS else Tone.DANGER

    /** The mod a history row is about; Update all is about several. */
    fun historyName(strings: Strings, row: ModHistoryRow): String =
        row.modName ?: row.modId ?: if (row.action == "update_all") row.detail.orEmpty() else strings.t("value.unknown")

    /** The kept versions newest first, as the agent lists them; only one
     * whose file is still there and isn't in use can be rolled back to. */
    fun canRollBack(version: ModVersion): Boolean = version.exists && !version.isCurrent

    /** The required dependencies still missing: what "Install the missing ones" fetches. */
    fun missing(report: DependencyReport?): List<DependencyItem> =
        report?.items.orEmpty().filter { !it.platform && it.kind == "required" && it.status == "missing" }

    /** A Bedrock pack's kind in words. */
    fun packKind(addon: Addon): String = if (addon.kind == "resource") "addons.kind_resource" else "addons.kind_behavior"

    /** The files each screen picks. */
    const val JAR = ".jar"
    val PACK_FILES = listOf(".mcpack", ".mcaddon", ".mctemplate")
    const val MODPACK = ".mrpack"

    /** Whether a picked file's name is one the screen sends; the agent
     * checks again and explains anything else in its own words. */
    fun accepts(kind: ContentKind, name: String, modpack: Boolean = false): Boolean {
        val lower = name.lowercase()
        return when {
            modpack -> lower.endsWith(MODPACK)
            kind == ContentKind.PACKS -> PACK_FILES.any { lower.endsWith(it) }
            else -> lower.endsWith(JAR)
        }
    }
}

/** Modrinth search results and the install confirmation (installMod in mods.js). */
object Install {
    /** The version that would be installed: the newest that fits. */
    fun latest(project: ModProject): ModrinthVersion? = project.versions.firstOrNull()

    fun required(version: ModrinthVersion): List<ModrinthDependency> = version.dependencies.filter { it.type == "required" }

    private val SIDES = setOf("required", "optional", "unsupported")

    /** What the confirmation lists before anything is downloaded. */
    fun facts(strings: Strings, project: ModProject, version: ModrinthVersion): List<String> {
        val unknown = strings.t("value.unknown")
        return listOfNotNull(
            strings.t("mods.install_what", "name" to project.project.title, "version" to (version.versionNumber ?: unknown)),
            strings.t("mods.install_size", "size" to Display.bytes(strings, version.file?.size)),
            version.gameVersions.takeIf { it.isNotEmpty() }?.let { strings.t("mods.install_minecraft", "versions" to it.joinToString(", ")) },
            strings.t("mods.install_licence", "licence" to (project.project.license ?: unknown)),
            strings.t("mobile.content.side_server", "side" to side(strings, project.project.serverSide)),
            strings.t("mobile.content.side_client", "side" to side(strings, project.project.clientSide)),
        )
    }

    private fun side(strings: Strings, value: String?): String =
        if (value in SIDES) strings.t("mobile.content.side_$value") else strings.t("value.unknown")

    /** "1.2M downloads"; Unknown when Modrinth didn't say. */
    fun downloads(strings: Strings, count: Long?): String {
        count ?: return strings.t("value.unknown")
        val short = when {
            count >= 1_000_000 -> "${Display.fixed(count / 1_000_000.0, 1)}M"
            count >= 1_000 -> "${Display.fixed(count / 1_000.0, 1)}K"
            else -> count.toString()
        }
        return strings.t("mobile.content.downloads", "count" to short)
    }

    /** Only Modrinth's own pages open, in the in-app browser. */
    fun page(url: String?): String? = url?.takeIf { it.startsWith("https://modrinth.com/") }

    /** What "Install the missing ones" did with each: installed, already there, or failed and why. */
    fun missingDone(strings: Strings, results: List<DependencyResult>): String {
        val words = results.joinToString(", ") { r ->
            val said = when (r.result) {
                "installed" -> strings.t("mobile.content.result_ok")
                "skipped" -> strings.t("mobile.content.result_skipped")
                else -> strings.t("mobile.content.result_failed") + (r.detail?.takeIf { it.isNotBlank() }?.let { ": $it" } ?: "")
            }
            "${r.title} ($said)"
        }
        return strings.t("mobile.content.missing_done", "results" to words)
    }

    fun allWorked(results: List<DependencyResult>): Boolean = results.all { it.result == "installed" || it.result == "skipped" }
}

/** Update all's list and what is sent. */
object UpdateAll {
    /** Offered when the last check found a newer version of any mod. */
    fun offered(list: ModList?): Boolean = list?.mods?.any { it.updateAvailable != null } == true

    /** Ticked at first: every one that is on. Turned-off ones are listed
     * but left for the person to tick. */
    fun ticked(plan: UpdateAllPlan): Set<String> = plan.updates.filter { it.enabled }.map { it.filename }.toSet()

    /** The ticked ones, in the plan's order, with the version the plan showed. */
    fun body(plan: UpdateAllPlan, ticked: Set<String>): JsonObject = buildJsonObject {
        put(
            "mods",
            buildJsonArray {
                for (row in plan.updates) {
                    if (row.filename !in ticked) continue
                    add(
                        buildJsonObject {
                            put("filename", row.filename)
                            put("version_id", row.versionId)
                        },
                    )
                }
            },
        )
    }

    /** Why it can't start now, or null when it can. */
    fun blocked(strings: Strings, plan: UpdateAllPlan): String? = when {
        plan.serverRunning -> strings.t("mobile.content.stop_first")
        plan.blockedBy != null -> strings.t("world.busy", "job" to plan.blockedBy)
        else -> null
    }

    fun done(strings: Strings, result: UpdateAllResult): String =
        strings.tn("mobile.content.update_all_done", result.updated.size, "backup" to (result.safetyBackup?.name ?: strings.t("value.unknown")))

    /** The step a running Update all is on, from the live feed. */
    fun progress(job: Job?, serverId: String): String? =
        job?.takeIf { it.state == "running" && it.serverId == serverId && it.kind == "mods" }?.step
}

/** The "Still fit?" card: its first line, and "Change these" as an Update all. */
object StillFit {
    fun lead(strings: Strings, fit: ModFit, plugins: Boolean): String {
        val word = if (plugins) "plugin" else "mod"
        val count = fit.needsChange.size
        return if (count > 0) strings.tn("fit.lead", count, "word" to word, "about" to fit.about)
        else strings.t("fit.lead_none", "word" to word, "about" to fit.about)
    }

    /** The mods to change, shown and sent the way Update all's own list is (all ticked). */
    fun plan(fit: ModFit): UpdateAllPlan = UpdateAllPlan(
        updates = fit.needsChange.map {
            UpdateAllRow(modId = it.modId, name = it.name, filename = it.filename, installedVersion = it.from, latestVersion = it.to, versionId = it.versionId)
        },
    )
}

/** The "Doing the same job" rows. */
object SameJob {
    /** The mods that get a Turn off button: with a pick, the others; with
     * none, each one no other mod needs, so the owner keeps the one they use. */
    fun turnOff(overlap: Overlap): List<OverlapMod> =
        overlap.mods.filter { it.neededBy.isEmpty() && (overlap.keep.isEmpty() || it.turnOff) }
}

/** Server / Players / Both: the words under a mod and the picks offered. */
object Sides {
    val ALL = listOf("server", "players", "both")
    private val PLUGINS = setOf("paper", "bukkit")
    private val FROM = setOf("you", "unknown", "modpack", "file")
    private val SITES = mapOf("modrinth" to "Modrinth", "curseforge" to "CurseForge")

    /** "For: Both (not sure, the mod doesn't say)"; null for plugins, which only run on the server. */
    fun words(strings: Strings, mod: Mod): String? {
        val side = mod.side ?: return null
        if (mod.loader in PLUGINS) return null
        val words = strings.t("sides.for", "side" to strings.t("sides.$side"))
        val from = mod.sideFrom ?: return words
        val source = if (from in FROM) strings.t("sides.from_$from") else strings.t("sides.from_site", "site" to (SITES[from] ?: from))
        return "$words $source"
    }

    /** The marks a mod can be changed to. Players stays offered while the jar
     * is still in the mods folder, since picking it moves the jar out. */
    fun picks(mod: Mod): List<String> =
        if (mod.side == null || mod.loader in PLUGINS) emptyList()
        else ALL.filter { it == "players" || it != mod.side }

    /** A file the Players only list takes: a mod (.jar) or a pack (.zip). */
    fun accepts(name: String): Boolean = name.lowercase().let { it.endsWith(".jar") || it.endsWith(".zip") }
}

/** The linked modpack card and Modpack update's question. */
object LinkedPacks {
    fun lines(strings: Strings, pack: LinkedModpack): List<String> = listOfNotNull(
        strings.t("linked.from", "name" to pack.name, "version" to pack.version, "site" to pack.site),
        pack.update?.let {
            if (it.version.isNotBlank()) strings.t("linked.update_ready", "version" to it.version)
            else strings.t("linked.update_ready_unnamed")
        },
        if (pack.addedByYou > 0) strings.tn("linked.yours", pack.addedByYou) else null,
    )

    fun changes(strings: Strings, changes: ModpackChanges): List<String> = listOf(
        "linked.newer" to changes.newer.map { "${it.from} → ${it.to}" },
        "linked.added" to changes.added,
        "linked.removed" to changes.removed,
        "linked.kept" to changes.addedByYou,
        "linked.configs" to changes.configsChangedByYou,
    ).filter { it.second.isNotEmpty() }.map { (key, names) -> strings.t(key, "names" to names.joinToString(", ")) }

    fun steps(strings: Strings, preview: ModpackUpdatePreview): List<String> = listOfNotNull(
        strings.t("pack.step_stop"),
        strings.t("linked.step_one_backup"),
        if (preview.changes.versionChange) {
            strings.t(
                "pack.step_version",
                "type" to (preview.pack.typeName ?: strings.t("value.unknown")),
                "version" to preview.changes.minecraftVersion,
            )
        } else null,
    )
}

/** A modpack import's steps, as the confirmation lists them. */
object Modpacks {
    fun steps(strings: Strings, inspected: ModpackInspected): List<String> {
        val pack = inspected.pack
        return listOfNotNull(
            strings.t("pack.step_stop"),
            strings.t("pack.step_backup"),
            if (inspected.into?.versionChange == true) {
                strings.t(
                    "pack.step_version",
                    "type" to (pack.typeName ?: strings.t("value.unknown")),
                    "version" to (pack.minecraftVersion ?: strings.t("value.unknown")),
                )
            } else null,
            strings.t("pack.step_aside"),
            strings.t("pack.step_files", "count" to pack.downloadCount),
        )
    }

    /** Where a found modpack is read from. */
    fun site(hit: ModrinthHit): String = if (hit.source == "curseforge") "curseforge" else "modrinth"

    /** A found modpack on one line under its name: site, loader and newest Minecraft
     *  version, downloads, and what Modrinth was told it holds. */
    fun about(strings: Strings, hit: ModrinthHit): String = listOfNotNull(
        hit.sourceName,
        (hit.loaders + listOfNotNull(hit.minecraftVersion)).joinToString(" ").ifBlank { null },
        hit.downloads?.let { strings.t("pack.downloads", "count" to String.format(java.util.Locale.ROOT, "%,d", it)) },
        if (hit.serverSide == "unsupported") strings.t("pack.players_only") else null,
    ).plus(hit.disclosures).joinToString("  ·  ")

    fun fileStatus(file: ModpackFile): StateWords = when (file.status) {
        "download" -> StateWords("pack.status_download", Tone.SUCCESS)
        "client_only" -> StateWords("pack.status_client", Tone.NEUTRAL)
        else -> StateWords("pack.status_refused", Tone.DANGER)
    }
}

/** The Game settings form (game-settings.js): which fields, in which
 * groups, their words, and what Save sends. */
object GameForm {
    /** Each key's label and hint. Bedrock's allow-list shares the whitelist's label. */
    val LABELS = mapOf(
        "difficulty" to ("game.difficulty" to "game.difficulty_hint"),
        "gamemode" to ("game.gamemode" to "game.gamemode_hint"),
        "max-players" to ("game.max_players" to "game.max_players_hint"),
        "level-seed" to ("game.seed" to "game.seed_hint"),
        "white-list" to ("game.whitelist" to "game.whitelist_hint"),
        "pvp" to ("game.pvp" to "game.pvp_hint"),
        "view-distance" to ("game.view_distance" to "game.view_distance_hint"),
        "motd" to ("game.motd" to "game.motd_hint"),
        "server-port" to ("game.port" to "game.port_hint"),
        "allow-list" to ("mobile.game.allowlist" to "game.allowlist_hint"),
        "allow-cheats" to ("game.cheats" to "game.cheats_hint"),
        "server-name" to ("game.server_name" to "game.server_name_hint"),
    )

    private val CHOICE_WORDS = setOf("peaceful", "easy", "normal", "hard", "survival", "creative", "adventure", "spectator")

    /** How the game plays, who can join, then the technical bits. */
    val ORDER = listOf(
        "game.group_play" to listOf("gamemode", "difficulty", "pvp", "allow-cheats"),
        "game.group_who" to listOf("motd", "server-name", "max-players", "white-list", "allow-list"),
        "game.group_world" to listOf("level-seed", "view-distance", "server-port"),
    )

    data class Group(val titleKey: String, val fields: List<GameSetting>)

    /** The groups with the fields this server's file has, in order. */
    fun groups(settings: GameSettings): List<Group> = ORDER.mapNotNull { (title, keys) ->
        val fields = keys.mapNotNull { key -> settings.fields.firstOrNull { it.key == key } }
        if (fields.isEmpty()) null else Group(title, fields)
    }

    fun choiceLabel(strings: Strings, value: String): String = if (value in CHOICE_WORDS) strings.t("game.$value") else value

    /**
     * The value the form starts with, as text: what the file says when it
     * can be read, otherwise what Minecraft uses then (an empty seed means
     * a random one). Booleans are "true" or "false".
     */
    fun initial(field: GameSetting): String {
        // A value Minecraft wouldn't accept is shown as the file has it (its problem is
        // said next to it), never swapped for the default.
        if (field.set && field.problem != null) return field.raw.orEmpty()
        val shown = if (field.set) text(field.value) else null
        return when (field.kind) {
            "seed" -> shown.orEmpty()
            else -> shown ?: field.minecraftDefault
        }
    }

    /** Locked: the seed once the world exists, or everything for someone
     * who may not edit settings. */
    fun locked(field: GameSetting, canEdit: Boolean): Boolean = !canEdit || field.readOnly != null

    /** The line under a field: its hint (or why the seed is locked), and
     * what Minecraft uses when the file doesn't set it. */
    fun hint(strings: Strings, field: GameSetting): String {
        val notes = mutableListOf<String>()
        notes += if (field.readOnly == "world_exists") strings.t("game.seed_locked") else LABELS[field.key]?.second?.let { strings.t(it) }.orEmpty()
        if (!field.set) {
            val default = when {
                field.kind == "choice" -> choiceLabel(strings, field.minecraftDefault)
                field.minecraftDefault.isEmpty() -> strings.t("game.blank")
                else -> field.minecraftDefault
            }
            notes += strings.t("game.not_set", "value" to default)
        }
        return notes.filter { it.isNotBlank() }.joinToString(" ")
    }

    /** Why the file's own value couldn't be used, or null. */
    fun fileProblem(strings: Strings, field: GameSetting): String? =
        field.problem?.let { strings.t("game.file_problem", "problem" to it) }

    /**
     * Only what changed, as the agent takes it: on and off as true or
     * false, numbers as numbers when they read as one (the agent checks
     * and explains anything else), words as typed. Locked fields never.
     */
    fun changed(settings: GameSettings, edits: Map<String, String>): JsonObject = buildJsonObject {
        for (field in settings.fields) {
            if (field.readOnly != null) continue
            val edit = edits[field.key] ?: continue
            if (edit == initial(field)) continue
            when (field.kind) {
                "bool" -> put(field.key, edit == "true")
                "int", "port" -> {
                    val number = edit.trim().toLongOrNull()
                    if (number != null) put(field.key, number) else put(field.key, edit.trim())
                }
                else -> put(field.key, edit)
            }
        }
    }

    /** What to say after Save. Running: it waits for a restart, offered next to it. */
    fun saved(strings: Strings, result: GameSettingsSaved): String = when {
        result.nothingChanged == true -> strings.t("settings.nothing_changed")
        result.running -> strings.t("game.saved_running")
        else -> strings.t("game.saved")
    }

    private fun text(value: JsonElement?): String? = when (value) {
        is JsonPrimitive -> value.booleanOrNull?.toString() ?: value.contentOrNull
        else -> null
    }
}

/**
 * Modrinth's icons for the search results, loaded straight from Modrinth
 * (never through the PC) into the phone's own cache, so scrolling back
 * doesn't download them again. Only https addresses on Modrinth's own
 * hosts are loaded; anything else is no icon.
 */
class IconCache(
    directory: File?,
    baseClient: OkHttpClient = OkHttpClient(),
    private val hosts: Set<String> = MODRINTH_HOSTS,
    private val httpsOnly: Boolean = true,
    private val kept: Int = 120,
) {
    private val http: OkHttpClient = baseClient.newBuilder()
        .apply { if (directory != null) cache(Cache(directory, DISK_BYTES)) }
        // A redirect could lead off Modrinth's own address: never followed.
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    private val memory = object : LinkedHashMap<String, ByteArray>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, ByteArray>?): Boolean = size > kept
    }

    fun allowed(url: String?): Boolean {
        val parsed = url?.toHttpUrlOrNull() ?: return false
        return (!httpsOnly || parsed.isHttps) && parsed.host in hosts
    }

    /** The icon's bytes, from memory, the disk cache or Modrinth; null when
     * it isn't allowed or couldn't be loaded. */
    suspend fun load(url: String?): ByteArray? {
        if (url == null || !allowed(url)) return null
        synchronized(memory) { memory[url] }?.let { return it }
        return withContext(Dispatchers.IO) {
            runCatching {
                http.newCall(Request.Builder().url(url).build()).execute().use { response ->
                    if (!response.isSuccessful) return@use null
                    val body = response.body ?: return@use null
                    if (body.contentLength() > MAX_ICON_BYTES) return@use null
                    // Read no more than the limit, even when the size wasn't said.
                    val source = body.source()
                    if (source.request(MAX_ICON_BYTES + 1L)) return@use null
                    source.buffer.readByteArray()
                }
            }.getOrNull()?.also { bytes -> synchronized(memory) { memory[url] = bytes } }
        }
    }

    companion object {
        val MODRINTH_HOSTS = setOf("cdn.modrinth.com")
        const val DISK_BYTES = 20L * 1024 * 1024
        const val MAX_ICON_BYTES = 1024 * 1024
    }
}
