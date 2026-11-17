package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The server's half that the Fabric mod, the NeoForge mod and the Paper plugin share. */
class ServerSideTest {
    @TempDir
    Path dir;

    final List<String> log = new ArrayList<>();
    final List<String> kicks = new ArrayList<>();
    final List<byte[]> sent = new ArrayList<>();
    long now = 0;

    ServerSide side(boolean required) throws Exception {
        Path file = dir.resolve("commandblock-companion.json");
        Files.writeString(file, SettingsTest.changed(r -> r.addProperty("required", required)));
        return ServerSide.load(file, dir, log::add, log::add, () -> now);
    }

    ServerSide.Online online(UUID id, List<ServerSide.Online> world) {
        return new ServerSide.Online(id, "Bob", sent::add, message -> {
            kicks.add(message);
            world.removeIf(p -> p.id().equals(id)); // like Minecraft taking them off the list
        });
    }

    @Test
    void noSettingsFileMeansNoCheck() {
        assertNull(ServerSide.load(dir.resolve("missing.json"), dir, log::add, log::add));
        assertTrue(log.get(0).startsWith("No missing.json yet"));
    }

    @Test
    void noAnswerOnARequiredModpackSendsThePlayerAway() throws Exception {
        ServerSide side = side(true);
        assertFalse(side.judged(UUID.randomUUID(), "Bob", side.newChallenge(), null, kicks::add));
        assertEquals("Companion check: Bob failed, modpack -", log.get(log.size() - 1), "the app reads this line");
        assertTrue(kicks.get(0).startsWith("This server needs 'Skyblock with friends' 1.4"));
    }

    @Test
    void notRequiredLetsThemInWithTheChatLine() throws Exception {
        ServerSide side = side(false);
        UUID id = UUID.randomUUID();
        assertTrue(side.judged(id, "Bob", side.newChallenge(), new byte[] {9}, kicks::add), "nonsense counts as no answer");
        assertEquals(List.of(), kicks);
        assertTrue(side.joined(id).startsWith("This server uses the modpack"));
        assertNull(side.joined(id), "once");
    }

    @Test
    void theJoinLimitNeverCountsThisPc() throws Exception {
        ServerSide side = side(true); // 6 a minute
        for (int i = 0; i < 20; i++) {
            assertTrue(side.mayCheck("127.0.0.1", kicks::add), "the join guard passes every player on from 127.0.0.1");
        }
        for (int i = 0; i < 6; i++) {
            assertTrue(side.mayCheck("1.2.3.4", kicks::add));
        }
        assertFalse(side.mayCheck("1.2.3.4", kicks::add));
        assertEquals(List.of("Too many joins from your address. Wait a minute and try again."), kicks);
    }

    @Test
    void rechecksComeDueAndALateAnswerSendsThemAway() throws Exception {
        ServerSide side = side(true);
        UUID id = UUID.randomUUID();
        List<ServerSide.Online> world = new ArrayList<>();
        world.add(online(id, world));
        side.joined(id);

        side.tick(world);
        assertEquals(0, sent.size(), "not due yet");
        now += 31 * 60_000L; // the settings say a recheck within 10 to 30 minutes
        side.tick(world);
        assertEquals(1, sent.size());
        Challenge.decode(sent.get(0));

        now += 11_000; // the timeout is 10 seconds
        side.tick(world); // the kick takes Bob off the list while the tick goes through it
        assertEquals(1, kicks.size());
        assertTrue(world.isEmpty());
    }

    @Test
    void onlyAnAnswerToARecheckCounts() throws Exception {
        ServerSide side = side(true);
        UUID id = UUID.randomUUID();
        List<ServerSide.Online> world = new ArrayList<>();
        ServerSide.Online bob = online(id, world);
        world.add(bob);
        side.joined(id);
        side.answered(bob, new byte[] {1});
        assertEquals(List.of(), kicks, "not asked: ignored");

        now += 31 * 60_000L;
        side.tick(world);
        side.answered(bob, new byte[] {1});
        assertEquals(1, kicks.size(), "asked, and the answer is nonsense");
    }
}
