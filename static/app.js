"use strict";
/* Jarvis Companion v2.1 dashboard (HUD theme). Renders all server data with textContent only. */

const $ = (id) => document.getElementById(id);
const POLL_MS = 2000, HIDDEN_POLL_MS = 10000, MAX_BACKOFF_MS = 15000, PAGE = 20;

const ACTIONS = Object.freeze({read_file: "Read sample file", read_calendar: "Read sample calendar event"});
const STATUSES = Object.freeze({pending: "Pending", approved: "Approved", consumed: "Completed", denied: "Denied", expired: "Expired", revoked: "Revoked"});
const EVENTS = Object.freeze({
  proposed: "Request created", approved: "Allowed once", denied: "Denied", expired: "Expired",
  revoked: "Revoked", paused: "All access paused", resumed: "Access resumed", consumed: "Approved action completed",
});
const WORKER = Object.freeze({running: "Automatic", off: "Manual", stopped: "Stopped"});
const ERRORS = Object.freeze({
  owner_required: "Unlock the access desk to continue.",
  invalid_key: "That key didn’t match. Use the key shown in your server terminal.",
  try_again_later: "Too many attempts. Wait a moment and try again.",
  paused: "Access is paused. Resume it before creating or allowing a request.",
  invalid_state: "This request already changed. Its current status is shown below.",
  unknown_action: "Choose an action and item from the demo catalog.",
  invalid_reason: "Enter a plain-text reason of up to 240 characters.",
  request_limit: "Resolve existing requests before creating more.",
  not_found: "That item is no longer available.",
  invalid_origin: "Open the access desk at its local server address.",
  invalid_host: "Open the access desk at its local server address.",
  unavailable_target: "This item is no longer in the catalog and can’t run.",
  invalid_filter: "That filter isn’t supported.", invalid_query: "That filter isn’t supported.",
  memory_disabled: "Workspace memory is turned off on this server.",
  invalid_text: "Enter plain text within the length limit.", invalid_kind: "Choose a record kind.",
  internal_error: "The server hit an unexpected error. Check the terminal log.",
  network: "Can’t reach the local server. Is it still running?",
  brain_disabled: "Set an OpenJarvis address in the Brain tab first.",
  brain_unreachable: "Can’t reach OpenJarvis at that address. Is jarvis gui running on this computer?",
  brain_rejected: "OpenJarvis rejected that request. Check the server and try again.",
  invalid_url: "Use a loopback or private LAN address, for example 127.0.0.1:8000.",
  tool_blocked: "OpenJarvis asked for a tool that this desk does not allow. Nothing was run.",
});
const dateFormat = new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "short"});

const S = {
  state: null, revision: null, offset: 0, busy: false, epoch: 0, tab: "requests", failures: 0,
  timer: null, sessionLeft: null, sessionAt: 0, link: "live", history_total: 0,
  history: {next: null, loaded: false, paged: false},
  activity: {next: null, loaded: false, paged: false},
  memory: {loaded: false},
};

/* ---------- helpers ---------- */
function h(tag, opts = {}, ...children) {
  const el = document.createElement(tag);
  if (opts.cls) el.className = opts.cls;
  if (opts.text !== undefined && opts.text !== null) el.textContent = String(opts.text);
  for (const [k, v] of Object.entries(opts.attrs || {})) el.setAttribute(k, v);
  el.append(...children.filter(Boolean));
  return el;
}
function icon(id) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const use = document.createElementNS(ns, "use");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "#i-" + id);
  svg.append(use);
  return svg;
}
function button(label, cls, onClick, iconId) {
  const b = h("button", {cls: "button " + (cls || ""), attrs: {type: "button"}});
  if (iconId) b.append(icon(iconId));
  b.append(h("span", {text: label}));
  b.addEventListener("click", onClick);
  return b;
}
function empty(title, message, error = false) {
  return h("div", {cls: "empty" + (error ? " error" : "")}, icon(error ? "alert" : "inbox"), h("strong", {text: title}), h("p", {text: message}));
}
const pick = (map, key) => (Object.hasOwn(map, key) ? map[key] : String(key ?? "").replaceAll("_", " "));
function when(ts) {
  if (!Number.isFinite(ts)) return "";
  const d = new Date(ts * 1000);
  return Number.isFinite(d.getTime()) ? dateFormat.format(d) : "";
}
function timeEl(ts) {
  const text = when(ts);
  if (!text) return null;
  const t = h("time", {text});
  t.dateTime = new Date(ts * 1000).toISOString();
  return t;
}
function serverNow() { return Date.now() / 1000 + S.offset; }
function duration(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const m = Math.floor(seconds / 60), s = seconds % 60;
  if (m >= 60) return Math.floor(m / 60) + "h " + (m % 60) + "m";
  return m + ":" + String(s).padStart(2, "0");
}
let toastTimer;
function toast(text, error = false) {
  const el = $("toast");
  clearTimeout(toastTimer);
  el.textContent = text || "";
  el.hidden = !text;
  el.classList.toggle("is-error", error);
  if (text) toastTimer = setTimeout(() => { el.hidden = true; }, error ? 7000 : 3500);
}

/* ---------- theme ---------- */
/* The dark HUD theme is the default; light and "follow the system" stay available. */
const THEMES = ["dark", "light", "system"], THEME_ICON = {system: "auto", light: "sun", dark: "moon"};
const THEME_NAME = {dark: "HUD dark", light: "light", system: "system"};
function applyTheme(theme) {
  if (!THEMES.includes(theme)) theme = "dark";
  document.documentElement.dataset.theme = theme;
  const btn = $("theme");
  btn.replaceChildren(icon(THEME_ICON[theme]));
  btn.setAttribute("aria-label", "Theme: " + THEME_NAME[theme] + ". Activate to change.");
  btn.title = "Theme: " + THEME_NAME[theme];
  try { localStorage.setItem("jarvis-theme", theme); } catch { /* storage may be blocked */ }
}
function storedTheme() {
  try { const t = localStorage.getItem("jarvis-theme"); return THEMES.includes(t) ? t : "dark"; } catch { return "dark"; }
}
$("theme").addEventListener("click", () => {
  const next = THEMES[(THEMES.indexOf(storedTheme()) + 1) % THEMES.length];
  applyTheme(next);
});
applyTheme(storedTheme());

/* ---------- API ---------- */
async function api(path, data) {
  const options = {cache: "no-store", credentials: "same-origin", headers: {Accept: "application/json"}};
  if (data !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  let response;
  try {
    response = await fetch("/api/" + path, options);
  } catch {
    const error = new Error(ERRORS.network);
    error.status = 0; error.code = "network";
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const code = typeof payload.error === "string" ? payload.error : "";
    let message = Object.hasOwn(ERRORS, code) ? ERRORS[code] : "Request failed (" + response.status + ").";
    if (code === "try_again_later" && Number.isFinite(payload.retry_after)) message = "Too many attempts. Try again in " + payload.retry_after + " s.";
    const error = new Error(message);
    error.status = response.status; error.code = code;
    throw error;
  }
  return payload;
}
function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== "" && v !== null && v !== undefined) p.set(k, String(v));
  const s = p.toString();
  return s ? "?" + s : "";
}

/* ---------- views ---------- */
function show(view) {
  for (const id of ["loading", "offline", "unlock", "desk"]) $(id).hidden = id !== view;
  const signedIn = view === "desk";
  $("logout").hidden = !signedIn;
  $("live").hidden = !signedIn;
  $("mode").hidden = !signedIn;
  if (view === "offline") S.link = "offline";
  if (!signedIn) setCore(view === "offline" ? "offline" : view === "unlock" ? "locked" : "idle");
}
function showLock(message) {
  S.epoch++;
  S.state = null; S.revision = null;
  S.history = {next: null, loaded: false, paged: false};
  S.activity = {next: null, loaded: false, paged: false};
  S.memory = {loaded: false};
  for (const id of ["requests", "results", "history", "activity", "mem-records", "action", "target", "mem-workspace", "mem-project", "brain-log"]) $(id).replaceChildren();
  $("reason").value = "";
  $("key").value = "";
  show("unlock");
  const err = $("login-error");
  err.hidden = !message; err.textContent = message || "";
  $("key").focus();
}
function setLive(mode) {
  const el = $("live");
  el.classList.toggle("is-paused", mode === "paused");
  el.classList.toggle("is-offline", mode === "offline");
  $("live-label").textContent = {live: "Link online", paused: "Link online · paused", offline: "Link lost · reconnecting…"}[mode];
  S.link = mode;
  updateCore();
}


/* ---------- HUD: core orb, gauges, clock, greeting, boot sequence ---------- */
const reducedMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : {matches: false};
const CORE_STATE = {idle: "Standby", alert: "Approval needed", busy: "Executing", paused: "Paused", offline: "Offline", locked: "Locked"};
function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage may be blocked */ } }
function setGauge(name, value, scale, floor = 0) {
  const v = Number(value) || 0;
  const pct = v <= 0 ? floor : Math.min(100, Math.max(8, Math.round((Math.min(v, scale) / scale) * 100)));
  $("gauge-" + name).setAttribute("stroke-dasharray", pct + " " + (100 - pct));
}
function setCore(state) {
  if (!Object.hasOwn(CORE_STATE, state)) state = "idle";
  document.body.dataset.core = state;
  $("core-state").textContent = CORE_STATE[state];
}
function greetingWord(date = new Date()) {
  const hour = date.getHours();
  return hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}
function greetingText() {
  const st = S.state;
  if (S.link === "offline") return greetingWord() + ". Link lost; reconnecting.";
  if (st && st.paused) return greetingWord() + ". All access is paused.";
  if (st && st.worker === "stopped") return greetingWord() + ". Worker offline; manual control only.";
  return greetingWord() + ". All systems online.";
}
function updateCore() {
  const st = S.state;
  if (!st || $("desk").hidden) return;
  const c = S.counts || {pending: 0, approved: 0, running: 0};
  let state = "idle";
  if (S.link === "offline") state = "offline";
  else if (st.paused) state = "paused";
  else if (c.pending > 0) state = "alert";
  else if (c.running > 0 || c.approved > 0) state = "busy";
  setCore(state);
  $("mode-label").textContent = st.paused ? "Paused" : pick(WORKER, st.worker);
  $("greeting").textContent = greetingText();
  const status = {
    offline: "The local server is not answering. Your decisions are safe; nothing runs without you.",
    paused: "Every outstanding approval was revoked. Resume to accept new requests.",
    alert: c.pending + (c.pending === 1 ? " request is" : " requests are") + " waiting for your decision.",
    busy: c.running > 0 ? "Running " + c.running + " approved " + (c.running === 1 ? "request" : "requests") + " now."
      : c.approved + " approved " + (c.approved === 1 ? "request is" : "requests are") + " ready to run.",
    idle: "You decide what Jarvis can access, one request at a time.",
  }[state];
  $("core-status").textContent = status;
}
const clockTime = new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"});
const clockDate = new Intl.DateTimeFormat(undefined, {weekday: "short", day: "2-digit", month: "short"});
function tickClock() {
  const now = new Date();
  $("clock").textContent = clockTime.format(now);
  $("clock").dateTime = now.toISOString();
  $("clock-date").textContent = clockDate.format(now);
}
tickClock();

/* Short "system online" sequence on unlock. Under 2 s, skippable, never shown with reduced motion. */
let bootTimer = null;
function endBoot() {
  if ($("boot").hidden) return;
  clearTimeout(bootTimer);
  $("boot").hidden = true;
  document.body.classList.remove("is-booting");
  if (!$("desk").hidden) $("main").focus({preventScroll: true});
}
function playBoot() {
  sessionStorageSet("jarvis-booted", "1");
  if (reducedMotion.matches) return;
  $("boot-greeting").textContent = greetingWord() + ". All systems online.";
  $("boot").hidden = false;
  document.body.classList.add("is-booting");
  $("boot-skip").focus({preventScroll: true});
  clearTimeout(bootTimer);
  bootTimer = setTimeout(endBoot, 1800);
}
$("boot-skip").addEventListener("click", endBoot);
$("boot").addEventListener("click", endBoot);
document.addEventListener("keydown", (e) => { if (!$("boot").hidden && (e.key === "Escape" || e.key === "Enter" || e.key === " ")) { e.preventDefault(); endBoot(); } });

/* Purely decorative telemetry text (generated locally, never server data). */
function fillTelemetry() {
  const hex = (n) => Math.floor(Math.random() * 16 ** n).toString(16).toUpperCase().padStart(n, "0");
  const line = (i) => hex(4) + " " + String(i).padStart(3, "0") + " " + (Math.random() * 99).toFixed(2).padStart(5, "0") + " " + hex(2) + ":" + hex(2);
  for (const id of ["tele-left", "tele-right"]) {
    const rows = Array.from({length: 28}, (_, i) => line(i));
    $(id).replaceChildren(h("span", {text: rows.concat(rows).join("\n")}));
  }
}
fillTelemetry();

/* Pause every HUD animation while the tab is hidden. */
function syncHidden() { document.documentElement.classList.toggle("is-hidden", document.hidden); }
document.addEventListener("visibilitychange", syncHidden);
syncHidden();

/* ---------- requests tab ---------- */
function fillSelect(select, options, keep = true) {
  const before = select.value;
  select.replaceChildren(...options.map(([value, text]) => { const o = h("option", {text}); o.value = value; return o; }));
  if (keep && options.some(([v]) => v === before)) select.value = before;
}
function fillCatalog() {
  const st = S.state;
  const actions = [...new Set(st.catalog.map((i) => i.action))];
  fillSelect($("action"), actions.map((a) => [a, pick(ACTIONS, a)]));
  fillTargets();
  const hs = $("history-status"), ha = $("history-action"), ae = $("activity-event");
  if (hs.options.length <= 1) {
    for (const [k, v] of Object.entries(STATUSES)) { const o = h("option", {text: v}); o.value = k; hs.append(o); }
    for (const a of actions) { const o = h("option", {text: pick(ACTIONS, a)}); o.value = a; ha.append(o); }
    for (const [k, v] of Object.entries(EVENTS)) { const o = h("option", {text: v}); o.value = k; ae.append(o); }
  }
}
function fillTargets() {
  const st = S.state;
  if (!st) return;
  const items = st.catalog.filter((i) => i.action === $("action").value);
  fillSelect($("target"), items.map((i) => [i.target, i.label || i.target]));
  const off = st.paused || !items.length;
  $("action").disabled = st.paused;
  $("target").disabled = off;
  $("reason").disabled = st.paused;
  $("propose").disabled = off;
}
function countdown(expires) {
  const span = h("span", {cls: "countdown"});
  span.dataset.expires = String(expires);
  updateCountdown(span);
  return span;
}
function updateCountdown(span) {
  const left = Number(span.dataset.expires) - serverNow();
  span.textContent = left > 0 ? "in " + duration(left) : "expiring…";
  span.classList.toggle("is-soon", left < 30);
}
function scope(rows) {
  const dl = h("dl", {cls: "scope"});
  for (const [k, v] of rows) if (v) dl.append(h("dt", {text: k}), typeof v === "string" ? h("dd", {text: v}) : h("dd", {}, v));
  return dl;
}
function badge(status) { return h("span", {cls: "badge badge-" + status, text: pick(STATUSES, status)}); }
function requestCard(item) {
  const st = S.state;
  const card = h("article", {cls: "item is-" + item.status, attrs: {"aria-label": (item.label || item.target) + ", " + pick(STATUSES, item.status)}});
  card.append(
    h("div", {cls: "item-head"}, h("h3", {text: item.label || item.target}), badge(item.status)),
    scope([["Action", pick(ACTIONS, item.action)], ["Item", item.target], ["Reason", item.reason || "No reason given."],
      ["Requested", timeEl(item.created_at)], ["Expires", countdown(item.expires_at)]]),
  );
  const controls = h("div", {cls: "controls"});
  const name = item.label || item.target;
  if (item.status === "pending") {
    const allow = button("Allow once", "primary", (e) => mutate("approve", {id: item.id}, "Allowed once. Only this request can run.", e.currentTarget), "check");
    const deny = button("Deny", "danger", (e) => mutate("deny", {id: item.id}, "Request denied.", e.currentTarget), "x");
    allow.disabled = st.paused;
    allow.dataset.focus = "approve-" + item.id; deny.dataset.focus = "deny-" + item.id;
    allow.setAttribute("aria-label", "Allow once: " + name);
    deny.setAttribute("aria-label", "Deny: " + name);
    controls.append(allow, deny);
  } else if (item.status === "approved") {
    if (st.auto && st.worker === "running") controls.append(h("span", {cls: "running", text: "Running automatically…"}));
    else {
      const run = button("Run now", "primary", (e) => mutate("execute", {id: item.id}, "Approved action completed.", e.currentTarget), "play");
      run.disabled = st.paused;
      run.dataset.focus = "execute-" + item.id;
      run.setAttribute("aria-label", "Run now: " + name);
      controls.append(run);
    }
    const revoke = button("Revoke", "danger", (e) => mutate("deny", {id: item.id}, "Approval revoked before it ran.", e.currentTarget), "x");
    revoke.dataset.focus = "revoke-" + item.id;
    revoke.setAttribute("aria-label", "Revoke approval: " + name);
    controls.append(revoke);
  }
  card.append(controls);
  return card;
}
function resultCard(item) {
  const card = h("article", {cls: "item"});
  card.append(h("div", {cls: "item-head"}, h("h3", {text: item.label || item.target}), badge(item.status)));
  if (item.result && typeof item.result.text === "string") card.append(h("pre", {cls: "result-text", text: item.result.text}));
  const at = timeEl(item.completed_at);
  if (at) card.append(h("p", {cls: "meta"}, document.createTextNode(pick(STATUSES, item.status) + " · "), at));
  return card;
}
function renderDesk() {
  const st = S.state;
  show("desk");
  $("pause").setAttribute("aria-pressed", String(st.paused));
  $("pause").replaceChildren(icon(st.paused ? "play" : "pause"), h("span", {text: st.paused ? "Resume access" : "Pause all access", attrs: {id: "pause-label"}}));
  $("paused-banner").hidden = !st.paused;
  $("worker-banner").hidden = st.worker !== "stopped";
  $("tab-memory").hidden = !st.memory;
  fillBrain();
  $("version").textContent = st.version ? "Version " + st.version : "";
  setLive(st.paused ? "paused" : "live");
  fillCatalog();
  const active = st.requests.filter((i) => i.status === "pending" || i.status === "approved");
  const pending = active.filter((i) => i.status === "pending").length;
  const closed = st.requests.length - active.length;
  S.history_total = closed;
  updateStats({pending, approved: active.length - pending, worker: st.worker, session_expires_in: st.session_expires_in,
    history: closed, history_capped: st.requests.length >= 100});
  $("queue-count").textContent = pending + " pending";
  const focused = $("requests").contains(document.activeElement) ? document.activeElement.dataset.focus : null;
  $("requests").replaceChildren(...(active.length ? active.map(requestCard)
    : [empty("No requests waiting", st.paused ? "Access is paused. Resume to create test requests." : "Create a demo request to see how an approval works.")]));
  if (focused !== null) {
    const next = [...$("requests").querySelectorAll("button")].find((b) => b.dataset.focus === focused && !b.disabled)
      || $("requests").querySelector("button:not(:disabled)") || $("tab-requests");
    next.focus({preventScroll: true});
  }
  const done = st.requests.filter((i) => i.status !== "pending" && i.status !== "approved").slice(0, 5);
  $("results").replaceChildren(...(done.length ? done.map(resultCard) : [empty("No results yet", "Completed, denied and expired requests appear here.")]));
}
function updateStats(s) {
  $("stat-pending").textContent = String(s.pending ?? 0);
  $("stat-approved").textContent = String(s.approved ?? 0);
  if (s.worker !== undefined) $("stat-worker").textContent = pick(WORKER, s.worker);
  const st = S.state;
  const worker = s.worker ?? (st && st.worker);
  const running = st && st.auto && worker === "running" && !(s.paused ?? st.paused) ? (s.approved ?? 0) : 0;
  $("stat-running").textContent = String(running);
  setGauge("pending", s.pending ?? 0, 10);
  setGauge("approved", s.approved ?? 0, 10);
  setGauge("running", running, 5, worker === "running" ? 8 : 0);
  if (Number.isFinite(s.history)) {
    $("stat-history").textContent = s.history + (s.history_capped ? "+" : "");
    setGauge("history", s.history, 50);
  }
  S.counts = {pending: s.pending ?? 0, approved: s.approved ?? 0, running};
  updateCore();
  $("tab-badge").hidden = !s.pending;
  $("tab-badge").textContent = String(s.pending ?? 0);
  if (Number.isFinite(s.session_expires_in)) { S.sessionLeft = s.session_expires_in; S.sessionAt = Date.now(); }
  tickSession();
  document.title = (s.pending ? "(" + s.pending + ") " : "") + "Jarvis Companion · Access desk";
}

/* ---------- state & live polling ---------- */
async function loadState() {
  const epoch = S.epoch;
  const next = await api("state");
  if (epoch !== S.epoch) return;
  const first = !S.state;
  S.state = next;
  S.revision = next.revision;
  S.offset = next.server_time - Date.now() / 1000;
  renderDesk();
  if (first && $("boot").hidden) $("main").focus({preventScroll: true});
  refreshOpenTab();
}
function refreshOpenTab() {
  if (S.tab === "history" && !S.history.paged) void loadHistory(true);
  if (S.tab === "activity" && !S.activity.paged) void loadActivity(true);
}
function schedule(ms) {
  clearTimeout(S.timer);
  S.timer = setTimeout(poll, ms);
}
async function poll() {
  if (!S.state) return;
  const epoch = S.epoch;
  try {
    if (!S.busy) {
      const status = await api("status");
      if (epoch !== S.epoch) return;
      S.failures = 0;
      S.offset = status.server_time - Date.now() / 1000;
      updateStats({...status, worker: status.worker ?? S.state.worker});
      setLive(status.paused ? "paused" : "live");
      if (status.revision !== S.revision || status.paused !== S.state.paused || status.worker !== S.state.worker) await loadState();
    }
    schedule(document.hidden ? HIDDEN_POLL_MS : POLL_MS);
  } catch (error) {
    if (epoch !== S.epoch) return;
    if (error.status === 401) return showLock("Your session ended. Unlock to continue.");
    S.failures++;
    setLive("offline");
    schedule(Math.min(POLL_MS * 2 ** S.failures, MAX_BACKOFF_MS));
  }
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.state) schedule(50); });
function tickSession() {
  if (S.sessionLeft === null) return;
  const left = S.sessionLeft - (Date.now() - S.sessionAt) / 1000;
  $("stat-session").textContent = left > 0 ? duration(left) : "–";
}
setInterval(() => {
  for (const span of document.querySelectorAll(".countdown")) updateCountdown(span);
  tickSession();
  tickClock();
}, 1000);

async function mutate(path, body, message, btn) {
  if (S.busy) return false;
  S.busy = true;
  if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
  try {
    await api(path, body);
    if (message) toast(message);
    await loadState();
    return true;
  } catch (error) {
    if (error.status === 401) { showLock("Your session ended. Unlock to continue."); return false; }
    toast(error.message, true);
    await loadState().catch(() => {});
    return false;
  } finally {
    S.busy = false;
    if (btn && btn.isConnected) { btn.disabled = false; btn.removeAttribute("aria-busy"); }
  }
}

/* ---------- history & activity ---------- */
function historyRow(item) {
  const card = h("article", {cls: "item"});
  card.append(
    h("div", {cls: "item-head"}, h("h3", {text: item.label || item.target}), badge(item.status)),
    scope([["Action", pick(ACTIONS, item.action)], ["Reason", item.reason || "No reason given."],
      ["Requested", timeEl(item.created_at)], ["Closed", timeEl(item.completed_at)]]),
  );
  if (item.result && typeof item.result.text === "string") card.append(h("pre", {cls: "result-text", text: item.result.text}));
  return card;
}
async function loadHistory(reset) {
  const box = $("history"), more = $("history-more");
  const epoch = S.epoch;
  if (reset) { S.history.next = null; S.history.paged = false; }
  more.setAttribute("aria-busy", "true"); more.disabled = true;
  try {
    const page = await api("history" + qs({status: $("history-status").value, action: $("history-action").value, limit: PAGE, before: reset ? null : S.history.next}));
    if (epoch !== S.epoch) return;
    if (!reset) S.history.paged = true;
    const rows = page.items.map(historyRow);
    if (reset) box.replaceChildren(...(rows.length ? rows : [empty("Nothing here", "No requests match these filters.")]));
    else box.append(...rows);
    S.history.next = page.next_cursor;
    S.history.loaded = true;
    more.hidden = page.next_cursor === null;
    const shown = box.querySelectorAll("article").length;
    $("history-count").textContent = shown + " of " + page.total;
  } catch (error) {
    if (error.status === 401) return showLock("Your session ended. Unlock to continue.");
    if (reset) box.replaceChildren(empty("Couldn’t load history", error.message, true));
    else toast(error.message, true);
  } finally {
    more.removeAttribute("aria-busy"); more.disabled = false;
  }
}
function activityRow(ev) {
  const li = h("li");
  const text = pick(EVENTS, ev.event) + (ev.label ? ": " + ev.label : "");
  li.append(h("span", {cls: "ev"}, h("i", {cls: "dot dot-" + ev.event, attrs: {"aria-hidden": "true"}}), h("span", {text})), timeEl(ev.at));
  return li;
}
async function loadActivity(reset) {
  const box = $("activity"), more = $("activity-more");
  const epoch = S.epoch;
  if (reset) { S.activity.next = null; S.activity.paged = false; }
  more.setAttribute("aria-busy", "true"); more.disabled = true;
  try {
    const page = await api("audit" + qs({event: $("activity-event").value, limit: PAGE * 2, before: reset ? null : S.activity.next}));
    if (epoch !== S.epoch) return;
    if (!reset) S.activity.paged = true;
    const rows = page.items.map(activityRow);
    if (reset) box.replaceChildren(...(rows.length ? rows : [h("li", {}, empty("No activity yet", "Requests and decisions will show up here."))]));
    else box.append(...rows);
    S.activity.next = page.next_cursor;
    more.hidden = page.next_cursor === null;
  } catch (error) {
    if (error.status === 401) return showLock("Your session ended. Unlock to continue.");
    if (reset) box.replaceChildren(h("li", {}, empty("Couldn’t load activity", error.message, true)));
    else toast(error.message, true);
  } finally {
    more.removeAttribute("aria-busy"); more.disabled = false;
  }
}
$("history-filters").addEventListener("change", () => loadHistory(true));
$("activity-filters").addEventListener("change", () => loadActivity(true));
$("history-filters").addEventListener("submit", (e) => e.preventDefault());
$("activity-filters").addEventListener("submit", (e) => e.preventDefault());
$("history-more").addEventListener("click", () => loadHistory(false));
$("activity-more").addEventListener("click", () => loadActivity(false));

/* ---------- memory ---------- */
function memScope() { return {workspace: $("mem-workspace").value, project: $("mem-project").value}; }
function setMemFormsEnabled() {
  const hasWs = Boolean($("mem-workspace").value), hasProject = Boolean($("mem-project").value);
  $("mem-project").disabled = !hasWs;
  for (const el of $("mem-project-form").elements) el.disabled = !hasWs;
  for (const el of $("mem-record-form").elements) el.disabled = !hasProject;
  $("mem-query").disabled = $("mem-kind").disabled = !hasProject;
}
async function loadWorkspaces(select) {
  const {items} = await api("memory/workspaces");
  fillSelect($("mem-workspace"), items.map((w) => [w.id, w.title]));
  if (select) $("mem-workspace").value = select;
  await loadProjects();
}
async function loadProjects(select) {
  const ws = $("mem-workspace").value;
  const items = ws ? (await api("memory/projects" + qs({workspace: ws}))).items : [];
  fillSelect($("mem-project"), items.map((p) => [p.id, p.title]), false);
  if (select) $("mem-project").value = select;
  setMemFormsEnabled();
  await loadRecords();
}
function recordCard(rec) {
  const card = h("article", {cls: "item"});
  const del = button("Delete", "danger", () => deleteRecord(rec), "trash");
  del.setAttribute("aria-label", "Delete record: " + rec.title);
  card.append(h("div", {cls: "item-head"}, h("h3", {text: rec.title}), h("span", {cls: "badge", text: rec.kind})));
  if (rec.body) card.append(h("pre", {cls: "record-body", text: rec.body}));
  card.append(h("div", {cls: "row"}, h("p", {cls: "meta grow"}, document.createTextNode("v" + rec.version + " · updated "), timeEl(rec.updated_at)), del));
  return card;
}
async function loadRecords() {
  const box = $("mem-records");
  const {workspace, project} = memScope();
  if (!workspace) return box.replaceChildren(empty("No workspaces yet", "Create a workspace, then a project, to start saving records."));
  if (!project) return box.replaceChildren(empty("No project selected", "Create a project in this workspace to add records."));
  try {
    const {items} = await api("memory/records" + qs({workspace, project, q: $("mem-query").value.trim(), kind: $("mem-kind").value}));
    box.replaceChildren(...(items.length ? items.map(recordCard) : [empty("No records found", $("mem-query").value ? "Try a different search." : "Add your first record on the right.")]));
  } catch (error) {
    if (error.status === 401) return showLock("Your session ended. Unlock to continue.");
    box.replaceChildren(empty("Couldn’t load records", error.message, true));
  }
}
async function initMemory() {
  if (S.memory.loaded) return;
  S.memory.loaded = true;
  const kinds = (S.state && S.state.memory_kinds) || [];
  const cap = (k) => k.charAt(0).toUpperCase() + k.slice(1);
  fillSelect($("mem-record-kind"), kinds.map((k) => [k, cap(k)]));
  const kindFilter = $("mem-kind");
  kindFilter.replaceChildren(h("option", {text: "All kinds"}));
  kindFilter.options[0].value = "";
  for (const k of kinds) { const o = h("option", {text: cap(k)}); o.value = k; kindFilter.append(o); }
  try { await loadWorkspaces(); }
  catch (error) { S.memory.loaded = false; $("mem-records").replaceChildren(empty("Couldn’t load memory", error.message, true)); }
}
async function memoryAction(work, message) {
  try { await work(); if (message) toast(message); return true; }
  catch (error) {
    if (error.status === 401) { showLock("Your session ended. Unlock to continue."); return false; }
    toast(error.message, true); return false;
  }
}
function confirmDialog(text) {
  const dialog = $("confirm");
  $("confirm-text").textContent = text;
  if (typeof dialog.showModal !== "function") return Promise.resolve(window.confirm(text));
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), {once: true});
    dialog.returnValue = "cancel";
    dialog.showModal();
  });
}
async function deleteRecord(rec) {
  if (!(await confirmDialog("Delete “" + rec.title + "”" + (rec.kind === "task" ? " and its nested records" : "") + "? This can’t be undone."))) return;
  await memoryAction(async () => { await api("memory/records/delete", {...memScope(), id: rec.id}); await loadRecords(); }, "Record deleted.");
}
$("mem-workspace").addEventListener("change", () => memoryAction(() => loadProjects()));
$("mem-project").addEventListener("change", () => { setMemFormsEnabled(); void loadRecords(); });
let searchTimer;
$("mem-query").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadRecords, 250); });
$("mem-kind").addEventListener("change", loadRecords);
$("mem-search").addEventListener("submit", (e) => { e.preventDefault(); void loadRecords(); });
$("mem-workspace-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("mem-workspace-title");
  await memoryAction(async () => {
    const ws = await api("memory/workspaces", {title: input.value});
    input.value = "";
    await loadWorkspaces(ws.id);
  }, "Workspace created.");
});
$("mem-project-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("mem-project-title");
  await memoryAction(async () => {
    const p = await api("memory/projects", {workspace: $("mem-workspace").value, title: input.value});
    input.value = "";
    await loadProjects(p.id);
  }, "Project created.");
});
$("mem-record-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await memoryAction(async () => {
    await api("memory/records", {...memScope(), kind: $("mem-record-kind").value, title: $("mem-record-name").value, body: $("mem-record-body").value});
    $("mem-record-name").value = ""; $("mem-record-body").value = "";
    await loadRecords();
  }, "Record saved.");
});


/* ---------- OpenJarvis brain ---------- */
function fillBrain() {
  const st = S.state || {};
  const enabled = Boolean(st.brain && st.openjarvis_url);
  $("brain-pill").textContent = enabled ? "On" : "Off";
  if (document.activeElement !== $("brain-url") && $("brain-url").value === "") {
    $("brain-url").value = st.openjarvis_url || "";
  } else if (document.activeElement !== $("brain-url") && st.openjarvis_url && $("brain-url").value !== st.openjarvis_url) {
    $("brain-url").value = st.openjarvis_url;
  }
}

function setBrainError(id, message) {
  const el = $(id);
  el.hidden = !message;
  el.textContent = message || "";
}

function appendBrainLog(role, text) {
  const item = h("article", {cls: "item"});
  item.append(h("p", {cls: "muted small", text: role}), h("p", {text}));
  $("brain-log").prepend(item);
}

$("brain-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setBrainError("brain-config-error", "");
  const btn = $("brain-save");
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  try {
    const result = await api("brain/config", {url: $("brain-url").value.trim()});
    if (S.state) { S.state.brain = result.enabled; S.state.openjarvis_url = result.url || ""; }
    $("brain-url").value = result.url || "";
    fillBrain();
    $("brain-status").textContent = result.enabled ? "Saved. OpenJarvis is the brain for this session." : "Saved. The brain is off.";
    toast(result.enabled ? "OpenJarvis address saved." : "OpenJarvis brain turned off.");
  } catch (error) {
    setBrainError("brain-config-error", error.message);
  } finally {
    btn.disabled = false; btn.removeAttribute("aria-busy");
  }
});

$("brain-check").addEventListener("click", async () => {
  setBrainError("brain-config-error", "");
  $("brain-status").textContent = "Checking…";
  try {
    const result = await api("brain/status");
    if (result.ok) $("brain-status").textContent = "OpenJarvis is reachable" + (result.engine ? " (" + result.engine + ")." : ".");
    else if (!result.enabled) $("brain-status").textContent = "No OpenJarvis address is set.";
    else $("brain-status").textContent = "OpenJarvis did not respond. Start it with jarvis gui on this computer.";
  } catch (error) {
    $("brain-status").textContent = "";
    setBrainError("brain-config-error", error.message);
  }
});

$("brain-ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setBrainError("brain-ask-error", "");
  const message = $("brain-message").value.trim();
  if (!message) { setBrainError("brain-ask-error", "Enter a prompt."); return $("brain-message").focus(); }
  const btn = $("brain-ask");
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  appendBrainLog("You", message);
  try {
    const result = await api("brain/ask", {message});
    if (result.reply) appendBrainLog("OpenJarvis", result.reply);
    const queued = result.proposals || [];
    const blocked = result.blocked || [];
    if (queued.length) {
      appendBrainLog("Access desk", "OpenJarvis asked for approval. " + queued.length + " request(s) are in the permission queue. Nothing has been run.");
      toast("A request is waiting in the permission queue.");
    }
    if (blocked.length) {
      appendBrainLog("Access desk", "A tool was blocked. This desk only allows the approval request tool, and never runs actions on its own.");
    }
    if (!result.reply && !queued.length && !blocked.length) appendBrainLog("OpenJarvis", "(No text in the reply.)");
    $("brain-message").value = "";
  } catch (error) {
    setBrainError("brain-ask-error", error.message);
  } finally {
    btn.disabled = false; btn.removeAttribute("aria-busy");
  }
});

/* ---------- tabs ---------- */
const TABS = ["requests", "history", "activity", "memory", "brain"];
function selectTab(name, focus = false) {
  S.tab = name;
  for (const t of TABS) {
    const tab = $("tab-" + t), panel = $("panel-" + t);
    const on = t === name;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    panel.hidden = !on;
  }
  if (focus) $("tab-" + name).focus();
  if (name === "history" && !S.history.loaded) void loadHistory(true);
  else if (name === "activity") void loadActivity(true);
  else if (name === "memory") void initMemory();
  else if (name === "brain") fillBrain();
}
for (const t of TABS) $("tab-" + t).addEventListener("click", () => selectTab(t));
document.querySelector(".tabs").addEventListener("keydown", (e) => {
  const visible = TABS.filter((t) => !$("tab-" + t).hidden);
  let i = visible.indexOf(S.tab);
  if (e.key === "ArrowRight") i = (i + 1) % visible.length;
  else if (e.key === "ArrowLeft") i = (i - 1 + visible.length) % visible.length;
  else if (e.key === "Home") i = 0;
  else if (e.key === "End") i = visible.length - 1;
  else return;
  e.preventDefault();
  selectTab(visible[i], true);
});

/* ---------- forms & controls ---------- */
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("key").value.trim();
  const err = $("login-error");
  if (!key) { err.textContent = "Enter the owner key from your terminal."; err.hidden = false; return $("key").focus(); }
  $("key").value = "";
  const btn = event.currentTarget.querySelector("button");
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  try {
    await api("login", {key});
    err.hidden = true;
    playBoot();
    S.epoch++;
    await loadState();
    schedule(POLL_MS);
  } catch (error) {
    err.textContent = error.status === 401 ? ERRORS.invalid_key : error.message;
    err.hidden = false;
    $("key").focus();
  } finally {
    btn.disabled = false; btn.removeAttribute("aria-busy");
  }
});
$("request-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const ok = await mutate("propose", {action: $("action").value, target: $("target").value, reason: $("reason").value.trim()},
    "Demo request created. Allow or deny it in the queue.", $("propose"));
  if (ok) { $("reason").value = ""; $("reason-count").textContent = "0 / 240"; }
  fillTargets();
});
$("reason").addEventListener("input", () => { $("reason-count").textContent = $("reason").value.length + " / 240"; });
$("action").addEventListener("change", fillTargets);
$("pause").addEventListener("click", (e) => {
  if (!S.state) return;
  const paused = !S.state.paused;
  void mutate("pause", {paused}, paused ? "All access paused. Outstanding approvals were revoked." : "Access resumed. New requests still need your approval.", e.currentTarget);
});
$("logout").addEventListener("click", async () => {
  try { await api("logout", {}); } catch { /* lock locally regardless */ }
  clearTimeout(S.timer);
  showLock();
  toast("Access desk locked.");
});
$("retry").addEventListener("click", boot);

async function boot() {
  show("loading");
  try {
    await loadState();
    if (!sessionStorageGet("jarvis-booted")) playBoot();
    schedule(POLL_MS);
  } catch (error) {
    if (error.status === 401) showLock();
    else if (error.status === 0) show("offline");
    else { showLock(); toast(error.message, true); }
  }
}
void boot();
