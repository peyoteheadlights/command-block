package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/*
 * What the Mods / Plugins / Packs and Game settings screens read. As in
 * PageModels.kt, only the fields the app uses are listed, and a value the
 * agent can leave out is null here and shown as Unknown.
 */

/** Something wrong with one mod, in the agent's words. */
@Serializable
data class ModIssue(
    val kind: String = "",
    val severity: String = "info",
    val detail: String = "",
)

/** A newer version found on Modrinth at the agent's last check. */
@Serializable
data class ModUpdate(
    @SerialName("latest_version") val latestVersion: String = "",
    @SerialName("version_id") val versionId: String? = null,
)

/** One mod or plugin in the server's folder. */
@Serializable
data class Mod(
    val filename: String,
    @SerialName("mod_id") val modId: String = "",
    val name: String = "",
    val version: String = "",
    val enabled: Boolean = true,
    val description: String? = null,
    @SerialName("minecraft_range") val minecraftRange: String? = null,
    /** ok, warn or error while on; disabled while off. */
    val status: String? = null,
    val issues: List<ModIssue> = emptyList(),
    @SerialName("update_available") val updateAvailable: ModUpdate? = null,
    /** On a linked modpack's server: true when the modpack didn't put it there. */
    @SerialName("added_by_you") val addedByYou: Boolean? = null,
    val loader: String? = null,
    /** server, players or both: who needs it. */
    val side: String? = null,
    /** Where [side] came from: you, modpack, modrinth, curseforge, file or unknown. */
    @SerialName("side_from") val sideFrom: String? = null,
)

@Serializable
data class ModList(
    val mods: List<Mod> = emptyList(),
    /** Null until the server has started once. */
    @SerialName("minecraft_version") val minecraftVersion: String? = null,
    @SerialName("server_state") val serverState: String = "UNKNOWN",
    /** The server's drive can't link to the shared store, so its jars are copies. */
    @SerialName("stored_separately") val storedSeparately: Boolean = false,
    /** The "Still fit?" card after a mod or version change; null: nothing to show. */
    val fit: ModFit? = null,
    /** The same mod twice, or mods that do the same job. */
    val overlaps: List<Overlap> = emptyList(),
    /** The modpack this server came from; null: not linked. */
    val modpack: LinkedModpack? = null,
    /** The player-only folder: kept for the modpack, never loaded by the server. */
    @SerialName("player_only") val playerOnly: List<PlayerFile> = emptyList(),
    @SerialName("player_only_options") val playerOnlyOptions: PlayerOnlyOptions = PlayerOnlyOptions(),
    /** A mod needs one that sits on the other side. */
    @SerialName("odd_setups") val oddSetups: List<OddSetup> = emptyList(),
)

@Serializable
data class PlayerFile(
    val filename: String,
    val name: String = "",
    val version: String = "",
    /** mod or pack (a resource pack or shader pack). */
    val kind: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    /** Not the file the app first saw: changed outside it. */
    val changed: Boolean = false,
    /** The app added it, but it's gone (Get it again when [site] is modrinth). */
    val missing: Boolean = false,
    val site: String? = null,
)

@Serializable
data class PlayerOnlyOptions(
    /** Back up the player-only folder. */
    val backup: Boolean = true,
    /** Leave out files Modrinth lists; got again by hash after a restore. */
    @SerialName("skip_listed") val skipListed: Boolean = false,
)

@Serializable
data class OddSetup(val filename: String = "", val text: String = "")

@Serializable
data class ModSideChanged(val side: String = "", val moved: Boolean = false)

@Serializable
data class PlayerFileAdded(val added: String = "", val kind: String = "")

@Serializable
data class LinkedModpackUpdate(
    @SerialName("version_id") val versionId: String,
    /** Empty for CurseForge, whose names aren't kept. */
    val version: String = "",
)

@Serializable
data class LinkedModpack(
    val name: String = "",
    /** Modrinth or CurseForge. */
    val site: String = "",
    val version: String = "",
    val update: LinkedModpackUpdate? = null,
    @SerialName("added_by_you") val addedByYou: Int = 0,
)

@Serializable
data class NewerMod(val name: String = "", val from: String = "", val to: String = "")

/** What Modpack update changes, compared by file hash. */
@Serializable
data class ModpackChanges(
    @SerialName("from_version") val fromVersion: String = "",
    @SerialName("to_version") val toVersion: String = "",
    val added: List<String> = emptyList(),
    val removed: List<String> = emptyList(),
    val newer: List<NewerMod> = emptyList(),
    @SerialName("added_by_you") val addedByYou: List<String> = emptyList(),
    @SerialName("configs_changed_by_you") val configsChangedByYou: List<String> = emptyList(),
    /** New or changed own jars: the update needs ownJars = true. */
    @SerialName("new_own_jars") val newOwnJars: List<String> = emptyList(),
    @SerialName("version_change") val versionChange: Boolean = false,
    @SerialName("minecraft_version") val minecraftVersion: String = "",
    val problems: List<String> = emptyList(),
)

@Serializable
data class ModpackUpdatePreview(
    val token: String,
    val pack: Modpack = Modpack(),
    val changes: ModpackChanges = ModpackChanges(),
)

@Serializable
data class ModpackUpdated(val undo: Undo? = null)

/** A config file Make a modpack can put in. */
@Serializable
data class ModpackConfigFile(val path: String, @SerialName("size_bytes") val sizeBytes: Long = 0)

@Serializable
data class PlayersLoader(val id: String, val name: String = "")

@Serializable
data class RequiredToJoin(val available: Boolean = false)

/** What Make a modpack offers. [playersLoaders] is empty on a mod server. */
@Serializable
data class ModpackMakeOptions(
    val name: String = "",
    val version: String = "1",
    val configs: List<ModpackConfigFile> = emptyList(),
    @SerialName("players_loaders") val playersLoaders: List<PlayersLoader> = emptyList(),
    val discord: Boolean = false,
    @SerialName("required_to_join") val requiredToJoin: RequiredToJoin = RequiredToJoin(),
)

/** The person's answers to Make a modpack. */
data class MakeModpack(
    val name: String,
    val version: String,
    val leaveOutServerOnly: Boolean = true,
    val configs: List<String> = emptyList(),
    val playersLoader: String = "fabric",
)

/** A CurseForge mod its author keeps from other apps: never put in. */
@Serializable
data class GetYourself(val name: String = "", val page: String? = null)

/** A made modpack, downloaded with [token]. [trustNote]: own files are inside. */
@Serializable
data class ModpackMade(
    val token: String,
    val filename: String = "modpack.mrpack",
    val files: Int = 0,
    @SerialName("size_bytes") val sizeBytes: Long? = null,
    @SerialName("own_files") val ownFiles: List<String> = emptyList(),
    @SerialName("trust_note") val trustNote: String? = null,
    @SerialName("get_yourself") val getYourself: List<GetYourself> = emptyList(),
    @SerialName("plugins_to_get") val pluginsToGet: List<String> = emptyList(),
    @SerialName("left_out") val leftOut: List<String> = emptyList(),
    val discord: Boolean = false,
)

/** [asLink]: too big for Discord, so the bot posted the download link. */
@Serializable
data class ModpackPosted(val posted: Int = 0, @SerialName("as_link") val asLink: Boolean = false)

/** The server resource pack: a zip the download link serves ([served]), or an outside link. */
@Serializable
data class ResourcePack(
    val source: String,
    val filename: String? = null,
    val url: String? = null,
    val sha1: String? = null,
    @SerialName("size_bytes") val sizeBytes: Long? = null,
    val required: Boolean = false,
    val served: Boolean = false,
)

/** The Download link card. [whyNot]: why it can't be turned on (no Tailscale yet). */
@Serializable
data class DownloadLink(
    val on: Boolean = false,
    @SerialName("why_not") val whyNot: String? = null,
    val listening: Boolean = false,
    @SerialName("modpack_url") val modpackUrl: String? = null,
    @SerialName("speed_cap_bytes") val speedCapBytes: Long = 0,
    @SerialName("resource_pack") val resourcePack: ResourcePack? = null,
    @SerialName("max_pack_bytes") val maxPackBytes: Long = 0,
    @SerialName("restart_needed") val restartNeeded: Boolean = false,
)

/** Sign in with Modrinth on the PC. The token itself never reaches the phone. */
@Serializable
data class ModrinthAccount(
    val available: Boolean = false,
    @SerialName("signed_in") val signedIn: Boolean = false,
    val username: String? = null,
    @SerialName("ran_out") val ranOut: Boolean = false,
)

@Serializable
data class ModrinthSignInStarted(val url: String)

@Serializable
data class ModrinthMade(val name: String, val version: String)

@Serializable
data class ModrinthOwnProject(val id: String, val slug: String, val title: String = "", val url: String)

@Serializable
data class ModrinthUpload(@SerialName("version_id") val versionId: String, val version: String)

/** An own jar in the newest modpack; [url] once it is on Modrinth. */
@Serializable
data class ModrinthJar(val path: String, val name: String, val url: String? = null)

@Serializable
data class ModrinthServerProject(val url: String)

@Serializable
data class ModrinthOlder(val listed: String, val newest: String)

/** The "Put it on Modrinth" card. [address]: the address typed for friends. */
@Serializable
data class ModrinthShare(
    val account: ModrinthAccount = ModrinthAccount(),
    val made: ModrinthMade? = null,
    val project: ModrinthOwnProject? = null,
    val uploads: List<ModrinthUpload> = emptyList(),
    @SerialName("own_jars") val ownJars: List<ModrinthJar> = emptyList(),
    @SerialName("server_project") val serverProject: ModrinthServerProject? = null,
    val older: ModrinthOlder? = null,
    val address: String? = null,
)

/** A free anti-cheat, installed from Modrinth with the normal install. [warning]: Fabric with mods players need. */
@Serializable
data class AntiCheatFree(val id: String, val name: String, val project: String, val on: Boolean = false, val warning: Boolean = false)

/** A paid anti-cheat: bought on the seller's [link]. [price]: null when none was read; [checked]: the day it was. */
@Serializable
data class AntiCheatPaid(val id: String, val name: String, val on: Boolean = false, val link: String, val setup: String? = null, val price: String? = null, val checked: String? = null)

/** The anti-cheat picker. [supported]: false when none runs on this server type. */
@Serializable
data class AntiCheat(
    val supported: Boolean = false,
    val installed: List<String> = emptyList(),
    val free: List<AntiCheatFree> = emptyList(),
    val paid: List<AntiCheatPaid> = emptyList(),
    @SerialName("may_install") val mayInstall: Boolean = false,
    @SerialName("may_add_own_jar") val mayAddOwnJar: Boolean = false,
)

/** One mod in an [Overlap]. [turnOff]: the pick isn't this one. */
@Serializable
data class OverlapMod(
    val filename: String,
    @SerialName("mod_id") val modId: String = "",
    val name: String = "",
    val version: String = "",
    @SerialName("turn_off") val turnOff: Boolean = false,
    /** An installed mod that needs this one, "" when none. */
    @SerialName("needed_by") val neededBy: String = "",
)

/** The same mod twice ("same_mod"), mods on the notes file's same-job list
 * ("same_job") or a site saying two don't work together ("site_says"). */
@Serializable
data class Overlap(
    val kind: String = "",
    val why: String = "",
    /** The file to keep, "" when nothing says which. */
    val keep: String = "",
    /** "Keep 0.5.2, turn off 0.5.1", or "Keep the one you use". */
    val say: String = "",
    val mods: List<OverlapMod> = emptyList(),
)

/** One mod on the "Still fit?" card: what the mods or its site said. For
 * one that needs a change, [from], [to] and [versionId] are filled. */
@Serializable
data class FitRow(
    @SerialName("mod_id") val modId: String = "",
    val name: String = "",
    val filename: String,
    val why: String = "",
    val from: String = "",
    val to: String = "",
    @SerialName("version_id") val versionId: String? = null,
)

@Serializable
data class ModFit(
    /** What changed: "Create 6.0" or "Minecraft 1.21.4". */
    val about: String = "",
    @SerialName("needs_change") val needsChange: List<FitRow> = emptyList(),
    @SerialName("no_fit") val noFit: List<FitRow> = emptyList(),
    @SerialName("cant_tell") val cantTell: List<FitRow> = emptyList(),
)

@Serializable
data class ModChanged(val mod: Mod? = null)

@Serializable
data class ModDependent(
    val name: String = "",
    val requires: String? = null,
)

/** What else needs a mod, asked before it is removed. */
@Serializable
data class ModImpact(
    val dependents: List<ModDependent> = emptyList(),
    val warning: String? = null,
)

@Serializable
data class ModRef(
    val filename: String = "",
    val name: String = "",
    val version: String = "",
)

@Serializable
data class ModRemoved(val removed: ModRef? = null)

@Serializable
data class ModUpdated(
    val updated: ModRef? = null,
    @SerialName("previous_version") val previousVersion: String? = null,
)

/** A copy of a mod kept on this server, which it can roll back to. */
@Serializable
data class ModVersion(
    val version: String? = null,
    val filename: String = "",
    @SerialName("archive_path") val archivePath: String,
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("is_current") val isCurrent: Boolean = false,
    /** False when the kept file has gone. */
    val exists: Boolean = false,
)

@Serializable
data class ModVersions(val versions: List<ModVersion> = emptyList())

@Serializable
data class ModRolledBack(
    val restored: ModRef? = null,
    @SerialName("previous_version") val previousVersion: String? = null,
)

/** One change in the history: what, when, who, and whether it worked. */
@Serializable
data class ModHistoryRow(
    val ts: Double = 0.0,
    val user: String? = null,
    val action: String = "",
    @SerialName("mod_id") val modId: String? = null,
    @SerialName("mod_name") val modName: String? = null,
    @SerialName("old_version") val oldVersion: String? = null,
    @SerialName("new_version") val newVersion: String? = null,
    val result: String = "",
    val detail: String? = null,
)

@Serializable
data class ModHistory(val history: List<ModHistoryRow> = emptyList())

/** A Modrinth search result. [iconUrl] is loaded straight from Modrinth. */
@Serializable
data class ModrinthHit(
    @SerialName("project_id") val projectId: String? = null,
    val slug: String = "",
    val title: String = "",
    val description: String? = null,
    val downloads: Long? = null,
    @SerialName("icon_url") val iconUrl: String? = null,
    val page: String? = null,
    /** "modrinth" or "curseforge", and the site's name as shown. */
    val source: String? = null,
    @SerialName("source_name") val sourceName: String? = null,
    /** Modpacks: "Fabric", "Forge"..., the newest Minecraft version, "unsupported" when it's
     *  made for players' games only, and what Modrinth was told it holds ("Shows ads"). */
    val loaders: List<String> = emptyList(),
    @SerialName("minecraft_version") val minecraftVersion: String? = null,
    @SerialName("server_side") val serverSide: String? = null,
    val disclosures: List<String> = emptyList(),
)

@Serializable
data class ModSearch(
    val hits: List<ModrinthHit> = emptyList(),
    val total: Int? = null,
    /** A site that couldn't be asked: "CurseForge isn't available right now." */
    val problems: List<String> = emptyList(),
)

@Serializable
data class ModrinthFile(val filename: String? = null, val size: Long? = null)

@Serializable
data class ModrinthDependency(
    @SerialName("project_id") val projectId: String? = null,
    val type: String? = null,
)

@Serializable
data class ModrinthVersion(
    @SerialName("version_id") val versionId: String? = null,
    @SerialName("version_number") val versionNumber: String? = null,
    @SerialName("game_versions") val gameVersions: List<String> = emptyList(),
    val dependencies: List<ModrinthDependency> = emptyList(),
    val file: ModrinthFile? = null,
)

@Serializable
data class ModrinthProject(
    val slug: String = "",
    val title: String = "",
    val description: String? = null,
    val license: String? = null,
    @SerialName("server_side") val serverSide: String? = null,
    @SerialName("client_side") val clientSide: String? = null,
    @SerialName("icon_url") val iconUrl: String? = null,
    val page: String? = null,
)

/** A Modrinth project and its versions that fit this server, newest first. */
@Serializable
data class ModProject(
    val project: ModrinthProject = ModrinthProject(),
    val versions: List<ModrinthVersion> = emptyList(),
)

@Serializable
data class DependencyNeeded(
    @SerialName("mod_id") val modId: String = "",
    val title: String? = null,
    val installed: Boolean = false,
)

@Serializable
data class ModInstalled(
    val installed: ModRef? = null,
    @SerialName("dependencies_required") val dependenciesRequired: List<DependencyNeeded> = emptyList(),
)

/** A link to a mod's page, when Modrinth has one. */
@Serializable
data class DependencyPage(val page: String? = null)

@Serializable
data class DependencyItem(
    @SerialName("mod_id") val modId: String,
    val name: String = "",
    val kind: String = "",
    /** Minecraft, the loader or Java rather than a mod. */
    val platform: Boolean = false,
    val status: String? = null,
    @SerialName("range_text") val rangeText: String? = null,
    val reason: String? = null,
    val modrinth: DependencyPage? = null,
)

@Serializable
data class DependencyReport(
    val items: List<DependencyItem> = emptyList(),
    @SerialName("missing_required") val missingRequired: List<String> = emptyList(),
)

@Serializable
data class PlannedDependency(
    @SerialName("mod_id") val modId: String = "",
    val title: String? = null,
    @SerialName("version_number") val versionNumber: String? = null,
    val size: Long? = null,
)

/** One the app can't install by itself, with a page to get it by hand. */
@Serializable
data class UnresolvableDependency(
    @SerialName("mod_id") val modId: String = "",
    val name: String? = null,
    val reason: String = "",
    val page: String? = null,
)

/** What "Install the missing ones" would download. Nothing is yet. */
@Serializable
data class DependencyPlan(
    val items: List<PlannedDependency> = emptyList(),
    val unresolvable: List<UnresolvableDependency> = emptyList(),
)

@Serializable
data class DependencyResult(
    val title: String = "",
    val result: String = "",
    val detail: String? = null,
)

@Serializable
data class DependencyInstall(val results: List<DependencyResult> = emptyList())

@Serializable
data class ModUploaded(val installed: ModRef? = null)

/** A mod Update all would change. Turned-off ones are listed, not ticked. */
@Serializable
data class UpdateAllRow(
    @SerialName("mod_id") val modId: String = "",
    val name: String = "",
    val filename: String,
    val enabled: Boolean = true,
    @SerialName("installed_version") val installedVersion: String = "",
    @SerialName("latest_version") val latestVersion: String = "",
    @SerialName("version_id") val versionId: String? = null,
)

/** One Modrinth couldn't be asked about, with the reason. */
@Serializable
data class UpdateAllUnchecked(
    val name: String = "",
    val reason: String = "",
)

@Serializable
data class UpdateAllPlan(
    val updates: List<UpdateAllRow> = emptyList(),
    val unchecked: List<UpdateAllUnchecked> = emptyList(),
    @SerialName("server_running") val serverRunning: Boolean = false,
    @SerialName("blocked_by") val blockedBy: String? = null,
)

@Serializable
data class UpdateAllDone(
    val name: String = "",
    @SerialName("previous_version") val previousVersion: String = "",
    val version: String = "",
)

@Serializable
data class UpdateAllResult(
    val updated: List<UpdateAllDone> = emptyList(),
    @SerialName("safety_backup") val safetyBackup: SafetyBackup? = null,
    val undo: Undo? = null,
)

@Serializable
data class ModpackFile(
    val name: String = "",
    val size: Long? = null,
    /** download, client_only or refused. */
    val status: String = "",
    val reason: String? = null,
)

/** What a modpack holds, read before anything changes. */
@Serializable
data class Modpack(
    val name: String = "",
    val version: String? = null,
    val summary: String? = null,
    @SerialName("minecraft_version") val minecraftVersion: String? = null,
    @SerialName("type_name") val typeName: String? = null,
    @SerialName("loader_version") val loaderVersion: String? = null,
    val files: List<ModpackFile> = emptyList(),
    @SerialName("download_count") val downloadCount: Int = 0,
    @SerialName("download_bytes") val downloadBytes: Long = 0,
    @SerialName("client_only") val clientOnly: List<String> = emptyList(),
    @SerialName("skipped_overrides") val skippedOverrides: List<String> = emptyList(),
    /** Jars no site checked: importing them needs ownJars = true and "Add own .jar". */
    @SerialName("own_jars") val ownJars: List<String> = emptyList(),
    /** From the pack's command-block.json: plugins to get by hand. */
    @SerialName("plugins_to_get") val pluginsToGet: List<String> = emptyList(),
    val problems: List<String> = emptyList(),
    @SerialName("can_import") val canImport: Boolean = false,
)

@Serializable
data class ModpackInto(@SerialName("version_change") val versionChange: Boolean = false)

@Serializable
data class ModpackInspected(
    val token: String,
    val pack: Modpack = Modpack(),
    val into: ModpackInto? = null,
)

@Serializable
data class ModpackSummary(val name: String = "")

@Serializable
data class ModpackImported(
    val modpack: ModpackSummary = ModpackSummary(),
    val undo: Undo? = null,
)

/** One Bedrock behavior or resource pack. */
@Serializable
data class Addon(
    val uuid: String,
    val name: String = "",
    val version: String = "",
    val description: String? = null,
    /** behavior or resource. */
    val kind: String = "",
    val folder: String = "",
    val enabled: Boolean = false,
)

@Serializable
data class AddonList(
    val addons: List<Addon> = emptyList(),
    /** The world the packs are on; null before the server made one. */
    val world: String? = null,
    val running: Boolean = false,
    @SerialName("restart_needed") val restartNeeded: Boolean? = null,
)

@Serializable
data class AddonResult(
    val installed: List<Addon> = emptyList(),
    @SerialName("restart_needed") val restartNeeded: Boolean? = null,
)

/** One setting from server.properties. [value] is typed (a word, a number,
 * true or false); null when the file doesn't set it or can't be read. */
@Serializable
data class GameSetting(
    val key: String,
    /** choice, bool, int, text, port or seed. */
    val kind: String = "text",
    val value: JsonElement? = null,
    val set: Boolean = false,
    val problem: String? = null,
    /** The file's own text for it, as written (shown when it can't be read as a value). */
    val raw: String? = null,
    @SerialName("minecraft_default") val minecraftDefault: String = "",
    val choices: List<String> = emptyList(),
    val min: Int? = null,
    val max: Int? = null,
    @SerialName("max_length") val maxLength: Int? = null,
    /** How many lines the value can have (the MOTD has 2). */
    val lines: Int = 1,
    /** "world_exists" for the seed once the world is made. */
    @SerialName("read_only") val readOnly: String? = null,
)

@Serializable
data class GameSettings(
    val file: String = "",
    val exists: Boolean = false,
    val fields: List<GameSetting> = emptyList(),
    val running: Boolean = false,
    @SerialName("restart_needed") val restartNeeded: Boolean = false,
    /** The whole file as it is now, for the Technical mode editor. */
    val text: String = "",
    /** Whether server-icon.png is there; null on Bedrock, which shows none. */
    val icon: Boolean? = null,
)

/** server-icon.png in base64, or null when the server has none. */
@Serializable
data class ServerIcon(val png: String? = null)

@Serializable
data class ServerIconSaved(
    val icon: Boolean = false,
    val running: Boolean = false,
)

@Serializable
data class GameSettingsSaved(
    val keys: List<String> = emptyList(),
    @SerialName("nothing_changed") val nothingChanged: Boolean? = null,
    val running: Boolean = false,
    @SerialName("restart_needed") val restartNeeded: Boolean = false,
)
