package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.Arrays;
import java.util.Random;
import org.junit.jupiter.api.Test;

class OfferTest {
    @Test
    void roundTrip() throws BadInput {
        Offer o = new Offer("Friends", "1.4", "http://100.64.0.1:25590/abc/modpack.mrpack");
        assertEquals(o, Offer.decode(o.encode()));
    }

    @Test
    void refusesOddLinksNamesAndVersions() {
        for (Offer bad : new Offer[] {
            new Offer("Friends", "1", "file:///C:/Windows/x"),
            new Offer("Friends", "1", "javascript:alert(1)"),
            new Offer("Friends", "1", "http://user:pw@host/x"),
            new Offer("Friends", "1", "http://host/a b"),
            new Offer("Friends", "1 2", "http://host/x"),
            new Offer("", "1", "http://host/x"),
            new Offer("Bad\nname", "1", "http://host/x"),
        }) {
            assertThrows(BadInput.class, () -> Offer.decode(bad.encode()), bad.toString());
        }
    }

    @Test
    void refusesLeftOverBytesAndOtherVersions() {
        byte[] good = new Offer("F", "1", "http://h/x").encode();
        byte[] longer = Arrays.copyOf(good, good.length + 1);
        assertThrows(BadInput.class, () -> Offer.decode(longer));
        good[0] = 2;
        assertThrows(BadInput.class, () -> Offer.decode(good));
    }

    @Test
    void randomBytesOnlyEverGiveBadInput() {
        Random r = new Random(4);
        for (int i = 0; i < 5000; i++) {
            byte[] junk = new byte[r.nextInt(80)];
            r.nextBytes(junk);
            if (junk.length > 0) {
                junk[0] = 1;
            }
            try {
                Offer.decode(junk);
            } catch (BadInput expected) {
                // the only way it may fail
            }
        }
    }
}
