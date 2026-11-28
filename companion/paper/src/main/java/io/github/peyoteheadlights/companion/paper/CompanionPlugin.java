package io.github.peyoteheadlights.companion.paper;

import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.ServerSide;
import io.papermc.paper.connection.PlayerConfigurationConnection;
import io.papermc.paper.connection.PlayerConnection;
import io.papermc.paper.connection.PlayerGameConnection;
import io.papermc.paper.event.connection.configuration.AsyncPlayerConnectionConfigureEvent;
import java.io.ByteArrayOutputStream;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Consumer;
import net.kyori.adventure.text.Component;
import org.bukkit.Bukkit;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.plugin.java.JavaPlugin;
import org.bukkit.plugin.messaging.PluginMessageListener;

/**
 * The server's half of the check on Paper: connects common's ServerSide to
 * Paper's events. Players answer with the Fabric or NeoForge mod, which
 * send the same bytes on the same channel. The join check runs on Paper's
 * own thread for joining players (it may wait there); everything else runs
 * on the server thread.
 */
public final class CompanionPlugin extends JavaPlugin implements Listener, PluginMessageListener {
    static final String CHANNEL = "commandblock:check";
    static final String OFFER_CHANNEL = "commandblock:offer";
    /** Same limit as the mods' CheckPayload: 4000 hashes of 32 bytes, plus spots. */
    static final int MAX_BYTES = 1 + Challenge.NONCE_BYTES + 2 + 32 * 4000 + 1 + 14 * Challenge.MAX_SPOTS;

    private ServerSide side;
    /** Players being checked while they join, waiting for their answer. */
    private final Map<UUID, CompletableFuture<byte[]>> joining = new ConcurrentHashMap<>();

    @Override
    public void onEnable() {
        // The app writes on_server paths from the server folder: plugins/CommandBlockCompanion/.. /..
        Path serverDir = getDataFolder().toPath().toAbsolutePath().getParent().getParent();
        side = ServerSide.load(getDataFolder().toPath().resolve("config.json"), serverDir,
                getLogger()::info, getLogger()::warning);
        if (side == null) {
            return;
        }
        getServer().getMessenger().registerOutgoingPluginChannel(this, CHANNEL);
        getServer().getMessenger().registerOutgoingPluginChannel(this, OFFER_CHANNEL);
        getServer().getMessenger().registerIncomingPluginChannel(this, CHANNEL, this);
        getServer().getPluginManager().registerEvents(this, this);
        getServer().getScheduler().runTaskTimer(this,
                () -> side.tick(getServer().getOnlinePlayers().stream().map(this::online).toList()), 20, 20);
    }

    /** A player is joining, not yet in the world: ask, and wait for the answer. */
    @EventHandler
    public void onConfigure(AsyncPlayerConnectionConfigureEvent event) {
        PlayerConfigurationConnection connection = event.getConnection();
        UUID id = connection.getProfile().getId();
        String name = connection.getProfile().getName();
        Consumer<String> kick = message -> connection.disconnect(Component.text(message));
        if (!side.mayCheck(id, name, connection.getClientAddress().getAddress().getHostAddress(), kick)) {
            return;
        }
        if (id == null || !connection.getListeningPluginChannels().contains(CHANNEL)) {
            side.judged(id, name, null, null, kick); // no Companion at all
            return;
        }
        CompletableFuture<byte[]> reply = new CompletableFuture<>();
        joining.put(id, reply);
        Challenge sent = side.newChallenge();
        byte[] answer = null;
        try {
            byte[] offer = side.offer();
            if (offer != null && connection.getListeningPluginChannels().contains(OFFER_CHANNEL)) {
                // First, so the player's Companion can offer the update if the check finds them behind.
                connection.sendPluginMessage(this, OFFER_CHANNEL, frame(offer));
            }
            connection.sendPluginMessage(this, CHANNEL, frame(sent.encode()));
            answer = unframe(reply.get(side.timeoutSeconds(), TimeUnit.SECONDS));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (TimeoutException | ExecutionException | BadInput e) {
            // no answer in time, or nonsense: judged as no answer
        } finally {
            joining.remove(id, reply); // only our own: a second join with the same name has its own
        }
        side.judged(id, name, sent, answer, kick);
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        String line = side.joined(event.getPlayer().getUniqueId());
        if (line != null) {
            event.getPlayer().sendMessage(Component.text(line));
        }
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        side.left(event.getPlayer().getUniqueId());
    }

    /** Paper calls this one for joining and joined players alike. */
    @Override
    public void onPluginMessageReceived(String channel, PlayerConnection connection, byte[] message) {
        if (connection instanceof PlayerConfigurationConnection joiner) {
            CompletableFuture<byte[]> reply = joining.get(joiner.getProfile().getId());
            if (reply != null) {
                reply.complete(message); // only the first answer counts: one check per join
            }
        } else if (connection instanceof PlayerGameConnection game) {
            Player player = game.getPlayer();
            if (Bukkit.isPrimaryThread()) {
                rechecked(player, message);
            } else {
                getServer().getScheduler().runTask(this, () -> rechecked(player, message));
            }
        }
    }

    @Override
    public void onPluginMessageReceived(String channel, Player player, byte[] message) {
        // Handled above, with the connection.
    }

    private void rechecked(Player player, byte[] message) {
        if (!player.isOnline()) {
            return;
        }
        byte[] answer;
        try {
            answer = unframe(message);
        } catch (BadInput e) {
            answer = new byte[0]; // nonsense: judged as no answer
        }
        side.answered(online(player), answer);
    }

    private ServerSide.Online online(Player player) {
        return new ServerSide.Online(player.getUniqueId(), player.getName(),
                challenge -> player.sendPluginMessage(this, CHANNEL, frame(challenge)),
                message -> player.kick(Component.text(message)));
    }

    /**
     * The mods send a byte array the Minecraft way: its length as a VarInt,
     * then the bytes. Paper hands plugins the raw packet, so we add and
     * strip that length ourselves.
     */
    static byte[] frame(byte[] data) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(data.length + 5);
        int n = data.length;
        while ((n & ~0x7F) != 0) {
            out.write((n & 0x7F) | 0x80);
            n >>>= 7;
        }
        out.write(n);
        out.write(data, 0, data.length);
        return out.toByteArray();
    }

    static byte[] unframe(byte[] packet) throws BadInput {
        int length = 0;
        int i = 0;
        while (true) {
            if (i >= packet.length || i >= 3) {
                throw new BadInput("bad length"); // MAX_BYTES fits in 3 VarInt bytes
            }
            byte b = packet[i];
            length |= (b & 0x7F) << (7 * i++);
            if ((b & 0x80) == 0) {
                break;
            }
        }
        if (length > MAX_BYTES || length != packet.length - i) {
            throw new BadInput("bad length");
        }
        return Arrays.copyOfRange(packet, i, packet.length);
    }
}
