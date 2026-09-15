package dev.sceneguard.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.sceneguard.core.SkipPrefs

/**
 * Entry screen: pick a video, optionally pick a .sceneguard.json, choose a
 * filter profile, play. Nothing is uploaded and nothing is decoded that the user
 * did not choose - that constraint is the entire legal basis of this app.
 *
 * Deliberately NOT included: a WebView of a streaming site, screen capture, or a
 * media-session controller. All three are how a personal tool becomes a Play
 * Store rejection or a DMCA/s.65A problem. See ANDROID.md sections 0 and 1.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface { Home() }
            }
        }
    }

    @androidx.compose.runtime.Composable
    private fun Home() {
        var video by remember { mutableStateOf<Uri?>(null) }
        var manifest by remember { mutableStateOf<Uri?>(null) }
        var skip by remember { mutableStateOf(true) }
        var blurShort by remember { mutableStateOf(true) }
        var muteLang by remember { mutableStateOf(false) }

        val pickVideo = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { video = it }
        val pickManifest = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { manifest = it }

        Column(
            Modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("SceneGuard", style = MaterialTheme.typography.headlineMedium)
            Text("Plays your own video files with adult scenes skipped or blurred.\nViolence, gore and language are never cut.",
                style = MaterialTheme.typography.bodyMedium)
            Button(onClick = { pickVideo.launch("video/*") }) { Text(if (video == null) "Choose video" else "Video chosen") }
            Button(onClick = { pickManifest.launch("application/json") }) {
                Text(if (manifest == null) "Choose .sceneguard.json (optional)" else "Manifest chosen")
            }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = skip, onCheckedChange = { v -> skip = v ?: false })
                Text("Skip adult spans", modifier = Modifier.clickable { skip = !skip })
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = blurShort, onCheckedChange = { v -> blurShort = v ?: false })
                Text("Blur short spans instead of cutting", modifier = Modifier.clickable { blurShort = !blurShort })
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = muteLang, onCheckedChange = { v -> muteLang = v ?: false })
                Text("Mute profanity (audio only)", modifier = Modifier.clickable { muteLang = !muteLang })
            }
            Button(
                enabled = video != null,
                onClick = {
                    startActivity(Intent(this@MainActivity, PlayerActivity::class.java).apply {
                        putExtra("video", video)
                        putExtra("manifest", manifest)
                        putExtra("prefs", SkipPrefs(skip, blurShort, muteLang).serialize())
                    })
                },
            ) { Text("Play") }
        }
    }
}
