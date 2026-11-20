package io.github.peyoteheadlights.mcsc.core

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One of the three Store ranks the app makes in LuckPerms. */
@Serializable
data class StoreRank(val id: String = "", val name: String = "", val made: Boolean = false)

/** What the last check read from Tebex; never the key. */
@Serializable
data class StoreFacts(
    val name: String? = null,
    val link: String? = null,
    val currency: String? = null,
)

/** One package on Tebex; [rank] when it matches a Store rank. */
@Serializable
data class StorePackage(val name: String = "", val price: Double? = null, val rank: String? = null)

/** The Store tab (agent/store.py). The answer never holds the key. */
@Serializable
data class StoreView(
    val offered: Boolean = false,
    val reason: String? = null,
    @SerialName("needs_type") val needsType: Boolean = false,
    val connected: Boolean = false,
    @SerialName("key_saved") val keySaved: Boolean = false,
    @SerialName("offline_warning") val offlineWarning: String? = null,
    @SerialName("ranks_by_hand") val ranksByHand: Boolean = false,
    val ranks: List<StoreRank> = emptyList(),
    val store: StoreFacts = StoreFacts(),
    /** Null until "Check my store" read them. */
    val packages: List<StorePackage>? = null,
    @SerialName("buy_command") val buyCommand: String = "/store",
    val checklist: String = "",
    @SerialName("never_sold") val neverSold: List<String> = emptyList(),
    @SerialName("gameplay_warning") val gameplayWarning: String = "",
    val fees: String = "",
    @SerialName("open_tebex") val openTebex: String = "",
    /** After a check: packages the app didn't make, seen for the first time. */
    val remind: List<String> = emptyList(),
)

/** The Store tab's rules, as the desktop app has them (desktop/store.py). */
object Store {
    /** Only the owner, in Technical, on a Java server for a community that isn't a Test server. */
    fun shown(row: ServerRow?, me: Me?, technical: Boolean): Boolean =
        row != null && technical && me?.isOwner == true &&
            row.playedBy == "community" && !row.test && (row.edition ?: "java") == "java"

    /** Whether the key box and buttons work: offered and on a type that takes the plugin. */
    fun usable(view: StoreView): Boolean = view.offered && !view.needsType

    /** "Supporter  4.99 EUR", with ✓ when the package matches a Store rank. */
    fun packageLine(view: StoreView, p: StorePackage): String {
        val price = p.price?.let { "${plain(it)} ${view.store.currency.orEmpty()}".trimEnd() }.orEmpty()
        val mark = if (p.rank != null) "✓ " else ""
        return "$mark${p.name}  $price".trimEnd()
    }

    /** 5.0 as "5", 4.99 as "4.99" (Python's :g). */
    private fun plain(n: Double): String =
        if (n == Math.floor(n) && !n.isInfinite()) n.toLong().toString() else n.toString()
}
