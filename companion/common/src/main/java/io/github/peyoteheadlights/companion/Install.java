package io.github.peyoteheadlights.companion;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.List;
import java.util.zip.ZipFile;

/**
 * Gets an update ready without touching the mods folder: the modpack file
 * first, then each ticked file into a waiting folder, every one checked.
 * Swap moves them in after the game closes (Windows locks the jars of a
 * running game). Nothing downloaded is ever run.
 */
public final class Install {
    /** The Companion's own folder in the game folder. */
    public static final String FOLDER = ".commandblock-companion";
    static final long MAX_PACK_BYTES = 1L << 30;

    private Install() {}

    /** Told about each file as it's done, for a progress bar. */
    public interface Progress {
        void at(String file, int done, int total);
    }

    /** Downloads the modpack the offer points to. Its files are checked one by one later (Plan). */
    public static Path fetchPack(HttpClient http, Offer offer, Path game) throws IOException, BadInput {
        Path target = game.resolve(FOLDER).resolve("modpack.mrpack");
        Files.createDirectories(target.getParent());
        download(http, offer.link(), target, MAX_PACK_BYTES, null, null);
        return target;
    }

    /**
     * Puts the ticked files into the waiting folder and writes what Swap
     * will do. A file that doesn't match its hash stops everything: half an
     * update is never left waiting.
     */
    public static void prepare(HttpClient http, Plan plan, List<Plan.Item> ticked, Path mrpack, Path game, Progress progress)
            throws IOException, BadInput {
        Path waiting = game.resolve(FOLDER).resolve("waiting");
        clear(waiting);
        Files.createDirectories(waiting);
        JsonArray add = new JsonArray();
        JsonArray remove = new JsonArray();
        int done = 0;
        try (ZipFile zip = new ZipFile(mrpack.toFile())) {
            for (Plan.Item item : ticked) {
                Path target = Swap.inside(waiting, item.file());
                if (item.change() == Plan.Change.REMOVE) {
                    remove.add(item.file());
                } else if (item.source() == Plan.Source.OWN) {
                    // Hashed again: the modpack file could have changed since the list was made.
                    try (InputStream in = zip.getInputStream(zip.getEntry(item.zipEntry()))) {
                        copy(in, target, Settings.MAX_FILE_BYTES, "SHA-256", item.hash());
                    }
                    add.add(item.file());
                } else {
                    if (Plan.site(item.url()) != item.source()) {
                        throw new BadInput(item.file() + ": not from " + item.source()); // never trust a changed list
                    }
                    download(http, item.url(), target, Settings.MAX_FILE_BYTES, "SHA-512", item.hash());
                    onServerList(plan, target);
                    add.add(item.file());
                }
                progress.at(item.file(), ++done, ticked.size());
            }
        } catch (IOException | BadInput | RuntimeException e) {
            clear(waiting);
            throw e;
        }
        JsonObject what = new JsonObject();
        what.add("add", add);
        what.add("remove", remove);
        what.addProperty("players", plan.playersFile(ticked));
        Files.writeString(waiting.resolve(Swap.PLAN), new Gson().toJson(what), StandardCharsets.UTF_8);
    }

    /**
     * The modpack file comes over plain http, so a changed one could name any
     * file on Modrinth or CurseForge. Only jars the server's own check named
     * (it came over the game's connection) may go in.
     */
    static void onServerList(Plan plan, Path file) throws IOException, BadInput {
        if (!plan.onServerList(Hashes.ofFile(file))) {
            throw new BadInput(file.getFileName() + " isn't on the server's own list, so the modpack may have been changed on the way");
        }
    }

    /**
     * A GET streamed to disk with a size cap and, when given, checked by
     * hash. A redirect is followed only from a site's download server to
     * the same site's (CurseForge moves files between its two), never
     * anywhere else.
     */
    static void download(HttpClient http, String url, Path target, long max, String algorithm, byte[] want)
            throws IOException, BadInput {
        Plan.Source site = Plan.site(url);
        for (int hop = 0; hop < 3; hop++) {
            HttpRequest request = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofMinutes(5)).GET().build();
            HttpResponse<InputStream> response;
            try {
                response = http.send(request, HttpResponse.BodyHandlers.ofInputStream());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException("stopped");
            }
            try (InputStream in = response.body()) {
                int status = response.statusCode();
                String next = response.headers().firstValue("Location").orElse(null);
                if (status / 100 == 3 && site != null && next != null) {
                    url = URI.create(url).resolve(next).toString();
                    if (Plan.site(url) != site) {
                        throw new BadInput(target.getFileName() + ": the site sent it somewhere else");
                    }
                    continue;
                }
                if (status != 200) {
                    throw new IOException("the download said " + status);
                }
                copy(in, target, max, algorithm, want);
                return;
            }
        }
        throw new IOException("too many redirects");
    }

    /** The client for downloads: never follows redirects. */
    public static HttpClient client() {
        return HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(Duration.ofSeconds(15))
                .build();
    }

    private static void copy(InputStream in, Path target, long max, String algorithm, byte[] want)
            throws IOException, BadInput {
        MessageDigest d;
        try {
            d = algorithm == null ? null : MessageDigest.getInstance(algorithm);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        Path part = target.resolveSibling(target.getFileName() + ".part");
        long total = 0;
        try (OutputStream out = Files.newOutputStream(part)) {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) {
                total += n;
                if (total > max) {
                    throw new BadInput(target.getFileName() + " is too big");
                }
                if (d != null) {
                    d.update(buf, 0, n);
                }
                out.write(buf, 0, n);
            }
        } catch (IOException | BadInput e) {
            Files.deleteIfExists(part);
            throw e;
        }
        if (d != null && !MessageDigest.isEqual(d.digest(), want)) {
            Files.deleteIfExists(part);
            throw new BadInput(target.getFileName() + " isn't the file the modpack names");
        }
        Files.move(part, target, StandardCopyOption.REPLACE_EXISTING);
    }

    static void clear(Path folder) throws IOException {
        if (!Files.isDirectory(folder)) {
            return;
        }
        try (var files = Files.list(folder)) {
            for (Path f : files.toList()) {
                Files.delete(f); // the Companion's folders hold files only
            }
        }
        Files.delete(folder);
    }
}
