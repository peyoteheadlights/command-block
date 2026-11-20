package io.github.peyoteheadlights.mcsc.core

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ContentTest {
    private val token = "secret-session-token-1234567890"
    private val strings = Strings(Shared.strings)

    private fun client(agent: TestAgent) = AgentClient(agent.pairing(), { token }, systemTrust = TestAgent.trustingNothing())

    private fun mod(filename: String = "a.jar", enabled: Boolean = true, status: String = "ok", vararg issues: ModIssue) =
        Mod(filename = filename, modId = "a", name = "Alpha", version = "1.0", enabled = enabled, status = status, issues = issues.toList())

    // ------------------------------------------------------------ the screen's words
    @Test
    fun `the screen is named for what the server type takes`() {
        val row = ServerRow(id = "s", name = "S")
        assertNull(Content.kind(null))
        assertEquals(ContentKind.MODS, Content.kind(row.copy(content = "mods")))
        assertEquals(ContentKind.PLUGINS, Content.kind(row.copy(content = "plugins")))
        assertEquals(ContentKind.PACKS, Content.kind(row.copy(edition = "bedrock", capabilities = Capabilities(addons = true))))
        assertEquals(ContentKind.NONE, Content.kind(row))
        assertEquals("Mods", strings.t(ContentKind.MODS.titleKey))
        assertEquals("Plugins", strings.t(ContentKind.PLUGINS.titleKey))
        assertEquals("Packs", strings.t(ContentKind.PACKS.titleKey))
    }

    @Test
    fun `each mod says whether it works and why not`() {
        val missing = ModIssue("missing_dependency", "error", "Alpha needs Fabric API, which isn't installed.")
        val version = ModIssue("version_mismatch", "warn", "Alpha is made for Minecraft 1.20.")
        val note = ModIssue("unverified", "info", "Checked from the file only.")
        val broken = mod(status = "error", issues = arrayOf(version, note, missing))
        assertEquals("mods.status_error", Content.status(broken).key)
        assertEquals(Tone.DANGER, Content.status(broken).tone)
        assertEquals(listOf(missing.detail, version.detail), Content.problems(broken))
        assertEquals("mods.status_off", Content.status(mod(enabled = false, status = "disabled")).key)
        assertEquals("value.unknown", Content.status(mod(status = "something new")).key)
    }

    @Test
    fun `a newer version and technical details are shown in the shared words`() {
        val newer = mod().copy(updateAvailable = ModUpdate(latestVersion = "2.0"), minecraftRange = ">=1.21")
        assertEquals("Update: 2.0", Content.newer(strings, newer))
        assertNull(Content.newer(strings, mod()))
        assertEquals("a · a.jar · For Minecraft >=1.21", Content.technical(strings, newer))
        assertEquals("1.0 → 2.0", Content.change(strings, "1.0", "2.0"))
        assertEquals("Unknown → 2.0", Content.change(strings, null, "2.0"))
    }

    // ------------------------------------------------------------ who may change what, and when
    @Test
    fun `only someone allowed sees change buttons, and they work only while stopped`() {
        assertEquals(Changes.NOT_ALLOWED, Content.changes(canManage = false, serverState = States.OFFLINE))
        assertEquals(Changes.READY, Content.changes(canManage = true, serverState = States.OFFLINE))
        assertEquals(Changes.READY, Content.changes(canManage = true, serverState = States.CRASHED))
        for (state in listOf(States.ONLINE, States.STARTING, States.STOPPING, States.RESTART_PENDING)) {
            assertEquals(state, Changes.STOP_FIRST, Content.changes(canManage = true, serverState = state))
        }
        // A state not known is never taken to be stopped.
        assertEquals(Changes.UNKNOWN, Content.changes(canManage = true, serverState = null))
        assertEquals(Changes.UNKNOWN, Content.changes(canManage = true, serverState = States.UNKNOWN))
        // Bedrock packs change while running too (a restart is offered after).
        assertEquals(Changes.READY, Content.packChanges(canManage = true))
        assertEquals(Changes.NOT_ALLOWED, Content.packChanges(canManage = false))
        // "Stop the server first" offers Stop only while it would work.
        assertTrue(Content.canStopHere(States.ONLINE, canControl = true))
        assertFalse(Content.canStopHere(States.ONLINE, canControl = false))
        assertFalse(Content.canStopHere(States.STOPPING, canControl = true))
    }

    @Test
    fun `only a kept version that is there and not in use can be rolled back to`() {
        val kept = ModVersion(version = "1.0", archivePath = "p", exists = true)
        assertTrue(Content.canRollBack(kept))
        assertFalse(Content.canRollBack(kept.copy(isCurrent = true)))
        assertFalse(Content.canRollBack(kept.copy(exists = false)))
    }

    @Test
    fun `history rows say what happened in words`() {
        val row = ModHistoryRow(ts = 1.0, user = "admin", action = "rollback", modName = "Alpha", result = "ok")
        assertEquals("Rolled back", Content.action(strings, row))
        assertEquals("something_new", Content.action(strings, row.copy(action = "something_new")))
        assertEquals(Tone.DANGER, Content.historyTone(row.copy(result = "failed")))
        assertEquals("Alpha, Beta", Content.historyName(strings, ModHistoryRow(action = "update_all", detail = "Alpha, Beta")))
        assertEquals("Update all", Content.action(strings, ModHistoryRow(action = "update_all")))
    }

    @Test
    fun `only the files each screen takes are sent`() {
        assertTrue(Content.accepts(ContentKind.MODS, "Sodium-0.6.JAR"))
        assertFalse(Content.accepts(ContentKind.MODS, "pack.mcpack"))
        assertTrue(Content.accepts(ContentKind.PACKS, "skins.mcaddon"))
        assertFalse(Content.accepts(ContentKind.PACKS, "mod.jar"))
        assertTrue(Content.accepts(ContentKind.MODS, "Pack 1.2.mrpack", modpack = true))
    }

    @Test
    fun `missing dependencies are the required mods that aren't there`() {
        val report = DependencyReport(
            items = listOf(
                DependencyItem("fabric-api", kind = "required", status = "missing"),
                DependencyItem("minecraft", kind = "required", platform = true, status = "platform_incompatible"),
                DependencyItem("modmenu", kind = "optional", status = "missing"),
                DependencyItem("cloth", kind = "required", status = "satisfied"),
            ),
        )
        assertEquals(listOf("fabric-api"), Content.missing(report).map { it.modId })
        assertEquals(emptyList<DependencyItem>(), Content.missing(null))
    }

    // ------------------------------------------------------------ Modrinth
    @Test
    fun `the install confirmation lists version, size, licence and sides`() {
        val project = ModProject(
            project = ModrinthProject(slug = "sodium", title = "Sodium", license = "LGPL-3.0", serverSide = "unsupported", clientSide = "required"),
            versions = listOf(
                ModrinthVersion(
                    versionId = "v1", versionNumber = "0.6.0", gameVersions = listOf("1.21.1"),
                    dependencies = listOf(ModrinthDependency("P1", "required"), ModrinthDependency("P2", "optional")),
                    file = ModrinthFile("sodium.jar", 2048),
                ),
            ),
        )
        val version = Install.latest(project)!!
        assertEquals(listOf("P1"), Install.required(version).map { it.projectId })
        assertEquals(
            listOf(
                "Sodium 0.6.0", "Size: 2.0 KB", "For Minecraft 1.21.1", "Licence: LGPL-3.0",
                "Needed on the server: No", "Needed in the game: Yes",
            ),
            Install.facts(strings, project, version),
        )
        assertEquals("1.2M downloads", Install.downloads(strings, 1_234_567))
        assertEquals("Unknown", Install.downloads(strings, null))
        assertEquals("https://modrinth.com/mod/sodium", Install.page("https://modrinth.com/mod/sodium"))
        assertNull(Install.page("https://example.com/mod/sodium"))
    }

    @Test
    fun `the missing ones say what happened to each, in the agent's three answers`() {
        val results = listOf(
            DependencyResult("Fabric API", "installed"),
            DependencyResult("Cloth", "skipped", "Already installed"),
            DependencyResult("Iris", "failed", "Modrinth stopped answering."),
        )
        assertEquals(
            "Done: Fabric API (Worked), Cloth (Already there), Iris (Failed: Modrinth stopped answering.)",
            Install.missingDone(strings, results),
        )
        assertFalse(Install.allWorked(results))
        assertTrue(Install.allWorked(results.take(2)))
    }

    // ------------------------------------------------------------ Update all
    private val plan = UpdateAllPlan(
        updates = listOf(
            UpdateAllRow(modId = "a", name = "Alpha", filename = "a.jar", enabled = true, installedVersion = "1.0", latestVersion = "2.0", versionId = "va"),
            UpdateAllRow(modId = "b", name = "Beta", filename = "b.jar.disabled", enabled = false, installedVersion = "1.0", latestVersion = "1.1", versionId = "vb"),
            UpdateAllRow(modId = "c", name = "Gamma", filename = "c.jar", enabled = true, installedVersion = "3", latestVersion = "4", versionId = null),
        ),
    )

    @Test
    fun `update all ticks the ones that are on and leaves turned-off ones for the person`() {
        assertEquals(setOf("a.jar", "c.jar"), UpdateAll.ticked(plan))
        val body = UpdateAll.body(plan, setOf("c.jar", "b.jar.disabled")).toString()
        assertEquals(
            """{"mods":[{"filename":"b.jar.disabled","version_id":"vb"},{"filename":"c.jar","version_id":null}]}""",
            body,
        )
        assertTrue(UpdateAll.offered(ModList(mods = listOf(mod().copy(updateAvailable = ModUpdate("2.0"))))))
        assertFalse(UpdateAll.offered(ModList(mods = listOf(mod()))))
        assertFalse(UpdateAll.offered(null))
    }

    @Test
    fun `the still fit card reads the agent's answer and changes these as one update all`() {
        val list = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }.decodeFromString(
            ModList.serializer(),
            """{"mods": [], "server_state": "OFFLINE", "fit": {"about": "Create 6.0", "needs_change": [{"mod_id": "r", "name": "Rails", "filename": "r.jar", "why": "Rails needs Create below 6", "from": "1.0", "to": "2.0", "version_id": "R2"}], "no_fit": [{"mod_id": "g", "name": "Gamma", "filename": "g.jar", "why": "No version for Minecraft 1.21.4 yet"}], "cant_tell": []}}""",
        )
        val fit = list.fit!!
        assertEquals("1 mod needs a different version to go with Create 6.0.", StillFit.lead(strings, fit, plugins = false))
        assertEquals("Some plugins may not go with Create 6.0.", StillFit.lead(strings, fit.copy(needsChange = emptyList()), plugins = true))
        val plan = StillFit.plan(fit)
        assertEquals(setOf("r.jar"), UpdateAll.ticked(plan))
        assertEquals("""{"mods":[{"filename":"r.jar","version_id":"R2"}]}""", UpdateAll.body(plan, UpdateAll.ticked(plan)).toString())
        assertEquals("No version for Minecraft 1.21.4 yet", fit.noFit.single().why)
        assertNull(ModList().fit)
    }

    @Test
    fun `the same job card offers turn off on the pick's others, or on each one when there's no pick`() {
        val list = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }.decodeFromString(
            ModList.serializer(),
            """{"mods": [], "server_state": "OFFLINE", "overlaps": [
                {"kind": "same_mod", "why": "Sodium is here 2 times.", "keep": "new.jar", "say": "Keep 0.5.2, turn off 0.5.1",
                 "mods": [{"filename": "old.jar", "mod_id": "sodium", "name": "Sodium", "version": "0.5.1", "turn_off": true, "needed_by": ""},
                          {"filename": "new.jar", "mod_id": "sodium", "name": "Sodium", "version": "0.5.2", "turn_off": false, "needed_by": ""}]},
                {"kind": "same_job", "why": "They all draw a web map of the world.", "keep": "", "say": "These do the same job. Keep the one you use.",
                 "mods": [{"filename": "dynmap.jar", "name": "Dynmap", "version": "3.7", "turn_off": false, "needed_by": "Dynmap Addon"},
                          {"filename": "squaremap.jar", "name": "squaremap", "version": "1.2", "turn_off": false, "needed_by": ""}]}
            ]}""",
        )
        val (twice, maps) = list.overlaps
        assertEquals("Keep 0.5.2, turn off 0.5.1", twice.say)
        assertEquals(listOf("old.jar"), SameJob.turnOff(twice).map { it.filename })
        assertEquals(listOf("squaremap.jar"), SameJob.turnOff(maps).map { it.filename })
        assertEquals(emptyList<Overlap>(), ModList().overlaps)
    }

    @Test
    fun `server players and both say where the mark came from`() {
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
        val list = json.decodeFromString(
            ModList.serializer(),
            """{"mods": [{"filename": "a.jar", "side": "both", "side_from": "unknown", "loader": "forge"}, {"filename": "b.jar", "side": "server", "side_from": "modrinth"}, {"filename": "p.jar", "side": "server", "side_from": "file", "loader": "paper"}], "server_state": "OFFLINE", "player_only": [{"filename": "map.jar", "name": "Minimap", "kind": "mod", "size_bytes": 10, "changed": true}, {"filename": "f.zip", "missing": true, "site": "modrinth"}], "player_only_options": {"backup": true, "skip_listed": true}, "odd_setups": [{"filename": "a.jar", "text": "Alpha needs Beta."}]}""",
        )
        val (a, b, plugin) = list.mods
        assertEquals("For: Both (not sure, the mod doesn't say)", Sides.words(strings, a))
        assertEquals("For: Server (from Modrinth)", Sides.words(strings, b))
        assertEquals(null, Sides.words(strings, plugin))
        assertEquals(listOf("server", "players"), Sides.picks(a))
        assertEquals(listOf("players", "both"), Sides.picks(b))
        assertEquals(emptyList<String>(), Sides.picks(plugin))
        assertEquals("Minimap", list.playerOnly.first().name)
        assertEquals(true, list.playerOnly.first().changed)
        assertEquals(listOf(false, true), list.playerOnly.map { it.missing })
        assertEquals(PlayerOnlyOptions(backup = true, skipListed = true), list.playerOnlyOptions)
        assertEquals(PlayerOnlyOptions(), ModList().playerOnlyOptions)
        assertEquals("Alpha needs Beta.", list.oddSetups.single().text)
        assertEquals(true, Sides.accepts("Faithful.ZIP"))
        assertEquals(false, Sides.accepts("pack.mrpack"))
    }

    @Test
    fun `a linked modpack's card and modpack update's question`() {
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
        val list = json.decodeFromString(
            ModList.serializer(),
            """{"mods": [{"filename": "mine.jar", "added_by_you": true}], "server_state": "OFFLINE", "modpack": {"name": "Test Pack", "site": "Modrinth", "version": "1.2.0", "update": {"version_id": "v2", "version": "1.3.0"}, "added_by_you": 1}}""",
        )
        val pack = list.modpack!!
        assertEquals(
            listOf("Made from Test Pack 1.2.0 on Modrinth.", "Version 1.3.0 is out.", "1 mod was added by you. Modpack update keeps it."),
            LinkedPacks.lines(strings, pack),
        )
        assertEquals("A new version is out.", LinkedPacks.lines(strings, pack.copy(update = LinkedModpackUpdate("cf:1-2"), addedByYou = 0))[1])
        assertEquals(true, list.mods.single().addedByYou)
        val preview = json.decodeFromString(
            ModpackUpdatePreview.serializer(),
            """{"token": "t", "pack": {"name": "Test Pack", "type_name": "Fabric"}, "changes": {"to_version": "1.3.0", "added": ["ferrite.jar"], "removed": [], "newer": [{"name": "l2.jar", "from": "l.jar", "to": "l2.jar"}], "added_by_you": ["mine.jar"], "configs_changed_by_you": [], "version_change": true, "minecraft_version": "1.21.1", "problems": []}}""",
        )
        assertEquals(
            listOf("Newer: l.jar → l2.jar", "Added: ferrite.jar", "Kept, added by you: mine.jar"),
            LinkedPacks.changes(strings, preview.changes),
        )
        assertEquals("The server is set up as Fabric 1.21.1 first.", LinkedPacks.steps(strings, preview).last())
        assertNull(ModList().modpack)
    }

    @Test
    fun `update all says why it can't start and what it did`() {
        assertEquals(strings.t("mobile.content.stop_first"), UpdateAll.blocked(strings, plan.copy(serverRunning = true)))
        assertEquals("Wait for Backing up to finish first.", UpdateAll.blocked(strings, plan.copy(blockedBy = "Backing up")))
        assertNull(UpdateAll.blocked(strings, plan))
        val done = UpdateAllResult(updated = listOf(UpdateAllDone("Alpha", "1.0", "2.0")), safetyBackup = SafetyBackup(4, "before-update-all.zip"))
        assertEquals("Updated 1. The backup made first is before-update-all.zip.", UpdateAll.done(strings, done))
        val running = Job(id = "j", kind = "mods", state = "running", step = "Updating Alpha (1 of 2)", serverId = "s")
        assertEquals("Updating Alpha (1 of 2)", UpdateAll.progress(running, "s"))
        assertNull(UpdateAll.progress(running, "other"))
        assertNull(UpdateAll.progress(running.copy(state = "succeeded"), "s"))
    }

    // ------------------------------------------------------------ modpacks
    @Test
    fun `a modpack import lists its steps, with the version change only when needed`() {
        val inspected = ModpackInspected(
            token = "t",
            pack = Modpack(name = "Pack", minecraftVersion = "1.21.1", typeName = "Fabric", downloadCount = 3, canImport = true),
            into = ModpackInto(versionChange = true),
        )
        val steps = Modpacks.steps(strings, inspected)
        assertEquals(5, steps.size)
        assertEquals(strings.t("pack.step_version", "type" to "Fabric", "version" to "1.21.1"), steps[2])
        assertEquals(4, Modpacks.steps(strings, inspected.copy(into = ModpackInto(false))).size)
        assertEquals("pack.status_client", Modpacks.fileStatus(ModpackFile(status = "client_only")).key)
        assertEquals(Tone.DANGER, Modpacks.fileStatus(ModpackFile(status = "refused")).tone)
    }

    @Test
    fun `a found modpack says its site, loader, version, downloads and what it holds`() = runBlocking {
        val hit = ModrinthHit(
            projectId = "xyz", title = "Pack", downloads = 1234, sourceName = "Modrinth",
            loaders = listOf("Fabric"), minecraftVersion = "1.21.10", serverSide = "unsupported",
            disclosures = listOf("Shows ads"),
        )
        assertEquals(
            "Modrinth  ·  Fabric 1.21.10  ·  1,234 downloads  ·  Made for players' games, not servers  ·  Shows ads",
            Modpacks.about(strings, hit),
        )
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"hits": [{"project_id": "curseforge:9", "slug": "curseforge:9", "title": "Cozy", "source": "curseforge", "source_name": "CurseForge", "loaders": ["Forge"]}], "total": 1, "problems": ["Modrinth isn't available right now."]}"""))
            val found = c.searchModpacks("cozy pack")
            assertEquals("Modrinth isn't available right now.", found.problems.single())
            assertEquals("/api/modpacks/search?q=cozy%20pack&limit=20", agent.server.takeRequest().path)
            agent.server.enqueue(agent.json("""{"token": "t", "pack": {"name": "Cozy", "can_import": true}}"""))
            assertEquals("Cozy", c.fetchModpack(found.hits.single(), null).pack.name)
            val sent = agent.server.takeRequest()
            assertEquals("/api/modpacks/curseforge", sent.path)
            assertEquals("""{"project":"curseforge:9","server_id":""}""", sent.body.readUtf8())
        }
    }

    // ------------------------------------------------------------ game settings
    private fun setting(key: String, kind: String, value: kotlinx.serialization.json.JsonElement?, set: Boolean = value != null, default: String = "", readOnly: String? = null) =
        GameSetting(key = key, kind = kind, value = value, set = set, minecraftDefault = default, readOnly = readOnly)

    private val settings = GameSettings(
        exists = true,
        fields = listOf(
            setting("difficulty", "choice", null, default = "easy"),
            setting("max-players", "int", JsonPrimitive(20), default = "20"),
            setting("pvp", "bool", JsonPrimitive(true), default = "true"),
            setting("white-list", "bool", null, default = "false"),
            setting("motd", "text", JsonPrimitive("Hi"), default = "A Minecraft Server"),
            setting("level-seed", "seed", JsonPrimitive("123"), default = "", readOnly = "world_exists"),
            setting("server-port", "port", JsonPrimitive(25565), default = "25565"),
        ),
    )

    @Test
    fun `game settings are grouped as on the PC, with each field's hint`() {
        val groups = GameForm.groups(settings)
        assertEquals(listOf("game.group_play", "game.group_who", "game.group_world"), groups.map { it.titleKey })
        assertEquals(listOf("difficulty", "pvp"), groups[0].fields.map { it.key })
        assertEquals(listOf("motd", "max-players", "white-list"), groups[1].fields.map { it.key })
        val difficulty = settings.fields[0]
        assertEquals("easy", GameForm.initial(difficulty))
        assertEquals("Easy", GameForm.choiceLabel(strings, "easy"))
        assertEquals(
            strings.t("game.difficulty_hint") + " " + strings.t("game.not_set", "value" to "Easy"),
            GameForm.hint(strings, difficulty),
        )
        assertEquals("true", GameForm.initial(settings.fields[2]))
        assertEquals("false", GameForm.initial(settings.fields[3]))
        assertEquals("20", GameForm.initial(settings.fields[1]))
        // A value the file has but Minecraft won't take is shown as the file has it, never
        // replaced by the default, and isn't written back unless it is changed.
        val bad = setting("view-distance", "int", null, set = true, default = "10").copy(problem = "not a number", raw = "ten")
        assertEquals("ten", GameForm.initial(bad))
        assertTrue(GameForm.changed(GameSettings(fields = listOf(bad)), mapOf("view-distance" to "ten")).isEmpty())
        assertTrue(GameForm.fileProblem(strings, bad)!!.endsWith("not a number"))
        assertEquals("Allowlist", strings.t(GameForm.LABELS.getValue("allow-list").first))
    }

    @Test
    fun `the seed is locked once the world exists, and everything for someone who may not edit`() {
        val seed = settings.fields.first { it.key == "level-seed" }
        assertTrue(GameForm.locked(seed, canEdit = true))
        assertEquals(strings.t("game.seed_locked"), GameForm.hint(strings, seed))
        assertFalse(GameForm.locked(settings.fields[0], canEdit = true))
        assertTrue(GameForm.locked(settings.fields[0], canEdit = false))
    }

    @Test
    fun `save sends only what changed, typed as the agent takes it`() {
        val edits = mapOf(
            "difficulty" to "hard",
            "max-players" to "20",
            "pvp" to "false",
            "motd" to "Hi",
            "level-seed" to "999",
            "server-port" to "25570",
            "white-list" to "false",
        )
        assertEquals(
            """{"difficulty":"hard","pvp":false,"server-port":25570}""",
            GameForm.changed(settings, edits).toString(),
        )
        assertEquals("{}", GameForm.changed(settings, emptyMap()).toString())
        assertEquals("""{"max-players":"lots"}""", GameForm.changed(settings, mapOf("max-players" to "lots")).toString())
        assertEquals(strings.t("game.saved_running"), GameForm.saved(strings, GameSettingsSaved(keys = listOf("pvp"), running = true)))
        assertEquals(strings.t("settings.nothing_changed"), GameForm.saved(strings, GameSettingsSaved(nothingChanged = true)))
    }

    // ------------------------------------------------------------ icons
    @Test
    fun `icons come from Modrinth once, then from the phone's cache`() = runBlocking {
        val cdn = MockWebServer()
        cdn.enqueue(MockResponse().setHeader("Cache-Control", "max-age=86400").setBody(Buffer().write(byteArrayOf(1, 2, 3))))
        cdn.start()
        try {
            val dir = kotlin.io.path.createTempDirectory("icons").toFile()
            val icons = IconCache(dir, hosts = setOf(cdn.hostName), httpsOnly = false)
            val url = cdn.url("/data/abc/icon.png").toString()
            assertTrue(byteArrayOf(1, 2, 3).contentEquals(icons.load(url)))
            assertTrue(byteArrayOf(1, 2, 3).contentEquals(icons.load(url)))
            assertEquals(1, cdn.requestCount)
            // A fresh cache on the same folder reads the disk, not Modrinth.
            val again = IconCache(dir, hosts = setOf(cdn.hostName), httpsOnly = false)
            assertTrue(byteArrayOf(1, 2, 3).contentEquals(again.load(url)))
            assertEquals(1, cdn.requestCount)
        } finally {
            cdn.shutdown()
        }
    }

    @Test
    fun `an icon that redirects elsewhere or is too big is not loaded`() = runBlocking {
        val cdn = MockWebServer()
        cdn.start()
        try {
            val icons = IconCache(null, hosts = setOf(cdn.hostName), httpsOnly = false)
            cdn.enqueue(MockResponse().setResponseCode(302).setHeader("Location", "https://evil.example/icon.png"))
            assertNull(icons.load(cdn.url("/moved.png").toString()))
            cdn.enqueue(MockResponse().setChunkedBody(Buffer().write(ByteArray(IconCache.MAX_ICON_BYTES + 1)), 64 * 1024))
            assertNull(icons.load(cdn.url("/huge.png").toString()))
            assertEquals(2, cdn.requestCount)
        } finally {
            cdn.shutdown()
        }
    }

    @Test
    fun `an icon from anywhere but Modrinth's own address is never loaded`() = runBlocking {
        val icons = IconCache(null)
        assertTrue(icons.allowed("https://cdn.modrinth.com/data/abc/icon.png"))
        assertFalse(icons.allowed("http://cdn.modrinth.com/data/abc/icon.png"))
        assertFalse(icons.allowed("https://evil.example/icon.png"))
        assertNull(icons.load("https://evil.example/icon.png"))
        assertNull(icons.load(null))
    }

    // ------------------------------------------------------------ the agent
    @Test
    fun `the installed list, update all and history are read`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"mods": [{"filename": "a.jar", "path": "C:\\s\\mods\\a.jar", "mod_id": "a", "name": "Alpha", "version": "1.0", "enabled": true, "status": "warn", "issues": [{"kind": "version_mismatch", "severity": "warn", "detail": "Made for 1.20"}], "has_history": true, "update_available": {"mod_id": "a", "latest_version": "2.0", "version_id": "va"}}], "problems": [], "minecraft_version": null, "server_state": "OFFLINE", "updates_checked_at": null}"""))
            val list = c.mods("s")
            assertEquals("2.0", list.mods.single().updateAvailable?.latestVersion)
            assertNull(list.minecraftVersion)
            assertEquals("/api/servers/s/mods", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"updates": [{"mod_id": "b", "name": "Beta", "filename": "b.jar.disabled", "enabled": false, "installed_version": "1.0", "latest_version": "1.1", "version_id": "vb", "project": "beta"}], "unchecked": [{"mod_id": "c", "name": "Gamma", "filename": "c.jar", "reason": "Modrinth didn't answer."}], "server_running": false, "blocked_by": null, "checked_at": 5.0}"""))
            val plan = c.updateAllPlan("s")
            assertFalse(plan.updates.single().enabled)
            assertEquals("Modrinth didn't answer.", plan.unchecked.single().reason)
            agent.server.takeRequest()

            agent.server.enqueue(agent.json("""{"ok": true, "job_id": "j", "updated": [{"mod_id": "b", "name": "Beta", "filename": "b-1.1.jar.disabled", "previous_version": "1.0", "version": "1.1"}], "safety_backup": {"id": 3, "name": "before-update-all.zip"}, "undo": {"kind": "restore_backup", "backup_id": 3, "backup": "before-update-all.zip"}}"""))
            val done = c.updateAll("s", UpdateAll.body(plan, setOf("b.jar.disabled")))
            assertEquals("1.1", done.updated.single().version)
            val sent = agent.server.takeRequest()
            assertEquals("POST", sent.method)
            assertEquals("/api/servers/s/mods/update-all", sent.path)
            assertEquals("""{"mods":[{"filename":"b.jar.disabled","version_id":"vb"}]}""", sent.body.readUtf8())

            agent.server.enqueue(agent.json("""{"history": [{"id": 9, "server_id": "s", "ts": 10.0, "user": "admin", "action": "update_all", "mod_id": null, "mod_name": null, "old_version": null, "new_version": null, "source": null, "sha256": null, "result": "failed", "detail": "Updating Beta failed"}]}"""))
            assertEquals("failed", c.modHistory("s").history.single().result)
            assertEquals("/api/servers/s/mods/history?limit=100", agent.server.takeRequest().path)
        }
    }

    @Test
    fun `turning on, removing, rolling back and installing send what the desktop app sends`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"ok": true, "mod": {"filename": "a.jar.disabled", "mod_id": "a", "name": "Alpha", "version": "1.0", "enabled": false}, "changed": true}"""))
            assertFalse(c.setModEnabled("s", "a.jar", enabled = false).mod!!.enabled)
            val off = agent.server.takeRequest()
            assertEquals("/api/servers/s/mods/disable", off.path)
            assertEquals("""{"filename":"a.jar"}""", off.body.readUtf8())

            agent.server.enqueue(agent.json("""{"mod": {}, "dependents": [{"name": "Beta", "mod_id": "b", "filename": "b.jar", "requires": ">=1.0"}], "warning": "Beta needs it."}"""))
            assertEquals("Beta", c.modImpact("s", "my mod.jar").dependents.single().name)
            assertEquals("/api/servers/s/mods/impact?filename=my%20mod.jar", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"versions": [{"id": 1, "version": "1.0", "filename": "a.jar", "archive_path": "C:\\kept\\a.jar", "created_at": 3.0, "is_current": false, "exists": true}]}"""))
            val kept = c.modVersions("s", "a").versions.single()
            agent.server.takeRequest()
            agent.server.enqueue(agent.json("""{"ok": true, "restored": {"filename": "a.jar", "name": "Alpha", "version": "1.0"}, "previous_version": "2.0"}"""))
            assertEquals("2.0", c.rollBackMod("s", "a", kept.archivePath).previousVersion)
            assertTrue(agent.server.takeRequest().body.readUtf8().contains("\"archive_path\":\"C:\\\\kept\\\\a.jar\""))

            agent.server.enqueue(agent.json("""{"hits": [{"project_id": "P", "slug": "sodium", "title": "Sodium", "description": "Fast", "downloads": 5, "icon_url": "https://cdn.modrinth.com/i.png", "page": "https://modrinth.com/mod/sodium"}], "total": 1, "offset": 0}"""))
            assertEquals("https://cdn.modrinth.com/i.png", c.searchMods("s", "so dium").hits.single().iconUrl)
            assertEquals("/api/servers/s/mods/search?q=so%20dium&limit=20&offset=0", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"ok": true, "installed": {"filename": "sodium.jar", "name": "Sodium", "version": "0.6"}, "dependencies_required": [{"mod_id": "fabric-api", "title": "Fabric API", "installed": false}], "dependencies_installed": []}"""))
            assertEquals("Fabric API", c.installMod("s", "sodium", "v1", withDependencies = false).dependenciesRequired.single().title)
            assertEquals("""{"project":"sodium","version_id":"v1","install_dependencies":false}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json("""{"items": [], "unresolvable": [{"mod_id": "x", "name": "X", "required_by": ["Alpha"], "reason": "Not found on Modrinth. Install it manually.", "page": null}], "skipped": [], "minecraft_version": "1.21.1"}"""))
            assertEquals("X", c.planDependencies("s").unresolvable.single().name)
            assertEquals("/api/servers/s/mods/dependencies/plan", agent.server.takeRequest().path)
        }
    }

    @Test
    fun `a file picked on the phone is sent as an upload form`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"ok": true, "installed": {"filename": "e.jar", "name": "Epsilon", "version": "3.0"}}"""))
            assertEquals("Epsilon", c.uploadMod("s", PickedFile.of("e.jar", byteArrayOf(80, 75, 3, 4))).installed?.name)
            val sent = agent.server.takeRequest()
            assertEquals("/api/servers/s/mods/upload?confirm=true", sent.path)
            assertTrue(sent.getHeader("Content-Type")!!.startsWith("multipart/form-data"))
            val body = sent.body.readUtf8()
            assertTrue(body, body.contains("name=\"file\"; filename=\"e.jar\""))

            agent.server.enqueue(agent.json("""{"token": "tok", "pack": {"name": "Pack", "version": "1.2", "summary": null, "minecraft_version": "1.21.1", "type": "fabric", "type_name": "Fabric", "loader_version": "0.16.5", "files": [{"name": "lithium.jar", "path": "mods/lithium.jar", "size": 10, "status": "download", "reason": null, "env_server": "required"}], "download_count": 1, "download_bytes": 10, "client_only": [], "refused": [], "overrides": 0, "overrides_bytes": 0, "skipped_overrides": [], "problems": [], "can_import": true}, "into": {"version_change": false, "running": false}}"""))
            val pack = c.inspectModpack("s", PickedFile.of("p.mrpack", byteArrayOf(1)))
            assertEquals("tok", pack.token)
            assertEquals("/api/modpacks/inspect?server_id=s", agent.server.takeRequest().path)
            agent.server.enqueue(agent.json("""{"ok": true, "job_id": "j", "modpack": {"name": "Pack"}, "version_change": false, "undo": null}"""))
            assertEquals("Pack", c.importModpack("s", "tok").modpack.name)
            assertEquals("""{"token":"tok","confirm":true,"own_jars":false}""", agent.server.takeRequest().body.readUtf8())
            agent.server.enqueue(agent.json("""{"ok": true, "job_id": "j", "modpack": {"name": "Pack"}, "version_change": false, "undo": null}"""))
            c.importModpack("s", "tok", ownJars = true)
            assertEquals("""{"token":"tok","confirm":true,"own_jars":true}""", agent.server.takeRequest().body.readUtf8())
        }
    }

    @Test
    fun `another owner's modpack lists its own jars and the plugins to get`() {
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
        val pack = json.decodeFromString(
            Modpack.serializer(),
            """{"name": "P", "own_jars": ["mods/a.jar"], "plugins_to_get": ["EssentialsX"], "can_import": true}""",
        )
        val notes = Modpacks.notes(strings, pack)
        assertEquals("Get these plugins yourself: EssentialsX", notes[0])
        assertTrue(notes[1], notes[1].startsWith("Not checked by a site: mods/a.jar."))
        assertEquals(emptyList<String>(), Modpacks.notes(strings, Modpack()))
        val changes = json.decodeFromString(ModpackChanges.serializer(), """{"new_own_jars": ["mods/a.jar"]}""")
        assertTrue(LinkedPacks.changes(strings, changes).single().startsWith("Not checked by a site"))
    }

    @Test
    fun `make a modpack sends the answers, then downloads it and posts it in discord`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"name": "Friends", "version": "2", "configs": [{"path": "config/a.toml", "size_bytes": 9}], "players_loaders": [{"id": "fabric", "name": "Fabric"}], "discord": true, "discord_limit_bytes": 10485760, "required_to_join": {"available": false, "needs_fabric": true}}"""))
            val options = c.makeModpackOptions("s")
            assertEquals("config/a.toml", options.configs.single().path)
            assertEquals(false, options.requiredToJoin.available)
            assertEquals(true, options.requiredToJoin.needsFabric)
            assertEquals(true, options.chatLine.on)
            assertEquals("/api/servers/s/modpack/make", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"ok": true, "token": "t1", "filename": "Friends 2.mrpack", "files": 3, "size_bytes": 2048, "own_files": ["overrides/mods/x.jar"], "trust_note": "Only share it with people you trust.", "get_yourself": [{"name": "Secret Mod", "page": null}], "plugins_to_get": [], "left_out": ["Ledger"], "discord": true}"""))
            val made = c.makeModpack("s", MakeModpack("Friends", "2", configs = listOf("config/a.toml")))
            val sent = agent.server.takeRequest()
            assertEquals("POST", sent.method)
            assertEquals(
                """{"name":"Friends","version":"2","leave_out_server_only":true,"configs":["config/a.toml"],"players_loader":"fabric","required":false}""",
                sent.body.readUtf8(),
            )
            val lines = MadeModpacks.lines(strings, made)
            assertTrue(lines[0], lines[0].startsWith("Friends 2.mrpack is ready"))
            assertTrue(lines[1], lines[1].contains("people you trust"))
            assertTrue(lines.any { it.endsWith("Secret Mod") })
            assertTrue(lines.any { it.endsWith("Ledger") })

            agent.server.enqueue(MockResponse().setHeader("Content-Type", "application/zip").setBody("PK modpack"))
            val out = java.io.ByteArrayOutputStream()
            c.downloadModpack("s", made, out)
            assertEquals("/api/servers/s/modpack/made/t1", agent.server.takeRequest().path)
            assertEquals("PK modpack", out.toString())

            agent.server.enqueue(agent.json("""{"ok": true, "posted": 1}"""))
            assertEquals(1, c.postModpack("s", "t1").posted)
            val posted = agent.server.takeRequest()
            assertEquals("/api/servers/s/modpack/made/discord", posted.path)
            assertEquals("""{"token":"t1"}""", posted.body.readUtf8())
        }
    }

    @Test
    fun `a required modpack and its chat line are sent when the companion is on the server`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"name": "Friends", "version": "2", "configs": [], "players_loaders": [], "discord": false, "discord_limit_bytes": 1, "required_to_join": {"available": true, "on": true}, "chat_line": {"on": false, "text": "Get {name}"}}"""))
            val options = c.makeModpackOptions("s")
            assertEquals(RequiredToJoin(available = true, on = true), options.requiredToJoin)
            assertEquals(ChatLine(on = false, text = "Get {name}"), options.chatLine)
            agent.server.takeRequest()

            agent.server.enqueue(agent.json("""{"ok": true, "token": "t1", "filename": "Friends 2.mrpack", "files": 1, "size_bytes": 1}"""))
            c.makeModpack("s", MakeModpack("Friends", "2", required = true, chatLine = ChatLine(false, "Get {name}")))
            val sent = agent.server.takeRequest().body.readUtf8()
            assertTrue(sent, sent.endsWith(""""required":true,"chat_line":{"on":false,"text":"Get {name}"}}"""))
        }
    }

    @Test
    fun `the title screen is read, sent with the modpack and its picture uploaded`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            val options = """{"name": "Friends", "version": "2", "required_to_join": {"available": true, "on": false}, "title_screen": {"button": {"on": true, "address": "play.example.net"}, "flavor_text": {"on": false, "text": ""}, "background": {"on": false}, "background_set": true}}"""
            agent.server.enqueue(agent.json(options))
            val got = c.makeModpackOptions("s").titleScreen
            assertEquals(TitleButton(true, "play.example.net"), got.button)
            assertTrue(got.backgroundSet)
            agent.server.takeRequest()

            agent.server.enqueue(agent.json(options))
            c.uploadTitleBackground("s", PickedFile("sky.png", 2) { "PN".byteInputStream() })
            assertEquals("/api/servers/s/modpack/title-background", agent.server.takeRequest().path)

            agent.server.enqueue(agent.json("""{"ok": true, "token": "t1", "filename": "Friends 2.mrpack", "files": 1, "size_bytes": 1}"""))
            val screen = TitleScreen(TitleButton(true, "play.example.net"), FlavorText(true, "Dragons!"), OnOff(true))
            c.makeModpack("s", MakeModpack("Friends", "2", titleScreen = screen))
            val sent = agent.server.takeRequest().body.readUtf8()
            assertTrue(sent, sent.endsWith(""""title_screen":{"button":{"on":true,"address":"play.example.net"},"flavor_text":{"on":true,"text":"Dragons!"},"background":{"on":true}}}"""))
        }
    }

    @Test
    fun `the download link turns on and the resource pack is sent, set by link and removed`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"on": false, "why_not": null, "listening": false, "modpack_url": null, "speed_cap_bytes": 4194304, "resource_pack": null, "max_pack_bytes": 262144000}"""))
            val off = c.downloadLink("s")
            assertEquals("/api/servers/s/download-link", agent.server.takeRequest().path)
            assertEquals(listOf("No resource pack."), DownloadLinks.packLines(strings, off))
            assertTrue(DownloadLinks.lines(strings, off, owner = false).single().contains("owner"))

            val on = """{"on": true, "listening": true, "modpack_url": "http://100.64.0.1:25590/abc/modpack.mrpack", "speed_cap_bytes": 4194304, "resource_pack": {"source": "file", "filename": "Textures.zip", "url": "http://100.64.0.1:25590/abc/resource-pack.zip", "sha1": "aa", "size_bytes": 2048, "required": true, "served": true}, "max_pack_bytes": 262144000, "restart_needed": true}"""
            agent.server.enqueue(agent.json(on))
            val link = c.setDownloadLink("s", true)
            val switched = agent.server.takeRequest()
            assertEquals("POST", switched.method)
            assertEquals("""{"on":true}""", switched.body.readUtf8())
            val lines = DownloadLinks.lines(strings, link, owner = true)
            assertTrue(lines[0], lines[0].endsWith("/abc/modpack.mrpack"))
            val pack = DownloadLinks.packLines(strings, link)
            assertTrue(pack[0], pack[0].startsWith("Textures.zip") && pack[0].contains("served by the download link"))
            assertTrue(pack.last().startsWith("Restart the server"))

            agent.server.enqueue(agent.json(on))
            c.uploadResourcePack("s", PickedFile("Textures.zip", 2) { "PK".byteInputStream() }, required = true)
            val sent = agent.server.takeRequest()
            assertEquals("/api/servers/s/resource-pack/upload?required=true", sent.path)
            assertTrue(sent.body.readUtf8().contains("Textures.zip"))

            agent.server.enqueue(agent.json(on))
            c.setResourcePackLink("s", "https://packs.example.com/t.zip", "", false)
            assertEquals("""{"url":"https://packs.example.com/t.zip","sha1":"","required":false}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json(on))
            c.setResourcePackRequired("s", false)
            assertEquals("""{"required":false}""", agent.server.takeRequest().body.readUtf8())

            agent.server.enqueue(agent.json("""{"on": true, "listening": true, "speed_cap_bytes": 1, "max_pack_bytes": 1}"""))
            assertEquals(null, c.removeResourcePack("s").resourcePack)
            val removed = agent.server.takeRequest()
            assertEquals("DELETE", removed.method)
            assertEquals("/api/servers/s/resource-pack", removed.path)

            agent.server.enqueue(agent.json(on))
            c.newDownloadLink("s")
            assertEquals("/api/servers/s/download-link/new", agent.server.takeRequest().path)
        }
    }

    @Test
    fun `sign in with Modrinth comes back as mcsc link and the modpack goes up`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"url": "https://modrinth.com/auth/authorize?state=s1"}"""))
            assertEquals("https://modrinth.com/auth/authorize?state=s1", c.modrinthSignIn().url)
            val started = agent.server.takeRequest()
            assertEquals("/api/modrinth/sign-in", started.path)
            assertEquals("""{"return_to":"phone"}""", started.body.readUtf8())

            assertEquals("abc" to "x.1.phone.y", ModrinthCards.cameBack("mcsc://modrinth?state=x.1.phone.y&code=abc"))
            assertEquals(null, ModrinthCards.cameBack("mcsc://modrinth?state=x.1.phone.y&error=access_denied"))
            assertEquals(null, ModrinthCards.cameBack("mcsc://server/abc?code=1&state=2"))
            assertEquals(null, ModrinthCards.cameBack("https://modrinth?code=1&state=2"))

            agent.server.enqueue(agent.json("""{"available": true, "signed_in": true, "username": "mark", "ran_out": false}"""))
            assertTrue(c.modrinthFinish("abc", "x.1.phone.y").signedIn)
            assertEquals("""{"code":"abc","state":"x.1.phone.y"}""", agent.server.takeRequest().body.readUtf8())

            val card = """{"account": {"available": true, "signed_in": true, "username": "mark", "ran_out": false}, "made": {"name": "Friends", "version": "2"}, "project": {"id": "p1", "slug": "friends", "title": "Friends", "url": "https://modrinth.com/modpack/friends"}, "uploads": [{"version_id": "v2", "version": "2", "at": 1.0}], "own_jars": [{"path": "mods/a.jar", "name": "A", "side": "both", "slug": "a", "url": "https://modrinth.com/mod/a"}], "server_project": {"id": "s", "slug": "s", "url": "https://modrinth.com/server/s"}, "older": {"listed": "1", "newest": "2"}, "address": "play.example.org"}"""
            agent.server.enqueue(agent.json(card))
            val share = c.modrinthUpload("s", "friends", "Our pack")
            assertEquals("/api/servers/s/modrinth/upload", agent.server.takeRequest().path)
            val lines = ModrinthCards.lines(strings, share, owner = true)
            assertEquals("Signed in to Modrinth as mark.", lines[0])
            assertTrue(lines[1], lines[1].contains("https://modrinth.com/modpack/friends"))
            assertTrue(lines.last().contains("https://modrinth.com/mod/a"))
            val server = ModrinthCards.serverLines(strings, share)
            assertTrue(server[1], server[1].contains("version 1"))
            assertTrue(ModrinthCards.steps(strings, share).contains("play.example.org"))
            assertEquals("our-friends-pack", ModrinthCards.slug("Our Friends' Pack!"))

            agent.server.enqueue(agent.json(card))
            c.modrinthOwnJar("s", "mods/a.jar", "a", "A mod")
            assertEquals("""{"path":"mods/a.jar","slug":"a","summary":"A mod"}""", agent.server.takeRequest().body.readUtf8())
            agent.server.enqueue(agent.json(card))
            c.modrinthServerProject("s", "")
            assertEquals("/api/servers/s/modrinth/server-project", agent.server.takeRequest().path)
        }
    }

    @Test
    fun `far view lists what each choice adds and says why one can't`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"supported": true, "choice": "off", "choices": [{"id": "distanthorizons", "name": "Distant Horizons", "available": true, "server": ["Distant Horizons"], "players": []}, {"id": "voxy", "name": "Voxy", "available": false, "why": "Voxy has nothing for NeoForge on Minecraft 1.21.4 yet.", "server": [], "players": []}], "unvisited": false, "load_ahead": {"on": false, "radius": 2000, "world": "", "chunky": false, "running": false, "night": "1:00-6:00", "main_world": "world"}, "may_change": true}"""))
            val card = c.farView("s")
            assertEquals("/api/servers/s/far-view", agent.server.takeRequest().path)
            assertEquals("Far view is off.", FarViews.status(strings, card))
            assertEquals("The server gets: Distant Horizons", FarViews.lines(strings, card.choices[0], card.choice)[1])
            assertEquals("Voxy has nothing for NeoForge on Minecraft 1.21.4 yet.", FarViews.lines(strings, card.choices[1], card.choice)[1])
            assertEquals("1:00-6:00", card.loadAhead.night)
            agent.server.enqueue(agent.json("""{"ok": true, "choice": "voxy", "removed": [], "added": ["voxy.jar"]}"""))
            assertEquals(listOf("voxy.jar"), c.chooseFarView("s", "voxy").added)
            val sent = agent.server.takeRequest()
            assertEquals("/api/servers/s/far-view", sent.path)
            assertEquals("""{"choice":"voxy"}""", sent.body.readUtf8())
        }
    }

    @Test
    fun `the anti-cheat picker never shows a guessed price and warns about two`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"supported": true, "installed": ["Grim", "Vulcan"], "free": [{"id": "grim", "name": "Grim", "project": "grimac", "on": true, "warning": true}], "paid": [{"id": "vulcan", "name": "Vulcan", "on": true, "link": "https://www.spigotmc.org/resources/83626/", "setup": null, "price": null}, {"id": "polar", "name": "Polar", "on": false, "link": "https://polar.top", "setup": "https://docs.polar.top/getting-started.html", "price": "19.99 USD", "checked": "14 November 2026"}], "may_install": true, "may_add_own_jar": false}"""))
            val picker = c.antiCheat("s")
            assertEquals("/api/servers/s/anticheat", agent.server.takeRequest().path)
            assertTrue(AntiCheats.status(strings, picker).startsWith("Two anti-cheats are in: Grim, Vulcan."))
            val grim = AntiCheats.free(strings, picker.free[0])
            assertTrue(grim.any { it.contains("Try it with a friend first") })
            assertEquals("Price: see the seller's page.", AntiCheats.paid(strings, picker.paid[0])[1])
            assertEquals("Price: 19.99 USD (checked 14 November 2026).", AntiCheats.paid(strings, picker.paid[1])[1])
            assertEquals("No anti-cheat runs on this server type.", AntiCheats.status(strings, AntiCheat()))
        }
    }

    @Test
    fun `bedrock packs and game settings are read and saved`() = runBlocking {
        TestAgent().use { agent ->
            val c = client(agent)
            agent.server.enqueue(agent.json("""{"addons": [{"uuid": "u1", "version": "1.0.0", "name": "Skins", "description": null, "kind": "resource", "folder": "resource_packs/skins", "enabled": true}], "world": "Bedrock level", "running": true, "restart_needed": true}"""))
            val packs = c.addons("s")
            assertEquals("Bedrock level", packs.world)
            assertEquals("addons.kind_resource", Content.packKind(packs.addons.single()))
            agent.server.takeRequest()
            agent.server.enqueue(agent.json("""{"ok": true, "installed": [], "restart_needed": true}"""))
            assertEquals(true, c.setAddonEnabled("s", "u1", enabled = false).restartNeeded)
            val toggled = agent.server.takeRequest()
            assertEquals("PUT", toggled.method)
            assertEquals("""{"enabled":false}""", toggled.body.readUtf8())

            agent.server.enqueue(agent.json("""{"file": "C:\\s\\server.properties", "exists": true, "modified_at": 1.0, "world_exists": true, "world_folder": "world", "fields": [{"key": "max-players", "kind": "int", "value": 20, "raw": "20", "set": true, "problem": null, "minecraft_default": "20", "choices": [], "min": 1, "max": 10000, "max_length": 200, "read_only": null}], "text": "", "running": false, "restart_needed": false}"""))
            assertEquals("20", GameForm.initial(c.gameSettings("s").fields.single()))
            agent.server.takeRequest()
            agent.server.enqueue(agent.json("""{"detail": "Some values can't be used.", "problems": {"server-port": "Another program is using port 80."}}""", code = 400))
            val e = assertThrows(AgentException.Failed::class.java) {
                runBlocking { c.saveGameSettings("s", buildJsonObject { put("server-port", 80) }) }
            }
            assertEquals("Another program is using port 80.", e.problems["server-port"])
            val saved = agent.server.takeRequest()
            assertEquals("PUT", saved.method)
            assertEquals("""{"values":{"server-port":80}}""", saved.body.readUtf8())
        }
    }
}
