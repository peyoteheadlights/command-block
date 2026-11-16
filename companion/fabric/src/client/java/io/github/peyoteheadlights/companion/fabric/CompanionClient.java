package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Prover;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CompletableFuture;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.networking.v1.ClientConfigurationNetworking;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.networking.v1.PacketSender;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** The player's side: answers the server's challenge, while joining and again while playing. */
public final class CompanionClient implements ClientModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String PLAYERS_FILE = "commandblock-companion-client.json";

    @Override
    public void onInitializeClient() {
        String version = packVersion();
        ClientConfigurationNetworking.registerGlobalReceiver(
                CheckPayload.TYPE, (payload, context) -> answer(payload, context.responseSender(), version));
        ClientPlayNetworking.registerGlobalReceiver(
                CheckPayload.TYPE, (payload, context) -> answer(payload, context.responseSender(), version));
    }

    /** Hashing jars takes a moment, so it runs off the game's threads. */
    private static void answer(CheckPayload payload, PacketSender sender, String version) {
        CompletableFuture.runAsync(() -> {
            try {
                Challenge challenge = Challenge.decode(payload.data());
                byte[] answer = Prover.answer(challenge, LoadedJars.get(), version).encode();
                sender.sendPacket(new CheckPayload(answer));
            } catch (BadInput e) {
                LOG.warn("The server's check didn't make sense ({}), so it wasn't answered", e.getMessage());
            }
        });
    }

    /** The modpack's own version, from the file the modpack carries; "" without one. */
    private static String packVersion() {
        Path file = FabricLoader.getInstance().getConfigDir().resolve(PLAYERS_FILE);
        try {
            if (Files.size(file) > 4096) {
                return "";
            }
            return Prover.packVersion(Files.readString(file, StandardCharsets.UTF_8));
        } catch (IOException e) {
            return "";
        }
    }
}
