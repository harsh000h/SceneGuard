package dev.sceneguard.core

/**
 * One viewer's filter profile. Serialised as a flat "k=v;k=v" string across the
 * Intent boundary rather than JSON, so the app has no serialisation dependency
 * and a malformed value degrades to defaults instead of crashing playback.
 *
 * `skip` and `blurShort` can only ever apply to SKIP_ELIGIBLE categories; the
 * fields are preferences about *how* to hide, not about *what* may be hidden.
 */
data class SkipPrefs(
    val skip: Boolean = true,
    val blurShort: Boolean = true,
    val muteLang: Boolean = false,
    val blurMaxSeconds: Double = 3.0,
    val minConfidence: Double = 0.6,
    val blocked: Set<String> = setOf("nudity", "sex"),
) {
    fun serialize(): String = buildString {
        append("skip=").append(skip).append(';')
        append("blurShort=").append(blurShort).append(';')
        append("muteLang=").append(muteLang).append(';')
        append("blurMax=").append(blurMaxSeconds).append(';')
        append("minConf=").append(minConfidence).append(';')
        append("blocked=").append(blocked.sorted().joinToString("+"))
    }

    companion object {
        fun parse(s: String): SkipPrefs {
            val m = s.split(';').mapNotNull {
                val i = it.indexOf('='); if (i < 0) null else it.substring(0, i) to it.substring(i + 1)
            }.toMap()
            fun b(k: String, d: Boolean) = m[k]?.toBooleanStrictOrNull() ?: d
            fun d(k: String, def: Double) = m[k]?.toDoubleOrNull() ?: def
            val blocked = m["blocked"]?.split('+')?.filter { it.isNotEmpty() }?.toSet()
            // a profile that names categories outside the invariant is clamped,
            // never trusted: UI bugs must not be able to reach the render lane.
            val safe = blocked?.intersect(SKIP_ELIGIBLE)?.ifEmpty { null }
            return SkipPrefs(b("skip", true), b("blurShort", true), b("muteLang", false), d("blurMax", 3.0), d("minConf", 0.6), safe ?: setOf("nudity", "sex"))
        }
    }
}
