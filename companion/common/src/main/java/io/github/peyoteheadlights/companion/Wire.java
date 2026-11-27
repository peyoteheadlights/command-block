package io.github.peyoteheadlights.companion;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;

/**
 * Reads and writes the small pieces packets are made of (big-endian, like
 * Java's DataOutputStream). Every read checks there are enough bytes left,
 * so a short or broken packet gives a BadInput, never an exception from
 * deep inside Java. This file is done for you: use it as the example of
 * the style the other files follow.
 */
final class Wire {
    private Wire() {}

    /** Reading side: wraps the packet's bytes. */
    static final class In {
        private final ByteBuffer buf;

        In(byte[] packet) {
            this.buf = ByteBuffer.wrap(packet);
        }

        private void need(int n) throws BadInput {
            if (n < 0 || buf.remaining() < n) {
                throw new BadInput("packet too short");
            }
        }

        int u8() throws BadInput {
            need(1);
            return buf.get() & 0xFF;
        }

        int u16() throws BadInput {
            need(2);
            return buf.getShort() & 0xFFFF;
        }

        int i32() throws BadInput {
            need(4);
            return buf.getInt();
        }

        long i64() throws BadInput {
            need(8);
            return buf.getLong();
        }

        byte[] bytes(int n) throws BadInput {
            need(n);
            byte[] out = new byte[n];
            buf.get(out);
            return out;
        }

        /** A UTF-8 string with a u16 length in front, at most maxBytes long. */
        String text(int maxBytes) throws BadInput {
            int n = u16();
            if (n > maxBytes) {
                throw new BadInput("text too long");
            }
            return new String(bytes(n), StandardCharsets.UTF_8);
        }

        /** Call last: refuses bytes left over (no hidden extra fields). */
        void end() throws BadInput {
            if (buf.hasRemaining()) {
                throw new BadInput("bytes left over");
            }
        }
    }

    /** Writing side. */
    static final class Out {
        private final ByteArrayOutputStream out = new ByteArrayOutputStream();

        Out u8(int v) {
            out.write(v);
            return this;
        }

        Out u16(int v) {
            out.write(v >>> 8);
            out.write(v);
            return this;
        }

        Out i32(int v) {
            out.writeBytes(ByteBuffer.allocate(4).putInt(v).array());
            return this;
        }

        Out i64(long v) {
            out.writeBytes(ByteBuffer.allocate(8).putLong(v).array());
            return this;
        }

        Out bytes(byte[] v) {
            out.writeBytes(v);
            return this;
        }

        Out text(String v) {
            byte[] b = v.getBytes(StandardCharsets.UTF_8);
            return u16(b.length).bytes(b);
        }

        byte[] done() {
            return out.toByteArray();
        }
    }
}
