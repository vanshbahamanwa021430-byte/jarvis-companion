package app.jarviscompanion.hud;

import android.content.Context;
import android.webkit.CookieManager;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class DeskClient {
    private DeskClient() {}

    static JSONObject request(Context ctx, String method, String path, JSONObject body) throws Exception {
        String base = Prefs.serverUrl(ctx);
        if (base == null || base.isEmpty()) {
            throw new IllegalStateException("not_connected");
        }
        URI uri = URI.create(base.endsWith("/") ? base.substring(0, base.length() - 1) : base);
        URL url = new URL(uri.toString() + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(4000);
        conn.setReadTimeout(8000);
        conn.setRequestMethod(method);
        conn.setInstanceFollowRedirects(false);
        conn.setRequestProperty("Host", uri.getHost() + (uri.getPort() > 0 ? ":" + uri.getPort() : ""));
        conn.setRequestProperty("Origin", uri.getScheme() + "://" + uri.getHost()
                + (uri.getPort() > 0 ? ":" + uri.getPort() : ""));
        conn.setRequestProperty("Accept", "application/json");
        String cookie = CookieManager.getInstance().getCookie(uri.toString() + "/");
        if (cookie != null && !cookie.isEmpty()) {
            conn.setRequestProperty("Cookie", cookie);
        }
        if (body != null) {
            byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setFixedLengthStreamingMode(data.length);
            try (OutputStream out = conn.getOutputStream()) {
                out.write(data);
            }
        }
        int code = conn.getResponseCode();
        InputStream stream = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        if (stream != null) {
            byte[] tmp = new byte[2048];
            int n;
            while ((n = stream.read(tmp)) > 0) {
                buf.write(tmp, 0, n);
                if (buf.size() > 80_000) break;
            }
            stream.close();
        }
        conn.disconnect();
        String raw = buf.toString("UTF-8");
        JSONObject parsed = raw.isEmpty() ? new JSONObject() : new JSONObject(raw);
        parsed.put("_status", code);
        return parsed;
    }
}
