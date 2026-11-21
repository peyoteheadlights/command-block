package io.github.peyoteheadlights.companion.screen;

import com.mojang.blaze3d.platform.NativeImage;
import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Ping;
import io.github.peyoteheadlights.companion.Pictures;
import io.github.peyoteheadlights.companion.TitleSettings;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.SplashRenderer;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.ServerList;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.client.renderer.RenderPipelines;
import net.minecraft.client.renderer.texture.DynamicTexture;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.contents.TranslatableContents;
import net.minecraft.resources.Identifier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The title screen, shared by the Fabric, Forge and NeoForge mods: the small join
 * button beside Realms, the owner's background picture and the yellow
 * flavor text. Everything comes from the modpack's players file and the
 * two PNGs next to it; anything missing or odd leaves Minecraft's own.
 * Only runs on the game's thread (screen events), except the ping.
 */
public final class TitleExtras {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    static final String FOLDER = "commandblock-companion"; // in config/
    static final Identifier ICON = Identifier.fromNamespaceAndPath("commandblock_companion", "title/icon");
    static final Identifier BACKGROUND = Identifier.fromNamespaceAndPath("commandblock_companion", "title/background");
    static final long PING_EVERY_MS = 30_000;

    private static String loadedFrom;
    private static TitleSettings settings;
    private static int[] iconSize; // null: no icon, the name's first letters instead
    private static int[] backgroundSize; // null: Minecraft's panorama
    private static volatile Ping.Status status;
    private static volatile boolean reached;
    private static long pingedAt;

    private TitleExtras() {}

    /** Each time the title screen opens: read the settings and the pictures when the file changed. */
    public static void load(Path configDir, Path gameDir, String playersFileJson) {
        if (playersFileJson.equals(loadedFrom)) {
            return;
        }
        loadedFrom = playersFileJson;
        settings = TitleSettings.parse(playersFileJson);
        Path folder = configDir.resolve(FOLDER);
        if (settings.host() != null) {
            iconSize = texture(folder.resolve("icon.png"), Pictures.MAX_ICON_BYTES, ICON);
            followAddress(gameDir.resolve(".commandblock-companion").resolve("title-address.txt"));
        }
        backgroundSize = texture(folder.resolve("background.png"), Pictures.MAX_BACKGROUND_BYTES, BACKGROUND);
    }

    /** After the title screen's own buttons are made: the flavor text and the join button. */
    public static Button init(TitleScreen screen, List<? extends GuiEventListener> widgets) {
        if (settings == null) {
            return null;
        }
        if (settings.flavorText() != null) {
            screen.splash = new SplashRenderer(Component.literal(settings.flavorText()).withColor(0xFFFF00)); // Minecraft's splash yellow
        }
        if (settings.host() == null) {
            return null;
        }
        Button realms = null;
        for (GuiEventListener w : widgets) {
            if (w instanceof Button b && b.getMessage().getContents() instanceof TranslatableContents t
                    && t.getKey().equals("menu.online")) {
                realms = b;
            }
        }
        if (realms == null) {
            return null; // another mod moved things around: leave the screen alone
        }
        ping();
        ServerButton button = new ServerButton(realms.getX() + realms.getWidth() + 4, realms.getY(),
                b -> join(screen), settings.name(), iconSize, TitleExtras::hover);
        button.active = realms.active; // same rule as Multiplayer and Realms
        return button;
    }

    /** Before the title screen draws: the owner's picture over the panorama, like a cover photo. */
    public static void drawBackground(GuiGraphicsExtractor graphics, int width, int height) {
        int[] size = backgroundSize;
        if (size == null) {
            return;
        }
        // Cut the picture to the window's shape, keeping its middle.
        float scale = Math.max((float) width / size[0], (float) height / size[1]);
        int srcWidth = Math.round(width / scale);
        int srcHeight = Math.round(height / scale);
        graphics.blit(RenderPipelines.GUI_TEXTURED, BACKGROUND, 0, 0, (size[0] - srcWidth) / 2f,
                (size[1] - srcHeight) / 2f, width, height, srcWidth, srcHeight, size[0], size[1]);
    }

    private static String hover() {
        String name = settings.name().isEmpty() ? settings.address() : settings.name();
        return name + "\n" + (reached || status != null ? TitleSettings.statusLine(status) : "Checking...");
    }

    private static void join(TitleScreen screen) {
        Minecraft mc = Minecraft.getInstance();
        String address = settings.address();
        String name = settings.name().isEmpty() ? address : settings.name();
        ConnectScreen.startConnecting(screen, mc, ServerAddress.parseString(address),
                new ServerData(name, address, ServerData.Type.OTHER), false, null);
    }

    /** At most every 30 seconds, off the game's thread; the hover text shows it next time. */
    private static void ping() {
        long now = System.currentTimeMillis();
        if (now - pingedAt < PING_EVERY_MS) {
            return;
        }
        pingedAt = now;
        String host = settings.host();
        int port = settings.port();
        CompletableFuture.runAsync(() -> {
            try {
                status = Ping.ping(host, port, 5000);
            } catch (IOException | BadInput e) {
                status = null;
            }
            reached = true;
        });
    }

    /** A checked PNG from the modpack as a texture; its size, or null when there's none. */
    /** "play.example.net" and "Play.Example.net:25565" are the same server. */
    static boolean sameAddress(String a, String b) {
        return a.replaceFirst(":25565$", "").equalsIgnoreCase(b.replaceFirst(":25565$", ""));
    }

    private static int[] texture(Path file, long maxBytes, Identifier id) {
        if (!Files.isRegularFile(file)) {
            return null;
        }
        try {
            byte[] data = Pictures.read(file, maxBytes);
            NativeImage image = NativeImage.read(data);
            Minecraft.getInstance().getTextureManager().register(id, new DynamicTexture(id::toString, image));
            return new int[] {image.getWidth(), image.getHeight()};
        } catch (IOException | BadInput e) {
            LOG.warn("Not showing {}: {}", file.getFileName(), e.getMessage());
            return null;
        }
    }

    /**
     * The owner's address changed: friends' server list entries with the old
     * address get the new one. Only entries that had the old address change,
     * and only when the update came from the server at the old address (or
     * not through the Companion at all, from the player's own launcher):
     * another server's update can't point a friend's entry at itself.
     */
    private static void followAddress(Path remembered) {
        String now = settings.address();
        Path offeredBy = remembered.resolveSibling(Updates.OFFERED_BY);
        try {
            String before = Files.isRegularFile(remembered)
                    ? Files.readString(remembered, StandardCharsets.UTF_8).strip() : "";
            String from = Files.isRegularFile(offeredBy) ? Files.readString(offeredBy, StandardCharsets.UTF_8).strip() : null;
            if (!before.isEmpty() && !before.equals(now) && (from == null || sameAddress(from, before))) {
                ServerList list = new ServerList(Minecraft.getInstance());
                list.load();
                boolean changed = false;
                for (int i = 0; i < list.size(); i++) {
                    if (before.equalsIgnoreCase(list.get(i).ip)) {
                        list.get(i).ip = now;
                        changed = true;
                    }
                }
                if (changed) {
                    list.save();
                    LOG.info("Server list: {} now joins {}", before, now);
                }
            }
            if (!before.equals(now)) {
                Files.createDirectories(remembered.getParent());
                Files.writeString(remembered, now, StandardCharsets.UTF_8);
                Files.deleteIfExists(offeredBy); // used up
            }
        } catch (IOException e) {
            LOG.warn("Couldn't update the server list's address: {}", e.toString());
        }
    }
}
