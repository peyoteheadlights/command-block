package io.github.peyoteheadlights.companion.neoforge;

import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Checker;
import java.util.function.Consumer;
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
    private final Checker checker;
    private final long deadline;
    private Challenge sent;
    private Answer answer;
    private boolean badAnswer;

    JoinCheck(ServerConfigurationPacketListenerImpl handler, Checker checker, int timeoutSeconds) {
        this.handler = handler;
        this.checker = checker;
        this.deadline = System.nanoTime() + timeoutSeconds * 1_000_000_000L;
    }

    @Override
    public void run(Consumer<CustomPacketPayload> sender) {
        sent = checker.newChallenge();
        sender.accept(new CheckPayload(sent.encode()));
    }

    /** The player's answer. Only the first one counts: one check per join. */
    void answered(byte[] data) {
        if (answer != null || badAnswer) {
            return;
        }
        try {
            answer = Answer.decode(data);
        } catch (BadInput e) {
            badAnswer = true;
        }
    }

    @Override
    public boolean tick() {
        if (answer == null && !badAnswer && System.nanoTime() < deadline) {
            return false; // still waiting
        }
        Companion.joining.remove(handler);
        Checker.Verdict verdict = answer == null ? null : checker.judge(sent, answer);
        // false after a kick: the player is gone, nothing more to run.
        return Companion.judged(handler.getOwner(), verdict, handler::disconnect);
    }

    @Override
    public Type type() {
        return TYPE;
    }
}
