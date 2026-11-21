package io.github.peyoteheadlights.mcsc.core

/**
 * The top view's rules that don't need a screen: the binder tips back and
 * every server's tab stands behind it, one step apart. On a phone it opens
 * by pulling the band (the top bar) down.
 */
object TopView {
    /** Degrees the page tips back. */
    const val TIP = 14f

    /** The design's time for a binder move, with its curve cubic-bezier(0.22, 1, 0.36, 1). */
    const val MILLIS = 620

    /** How far of the way a pull must go before letting go finishes it. */
    const val ENOUGH = 0.25f

    /** The tabs from the front one back: the open one, then the ones after
     * it on the strip, then around to the ones before it. */
    fun <T> order(strip: List<T>, open: T): List<T> {
        val at = strip.indexOf(open).coerceAtLeast(0)
        return strip.drop(at) + strip.take(at)
    }

    /** Whether the top view is open after a pull is let go. [pulled] is how
     * far the finger went (down is positive), [distance] the whole way. */
    fun opens(wasOpen: Boolean, pulled: Float, distance: Float): Boolean =
        if (wasOpen) pulled > -distance * ENOUGH else pulled > distance * ENOUGH
}
