package io.github.peyoteheadlights.mcsc.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.ServerColors
import io.github.peyoteheadlights.mcsc.core.States
import io.github.peyoteheadlights.mcsc.core.TopView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/*
 * The top view on a phone, as in the desktop app: the page tips back and
 * every server's tab stands behind it, one step apart. Pulling the band
 * (the top bar) down opens it and a tap on a tab opens that server.
 * Pushing it back up, a tap on the page or Back closes it.
 */

/** One tab's room in the top view, and how many show before the list scrolls. */
private val Step = 52.dp
private const val MOST = 6
private val Glide = CubicBezierEasing(0.22f, 1f, 0.36f, 1f)

/** Opens a server's screen by its id, or the list of all servers (null). */
val LocalOpenServer = staticCompositionLocalOf<(String?) -> Unit> { {} }

/** The binder a screen is in, for its [Bar]. Null on screens without one. */
val LocalBinder = staticCompositionLocalOf<BinderState?> { null }

/** How far the top view is open: 0 is the page as usual, 1 the top view. */
class BinderState(private val scope: CoroutineScope, private val reduceMotion: Boolean) {
    val at = Animatable(0f)

    /** How far the page drops when the top view is open, in pixels. */
    var distance = 1f
    private var wasOpen = false
    private var pulled = 0f

    fun pullStarted() {
        wasOpen = at.targetValue > 0.5f
        pulled = 0f
    }

    /** The page follows the finger. With "Remove animations" on it waits
     * until the finger lets go. */
    fun pullBy(delta: Float) {
        pulled += delta
        if (reduceMotion) return
        val to = ((if (wasOpen) 1f else 0f) + pulled / distance).coerceIn(0f, 1f)
        scope.launch { at.snapTo(to) }
    }

    fun pullEnded() = show(TopView.opens(wasOpen, pulled, distance))

    fun show(open: Boolean) {
        val to = if (open) 1f else 0f
        scope.launch {
            if (reduceMotion) at.snapTo(to) else at.animateTo(to, tween(TopView.MILLIS, easing = Glide))
        }
    }
}

/** A pull down on this opens the top view, a push up closes it. */
fun Modifier.pulls(binder: BinderState): Modifier = composed {
    draggable(
        state = rememberDraggableState { delta -> binder.pullBy(delta) },
        orientation = Orientation.Vertical,
        onDragStarted = { binder.pullStarted() },
        onDragStopped = { binder.pullEnded() },
    )
}

/**
 * Puts a screen in the binder. [current] is the server the screen is of,
 * or null for the list of all servers.
 */
@Composable
fun Binder(vm: AppViewModel, current: String?, content: @Composable () -> Unit) {
    val scope = rememberCoroutineScope()
    val reduceMotion = LocalReduceMotion.current
    val binder = remember(reduceMotion) { BinderState(scope, reduceMotion) }
    val open = LocalOpenServer.current
    val rows = vm.servers.list
    val strip: List<String?> = listOf<String?>(null) + rows.map { it.id }
    // Until the tabs are on screen and say how much room they take.
    val room = with(LocalDensity.current) { (Step * minOf(strip.size, MOST) + 8.dp).toPx() }
    if (binder.distance < room) binder.distance = room
    // Only "is it showing at all" redraws this; the moves are read where they are drawn.
    val showing by remember(binder) { derivedStateOf { binder.at.value > 0f } }

    BackHandler(enabled = binder.at.targetValue > 0f) { binder.show(false) }
    CompositionLocalProvider(LocalBinder provides binder) {
        Box(Modifier.fillMaxSize().background(LocalColors.current.desk).clipToBounds()) {
            if (showing) {
                val tabs = Modifier
                    .onSizeChanged { binder.distance = it.height.toFloat() }
                    .graphicsLayer { alpha = binder.at.value }
                Tabs(vm, TopView.order(strip, current), strip, current, tabs) { id ->
                    binder.show(false)
                    if (id != current) open(id)
                }
            }
            Box(
                Modifier.fillMaxSize().graphicsLayer {
                    // Hinged on its top edge, which goes back and down.
                    val at = binder.at.value
                    transformOrigin = TransformOrigin(0.5f, 0f)
                    translationY = at * binder.distance
                    rotationX = at * TopView.TIP
                    scaleX = 1f - 0.14f * at
                    scaleY = 1f - 0.14f * at
                },
            ) {
                content()
                if (showing) {
                    // While it is tipped back the page is only a way back.
                    Box(
                        Modifier
                            .fillMaxSize()
                            .pulls(binder)
                            .clickable(
                                interactionSource = remember { MutableInteractionSource() },
                                indication = null,
                                onClickLabel = t("mobile.common.close"),
                            ) { binder.show(false) },
                    )
                }
            }
        }
    }
}

/** The tabs, the furthest at the top and the open one at the bottom, right
 * behind the page. */
@Composable
private fun Tabs(
    vm: AppViewModel,
    order: List<String?>,
    strip: List<String?>,
    current: String?,
    modifier: Modifier,
    onPick: (String?) -> Unit,
) {
    val colors = LocalColors.current
    val theme = themeName(vm.theme)
    val tokens = remember(vm.shared, theme) { vm.shared.theme(theme) }
    val height = Step * minOf(order.size, MOST) + 8.dp
    Column(
        modifier
            .statusBarsPadding()
            .fillMaxWidth()
            .height(height)
            .verticalScroll(rememberScrollState(), reverseScrolling = true),
    ) {
        for ((depth, id) in order.withIndex().reversed()) {
            val row = vm.servers.list.firstOrNull { it.id == id }
            val look = remember(row?.color, tokens, theme) { ServerColors.binder(row?.color, tokens, theme) }
            // A tab keeps its place on the strip, so tabs don't hide each other.
            val place = strip.indexOf(id).coerceAtLeast(0) % 3
            Tab(
                name = row?.name ?: t("page.servers"),
                fill = hexColor(look["--tab-fill"]) ?: colors.neutral,
                text = hexColor(look["--tab-text"]) ?: colors.text,
                // Read before the last failed attempt: not current, so no dot.
                running = vm.servers.problem == null && row?.state == States.ONLINE,
                open = id == current,
                inset = 4.dp * minOf(depth, 5),
                place = place,
                onClick = { onPick(id) },
            )
        }
    }
}

/** One divider of the binder: its tab, and the top edge of its sheet. */
@Composable
private fun Tab(
    name: String,
    fill: Color,
    text: Color,
    running: Boolean,
    open: Boolean,
    inset: Dp,
    place: Int,
    onClick: () -> Unit,
) {
    val colors = LocalColors.current
    Column(Modifier.fillMaxWidth().height(Step).padding(horizontal = 12.dp + inset)) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .padding(start = 12.dp + 56.dp * place)
                .height(Step - 8.dp)
                .widthIn(min = 96.dp, max = 220.dp)
                .background(fill, RoundedCornerShape(topStart = 10.dp, topEnd = 10.dp))
                .clickable(role = Role.Tab, onClick = onClick)
                .semantics { selected = open }
                .padding(horizontal = 14.dp),
        ) {
            if (running) {
                // The green dot of a running server, as on the desktop app's tabs.
                Box(Modifier.size(8.dp).background(colors.success, CircleShape))
                Spacer(Modifier.width(8.dp))
            }
            Text(
                name,
                color = text,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = if (open) FontWeight.Bold else FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Box(
            Modifier
                .fillMaxWidth()
                .height(8.dp)
                .background(colors.sheet, RoundedCornerShape(topStart = 6.dp, topEnd = 6.dp)),
        )
    }
}
