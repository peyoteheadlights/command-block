package io.github.peyoteheadlights.companion.fabric;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Checker;
import io.github.peyoteheadlights.companion.Gate;
import io.github.peyoteheadlights.companion.Settings;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.FabricServerConfigurationPacketListenerImpl;
import net.fabricmc.fabric.api.networking.v1.ServerConfigurationConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerConfigurationNetworking;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The server's side: reads the settings the app writes, checks each
 * player as they join and again now and then while they play. Runs on the
 * server thread only, so the maps need no locks.
 */
public final class CompanionServer implements ModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String SETTINGS_FILE = "commandblock-companion.json";
    private static final int TICKS_PER_MINUTE = 20 * 60;

    private static final SecureRandom RANDOM = new SecureRandom();
    private static Settings settings;
    private static Checker checker;
    private static Gate gate;
    /** Players to show the chat line once they're in. */
    private static final Map<UUID, String> chatLines = new HashMap<>();
    /** Required modpacks: when each player's next check is, and the one waiting for an answer. */
    private static final Map<UUID, Recheck> rechecks = new HashMap<>();

    private record Recheck(int dueTick, Challenge sent, int deadlineTick) {}

    @Override
    public void onInitialize() {
        CheckPayload.register();
        ServerLifecycleEvents.SERVER_STARTING.register(CompanionServer::load);
        ServerConfigurationConnectionEvents.CONFIGURE.register((handler, server) -> {
            if (checker == null) {
                return; // no settings: the app hasn't made a modpack here
            }
            if (!gate.allow(address(handler.connection.getRemoteAddress()), System.currentTimeMillis())) {
                if (settings.required()) {
                    handler.disconnect(Component.literal("Too many joins from your address. Wait a minute and try again."));
                }
                return;
            }
            if (ServerConfigurationNetworking.canSend(handler, CheckPayload.TYPE)) {
                ((FabricServerConfigurationPacketListenerImpl) handler).addTask(new JoinCheck(handler, checker, settings.timeoutSeconds()));
            } else {
                judged(handler.getOwner(), null, handler::disconnect); // no Companion at all
            }
        });
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
            ServerPlayer player = handler.getPlayer();
            String line = chatLines.remove(player.getUUID());
            if (line != null) {
                player.sendSystemMessage(Component.literal(line));
            }
            if (checker != null && settings.required()) {
                rechecks.put(player.getUUID(), new Recheck(nextCheck(server), null, 0));
            }
        });
        ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> {
            rechecks.remove(handler.getPlayer().getUUID());
            chatLines.remove(handler.getPlayer().getUUID());
        });
        ServerPlayNetworking.registerGlobalReceiver(CheckPayload.TYPE, (payload, context) -> {
            ServerPlayer player = context.player();
            Recheck waiting = rechecks.get(player.getUUID());
            if (waiting == null || waiting.sent() == null) {
                return; // not asked, or already answered
            }
            Checker.Verdict verdict;
            try {
                verdict = checker.judge(waiting.sent(), Answer.decode(payload.data()));
            } catch (BadInput e) {
                verdict = null;
            }
            rechecks.put(player.getUUID(), new Recheck(nextCheck(context.server()), null, 0));
            judged(player.getGameProfile(), verdict, player.connection::disconnect);
        });
        ServerTickEvents.END_SERVER_TICK.register(CompanionServer::tick);
    }

    /** Reads the settings once at start. Bad or missing: the Companion does nothing. */
    private static void load(MinecraftServer server) {
        settings = null;
        checker = null;
        Path file = FabricLoader.getInstance().getConfigDir().resolve(SETTINGS_FILE);
        try {
            if (Files.size(file) > Settings.MAX_SETTINGS_BYTES) {
                throw new BadInput("settings file too big");
            }
            settings = Settings.parse(Files.readString(file, StandardCharsets.UTF_8));
        } catch (NoSuchFileException e) {
            LOG.info("No {} yet: make a modpack in Command Block to turn on the check", SETTINGS_FILE);
            return;
        } catch (IOException | BadInput e) {
            LOG.warn("Not checking players: {} can't be used ({})", SETTINGS_FILE, e.getMessage());
            return;
        }
        checker = Checker.create(settings, server.getServerDirectory(), RANDOM);
        gate = new Gate(settings.handshakesPerIpPerMinute());
        LOG.info("Checking players for '{}' {} ({})", settings.modpackName(), settings.modpackVersion(),
                settings.required() ? "required to join" : "not required");
    }

    /** Sends rechecks that are due, and judges the ones nobody answered in time. */
    private static void tick(MinecraftServer server) {
        if (checker == null || rechecks.isEmpty()) {
            return;
        }
        int now = server.getTickCount();
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            Recheck r = rechecks.get(player.getUUID());
            if (r == null) {
                continue;
            }
            if (r.sent() == null && now >= r.dueTick()) {
                Challenge challenge = checker.newChallenge();
                rechecks.put(player.getUUID(), new Recheck(r.dueTick(), challenge, now + settings.timeoutSeconds() * 20));
                ServerPlayNetworking.send(player, new CheckPayload(challenge.encode()));
            } else if (r.sent() != null && now >= r.deadlineTick()) {
                rechecks.put(player.getUUID(), new Recheck(nextCheck(server), null, 0));
                judged(player.getGameProfile(), null, player.connection::disconnect);
            }
        }
    }

    /**
     * What happens after a check. {@code verdict} null means no answer (no
     * Companion, too slow, or nonsense). Returns false when the player was
     * sent away. Writes one line to the server log, which the app reads
     * for the Players page: keep its shape.
     */
    static boolean judged(GameProfile player, Checker.Verdict verdict, Consumer<Component> kick) {
        String has = verdict == null || verdict.playerVersion().isEmpty()
                ? "-"
                : verdict.playerVersion().replaceAll("[^\\w.+-]", "?");
        boolean passed = verdict != null && verdict.passed();
        LOG.info("Companion check: {} {}, modpack {}", player.name(), passed ? "passed" : "failed", has);
        if (passed) {
            return true;
        }
        String message = verdict == null ? checker.noAnswerMessage() : verdict.message();
        if (settings.required()) {
            kick.accept(Component.literal(message));
            return false;
        }
        if (settings.chatLineOn()) {
            chatLines.put(player.id(), settings.chatLineText());
        }
        return true;
    }

    private static int nextCheck(MinecraftServer server) {
        int min = settings.recheckMinMinutes();
        int minutes = min + RANDOM.nextInt(settings.recheckMaxMinutes() - min + 1);
        return server.getTickCount() + minutes * TICKS_PER_MINUTE;
    }

    private static String address(SocketAddress remote) {
        return remote instanceof InetSocketAddress inet ? inet.getAddress().getHostAddress() : String.valueOf(remote);
    }
}
