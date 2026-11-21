package app.jarviscompanion.hud;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import java.util.Locale;
import java.util.regex.Pattern;

public final class MainActivity extends Activity {
    private static final String PREFS = "jarvis";
    private static final String KEY_URL = "server_url";
    private static final Pattern IPV4 = Pattern.compile(
            "^(25[0-5]|2[0-4]\\d|1\\d{2}|[1-9]?\\d)(\\.(25[0-5]|2[0-4]\\d|1\\d{2}|[1-9]?\\d)){3}$");

    private View connectPanel;
    private EditText urlField;
    private TextView errorText;
    private Button changeServerButton;
    private WebView webView;
    private String connectedUrl;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        connectPanel = findViewById(R.id.connect_panel);
        urlField = findViewById(R.id.server_url);
        errorText = findViewById(R.id.error_text);
        changeServerButton = findViewById(R.id.change_server);
        webView = findViewById(R.id.webview);
        setupWebView();

        String saved = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, "");
        if (saved != null && !saved.isEmpty()) {
            urlField.setText(saved);
        } else {
            urlField.setText("http://127.0.0.1:8765");
        }

        findViewById(R.id.connect_button).setOnClickListener(
                v -> tryConnect(urlField.getText().toString(), true));
        findViewById(R.id.phone_setup).setOnClickListener(
                v -> startActivity(new Intent(this, SetupActivity.class)));
        changeServerButton.setOnClickListener(v -> showConnect(true));
        urlField.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO || actionId == EditorInfo.IME_ACTION_DONE) {
                tryConnect(urlField.getText().toString(), true);
                return true;
            }
            return false;
        });

        String extra = getIntent() != null ? getIntent().getStringExtra("server_url") : null;
        boolean auto = getIntent() != null && getIntent().getBooleanExtra("auto_connect", false);
        if (extra != null && !extra.isEmpty()) {
            urlField.setText(extra);
            if (auto) {
                tryConnect(extra, true);
            }
        }
    }

    private void setupWebView() {
        WebView.setWebContentsDebuggingEnabled(false);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSaveFormData(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.setBackgroundColor(Color.parseColor("#03070D"));
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !isAllowedNavigation(request.getUrl());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (connectedUrl != null && url != null && !url.startsWith("about:")) {
                    showHud();
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    CharSequence description = error.getDescription();
                    String detail = description != null ? description.toString() : "connection failed";
                    showConnectError("Could not reach the access desk. " + detail
                            + " Check that the Python server is running, and that you used --allow-lan or adb reverse.");
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                showConnectError("The server's HTTPS certificate was not trusted. Use http:// on the local network, or install a trusted certificate.");
            }
        });
    }

    private boolean isAllowedNavigation(Uri uri) {
        if (connectedUrl == null || uri == null) {
            return false;
        }
        Uri expected = Uri.parse(connectedUrl);
        String scheme = uri.getScheme();
        return scheme != null
                && ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme))
                && scheme.equalsIgnoreCase(expected.getScheme())
                && expected.getHost() != null
                && expected.getHost().equalsIgnoreCase(uri.getHost())
                && effectivePort(expected) == effectivePort(uri);
    }

    static int effectivePort(Uri uri) {
        int port = uri.getPort();
        if (port != -1) {
            return port;
        }
        return "https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80;
    }

    private void tryConnect(String raw, boolean persist) {
        hideKeyboard();
        final String normalized;
        try {
            normalized = normalizeServerUrl(raw);
        } catch (IllegalArgumentException ex) {
            showConnectError(ex.getMessage());
            return;
        }
        errorText.setVisibility(View.GONE);
        if (persist) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, normalized).apply();
        }
        connectedUrl = normalized;
        urlField.setText(normalized);
        webView.loadUrl(normalized);
    }

    private void showHud() {
        connectPanel.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        changeServerButton.setVisibility(View.VISIBLE);
        if (connectedUrl != null) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, connectedUrl).apply();
        }
        JarvisService.start(this);
    }

    private void showConnect(boolean clearWeb) {
        if (clearWeb) {
            webView.stopLoading();
            webView.loadUrl("about:blank");
            CookieManager.getInstance().removeAllCookies(null);
            CookieManager.getInstance().flush();
            connectedUrl = null;
        }
        webView.setVisibility(View.GONE);
        changeServerButton.setVisibility(View.GONE);
        connectPanel.setVisibility(View.VISIBLE);
    }

    private void showConnectError(String message) {
        showConnect(true);
        errorText.setText(message);
        errorText.setVisibility(View.VISIBLE);
    }

    @Override
    public void onBackPressed() {
        if (webView.getVisibility() == View.VISIBLE && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        if (webView.getVisibility() == View.VISIBLE) {
            showConnect(true);
            return;
        }
        super.onBackPressed();
    }

    private void hideKeyboard() {
        InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null && getCurrentFocus() != null) {
            imm.hideSoftInputFromWindow(getCurrentFocus().getWindowToken(), 0);
        }
    }

    static String normalizeServerUrl(String raw) {
        if (raw == null) {
            throw new IllegalArgumentException("Enter the access desk address from the server terminal.");
        }
        String text = raw.trim();
        if (text.isEmpty()) {
            throw new IllegalArgumentException("Enter the access desk address from the server terminal.");
        }
        if (text.contains(" ") || text.contains("\\") || text.contains("@")) {
            throw new IllegalArgumentException("Enter an http:// address such as http://192.168.1.10:8765 or http://127.0.0.1:8765.");
        }
        Uri uri = Uri.parse(text);
        String scheme = uri.getScheme();
        if (scheme == null) {
            uri = Uri.parse("http://" + text);
            scheme = uri.getScheme();
        }
        if (scheme == null) {
            throw new IllegalArgumentException("Enter an http:// address such as http://192.168.1.10:8765.");
        }
        scheme = scheme.toLowerCase(Locale.US);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new IllegalArgumentException("Only http:// or https:// addresses are allowed.");
        }
        if (uri.getUserInfo() != null) {
            throw new IllegalArgumentException("The address cannot include a username or password.");
        }
        String host = uri.getHost();
        if (host == null || host.isEmpty()) {
            throw new IllegalArgumentException("Enter a host such as 192.168.1.10 or 127.0.0.1.");
        }
        host = host.toLowerCase(Locale.US);
        if (!host.equals("localhost") && !IPV4.matcher(host).matches()) {
            throw new IllegalArgumentException("Use an IPv4 address or localhost, not a website name.");
        }
        if (!host.equals("localhost") && !host.equals("127.0.0.1") && !isPrivateIpv4(host)) {
            throw new IllegalArgumentException("The host must be localhost, 127.0.0.1, or a private LAN IP.");
        }
        int port = uri.getPort();
        if (port == -1) {
            port = scheme.equals("https") ? 443 : 8765;
        }
        if (port < 1 || port > 65535) {
            throw new IllegalArgumentException("The port must be between 1 and 65535.");
        }
        String path = uri.getPath();
        if (path != null && !path.isEmpty() && !path.equals("/")) {
            throw new IllegalArgumentException("Leave the path empty; the app opens the access desk root.");
        }
        if (uri.getQuery() != null || uri.getFragment() != null) {
            throw new IllegalArgumentException("The address cannot include a query or fragment.");
        }
        return scheme + "://" + host + ":" + port + "/";
    }

    static boolean isPrivateIpv4(String host) {
        String[] parts = host.split("\\.");
        if (parts.length != 4) {
            return false;
        }
        int[] octets = new int[4];
        for (int i = 0; i < 4; i++) {
            try {
                octets[i] = Integer.parseInt(parts[i]);
            } catch (NumberFormatException e) {
                return false;
            }
            if (octets[i] < 0 || octets[i] > 255) {
                return false;
            }
        }
        int a = octets[0];
        int b = octets[1];
        return a == 10
                || (a == 172 && b >= 16 && b <= 31)
                || (a == 192 && b == 168)
                || (a == 169 && b == 254);
    }
}
