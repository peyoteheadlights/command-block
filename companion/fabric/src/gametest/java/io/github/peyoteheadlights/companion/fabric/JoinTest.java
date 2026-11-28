package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.screen.Updates;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.loader.api.FabricLoader;

/**
 * A real game opens a world with the Companion's settings in place. In
 * singleplayer the game is both player and server, so both halves of the
 * check run: the server asks, the player's side answers.
 */
public final class JoinTest implements FabricClientGameTest {
    private static final String FAKE_SHA = "ab".repeat(32);
    private final List<String> chat = new CopyOnWriteArrayList<>();

    @Override
    public void runTest(ClientGameTestContext context) {
        ClientReceiveMessageEvents.GAME.register((message, overlay) -> chat.add(message.getString()));

        // Required, and the modpack has no mod jars: only a player whose
        // Companion answered in time gets in (no answer means a kick).
        settings(true, "[]", "unused");
        try (TestSingleplayerContext world = context.worldBuilder().create()) {
            world.getConnection().waitForChunksDownload();
            check(context.computeOnClient(mc -> mc.player != null), "a required check with every file loaded lets the player in");
        }

        // Not required, and the player lacks a modpack file: they get in and see the chat line.
        settings(false, "[{\"path\": \"mods/missing.jar\", \"name\": \"Missing\", \"sha256\": \"" + FAKE_SHA
                + "\", \"size\": 10, \"on_server\": null}]", "Get the Friends modpack");
        chat.clear();
        try (TestSingleplayerContext world = context.worldBuilder().create()) {
            world.getConnection().waitForChunksDownload();
            context.waitFor(mc -> chat.contains("Get the Friends modpack"), 200);
        }

        // With the download link on, a player who's behind is offered the update (the button the
        // disconnect and pause screens get). Nothing is downloaded until they press it.
        settings(false, "[{\"path\": \"mods/missing.jar\", \"name\": \"Missing\", \"sha256\": \"" + FAKE_SHA
                + "\", \"size\": 10, \"on_server\": null}]", "Get the Friends modpack", "\"http://127.0.0.1:9/x/modpack.mrpack\"");
        try (TestSingleplayerContext world = context.worldBuilder().create()) {
            world.getConnection().waitForChunksDownload();
            context.waitFor(mc -> Updates.button(null, 0, 0) != null, 200);
        }
    }

    private static void settings(boolean required, String files, String chatLine) {
        settings(required, files, chatLine, "null");
    }

    private static void settings(boolean required, String files, String chatLine, String link) {
        String json = """
                {"format": 1, "required": %s, "modpack": {"name": "Friends", "version": "1"},
                 "files": %s, "download_link": %s, "chat_line": {"on": true, "text": "%s"},
                 "check": {"timeout_seconds": 10, "recheck_minutes": [10, 30], "spot_checks": 3,
                           "handshakes_per_ip_per_minute": 60}}
                """.formatted(required, files, link, chatLine);
        Path file = FabricLoader.getInstance().getConfigDir().resolve("commandblock-companion.json");
        try {
            Files.writeString(file, json);
        } catch (IOException e) {
            throw new AssertionError(e);
        }
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError(what);
        }
    }
}
