package io.github.peyoteheadlights.companion.waterdog;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;
import java.nio.file.Path;

/**
 * Who may join, from the Bedrock server's own allowlist.json. Plain Java (no
 * WaterdogPE), so the tests can call it.
 *
 * <p>The plugin's config.json is written by the agent (agent/waterdog.py):
 * {@code {"format": 1, "allow_list": "<the server's allowlist.json>"}}, or
 * {@code "allow_list": null} when the owner keeps the server open.
 */
final class AllowList {
    static final int FORMAT = 1;
    static final String NOT_LISTED = "You are not on this server's allow list. Ask its owner to add you.";
    static final String NOT_SIGNED_IN = "Sign in to Xbox to join this server.";
    static final String CANT_READ = "This server's allow list can't be read right now. Tell its owner.";

    private AllowList() {
    }

    /** The allowlist.json to check, or null when the server is open to everyone. */
    static Path fromConfig(String json) {
        try {
            JsonObject config = JsonParser.parseString(json).getAsJsonObject();
            if (!config.has("format") || config.get("format").getAsInt() != FORMAT) {
                throw new IllegalArgumentException("config.json has another format than " + FORMAT);
            }
            JsonElement list = config.get("allow_list");
            return list == null || list.isJsonNull() ? null : Path.of(list.getAsString());
        } catch (JsonParseException | IllegalStateException | UnsupportedOperationException e) {
            throw new IllegalArgumentException("config.json can't be read: " + e.getMessage(), e);
        }
    }

    /**
     * Why this player can't join, or null when they can. {@code list} is the
     * allowlist.json's text, or null when the server is open to everyone.
     * A list that can't be read turns everyone away: never open by mistake.
     */
    static String refusal(String list, String name, String xuid, boolean xboxSignedIn) {
        if (!xboxSignedIn) {
            return NOT_SIGNED_IN;
        }
        if (list == null) {
            return null;
        }
        try {
            JsonArray players = JsonParser.parseString(list).getAsJsonArray();
            for (JsonElement entry : players) {
                JsonObject player = entry.getAsJsonObject();
                if (same(player, "name", name) || (!xuid.isEmpty() && same(player, "xuid", xuid))) {
                    return null;
                }
            }
            return NOT_LISTED;
        } catch (JsonParseException | IllegalStateException | UnsupportedOperationException e) {
            return CANT_READ;
        }
    }

    private static boolean same(JsonObject player, String key, String value) {
        JsonElement found = player.get(key);
        return found != null && found.isJsonPrimitive() && found.getAsString().equalsIgnoreCase(value);
    }
}
