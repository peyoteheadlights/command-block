package io.github.peyoteheadlights.companion.screen;

import io.github.peyoteheadlights.companion.Answer;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Challenge;
import io.github.peyoteheadlights.companion.Offer;
import io.github.peyoteheadlights.companion.Prover;
import io.github.peyoteheadlights.companion.Swap;
import java.io.IOException;
import java.lang.ref.WeakReference;
import java.nio.file.Path;
import java.util.List;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.network.chat.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The player's side of updating, shared by the Fabric, Forge and NeoForge mods
 * (only Minecraft's own classes): remembers this server's offer and the
 * last check, and makes the "Update modpack" button when the player is
 * behind. Network threads write, the game thread reads: volatile.
 *
 * <p>Everything is forgotten when a new join starts, and an offer only
 * counts for the modpack in place (same name): another server can't offer
 * to swap a friend's modpack for its own.
 */
public final class Updates {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    /** In the Companion's folder: the address of the server whose update was got ready (TitleExtras reads it). */
    static final String OFFERED_BY = "offered-by.txt";
    private static volatile Offer offer;
    private static volatile String offeredBy;
    private static volatile List<byte[]> serverHashes = List.of();
    private static volatile boolean behind;
    private static WeakReference<Screen> lastJoin = new WeakReference<>(null);

    private Updates() {}

    /** A join starts (its connecting screen opened): forget the last server's offer and check. */
    public static void joining(Screen connectScreen) {
        if (lastJoin.get() == connectScreen) {
            return; // the same join: the window was only resized
        }
        lastJoin = new WeakReference<>(connectScreen);
        offer = null;
        offeredBy = null;
        serverHashes = List.of();
        behind = false;
    }

    /** The server's offer arrived (it comes before the check). */
    public static void offered(byte[] packet) {
        try {
            offer = Offer.decode(packet);
            ServerData server = Minecraft.getInstance().getCurrentServer();
            offeredBy = server == null ? null : server.ip;
        } catch (BadInput e) {
            LOG.warn("The server's update offer didn't make sense ({})", e.getMessage());
        }
    }

    /**
     * The check was answered: is this player's modpack behind the server's?
     * Only when the offer is for the modpack in place (or there is none yet).
     */
    public static void checked(Challenge challenge, Answer answer, String playersFileJson) {
        Offer o = offer;
        String have = Prover.packName(playersFileJson);
        serverHashes = challenge.expected();
        behind = o != null && (have.isEmpty() || have.equals(o.name()))
                && (answer.loaded().contains(false) || !o.version().equals(Prover.packVersion(playersFileJson)));
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
        List<byte[]> hashes = serverHashes;
        String by = offeredBy;
        return Button.builder(Component.literal("Update to '" + o.name() + "' " + o.version()),
                b -> Minecraft.getInstance().gui.setScreen(new UpdateScreen(parent, o, hashes, by)))
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
