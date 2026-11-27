package io.github.peyoteheadlights.mcsc.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.peyoteheadlights.mcsc.core.Display

/**
 * Server files: tap through the server's folder, download a file to the
 * phone, upload one from it into the folder that's open. The owner's alone
 * (files.browse); the agent checks every path and never replaces a file.
 */
@Composable
fun FilesScreen(vm: AppViewModel, serverId: String, onBack: () -> Unit) {
    val pages = vm.pages(serverId)
    val strings = vm.strings
    val files = pages.files
    var saving by remember { mutableStateOf<String?>(null) }
    val save = rememberZipSaver("application/octet-stream") { open, discard ->
        val file = saving
        saving = null
        if (file == null) discard() else pages.downloadFile(file, open, discard, strings)
    }
    val pick = rememberFilePicker { pages.uploadFile(it, strings) }
    LaunchedEffect(serverId) { pages.loadFiles("") }
    val parent = files.value?.parent
    ServerFrame(
        vm, serverId,
        title = t("files.title"),
        onBack = { if (parent != null) pages.loadFiles(parent) else onBack() },
        refreshing = files.loading,
        onRefresh = { pages.loadFiles() },
    ) {
        item { Note(t("files.hint")) }
        item {
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(onClick = { parent?.let(pages::loadFiles) }, enabled = parent != null) { Text(t("files.up")) }
                Button(onClick = pick, enabled = files.value != null && !pages.filesBusy) { Text(t("files.upload")) }
            }
        }
        item { Note("/" + files.value?.path.orEmpty()) }
        item { SaidNote(pages.filesSaid) }
        when {
            files.problem != null -> item { ProblemNote(files.problem) { pages.loadFiles() } }
            files.value == null -> item { Note(t("files.loading")) }
            files.value.entries.isEmpty() -> item { Note(t("files.empty")) }
            files.value.truncated -> item { Note(t("files.truncated")) }
        }
        val folder = files.value
        items(folder?.entries.orEmpty()) { entry ->
            Card {
                if (entry.folder) {
                    Item("${entry.name}/", subtitle = t("files.folder"), onClick = { pages.loadFiles(folder!!.inside(entry.name)) })
                } else {
                    Item(
                        entry.name,
                        subtitle = Display.bytes(strings, entry.size),
                        onClick = if (pages.filesBusy) null else ({
                            saving = folder!!.inside(entry.name)
                            save(entry.name)
                        }),
                    )
                }
            }
        }
    }
}
