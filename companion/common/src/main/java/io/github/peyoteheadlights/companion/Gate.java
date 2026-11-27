package io.github.peyoteheadlights.companion;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Map;

/**
 * At most N checks a minute from one IP address, so nobody can make a
 * weak PC hash files all day. Works with the app's Join protection.
 * IPv6 addresses count per /64 (one home gets a whole /64), like the app's
 * own limits.
 *
 * <p>Joins from this PC itself are never counted: behind Join protection
 * (or a tunnel on this PC) every player arrives from 127.0.0.1, so one
 * count for all of them would let one person keep everyone out. The join
 * guard already limits each real address before it passes a player on.
 */
public final class Gate {
    private static final long MINUTE = 60_000L;
    private static final int FORGET_AFTER = 1000; // addresses kept before old ones are dropped
    /** Never more addresses than this: in a flood, a new address waits until old ones are forgotten. */
    static final int MAX_REMEMBERED = 10_000;

    private final int perMinute;
    private final Map<String, ArrayDeque<Long>> seen = new HashMap<>();
    private long lastSweep = -MINUTE;

    public Gate(int perMinute) {
        this.perMinute = perMinute;
    }

    /** True if this address may start a check now; counts it if so. */
    public synchronized boolean allow(String ip, long nowMillis) {
        if (loopback(ip)) {
            return true;
        }
        // At most once a second: sweeping on every call made a flood slow every check.
        if (seen.size() > FORGET_AFTER && nowMillis - lastSweep >= 1000) {
            seen.values().removeIf(times -> times.isEmpty() || times.peekLast() <= nowMillis - MINUTE);
            lastSweep = nowMillis;
        }
        String key = key(ip);
        ArrayDeque<Long> times = seen.get(key);
        if (times == null) {
            if (seen.size() >= MAX_REMEMBERED) {
                return false;
            }
            times = new ArrayDeque<>();
            seen.put(key, times);
        }
        while (!times.isEmpty() && times.peekFirst() <= nowMillis - MINUTE) {
            times.pollFirst();
        }
        if (times.size() >= perMinute) {
            return false;
        }
        times.addLast(nowMillis);
        return true;
    }

    /** True for 127.x.x.x and ::1. Only a literal address is looked at, so no name lookup ever happens. */
    static boolean loopback(String ip) {
        if (!ip.matches("[0-9.]+|[0-9A-Fa-f.]*:[0-9A-Fa-f:.]*")) {
            return false;
        }
        try {
            return InetAddress.getByName(ip).isLoopbackAddress();
        } catch (UnknownHostException e) {
            return false;
        }
    }

    /** The address itself, or for IPv6 its first 64 bits. */
    static String key(String ip) {
        if (ip.indexOf(':') < 0) {
            return ip;
        }
        try {
            // A literal address: no name lookup happens.
            byte[] bytes = InetAddress.getByName(ip).getAddress();
            return bytes.length == 16 ? HexFormat.of().formatHex(Arrays.copyOf(bytes, 8)) + "::/64" : ip;
        } catch (UnknownHostException e) {
            return ip;
        }
    }

    synchronized int remembered() {
        return seen.size();
    }
}
