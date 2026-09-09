package dev.sceneguard.core

/**
 * Standalone JVM entry point running the golden checks with no Gradle and no
 * JUnit, so the algorithm can be verified in a plain container (see
 * scripts/verify-kotlin.sh in this repo). Same assertions as SkipCoreTest.
 */
object CheckMain {
    @JvmStatic
    fun main(args: Array<String>) {
        val path = args.firstOrNull() ?: "extension/fixtures/synthetic.sceneguard.json"
        @Suppress("UNCHECKED_CAST")
        val root = MiniJson.parse(java.io.File(path).readText()) as Map<String, Any?>
        @Suppress("UNCHECKED_CAST")
        val segs = (root["segments"] as? List<Map<String, Any?>>).orEmpty().map {
            Segment(
                start = (it["start"] as? Double) ?: 0.0, end = (it["end"] as? Double) ?: 0.0,
                categories = (it["categories"] as? List<String>).orEmpty(),
                confidence = (it["confidence"] as? Double) ?: 0.5,
                label = (it["label"] as? String) ?: "", mode = (it["mode"] as? String) ?: "",
            )
        }
        val r = resolveManifest(
            segs, setOf("nudity", "sex"),
            mapOf("nudity" to "skip", "sex" to "skip", "gore" to "mark_only", "violence" to "mark_only"),
            0.6, 1.0,
        )
        var fails = 0
        fun chk(name: String, cond: Boolean, detail: String = "") {
            println("[${if (cond) "PASS" else "FAIL"}] $name" + (if (detail.isNotEmpty()) ": $detail" else ""))
            if (!cond) fails++
        }
        chk("4 segments parsed from the Python-produced manifest", segs.size == 4)
        val got = r.spans.map { it.start to it.end }
        chk(
            "seek edits match the Python golden pair",
            got.size == 2 && kotlin.math.abs(got[0].first - 5.24) < .011 && kotlin.math.abs(got[1].second - 31.24) < .011,
            got.toString(),
        )
        chk("violence is never skipped, even at confidence 0.99", enforceScope(listOf(Segment(1.0, 99.0, listOf("violence"), 0.99))).isEmpty())
        chk("mixed span narrowed to the adult part", enforceScope(listOf(Segment(1.0, 9.0, listOf("sex", "gore"), 0.9))).firstOrNull()?.categories == listOf("sex"))
        chk("weak evidence degrades to ask", r.annotated.count { it.mode == "ask" } >= 1)
        chk("prefs cannot escalate scope", SkipPrefs.parse("skip=true;blocked=violence+gore").blocked == setOf("nudity", "sex"))
        chk("prefs round-trip", SkipPrefs.parse(SkipPrefs(false, true, true, 2.5).serialize()) == SkipPrefs(false, true, true, 2.5))
        var t = 0.0
        var leaked = 0.0
        var jumps = 0
        var g = 0
        while (t < 50.0 && g++ < 200000) {
            val d = decide(t, r.spans)
            if (d.action == Action.SKIP) { jumps++; t = d.target ?: t; continue }
            if (r.spans.any { t >= it.start && t < it.end }) leaked += 1.0 / 30
            t += 1.0 / 30
        }
        chk("zero blocked frames painted", leaked == 0.0 && jumps == r.spans.size, "$jumps jump(s), ${leaked}s leaked")
        println(if (fails == 0) "\nKotlin core verified against the Python golden manifest" else "\n$fails FAILURE(S)")
        if (fails > 0) System.exit(1)
    }
}
