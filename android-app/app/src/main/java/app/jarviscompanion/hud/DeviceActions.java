package app.jarviscompanion.hud;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.AlarmClock;
import android.provider.Settings;
import android.widget.Toast;

import org.json.JSONObject;

final class DeviceActions {
    private DeviceActions() {}

    static String execute(Context ctx, JSONObject action) throws Exception {
        if (Prefs.killed(ctx)) throw new IllegalStateException("killed");
        String kind = action.getString("kind");
        JSONObject payload = action.optJSONObject("payload");
        if (payload == null) payload = new JSONObject();
        switch (kind) {
            case "phone.open_app":
                return openApp(ctx, payload.optString("package", ""), payload.optString("app", ""));
            case "phone.call":
                Intent dial = new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + payload.getString("number")));
                dial.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(dial);
                return "opened dialer";
            case "phone.sms_draft":
                Intent sms = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + payload.getString("number")));
                sms.putExtra("sms_body", payload.optString("body", ""));
                sms.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(sms);
                return "opened sms draft";
            case "phone.set_alarm":
                Intent alarm = new Intent(AlarmClock.ACTION_SET_ALARM)
                        .putExtra(AlarmClock.EXTRA_HOUR, payload.optInt("hour", 7))
                        .putExtra(AlarmClock.EXTRA_MINUTES, payload.optInt("minute", 0))
                        .putExtra(AlarmClock.EXTRA_MESSAGE, payload.optString("message", "Jarvis alarm"))
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, false);
                alarm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(alarm);
                return "opened alarm";
            case "phone.open_settings":
                return openSettings(ctx, payload.optString("panel", "main"));
            case "google.maps.open":
                Intent maps = new Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(payload.optString("query", ""))));
                maps.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(maps);
                return "opened maps";
            case "google.youtube.open":
                String q = payload.optString("query", "");
                Intent tube = new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.youtube.com/results?search_query=" + Uri.encode(q)));
                tube.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(tube);
                return "opened youtube";
            case "phone.read_notifications":
                return JarvisNotificationListener.readApproved();
            case "phone.back":
            case "phone.home":
            case "phone.scroll":
            case "phone.tap":
            case "phone.type":
            case "phone.read_screen":
                return JarvisAccessibilityService.execute(action);
            default:
                throw new IllegalStateException("unsupported");
        }
    }

    private static String openApp(Context ctx, String packageName, String app) {
        if (packageName != null && !packageName.isEmpty()) {
            Intent launch = ctx.getPackageManager().getLaunchIntentForPackage(packageName);
            if (launch == null) throw new IllegalStateException("app_not_found");
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(launch);
            return "opened " + packageName;
        }
        // Best-effort by label; never launches a guessed package silently if none match.
        android.content.pm.PackageManager pm = ctx.getPackageManager();
        for (android.content.pm.ApplicationInfo info : pm.getInstalledApplications(0)) {
            CharSequence label = pm.getApplicationLabel(info);
            if (label != null && label.toString().toLowerCase().contains(app.toLowerCase())) {
                Intent launch = pm.getLaunchIntentForPackage(info.packageName);
                if (launch != null) {
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    ctx.startActivity(launch);
                    return "opened " + info.packageName;
                }
            }
        }
        throw new IllegalStateException("app_not_found");
    }

    private static String openSettings(Context ctx, String panel) {
        String action = Settings.ACTION_SETTINGS;
        if ("wifi".equals(panel)) action = Settings.ACTION_WIFI_SETTINGS;
        else if ("bluetooth".equals(panel)) action = Settings.ACTION_BLUETOOTH_SETTINGS;
        else if ("battery".equals(panel)) action = Settings.ACTION_BATTERY_SAVER_SETTINGS;
        Intent intent = new Intent(action);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ctx.startActivity(intent);
        return "opened settings";
    }
}
