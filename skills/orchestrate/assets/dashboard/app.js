"use strict";
// orchestrate dashboard: polls /api/<slug>/state with If-None-Match and renders it. No build step, no network
// beyond this server, no model calls. Everything on screen comes from state.json, written by dash.py.

const POLL_MS = 5000;
const PROGRAMS_MS = 15000;
const LS = { theme: "orch-dash:theme", sidebar: "orch-dash:sidebar" };

const SECTIONS = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "issues", label: "Issues", icon: "issues" },
  { id: "prs", label: "Pull requests", icon: "prs" },
  { id: "tasks", label: "Tasks", icon: "graph" },
  { id: "workers", label: "Workers", icon: "workers" },
  { id: "activity", label: "Activity", icon: "activity" },
];

const ICON = {
  overview: '<rect x="2" y="2" width="5" height="5" rx="1.2"/><rect x="9" y="2" width="5" height="5" rx="1.2"/><rect x="2" y="9" width="5" height="5" rx="1.2"/><rect x="9" y="9" width="5" height="5" rx="1.2"/>',
  issues: '<circle cx="8" cy="8" r="5.6"/><circle cx="8" cy="8" r="1.6" fill="currentColor" stroke="none"/>',
  prs: '<circle cx="4" cy="3.5" r="1.6"/><circle cx="4" cy="12.5" r="1.6"/><circle cx="12" cy="12.5" r="1.6"/><path d="M4 5.1v5.8M12 10.9V6.5a2 2 0 0 0-2-2H7.2M8.8 2.9 7.2 4.5l1.6 1.6"/>',
  graph: '<rect x="1.5" y="2.5" width="4" height="3" rx="1"/><rect x="10.5" y="2.5" width="4" height="3" rx="1"/><rect x="6" y="10.5" width="4" height="3" rx="1"/><path d="M5.5 4h5M3.5 5.5v2.5a2 2 0 0 0 2 2H6M12.5 5.5v2.5a2 2 0 0 1-2 2H10"/>',
  workers: '<rect x="4" y="4" width="8" height="8" rx="1.6"/><path d="M6.5 1.5v2M9.5 1.5v2M6.5 12.5v2M9.5 12.5v2M1.5 6.5h2M1.5 9.5h2M12.5 6.5h2M12.5 9.5h2"/>',
  activity: '<path d="M1.5 8.5h2.8l1.9-5 3.4 9.5 1.9-4.5h3"/>',
  collapse: '<rect x="2" y="2.5" width="12" height="11" rx="2"/><path d="M6 2.5v11M10.5 6.2 8.8 8l1.7 1.8"/>',
  expand: '<rect x="2" y="2.5" width="12" height="11" rx="2"/><path d="M6 2.5v11M9 6.2 10.7 8 9 9.8"/>',
  sun: '<circle cx="8" cy="8" r="2.8"/><path d="M8 1.2v1.6M8 13.2v1.6M1.2 8h1.6M13.2 8h1.6M3.2 3.2l1.1 1.1M11.7 11.7l1.1 1.1M3.2 12.8l1.1-1.1M11.7 4.3l1.1-1.1"/>',
  moon: '<path d="M13.4 9.6A5.6 5.6 0 0 1 6.4 2.6a5.6 5.6 0 1 0 7 7Z"/>',
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8"/>',
  arrow: '<path d="M2.5 8h10.5M9 4l4 4-4 4"/>',
  alert: '<path d="M8 2.2 1.6 13.3h12.8Z"/><path d="M8 6.6v3.1M8 11.4v.1"/>',
  check: '<path d="M3.3 8.4 6.4 11.4 12.7 4.6"/>',
  fail: '<circle cx="8" cy="8" r="6"/><path d="M6 6l4 4M10 6l-4 4"/>',
  clock: '<circle cx="8" cy="8" r="6"/><path d="M8 4.8V8l2.2 1.5"/>',
  user: '<circle cx="8" cy="5.4" r="2.6"/><path d="M2.8 14c.6-2.8 2.6-4.3 5.2-4.3s4.6 1.5 5.2 4.3"/>',
  lock: '<rect x="3" y="7" width="10" height="7" rx="1.6"/><path d="M5.4 7V5.2a2.6 2.6 0 0 1 5.2 0V7"/>',
  merge: '<circle cx="4" cy="3.5" r="1.6"/><circle cx="4" cy="12.5" r="1.6"/><circle cx="12" cy="9" r="1.6"/><path d="M4 5.1v5.8M4 5.1c0 2.4 2.2 3.9 6.4 3.9"/>',
  note: '<path d="M3 2.2h6.8L13 5.4v8.4H3Z"/><path d="M5.6 7.6h4.8M5.6 10.3h3.2"/>',
  spark: '<path d="M8 1.8v3.4M8 10.8v3.4M1.8 8h3.4M10.8 8h3.4"/>',
  flag: '<path d="M3.6 14.2V2.4M3.6 2.8h8.2l-1.6 3 1.6 3H3.6"/>',
  stop: '<path d="M5.5 1.8h5l3.7 3.7v5l-3.7 3.7h-5l-3.7-3.7v-5Z"/><path d="M6 8h4"/>',
  park: '<rect x="2.5" y="2.5" width="11" height="11" rx="2"/><path d="M6.5 11V5h2.2a1.9 1.9 0 0 1 0 3.8H6.5"/>',
  gear: '<circle cx="8" cy="8" r="2.2"/><path d="M8 1.6v2M8 12.4v2M1.6 8h2M12.4 8h2M3.5 3.5l1.4 1.4M11.1 11.1l1.4 1.4M3.5 12.5l1.4-1.4M11.1 4.9l1.4-1.4"/>',
  link: '<path d="M6.8 9.2a2.8 2.8 0 0 0 4 0l2-2a2.8 2.8 0 0 0-4-4l-.7.7M9.2 6.8a2.8 2.8 0 0 0-4 0l-2 2a2.8 2.8 0 0 0 4 4l.7-.7"/>',
  sort: '<path d="M5 2.5v11M2.5 11 5 13.5 7.5 11M11 13.5v-11M8.5 5 11 2.5 13.5 5"/>',
  yield: '<path d="M13.5 8H3M6.5 4.5 3 8l3.5 3.5"/>',
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const icon = (name) => `<svg class="i" viewBox="0 0 16 16" aria-hidden="true">${ICON[name] || ""}</svg>`;
const ms = (iso) => (iso ? Date.parse(iso) : NaN);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked: the choice just is not remembered */ } },
};

const S = {
  programs: [], slug: null, section: "overview", state: null, etag: null, lastOk: 0, down: false, sbOpen: false,
  filters: { issues: "all", prs: "open", workers: "active", activity: "all" }, q: "", showDone: false,
};

// ------------------------------------------------------------ formatting

function rel(iso, now = Date.now()) {
  const t = ms(iso);
  if (isNaN(t)) return "—";
  let s = Math.round((now - t) / 1000);
  const future = s < 0;
  s = Math.abs(s);
  if (s < 3) return "방금";
  const txt = s < 60 ? `${s}초` : s < 3600 ? `${Math.floor(s / 60)}분` : s < 86400 * 2 ? `${Math.floor(s / 3600)}시간` : `${Math.floor(s / 86400)}일`;
  return future ? `${txt} 후` : `${txt} 전`;
}
const relSpan = (iso) => `<span data-rel="${esc(iso || "")}" title="${esc(iso ? new Date(iso).toLocaleString() : "")}">${rel(iso)}</span>`;
const hhmm = (t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const dayLabel = (t) => new Date(t).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
const num = (v, d = 0) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(d));
const compact = (n) => (n == null ? "—" : n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : String(n));

function tag(text, tone = "", ic = "dot") {
  const lead = ic === "dot" ? '<span class="dot"></span>' : ic ? icon(ic) : "";
  return `<span class="tag ${tone ? "tone-" + tone : ""}">${lead}${esc(text)}</span>`;
}
function link(url, text, cls = "link") {
  const u = safeUrl(url);
  return u ? `<a class="${cls}" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text;
}
// Age of a waiting PR; amber past 2 h and red past 3 h, re-evaluated every second by the ticker.
function ageTone(iso) { const h = (Date.now() - ms(iso)) / 3600e3; return h > 3 ? "bad" : h > 2 ? "warn" : ""; }
function ageText(iso) { const m = Math.max(0, Math.floor((Date.now() - ms(iso)) / 60000)); return isNaN(m) ? "—" : m < 60 ? `${m}m` : `${(m / 60).toFixed(1)}h`; }
const ageTag = (iso) => iso ? `<span class="tag ${ageTone(iso) ? "tone-" + ageTone(iso) : ""}" data-age="${esc(iso)}">${icon("clock")}<span>${ageText(iso)}</span></span>` : "";

const STATE_TONE = { completed: "good", started: "accent", triage: "warn" };
const CI = { pass: ["passing", "good", "check"], fail: ["failing", "bad", "fail"], pending: ["running", "warn", "clock"], none: ["no checks", "", null] };
const VERDICT = { pass: ["pass", "good", "check"], fail: ["fail", "bad", "fail"], stale: ["stale", "warn", "clock"], none: ["none", "", null] };
const ORDER_TONE = { ready: "good", catching_up: "accent", waiting: "warn", blocked: "bad", gone: "", unknown: "" };
const NODE_TONE = { done: "good", landing: "accent", in_progress: "accent", blocked: "bad", waiting: "" };
const NODE_LABEL = { done: "done", landing: "landing", in_progress: "in progress", blocked: "blocked", waiting: "waiting" };

// ------------------------------------------------------------ theme

const systemDark = matchMedia("(prefers-color-scheme: dark)");
const theme = () => document.documentElement.dataset.theme || (systemDark.matches ? "dark" : "light");

function paintThemeButton() {
  const next = theme() === "dark" ? "light" : "dark";
  const btn = $("#theme-toggle");
  btn.innerHTML = `${icon(next === "light" ? "sun" : "moon")}<span class="sb-text">${next === "light" ? "Light theme" : "Dark theme"}</span>`;
  btn.setAttribute("aria-label", `Switch to ${next} theme`);
  btn.title = `Switch to ${next} theme`;
}

// ------------------------------------------------------------ sidebar: full / icons (wide), icons / overlay (split), drawer (phone)

function sbMode() {
  const w = innerWidth;
  if (w <= 600) return S.sbOpen ? "drawer-open" : "drawer";
  if (w <= 960) return S.sbOpen ? "overlay" : "icons";
  return store.get(LS.sidebar) === "collapsed" ? "icons" : "full";
}

function applySidebar() {
  const mode = sbMode();
  document.documentElement.dataset.sb = mode;
  $("#scrim").hidden = !(mode === "overlay" || mode === "drawer-open");
  $("#menu-btn").setAttribute("aria-expanded", String(mode === "drawer-open"));
  const expanded = mode === "full" || mode === "overlay";
  const btn = $("#sb-toggle");
  btn.innerHTML = `${icon(expanded ? "collapse" : "expand")}<span class="sb-text">${mode === "overlay" ? "Close" : "Collapse"}</span>`;
  btn.setAttribute("aria-expanded", String(expanded));
  btn.setAttribute("aria-label", `${expanded ? "Collapse" : "Expand"} sidebar ( [ )`);
  btn.title = `${expanded ? "Collapse" : "Expand"} sidebar  [`;
}

function toggleSidebar() {
  if (innerWidth > 960) store.set(LS.sidebar, store.get(LS.sidebar) === "collapsed" ? "expanded" : "collapsed");
  else S.sbOpen = !S.sbOpen;
  applySidebar();
  if (S.sbOpen) { const a = $(".nav-item[aria-current='page']", $("#sidebar")); a && a.focus(); }
  setTimeout(redrawCharts, 50);
}

function closeFloatingSidebar() {
  if (!S.sbOpen) return;
  S.sbOpen = false; applySidebar();
  if ($("#sidebar").contains(document.activeElement)) (innerWidth <= 600 ? $("#menu-btn") : $("#sb-toggle")).focus();
}

function initChrome() {
  paintThemeButton();
  applySidebar();
  $("#menu-btn").innerHTML = icon("menu");
  $("#sb-close").innerHTML = icon("x");
  $("#theme-toggle").addEventListener("click", () => {
    const t = theme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t; store.set(LS.theme, t);
    paintThemeButton(); redrawCharts();
  });
  systemDark.addEventListener("change", () => { paintThemeButton(); redrawCharts(); });
  $("#sb-toggle").addEventListener("click", toggleSidebar);
  $("#menu-btn").addEventListener("click", toggleSidebar);
  $("#sb-close").addEventListener("click", closeFloatingSidebar);
  $("#scrim").addEventListener("click", closeFloatingSidebar);
  $("#sidebar").addEventListener("click", (e) => { if (e.target.closest("a.nav-item")) closeFloatingSidebar(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeFloatingSidebar();
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
    if (e.key === "[" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) toggleSidebar();
  });
  let resizeTimer, lastW = innerWidth;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const crossed = [600, 960].some((b) => (lastW <= b) !== (innerWidth <= b));
      lastW = innerWidth;
      if (crossed) S.sbOpen = false;
      applySidebar();
      if (S.section === "tasks") renderView(); else redrawCharts();
    }, 120);
  });
}

// ------------------------------------------------------------ sidebar content, header, freshness

function mainTone(m) { return m === "red" ? "bad" : m === "pending" ? "warn" : m === "green" ? "good" : ""; }

function renderSidebar() {
  const st = S.state;
  $("#program-list").innerHTML = S.programs.length ? S.programs.map((p) => {
    const s = p.summary || {};
    const cur = p.slug === S.slug;
    const prog = s.predicate_total ? `${s.predicate_done ?? "–"}/${s.predicate_total}` : "";
    return `<li><a class="nav-item" href="#/${encodeURIComponent(p.slug)}/${S.section}" ${cur ? 'aria-current="page"' : ""} title="${esc(p.slug)}">
      <span class="avatar" aria-hidden="true">${esc(p.slug.slice(0, 2))}<span class="dot ${mainTone(s.main) ? "tone-" + mainTone(s.main) : ""}"></span></span>
      <span class="sb-text">${esc(p.slug)}</span><span class="count">${esc(prog)}</span></a></li>`;
  }).join("") : `<li class="sb-text muted" style="padding:0 8px">No programs</li>`;

  const counts = st ? {
    issues: (st.issues || []).filter((i) => !["completed", "canceled"].includes(i.state_type)).length,
    prs: (st.prs || []).filter((p) => p.state === "open").length,
    tasks: (st.tasks || []).filter((t) => t.status !== "completed").length || null,
    workers: st.summary?.in_flight,
  } : {};
  $("#section-list").innerHTML = SECTIONS.map((s) => `<li><a class="nav-item" href="#/${encodeURIComponent(S.slug || "")}/${s.id}"
      ${s.id === S.section ? 'aria-current="page"' : ""} title="${s.label}">${icon(s.icon)}<span class="sb-text">${s.label}</span>
      ${counts[s.id] != null ? `<span class="count">${counts[s.id]}</span>` : ""}</a></li>`).join("");
}

function setLive() {
  const el = $("#live");
  const age = Date.now() - S.lastOk;
  const state = S.down ? "down" : !S.lastOk ? "" : age < POLL_MS * 3 ? "ok" : "stale";
  el.dataset.state = state;
  $("#live-text").textContent = state === "ok" ? "Live" : state === "stale" ? "Stale" : state === "down" ? "Offline" : "Connecting";
}

function renderHeader() {
  const st = S.state;
  if (!st) { $("#headline").innerHTML = `<h1>${esc(S.slug || "orchestrate")}</h1>`; $("#top-right").innerHTML = ""; return; }
  const s = st.summary || {};
  const policy = st.merge_policy === "human-gate" ? tag("human-gate", "warn", "user") : tag("autonomous", "");
  const pct = s.predicate_total && s.predicate_done != null ? Math.round((100 * s.predicate_done) / s.predicate_total) : null;
  const segs = (st.predicate || []).map((p) => `<i class="${p.state_type === "completed" ? "done" : p.state_type === "started" ? "started" : ""}"></i>`).join("");
  $("#headline").innerHTML = `<h1 class="ellipsis" title="${esc(st.run_objective || st.slug)}">${esc(st.run_objective || st.slug)}</h1>
    <div class="meta">
      <span class="progress" data-src="tracker"><span class="segs" style="width:96px" aria-hidden="true">${segs}</span>
        ${pct == null ? "predicate —" : `predicate ${s.predicate_done}/${s.predicate_total} · ${pct}%`}</span>
      ${st.run_objective ? `<span>${esc(st.slug)}</span>` : ""}
      ${link(st.repo ? `https://github.com/${st.repo}` : "", `<span class="mono">${esc(st.repo || "")}</span>`, "")}
      <span class="mono">${esc(st.run || "")}</span>${policy}</div>`;
  const errs = (st.errors || []).length;
  $("#top-right").innerHTML = errs ? `<a href="#/${encodeURIComponent(S.slug)}/overview">${tag(`${errs} source error${errs > 1 ? "s" : ""}`, "warn", "alert")}</a>` : "";
}

// Freshness thresholds in seconds: [warn, stale]. Tracker and GitHub are polled once per collect interval.
const SOURCE_META = [
  ["ledger", "Ledger", () => [60, 180]],
  ["orca", "Orca", () => [60, 180]],
  ["github", "GitHub", (iv) => [3 * iv, 6 * iv]],
  ["tracker", "Tracker", (iv) => [3 * iv, 6 * iv]],
];

function sourceLevel(name) {
  const st = S.state, src = st?.sources?.[name], meta = SOURCE_META.find((m) => m[0] === name);
  if (!src || !meta) return "none";
  if (src.configured === false) return "off";
  const age = (Date.now() - ms(src.updated_at)) / 1000;
  if (isNaN(age)) return "stale";
  const [warn, stale] = meta[2](st.interval || 60);
  return age > stale ? "stale" : age > warn || src.ok === false ? "warn" : "good";
}

function renderFresh() {
  const st = S.state, el = $("#fresh");
  if (!st) { el.innerHTML = ""; return; }
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  el.innerHTML = SOURCE_META.map(([name, label]) => {
    const src = st.sources?.[name] || {}, at = src.updated_at;
    const time = at ? new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "—";
    const title = src.configured === false ? `${label}: not configured`
      : `${label} · last success ${at ? `${new Date(at).toLocaleString()} (${tz})` : "never"}${src.ok === false ? ` · failing: ${src.error}` : ""}`;
    return `<span class="src" data-fresh="${name}" title="${esc(title)}"><span class="dot"></span><span class="nm">${label}</span>
      ${src.configured === false ? '<span>not configured</span>' : `<span class="at">${time}</span><span class="ago" data-rel="${esc(at || "")}">${rel(at)}</span>`}
      ${src.ok === false ? `<span class="fail">${icon("alert")}</span>` : ""}</span>`;
  }).join("");
  applyFreshness();
}

function applyFreshness() {
  if (!S.state) return;
  const levels = Object.fromEntries(SOURCE_META.map(([n]) => [n, sourceLevel(n)]));
  $$("[data-fresh]").forEach((el) => { el.dataset.level = levels[el.dataset.fresh]; });
  $$("[data-src]").forEach((el) => el.classList.toggle("is-stale", el.dataset.src.split(" ").some((n) => levels[n] === "stale")));
}

// ------------------------------------------------------------ charts

function niceStep(max, ticks = 4) {
  if (max <= ticks) return 1;
  const raw = max / ticks, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p;
  return Math.max(1, (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p); // counts: integer ticks only
}

function timeTicks(t0, t1, width) {
  const H = 3600e3;
  const steps = [H, 2 * H, 3 * H, 6 * H, 12 * H, 24 * H, 48 * H, 96 * H, 168 * H];
  const want = Math.max(2, Math.floor(width / 96));
  const step = steps.find((s) => (t1 - t0) / s <= want) || steps[steps.length - 1];
  const out = [];
  const d = new Date(t0); d.setMinutes(0, 0, 0);
  let t = d.getTime();
  const hs = step / H;
  while (hs <= 24 && new Date(t).getHours() % hs !== 0) t += H;
  for (; t <= t1; t += step) if (t >= t0) out.push(t);
  return out.map((t) => ({ t, label: new Date(t).getHours() === 0 ? new Date(t).toLocaleDateString([], { month: "short", day: "numeric" }) : hhmm(t) }));
}

function valueAt(points, t) {
  let v = null;
  for (const p of points) { if (p.t <= t) v = p.v; else break; }
  return v;
}

function showTip(html, x, y) {
  const el = $("#tip");
  el.innerHTML = html; el.hidden = false;
  const r = el.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > innerHeight - 8) top = y - r.height - 14;
  el.style.left = `${Math.max(8, left)}px`; el.style.top = `${Math.max(8, top)}px`;
}
function hideTip() { $("#tip").hidden = true; }

function yAxis(max, x0, x1, y) {
  const step = niceStep(max);
  let g = "";
  for (let v = 0; v <= max + 1e-9; v += step) {
    g += `<line class="${v === 0 ? "base-line" : "grid-line"}" x1="${x0}" x2="${x1}" y1="${y(v)}" y2="${y(v)}"/>
      <text x="${x0 - 8}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`;
  }
  return g;
}

// Step lines over time; every series shares one y-scale (never a second axis).
function stepChart(el, { series, height = 200, domain, label }) {
  const W = Math.max(240, el.clientWidth), H = height, m = { l: 30, r: 14, t: 10, b: 24 };
  const [t0, t1] = domain;
  const all = series.flatMap((s) => s.points.map((p) => p.v)).filter((v) => v != null);
  const step = niceStep(Math.max(1, ...all));
  const max = Math.max(step, Math.ceil(Math.max(1, ...all) / step) * step);
  const x = (t) => m.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - m.l - m.r);
  const y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(label)}"><g class="axis">${yAxis(max, m.l, W - m.r, y)}`;
  for (const tk of timeTicks(t0, t1, W - m.l - m.r)) svg += `<text x="${x(tk.t)}" y="${H - 7}" text-anchor="middle">${esc(tk.label)}</text>`;
  svg += `</g>`;
  for (const s of series) {
    const pts = s.points.filter((p) => p.v != null);
    if (!pts.length) continue;
    let d = `M${x(pts[0].t)},${y(pts[0].v)}`;
    for (let i = 1; i < pts.length; i++) d += `H${x(pts[i].t)}V${y(pts[i].v)}`;
    d += `H${x(t1)}`;
    if (s.area) svg += `<path d="${d}V${y(0)}H${x(pts[0].t)}Z" style="fill:${s.color};opacity:.1"/>`;
    svg += `<path d="${d}" fill="none" style="stroke:${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    svg += `<circle cx="${x(t1)}" cy="${y(pts[pts.length - 1].v)}" r="4" style="fill:${s.color};stroke:var(--panel)" stroke-width="2"/>`;
  }
  svg += `<g class="hover" visibility="hidden"><line class="hover-line" y1="${m.t}" y2="${H - m.b}"/>${series.map((s) => `<circle r="4" style="fill:${s.color};stroke:var(--panel)" stroke-width="2"/>`).join("")}</g>`;
  svg += `<rect class="hit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/></svg>`;
  el.innerHTML = svg;
  const hover = $(".hover", el), line = $("line", hover), dots = $$("circle", hover), hit = $(".hit", el);
  hit.addEventListener("pointermove", (e) => {
    const r = el.querySelector("svg").getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const t = t0 + ((px - m.l) / (W - m.l - m.r)) * (t1 - t0);
    hover.setAttribute("visibility", "visible");
    line.setAttribute("x1", px); line.setAttribute("x2", px);
    const rows = series.map((s, i) => {
      const v = valueAt(s.points.filter((p) => p.v != null), t);
      dots[i].setAttribute("cx", px); dots[i].setAttribute("cy", v == null ? -99 : y(v));
      return `<div class="row"><span class="sw" style="background:${s.color}"></span>${esc(s.name)}<b>${v ?? "—"}</b></div>`;
    }).join("");
    showTip(`<div class="tt">${esc(dayLabel(t))} ${hhmm(t)}</div>${rows}`, e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", () => { hover.setAttribute("visibility", "hidden"); hideTip(); });
}

function barPath(x, y, w, h, r) {
  if (h <= 0) return "";
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function groupedBars(el, { buckets, series, height = 200, label }) {
  const W = Math.max(240, el.clientWidth), H = height, m = { l: 30, r: 10, t: 10, b: 24 };
  const max0 = Math.max(1, ...buckets.flatMap((b) => b.values));
  const step = niceStep(max0);
  const max = Math.max(step, Math.ceil(max0 / step) * step);
  const band = (W - m.l - m.r) / buckets.length;
  const bw = Math.max(3, Math.min(12, (band - 8) / series.length - 2));
  const y = (v) => H - m.b - (v / max) * (H - m.t - m.b);
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(label)}"><g class="axis">${yAxis(max, m.l, W - m.r, y)}`;
  const every = Math.max(1, Math.ceil(buckets.length / Math.max(2, Math.floor((W - m.l) / 70))));
  buckets.forEach((b, i) => {
    if ((buckets.length - 1 - i) % every === 0) svg += `<text x="${m.l + band * i + band / 2}" y="${H - 7}" text-anchor="middle">${esc(b.label)}</text>`;
  });
  svg += `</g>`;
  buckets.forEach((b, i) => {
    const gx = m.l + band * i + band / 2 - (series.length * bw + (series.length - 1) * 2) / 2;
    series.forEach((s, j) => { svg += `<path d="${barPath(gx + j * (bw + 2), y(b.values[j]), bw, y(0) - y(b.values[j]), 3)}" style="fill:${s.color}"/>`; });
    svg += `<rect class="band" data-i="${i}" x="${m.l + band * i}" y="0" width="${band}" height="${H - m.b}" fill="transparent"/>`;
  });
  el.innerHTML = svg + "</svg>";
  $$(".band", el).forEach((r) => {
    r.addEventListener("pointermove", (e) => {
      const b = buckets[+r.dataset.i];
      showTip(`<div class="tt">${esc(b.title)}</div>${series.map((s, j) => `<div class="row"><span class="sw" style="background:${s.color}"></span>${esc(s.name)}<b>${b.values[j]}</b></div>`).join("")}`, e.clientX, e.clientY);
    });
    r.addEventListener("pointerleave", hideTip);
  });
}

function sparkline(values, color, w = 120, h = 28) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * (w - 4) + 2).toFixed(1)},${(h - 3 - ((x - lo) / span) * (h - 6)).toFixed(1)}`);
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts.join(" ")}" fill="none" style="stroke:${color}" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
}

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function drawCharts(st) {
  const C = { accent: cssVar("--accent"), ink3: cssVar("--ink-3"), derived: cssVar("--derived") };
  const now = ms(st.generated_at);
  const rows = (st.series || []).map((r) => ({ ...r, t: ms(r.t) })).filter((r) => !isNaN(r.t));
  const t0 = Math.min(ms(st.created_at) || Infinity, rows[0]?.t ?? now);
  const burn = $("#chart-burn");
  if (burn) {
    const pts = rows.filter((r) => r.done != null);
    if (!pts.length) burn.innerHTML = `<div class="empty-chart">No tracker data yet — the first collect fills this in.</div>`;
    else stepChart(burn, { domain: [t0, now], height: 210, label: "Burn-up: done versus total scope", series: [
      { name: "Scope", color: C.ink3, points: pts.map((r) => ({ t: r.t, v: r.done + r.open })) },
      { name: "Done", color: C.accent, area: true, points: pts.map((r) => ({ t: r.t, v: r.done })) },
    ] });
  }
  const flow = $("#chart-flow");
  if (flow) {
    if (!rows.length) flow.innerHTML = `<div class="empty-chart">No samples yet.</div>`;
    else stepChart(flow, { domain: [t0, now], height: 170, label: "In-flight workers versus concurrency cap", series: [
      { name: "Cap", color: C.ink3, points: rows.map((r) => ({ t: r.t, v: r.cap })) },
      { name: "In-flight", color: C.accent, points: rows.map((r) => ({ t: r.t, v: r.in_flight })) },
    ] });
  }
  const bars = $("#chart-growth");
  if (bars) {
    if (!st.issues) { bars.innerHTML = `<div class="empty-chart">No tracker data.</div>`; return; }
    const B = 6 * 3600e3;
    const start = new Date(t0); start.setMinutes(0, 0, 0); start.setHours(start.getHours() - (start.getHours() % 6));
    let edges = [];
    for (let t = start.getTime(); t <= now; t += B) edges.push(t);
    edges = edges.slice(-16);
    const inScope = (i) => i.in_predicate || i.triage === "admitted";
    const buckets = edges.map((b0) => {
      const inB = (iso) => { const t = ms(iso); return t >= b0 && t < b0 + B; };
      return {
        label: new Date(b0).getHours() === 0 ? new Date(b0).toLocaleDateString([], { month: "short", day: "numeric" }) : hhmm(b0),
        title: `${dayLabel(b0)} ${hhmm(b0)}–${hhmm(b0 + B)}`,
        values: [st.issues.filter((i) => i.derived && inB(i.created_at)).length,
                 st.issues.filter((i) => inScope(i) && i.state_type === "completed" && inB(i.completed_at)).length],
      };
    });
    groupedBars(bars, { buckets, height: 170, label: "Derived tickets versus done tickets per 6 hours",
      series: [{ name: "Derived", color: C.derived }, { name: "Done", color: C.accent }] });
  }
}

function redrawCharts() {
  if (!S.state) return;
  if (S.section === "overview") drawCharts(S.state);
  if (S.section === "tasks") drawDag(S.state);
}

// ------------------------------------------------------------ dependency graph

function dagNodes(g) {
  const live = new Set(g.nodes.filter((n) => n.status !== "done").map((n) => n.id));
  const context = new Set(g.edges.filter(([a, b]) => live.has(b)).map(([a]) => a));
  return g.nodes.filter((n) => S.showDone || live.has(n.id) || context.has(n.id));
}

function drawDag(st) {
  const el = $("#dag");
  if (!el) return;
  const g = st.graph || { nodes: [], edges: [], critical: [] };
  const nodes = dagNodes(g);
  if (!nodes.length) { el.innerHTML = `<div class="empty"><span>${g.nodes.length ? "Everything is done." : "No tasks or dependencies recorded yet."}</span></div>`; return; }
  const crit = new Set(g.critical || []);
  const critEdge = new Set((g.critical || []).slice(1).map((b, i) => `${g.critical[i]}>${b}`));
  const W = el.clientWidth;
  // A start node sits just before its first successor, not in column 0, so its edge stays short.
  const vis = new Set(nodes.map((n) => n.id));
  const depthOf = Object.fromEntries(nodes.map((n) => [n.id, n.depth]));
  for (const n of nodes) {
    const hasPred = g.edges.some(([a, b]) => b === n.id && vis.has(a));
    const succ = g.edges.filter(([a, b]) => a === n.id && vis.has(b)).map(([, b]) => depthOf[b]);
    if (!hasPred && succ.length) depthOf[n.id] = Math.max(depthOf[n.id], Math.min(...succ) - 1);
  }
  for (const n of nodes) n.depth = depthOf[n.id]; // idempotent, so re-renders of the same state agree
  const depths = [...new Set(nodes.map((n) => n.depth))].sort((a, b) => a - b);
  // When columns get narrower than a readable node, fall back to the same data as a list.
  if (W < 480 || W / depths.length < 130) {
    const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
    el.innerHTML = `<ul class="rows">${depths.map((d) => nodes.filter((n) => n.depth === d).map((n) => {
      const before = g.edges.filter(([, b]) => b === n.id).map(([a]) => a);
      return `<li><span class="pos">${d + 1}</span><span class="mono">${esc(n.ticket || n.id)}</span>
        <span class="grow ellipsis dim" title="${esc(n.title || "")}">${esc(n.title || "")}${before.length ? ` <span class="muted">· after ${esc(before.map((b) => byId[b]?.ticket || b).join(", "))}</span>` : ""}</span>
        ${crit.has(n.id) ? tag("critical", "accent", null) : ""}${tag(NODE_LABEL[n.status], NODE_TONE[n.status])}</li>`;
    }).join("")).join("")}</ul>`;
    return;
  }
  const col = Object.fromEntries(depths.map((d, i) => [d, i]));
  const colW = W / depths.length, nodeW = Math.min(200, colW - 36), nodeH = 40, gap = 12, pad = 12;
  const cols = depths.map(() => []);
  const pos = {};
  const preds = (id) => g.edges.filter(([, b]) => b === id).map(([a]) => a);
  depths.forEach((d, ci) => {
    const list = nodes.filter((n) => n.depth === d);
    // Barycentre ordering against the previous columns keeps edges from crossing needlessly.
    list.sort((a, b) => {
      const ya = preds(a.id).filter((p) => pos[p]).map((p) => pos[p].y), yb = preds(b.id).filter((p) => pos[p]).map((p) => pos[p].y);
      const ma = ya.length ? ya.reduce((s, v) => s + v, 0) / ya.length : 1e9, mb = yb.length ? yb.reduce((s, v) => s + v, 0) / yb.length : 1e9;
      return ma - mb || (crit.has(b.id) - crit.has(a.id)) || String(a.id).localeCompare(String(b.id));
    });
    list.forEach((n, ri) => { cols[ci].push(n); pos[n.id] = { x: ci * colW + (colW - nodeW) / 2, y: pad + ri * (nodeH + gap) }; });
  });
  const tallest = Math.max(...cols.map((c) => c.length));
  const H = pad * 2 + tallest * (nodeH + gap) - gap + 24; // room for edges that arc under a row
  cols.forEach((c) => { const off = ((tallest - c.length) * (nodeH + gap)) / 2; c.forEach((n) => { pos[n.id].y += off; }); });
  const maxChars = Math.floor((nodeW - 22) / 7.2);
  const cut = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Dependency graph, ${nodes.length} nodes; critical path ${esc((g.critical || []).join(" → "))}">`;
  for (const [a, b] of g.edges) {
    if (!pos[a] || !pos[b]) continue;
    const x1 = pos[a].x + nodeW, y1 = pos[a].y + nodeH / 2, x2 = pos[b].x, y2 = pos[b].y + nodeH / 2, mx = (x1 + x2) / 2;
    const skips = col[depthOf[b]] - col[depthOf[a]] > 1 && Math.abs(y1 - y2) < nodeH;
    // An edge that jumps columns on the same row would run under the nodes between; arc it below them.
    const d = skips ? `M${x1},${y1}C${x1 + 40},${y1 + nodeH} ${x2 - 40},${y2 + nodeH} ${x2},${y2}` : `M${x1},${y1}C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    svg += `<path class="edge ${critEdge.has(`${a}>${b}`) ? "crit" : ""}" d="${d}"/>`;
  }
  for (const n of nodes) {
    const p = pos[n.id], tone = NODE_TONE[n.status];
    svg += `<g class="node st-${n.status} ${crit.has(n.id) ? "crit" : ""} ${tone ? "tone-" + tone : ""}" transform="translate(${p.x},${p.y})">
      <title>${esc(`${n.ticket || n.id} — ${n.title || ""} (${NODE_LABEL[n.status]})`)}</title>
      <rect class="box" width="${nodeW}" height="${nodeH}" rx="6" ${n.status === "waiting" ? 'stroke-dasharray="4 3"' : ""}/>
      <rect class="bar" x="0" y="0" width="3" height="${nodeH}" rx="1.5"/>
      <text x="12" y="17">${esc(cut(n.ticket || n.id, maxChars))}</text>
      <text class="sub" x="12" y="31">${esc(cut(`${NODE_LABEL[n.status]}${n.title && n.title !== n.ticket ? " · " + n.title : ""}`, maxChars + 4))}</text></g>`;
  }
  el.innerHTML = svg + "</svg>";
}

// ------------------------------------------------------------ views

function nextTone(n) {
  if (/^(SAFETY|STOP)/.test(n)) return "bad";
  if (/^stop spawning/.test(n)) return "warn";
  if (/predicate met|Close/.test(n)) return "good";
  return "accent";
}

function attentionItems(st) {
  const s = st.summary || {}, out = [];
  const workers = Object.fromEntries((st.workers || []).map((w) => [w.dispatch, w]));
  if (s.main === "red") out.push(["bad", "fail", "main is red — only the repairing PR may land (prog.py land --class main-fix).", "prs"]);
  if (s.stopped) out.push(["bad", "stop", "STOP line is active — nothing new is spawned.", "activity"]);
  for (const e of st.land_order || []) {
    if ((Date.now() - ms(e.since)) / 3600e3 > 3 && e.state !== "gone") out.push(["bad", "clock", `#${e.pr}${e.ticket ? ` (${e.ticket})` : ""} has waited ${ageText(e.since)} to land — ${e.reasons?.[0] || e.state}`, "overview"]);
  }
  if (s.human_wait) out.push(["warn", "user", `${s.human_wait} PR${s.human_wait > 1 ? "s" : ""} waiting for your go: ${(s.human_wait_prs || []).map((n) => "#" + n).join(", ")}`, "prs"]);
  for (const d of s.idle_waiting || []) {
    const w = workers[d] || {};
    out.push(["warn", "clock", `Worker ${d}${w.ticket ? ` (${w.ticket})` : ""} is waiting — maybe a permission prompt in its terminal.`, "workers"]);
  }
  if ((s.untriaged || []).length) out.push(["accent", "issues", `Untriaged follow-up: ${s.untriaged.join(", ")} — admit or park.`, "issues"]);
  const risk = (st.notes || []).find((n) => n.kind === "risk" && Date.now() - ms(n.ts) < 24 * 3600e3);
  if (risk) out.push(["warn", "note", risk.text, "activity"]);
  for (const e of st.errors || []) out.push(["warn", "alert", `Source error — ${e} (showing last good data)`, null]);
  const slug = encodeURIComponent(st.slug);
  return out.map(([tone, ic, text, sec]) => `<li class="tone-${tone}"><span class="ic">${icon(ic)}</span><span class="grow">${esc(text)}</span>
    ${sec && sec !== "overview" ? `<a class="go" href="#/${slug}/${sec}">${esc(SECTIONS.find((x) => x.id === sec).label)} ${icon("arrow")}</a>` : ""}</li>`);
}

function kpi(label, value, sub, { extra = "", src = "" } = {}) {
  return `<div class="card kpi" ${src ? `data-src="${src}"` : ""}><div class="eyebrow">${label}</div><div class="v">${value}</div>${extra}<div class="sub">${sub}</div></div>`;
}

function landOrderCard(st) {
  const order = st.land_order || [];
  const lanes = Object.entries(st.landing || {}).filter(([, l]) => l.holder);
  const holders = lanes.length ? lanes.map(([base, l]) => `<div class="holder">${icon("lock")}<span class="mono">#${esc(l.holder.pr)}</span>
      <span class="mono dim">${esc(l.holder.ticket || "")}</span><span class="grow"></span><span class="muted">${esc(base)} · 독점 레인 ${relSpan(l.holder.since)}</span></div>`).join("")
    : `<div class="holder idle">${icon("lock")}<span>exclusive lane free</span></div>`;
  const prUrl = (n) => (st.prs || []).find((p) => p.number === n)?.url;
  const rows = order.map((e, i) => `<li>
      <span class="pos">${i + 1}</span>${link(prUrl(e.pr), `<span class="mono">#${esc(e.pr)}</span>`)}
      <span class="mono dim">${esc(e.ticket || "—")}</span>${ageTag(e.since)}
      ${tag(String(e.state || "?").replace("_", " "), ORDER_TONE[e.state] ?? "")}
      ${e.klass && e.klass !== "normal" ? tag(e.klass, e.klass === "main-fix" ? "bad" : "warn", null) : ""}
      ${e.exclusive ? tag("exclusive", "accent", "lock") : ""}
      <span class="grow ellipsis muted" title="${esc((e.reasons || []).join(" · "))}">${esc(e.reasons?.[0] || (e.unblocks ? `unblocks ${e.unblocks}` : ""))}</span>
    </li>`).join("");
  return `<div class="card" data-src="ledger github"><div class="card-head"><h3>Land order</h3><span class="aside">${order.length} waiting · parallel, one exclusive per base</span></div>
    ${holders}<ul class="rows">${rows || '<li class="muted">Nothing is waiting to land.</li>'}</ul></div>`;
}

function viewOverview(st) {
  const s = st.summary || {};
  const nt = nextTone(s.next || "");
  const pct = s.predicate_total && s.predicate_done != null ? Math.round((100 * s.predicate_done) / s.predicate_total) : null;
  const segs = `<div class="segs" aria-hidden="true">${(st.predicate || []).map((p) => `<i class="${p.state_type === "completed" ? "done" : p.state_type === "started" ? "started" : ""}"></i>`).join("")}</div>`;
  const cells = `<div class="cells" aria-hidden="true">${Array.from({ length: s.ceiling || 0 }, (_, i) => `<i class="${i < s.in_flight ? "on" : ""} ${i < s.cap ? "cap" : ""}"></i>`).join("")}</div>`;
  const mainEv = (st.activity || []).find((a) => a.kind === "main_green" || a.kind === "main_red");
  const mt = mainTone(s.main);
  const oldest = s.oldest_open_pr;
  const budget = s.budget_used != null ? `<div class="budget"><div style="display:flex;justify-content:space-between"><span>Budget</span><span>${Math.round(s.budget_used * 100)}% used</span></div>
      <div class="meter ${s.budget_used >= 0.7 ? "tone-warn" : ""}"><i style="width:${Math.min(100, Math.round(s.budget_used * 100))}%"></i></div>
      <span>Deadline ${relSpan(st.deadline)}</span></div>` : "<span></span>";
  const att = attentionItems(st);
  return `
  <div class="card next tone-${nt}">
    <span class="badge-ic">${icon(nt === "bad" ? "alert" : nt === "good" ? "flag" : "arrow")}</span>
    <div><div class="eyebrow">Next move</div><div class="text">${esc(s.next || "—")}</div></div>
    ${budget}
  </div>
  <div class="kpis">
    ${kpi("Predicate", pct == null ? "—" : `${pct}%<small>${s.predicate_done}/${s.predicate_total}</small>`, pct == null ? "no tracker data" : s.final_check ? "final check recorded" : "final check not recorded", { extra: segs, src: "tracker" })}
    ${kpi("Main CI", tag(s.main || "—", mt, mt === "good" ? "check" : mt === "bad" ? "fail" : "clock"), mainEv ? `${mainEv.kind === "main_red" ? "red" : "green"} ${relSpan(mainEv.ts)}` : "no landing yet", { src: "ledger" })}
    ${kpi("In-flight / cap", `${num(s.in_flight)}<small>/ ${num(s.cap)}</small>`, `ceiling ${num(s.ceiling)}`, { extra: cells, src: "orca" })}
    ${kpi("Oldest open PR", oldest ? `<span class="age-v ${ageTone(oldest.since) ? "tone-" + ageTone(oldest.since) : ""}" data-age-text="${esc(oldest.since)}">${ageText(oldest.since)}</span>` : "—", oldest ? `#${oldest.pr} · opened ${relSpan(oldest.since)}` : "no open PRs", { src: "github" })}
    ${kpi("Ready to land", num(s.ready_to_land), (s.ready_prs || []).map((n) => "#" + n).join(" ") || "queue empty", { src: "ledger" })}
    ${kpi("Human wait", num(s.human_wait), st.merge_policy === "human-gate" ? ((s.human_wait_prs || []).map((n) => "#" + n).join(" ") || "nothing waiting") : "autonomous policy", { src: "ledger" })}
    ${kpi("Landed · 24h", num(s.landed_24h), `${num(s.landed_total)} total`, { src: "ledger" })}
    ${kpi("Derived / item", num(s.derived_per_item, 1), s.derived_total == null ? "no tracker data" : `${s.derived_total} derived · ${s.admitted} admitted · ${s.parked} parked`, { src: "tracker" })}
  </div>
  <div class="grid">
    ${landOrderCard(st)}
    <div class="card"><div class="card-head"><h3>Needs attention</h3><span class="aside">${att.length || ""}</span></div>
      ${att.length ? `<ul class="rows">${att.join("")}</ul>` : `<div class="empty">${icon("check")}Nothing needs a human right now.</div>`}</div>
  </div>
  <div class="grid charts">
    <div class="card wide" data-src="tracker"><div class="card-head"><h3>Burn-up</h3><span class="aside legend"><span><i class="key" style="background:var(--ink-3)"></i>Scope</span><span><i class="key" style="background:var(--accent)"></i>Done</span></span></div>
      <div class="card-body"><div class="chart" id="chart-burn"></div></div></div>
    <div class="card" data-src="orca"><div class="card-head"><h3>In-flight vs cap</h3><span class="aside legend"><span><i class="key" style="background:var(--ink-3)"></i>Cap</span><span><i class="key" style="background:var(--accent)"></i>In-flight</span></span></div>
      <div class="card-body"><div class="chart" id="chart-flow"></div></div></div>
    <div class="card" data-src="tracker"><div class="card-head"><h3>Derived vs done · per 6h</h3><span class="aside legend"><span><i class="key sq" style="background:var(--derived)"></i>Derived</span><span><i class="key sq" style="background:var(--accent)"></i>Done</span></span></div>
      <div class="card-body"><div class="chart" id="chart-growth"></div></div></div>
  </div>
  <div class="grid">
    <div class="card" data-src="tracker"><div class="card-head"><h3>Predicate</h3><span class="aside">${s.predicate_done ?? "–"} of ${s.predicate_total} done</span></div>
      <ul class="rows">${(st.predicate || []).map((p) => `<li>
        <span class="ic ${p.state_type === "completed" ? "tone-good" : p.state_type === "started" ? "tone-accent" : ""}">${icon(p.state_type === "completed" ? "check" : p.state_type === "started" ? "clock" : "issues")}</span>
        <span class="mono" style="width:78px;flex:none">${link(p.url, esc(p.id))}</span><span class="grow ellipsis" title="${esc(p.title)}">${esc(p.title || "")}</span>
        <span class="hide-sm">${tag(p.state || "unknown", STATE_TONE[p.state_type] ?? "")}</span></li>`).join("") || `<li class="muted">No predicate set.</li>`}</ul></div>
    <div class="card" data-src="ledger"><div class="card-head"><h3>Recent activity</h3><span class="aside"><a class="link" href="#/${encodeURIComponent(st.slug)}/activity">All</a></span></div>
      <ul class="rows feed">${(st.activity || []).slice(0, 8).map(evRow).join("") || `<li class="muted">No events yet.</li>`}</ul></div>
  </div>`;
}

function chips(group, options, current) {
  return `<div class="chips" role="group" aria-label="Filter">${options.map(([id, label, n]) =>
    `<button class="chip" type="button" data-group="${group}" data-val="${id}" aria-pressed="${id === current}">${esc(label)}${n != null ? `<span class="n">${n}</span>` : ""}</button>`).join("")}</div>`;
}

const ISSUE_FILTERS = {
  all: [() => true, "All"],
  predicate: [(i) => i.in_predicate, "Predicate"],
  open: [(i) => !["completed", "canceled"].includes(i.state_type), "Open"],
  derived: [(i) => i.derived, "Derived"],
  untriaged: [(i) => i.derived && !i.triage, "Untriaged"],
  admitted: [(i) => i.triage === "admitted", "Admitted"],
  parked: [(i) => i.triage === "parked", "Parked"],
  done: [(i) => i.state_type === "completed", "Done"],
};

function viewIssues(st) {
  const issues = st.issues;
  if (!issues) return `<div class="view-head"><div><h2>Issues</h2><p>No tracker data — set a tracker project in program.json.</p></div></div>`;
  const f = ISSUE_FILTERS[S.filters.issues] ? S.filters.issues : "all";
  const q = S.q.trim().toLowerCase();
  const rows = issues.filter(ISSUE_FILTERS[f][0]).filter((i) => !q || `${i.id} ${i.title} ${(i.labels || []).join(" ")}`.toLowerCase().includes(q));
  const opts = Object.entries(ISSUE_FILTERS).map(([id, [fn, label]]) => [id, label, issues.filter(fn).length]);
  return `<div class="view-head"><div><h2>Issues</h2><p>Predicate items, admitted and derived tickets this program touched.</p></div></div>
  <div class="toolbar">${chips("issues", opts, f)}<input class="search" id="issue-search" type="search" placeholder="Filter by ID or title" value="${esc(S.q)}" aria-label="Filter issues"></div>
  <div class="card table-wrap" data-src="tracker"><table><thead><tr><th>ID</th><th>Title</th><th>State</th><th>Flags</th><th class="hide-md">Updated</th><th class="hide-md">Assignee</th></tr></thead><tbody>
  ${rows.map((i) => `<tr><td class="mono">${link(i.url, esc(i.id))}</td><td class="title"><span class="ellipsis" style="display:block" title="${esc(i.title)}">${esc(i.title)}</span></td>
    <td>${tag(i.state || i.state_type || "?", STATE_TONE[i.state_type] ?? "")}</td>
    <td><span class="flags">${i.in_predicate ? tag("predicate", "accent", null) : ""}${i.derived ? tag("derived", "derived") : ""}${i.triage ? tag(i.triage, i.triage === "admitted" ? "good" : "", null) : i.derived ? tag("untriaged", "warn", null) : ""}</span></td>
    <td class="hide-md muted">${relSpan(i.updated_at)}</td><td class="hide-md dim">${esc(i.assignee || "—")}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No issues match.</td></tr>`}
  </tbody></table></div>`;
}

function viewPrs(st) {
  const prs = st.prs || [];
  const F = { open: (p) => p.state === "open", merged: (p) => p.state === "merged", closed: (p) => p.state === "closed", all: () => true };
  const f = F[S.filters.prs] ? S.filters.prs : "open";
  const cell = (map, key) => { const [t, tone, ic] = map[key] || [key, "", null]; return tag(t, tone, ic || "dot"); };
  const pos = Object.fromEntries((st.land_order || []).map((e, i) => [e.pr, i + 1]));
  return `<div class="view-head"><div><h2>Pull requests</h2><p>Open PRs plus everything opened in the program window, with CI, the recorded verdict and review rounds.</p></div></div>
  <div class="toolbar">${chips("prs", [["open", "Open", prs.filter(F.open).length], ["merged", "Merged", prs.filter(F.merged).length], ["closed", "Closed", prs.filter(F.closed).length], ["all", "All", prs.length]], f)}</div>
  <div class="card table-wrap" data-src="github"><table><thead><tr><th>PR</th><th>Title</th><th>Ticket</th><th>CI</th><th>Verdict</th><th class="num hide-md">Rounds</th><th class="hide-md">State</th><th>Age</th></tr></thead><tbody>
  ${prs.filter(F[f]).map((p) => {
    const issue = (st.issues || []).find((i) => i.id === p.ticket);
    const state = p.state === "merged" ? tag("merged", "accent", "merge") : p.state === "open" ? tag(p.draft ? "draft" : pos[p.number] ? `land #${pos[p.number]}` : "open", p.draft ? "" : "good") : tag(p.state, "");
    return `<tr><td class="mono">${link(p.url, "#" + esc(p.number))}</td><td class="title"><span class="ellipsis" style="display:block" title="${esc(p.title)}">${esc(p.title)}</span></td>
    <td class="mono">${p.ticket ? link(issue?.url, esc(p.ticket)) : '<span class="muted">—</span>'}</td>
    <td>${cell(CI, p.ci)}</td><td>${cell(VERDICT, p.verdict)}</td><td class="num hide-md">${esc(p.review_rounds ?? 0)}</td>
    <td class="hide-md">${state}</td><td>${p.state === "open" ? ageTag(p.created_at) : `<span class="muted">${relSpan(p.merged_at || p.created_at)}</span>`}</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="muted">No pull requests here.</td></tr>`}
  </tbody></table></div>`;
}

function viewTasks(st) {
  const tasks = st.tasks || [], g = st.graph || { nodes: [] };
  const counts = {};
  for (const n of g.nodes) counts[n.status] = (counts[n.status] || 0) + 1;
  const title = Object.fromEntries(tasks.map((t) => [t.id, t.ticket || t.title]));
  const TT = { completed: "good", dispatched: "accent", blocked: "bad", ready: "warn" };
  return `<div class="view-head"><div><h2>Tasks</h2><p>Orca tasks and ledger dependencies, left to right by depth. The accent chain is the critical path: the longest run of unfinished work.</p></div></div>
  <div class="toolbar"><div class="chips">${Object.keys(NODE_LABEL).filter((k) => counts[k]).map((k) => tag(`${NODE_LABEL[k]} ${counts[k]}`, NODE_TONE[k])).join(" ")}</div>
    <button class="chip" type="button" id="show-done" aria-pressed="${S.showDone}">Show done</button></div>
  <div class="card" data-src="orca ledger"><div class="card-head"><h3>Dependency graph</h3><span class="aside">${(g.critical || []).length ? `critical path ${esc(g.critical.join(" → "))}` : ""}</span></div>
    <div class="card-body dag" id="dag"></div></div>
  <div class="card table-wrap" data-src="orca"><table><thead><tr><th>Task</th><th>Status</th><th class="hide-md">After</th><th class="hide-md">Dispatch</th><th>Created</th><th>Done</th></tr></thead><tbody>
  ${tasks.map((t) => `<tr><td class="title"><span class="mono">${esc(t.ticket || "")}</span> <span class="dim">${t.title !== t.ticket ? esc(t.title) : ""}</span></td>
    <td>${tag(t.status || "?", TT[t.status] ?? "")}</td><td class="hide-md muted">${esc(t.deps.map((d) => title[d] || d).join(", ") || "—")}</td>
    <td class="hide-md mono muted">${esc(t.dispatch || "—")}</td><td class="muted">${relSpan(orcaTime(t.created_at))}</td><td class="muted">${t.completed_at ? relSpan(orcaTime(t.completed_at)) : "—"}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No Orca tasks on this run.</td></tr>`}
  </tbody></table></div>`;
}
// Orca task timestamps are "YYYY-MM-DD HH:MM:SS" in UTC.
const orcaTime = (s) => (s && !/[zZ+]/.test(s.slice(10)) ? s.replace(" ", "T") + "Z" : s);

function viewWorkers(st) {
  const ws = st.workers || [];
  const F = { active: (w) => w.outcome === "in_progress", all: () => true };
  const f = F[S.filters.workers] ? S.filters.workers : "active";
  const u = st.usage?.total;
  const trend = (st.series || []).map((r) => r.tokens).filter((v) => v != null);
  const act = (w) => w.outcome !== "in_progress" ? `<span class="muted">${esc(w.outcome || "—")}</span>`
    : w.activity === "running" ? `<span style="display:inline-flex;align-items:center;gap:6px"><span class="pulse"></span>running</span>`
    : w.activity === "waiting" ? tag("waiting", "warn", "clock") : `<span class="muted">idle</span>`;
  const tok = (w) => w.tokens ? `<span title="in ${compact(w.tokens.input_tokens)} · out ${compact(w.tokens.output_tokens)} · cache write ${compact(w.tokens.cache_creation_input_tokens)} · cache read ${compact(w.tokens.cache_read_input_tokens)}">${compact(w.tokens.total)}</span>`
    : `<span class="muted">${/^(gpt|o3|o4|codex)/.test(w.model || "") ? "n/a" : "—"}</span>`;
  return `<div class="view-head"><div><h2>Workers</h2><p>Orca dispatches on run <span class="mono">${esc(st.run)}</span>. A worker stuck on “waiting” may be sitting on a permission prompt.</p></div></div>
  <div class="card" data-src="orca"><div class="card-head"><h3>Tokens</h3><span class="aside">from Claude Code transcripts · Codex workers n/a</span></div>
    <div class="card-body" style="display:flex;flex-wrap:wrap;align-items:center;gap:24px">
      <div><div class="eyebrow">Program total</div><div style="font-size:var(--t-2xl);font-weight:600">${compact(u?.total)}</div></div>
      ${u ? [["input", u.input_tokens], ["output", u.output_tokens], ["cache write", u.cache_creation_input_tokens], ["cache read", u.cache_read_input_tokens]].map(([k, v]) => `<div><div class="eyebrow">${k}</div><div>${compact(v)}</div></div>`).join("") : '<span class="muted">No transcripts found for these workers.</span>'}
      <span style="margin-left:auto">${sparkline(trend.slice(-60), cssVar("--accent"), 160, 32)}</span></div></div>
  <div class="toolbar">${chips("workers", [["active", "In flight", ws.filter(F.active).length], ["all", "All", ws.length]], f)}</div>
  <div class="card table-wrap" data-src="orca"><table><thead><tr><th>Dispatch</th><th>Ticket</th><th>Activity</th><th class="hide-md">Liveness</th><th class="hide-md">Model</th><th class="num">Tokens</th><th>Since</th></tr></thead><tbody>
  ${ws.filter(F[f]).map((w) => `<tr><td class="mono">${esc(w.dispatch)}</td><td class="mono">${esc(w.ticket || w.worktree || "—")}</td><td>${act(w)}</td>
    <td class="hide-md">${tag(w.liveness || "—", w.liveness === "live" ? "good" : "")}</td>
    <td class="hide-md dim">${esc(w.model || "—")}</td><td class="num">${tok(w)}</td><td class="muted">${relSpan(w.since)}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No workers ${f === "active" ? "in flight" : "yet"}.</td></tr>`}
  </tbody></table></div>`;
}

const EV_STYLE = {
  landed: ["accent", "merge"], main_green: ["good", "check"], main_red: ["bad", "fail"], land_failed: ["bad", "fail"],
  ready: ["", "prs"], spawned: ["", "spark"], admitted: ["good", "arrow"], parked: ["", "park"], approved: ["good", "user"],
  stop: ["bad", "stop"], resume: ["good", "arrow"], predicate_verified: ["good", "flag"], config: ["", "gear"],
  lock_acquired: ["accent", "lock"], lock_released: ["", "lock"], dep: ["", "link"], land_check: ["", "check"],
  yield: ["warn", "yield"], reprioritized: ["warn", "sort"],
};

function evRow(a) {
  let tone, ic, cls = "ev";
  if (a.kind === "note") { tone = { risk: "warn", decision: "accent" }[a.note_kind] ?? ""; ic = "note"; cls += " note"; }
  else if (a.kind === "verdict") { tone = /^verdict pass/.test(a.text) ? "good" : "bad"; ic = tone === "good" ? "check" : "fail"; }
  else [tone, ic] = EV_STYLE[a.kind] || ["", "gear"];
  const by = a.kind === "note" ? `<span class="by">${esc(a.note_kind || "note")}${a.author ? " · " + esc(a.author) : ""}</span>` : "";
  return `<li class="${cls} ${tone ? "tone-" + tone : ""}"><span class="tm" title="${esc(new Date(a.ts).toLocaleString())}">${hhmm(ms(a.ts))}</span>
    <span class="ic">${icon(ic)}</span><span class="tx">${esc(a.text)}${by}</span></li>`;
}

function viewActivity(st) {
  const all = st.activity || [];
  const F = { all: () => true, ledger: (a) => a.kind !== "note", notes: (a) => a.kind === "note" };
  const f = F[S.filters.activity] ? S.filters.activity : "all";
  let day = null, html = "";
  for (const a of all.filter(F[f])) {
    const d = dayLabel(ms(a.ts));
    if (d !== day) { html += `<li class="day eyebrow">${esc(d)}</li>`; day = d; }
    html += evRow(a);
  }
  return `<div class="view-head"><div><h2>Activity</h2><p>Ledger events as prog.py records them, and notes added with <span class="mono">dash.py note</span>.</p></div></div>
  <div class="toolbar">${chips("activity", [["all", "All", all.length], ["ledger", "Ledger", all.filter(F.ledger).length], ["notes", "Notes", all.filter(F.notes).length]], f)}</div>
  <div class="card" data-src="ledger"><ul class="rows feed">${html || '<li class="muted">Nothing recorded yet.</li>'}</ul></div>`;
}

const VIEWS = { overview: viewOverview, issues: viewIssues, prs: viewPrs, tasks: viewTasks, workers: viewWorkers, activity: viewActivity };

function renderView() {
  const view = $("#view"), st = S.state;
  const focusId = document.activeElement?.id, sel = document.activeElement?.selectionStart;
  if (!S.slug) view.innerHTML = `<div class="loading">${S.programs.length ? "Choose a program." : `No programs under the store yet. Start one with <span class="mono">prog.py init</span>.`}</div>`;
  else if (!st) view.innerHTML = `<div class="loading">${S.down ? "Cannot reach the dashboard server." : "Loading…"}</div>`;
  else { view.innerHTML = VIEWS[S.section](st); redrawCharts(); applyFreshness(); }
  if (focusId && $("#" + focusId)) { const el = $("#" + focusId); el.focus(); if (sel != null && el.setSelectionRange) el.setSelectionRange(sel, sel); }
  document.title = st ? `${st.slug} · ${SECTIONS.find((s) => s.id === S.section).label}` : "Program Dashboard";
}

function render() { renderSidebar(); renderHeader(); renderFresh(); renderView(); setLive(); }

// ------------------------------------------------------------ data

async function pollPrograms() {
  try {
    const r = await fetch("/api/programs", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    S.programs = await r.json();
    if (!S.slug && S.programs.length) { location.replace(`#/${encodeURIComponent(S.programs[0].slug)}/${S.section}`); return; }
    renderSidebar();
  } catch (e) { S.down = true; setLive(); }
}

async function pollState() {
  const slug = S.slug;
  if (!slug || document.hidden) return;
  try {
    const r = await fetch(`/api/${encodeURIComponent(slug)}/state`, { cache: "no-store", headers: S.etag ? { "If-None-Match": S.etag } : {} });
    if (slug !== S.slug) return;
    S.down = false; S.lastOk = Date.now();
    if (r.status === 304) { setLive(); return; }
    if (!r.ok) throw new Error(String(r.status));
    S.etag = r.headers.get("ETag");
    S.state = await r.json();
    render();
  } catch (e) { S.down = true; setLive(); if (!S.state) renderView(); }
}

function route() {
  const m = location.hash.match(/^#\/([^/]*)(?:\/([a-z]+))?/);
  const slug = m && m[1] ? decodeURIComponent(m[1]) : null;
  const section = m && VIEWS[m[2]] ? m[2] : "overview";
  const changed = slug !== S.slug;
  S.slug = slug; S.section = section;
  if (changed) { S.state = null; S.etag = null; S.q = ""; }
  render();
  if (changed) pollState();
  document.querySelector(".main").scrollTo(0, 0);
}

function tick() {
  $$("[data-rel]").forEach((el) => { el.textContent = rel(el.dataset.rel); });
  $$("[data-age]").forEach((el) => {
    el.lastElementChild.textContent = ageText(el.dataset.age);
    const tone = ageTone(el.dataset.age);
    el.className = `tag ${tone ? "tone-" + tone : ""}`;
  });
  $$("[data-age-text]").forEach((el) => {
    el.textContent = ageText(el.dataset.ageText);
    const tone = ageTone(el.dataset.ageText);
    el.className = `age-v ${tone ? "tone-" + tone : ""}`;
  });
  applyFreshness(); setLive();
}

// ------------------------------------------------------------ boot

initChrome();
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip[data-group]");
  if (chip) { S.filters[chip.dataset.group] = chip.dataset.val; renderView(); return; }
  if (e.target.closest("#show-done")) { S.showDone = !S.showDone; renderView(); }
});
document.addEventListener("input", (e) => { if (e.target.id === "issue-search") { S.q = e.target.value; renderView(); } });
addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollState(); });
setInterval(pollState, POLL_MS);
setInterval(pollPrograms, PROGRAMS_MS);
setInterval(tick, 1000);
route();
pollPrograms();
