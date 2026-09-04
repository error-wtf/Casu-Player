// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// SPDX-FileCopyrightText: 2026 Lino Casu
package org.casu.mpcasu;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.util.AttributeSet;
import android.view.View;

/** Oscilloscope waveform line over the MPCASU radial-gradient cover layer —
 *  the Android twin of the Linux VisualizerWidget (waveform-only product
 *  decision, no FFT). Fed by android.media.audiofx.Visualizer waveform taps. */
public final class WaveView extends View {

    private byte[] waveform;
    private android.graphics.Bitmap cover;
    private final Paint wavePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint coverPaint = new Paint(Paint.FILTER_BITMAP_FLAG);
    private RadialGradient background;
    private Paint bgPaint;
    private int lastW = -1;
    private int lastH = -1;

    public WaveView(Context context) {
        super(context);
        init();
    }

    public WaveView(Context context, AttributeSet attrs) {
        super(context, attrs);
        init();
    }

    private void init() {
        wavePaint.setColor(Color.argb(0x88, 0xFF, 0x1E, 0x2D));
        wavePaint.setStrokeWidth(3f);
        wavePaint.setStyle(Paint.Style.STROKE);
        wavePaint.setStrokeCap(Paint.Cap.ROUND);
    }

    public void setWaveform(byte[] data) {
        waveform = data;
        invalidate();
    }

    public void setCover(android.graphics.Bitmap bitmap) {
        cover = bitmap;
        invalidate();
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        super.onSizeChanged(w, h, oldw, oldh);
        if (w > 0 && h > 0) {
            background = new RadialGradient(w / 2f, h / 2f, Math.max(w, h) * 0.7f,
                    Color.parseColor("#1a0e12"), Color.parseColor("#050608"),
                    Shader.TileMode.CLAMP);
            lastW = w;
            lastH = h;
        }
    }

    @Override
    protected void onDraw(Canvas canvas) {
        final int w = getWidth();
        final int h = getHeight();
        if (w <= 0 || h <= 0) return;
        if (background == null || lastW != w || lastH != h) {
            background = new RadialGradient(w / 2f, h / 2f, Math.max(w, h) * 0.7f,
                    Color.parseColor("#1a0e12"), Color.parseColor("#050608"),
                    Shader.TileMode.CLAMP);
            lastW = w;
            lastH = h;
        }
        if (bgPaint == null) {
            bgPaint = new Paint();
        }
        bgPaint.setShader(background);
        canvas.drawRect(0, 0, w, h, bgPaint);

        if (cover != null && !cover.isRecycled()) {
            float size = Math.min(w, h) * 0.44f;
            size = Math.max(80f, Math.min(size, 640f));
            float left = (w - size) / 2f;
            float top = (h - size) / 2f;
            canvas.drawBitmap(cover, null, new android.graphics.RectF(left, top, left + size, top + size), coverPaint);
        }

        byte[] data = waveform;
        if (data == null || data.length < 16) return;
        float step = w / (float) data.length;
        android.graphics.Path path = new android.graphics.Path();
        for (int i = 0; i < data.length; i++) {
            float v = data[i] / 128f;
            if (v > 1f) v = 1f;
            if (v < -1f) v = -1f;
            float x = i * step;
            float y = h * 0.75f + v * h * 0.5f;
            if (i == 0) path.moveTo(x, y);
            else path.lineTo(x, y);
        }
        canvas.drawPath(path, wavePaint);
    }
}
