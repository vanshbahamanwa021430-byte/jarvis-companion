# Changelog

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
