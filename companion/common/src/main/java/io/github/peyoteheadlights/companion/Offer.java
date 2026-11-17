package io.github.peyoteheadlights.companion;

import java.net.URI;
import java.net.URISyntaxException;

/**
 * What the server tells a player's Companion on join, on its own channel
 * (commandblock:offer), so it can offer to update: the modpack's name and
 * version, and where to download it. The download itself is checked file
 * by file (Plan, Install), so a bad link can't put anything unchecked in.
 *
 * @param name the modpack's name
 * @param version the modpack's version
 * @param link the download link (http or https, as the app writes it)
 */
public record Offer(String name, String version, String link) {

    public static final int VERSION = 1;
    public static final int MAX_NAME_BYTES = 256; // the app allows 64 characters, up to 4 bytes each
    public static final int MAX_LINK_BYTES = 512;

    public byte[] encode() {
        return new Wire.Out().u8(VERSION).text(name).text(version).text(link).done();
    }

    /** Strict: wrong version, odd text, a link that isn't plain http(s) or left-over bytes are refused. */
    public static Offer decode(byte[] packet) throws BadInput {
        Wire.In in = new Wire.In(packet);
        if (in.u8() != VERSION) {
            throw new BadInput("unknown version");
        }
        String name = in.text(MAX_NAME_BYTES);
        String version = in.text(Answer.MAX_VERSION_BYTES);
        String link = in.text(MAX_LINK_BYTES);
        in.end();
        if (name.isBlank() || name.chars().anyMatch(c -> c < 0x20 || c == 0x7f)) {
            throw new BadInput("bad name");
        }
        if (!Settings.versionOk(version)) {
            throw new BadInput("bad version");
        }
        if (!link.matches("https?://[\\x21-\\x7e]{1,500}")) {
            throw new BadInput("bad link");
        }
        try {
            URI uri = new URI(link);
            if (uri.getHost() == null || uri.getUserInfo() != null) {
                throw new BadInput("bad link");
            }
        } catch (URISyntaxException e) {
            throw new BadInput("bad link");
        }
        return new Offer(name, version, link);
    }
}
