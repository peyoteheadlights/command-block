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
    void joinsFromThisPcAreNeverCounted() {
        // Behind Join protection every player comes from 127.0.0.1: one count for all of
        // them would let one person keep everyone out. The guard limits real addresses itself.
        Gate g = new Gate(1);
        for (int i = 0; i < 100; i++) {
            assertTrue(g.allow("127.0.0.1", i));
            assertTrue(g.allow("0:0:0:0:0:0:0:1", i));
        }
        assertTrue(g.allow("1.2.3.4", 0));
        assertFalse(g.allow("1.2.3.4", 1), "other addresses still count");
        assertFalse(Gate.loopback("localhost"), "only literal addresses: no name lookup");
        assertFalse(Gate.loopback("local:E:1234"));
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

    // ---- Part 7 (security review) ----

    @Test
    void ipv6AddressesInOneNetworkShareACount() {
        // One home gets a whole /64 of IPv6 addresses, so per address alone would be no limit.
        Gate g = new Gate(2);
        assertTrue(g.allow("2001:db8:1:2:0:0:0:1", 0));
        assertTrue(g.allow("2001:db8:1:2:ffff:ffff:ffff:ffff", 0));
        assertFalse(g.allow("2001:db8:1:2:abcd:0:0:9", 0), "same /64: same count");
        assertTrue(g.allow("2001:db8:1:3:0:0:0:1", 0), "another /64 has its own count");
    }

    @Test
    void aFloodOfNewAddressesCantFillTheMemory() {
        Gate g = new Gate(1);
        for (int i = 0; i < 50_000; i++) {
            g.allow("10." + (i >> 16) + "." + ((i >> 8) & 255) + "." + (i & 255), i % 1000);
        }
        assertTrue(g.remembered() <= Gate.MAX_REMEMBERED, "remembered " + g.remembered());
        assertFalse(g.allow("192.0.2.1", 30_000), "full: a new address waits");
        assertTrue(g.allow("192.0.2.1", 120_000), "a minute later the old ones are forgotten");
    }

    @Test
    void aFloodDoesNotMakeEveryCheckSlow() {
        Gate g = new Gate(1);
        long start = System.nanoTime();
        for (int i = 0; i < 200_000; i++) {
            g.allow("10." + (i >> 16) + "." + ((i >> 8) & 255) + "." + (i & 255), i / 100);
        }
        // Sweeping every remembered address on every call took minutes here.
        assertTrue(System.nanoTime() - start < 5_000_000_000L, "took " + (System.nanoTime() - start) / 1_000_000 + " ms");
    }
}
