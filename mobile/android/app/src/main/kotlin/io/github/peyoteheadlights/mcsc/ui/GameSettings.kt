package io.github.peyoteheadlights.mcsc.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.TextButton
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import io.github.peyoteheadlights.mcsc.core.Motd
import java.io.ByteArrayOutputStream
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.GameForm
import io.github.peyoteheadlights.mcsc.core.GameSetting
import io.github.peyoteheadlights.mcsc.core.Permissions
import io.github.peyoteheadlights.mcsc.core.RawText
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/**
 * Game settings: the PC page's fields in its groups, each with its hint.
 * The seed is shown but locked once the world exists; someone who may not
 * edit settings sees them all read-only. Save sends only what changed, and
 * a refused value (a port in use, a number out of range) says why under
 * the field. Saved while running, it offers Restart now, which asks first.
 */
@Composable
fun GameSettingsScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit) {
    val content = vm.pages(serverId).content
    val strings = vm.strings
    val canEdit = vm.canDo(Permissions.SETTINGS_GAME)
    var restarting by remember { mutableStateOf(false) }
    LaunchedEffect(serverId) {
        content.clearSaid()
        content.loadGame()
        content.loadIcon()
        content.loadRules()
    }

    ServerFrame(
        vm, serverId,
        title = t("page.game_settings"),
        onBack = onBack,
        refreshing = false,
        onRefresh = content::loadGame,
        overlay = {
            if (restarting) {
                Confirm(t("confirm.restart_title"), t("confirm.restart_body"), t("action.restart"), danger = false, onConfirm = {
                    restarting = false
                    content.gameRestarted()
                    vm.act(serverId, "restart")
                }, onDismiss = { restarting = false })
            }
        },
    ) {
        val read = content.game
        val settings = read.value
        item { Note(t("game.intro"), color = LocalColors.current.text, modifier = Modifier.padding(top = 8.dp)) }
        if (!canEdit && vm.me != null) item { Note(t("mobile.game.read_only"), color = LocalColors.current.text3) }
        read.problem?.let { problem -> item { ProblemNote(problem, onRetry = content::loadGame) } }
        if (settings == null) {
            if (read.problem == null) item { Note(t("mobile.common.loading")) }
            return@ServerFrame
        }
        if (!settings.exists) item { Card { Item(t("game.no_file")) } }
        if (settings.restartNeeded) item { Note(t("game.restart_needed"), color = LocalColors.current.warning) }
        else if (settings.running && canEdit) item { Note(t("game.after_restart"), color = LocalColors.current.text3) }

        for (group in GameForm.groups(settings)) {
            item { SectionTitle(t(group.titleKey)) }
            item {
                Card {
                    for (field in group.fields) {
                        Field(vm, field, content.edits[field.key] ?: GameForm.initial(field), GameForm.locked(field, canEdit), content.fieldProblems[field.key]) {
                            content.edit(field.key, it)
                        }
                    }
                }
            }
        }
        if (settings.icon != null) {
            item { SectionTitle(t("motd.icon")) }
            item { Card { IconCard(vm, content, canEdit) } }
        }
        item { SectionTitle(t("gamerules.title"), info = t("gamerules.hint")) }
        item { Card { RulesCard(content, canEdit) } }
        if (canEdit) {
            item {
                Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Button(onClick = { content.saveGame(strings) }, enabled = content.busy == null && content.gameChanged()) {
                        Text(t(if (content.busy == "game") "action.saving" else "game.save"))
                    }
                }
            }
            item { Note(t("game.copy_kept"), color = LocalColors.current.text3) }
        }
        if (canEdit && RawText.allowed(vm.me, vm.technical, Permissions.SETTINGS_GAME) && settings.exists) {
            item { SectionTitle(t("game.raw_title")) }
            item {
                Card {
                    var typed by remember(settings.text) { mutableStateOf(settings.text) }
                    RawEditor(
                        vm,
                        title = t("game.raw_label"),
                        hint = t("game.raw_hint", "file" to settings.file),
                        text = typed,
                        onText = { typed = it },
                        changes = { RawText.propertyChanges(settings.text, typed) },
                        busy = content.busy != null,
                        onSave = { content.saveGameText(typed, strings) },
                    )
                }
            }
        }
        item { SaidNote(content.said) }
        if (content.gameRestart && vm.canControl()) {
            item { Presses(listOf(Press(t("action.restart_now"), { restarting = true }))) }
        }
    }
}

/** One setting: a switch, choices, or a box for a number or words. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Field(vm: AppViewModel, field: GameSetting, value: String, locked: Boolean, problem: String?, onChange: (String) -> Unit) {
    val colors = LocalColors.current
    val strings = vm.strings
    val label = GameForm.LABELS[field.key]?.first?.let { t(it) } ?: field.key
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        when (field.kind) {
            "bool" -> Row(verticalAlignment = Alignment.CenterVertically) {
                Text(label, color = colors.text, modifier = Modifier.weight(1f))
                Switch(checked = value == "true", onCheckedChange = { onChange(it.toString()) }, enabled = !locked)
            }
            "choice" -> {
                Text(label, color = colors.text)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (choice in field.choices) {
                        FilterChip(
                            selected = value == choice,
                            onClick = { onChange(choice) },
                            enabled = !locked,
                            label = { Text(GameForm.choiceLabel(strings, choice)) },
                        )
                    }
                }
            }
            else -> if (field.key in Motd.KEYS) MotdField(vm, field, value, locked, problem, label, onChange) else OutlinedTextField(
                value = value,
                onValueChange = { typed -> onChange(field.maxLength?.let { typed.take(it) } ?: typed) },
                label = { Text(label) },
                singleLine = true,
                readOnly = locked,
                enabled = !locked,
                isError = problem != null,
                keyboardOptions = KeyboardOptions(keyboardType = if (field.kind == "int" || field.kind == "port") KeyboardType.Number else KeyboardType.Text),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        problem?.let { Text(it, color = colors.danger, modifier = Modifier.padding(top = 4.dp)) }
        GameForm.fileProblem(strings, field)?.let { Text(it, color = colors.warning, modifier = Modifier.padding(top = 4.dp)) }
        GameForm.hint(strings, field).takeIf { it.isNotBlank() }?.let { Text(it, color = colors.text2, modifier = Modifier.padding(top = 4.dp)) }
    }
}

/** The MOTD builder: the words (two lines on Java), a preview on the server
 * list's dark background, and buttons that add Minecraft's colour codes. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MotdField(vm: AppViewModel, field: GameSetting, value: String, locked: Boolean, problem: String?, label: String, onChange: (String) -> Unit) {
    val colors = LocalColors.current
    val previewWords = t("motd.preview")
    OutlinedTextField(
        value = value,
        onValueChange = { typed ->
            val lines = typed.split("\n").take(field.lines).joinToString("\n")
            onChange(field.maxLength?.let { lines.take(it) } ?: lines)
        },
        label = { Text(label) },
        singleLine = field.lines == 1,
        maxLines = field.lines,
        readOnly = locked,
        enabled = !locked,
        isError = problem != null,
        modifier = Modifier.fillMaxWidth(),
    )
    Box(
        Modifier.fillMaxWidth().padding(top = 8.dp)
            .background(Color(0xFF1D1D1D), RoundedCornerShape(6.dp)).padding(10.dp)
            .semantics { contentDescription = previewWords },
    ) {
        Text(
            buildAnnotatedString {
                for (piece in Motd.pieces(value)) {
                    val lines = listOfNotNull(
                        TextDecoration.Underline.takeIf { piece.underline },
                        TextDecoration.LineThrough.takeIf { piece.strike },
                    )
                    withStyle(
                        SpanStyle(
                            color = Color(0xFF000000.toInt() or piece.color),
                            fontWeight = if (piece.bold) FontWeight.Bold else null,
                            fontStyle = if (piece.italic) FontStyle.Italic else null,
                            textDecoration = if (lines.isEmpty()) null else TextDecoration.combine(lines),
                        ),
                    ) { append(piece.text) }
                }
            },
        )
    }
    if (locked) return
    Text(t("motd.colors"), color = colors.text2, modifier = Modifier.padding(top = 8.dp))
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for ((code, rgb) in Motd.COLORS) {
            val name = t("motd.color_$code")
            Box(
                Modifier.size(32.dp).background(Color(0xFF000000.toInt() or rgb), CircleShape)
                    .clickable(onClickLabel = name) { onChange(value + "\u00A7$code") }
                    .semantics { contentDescription = name },
            )
        }
    }
    FlowRow {
        for ((code, key) in Motd.STYLES) {
            TextButton(onClick = { onChange(value + "\u00A7$code") }) { Text(t(key)) }
        }
    }
}

/** The server icon: what it is now, Pick a picture (any picture: cut to a
 * square from its middle and made 64 x 64 PNG here), and Remove. */
@Composable
private fun IconCard(vm: AppViewModel, content: ContentPages, canEdit: Boolean) {
    val colors = LocalColors.current
    val context = LocalContext.current
    val strings = vm.strings
    var unreadable by remember { mutableStateOf(false) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val picture = runCatching { context.contentResolver.openInputStream(uri)?.use(BitmapFactory::decodeStream) }.getOrNull()
        unreadable = picture == null
        if (picture != null) content.saveIcon(iconPng(picture), strings)
    }
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        val shown = content.icon?.let { png ->
            runCatching { Base64.decode(png, Base64.DEFAULT).let { BitmapFactory.decodeByteArray(it, 0, it.size) }?.asImageBitmap() }.getOrNull()
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (shown != null) Image(shown, contentDescription = t("motd.icon"), modifier = Modifier.size(64.dp).padding(end = 12.dp))
            Text(t("motd.icon_hint"), color = colors.text2, modifier = Modifier.weight(1f))
        }
        if (unreadable) Text(t("motd.icon_unreadable"), color = colors.danger, modifier = Modifier.padding(top = 4.dp))
        if (canEdit) {
            Row {
                TextButton(
                    onClick = { picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
                    enabled = content.busy == null,
                ) { Text(t("motd.icon_pick")) }
                if (shown != null) {
                    TextButton(onClick = { content.removeIcon(strings) }, enabled = content.busy == null) { Text(t("motd.icon_remove")) }
                }
            }
        }
    }
}

/** A picture cut to a square from its middle, made 64 x 64, as base64 PNG. */
private fun iconPng(picture: Bitmap): String {
    val side = minOf(picture.width, picture.height)
    val square = Bitmap.createBitmap(picture, (picture.width - side) / 2, (picture.height - side) / 2, side, side)
    val small = Bitmap.createScaledBitmap(square, 64, 64, true)
    val out = ByteArrayOutputStream()
    small.compress(Bitmap.CompressFormat.PNG, 100, out)
    return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
}

/** Game rules: read from the running server, changed at once (no restart). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun RulesCard(content: ContentPages, canEdit: Boolean) {
    val read = content.rules
    val data = read.value
    val colors = LocalColors.current
    Column(Modifier.padding(vertical = 8.dp)) {
        read.problem?.let { ProblemNote(it, onRetry = content::loadRules) }
        when {
            data == null && read.problem == null -> Note(t("gamerules.reading"))
            data != null && !data.running -> Note(t("gamerules.stopped"))
            data != null && data.rules.isEmpty() -> Note(t("gamerules.none"))
        }
        content.rulesSaid?.let { said ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Note(said.text(LocalStrings.current), modifier = Modifier.weight(1f))
                if (content.rulesBefore.isNotEmpty() && canEdit) {
                    TextButton(onClick = { content.saveRules(content.rulesBefore) }) { Text(t("changes.undo")) }
                }
            }
        }
        if (content.rulesSkipped.isNotEmpty()) {
            Note(t("gamerules.skipped", "names" to content.rulesSkipped.joinToString(", ")), color = colors.text3)
        }
        val rules = data?.rules.orEmpty()
        if (rules.isEmpty()) return@Column
        if (canEdit) {
            Text(t("gamerules.presets"), modifier = Modifier.padding(horizontal = 16.dp))
            FlowRow(Modifier.padding(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for (preset in data?.presets.orEmpty()) {
                    TextButton(onClick = { content.saveRules(preset = preset) }, enabled = content.busy == null) {
                        Text(t("presets.$preset"))
                    }
                }
            }
        }
        for (rule in rules) {
            val value = content.ruleEdits[rule.name] ?: rule.value
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(rule.name, modifier = Modifier.weight(1f))
                val flag = value.booleanOrNull
                val number = value.content.toIntOrNull()
                when {
                    !value.isString && flag != null ->
                        Switch(checked = flag, onCheckedChange = { content.editRule(rule.name, JsonPrimitive(it)) }, enabled = canEdit)
                    !value.isString && number != null -> OutlinedTextField(
                        value = value.content,
                        onValueChange = { typed -> typed.toIntOrNull()?.let { content.editRule(rule.name, JsonPrimitive(it)) } },
                        singleLine = true,
                        enabled = canEdit,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        modifier = Modifier.width(110.dp),
                    )
                    else -> Text(value.content, color = colors.text3) // shown as the server said it
                }
            }
        }
        if (canEdit) {
            Button(
                onClick = { content.saveRules() },
                enabled = content.busy == null && content.ruleEdits.isNotEmpty(),
                modifier = Modifier.padding(16.dp),
            ) { Text(t(if (content.busy == "rules") "action.saving" else "gamerules.save")) }
        }
    }
}
