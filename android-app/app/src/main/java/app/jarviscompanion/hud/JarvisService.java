package app.jarviscompanion.hud;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

import org.json.JSONArray;
import org.json.JSONObject;

public final class JarvisService extends Service implements VoiceCoordinator.Listener {
    public static final String CHANNEL = "jarvis_connection";
    public static final String ACTION_MUTE = "app.jarviscompanion.hud.MUTE";
    public static final String ACTION_KILL = "app.jarviscompanion.hud.KILL";
    public static final String ACTION_OPEN = "app.jarviscompanion.hud.OPEN";
    private static final int NOTIF_ID = 21;

    private final Handler main = new Handler(Looper.getMainLooper());
    private Speaker speaker;
    private VoiceCoordinator voice;
    private String voiceLabel = "Connected";
    private boolean polling;
    private int pendingCount;

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannel();
        startAsForeground();
        speaker = new Speaker(this);
        voice = new VoiceCoordinator(this, speaker, this);
        if (!Prefs.muted(this) && !Prefs.killed(this)) voice.start();
        polling = true;
        main.post(poll);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startAsForeground();
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_MUTE.equals(action)) {
            boolean next = !Prefs.muted(this);
            Prefs.setMuted(this, next);
            if (next) voice.stop();
            else if (!Prefs.killed(this)) voice.start();
            pushMute(next);
            refreshNotification();
        } else if (ACTION_KILL.equals(action)) {
            applyKill(true);
        } else if (ACTION_OPEN.equals(action)) {
            Intent open = new Intent(this, ApprovalActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(open);
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        polling = false;
        main.removeCallbacksAndMessages(null);
        if (voice != null) voice.stop();
        if (speaker != null) speaker.shutdown();
        super.onDestroy();
    }

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (!polling) return;
            new Thread(() -> {
                try {
                    JSONObject beat = new JSONObject()
                            .put("accessibility", JarvisAccessibilityService.isConnected())
                            .put("notifications", JarvisNotificationListener.isConnected())
                            .put("battery_exempt", isIgnoringBattery())
                            .put("boot_enabled", Prefs.bootEnabled(JarvisService.this))
                            .put("voice_muted", Prefs.muted(JarvisService.this))
                            .put("listening", !Prefs.muted(JarvisService.this) && !Prefs.killed(JarvisService.this))
                            .put("kill_switch", Prefs.killed(JarvisService.this));
                    DeskClient.request(JarvisService.this, "POST", "/api/device/heartbeat", beat);
                    JSONObject pending = DeskClient.request(JarvisService.this, "GET", "/api/device/pending", null);
                    if (pending.optBoolean("killed", false) && !Prefs.killed(JarvisService.this)) {
                        main.post(() -> applyKill(false));
                    }
                    JSONArray actions = pending.optJSONArray("actions");
                    handlePending(actions);
                } catch (Exception ignored) {
                    // Stay running; the notification still shows the local state.
                }
                main.post(() -> {
                    refreshNotification();
                    if (polling) main.postDelayed(poll, 3500);
                });
            }, "jarvis-poll").start();
        }
    };

    private void handlePending(JSONArray actions) throws Exception {
        int pending = 0;
        if (actions == null) {
            pendingCount = 0;
            return;
        }
        for (int i = 0; i < actions.length(); i++) {
            JSONObject item = actions.getJSONObject(i);
            String status = item.optString("status");
            if ("pending".equals(status)) pending++;
            if ("approved".equals(status) && "device".equals(item.optString("runs_on"))) {
                runApproved(item);
            }
        }
        int previous = pendingCount;
        pendingCount = pending;
        if (pending > previous) {
            main.post(() -> {
                Intent open = new Intent(this, ApprovalActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(open);
            });
        }
    }

    private void runApproved(JSONObject item) {
        if (Prefs.killed(this)) return;
        try {
            String result = DeviceActions.execute(this, item);
            JSONObject body = new JSONObject().put("id", item.getString("id")).put("ok", true)
                    .put("result", new JSONObject().put("text", result));
            DeskClient.request(this, "POST", "/api/device/complete", body);
            speaker.speak("Done. " + item.optString("label", ""));
        } catch (Exception e) {
            try {
                JSONObject body = new JSONObject().put("id", item.getString("id")).put("ok", false)
                        .put("result", new JSONObject().put("error", e.getMessage()));
                DeskClient.request(this, "POST", "/api/device/complete", body);
            } catch (Exception ignored) {}
            speaker.speak("I could not complete that action.");
        }
    }

    private void applyKill(boolean report) {
        Prefs.setKilled(this, true);
        Prefs.setMuted(this, true);
        if (voice != null) voice.stop();
        if (report) {
            new Thread(() -> {
                try {
                    DeskClient.request(this, "POST", "/api/kill", new JSONObject().put("killed", true));
                } catch (Exception ignored) {}
            }, "jarvis-kill").start();
        }
        refreshNotification();
        speaker.speak("Kill switch on.");
    }

    private void pushMute(boolean muted) {
        new Thread(() -> {
            try {
                DeskClient.request(this, "POST", "/api/voice/mute", new JSONObject().put("muted", muted));
            } catch (Exception ignored) {}
        }, "jarvis-mute").start();
    }

    @Override
    public void onVoiceState(String label) {
        voiceLabel = label;
        refreshNotification();
    }

    @Override
    public void onUtterance(String text, JSONObject deskReply) {
        if (deskReply != null && deskReply.optBoolean("needs_spoken_confirm", false)) {
            Intent open = new Intent(this, ApprovalActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(open);
        }
        refreshNotification();
    }

    private void startAsForeground() {
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= 34) {
            int types = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
                    | ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
            startForeground(NOTIF_ID, notification, types);
        } else if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        } else {
            startForeground(NOTIF_ID, notification);
        }
    }

    private void refreshNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) nm.notify(NOTIF_ID, buildNotification());
    }

    private Notification buildNotification() {
        PendingIntent open = PendingIntent.getActivity(this, 1,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent mute = PendingIntent.getService(this, 2,
                new Intent(this, JarvisService.class).setAction(ACTION_MUTE),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent kill = PendingIntent.getService(this, 3,
                new Intent(this, JarvisService.class).setAction(ACTION_KILL),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent approve = PendingIntent.getActivity(this, 4,
                new Intent(this, ApprovalActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String title = Prefs.killed(this) ? "Jarvis kill switch is on"
                : Prefs.muted(this) ? getString(R.string.notif_muted)
                : pendingCount > 0 ? getString(R.string.notif_pending)
                : getString(R.string.notif_listening);
        String text = pendingCount > 0 ? (pendingCount + " waiting for you. " + voiceLabel) : voiceLabel;
        Notification.Builder b = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat_jarvis)
                .setContentTitle(title)
                .setContentText(text)
                .setContentIntent(open)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .addAction(new Notification.Action.Builder(null, Prefs.muted(this) ? "Unmute" : "Mute", mute).build())
                .addAction(new Notification.Action.Builder(null, "Kill", kill).build());
        if (pendingCount > 0) {
            b.addAction(new Notification.Action.Builder(null, "Review", approve).build());
        }
        return b.build();
    }

    private void ensureChannel() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm == null) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL, getString(R.string.notif_channel),
                NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Keeps Jarvis connected to your desk and shows approval cards.");
        ch.setShowBadge(false);
        nm.createNotificationChannel(ch);
    }

    private boolean isIgnoringBattery() {
        if (Build.VERSION.SDK_INT < 23) return true;
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        return pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
    }

    static void start(Context ctx) {
        Intent intent = new Intent(ctx, JarvisService.class);
        if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(intent);
        else ctx.startService(intent);
    }
}
