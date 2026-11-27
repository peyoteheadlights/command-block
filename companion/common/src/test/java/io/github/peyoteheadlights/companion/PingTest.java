package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import org.junit.jupiter.api.Test;

class PingTest {
    @Test
    void handshakeIsWhatMinecraftSends() {
        // id 0, protocol -1 (5 bytes), "ab", port 25565 (0x63DD), next state 1
        byte[] want = {0, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, 0x0F, 2, 'a', 'b', 0x63, (byte) 0xDD, 1};
        assertArrayEquals(want, Ping.handshake("ab", 25565));
    }

    @Test
    void readsOnlineAndAsleep() throws BadInput {
        assertEquals(new Ping.Status(3, 20, false),
                Ping.status("{\"version\": {}, \"players\": {\"online\": 3, \"max\": 20}, \"description\": \"hi\"}"));
        assertEquals(new Ping.Status(0, 20, true),
                Ping.status("{\"players\": {\"online\": 0, \"max\": 20}, \"commandblock\": {\"asleep\": true}}"));
        assertEquals(new Ping.Status(0, 0, false), Ping.status("{\"players\": {\"online\": -5, \"max\": -1}}"));
    }

    @Test
    void refusesOddStatus() {
        for (String bad : new String[] {"", "[]", "{}", "{\"players\": 3}", "{\"players\": {\"online\": \"x\", \"max\": 1}}", "nope"}) {
            assertThrows(BadInput.class, () -> Ping.status(bad), bad);
        }
    }

    @Test
    void refusesAnswersThatLieAboutTheirSize() {
        byte[] huge = {(byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0x7F}; // ~268 MB
        assertThrows(BadInput.class, () -> Ping.readAnswer(new ByteArrayInputStream(huge)));
        byte[] tooLong = {(byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, (byte) 0xFF, 1};
        assertThrows(BadInput.class, () -> Ping.readAnswer(new ByteArrayInputStream(tooLong)));
        byte[] textBiggerThanPacket = Ping.packet(new byte[] {0, 100, 'x'});
        assertThrows(BadInput.class, () -> Ping.readAnswer(new ByteArrayInputStream(textBiggerThanPacket)));
    }

    @Test
    void pingsAFakeServer() throws Exception {
        try (ServerSocket server = new ServerSocket(0)) {
            CompletableFuture<byte[]> seen = CompletableFuture.supplyAsync(() -> {
                try (Socket s = server.accept()) {
                    DataInputStream in = new DataInputStream(s.getInputStream());
                    byte[] hello = new byte[Ping.readVarInt(in)];
                    in.readFully(hello);
                    byte[] request = new byte[Ping.readVarInt(in)];
                    in.readFully(request);
                    byte[] json = "{\"players\": {\"online\": 2, \"max\": 8}}".getBytes(StandardCharsets.UTF_8);
                    ByteArrayOutputStream body = new ByteArrayOutputStream();
                    Ping.varInt(body, 0);
                    Ping.varInt(body, json.length);
                    body.writeBytes(json);
                    s.getOutputStream().write(Ping.packet(body.toByteArray()));
                    return hello;
                } catch (IOException | BadInput e) {
                    throw new RuntimeException(e);
                }
            });
            assertEquals(new Ping.Status(2, 8, false), Ping.ping("127.0.0.1", server.getLocalPort(), 2000));
            assertArrayEquals(Ping.handshake("127.0.0.1", server.getLocalPort()), seen.get());
        }
    }

    @Test
    void aSilentServerTimesOut() throws IOException {
        try (ServerSocket server = new ServerSocket(0)) {
            assertThrows(IOException.class, () -> Ping.ping("127.0.0.1", server.getLocalPort(), 300));
        }
    }
}
