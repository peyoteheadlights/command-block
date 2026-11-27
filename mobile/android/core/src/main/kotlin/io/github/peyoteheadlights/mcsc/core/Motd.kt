package io.github.peyoteheadlights.mcsc.core

/**
 * The MOTD builder's colour codes. Minecraft writes colours into the text
 * itself: a § and one letter or digit (§6 gold, §l bold, §r back to plain).
 * Same table as the desktop app (desktop/motd.py).
 */
object Motd {
    /** Minecraft's 16 text colours, by code, as 0xRRGGBB. */
    val COLORS: Map<Char, Int> = linkedMapOf(
        '0' to 0x000000, '1' to 0x0000AA, '2' to 0x00AA00, '3' to 0x00AAAA,
        '4' to 0xAA0000, '5' to 0xAA00AA, '6' to 0xFFAA00, '7' to 0xAAAAAA,
        '8' to 0x555555, '9' to 0x5555FF, 'a' to 0x55FF55, 'b' to 0x55FFFF,
        'c' to 0xFF5555, 'd' to 0xFF55FF, 'e' to 0xFFFF55, 'f' to 0xFFFFFF,
    )

    /** The style buttons: code and its words' key. */
    val STYLES: List<Pair<Char, String>> = listOf(
        'l' to "motd.bold", 'o' to "motd.italic", 'n' to "motd.underline", 'm' to "motd.strike", 'r' to "motd.plain",
    )

    /** What the server list shows text in before any colour. */
    const val PLAIN = 0xAAAAAA

    /** The keys that get the builder: Java's MOTD, a Bedrock server's name. */
    val KEYS = setOf("motd", "server-name")

    data class Piece(
        val text: String,
        val color: Int = PLAIN,
        val bold: Boolean = false,
        val italic: Boolean = false,
        val underline: Boolean = false,
        val strike: Boolean = false,
    )

    /** The text cut where its style changes. A colour code ends bold,
     * italic and the rest, the way Java Edition draws it. */
    fun pieces(motd: String): List<Piece> {
        val out = mutableListOf<Piece>()
        var style = Piece("")
        var i = 0
        while (i < motd.length) {
            if (motd[i] == '§' && i + 1 < motd.length) {
                val code = motd[i + 1].lowercaseChar()
                style = when {
                    code in COLORS -> Piece("", color = COLORS.getValue(code))
                    code == 'l' -> style.copy(bold = true)
                    code == 'o' -> style.copy(italic = true)
                    code == 'n' -> style.copy(underline = true)
                    code == 'm' -> style.copy(strike = true)
                    code == 'r' -> Piece("")
                    else -> style // unknown codes (and §k, scrambled) show nothing
                }
                i += 2
                continue
            }
            val end = motd.indexOf('§', i).let { if (it < 0) motd.length else it }
            out += style.copy(text = motd.substring(i, end))
            i = end
        }
        return out
    }
}
