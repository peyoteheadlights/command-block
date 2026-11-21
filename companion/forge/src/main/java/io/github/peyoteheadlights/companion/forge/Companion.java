package io.github.peyoteheadlights.companion.forge;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Hashes;
import io.github.peyoteheadlights.companion.Offer;
import io.github.peyoteheadlights.companion.Prover;
import io.github.peyoteheadlights.companion.ServerSide;
import io.github.peyoteheadlights.companion.Settings;
import io.github.peyoteheadlights.companion.screen.Updates;
import io.netty.buffer.Unpooled;
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
import net.minecraft.network.Connection;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;
import net.minecraftforge.event.TickEvent;
import net.minecraftforge.event.entity.player.PlayerEvent;
import net.minecraftforge.event.network.CustomPayloadEvent;
import net.minecraftforge.event.network.GatherLoginConfigurationTasksEvent;
import net.minecraftforge.event.server.ServerStartingEvent;
import net.minecraftforge.eventbus.api.listener.Priority;
import net.minecraftforge.fml.ModList;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.loading.FMLEnvironment;
import net.minecraftforge.fml.loading.FMLPaths;
import net.minecraftforge.network.ChannelBuilder;
import net.minecraftforge.network.EventNetworkChannel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Both sides of the check, like the NeoForge mod's Companion: Forge only
 * runs the player's half on a player's game. The server's half connects
 * common's ServerSide to Forge's events, on the server thread.
 */
@Mod("commandblock_companion")
public final class Companion {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String SETTINGS_FILE = "commandblock-companion.json";
    private static final String PLAYERS_FILE = "commandblock-companion-client.json";

    /** A challenge is the bigger one: 4000 hashes of 32 bytes, plus spots (as NeoForge's CheckPayload). */
    static final int CHECK_MAX_BYTES = 1 + Challenge.NONCE_BYTES + 2 + 32 * 4000 + 1 + 14 * Challenge.MAX_SPOTS;
    static final int OFFER_MAX_BYTES = 1 + 3 * 2 + Offer.MAX_NAME_BYTES + 32 + Offer.MAX_LINK_BYTES;

    // optional(): players without the Companion (or without Forge) can still connect, and get our message instead of Forge's.
    static final EventNetworkChannel CHECK = ChannelBuilder
            .named(Identifier.fromNamespaceAndPath("commandblock", "check")).optional().eventNetworkChannel();
    static final EventNetworkChannel OFFER = ChannelBuilder
            .named(Identifier.fromNamespaceAndPath("commandblock", "offer")).optional().eventNetworkChannel();

    /** null: no settings, so the Companion does nothing on this server. */
    static ServerSide side;
    /** Players being checked while they join. Concurrent: Forge may gather join steps off the server thread. */
    static final Map<Connection, JoinCheck> joining = new ConcurrentHashMap<>();
    private static Map<String, Path> loadedJars;

    public Companion() {
        CHECK.addListener(event -> {
            byte[] data = read(event, CHECK_MAX_BYTES);
            if (data != null && event.getSource().isClientSide()) {
                playerGot(data, event.getSource().getConnection());
            } else if (data != null) {
                serverGot(data, event.getSource());
            }
        });
        OFFER.addListener(event -> {
            byte[] data = read(event, OFFER_MAX_BYTES);
            if (data != null && event.getSource().isClientSide()) { // server to player only
                event.getSource().enqueueWork(() -> Updates.offered(data));
            }
        });
        if (FMLEnvironment.dist.isClient()) {
            ClientHooks.register();
        }
        // LOWEST: after Forge's own steps, so the player's channel list has arrived when the check starts.
        GatherLoginConfigurationTasksEvent.BUS.addListener(Priority.LOWEST, Companion::joining);
        ServerStartingEvent.BUS.addListener(event -> side = ServerSide.load(
                FMLPaths.CONFIGDIR.get().resolve(SETTINGS_FILE), event.getServer().getServerDirectory(), LOG::info, LOG::warn));
        PlayerEvent.PlayerLoggedInEvent.BUS.addListener(event -> {
            String line = side == null ? null : side.joined(event.getEntity().getUUID());
            if (line != null) {
                ((ServerPlayer) event.getEntity()).sendSystemMessage(Component.literal(line));
            }
        });
        PlayerEvent.PlayerLoggedOutEvent.BUS.addListener(event -> {
            if (side != null) {
                side.left(event.getEntity().getUUID());
            }
        });
        TickEvent.ServerTickEvent.Post.BUS.addListener(event -> {
            MinecraftServer server = event.server();
            if (side != null && server.getTickCount() % 20 == 0) { // once a second is enough
                side.tick(server.getPlayerList().getPlayers().stream().map(Companion::online).toList());
                joining.keySet().removeIf(connection -> !connection.isConnected()); // left mid-check
            }
        });
    }

    /** One packet is one Minecraft byte array (PROTOCOL.md). null when it's too big or broken. */
    private static byte[] read(CustomPayloadEvent event, int maxBytes) {
        event.getSource().setPacketHandled(true);
        FriendlyByteBuf buf = event.getPayload();
        try {
            return buf == null ? null : buf.readByteArray(maxBytes); // refuses a bigger length before reading it
        } catch (RuntimeException e) {
            return null;
        }
    }

    static void send(EventNetworkChannel channel, byte[] data, Connection to) {
        channel.send(new FriendlyByteBuf(Unpooled.buffer()).writeByteArray(data), to);
    }

    // ---- The server's half ----

    /** A player starts joining: queue the check. */
    private static void joining(GatherLoginConfigurationTasksEvent event) {
        Connection connection = event.getConnection();
        if (side == null || !(connection.getPacketListener() instanceof ServerConfigurationPacketListenerImpl handler)) {
            return; // no settings: the app hasn't made a modpack here
        }
        GameProfile profile = handler.getOwner();
        Consumer<String> kick = message -> handler.disconnect(Component.literal(message));
        if (side.mayCheck(profile.id(), profile.name(), address(connection.getRemoteAddress()), kick)) {
            JoinCheck check = new JoinCheck(handler, side);
            joining.put(connection, check);
            event.addTask(check);
        }
    }

    /** An answer from a player: while joining it goes to their JoinCheck, while playing to their recheck. */
    private static void serverGot(byte[] data, CustomPayloadEvent.Context source) {
        ServerPlayer player = source.getSender(); // null while still joining
        Connection connection = source.getConnection();
        source.enqueueWork(() -> {
            if (player == null) {
                JoinCheck check = joining.get(connection);
                if (check != null) {
                    check.answered(data);
                }
            } else if (side != null) {
                side.answered(online(player), data);
            }
        });
    }

    private static ServerSide.Online online(ServerPlayer player) {
        return new ServerSide.Online(player.getUUID(), player.getGameProfile().name(),
                challenge -> send(CHECK, challenge, player.connection.getConnection()),
                message -> player.connection.disconnect(Component.literal(message)));
    }

    private static String address(SocketAddress remote) {
        return remote instanceof InetSocketAddress inet ? inet.getAddress().getHostAddress() : String.valueOf(remote);
    }

    // ---- The player's half ----

    /** The server's challenge. Hashing jars takes a moment, so it runs off the game's threads. */
    private static void playerGot(byte[] data, Connection server) {
        CompletableFuture.runAsync(() -> {
            try {
                Challenge challenge = Challenge.decode(data);
                String players = playersFile();
                Answer answer = Prover.answer(challenge, loadedJars(), Prover.packVersion(players));
                send(CHECK, answer.encode(), server);
                Updates.checked(challenge, answer, players);
            } catch (BadInput e) {
                LOG.warn("The server's check didn't make sense ({}), so it wasn't answered", e.getMessage());
            }
        });
    }

    /**
     * The jars in the game's mods folder that Forge really loaded, by
     * their SHA-256 (lowercase hex). Asks the loader, not the folder: a jar
     * that's there but didn't load doesn't count. Hashed once per game.
     */
    private static synchronized Map<String, Path> loadedJars() {
        if (loadedJars != null) {
            return loadedJars;
        }
        Path mods = FMLPaths.MODSDIR.get().toAbsolutePath().normalize();
        Map<String, Path> jars = new HashMap<>();
        for (var info : ModList.getModFiles()) {
            Path jar = info.getFile().getFilePath().toAbsolutePath().normalize();
            if (!jar.startsWith(mods) || !Files.isRegularFile(jar)) {
                continue; // only the mods folder is the modpack's business (not Minecraft or Forge)
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
