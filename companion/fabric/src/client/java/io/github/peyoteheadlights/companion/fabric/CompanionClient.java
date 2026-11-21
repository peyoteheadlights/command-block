package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Prover;
import io.github.peyoteheadlights.companion.Settings;
import io.github.peyoteheadlights.companion.screen.TitleExtras;
import io.github.peyoteheadlights.companion.screen.Updates;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CompletableFuture;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.fabricmc.fabric.api.client.networking.v1.ClientConfigurationNetworking;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PacketSender;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.DisconnectedScreen;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** The player's side: answers the server's challenge, while joining and again while playing. */
public final class CompanionClient implements ClientModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String PLAYERS_FILE = "commandblock-companion-client.json";

    @Override
    public void onInitializeClient() {
        String players = playersFile();
        ClientConfigurationNetworking.registerGlobalReceiver(
                CheckPayload.TYPE, (payload, context) -> answer(payload, context.responseSender(), players));
        ClientPlayNetworking.registerGlobalReceiver(
                CheckPayload.TYPE, (payload, context) -> answer(payload, context.responseSender(), players));
        ClientConfigurationNetworking.registerGlobalReceiver(OfferPayload.TYPE, (payload, context) -> Updates.offered(payload.data()));
        ClientPlayNetworking.registerGlobalReceiver(OfferPayload.TYPE, (payload, context) -> Updates.offered(payload.data()));
        // "Update modpack" on the screen after a refused join, and on the pause menu.
        ScreenEvents.AFTER_INIT.register((client, screen, width, height) -> {
            if (screen instanceof ConnectScreen) {
                Updates.joining(screen); // a new join: the last server's offer no longer counts
            }
            if (screen instanceof DisconnectedScreen || screen instanceof PauseScreen) {
                Button button = Updates.button(screen, width / 2 - 100, 8);
                if (button != null) {
                    Screens.getWidgets(screen).add(button);
                }
            }
            // The owner's join button, background and flavor text (part 5).
            if (screen instanceof TitleScreen title) {
                TitleExtras.load(FabricLoader.getInstance().getConfigDir(), FabricLoader.getInstance().getGameDir(), playersFile());
                Button join = TitleExtras.init(title, Screens.getWidgets(screen));
                if (join != null) {
                    Screens.getWidgets(screen).add(join);
                }
                ScreenEvents.beforeExtract(screen).register((s, graphics, mouseX, mouseY, delta) ->
                        TitleExtras.drawBackground(graphics, s.width, s.height));
            }
        });
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> Updates.closing(
                FabricLoader.getInstance().getGameDir(),
                FabricLoader.getInstance().getModContainer("commandblock_companion").orElseThrow().getOrigin().getPaths().get(0)));
    }

    /** Hashing jars takes a moment, so it runs off the game's threads. */
    private static void answer(CheckPayload payload, PacketSender sender, String players) {
        CompletableFuture.runAsync(() -> {
            try {
                Challenge challenge = Challenge.decode(payload.data());
                Answer answer = Prover.answer(challenge, LoadedJars.get(), Prover.packVersion(players));
                sender.sendPacket(new CheckPayload(answer.encode()));
                Updates.checked(challenge, answer, players);
            } catch (BadInput e) {
                LOG.warn("The server's check didn't make sense ({}), so it wasn't answered", e.getMessage());
            }
        });
    }

    /** The players file the modpack carries (version, title screen); "" without one. */
    private static String playersFile() {
        Path file = FabricLoader.getInstance().getConfigDir().resolve(PLAYERS_FILE);
        try {
            if (Files.size(file) > Settings.MAX_SETTINGS_BYTES) { // it lists the modpack's files
                return "";
            }
            return Files.readString(file, StandardCharsets.UTF_8);
        } catch (IOException e) {
            return "";
        }
    }
}
