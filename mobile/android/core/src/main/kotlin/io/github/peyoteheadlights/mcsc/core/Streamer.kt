package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Streamer mode: addresses, folder paths and keys hidden on screen. The
 * rules are shared/streamer.json, the same file the desktop app reads.
 */
class Streamer(json: String) {
    private val root = Json.parseToJsonElement(json) as JsonObject
    val hidden: String = root.getValue("hidden").jsonPrimitive.content
    private val pattern = Regex(
        (root.getValue("patterns") as JsonArray).joinToString("|") { "(?:${it.jsonPrimitive.content})" },
    )

    /** [text] with every address, path and key swapped for dots. */
    fun hide(text: String): String = pattern.replace(text, Regex.escapeReplacement(hidden))

    /** The file's own examples: (shown, hidden). */
    val cases: List<Pair<String, String>> = (root["cases"] as? JsonArray).orEmpty().map {
        val pair = it as JsonArray
        pair[0].jsonPrimitive.content to pair[1].jsonPrimitive.content
    }
}
