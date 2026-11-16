package io.github.peyoteheadlights.companion.fabric;

import io.github.peyoteheadlights.companion.Hashes;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Map;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.fabricmc.loader.api.metadata.ModOrigin;

/**
 * The jars in the game's mods folder that Fabric really loaded, by their
 * SHA-256. Asks the loader, not the folder: a jar that's there but didn't
 * load doesn't count. Done for you (Fabric plumbing). Hashed once per game.
 */
final class LoadedJars {
    private static Map<String, Path> found;

    private LoadedJars() {}

    /** SHA-256 (lowercase hex) to the jar. The first call reads every jar, so never call it on the render thread. */
    static synchronized Map<String, Path> get() {
        if (found == null) {
            found = Map.copyOf(read());
        }
        return found;
    }

    private static Map<String, Path> read() {
        Path mods = FabricLoader.getInstance().getGameDir().resolve("mods").toAbsolutePath().normalize();
        Map<String, Path> jars = new HashMap<>();
        for (ModContainer mod : FabricLoader.getInstance().getAllMods()) {
            ModOrigin origin = mod.getOrigin();
            if (origin.getKind() != ModOrigin.Kind.PATH) {
                continue; // inside another jar
            }
            for (Path path : origin.getPaths()) {
                Path jar = path.toAbsolutePath().normalize();
                if (!jar.startsWith(mods) || !Files.isRegularFile(jar)) {
                    continue; // only the mods folder is the modpack's business (not Minecraft or Java)
                }
                try {
                    jars.put(HexFormat.of().formatHex(Hashes.ofFile(jar)), jar);
                } catch (IOException e) {
                    CompanionClient.LOG.warn("Couldn't read {}: {}", jar.getFileName(), e.toString());
                }
            }
        }
        return jars;
    }
}
