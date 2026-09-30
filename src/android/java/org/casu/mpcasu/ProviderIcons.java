// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;

/** Programmatic provider brand icons — small recognizable shapes drawn
 *  with Canvas. No XML resources needed; icons are cached as Bitmaps. */
public final class ProviderIcons {

    private static final int SIZE = 96;
    private static Bitmap hearthis, netflix, browse;

    public static Bitmap get(String provider) {
        if (provider == null) return null;
        switch (provider.toUpperCase(Locale.ROOT)) {
            case "HEARTHIS": return hearthis != null ? hearthis : (hearthis = drawHearThis());
            case "NETFLIX":  return netflix != null ? netflix : (netflix = drawNetflix());
            case "BROWSE":   return browse != null ? browse : (browse = drawBrowse());
            default: return null;
        }
    }

    private static Bitmap newBitmap() {
        return Bitmap.createBitmap(SIZE, SIZE, Bitmap.Config.ARGB_8888);
    }

    // ── HEARTHIS: orange circle + sound wave ───────────────────────────
    private static Bitmap drawHearThis() {
        Bitmap b = newBitmap();
        Canvas c = new Canvas(b);
        Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
        bg.setColor(Color.parseColor("#1a1210"));
        c.drawCircle(SIZE/2f, SIZE/2f, SIZE/2f, bg);
        Paint fg = new Paint(Paint.ANTI_ALIAS_FLAG);
        fg.setColor(Color.parseColor("#FF6B35"));
        fg.setStyle(Paint.Style.STROKE);
        fg.setStrokeWidth(4f);
        fg.setStrokeCap(Paint.Cap.ROUND);
        float cx = SIZE/2f, cy = SIZE/2f;
        // vertical bars (sound wave)
        float[] heights = {18, 28, 22, 32, 16};
        float[] xOffsets = {-24, -12, 0, 12, 24};
        for (int i = 0; i < heights.length; i++) {
            float h = heights[i];
            c.drawLine(cx + xOffsets[i], cy - h/2, cx + xOffsets[i], cy + h/2, fg);
        }
        return b;
    }

    // ── NETFLIX: red bg + white N ──────────────────────────────────────
    private static Bitmap drawNetflix() {
        Bitmap b = newBitmap();
        Canvas c = new Canvas(b);
        // red gradient circle
        Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
        bg.setShader(new LinearGradient(0, 0, SIZE, SIZE,
                Color.parseColor("#B20710"), Color.parseColor("#E50914"),
                Shader.TileMode.CLAMP));
        c.drawCircle(SIZE/2f, SIZE/2f, SIZE/2f, bg);
        // white N
        Paint fg = new Paint(Paint.ANTI_ALIAS_FLAG);
        fg.setColor(Color.WHITE);
        fg.setStyle(Paint.Style.STROKE);
        fg.setStrokeWidth(6f);
        fg.setStrokeCap(Paint.Cap.ROUND);
        fg.setStrokeJoin(Paint.Join.ROUND);
        float cx = SIZE/2f, cy = SIZE/2f;
        Path n = new Path();
        n.moveTo(cx - 18, cy + 20);
        n.lineTo(cx - 18, cy - 20);
        n.lineTo(cx, cy + 6);
        n.lineTo(cx + 18, cy - 20);
        n.lineTo(cx + 18, cy + 20);
        c.drawPath(n, fg);
        return b;
    }

    // ── BROWSE: blue circle + globe lines ──────────────────────────────
    private static Bitmap drawBrowse() {
        Bitmap b = newBitmap();
        Canvas c = new Canvas(b);
        Paint bg = new Paint(Paint.ANTI_ALIAS_FLAG);
        bg.setColor(Color.parseColor("#1a2233"));
        c.drawCircle(SIZE/2f, SIZE/2f, SIZE/2f, bg);
        Paint fg = new Paint(Paint.ANTI_ALIAS_FLAG);
        fg.setColor(Color.parseColor("#4285F4"));
        fg.setStyle(Paint.Style.STROKE);
        fg.setStrokeWidth(3f);
        float cx = SIZE/2f, cy = SIZE/2f;
        float r = 28;
        // outer circle (globe outline)
        c.drawCircle(cx, cy, r, fg);
        // vertical ellipse
        RectF vert = new RectF(cx - 12, cy - r, cx + 12, cy + r);
        c.drawOval(vert, fg);
        // horizontal line (equator)
        c.drawLine(cx - r, cy, cx + r, cy, fg);
        // latitude lines
        c.drawCircle(cx, cy, r * 0.6f, fg);
        return b;
    }

    private static java.util.Locale Locale = java.util.Locale.US;
}
