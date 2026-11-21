package app.jarviscompanion.hud;

import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import java.util.ArrayList;
import java.util.List;

public final class JarvisNotificationListener extends NotificationListenerService {
    private static JarvisNotificationListener instance;

    public static boolean isConnected() {
        return instance != null;
    }

    @Override
    public void onListenerConnected() {
        instance = this;
    }

    @Override
    public void onListenerDisconnected() {
        if (instance == this) instance = null;
    }

    static String readApproved() {
        JarvisNotificationListener svc = instance;
        if (svc == null) throw new IllegalStateException("listener_off");
        if (Prefs.killed(svc)) throw new IllegalStateException("killed");
        List<String> lines = new ArrayList<>();
        StatusBarNotification[] notes = svc.getActiveNotifications();
        if (notes == null) return "";
        for (StatusBarNotification n : notes) {
            if (lines.size() >= 20) break;
            CharSequence title = n.getNotification().extras.getCharSequence("android.title");
            CharSequence text = n.getNotification().extras.getCharSequence("android.text");
            lines.add((n.getPackageName()) + ": " + String.valueOf(title) + " — " + String.valueOf(text));
        }
        return String.join("\n", lines);
    }
}
