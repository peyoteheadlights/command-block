package io.github.peyoteheadlights.companion.screen;

import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Offer;
import io.github.peyoteheadlights.companion.Swap;
import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The player's side of updating, shared by the Fabric and NeoForge mods
 * (only Minecraft's own classes): remembers the server's offer and the
 * last check, and makes the "Update modpack" button when the player is
 * behind. Network threads write, the game thread reads: volatile.
 */
public final class Updates {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static volatile Offer offer;
    private static volatile List<byte[]> serverHashes = List.of();
    private static volatile boolean behind;

    private Updates() {}

    /** The server's offer arrived (it comes before the check). */
    public static void offered(byte[] packet) {
        try {
            offer = Offer.decode(packet);
        } catch (BadInput e) {
            LOG.warn("The server's update offer didn't make sense ({})", e.getMessage());
        }
    }

    /** The check was answered: is this player's modpack behind the server's? */
    public static void checked(Challenge challenge, Answer answer, String packVersion) {
        Offer o = offer;
        serverHashes = challenge.expected();
        behind = o != null && (answer.loaded().contains(false) || !o.version().equals(packVersion));
    }

    /**
     * The button for the disconnect and pause screens: "Update" when behind,
     * else "Go back" after an update (rule 5), else null.
     */
    public static Button button(Screen parent, int x, int y) {
        Offer o = offer;
        if (!behind || o == null) {
            Path game = Minecraft.getInstance().gameDirectory.toPath();
            if (!Swap.canGoBack(game) || Swap.waiting(game)) {
                return null;
            }
            return Button.builder(Component.literal("Go back to the modpack before the update"), b -> {
                try {
                    Swap.askGoBack(game);
                    b.setMessage(Component.literal("Goes back when you close the game"));
                    b.active = false;
                } catch (IOException e) {
                    b.setMessage(Component.literal("Couldn't: " + e.getMessage()));
                }
            }).bounds(x, y, 200, 20).build();
        }
        return Button.builder(Component.literal("Update to '" + o.name() + "' " + o.version()),
                b -> Minecraft.getInstance().gui.setScreen(new UpdateScreen(parent, o, serverHashes)))
                .bounds(x, y, 200, 20)
                .build();
    }

    /** As the game closes: start the swap if an update or a "Go back" is waiting. */
    public static void closing(Path game, Path companionJar) {
        if (!Swap.waiting(game)) {
            return;
        }
        try {
            Swap.startAfterExit(companionJar, game);
        } catch (IOException e) {
            LOG.warn("Couldn't start putting in the modpack update: {}", e.toString());
        }
    }
}
