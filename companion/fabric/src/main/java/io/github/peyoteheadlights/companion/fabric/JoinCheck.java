package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Checker;
import java.util.function.Consumer;
import net.fabricmc.fabric.api.networking.v1.ServerConfigurationNetworking;
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
    private final Checker checker;
    private final long deadline;
    private final OfferPayload offer;
    private Challenge sent;
    private volatile Answer answer;
    private volatile boolean badAnswer;

    JoinCheck(ServerConfigurationPacketListenerImpl handler, Checker checker, int timeoutSeconds, OfferPayload offer) {
        this.offer = offer;
        this.handler = handler;
        this.checker = checker;
        this.deadline = System.nanoTime() + timeoutSeconds * 1_000_000_000L;
    }

    @Override
    public void start(Consumer<Packet<?>> sender) {
        sent = checker.newChallenge();
        // Only the first answer counts: one check per join.
        ServerConfigurationNetworking.registerReceiver(handler, CheckPayload.TYPE, (payload, context) -> {
            if (answer != null || badAnswer) {
                return;
            }
            try {
                answer = Answer.decode(payload.data());
            } catch (BadInput e) {
                badAnswer = true;
            }
        });
        if (offer != null && ServerConfigurationNetworking.canSend(handler, OfferPayload.TYPE)) {
            // First, so the player's Companion can offer the update if the check finds them behind.
            sender.accept(ServerConfigurationNetworking.createClientboundPacket(offer));
        }
        sender.accept(ServerConfigurationNetworking.createClientboundPacket(new CheckPayload(sent.encode())));
    }

    @Override
    public boolean tick() {
        Answer got = answer;
        if (got == null && !badAnswer && System.nanoTime() < deadline) {
            return false; // still waiting
        }
        ServerConfigurationNetworking.unregisterReceiver(handler, CheckPayload.TYPE.id());
        Checker.Verdict verdict = got == null ? null : checker.judge(sent, got);
        // false after a kick: the player is gone, nothing more to run.
        return CompanionServer.judged(handler.getOwner(), verdict, handler::disconnect);
    }

    @Override
    public Type type() {
        return TYPE;
    }
}
