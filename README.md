# Jarvis Companion · v2.2.1

The first backend increment of an Android-first assistant. Only fictional resources are enabled. The selected assistant engine is the upstream [open-jarvis/OpenJarvis repository](https://github.com/open-jarvis/OpenJarvis), not a fork or a replacement. This increment includes an optional OpenJarvis tool adapter, a tested local agent API, and an Android WebView APK that opens the HUD from the Python server on your computer. A Brain tab can point the desk at a local OpenJarvis server (`jarvis gui`, usually 127.0.0.1:8000). The phone still talks only to this desk; tool calls are queued for your approval and are never auto-executed. A full OpenJarvis + Ollama runtime was not installed in this build environment. Private hosting is not verified.

## What's new in v2.2

Voice-first control of the desk and, with opt-in Android services, of your own phone. Every action is still proposed and needs your explicit approval before it can run. Loopback remains the default bind. The repository stays private.

- **Hey Jarvis.** The phone’s background service can listen for the wake word (Android SpeechRecognizer, offline preferred when the device has a model). Say a command; the desk parses it into a typed action and queues it. Risky actions also need you to say Confirm or Cancel, and always show a card. There is a visible mic state and a mute toggle. Spoken replies use Android TextToSpeech. No Porcupine; no Vosk model is bundled.
- **Background.** A foreground service with a persistent notification keeps the connection when you leave the app. Start-on-boot is opt-in. The setup screen can ask Android to ignore battery optimisation.
- **Phone control (opt-in).** An Accessibility Service, clearly explained in the app, can open apps, tap, type, scroll, go back or home, and read screen text after you approve the card. Intents cover the dialer, SMS drafts (not silent send), alarms, settings, Maps and YouTube. An optional notification listener can read notifications only after a separate approval. A global kill switch on the desk, the notification and the phone setup screen stops everything.
- **Google apps.** Connect Gmail, Calendar, Drive, Contacts and Tasks from the Connect tab using a Desktop OAuth client you create. Least-privilege scopes, PKCE, tokens in a mode-0600 file on the desk. Read still needs approval; send and write always need approval. See [docs/GOOGLE_OAUTH.md](docs/GOOGLE_OAUTH.md). Do not reuse another project’s client.
- **Other apps and services.** A plugin manifest system (examples: `local-note`, `clock-alarm`, `webhook-ping`), an MCP HTTP client, and generic webhooks. Destinations must be loopback or private IPv4. Every call goes through the approval broker.

**Play Store.** A general “run my whole phone” accessibility service is not something you can ship on Google Play. This APK is for personal sideload.

**What v2.2 does *not* verify:** automated tests cover parsing, the approval/confirmation flow, plugin manifests, webhook SSRF policy, a mocked MCP server, and a mocked Google token/API transport (185 tests). Headless Chrome screenshots of the new Connect tab were taken. This APK was not installed on a physical phone or a booted emulator in this environment, so the real microphone, Accessibility, notification listener, boot restart, battery prompt, and a live Google sign-in were not verified.

## Working memory increment

See [WORKSPACE_MEMORY.md](WORKSPACE_MEMORY.md) for the persistent project tree and verified synthetic workflow. In v2 it is wired into the **owner dashboard only** (Memory tab). It is still not wired into live OpenJarvis or Android, and the agent/proposal key has no access to it.

## What's new in v2.1: HUD theme

The dashboard now looks like a holographic heads-up display. The backend, API and every safeguard from v2 remain, including one fresh owner approval per access, loopback by default and demo data only. All previous tests pass, plus tests for opt-in LAN binding, the amber artwork, and the OpenJarvis brain proxy. See [CHANGELOG.md](CHANGELOG.md).

- **Dark HUD by default:** a near-black amber grid (`#050302`) with scanlines and vignette, glowing amber/orange line art (`#ff9a1f` / `#ffb347`) and translucent angled panels with corner brackets. A local WebP of the core orb sits behind the SVG rings, with a dark veil so nearby text stays WCAG AA. If the image fails to load, the solid `#050302` background remains. Light and system themes are still available from the theme button (the photograph is hidden in light theme).
- **Animated core** in the centre of the desk, made of rotating rings, tick marks, arcs and the artwork orb. It idles slowly, turns brighter amber and speeds up when a request needs you, brightens while approved work runs, and dims when paused or offline.
- **HUD gauges** for Pending, Approved, Running and History. A **status bar** shows the clock, the link state and the mode. A greeting based on local time reads, for example, "Good evening. All systems online."
- **Short unlock sequence** (about 1.8 s, skippable).
- **Same controls, restyled:** Allow once, Deny, Revoke, Run now, Pause and Resume, plus the Requests, History, Activity, Memory and Brain tabs.
- The HUD chrome is drawn in CSS and SVG. The core photograph is served only from `/img/jarvis-theme.webp` on this server (CSP `img-src 'self'`; no remote URLs or CSS file-url functions). Reduced motion turns off all rotation, scanlines and the boot animation. Animations pause while the tab is hidden. Phone layout works at 390 px: the core shrinks and the gauges stack.

**Artwork credit.** The orb image at `static/img/jarvis-theme.webp` is Marvel/Disney artwork. It is included for personal use only. This repository must stay private and is not a redistribution of that artwork.

**What v2.1 does *not* verify:** the dashboard was checked in headless Chrome at 1440 px and at 390 px. The APK was built and signed with a project debug key. An Android emulator on this host hit a KVM kernel bug and did not finish booting, so the APK was not re-tested on a running emulator after the amber restyle. It was not tested on a physical phone. Screen-reader output was not tested with a real screen reader. A live OpenJarvis + Ollama install was not run here (no Ollama, and the upstream package pulls a heavy dependency set including `datasets`); the brain proxy was tested against a mocked OpenJarvis HTTP server.

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

## Android APK

The APK is a small HUD-themed shell. It does not run the Python broker on the phone. The broker stays on your computer; the app loads the live dashboard from that server so the page is same-origin and every existing safeguard still applies.

**On your computer**

1. Install Python 3.10+ and run the server from this folder.
2. Pick one of these two ways for the phone to reach the server:

   **Same Wi-Fi (opt-in LAN bind).** Start the server with a warning:

   ```sh
   python server.py --allow-lan
   ```

   The first two lines are still the loopback URL and the owner unlock key. After that the server prints a LAN warning and a `LAN demo: http://<your-PC-IP>:8765/` address. Phone and computer must be on the same trusted home or lab network. Do not use `--allow-lan` on public Wi-Fi or the internet. You may also need to allow the port through the computer's firewall.

   **USB debugging (`adb reverse`), stays on loopback.** Start the server as usual (`python server.py`, no `--allow-lan`). On a computer with `adb`:

   ```sh
   adb reverse tcp:8765 tcp:8765
   ```

   In the app, enter `http://127.0.0.1:8765`. This is the tighter option: the desk is not opened to the LAN.

**On your phone**

1. Install `jarvis-companion-v2.1.apk` (self-signed; Android will ask you to allow installs from this source).
2. Open Jarvis Companion and enter the address from the terminal (`http://192.168.x.x:8765` or `http://127.0.0.1:8765`).
3. Paste the owner unlock key printed in the computer terminal. Do not share it.
4. Use **Change server** if you need to point the app at a different address.

The application id is `app.jarviscompanion.hud`. Production APKs (v2.2.1+) are signed with a dedicated Jarvis release key, not the earlier debug keystore and not a store or Prvlar key. Because the key changed, uninstall the debug-signed v2.2.0 app before installing this one. The launcher icon and splash use a crop of the same core orb. To rebuild a release APK: `scripts/build-apk.sh` (needs a JDK, the Android SDK, and the release keystore directory).


## Set up v2.2 on your computer and phone

1. Start the desk as in v2.1 (`python server.py --allow-lan` on trusted Wi-Fi, or `adb reverse` for USB).
2. Install `jarvis-companion-v2.2.1.apk`. If you already installed the debug-signed v2.2.0 APK, uninstall it first — the production signing key is different, so Android will not treat this as an update. Open the app, enter the desk address, paste the owner key.
3. Tap **Phone setup**. Allow notifications and the microphone if you want “Hey Jarvis”. Optionally ignore battery optimisation, and optionally enable **Start Jarvis after the phone boots**.
4. If you want phone control, open Accessibility settings from that screen, read the in-app explanation, and enable Jarvis Companion. This is opt-in. The service will not tap, type or read until you approve a specific card (and, for risky actions, say Confirm or tap Allow once).
5. If you want notification reading, enable the notification listener from the same screen. Each read still needs approval.
6. To connect Google apps, follow [docs/GOOGLE_OAUTH.md](docs/GOOGLE_OAUTH.md) on the computer: create a **new** Desktop client, paste the client id in the Connect tab, then Connect read / Connect send. Finish the browser sign-in on the computer.
7. To add a plugin, leave the example manifests in `plugins/` or add a folder with `manifest.json` (id must match the folder; only declared permissions). Enable it on the Connect tab. The action still appears as a card.
8. To add a webhook or MCP server, use the Connect tab with a loopback or private IPv4 address. Each call is approved first.
9. The kill switch on the desk, in the notification, and on Phone setup immediately revokes pending work and stops listening.

Use **Mute** on the notification when you do not want the mic. The Android system will also show a mic indicator while SpeechRecognizer is running.

## OpenJarvis as the brain

OpenJarvis stays a separate process on your computer. This desk does not vendor or replace it. The phone never talks to OpenJarvis directly: you set the OpenJarvis URL in the **Brain** tab, and the desk proxies prompts to `POST /v1/chat/completions` with only the `jarvis_request_approval` tool advertised. OpenJarvis returns raw tool calls for client-supplied tools and does not auto-execute them. This desk queues an allowed proposal on the broker (you still Allow once or Deny) and blocks every other tool name. Sample reads still never run without your approval.

**On your computer**

1. Install [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) (Python 3.11+, Apache-2.0) and [Ollama](https://ollama.com/) with a local model, using their current docs. From a source checkout, `jarvis gui` starts the API on port 8000 and the UI on port 5173.
2. Start the Companion desk, optionally passing the URL so it is set at boot:

   ```sh
   python server.py --openjarvis-url http://127.0.0.1:8000
   ```

   You can also leave this unset and paste `127.0.0.1:8000` in the Brain tab after you unlock (the desk adds `http://` if needed). Only loopback or a private LAN address is accepted.
3. Optional: in the OpenJarvis process, import `openjarvis_tool.py` (on `PYTHONPATH` next to this repo) so `jarvis ask --agent orchestrator --tools jarvis_request_approval "..."` can queue a proposal through `JARVIS_PROPOSAL_KEY`. The adapter matches the current OpenJarvis `ToolRegistry` / `BaseTool` / `ToolSpec` / `ToolResult` API (checked against upstream on 2026-11-21). Do not enable OpenJarvis shell, file, browser or account tools against real data.
4. On the phone, open this desk as usual. In **Brain**, confirm the address (from the phone this is still `127.0.0.1:8000` because the desk on your PC talks to OpenJarvis on the same machine). Send a prompt. If a sample read is requested, switch to **Requests** and allow or deny it once.

`openjarvis_url` may be set in `config.json` or with `--openjarvis-url`. It is not a secret. The owner key and `JARVIS_PROPOSAL_KEY` still must not go in the config file.

Run automated tests from the same directory:

```sh
python -m unittest discover -s tests -v
```

**Do not forward this port to the internet, and do not connect real accounts.** This uses a development HTTP server. Leave the default (loopback only) unless you deliberately pass `--allow-lan` on a trusted network and accept the warning. The mock worker runs only while the local process is running. The Android APK was tested in an emulator; a physical phone was not verified in this build.

## What's implemented

- A fixed catalog of fictional resources, with no real account tokens, filesystem reads on behalf of the user, shell commands, or arbitrary network tools.
- Every access needs a distinct approval. The requested operation and target cannot be changed after creation. Requests expire; the server checks the approval before execution and consumes it once, atomically with the synthetic read.
- A background worker runs approved demo jobs without the browser remaining open. Pausing revokes pending/unused approvals. It cannot undo a completed read.
- A host-side owner UI (loopback by default; opt-in LAN bind with a warning), temporary unlock key, and local activity history. This demo does not defend against another administrator or arbitrary software on the same host, and the SQLite log is not tamper-proof. LAN mode also does not defend against other devices or users on that network who obtain the owner unlock key.

## Agent boundary (new)

The owner session can decide requests; the separate proposal key can only submit a named sample action and target. Responses report the pending request ID, never the result. Requests expire and require a new owner decision every time. The endpoint is off unless the backend operator sets a separate cryptographically generated `JARVIS_PROPOSAL_KEY` of at least 32 URL-safe characters. Do not include it in model prompts or publish it; supply it only to the backend and a dedicated adapter process through private environment configuration.

`agent_client.py` submits only to the explicitly selected loopback port. `openjarvis_tool.py` implements the [OpenJarvis custom tool interface](https://open-jarvis.github.io/OpenJarvis/user-guide/tools/); import it in the agent process before using the `jarvis_request_approval` tool. Set `JARVIS_PROPOSAL_PORT` if using a port other than 8765. Test only with the sample targets, and don't enable unrelated OpenJarvis shell, file, browser, or account tools. The protocol tests exercise the real local broker with a separate client, and the brain proxy is tested against a mocked OpenJarvis `/health` and `/v1/chat/completions` server, not an installed OpenJarvis runtime.

This is API role separation, **not OS isolation**. Agents with arbitrary code access under the owner's OS identity could access files outside the broker. Before using live data, separate OS identities or hosts, deny access to owner secrets/database and direct account tools, and verify those controls. Do not expose the current HTTP server to the internet. Only use `--allow-lan` on a trusted local network.

## Android-only use and voice handoff

The target setup must not require you to own a PC. A paired Android client can control a remotely managed private workspace; the backend may continue while the phone is offline, but cannot perform new personal reads until you approve. Android verification and private deployment are still pending.

Design for “switch to my Android”: an authenticated user requests a resume on a specific, enrolled device. Issue a short-lived, device-bound handoff that opens or resumes only Jarvis. If the app is in the background, use a normal notification the user taps after unlocking if necessary. Do not put private content in notification text, wake a powered-off phone, bypass Android's lock screen, or treat a spoken instruction as identity verification or a personal-data approval. See [Android activity launch restrictions](https://developer.android.com/guide/components/activities/secure-bal). No push delivery or voice integration is implemented in this milestone.

## Next milestones

1. **Isolate the agent and owner services.** Run OpenJarvis and any browser worker in a separate Linux workspace. Expose a proposal-only interface to the untrusted agent, with no owner session, host mounts, credentials, direct egress to personal services, or unrestricted shell/tool access. Keep results and actions explicit and out of model authority. Test that any untrusted instructions in content cannot authorize a read or write.
2. **Private Android control.** Owner-only TLS transport with device enrollment, lost-device revocation, and Android-friendly timed approval. A push notification may say a request is pending, but must not include personal content or count as authorization. Use an Android document picker for explicitly selected local items; never request root or broad app-data access.
3. **First account integration.** Start with a separate test calendar through official OAuth and the narrowest viable read scope. Vault tokens outside the model/worker, permit only exact approved operation/resource, validate stale approvals and duplicate requests, and ensure emergency pause is effective. Connect a real account only after the user authorizes it. Reads also require approval; there are no standing permissions.
4. **Reliability and security review.** Verify on an actual phone while locked, restart and revocation behavior, backups, upgrades, and resource-level authorization before any broader rollout. Nothing here is a security certification or a claim of an unhackable system.
