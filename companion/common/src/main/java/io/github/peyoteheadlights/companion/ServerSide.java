package io.github.peyoteheadlights.companion;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;
import java.util.function.LongSupplier;
import java.util.function.Predicate;

/**
 * The server's half of the check, shared by the Fabric, Forge and NeoForge
 * mods and the Paper plugin. They only connect it to their own events:
 * a player starts joining, joins, answers, leaves, and a timer about once
 * a second. Paper calls the joining part from its own joining threads, so
 * the maps are thread-safe.
 */
public final class ServerSide {

    /**
     * A player who is in the world, as the loader sees them.
     *
     * @param send sends a challenge's bytes on commandblock:check
     * @param kick sends them away with this message
     */
    public record Online(UUID id, String name, Consumer<byte[]> send, Consumer<String> kick) {}

    private record Recheck(long dueMillis, Challenge sent, long deadlineMillis) {}

    private final Settings settings;
    private final Checker checker;
    private final Gate gate;
    private final SecureRandom random;
    private final Consumer<String> log;
    private final LongSupplier clock;
    /** True for Bedrock players (Geyser and Floodgate): never checked. */
    private final Predicate<UUID> bedrock;
    /** Players to show the chat line once they're in. */
    private final Map<UUID, String> chatLines = new ConcurrentHashMap<>();
    /** Required modpacks: when each player's next check is, and the one waiting for an answer. */
    private final Map<UUID, Recheck> rechecks = new ConcurrentHashMap<>();

    private ServerSide(Settings settings, Path serverDir, Consumer<String> log, LongSupplier clock,
            Predicate<UUID> bedrock) {
        this.settings = settings;
        this.random = new SecureRandom();
        this.checker = Checker.create(settings, serverDir, random);
        this.gate = new Gate(settings.handshakesPerIpPerMinute());
        this.log = log;
        this.clock = clock;
        this.bedrock = bedrock;
    }

    /**
     * Reads the settings file the app writes. Missing or bad: null, and a
     * log line says why (the Companion then does nothing).
     */
    public static ServerSide load(Path settingsFile, Path serverDir, Consumer<String> info, Consumer<String> warn) {
        return load(settingsFile, serverDir, info, warn, System::currentTimeMillis,
                Bedrock.find(Bedrock.FLOODGATE_API));
    }

    static ServerSide load(Path settingsFile, Path serverDir, Consumer<String> info, Consumer<String> warn,
            LongSupplier clock, Predicate<UUID> bedrock) {
        String name = settingsFile.getFileName().toString();
        Settings settings;
        try {
            if (Files.size(settingsFile) > Settings.MAX_SETTINGS_BYTES) {
                throw new BadInput("settings file too big");
            }
            settings = Settings.parse(Files.readString(settingsFile, StandardCharsets.UTF_8));
        } catch (NoSuchFileException e) {
            info.accept("No " + name + " yet: make a modpack in Command Block to turn on the check");
            return null;
        } catch (IOException | BadInput e) {
            warn.accept("Not checking players: " + name + " can't be used (" + e.getMessage() + ")");
            return null;
        }
        ServerSide side = new ServerSide(settings, serverDir, info, clock, bedrock);
        info.accept("Checking players for '" + settings.modpackName() + "' " + settings.modpackVersion()
                + " (" + (settings.required() ? "required to join" : "not required") + ")");
        return side;
    }

    /**
     * A player starts joining, from this address. False when they aren't
     * checked: Bedrock players never are (they get in, with no chat line),
     * and with too many joins from that address a required modpack sends
     * them away, otherwise they get in unchecked.
     */
    public boolean mayCheck(UUID id, String name, String ip, Consumer<String> kick) {
        if (bedrock.test(id)) {
            log.accept("Companion check: " + name + " skipped, Bedrock player");
            return false;
        }
        if (gate.allow(ip, clock.getAsLong())) {
            return true;
        }
        if (settings.required()) {
            kick.accept("Too many joins from your address. Wait a minute and try again.");
        }
        return false;
    }

    /** A fresh challenge for a player who is joining. */
    public Challenge newChallenge() {
        return checker.newChallenge();
    }

    public int timeoutSeconds() {
        return settings.timeoutSeconds();
    }

    /** The update offer's bytes (send it before the challenge), or null when the download link is off. */
    public byte[] offer() {
        return settings.downloadLink() == null
                ? null
                : new Offer(settings.modpackName(), settings.modpackVersion(), settings.downloadLink()).encode();
    }

    /**
     * What happens after a check. {@code answer} null means no answer (no
     * Companion, or too slow); an answer that doesn't parse counts the
     * same. Writes the one log line the app reads for the Players page
     * (PROTOCOL.md: keep its shape). Returns false when the player was sent
     * away.
     */
    public boolean judged(UUID id, String name, Challenge sent, byte[] answer, Consumer<String> kick) {
        Checker.Verdict verdict = null;
        if (sent != null && answer != null) {
            try {
                verdict = checker.judge(sent, Answer.decode(answer));
            } catch (BadInput e) {
                // nonsense: judged as no answer
            }
        }
        String has = verdict == null || verdict.playerVersion().isEmpty()
                ? "-"
                : Settings.versionOk(verdict.playerVersion()) ? verdict.playerVersion() : "?";
        boolean passed = verdict != null && verdict.passed();
        log.accept("Companion check: " + name + " " + (passed ? "passed" : "failed") + ", modpack " + has);
        if (passed) {
            return true;
        }
        if (settings.required()) {
            kick.accept(verdict == null ? checker.noAnswerMessage() : verdict.message());
            return false;
        }
        if (settings.chatLineOn() && id != null) {
            chatLines.put(id, settings.chatLineText());
        }
        return true;
    }

    /** The player is in the world: returns their chat line (or null), and plans their first recheck. */
    public String joined(UUID id) {
        if (settings.required() && !bedrock.test(id)) {
            rechecks.put(id, new Recheck(nextCheck(), null, 0));
        }
        return chatLines.remove(id);
    }

    public void left(UUID id) {
        rechecks.remove(id);
        chatLines.remove(id);
    }

    /** A player in the world answered. Only an answer to the recheck they were sent counts. */
    public void answered(Online player, byte[] answer) {
        Recheck waiting = rechecks.get(player.id());
        if (waiting == null || waiting.sent() == null) {
            return; // not asked, or already answered
        }
        rechecks.put(player.id(), new Recheck(nextCheck(), null, 0));
        judged(player.id(), player.name(), waiting.sent(), answer, player.kick());
    }

    /**
     * Call about once a second with everyone in the world: sends the
     * rechecks that are due, and judges the ones nobody answered in time.
     * Works on a copy of the list, so a kick can't change it underneath.
     */
    public void tick(Collection<Online> players) {
        if (rechecks.isEmpty()) {
            return;
        }
        long now = clock.getAsLong();
        for (Online p : List.copyOf(players)) {
            Recheck r = rechecks.get(p.id());
            if (r == null) {
                continue;
            }
            if (r.sent() == null && now >= r.dueMillis()) {
                Challenge challenge = checker.newChallenge();
                rechecks.put(p.id(), new Recheck(r.dueMillis(), challenge, now + settings.timeoutSeconds() * 1000L));
                p.send().accept(challenge.encode());
            } else if (r.sent() != null && now >= r.deadlineMillis()) {
                rechecks.put(p.id(), new Recheck(nextCheck(), null, 0));
                judged(p.id(), p.name(), null, null, p.kick());
            }
        }
    }

    private long nextCheck() {
        int min = settings.recheckMinMinutes();
        int minutes = min + random.nextInt(settings.recheckMaxMinutes() - min + 1);
        return clock.getAsLong() + minutes * 60_000L;
    }
}
