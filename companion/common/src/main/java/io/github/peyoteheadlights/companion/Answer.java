package io.github.peyoteheadlights.companion;

import java.util.ArrayList;
import java.util.List;

/**
 * The player's answer to a Challenge. It says, for each expected file,
 * only "loaded" or "not loaded", plus yes/no for extra mods: never the
 * player's own mod list. See PROTOCOL.md for the byte layout.
 *
 * @param nonce the challenge's nonce, sent back
 * @param packVersion the modpack version the player has ("" if they don't know)
 * @param loaded one entry per expected file, true if the game loaded that exact file
 * @param extraMods true if the game loaded any jar that isn't in the modpack
 * @param proofs one SHA-256 per spot: of nonce + the file's bytes in that range
 */
public record Answer(byte[] nonce, String packVersion, List<Boolean> loaded, boolean extraMods, List<byte[]> proofs) {

    public static final int VERSION = 1;
    public static final int MAX_VERSION_BYTES = 32;

    public byte[] encode() {
        Wire.Out out = new Wire.Out().u8(VERSION).bytes(nonce).text(packVersion).u16(loaded.size());
        for (boolean b : loaded) {
            out.u8(b ? 1 : 0);
        }
        out.u8(extraMods ? 1 : 0).u8(proofs.size());
        for (byte[] p : proofs) {
            out.bytes(p);
        }
        return out.done();
    }

    public static Answer decode(byte[] packet) throws BadInput {
        Wire.In in = new Wire.In(packet);
        if (in.u8() != VERSION) {
            throw new BadInput("unknown version");
        }
        byte[] nonce = in.bytes(Challenge.NONCE_BYTES);
        String version = in.text(MAX_VERSION_BYTES);
        int count = in.u16();
        if (count > Settings.MAX_FILES) {
            throw new BadInput("too many files");
        }
        List<Boolean> loaded = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            loaded.add(flag(in));
        }
        boolean extra = flag(in);
        int proofCount = in.u8();
        if (proofCount > Challenge.MAX_SPOTS) {
            throw new BadInput("too many proofs");
        }
        List<byte[]> proofs = new ArrayList<>();
        for (int i = 0; i < proofCount; i++) {
            proofs.add(in.bytes(32));
        }
        in.end();
        return new Answer(nonce, version, List.copyOf(loaded), extra, List.copyOf(proofs));
    }

    private static boolean flag(Wire.In in) throws BadInput {
        int b = in.u8();
        if (b > 1) {
            throw new BadInput("flag must be 0 or 1");
        }
        return b == 1;
    }
}
