package dev.sceneguard.core

/**
 * SceneGuard core - pure Kotlin, zero Android dependencies.
 *
 * Port of sceneguard/skipcore.py and extension/core.mjs. Deliberately free of
 * android.* imports so it compiles and unit-tests on a plain JVM, which means the
 * three surfaces (CLI, browser, Android) can be proven to agree on one algorithm
 * instead of each re-deriving it. Keep it that way: no Player, no Context.
 */

/** HARD INVARIANT (mirror of SKIP_ELIGIBLE in schema.py / core.mjs). */
val SKIP_ELIGIBLE = setOf("nudity", "sex")
val CATEGORIES = listOf("nudity", "sex", "language", "gore", "violence", "drugs", "horror")

data class Segment(
    val start: Double,
    val end: Double,
    val categories: List<String>,
    val confidence: Double = 0.5,
    val label: String = "",
    val source: String = "community",
    val mode: String = "",
)

enum class Action { BEFORE, SKIP, AFTER }

data class Decision(val action: Action, val target: Double? = null, val label: String = "", val index: Int = -1)

/**
 * What to do at playback position [t]. One pass, allocation-free: this is called
 * ~20x/sec by the player listener and must not jitter.
 *
 * preRoll fires the seek slightly EARLY. Buffered video makes that free, and it
 * is the only way to guarantee not one frame of a flagged span gets painted.
 */
fun decide(t: Double, spans: List<Segment>, preRoll: Double = 0.35, grace: Double = 1.2): Decision {
    for (i in spans.indices) {
        val s = spans[i]
        if (t < s.start - preRoll) return Decision(Action.BEFORE, index = i)
        if (t >= s.start - preRoll && t < s.end)
            return Decision(Action.SKIP, target = maxOf(s.end, s.targetOrEnd()), label = s.label, index = i)
        if (t >= s.end && t < s.end + grace) return Decision(Action.AFTER, index = i)
    }
    return Decision(Action.AFTER)
}

private fun Segment.targetOrEnd(): Double = end

/** Union of spans, optionally without bleeding categories into each other. */
fun mergeOverlapping(segs: List<Segment>, gap: Double = 0.0, byCategory: Boolean = false): List<Segment> {
    if (segs.isEmpty()) return emptyList()
    val ordered = segs.sortedWith(compareBy({ it.start }, { it.end }))
    val out = ArrayList<Segment>()
    for (seg in ordered) {
        val last = out.lastOrNull()
        val sameSig = !byCategory ||
            (last != null && last.categories.sorted() == seg.categories.sorted())
        if (last != null && seg.start - last.end <= gap && sameSig) {
            val idx = out.size - 1
            out[idx] = last.copy(
                end = maxOf(last.end, seg.end),
                categories = (last.categories + seg.categories).distinct().sorted(),
                confidence = maxOf(last.confidence, seg.confidence),
            )
        } else out.add(seg)
    }
    return out
}

/**
 * Last gate before anything may remove picture. Violence/gore/language can be
 * muted or marked, never skipped or blurred - even if a manifest, a config or a
 * fork says otherwise. A span mixing adult + violence is NARROWED, not dropped:
 * the adult seconds still go, the fight stays.
 */
fun enforceScope(spans: List<Segment>, eligible: Set<String> = SKIP_ELIGIBLE): List<Segment> {
    val kept = ArrayList<Segment>()
    for (s in spans) {
        val cats = s.categories.filter { eligible.contains(it) }.sorted()
        if (cats.isNotEmpty()) kept.add(s.copy(categories = cats, mode = "skip"))
    }
    return mergeOverlapping(kept, 0.0, true)
}

data class Resolved(val spans: List<Segment>, val annotated: List<Segment>, val muted: List<Segment>)

/** Resolve one manifest against one viewer's filter choices. */
fun resolveManifest(
    segments: List<Segment>,
    blocked: Set<String> = setOf("nudity", "sex"),
    modes: Map<String, String> = mapOf(
        "nudity" to "skip", "sex" to "skip", "language" to "mute",
        "gore" to "mark_only", "violence" to "mark_only",
    ),
    minConfidence: Double = 0.6,
    pad: Double = 1.0,
): Resolved {
    val annotated = ArrayList<Segment>()
    val toBlock = ArrayList<Segment>()
    val muted = ArrayList<Segment>()
    for (a in segments) {
        val matched = a.categories.filter { blocked.contains(it) }.sorted()
        val want = a.mode.ifEmpty { if (matched.isNotEmpty()) modes[matched[0]] ?: "mark_only" else "mark_only" }
        // confidence gate sits IN FRONT of the mode: weak evidence can never earn
        // the right to remove picture, whatever the viewer chose to block.
        val eff = if (a.confidence >= minConfidence) want else if (want == "skip") "ask" else want
        val item = a.copy(mode = eff)
        annotated.add(item)
        when {
            eff == "skip" -> toBlock.add(item)
            eff == "mute" -> muted.add(Segment(item.start, item.end, item.categories))
        }
    }
    val merged = mergeOverlapping(toBlock, pad, true)
    val padded = merged.map { it.copy(start = maxOf(0.0, it.start - pad), end = it.end + pad) }
    return Resolved(enforceScope(padded), annotated.sortedBy { it.start }, muted.sortedBy { it.start })
}
