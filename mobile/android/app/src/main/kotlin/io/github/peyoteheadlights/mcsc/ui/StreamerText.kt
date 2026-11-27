package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.material3.LocalTextStyle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.TextUnit
import io.github.peyoteheadlights.mcsc.core.Streamer

/**
 * Streamer mode: addresses, folder paths and keys hidden on screen
 * (shared/streamer.json). The screens in this package call this Text
 * instead of material3's, so every word they show passes through here,
 * the same idea as the desktop app's one filter for the whole window.
 * Text boxes keep their real words, so saving never saves the dots.
 */
object StreamerMode {
    var rules: Streamer? = null
    var on: Boolean by mutableStateOf(false)

    fun shown(text: String): String = rules?.takeIf { on }?.hide(text) ?: text
}

@Composable
fun Text(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontStyle: FontStyle? = null,
    fontWeight: FontWeight? = null,
    fontFamily: FontFamily? = null,
    letterSpacing: TextUnit = TextUnit.Unspecified,
    textDecoration: TextDecoration? = null,
    textAlign: TextAlign? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    overflow: TextOverflow = TextOverflow.Clip,
    softWrap: Boolean = true,
    maxLines: Int = Int.MAX_VALUE,
    minLines: Int = 1,
    onTextLayout: ((TextLayoutResult) -> Unit)? = null,
    style: TextStyle = LocalTextStyle.current,
) = androidx.compose.material3.Text(
    text = StreamerMode.shown(text),
    modifier = modifier,
    color = color,
    fontSize = fontSize,
    fontStyle = fontStyle,
    fontWeight = fontWeight,
    fontFamily = fontFamily,
    letterSpacing = letterSpacing,
    textDecoration = textDecoration,
    textAlign = textAlign,
    lineHeight = lineHeight,
    overflow = overflow,
    softWrap = softWrap,
    maxLines = maxLines,
    minLines = minLines,
    onTextLayout = onTextLayout,
    style = style,
)

/** Styled text keeps its styles unless something in it has to be hidden. */
@Composable
fun Text(
    text: AnnotatedString,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontWeight: FontWeight? = null,
    textAlign: TextAlign? = null,
    overflow: TextOverflow = TextOverflow.Clip,
    maxLines: Int = Int.MAX_VALUE,
    style: TextStyle = LocalTextStyle.current,
) {
    val hidden = StreamerMode.shown(text.text)
    androidx.compose.material3.Text(
        text = if (hidden == text.text) text else AnnotatedString(hidden),
        modifier = modifier,
        color = color,
        fontWeight = fontWeight,
        textAlign = textAlign,
        overflow = overflow,
        maxLines = maxLines,
        style = style,
    )
}
