package io.github.peyoteheadlights.companion.neoforge;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Hashes;
import io.github.peyoteheadlights.companion.Prover;
import io.github.peyoteheadlights.companion.ServerSide;
import io.github.peyoteheadlights.companion.Settings;
import io.github.peyoteheadlights.companion.screen.Updates;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
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
 * player's game. The server's half connects common's ServerSide to
 * NeoForge's events, on the server thread.
 */
@Mod("commandblock_companion")
public final class Companion {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String SETTINGS_FILE = "commandblock-companion.json";
    private static final String PLAYERS_FILE = "commandblock-companion-client.json";

    /** null: no settings, so the Companion does nothing on this server. */
    static ServerSide side;
    /**
     * Players being checked while they join. Not a WeakHashMap: each JoinCheck holds its
     * handler, so a weak key would never be freed. Players who leave mid-check are dropped
     * once a second instead.
     */
    static final Map<ServerConfigurationPacketListenerImpl, JoinCheck> joining = new ConcurrentHashMap<>();
    private static Map<String, Path> loadedJars;

    public Companion(IEventBus modBus) {
        // optional(): players without the Companion can still connect, and get our message instead of NeoForge's.
        modBus.addListener(RegisterPayloadHandlersEvent.class, event -> event.registrar("1").optional()
                .commonBidirectional(CheckPayload.TYPE, CheckPayload.CODEC, Companion::serverGot, Companion::playerGot)
                .commonToClient(OfferPayload.TYPE, OfferPayload.CODEC, (payload, context) -> Updates.offered(payload.data())));
        if (FMLEnvironment.getDist().isClient()) {
            ClientHooks.register();
        }
        modBus.addListener(RegisterConfigurationTasksEvent.class, Companion::joining);
        NeoForge.EVENT_BUS.addListener(ServerStartingEvent.class, event -> side = ServerSide.load(
                FMLPaths.CONFIGDIR.get().resolve(SETTINGS_FILE), event.getServer().getServerDirectory(), LOG::info, LOG::warn));
        NeoForge.EVENT_BUS.addListener(PlayerEvent.PlayerLoggedInEvent.class, event -> {
            String line = side == null ? null : side.joined(event.getEntity().getUUID());
            if (line != null) {
                ((ServerPlayer) event.getEntity()).sendSystemMessage(Component.literal(line));
            }
        });
        NeoForge.EVENT_BUS.addListener(PlayerEvent.PlayerLoggedOutEvent.class, event -> {
            if (side != null) {
                side.left(event.getEntity().getUUID());
            }
        });
        NeoForge.EVENT_BUS.addListener(ServerTickEvent.Post.class, event -> {
            MinecraftServer server = event.getServer();
            if (side != null && server.getTickCount() % 20 == 0) { // once a second is enough
                side.tick(server.getPlayerList().getPlayers().stream().map(Companion::online).toList());
                joining.keySet().removeIf(handler -> !handler.getConnection().isConnected()); // left mid-check
            }
        });
    }

    // ---- The server's half ----

    /** A player starts joining: queue the check. */
    private static void joining(RegisterConfigurationTasksEvent event) {
        if (side == null || !(event.getListener() instanceof ServerConfigurationPacketListenerImpl handler)) {
            return; // no settings: the app hasn't made a modpack here
        }
        Consumer<String> kick = message -> handler.disconnect(Component.literal(message));
        GameProfile profile = handler.getOwner();
        if (!side.mayCheck(profile.id(), profile.name(), address(handler.getConnection().getRemoteAddress()), kick)) {
            return;
        }
        if (handler.hasChannel(CheckPayload.TYPE)) {
            JoinCheck check = new JoinCheck(handler, side, handler.hasChannel(OfferPayload.TYPE));
            joining.put(handler, check);
            event.register(check);
        } else {
            side.judged(profile.id(), profile.name(), null, null, kick); // no Companion at all
        }
    }

    /** An answer from a player: while joining it goes to their JoinCheck, while playing to their recheck. */
    private static void serverGot(CheckPayload payload, IPayloadContext context) {
        if (context.protocol() == ConnectionProtocol.CONFIGURATION) {
            JoinCheck check = joining.get(context.listener());
            if (check != null) {
                check.answered(payload.data());
            }
        } else if (side != null) {
            side.answered(online((ServerPlayer) context.player()), payload.data());
        }
    }

    private static ServerSide.Online online(ServerPlayer player) {
        return new ServerSide.Online(player.getUUID(), player.getGameProfile().name(),
                challenge -> player.connection.send(new CheckPayload(challenge)),
                message -> player.connection.disconnect(Component.literal(message)));
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
                String players = playersFile();
                Answer answer = Prover.answer(challenge, loadedJars(), Prover.packVersion(players));
                context.reply(new CheckPayload(answer.encode()));
                Updates.checked(challenge, answer, players);
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
