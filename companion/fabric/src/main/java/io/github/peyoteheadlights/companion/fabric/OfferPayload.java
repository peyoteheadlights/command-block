package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.Offer;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.Identifier;

/** The server's update offer (common's Offer) on channel commandblock:offer, server to player only. */
public record OfferPayload(byte[] data) implements CustomPacketPayload {
    public static final int MAX_BYTES = 1 + 3 * 2 + Offer.MAX_NAME_BYTES + 32 + Offer.MAX_LINK_BYTES;

    public static final Type<OfferPayload> TYPE =
            new Type<>(Identifier.fromNamespaceAndPath("commandblock", "offer"));

    public static final StreamCodec<FriendlyByteBuf, OfferPayload> CODEC = StreamCodec.of(
            (buf, payload) -> buf.writeByteArray(payload.data()),
            buf -> new OfferPayload(buf.readByteArray(MAX_BYTES)));

    @Override
    public Type<OfferPayload> type() {
        return TYPE;
    }

    /** Call once, at start, next to CheckPayload.register(). */
    public static void register() {
        PayloadTypeRegistry.clientboundConfiguration().register(TYPE, CODEC);
        PayloadTypeRegistry.clientboundPlay().register(TYPE, CODEC);
    }
}
