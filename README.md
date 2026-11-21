# Jarvis Companion · v2.1 "HUD theme" (milestone 3, improved)

The first backend increment of an Android-first assistant. Only fictional resources are enabled. The selected assistant engine is the upstream [open-jarvis/OpenJarvis repository](https://github.com/open-jarvis/OpenJarvis), not a fork or a replacement. This increment includes an optional OpenJarvis tool adapter and a tested local agent API. The OpenJarvis runtime, Android client, and private hosting are not running or verified.

## Working memory increment

See [WORKSPACE_MEMORY.md](WORKSPACE_MEMORY.md) for the persistent project tree and verified synthetic workflow. In v2 it is wired into the **owner dashboard only** (Memory tab). It is still not wired into live OpenJarvis or Android, and the agent/proposal key has no access to it.

## What's new in v2.1: HUD theme

The dashboard now looks like a holographic heads-up display. The backend, API and every safeguard are exactly as in v2, including one fresh owner approval per access, loopback only and demo data only. All 105 v2 tests pass unchanged, and 11 new tests were added (116 total). See [CHANGELOG.md](CHANGELOG.md).

- **Dark HUD by default:** a deep navy grid background with scanlines and vignette, glowing cyan line art and translucent angled panels with corner brackets. Amber marks pending approvals and red marks deny or errors. Light and system themes are still available from the theme button.
- **Animated core** in the centre of the desk, made of rotating rings, tick marks, arcs and a pulsing centre. It idles slowly, turns amber and speeds up when a request needs you, brightens while approved work runs, and dims when paused or offline.
- **HUD gauges** for Pending, Approved, Running and History. A **status bar** shows the clock, the link state and the mode. A greeting based on local time reads, for example, "Good evening. All systems online."
- **Short unlock sequence** (about 1.8 s, skippable).
- **Same controls, restyled:** Allow once, Deny, Revoke, Run now, Pause and Resume, plus the Requests, History, Activity and Memory tabs.
- Everything is drawn originally in CSS and SVG. There are no external files, fonts or CDNs, and the CSP is unchanged. Reduced motion turns off all rotation, scanlines and the boot animation. Animations pause while the tab is hidden. Phone layout works at 390 px: the core shrinks and the gauges stack.

**What v2.1 does *not* verify:** it was checked in headless Chrome at 1440 px and at 390 px with phone emulation only, not on a real Android phone. Screen-reader output was not tested with a real screen reader.

## What's new in v2

v2 keeps every milestone-3 guarantee: IPv4 loopback only, no real accounts, no shell or file tools, a fixed synthetic catalog, and a fresh owner approval for every single access. All 50 original tests still pass unchanged (one test had a SQLite connection leak fixed), and 55 new tests cover the fixes and features below. See [CHANGELOG.md](CHANGELOG.md) for the full list.

**Bug fixes**
- The server no longer crashes with `AttributeError` when the port is already in use; it exits with a clear `startup_failed` log line.
- The background worker no longer dies silently on its first error. It retries with backoff, stops after 5 consecutive failures, and the dashboard shows a "worker stopped" banner. Manual **Run now** still works, and still needs approval.
- Unexpected server errors now return a JSON `500 internal_error` instead of dropping the connection.
- Rows whose action or item is no longer in the catalog no longer crash the dashboard or the worker. They show as "Unavailable item" and can never be approved or executed.
- The agent client now turns non-`OSError` HTTP failures (such as a bad status line) and malformed receipts into `ProposalError`, so the OpenJarvis tool adapter returns a clean failure instead of raising.
- Owner request bodies are read with an exact length check and parsed as strict JSON: no duplicate keys, no `NaN`/`Infinity`, UTF-8 only.
- Owner POSTs whose `Sec-Fetch-Site` is not `same-origin` are rejected, on top of the exact `Origin` check.
- Logout now clears the session expiry as well as the token.
- Static assets are served even with a cache-busting query string, and `HEAD` is supported.
- Login lockout responses include `retry_after`, so the dashboard can say how long to wait.
- In automatic mode the dashboard no longer shows a **Run now** button that races the worker.
- The session countdown shows right after unlocking (no 1-second blank).

**Improvements**
- **Config file and CLI flags.** Use `--config config.json` (see `config.example.json`), plus `--port`, `--db`, `--memory-db`, `--no-memory`, `--manual`, `--ttl-seconds`, `--session-minutes`, `--worker-interval`, `--log-level` and `--log-format`. The bind address and keys **cannot** be set in the file: `host` and `*_key` entries are rejected.
- **Structured logging.** JSON lines (or `--log-format text`) on stderr for startup, access, decisions, the worker and shutdown. Logs never contain keys, cookies, request bodies, query strings, reasons or results. The owner unlock key is printed only to stdout, as before.
- **Graceful shutdown.** SIGINT and SIGTERM stop accepting connections, let in-flight requests finish, stop the worker and log `shutdown_complete` with exit code 0.
- **History pagination and filtering.** `GET /api/history?status=&action=&limit=&before=` and `GET /api/audit?event=&request=&limit=&before=` use keyset cursors (no gaps or duplicates while new items arrive).
- **Live status.** `GET /api/status` is a cheap revision/summary endpoint. The dashboard polls it every 2 s (10 s when the tab is hidden, with backoff when offline) and reloads full state only when something changed.
- **Lighter worker.** It uses a small `approved_ids()` query instead of building a full snapshot every 0.35 s. SQLite runs in WAL mode.
- **Dashboard redesign.** Responsive layout from phone width up, light/dark/system theme toggle, keyboard-navigable tabs (Requests, History, Activity, Memory), expiry countdowns, Allow once / Deny / Revoke / Run now controls with busy states, a clear Pause/Resume toggle and paused banner, summary stats, empty, error and offline states, toasts, an accessible confirm dialog, reduced-motion support and no external assets. All server data is rendered with `textContent`.
- **Workspace memory in the dashboard.** Owner-only endpoints under `/api/memory/` let you create workspaces, projects and records, and search, update and delete them. The same Host, Origin, session and fetch-metadata checks apply. The agent key gets `401`.
- Type hints throughout the new and changed modules.

**What v2 does *not* verify:** no Android device, OpenJarvis runtime (`openjarvis_tool.py` is untested because `openjarvis` is not installed), private hosting or deployment was tested. The dashboard was checked in headless desktop Chrome at desktop and phone-sized viewports, which is not the same as a real phone.

## Run it

1. Extract the ZIP on a computer with Python 3.10 or newer. Open a terminal in the extracted folder. These are backend developer instructions, not a requirement that the owner have a computer.
2. Run `python server.py` (on Windows, `py server.py` also works).
3. Open the **localhost address printed in the terminal on the same computer**. Enter the temporary owner unlock key from that terminal. Do not share it.
4. Make a sample request. Try **Deny**, then submit a fresh request and choose **Allow once**. Approved jobs normally complete automatically; the result appears under **Recent results**, and the **History** and **Activity** tabs list every decision. Try **Pause all access** to revoke pending and unused approvals. The **Memory** tab stores synthetic project notes.

Stop with Ctrl+C (or SIGTERM); shutdown is graceful. The generated owner key and active owner session do not survive a restart. The demo databases stay under `.local-demo/` (`broker.sqlite3` and `memory.sqlite3`). Pass `--manual` to turn off background execution and use the dashboard's **Run now** control. Run `python server.py --help` for all flags, or copy `config.example.json` and pass `--config`.

Run automated tests from the same directory:

```sh
python -m unittest discover -s tests -v
```

**Do not forward this port, open it to your LAN or the internet, or connect real accounts.** This uses a development HTTP server. A responsive mobile layout does not mean that this build is installed on or reachable from a real Android phone. The mock worker runs only while the local process is running.

## What's implemented

- A fixed catalog of fictional resources, with no real account tokens, filesystem reads on behalf of the user, shell commands, or arbitrary network tools.
- Every access needs a distinct approval. The requested operation and target cannot be changed after creation. Requests expire; the server checks the approval before execution and consumes it once, atomically with the synthetic read.
- A background worker runs approved demo jobs without the browser remaining open. Pausing revokes pending/unused approvals. It cannot undo a completed read.
- A host-side, loopback-only owner UI, temporary unlock key, and local activity history. This demo does not defend against another administrator or arbitrary software on the same host, and the SQLite log is not tamper-proof.

## Agent boundary (new)

The owner session can decide requests; the separate proposal key can only submit a named sample action and target. Responses report the pending request ID, never the result. Requests expire and require a new owner decision every time. The endpoint is off unless the backend operator sets a separate cryptographically generated `JARVIS_PROPOSAL_KEY` of at least 32 URL-safe characters. Do not include it in model prompts or publish it; supply it only to the backend and a dedicated adapter process through private environment configuration.

`agent_client.py` submits only to the explicitly selected loopback port. `openjarvis_tool.py` implements the [OpenJarvis custom tool interface](https://open-jarvis.github.io/OpenJarvis/user-guide/tools/); import it in the agent process before using the `jarvis_request_approval` tool. Set `JARVIS_PROPOSAL_PORT` if using a port other than 8765. Test only with the sample targets, and don't enable unrelated OpenJarvis shell, file, browser, or account tools. The protocol tests exercise the real local broker with a separate client, not an installed OpenJarvis runtime.

This is API role separation, **not OS isolation**. Agents with arbitrary code access under the owner's OS identity could access files outside the broker. Before using live data, separate OS identities or hosts, deny access to owner secrets/database and direct account tools, and verify those controls. Do not expose the current HTTP server outside loopback.

## Android-only use and voice handoff

The target setup must not require you to own a PC. A paired Android client can control a remotely managed private workspace; the backend may continue while the phone is offline, but cannot perform new personal reads until you approve. Android verification and private deployment are still pending.

Design for “switch to my Android”: an authenticated user requests a resume on a specific, enrolled device. Issue a short-lived, device-bound handoff that opens or resumes only Jarvis. If the app is in the background, use a normal notification the user taps after unlocking if necessary. Do not put private content in notification text, wake a powered-off phone, bypass Android's lock screen, or treat a spoken instruction as identity verification or a personal-data approval. See [Android activity launch restrictions](https://developer.android.com/guide/components/activities/secure-bal). No push delivery or voice integration is implemented in this milestone.

## Next milestones

1. **Isolate the agent and owner services.** Run OpenJarvis and any browser worker in a separate Linux workspace. Expose a proposal-only interface to the untrusted agent, with no owner session, host mounts, credentials, direct egress to personal services, or unrestricted shell/tool access. Keep results and actions explicit and out of model authority. Test that any untrusted instructions in content cannot authorize a read or write.
2. **Private Android control.** Owner-only TLS transport with device enrollment, lost-device revocation, and Android-friendly timed approval. A push notification may say a request is pending, but must not include personal content or count as authorization. Use an Android document picker for explicitly selected local items; never request root or broad app-data access.
3. **First account integration.** Start with a separate test calendar through official OAuth and the narrowest viable read scope. Vault tokens outside the model/worker, permit only exact approved operation/resource, validate stale approvals and duplicate requests, and ensure emergency pause is effective. Connect a real account only after the user authorizes it. Reads also require approval; there are no standing permissions.
4. **Reliability and security review.** Verify on an actual phone while locked, restart and revocation behavior, backups, upgrades, and resource-level authorization before any broader rollout. Nothing here is a security certification or a claim of an unhackable system.
