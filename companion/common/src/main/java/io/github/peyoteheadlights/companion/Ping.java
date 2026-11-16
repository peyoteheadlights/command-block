package io.github.peyoteheadlights.companion;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

/**
 * The title-screen button's one normal server ping, the same one
 * Minecraft's server list sends (no extra port): "3 online", or "Asleep,
 * ask to start" when Command Block's join guard answers for a slept server.
 *
 * <p>The protocol (https://minecraft.wiki/w/Java_Edition_protocol/Server_List_Ping):
 * every packet is [VarInt length][VarInt packet id][data]. We send a
 * handshake (id 0) and a status request (id 0, no data); the server
 * answers with one packet (id 0) holding a JSON string.
 */
public final class Ping {
    /** What the button shows. */
    public record Status(int online, int max, boolean asleep) {}

    /** Any protocol number works for a status ping; this one is "unknown". */
    static final int PROTOCOL = -1;
    static final int MAX_ANSWER_BYTES = 256 * 1024; // a modded server's list can be long, but not this long

    private Ping() {}

    /** Asks the server; throws on no answer within {@code timeoutMs} or an answer we refuse. */
    public static Status ping(String host, int port, int timeoutMs) throws IOException, BadInput {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress(host, port), timeoutMs);
            socket.setSoTimeout(timeoutMs);
            OutputStream out = socket.getOutputStream();
            out.write(packet(handshake(host, port)));
            out.write(packet(new byte[] {0})); // the status request
            out.flush();
            return status(readAnswer(socket.getInputStream()));
        }
    }

    /**
     * The handshake's id and data (packet() puts the length in front).
     *
     * <p>In order: VarInt 0 (the packet id), VarInt {@link #PROTOCOL}, the host as a
     * string (VarInt byte length, then its UTF-8 bytes), the port as an unsigned
     * short (2 bytes, big-endian: {@code port >> 8} then {@code port & 0xFF}),
     * and VarInt 1 ("next: status").
     */
    static byte[] handshake(String host, int port) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        varInt(b, 0);
        varInt(b, PROTOCOL);
        byte[] h = host.getBytes(StandardCharsets.UTF_8);
        varInt(b, h.length);
        b.writeBytes(h);
        b.write(port >> 8);
        b.write(port & 0xFF);
        varInt(b, 1);
        return b.toByteArray();
    }

    /**
     * What the answer's JSON says.
     *
     * <p>It looks like {@code {"players": {"online": 3, "max": 20}, ...}}. Command
     * Block's join guard adds {@code "commandblock": {"asleep": true}} for a slept
     * server. Refuses (BadInput) what isn't a JSON object with a "players"
     * object; online and max below 0 count as 0; anything else odd is a
     * RuntimeException from Gson, caught and turned into BadInput.
     */
    static Status status(String json) throws BadInput {
        try {
            JsonObject root = JsonParser.parseString(json).getAsJsonObject();
            JsonObject players = root.getAsJsonObject("players");
            if (players == null) {
                throw new BadInput("no players");
            }
            int online = Math.max(0, players.get("online").getAsInt());
            int max = Math.max(0, players.get("max").getAsInt());
            JsonElement ours = root.get("commandblock");
            boolean asleep = ours != null && ours.isJsonObject()
                    && ours.getAsJsonObject().has("asleep")
                    && ours.getAsJsonObject().get("asleep").getAsBoolean();
            return new Status(online, max, asleep);
        } catch (RuntimeException e) {
            throw new BadInput("odd status: " + e.getClass().getSimpleName());
        }
    }

    // ---- done for you: VarInts and reading the answer ----

    /** A VarInt: 7 bits a byte, low bits first, the top bit says "more follows". */
    static void varInt(ByteArrayOutputStream out, int value) {
        while ((value & ~0x7F) != 0) {
            out.write((value & 0x7F) | 0x80);
            value >>>= 7;
        }
        out.write(value);
    }

    static int readVarInt(InputStream in) throws IOException, BadInput {
        int value = 0;
        for (int i = 0; i < 5; i++) {
            int b = in.read();
            if (b < 0) {
                throw new BadInput("answer too short");
            }
            value |= (b & 0x7F) << (7 * i);
            if ((b & 0x80) == 0) {
                return value;
            }
        }
        throw new BadInput("VarInt too long");
    }

    static byte[] packet(byte[] body) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        varInt(out, body.length);
        out.writeBytes(body);
        return out.toByteArray();
    }

    /** One status answer: [length][id 0][string length][JSON], every size checked before reading. */
    static String readAnswer(InputStream raw) throws IOException, BadInput {
        DataInputStream in = new DataInputStream(raw);
        int length = readVarInt(in);
        if (length < 2 || length > MAX_ANSWER_BYTES) {
            throw new BadInput("odd answer length");
        }
        byte[] body = new byte[length];
        in.readFully(body);
        InputStream b = new java.io.ByteArrayInputStream(body);
        if (readVarInt(b) != 0) {
            throw new BadInput("not a status answer");
        }
        int textLength = readVarInt(b);
        if (textLength < 0 || textLength > b.available()) {
            throw new BadInput("odd text length");
        }
        return new String(b.readNBytes(textLength), StandardCharsets.UTF_8);
    }
}
