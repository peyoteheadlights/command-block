package io.github.peyoteheadlights.companion.fabric;

import com.mojang.authlib.GameProfile;
import io.github.peyoteheadlights.companion.ServerSide;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.util.function.Consumer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.networking.v1.FabricServerConfigurationPacketListenerImpl;
import net.fabricmc.fabric.api.networking.v1.ServerConfigurationConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerConfigurationNetworking;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The server's side: connects common's ServerSide (the settings the app
 * writes, the check, rechecks while playing) to Fabric's events. Runs on
 * the server thread.
 */
public final class CompanionServer implements ModInitializer {
    static final Logger LOG = LoggerFactory.getLogger("Companion");
    private static final String SETTINGS_FILE = "commandblock-companion.json";

    /** null: no settings, so the Companion does nothing on this server. */
    private static ServerSide side;

    @Override
    public void onInitialize() {
        CheckPayload.register();
        OfferPayload.register();
        ServerLifecycleEvents.SERVER_STARTING.register(server -> side = ServerSide.load(
                FabricLoader.getInstance().getConfigDir().resolve(SETTINGS_FILE), server.getServerDirectory(),
                LOG::info, LOG::warn));
        ServerConfigurationConnectionEvents.CONFIGURE.register((handler, server) -> {
            if (side == null) {
                return; // no settings: the app hasn't made a modpack here
            }
            Consumer<String> kick = message -> handler.disconnect(Component.literal(message));
            GameProfile profile = handler.getOwner();
            if (!side.mayCheck(profile.id(), profile.name(), address(handler.connection.getRemoteAddress()), kick)) {
                return;
            }
            if (ServerConfigurationNetworking.canSend(handler, CheckPayload.TYPE)) {
                ((FabricServerConfigurationPacketListenerImpl) handler).addTask(new JoinCheck(handler, side));
            } else {
                side.judged(profile.id(), profile.name(), null, null, kick); // no Companion at all
            }
        });
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> {
            String line = side == null ? null : side.joined(handler.getPlayer().getUUID());
            if (line != null) {
                handler.getPlayer().sendSystemMessage(Component.literal(line));
            }
        });
        ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> {
            if (side != null) {
                side.left(handler.getPlayer().getUUID());
            }
        });
        ServerPlayNetworking.registerGlobalReceiver(CheckPayload.TYPE, (payload, context) -> {
            if (side != null) {
                side.answered(online(context.player()), payload.data());
            }
        });
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            if (side != null && server.getTickCount() % 20 == 0) { // once a second is enough
                side.tick(server.getPlayerList().getPlayers().stream().map(CompanionServer::online).toList());
            }
        });
    }

    private static ServerSide.Online online(ServerPlayer player) {
        return new ServerSide.Online(player.getUUID(), player.getGameProfile().name(),
                challenge -> ServerPlayNetworking.send(player, new CheckPayload(challenge)),
                message -> player.connection.disconnect(Component.literal(message)));
    }

    private static String address(SocketAddress remote) {
        return remote instanceof InetSocketAddress inet ? inet.getAddress().getHostAddress() : String.valueOf(remote);
    }
}
