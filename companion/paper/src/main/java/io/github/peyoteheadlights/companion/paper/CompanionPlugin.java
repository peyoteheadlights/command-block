package io.github.peyoteheadlights.companion.paper;

import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Checker;
import io.github.peyoteheadlights.companion.Gate;
import io.github.peyoteheadlights.companion.Offer;
import io.github.peyoteheadlights.companion.Settings;
import io.papermc.paper.connection.PlayerConfigurationConnection;
import io.papermc.paper.connection.PlayerConnection;
import io.papermc.paper.connection.PlayerGameConnection;
import io.papermc.paper.event.connection.configuration.AsyncPlayerConnectionConfigureEvent;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.HashMap;
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
 * The server's half of the check on Paper. Players answer with the Fabric
 * or NeoForge mod, which send the same bytes on the same channel.
 * The join check runs on Paper's own thread for joining players (it may
 * wait there); everything else runs on the server thread.
 */
public final class CompanionPlugin extends JavaPlugin implements Listener, PluginMessageListener {
    static final String CHANNEL = "commandblock:check";
    static final String OFFER_CHANNEL = "commandblock:offer";
    /** Same limit as the mods' CheckPayload: 4000 hashes of 32 bytes, plus spots. */
    static final int MAX_BYTES = 1 + Challenge.NONCE_BYTES + 2 + 32 * 4000 + 1 + 14 * Challenge.MAX_SPOTS;
    private static final int TICKS_PER_MINUTE = 20 * 60;

    private final SecureRandom random = new SecureRandom();
    private Settings settings;
    private Checker checker;
    private Gate gate;
    /** Players being checked while they join, waiting for their answer. */
    private final Map<UUID, CompletableFuture<byte[]>> joining = new ConcurrentHashMap<>();
    /** Players to show the chat line once they're in. */
    private final Map<UUID, String> chatLines = new ConcurrentHashMap<>();
    /** Required modpacks: when each player's next check is, and the one waiting for an answer. */
    private final Map<UUID, Recheck> rechecks = new HashMap<>();

    private record Recheck(int dueTick, Challenge sent, int deadlineTick) {}

    @Override
    public void onEnable() {
        Path file = getDataFolder().toPath().resolve("config.json");
        try {
            if (Files.size(file) > Settings.MAX_SETTINGS_BYTES) {
                throw new BadInput("settings file too big");
            }
            settings = Settings.parse(Files.readString(file, StandardCharsets.UTF_8));
        } catch (NoSuchFileException e) {
            getSLF4JLogger().info("No config.json yet: make a modpack in Command Block to turn on the check");
            return;
        } catch (IOException | BadInput e) {
            getSLF4JLogger().warn("Not checking players: config.json can't be used ({})", e.getMessage());
            return;
        }
        // The app writes on_server paths from the server folder: plugins/CommandBlockCompanion/.. /..
        Path serverDir = getDataFolder().toPath().toAbsolutePath().getParent().getParent();
        checker = Checker.create(settings, serverDir, random);
        gate = new Gate(settings.handshakesPerIpPerMinute());
        getServer().getMessenger().registerOutgoingPluginChannel(this, CHANNEL);
        getServer().getMessenger().registerOutgoingPluginChannel(this, OFFER_CHANNEL);
        getServer().getMessenger().registerIncomingPluginChannel(this, CHANNEL, this);
        getServer().getPluginManager().registerEvents(this, this);
        getServer().getScheduler().runTaskTimer(this, this::tick, 20, 20);
        getSLF4JLogger().info("Checking players for '{}' {} ({})", settings.modpackName(), settings.modpackVersion(),
                settings.required() ? "required to join" : "not required");
    }

    /** A player is joining, not yet in the world: ask, and wait for the answer. */
    @EventHandler
    public void onConfigure(AsyncPlayerConnectionConfigureEvent event) {
        PlayerConfigurationConnection connection = event.getConnection();
        UUID id = connection.getProfile().getId();
        String name = connection.getProfile().getName();
        Consumer<String> kick = message -> connection.disconnect(Component.text(message));
        if (!gate.allow(connection.getClientAddress().getAddress().getHostAddress(), System.currentTimeMillis())) {
            if (settings.required()) {
                kick.accept("Too many joins from your address. Wait a minute and try again.");
            }
            return;
        }
        if (id == null || !connection.getListeningPluginChannels().contains(CHANNEL)) {
            judged(id, name, null, kick); // no Companion at all
            return;
        }
        CompletableFuture<byte[]> reply = new CompletableFuture<>();
        joining.put(id, reply);
        Checker.Verdict verdict = null;
        try {
            Challenge sent = checker.newChallenge();
            if (settings.downloadLink() != null && connection.getListeningPluginChannels().contains(OFFER_CHANNEL)) {
                // First, so the player's Companion can offer the update if the check finds them behind.
                Offer offer = new Offer(settings.modpackName(), settings.modpackVersion(), settings.downloadLink());
                connection.sendPluginMessage(this, OFFER_CHANNEL, frame(offer.encode()));
            }
            connection.sendPluginMessage(this, CHANNEL, frame(sent.encode()));
            verdict = checker.judge(sent, Answer.decode(unframe(reply.get(settings.timeoutSeconds(), TimeUnit.SECONDS))));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (TimeoutException | ExecutionException | BadInput e) {
            // no answer in time, or nonsense: judged as no answer
        } finally {
            joining.remove(id);
        }
        judged(id, name, verdict, kick);
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        Player player = event.getPlayer();
        String line = chatLines.remove(player.getUniqueId());
        if (line != null) {
            player.sendMessage(Component.text(line));
        }
        if (settings.required()) {
            rechecks.put(player.getUniqueId(), new Recheck(nextCheck(), null, 0));
        }
    }

    @EventHandler
    public void onQuit(PlayerQuitEvent event) {
        rechecks.remove(event.getPlayer().getUniqueId());
        chatLines.remove(event.getPlayer().getUniqueId());
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
        Recheck waiting = rechecks.get(player.getUniqueId());
        if (waiting == null || waiting.sent() == null || !player.isOnline()) {
            return; // not asked, or already answered
        }
        Checker.Verdict verdict;
        try {
            verdict = checker.judge(waiting.sent(), Answer.decode(unframe(message)));
        } catch (BadInput e) {
            verdict = null;
        }
        rechecks.put(player.getUniqueId(), new Recheck(nextCheck(), null, 0));
        judged(player.getUniqueId(), player.getName(), verdict, reason -> player.kick(Component.text(reason)));
    }

    /** Once a second: sends rechecks that are due, and judges the ones nobody answered in time. */
    private void tick() {
        int now = Bukkit.getCurrentTick();
        for (Player player : getServer().getOnlinePlayers()) {
            Recheck r = rechecks.get(player.getUniqueId());
            if (r == null) {
                continue;
            }
            if (r.sent() == null && now >= r.dueTick()) {
                Challenge challenge = checker.newChallenge();
                rechecks.put(player.getUniqueId(), new Recheck(r.dueTick(), challenge, now + settings.timeoutSeconds() * 20));
                player.sendPluginMessage(this, CHANNEL, frame(challenge.encode()));
            } else if (r.sent() != null && now >= r.deadlineTick()) {
                rechecks.put(player.getUniqueId(), new Recheck(nextCheck(), null, 0));
                judged(player.getUniqueId(), player.getName(), null, message -> player.kick(Component.text(message)));
            }
        }
    }

    /**
     * What happens after a check, like the mods' judged(). {@code verdict}
     * null means no answer. Writes one line to the server log, which the
     * app reads for the Players page: keep its shape.
     */
    private void judged(UUID id, String name, Checker.Verdict verdict, Consumer<String> kick) {
        String has = verdict == null || verdict.playerVersion().isEmpty()
                ? "-"
                : verdict.playerVersion().replaceAll("[^\\w.+-]", "?");
        boolean passed = verdict != null && verdict.passed();
        getLogger().info("Companion check: " + name + " " + (passed ? "passed" : "failed") + ", modpack " + has);
        if (passed) {
            return;
        }
        if (settings.required()) {
            kick.accept(verdict == null ? checker.noAnswerMessage() : verdict.message());
        } else if (settings.chatLineOn() && id != null) {
            chatLines.put(id, settings.chatLineText());
        }
    }

    private int nextCheck() {
        int min = settings.recheckMinMinutes();
        int minutes = min + random.nextInt(settings.recheckMaxMinutes() - min + 1);
        return Bukkit.getCurrentTick() + minutes * TICKS_PER_MINUTE;
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
