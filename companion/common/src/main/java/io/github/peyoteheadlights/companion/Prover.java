package io.github.peyoteheadlights.companion;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The player's side of the check: answers a challenge from what the game loaded. */
public final class Prover {
    private Prover() {}

    /**
     * Answers a challenge.
     *
     * @param c the server's challenge
     * @param loadedJars every jar file the game loaded, by its SHA-256 (lowercase hex)
     * @param packVersion the version from the modpack's players file ("" if none)
     */
    public static Answer answer(Challenge c, Map<String, Path> loadedJars, String packVersion) {
        HexFormat hex = HexFormat.of();
        Set<String> expected = new HashSet<>();
        List<Boolean> loaded = new ArrayList<>();
        for (byte[] hash : c.expected()) {
            String h = hex.formatHex(hash);
            expected.add(h);
            loaded.add(loadedJars.containsKey(h));
        }
        boolean extra = !expected.containsAll(loadedJars.keySet());
        List<byte[]> proofs = new ArrayList<>();
        for (Challenge.Spot s : c.spots()) {
            Path file = loadedJars.get(hex.formatHex(c.expected().get(s.fileIndex())));
            byte[] proof = new byte[32];
            if (file != null) {
                try {
                    proof = Hashes.proof(c.nonce(), file, s.offset(), s.length());
                } catch (IOException e) {
                    // leave it zero: the server will see it doesn't match
                }
            }
            proofs.add(proof);
        }
        String version = packVersion.length() > Answer.MAX_VERSION_BYTES ? "" : packVersion;
        return new Answer(c.nonce(), version, List.copyOf(loaded), extra, List.copyOf(proofs));
    }

    /** The modpack's name from the players file, like packVersion; "" for anything odd. */
    public static String packName(String playersFileJson) {
        try {
            JsonObject root = JsonParser.parseString(playersFileJson).getAsJsonObject();
            String name = root.getAsJsonObject("modpack").get("name").getAsString();
            return name.isBlank() || name.length() > 128 || name.chars().anyMatch(c -> c < 0x20 || c == 0x7f) ? "" : name;
        } catch (RuntimeException e) {
            return "";
        }
    }

    /**
     * Reads the modpack version from the players file the modpack carries
     * (config/commandblock-companion-client.json). Anything odd gives "".
     */
    public static String packVersion(String playersFileJson) {
        try {
            JsonObject root = JsonParser.parseString(playersFileJson).getAsJsonObject();
            if (root.get("format").getAsInt() != 1) {
                return "";
            }
            JsonElement v = root.getAsJsonObject("modpack").get("version");
            String version = v.getAsString();
            return version.matches("[\\w.+-]{1,32}") ? version : "";
        } catch (RuntimeException e) {
            return "";
        }
    }
}
