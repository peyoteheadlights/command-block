package io.github.peyoteheadlights.companion;

import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.Map;

/**
 * At most N checks a minute from one IP address, so nobody can make a
 * weak PC hash files all day. Works with the app's Join protection.
 */
public final class Gate {
    private static final long MINUTE = 60_000L;
    private static final int FORGET_AFTER = 1000; // addresses kept before old ones are dropped

    private final int perMinute;
    private final Map<String, ArrayDeque<Long>> seen = new HashMap<>();

    public Gate(int perMinute) {
        this.perMinute = perMinute;
    }

    /** True if this address may start a check now; counts it if so. */
    public synchronized boolean allow(String ip, long nowMillis) {
        if (seen.size() > FORGET_AFTER) {
            seen.values().removeIf(times -> times.isEmpty() || times.peekLast() <= nowMillis - MINUTE);
        }
        ArrayDeque<Long> times = seen.computeIfAbsent(ip, k -> new ArrayDeque<>());
        while (!times.isEmpty() && times.peekFirst() <= nowMillis - MINUTE) {
            times.pollFirst();
        }
        if (times.size() >= perMinute) {
            return false;
        }
        times.addLast(nowMillis);
        return true;
    }

    synchronized int remembered() {
        return seen.size();
    }
}
