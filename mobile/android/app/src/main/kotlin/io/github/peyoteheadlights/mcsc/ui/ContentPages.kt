package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import io.github.peyoteheadlights.mcsc.core.AddonList
import io.github.peyoteheadlights.mcsc.core.Addon
import io.github.peyoteheadlights.mcsc.core.AgentClient
import io.github.peyoteheadlights.mcsc.core.AgentException
import io.github.peyoteheadlights.mcsc.core.Content
import io.github.peyoteheadlights.mcsc.core.DependencyPlan
import io.github.peyoteheadlights.mcsc.core.DependencyReport
import io.github.peyoteheadlights.mcsc.core.GameForm
import io.github.peyoteheadlights.mcsc.core.GameRules
import io.github.peyoteheadlights.mcsc.core.GameSettings
import io.github.peyoteheadlights.mcsc.core.Install
import io.github.peyoteheadlights.mcsc.core.Job
import io.github.peyoteheadlights.mcsc.core.Mod
import io.github.peyoteheadlights.mcsc.core.ModHistory
import io.github.peyoteheadlights.mcsc.core.ModImpact
import io.github.peyoteheadlights.mcsc.core.ModList
import io.github.peyoteheadlights.mcsc.core.ModSides
import io.github.peyoteheadlights.mcsc.core.ModProject
import io.github.peyoteheadlights.mcsc.core.ModVersion
import io.github.peyoteheadlights.mcsc.core.ModVersions
import io.github.peyoteheadlights.mcsc.core.DownloadLink
import io.github.peyoteheadlights.mcsc.core.MakeModpack
import io.github.peyoteheadlights.mcsc.core.ModpackInspected
import io.github.peyoteheadlights.mcsc.core.AntiCheat
import io.github.peyoteheadlights.mcsc.core.ModpackMade
import io.github.peyoteheadlights.mcsc.core.ModpackMakeOptions
import io.github.peyoteheadlights.mcsc.core.ModpackUpdatePreview
import io.github.peyoteheadlights.mcsc.core.ModSearch
import io.github.peyoteheadlights.mcsc.core.ModrinthCards
import io.github.peyoteheadlights.mcsc.core.ModrinthHit
import io.github.peyoteheadlights.mcsc.core.ModrinthJar
import io.github.peyoteheadlights.mcsc.core.ModrinthShare
import io.github.peyoteheadlights.mcsc.core.PickedFile
import io.github.peyoteheadlights.mcsc.core.PlayerOnlyOptions
import io.github.peyoteheadlights.mcsc.core.Strings
import io.github.peyoteheadlights.mcsc.core.ModFit
import io.github.peyoteheadlights.mcsc.core.StillFit
import io.github.peyoteheadlights.mcsc.core.UpdateAll
import io.github.peyoteheadlights.mcsc.core.UpdateAllPlan
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.coroutines.withContext

/** A remove waiting for "yes", with what else needs the mod. */
data class RemoveAsk(val mod: Mod, val impact: ModImpact)

/** The Modrinth address and summary being asked: the modpack, or one own [jar]. */
data class ModrinthAsk(val jar: ModrinthJar?, val name: String)

/** The kept versions of one mod, shown to roll back to. */
data class VersionsAsk(val mod: Mod, val versions: Read<ModVersions>)

/** Search results so far, and whether more can be asked for. */
data class Found(val query: String, val hits: List<ModrinthHit>, val total: Int?) {
    val more: Boolean get() = total != null && hits.size < total
}

/**
 * Everything the Mods / Plugins / Packs and Game settings screens show for
 * one server. Every button is the agent's own route with the person's
 * own sign-in, so the agent decides what they may do and explains a refusal.
 */
class ContentPages(
    val serverId: String,
    private val scope: CoroutineScope,
    private val client: () -> AgentClient?,
    private val noted: (AgentException) -> Unit,
) {
    /** "toggle:file", "remove:file", "update:file", "update_all", ... while that button works. */
    var busy: String? by mutableStateOf(null)
        private set
    var said: Said? by mutableStateOf(null)
        private set

    fun clearSaid() {
        said = null
    }

    /** Something the phone itself says, like a file of the wrong kind. */
    fun tell(words: Said) {
        said = words
    }

    // ------------------------------------------------------------ installed
    var mods: Read<ModList> by mutableStateOf(Read())
        private set
    var history: Read<ModHistory> by mutableStateOf(Read())
        private set
    var dependencies: DependencyReport? by mutableStateOf(null)
        private set
    var removing: RemoveAsk? by mutableStateOf(null)
        private set
    var updating: Mod? by mutableStateOf(null)
        private set
    var versions: VersionsAsk? by mutableStateOf(null)
        private set

    fun loadMods() {
        val c = client() ?: return
        mods = mods.copy(loading = true)
        scope.launch {
            try {
                mods = Read(c.mods(serverId))
            } catch (e: AgentException) {
                noted(e)
                // Never the old list as if current: Unknown until the PC answers.
                mods = Read(problem = Message.of(e))
                dependencies = null
                return@launch
            }
            // The list still shows without these.
            dependencies = try { c.dependencies(serverId) } catch (e: AgentException) { null }
            history = try { Read(c.modHistory(serverId)) } catch (e: AgentException) { Read(problem = Message.of(e)) }
        }
    }

    /** The Server / Players / Both filter ("all": every mod). */
    var sideFilter by mutableStateOf("all")

    /** "On all servers": every server's marks; null while it's off. */
    var everywhere: Read<ModSides>? by mutableStateOf(null)
        private set

    fun showEverywhere(on: Boolean) {
        if (!on) {
            everywhere = null
            return
        }
        val c = client() ?: return
        everywhere = Read(loading = true)
        scope.launch {
            everywhere = try {
                Read(c.modSides())
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    fun setEnabled(mod: Mod, on: Boolean, strings: Strings) = work("toggle:${mod.filename}", strings, reload = ::loadMods) {
        it.setModEnabled(serverId, mod.filename, on)
        Said(strings.t(if (on) "mods.turned_on" else "mods.turned_off", "name" to mod.name), good = true)
    }

    /** Asks the agent what else needs [mod]; nothing changes yet. */
    fun askRemove(mod: Mod, strings: Strings) = work("remove:${mod.filename}", strings) {
        removing = RemoveAsk(mod, it.modImpact(serverId, mod.filename))
        null
    }

    fun cancelRemove() {
        removing = null
    }

    fun remove(strings: Strings) {
        val ask = removing ?: return
        removing = null
        work("remove:${ask.mod.filename}", strings, reload = ::loadMods) {
            it.removeMod(serverId, ask.mod.filename)
            Said(strings.t("mods.removed_toast", "name" to ask.mod.name), good = true)
        }
    }

    fun askUpdate(mod: Mod) {
        updating = mod
    }

    fun cancelUpdate() {
        updating = null
    }

    fun update(strings: Strings) {
        val mod = updating ?: return
        updating = null
        work("update:${mod.filename}", strings, reload = ::loadMods) {
            val done = it.updateMod(serverId, mod.filename, mod.updateAvailable?.versionId)
            Said(strings.t("mods.updated_toast", "version" to (done.updated?.version ?: strings.t("value.unknown"))), good = true)
        }
    }

    fun showVersions(mod: Mod) {
        val c = client() ?: return
        versions = VersionsAsk(mod, Read(loading = true))
        scope.launch {
            val read = try {
                Read(c.modVersions(serverId, mod.modId))
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
            if (versions?.mod == mod) versions = VersionsAsk(mod, read)
        }
    }

    fun closeVersions() {
        versions = null
    }

    fun rollBack(version: ModVersion, strings: Strings) {
        val ask = versions ?: return
        if (!Content.canRollBack(version)) return
        versions = null
        work("rollback:${ask.mod.filename}", strings, reload = ::loadMods) {
            val done = it.rollBackMod(serverId, ask.mod.modId, version.archivePath)
            Said(strings.t("mods.rolled_back", "version" to (done.restored?.version ?: version.version ?: strings.t("value.unknown"))), good = true)
        }
    }

    // ------------------------------------------------------------ update all
    var plan: Read<UpdateAllPlan>? by mutableStateOf(null)
        private set
    var ticked: Set<String> by mutableStateOf(emptySet())
        private set

    /** The live feed's latest word on a task of this server. */
    var task: Job? by mutableStateOf(null)
        private set

    fun onTask(job: Job) {
        task = job
    }

    /** Asks Modrinth (through the PC) what Update all would change. */
    fun openUpdateAll() {
        val c = client() ?: return
        plan = Read(loading = true)
        scope.launch {
            plan = try {
                val read = c.updateAllPlan(serverId)
                ticked = UpdateAll.ticked(read)
                Read(read)
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    /** "Change these" on the Still fit? card: Update all's list, filled from the card. */
    fun openFit(fit: ModFit) {
        val read = StillFit.plan(fit)
        ticked = UpdateAll.ticked(read)
        plan = Read(read)
    }

    fun hideFit(strings: Strings) = work("hide_fit", strings, reload = ::loadMods) {
        it.hideFit(serverId)
        null
    }

    fun tick(filename: String, on: Boolean) {
        ticked = if (on) ticked + filename else ticked - filename
    }

    fun closeUpdateAll() {
        plan = null
    }

    fun updateAll(strings: Strings) {
        val read = plan?.value ?: return
        val body = UpdateAll.body(read, ticked)
        plan = null
        task = null
        work("update_all", strings, reload = ::loadMods) {
            Said(UpdateAll.done(strings, it.updateAll(serverId, body)), good = true)
        }
    }

    // ------------------------------------------------------------ Modrinth
    var found: Read<Found>? by mutableStateOf(null)
        private set
    var project: Read<ModProject>? by mutableStateOf(null)
        private set
    var missingPlan: Read<DependencyPlan>? by mutableStateOf(null)
        private set

    /** Counts searches, so an answer for an older query is dropped. */
    private var searchRead = 0

    fun search(query: String) {
        val c = client() ?: return
        val q = query.trim()
        if (q.isEmpty()) return
        val mine = ++searchRead
        found = Read(loading = true)
        scope.launch {
            val read = try {
                val page = c.searchMods(serverId, q)
                Read(Found(q, page.hits, page.total))
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
            if (mine == searchRead) found = read
        }
    }

    fun searchMore() {
        val c = client() ?: return
        val now = found?.value ?: return
        if (!now.more || found?.loading == true) return
        val mine = ++searchRead
        found = Read(now, loading = true)
        scope.launch {
            val read = try {
                val page = c.searchMods(serverId, now.query, offset = now.hits.size)
                Read(Found(now.query, now.hits + page.hits.filter { hit -> now.hits.none { it.slug == hit.slug } }, page.total))
            } catch (e: AgentException) {
                noted(e)
                Read(now, problem = Message.of(e))
            }
            if (mine == searchRead) found = read
        }
    }

    /** Reads a project's versions that fit this server, for the install question. */
    fun openProject(hit: ModrinthHit) {
        val c = client() ?: return
        project = Read(loading = true)
        scope.launch {
            project = try {
                Read(c.modProject(serverId, hit.slug))
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    fun closeProject() {
        project = null
    }

    fun install(withDependencies: Boolean, strings: Strings) {
        val read = project?.value ?: return
        val version = Install.latest(read) ?: return
        project = null
        work("install:${read.project.slug}", strings, reload = ::loadMods) {
            val done = it.installMod(serverId, read.project.slug, version.versionId, withDependencies)
            val name = done.installed?.name ?: read.project.title
            Said(strings.t("mobile.content.installed", "name" to name, "version" to (done.installed?.version ?: version.versionNumber ?: strings.t("value.unknown"))), good = true)
        }
    }

    /** What "Install the missing ones" would download; nothing is yet. */
    fun planMissing() {
        val c = client() ?: return
        missingPlan = Read(loading = true)
        scope.launch {
            missingPlan = try {
                Read(c.planDependencies(serverId))
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    fun closeMissing() {
        missingPlan = null
    }

    fun installMissing(strings: Strings) {
        missingPlan = null
        work("missing", strings, reload = ::loadMods) {
            val results = it.installDependencies(serverId).results
            Said(Install.missingDone(strings, results), good = Install.allWorked(results))
        }
    }

    // ------------------------------------------------------------ files
    var modpack: ModpackInspected? by mutableStateOf(null)
        private set

    fun uploadMod(file: PickedFile, strings: Strings) = work("upload", strings, reload = ::loadMods) {
        val done = it.uploadMod(serverId, file)
        Said(strings.t("mobile.content.added", "name" to (done.installed?.name ?: file.name)), good = true)
    }

    /** Reads the modpack on the PC; nothing changes until Import. */
    fun inspectModpack(file: PickedFile, strings: Strings) = work("modpack", strings) {
        modpack = it.inspectModpack(serverId, file)
        null
    }

    /** Modpacks found by name on Modrinth and CurseForge; picking one reads it like a file. */
    var packsFound: ModSearch? by mutableStateOf(null)
        private set

    fun findPacks(query: String, strings: Strings) = work("find_pack", strings) {
        packsFound = it.searchModpacks(query.trim())
        null
    }

    fun readFound(hit: ModrinthHit, strings: Strings) = work("modpack", strings) {
        modpack = it.fetchModpack(hit, serverId)
        packsFound = null
        null
    }

    /** No import after all: the PC forgets the pack it read. */
    fun cancelModpack() {
        val token = modpack?.token ?: return
        modpack = null
        val c = client() ?: return
        scope.launch {
            try { c.forgetModpack(token) } catch (e: AgentException) { /* The PC drops it later itself. */ }
        }
    }

    fun importModpack(strings: Strings) {
        val read = modpack ?: return
        modpack = null
        task = null
        work("modpack", strings, reload = ::loadMods) {
            // Its own jars were listed in the question just answered.
            val done = it.importModpack(serverId, read.token, ownJars = read.pack.ownJars.isNotEmpty())
            Said(strings.t("mobile.content.modpack_done", "name" to done.modpack.name.ifBlank { read.pack.name }), good = true)
        }
    }

    // ------------------------------------------------------------ Server / Players / Both
    fun setSide(filename: String, name: String, side: String, strings: Strings) = work("side:$filename", strings, reload = ::loadMods) {
        it.setModSide(serverId, filename, side)
        Said(strings.t("sides.marked_$side", "name" to name), good = true)
    }

    fun uploadForPlayers(file: PickedFile, strings: Strings) = work("players", strings, reload = ::loadMods) {
        it.uploadForPlayers(serverId, file)
        Said(strings.t("sides.players_added", "name" to file.name), good = true)
    }

    fun fetchForPlayers(filename: String, strings: Strings) = work("fetch:$filename", strings, reload = ::loadMods) {
        it.fetchForPlayers(serverId, filename)
        Said(strings.t("sides.players_added", "name" to filename), good = true)
    }

    fun setPlayerOnlyOptions(options: PlayerOnlyOptions, strings: Strings) = work("options", strings, reload = ::loadMods) {
        it.setPlayerOnlyOptions(serverId, options)
        Said(strings.t("sides.options_saved"), good = true)
    }

    fun removeForPlayers(filename: String, strings: Strings) = work("remove:$filename", strings, reload = ::loadMods) {
        it.removeForPlayers(serverId, filename)
        null
    }

    // ------------------------------------------------------------ linked modpack
    var packUpdate: ModpackUpdatePreview? by mutableStateOf(null)
        private set

    /** Gets the new version; nothing changes until Modpack update is pressed. */
    fun previewPackUpdate(versionId: String?, strings: Strings) = work("modpack", strings) {
        packUpdate = it.previewModpackUpdate(serverId, versionId)
        null
    }

    fun cancelPackUpdate() {
        val token = packUpdate?.token ?: return
        packUpdate = null
        val c = client() ?: return
        scope.launch {
            try { c.forgetModpack(token) } catch (e: AgentException) { /* The PC drops it later itself. */ }
        }
    }

    fun updatePack(usePackConfigs: Boolean, strings: Strings) {
        val read = packUpdate ?: return
        packUpdate = null
        work("modpack", strings, reload = ::loadMods) {
            it.updateModpack(serverId, read.token, usePackConfigs, ownJars = read.changes.newOwnJars.isNotEmpty())
            Said(strings.t("linked.updated", "name" to read.pack.name, "version" to read.changes.toVersion), good = true)
        }
    }

    fun unlinkPack(strings: Strings) = work("unlink", strings, reload = ::loadMods) {
        it.unlinkModpack(serverId)
        null
    }

    // ------------------------------------------------------------ Make a modpack
    /** Set while the Make a modpack question shows. */
    var makeOptions: ModpackMakeOptions? by mutableStateOf(null)
        private set

    /** The modpack made last, with Share and Post in Discord. */
    var made: ModpackMade? by mutableStateOf(null)
        private set

    fun askMakeModpack(strings: Strings) = work("make", strings) {
        makeOptions = it.makeModpackOptions(serverId)
        null
    }

    fun cancelMakeModpack() {
        makeOptions = null
        backgroundSet = false
    }

    /** A title-screen picture was uploaded while the question shows (its answers stay). */
    var backgroundSet by mutableStateOf(false)
        private set

    fun uploadTitleBackground(file: PickedFile, strings: Strings) = work("background", strings) {
        backgroundSet = it.uploadTitleBackground(serverId, file).titleScreen.backgroundSet
        null
    }

    fun makeModpack(answers: MakeModpack, strings: Strings) {
        makeOptions = null
        backgroundSet = false
        work("make", strings) {
            made = it.makeModpack(serverId, answers)
            loadLink() // the modpack's download link, if it's on
            loadModrinth() // "Upload Friends 2"
            null
        }
    }

    /** Downloads the modpack into [folder] (the old one there is cleared out) and opens the share menu. */
    fun shareModpack(folder: File, strings: Strings, share: (File) -> Unit) = work("share", strings) { c ->
        val pack = made ?: return@work null
        val name = pack.filename.substringAfterLast('/').substringAfterLast('\\').ifBlank { "modpack.mrpack" }
        val file = withContext(Dispatchers.IO) {
            folder.mkdirs()
            folder.listFiles()?.forEach { it.delete() }
            val target = File(folder, name)
            try {
                target.outputStream().use { out -> c.downloadModpack(serverId, pack, out) }
            } catch (e: Exception) {
                target.delete()
                throw e
            }
            target
        }
        share(file)
        null
    }

    fun postModpack(strings: Strings) = work("discord", strings) {
        val pack = made ?: return@work null
        val posted = it.postModpack(serverId, pack.token)
        Said(strings.t(if (posted.asLink) "make.posted_link" else "make.posted", "count" to posted.posted), good = true)
    }

    // ------------------------------------------------------------ Download link
    /** The download link and the server resource pack, once read. */
    var link: DownloadLink? by mutableStateOf(null)
        private set

    /** Set while "Use a link…" asks for the pack's link and SHA-1. */
    var askingPackLink by mutableStateOf(false)

    /** Read on its own, not as a "work": the mods list may be loading at the same time. */
    fun loadLink() {
        val c = client() ?: return
        scope.launch {
            try {
                link = c.downloadLink(serverId)
            } catch (e: AgentException) {
                noted(e)
            }
        }
    }

    fun switchLink(on: Boolean, strings: Strings) = work("link", strings) {
        link = it.setDownloadLink(serverId, on)
        null
    }

    fun newLink(strings: Strings) = work("link", strings) {
        link = it.newDownloadLink(serverId)
        null
    }

    fun uploadPack(file: PickedFile, strings: Strings) = work("pack", strings) {
        link = it.uploadResourcePack(serverId, file, link?.resourcePack?.required ?: false)
        null
    }

    fun setPackLink(url: String, sha1: String, strings: Strings) {
        askingPackLink = false
        work("pack", strings) {
            link = it.setResourcePackLink(serverId, url, sha1, link?.resourcePack?.required ?: false)
            null
        }
    }

    fun setPackRequired(required: Boolean, strings: Strings) = work("pack", strings) {
        link = it.setResourcePackRequired(serverId, required)
        null
    }

    fun removePack(strings: Strings) = work("pack", strings) {
        link = it.removeResourcePack(serverId)
        null
    }

    // ------------------------------------------------------------ Put it on Modrinth
    var modrinth: ModrinthShare? by mutableStateOf(null)
        private set

    /** Asking the Modrinth address and summary: for the modpack (no [ModrinthAsk.jar]) or one own jar. */
    var askingModrinth: ModrinthAsk? by mutableStateOf(null)

    /** Set while "Paste its link…" asks for the Server Project's link. */
    var askingServerLink by mutableStateOf(false)

    fun loadModrinth() {
        val c = client() ?: return
        scope.launch {
            try {
                modrinth = c.modrinth(serverId)
            } catch (e: AgentException) {
                noted(e)
            }
        }
    }

    /** Opens Modrinth's page; it comes back as mcsc://modrinth (MainActivity). */
    fun signInModrinth(open: (String) -> Unit, strings: Strings) = work("modrinth", strings) {
        open(it.modrinthSignIn().url)
        Said(strings.t("modrinth.waiting"), good = true)
    }

    fun finishModrinth(link: String, strings: Strings) {
        val (code, state) = ModrinthCards.cameBack(link) ?: return tell(Said(strings.t("modrinth.said_no"), good = false))
        work("modrinth", strings) {
            it.modrinthFinish(code, state)
            modrinth = it.modrinth(serverId)
            Said(strings.t("modrinth.welcome"), good = true)
        }
    }

    fun signOutModrinth(strings: Strings) = work("modrinth", strings) {
        it.modrinthSignOut()
        modrinth = it.modrinth(serverId)
        null
    }

    fun uploadToModrinth(slug: String, summary: String, strings: Strings) {
        val jar = askingModrinth?.jar
        askingModrinth = null
        work("modrinth", strings) {
            modrinth = if (jar == null) it.modrinthUpload(serverId, slug, summary) else it.modrinthOwnJar(serverId, jar.path, slug, summary)
            Said(strings.t("modrinth.uploaded"), good = true)
        }
    }

    fun setServerProject(link: String, strings: Strings) {
        askingServerLink = false
        work("modrinth", strings) {
            modrinth = it.modrinthServerProject(serverId, link)
            null
        }
    }

    // ------------------------------------------------------------ the anti-cheat picker
    var antiCheat: AntiCheat? by mutableStateOf(null)
        private set

    fun loadAntiCheat() {
        val c = client() ?: return
        scope.launch {
            try {
                antiCheat = c.antiCheat(serverId)
            } catch (e: AgentException) {
                noted(e)
            }
        }
    }

    // ------------------------------------------------------------ Bedrock packs
    var addons: Read<AddonList> by mutableStateOf(Read())
        private set

    /** Set after a pack change while the server runs: "Restart now" shows. */
    var restartOffered: Boolean by mutableStateOf(false)
        private set

    fun loadAddons() {
        val c = client() ?: return
        addons = addons.copy(loading = true)
        scope.launch {
            addons = try {
                Read(c.addons(serverId))
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    fun setAddon(addon: Addon, on: Boolean, strings: Strings) = work("pack:${addon.uuid}", strings, reload = ::loadAddons) {
        restartOffered = it.setAddonEnabled(serverId, addon.uuid, on).restartNeeded == true
        Said(strings.t(if (on) "addons.turned_on" else "addons.turned_off", "name" to addon.name), good = true)
    }

    fun removeAddon(addon: Addon, strings: Strings) = work("pack:${addon.uuid}", strings, reload = ::loadAddons) {
        restartOffered = it.removeAddon(serverId, addon.uuid).restartNeeded == true
        Said(strings.t("addons.removed", "name" to addon.name), good = true)
    }

    fun uploadAddon(file: PickedFile, strings: Strings) = work("upload", strings, reload = ::loadAddons) {
        val done = it.uploadAddon(serverId, file)
        restartOffered = done.restartNeeded == true
        Said(strings.t("addons.installed_done", "name" to done.installed.joinToString(", ") { a -> a.name }.ifBlank { file.name }), good = true)
    }

    fun restarted() {
        restartOffered = false
    }

    // ------------------------------------------------------------ game settings
    var game: Read<GameSettings> by mutableStateOf(Read())
        private set

    /** What the person typed or picked, by key, as text. */
    var edits: Map<String, String> by mutableStateOf(emptyMap())
        private set

    /** Each refused value's reason, by key, from the last Save. */
    var fieldProblems: Map<String, String> by mutableStateOf(emptyMap())
        private set

    /** Saved while running: "Restart now" shows next to the words. */
    var gameRestart: Boolean by mutableStateOf(false)
        private set

    fun loadGame() {
        val c = client() ?: return
        game = game.copy(loading = true)
        scope.launch {
            game = try {
                Read(c.gameSettings(serverId)).also {
                    edits = emptyMap()
                    fieldProblems = emptyMap()
                }
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    fun edit(key: String, value: String) {
        edits = edits + (key to value)
        fieldProblems = fieldProblems - key
    }

    /** True when Save would send something. */
    fun gameChanged(): Boolean {
        val settings = game.value ?: return false
        return GameForm.changed(settings, edits).isNotEmpty()
    }

    fun saveGame(strings: Strings) {
        val settings = game.value ?: return
        val values = GameForm.changed(settings, edits)
        val c = client() ?: return
        if (busy != null) return
        busy = "game"
        said = null
        scope.launch {
            try {
                val done = c.saveGameSettings(serverId, values)
                said = Said(GameForm.saved(strings, done), good = true)
                gameRestart = done.running && done.nothingChanged != true
                loadGame()
            } catch (e: AgentException.Failed) {
                noted(e)
                fieldProblems = e.problems
                said = Said(if (e.problems.isNotEmpty()) strings.t("mobile.game.fix_marked") else Message.of(e).text(strings), good = false)
            } catch (e: AgentException) {
                noted(e)
                said = Said(Message.of(e).text(strings), good = false)
            } finally {
                busy = null
            }
        }
    }

    /** Saves the whole file as typed in Technical mode. A refused value
     * leaves the file as it was, and its reason is shown. */
    fun saveGameText(text: String, strings: Strings) {
        val c = client() ?: return
        if (busy != null) return
        busy = "game_text"
        said = null
        scope.launch {
            try {
                val done = c.saveGameSettingsText(serverId, text)
                said = Said(GameForm.saved(strings, done), good = true)
                gameRestart = done.running && done.nothingChanged != true
                loadGame()
            } catch (e: AgentException.Failed) {
                noted(e)
                said = Said(e.problems.entries.sortedBy { it.key }.joinToString(" ") { "${it.key}: ${it.value}" }.ifBlank { Message.of(e).text(strings) }, good = false)
            } catch (e: AgentException) {
                noted(e)
                said = Said(Message.of(e).text(strings), good = false)
            } finally {
                busy = null
            }
        }
    }

    fun gameRestarted() {
        gameRestart = false
    }

    // ------------------------------------------------------------ game rules
    /** Read from the running server only when the screen asks (one question per rule). */
    var rules: Read<GameRules> by mutableStateOf(Read())
        private set

    /** Changed values by rule name, waiting for Save rules. */
    var ruleEdits: Map<String, JsonPrimitive> by mutableStateOf(emptyMap())
        private set

    /** What the last change was before, for Undo; empty: nothing to undo. */
    var rulesBefore: JsonObject by mutableStateOf(JsonObject(emptyMap()))
        private set

    var rulesSaid: Message? by mutableStateOf(null)
        private set

    /** Rules of a preset this server doesn't have, from the last change. */
    var rulesSkipped: List<String> by mutableStateOf(emptyList())
        private set

    fun loadRules() {
        val c = client() ?: return
        rules = rules.copy(loading = true)
        scope.launch {
            rules = try {
                Read(c.gameRules(serverId)).also { ruleEdits = emptyMap() }
            } catch (e: AgentException) {
                noted(e)
                Read(problem = Message.of(e))
            }
        }
    }

    fun editRule(name: String, value: JsonPrimitive) {
        val said = rules.value?.rules?.firstOrNull { it.name == name }?.value
        ruleEdits = if (said == value) ruleEdits - name else ruleEdits + (name to value)
    }

    /** Saves [changed] (Save rules or Undo), or applies [preset]. */
    fun saveRules(changed: JsonObject = JsonObject(ruleEdits), preset: String? = null) {
        val c = client() ?: return
        if (busy != null) return
        busy = "rules"
        scope.launch {
            try {
                val done = c.saveGameRules(serverId, changed, preset)
                val current = rules.value ?: GameRules(running = true)
                rules = Read(current.copy(rules = done.rules))
                ruleEdits = emptyMap()
                rulesBefore = done.before
                rulesSaid = if (done.before.isEmpty()) Message("gamerules.no_change")
                else Message("gamerules.saved", mapOf("n" to done.before.size.toString()))
                rulesSkipped = done.skipped
            } catch (e: AgentException) {
                noted(e)
                rulesSaid = Message.of(e)
                rulesSkipped = emptyList()
            } finally {
                busy = null
            }
        }
    }

    // ------------------------------------------------------------ server icon
    /** The server icon as base64 PNG; null while unknown or when there is none. */
    var icon: String? by mutableStateOf(null)
        private set

    fun loadIcon() {
        val c = client() ?: return
        scope.launch {
            icon = try {
                c.serverIcon(serverId)
            } catch (e: AgentException) {
                noted(e)
                null
            }
        }
    }

    /** [png] is already 64 x 64 (ServerIconPicker makes it). Undo is the
     * server's own Undo, like every change the agent backs up first. */
    fun saveIcon(png: String, strings: Strings) = work("icon", strings, reload = ::loadGame) {
        val done = it.saveServerIcon(serverId, png)
        icon = png
        Said(strings.t("motd.icon_saved") + if (done.running) " " + strings.t("motd.icon_restart") else "", good = true)
    }

    fun removeIcon(strings: Strings) = work("icon", strings, reload = ::loadGame) {
        it.removeServerIcon(serverId)
        icon = null
        Said(strings.t("motd.icon_removed"), good = true)
    }

    // ------------------------------------------------------------ doing
    private fun work(name: String, strings: Strings, reload: (() -> Unit)? = null, call: suspend (AgentClient) -> Said?) {
        val c = client() ?: return
        if (busy != null) return
        busy = name
        said = null
        scope.launch {
            try {
                call(c)?.let { said = it }
            } catch (e: AgentException) {
                noted(e)
                said = Said(Message.of(e).text(strings), good = false)
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                // Reading the picked file on the phone failed: said as such.
                said = Said(strings.t("mobile.error.failed", "reason" to (e.message ?: strings.t("value.unknown"))), good = false)
            } finally {
                busy = null
            }
            reload?.invoke()
        }
    }
}
