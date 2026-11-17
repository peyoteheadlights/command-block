package io.github.peyoteheadlights.companion;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The server's settings file, written by the agent each time the owner
 * makes a modpack (agent/companion.py). See PROTOCOL.md for every field.
 *
 * <p>Like a Java record you'd write for a REST answer, but parse() is
 * strict: an unknown field, a wrong type or a value out of range means
 * the whole file is refused.
 */
public record Settings(
        boolean required,
        String modpackName,
        String modpackVersion,
        List<PackFile> files,
        String downloadLink,
        boolean chatLineOn,
        String chatLineText,
        int timeoutSeconds,
        int recheckMinMinutes,
        int recheckMaxMinutes,
        int spotChecks,
        int handshakesPerIpPerMinute) {

    public static final int FORMAT = 1;
    public static final int MAX_FILES = 4000;
    public static final long MAX_FILE_BYTES = 1L << 30; // 1 GiB
    public static final int MAX_SETTINGS_BYTES = 2 * 1024 * 1024;

    /** One mod jar players load from the modpack. */
    public record PackFile(String path, String name, byte[] sha256, long size, String onServer) {}

    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    // One file name: no folders, no characters Windows refuses in names.
    private static final String NAME = "[^/\\\\:*?\"<>|\\x00-\\x1f]{1,255}";
    static final Pattern MOD_PATH = Pattern.compile("mods/" + NAME + "\\.jar");
    // The server's copy, from the server folder: up to 4 folders deep, never "..".
    private static final Pattern ON_SERVER = Pattern.compile("(" + NAME + "/){0,3}" + NAME + "\\.jar");
    // English letters and digits only: the app, the server and every player read it the same way.
    private static final Pattern VERSION = Pattern.compile("[A-Za-z0-9_.+-]{1,32}");
    private static final Pattern LINK = Pattern.compile("https?://[\\x21-\\x7e]{1,500}");

    /** Reads the settings file's text. Throws BadInput for anything we don't accept. */
    public static Settings parse(String json) throws BadInput {
        if (json == null || json.length() > MAX_SETTINGS_BYTES) {
            throw new BadInput("settings file too big");
        }
        JsonObject root;
        try {
            JsonElement e = JsonParser.parseString(json);
            if (!e.isJsonObject()) {
                throw new BadInput("not an object");
            }
            root = e.getAsJsonObject();
        } catch (RuntimeException e) {
            throw new BadInput("not JSON");
        }
        onlyKeys(root, Set.of("format", "required", "modpack", "files", "download_link", "chat_line", "check"));
        if (integer(root, "format", 1, 1000) != FORMAT) {
            throw new BadInput("unknown format");
        }
        JsonObject modpack = object(root, "modpack");
        onlyKeys(modpack, Set.of("name", "version"));
        String name = text(modpack, "name", 128);
        String version = text(modpack, "version", 32);
        if (name.isBlank() || !versionOk(version)) {
            throw new BadInput("bad modpack name or version");
        }

        JsonElement fileList = root.get("files");
        if (fileList == null || !fileList.isJsonArray() || fileList.getAsJsonArray().size() > MAX_FILES) {
            throw new BadInput("bad files");
        }
        List<PackFile> files = new ArrayList<>();
        for (JsonElement f : fileList.getAsJsonArray()) {
            if (!f.isJsonObject()) {
                throw new BadInput("bad file");
            }
            JsonObject o = f.getAsJsonObject();
            onlyKeys(o, Set.of("path", "name", "sha256", "size", "on_server"));
            String path = text(o, "path", 300);
            String sha = text(o, "sha256", 64);
            if (!MOD_PATH.matcher(path).matches() || !SHA256.matcher(sha).matches()) {
                throw new BadInput("bad file path or hash");
            }
            String onServer = nullableText(o, "on_server", 1100);
            if (onServer != null && (!ON_SERVER.matcher(onServer).matches() || List.of(onServer.split("/")).contains(".."))) {
                throw new BadInput("bad on_server");
            }
            files.add(new PackFile(
                    path,
                    text(o, "name", 200),
                    HexFormat.of().parseHex(sha),
                    longInteger(o, "size", 0, MAX_FILE_BYTES),
                    onServer));
        }

        String link = nullableText(root, "download_link", 512);
        if (link != null && !LINK.matcher(link).matches()) {
            throw new BadInput("bad download link");
        }
        JsonObject chat = object(root, "chat_line");
        onlyKeys(chat, Set.of("on", "text"));
        JsonObject check = object(root, "check");
        onlyKeys(check, Set.of("timeout_seconds", "recheck_minutes", "spot_checks", "handshakes_per_ip_per_minute"));
        JsonElement recheck = check.get("recheck_minutes");
        if (recheck == null || !recheck.isJsonArray() || recheck.getAsJsonArray().size() != 2) {
            throw new BadInput("bad recheck_minutes");
        }
        int min = asInt(recheck.getAsJsonArray().get(0), 1, 1440);
        int max = asInt(recheck.getAsJsonArray().get(1), 1, 1440);
        if (min > max) {
            throw new BadInput("recheck min above max");
        }
        return new Settings(
                bool(root, "required"),
                name,
                version,
                List.copyOf(files),
                link,
                bool(chat, "on"),
                text(chat, "text", 1024),
                integer(check, "timeout_seconds", 1, 60),
                min,
                max,
                integer(check, "spot_checks", 0, Challenge.MAX_SPOTS),
                integer(check, "handshakes_per_ip_per_minute", 1, 60));
    }

    /** The one rule for a modpack version, used everywhere: up to 32 of A-Z a-z 0-9 _ . + - */
    public static boolean versionOk(String version) {
        return version != null && VERSION.matcher(version).matches();
    }

    // ---------------------------------------------------------------- small helpers

    private static void onlyKeys(JsonObject o, Set<String> allowed) throws BadInput {
        if (!allowed.equals(o.keySet())) {
            throw new BadInput("fields must be exactly " + allowed);
        }
    }

    private static JsonObject object(JsonObject o, String key) throws BadInput {
        JsonElement e = o.get(key);
        if (e == null || !e.isJsonObject()) {
            throw new BadInput(key + " must be an object");
        }
        return e.getAsJsonObject();
    }

    private static boolean bool(JsonObject o, String key) throws BadInput {
        JsonElement e = o.get(key);
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isBoolean()) {
            throw new BadInput(key + " must be true or false");
        }
        return e.getAsBoolean();
    }

    private static String text(JsonObject o, String key, int max) throws BadInput {
        String s = nullableText(o, key, max);
        if (s == null) {
            throw new BadInput(key + " is missing");
        }
        return s;
    }

    private static String nullableText(JsonObject o, String key, int max) throws BadInput {
        JsonElement e = o.get(key);
        if (e == null || e.isJsonNull()) {
            return null;
        }
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString() || e.getAsString().length() > max) {
            throw new BadInput(key + " must be text up to " + max);
        }
        return e.getAsString();
    }

    private static int integer(JsonObject o, String key, int min, int max) throws BadInput {
        return (int) longInteger(o, key, min, max);
    }

    private static long longInteger(JsonObject o, String key, long min, long max) throws BadInput {
        JsonElement e = o.get(key);
        if (e == null) {
            throw new BadInput(key + " is missing");
        }
        return asLong(e, min, max);
    }

    private static int asInt(JsonElement e, int min, int max) throws BadInput {
        return (int) asLong(e, min, max);
    }

    private static long asLong(JsonElement e, long min, long max) throws BadInput {
        if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) {
            throw new BadInput("must be a whole number");
        }
        try {
            long v = e.getAsBigDecimal().longValueExact();
            if (v < min || v > max) {
                throw new BadInput("out of range");
            }
            return v;
        } catch (ArithmeticException ex) {
            throw new BadInput("must be a whole number");
        }
    }
}
