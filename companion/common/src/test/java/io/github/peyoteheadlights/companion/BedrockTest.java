package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.UUID;
import java.util.function.Predicate;
import org.junit.jupiter.api.Test;

/** Asking Floodgate who is a Bedrock player, through its API, when it's there. */
class BedrockTest {
    static final UUID STEVE = new UUID(0, 2535400000000000L); // how Floodgate makes a Bedrock player's id

    /** Looks like Floodgate's FloodgateApi: getInstance(), then isFloodgatePlayer(id). */
    public static final class FakeFloodgate {
        static FakeFloodgate instance = new FakeFloodgate();

        public static FakeFloodgate getInstance() {
            return instance;
        }

        public boolean isFloodgatePlayer(UUID id) {
            return STEVE.equals(id);
        }
    }

    /** Floodgate's getInstance() throws before Floodgate has started. */
    public static final class BrokenFloodgate {
        public static BrokenFloodgate getInstance() {
            throw new IllegalStateException("not started");
        }

        public boolean isFloodgatePlayer(UUID id) {
            return true;
        }
    }

    @Test
    void asksFloodgate() {
        Predicate<UUID> bedrock = Bedrock.find(FakeFloodgate.class.getName());
        assertTrue(bedrock.test(STEVE));
        assertFalse(bedrock.test(UUID.randomUUID()));
        assertFalse(bedrock.test(null));
    }

    @Test
    void floodgateStartingLaterIsSeen() {
        FakeFloodgate.instance = null;
        Predicate<UUID> bedrock = Bedrock.find(FakeFloodgate.class.getName());
        assertFalse(bedrock.test(STEVE), "not started yet: check them like anyone");
        FakeFloodgate.instance = new FakeFloodgate();
        assertTrue(bedrock.test(STEVE));
    }

    @Test
    void noFloodgateMeansNobodyIsBedrock() {
        assertFalse(Bedrock.find("org.example.NotThere").test(STEVE));
    }

    @Test
    void aBrokenFloodgateMeansTheyAreChecked() {
        assertFalse(Bedrock.find(BrokenFloodgate.class.getName()).test(STEVE));
    }
}
