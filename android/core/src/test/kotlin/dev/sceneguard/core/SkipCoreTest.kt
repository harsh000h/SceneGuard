package dev.sceneguard.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Golden test against the SAME manifest the Python pipeline writes. This is what
 * keeps the Android decision function from drifting: the expected span pair below
 * is copied from `python -m sceneguard selftest` output, not from a re-derivation.
 *
 * `kotlinc + main()` (see scripts/verify-kotlin.sh) and `./gradlew :core:test`
 * run the identical assertions, so CI does not need an Android SDK to check the
 * algorithm.
 */
class SkipCoreTest {

    private fun fixture(): File = listOf(
        "../../extension/fixtures/synthetic.sceneguard.json",
        "extension/fixtures/synthetic.sceneguard.json",
        "fixtures/synthetic.sceneguard.json",
    ).map(::File).first { it.exists() }

    private fun segs(): List<Segment> {
        @Suppress("UNCHECKED_CAST")
        val root = MiniJson.parse(fixture().readText()) as Map<String, Any?>
        @Suppress("UNCHECKED_CAST")
        return (root["segments"] as? List<Map<String, Any?>>).orEmpty().map {
            Segment(
                start = (it["start"] as? Double) ?: 0.0,
                end = (it["end"] as? Double) ?: 0.0,
                categories = (it["categories"] as? List<String>).orEmpty(),
                confidence = (it["confidence"] as? Double) ?: 0.5,
                label = (it["label"] as? String) ?: "",
                source = (it["source"] as? String) ?: "community",
                mode = (it["mode"] as? String) ?: "",
            )
        }
    }

    private fun resolved() = resolveManifest(
        segs(),
        blocked = setOf("nudity", "sex"),
        modes = mapOf("nudity" to "skip", "sex" to "skip", "gore" to "mark_only", "violence" to "mark_only", "language" to "mute"),
        minConfidence = 0.6, pad = 1.0,
    )

    @Test fun parsesRealManifest() = assertEquals(4, segs().size)

    @Test fun seekEditsMatchPythonGolden() {
        val expected = listOf(5.24 to 14.44, 23.96 to 31.24)
        val got = resolved().spans.map { it.start to it.end }
        assertEquals(expected.size, got.size)
        got.zip(expected).forEach { (g, e) ->
            assertTrue("start ${g.first} vs ${e.first}", kotlin.math.abs(g.first - e.first) < 0.011)
            assertTrue("end ${g.second} vs ${e.second}", kotlin.math.abs(g.second - e.second) < 0.011)
        }
    }

    @Test fun goreIsKeptAndProducesNoEdit() {
        val r = resolved()
        assertTrue(r.annotated.any { it.categories.contains("gore") })
        assertTrue(r.spans.none { it.categories.contains("gore") })
    }

    @Test fun weakEvidenceDegradesToAsk() = assertTrue(resolved().annotated.count { it.mode == "ask" } >= 1)

    @Test fun violenceCannotBeSkippedEvenAtMaxConfidence() =
        assertTrue(enforceScope(listOf(Segment(100.0, 200.0, listOf("violence"), 0.99, mode = "skip"))).isEmpty())

    @Test fun mixedSpanIsNarrowedToAdultOnly() {
        val m = enforceScope(listOf(Segment(100.0, 120.0, listOf("sex", "gore"), 0.9)))
        assertEquals(1, m.size)
        assertEquals(listOf("sex"), m[0].categories)
    }

    @Test fun noBlockedFrameIsEverPainted() {
        val spans = resolved().spans
        val dt = 1.0 / 30.0
        var t = 0.0; var leaked = 0.0; var jumps = 0; var guard = 0
        while (t < 50.0 && guard++ < 200000) {
            val d = decide(t, spans)
            if (d.action == Action.SKIP) { jumps++; t = d.target ?: t; continue }
            if (spans.any { t >= it.start && t < it.end }) leaked += dt
            t += dt
        }
        assertEquals(0.0, leaked, 0.0)
        assertEquals(spans.size, jumps)
    }

    @Test fun prefsCannotEscalateScope() {
        val p = SkipPrefs.parse("skip=true;blocked=violence+gore")
        assertEquals(setOf("nudity", "sex"), p.blocked)
    }

    @Test fun prefsRoundTrip() {
        val p = SkipPrefs(skip = false, blurShort = true, muteLang = true, blurMaxSeconds = 2.5)
        val q = SkipPrefs.parse(p.serialize())
        assertEquals(p, q)
    }
}
