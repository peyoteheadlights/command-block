package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Plan, Install and Swap together: the 5 rules, on real files. */
class UpdateTest {
    @TempDir
    Path game;

    static final byte[] SODIUM = "sodium v2".getBytes(StandardCharsets.UTF_8);
    static final byte[] OWN = "own tweaks v2".getBytes(StandardCharsets.UTF_8);

    static byte[] hash(String alg, byte[] b) throws Exception {
        return MessageDigest.getInstance(alg).digest(b);
    }

    Path mods() throws IOException {
        return Files.createDirectories(game.resolve("mods"));
    }

    /** A modpack: sodium from a download link, an own jar, the players file listing both. */
    Path pack(String sodiumUrl, Map<String, byte[]> extra) throws Exception {
        Path file = game.resolve("pack.mrpack");
        String index = """
                {"formatVersion": 1, "files": [
                 {"path": "mods/sodium.jar", "hashes": {"sha1": "x", "sha512": "%s"},
                  "env": {"client": "required", "server": "required"}, "downloads": ["%s"], "fileSize": 9},
                 {"path": "mods/serveronly.jar", "hashes": {"sha512": "%s"},
                  "env": {"client": "unsupported", "server": "required"}, "downloads": ["https://cdn.modrinth.com/x"]},
                 {"path": "shaderpacks/pretty.zip", "hashes": {"sha512": "%s"}, "downloads": ["https://cdn.modrinth.com/y"]}
                ]}""".formatted(HexFormat.of().formatHex(hash("SHA-512", SODIUM)), sodiumUrl, "ab".repeat(64), "ab".repeat(64));
        String players = """
                {"format": 1, "modpack": {"name": "Friends", "version": "2"}, "files": ["mods/sodium.jar", "mods/own.jar"]}""";
        try (ZipOutputStream z = new ZipOutputStream(Files.newOutputStream(file))) {
            put(z, "modrinth.index.json", index.getBytes(StandardCharsets.UTF_8));
            put(z, Plan.PLAYERS_FILE, players.getBytes(StandardCharsets.UTF_8));
            put(z, "client-overrides/mods/own.jar", OWN);
            for (Map.Entry<String, byte[]> e : extra.entrySet()) {
                put(z, e.getKey(), e.getValue());
            }
        }
        return file;
    }

    static void put(ZipOutputStream z, String name, byte[] data) throws IOException {
        z.putNextEntry(new ZipEntry(name));
        z.write(data);
        z.closeEntry();
    }

    static List<byte[]> serverList() throws Exception {
        return List.of(hash("SHA-256", SODIUM), hash("SHA-256", OWN));
    }

    static Map<String, Plan.Item> byName(Plan plan) {
        Map<String, Plan.Item> by = new HashMap<>();
        plan.items().forEach(i -> by.put(i.file() + " " + i.change(), i));
        return by;
    }

    @Test
    void theListAddsUpdatesAndRemovesOnlyModpackFiles() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("sodium.jar"), "sodium v1"); // the old modpack's
        Files.writeString(mods.resolve("oldlib.jar"), "dropped from the modpack");
        Files.writeString(mods.resolve("minimap.jar"), "the player's own"); // never touched
        Plan plan = Plan.read(pack("https://cdn.modrinth.com/data/s/sodium.jar", Map.of()), mods, serverList(),
                List.of("mods/sodium.jar", "mods/oldlib.jar"));

        assertEquals("Friends", plan.name());
        assertEquals("2", plan.version());
        Map<String, Plan.Item> by = byName(plan);
        assertEquals(Plan.Source.MODRINTH, by.get("sodium.jar ADD").source());
        assertTrue(by.get("sodium.jar ADD").ticked());
        assertEquals(Plan.Source.OWN, by.get("own.jar ADD").source());
        assertFalse(by.get("own.jar ADD").ticked(), "own jars start unticked");
        assertTrue(by.containsKey("oldlib.jar REMOVE"));
        assertEquals(3, plan.items().size(), "nothing about the minimap, server-only mods or shaders");
    }

    @Test
    void filesAlreadyInPlaceAreLeftOut() throws Exception {
        Path mods = mods();
        Files.write(mods.resolve("sodium.jar"), SODIUM);
        Files.write(mods.resolve("own.jar"), OWN);
        Plan plan = Plan.read(pack("https://cdn.modrinth.com/s.jar", Map.of()), mods, serverList(), List.of());
        assertEquals(List.of(), plan.items());
    }

    @Test
    void onlyModrinthAndCurseForgeDownloadServersCount() throws Exception {
        for (String url : List.of("http://cdn.modrinth.com/s.jar", "https://evil.example/s.jar",
                "https://cdn.modrinth.com.evil.example/s.jar", "https://cdn.modrinth.com:8443/s.jar")) {
            Plan plan = Plan.read(pack(url, Map.of()), mods(), serverList(), List.of());
            assertFalse(byName(plan).containsKey("sodium.jar ADD"), url);
            assertTrue(plan.skipped().get(0).startsWith("sodium.jar"), url);
        }
        assertEquals(Plan.Source.CURSEFORGE, Plan.site("https://edge.forgecdn.net/files/1/2/x.jar"));
    }

    @Test
    void anOwnJarNotOnTheServersListIsNeverOffered() throws Exception {
        Plan plan = Plan.read(pack("https://cdn.modrinth.com/s.jar", Map.of("overrides/mods/sneaky.jar", new byte[] {1})),
                mods(), serverList(), List.of());
        assertFalse(byName(plan).containsKey("sneaky.jar ADD"));
        assertTrue(plan.skipped().stream().anyMatch(s -> s.startsWith("sneaky.jar")));
    }

    @Test
    void pathsThatLeadOutOfTheModsFolderAreIgnored() throws Exception {
        Plan plan = Plan.read(pack("https://cdn.modrinth.com/s.jar",
                Map.of("overrides/mods/../../evil.jar", OWN, "overrides/mods/sub/evil.jar", OWN)), mods(), serverList(), List.of());
        assertTrue(plan.items().stream().noneMatch(i -> i.file().contains("evil")));
        assertThrows(BadInput.class, () -> Swap.inside(game, "..\\evil.jar"));
        assertThrows(BadInput.class, () -> Swap.inside(game, "../evil.jar"));
        assertThrows(BadInput.class, () -> Swap.inside(game, ".hidden.jar"));
    }

    @Test
    void aPlayersOwnFileWithAModpackNameIsLeftAlone() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("extra.jar"), "the player's own extra.jar");
        Plan plan = Plan.read(pack("https://cdn.modrinth.com/s.jar", Map.of("overrides/mods/extra.jar", OWN)), mods,
                serverList(), List.of());
        assertFalse(byName(plan).containsKey("extra.jar ADD"), "extra.jar isn't on the modpack's list");
        assertTrue(plan.skipped().stream().anyMatch(s -> s.startsWith("extra.jar")));
    }

    @Test
    void installWaitsThenSwapPutsItInAndGoBackUndoesIt() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("sodium.jar"), "sodium v1");
        Files.writeString(mods.resolve("oldlib.jar"), "old");
        Files.writeString(mods.resolve("minimap.jar"), "the player's own");
        Files.createDirectories(game.resolve("config"));
        Files.writeString(game.resolve(Swap.PLAYERS), "{\"old\": true}");

        Path mrpack = pack("https://cdn.modrinth.com/s.jar", Map.of());
        Plan plan = Plan.read(mrpack, mods, serverList(), List.of("mods/sodium.jar", "mods/oldlib.jar"));
        Map<String, Plan.Item> by = byName(plan);
        // The test can't reach Modrinth: sodium comes from a local stand-in, through the same download().
        Install.prepare(Install.client(), plan, List.of(by.get("own.jar ADD"), by.get("oldlib.jar REMOVE")), mrpack, game,
                (f, d, t) -> {});
        Path waiting = game.resolve(Install.FOLDER).resolve("waiting");
        HttpServer site = serve(SODIUM);
        try {
            Install.download(Install.client(), "http://127.0.0.1:" + site.getAddress().getPort() + "/s.jar",
                    waiting.resolve("sodium.jar"), 100, "SHA-512", hash("SHA-512", SODIUM));
        } finally {
            site.stop(0);
        }
        String planText = Files.readString(waiting.resolve(Swap.PLAN));
        Files.writeString(waiting.resolve(Swap.PLAN), planText.replace("\"add\":[\"own.jar\"]", "\"add\":[\"own.jar\",\"sodium.jar\"]"));
        assertEquals("sodium v1", Files.readString(mods.resolve("sodium.jar")), "nothing changes while the game runs");
        assertTrue(Swap.waiting(game));

        Swap.apply(game);
        assertEquals("sodium v2", Files.readString(mods.resolve("sodium.jar")));
        assertEquals("own tweaks v2", Files.readString(mods.resolve("own.jar")));
        assertFalse(Files.exists(mods.resolve("oldlib.jar")));
        assertEquals("the player's own", Files.readString(mods.resolve("minimap.jar")));
        assertEquals("2", Prover.packVersion(Files.readString(game.resolve(Swap.PLAYERS))));
        assertFalse(Swap.waiting(game));
        assertTrue(Swap.canGoBack(game));

        Swap.askGoBack(game);
        assertTrue(Swap.waiting(game));
        Swap.back(game);
        assertEquals("sodium v1", Files.readString(mods.resolve("sodium.jar")));
        assertEquals("old", Files.readString(mods.resolve("oldlib.jar")));
        assertFalse(Files.exists(mods.resolve("own.jar")));
        assertEquals("the player's own", Files.readString(mods.resolve("minimap.jar")));
        assertEquals("{\"old\": true}", Files.readString(game.resolve(Swap.PLAYERS)));
        assertFalse(Swap.canGoBack(game));
        assertFalse(Swap.waiting(game));
    }

    @Test
    void aFileThatDoesntMatchItsHashIsThrownAway() throws Exception {
        HttpServer site = serve("not sodium".getBytes(StandardCharsets.UTF_8));
        String url = "http://127.0.0.1:" + site.getAddress().getPort() + "/s.jar";
        try {
            Path target = game.resolve("x.jar");
            assertThrows(BadInput.class, () -> Install.download(Install.client(), url, target, 100, "SHA-512", hash("SHA-512", SODIUM)));
            assertFalse(Files.exists(target));
            assertFalse(Files.exists(game.resolve("x.jar.part")));
            assertThrows(BadInput.class, () -> Install.download(Install.client(), url, target, 3, null, null), "too big");
        } finally {
            site.stop(0);
        }
    }

    @Test
    void aSwapThatCantFinishUndoesEverything() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("a.jar"), "old a");
        Path waiting = Files.createDirectories(game.resolve(Install.FOLDER).resolve("waiting"));
        Files.writeString(waiting.resolve("a.jar"), "new a");
        // b.jar is on the list but never arrived: the swap fails and puts a.jar back.
        Files.writeString(waiting.resolve(Swap.PLAN), "{\"add\": [\"a.jar\", \"b.jar\"], \"remove\": [], \"players\": \"{}\"}");
        assertThrows(IOException.class, () -> Swap.apply(game));
        assertEquals("old a", Files.readString(mods.resolve("a.jar")));
        assertEquals("new a", Files.readString(waiting.resolve("a.jar")));
        assertFalse(Files.exists(game.resolve(Swap.PLAYERS)));
    }

    @Test
    void aPlanWithABadNameChangesNothing() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("a.jar"), "old a");
        Path waiting = Files.createDirectories(game.resolve(Install.FOLDER).resolve("waiting"));
        Files.writeString(waiting.resolve(Swap.PLAN), "{\"add\": [], \"remove\": [\"a.jar\", \"../../x.jar\"], \"players\": \"{}\"}");
        assertThrows(BadInput.class, () -> Swap.apply(game));
        assertEquals("old a", Files.readString(mods.resolve("a.jar")));
    }

    static HttpServer serve(byte[] body) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        s.createContext("/", ex -> {
            ex.sendResponseHeaders(200, body.length);
            try (OutputStream o = ex.getResponseBody()) {
                o.write(body);
            }
        });
        s.start();
        return s;
    }

    // ---- Part 7 (security review): a modpack can only claim files it really brings ----

    /** A modpack whose players file lists {@code claimed}, with sodium and minimap.jar from Modrinth. */
    Path claimingPack(String claimed) throws Exception {
        Path file = game.resolve("claiming.mrpack");
        String sha = HexFormat.of().formatHex(hash("SHA-512", SODIUM));
        String index = """
                {"formatVersion": 1, "files": [
                 {"path": "mods/sodium.jar", "hashes": {"sha512": "%s"}, "downloads": ["https://cdn.modrinth.com/s.jar"]},
                 {"path": "mods/minimap.jar", "hashes": {"sha512": "%s"}, "downloads": ["https://cdn.modrinth.com/m.jar"]}
                ]}""".formatted(sha, "cd".repeat(64));
        String players = """
                {"format": 1, "modpack": {"name": "Friends", "version": "3"}, "files": %s,
                 "title_screen": {"name": "Survival", "address": "play.example.net", "flavor_text": null}}""".formatted(claimed);
        try (ZipOutputStream z = new ZipOutputStream(Files.newOutputStream(file))) {
            put(z, "modrinth.index.json", index.getBytes(StandardCharsets.UTF_8));
            put(z, Plan.PLAYERS_FILE, players.getBytes(StandardCharsets.UTF_8));
        }
        return file;
    }

    @Test
    void aNewModpackCantClaimAPlayersOwnFile() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("minimap.jar"), "the player's own minimap");
        // The new modpack says minimap.jar is its own and brings one with that name.
        Plan plan = Plan.read(claimingPack("[\"mods/sodium.jar\", \"mods/minimap.jar\"]"), mods, serverList(),
                List.of("mods/sodium.jar"));
        assertFalse(byName(plan).containsKey("minimap.jar ADD"), "only the modpack in place says what is its own");
        assertTrue(plan.skipped().stream().anyMatch(s -> s.startsWith("minimap.jar")));
        assertTrue(byName(plan).containsKey("sodium.jar ADD"));
    }

    @Test
    void theNewPlayersFileListsOnlyFilesTheUpdateBrings() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("minimap.jar"), "the player's own minimap");
        Files.writeString(mods.resolve("journal.jar"), "the player's own journal");
        // It also claims journal.jar, which it doesn't bring: a later update would then "remove" it.
        Plan plan = Plan.read(claimingPack("[\"mods/sodium.jar\", \"mods/minimap.jar\", \"mods/journal.jar\"]"),
                mods, serverList(), List.of());
        assertEquals(List.of("mods/sodium.jar"), Plan.packFiles(plan.playersFile()));
        // The rest of the players file is kept.
        assertEquals("3", Prover.packVersion(plan.playersFile()));
        assertEquals("play.example.net", TitleSettings.parse(plan.playersFile()).host());
    }

    @Test
    void aLaterUpdateNeverRemovesAFileTheOldModpackOnlyClaimed() throws Exception {
        Path mods = mods();
        Files.writeString(mods.resolve("journal.jar"), "the player's own journal");
        Plan first = Plan.read(claimingPack("[\"mods/sodium.jar\", \"mods/journal.jar\"]"), mods, serverList(), List.of());
        Files.write(mods.resolve("sodium.jar"), SODIUM); // the first update went in
        Plan second = Plan.read(claimingPack("[\"mods/sodium.jar\"]"), mods, serverList(), Plan.packFiles(first.playersFile()));
        assertFalse(byName(second).containsKey("journal.jar REMOVE"));
    }
}
