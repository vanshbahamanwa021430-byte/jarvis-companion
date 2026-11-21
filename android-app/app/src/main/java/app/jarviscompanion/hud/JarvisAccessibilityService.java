package app.jarviscompanion.hud;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONObject;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

public final class JarvisAccessibilityService extends AccessibilityService {
    private static JarvisAccessibilityService instance;

    public static boolean isConnected() {
        return instance != null;
    }

    @Override
    public void onServiceConnected() {
        instance = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // Events are not recorded or uploaded. Content is only read when an
        // approved phone.read_screen action is being executed.
    }

    @Override
    public void onInterrupt() {}

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    static String execute(JSONObject action) throws Exception {
        JarvisAccessibilityService svc = instance;
        if (svc == null) throw new IllegalStateException("accessibility_off");
        if (Prefs.killed(svc)) throw new IllegalStateException("killed");
        String kind = action.getString("kind");
        JSONObject payload = action.optJSONObject("payload");
        if (payload == null) payload = new JSONObject();
        switch (kind) {
            case "phone.back":
                svc.performGlobalAction(GLOBAL_ACTION_BACK);
                return "back";
            case "phone.home":
                svc.performGlobalAction(GLOBAL_ACTION_HOME);
                return "home";
            case "phone.scroll":
                return svc.gestureScroll(payload.optString("direction", "down"));
            case "phone.tap":
                if (payload.has("text")) return svc.tapText(payload.getString("text"));
                return svc.tapPoint(payload.getInt("x"), payload.getInt("y"));
            case "phone.type":
                return svc.typeText(payload.getString("text"));
            case "phone.read_screen":
                return svc.readScreen();
            default:
                throw new IllegalStateException("unsupported");
        }
    }

    private String gestureScroll(String direction) {
        int w = getResources().getDisplayMetrics().widthPixels;
        int h = getResources().getDisplayMetrics().heightPixels;
        float x1 = w / 2f, y1 = h * 0.7f, x2 = w / 2f, y2 = h * 0.3f;
        if ("up".equals(direction)) { y1 = h * 0.3f; y2 = h * 0.7f; }
        else if ("left".equals(direction)) { x1 = w * 0.7f; x2 = w * 0.3f; y1 = y2 = h / 2f; }
        else if ("right".equals(direction)) { x1 = w * 0.3f; x2 = w * 0.7f; y1 = y2 = h / 2f; }
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        dispatchGesture(new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, 250))
                .build(), null, null);
        return "scrolled " + direction;
    }

    private String tapPoint(int x, int y) {
        Path path = new Path();
        path.moveTo(x, y);
        dispatchGesture(new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, 50))
                .build(), null, null);
        return "tapped " + x + "," + y;
    }

    private String tapText(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new IllegalStateException("no_window");
        List<AccessibilityNodeInfo> found = new ArrayList<>();
        findByText(root, text, found);
        if (found.isEmpty()) throw new IllegalStateException("not_found");
        AccessibilityNodeInfo node = found.get(0);
        Rect r = new Rect();
        node.getBoundsInScreen(r);
        return tapPoint(r.centerX(), r.centerY());
    }

    private String typeText(String text) {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new IllegalStateException("no_window");
        AccessibilityNodeInfo focus = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (focus == null || !focus.refresh()) throw new IllegalStateException("no_input");
        android.os.Bundle args = new android.os.Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        if (!focus.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            throw new IllegalStateException("type_failed");
        }
        return "typed";
    }

    private String readScreen() {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return "";
        StringBuilder out = new StringBuilder();
        collectText(root, out);
        String text = out.toString().trim();
        return text.length() > 2000 ? text.substring(0, 2000) : text;
    }

    private void findByText(AccessibilityNodeInfo node, String text, List<AccessibilityNodeInfo> into) {
        if (node == null || into.size() > 8) return;
        CharSequence t = node.getText();
        CharSequence d = node.getContentDescription();
        String needle = text.toLowerCase();
        if ((t != null && t.toString().toLowerCase().contains(needle))
                || (d != null && d.toString().toLowerCase().contains(needle))) {
            into.add(node);
        }
        for (int i = 0; i < node.getChildCount(); i++) findByText(node.getChild(i), text, into);
    }

    private void collectText(AccessibilityNodeInfo node, StringBuilder out) {
        if (node == null || out.length() > 4000) return;
        CharSequence t = node.getText();
        if (t != null && t.length() > 0) {
            if (out.length() > 0) out.append('\n');
            out.append(t);
        }
        for (int i = 0; i < node.getChildCount(); i++) collectText(node.getChild(i), out);
    }
}
