package io.github.peyoteheadlights.mcsc.core

/**
 * All servers' search, groups and order, the same way as the desktop app:
 * the search narrows by name or group, servers with a group sit under its
 * heading (the rest under "Other servers", last), and moving one up or
 * down is among the servers shown.
 */
object ServerListing {
    fun matches(row: ServerRow, search: String): Boolean {
        val needle = search.trim().lowercase()
        return needle.isEmpty() || "${row.name} ${row.group.orEmpty()}".lowercase().contains(needle)
    }

    /** Headings and their servers, in list order. One heading "" when nobody made groups. */
    fun grouped(rows: List<ServerRow>, search: String): List<Pair<String, List<ServerRow>>> {
        val shown = rows.filter { matches(it, search) }
        val names = shown.mapNotNull { it.group?.ifBlank { null } }.distinct()
        if (names.isEmpty()) return if (shown.isEmpty()) emptyList() else listOf("" to shown)
        return (names + "").map { name -> name to shown.filter { it.group.orEmpty() == name } }
            .filter { it.second.isNotEmpty() }
    }

    /** The whole order after moving [id] one place [up] (or down) among [shown],
     * or null when it's already first or last. */
    fun moved(order: List<String>, shown: List<String>, id: String, up: Boolean): List<String>? {
        val at = shown.indexOf(id) + if (up) -1 else 1
        if (at !in shown.indices || shown.indexOf(id) < 0) return null
        val rest = order.filter { it != id }.toMutableList()
        rest.add(rest.indexOf(shown[at]) + if (up) 0 else 1, id)
        return rest
    }

    /** The server's own command names that start with what's typed, for the
     * chips above the console box. Only while the first word is typed. */
    fun completions(names: List<String>, typed: String, most: Int = 6): List<String> {
        val word = typed.trimStart()
        if (word.isEmpty() || word.contains(' ')) return emptyList()
        return names.filter { it.startsWith(word, ignoreCase = true) && !it.equals(word, ignoreCase = true) }.take(most)
    }
}
