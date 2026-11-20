package io.github.peyoteheadlights.companion.waterdog;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;

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
}
