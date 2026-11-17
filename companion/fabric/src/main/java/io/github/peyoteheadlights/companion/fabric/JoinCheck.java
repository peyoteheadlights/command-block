package io.github.peyoteheadlights.companion.fabric;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.ServerSide;
import java.util.function.Consumer;
import net.fabricmc.fabric.api.networking.v1.ServerConfigurationNetworking;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.Packet;
import net.minecraft.server.network.ConfigurationTask;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;

/**
 * The check while a player joins, before they're in the world. Minecraft
 * runs "configuration tasks" one after another while a player joins:
 * start() once, then tick() 20 times a second until it says true.
 */
final class JoinCheck implements ConfigurationTask {
    static final Type TYPE = new Type("commandblock:check");

    private final ServerConfigurationPacketListenerImpl handler;
    private final ServerSide side;
    private Challenge sent;
    private long deadline;
    private volatile byte[] answer;

    JoinCheck(ServerConfigurationPacketListenerImpl handler, ServerSide side) {
        this.handler = handler;
        this.side = side;
    }

    @Override
    public void start(Consumer<Packet<?>> sender) {
        sent = side.newChallenge();
        // The time starts now, not when the task was queued: the tasks before this one
        // (a resource pack prompt, a big modpack's registry sync) don't count.
        deadline = System.nanoTime() + side.timeoutSeconds() * 1_000_000_000L;
        ServerConfigurationNetworking.registerReceiver(handler, CheckPayload.TYPE, (payload, context) -> {
            if (answer == null) {
                answer = payload.data(); // only the first answer counts: one check per join
            }
        });
        byte[] offer = side.offer();
        if (offer != null && ServerConfigurationNetworking.canSend(handler, OfferPayload.TYPE)) {
            // First, so the player's Companion can offer the update if the check finds them behind.
            sender.accept(ServerConfigurationNetworking.createClientboundPacket(new OfferPayload(offer)));
        }
        sender.accept(ServerConfigurationNetworking.createClientboundPacket(new CheckPayload(sent.encode())));
    }

    @Override
    public boolean tick() {
        byte[] got = answer;
        if (got == null && System.nanoTime() < deadline) {
            return false; // still waiting
        }
        ServerConfigurationNetworking.unregisterReceiver(handler, CheckPayload.TYPE.id());
        GameProfile profile = handler.getOwner();
        // false after a kick: the player is gone, nothing more to run.
        return side.judged(profile.id(), profile.name(), sent, got, message -> handler.disconnect(Component.literal(message)));
    }

    @Override
    public Type type() {
        return TYPE;
    }
}
