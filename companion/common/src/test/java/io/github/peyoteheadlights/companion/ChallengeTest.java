package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.ByteBuffer;
import java.util.List;
import java.util.Random;
import org.junit.jupiter.api.Test;

class ChallengeTest {

    static byte[] filled(int n, int value) {
        byte[] b = new byte[n];
        java.util.Arrays.fill(b, (byte) value);
        return b;
    }

    static Challenge sample() {
        return new Challenge(
                filled(16, 7),
                List.of(filled(32, 1), filled(32, 2)),
                List.of(new Challenge.Spot(1, 100, 4096)));
    }

    @Test
    void comesBackTheSameAfterEncodeAndDecode() throws Exception {
        Challenge back = Challenge.decode(sample().encode());
        assertArrayEquals(filled(16, 7), back.nonce());
        assertEquals(2, back.expected().size());
        assertArrayEquals(filled(32, 2), back.expected().get(1));
        assertEquals(List.of(new Challenge.Spot(1, 100, 4096)), back.spots());
    }

    @Test
    void hasTheLayoutFromProtocolMd() {
        byte[] b = sample().encode();
        // 1 version + 16 nonce + 2 count + 2*32 hashes + 1 spot count + (2 + 8 + 4) spot
        assertEquals(1 + 16 + 2 + 64 + 1 + 14, b.length);
        assertEquals(1, b[0]);
        assertEquals(2, ByteBuffer.wrap(b, 17, 2).getShort());
    }

    @Test
    void refusesAnotherVersion() {
        byte[] b = sample().encode();
        b[0] = 2;
        assertThrows(BadInput.class, () -> Challenge.decode(b));
    }

    @Test
    void refusesBytesLeftOverOrMissing() {
        byte[] b = sample().encode();
        assertThrows(BadInput.class, () -> Challenge.decode(java.util.Arrays.copyOf(b, b.length + 1)));
        assertThrows(BadInput.class, () -> Challenge.decode(java.util.Arrays.copyOf(b, b.length - 1)));
        assertThrows(BadInput.class, () -> Challenge.decode(new byte[0]));
    }

    @Test
    void refusesBadSpots() {
        for (Challenge.Spot bad : List.of(
                new Challenge.Spot(2, 0, 10), // no file 2
                new Challenge.Spot(0, -1, 10),
                new Challenge.Spot(0, 0, 0),
                new Challenge.Spot(0, 0, Challenge.MAX_SPOT_BYTES + 1))) {
            byte[] b = new Challenge(filled(16, 0), List.of(filled(32, 1), filled(32, 2)), List.of(bad)).encode();
            assertThrows(BadInput.class, () -> Challenge.decode(b), bad.toString());
        }
    }

    @Test
    void refusesCountsOverTheLimits() {
        byte[] tooManyFiles = new Wire.Out().u8(1).bytes(new byte[16]).u16(Settings.MAX_FILES + 1).done();
        assertThrows(BadInput.class, () -> Challenge.decode(tooManyFiles));
        byte[] tooManySpots = new Wire.Out().u8(1).bytes(new byte[16]).u16(0).u8(Challenge.MAX_SPOTS + 1).done();
        assertThrows(BadInput.class, () -> Challenge.decode(tooManySpots));
    }

    @Test
    void randomBytesOnlyEverGiveBadInput() {
        Random r = new Random(11);
        for (int i = 0; i < 20_000; i++) {
            byte[] junk = new byte[r.nextInt(300)];
            r.nextBytes(junk);
            if (junk.length > 0 && r.nextBoolean()) {
                junk[0] = 1; // get past the version byte more often
            }
            try {
                Challenge.decode(junk);
            } catch (BadInput expected) {
                // fine: any other exception fails the test
            }
        }
    }

    @Test
    void aHugeClaimedCountDoesNotMakeAHugeList() {
        // Says 4000 files but has none: must fail fast, not wait or run out of memory.
        byte[] b = new Wire.Out().u8(1).bytes(new byte[16]).u16(Settings.MAX_FILES).done();
        assertThrows(BadInput.class, () -> Challenge.decode(b));
    }
}
