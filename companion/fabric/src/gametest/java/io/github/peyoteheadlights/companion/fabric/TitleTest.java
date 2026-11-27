package io.github.peyoteheadlights.companion.fabric;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.screens.TitleScreen;

/**
 * The title screen with a modpack's players file and pictures in the
 * config folder: the join button beside Realms, the owner's flavor text
 * and background. Nothing listens on 127.0.0.1:9, so the button's hover
 * says it can't reach the server. The screenshot shows it all.
 */
public final class TitleTest implements FabricClientGameTest {
    @Override
    public void runTest(ClientGameTestContext context) {
        Path config = FabricLoader.getInstance().getConfigDir();
        copy("commandblock-companion-client.json", config);
        copy("commandblock-companion/icon.png", config);
        copy("commandblock-companion/background.png", config);
        context.setScreen(TitleScreen::new);
        context.waitFor(mc -> mc.gui.screen() instanceof TitleScreen t && joinButton(t) != null, 100);
        check(context.computeOnClient(mc -> ((TitleScreen) mc.gui.screen()).splash != null), "the flavor text is set");
        context.waitTicks(60); // the ping fails, the fade-in ends
        context.takeScreenshot("companion-title-screen");
        // The next tests (JoinTest) join worlds; a players file would change their versions.
        try {
            Files.delete(config.resolve("commandblock-companion-client.json"));
        } catch (IOException e) {
            throw new AssertionError(e);
        }
    }

    /** The square button beside Realms: 20 wide, "FR" for Friends under its icon. */
    private static AbstractWidget joinButton(TitleScreen screen) {
        for (AbstractWidget w : Screens.getWidgets(screen)) {
            if (w.getWidth() == 20 && w.getMessage().getString().equals("FR")) {
                return w;
            }
        }
        return null;
    }

    private static void copy(String name, Path config) {
        try (InputStream in = TitleTest.class.getResourceAsStream("/title-test/" + name)) {
            Path target = config.resolve(name);
            Files.createDirectories(target.getParent());
            Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException e) {
            throw new AssertionError(e);
        }
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError(what);
        }
    }
}
