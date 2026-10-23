package io.github.peyoteheadlights.mcsc.ui

import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat

/** Android 17's permission for reaching the home network (the wake signal). */
private const val LOCAL_NETWORK = "android.permission.ACCESS_LOCAL_NETWORK"

/**
 * "Wake <PC>" when the PC doesn't answer, for a phone allowed to wake it
 * (core/Wake.kt). At home the phone sends the wake signal itself, after
 * Android asks for the local network permission the first time; from
 * anywhere it asks the helping PC; with only a router, it opens the
 * router's wake page.
 */
@Composable
fun WakeCard(vm: AppViewModel) {
    val ui = vm.wake
    val kept = ui.kept ?: return
    if (!kept.offered(canSignal = true)) return
    val context = LocalContext.current
    val colors = LocalColors.current
    val pc = kept.info.pc.ifBlank { vm.pairing?.host ?: "" }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        vm.wakePc(signal = granted)
    }
    fun wake() {
        if (kept.helper == null) kept.info.routerUrl?.let { openLink(context, it) }
        val needsAsking = Build.VERSION.SDK_INT >= 37 &&
            ContextCompat.checkSelfPermission(context, LOCAL_NETWORK) != PackageManager.PERMISSION_GRANTED
        if (needsAsking) ask.launch(LOCAL_NETWORK) else vm.wakePc(signal = true)
    }
    Card(Modifier.padding(vertical = 8.dp)) {
        when {
            ui.waking -> Note(t("desktop.wake.waking", "pc" to pc))
            ui.awake -> Note(t("desktop.wake.awake"), color = colors.success)
            else -> {
                if (ui.failed) Note(t("desktop.wake.failed", "pc" to pc), color = colors.danger)
                Button(onClick = ::wake, modifier = Modifier.fillMaxWidth().padding(16.dp)) {
                    Text(t("desktop.wake.button", "pc" to pc))
                }
            }
        }
    }
}
