package io.github.peyoteheadlights.companion;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * The server's side of the check: makes challenges and judges answers.
 * One Checker per settings file; the loader module calls it.
 */
public final class Checker {

    /**
     * What came out of one check.
     *
     * @param passed the player has every modpack file loaded and every spot proof was right
     * @param message what to tell the player when they didn't pass (null when they did)
     * @param missing names of the modpack files that weren't loaded
     * @param extraMods the player said they have mods beyond the modpack (that's fine)
     * @param playerVersion the modpack version the player said they have
     */
    public record Verdict(boolean passed, String message, List<String> missing, boolean extraMods, String playerVersion) {}

    private static final int NAMES_SHOWN = 3;

    private final Settings settings;
    private final Path serverDir;
    private final SecureRandom random;
    private final List<Integer> spotFiles; // indexes of files whose server copy matches its hash

    private Checker(Settings settings, Path serverDir, SecureRandom random, List<Integer> spotFiles) {
        this.settings = settings;
        this.serverDir = serverDir;
        this.random = random;
        this.spotFiles = spotFiles;
    }

    /**
     * Makes a Checker. Hashes the server's own copy of each file once, and
     * only uses copies that still match the modpack for spot checks (an owner
     * may have changed a mod since making the modpack). It reads every file,
     * so a big modpack makes the server start a little slower.
     */
    public static Checker create(Settings settings, Path serverDir, SecureRandom random) {
        List<Integer> usable = new ArrayList<>();
        List<Settings.PackFile> files = settings.files();
        for (int i = 0; i < files.size(); i++) {
            Path copy = serverCopy(serverDir, files.get(i));
            try {
                if (copy != null && MessageDigest.isEqual(Hashes.ofFile(copy), files.get(i).sha256())) {
                    usable.add(i);
                }
            } catch (IOException e) {
                // no spot checks on this file
            }
        }
        return new Checker(settings, serverDir, random, List.copyOf(usable));
    }

    /** The server's copy of a file, or null if it has none or the path leads outside the server folder. */
    static Path serverCopy(Path serverDir, Settings.PackFile file) {
        if (file.onServer() == null) {
            return null;
        }
        Path base = serverDir.toAbsolutePath().normalize();
        Path copy = base.resolve(file.onServer()).normalize();
        if (!copy.startsWith(base) || Files.isSymbolicLink(copy) || !Files.isRegularFile(copy)) {
            return null;
        }
        return copy;
    }

    /** A fresh challenge: new nonce, the modpack's hashes, random spots. */
    public Challenge newChallenge() {
        byte[] nonce = new byte[Challenge.NONCE_BYTES];
        random.nextBytes(nonce);
        List<byte[]> expected = new ArrayList<>();
        for (Settings.PackFile f : settings.files()) {
            expected.add(f.sha256());
        }
        List<Integer> pool = new ArrayList<>(spotFiles);
        List<Challenge.Spot> spots = new ArrayList<>();
        while (spots.size() < settings.spotChecks() && !pool.isEmpty()) {
            int index = pool.remove(random.nextInt(pool.size()));
            long size = settings.files().get(index).size();
            int length = (int) Math.min(Challenge.MAX_SPOT_BYTES, size);
            if (length < 1) {
                continue;
            }
            long offset = size == length ? 0 : (long) (random.nextDouble() * (size - length + 1));
            spots.add(new Challenge.Spot(index, offset, length));
        }
        return new Challenge(nonce, List.copyOf(expected), List.copyOf(spots));
    }

    /** Judges an answer to a challenge this Checker made. */
    public Verdict judge(Challenge sent, Answer got) {
        if (!MessageDigest.isEqual(sent.nonce(), got.nonce())
                || got.loaded().size() != sent.expected().size()
                || got.proofs().size() != sent.spots().size()) {
            return fail(List.of(), got, "Your game's answer didn't fit this server's check. Restart the game and join again.");
        }
        List<String> missing = new ArrayList<>();
        for (int i = 0; i < got.loaded().size(); i++) {
            if (!got.loaded().get(i)) {
                missing.add(settings.files().get(i).name());
            }
        }
        boolean proofsOk = true;
        for (int i = 0; i < sent.spots().size(); i++) {
            Challenge.Spot s = sent.spots().get(i);
            Path copy = serverCopy(serverDir, settings.files().get(s.fileIndex()));
            try {
                byte[] want = Hashes.proof(sent.nonce(), copy, s.offset(), s.length());
                proofsOk &= MessageDigest.isEqual(want, got.proofs().get(i));
            } catch (IOException | NullPointerException e) {
                // the server's copy went away: don't hold it against the player
            }
        }
        if (missing.isEmpty() && proofsOk) {
            return new Verdict(true, null, List.of(), got.extraMods(), got.packVersion());
        }
        return fail(missing, got, needs(missing, got.packVersion()));
    }

    /** For a player whose game never answered (no Companion, or too slow). */
    public String noAnswerMessage() {
        return "This server needs '" + settings.modpackName() + "' " + settings.modpackVersion()
                + " with the Command Block Companion mod." + getIt();
    }

    private Verdict fail(List<String> missing, Answer got, String message) {
        return new Verdict(false, message, List.copyOf(missing), got.extraMods(), got.packVersion());
    }

    private String needs(List<String> missing, String playerVersion) {
        String start = "This server needs '" + settings.modpackName() + "' " + settings.modpackVersion() + ".";
        if (!playerVersion.isEmpty() && !playerVersion.equals(settings.modpackVersion())) {
            return start + " You have " + playerVersion + "." + getIt();
        }
        if (missing.isEmpty()) {
            return start + " Some of your modpack's files are different. Get it again." + getIt();
        }
        String names = String.join(", ", missing.subList(0, Math.min(NAMES_SHOWN, missing.size())));
        int more = missing.size() - NAMES_SHOWN;
        return start + " Missing or different: " + names + (more > 0 ? " and " + more + " more" : "") + "." + getIt();
    }

    private String getIt() {
        return settings.downloadLink() == null ? "" : " Get it here: " + settings.downloadLink();
    }

    // For tests: which files spot checks may use.
    List<Integer> spotFiles() {
        return spotFiles;
    }

    @Override
    public String toString() {
        return "Checker" + Arrays.toString(spotFiles.toArray());
    }
}
