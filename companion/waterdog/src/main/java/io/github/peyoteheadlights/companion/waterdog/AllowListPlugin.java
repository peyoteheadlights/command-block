package io.github.peyoteheadlights.companion.waterdog;

import dev.waterdog.waterdogpe.event.defaults.PlayerAuthenticatedEvent;
import dev.waterdog.waterdogpe.network.protocol.user.LoginData;
import dev.waterdog.waterdogpe.plugin.Plugin;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Keeps a plain Bedrock server's allow list behind WaterdogPE. The server
 * itself runs with allow-list=false there (playit's setup needs it), so this
 * turns players away right after WaterdogPE signed them in, before they
 * reach the server. allowlist.json is read again for every join, so a name
 * added in the console counts at once.
 */
public final class AllowListPlugin extends Plugin {
    /** The server's allowlist.json, or null when the server is open to everyone. */
    private Path list;
    /** True when config.json couldn't be read: then nobody joins. */
    private boolean broken;

    @Override
    public void onEnable() {
        try {
            String config = Files.readString(getDataFolder().toPath().resolve("config.json"),
                    StandardCharsets.UTF_8);
            list = AllowList.fromConfig(config);
        } catch (IOException | IllegalArgumentException e) {
            broken = true;
            getLogger().error("Nobody can join until Command Block writes this plugin's config.json again: {}",
                    e.getMessage());
        }
        getProxy().getEventManager().subscribe(PlayerAuthenticatedEvent.class, this::check);
    }

    private void check(PlayerAuthenticatedEvent event) {
        LoginData login = event.getLoginData();
        String why;
        if (broken) {
            why = AllowList.CANT_READ;
        } else {
            String text = null;
            if (list != null) {
                try {
                    text = Files.readString(list, StandardCharsets.UTF_8);
                } catch (IOException e) {
                    text = "not a list"; // can't be read: AllowList turns everyone away
                }
            }
            String xuid = login.getXuid() == null ? "" : login.getXuid();
            why = AllowList.refusal(text, login.getDisplayName(), xuid, login.isXboxAuthed());
        }
        if (why != null) {
            getLogger().info("Turned away {}: {}", login.getDisplayName(), why);
            event.setCancelReason(why);
            event.setCancelled(true);
        }
    }
}
