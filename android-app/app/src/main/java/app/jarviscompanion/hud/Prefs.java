package app.jarviscompanion.hud;

import android.content.Context;
import android.content.SharedPreferences;

final class Prefs {
    static final String FILE = "jarvis";
    static final String SERVER_URL = "server_url";
    static final String BOOT = "boot_enabled";
    static final String MUTE = "voice_muted";
    static final String KILL = "kill_switch";

    private Prefs() {}

    static SharedPreferences get(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    static String serverUrl(Context ctx) {
        return get(ctx).getString(SERVER_URL, "");
    }

    static boolean bootEnabled(Context ctx) {
        return get(ctx).getBoolean(BOOT, false);
    }

    static boolean muted(Context ctx) {
        return get(ctx).getBoolean(MUTE, false);
    }

    static boolean killed(Context ctx) {
        return get(ctx).getBoolean(KILL, false);
    }

    static void setKilled(Context ctx, boolean killed) {
        get(ctx).edit().putBoolean(KILL, killed).apply();
    }

    static void setMuted(Context ctx, boolean muted) {
        get(ctx).edit().putBoolean(MUTE, muted).apply();
    }

    static void setBoot(Context ctx, boolean enabled) {
        get(ctx).edit().putBoolean(BOOT, enabled).apply();
    }
}
