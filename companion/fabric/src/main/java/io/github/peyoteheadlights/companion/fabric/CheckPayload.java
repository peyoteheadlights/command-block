package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.Challenge;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

/**
 * One packet on the plugin channel commandblock:check, both ways. It only
 * carries bytes: common's Challenge and Answer read and write them
 * (PROTOCOL.md). Done for you: this is Minecraft plumbing, not the check.
 */
public record CheckPayload(byte[] data) implements CustomPacketPayload {
    /** A challenge is the bigger one: 4000 hashes of 32 bytes, plus spots. */
    public static final int MAX_BYTES = 1 + Challenge.NONCE_BYTES + 2 + 32 * 4000 + 1 + 14 * Challenge.MAX_SPOTS;

    public static final Type<CheckPayload> TYPE =
            new Type<>(Identifier.fromNamespaceAndPath("commandblock", "check"));

    // Reads at most MAX_BYTES, so a huge packet is refused before it's in memory.
    public static final StreamCodec<FriendlyByteBuf, CheckPayload> CODEC = StreamCodec.of(
            (buf, payload) -> buf.writeByteArray(payload.data()),
            buf -> new CheckPayload(buf.readByteArray(MAX_BYTES)));

    @Override
    public Type<CheckPayload> type() {
        return TYPE;
    }

    /** Tells Fabric API about the channel, in the joining phase and while playing. Call once, at start. */
    public static void register() {
        PayloadTypeRegistry.clientboundConfiguration().register(TYPE, CODEC);
        PayloadTypeRegistry.serverboundConfiguration().register(TYPE, CODEC);
        PayloadTypeRegistry.clientboundPlay().register(TYPE, CODEC);
        PayloadTypeRegistry.serverboundPlay().register(TYPE, CODEC);
    }
}
