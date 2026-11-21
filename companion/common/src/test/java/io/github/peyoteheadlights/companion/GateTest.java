package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class GateTest {

    @Test
    void letsNThroughAMinuteFromOneAddress() {
        Gate g = new Gate(3);
        assertTrue(g.allow("1.2.3.4", 0));
        assertTrue(g.allow("1.2.3.4", 1000));
        assertTrue(g.allow("1.2.3.4", 2000));
        assertFalse(g.allow("1.2.3.4", 3000));
        assertTrue(g.allow("5.6.7.8", 3000), "another address has its own count");
        assertTrue(g.allow("1.2.3.4", 60_001), "the first one is over a minute old now");
    }

    @Test
    void refusedTriesDontCount() {
        Gate g = new Gate(1);
        assertTrue(g.allow("a", 0));
        for (int i = 1; i < 100; i++) {
            assertFalse(g.allow("a", i * 100));
        }
        assertTrue(g.allow("a", 60_000));
    }

    @Test
    void forgetsOldAddressesSoMemoryStaysSmall() {
        Gate g = new Gate(1);
        for (int i = 0; i < 5000; i++) {
            g.allow("10.0." + (i / 256) + "." + (i % 256), i);
        }
        g.allow("late", 200_000);
        assertTrue(g.remembered() < 2000);
    }
}
