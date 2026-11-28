package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;

/** Settings.parse is given; these tests check the agent and the Java side agree. */
class SettingsTest {

    static String fromAgent() throws IOException {
        try (var in = SettingsTest.class.getResourceAsStream("/settings-from-agent.json")) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    /** The agent's file with one change made to it. */
    static String changed(Consumer<JsonObject> change) throws IOException {
        JsonObject root = JsonParser.parseString(fromAgent()).getAsJsonObject();
        change.accept(root);
        return root.toString();
    }

    static JsonObject firstFile(JsonObject root) {
        return root.getAsJsonArray("files").get(0).getAsJsonObject();
    }

    @Test
    void readsTheFileTheAgentWrites() throws Exception {
        Settings s = Settings.parse(fromAgent());
        assertFalse(s.required());
        assertEquals("Skyblock with friends", s.modpackName());
        assertEquals("1.4", s.modpackVersion());
        assertEquals(3, s.files().size());
        assertEquals("player-only/minimap.jar", s.files().get(1).onServer());
        assertNull(s.files().get(2).onServer());
        assertEquals(32, s.files().get(0).sha256().length);
        assertEquals(10, s.timeoutSeconds());
        assertEquals(10, s.recheckMinMinutes());
        assertEquals(30, s.recheckMaxMinutes());
        assertEquals(3, s.spotChecks());
    }

    @Test
    void theVersionRuleIsTheSameEverywhere() throws Exception {
        // Letters from other languages pass Java's and Python's \\w, but players' Companions refused
        // them, so the Update button never showed. One rule now: English letters, digits, _ . + -
        for (String odd : new String[] {"\u00e92", "\u0663", "1 beta", "1\n"}) {
            assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.getAsJsonObject("modpack").addProperty("version", odd))), odd);
            assertFalse(Settings.versionOk(odd), odd);
            assertEquals("", Prover.packVersion("{\"format\": 1, \"modpack\": {\"name\": \"S\", \"version\": \"" + odd.replace("\n", "\\n") + "\"}}"));
        }
        assertEquals("1.4", Settings.parse(fromAgent()).modpackVersion());
    }

    @Test
    void refusesAnUnknownField() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.addProperty("surprise", 1))));
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> firstFile(r).addProperty("x", 1))));
    }

    @Test
    void refusesAMissingField() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.remove("check"))));
    }

    @Test
    void refusesANewerFormat() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.addProperty("format", 2))));
    }

    @Test
    void refusesABadHash() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> firstFile(r).addProperty("sha256", "ABC"))));
    }

    @Test
    void refusesAServerCopyOutsideTheServerFolder() {
        for (String bad : new String[] {"../x.jar", "mods/../../x.jar", "/etc/x.jar", "C:\\x.jar", "mods/x.txt"}) {
            assertThrows(BadInput.class, () -> Settings.parse(changed(r -> firstFile(r).addProperty("on_server", bad))), bad);
        }
    }

    @Test
    void refusesAModPathOutsideMods() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> firstFile(r).addProperty("path", "config/x.jar"))));
    }

    @Test
    void refusesTooManyFiles() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> {
            JsonArray many = new JsonArray();
            for (int i = 0; i <= Settings.MAX_FILES; i++) {
                many.add(firstFile(r));
            }
            r.add("files", many);
        })));
    }

    @Test
    void refusesOddValues() {
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.addProperty("required", "yes"))));
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.addProperty("download_link", "javascript:alert(1)"))));
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.getAsJsonObject("check").addProperty("timeout_seconds", 0))));
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> r.getAsJsonObject("check").addProperty("spot_checks", 1.5))));
        assertThrows(BadInput.class, () -> Settings.parse(changed(r -> {
            JsonArray backwards = new JsonArray();
            backwards.add(30);
            backwards.add(10);
            r.getAsJsonObject("check").add("recheck_minutes", backwards);
        })));
        assertThrows(BadInput.class, () -> Settings.parse("[]"));
        assertThrows(BadInput.class, () -> Settings.parse("{not json"));
        assertThrows(BadInput.class, () -> Settings.parse("x".repeat(Settings.MAX_SETTINGS_BYTES + 1)));
    }
}
