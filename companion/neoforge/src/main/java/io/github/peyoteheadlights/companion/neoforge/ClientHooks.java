package io.github.peyoteheadlights.companion.neoforge;

import io.github.peyoteheadlights.companion.screen.TitleExtras;
import io.github.peyoteheadlights.companion.screen.Updates;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.DisconnectedScreen;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.neoforged.fml.ModList;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.client.event.ScreenEvent;
import net.neoforged.neoforge.client.event.lifecycle.ClientStoppingEvent;
import net.neoforged.neoforge.common.NeoForge;

/**
 * The player's game only: the "Update modpack" button, the swap when the
 * game closes, and the owner's title screen.
 */
final class ClientHooks {
    private ClientHooks() {}

    static void register() {
        NeoForge.EVENT_BUS.addListener(ScreenEvent.Init.Post.class, event -> {
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
        NeoForge.EVENT_BUS.addListener(ScreenEvent.Render.Pre.class, event -> {
            if (event.getScreen() instanceof TitleScreen) {
                TitleExtras.drawBackground(event.getGuiGraphics(), event.getScreen().width, event.getScreen().height);
            }
        });
        NeoForge.EVENT_BUS.addListener(ClientStoppingEvent.class, event -> Updates.closing(
                FMLPaths.GAMEDIR.get(),
                ModList.get().getModFileById("commandblock_companion").getFile().getFilePath()));
    }
}
