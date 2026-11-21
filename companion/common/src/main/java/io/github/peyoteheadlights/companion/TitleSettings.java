package io.github.peyoteheadlights.companion;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The title screen's settings, from the "title_screen" part of the players
 * file the modpack carries (config/commandblock-companion-client.json):
 * the join button's address and the yellow flavor text. Each is null when
 * the owner turned it off, and then Minecraft's own stays.
 *
 * <p>The file comes from the modpack, so it is read as strictly as a packet:
 * anything odd turns that part off, it never crashes the game.
 *
 * @param name the server's name, for the button's hover text
 * @param host the address to join, or null: no button
 * @param port the port (25565 when the address has none)
 * @param flavorText the yellow text, or null: Minecraft's own splashes
 */
public record TitleSettings(String name, String host, int port, String flavorText) {

    public static final TitleSettings OFF = new TitleSettings("", null, 25565, null);
    public static final int MAX_NAME = 64;
    public static final int MAX_FLAVOR = 60;
    /** A host name or IPv4 address, then maybe ":port". The app checks the same. */
    static final Pattern ADDRESS = Pattern.compile("([A-Za-z0-9][A-Za-z0-9.-]{0,252})(?::(\\d{1,5}))?");

    /** True for text that may be shown: no control characters and no § colour codes. */
    static boolean plain(String text, int max) {
        return !text.isEmpty() && text.length() <= max
                && text.chars().noneMatch(c -> c < 0x20 || c == 0x7f || c == '§');
    }

    /**
     * Reads the title screen settings.
     *
     * <p>The part looks like this ("address" and "flavor_text" may be null):
     * <pre>
     * "title_screen": {"name": "Survival", "address": "play.example.net:25570", "flavor_text": "Dragons!"}
     * </pre>
     *
     * <p>How it reads:
     * <ul>
     *   <li>Same try/catch as {@code Prover.packName}: any RuntimeException (no
     *       such part, wrong type) gives {@link #OFF}.</li>
     *   <li>A missing or JSON-null value: {@code e == null || e.isJsonNull()}.</li>
     *   <li>name: keep it only if {@link #plain}(name, MAX_NAME), else "".</li>
     *   <li>address: {@link #ADDRESS}.matcher(...).matches(); group(1) is the host,
     *       group(2) the port or null. A port outside 1..65535 turns the button off.</li>
     *   <li>flavor_text: keep it only if {@link #plain}(text, MAX_FLAVOR).</li>
     *   <li>One bad part never turns off the others.</li>
     * </ul>
     */
    public static TitleSettings parse(String playersFileJson) {
        JsonObject part;
        try {
            part = JsonParser.parseString(playersFileJson).getAsJsonObject().getAsJsonObject("title_screen");
            if (part == null) {
                return OFF;
            }
        } catch (RuntimeException e) {
            return OFF;
        }
        String name = text(part.get("name"));
        name = name != null && plain(name, MAX_NAME) ? name : "";
        String host = null;
        int port = 25565;
        String address = text(part.get("address"));
        if (address != null) {
            Matcher m = ADDRESS.matcher(address);
            if (m.matches()) {
                int p = m.group(2) == null ? 25565 : Integer.parseInt(m.group(2));
                if (p >= 1 && p <= 65535) {
                    host = m.group(1);
                    port = p;
                }
            }
        }
        String flavor = text(part.get("flavor_text"));
        flavor = flavor != null && plain(flavor, MAX_FLAVOR) ? flavor : null;
        return new TitleSettings(name, host, port, flavor);
    }

    /** A JSON string, or null for anything else. */
    private static String text(JsonElement e) {
        if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) {
            return null;
        }
        return e.getAsString();
    }

    /**
     * The second line of the button's hover text, from the ping.
     *
     * <p>null (no answer): "Can't reach it right now". Asleep: "Asleep, join to
     * start it" (joining wakes it, like the server list). Else "3 online"; the
     * max isn't shown.
     */
    public static String statusLine(Ping.Status status) {
        if (status == null) {
            return "Can't reach it right now";
        }
        return status.asleep() ? "Asleep, join to start it" : status.online() + " online";
    }

    /** "host" or "host:port", as Minecraft's server list writes it. */
    public String address() {
        return port == 25565 ? host : host + ":" + port;
    }
}
