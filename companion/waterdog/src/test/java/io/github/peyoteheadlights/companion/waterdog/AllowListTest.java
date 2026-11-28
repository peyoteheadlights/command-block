package io.github.peyoteheadlights.companion.waterdog;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class AllowListTest {
    private static final String LIST =
            "[{\"ignoresPlayerLimit\": false, \"name\": \"Steve Two\", \"xuid\": \"2535400000000001\"},"
                    + " {\"name\": \"alex\"}]";

    @Test
    void aNameOnTheListJoins() {
        assertNull(AllowList.refusal(LIST, "Steve Two", "", true));
        assertNull(AllowList.refusal(LIST, "ALEX", "", true)); // Xbox names ignore case
    }

    @Test
    void aRenamedPlayerStillJoinsByXuid() {
        assertNull(AllowList.refusal(LIST, "New Name", "2535400000000001", true));
    }

    @Test
    void anyoneElseIsTurnedAway() {
        assertEquals(AllowList.NOT_LISTED, AllowList.refusal(LIST, "Stranger", "2535400000000009", true));
        assertEquals(AllowList.NOT_LISTED, AllowList.refusal("[]", "alex", "", true));
    }

    @Test
    void anEmptyXuidNeverMatchesAnEmptyOne() {
        String list = "[{\"name\": \"someone\", \"xuid\": \"\"}]";
        assertEquals(AllowList.NOT_LISTED, AllowList.refusal(list, "Stranger", "", true));
    }

    @Test
    void anOpenServerLetsSignedInPlayersIn() {
        assertNull(AllowList.refusal(null, "Anyone", "", true));
    }

    @Test
    void notSignedInIsAlwaysTurnedAway() {
        assertEquals(AllowList.NOT_SIGNED_IN, AllowList.refusal(null, "Anyone", "", false));
        assertEquals(AllowList.NOT_SIGNED_IN, AllowList.refusal(LIST, "alex", "", false));
    }

    @Test
    void aBrokenListTurnsEveryoneAway() {
        assertEquals(AllowList.CANT_READ, AllowList.refusal("not a list", "alex", "", true));
        assertEquals(AllowList.CANT_READ, AllowList.refusal("{\"name\": \"alex\"}", "alex", "", true));
        assertEquals(AllowList.CANT_READ, AllowList.refusal("[\"alex\"]", "alex", "", true));
    }

    @Test
    void configSaysWhichListOrNone() {
        assertEquals(Path.of("C:/mc/allowlist.json"),
                AllowList.fromConfig("{\"format\": 1, \"allow_list\": \"C:/mc/allowlist.json\"}"));
        assertNull(AllowList.fromConfig("{\"format\": 1, \"allow_list\": null}"));
        assertNull(AllowList.fromConfig("{\"format\": 1}"));
    }

    @Test
    void anotherConfigIsRefused() {
        assertThrows(IllegalArgumentException.class, () -> AllowList.fromConfig("{\"format\": 2}"));
        assertThrows(IllegalArgumentException.class, () -> AllowList.fromConfig("[]"));
        assertThrows(IllegalArgumentException.class, () -> AllowList.fromConfig("{"));
    }

    @Test
    void theReadyLineIsTheOneTheAgentWaitsFor() {
        // agent/waterdog.py PLUGIN_READY: without it WaterdogPE is stopped.
        assertEquals("Command Block allow list ready", AllowList.READY);
    }

    @Test
    void configAndListAreReadForEveryJoin(@TempDir Path dir) throws IOException {
        Path config = dir.resolve("config.json");
        Path list = dir.resolve("allowlist.json");
        Files.writeString(list, LIST, StandardCharsets.UTF_8);
        Files.writeString(config, configFor(null), StandardCharsets.UTF_8);
        assertNull(AllowList.check(config, "Stranger", "", true));
        // The owner turns the allow list on: the next join is checked against it.
        Files.writeString(config, configFor(list), StandardCharsets.UTF_8);
        assertEquals(AllowList.NOT_LISTED, AllowList.check(config, "Stranger", "", true));
        assertNull(AllowList.check(config, "alex", "", true));
    }

    @Test
    void aMissingOrBrokenConfigTurnsEveryoneAway(@TempDir Path dir) throws IOException {
        Path config = dir.resolve("config.json");
        assertEquals(AllowList.CANT_READ, AllowList.check(config, "alex", "", true));
        Files.writeString(config, "{", StandardCharsets.UTF_8);
        assertEquals(AllowList.CANT_READ, AllowList.check(config, "alex", "", true));
        Files.writeString(config, configFor(dir.resolve("gone.json")), StandardCharsets.UTF_8);
        assertEquals(AllowList.CANT_READ, AllowList.check(config, "alex", "", true));
    }

    /** The plugin's config.json as the agent writes it. */
    private static String configFor(Path list) {
        JsonObject config = new JsonObject();
        config.addProperty("format", 1);
        config.addProperty("allow_list", list == null ? null : list.toString());
        return config.toString();
    }
}
