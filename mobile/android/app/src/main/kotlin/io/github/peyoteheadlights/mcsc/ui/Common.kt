package io.github.peyoteheadlights.mcsc.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import io.github.peyoteheadlights.mcsc.core.Display
import io.github.peyoteheadlights.mcsc.core.ServerColors
import kotlinx.coroutines.delay

/** Other companies' sites and the GitHub forms open in the phone's browser
 * inside the app (Custom Tabs). Nothing of the app's sign-in goes with them. */
fun openLink(context: Context, url: String) {
    try {
        CustomTabsIntent.Builder().setShowTitle(true).build().launchUrl(context, Uri.parse(url))
    } catch (e: ActivityNotFoundException) {
        try {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (ignored: ActivityNotFoundException) {
            // No browser at all: nothing to open it with.
        }
    }
}

object Links {
    const val REPO = "https://github.com/peyoteheadlights/command-block"
    const val RELEASES = "$REPO/releases"
    const val PRIVACY = "$REPO/blob/main/PRIVACY.md"
    const val REPORT = "$REPO/issues/new?template=bug_report.yml"
    const val SUGGEST = "$REPO/issues/new?template=feature_request.yml"

    // Placeholders until the accounts exist; shared/links.json has the same.
    const val SPONSORS = "https://github.com/sponsors/peyoteheadlights"
    const val KOFI = "https://ko-fi.com/peyoteheadlights"
    const val TAILSCALE_ANDROID = "https://play.google.com/store/apps/details?id=com.tailscale.ipn"
    const val TAILSCALE_SHARE = "https://tailscale.com/kb/1084/sharing"

    /** Where an invited moderator gets the app: Google Play and the App Store once they exist. */
    const val APP_DOWNLOAD = RELEASES
}

/** Seconds since 1970, ticking every [every] ms, for "Checked 2m ago". */
@Composable
fun rememberNow(every: Long = 15_000): Double {
    var value by remember { mutableDoubleStateOf(now()) }
    LaunchedEffect(every) {
        while (true) {
            delay(every)
            value = now()
        }
    }
    return value
}

/** How round the sheet's and a box's corners are, and how thick a box's edge. */
val Corner = 14.dp
val Edge = 1.5.dp

/**
 * The top of a screen: the band in the server's color (copper's grey where
 * no server is open) with the screen's name on it, and under it the top
 * edge of the sheet, which lies on the band. On a screen inside a [Binder],
 * pulling the band down opens the top view.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Bar(title: String, onBack: (() -> Unit)? = null, actions: @Composable () -> Unit = {}) {
    val colors = LocalColors.current
    val binder = LocalBinder.current
    val servers = t("page.servers")
    Column(Modifier.background(colors.band).then(binder?.let { Modifier.pulls(it) } ?: Modifier)) {
        TopAppBar(
            title = {
                Text(
                    title,
                    modifier = Modifier.semantics {
                        heading()
                        // For TalkBack, which can't pull: the top view as an action.
                        if (binder != null) {
                            customActions = listOf(
                                CustomAccessibilityAction(servers) {
                                    binder.show(true)
                                    true
                                },
                            )
                        }
                    },
                )
            },
            navigationIcon = {
                if (onBack != null) {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = t("mobile.common.close"))
                    }
                }
            },
            actions = { actions() },
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = colors.band,
                scrolledContainerColor = colors.band,
                titleContentColor = colors.bandText,
                navigationIconContentColor = colors.bandText,
                actionIconContentColor = colors.bandText,
            ),
        )
        Box(
            Modifier
                .fillMaxWidth()
                .height(Corner)
                .background(colors.sheet, RoundedCornerShape(topStart = Corner, topEnd = Corner)),
        )
    }
}

/** A heading over a group. [info] is the sentence that explains the group,
 * behind an info dot. */
@Composable
fun SectionTitle(text: String, info: String? = null) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(start = 20.dp, end = 8.dp, top = if (info == null) 20.dp else 8.dp, bottom = if (info == null) 6.dp else 0.dp),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.titleSmall,
            color = LocalColors.current.text,
            modifier = Modifier.semantics { heading() },
        )
        if (info != null) InfoDot(info)
    }
}

/** A raised box of rows: only for a thing you act on as one piece. Its
 * edge is in the server's color. */
@Composable
fun Card(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalColors.current
    Surface(
        color = colors.surface,
        shape = RoundedCornerShape(Corner),
        border = BorderStroke(Edge, colors.border),
        shadowElevation = 2.dp,
        modifier = modifier.fillMaxWidth().padding(horizontal = 16.dp),
    ) {
        Column(content = content)
    }
}

/**
 * A small (i) next to a heading or a label. A tap shows the sentence that
 * explains it, and a tap anywhere else puts it away. (The desktop app shows
 * the same sentence on hover.)
 */
@Composable
fun InfoDot(words: String) {
    val colors = LocalColors.current
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(onClick = { open = !open }) {
            Icon(
                Icons.Outlined.Info,
                contentDescription = t("help.what_is_this"),
                tint = if (open) colors.accentInk else colors.text2,
                modifier = Modifier.size(20.dp),
            )
        }
        if (open) {
            val below = with(LocalDensity.current) { 44.dp.roundToPx() }
            Popup(
                alignment = Alignment.TopStart,
                offset = IntOffset(0, below),
                onDismissRequest = { open = false },
                properties = PopupProperties(focusable = true),
            ) {
                Surface(
                    color = colors.float,
                    shape = RoundedCornerShape(8.dp),
                    border = BorderStroke(1.dp, colors.border),
                    shadowElevation = 6.dp,
                    modifier = Modifier.padding(horizontal = 12.dp).widthIn(max = 280.dp),
                ) {
                    Text(
                        words,
                        color = colors.text,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.padding(12.dp).semantics { liveRegion = LiveRegionMode.Polite },
                    )
                }
            }
        }
    }
}

/** A player's head. A plain grey head for everyone until the app can get
 * real skins, as in the desktop app. */
@Composable
fun Face(side: Dp = 28.dp) {
    val colors = LocalColors.current
    Canvas(Modifier.size(side)) {
        val edge = Edge.toPx()
        val corner = CornerRadius(6.dp.toPx())
        drawRoundRect(colors.neutral, cornerRadius = corner)
        drawRoundRect(
            colors.border.copy(alpha = 1f),
            topLeft = Offset(edge / 2, edge / 2),
            size = Size(size.width - edge, size.height - edge),
            cornerRadius = corner,
            style = Stroke(edge),
        )
        // Two eyes, on the 8 by 8 grid a Minecraft face is drawn on.
        val cell = size.width / 8
        for (column in listOf(1.5f, 5f)) {
            drawRect(colors.sheet, topLeft = Offset(column * cell, 4 * cell), size = Size(1.5f * cell, cell))
        }
    }
}

/** A row with a checkbox at its end; the whole row ticks it. */
@Composable
fun Tick(
    title: String,
    checked: Boolean,
    subtitle: String? = null,
    enabled: Boolean = true,
    info: String? = null,
    onChange: (Boolean) -> Unit,
) {
    Box(Modifier.toggleable(value = checked, enabled = enabled, role = Role.Checkbox, onValueChange = onChange)) {
        Item(title, subtitle = subtitle, info = info, trailing = { Checkbox(checked = checked, onCheckedChange = null, enabled = enabled) })
    }
}

@Composable
fun Item(
    title: String,
    subtitle: String? = null,
    onClick: (() -> Unit)? = null,
    external: Boolean = false,
    leading: (@Composable () -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
    /** A sentence that only explains the row, behind an info dot. */
    info: String? = null,
) {
    val colors = LocalColors.current
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .let { if (onClick != null) it.clickable(role = Role.Button, onClick = onClick) else it }
            .padding(horizontal = 16.dp, vertical = 10.dp),
    ) {
        if (leading != null) {
            leading()
            Spacer(Modifier.width(12.dp))
        }
        Column(Modifier.weight(1f)) {
            if (info == null) {
                Text(title, style = MaterialTheme.typography.bodyLarge, color = colors.text)
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, style = MaterialTheme.typography.bodyLarge, color = colors.text, modifier = Modifier.weight(1f, fill = false))
                    InfoDot(info)
                }
            }
            if (subtitle != null) {
                Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = colors.text2)
            }
        }
        when {
            trailing != null -> trailing()
            external -> Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null, tint = colors.text3)
            onClick != null -> Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null, tint = colors.text3)
        }
    }
}

/**
 * A warning or tip with "Don't show again" (kept per account on the agent).
 * [key] is its words' key; a hidden one shows nothing. Never for questions
 * that protect a world.
 */
@Composable
fun HideableNote(vm: AppViewModel, key: String, text: String = t(key)) {
    if (key in vm.hidden) return
    Column {
        Note(text, color = LocalColors.current.warning)
        TextButton(onClick = { vm.hide(key) }, modifier = Modifier.padding(horizontal = 8.dp)) { Text(t("tips.dont_show")) }
    }
}

@Composable
fun Note(text: String, color: Color = LocalColors.current.text2, modifier: Modifier = Modifier) {
    Text(
        text,
        style = MaterialTheme.typography.bodyMedium,
        color = color,
        modifier = modifier.padding(horizontal = 20.dp, vertical = 6.dp),
    )
}

/** A problem said plainly, with what to do about it. */
@Composable
fun ProblemNote(message: Message, onRetry: (() -> Unit)? = null) {
    val colors = LocalColors.current
    Card(Modifier.padding(vertical = 8.dp)) {
        Text(
            message.text(),
            color = colors.danger,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.padding(16.dp),
        )
        if (onRetry != null) {
            TextButton(onClick = onRetry, modifier = Modifier.padding(start = 6.dp, bottom = 6.dp)) {
                Text(t("mobile.common.retry"))
            }
        }
    }
}

/** A server's color as a dot, with a fill and edge that read in every theme. */
@Composable
fun ServerDot(color: String?) {
    val badge = ServerColors.badge(color)
    Box(
        Modifier
            .size(14.dp)
            .background(hexColor(badge?.first) ?: LocalColors.current.neutral, CircleShape),
    )
}

/** The status mark: a dot in the state's color and its word. */
@Composable
fun StateText(label: String?) {
    val words = Display.state(label)
    val color = LocalColors.current.tone(words.tone)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(color, CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(
            t(words.key),
            color = color,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
fun Confirm(
    title: String,
    body: String,
    confirm: String,
    danger: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    /** The other button's words: Cancel when null. */
    dismiss: String? = null,
) {
    val colors = LocalColors.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(body) },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                colors = ButtonDefaults.textButtonColors(contentColor = if (danger) colors.danger else colors.accentInk),
            ) { Text(confirm) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(dismiss ?: t("mobile.common.cancel")) } },
        containerColor = colors.float,
        titleContentColor = colors.text,
        textContentColor = colors.text2,
    )
}

val ScreenPadding = PaddingValues(bottom = 24.dp)
val Gap = Arrangement.spacedBy(8.dp)
