package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The whole check, server and player side together, with real files in a
 * temporary folder standing in for the server folder and the player's game.
 */
class CheckerTest {

    @TempDir Path server;
    @TempDir Path player;

    Settings settings;
    Map<String, Path> playerJars = new HashMap<>();

    /** A jar of random bytes, on the server (at onServer) and on the player's side. */
    Settings.PackFile jar(String name, int size, String onServer) throws Exception {
        byte[] data = new byte[size];
        new SecureRandom().nextBytes(data);
        if (onServer != null) {
            Files.createDirectories(server.resolve(onServer).getParent());
            Files.write(server.resolve(onServer), data);
        }
        Path mine = player.resolve(name + ".jar");
        Files.write(mine, data);
        byte[] sha = Hashes.ofFile(mine);
        playerJars.put(HexFormat.of().formatHex(sha), mine);
        return new Settings.PackFile("mods/" + name + ".jar", name, sha, size, onServer);
    }

    Settings settings(boolean required, List<Settings.PackFile> files, int spots) {
        return new Settings(required, "Skyblock with friends", "1.4", files, "http://100.64.1.2:25590/a/modpack.mrpack",
                true, "Get the modpack", 10, 10, 30, spots, 6);
    }

    @BeforeEach
    void modpack() throws Exception {
        settings = settings(true, List.of(
                jar("sodium", 200_000, "mods/sodium.jar"),
                jar("minimap", 50_000, "player-only/minimap.jar"),
                jar("secret", 300, null)), 3);
    }

    Checker checker() {
        return Checker.create(settings, server, new SecureRandom());
    }

    /** One round trip, through the bytes, like the real thing. */
    Checker.Verdict check(Checker checker, String version) throws Exception {
        Challenge sent = checker.newChallenge();
        Challenge received = Challenge.decode(sent.encode());
        Answer answer = Prover.answer(received, playerJars, version);
        return checker.judge(sent, Answer.decode(answer.encode()));
    }

    @Test
    void theRightModpackPasses() throws Exception {
        Checker.Verdict v = check(checker(), "1.4");
        assertTrue(v.passed(), v.message());
        assertNull(v.message());
        assertFalse(v.extraMods());
        assertEquals("1.4", v.playerVersion());
    }

    @Test
    void extraModsOfTheirOwnAreFine() throws Exception {
        Files.write(player.resolve("shaders.jar"), new byte[] {1, 2, 3});
        playerJars.put(HexFormat.of().formatHex(Hashes.ofFile(player.resolve("shaders.jar"))), player.resolve("shaders.jar"));
        Checker.Verdict v = check(checker(), "1.4");
        assertTrue(v.passed());
        assertTrue(v.extraMods());
    }

    @Test
    void anOlderVersionIsToldWhichVersionToGet() throws Exception {
        playerJars.values().removeIf(p -> p.getFileName().toString().equals("minimap.jar"));
        Checker.Verdict v = check(checker(), "1.3");
        assertFalse(v.passed());
        assertTrue(v.message().contains("Skyblock with friends"), v.message());
        assertTrue(v.message().contains("1.4") && v.message().contains("1.3"), v.message());
        assertTrue(v.message().contains("http://100.64.1.2:25590/a/modpack.mrpack"), v.message());
        assertEquals(List.of("minimap"), v.missing());
    }

    @Test
    void aMissingFileIsNamed() throws Exception {
        playerJars.values().removeIf(p -> p.getFileName().toString().equals("secret.jar"));
        Checker.Verdict v = check(checker(), "1.4");
        assertFalse(v.passed());
        assertTrue(v.message().contains("secret"), v.message());
    }

    @Test
    void anOldAnswerIsUselessForANewChallenge() throws Exception {
        Checker checker = checker();
        Challenge first = checker.newChallenge();
        Answer old = Prover.answer(first, playerJars, "1.4");
        Challenge second = checker.newChallenge();
        assertFalse(Arrays.equals(first.nonce(), second.nonce()), "a fresh nonce each time");
        assertFalse(checker.judge(second, old).passed());
    }

    @Test
    void aFakedSpotProofFails() throws Exception {
        Checker checker = checker();
        Challenge sent = checker.newChallenge();
        assertFalse(sent.spots().isEmpty());
        Answer honest = Prover.answer(sent, playerJars, "1.4");
        Answer faked = new Answer(honest.nonce(), honest.packVersion(), honest.loaded(), honest.extraMods(),
                honest.proofs().stream().map(p -> new byte[32]).toList());
        assertFalse(checker.judge(sent, faked).passed());
    }

    @Test
    void aGameThatOnlyKnowsTheHashesFails() throws Exception {
        // It says "loaded" for every file but has none of the bytes.
        Checker checker = checker();
        Challenge sent = checker.newChallenge();
        Map<String, Path> liar = new HashMap<>();
        Path empty = Files.write(player.resolve("empty.jar"), new byte[0]);
        for (byte[] h : sent.expected()) {
            liar.put(HexFormat.of().formatHex(h), empty);
        }
        assertFalse(checker.judge(sent, Prover.answer(sent, liar, "1.4")).passed());
    }

    @Test
    void spotsOnlyUseFilesTheServerHasUnchanged() throws Exception {
        Files.write(server.resolve("mods/sodium.jar"), new byte[] {9}); // the owner changed it since
        Checker checker = checker();
        Set<Integer> used = new HashSet<>();
        for (int i = 0; i < 50; i++) {
            checker.newChallenge().spots().forEach(s -> used.add(s.fileIndex()));
        }
        assertEquals(Set.of(1), used, "only minimap: sodium changed, secret isn't on the server");
        assertTrue(check(checker, "1.4").passed(), "honest players still pass");
    }

    @Test
    void aServerCopyOutsideTheServerFolderIsNeverRead() throws Exception {
        Path outside = Files.write(player.resolve("outside.jar"), new byte[] {1});
        Settings.PackFile escaping = new Settings.PackFile("mods/x.jar", "x", Hashes.ofFile(outside), 1,
                "../" + player.getFileName() + "/outside.jar");
        assertNull(Checker.serverCopy(server, escaping));
    }

    @Test
    void spotsStayInsideTheFileAndUnderTheLimits() throws Exception {
        Checker checker = checker();
        for (int i = 0; i < 200; i++) {
            Challenge c = checker.newChallenge();
            assertTrue(c.spots().size() <= settings.spotChecks());
            for (Challenge.Spot s : c.spots()) {
                long size = settings.files().get(s.fileIndex()).size();
                assertTrue(s.offset() >= 0 && s.offset() + s.length() <= size, s.toString());
                assertTrue(s.length() <= Challenge.MAX_SPOT_BYTES);
            }
            assertEquals(c.spots().size(), c.spots().stream().map(Challenge.Spot::fileIndex).distinct().count());
        }
    }

    @Test
    void aShortAnswerFails() throws Exception {
        Checker checker = checker();
        Challenge sent = checker.newChallenge();
        Answer a = Prover.answer(sent, playerJars, "1.4");
        Answer cut = new Answer(a.nonce(), a.packVersion(), a.loaded().subList(0, 1), false, a.proofs());
        assertFalse(checker.judge(sent, cut).passed());
    }

    @Test
    void noAnswerSaysHowToGetIt() {
        String m = checker().noAnswerMessage();
        assertTrue(m.contains("Skyblock with friends") && m.contains("1.4") && m.contains("Companion"), m);
        assertTrue(m.contains("http://100.64.1.2:25590/a/modpack.mrpack"), m);
    }

    @Test
    void theAnswerNeverCarriesThePlayersOwnModHashes() throws Exception {
        Files.write(player.resolve("private.jar"), new byte[] {4, 5, 6});
        byte[] own = Hashes.ofFile(player.resolve("private.jar"));
        playerJars.put(HexFormat.of().formatHex(own), player.resolve("private.jar"));
        byte[] sent = Prover.answer(checker().newChallenge(), playerJars, "1.4").encode();
        String hex = HexFormat.of().formatHex(sent);
        assertFalse(hex.contains(HexFormat.of().formatHex(own)));
    }
}
