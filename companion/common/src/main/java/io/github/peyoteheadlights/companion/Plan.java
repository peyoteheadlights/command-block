package io.github.peyoteheadlights.companion;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * What an update would change in a player's mods folder, worked out from
 * the modpack file (.mrpack) the server's link gave. Nothing is downloaded
 * or changed here: the player sees this list first and ticks what to do
 * (rule 1). The rules:
 * <ol>
 *   <li>always asked first (the list, then the player says yes)</li>
 *   <li>Modrinth and CurseForge files come only from those sites' own
 *       download servers and must match the modpack's SHA-512</li>
 *   <li>own jars (in the modpack itself) start unticked, and must be on
 *       the server's own hash list (from the last check)</li>
 *   <li>nothing goes in while the game runs (Install fills a waiting
 *       folder, Swap moves it in after the game closes)</li>
 *   <li>the old set is kept (Swap moves it aside, for "Go back")</li>
 * </ol>
 * Only the modpack's own files are ever replaced or removed: a player's
 * own mods are never touched.
 *
 * @param name the modpack's name, from its players file
 * @param version the modpack's version, from its players file
 * @param items what would change
 * @param skipped files that can't be put in, with why (shown to the player)
 * @param playersFile the new players file's text (written by Swap)
 */
public record Plan(String name, String version, List<Item> items, List<String> skipped, String playersFile) {

    public static final String PLAYERS_FILE = "client-overrides/config/commandblock-companion-client.json";
    static final int MAX_ENTRIES = 20_000;
    static final int MAX_INDEX_BYTES = 8 * 1024 * 1024;
    static final Set<String> MODRINTH_HOSTS = Set.of("cdn.modrinth.com");
    static final Set<String> CURSEFORGE_HOSTS = Set.of("edge.forgecdn.net", "mediafilez.forgecdn.net");

    public enum Source { MODRINTH, CURSEFORGE, OWN }

    public enum Change { ADD, REMOVE }

    /**
     * One file to add or remove.
     *
     * @param file its name in the mods folder ("sodium.jar")
     * @param url where a Modrinth or CurseForge file is downloaded from; null otherwise
     * @param sha512 what a downloaded file must hash to; null otherwise
     * @param zipEntry where an own jar is inside the modpack; null otherwise
     * @param ticked the list's starting tick: own jars start unticked
     */
    public record Item(String file, Change change, Source source, String url, byte[] sha512, String zipEntry, boolean ticked) {}

    /**
     * Reads the modpack and compares it with the mods folder.
     *
     * @param mrpack the downloaded modpack file
     * @param mods the instance's mods folder
     * @param serverHashes SHA-256 of every modpack jar, from the server's last check
     * @param oldPackFiles the current modpack's own files ("mods/x.jar"), from the players file now in place
     */
    public static Plan read(Path mrpack, Path mods, Collection<byte[]> serverHashes, List<String> oldPackFiles)
            throws IOException, BadInput {
        HexFormat hex = HexFormat.of();
        Set<String> onServerList = new HashSet<>();
        serverHashes.forEach(h -> onServerList.add(hex.formatHex(h)));
        try (ZipFile zip = new ZipFile(mrpack.toFile())) {
            if (zip.size() > MAX_ENTRIES) {
                throw new BadInput("too many files in the modpack");
            }
            String playersText = text(zip, PLAYERS_FILE, Settings.MAX_SETTINGS_BYTES);
            JsonObject players = object(playersText);
            String name = Prover.packName(playersText);
            String version = Prover.packVersion(playersText);
            if (name.isEmpty() || version.isEmpty()) {
                throw new BadInput("the modpack has no Companion players file");
            }
            List<String> newPackFiles = packFiles(players);

            List<Item> items = new ArrayList<>();
            List<String> skipped = new ArrayList<>();
            Map<String, Boolean> wanted = new LinkedHashMap<>(); // file -> already in place
            JsonObject index = object(text(zip, "modrinth.index.json", MAX_INDEX_BYTES));
            for (JsonElement e : array(index, "files")) {
                JsonObject f = e.getAsJsonObject();
                String path = f.get("path").getAsString();
                if (!Settings.MOD_PATH.matcher(path).matches()) {
                    continue; // resource packs, shaders and the rest: not this part's business
                }
                if (f.has("env") && "unsupported".equals(f.getAsJsonObject("env").get("client").getAsString())) {
                    continue; // server only
                }
                String file = path.substring("mods/".length());
                byte[] sha512 = hex.parseHex(f.getAsJsonObject("hashes").get("sha512").getAsString().toLowerCase());
                if (sha512.length != 64) {
                    throw new BadInput("bad sha512");
                }
                String url = null;
                Source source = null;
                for (JsonElement d : array(f, "downloads")) {
                    source = site(d.getAsString());
                    if (source != null) {
                        url = d.getAsString();
                        break;
                    }
                }
                if (source == null) {
                    skipped.add(file + ": not from Modrinth or CurseForge's own download sites");
                    continue;
                }
                boolean there = matches(mods.resolve(file), "SHA-512", sha512);
                wanted.put(file, there);
                if (!there) {
                    items.add(new Item(file, Change.ADD, source, url, sha512, null, true));
                }
            }
            for (String folder : List.of("overrides/mods/", "client-overrides/mods/")) {
                for (ZipEntry entry : zip.stream().filter(z -> z.getName().startsWith(folder)).toList()) {
                    String path = "mods/" + entry.getName().substring(folder.length());
                    if (entry.isDirectory() || !Settings.MOD_PATH.matcher(path).matches()) {
                        continue;
                    }
                    String file = path.substring("mods/".length());
                    byte[] sha256 = entryHash(zip, entry);
                    if (!onServerList.contains(hex.formatHex(sha256))) {
                        skipped.add(file + ": not on the server's own list, so it may have been changed on the way");
                        continue;
                    }
                    boolean there = matches(mods.resolve(file), "SHA-256", sha256);
                    wanted.put(file, there);
                    if (!there) {
                        items.add(new Item(file, Change.ADD, Source.OWN, null, null, entry.getName(), false));
                    }
                }
            }
            // A player's own mod with the same name as a modpack file is never replaced.
            Set<String> ours = new HashSet<>(oldPackFiles);
            ours.addAll(newPackFiles);
            items.removeIf(i -> {
                boolean theirs = Files.exists(mods.resolve(i.file())) && !ours.contains("mods/" + i.file());
                if (theirs) {
                    skipped.add(i.file() + ": you have your own file with that name, so it was left alone");
                }
                return theirs;
            });
            for (String old : oldPackFiles) {
                String file = old.substring("mods/".length());
                if (!wanted.containsKey(file) && Files.isRegularFile(mods.resolve(file))) {
                    items.add(new Item(file, Change.REMOVE, null, null, null, null, true));
                }
            }
            return new Plan(name, version, List.copyOf(items), List.copyOf(skipped), playersText);
        } catch (IllegalArgumentException | IllegalStateException | NullPointerException | UnsupportedOperationException e) {
            throw new BadInput("the modpack file doesn't make sense"); // Gson and HexFormat throw these
        }
    }

    /** Which site a download link is from, by its host; null for anything else (or not https). */
    public static Source site(String link) {
        try {
            URI uri = new URI(link);
            if (!"https".equals(uri.getScheme()) || uri.getUserInfo() != null || uri.getPort() != -1) {
                return null;
            }
            if (MODRINTH_HOSTS.contains(uri.getHost())) {
                return Source.MODRINTH;
            }
            return CURSEFORGE_HOSTS.contains(uri.getHost()) ? Source.CURSEFORGE : null;
        } catch (URISyntaxException e) {
            return null;
        }
    }

    /** The modpack's own files ("mods/x.jar") from a players file's text; empty for anything odd. */
    public static List<String> packFiles(String playersFileJson) {
        try {
            return packFiles(object(playersFileJson));
        } catch (BadInput | RuntimeException e) {
            return List.of();
        }
    }

    private static List<String> packFiles(JsonObject players) throws BadInput {
        List<String> files = new ArrayList<>();
        if (!players.has("files")) {
            return files; // made before part 4: nothing known, so nothing is removed
        }
        for (JsonElement e : array(players, "files")) {
            String path = e.getAsString();
            if (!Settings.MOD_PATH.matcher(path).matches()) {
                throw new BadInput("bad file in the players file");
            }
            files.add(path);
        }
        if (files.size() > Settings.MAX_FILES) {
            throw new BadInput("too many files");
        }
        return files;
    }

    private static String text(ZipFile zip, String name, int max) throws IOException, BadInput {
        ZipEntry entry = zip.getEntry(name);
        if (entry == null) {
            throw new BadInput("the modpack has no " + name);
        }
        try (InputStream in = zip.getInputStream(entry)) {
            byte[] bytes = in.readNBytes(max + 1);
            if (bytes.length > max) {
                throw new BadInput(name + " is too big");
            }
            return new String(bytes, StandardCharsets.UTF_8);
        }
    }

    private static JsonObject object(String json) throws BadInput {
        JsonElement e = JsonParser.parseString(json);
        if (!e.isJsonObject()) {
            throw new BadInput("not an object");
        }
        return e.getAsJsonObject();
    }

    private static JsonArray array(JsonObject o, String key) throws BadInput {
        JsonElement e = o.get(key);
        if (e == null || !e.isJsonArray() || e.getAsJsonArray().size() > Settings.MAX_FILES) {
            throw new BadInput("bad " + key);
        }
        return e.getAsJsonArray();
    }

    private static byte[] entryHash(ZipFile zip, ZipEntry entry) throws IOException, BadInput {
        MessageDigest d = Hashes.sha256();
        long total = 0;
        try (InputStream in = zip.getInputStream(entry)) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                total += n;
                if (total > Settings.MAX_FILE_BYTES) {
                    throw new BadInput("a file in the modpack is too big");
                }
                d.update(buf, 0, n);
            }
        }
        return d.digest();
    }

    /** True when the file is there and hashes to want (a missing or unreadable file is just false). */
    static boolean matches(Path file, String algorithm, byte[] want) {
        if (!Files.isRegularFile(file)) {
            return false;
        }
        try (InputStream in = Files.newInputStream(file)) {
            MessageDigest d = MessageDigest.getInstance(algorithm);
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                d.update(buf, 0, n);
            }
            return MessageDigest.isEqual(d.digest(), want);
        } catch (IOException | NoSuchAlgorithmException e) {
            return false;
        }
    }
}
