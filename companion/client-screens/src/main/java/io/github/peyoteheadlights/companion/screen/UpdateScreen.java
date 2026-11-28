package io.github.peyoteheadlights.companion.screen;

import io.github.peyoteheadlights.companion.BadInput;
import io.github.peyoteheadlights.companion.Install;
import io.github.peyoteheadlights.companion.Offer;
import io.github.peyoteheadlights.companion.Plan;
import io.github.peyoteheadlights.companion.Swap;
import java.io.IOException;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Checkbox;
import net.minecraft.client.gui.components.StringWidget;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;

/**
 * Rule 1, "always asked first": shows what an update would add and
 * remove, with ticks, and only then downloads. Nothing goes in until the
 * game closes. Built from Minecraft's own widgets only.
 */
final class UpdateScreen extends Screen {
    private static final int SHOWN = 10;
    private final Screen parent;
    private final Offer offer;
    private final List<byte[]> serverHashes;
    private final String offeredBy;
    private final Path game;
    private final String lastFailure;
    private final HttpClient http = Install.client();
    private Plan plan;
    private final Map<Plan.Item, Boolean> ticks = new LinkedHashMap<>();
    private volatile String status = "Getting the modpack's list from the server...";
    private volatile boolean busy = true;
    private volatile boolean done;

    UpdateScreen(Screen parent, Offer offer, List<byte[]> serverHashes, String offeredBy) {
        super(Component.literal("Update '" + offer.name() + "' to " + offer.version()));
        this.parent = parent;
        this.offer = offer;
        this.serverHashes = serverHashes;
        this.offeredBy = offeredBy;
        this.game = net.minecraft.client.Minecraft.getInstance().gameDirectory.toPath();
        this.lastFailure = Swap.lastFailure(game); // the swap ran after the game closed: say so here
        work(this::read);
    }

    private interface Job {
        void run() throws IOException, BadInput;
    }

    /** Downloads and hashing run off the game's thread; the screen is rebuilt on it after. */
    private void work(Job job) {
        busy = true;
        CompletableFuture.runAsync(() -> {
            try {
                job.run();
            } catch (IOException | BadInput | RuntimeException e) {
                status = "That didn't work: " + e.getMessage() + ". Nothing was changed.";
                Updates.LOG.warn("Modpack update: {}", e.toString());
            }
            busy = false;
            minecraft().execute(this::rebuildWidgets);
        });
    }

    private void read() throws IOException, BadInput {
        Path mrpack = Install.fetchPack(http, offer, game);
        Path playersFile = game.resolve("config").resolve("commandblock-companion-client.json");
        List<String> old = Files.isRegularFile(playersFile)
                ? Plan.packFiles(Files.readString(playersFile, StandardCharsets.UTF_8))
                : List.of();
        Plan p = Plan.read(mrpack, game.resolve("mods"), serverHashes, old);
        p.items().forEach(i -> ticks.put(i, i.ticked()));
        plan = p;
        status = p.items().isEmpty() ? "Nothing to change: you have every file already." : null;
    }

    private void download() throws IOException, BadInput {
        List<Plan.Item> ticked = new ArrayList<>();
        ticks.forEach((item, on) -> {
            if (on) {
                ticked.add(item);
            }
        });
        Install.prepare(http, plan, ticked, game.resolve(Install.FOLDER).resolve("modpack.mrpack"), game,
                (file, n, total) -> status = "Downloading " + file + " (" + n + " of " + total + ")");
        // So the title screen moves server list entries only for the server this update came from.
        Files.writeString(game.resolve(Install.FOLDER).resolve(Updates.OFFERED_BY), offeredBy == null ? "" : offeredBy,
                StandardCharsets.UTF_8);
        done = true;
        status = "Ready. It goes in when you close the game, and your own mods stay as they are.";
    }

    @Override
    protected void init() {
        int x = width / 2 - 160;
        int y = 20;
        add(new StringWidget(x, y, 320, 12, getTitle(), font));
        y += 18;
        if (lastFailure != null && !done) {
            add(new StringWidget(x, y, 320, 12, Component.literal("Your last update didn't go in. " + lastFailure), font));
            y += 18;
        }
        if (busy || status != null || done) {
            add(new StringWidget(x, y, 320, 12, Component.literal(status == null ? "" : status), font));
            y += 24;
        }
        if (!busy && !done && plan != null && !plan.items().isEmpty()) {
            int i = 0;
            for (Map.Entry<Plan.Item, Boolean> e : ticks.entrySet()) {
                if (i++ == SHOWN) {
                    break;
                }
                Plan.Item item = e.getKey();
                add(Checkbox.builder(Component.literal(label(item)), font)
                        .pos(x, y)
                        .maxWidth(320)
                        .selected(e.getValue())
                        .onValueChange((box, on) -> ticks.put(item, on))
                        .build());
                y += 22;
            }
            int more = ticks.size() - SHOWN;
            String rest = (more > 0 ? "And " + more + " more, as ticked. " : "")
                    + (plan.skipped().isEmpty() ? "" : plan.skipped().size() + " file(s) can't be added: " + plan.skipped().get(0));
            if (!rest.isEmpty()) {
                add(new StringWidget(x, y, 320, 12, Component.literal(rest), font));
                y += 18;
            }
            add(Button.builder(Component.literal("Download (goes in when you close the game)"), b -> {
                status = "Starting...";
                work(this::download);
                rebuildWidgets();
            }).bounds(x, y, 320, 20).build());
            y += 24;
        }
        if (!busy && !done && Swap.canGoBack(game)) {
            add(Button.builder(Component.literal("Go back to the modpack before the last update"), b -> {
                try {
                    Swap.askGoBack(game);
                    done = true;
                    status = "Going back when you close the game.";
                } catch (IOException e) {
                    status = "Couldn't: " + e.getMessage();
                }
                rebuildWidgets();
            }).bounds(x, y, 320, 20).build());
            y += 24;
        }
        add(Button.builder(Component.literal(done ? "Done" : "Cancel"), b -> onClose()).bounds(x, y, 320, 20).build());
    }

    private static String label(Plan.Item item) {
        if (item.change() == Plan.Change.REMOVE) {
            return "Remove " + item.file() + " (the modpack doesn't use it any more)";
        }
        return switch (item.source()) {
            case MODRINTH -> "Add " + item.file() + " (from Modrinth)";
            case CURSEFORGE -> "Add " + item.file() + " (from CurseForge)";
            case OWN -> "Add " + item.file() + ": not from Modrinth or CurseForge. Only add this if you trust the owner";
        };
    }

    private <T extends net.minecraft.client.gui.components.events.GuiEventListener
            & net.minecraft.client.gui.components.Renderable
            & net.minecraft.client.gui.narration.NarratableEntry> void add(T widget) {
        addRenderableWidget(widget);
    }

    private net.minecraft.client.Minecraft minecraft() {
        return net.minecraft.client.Minecraft.getInstance();
    }

    @Override
    public void onClose() {
        minecraft().gui.setScreen(parent);
    }
}
