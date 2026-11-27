package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Arrays;
import java.util.List;
import java.util.Random;
import org.junit.jupiter.api.Test;

class AnswerTest {

    static Answer sample() {
        return new Answer(ChallengeTest.filled(16, 7), "1.4", List.of(true, false), true, List.of(ChallengeTest.filled(32, 9)));
    }

    @Test
    void comesBackTheSameAfterEncodeAndDecode() throws Exception {
        Answer back = Answer.decode(sample().encode());
        assertArrayEquals(ChallengeTest.filled(16, 7), back.nonce());
        assertEquals("1.4", back.packVersion());
        assertEquals(List.of(true, false), back.loaded());
        assertTrue(back.extraMods());
        assertArrayEquals(ChallengeTest.filled(32, 9), back.proofs().get(0));
    }

    @Test
    void hasTheLayoutFromProtocolMd() {
        // 1 version + 16 nonce + (2 + 3) version text + 2 count + 2 flags + 1 extra + 1 proof count + 32
        assertEquals(1 + 16 + 5 + 2 + 2 + 1 + 1 + 32, sample().encode().length);
    }

    @Test
    void refusesAFlagThatIsntZeroOrOne() {
        byte[] b = sample().encode();
        b[1 + 16 + 5 + 2] = 2; // first "loaded" flag
        assertThrows(BadInput.class, () -> Answer.decode(b));
    }

    @Test
    void refusesALongVersionText() {
        byte[] b = new Answer(new byte[16], "x".repeat(33), List.of(), false, List.of()).encode();
        assertThrows(BadInput.class, () -> Answer.decode(b));
    }

    @Test
    void refusesBytesLeftOverOrMissing() {
        byte[] b = sample().encode();
        assertThrows(BadInput.class, () -> Answer.decode(Arrays.copyOf(b, b.length + 1)));
        assertThrows(BadInput.class, () -> Answer.decode(Arrays.copyOf(b, b.length - 1)));
    }

    @Test
    void refusesTooManyProofs() {
        byte[] b = new Wire.Out().u8(1).bytes(new byte[16]).text("").u16(0).u8(0).u8(Challenge.MAX_SPOTS + 1).done();
        assertThrows(BadInput.class, () -> Answer.decode(b));
    }

    @Test
    void randomBytesOnlyEverGiveBadInput() {
        Random r = new Random(12);
        for (int i = 0; i < 20_000; i++) {
            byte[] junk = new byte[r.nextInt(200)];
            r.nextBytes(junk);
            if (junk.length > 0 && r.nextBoolean()) {
                junk[0] = 1;
            }
            try {
                Answer.decode(junk);
            } catch (BadInput expected) {
                // fine
            }
        }
    }
}
