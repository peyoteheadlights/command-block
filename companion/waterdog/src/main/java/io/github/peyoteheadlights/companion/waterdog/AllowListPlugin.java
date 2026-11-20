package io.github.peyoteheadlights.companion.waterdog;

import dev.waterdog.waterdogpe.event.defaults.PlayerAuthenticatedEvent;
import dev.waterdog.waterdogpe.network.protocol.user.LoginData;
import dev.waterdog.waterdogpe.plugin.Plugin;

/**
 * Keeps a plain Bedrock server's allow list behind WaterdogPE. The server
 * itself runs with allow-list=false there (playit's setup needs it), so this
 * turns players away right after WaterdogPE signed them in, before they
 * reach the server. config.json and allowlist.json are read again for every
 * join, so the owner turning the list on or off, or a name added in the
 * console, counts at once.
 */
public final class AllowListPlugin extends Plugin {
    @Override
    public void onEnable() {
        getProxy().getEventManager().subscribe(PlayerAuthenticatedEvent.class, this::check);
        // The agent stops WaterdogPE if this line doesn't come before playit's
        // "Registered" line: a plugin that didn't load must never leave the server open.
        getLogger().info(AllowList.READY);
    }

    private void check(PlayerAuthenticatedEvent event) {
        LoginData login = event.getLoginData();
        String xuid = login.getXuid() == null ? "" : login.getXuid();
        String why = AllowList.check(getDataFolder().toPath().resolve("config.json"),
                login.getDisplayName(), xuid, login.isXboxAuthed());
        if (why != null) {
            getLogger().info("Turned away {}: {}", login.getDisplayName(), why);
            event.setCancelReason(why);
            event.setCancelled(true);
        }
    }
}
