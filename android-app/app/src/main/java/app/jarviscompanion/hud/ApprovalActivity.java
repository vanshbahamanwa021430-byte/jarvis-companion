package app.jarviscompanion.hud;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

public final class ApprovalActivity extends Activity {
    private JSONObject current;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_approval);
        findViewById(R.id.btn_allow).setOnClickListener(v -> decide(true));
        findViewById(R.id.btn_deny).setOnClickListener(v -> decide(false));
        findViewById(R.id.btn_kill).setOnClickListener(v -> {
            Intent kill = new Intent(this, JarvisService.class).setAction(JarvisService.ACTION_KILL);
            startService(kill);
            finish();
        });
        refresh();
    }

    private void refresh() {
        new Thread(() -> {
            try {
                JSONObject pending = DeskClient.request(this, "GET", "/api/device/pending", null);
                JSONArray actions = pending.optJSONArray("actions");
                JSONObject first = null;
                if (actions != null) {
                    for (int i = 0; i < actions.length(); i++) {
                        JSONObject item = actions.getJSONObject(i);
                        if ("pending".equals(item.optString("status"))) { first = item; break; }
                    }
                }
                JSONObject shown = first;
                runOnUiThread(() -> show(shown));
            } catch (Exception e) {
                runOnUiThread(() -> {
                    ((TextView) findViewById(R.id.approval_label)).setText("Could not reach the desk.");
                    findViewById(R.id.btn_allow).setEnabled(false);
                });
            }
        }, "jarvis-approval").start();
    }

    private void show(JSONObject item) {
        current = item;
        TextView label = findViewById(R.id.approval_label);
        TextView detail = findViewById(R.id.approval_detail);
        TextView risk = findViewById(R.id.approval_risk);
        if (item == null) {
            label.setText("No pending requests.");
            detail.setText("When Jarvis wants to do something, the card appears here and on the desk.");
            risk.setText("");
            findViewById(R.id.btn_allow).setEnabled(false);
            findViewById(R.id.btn_deny).setEnabled(false);
            return;
        }
        label.setText(item.optString("label", item.optString("kind")));
        String reason = item.optString("reason", "");
        String source = item.optString("source", "");
        detail.setText((reason.isEmpty() ? "No reason given." : reason) + "\nSource: " + source
                + "\nRuns on: " + item.optString("runs_on"));
        if ("high".equals(item.optString("risk"))) {
            risk.setText("Risky action. Say Confirm or Cancel, or use the buttons. It will not run from the first command alone.");
        } else {
            risk.setText("Approve it here or on the desk. It will not run until you allow it once.");
        }
        findViewById(R.id.btn_allow).setEnabled(!Prefs.killed(this));
        findViewById(R.id.btn_deny).setEnabled(true);
    }

    private void decide(boolean allow) {
        if (current == null) return;
        String id = current.optString("id");
        findViewById(R.id.btn_allow).setEnabled(false);
        new Thread(() -> {
            try {
                DeskClient.request(this, "POST", allow ? "/api/actions/approve" : "/api/actions/deny",
                        new JSONObject().put("id", id));
                runOnUiThread(() -> {
                    Toast.makeText(this, allow ? "Allowed once" : "Denied", Toast.LENGTH_SHORT).show();
                    refresh();
                });
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "Could not send that decision", Toast.LENGTH_SHORT).show());
            }
        }, "jarvis-decide").start();
    }
}
