package io.github.peyoteheadlights.companion;

import java.util.ArrayList;
import java.util.List;

/**
 * What the server sends a player on join (and again at random times
 * while they play). See PROTOCOL.md for the byte layout.
 *
 * @param nonce 16 fresh random bytes, so an old answer is useless
 * @param expected the SHA-256 of each mod jar the modpack has, in the settings file's order
 * @param spots byte ranges the player must prove they have
 */
public record Challenge(byte[] nonce, List<byte[]> expected, List<Spot> spots) {

    public static final int VERSION = 1;
    public static final int NONCE_BYTES = 16;
    public static final int MAX_SPOTS = 8;
    public static final int MAX_SPOT_BYTES = 64 * 1024;

    /** "Hash bytes offset..offset+length of the file at this index in expected." */
    public record Spot(int fileIndex, long offset, int length) {}

    public byte[] encode() {
        Wire.Out out = new Wire.Out().u8(VERSION).bytes(nonce).u16(expected.size());
        for (byte[] hash : expected) {
            out.bytes(hash);
        }
        out.u8(spots.size());
        for (Spot s : spots) {
            out.u16(s.fileIndex()).i64(s.offset()).i32(s.length());
        }
        return out.done();
    }

    /** Strict: refuses a wrong version, counts over the limits, bad spots and left-over bytes. */
    public static Challenge decode(byte[] packet) throws BadInput {
        Wire.In in = new Wire.In(packet);
        if (in.u8() != VERSION) {
            throw new BadInput("unknown version");
        }
        byte[] nonce = in.bytes(NONCE_BYTES);
        int count = in.u16();
        if (count > Settings.MAX_FILES) {
            throw new BadInput("too many files");
        }
        List<byte[]> expected = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            expected.add(in.bytes(32));
        }
        int spotCount = in.u8();
        if (spotCount > MAX_SPOTS) {
            throw new BadInput("too many spots");
        }
        List<Spot> spots = new ArrayList<>();
        for (int i = 0; i < spotCount; i++) {
            Spot s = new Spot(in.u16(), in.i64(), in.i32());
            if (s.fileIndex() >= count || s.offset() < 0 || s.length() < 1 || s.length() > MAX_SPOT_BYTES) {
                throw new BadInput("bad spot");
            }
            spots.add(s);
        }
        in.end();
        return new Challenge(nonce, List.copyOf(expected), List.copyOf(spots));
    }
}
