package io.github.peyoteheadlights.companion.paper;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.github.peyoteheadlights.companion.BadInput;
import org.junit.jupiter.api.Test;

/** The length in front of each packet must match what the mods' FriendlyByteBuf writes and reads. */
class FrameTest {
    @Test
    void lengthIsAMinecraftVarInt() {
        byte[] framed = CompanionPlugin.frame(new byte[300]);
        assertArrayEquals(new byte[] {(byte) 0xAC, 0x02}, new byte[] {framed[0], framed[1]});
        assertArrayEquals(new byte[] {2, 1, 2}, CompanionPlugin.frame(new byte[] {1, 2}));
    }

    @Test
    void roundTrip() throws BadInput {
        byte[] data = new byte[CompanionPlugin.MAX_BYTES];
        data[data.length - 1] = 7;
        assertArrayEquals(data, CompanionPlugin.unframe(CompanionPlugin.frame(data)));
    }

    @Test
    void refusesWrongOrHugeLengths() {
        assertThrows(BadInput.class, () -> CompanionPlugin.unframe(new byte[0]));
        assertThrows(BadInput.class, () -> CompanionPlugin.unframe(new byte[] {5, 1, 2}));
        assertThrows(BadInput.class, () -> CompanionPlugin.unframe(CompanionPlugin.frame(new byte[CompanionPlugin.MAX_BYTES + 1])));
        assertThrows(BadInput.class, () -> CompanionPlugin.unframe(new byte[] {(byte) 0xFF, (byte) 0xFF, (byte) 0xFF, 0x7F}));
    }
}
