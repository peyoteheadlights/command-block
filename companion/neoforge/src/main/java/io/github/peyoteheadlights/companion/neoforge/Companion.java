package io.github.peyoteheadlights.companion.neoforge;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Checker;
import io.github.peyoteheadlights.companion.Gate;
import io.github.peyoteheadlights.companion.Hashes;
import io.github.peyoteheadlights.companion.Offer;
import io.github.peyoteheadlights.companion.Prover;
import io.github.peyoteheadlights.companion.Settings;
import io.github.peyoteheadlights.companion.screen.Updates;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import java.util.WeakHashMap;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;
import net.minecraft.network.ConnectionProtocol;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModList;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.loading.FMLEnvironment;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.common.NeoForge;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.server.ServerStartingEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import net.neoforged.neoforge.network.event.RegisterConfigurationTasksEvent;
import net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent;
import net.neoforged.neoforge.network.handling.IPayloadContext;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Both sides of the check, like the Fabric mod's CompanionServer and
 * CompanionClient together: NeoForge only calls the player's half on a
 * player's game. The server's half runs on the server thread only, so the
 * maps need no locks.
 */
@Mod("commandblock_companion")
public final class Companion {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String SETTINGS_FILE = "commandblock-companion.json";
    private static final String PLAYERS_FILE = "commandblock-companion-client.json";
    private static final int TICKS_PER_MINUTE = 20 * 60;

    private static final SecureRandom RANDOM = new SecureRandom();
    private static Settings settings;
    private static Checker checker;
    private static Gate gate;
    /** Players being checked while they join. Weak: a player who leaves mid-check is dropped. */
    static final Map<ServerConfigurationPacketListenerImpl, JoinCheck> joining = new WeakHashMap<>();
    /** Players to show the chat line once they're in. */
    private static final Map<UUID, String> chatLines = new HashMap<>();
    /** Required modpacks: when each player's next check is, and the one waiting for an answer. */
    private static final Map<UUID, Recheck> rechecks = new HashMap<>();
    private static Map<String, Path> loadedJars;

    private record Recheck(int dueTick, Challenge sent, int deadlineTick) {}

    public Companion(IEventBus modBus) {
        // optional(): players without the Companion can still connect, and get our message instead of NeoForge's.
        modBus.addListener(RegisterPayloadHandlersEvent.class, event -> event.registrar("1").optional()
                .commonBidirectional(CheckPayload.TYPE, CheckPayload.CODEC, Companion::serverGot, Companion::playerGot)
                .commonToClient(OfferPayload.TYPE, OfferPayload.CODEC, (payload, context) -> Updates.offered(payload.data())));
        if (FMLEnvironment.getDist().isClient()) {
            ClientHooks.register();
        }
        modBus.addListener(RegisterConfigurationTasksEvent.class, Companion::joining);
        NeoForge.EVENT_BUS.addListener(ServerStartingEvent.class, event -> load(event.getServer()));
        NeoForge.EVENT_BUS.addListener(PlayerEvent.PlayerLoggedInEvent.class, event -> joined((ServerPlayer) event.getEntity()));
        NeoForge.EVENT_BUS.addListener(PlayerEvent.PlayerLoggedOutEvent.class, event -> {
            rechecks.remove(event.getEntity().getUUID());
            chatLines.remove(event.getEntity().getUUID());
        });
        NeoForge.EVENT_BUS.addListener(ServerTickEvent.Post.class, event -> tick(event.getServer()));
    }

    // ---- The server's half ----

    /** Reads the settings once at start. Bad or missing: the Companion does nothing. */
    private static void load(MinecraftServer server) {
        settings = null;
        checker = null;
        Path file = FMLPaths.CONFIGDIR.get().resolve(SETTINGS_FILE);
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

    /** A player starts joining: queue the check. */
    private static void joining(RegisterConfigurationTasksEvent event) {
        if (checker == null || !(event.getListener() instanceof ServerConfigurationPacketListenerImpl handler)) {
            return; // no settings: the app hasn't made a modpack here
        }
        if (!gate.allow(address(handler.getConnection().getRemoteAddress()), System.currentTimeMillis())) {
            if (settings.required()) {
                handler.disconnect(Component.literal("Too many joins from your address. Wait a minute and try again."));
            }
            return;
        }
        if (handler.hasChannel(CheckPayload.TYPE)) {
            JoinCheck check = new JoinCheck(handler, checker, settings.timeoutSeconds(), offer(handler));
            joining.put(handler, check);
            event.register(check);
        } else {
            judged(handler.getOwner(), null, handler::disconnect); // no Companion at all
        }
    }

    private static void joined(ServerPlayer player) {
        String line = chatLines.remove(player.getUUID());
        if (line != null) {
            player.sendSystemMessage(Component.literal(line));
        }
        if (checker != null && settings.required()) {
            rechecks.put(player.getUUID(), new Recheck(nextCheck(player.level().getServer()), null, 0));
        }
    }

    /** An answer from a player: while joining it goes to their JoinCheck, while playing to their recheck. */
    private static void serverGot(CheckPayload payload, IPayloadContext context) {
        if (context.protocol() == ConnectionProtocol.CONFIGURATION) {
            JoinCheck check = joining.get(context.listener());
            if (check != null) {
                check.answered(payload.data());
            }
            return;
        }
        ServerPlayer player = (ServerPlayer) context.player();
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
        rechecks.put(player.getUUID(), new Recheck(nextCheck(player.level().getServer()), null, 0));
        judged(player.getGameProfile(), verdict, player.connection::disconnect);
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
                player.connection.send(new CheckPayload(challenge.encode()));
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
     * for the Players page: keep its shape (same as the Fabric mod).
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

    /** The update offer for this player, or null when the download link is off or they can't take it. */
    private static OfferPayload offer(ServerConfigurationPacketListenerImpl handler) {
        if (settings.downloadLink() == null || !handler.hasChannel(OfferPayload.TYPE)) {
            return null;
        }
        return new OfferPayload(new Offer(settings.modpackName(), settings.modpackVersion(), settings.downloadLink()).encode());
    }

    private static int nextCheck(MinecraftServer server) {
        int min = settings.recheckMinMinutes();
        int minutes = min + RANDOM.nextInt(settings.recheckMaxMinutes() - min + 1);
        return server.getTickCount() + minutes * TICKS_PER_MINUTE;
    }

    private static String address(SocketAddress remote) {
        return remote instanceof InetSocketAddress inet ? inet.getAddress().getHostAddress() : String.valueOf(remote);
    }

    // ---- The player's half ----

    /** The server's challenge. Hashing jars takes a moment, so it runs off the game's threads. */
    private static void playerGot(CheckPayload payload, IPayloadContext context) {
        CompletableFuture.runAsync(() -> {
            try {
                Challenge challenge = Challenge.decode(payload.data());
                String version = packVersion();
                Answer answer = Prover.answer(challenge, loadedJars(), version);
                context.reply(new CheckPayload(answer.encode()));
                Updates.checked(challenge, answer, version);
            } catch (BadInput e) {
                LOG.warn("The server's check didn't make sense ({}), so it wasn't answered", e.getMessage());
            }
        });
    }

    /**
     * The jars in the game's mods folder that NeoForge really loaded, by
     * their SHA-256 (lowercase hex). Asks the loader, not the folder: a jar
     * that's there but didn't load doesn't count. Hashed once per game.
     */
    private static synchronized Map<String, Path> loadedJars() {
        if (loadedJars != null) {
            return loadedJars;
        }
        Path mods = FMLPaths.MODSDIR.get().toAbsolutePath().normalize();
        Map<String, Path> jars = new HashMap<>();
        for (var info : ModList.get().getModFiles()) {
            Path jar = info.getFile().getFilePath().toAbsolutePath().normalize();
            if (!jar.startsWith(mods) || !Files.isRegularFile(jar)) {
                continue; // only the mods folder is the modpack's business (not Minecraft or NeoForge)
            }
            try {
                jars.put(HexFormat.of().formatHex(Hashes.ofFile(jar)), jar);
            } catch (IOException e) {
                LOG.warn("Couldn't read {}: {}", jar.getFileName(), e.toString());
            }
        }
        loadedJars = Map.copyOf(jars);
        return loadedJars;
    }

    /** The modpack's own version, from the file the modpack carries; "" without one. */
    private static String packVersion() {
        return Prover.packVersion(playersFile());
    }

    /** The players file the modpack carries (version, title screen); "" without one. */
    static String playersFile() {
        Path file = FMLPaths.CONFIGDIR.get().resolve(PLAYERS_FILE);
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
