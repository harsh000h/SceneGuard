package dev.sceneguard.app

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint

/**
 * The blur lane on Android: a coarse mosaic, computed as a real downscale +
 * NEAREST upscale (not a radius blur).
 *
 * Why not BlurMaskFilter: with a small radius it is invertible, so it is a
 * cosmetic filter, not a privacy one. Downscaling to ~1/14 resolution destroys
 * the information instead of hiding it - which is what "blur the scene" has to
 * mean if a parent is going to trust it. Same reasoning as desktop/app.py.
 *
 * Pure Bitmap math with no Context, so it is unit-testable under Robolectric -
 * or, as here, kept as a function over an IntArray so the downscale rule can be
 * tested on the JVM without any Android runtime at all.
 */
object BlurOverlay {

    const val STRENGTH = 14

    /** Downscale-average then block-fill. Works on raw pixels so it is testable. */
    fun mosaicize(width: Int, height: Int, argb: IntArray, strength: Int = STRENGTH): IntArray {
        require(argb.size == width * height)
        val out = argb.copyOf()
        val sw = maxOf(1, width / strength)
        val sh = maxOf(1, height / strength)
        val sx = width.toFloat() / sw
        val sy = height.toFloat() / sh
        for (by in 0 until sh) {
            for (bx in 0 until sw) {
                var r = 0L; var g = 0L; var b = 0L; var n = 0
                val x0 = (bx * sx).toInt(); val x1 = minOf(width, ((bx + 1) * sx).toInt().coerceAtLeast(x0 + 1))
                val y0 = (by * sy).toInt(); val y1 = minOf(height, ((by + 1) * sy).toInt().coerceAtLeast(y0 + 1))
                for (y in y0 until y1) for (x in x0 until x1) {
                    val p = argb[y * width + x]
                    r += (p shr 16) and 0xFF; g += (p shr 8) and 0xFF; b += p and 0xFF; n++
                }
                if (n == 0) continue
                val c = (0xFF shl 24) or ((r / n).toInt() shl 16) or ((g / n).toInt() shl 8) or (b / n).toInt()
                for (y in y0 until y1) for (x in x0 until x1) out[y * width + x] = c
            }
        }
        return out
    }

    fun apply(bmp: Bitmap, strength: Int = STRENGTH): Bitmap {
        val w = bmp.width; val h = bmp.height
        val px = IntArray(w * h).also { bmp.getPixels(it, 0, w, 0, 0, w, h) }
        val out = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        out.setPixels(mosaicize(w, h, px, strength), 0, w, 0, 0, w, h)
        return out
    }

    /** Solid placeholder used before the first frame is decoded, so the overlay never flashes. */
    fun black(width: Int, height: Int): Bitmap =
        Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).also {
            Canvas(it).drawColor(Color.argb(255, 10, 10, 12))
        }
}
