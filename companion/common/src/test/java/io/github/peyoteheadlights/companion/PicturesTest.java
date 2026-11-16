package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class PicturesTest {
    static byte[] head(int width, int height) {
        return ByteBuffer.allocate(24)
                .put(new byte[] {(byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n', 0, 0, 0, 13, 'I', 'H', 'D', 'R'})
                .putInt(width).putInt(height).array();
    }

    @Test
    void readsTheSize() throws BadInput {
        assertArrayEquals(new int[] {640, 360}, Pictures.size(head(640, 360)));
    }

    @Test
    void refusesHugeOrBrokenPictures() {
        assertThrows(BadInput.class, () -> Pictures.size(head(100_000, 100_000)), "would eat memory");
        assertThrows(BadInput.class, () -> Pictures.size(head(0, 5)));
        assertThrows(BadInput.class, () -> Pictures.size("GIF89a not a png at all!".getBytes()));
        assertThrows(BadInput.class, () -> Pictures.size(new byte[3]));
    }

    @Test
    void refusesTooBigFiles(@TempDir Path dir) throws IOException, BadInput {
        Path file = dir.resolve("icon.png");
        Files.write(file, head(64, 64));
        assertArrayEquals(head(64, 64), Pictures.read(file, 100));
        assertThrows(BadInput.class, () -> Pictures.read(file, 10));
    }
}
