package app.jarviscompanion.hud;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.TextView;

public final class SetupActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_setup);
        bind();
    }

    @Override
    protected void onResume() {
        super.onResume();
        bind();
    }

    private void bind() {
        TextView status = findViewById(R.id.setup_status);
        status.setText(statusText());
        CheckBox boot = findViewById(R.id.chk_boot);
        CheckBox mute = findViewById(R.id.chk_mute);
        boot.setOnCheckedChangeListener(null);
        mute.setOnCheckedChangeListener(null);
        boot.setChecked(Prefs.bootEnabled(this));
        mute.setChecked(Prefs.muted(this));
        boot.setOnCheckedChangeListener((v, on) -> Prefs.setBoot(this, on));
        mute.setOnCheckedChangeListener((v, on) -> {
            Prefs.setMuted(this, on);
            JarvisService.start(this);
        });
        findViewById(R.id.btn_notifications).setOnClickListener(v -> {
            if (Build.VERSION.SDK_INT >= 33) {
                requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 11);
            }
        });
        findViewById(R.id.btn_mic).setOnClickListener(v ->
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 12));
        findViewById(R.id.btn_battery).setOnClickListener(v -> {
            Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
            intent.setData(Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        });
        findViewById(R.id.btn_accessibility).setOnClickListener(v ->
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        findViewById(R.id.btn_listener).setOnClickListener(v ->
                startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        findViewById(R.id.btn_kill).setOnClickListener(v -> {
            Prefs.setKilled(this, true);
            Prefs.setMuted(this, true);
            Intent kill = new Intent(this, JarvisService.class).setAction(JarvisService.ACTION_KILL);
            JarvisService.start(this);
            startService(kill);
            bind();
        });
        findViewById(R.id.btn_done).setOnClickListener(v -> finish());
    }

    private String statusText() {
        boolean mic = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        boolean notif = Build.VERSION.SDK_INT < 33
                || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED;
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        boolean battery = pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
        return "Notifications: " + on(notif)
                + "\nMicrophone: " + on(mic)
                + "\nBattery exemption: " + on(battery)
                + "\nAccessibility: " + on(JarvisAccessibilityService.isConnected())
                + "\nNotification listener: " + on(JarvisNotificationListener.isConnected())
                + "\nKill switch: " + on(Prefs.killed(this));
    }

    private static String on(boolean v) { return v ? "on" : "off"; }
}
