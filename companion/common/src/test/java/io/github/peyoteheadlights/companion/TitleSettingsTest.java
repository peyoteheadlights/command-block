package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class TitleSettingsTest {
    static String file(String part) {
        return "{\"format\": 1, \"modpack\": {\"name\": \"F\", \"version\": \"1\"}, \"files\": [], \"title_screen\": " + part + "}";
    }

    @Test
    void readsTheButtonAndTheFlavorText() {
        TitleSettings got = TitleSettings.parse(file(
                "{\"name\": \"Survival\", \"address\": \"play.example.net:25570\", \"flavor_text\": \"Dragons!\"}"));
        assertEquals(new TitleSettings("Survival", "play.example.net", 25570, "Dragons!"), got);
        assertEquals("play.example.net:25570", got.address());
    }

    @Test
    void noPortMeansMinecraftsOwn() {
        TitleSettings got = TitleSettings.parse(file("{\"name\": \"S\", \"address\": \"10.0.0.5\", \"flavor_text\": null}"));
        assertEquals(new TitleSettings("S", "10.0.0.5", 25565, null), got);
        assertEquals("10.0.0.5", got.address());
    }

    @Test
    void offWhenThePartIsMissingOrTheFileIsBroken() {
        assertEquals(TitleSettings.OFF, TitleSettings.parse("{\"format\": 1}"));
        assertEquals(TitleSettings.OFF, TitleSettings.parse("not json"));
        assertEquals(TitleSettings.OFF, TitleSettings.parse(file("[1, 2]")));
        assertEquals(TitleSettings.OFF, TitleSettings.parse(file("null")));
    }

    @Test
    void aBadAddressTurnsOffOnlyTheButton() {
        for (String bad : new String[] {"\"\"", "\"has space.net\"", "\"host:0\"", "\"host:99999\"", "\"a\\nb\"", "\"-dash.net\"", "42"}) {
            TitleSettings got = TitleSettings.parse(file("{\"name\": \"S\", \"address\": " + bad + ", \"flavor_text\": \"Hi\"}"));
            assertEquals(new TitleSettings("S", null, 25565, "Hi"), got, bad);
        }
    }

    @Test
    void aBadFlavorTextOrNameIsLeftOut() {
        String tooLong = "a".repeat(65); // too long for both (name 64, flavor 60)
        for (String bad : new String[] {"\"\"", "\"" + tooLong + "\"", "\"two\\nlines\"", "\"\\u00a7ccolour\"", "{}"}) {
            TitleSettings got = TitleSettings.parse(file("{\"name\": " + bad + ", \"address\": \"h\", \"flavor_text\": " + bad + "}"));
            assertEquals(new TitleSettings("", "h", 25565, null), got, bad);
        }
    }

    @Test
    void theHoverSaysWhoIsOnOrThatItSleeps() {
        assertEquals("3 online", TitleSettings.statusLine(new Ping.Status(3, 20, false)));
        assertEquals("0 online", TitleSettings.statusLine(new Ping.Status(0, 20, false)));
        assertEquals("Asleep, join to start it", TitleSettings.statusLine(new Ping.Status(0, 20, true)));
        assertEquals("Can't reach it right now", TitleSettings.statusLine(null));
    }
}
