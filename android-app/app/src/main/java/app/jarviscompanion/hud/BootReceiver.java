package app.jarviscompanion.hud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || intent.getAction() == null) return;
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())
                && !"android.intent.action.LOCKED_BOOT_COMPLETED".equals(intent.getAction())) {
            return;
        }
        if (!Prefs.bootEnabled(context) || Prefs.serverUrl(context).isEmpty()) return;
        JarvisService.start(context);
    }
}
