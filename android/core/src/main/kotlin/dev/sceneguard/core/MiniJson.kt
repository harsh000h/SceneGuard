package dev.sceneguard.core

/**
 * ~90-line recursive-descent JSON reader, so `core` has no third-party deps and
 * unit-tests on a bare JVM. It parses only what our manifest schema needs
 * (objects, arrays, strings, numbers, booleans, null) - deliberately not a
 * general-purpose library. App/CI code may use kotlinx.serialization instead;
 * this exists so the *decision logic* stays dependency-free and portable.
 */
object MiniJson {
    private lateinit var src: String
    private var i = 0

    fun parse(text: String): Any? {
        src = text; i = 0; ws()
        val v = value(); ws()
        return v
    }

    private fun ws() { while (i < src.length && src[i].isWhitespace()) i++ }

    private fun value(): Any? {
        ws()
        if (i >= src.length) error("unexpected end of JSON at $i")
        return when (val c = src[i]) {
            '{' -> obj()
            '[' -> arr()
            '"' -> str()
            't', 'f' -> bool()
            'n' -> nul()
            else -> if (c == '-' || c.isDigit()) num() else error("unexpected '$c' at $i")
        }
    }

    private fun obj(): Map<String, Any?> {
        i++; val m = LinkedHashMap<String, Any?>(); ws()
        if (i < src.length && src[i] == '}') { i++; return m }
        while (true) {
            ws(); val k = str(); ws()
            if (src[i] != ':') error("expected ':' at $i"); i++
            m[k] = value(); ws()
            when (src[i]) { ',' -> i++; '}' -> { i++; return m }; else -> error("expected ',' or '}' at $i") }
        }
    }

    private fun arr(): List<Any?> {
        i++; val l = ArrayList<Any?>(); ws()
        if (src[i] == ']') { i++; return l }
        while (true) {
            l.add(value()); ws()
            when (src[i]) { ',' -> i++; ']' -> { i++; return l }; else -> error("expected ',' or ']' at $i") }
        }
    }

    private fun str(): String {
        ws(); if (src[i] != '"') error("expected string at $i"); i++
        val sb = StringBuilder()
        while (src[i] != '"') {
            val c = src[i]
            if (c == '\\') {
                i++
                sb.append(when (val e = src[i]) {
                    'n' -> '\n'; 't' -> '\t'; 'r' -> '\r'; 'b' -> '\b'
                    'f' -> '\u000C'; '"' -> '"'; '\\' -> '\\'; '/' -> '/'
                    'u' -> { val h = src.substring(i + 1, i + 5); i += 4; h.toInt(16).toChar() }
                    else -> error("bad escape \\$e at $i")
                })
                i++
            } else { sb.append(c); i++ }
        }
        i++
        return sb.toString()
    }

    private fun num(): Double {
        val s = i
        if (src[i] == '-') i++
        while (i < src.length && (src[i].isDigit() || src[i] in ".eE+-")) i++
        return src.substring(s, i).toDouble()
    }

    private fun bool(): Boolean =
        if (src.startsWith("true", i)) { i += 4; true }
        else if (src.startsWith("false", i)) { i += 5; false }
        else error("bad literal at $i")

    private fun nul(): Any? { if (!src.startsWith("null", i)) error("bad literal at $i"); i += 4; return null }

    @Suppress("UNCHECKED_CAST")
    fun Any?.objs(): List<Map<String, Any?>> = (this as? List<Map<String, Any?>>) ?: emptyList()
    fun Map<String, Any?>?.d(key: String, def: Double = 0.0): Double = (this?.get(key) as? Double) ?: def
    fun Map<String, Any?>?.s(key: String, def: String = ""): String = (this?.get(key) as? String) ?: def
    fun Map<String, Any?>?.strs(key: String): List<String> =
        (this?.get(key) as? List<*>)?.filterIsInstance<String>() ?: emptyList()
}
