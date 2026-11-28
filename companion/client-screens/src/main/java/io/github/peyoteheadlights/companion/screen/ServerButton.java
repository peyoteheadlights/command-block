package io.github.peyoteheadlights.companion.screen;

import java.util.function.Supplier;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.renderer.RenderPipelines;
import net.minecraft.network.chat.Component;

/**
 * The small square join button beside Realms: the server's icon, or the
 * first letters of its name when the modpack has no icon.
 */
final class ServerButton extends Button {
    static final int SIZE = 20;
    private final int[] iconSize;
    private final Supplier<String> hover;
    private String shown = "";

    ServerButton(int x, int y, OnPress onPress, String name, int[] iconSize, Supplier<String> hover) {
        super(x, y, SIZE, SIZE, Component.literal(initials(name)), onPress, DEFAULT_NARRATION);
        this.iconSize = iconSize;
        this.hover = hover;
    }

    /** "Survival World" gives "SW"; one word gives its first two letters. */
    static String initials(String name) {
        String[] words = name.trim().split("\\s+");
        if (words.length >= 2 && !words[1].isEmpty()) {
            return ("" + words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
        }
        return words[0].isEmpty() ? "?" : words[0].substring(0, Math.min(2, words[0].length())).toUpperCase();
    }

    @Override
    protected void extractContents(GuiGraphicsExtractor graphics, int mouseX, int mouseY, float a) {
        String text = hover.get(); // the ping may have answered since the last frame
        if (!text.equals(shown)) {
            shown = text;
            setTooltip(Tooltip.create(Component.literal(text)));
        }
        extractDefaultSprite(graphics);
        if (iconSize == null) {
            extractDefaultLabel(graphics.textRendererForWidget(this, GuiGraphicsExtractor.HoveredTextEffects.NONE));
            return;
        }
        // The icon inside the button's frame, scaled down from 64x64.
        graphics.blit(RenderPipelines.GUI_TEXTURED, TitleExtras.ICON, getX() + 2, getY() + 2, 0f, 0f,
                SIZE - 4, SIZE - 4, iconSize[0], iconSize[1], iconSize[0], iconSize[1]);
    }
}
