package io.github.peyoteheadlights.companion;

import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

/**
 * The title screen's pictures (the server's icon and the background) come
 * from the modpack, so they are checked before Minecraft decodes them: a
 * real PNG, not too big a file, and not so many pixels that decoding it
 * would eat the game's memory. Done for you.
 */
public final class Pictures {
    public static final long MAX_BACKGROUND_BYTES = 4L * 1024 * 1024;
    public static final long MAX_ICON_BYTES = 256L * 1024;
    public static final int MAX_SIDE = 4096;
    private static final byte[] SIGNATURE = {(byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'};

    private Pictures() {}

    /** Width and height of a PNG, from its first chunk. */
    public static int[] size(byte[] head) throws BadInput {
        if (head.length < 24 || !Arrays.equals(head, 0, 8, SIGNATURE, 0, 8)
                || head[12] != 'I' || head[13] != 'H' || head[14] != 'D' || head[15] != 'R') {
            throw new BadInput("not a PNG");
        }
        ByteBuffer b = ByteBuffer.wrap(head, 16, 8);
        int width = b.getInt();
        int height = b.getInt();
        if (width < 1 || height < 1 || width > MAX_SIDE || height > MAX_SIDE) {
            throw new BadInput("odd size");
        }
        return new int[] {width, height};
    }

    /** The file's bytes, or BadInput if it is missing a PNG's shape or is too big. */
    public static byte[] read(Path file, long maxBytes) throws IOException, BadInput {
        if (Files.isSymbolicLink(file) || Files.size(file) > maxBytes) {
            throw new BadInput("too big");
        }
        byte[] data;
        try (InputStream in = Files.newInputStream(file)) {
            data = in.readNBytes((int) maxBytes + 1);
        }
        if (data.length > maxBytes) {
            throw new BadInput("too big");
        }
        size(data);
        return data;
    }
}
