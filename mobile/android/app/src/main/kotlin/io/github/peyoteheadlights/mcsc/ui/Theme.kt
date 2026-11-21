package io.github.peyoteheadlights.mcsc.ui

import android.content.res.AssetManager
import android.provider.Settings
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import io.github.peyoteheadlights.mcsc.core.ServerColors
import io.github.peyoteheadlights.mcsc.core.Strings
import io.github.peyoteheadlights.mcsc.core.Tone
import io.github.peyoteheadlights.mcsc.data.SharedData

/** The shared color tokens (shared/theme.json) in the binder look, as in
 * the desktop app: the page is plain paper, and a server's color goes on
 * the band behind the sheet, the buttons and the edges of its screens. */
@Immutable
data class McscColors(
    val dark: Boolean,
    val desk: Color,
    val sheet: Color,
    val surface: Color,
    val sunken: Color,
    val float: Color,
    val text: Color,
    val text2: Color,
    val text3: Color,
    val accent: Color,
    val accentText: Color,
    val accentInk: Color,
    val accentBand: Color,
    val success: Color,
    val warning: Color,
    val danger: Color,
    val onDanger: Color,
    val neutral: Color,
    /** The line around a box: the server's color, half see-through. */
    val border: Color,
    /** The band behind the sheet (the top bar), and the words on it. */
    val band: Color,
    val bandText: Color,
) {
    fun tone(tone: Tone): Color = when (tone) {
        Tone.SUCCESS -> success
        Tone.WARNING -> warning
        Tone.DANGER -> danger
        Tone.NEUTRAL -> text2
    }
}

fun hexColor(hex: String?): Color? = ServerColors.rgb(hex)?.let { Color(it[0], it[1], it[2]) }

/** [tokens] is the theme, [look] what ServerColors.binder sets on it. */
fun colorsFor(tokens: Map<String, String>, look: Map<String, String>): McscColors {
    fun c(name: String): Color = hexColor(look["--$name"] ?: tokens[name]) ?: Color.Gray
    val paper = look["--paper"] ?: tokens["sheet"]
    val dark = ServerColors.luminance(ServerColors.rgb(paper) ?: intArrayOf(255, 255, 255)) < 0.18
    return McscColors(
        dark = dark,
        desk = c("desk"),
        sheet = c("paper"),
        surface = c("surface"),
        sunken = c("surface-sunken"),
        float = c("surface-float"),
        text = c("text-primary"),
        text2 = c("text-secondary"),
        text3 = c("text-tertiary"),
        accent = c("accent"),
        accentText = c("accent-text"),
        accentInk = c("accent-ink"),
        accentBand = c("accent-band"),
        success = c("success"),
        warning = c("warning"),
        danger = c("danger"),
        onDanger = c("on-danger"),
        neutral = c("neutral"),
        border = c("accent-ring").copy(alpha = 0.5f),
        band = c("band"),
        bandText = c("band-text"),
    )
}

/** Archivo, the heading font, from the app's own files (shared/fonts). */
private fun headings(assets: AssetManager): Typography {
    val archivo = FontFamily(
        Font("fonts/Archivo-Medium.ttf", assets, FontWeight.Medium),
        Font("fonts/Archivo-SemiBold.ttf", assets, FontWeight.SemiBold),
        Font("fonts/Archivo-Bold.ttf", assets, FontWeight.Bold),
    )
    val base = Typography()
    return base.copy(
        headlineLarge = base.headlineLarge.copy(fontFamily = archivo, fontWeight = FontWeight.Bold),
        headlineMedium = base.headlineMedium.copy(fontFamily = archivo, fontWeight = FontWeight.Bold),
        headlineSmall = base.headlineSmall.copy(fontFamily = archivo, fontWeight = FontWeight.Bold),
        titleLarge = base.titleLarge.copy(fontFamily = archivo, fontWeight = FontWeight.SemiBold),
        titleMedium = base.titleMedium.copy(fontFamily = archivo, fontWeight = FontWeight.SemiBold),
        titleSmall = base.titleSmall.copy(fontFamily = archivo, fontWeight = FontWeight.SemiBold),
    )
}

val LocalColors = staticCompositionLocalOf<McscColors> { error("McscTheme is missing") }
val LocalStrings = staticCompositionLocalOf<Strings> { error("McscTheme is missing") }

/** True when the phone's "Remove animations" setting is on: nothing glides,
 * things are just there. */
val LocalReduceMotion = staticCompositionLocalOf { false }

/** The shared theme name for the person's choice ("system" follows the phone). */
@Composable
fun themeName(choice: String): String =
    if (choice == "system") (if (isSystemInDarkTheme()) "dark" else "light") else choice

@Composable
fun McscTheme(shared: SharedData, theme: String, serverColor: String? = null, content: @Composable () -> Unit) {
    val tokens = remember(shared, theme) { shared.theme(theme) }
    val colors = remember(tokens, theme, serverColor) {
        colorsFor(tokens, ServerColors.binder(serverColor, tokens, theme))
    }
    val context = LocalContext.current
    val typography = remember(context) { headings(context.assets) }
    // "Remove animations" sets Android's animation scale to 0.
    val reduceMotion = Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
    val scheme = if (colors.dark) {
        darkColorScheme(
            primary = colors.accent, onPrimary = colors.accentText,
            secondary = colors.accentInk, onSecondary = colors.sheet,
            background = colors.sheet, onBackground = colors.text,
            surface = colors.sheet, onSurface = colors.text,
            surfaceVariant = colors.sunken, onSurfaceVariant = colors.text2,
            surfaceContainer = colors.surface, surfaceContainerLow = colors.surface,
            surfaceContainerHigh = colors.float, surfaceContainerHighest = colors.float,
            surfaceContainerLowest = colors.sunken,
            outline = colors.border.copy(alpha = 1f), outlineVariant = colors.border,
            error = colors.danger, onError = colors.onDanger,
            secondaryContainer = colors.accentBand, onSecondaryContainer = colors.text,
        )
    } else {
        lightColorScheme(
            primary = colors.accent, onPrimary = colors.accentText,
            secondary = colors.accentInk, onSecondary = colors.surface,
            background = colors.sheet, onBackground = colors.text,
            surface = colors.sheet, onSurface = colors.text,
            surfaceVariant = colors.sunken, onSurfaceVariant = colors.text2,
            surfaceContainer = colors.surface, surfaceContainerLow = colors.surface,
            surfaceContainerHigh = colors.float, surfaceContainerHighest = colors.float,
            surfaceContainerLowest = colors.surface,
            outline = colors.border.copy(alpha = 1f), outlineVariant = colors.border,
            error = colors.danger, onError = colors.onDanger,
            secondaryContainer = colors.accentBand, onSecondaryContainer = colors.text,
        )
    }
    CompositionLocalProvider(LocalColors provides colors, LocalReduceMotion provides reduceMotion) {
        MaterialTheme(colorScheme = scheme, typography = typography, content = content)
    }
}

/** A word from shared/strings.json, in Simple or Technical wording. */
@Composable
fun t(key: String, vararg values: Pair<String, Any?>): String = LocalStrings.current.t(key, *values)

@Composable
fun Message.text(): String = text(LocalStrings.current)
