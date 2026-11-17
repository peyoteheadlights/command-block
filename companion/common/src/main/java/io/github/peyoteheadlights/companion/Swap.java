package io.github.peyoteheadlights.companion;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Moves a waiting update into the mods folder, after the game has closed
 * (a running game locks its jars on Windows). The game starts this as a
 * small separate Java program from the Companion's own jar when it closes
 * with an update waiting; it waits for the game to finish, then:
 * the old files go to "previous" (rule 5, for "Go back"), the waiting ones
 * go in, and the new players file replaces the old. If anything fails,
 * every move is undone. Only file names on the list, and only straight
 * inside the mods folder, are ever touched.
 */
public final class Swap {
    static final String PLAN = "plan.json";
    static final String GO_BACK = "go-back";
    static final String PLAYERS = "config/commandblock-companion-client.json";

    private Swap() {}

    /** args: the game's process id, the game folder. */
    public static void main(String[] args) throws Exception {
        long pid = Long.parseLong(args[0]);
        Path game = Path.of(args[1]).toAbsolutePath().normalize();
        ProcessHandle.of(pid).ifPresent(p -> {
            try {
                p.onExit().get(10, TimeUnit.MINUTES);
            } catch (Exception e) {
                // still running after 10 minutes: try anyway, a locked file just undoes it all
            }
        });
        Path log = game.resolve(Install.FOLDER).resolve("swap.log");
        String result;
        try {
            result = Files.exists(game.resolve(Install.FOLDER).resolve(GO_BACK)) ? back(game) : apply(game);
        } catch (IOException | BadInput | RuntimeException e) {
            result = "Nothing changed: " + e.getMessage();
        }
        Files.writeString(log, Instant.now() + " " + result + System.lineSeparator(), StandardCharsets.UTF_8);
    }

    /**
     * Starts this program for when the game closes. Call it as the game
     * closes, only when {@link #waiting} says there's something to do.
     * Runs the Companion's own jar with the game's own Java, nothing else.
     */
    public static void startAfterExit(Path companionJar, Path game) throws IOException {
        String java = ProcessHandle.current().info().command().orElse("java");
        String classPath = companionJar + File.pathSeparator + jarOf(JsonParser.class); // Gson comes with the game
        new ProcessBuilder(java, "-cp", classPath, Swap.class.getName(),
                Long.toString(ProcessHandle.current().pid()), game.toString())
                .redirectErrorStream(true)
                .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                .start();
    }

    private static Path jarOf(Class<?> c) throws IOException {
        try {
            return Path.of(c.getProtectionDomain().getCodeSource().getLocation().toURI());
        } catch (Exception e) {
            throw new IOException("can't find " + c.getSimpleName() + "'s jar", e);
        }
    }

    /** True when an update or a "Go back" waits for the game to close. */
    public static boolean waiting(Path game) {
        Path folder = game.resolve(Install.FOLDER);
        return Files.isRegularFile(folder.resolve("waiting").resolve(PLAN)) || Files.exists(folder.resolve(GO_BACK));
    }

    /**
     * Why the last swap changed nothing, or null when it worked (or never
     * ran). The swap runs after the game closed, so the next update screen
     * shows this: otherwise nobody would ever see it.
     */
    public static String lastFailure(Path game) {
        try {
            String line = Files.readString(game.resolve(Install.FOLDER).resolve("swap.log"), StandardCharsets.UTF_8).strip();
            int at = line.indexOf(" Nothing changed: ");
            return at < 0 ? null : line.substring(at + 1);
        } catch (IOException e) {
            return null;
        }
    }

    /** True when there's an older set to go back to. */
    public static boolean canGoBack(Path game) {
        return Files.isRegularFile(game.resolve(Install.FOLDER).resolve("previous").resolve(PLAN));
    }

    /** Asks for the older set back at the next close. */
    public static void askGoBack(Path game) throws IOException {
        Path folder = game.resolve(Install.FOLDER);
        Files.createDirectories(folder);
        Install.clear(folder.resolve("waiting")); // going back replaces any waiting update
        Files.writeString(folder.resolve(GO_BACK), "");
    }

    /** The waiting update goes in. Returns what happened, for the log. */
    static String apply(Path game) throws IOException, BadInput {
        Path folder = game.resolve(Install.FOLDER);
        Path waiting = folder.resolve("waiting");
        Path mods = game.resolve("mods");
        JsonObject plan = read(waiting.resolve(PLAN));
        List<String> add = names(plan, "add");
        List<String> remove = names(plan, "remove");
        String players = plan.get("players").getAsString();
        Path fresh = folder.resolve("previous.new");
        Install.clear(fresh);
        Files.createDirectories(fresh);
        Files.createDirectories(mods);

        List<Path[]> done = new ArrayList<>(); // each move, to undo in reverse
        List<String> movedOut = new ArrayList<>();
        try {
            List<String> out = new ArrayList<>(remove);
            out.addAll(add); // a file with the same name is replaced: keep the old one
            for (String name : out) {
                Path old = inside(mods, name);
                if (Files.isRegularFile(old)) {
                    move(old, inside(fresh, name), done);
                    movedOut.add(name);
                }
            }
            for (String name : add) {
                move(inside(waiting, name), inside(mods, name), done);
            }
            Path playersFile = game.resolve(PLAYERS);
            if (Files.isRegularFile(playersFile)) {
                move(playersFile, fresh.resolve("players.json"), done);
            }
            Files.createDirectories(playersFile.getParent());
            Files.writeString(playersFile, players, StandardCharsets.UTF_8);
            done.add(new Path[] {null, playersFile});
        } catch (IOException | BadInput | RuntimeException e) {
            undo(done);
            Install.clear(fresh);
            throw e;
        }
        JsonObject kept = new JsonObject();
        kept.add("added", array(add));
        kept.add("moved_out", array(movedOut));
        Files.writeString(fresh.resolve(PLAN), new Gson().toJson(kept), StandardCharsets.UTF_8);
        Install.clear(folder.resolve("previous"));
        Files.move(fresh, folder.resolve("previous"));
        Install.clear(waiting);
        return "Updated: " + add.size() + " added, " + movedOut.size() + " kept in previous";
    }

    /** The older set comes back, and the files the update added go. */
    static String back(Path game) throws IOException, BadInput {
        Path folder = game.resolve(Install.FOLDER);
        Path previous = folder.resolve("previous");
        Path mods = game.resolve("mods");
        JsonObject plan = read(previous.resolve(PLAN));
        List<String> added = names(plan, "added");
        List<String> movedOut = names(plan, "moved_out");
        Path undone = folder.resolve("undone");
        Install.clear(undone);
        Files.createDirectories(undone);
        List<Path[]> done = new ArrayList<>();
        try {
            for (String name : added) {
                Path now = inside(mods, name);
                if (Files.isRegularFile(now)) {
                    move(now, inside(undone, name), done);
                }
            }
            for (String name : movedOut) {
                move(inside(previous, name), inside(mods, name), done);
            }
            Path playersFile = game.resolve(PLAYERS);
            if (Files.isRegularFile(previous.resolve("players.json"))) {
                if (Files.isRegularFile(playersFile)) {
                    move(playersFile, undone.resolve("players.json"), done);
                }
                move(previous.resolve("players.json"), playersFile, done);
            }
        } catch (IOException | BadInput | RuntimeException e) {
            undo(done);
            throw e;
        }
        Install.clear(undone);
        Install.clear(previous);
        Files.delete(folder.resolve(GO_BACK));
        return "Went back: " + movedOut.size() + " back, " + added.size() + " taken out";
    }

    /** The path of a file name straight inside a folder; refuses anything else. */
    static Path inside(Path folder, String name) throws BadInput {
        if (!Settings.MOD_PATH.matcher("mods/" + name).matches() || name.startsWith(".")) {
            throw new BadInput("bad file name");
        }
        Path base = folder.toAbsolutePath().normalize();
        Path path = base.resolve(name).normalize();
        if (!base.equals(path.getParent())) {
            throw new BadInput("bad file name");
        }
        return path;
    }

    private static void move(Path from, Path to, List<Path[]> done) throws IOException {
        Files.move(from, to);
        done.add(new Path[] {from, to});
    }

    private static void undo(List<Path[]> done) {
        for (int i = done.size() - 1; i >= 0; i--) {
            Path[] m = done.get(i);
            try {
                if (m[0] == null) {
                    Files.deleteIfExists(m[1]); // a file we wrote
                } else {
                    Files.move(m[1], m[0]);
                }
            } catch (IOException e) {
                // keep undoing the rest
            }
        }
    }

    private static JsonObject read(Path file) throws IOException, BadInput {
        if (Files.size(file) > Settings.MAX_SETTINGS_BYTES * 2L) {
            throw new BadInput("plan too big");
        }
        try {
            return JsonParser.parseString(Files.readString(file, StandardCharsets.UTF_8)).getAsJsonObject();
        } catch (RuntimeException e) {
            throw new BadInput("plan doesn't make sense");
        }
    }

    private static List<String> names(JsonObject o, String key) throws BadInput {
        try {
            List<String> names = new ArrayList<>();
            for (JsonElement e : o.getAsJsonArray(key)) {
                names.add(e.getAsString());
            }
            return names;
        } catch (RuntimeException e) {
            throw new BadInput("plan doesn't make sense");
        }
    }

    private static JsonArray array(List<String> names) {
        JsonArray a = new JsonArray();
        names.forEach(a::add);
        return a;
    }
}
