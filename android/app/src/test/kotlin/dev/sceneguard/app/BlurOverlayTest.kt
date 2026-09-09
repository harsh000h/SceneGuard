package dev.sceneguard.app

import kotlin.test.assertTrue
import org.junit.Test

class BlurOverlayTest {
    @Test
    fun mosaic_reduces_unique_colors() {
        val w = 28; val h = 28
        val px = IntArray(w * h) { (it * 7919) and 0xFFFFFF or (0xFF shl 24) }
        val out = BlurOverlay.mosaicize(w, h, px, strength = 14)
        val before = px.toSet().size
        val after = out.toSet().size
        assertTrue(after < before / 4, "expected heavy reduction, $before -> $after")
    }

    @Test
    fun mosaic_is_idempotent() {
        val w = 40; val h = 40
        val px = IntArray(w * h) { (it * 2654435761L).toInt() }
        val a = BlurOverlay.mosaicize(w, h, px, 14)
        val b = BlurOverlay.mosaicize(w, h, a, 14)
        assertTrue(a.contentEquals(b), "a second pass must not change an already-mosaicked frame")
    }
}
