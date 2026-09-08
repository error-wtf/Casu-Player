package org.casu.mpcasu;

import android.app.Activity;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.FrameLayout;

/** Draw focus above custom backgrounds without replacing selected/playing colours. */
public final class RemoteFocus extends View {
    private final ViewGroup decor;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Rect bounds = new Rect();
    private View hovered;
    private final float density;
    private RemoteFocus(Activity activity, ViewGroup decor) {
        super(activity); this.decor = decor;
        density = getResources().getDisplayMetrics().density;
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        setFocusable(false); setClickable(false);
        setWillNotDraw(false);
    }
    public static RemoteFocus install(Activity activity) {
        ViewGroup decor = (ViewGroup) activity.getWindow().getDecorView();
        RemoteFocus ring = new RemoteFocus(activity, decor);
        decor.addView(ring, new ViewGroup.LayoutParams(-1, -1));
        decor.getViewTreeObserver().addOnGlobalLayoutListener(() -> prepare(decor, ring));
        decor.getViewTreeObserver().addOnPreDrawListener(() -> {
            View target = ring.hovered != null ? ring.hovered : decor.findFocus();
            if (target instanceof AdapterView) {
                View row = ((AdapterView<?>) target).getSelectedView();
                if (row != null) target = row;
            }
            Rect next = new Rect();
            if (target != null && target != ring && target.isShown()) {
                target.getGlobalVisibleRect(next);
                int[] origin = new int[2]; ring.getLocationOnScreen(origin);
                next.offset(-origin[0], -origin[1]);
            }
            if (!next.equals(ring.bounds)) { ring.bounds.set(next); ring.invalidate(); }
            return true;
        });
        prepare(decor, ring);
        return ring;
    }
    private static void prepare(View view, RemoteFocus ring) {
        if (view == ring || view instanceof android.webkit.WebView) return;
        if (view.isClickable() || view instanceof android.widget.EditText || view instanceof android.widget.SeekBar) {
            view.setFocusable(true);
            if (view.getId() == NO_ID) view.setId(generateViewId());
        }
        // Adapter rows are selected by the list, not independent focus stops.
        if (view instanceof AdapterView) return;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i=0;i<group.getChildCount();i++) prepare(group.getChildAt(i), ring);
        }
    }
    public void pointer(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_HOVER_EXIT) hovered = null;
        else if (action == MotionEvent.ACTION_HOVER_MOVE || action == MotionEvent.ACTION_HOVER_ENTER)
            hovered = hit(decor, (int) event.getRawX(), (int) event.getRawY());
        invalidate();
    }
    public void keyboard() { hovered = null; invalidate(); }
    private View hit(View view, int x, int y) {
        if (view == this || !view.isShown()) return null;
        Rect rect = new Rect();
        if (!view.getGlobalVisibleRect(rect) || !rect.contains(x,y)) return null;
        if (view instanceof ViewGroup && !(view instanceof android.webkit.WebView)) {
            ViewGroup group = (ViewGroup) view;
            for (int i=group.getChildCount()-1;i>=0;i--) {
                View child=hit(group.getChildAt(i),x,y);
                if (child!=null) return child;
            }
        }
        if (view.getParent() instanceof AdapterView) return view;
        return view.isEnabled() && (view.isClickable() || view.isFocusable()) ? view : null;
    }
    @Override protected void onDraw(Canvas canvas) {
        if (bounds.isEmpty()) return;
        float inset=2*density;
        paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(4*density);
        paint.setColor(Color.rgb(255,220,64));
        canvas.drawRoundRect(bounds.left+inset,bounds.top+inset,bounds.right-inset,bounds.bottom-inset,7*density,7*density,paint);
        paint.setStrokeWidth(density);paint.setColor(Color.BLACK);
        canvas.drawRoundRect(bounds.left+inset+3*density,bounds.top+inset+3*density,bounds.right-inset-3*density,bounds.bottom-inset-3*density,5*density,5*density,paint);
    }
}
