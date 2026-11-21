package io.github.peyoteheadlights.companion.forge;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.ServerSide;
import java.util.function.Consumer;
import net.minecraft.network.Connection;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.Packet;
import net.minecraft.server.network.ConfigurationTask;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;
import net.minecraftforge.network.config.ConfigurationTaskContext;

/**
 * The check while a player joins, before they're in the world. Minecraft
 * runs "configuration tasks" one after another while a player joins:
 * start() once, then tick() 20 times a second until it says true. Both
 * run on the server thread, and so does answered().
 */
final class JoinCheck implements ConfigurationTask {
    static final Type TYPE = new Type("commandblock:check");

    private final ServerConfigurationPacketListenerImpl handler;
    private final ServerSide side;
    private Challenge sent;
    private long deadline;
    private byte[] answer;

    JoinCheck(ServerConfigurationPacketListenerImpl handler, ServerSide side) {
        this.handler = handler;
        this.side = side;
    }

    @Override
    public void start(ConfigurationTaskContext context) {
        Connection connection = context.getConnection();
        // Asked here, not when queued: the player's channel list comes during Forge's own steps before this one.
        if (!Companion.CHECK.isRemotePresent(connection)) {
            return; // no Companion at all: judged on the first tick
        }
        sent = side.newChallenge();
        // The time starts now, not when the task was queued: the tasks before this one
        // (a resource pack prompt, a big modpack's registry sync) don't count.
        deadline = System.nanoTime() + side.timeoutSeconds() * 1_000_000_000L;
        byte[] offer = side.offer();
        if (offer != null && Companion.OFFER.isRemotePresent(connection)) {
            Companion.send(Companion.OFFER, offer, connection); // first, so the player's Companion can offer the update if the check finds them behind
        }
        Companion.send(Companion.CHECK, sent.encode(), connection);
    }

    @Override
    public void start(Consumer<Packet<?>> send) {
        throw new IllegalStateException("Forge calls start(ConfigurationTaskContext)");
    }

    /** The player's answer. Only the first one counts: one check per join. */
    void answered(byte[] data) {
        if (answer == null) {
            answer = data;
        }
    }

    @Override
    public boolean tick() {
        if (sent != null && answer == null && System.nanoTime() < deadline) {
            return false; // still waiting
        }
        Companion.joining.remove(handler.getConnection());
        GameProfile profile = handler.getOwner();
        // false after a kick: the player is gone, nothing more to run.
        return side.judged(profile.id(), profile.name(), sent, answer, message -> handler.disconnect(Component.literal(message)));
    }

    @Override
    public Type type() {
        return TYPE;
    }
}
