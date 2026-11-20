package io.github.peyoteheadlights.companion;

import java.lang.reflect.Method;
import java.util.UUID;
import java.util.function.Predicate;

/**
 * Bedrock players who join through Geyser and Floodgate (crossplay). Their
 * game can't run Java mods, so they can never answer the check. Floodgate
 * knows who they are (Geyser vouches for them with a key, so a Java player
 * can't pretend), and we ask its API by name so the Companion doesn't need
 * Floodgate to load. No Floodgate, or it can't answer: nobody is a Bedrock
 * player and everyone is checked as before.
 */
final class Bedrock {
    static final String FLOODGATE_API = "org.geysermc.floodgate.api.FloodgateApi";

    private Bedrock() {}

    /** Floodgate's answer for a player, from the API class with this name. */
    static Predicate<UUID> find(String apiClass) {
        Method getInstance;
        Method isFloodgatePlayer;
        try {
            Class<?> api = Class.forName(apiClass);
            getInstance = api.getMethod("getInstance");
            isFloodgatePlayer = api.getMethod("isFloodgatePlayer", UUID.class);
        } catch (ReflectiveOperationException | LinkageError e) {
            return id -> false;
        }
        return id -> {
            if (id == null) {
                return false;
            }
            try {
                // Asked each time: Floodgate may start after the Companion.
                Object floodgate = getInstance.invoke(null);
                return floodgate != null && Boolean.TRUE.equals(isFloodgatePlayer.invoke(floodgate, id));
            } catch (ReflectiveOperationException | RuntimeException | LinkageError e) {
                return false;
            }
        };
    }
}
