package dev.sceneguard.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import android.widget.FrameLayout
import android.widget.ImageView
import dev.sceneguard.core.Action
import dev.sceneguard.core.MiniJson
import dev.sceneguard.core.Segment
import dev.sceneguard.core.SkipPrefs
import dev.sceneguard.core.decide
import dev.sceneguard.core.resolveManifest
import kotlin.math.abs

/**
 * The whole runtime is here: an ExoPlayer, a 50 ms listener, and a seek.
 *
 * Note what is absent: no MediaCodec input tinkering, no ImageReader, no
 * MediaProjection. On a DRM file none of those would work anyway (secure input
 * buffer), and on a plain file we do not need them - we only move the playhead
 * and, for blur, draw a mosaic over the view. That keeps this app a *controller*
 * rather than a re-encoder, which is the distinction that matters legally.
 */
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
class PlayerActivity : ComponentActivity() {

    private var player: ExoPlayer? = null
    private var spans: List<Segment> = emptyList()
    private var muted: List<Segment> = emptyList()
    private var prefs: SkipPrefs = SkipPrefs()
    private var timer: java.util.concurrent.ScheduledExecutorService? = null
    private var overlay: ImageView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = FrameLayout(this)
        val view = PlayerView(this).apply { useController = true }
        overlay = ImageView(this).apply { visibility = android.view.View.GONE; scaleType = ImageView.ScaleType.FIT_XY }
        root.addView(view, FrameLayout.LayoutParams(-1, -1))
        root.addView(overlay, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)

        prefs = SkipPrefs.parse(intent.getStringExtra("prefs") ?: "")
        val uri = intent.getParcelableExtra<android.net.Uri>("video")!!
        loadManifest(intent.getParcelableExtra("manifest"))

        player = ExoPlayer.Builder(this).build().also { p ->
            view.player = p
            p.setMediaItem(MediaItem.fromUri(uri))
            p.prepare()
            p.addListener(object : Player.Listener {
                override fun onIsPlayingChanged(playing: Boolean) {
                    if (playing) startTicking(p) else stopTicking()
                }
            })
        }
    }

    private fun loadManifest(uri: android.net.Uri?) {
        if (uri == null) return
        val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() } ?: return
        @Suppress("UNCHECKED_CAST")
        val root = MiniJson.parse(text) as Map<String, Any?>
        @Suppress("UNCHECKED_CAST")
        val raw = (root["segments"] as? List<Map<String, Any?>>).orEmpty()
        val segs = raw.map {
            Segment(
                start = (it["start"] as? Double) ?: 0.0,
                end = (it["end"] as? Double) ?: 0.0,
                categories = (it["categories"] as? List<String>).orEmpty(),
                confidence = (it["confidence"] as? Double) ?: 0.5,
                label = (it["label"] as? String) ?: "",
                source = (it["source"] as? String) ?: "community",
            )
        }
        val r = resolveManifest(
            segs,
            blocked = setOf("nudity", "sex"),
            modes = if (prefs.skip) mapOf("nudity" to "skip", "sex" to "skip", "language" to if (prefs.muteLang) "mute" else "mark_only")
                    else mapOf("nudity" to "mark_only", "sex" to "mark_only", "language" to "mark_only"),
            minConfidence = 0.6,
            pad = 1.0,
        )
        spans = r.spans
        muted = r.muted
    }

    private fun startTicking(p: ExoPlayer) {
        stopTicking()
        timer = java.util.concurrent.Executors.newSingleThreadScheduledExecutor().also { ex ->
            ex.scheduleAtFixedRate({
                val t = p.currentPosition / 1000.0
                val d = decide(t, spans)
                runOnUiThread {
                    when (d.action) {
                        Action.SKIP -> {
                            // Only ever seek FORWARD, and only if we are genuinely
                            // inside the span. Without the guard, a rounding edge at
                            // a span start can bounce the playhead backwards and loop.
                            val target = (d.target ?: t)
                            if (target > t + 0.05) p.seekTo((target * 1000).toLong())
                        }
                        else -> {
                            val inside = spans.any { t >= it.start && t < it.end }
                            // short spans get the mosaic drawn over the PlayerView
                            overlay?.visibility = if (prefs.blurShort && inside) android.view.View.VISIBLE else android.view.View.GONE
                            p.volume = if (muted.any { t >= it.start && t < it.end }) 0f else 1f
                        }
                    }
                }
            }, 0, 50, java.util.concurrent.TimeUnit.MILLISECONDS)
        }
    }

    private fun stopTicking() {
        timer?.shutdownNow(); timer = null; overlay?.visibility = android.view.View.GONE
    }

    override fun onDestroy() {
        stopTicking(); player?.release(); player = null; super.onDestroy()
    }
}
