package io.github.peyoteheadlights.companion.neoforge;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.ServerSide;
import java.util.function.Consumer;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;
import net.neoforged.neoforge.network.configuration.ICustomConfigurationTask;

/**
 * The check while a player joins, before they're in the world. Minecraft
 * runs "configuration tasks" one after another while a player joins:
 * run() once, then tick() 20 times a second until it says true. Both run
 * on the server thread, and so does answered().
 */
final class JoinCheck implements ICustomConfigurationTask {
    static final Type TYPE = new Type("commandblock:check");

    private final ServerConfigurationPacketListenerImpl handler;
    private final ServerSide side;
    private final boolean canTakeOffer;
    private Challenge sent;
    private long deadline;
    private byte[] answer;

    JoinCheck(ServerConfigurationPacketListenerImpl handler, ServerSide side, boolean canTakeOffer) {
        this.handler = handler;
        this.side = side;
        this.canTakeOffer = canTakeOffer;
    }

    @Override
    public void run(Consumer<CustomPacketPayload> sender) {
        sent = side.newChallenge();
        // The time starts now, not when the task was queued: the tasks before this one
        // (a resource pack prompt, a big modpack's registry sync) don't count.
        deadline = System.nanoTime() + side.timeoutSeconds() * 1_000_000_000L;
        byte[] offer = side.offer();
        if (offer != null && canTakeOffer) {
            sender.accept(new OfferPayload(offer)); // first, so the player's Companion can offer the update if the check finds them behind
        }
        sender.accept(new CheckPayload(sent.encode()));
    }

    /** The player's answer. Only the first one counts: one check per join. */
    void answered(byte[] data) {
        if (answer == null) {
            answer = data;
        }
    }

    @Override
    public boolean tick() {
        if (answer == null && System.nanoTime() < deadline) {
            return false; // still waiting
        }
        Companion.joining.remove(handler);
        GameProfile profile = handler.getOwner();
        // false after a kick: the player is gone, nothing more to run.
        return side.judged(profile.id(), profile.name(), sent, answer, message -> handler.disconnect(Component.literal(message)));
    }

    @Override
    public Type type() {
        return TYPE;
    }
}
