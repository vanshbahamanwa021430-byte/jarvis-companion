# Changelog

## 2.2.0 voice-first phone control, Google connectors, plugins

Builds on v2.1. The core model is unchanged: every action is proposed and needs an explicit owner approval before it can run, the default bind is loopback, and CSP / no CORS / one-approval-per-access still hold. 185 automated tests pass (145 previous plus 40 new).

### Added
- **Voice-first desk and phone.** Wake word “Hey Jarvis”. Transcripts are parsed into typed actions (`voice.py`). The first utterance only queues a request. Risky actions also need a spoken Confirm or Cancel, and always show an on-screen card. Android uses SpeechRecognizer (offline preferred when the device has a model) plus TextToSpeech replies; no Porcupine, and no Vosk model is bundled.
- **Background service.** Persistent notification, opt-in start-on-boot, and a prompt to ignore battery optimisation. Mute and kill actions live on the notification.
- **Phone control (opt-in Accessibility Service + optional notification listener).** Open apps, tap/type/scroll/back/home, read screen text, dialer, SMS drafts, alarms, settings, Maps/YouTube intents. Each action is an approval card; the global kill switch stops everything. Accessibility is explained in-app. Sideload only — Play Store policy does not allow a general “run my phone” accessibility service.
- **Google connectors.** Least-privilege OAuth (Gmail, Calendar, Drive, Contacts, Tasks) with PKCE and a placeholder Desktop client id. Tokens are a mode-0600 file on the desk, not encrypted at rest. Read still needs approval; send/write always need approval. YouTube and Maps use on-device intents, not OAuth. See `docs/GOOGLE_OAUTH.md`.
- **Plugins, MCP, webhooks.** Declarative plugin manifests (three examples shipped). MCP HTTP client and generic webhooks, loopback/private IPv4 only, no redirects, all routed through the approval broker.
- Android WebView shell updated to 2.2.0 (`app.jarviscompanion.hud`), still signed with the project debug keystore.

### Notes
- A live Google OAuth sign-in, a real microphone on a phone, and Accessibility on a device were not verified in this build environment.
- The orb artwork remains Marvel/Disney, personal use only; keep the repository private.


## 2.1.0 amber theme, Android APK, OpenJarvis brain

Builds on the HUD theme and the Android/LAN work. Default bind is still loopback. CSP, no CORS, one approval per access, and token rules are unchanged. All previous tests pass.

### Changed
- Dark theme accents moved from cyan to amber/orange (`#ff9a1f` / `#ffb347` on `#050302`). Text and muted colours meet WCAG AA on the solid fallback background and on the opaque panels.
- The core uses a locally served WebP (`/img/jarvis-theme.webp`) behind the SVG rings, with a dark radial veil. No remote URLs and no CSS file-url functions (an `<img>` tag keeps the existing `url()` test). Light theme hides the photograph.
- Android launcher icon and splash use a crop of the same orb. The APK remains self-signed with the project debug keystore.

### Added
- **Brain tab and proxy.** `openjarvis_url` / `--openjarvis-url`, plus `/api/brain/config`, `/api/brain/status` and `/api/brain/ask`. The desk calls OpenJarvis `/v1/chat/completions` with only `jarvis_request_approval`. Other tool calls are blocked and never executed. Approval tool calls are queued on the broker.
- `openjarvis_tool.py` still registers `jarvis_request_approval` on the current OpenJarvis ToolRegistry API and now sets `requires_confirmation=True`. It still only proposes, never executes.
- Tests: `tests/test_v21_brain.py`, plus HUD checks for the WebP asset and amber tokens.

### Notes
- The orb artwork is Marvel/Disney, for personal use only; keep the repository private.
- A live OpenJarvis + Ollama runtime was not installed in the build environment.

## 2.1.0 Android APK (same series)

An optional Android WebView shell and an opt-in LAN bind so a phone can open the HUD. Default bind is still IPv4 loopback. Existing safeguards (CSP, no CORS, one Host header, exact same-origin POSTs, one approval per access, tokens) are unchanged. All previous tests still pass.

### Added
- **`--allow-lan` / `allow_lan`:** opt-in bind on `0.0.0.0`. `host`, `bind` and `address` stay forbidden. The server still prints the loopback URL and owner key first, then a clear warning and any detected private IPv4 URLs.
- When LAN mode is on, the Host header may be `localhost`, `127.0.0.1`, or a private/link-local IPv4 address on the server port. Public IPs, hostnames and port mismatches are still rejected. Origin must still be an exact same-origin match. CORS is still never granted.
- **Android APK** (`android-app/`): a self-signed WebView shell. You enter the desk URL (`http://<PC-LAN-IP>:8765`, or `http://127.0.0.1:8765` with `adb reverse`). The app then loads the live dashboard from the Python server so the page stays same-origin. It does not weaken CSP or add a JavaScript bridge.

### Tests
- New: `tests/test_v21_lan.py`. Covers private IPv4 detection, Host-header policy, config defaults, HTTP Host/Origin behaviour in LAN mode, and the warning printed by `--allow-lan`.

## 2.1.0 (v2.1 "HUD theme")

A visual redesign only. The backend, API, security checks and one-approval-per-access rules are unchanged; the only server change is `VERSION = "2.1.0"`. All 105 v2 tests pass unchanged, and 11 new tests were added (116 total).

### Changed
- **Holographic HUD theme, dark by default.** The background is near-black navy with a fine grid, scanlines, a slow scan band and a soft vignette. Cyan line art glows on translucent panels with chamfered corners and corner brackets. Amber marks pending approvals and warnings, and red marks deny, revoke and errors.
- **Animated core.** An original SVG "core" sits at the centre of the desk, with rotating tick rings, arc segments, a segmented ring, rotating caption text, a crosshair, a hexagon and a pulsing centre. The core idles slowly. It turns amber and speeds up when a request is waiting, brightens and speeds up while approved work is ready or running, and dims and freezes when access is paused or the link is lost. Speeding up never makes the rings jump.
- **HUD gauges.** Four circular gauges (Pending, Approved, Running and History) replace the old stat tiles. On desktop they sit on either side of the core. Below 900 px they move into a 2×2 grid under the core, and below 520 px the core shrinks to 250 px.
- **Top status bar.** It shows a live clock and date, the link state (online, paused, or lost and reconnecting) and the mode (Automatic, Manual, Stopped or Paused). The session countdown now sits under the greeting.
- **Greeting.** "Good morning", "Good afternoon" or "Good evening", based on local time, followed by the system state, such as "All systems online." or "All access is paused."
- **Unlock sequence.** After you unlock, a short "system online" animation plays. It takes about 1.8 s, and Skip intro, a click, Esc, Enter or Space ends it early. It is never shown with reduced motion.
- Restyled the unlock card (animated lock emblem), the loading, offline, tabs, request, history, activity and memory panels, badges, the toast, the confirm dialog and every control. Allow once, Deny, Revoke, Run now and Pause now use glowing HUD buttons.
- Decorative telemetry columns scroll at the sides of the desk (desktop only). They are generated locally, are `aria-hidden`, and never contain server data.
- New favicon and logo glyph, drawn from scratch.
- The theme button cycles HUD dark → light → system. Light theme keeps the layout with the glow turned off.

### Accessibility and performance
- Text meets WCAG AA contrast in both themes. Dark theme danger red was raised to `#ff7d8a` so button text stays at 4.5:1 or better on hover.
- `prefers-reduced-motion: reduce` turns off ring rotation, pulses, the scan band, scanlines, the telemetry scroll and the boot sequence.
- Every decorative SVG is `aria-hidden`. Visible focus rings are drawn outside the clipped button shapes. Keyboard tab navigation is unchanged.
- Animations use CSS transforms only and pause while the tab is hidden (`.is-hidden`). There are no external fonts, images or CDNs, and the page still runs under the same strict CSP, with no inline styles or scripts.

### Tests
- New: `tests/test_v21_hud.py` (11 tests). It covers assets served with the strict CSP and correct types, no external URLs, `@import`, `@font-face` or file `url()` in any static file, the reduced-motion rule, a boot sequence that is short and skippable, dark as the default theme, decorative graphics hidden from assistive tech, core states and the hidden-tab pause, the phone layout rules, no film-studio marks, no HTML or style sinks in the script, and the reported version.

## 2.0.0 (v2)

Security model unchanged: IPv4 loopback only, a synthetic fixed catalog, no real accounts, no shell or file tools, and one owner approval per access, consumed atomically at most once.

### Fixed
- `App.server_close()` ran before `stop_worker` existed when binding failed, so a busy port crashed with `AttributeError`. The server now exits cleanly with code 1 and a `startup_failed` log.
- The worker stopped permanently and silently on the first exception, such as a transient SQLite lock. It now retries with exponential backoff, stops after 5 consecutive failures and reports `worker: "stopped"` to the dashboard.
- Uncaught handler exceptions dropped the socket with no response. They now return JSON `500 {"error":"internal_error"}` and log only the exception type.
- `Broker._details` raised `KeyError` for rows outside the current catalog, which broke `/api/state` and the worker. Such rows now show as "Unavailable item"; `approve` and `execute` refuse them with `409 unavailable_target`.
- `agent_client.propose` leaked `http.client.HTTPException` (not an `OSError`), which `openjarvis_tool` did not catch. Transport and receipt errors now raise `ProposalError`, a subclass of `RuntimeError`. Receipt IDs must be 32 hex characters, and a non-string key raises `ValueError` instead of `TypeError`.
- The owner API did not check that the body read matched `Content-Length`, and it accepted duplicate JSON keys and `NaN`. Both are now rejected with `400`.
- Owner POSTs now also require `Sec-Fetch-Site` to be absent or `same-origin`.
- Logout now clears `session_expires`.
- `/app.js?v=…` returned 404, and `HEAD` returned 501. Both are fixed.
- In auto mode the dashboard offered **Run now**, which raced the worker and showed a confusing "already changed" error.
- In the test suite, `test_broker_additional` leaked a SQLite connection (a `with sqlite3.connect()` block doesn't close it).

### Added
- `config.py`: defaults, then the JSON config file, then CLI flags, all validated. `host`/`bind`/`address` and any key fields are rejected. Includes `config.example.json`.
- `jlog.py`: JSON-lines or text structured logging, with a redaction-by-design policy (no bodies, query strings, cookies or keys).
- Graceful SIGINT/SIGTERM shutdown, which joins in-flight requests and the worker.
- `Broker.history()`, `Broker.audit_log()`, `Broker.status()` and `Broker.approved_ids()`, plus `GET /api/history`, `/api/audit` and `/api/status`.
- `memory_api.py` and owner-only `/api/memory/{workspaces,projects,records,links}` reads, plus `POST /api/memory/{workspaces,projects,records,records/update,records/delete}`. Also `WorkspaceMemory.list_workspaces()`.
- A redesigned dashboard (`static/`): responsive, dark mode, tabs, live status, countdowns, pagination, memory UI, accessible states and `icon.svg`.
- 55 new tests (105 total): `test_v2_broker`, `test_v2_http`, `test_v2_memory_http`, `test_v2_config`, `test_v2_logging`, `test_v2_shutdown` (real subprocess plus signals), `test_v2_agent_client` and `test_v2_dashboard_static`.

### Not verified
- Android devices, the OpenJarvis runtime (`openjarvis_tool.py` is not executed by the tests), private hosting and deployment.
- The dashboard was exercised in headless desktop Chrome only, at desktop and 390 px viewports. This is outside the unittest suite and the script is not shipped.
