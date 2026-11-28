package io.github.peyoteheadlights.companion;

import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** SHA-256 of a whole file, and the spot-check proof both sides compute. */
public final class Hashes {
    private Hashes() {}

    public static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("every Java has SHA-256", e);
        }
    }

    /** SHA-256 of the whole file, read in small pieces (never the whole file in memory). */
    public static byte[] ofFile(Path file) throws IOException {
        MessageDigest d = sha256();
        try (InputStream in = Files.newInputStream(file)) {
            feed(in, d, Long.MAX_VALUE);
        }
        return d.digest();
    }

    /**
     * Reads the stream into the digest in 64 KiB pieces. Stops once more
     * than {@code max} bytes came, so the caller can refuse a file that's
     * too big without reading all of it. Returns how many bytes it read.
     */
    static long feed(InputStream in, MessageDigest d, long max) throws IOException {
        byte[] buf = new byte[64 * 1024];
        long total = 0;
        int n;
        while (total <= max && (n = in.read(buf)) > 0) {
            d.update(buf, 0, n);
            total += n;
        }
        return total;
    }

    /**
     * SHA-256 of the nonce followed by the file's bytes from offset, at most
     * length of them (fewer if the file ends first). A fresh nonce means the
     * answer can't be worked out ahead of time.
     */
    public static byte[] proof(byte[] nonce, Path file, long offset, int length) throws IOException {
        MessageDigest d = sha256();
        d.update(nonce);
        try (FileChannel ch = FileChannel.open(file, StandardOpenOption.READ)) {
            ByteBuffer buf = ByteBuffer.allocate(Math.max(0, length));
            long at = offset;
            while (buf.hasRemaining()) {
                int n = ch.read(buf, at);
                if (n <= 0) {
                    break;
                }
                at += n;
            }
            buf.flip();
            d.update(buf);
        }
        return d.digest();
    }
}
