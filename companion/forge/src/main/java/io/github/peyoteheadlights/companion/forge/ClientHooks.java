package io.github.peyoteheadlights.companion.forge;

import io.github.peyoteheadlights.companion.screen.TitleExtras;
import io.github.peyoteheadlights.companion.screen.Updates;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.DisconnectedScreen;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraftforge.client.event.ScreenEvent;
import net.minecraftforge.event.GameShuttingDownEvent;
import net.minecraftforge.fml.ModList;
import net.minecraftforge.fml.loading.FMLPaths;

/**
 * The player's game only: the "Update modpack" button, the swap when the
 * game closes, and the owner's title screen.
 */
final class ClientHooks {
    private ClientHooks() {}

    static void register() {
        ScreenEvent.Init.Post.BUS.addListener(event -> {
            if (event.getScreen() instanceof ConnectScreen) {
                Updates.joining(event.getScreen()); // a new join: the last server's offer no longer counts
            }
            if (event.getScreen() instanceof DisconnectedScreen || event.getScreen() instanceof PauseScreen) {
                Button button = Updates.button(event.getScreen(), event.getScreen().width / 2 - 100, 8);
                if (button != null) {
                    event.addListener(button);
                }
            }
            if (event.getScreen() instanceof TitleScreen title) {
                TitleExtras.load(FMLPaths.CONFIGDIR.get(), FMLPaths.GAMEDIR.get(), Companion.playersFile());
                Button join = TitleExtras.init(title, event.getListenersList());
                if (join != null) {
                    event.addListener(join);
                }
            }
        });
        ScreenEvent.Render.Pre.BUS.addListener(event -> {
            if (event.getScreen() instanceof TitleScreen) {
                TitleExtras.drawBackground(event.getGuiGraphics(), event.getScreen().width, event.getScreen().height);
            }
        });
        // Fires on the player's game as it closes (the client's stop()).
        GameShuttingDownEvent.BUS.addListener(event -> Updates.closing(
                FMLPaths.GAMEDIR.get(),
                ModList.getModFileById("commandblock_companion").getFile().getFilePath()));
    }
}
