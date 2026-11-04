"use strict";
// Fleet views: every Orca session on this machine as orchestrator -> orchestrations -> tasks, plus standalone sessions.
// Loaded before app.js and only called after it, so it may use app.js helpers ($, esc, icon, tag, relSpan, kpi, S...).
// Everything comes from /api/fleet/state, written by fleet.py.

// Three groups, a rule before each `sep`: the work in flight, what runs unattended, how the skills and PRs fare.
const FLEET_SECTIONS = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "sessions", label: "Sessions", icon: "workers" },
  { id: "prs", label: "Pull requests", icon: "prs" },
  { id: "brief", label: "Brief", icon: "note" },
  { id: "stages", label: "Stages", icon: "flag" },
  { id: "todo", label: "To-do", icon: "check" },
  { id: "sent", label: "Sent", icon: "chat" },
  { id: "timeline", label: "Timeline", icon: "list" },
  { id: "automations", label: "Automations", icon: "clock", sep: true },
  { id: "monitors", label: "Monitors", icon: "activity" },
  { id: "skills", label: "Skills", icon: "spark", sep: true },
  { id: "changes", label: "Skill changes", icon: "merge" },
  { id: "tokens", label: "Tokens", icon: "coins" },
  { id: "stats", label: "PR stats", icon: "chart" },
  { id: "graph", label: "Graph", icon: "graph" },
  { id: "flow", label: "Data flow", icon: "link" },
];

// Freshness thresholds in seconds, [warn, stale], in the shape app.js SOURCE_META uses.
const FLEET_SOURCES = [
  ["orca", "Orca", () => [30, 90]],
  ["runs", "Runs", () => [300, 900]],
  ["github", "GitHub", () => [600, 1800]],
  ["automations", "Automations", () => [300, 900]],
  ["monitors", "Monitors", () => [300, 900]],
  ["sent", "Sent", () => [300, 900]],
];

// type -> [label, tone, icon]
const ITEM = {
  prompt: ["Pending confirmation", "warn", "lock"],
  permission: ["Waiting on a prompt", "warn", "lock"],
  approval: ["Approval", "warn", "user"],
  question: ["Question", "accent", "chat"],
  decision: ["Decision", "warn", "flag"],
  run_command: ["Run a command", "accent", "spark"],
  login: ["Login", "warn", "user"],
  verify_failed: ["Verify failed", "bad", "fail"],
  verify_ok: ["Check the result", "good", "check"],
  changes_requested: ["Changes requested", "bad", "prs"],
  review_comment_received: ["Review comments", "accent", "note"],
  ci_failed: ["CI failed", "bad", "fail"],
  approval_stale: ["Approval on old commit", "warn", "clock"],
  ready_to_merge: ["Ready to merge", "good", "merge"],
  sync_stalled: ["Skill sync stalled", "bad", "alert"],
  reload_pending: ["Reload not sent", "warn", "alert"],
  selfcheck: ["Dashboard self-check", "bad", "alert"],
  watcher_down: ["Card watch down", "bad", "alert"],
  patrol: ["Patrol finding", "warn", "alert"],
};
const itemMeta = (t) => ITEM[t] || [t, "", "dot"];
// What a session's kind reads as on screen -> [label, tone, icon]. kind itself stays as fleet.py wrote it (sort, adopt).
// A lead is a session with a Run of its own under the root; a standalone it made with `orca worktree create` is its worker.
function kindLabel(st, s) {
  if (s.kind === "orchestrator") return ["orchestrator", "accent", ""];
  if (s.kind === "orchestration") return ["lead", "accent", "workers"];
  if (s.kind === "task" || (byId(st)[s.parent] || {}).kind === "orchestration") return ["worker", "", ""];
  return [s.kind, "", ""];
}
const kindTag = (st, s) => tag(...kindLabel(st, s));

// phase -> what its dot means; the colour is .ph-<phase> in style.css, the same on every screen.
const PHASE = { working: "working", waiting: "waiting on you", idle: "idle, turn ended", open: "open, no agent", offline: "offline" };
// Reviewer edge status -> [css class, label]
const EDGE = {
  approved: ["tone-good", "approved"], approved_stale: ["tone-warn", "approved, old commit"], changes_requested: ["tone-bad", "changes requested"],
  commented: ["tone-accent", "commented"], requested: ["req", "review requested"], none: ["", "—"],
};
const CI_TONE = { success: "good", failure: "bad", pending: "warn" };

const F = { token: null, draft: {}, confirm: null, chat: {}, arm: null, prompt: {}, decide: {}, open: null, tlShow: {}, tlOpen: new Set(), stageFold: new Set(), jobOpen: new Set() };

// route() hands over "#/fleet/<section>[/<arg>]"; the only argument is a session id.
// The Work page's stage table is the Stages page now: an old #/fleet/work link opens it.
const MOVED = { work: "stages" };
function fleetSection(section, arg) {
  F.sessionId = section === "session" && arg ? arg : null;
  section = MOVED[section] || section;
  return F.sessionId ? "session" : FLEET_SECTIONS.some((s) => s.id === section) ? section : "overview";
}
const BY_ID = new WeakMap();  // one map per state, not one per rendered row
const byId = (st) => BY_ID.get(st) || BY_ID.set(st, Object.fromEntries((st.sessions || []).map((s) => [s.id, s]))).get(st);
// A tick that changed only timestamps needs the freshness bar redrawn, not the whole view (hover and scroll survive).
const fleetBody = (st) => JSON.stringify({ ...st, generated_at: 0, sources: 0 });
const sessionHref = (id) => `#/fleet/session/${encodeURIComponent(id)}`;
const phaseDot = (s) => `<span class="${s.phase === "working" ? "pulse" : "dot-s"} ph-${esc(s.phase)}" title="${esc(PHASE[s.phase] || s.phase)}"></span>`;
const phaseKeys = (only = Object.keys(PHASE), n = null) => only.map((p) => `<span>${phaseDot({ phase: p })}${n ? `${n(p)} ` : ""}${PHASE[p]}</span>`).join("");
const phaseLegend = (...a) => `<div class="legend">${phaseKeys(...a)}</div>`;
const badge = (n, missed) => n ? `<span class="badge ${missed ? "missed" : ""}" title="${n} waiting on you${missed ? `, ${missed} missed` : ""}">${n}</span>` : "";

async function fleetPost(path, body, retry = true) {
  if (!F.token) F.token = (await (await fetch("/api/fleet/token", { cache: "no-store" })).json()).token;
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Dash-Token": F.token }, body: JSON.stringify(body) });
  // A server restart (ensure restarts on a code change) rotates the token: fetch it again once.
  if (r.status === 403 && retry) { F.token = null; return fleetPost(path, body, false); }
  const out = await r.json().catch(() => ({}));
  // Every POST is an action that wakes the collector: fetch its result once that tick is written, not at the next poll.
  setTimeout(pollState, 1500);
  if (!r.ok && !("ok" in out)) throw new Error(out.error || String(r.status));
  return out;
}

// Depth-first order: root, then its children, so the list reads as a tree.
function sessionTree(st) {
  const all = st.sessions || [], kids = {};
  all.forEach((s) => (kids[s.parent || ""] ||= []).push(s));
  const rank = { orchestrator: 0, orchestration: 1, task: 2, standalone: 3 };
  const order = (a, b) => rank[a.kind] - rank[b.kind] || (b.last_activity || "").localeCompare(a.last_activity || "");
  const out = [], seen = new Set();
  const walk = (id, depth) => (kids[id] || []).sort(order).forEach((s) => {
    if (seen.has(s.id)) return;  // a parent cycle (Orca parents are user-editable) must not hang the page
    seen.add(s.id); out.push([s, depth]); walk(s.id, depth + 1);
  });
  walk("", 0);
  all.filter((s) => !seen.has(s.id)).forEach((s) => out.push([s, 0]));  // a parent that vanished from Orca
  return out;
}

// ------------------------------------------------------------ chrome

function fleetSidebar() {
  const st = S.state;
  const counts = st ? { inbox: (st.items || []).length || null, sessions: (st.sessions || []).length,
    prs: (st.prs || []).filter((p) => p.state === "OPEN").length || null, stages: stageOpen() || null, todo: todoCount() || null, automations: autoTroubled(st).length || null, monitors: monTroubled(st).length || null, graph: (st.prs || []).filter((p) => p.session).length || null } : {};
  $("#section-list").innerHTML = FLEET_SECTIONS.map((s) => `${s.sep ? '<li class="sb-sep" aria-hidden="true"></li>' : ""}<li><a class="nav-item" href="#/fleet/${s.id}"
      ${s.id === S.section ? 'aria-current="page"' : ""} title="${s.label}">${icon(s.icon)}<span class="sb-text">${s.label}</span>
      ${counts[s.id] != null ? `<span class="count">${counts[s.id]}</span>` : ""}</a></li>`).join("");
}

// The sidebar's session tree. The orchestrator sits on top with the programs that have no lead on screen. Everything
// else hangs off it as units: a session the orchestrator runs, with its program and the cards under it, so a lead keeps
// its children whatever their own phase. Its most urgent member puts a unit in a group: needs you (a badge or a
// prompt), running, idle (folded), offline (folded). A program that is closed or ARCHIVE_MS quiet with no orchestrator or lead
// on screen folds into Archived. st is null when the fleet collector is off.
const ARCHIVE_MS = 3 * 86400e3;
const FOLDS_KEY = "orch-dash:sb-folds";
// id -> [label, open by default, phase whose colour marks its chevron, what it holds]
const FOLDS = {
  needs: ["Needs you", true, "waiting", "Sessions with an inbox item, or waiting on a prompt"],
  running: ["Running", true, "working", "Sessions whose agent is working"],
  idle: ["Idle", false, "idle", "Turn ended, or a terminal with no agent"],
  offline: ["Offline", false, "offline", "Leads with no live terminal"],
  archived: ["Archived", false, "", "Closed programs, and programs quiet for 3 days with no orchestrator or lead on screen"],
};
const folds = () => { try { return JSON.parse(store.get(FOLDS_KEY)) || {}; } catch (e) { return {}; } };
const urgency = (s) => s.unread || s.phase === "waiting" ? 0 : s.phase === "working" ? 1 : s.phase === "idle" || s.phase === "open" ? 2 : 3;
const FOLD_OF = ["needs", "running", "idle", "offline"];

function fold(id, n, body) {
  const [label, def, phase, title] = FOLDS[id], open = folds()[id] ?? (def || body.includes('aria-current="page"'));
  // data-was is what this render drew, so the toggle a re-render fires is not taken for a click.
  return `<li><details class="sb-fold" data-fold="${id}" data-was="${open ? 1 : ""}" ${open ? "open" : ""}><summary class="nav-item" title="${title}">
    <span class="chev ${phase ? `ph-${phase}` : ""}" aria-hidden="true"></span><span class="sb-text">${label}</span><span class="count">${n}</span></summary>
    <ul class="sb-list">${body}</ul></details></li>`;
}

function navTree(st, programs) {
  const ss = st ? st.sessions || [] : [], now = Date.now(), kids = {};
  const isLead = (s) => s.kind === "orchestrator" || s.kind === "orchestration";
  const shown = (s) => s.phase !== "offline" || s.unread || isLead(s);
  ss.filter(shown).forEach((s) => { kids[s.parent] = (kids[s.parent] || 0) + 1; });
  const runOf = new Map(ss.filter((s) => s.run && isLead(s)).map((s) => [s.run, s.id]));
  const archived = (p) => p.closed || (!runOf.has(p.run) && now - ms(p.last_event || p.generated_at) > ARCHIVE_MS);
  const progOf = new Map(programs.filter((p) => runOf.has(p.run) && !archived(p)).map((p) => [runOf.get(p.run), p]));
  const loose = programs.filter((p) => !runOf.has(p.run) && !archived(p)), old = programs.filter(archived);
  const progRow = (p, d) => {
    const s = p.summary || {}, done = s.predicate_total ? `${s.predicate_done ?? "–"}/${s.predicate_total}` : "";
    return `<li><a class="nav-item tree" style="--d:${d}" href="#/${encodeURIComponent(p.slug)}/${isFleet() ? "overview" : S.section}"
      ${p.slug === S.slug ? 'aria-current="page"' : ""} title="Program ${esc(p.slug)}${done ? ` · ${done} done` : ""}">
      <span class="avatar" aria-hidden="true">${esc(p.slug.slice(0, 2))}<span class="dot ${mainTone(s.main) ? "tone-" + mainTone(s.main) : ""}"></span></span>
      <span class="sb-text">${esc(p.slug)}</span><span class="count">${esc(done)}</span></a></li>`;
  };
  const row = (s, d) => {
    const n = kids[s.id] || 0, p = progOf.get(s.id);
    return `<li><a class="nav-item tree ${isLead(s) ? "lead" : ""}" style="--d:${Math.min(d, 3)}" href="${sessionHref(s.id)}"
      ${F.sessionId === s.id ? 'aria-current="page"' : ""} title="${esc(s.name)} · ${esc(kindLabel(st, s)[0])} · ${esc(s.phase)}${n ? ` · ${n} under it` : ""}">${phaseDot(s)}
      <span class="sb-text">${esc(s.name)}</span>${n ? `<span class="kids" aria-label="${n} under it">${n}</span>` : ""}<span class="slot">${badge(s.unread, s.missed)}</span></a></li>`
      + (p ? progRow(p, Math.min(d + 1, 3)) : "");
  };
  const top = [], units = [];
  const rows = st ? sessionTree(st).filter(([s]) => shown(s)) : [];
  for (const [s, d] of rows) {
    if (s.kind === "orchestrator") { top.push(row(s, 0), ...loose.map((q) => progRow(q, 1))); continue; }
    const head = !units.length || d <= units[units.length - 1].d;
    if (head) units.push({ d, members: [] });
    units[units.length - 1].members.push([s, d]);
  }
  if (!rows.some(([s]) => s.kind === "orchestrator")) top.push(...loose.map((q) => progRow(q, 0)));
  const groups = FOLD_OF.map(() => ({ n: 0, html: [] }));
  for (const u of units) {
    const g = groups[Math.min(...u.members.map(([s]) => urgency(s)))];
    g.n += u.members.length;
    g.html.push(...u.members.map(([s, d]) => row(s, d - u.d + 1)));
  }
  const out = [...top, ...groups.map((g, i) => g.n ? fold(FOLD_OF[i], g.n, g.html.join("")) : "")];
  if (old.length) out.push(fold("archived", old.length, old.map((p) => progRow(p, 1)).join("")));
  return out.join("");
}
document.addEventListener("toggle", (e) => {
  const d = e.target;
  if (!d.dataset || !d.dataset.fold || d.open === (d.dataset.was === "1")) return;
  d.dataset.was = d.open ? "1" : "";
  store.set(FOLDS_KEY, JSON.stringify({ ...folds(), [d.dataset.fold]: d.open }));
}, true);

function fleetHeader() {
  const st = S.state;
  if (!st) { $("#headline").innerHTML = "<h1>Fleet</h1>"; $("#top-right").innerHTML = ""; return; }
  const root = byId(st)[st.root];
  const n = (p) => (st.sessions || []).filter((s) => s.phase === p).length;
  $("#headline").innerHTML = `<h1>Fleet${root ? ` · <span class="dim">${esc(root.name)}</span>` : ""}</h1>
    <div class="meta"><span>${(st.sessions || []).length} sessions</span><span>${n("working")} working</span><span>${n("waiting")} waiting</span>
      <span>${(st.runs || []).length} runs</span>${st.me ? `<span class="mono">@${esc(st.me)}</span>` : ""}</div>`;
  const missed = (st.items || []).filter((i) => i.missed).length;
  const errs = Object.entries(st.sources || {}).filter(([, v]) => v.ok === false).length, troubled = autoTroubled(st).length, lost = monTroubled(st).length;
  $("#top-right").innerHTML = [missed ? `<a href="#/fleet/inbox">${tag(`${missed} missed`, "bad", "alert")}</a>` : "",
    `<a href="#/fleet/automations">${troubled ? tag(`Automations ▲${troubled}`, "bad", "alert") : tag(`Automations ${(st.automations || []).length}`, "", "clock")}</a>`,
    lost ? `<a href="#/fleet/monitors">${tag(`Monitors ▲${lost}`, "bad", "alert")}</a>` : "",
    errs ? tag(`${errs} source error${errs > 1 ? "s" : ""}`, "warn", "alert") : ""].join("");
}

// ------------------------------------------------------------ pieces

// The project a row belongs to (fleet.py project_of, from live Orca/git state). A badge filters every fleet view to it.
const projNow = () => S.filters.fleet_project || "all";
const inProject = (p) => projNow() === "all" || p === projNow();
const projBadge = (p) => p ? `<button class="chip proj" type="button" data-group="fleet_project" data-val="${esc(p)}" title="Show only ${esc(p)}">${esc(p)}</button>` : "";

function projectChips(st) {
  const n = {}, cur = projNow();
  (st.sessions || []).forEach((s) => { if (s.project) n[s.project] = (n[s.project] || 0) + 1; });
  const names = Object.keys(n).sort();
  if (names.length < 2 && cur === "all") return "";
  const chip = (v, label, c) => `<button class="chip" type="button" data-group="fleet_project" data-val="${esc(v)}" aria-pressed="${v === cur}">${esc(label)}${c != null ? `<span class="n">${c}</span>` : ""}</button>`;
  return `<div class="chips proj-chips" role="group" aria-label="Project">${chip("all", "All projects")}${names.map((x) => chip(x, x, n[x])).join("")}</div>`;
}

function itemRow(st, it, { showSession = true } = {}) {
  const [label, tone, ic] = itemMeta(it.type), s = byId(st)[it.session];
  const copy = it.command || it.url || "";
  // Outside its own session page a row opens that session; an item with no live session has nowhere to go.
  const go = showSession && s ? `data-go="${sessionHref(s.id)}"` : "";
  return `<li class="item ${it.missed ? "is-missed" : ""}" ${go}><span class="ic tone-${tone}">${icon(ic)}</span>
    <div class="grow"><div class="full"><b>${esc(label)}</b> <span class="dim">${esc(it.title || "")}</span></div>
      <div class="sub muted full">${projBadge(it.project)}${showSession && s ? `<a href="${sessionHref(s.id)}">${esc(s.name)}</a> · ` : ""}${it.pr ? `<span class="mono">${esc(it.pr)}</span> · ` : ""}${esc(it.source || "")}${it.held ? ` · ${tag(it.held_todo ? "to-do" : "held", "")} ${esc(it.held)}` : ""}
      ${it.command ? ` · <span class="mono">${esc(it.command)}</span>` : it.detail ? ` · ${esc(String(it.detail).split("\n")[0])}` : ""}</div>
      ${it.type === "prompt" ? promptActs(it) : it.type === "decision" ? decisionActs(st, it) : ""}</div>
    <span class="acts">${it.missed ? tag("missed", "bad", "alert") : ""}<span class="when">${it.at ? relSpan(it.at) : ""}</span>
      <span class="slot">${safeUrl(it.url) ? link(it.url, icon("link"), "icon-btn") : ""}</span>
      <span class="slot">${copy ? `<button class="icon-btn" data-copy="${esc(copy)}" title="Copy">${icon("copy")}</button>` : ""}</span>
      <span class="slot"><button class="icon-btn" data-dismiss="${esc(it.key)}" title="Dismiss">${icon("x")}</button></span></span></li>`;
}

// Approve and Deny take a second click (the list re-renders under the pointer every tick); Open does not.
function promptActs(it) {
  const r = F.prompt[it.key], armed = F.arm && F.arm.key === it.key ? F.arm.action : "";
  const btn = (a, label, ic) => a === "open" || (it.answers || []).includes(a)
    ? `<button class="chip ${armed === a ? "armed tone-warn" : ""}" data-prompt="${a}" data-key="${esc(it.key)}" ${r && r.busy ? "disabled" : ""}>${icon(ic)}${armed === a ? `Confirm ${label.toLowerCase()}` : label}</button>` : "";
  const msg = !r || r.busy ? "" : r.ok ? (r.action === "open" ? "Opened in Orca." : "Sent; the dialog closed.")
    : r.typed ? `Pressed ${r.key}, but not confirmed (${r.reason}). Check the terminal.` : `Not sent: ${r.reason}.`;
  return `<div class="prompt-acts">${btn("approve", "Approve", "check")}${btn("deny", "Deny", "x")}${btn("open", "Open terminal", "arrow")}
    ${r && r.busy ? '<span class="muted">Checking the screen…</span>' : msg ? `<span class="toned tone-${r.ok ? "good" : r.typed ? "warn" : "bad"}">${esc(msg)}</span>` : ""}</div>`;
}

// True on the second click of the same button within 5 s; the first click only arms it.
function confirmed(key, action) {
  if (F.arm && F.arm.key === key && F.arm.action === action) { F.arm = null; return true; }
  F.arm = { key, action };
  clearTimeout(F.armTimer);
  F.armTimer = setTimeout(() => { F.arm = null; renderView(); }, 5000);
  renderView();
  return false;
}

async function promptAction(key, action) {
  const it = ((S.state || {}).items || []).find((i) => i.key === key);
  if (!it || (action !== "open" && !confirmed(key, action))) return;
  F.prompt[key] = { busy: true }; renderView();
  let out;
  try { out = await fleetPost("/api/fleet/prompt", { session: it.session, prompt: it.prompt, action }); } catch (err) { out = { ok: false, reason: String(err.message || err) }; }
  F.prompt[key] = { action, ...out };
  renderView();
}

// A decision registered with `orch decide`: each option, or a typed answer, answers it on a second click. The server
// records the answer and closes the decision first, then types "decision <id>: <answer>" into the orchestrator's or lead's terminal
// now if it is idle, or on a later collect once it is: a busy orchestrator or lead never makes the answer fail.
function decisionActs(st, it) {
  const r = F.decide[it.key], armed = F.arm && F.arm.key === it.key ? F.arm.action : "", off = r && r.busy ? "disabled" : "";
  const opts = it.options || [];
  const chip = (action, label, extra = "") => `<button class="chip ${armed === action ? "armed tone-warn" : ""}" data-decide="${action}" data-key="${esc(it.key)}" ${extra} ${off}>${label}</button>`;
  const buttons = opts.map((o, i) => chip(`opt:${i}`, `${it.recommend === i + 1 ? icon("check") : ""}${armed === `opt:${i}` ? "Confirm " : ""}${esc(o.label)}`,
    `title="${esc(o.description || o.label)}${it.recommend === i + 1 ? " (recommended)" : ""}"`)).join("");
  const line = armed ? `decision ${it.decision}: ${armed === "text" ? (F.draft[it.key] || "").trim() : (opts[+armed.slice(4)] || {}).label}` : "";
  const msg = !r || r.busy ? "" : !r.ok ? `Not recorded: ${r.reason}.`
    : r.delivered ? "Recorded and sent to the orchestrator or lead." : "Recorded; will relay to the orchestrator or lead when it is idle.";
  const hint = `<div class="sub muted">Or answer in the orchestrator's or lead's terminal: <span class="mono">${esc(it.decision)}: ${esc((opts[it.recommend - 1] || {}).label || "<answer>")}</span></div>`;
  return `<div class="decision">${buttons ? `<div class="prompt-acts">${buttons}</div>` : ""}${hint}
    <div class="prompt-acts"><input id="decide-${esc(it.decision)}" data-decide-input="${esc(it.key)}" class="search" maxlength="1900" autocomplete="off"
        placeholder="Or type an answer" value="${esc(F.draft[it.key] || "")}" ${off}>${chip("text", `${icon("send")}${armed === "text" ? "Confirm send" : "Send"}`)}
      ${r && r.busy ? '<span class="muted">Recording…</span>' : line ? `<span class="muted ellipsis">answers <span class="mono">${esc(line)}</span></span>`
        : msg ? `<span class="toned tone-${r.ok ? "good" : "bad"}">${esc(msg)}</span>${r.ok ? "" : `<button class="chip" data-copy="${esc(r.line)}">${icon("copy")}Copy</button>`}` : ""}</div></div>`;
}

async function decideAction(key, action) {
  const st = S.state || {}, it = (st.items || []).find((i) => i.key === key);
  if (!it) return;
  const answer = action === "text" ? (F.draft[key] || "").trim() : ((it.options || [])[+action.slice(4)] || {}).label;
  if (!answer || !confirmed(key, action)) return;
  const line = `decision ${it.decision}: ${answer}`;
  F.decide[key] = { busy: true, line }; renderView();
  let out;
  try { out = await fleetPost("/api/fleet/decide", { decision: it.decision, answer }); } catch (err) { out = { ok: false, reason: String(err.message || err) }; }
  F.decide[key] = { line, ...out };
  if (out.ok && action === "text") F.draft[key] = "";
  renderView();
}

function itemList(st, items, opts) {
  const pinned = items.filter((i) => inProject(i.project)).sort((a, b) => b.missed - a.missed).slice(0, opts?.limit);
  return `<ul class="rows items">${pinned.map((it) => itemRow(st, it, opts)).join("") || `<li class="empty">${icon("check")}Nothing waiting on you.</li>`}</ul>`;
}

// A timeline event's state picks its rail icon and colour (.tl-<state> in style.css). Each state has its own shape,
// so colour is not the only cue. A kind not named here (one the collector adds later) reads as info.
function eventState(kind) {
  if (/fail|error|escalation|changes_requested/.test(kind)) return "bad";
  if (/^(pr_merged|pr_approved|pr_checks_recovered|request_done|turn_done|item_verify_ok|mail_worker_done)$/.test(kind)) return "done";
  if (kind === "item_resolved") return "info";
  if (/^(item_|mail_(question|ask|decision))|^pr_(review_comment|approval_stale)$/.test(kind)) return "wait";
  if (/^(prompt|pr_head_pushed|pr_review_requested|request_link|mail_)/.test(kind)) return "active";
  return "info";
}
const TL_STATE = { done: "Done", active: "In progress", wait: "Waiting on you", bad: "Problem", info: "Info" };
const TL_TONE = { done: "tone-good", active: "tone-accent", wait: "tone-warn", bad: "tone-bad", info: "" };
const TL_LABEL = {
  prompt: "Prompt sent", turn_done: "Turn finished", item_resolved: "Inbox item closed", inbox_aged_out: "Inbox item aged out",
  pr_approved: "Approved", pr_changes_requested: "Changes requested", pr_review_comment: "Review comment",
  pr_review_requested: "Review requested", pr_head_pushed: "New commit", pr_approval_stale: "Approval on old commit",
  pr_checks_failed: "CI failed", pr_checks_recovered: "CI recovered", pr_merged: "Merged", pr_closed: "Closed unmerged",
  request_link: "Request linked", request_done: "Request done", request_drop: "Request dropped", requests_error: "Request ledger error",
  mail_worker_done: "Worker done", mail_escalation: "Escalation", mail_question: "Worker question",
};
const eventLabel = (kind) => TL_LABEL[kind] || (kind.startsWith("item_") && ITEM[kind.slice(5)] ? ITEM[kind.slice(5)][0]
  : kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()));
const TL_ICON = {
  done: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M4.8 8.2l2.1 2.1 4.3-4.4" fill="none" stroke="var(--panel)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  active: '<circle cx="8" cy="8" r="7" fill="currentColor"/><g fill="var(--panel)"><circle cx="4.9" cy="8" r="1.15"/><circle cx="8" cy="8" r="1.15"/><circle cx="11.1" cy="8" r="1.15"/></g>',
  wait: '<circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="8" cy="8" r="2.4" fill="currentColor"/>',
  bad: '<circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M8 4.4v4.4" stroke="var(--panel)" stroke-width="1.9" stroke-linecap="round"/><circle cx="8" cy="11.4" r="1.1" fill="var(--panel)"/>',
  info: '<circle cx="8" cy="8" r="3.5" fill="none" stroke="currentColor" stroke-width="1.6"/>',
};
const TL_STEP = 50;
// Clamped (two lines; `cls` may set another) until clicked (tlToggle), then the whole text.
const expandable = (id, text, cls = "") => `<div class="${["tl-desc", cls, F.tlOpen.has(id) && "open"].filter(Boolean).join(" ")}" data-tl-open="${esc(id)}" role="button" tabindex="0" aria-expanded="${F.tlOpen.has(id)}">${esc(text)}</div>`;

// Newest first, one group per day. `fold` names the list so "Show more" survives a redraw; `here` is the session
// whose page this is (its rows have nowhere else to go).
function timelineList(st, rows, { fold = null, here = null } = {}) {
  const ss = byId(st), today = dayLabel(Date.now()), yesterday = dayLabel(Date.now() - 864e5);
  const shown = fold ? rows.slice(0, F.tlShow[fold] || TL_STEP) : rows;
  let day = null;
  const out = shown.map((e) => {
    const d = e.at ? dayLabel(e.at) : "";
    const head = d !== day ? `<li class="tl-day eyebrow">${esc((day = d) === today ? "Today" : d === yesterday ? "Yesterday" : d || "Undated")}</li>` : "";
    const kind = e.kind || "event", state = eventState(kind), id = `${e.at}|${kind}|${e.session}`;
    const other = ss[e.session] && e.session !== here ? ss[e.session] : null;
    return `${head}<li class="tl-ev tl-${state} ${TL_TONE[state]}" ${other ? `data-go="${sessionHref(other.id)}"` : ""}>
      <span class="tl-tm" title="${esc(e.at ? new Date(e.at).toLocaleString() : "")}">${e.at ? hhmm(e.at) : ""}</span>
      <span class="tl-rail"><svg viewBox="0 0 16 16" role="img"><title>${TL_STATE[state]}</title>${TL_ICON[state]}</svg></span>
      <div class="tl-body"><div class="tl-title">${esc(eventLabel(kind))}${other ? `<a class="tl-by" href="${sessionHref(other.id)}">${esc(other.name)}</a>` : ""}</div>
        ${e.text ? expandable(id, e.text) : ""}</div>
      ${e.pr && safeUrl(e.url) ? link(e.url, `${esc(e.pr)} ${icon("arrow")}`, "tl-end mono") : ""}
      ${e.job ? `<a class="tl-end" href="#/fleet/automations" title="The line is cut to fit; the job's row has the whole error">Full error ${icon("arrow")}</a>` : ""}</li>`;
  }).join("");
  const more = rows.length - shown.length;
  return `<ul class="tl">${out || '<li class="tl-none muted">No events yet.</li>'}${more > 0
    ? `<li class="tl-more"><button class="chip" data-tl-more="${esc(fold)}">Show ${more > TL_STEP ? `${TL_STEP} more · ${more} hidden` : `${more} more`}</button></li>` : ""}</ul>`;
}

// The graph redraws every poll: a team (the server serves no avatar for one) or an avatar that failed once is not
// asked for again, or a page left open fetches the same 404s all day.
const AVATAR_LOGIN = /^[A-Za-z0-9-]{1,39}(\[bot\])?$/, AVATAR_MISSED = new Set();

function avatarMissed(img) {
  AVATAR_MISSED.add(img.dataset.login);
  img.remove();
}

function avatarNode(x, y, r, login) {
  const init = esc(login.replace(/\[bot\]$/, "").slice(0, 2));
  const img = AVATAR_LOGIN.test(login) && !AVATAR_MISSED.has(login)
    ? `<image href="/api/fleet/avatar/${encodeURIComponent(login)}" data-login="${esc(login)}" x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" clip-path="circle(${r}px)" onerror="avatarMissed(this)"/>` : "";
  return `<g><circle cx="${x}" cy="${y}" r="${r}" class="av-bg"/><text x="${x}" y="${y + 3.5}" text-anchor="middle" class="av-tx">${init}</text>
    ${img}</g>`;
}

const clip = (t, n) => (t && t.length > n ? t.slice(0, n - 1) + "…" : t || "");

// Session -> PR -> reviewer, three columns; each PR is as tall as its reviewer list.
function relGraph(st, sessionId) {
  const ss = byId(st);
  const prs = (st.prs || []).filter((p) => p.session && (!sessionId || p.session === sessionId)
    && (sessionId || inProject((ss[p.session] || {}).project)));
  if (!prs.length) return `<div class="empty-chart">No pull request linked to ${sessionId ? "this session" : "any session"} yet.</div>`;
  const groups = {};
  prs.forEach((p) => (groups[p.session] ||= []).push(p));
  const RH = 34, W = 980, X = [8, 300, 720], out = [];
  let y = 12;
  for (const [sid, list] of Object.entries(groups)) {
    const s = ss[sid] || { name: sid, phase: "offline" }, top = y;
    const mids = list.map((p) => {
      const revs = (p.reviewers || []).filter((r) => !r.bot || r.status !== "none");
      const h = Math.max(1, revs.length) * RH, mid = y + h / 2 - RH / 2 + 13;
      const ci = CI_TONE[p.ci] || "", closed = p.state !== "OPEN";
      out.push(`<g class="pr ${closed ? "closed" : ""}"><a href="${esc(safeUrl(p.url) || "#")}" target="_blank" rel="noopener">
        <rect class="box" x="${X[1]}" y="${mid - 13}" width="300" height="26" rx="6"/><rect class="bar tone-${ci}" x="${X[1]}" y="${mid - 13}" width="3" height="26" rx="1.5"/>
        <text x="${X[1] + 12}" y="${mid + 4}"><tspan class="num">#${esc(p.number)}</tspan> ${esc(clip(p.title, 34))}</text></a>
        <title>${esc(p.key)} ${esc(p.title || "")} · ${esc(p.state)} · CI ${esc(p.ci || "none")} · ${esc(p.decision || "no decision")}</title></g>`);
      revs.forEach((r, i) => {
        const ry = y + i * RH + 13, [cls, label] = EDGE[r.status] || EDGE.none;
        out.push(`<path class="edge rv ${cls}" d="M${X[1] + 300} ${mid} C ${X[1] + 360} ${mid}, ${X[2] - 60} ${ry}, ${X[2] - 12} ${ry}"><title>${esc(r.login)}: ${esc(label)}</title></path>
          ${avatarNode(X[2], ry, 10, r.login)}<text class="rv-name" x="${X[2] + 16}" y="${ry + 4}">${esc(clip(r.login, 18))} <tspan class="muted">${esc(label)}</tspan><title>${esc(r.login)}: ${esc(label)}</title></text>`);
      });
      if (!revs.length) out.push(`<text class="rv-name muted" x="${X[2] - 12}" y="${mid + 4}">no reviewer</text>`);
      y += h + 6;
      return [mid, ci];
    });
    const sy = (top + y - 6) / 2 - 1;
    mids.forEach(([mid, ci]) => out.push(`<path class="edge tone-${ci}" d="M${X[0] + 232} ${sy} C ${X[0] + 262} ${sy}, ${X[1] - 30} ${mid}, ${X[1]} ${mid}"/>`));
    out.push(`<g class="sess"><a href="${sessionHref(sid)}"><rect class="box" x="${X[0]}" y="${sy - 15}" width="232" height="30" rx="6"/>
      <circle class="ph ph-${esc(s.phase)}" cx="${X[0] + 14}" cy="${sy}" r="4"/>
      <text x="${X[0] + 26}" y="${sy + 4}">${s.project ? `<tspan class="proj-tx">${esc(clip(s.project, 14))}</tspan> ` : ""}${esc(clip(s.name, s.project ? 24 - Math.min(14, s.project.length) : 26))}</text></a>
      <title>${esc(s.project || "")} · ${esc(s.name)} · ${esc(s.kind || "")} · ${esc(s.phase)}</title></g>`);
    y += 10;
  }
  // Capped at its own width: scaled up to a wide card, its text would grow to heading size.
  return `<div class="relgraph"><svg viewBox="0 0 ${W} ${y}" style="min-width:720px;max-width:${W}px" role="img" aria-label="Sessions, pull requests and reviewers">${out.join("")}</svg></div>
    <div class="legend" style="padding:0 var(--s4) var(--s3)">${Object.entries(EDGE).filter(([k]) => k !== "none")
      .map(([, [cls, label]]) => `<span><i class="key ${cls}"></i>${label}</span>`).join("")}${phaseKeys()}</div>`;
}

// ------------------------------------------------------------ PC load

// reap-resources' pressure round (a LaunchAgent, every `interval` s) writes one point per round; /api/fleet/pressure
// hands back those points, the thresholds the hook judges by and the held queue. Nothing here measures.
const PRESSURE_LEVEL = { ok: ["ok", "good"], warn: ["warn", "warn"], danger: ["danger", "bad"] };
const PRESSURE_CHARTS = [
  ["load_per_cpu", "Load per CPU", (v) => num(v, 2)],
  ["mem_free_pct", "Memory free", (v) => (v == null ? "—" : `${v}%`)],
  ["docker_cpu_pct", "Docker CPU", (v) => (v == null ? "—" : `${v}%`)],
  ["queued", "Held starts", (v) => num(v)],
];
async function pollPressure() {
  const hours = S.filters.pressure_hours === "24" ? 24 : 6;
  if (F.pressureBusy || (F.pressure && F.pressureHours === hours && Date.now() - F.pressureAt < 60e3)) return;
  F.pressureBusy = true;
  try {
    const r = await fetch(`/api/fleet/pressure?hours=${hours}`, { cache: "no-store" });
    F.pressure = r.ok ? await r.json() : { error: `HTTP ${r.status}` };
  } catch (e) { F.pressure = { error: String(e.message || e) }; }
  F.pressureAt = Date.now(); F.pressureHours = hours; F.pressureBusy = false;
  if (isFleet() && S.section === "overview") renderView();
}
// One metric over the window. The SVG stretches to its cell (preserveAspectRatio none, strokes kept at 2px), so its
// text lives in HTML around it. A gap of over three rounds breaks the line instead of drawing across it.
function pressureChart(rep, key, label, fmt) {
  const W = 300, H = 56, t1 = rep.now * 1000, t0 = t1 - rep.hours * 3600e3, gap = 3 * rep.interval * 1000;
  const pts = (rep.series || []).map((r) => [r.t * 1000, r[key]]).filter(([, v]) => v != null);
  const [warn, danger] = (rep.thresholds || {})[key] || [null, null];
  const top = Math.max(key === "mem_free_pct" ? 100 : 1, ...pts.map(([, v]) => v), (danger ?? warn ?? 0) * 1.15);
  const x = (t) => ((t - t0) / (t1 - t0)) * W, y = (v) => H - 2 - (v / top) * (H - 4);
  let line = "", prev = null;
  for (const [t, v] of pts) { line += `${prev == null || t - prev > gap ? "M" : "L"}${x(t).toFixed(1)},${y(v).toFixed(1)}`; prev = t; }
  const rule = (v, tone) => v == null || v > top ? "" : `<line x1="0" x2="${W}" y1="${y(v)}" y2="${y(v)}" style="stroke:var(--${tone})" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`;
  const band = W / Math.max(1, (rep.hours * 3600) / rep.interval);
  const tips = (rep.series || []).filter((r) => r[key] != null).map((r) =>
    `<rect x="${(x(r.t * 1000) - band / 2).toFixed(1)}" y="0" width="${band.toFixed(2)}" height="${H}" fill="transparent" data-tip="${esc(`<div class="tt">${hhmm(r.t * 1000)}</div><div class="row">${esc(label)}<b>${esc(fmt(r[key]))}</b></div><div class="row">Level<b>${esc((PRESSURE_LEVEL[r.level] || [r.level])[0])}</b></div>`)}"/>`).join("");
  const now = key === "queued" ? (rep.queue || []).length : (((rep.last || {}).metrics || {})[key]);
  const said = `${label}: now ${fmt(now)}${warn != null ? `, warn ${warn}` : ""}${danger != null ? `, danger ${danger}` : ""}, ${pts.length} points over ${rep.hours} hours`;
  const limits = [warn != null && `warn ${(rep.higher_worse || {})[key] === false ? "≤" : "≥"} ${warn}`, danger != null && `danger ${danger}`].filter(Boolean).join(" · ");
  return `<div class="pmetric"><div class="pm-head"><span class="eyebrow">${esc(label)}</span><b class="pm-v">${esc(fmt(now))}</b></div>
    <div class="chart pm-chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" height="${H}" role="img" aria-label="${esc(said)}">
      ${rule(warn, "warn")}${rule(danger, "bad")}<path d="${line}" fill="none" style="stroke:var(--accent)" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>${tips}</svg></div>
    <div class="pm-foot muted">${limits || "no threshold"}</div></div>`;
}
function pressureCard() {
  pollPressure();
  const rep = F.pressure, hours = S.filters.pressure_hours === "24" ? "24" : "6";
  const head = (aside) => `<div class="card-head"><h3>PC load</h3><span class="aside">${aside}</span></div>`;
  if (!rep) return `<div class="card pressure">${head("")}<div class="empty">Loading…</div></div>`;
  if (rep.error) return `<div class="card pressure">${head("")}<div class="empty">${esc(rep.error)}</div></div>`;
  const last = rep.last, lv = PRESSURE_LEVEL[last && last.level] || ["no round yet", ""];
  const age = last ? new Date(last.t * 1000).toISOString() : null;
  // A level strip over the same window: what state the machine was in, at a glance, before reading any one chart.
  const t1 = rep.now * 1000, t0 = t1 - rep.hours * 3600e3, step = rep.interval * 1000;
  const strip = (rep.series || []).map((r) => {
    const l = ((r.t * 1000 - t0) / (t1 - t0)) * 100, w = (1.1 * step / (t1 - t0)) * 100;  // 10% over, so rounding leaves no seam
    return `<i class="tone-${(PRESSURE_LEVEL[r.level] || ["", "derived"])[1]}" style="left:${l.toFixed(2)}%;width:${w.toFixed(2)}%"></i>`;
  }).join("");
  const m = (last && last.metrics) || {};
  const extra = [["CPU idle", m.cpu_idle_pct, "%"], ["Swap", m.swap_gb, " GB"], ["Swap-out", m.swapout_pps, "/s"], ["Docker memory", m.docker_mem_pct, "%"], ["Containers", m.containers, ""], ["Disk free", m.disk_free_gb, " GB"]]
    .filter(([, v]) => v != null).map(([k, v, u]) => `${k} <b>${esc(String(v))}${u}</b>`).join(" · ");
  const queue = (rep.queue || []).map((e) => `<li>${tag(e.state, e.state === "waiting" ? "warn" : "accent", "")}<span class="grow ellipsis">${esc(e.what)} <span class="muted">${esc(e.why || "")}</span></span><span class="when">until ${hhmm(e.deadline * 1000)}</span></li>`).join("");
  const empty = !(rep.series || []).length;
  return `<div class="card pressure">${head(`${chips("pressure_hours", [["6", "6h"], ["24", "24h"]], hours)}`)}
    <div class="p-now">${tag(lv[0], lv[1])}<span class="muted">${last ? `last round ${relSpan(age)}` : rep.installed ? "the pressure round has not run yet" : "pressure-watch is not installed (reap-resources pressure.py install)"}</span>
      ${last && (last.why || []).length ? `<span class="p-why">${esc(last.why.join(", "))}</span>` : ""}</div>
    ${empty ? `<div class="empty">No points in the last ${rep.hours}h yet: the pressure round records one every ${Math.round(rep.interval / 60)} min.</div>`
      : `<div class="p-strip" role="img" aria-label="Level per round over the last ${rep.hours} hours">${strip}</div>
    <div class="p-grid">${PRESSURE_CHARTS.map(([k, label, fmt]) => pressureChart(rep, k, label, fmt)).join("")}</div>
    <div class="p-axis muted"><span>${rep.hours}h ago</span><span>now</span></div>`}
    ${extra ? `<div class="p-extra muted">${extra}</div>` : ""}
    ${queue ? `<ul class="rows p-queue">${queue}</ul>` : ""}</div>`;
}

// ------------------------------------------------------------ views

function fleetOverview(st) {
  const ss = st.sessions || [], items = st.items || [];
  const n = (p) => ss.filter((s) => s.phase === p).length;
  const open = (st.prs || []).filter((p) => p.state === "OPEN");
  const missed = items.filter((i) => i.missed).length;
  return `${pressureCard()}<div class="kpis" style="--n:3">
      ${kpi("Sessions", `${ss.length}`, phaseLegend(["working", "waiting", "idle"], n), { src: "orca" })}
      ${kpi("Needs you", `${items.length}${missed ? ` ${tag(`${missed} missed`, "bad", "alert")}` : ""}`, Object.entries(st.counts || {}).map(([t, c]) => `${itemMeta(t)[0]} ${c}`).join(" · ") || "inbox empty")}
      ${kpi("Open PRs", `${open.length}`, `${open.filter((p) => p.ci === "failure").length} CI failing · ${open.filter((p) => p.decision === "APPROVED").length} approved`, { src: "github" })}
    </div>
    ${projectChips(st)}
    <div class="grid fill">
      <div class="card"><div class="card-head"><h3>Needs you</h3><span class="aside"><a class="go" href="#/fleet/inbox">Inbox ${icon("arrow")}</a></span></div>${itemList(st, items, { limit: 8 })}</div>
      <div class="card" data-src="orca"><div class="card-head"><h3>Moving now</h3><span class="aside">${n("working")} working</span></div>${sessionRows(st, ss.filter((s) => ["working", "waiting"].includes(s.phase)))}</div>
      ${leadCard(st)}
      <div class="card span2" data-src="github"><div class="card-head"><h3>Open pull requests</h3><span class="aside">${prUpdated(st)}<a class="go" href="#/fleet/prs">All ${open.length} ${icon("arrow")}</a></span></div>${prRows(st, open.slice(0, 8))}</div>
      <div class="card"><div class="card-head"><h3>Timeline</h3><span class="aside"><a class="go" href="#/fleet/timeline">All ${icon("arrow")}</a></span></div>${timelineList(st, (st.timeline || []).slice(0, 12))}</div>
    </div>`;
}

// Acks (acks.py, `orch-dash ack`): the owner's "checked, stop showing it" on finished rows. The
// server decides what may be acked; the page offers it only where the server listed it, and shows a refusal's reason.
const ackBtn = (it) => !it.ack ? "" : `<button class="chip ack" type="button" data-ack="${esc(it.key)}"${it.ack === "acked" ? " data-undo" : ""}
  title="${it.ack === "acked" ? "Show it again" : "Checked: hide it here and in the brief (orch-dash ack)"}">${it.ack === "acked" ? "Unack" : "Ack"}</button>`;
// The items to draw (acked and hidden ones only while Show is on), and the Ack all / Show N acked controls.
function ackSplit(items, what) {
  const off = (it) => it.ack === "acked" || it.hidden, seen = items.filter(off).length;
  const open = items.filter((it) => it.ack === "ackable" && !it.hidden).map((it) => it.key);
  return { shown: items.filter((it) => F.showAcked || !off(it)), seen,
    head: (open.length > 1 ? `<button class="chip ack" type="button" data-ack="${esc(open.join(" "))}" title="Ack every ${what} listed">Ack all ${open.length}</button>` : "")
      + (seen ? `<button class="chip" type="button" data-ack-show aria-pressed="${!!F.showAcked}">Show ${seen} acked</button>` : "") };
}
const undoNote = () => !F.undo ? "" : `<div class="ack-note">${F.undo.attr === "ack" ? "Acked" : "Closed"} <b>${esc(F.undo.label)}</b>
  <button class="chip ack" type="button" data-${F.undo.attr}="${esc(F.undo.ids)}" data-undo title="Show it in the table again">Undo</button></div>`;
const ackNote = () => F.ackError ? `<div class="ack-note">${tag("not acked", "warn", "alert")} ${esc(F.ackError)}</div>` : "";
// Ack and Done post the same way: the ids, undo, and the refusals shown under F[err]. A row acked or closed from the
// brief table leaves it on the next poll, so its Undo waits in F.undo above the table until the next one.
async function idsAction(t, url, ids, err) {
  t.disabled = true;
  const undo = "undo" in t.dataset, row = t.closest("[data-row-label]");
  try {
    const out = await fleetPost(url, { ids: ids.split(" "), undo });
    F[err] = (out.refused || []).map(([id, why]) => `${id}: ${why}`).join(" · ") || null;
    if (undo) F.undo = null;
    else if (row && (out.written || []).length) F.undo = { attr: url.endsWith("ack") ? "ack" : "done", ids, label: row.dataset.rowLabel };
    F.briefEtag = null;
    await pollBrief();
  } catch (e) { F[err] = String(e.message || e); }
  t.disabled = false;
  renderView();
}

// One row per lead: its sessions, how many of them work, and the open PRs of the lead and those sessions.
function leadCard(st) {
  const ss = st.sessions || [], leads = ss.filter((s) => s.kind === "orchestration" && inProject(s.project));
  if (!leads.length) return "";
  const rows = leads.map((l) => {
    const kids = ss.filter((x) => x.parent === l.id), mine = new Set([l.id, ...kids.map((x) => x.id)]);
    const prs = (st.prs || []).filter((p) => p.state === "OPEN" && mine.has(p.session)).length;
    const n = (k, word) => `${k} ${word}${k === 1 ? "" : "s"}`;
    return `<li>${phaseDot(l)}${projBadge(l.project)}<a class="grow ellipsis" href="${sessionHref(l.id)}"><b>${esc(l.name)}</b></a>
      <span class="acts muted">${n(kids.length, "session")} · ${kids.filter((x) => x.phase === "working").length} working · ${n(prs, "open PR")}</span></li>`;
  }).join("");
  return `<div class="card" data-src="orca"><div class="card-head"><h3>Leads</h3><span class="aside">${leads.length}</span></div><ul class="rows">${rows}</ul></div>`;
}

function sessionRows(st, list) {
  return `<ul class="rows">${list.filter((s) => inProject(s.project)).map((s) => {
    const a = (s.agents || [])[0];
    const doing = a ? (a.state === "working" ? `${a.tool || ""} ${a.detail || ""}` : a.state === "waiting" ? "waiting on a prompt" : "") : "";
    return `<li>${phaseDot(s)}${projBadge(s.project)}<a class="grow ellipsis" href="${sessionHref(s.id)}"><b>${esc(s.name)}</b> <span class="muted">${esc(doing.trim())}</span></a>
      <span class="acts"><span class="slot">${badge(s.unread, s.missed)}</span><span class="when">${relSpan(s.last_activity)}</span></span></li>`;
  }).join("") || '<li class="muted">Nothing running.</li>'}</ul>`;
}

function fleetInbox(st) {
  const items = st.items || [], counts = st.counts || {};
  const type = counts[S.filters.fleet_inbox] ? S.filters.fleet_inbox : "all";
  const shown = type === "all" ? items : items.filter((i) => i.type === type);
  return `<div class="view-head"><div><h2>Inbox</h2><p>Things a session is waiting on you for. Missed items (raised before the session's latest prompt) stay pinned until dismissed.</p></div></div>
    ${chips("fleet_inbox", [["all", "All", items.length], ...Object.entries(counts).map(([t, n]) => [t, itemMeta(t)[0], n])], type)}
    ${projectChips(st)}
    <div class="card">${itemList(st, shown)}</div>`;
}

// ------------------------------------------------------------ pull requests

// mergeStateStatus -> [label, tone]
const MERGE = { CLEAN: ["mergeable", "good"], HAS_HOOKS: ["mergeable", "good"], UNSTABLE: ["mergeable, checks red", "warn"],
  BEHIND: ["behind base", "warn"], BLOCKED: ["blocked", "warn"], DIRTY: ["conflicts", "bad"], DRAFT: ["draft", ""], UNKNOWN: ["computing", ""] };
const CI_CELL = { failure: ["failure", "bad"], pending: ["pending", "warn"], success: ["success", "good"] };
const DECISION = { APPROVED: ["approved", "good"], CHANGES_REQUESTED: ["changes requested", "bad"], REVIEW_REQUIRED: ["review required", "warn"] };
const RABBIT = { reviewed: ["reviewed", "good"], in_progress: ["reviewing", "accent"], rate_limited: ["rate-limited", "warn"],
  skipped: ["skipped", ""], failed: ["failed", "bad"] };
const approvedNow = (p) => (p.reviewers || []).some((r) => r.state === "APPROVED" && !r.stale);
const PR_FILTERS = {
  all: [() => true, "All open"],
  review: [(p) => !p.draft && p.decision !== "APPROVED" && !approvedNow(p), "Needs review"],
  ci: [(p) => p.ci === "failure", "CI failing"],
  mergeable: [(p) => !p.draft && ["CLEAN", "HAS_HOOKS", "UNSTABLE"].includes(p.merge_state), "Mergeable"],
};
const orderOf = (map) => Object.fromEntries(Object.keys(map).map((k, i) => [k, i]));
const PR_SORT = {
  pr: (p) => p.number, title: (p) => p.title, author: (p) => p.author, review: (p) => orderOf(DECISION)[p.decision],
  ci: (p) => orderOf(CI_CELL)[p.ci], merge: (p) => orderOf(MERGE)[p.merge_state],
  rabbit: (p) => orderOf(RABBIT)[p.coderabbit], session: (p) => (byId(S.state)[p.session] || {}).name,
  age: (p) => ago(p.merged_at || p.created), updated: (p) => ago(p.updated),
};
const cellTag = (map, v, blank = "—") => { const [t, tone] = map[v] || [v, ""]; return v ? tag(t, tone) : `<span class="muted">${blank}</span>`; };
// When GitHub was last read for PRs; the freshness bar colours it like the GitHub source.
const prUpdated = (st) => `<span data-src="github" title="GitHub checked">${icon("clock")} ${relSpan((st.sources?.github || {}).updated_at)}</span>`;
const merged24h = (st) => (st.prs || []).filter((p) => p.state === "MERGED" && Date.now() - ms(p.merged_at) < 24 * 3600e3);

function prRows(st, list) {
  return `<ul class="rows">${list.map((p) => `<li>${tag(p.ci || "no CI", CI_TONE[p.ci] || "")}
    <a class="grow ellipsis" href="${esc(safeUrl(p.url) || "#")}" target="_blank" rel="noopener noreferrer"><b>#${esc(p.number)}</b> ${esc(p.title)} <span class="muted">${esc(p.repo)}</span></a>
    <span class="acts">${p.decision ? cellTag(DECISION, p.decision) : ""}<span class="when">${relSpan(p.updated)}</span></span></li>`).join("")
    || '<li class="muted">No open pull request.</li>'}</ul>`;
}

function prTable(st, list) {
  const ss = byId(st);
  const head = [["pr", "PR"], ["title", "Title"], ["author", "Author", "hide-md"], ["review", "Review"], ["ci", "CI"], ["merge", "Merge", "hide-md"],
    ["rabbit", "CodeRabbit", "hide-md"], ["session", "Session", "hide-sm"], ["age", "Age", "hide-sm"], ["updated", "Updated"]];
  const rows = sorted("fleet_prs", list, PR_SORT).map((p) => {
    const s = ss[p.session];
    const threads = p.unresolved ? ` <span class="tag tone-accent" title="${esc(p.unresolved)} unresolved review thread(s)">${icon("note")}${esc(p.unresolved)}</span>` : "";
    return `<tr><td class="mono">${link(p.url, "#" + esc(p.number))}</td>
      <td class="title"><span class="ellipsis" style="display:block" title="${esc(p.title)}">${p.draft ? tag("draft") + " " : ""}${esc(p.title)}</span></td>
      <td class="hide-md dim">${esc(p.author || "—")}</td><td>${cellTag(DECISION, p.decision)}</td><td>${cellTag(CI_CELL, p.ci, "no CI")}</td>
      <td class="hide-md">${p.state === "OPEN" ? cellTag(MERGE, p.merge_state) : tag("merged", "accent", "merge")}</td>
      <td class="hide-md">${cellTag(RABBIT, p.coderabbit, "none")}${threads}</td>
      <td class="hide-sm">${s ? `<a href="${sessionHref(s.id)}">${phaseDot(s)} ${esc(s.name)}</a>` : '<span class="muted">—</span>'}</td>
      <td class="hide-sm muted">${relSpan(p.merged_at || p.created)}</td><td class="muted">${relSpan(p.updated)}</td></tr>`;
  }).join("");
  return `<table><thead><tr>${head.map(([c, l, cls]) => th("fleet_prs", c, l, cls)).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}

function fleetPrs(st) {
  const open = (st.prs || []).filter((p) => p.state === "OPEN"), merged = merged24h(st);
  const f = PR_FILTERS[S.filters.fleet_prs] ? S.filters.fleet_prs : "all";
  const repos = [...new Set(open.map((p) => p.repo))].sort(), repo = repos.includes(S.filters.fleet_prs_repo) ? S.filters.fleet_prs_repo : "all";
  const shown = open.filter((p) => PR_FILTERS[f][0](p) && (repo === "all" || p.repo === repo));
  const groups = {};
  shown.forEach((p) => (groups[p.repo] ||= []).push(p));
  const repoChip = (v, label, n) => `<button class="chip" type="button" data-group="fleet_prs_repo" data-val="${esc(v)}" aria-pressed="${v === repo}">${esc(label)}${n != null ? `<span class="n">${n}</span>` : ""}</button>`;
  return `<div class="view-head"><div><h2>Pull requests</h2><p>Every open PR you authored, in any repository, and every PR a session's branch has. GitHub is read every 30 s while this page is open; a column head sorts.</p></div>
    <div class="aside muted">Updated ${prUpdated(st)}</div></div>
    <div class="toolbar">${chips("fleet_prs", Object.entries(PR_FILTERS).map(([id, [fn, label]]) => [id, label, open.filter(fn).length]), f)}</div>
    ${repos.length > 1 ? `<div class="chips proj-chips" role="group" aria-label="Repository">${repoChip("all", "All repositories")}${repos.map((r) => repoChip(r, r, open.filter((p) => p.repo === r).length)).join("")}</div>` : ""}
    ${Object.keys(groups).sort().map((r) => `<div class="card table-wrap" data-src="github"><div class="card-head"><h3 class="mono">${esc(r)}</h3><span class="aside">${groups[r].length} open</span></div>${prTable(st, groups[r])}</div>`).join("")
      || '<div class="card"><div class="empty-chart">No open pull request matches.</div></div>'}
    <details class="card table-wrap" id="prs-merged" ${F.mergedOpen ? "open" : ""}><summary class="card-head"><h3>Merged in the last 24 h</h3><span class="aside">${merged.length}</span></summary>
      ${merged.length ? prTable(st, merged) : '<div class="empty-chart">Nothing merged in the last 24 h.</div>'}</details>`;
}
// Every poll re-renders the view: the merged group stays as the reader left it.
document.addEventListener("toggle", (e) => { if (e.target.id === "prs-merged") F.mergedOpen = e.target.open; }, true);

const SKILL_FLAG = {
  unused_30d: ["unused 30 d", "warn", "Not invoked in 30 days: rewrite the description, merge it into another skill, or retire it (by PR)"],
  slash_only: ["slash only", "accent", "Only ever typed as /name: the description does not make it fire on its own. Skills with disable-model-invocation: true are never flagged"],
  retire_candidate: ["retire?", "warn", "No use for three 30-day windows, with misses or an overlapping trigger phrase as evidence: merge it or retire it (by PR)"],
  misses: ["misses", "bad", "Prompts carried one of its quoted trigger phrases and it did not fire (heuristic)"],
};
// What was missed, how often and where to look, for the misses chip and the Misses cell.
const missTip = (r) => [`${r.misses_30d} prompts in 30 d carried a trigger phrase while ${r.name} did not fire`
  + (r.miss_sessions_30d ? `, in ${r.miss_sessions_30d} sessions` : ""),
  ...(r.miss_phrases || []).map(([p, n]) => `  "${p}" ×${n}`),
  r.last_miss ? `last ${r.last_miss.slice(0, 16).replace("T", " ")}` : "",
  `list them: skill_usage.py misses ${r.name}`].filter(Boolean).join("\n");
const flagChip = (r, f) => f === "misses"
  ? `<span title="${esc(missTip(r))}">${tag(`missed ${r.misses_30d}×${r.miss_phrases?.length ? ` · “${r.miss_phrases[0][0]}”` : ""}`, "bad")}</span>`
  : `<span title="${esc((SKILL_FLAG[f] || [])[2] || f)}">${tag((SKILL_FLAG[f] || [f])[0], (SKILL_FLAG[f] || [])[1] || "")}</span>`;
const SKILL_FILTERS = {
  all: [() => true, "All"],
  flagged: [(r) => r.shown.length, "Flagged"],
  unused_30d: [(r) => r.shown.includes("unused_30d"), "Unused 30 d"],
  slash_only: [(r) => r.shown.includes("slash_only"), "Slash only"],
  misses: [(r) => r.shown.includes("misses"), "Misses"],
};
const SKILL_SORT = {
  name: (r) => r.name, d7: (r) => r.uses_7d, d30: (r) => r.uses_30d, mix: (r) => r.auto_30d + r.chained_30d,
  last: (r) => ago(r.last_used), reach: (r) => r.sessions_30d, misses: (r) => r.misses_30d, flags: (r) => r.shown.length,
};
async function pollSkills() {
  // A report is kept for a minute; any error, including the 503 before the first collect, is retried every 5 s.
  if (F.skillsBusy || (F.skills && Date.now() - F.skillsAt < (F.skills.error ? 5e3 : 60e3))) return;
  F.skillsBusy = true;
  try {
    const r = await fetch("/api/fleet/skills", { cache: "no-store" });
    F.skills = r.ok ? await r.json() : { error: r.status === 503 ? "The first skill usage collect has not finished." : `HTTP ${r.status}` };
  } catch (e) { F.skills = { error: String(e.message || e) }; }
  F.skillsAt = Date.now(); F.skillsBusy = false;
  if (F.skills.error) setTimeout(() => { if (S.section === "skills") renderView(); }, 5e3);
  if (S.section === "skills") renderView();
}
const skillMix = (r) => {
  const n = r.uses_30d || 1, seg = (v, cls, label) => v ? `<span class="${cls}" style="flex:${v / n}" title="${v} ${label}"></span>` : "";
  return `<span class="mix" aria-label="${r.auto_30d} auto, ${r.chained_30d} chained, ${r.slash_30d} slash">${seg(r.auto_30d, "m-auto", "auto")}${seg(r.chained_30d, "m-chained", "loaded by another skill")}${seg(r.slash_30d, "m-slash", "slash")}</span>
    <span class="mono dim">${r.auto_30d}·${r.chained_30d}·${r.slash_30d}</span>`;
};
function skillTable(list) {
  const head = [["name", "Skill"], ["d7", "7 d", "num"], ["d30", "30 d", "num"], ["mix", "auto · chained · slash", "hide-sm"], ["last", "Last used"],
    ["reach", "Sessions · repos", "num hide-md"], ["misses", "Misses", "num"], ["flags", "Flags"]];
  const rows = sorted("fleet_skills", list, SKILL_SORT).map((r) => `<tr>
      <td class="mono">${esc(r.name)}</td><td class="num">${r.uses_7d || '<span class="muted">0</span>'}</td><td class="num">${r.uses_30d || '<span class="muted">0</span>'}</td>
      <td class="hide-sm">${r.uses_30d ? skillMix(r) : '<span class="muted">—</span>'}</td>
      <td class="muted">${r.last_used ? relSpan(r.last_used) : "never"}</td>
      <td class="num hide-md dim">${r.sessions_30d} · ${r.repos_30d}</td>
      <td class="num">${r.misses_30d ? `<span class="${r.shown.includes("misses") ? "tone-bad" : "dim"}" title="${esc(missTip(r))}">${r.misses_30d}</span>` : '<span class="muted">0</span>'}</td>
      <td>${r.shown.map((f) => flagChip(r, f)).join(" ")}${r.flags.length > r.shown.length ? `<span class="muted" title="${esc(r.flags.join(", "))}: not editable here">${r.flags.length - r.shown.length} hidden</span>` : ""}</td></tr>`).join("");
  return `<table><thead><tr>${head.map(([c, l, cls]) => th("fleet_skills", c, l, cls)).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}
function fleetSkills() {
  pollSkills();
  const rep = F.skills;
  const headHtml = `<div class="view-head"><div><h2>Skills</h2><p>How often each installed skill ran in local Claude Code transcripts, and how it was triggered: on its own (auto), loaded by another skill (chained), or typed as /name (slash). Misses are prompts that carried a quoted trigger phrase from the skill's description while it did not fire: a heuristic, flagged past 3 in 30 days when they are at least a fifth of the prompts that carried the phrase. Hover a miss for the phrase and the command that lists the sessions. Flags on sources not editable here are hidden by default. No prompt text is kept.</p></div>
    ${rep && !rep.error ? `<div class="aside muted">Collected ${relSpan(rep.generated_at)} · ${rep.files} transcripts</div>` : ""}</div>`;
  if (!rep) return headHtml + '<div class="loading">Loading…</div>';
  if (rep.error) return headHtml + `<div class="card"><div class="empty-chart">${esc(rep.error)}</div></div>`;
  const editable = new Set(rep.editable || []);
  // A flag on a source this machine cannot edit has no action behind it: hidden unless "all sources" is picked.
  const scope = S.filters.fleet_skills_flags === "all" ? "all" : "editable";
  const all = (rep.skills || []).map((r) => ({ ...r, shown: scope === "all" || editable.has(r.source) ? r.flags : [] }));
  const f = SKILL_FILTERS[S.filters.fleet_skills] ? S.filters.fleet_skills : "all";
  const srcs = (rep.sources || []).map((x) => x.source), src = srcs.includes(S.filters.fleet_skills_src) ? S.filters.fleet_skills_src : "all";
  const shown = all.filter((r) => SKILL_FILTERS[f][0](r) && (src === "all" || r.source === src));
  const srcChip = (v, label, n) => `<button class="chip" type="button" data-group="fleet_skills_src" data-val="${esc(v)}" aria-pressed="${v === src}">${esc(label)}${n != null ? `<span class="n">${n}</span>` : ""}</button>`;
  const mine = all.filter((r) => editable.has(r.source));
  const kpis = `<div class="kpis" style="--n:4">
    ${kpi("Skills used in 30 d", `${all.filter((r) => r.uses_30d).length}<small>/${all.length}</small>`, `${all.reduce((a, r) => a + r.uses_30d, 0)} invocations`)}
    ${kpi("Fire on their own", `${all.filter((r) => r.auto_30d + r.chained_30d).length}`, `${all.filter((r) => r.flags.includes("slash_only")).length} slash only`)}
    ${kpi("Flagged, editable", `${mine.filter((r) => r.flags.length).length}<small>/${mine.length}</small>`, `sources ${[...editable].join(", ")}`)}
    ${kpi("Improvement signals", `${rep.signals ? rep.signals.added : "—"}`, rep.signals ? "new this collect, one per skill and flag per week" : "signals off")}</div>`;
  const groups = {};
  shown.forEach((r) => (groups[r.source] ||= []).push(r));
  return headHtml + kpis + `<div class="toolbar">${chips("fleet_skills", Object.entries(SKILL_FILTERS).map(([id, [fn, label]]) => [id, label, all.filter(fn).length]), f)}
    ${chips("fleet_skills_flags", [["editable", "Flags on editable sources"], ["all", "Flags on all sources"]], scope)}</div>
    ${srcs.length > 1 ? `<div class="chips proj-chips" role="group" aria-label="Source">${srcChip("all", "All sources")}${(rep.sources || []).map((x) => srcChip(x.source, x.source, x.skills)).join("")}</div>` : ""}
    ${srcs.filter((x) => groups[x]).map((x) => `<div class="card table-wrap"><div class="card-head"><h3 class="mono">${esc(x)}</h3><span class="aside">${editable.has(x) ? tag("editable", "accent", "") + " " : ""}${groups[x].length} skills</span></div>${skillTable(groups[x])}</div>`).join("")
      || '<div class="card"><div class="empty-chart">No skill matches.</div></div>'}`;
}

// Token use comes from /api/fleet/tokens (token_usage.py), refreshed by the server every 15 minutes.
const tokN = (n) => n == null ? "—" : n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : `${Math.round(n)}`;
const tokTrend = (t) => t == null ? '<span class="muted">new</span>' : `<span class="${t >= 0.5 ? "toned tone-bad" : t <= -0.3 ? "toned tone-good" : "dim"}">${t >= 0 ? "+" : ""}${Math.round(t * 100)}%</span>`;
function tokTable(rows, label, hot) {
  const body = rows.slice(0, 12).map((r) => `<tr><td class="mono">${hot.has(r.key) ? tag("hot", "bad", "") + " " : ""}${esc(r.key)}</td>
    <td class="num">${tokN(r.weighted)}</td><td class="num hide-sm dim">${tokN(r.tokens)}</td><td class="num">${(r.share * 100).toFixed(1)}%</td><td class="num">${tokTrend(r.trend)}</td></tr>`).join("");
  return `<div class="card table-wrap"><div class="card-head"><h3>${esc(label)}</h3><span class="aside">${rows.length}</span></div>
    <table><thead><tr><th>Key</th><th class="num">Weighted</th><th class="num hide-sm">Tokens</th><th class="num">Share</th><th class="num">vs prior</th></tr></thead><tbody>${body || '<tr><td colspan="5" class="muted">None in this window.</td></tr>'}</tbody></table></div>`;
}
function fleetTokens() {
  pollJson("tokens", "/api/fleet/tokens", 60e3);
  const rep = F.tokens;
  const head = `<div class="view-head"><div><h2>Tokens</h2><p>API token use in local Claude Code transcripts, by who spent it (automation, role, subagent, interactive) and by the skill in effect, over the last ${rep && rep.days ? rep.days : 7} UTC days against the days before. Weighted is in input-token equivalents: a cache read counts 0.1, a cache write 1.25 or 2, output 5. Read incrementally by token_usage.py; no prompt or message text is kept and nothing here calls a model. Static sizes next to a warning come from scripts/skill_tokens.py.</p></div>
    ${rep && !rep.error ? `<div class="aside muted">${esc(rep.window[0])} – ${esc(rep.window[1])} · collected ${relSpan(rep.collected_at)}</div>` : ""}</div>`;
  if (!rep) return head + '<div class="loading">Loading…</div>';
  if (rep.error) return head + `<div class="card"><div class="empty-chart">${esc(rep.error === "HTTP 503" ? "The first token usage collect has not finished." : rep.error)}</div></div>`;
  const t = rep.totals, hot = new Set(rep.warnings.map((w) => w.key)), f = t.fields || {};
  const cacheIn = (f.cache_read || 0) / ((f.cache_read || 0) + (f.input || 0) + (f.cache_5m || 0) + (f.cache_1h || 0) || 1);
  const kpis = `<div class="kpis" style="--n:4">
    ${kpi("Weighted tokens", tokN(t.weighted), `${tokN(t.tokens)} raw · ${t.calls.toLocaleString()} API calls`)}
    ${kpi("Cache read share", `${(cacheIn * 100).toFixed(0)}%`, "of input tokens")}
    ${kpi("Warnings", `${rep.warnings.length}`, rep.warnings.length ? "marked hot below" : "nothing over its threshold")}
    ${kpi("Unclassified", `${(rep.unclassified.who * 100).toFixed(1)}%`, `origin unknown ${(rep.unclassified.origin * 100).toFixed(1)}%`)}</div>`;
  const warn = rep.warnings.map((w) => `<li>${tag(w.kind, "bad", "")} <span class="mono">${esc(w.text)}</span>${w.static ? ` <span class="muted">static: ${esc(w.static.skill)} listing ${w.static.listing_tokens} tokens a turn, SKILL.md ${w.static.skill_md_tokens.toLocaleString()} a load</span>` : ""}</li>`).join("");
  const autos = rep.automations.map((a) => `<tr><td class="mono">${hot.has(a.key) ? tag("hot", "bad", "") + " " : ""}${esc(a.key)}</td><td class="num">${a.rounds}</td>
    <td class="num ${a.empty_recent >= a.recent / 2 && a.recent ? "toned tone-warn" : "dim"}">${a.empty_recent}/${a.recent}</td><td class="num">${tokN(a.weighted_per_round)}</td></tr>`).join("");
  const hooks = rep.hooks.map((h) => `<tr><td class="mono">${esc(h.name)}</td><td class="num">${h.count}</td><td class="num">${h.tokens.toLocaleString()}</td></tr>`).join("");
  return head + kpis + (warn ? `<div class="card"><div class="card-head"><h3>Using the most</h3><span class="aside">${rep.warnings.length}</span></div><ul class="rows">${warn}</ul></div>` : "")
    + `<div class="grid">${tokTable(rep.who, "Who", hot)}${tokTable(rep.skill, "Skill in effect", hot)}</div>
    <div class="grid">${tokTable(rep.origin, "Turn origin", hot)}
      <div class="card table-wrap"><div class="card-head"><h3>Automation rounds</h3><span class="aside">empty = nothing sent, commented, merged or pushed</span></div>
        <table><thead><tr><th>Automation</th><th class="num">Rounds</th><th class="num">Empty, recent</th><th class="num">Per round</th></tr></thead><tbody>${autos || '<tr><td colspan="4" class="muted">No automation sessions matched.</td></tr>'}</tbody></table>
        <div class="card-head"><h3>Hook injections</h3><span class="aside">count × approximate size</span></div>
        <table><thead><tr><th>Hook</th><th class="num">Count</th><th class="num">Tokens</th></tr></thead><tbody>${hooks || '<tr><td colspan="3" class="muted">No injected text in this window.</td></tr>'}</tbody></table></div></div>`;
}

// Data flow and skill changes come from /api/fleet/flow and /api/fleet/changes (flow.py), each cached by the server.
async function pollJson(key, url, keepMs, view = key) {
  const at = F[key + "At"] || 0;
  if (F[key + "Busy"] || (F[key] && Date.now() - at < (F[key].error ? 5e3 : keepMs))) return;
  F[key + "Busy"] = true;
  try {
    const r = await fetch(url, { cache: "no-store" });
    F[key] = r.ok ? await r.json() : { error: `HTTP ${r.status}` };
  } catch (e) { F[key] = { error: String(e.message || e) }; }
  F[key + "At"] = Date.now(); F[key + "Busy"] = false;
  if (S.section === view) renderView();
}
const isoOf = (sec) => sec ? new Date(sec * 1000).toISOString() : null;
const bytes = (n) => n == null ? "" : n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KB` : `${(n / (1 << 20)).toFixed(1)} MB`;
// A store is present, off (its feature's config key is missing), not yet (never written) or error; outside sources
// (Orca, GitHub, transcripts) are read, never written here.
const STORE_STATE = { present: "present", off: "off: config key missing", "not yet": "not yet written", error: "error", outside: "outside source" };
const storeMeta = (s) => s.state === "outside" ? "outside" : s.state === "error" ? `error: ${s.error || ""}`
  : s.state !== "present" ? STORE_STATE[s.state] : [s.age != null ? rel(isoOf(s.mtime)) : "", s.lines != null ? `${s.lines} lines` : "",
    s.entries != null ? `${s.entries} files` : bytes(s.size)].filter(Boolean).join(" · ");
const storeTone = (s) => s.state === "error" ? "bad" : s.state === "outside" ? "accent" : s.state !== "present" ? "" : s.stale ? "warn" : "good";
const factList = (s) => Object.entries(s.facts).map(([f, r]) => `${f} (${r})`).join(", ");

function flowGraph(rep) {
  const RH = 32, W = 1080, X = [8, 310, 780], BW = [250, 400, 290];
  const stores = rep.stores.filter((s) => s.drawn), ys = {};
  stores.forEach((s, i) => { ys[s.id] = 30 + i * RH; });
  const H = 30 + stores.length * RH;
  // A producer or consumer sits at the mean height of its stores, then is pushed down so no two overlap.
  const place = (nodes, key) => {
    let next = 30;
    return nodes.map((n) => {
      const mine = stores.filter((s) => s[key].includes(n.id));
      return { ...n, mine, y: mine.length ? mine.reduce((a, s) => a + ys[s.id], 0) / mine.length : null };
    }).filter((n) => n.y != null).sort((a, b) => a.y - b.y).map((n) => { n.y = Math.max(n.y, next); next = n.y + RH; return n; });
  };
  const prods = place(rep.producers, "writers"), cons = place(rep.consumers, "readers");
  const out = [];
  const curve = (x0, y0, x1, y1, cls) => `<path class="edge ${cls}" d="M${x0} ${y0} C ${x0 + 50} ${y0}, ${x1 - 50} ${y1}, ${x1} ${y1}"/>`;
  prods.forEach((p) => p.mine.forEach((s) => out.push(curve(X[0] + BW[0], p.y + 13, X[1], ys[s.id] + 13, `tone-${storeTone(s)}`))));
  cons.forEach((c) => c.mine.forEach((s) => out.push(curve(X[1] + BW[1], ys[s.id] + 13, X[2], c.y + 13, s.state === "present" || s.state === "outside" ? "" : "req"))));
  const box = (x, y, w, label, sub, tone, tip, dashed) => `<g class="fl-node${dashed ? " missing" : ""}"><rect class="box" x="${x}" y="${y}" width="${w}" height="26" rx="6"/>
    ${tone ? `<rect class="bar tone-${tone}" x="${x}" y="${y}" width="3" height="26" rx="1.5"/>` : ""}
    <text x="${x + 10}" y="${y + 17}">${esc(clip(label, sub ? 28 : 40))}${sub ? ` <tspan class="muted">${esc(clip(sub, 30))}</tspan>` : ""}</text><title>${esc(tip)}</title></g>`;
  prods.forEach((p) => out.push(box(X[0], p.y, BW[0], p.label, "", "", p.label)));
  stores.forEach((s) => out.push(box(X[1], ys[s.id], BW[1], s.name, storeMeta(s), storeTone(s),
    [s.path, storeMeta(s), factList(s), s.stale ? `stale: older than ${s.stale_after} s` : "", s.last ? `last row: ${s.last}` : "",
      s.feature ? `turned on by ${s.feature}` : "", s.rule || ""].filter(Boolean).join("\n"), s.state !== "present" && s.state !== "outside")));
  const starvedTip = (c) => `${c.label}: starved, ${STORE_STATE[c.state]}\n` + Object.entries(c.inputs).map(([k, v]) => `${STORE_STATE[k] || k}: ${v.join(", ")}`).join("\n");
  cons.forEach((c) => out.push(box(X[2], c.y, BW[2], c.label, c.starved ? c.state : "", c.state === "error" ? "bad" : c.starved ? "warn" : "",
    c.starved ? starvedTip(c) : c.rule || c.label, c.starved)));
  const h = Math.max(H, ...prods.map((p) => p.y + RH), ...cons.map((c) => c.y + RH));
  return `<div class="relgraph"><svg viewBox="0 0 ${W} ${h}" style="min-width:860px;max-width:${W}px" role="img" aria-label="Producers, stores and consumers">
    <text class="muted" x="${X[0]}" y="16">PRODUCERS</text><text class="muted" x="${X[1]}" y="16">STORES</text><text class="muted" x="${X[2]}" y="16">CONSUMERS</text>${out.join("")}</svg></div>
    <div class="legend" style="padding:0 var(--s4) var(--s3)"><span><i class="key tone-good" style="background:var(--tone)"></i>present</span><span><i class="key tone-warn" style="background:var(--tone)"></i>stale: older than its interval</span><span><i class="key tone-bad" style="background:var(--tone)"></i>error</span><span><i class="key tone-accent" style="background:var(--tone)"></i>outside source</span><span><i class="key req"></i>off or not yet written</span><span>hover a box for its path, facts and rule</span></div>`;
}

function fleetFlow() {
  pollJson("flow", "/api/fleet/flow", 30e3);
  const rep = F.flow;
  const head = `<div class="view-head"><div><h2>Data flow</h2><p>Every fact agent-skills keeps, the store that holds it and who writes and reads it, drawn from the registry in registry.py (the same one its tests check the code against). The values are stat, line counts kept incrementally, and the last row of each log. Nothing here calls a model.</p></div>
    ${rep && !rep.error ? `<div class="aside muted">Collected ${relSpan(rep.generated_at)}</div>` : ""}</div>`;
  if (!rep) return head + '<div class="loading">Loading…</div>';
  if (rep.error) return head + `<div class="card"><div class="empty-chart">${esc(rep.error)}</div></div>`;
  const local = rep.stores.filter((s) => s.state !== "outside"), by = (st) => local.filter((s) => s.state === st);
  const stale = rep.stores.filter((s) => s.stale), starved = rep.consumers.filter((c) => c.starved);
  const reports = rep.stores.find((s) => s.id === "reports");
  const absent = ["off", "not yet", "error"].map((st) => by(st).length ? `${by(st).length} ${STORE_STATE[st]}` : "").filter(Boolean).join(" · ");
  const kpis = `<div class="kpis" style="--n:5">
    ${kpi("Stores", `${by("present").length}<small>/${local.length}</small>`, absent || "all present")}
    ${kpi("Stale", `${stale.length}`, stale.map((s) => s.name).join(", ") || "every loop on time")}
    ${kpi("Starved consumers", `${starved.length}`, starved.map((c) => `${c.label} (${c.state})`).join(", ") || "every consumer has input")}
    ${kpi("Not registered", `${rep.unread.length}`, "files under the state roots no store names")}
    ${kpi("Two canonical stores", `${rep.conflicts.length}`, rep.conflicts.map((c) => c.fact).join(", ") || "one per fact")}</div>`;
  const rows = rep.stores.map((s) => `<tr><td class="mono">${esc(s.path)}</td><td>${tag(s.state, storeTone(s))}</td><td class="mono">${esc(factList(s))}</td>
    <td class="mono">${esc(s.writer_fns.join(", ") || "—")}</td><td>${esc(s.readers.join(", "))}</td><td>${esc(s.rule || "")}</td></tr>`).join("");
  const brief = rep.consumers.find((c) => c.id === "brief");
  return head + kpis + `<div class="card">${flowGraph(rep)}</div>
    ${brief ? `<div class="card"><div class="card-head"><h3>How the Brief's rows are derived</h3></div><p style="padding:0 var(--s4) var(--s3)">${esc(brief.rule)}</p></div>` : ""}
    <div class="grid">
      <div class="card"><div class="card-head"><h3>Two canonical stores</h3><span class="aside">${rep.conflicts.length}</span></div>
        <ul class="rows">${rep.conflicts.map((c) => `<li><span class="mono">${esc(c.fact)}</span>: ${esc(c.stores.join(", "))}${c.known ? ` <span class="muted">— ${esc(c.known)}</span>` : ""}</li>`).join("") || '<li class="muted">Every fact has one canonical store.</li>'}</ul></div>
      <div class="card"><div class="card-head"><h3>Not registered</h3><span class="aside">${rep.unread.length}</span></div>
        <ul class="rows">${rep.unread.map((p) => `<li class="mono">${esc(p)}</li>`).join("") || '<li class="muted">Every file is in the registry.</li>'}
          ${(rep.scan_problems || []).map((p) => `<li class="muted">not checked: <span class="mono">${esc(p)}</span></li>`).join("")}</ul></div>
      <div class="card"><div class="card-head"><h3>Report files</h3><span class="aside">${reports ? reports.entries : 0}</span></div>
        <ul class="rows">${reports && reports.files.length ? reports.files.map((f) => `<li><span class="mono">${esc(f.path)}</span> <span class="muted">${f.age != null ? rel(new Date(Date.now() - f.age * 1000).toISOString()) : "missing"}</span></li>`).join("")
          : '<li class="muted">No session names a brief with a REPORT line.</li>'}</ul></div></div>
    <div class="card table-wrap"><div class="card-head"><h3>Registry</h3><span class="aside">${rep.stores.length} stores</span></div>
      <table><thead><tr><th>Store</th><th>State</th><th>Facts (role)</th><th>Writers</th><th>Readers</th><th>What it holds</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

const CHANGE_STATUS = { proposed: "accent", applied: "good", "in PR": "warn", merged: "good" };
const CHANGE_FILTERS = ["all", "proposed", "in PR", "applied", "merged"];
function fleetChanges() {
  pollJson("changes", "/api/fleet/changes", 60e3);
  const rep = F.changes;
  const head = `<div class="view-head"><div><h2>Skill changes</h2><p>What agent-skills changed about itself: improvement signals from skill usage (proposed), open PRs (in PR), commits on this checkout pushed straight to main (applied) or merged by PR (merged). From git log, the collector's PR list and the signals ledger only.</p></div>
    ${rep && !rep.error ? `<div class="aside muted">Last ${rep.days} days · ${relSpan(rep.generated_at)}</div>` : ""}</div>`;
  if (!rep) return head + '<div class="loading">Loading…</div>';
  if (rep.error) return head + `<div class="card"><div class="empty-chart">${esc(rep.error)}</div></div>`;
  const f = CHANGE_FILTERS.includes(S.filters.fleet_changes) ? S.filters.fleet_changes : "all";
  const skill = rep.skills.some((s) => s.skill === S.filters.fleet_changes_skill) ? S.filters.fleet_changes_skill : "all";
  const count = (st) => rep.rows.filter((r) => r.status === st).length;
  const rows = rep.rows.filter((r) => (f === "all" || r.status === f) && (skill === "all" || r.skills.includes(skill)));
  const les = rep.lessons || {};
  const kpis = `<div class="kpis" style="--n:4">
    ${kpi("Merged", `${count("merged")}`, `${count("applied")} pushed straight to main`)}
    ${kpi("In PR", `${count("in PR")}`, rep.repo ? esc(rep.repo) : "origin unknown")}
    ${kpi("Proposed", `${count("proposed")}`, "skill usage signals")}
    ${kpi("Lessons ledger", les.readable ? `${les.lines}` : "—", esc(les.readable ? les.path : les.note || ""))}</div>`;
  const skillChip = (v, label, n) => `<button class="chip" type="button" data-group="fleet_changes_skill" data-val="${esc(v)}" aria-pressed="${v === skill}">${esc(label)}${n != null ? `<span class="n">${n}</span>` : ""}</button>`;
  const per = rep.skills.slice(0, 14).map((s) => `<tr><td class="mono">${skillChip(s.skill, s.skill)}</td><td class="num">${s.merged || '<span class="muted">0</span>'}</td><td class="num">${s.applied || '<span class="muted">0</span>'}</td>
    <td class="num">${s["in PR"] || '<span class="muted">0</span>'}</td><td class="num">${s.proposed || '<span class="muted">0</span>'}</td><td class="muted">${relSpan(s.last)}</td></tr>`).join("");
  const basis = (r) => [r.pr ? link(r.url, `#${r.pr}`) : "", r.sha ? `<span class="mono dim">${esc(r.sha.slice(0, 7))}</span>` : "",
    ...(r.lessons || []).map((l) => `<span class="mono">${esc(l)}</span>`), r.basis ? `<span class="mono dim" title="${esc(r.basis)}">${esc(clip(r.basis, 28))}</span>` : ""].filter(Boolean).join(" ");
  const list = rows.slice(0, 200).map((r) => `<tr><td class="muted when">${esc((r.at || "").slice(0, 10))}</td>
    <td class="mono">${esc(r.skills.join(", "))}${r.also?.length ? ` <span class="muted" title="${esc(r.also.join(", "))}">+${r.also.length}</span>` : ""}</td>
    <td>${esc(r.change || "")}</td><td>${basis(r)}</td><td>${tag(r.status, CHANGE_STATUS[r.status] || "")}</td></tr>`).join("");
  return head + kpis + `<div class="toolbar">${chips("fleet_changes", CHANGE_FILTERS.map((s) => [s, s === "all" ? "All" : s, s === "all" ? rep.rows.length : count(s)]), f)}
      ${skill !== "all" ? `<div class="chips">${skillChip("all", `skill: ${skill} ×`)}</div>` : ""}</div>
    <div class="card table-wrap"><div class="card-head"><h3>Per skill</h3><span class="aside">${rep.skills.length} skills · pick one to filter</span></div>
      <table><thead><tr><th>Skill</th><th class="num">Merged</th><th class="num">Applied</th><th class="num">In PR</th><th class="num">Proposed</th><th>Last</th></tr></thead><tbody>${per}</tbody></table></div>
    <div class="card table-wrap"><div class="card-head"><h3>Changes</h3><span class="aside">${rows.length}${rows.length > 200 ? ", newest 200 shown" : ""}</span></div>
      <table><thead><tr><th>Date</th><th>Skill</th><th>Change</th><th>Basis</th><th>Status</th></tr></thead><tbody>${list || '<tr><td colspan="5" class="muted">No change matches.</td></tr>'}</tbody></table></div>`;
}

// The periodic report as `orch brief render --periodic` prints it (via /api/fleet/brief), re-rendered on every poll
// while this view is open. The renderer owns rows, order, icons and bold; this only turns its Markdown into HTML.
const BRIEF_FED = ["brief", "stages", "todo", "session", "sessions"];  // the Stages and To-do pages read the same answer's `stages` and `open`, Sessions its marks
async function pollBrief() {
  if (F.briefBusy) return;
  F.briefBusy = true;
  try {
    const r = await fetch("/api/fleet/brief", { cache: "no-store", headers: F.briefEtag ? { "If-None-Match": F.briefEtag } : {} });
    if (r.ok || r.status === 304) F.briefAt = new Date().toISOString();
    if (r.status !== 304) {
      F.brief = r.ok ? await r.json() : { message: `HTTP ${r.status}` };
      F.briefEtag = r.ok ? r.headers.get("ETag") : null;
      if (isFleet() && BRIEF_FED.includes(S.section)) renderView();
      if (isFleet()) fleetSidebar();
    }
  } catch (e) { F.brief = { message: String(e.message || e) }; F.briefEtag = null; }
  F.briefBusy = false;
}
// ❔ is a pale outline glyph in most emoji fonts: drawn as a toned mark instead.
const briefInline = (s) => esc(s).replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => link(url, text))
  .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, '<span class="mono">$1</span>')
  .replace(/❔/g, '<span class="qmark" role="img" aria-label="확인 필요">?</span>');
// A pointer line ends with a page of this dashboard, as its host's 127.0.0.1 URL: linked by its hash, so it also opens
// from another device.
const DASH_URL = /(https?:\/\/[^\s<]+\/(#\/fleet\/[\w/-]+))$/;
// The renderer cuts a cell to fit 78 terminal columns as `head…[n]` and puts the whole text in `- [n] …` under the
// table. The page has the width: each cut cell gets its whole text back (its └ and bold kept) and the list goes.
const uncut = (cell, notes) => cell.replace(/^((?:[└ㄴ] )?(?:\*\*)?)(.*)…\[(\d+)\](\**)$/, (m, pre, head, n, post) => notes[n] != null ? pre + notes[n] + post : m);
// The sections around the tables, as `<emoji> **name**[ — first item]` then `- item` (or `n. item`) lines, become tables
// too. split: what parts a line falls into, in order: [separator, column name] — the text before the first separator
// is the first column. A line without the separator leaves the rest empty.
const BRIEF_SECTIONS = {
  "카드 없는 일": [["", "항목"], [" — ", "상황"]],
  "다른 사람 대기": [["", "누구 · 무엇 · 언제"], [" → ", "항목"], [" · 다음: ", "다음"]],
  "결정 필요": [["", "결정"], [" → 추천: ", "추천"]],
  "지금 오너가 할 일": [["", "할 일"], [" → ", "항목"]],
  "보류": [["", "언제 · 조건"], [" → ", "할 일"]],
};
// Which list_rows list each section's lines come from (board.py render).
const LIST_OF = { "카드 없는 일": "moving", "다른 사람 대기": "waiting", "지금 오너가 할 일": "owner", "보류": "held" };
const STATUS_MARK = /^(└ )?(🔄|🙋|⏳|⏸️|📅|⏰|✅|❔|❓|🔴|📝|\d+\.)\s+/u;  // a row under a lead keeps its └ in the item
// A row the orchestrator or a lead wrote ends with who wrote it; the old role name reads as today's.
const roleNow = (s) => s.replace(/(^|· )coordinator$/, "$1orchestrator").replace(/(^|· )코디네이터$/, "$1오케스트레이터");
function sectionTable(name, items, ids = []) {
  const split = BRIEF_SECTIONS[name] || [["", "항목"]], acts = ids.some(Boolean);
  const rows = items.map((it) => {
    const t = roleNow(it), m = t.match(STATUS_MARK), rest = m ? (m[1] || "") + t.slice(m[0].length) : t;
    const cells = [];
    split.reduce((left, [sep], i) => {
      const next = split[i + 1], at = next ? left.indexOf(next[0]) : -1;
      cells.push(at < 0 ? left : left.slice(0, at));
      return at < 0 ? "" : left.slice(at + next[0].length);
    }, rest);
    return [m ? m[2] : "", ...cells];
  });
  const marked = rows.some((r) => r[0]);  // a section with no mark on any line gets no mark column
  return `<div class="table-wrap"><table class="brief-table bsec"><thead><tr>${marked ? "<th></th>" : ""}${split.map(([, h]) => `<th>${esc(h)}</th>`).join("")}${acts ? "<th></th>" : ""}</tr></thead>
    <tbody>${rows.map((r, k) => `<tr>${(marked ? r : r.slice(1)).map((c, i) => `<td${marked && !i ? ' class="bmark"' : ""}>${briefInline(c)}</td>`).join("")}${acts ? `<td class="bact">${ids[k] ? doneBtn(ids[k]) : ""}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}
// A repository table's name cell as the reply's table_rows labels it: its whole text, without the └ and the bold.
const rowLabel = (cell, notes) => uncut(cell, notes).replace(/^[└ㄴ] /, "").replace(/^\*\*(.*)\*\*$/, "$1");
// tableRows (the reply's table_rows): per repository table, {table, rows}, each row {id, label, ack, done, session}.
// A row's buttons go to the table row with its label (render() makes labels unique in a report); a row no entry names
// gets none. listRows (list_rows): the items of the lists around the tables; an item with no table row of its own gets
// its Done in the list. placed collects the Ack ids that got a button, closed the Done ids, so no card lists them again.
function briefHtml(text, tableRows = [], placed = new Set(), listRows = {}, closed = new Set()) {
  const out = [], lines = text.split("\n");
  const rowIds = new Set(tableRows.flatMap((x) => (x.rows || []).map((r) => r.id)));
  const listed = Object.entries(listRows || {}).flatMap(([list, es]) => (es || []).map((e) => ({ ...e, list })))
    .filter((e) => e && e.id && e.text && !rowIds.has(e.id));
  let i = 0, t = 0;
  const blank = () => { while (i < lines.length && !lines[i].trim()) i++; };
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("|")) {
      const rows = [];
      for (; i < lines.length && lines[i].startsWith("|"); i++) {
        if (!/^\|[-| ]+\|$/.test(lines[i])) rows.push(lines[i].slice(1, -1).split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|")));
      }
      const at = i, notes = {};
      blank();
      for (let m; i < lines.length && (m = lines[i].match(/^- \[(\d+)\] (.*)$/)); i++) notes[m[1]] = m[2];
      if (!Object.keys(notes).length) i = at;
      const [head, ...body] = rows;
      // A repository table (`| | name | PR | [progress |] next | session |`, progress only where a row has a bar) gets
      // one shared set of column widths; a cell too long for its column scrolls sideways within itself instead of being cut.
      const sessAt = head.indexOf("세션"), repo = sessAt >= 0 && !head[0], acts = repo && tableRows[t++];
      // A label that is not one row's, in the reply and in the drawn table both (a lead's name over its sub-tasks in
      // another repository may equal one of them), gets no button: a wrong row's Done is worse than none.
      const tally = (xs) => xs.reduce((m, x) => m.set(x, (m.get(x) || 0) + 1), new Map());
      const labels = body.map((r) => rowLabel(r[1] || "", notes));
      const count = tally(((acts && acts.rows) || []).map((x) => x.label)), drawnCount = tally(labels);
      const byLabel = new Map(((acts && acts.rows) || []).filter((x) => (x.ack || x.done) && count.get(x.label) === 1)
        .map((x) => [x.label, x]));
      const found = labels.map((l) => (drawnCount.get(l) === 1 && byLabel.get(l)) || null);
      const btns = found.some(Boolean) ? found : null;
      (btns || []).forEach((x) => { if (x && x.ack) placed.add(x.ack); else if (x && x.done) closed.add(x.done); });
      const act = (x) => x ? `<td class="bact" data-row-label="${esc(x.label || "")}">${x.ack ? ackBtn({ ack: "ackable", key: x.ack }) : x.done ? doneBtn(x.done) : ""}</td>` : '<td class="bact"></td>';
      out.push(`<div class="table-wrap"><table class="brief-table${repo ? " brepo" : ""}">${repo ? `<colgroup><col class="bmark"><col><col class="bpr">${head.includes("진행") ? '<col class="bprog">' : ""}<col class="bnext"><col class="bsess">${btns ? '<col class="bact">' : ""}</colgroup>` : ""}
        <thead><tr>${head.map((c) => `<th>${briefInline(c)}</th>`).join("")}${btns ? "<th></th>" : ""}</tr></thead>
        <tbody>${body.map((r, k) => `<tr>${r.map((c, n) => repo && n === sessAt && btns && btns[k] ? `<td class="bsess">${sessCell(btns[k].session, c)}</td>`
          : `<td${repo && !n ? ' class="bmark"' : ""}>${repo && n ? `<div class="slide">${briefInline(uncut(c, notes))}</div>` : briefInline(uncut(c, notes))}</td>`).join("")}${btns ? act(btns[k]) : ""}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const sec = line.match(/^(\S+) \*\*(.+?)\*\*(?: — (.*))?$/u);
    if (sec && (BRIEF_SECTIONS[sec[2]] || sec[3] != null)) {
      const items = sec[3] && sec[3] !== "없음" ? [sec[3]] : [];
      i++;
      for (blank(); i < lines.length && /^(- |\d+\. )/.test(lines[i]); i++, blank()) items.push(lines[i].replace(/^- /, ""));
      out.push(`<h4 class="bhead">${briefInline(`${sec[1]} ${sec[2]}`)}${sec[3] === "없음" ? ' <span class="muted">— 없음</span>' : ""}</h4>`);
      // An item's Done goes on the line whose text is exactly the one the renderer wrote for it, in its own list.
      const own = listed.filter((x) => x.list === LIST_OF[sec[2]]);
      const ids = items.map((it) => {
        const hits = own.filter((x) => !closed.has(x.id) && x.text === it);
        if (hits.length !== 1) return null;
        closed.add(hits[0].id);
        return hits[0].id;
      });
      if (items.length) out.push(sectionTable(sec[2], items, ids));
      continue;
    }
    if (line.trim()) out.push(`<p>${briefInline(line).replace(DASH_URL, '<a class="link" href="$2">$1</a>')}</p>`);
    i++;
  }
  return out.join("");
}
// A brief row's session, linked by its Orca worktree id (board.py session_of): never by a terminal handle, which a
// restart changes. The state links to the session page; Orca brings its agent terminal to the front; under them, the
// line it last reported. A row no session runs says 세션 없음 with neither.
const SESSION_TONE = { working: "accent", stalled: "warn", none: "bad" };
function sessCell(s, text) {
  if (!s) return esc(text);
  const word = tag(s.word, SESSION_TONE[s.state] || "", "");
  const open = s.id && s.state !== "none" ? `<button class="chip" type="button" data-open-session="${esc(s.id)}" title="Bring this session's terminal to the front in the host's Orca window">${icon("arrow")}Orca</button>` : "";
  const last = s.last ? `<span class="bsess-last muted" title="${esc(s.last)}">${esc(s.last)}${s.at ? ` · ${relSpan(s.at)}` : ""}</span>` : "";
  return `<div class="bsess-head">${s.id ? `<a href="${sessionHref(s.id)}" title="${esc(`Session ${s.name || ""}`)}">${word}</a>` : word}${open}${last}</div>`;
}
// The server switches only the host's Orca window and cannot tell which device clicked, so the last click's outcome
// stays in a fixed note, with what to type or look for on another device, until it is closed.
function openNote(r) {
  if (!r) return "";
  if (r.busy) return `<span class="muted">Switching ${esc(r.name || "")}…</span>`;
  const msg = r.ok ? `${r.name || "Session"}: switched in the host's Orca window only. On another device:`
    : `${r.name || "Session"}: not switched (${r.reason || "unknown"}).${r.copy ? " Open it yourself:" : ""}`;
  return `<div class="open-note-head"><span class="toned tone-${r.ok ? "warn" : "bad"}">${esc(msg)}</span>
      <button class="icon-btn" type="button" data-open-close title="Close">${icon("x")}</button></div>
    ${r.copy ? `<div class="open-note-copy"><pre class="mono">${esc(r.copy)}</pre><button class="chip" type="button" data-copy="${esc(r.copy)}">${icon("copy")}Copy</button></div>` : ""}
    ${r.warning ? `<div class="sub muted">${esc(r.warning)}${r.result !== undefined ? `: <span class="mono">${esc(JSON.stringify(r.result))}</span>` : ""}</div>` : ""}`;
}
function showOpenNote() {
  let el = document.querySelector("#open-note");
  if (!F.open) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("div");
    el.id = "open-note"; el.className = "card open-note"; el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.innerHTML = openNote(F.open);
}
const sessionRowsOf = (id) => ((F.brief || {}).table_rows || []).flatMap((t) => t.rows || []).filter((x) => x.session && x.session.id === id);
function briefRowsCard(s, st) {
  if (!F.brief) pollBrief();
  const rows = sessionRowsOf(s.id), at = rows.map((x) => x.session.at).filter(Boolean).sort().pop();
  const prOf = (key) => (st.prs || []).find((p) => p.key === key);
  const list = rows.map((x) => {
    const p = x.session.pr && prOf(x.session.pr);
    return `<li><span class="bmark">${esc(x.status || "")}</span><a class="grow ellipsis" href="#/fleet/brief">${esc(x.label)}</a>
      <span class="acts">${x.session.pr ? (p && p.url ? link(p.url, esc(`#${p.number}`)) : `<span class="mono">${esc(x.session.pr)}</span>`) : ""}${tag(x.session.word, SESSION_TONE[x.session.state] || "", "")}</span></li>`;
  }).join("");
  return `<div class="card"><div class="card-head"><h3>Brief rows</h3><span class="aside">${rows.length}${at ? ` · last report ${relSpan(at)}` : ""}</span></div>
    <ul class="rows">${list || `<li class="muted">${F.brief ? "No brief row is linked to this session." : "Loading…"}</li>`}</ul></div>`;
}
async function openSession(t) {
  const id = t.dataset.openSession, name = ((S.state || {}).sessions || []).find((s) => s.id === id)?.name || id;
  const seq = (F.openSeq = (F.openSeq || 0) + 1);
  F.open = { busy: true, name };
  showOpenNote();
  let out;
  try { out = await fleetPost("/api/fleet/open", { session: id }); } catch (e) { out = { ok: false, reason: String(e.message || e) }; }
  if (seq !== F.openSeq) return;  // a later click's note is showing; this reply is not the last click's
  F.open = { ...out, name };
  showOpenNote();
}

function fleetBrief() {
  if (!F.brief) pollBrief();
  const b = F.brief;
  const headHtml = `<div class="view-head"><div><h2>Brief</h2><p>The periodic report as <span class="mono">orch brief render --periodic</span> builds it from the board, the sessions, the PR list and open decisions. A lead or card with no board rows shows from its session, with the next step from the report file its brief names; rows written with <span class="mono">orch board set</span> replace those. Bold rows changed since the last report sent.</p></div>
    ${b && b.text ? `<div class="aside muted">${(b.changed || []).length} changed · ${(b.stale || []).length} stale${b.width_ok === false ? ` · ${tag("over 78 columns", "warn", "")}` : ""}${F.briefAt ? ` · updated ${relSpan(F.briefAt)}` : ""}</div>` : ""}</div>`;
  if (!b) return headHtml + '<div class="loading">Loading…</div>';
  const stage = b.stages ? `<a class="card stage-link" href="#/fleet/stages">${icon("flag")}<b>Stages</b><span class="muted">${stageOpen()} in progress</span>${icon("arrow")}</a>` : "";
  if (!b.text) return headHtml + ackNote() + stage + `<div class="card"><div class="empty-chart">${esc(b.message || "Nothing to report.")}</div></div>`;
  // A row with its Ack in the table is not listed again: the card keeps the acked rows, those folded out of the table
  // and those of a table that got no buttons.
  const inTable = new Set(), closable = new Set(), report = briefHtml(b.text, b.table_rows, inTable, b.list_rows, closable);
  const { shown, head } = ackSplit((b.done || []).map((d) => ({ ...d, key: d.id, ack: d.acked ? "acked" : "ackable" })), "finished row");
  const rest = shown.filter((d) => !inTable.has(d.id));
  const finished = rest.length || head ? `<div class="card"><div class="card-head"><h3>Finished rows</h3><span class="aside">${head}</span></div>
    <ul class="rows">${rest.map((d) => `<li class="${d.acked ? "is-acked" : ""}"><span class="grow full">${esc(d.label)}</span><span class="acts muted">${d.at ? relSpan(d.at) : ""}</span>${ackBtn(d)}</li>`).join("")
      || '<li class="muted">Every finished row is in the table above or acked.</li>'}</ul></div>` : "";
  return headHtml + ackNote() + undoNote() + stage + `<div class="card"><div class="brief card-body">${report}</div></div>` + finished + doneCards(b, closable);
}

// Done (tidy.py, `orch board done`): close a row, card or board item that is over, or reopen one. The collector closes
// on evidence by itself after each tick; what it could not settle is marked "check". The server decides what may close.
const DONE_KIND = { row: "row", doing: "in progress", later: "held", do: "to-do", todo: "you, now" };
const doneBtn = (id, undo) => `<button class="chip ack" type="button" data-done="${esc(id)}"${undo ? " data-undo" : ""}
  title="${undo ? "Reopen: show it again (orch board reopen)" : "Done: close it here and in the brief (orch board done)"}">${undo ? "Reopen" : "Done"}</button>`;
// Open items lists what got no Done button in the report above (shown: the ids that did), so each id has one button.
function doneCards(b, shown = new Set()) {
  const rest = (b.open || []).filter((o) => !shown.has(o.id));
  const open = rest.map((o) => `<li>${tag(DONE_KIND[o.kind] || o.kind, o.check ? "warn" : "")}${o.due ? tag("due", "accent", "clock") : ""}<span class="grow full" title="${esc(o.label)}">${esc(o.label)}</span>
    ${o.check ? `<span class="acts muted full" title="${esc(o.check)}">check: ${esc(o.check)}</span>` : ""}${doneBtn(o.id)}</li>`).join("");
  const shut = (b.closed || []).map((c) => `<li class="is-acked">${tag(c.by === "auto" ? "auto" : "you", c.by === "auto" ? "accent" : "")}<span class="grow full" title="${esc(c.label || c.id)}">${esc(c.label || c.id)}</span>
    <span class="acts muted full" title="${esc(c.evidence || "")}">${esc(c.rule || "")}: ${esc(c.evidence || "")} · ${relSpan(c.ts)}</span>${c.reopen ? doneBtn(c.id, true) : ""}</li>`).join("");
  const note = F.doneError ? `<div class="ack-note">${tag("not closed", "warn", "alert")} ${esc(F.doneError)}</div>` : "";
  return `<div class="card"><div class="card-head"><h3>Open items</h3><span class="aside muted">${rest.length}${rest.length < (b.open || []).length ? ` · ${(b.open || []).length - rest.length} with Done above` : ""}</span></div>${note}
    <ul class="rows">${open || `<li class="muted">${(b.open || []).length ? "Every open item has its Done above." : "Nothing open."}</li>`}</ul></div>`
    + (shut ? `<div class="card"><div class="card-head"><h3>Closed in the last 7 days</h3><span class="aside muted">${(b.closed || []).length}</span></div><ul class="rows">${shut}</ul></div>` : "");
}

// The stage table (stages.py table(): rows the fleet tick generates per program, with `orch stage` cells over them),
// part of /api/fleet/brief, so a cell written by the orchestrator or lead shows within one poll. Every cell arrives filled.
const STAGE_MARK = { ok: ["✅", "good", "done · verified"], fail: ["❌", "bad", "not done · failed"], partial: ["⚠️", "warn", "partial · inferred"],
  checking: ["🔄", "accent", "checking"], na: ["–", "", "n/a"] };
const STAGE_DONE_KEEP = 7 * 86400e3;  // stages.py DONE_KEEP_S: a file nobody wrote to since can still hold older rows
// A row stages.counted() counts open stays however old its done_at, so the page shows every row the count names.
const stageShown = (w) => (w.rows || []).filter((r) => r.open || !r.done_at || Date.now() - ms(r.done_at) < STAGE_DONE_KEEP);
// stages.counted() marks each row `open` and counts them as the brief's `단계표 N행` line does: one number for both.
const stageOpen = () => { const w = (F.brief || {}).stages; return w && !w.error ? w.open || 0 : 0; };
// owner/repo#N becomes a link to the URL the server resolved (refs: /pull/ for a known PR, else /issues/, which GitHub
// redirects for a PR); esc() runs first and the pattern holds no markup characters.
const linkRefs = (text, refs = {}) => esc(text).replace(/\b([\w.-]+\/[\w.-]+)#(\d+)\b/g,
  (m, repo, n) => link(refs[m] || `https://github.com/${repo}/issues/${n}`, m));
function stageCell(c, refs) {
  if (!c) return "";
  const [mark, tone, label] = STAGE_MARK[c.mark] || [c.mark, "", c.mark];
  const text = [c.evidence && linkRefs(c.evidence, refs), c.by && `<span class="muted">(${esc(c.by)})</span>`].filter(Boolean).join(" ");
  return `<div class="wcell ${tone ? "tone-" + tone : ""}" title="${esc(c.from ? `${label} · ${c.from}` : label)}"><span class="wmark">${mark}</span>${text ? `<span class="wtext">${text}</span>` : ""}</div>`;
}
// Each program (a group row, or a top-level row of its own) is a block: its open rows always, its folded rows behind
// its own "▸ N folded" toggle (F.stageFold), so a finished program reads as one line instead of a hundred.
// A row's `now` (stages.with_now): the brief's status of the board row its PR links, drawn with the brief's icons.
const NOW_ICON = { moving: "🔄", owner: "🙋", waiting: "⏳", check: "❔", held: "⏸️", blocked: "🔴", done: "✅" };
const STAGE_COLS = [["Work", ""], ["PR", ""], ["Merged", "merge"], ["dev check", "dev"], ["prod check", "prod"]];
function stageSection(w) {
  if (!w) return "";
  if (w.error) return `<div class="card"><div class="empty-chart">${esc(w.error)}</div></div>`;
  const refs = w.refs || {};
  const legend = `<span class="legend">${Object.values(STAGE_MARK).map(([m, tone, l]) => `<span><span class="wmark">${m}</span>${l}</span>`).join("")}</span>`;
  const { shown: all, seen } = ackSplit(stageShown(w).map((r) => ({ ...r, key: `stage:${r.id}` })), "finished row");
  const byId = Object.fromEntries(all.map((r) => [r.id, r]));
  const topOf = (r) => { let g = r; while (g.parent && byId[g.parent]) g = byId[g.parent]; return g.id; };
  // A folded row with an open row under it stays open, as its heading does.
  const lit = new Set();
  all.filter((r) => !r.group && r.open).forEach((r) => { for (let p = r; p && !lit.has(p.id); p = byId[p.parent]) lit.add(p.id); });
  // A group's own rows sit flush under it; each level below them gets one └ step.
  const level = {};
  all.forEach((r) => { const p = byId[r.parent]; level[r.id] = !p || p.group ? 0 : (level[p.id] ?? 0) + 1; });
  const row = (r) => `<tr class="${r.done_at ? "is-done" : ""}">
      <td class="wtitle" data-label="Work" style="--lv:${level[r.id]}">${level[r.id] ? '<span class="muted">└ </span>' : ""}${r.now ? `<span class="wnow" title="Now, as the brief judges its board row">${briefInline(NOW_ICON[r.now] || "")}</span> ` : ""}${linkRefs(r.title_full || r.title, refs)}${r.done_at ? ` <span class="muted">· ${r.stopped ? "stopped" : "done"} ${relSpan(r.done_at)}</span>` : ""} ${ackBtn(r)}</td>
      <td class="mono wpr" data-label="PR">${r.pr ? linkRefs(r.pr, refs) : '<span class="muted">–</span>'}</td>
      ${STAGE_COLS.slice(2).map(([l, c]) => `<td data-label="${l}"${(r.cells || {})[c] ? "" : ' class="wempty"'}>${stageCell((r.cells || {})[c], refs)}</td>`).join("")}</tr>`;
  // A Map keeps the server's order (rowstate.order_tree): an object would sort a numeric id first.
  const blocks = new Map();
  all.forEach((r) => { const id = topOf(r); blocks.has(id) ? blocks.get(id).push(r) : blocks.set(id, [r]); });
  const html = [...blocks].map(([id, members]) => {
    const g = byId[id], work = members.filter((r) => !r.group);
    const done = work.filter((r) => !lit.has(r.id)), open = work.length - done.length, unfolded = F.stageFold.has(id);
    const ackable = done.filter((r) => r.ack === "ackable" && !r.hidden).map((r) => r.key);
    const fold = done.length ? `<button class="chip mini" type="button" data-stage-fold="${esc(id)}" aria-expanded="${unfolded}">${unfolded ? "▾" : "▸"} ${done.length} folded</button>`
      + (unfolded && ackable.length > 1 ? `<button class="chip ack" type="button" data-ack="${esc(ackable.join(" "))}" title="Ack every finished row of this program">Ack all ${ackable.length}</button>` : "") : "";
    const head = g.group ? `<tr class="wgroup ${open ? "" : "is-done"}"><td colspan="5"><div class="wghead"><b>${esc(g.title)}</b>
        <span class="muted">${open} open</span>${fold}${ackBtn(g)}</div></td></tr>`
      : !open ? `<tr class="wgroup is-done"><td colspan="5"><div class="wghead"><b>${linkRefs(g.title, refs)}</b>${fold}</div></td></tr>` : "";
    const shown = members.filter((r) => !r.group && (lit.has(r.id) || unfolded));
    return { open, html: head + shown.map(row).join("") };
  });
  // Open blocks read as `orch stage show` prints them; the rows it leaves out (stages.current) fold below them.
  const body = [...html.filter((b) => b.open), ...html.filter((b) => !b.open)].map((b) => b.html).join("");
  const where = w.env || {}, envFresh = where.at && Date.now() - ms(where.at) < STAGE_DONE_KEEP;
  const envText = ["prod", "dev"].filter((k) => where[k]).map((k) => `<span><b>${k}</b> <span class="mono">${esc(where[k])}</span></span>`).join("");
  // An env nobody re-recorded for a week is not where things stand: it folds away, still there to open.
  const env = !envText ? "" : envFresh ? `<div class="meta">${envText}<span class="muted">${relSpan(where.at)}</span></div>`
    : `<details class="meta"><summary class="muted">env recorded ${where.at ? relSpan(where.at) : "at an unknown time"}</summary>${envText}</details>`;
  const acked = seen ? `<button class="chip" type="button" data-ack-show aria-pressed="${!!F.showAcked}">Show ${seen} acked</button>` : "";
  const table = all.length ? `<div class="table-wrap"><table class="work"><colgroup><col class="c-work"><col class="c-pr"><col><col><col></colgroup>
      <thead><tr>${STAGE_COLS.map(([l]) => `<th>${l}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`
    : `<div class="empty-chart">${seen ? "Every row is acked." : 'No stage rows: no program had ledger activity in 7 days. <span class="mono">orch stage add</span> records work outside a program.'}</div>`;
  const next = (w.next || []).length ? `<div class="card"><div class="card-head"><h3>Next</h3></div><ol class="card-body wnext">${w.next.map((n) => `<li>${linkRefs(n, refs)}</li>`).join("")}</ol></div>` : "";
  return `<div class="card"><div class="toolbar wenv">${legend}${env}${acked}</div>${table}</div>` + next;
}
// The to-dos and the held queue (`orch board todo list` prints the same two tables): the brief only counts them. A
// to-do (`open` kind "do") is a promise for a date or condition: one that has come (`due`, ⏰ in the brief) goes first,
// then by first date, undated last as added. The queue (kind "later") is work set aside with no date, in the board's
// order (`n`, the number `later move` takes), its first next. Done is the Open items button.
const todoItems = () => ((F.brief || {}).open || []).filter((o) => o.kind === "do");
const queueItems = () => ((F.brief || {}).open || []).filter((o) => o.kind === "later").sort((a, b) => a.n - b.n);
// The sidebar counts as the brief's pointer line does: a come to-do is in the brief as ⏰, a met trigger is ❔.
const todoCount = () => todoItems().filter((o) => !o.due).length + queueItems().filter((o) => !o.check).length;
function fleetTodo() {
  if (!F.brief) pollBrief();
  const head = `<div class="view-head"><div><h2>To-do</h2><p>Promises kept on a date or a condition, and work held in turn. ⏰ has come and is in the brief until Done.</p></div></div>`;
  if (!F.brief) return head + '<div class="loading">Loading…</div>';
  const rank = (o) => [o.due ? 0 : 1, o.at ? ms(o.at) : Infinity];
  const items = todoItems().map((o, n) => [o, n]).sort(([a, i], [b, j]) => {
    const [x, y] = [rank(a), rank(b)];
    return x[0] - y[0] || (x[1] === y[1] ? i - j : x[1] < y[1] ? -1 : 1);
  }).map(([o]) => o), queue = queueItems();
  const note = F.doneError ? `<div class="ack-note">${tag("not closed", "warn", "alert")} ${esc(F.doneError)}</div>` : "";
  // The work takes the width, two lines until clicked; its condition (or trigger) and a check are one grey line under it.
  const work = (o, sub) => expandable(`todo:${o.id}`, o.title || o.label, "todo-text")
    + (sub ? expandable(`todo-sub:${o.id}`, sub, "todo-sub") : "")
    + (o.check ? `<div class="todo-sub muted">${tag("check", "warn")} ${esc(o.check)}</div>` : "");
  const who = (o) => `<td>${o.who ? `<span class="todo-who" title="${esc(o.who)}">${esc(o.who)}</span>` : '<span class="muted">–</span>'}</td>`;
  const done = (o) => `<td class="bact" data-row-label="${esc(o.title || o.label)}">${doneBtn(o.id)}</td>`;
  const table = (cols, rows) => `<div class="table-wrap"><table class="todo"><colgroup>${cols.map(([, c]) => `<col class="${c}">`).join("")}<col class="bact"></colgroup>
    <thead><tr>${cols.map(([h]) => `<th>${h}</th>`).join("")}<th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  const today = new Date().setHours(0, 0, 0, 0);
  const late = (o) => o.due && o.at && ms(o.at) < today ? Math.ceil((today - ms(o.at)) / 86400e3) : 0;
  const when = (o) => (o.date ? `<span${o.due ? ' class="todo-come"' : ""}>${esc(o.date)}</span>` : tag("condition", "", ""))
    + (late(o) ? `<div class="todo-sub">${tag(`${late(o)}d overdue`, "bad", "")}</div>` : "");
  const todoRows = items.map((o) => `<tr class="${late(o) ? "is-late" : ""}"><td class="bmark" title="${o.due ? "come" : "waiting"}">${o.due ? "⏰" : "📅"}</td>
      <td class="todo-when">${when(o)}</td><td>${work(o, o.cond)}</td>${who(o)}${done(o)}</tr>`).join("");
  const queueRows = queue.map((o) => `<tr><td class="todo-n${o.n === 1 ? " todo-next" : ""}" title="${o.n === 1 ? "next" : ""}">${o.n === 1 ? "▶ " : ""}${o.check ? "❔" : ""}${o.n}</td>
      <td>${work(o, o.trigger)}</td>${who(o)}${done(o)}</tr>`).join("");
  const nDue = items.filter((o) => o.due).length, nLate = items.filter(late).length, nCheck = queue.filter((o) => o.check).length;
  return head + note + undoNote()
    + `<div class="card"><div class="card-head"><h3>To-dos</h3><span class="aside muted">${items.length - nDue}${nDue ? ` · ⏰ ${nDue} come` : ""}${nLate ? ` · ${nLate} overdue` : ""}</span></div>
    ${items.length ? table([["", "bmark"], ["When", "c-when"], ["To-do", ""], ["Who", "c-who"]], todoRows) : '<div class="empty-chart">No to-do. <span class="mono">orch board todo add</span> records one.</div>'}</div>`
    + `<div class="card"><div class="card-head"><h3>Held queue</h3><span class="aside muted">${queue.length - nCheck}${nCheck ? ` · ❔ ${nCheck} check` : ""}</span></div>
    ${queue.length ? table([["#", "c-n"], ["Work · next when", ""], ["Who", "c-who"]], queueRows) : '<div class="empty-chart">Nothing held. <span class="mono">orch board later add</span> queues one.</div>'}</div>`;
}
function fleetStages() {
  if (!F.brief) pollBrief();
  const head = `<div class="view-head"><div><h2>Stages</h2><p>One row per predicate item and worker card of each program, generated every tick: merge from the merge sha; dev and prod only from <span class="mono">orch stage set</span>. A row ends when every cell is ✅ or n/a, or stops with its ticket or card. A program's finished rows, and rows unchanged for 7 days, fold behind its own ▸ N folded, as the brief counts them.</p></div></div>`;
  return head + (F.brief ? ackNote() + stageSection(F.brief.stages) : '<div class="loading">Loading…</div>');
}
document.addEventListener("click", (e) => {
  const fold = e.target.closest("[data-stage-fold]");
  if (fold) { const id = fold.dataset.stageFold; F.stageFold.has(id) ? F.stageFold.delete(id) : F.stageFold.add(id); renderView(); }
  const job = e.target.closest("[data-job-open]");
  if (job) { const id = job.dataset.jobOpen; F.jobOpen.has(id) ? F.jobOpen.delete(id) : F.jobOpen.add(id); renderView(); }
  if (e.target.closest("[data-ack-show]")) { F.showAcked = !F.showAcked; renderView(); }
  const ack = e.target.closest("[data-ack]");
  if (ack) idsAction(ack, "/api/fleet/ack", ack.dataset.ack, "ackError");
  const done = e.target.closest("[data-done]");
  if (done) idsAction(done, "/api/fleet/done", done.dataset.done, "doneError");
});

// Orca automations (state.json automations). An enabled one is ▲ when its last run failed or its next run is 10 min
// overdue: the scheduler stopped or the app was closed. A switched-off one is never a problem.
const AUTO_RUN = { completed: ["■", "good", "completed"], failed: ["■", "bad", "failed"], error: ["■", "bad", "failed"], running: ["◧", "warn", "running"],
  dispatched: ["◧", "warn", "running"], skipped: ["□", "", "skipped"] };
const AUTO_STALL = 10 * 60e3, AUTO_OVERDUE = "past its run time";
function autoIssue(a) {
  if (!a.enabled) return "";
  if (/fail|error/.test(((a.recent || [])[0] || {}).status || "")) return "last run failed";
  return a.next_run_at && Date.now() - ms(a.next_run_at) > AUTO_STALL ? AUTO_OVERDUE : "";
}
const autoTroubled = (st) => (st.automations || []).filter(autoIssue);
// The few cron shapes automations use, in words; anything else stays as written.
function cadence(rrule) {
  const r = (rrule || "").trim(), m = (re) => r.match(re), pad = (x) => x.padStart(2, "0");
  let g;
  if ((g = m(/^(\d+) \*\/(\d+) \* \* \*$/))) return `every ${g[2]}h at :${pad(g[1])}`;
  if ((g = m(/^\*\/(\d+) (\d+)-(\d+) \* \* 1-5$/))) return `weekdays ${g[2]}–${g[3]}h every ${g[1]}m`;
  if ((g = m(/^\*\/(\d+) \* \* \* \*$/))) return `every ${g[1]}m`;
  if ((g = m(/^(\d+) (\d+) \* \* \*$/))) return `daily ${g[2]}:${pad(g[1])}`;
  return r || "?";
}
function runBlock(r) {
  const [glyph, tone, label] = AUTO_RUN[r.status] || (/^skipped/.test(r.status || "") ? AUTO_RUN.skipped : ["▪", "", r.status || "?"]);
  const title = [label, r.status, r.at && new Date(r.at).toLocaleString(), r.summary].filter(Boolean).join(" · ");
  return `<span class="run ${tone ? "tone-" + tone : ""}" title="${esc(title)}">${glyph}</span>`;
}
function fleetAutomations(st) {
  const autos = st.automations || [], src = (st.sources || {}).automations || {};
  const legend = [AUTO_RUN.completed, AUTO_RUN.failed, AUTO_RUN.running, AUTO_RUN.skipped]
    .map(([g, tone, l]) => `<span><span class="run ${tone ? "tone-" + tone : ""}">${g}</span>${l}</span>`).join("");
  const headHtml = `<div class="view-head"><div><h2>Automations</h2><p>Each Orca automation with its cadence, next and last run, and its last 6 results (oldest on the left). A last run that failed, or a run 10 minutes past its time, gets a ▲. The janitor and its precheck are Orca automations, so they are here once registered. Below it, the jobs orch-dash runs on its own and the rules it applies as it reads. CronCreate jobs inside sessions are not collected, and of launchd jobs only the two that keep a record.</p></div>
    <div class="legend">${legend}</div></div>`;
  const failed = src.ok === false ? `<div class="card"><div class="empty-chart">${tag("collection failed", "warn", "alert")} Could not read the Orca automation list: ${esc(src.error || "")}. Below is from ${rel(src.updated_at)}.</div></div>` : "";
  const rows = autos.map((a) => {
    const issue = autoIssue(a), last = (a.recent || [])[0];
    return `<tr class="${a.enabled ? "" : "is-done"}"><td><b>${esc(a.name)}</b>${a.enabled ? "" : ` ${tag("off")}`}${issue ? ` ${tag(`▲ ${issue}`, "bad", "")}` : ""}</td>
      <td class="nowrap">${esc(cadence(a.rrule))}</td><td class="nowrap ${issue === AUTO_OVERDUE ? "toned tone-bad" : ""}">${a.enabled ? relSpan(a.next_run_at) : '<span class="muted">–</span>'}</td>
      <td class="nowrap muted">${a.last_run_at ? relSpan(a.last_run_at) : "–"}</td><td class="runs nowrap">${[...(a.recent || [])].reverse().map(runBlock).join("")}</td>
      <td class="dim"><span class="full">${last ? esc(last.summary || last.status || "") : '<span class="muted">no runs yet</span>'}</span></td></tr>`;
  }).join("");
  const table = autos.length ? `<div class="card table-wrap" data-src="automations"><table class="autos"><thead><tr><th>Name</th><th>Cadence</th><th>Next</th><th>Last</th><th>Last 6</th><th>Last result</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="card"><div class="empty-chart">No Orca automations registered.</div></div>';
  return headHtml + failed + table + internalJobs();
}

// orch-dash's own jobs (/api/fleet/jobs, jobs.py): each row is the record the job wrote when it last ran, read on every
// poll, so a fleet tick that keeps failing still shows here. A job with no record says so; nothing here is estimated.
function internalJobs() {
  pollJson("jobs", "/api/fleet/jobs", 10e3, "automations");
  const rep = F.jobs;
  if (!rep) return '<div class="card"><div class="empty-chart">Loading orch-dash jobs…</div></div>';
  if (rep.error) return `<div class="card"><div class="empty-chart">${tag("not loaded", "warn", "alert")} Could not read orch-dash's jobs: ${esc(rep.error)}</div></div>`;
  // A job whose `group` names another runs inside that job's tick: it folds into that row, which opens to one line each.
  const riders = (j) => rep.jobs.filter((k) => k.group === j.key);
  const line = (j, sub) => {
    const r = j.record, kids = sub ? [] : riders(j), open = F.jobOpen.has(j.key);
    const toggle = kids.length ? ` <button class="chip mini" type="button" data-job-open="${esc(j.key)}" aria-expanded="${open}">${open ? "▾" : "▸"} ${kids.length} more</button>` : "";
    const name = `<td class="${sub ? "jsub" : ""}"><b>${esc(j.name)}</b> <span class="src">${esc(j.runs_in)}</span>${toggle}</td>`;
    const every = `<td class="nowrap" title="${esc(j.cadence || "")}">${esc(j.every || (j.group ? "each tick" : "?"))}</td>`;
    if (j.error) return `<tr>${name}${every}<td colspan="3">${tag("not read", "warn", "alert")} ${esc(j.error)}</td></tr>`;
    if (!r) return `<tr class="is-done">${name}${every}<td colspan="3" class="muted">no record · ${esc(j.why || "It has not recorded a run in this store.")}</td></tr>`;
    const warned = r.warned_at ? ` <a href="#/fleet/timeline" title="The timeline line written when it started failing">Timeline warning ${esc(hhmm(r.warned_at))} ${icon("arrow")}</a>` : "";
    // A count of 0 is "no change", its words under it in grey; only a run that changed something stands out.
    const said = r.n === 0 ? `<span class="muted">no change</span>${r.result ? `<div class="muted">${esc(r.result)}</div>` : ""}` : r.n > 0 ? `<b>${esc(r.result || "ran")}</b>` : esc(r.result || "ran");
    const busy = kids.filter((k) => k.error || (k.record && (!k.record.ok || k.record.n > 0)));
    const unrecorded = kids.filter((k) => !k.error && !k.record).length, unchanged = kids.length - busy.length - unrecorded;
    const quiet = [unchanged && `${unchanged} no change`, unrecorded && `${unrecorded} no record`].filter(Boolean).join(" · ");
    const parts = busy.map((k) => `${esc(k.name)}: ${k.error || !k.record.ok ? tag("▲ failed", "bad", "") : `<b>${esc(k.record.result || "ran")}</b>`}`);
    if (quiet) parts.push(`<span class="muted">${busy.length ? quiet : `${kids.length} more: ${quiet}`}</span>`);
    const sum = kids.length ? `<div class="jsum">${parts.join(" · ")}</div>` : "";
    const res = r.ok ? `${said}${warned ? `<div class="muted">last failure ${relSpan(r.failed_at)}${warned}</div>` : ""}`
      : `${tag("▲ failed", "bad", "")}${expandable(`job|${j.key}`, r.error)}<div class="muted">failing since ${relSpan(r.failed_at)}${warned}</div>`;
    const acted = r.acted_at ? `<div class="muted">last acted: ${esc(String(r.acted_n))} · ${relSpan(r.acted_at)}</div>` : "";
    const next = r.next_at ? relSpan(r.next_at) : `<span class="muted">${j.group ? "with the next tick" : "–"}</span>`;
    return `<tr>${name}${every}<td class="nowrap">${relSpan(r.at)}</td><td>${res}${acted}${sum}</td><td class="nowrap">${next}</td></tr>`
      + (open ? kids.map((k) => line(k, true)).join("") : "");
  };
  const rows = rep.jobs.filter((j) => !j.group || !rep.jobs.some((k) => k.key === j.group)).map((j) => line(j, false)).join("");
  const rules = rep.rules.map((r) => `<tr><td><b>${esc(r.name)}</b></td><td>${esc(r.rule)}</td><td class="nowrap muted">${esc(r.applied)}</td></tr>`).join("");
  return `<div class="card table-wrap"><div class="card-head"><h3>orch-dash jobs</h3><span class="aside">${rep.jobs.length}</span></div>
      <table class="jobs"><thead><tr><th>Name</th><th>Every</th><th>Last run</th><th>Result</th><th>Next</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="card table-wrap"><div class="card-head"><h3>Rules applied on read</h3><span class="aside">no run of their own</span></div>
      <table class="jobs"><thead><tr><th>Name</th><th>Rule</th><th>Applied</th></tr></thead><tbody>${rules}</tbody></table></div>`;
}

// Background watches of the live Claude Code sessions (state.json monitors, fleet.py fetch_monitors): a Bash run in the
// background or a Monitor. A row is ▲ when it died unseen, or no process runs it and no end was recorded.
const MON = { running: ["●", "good", "running"], dead: ["▲", "bad", "dead"], seen: ["○", "", "dead, seen"], lost: ["▲", "bad", "unknown"] };
const monStatus = (m) => (m.status === "dead" && m.seen ? "seen" : m.status);
const monTroubled = (st) => (st.monitors || []).filter((m) => !["running", "seen"].includes(monStatus(m)));
function fleetMonitors(st) {
  const mons = st.monitors || [], src = (st.sources || {}).monitors || {};
  const legend = Object.values(MON).map(([g, tone, l]) => `<span><span class="toned tone-${tone}">${g}</span> ${l}</span>`).join("");
  const headHtml = `<div class="view-head"><div><h2>Monitors</h2><p>Background commands (run_in_background) and Monitors started by live Claude Code sessions, matching each session transcript's start and end records against the process list. A failed exit is "dead"; no end record and no process is "unknown". Both get a ▲ and drop off after 6 hours or once the same command runs again. A failure the session already received is "dead, seen", listed without a ▲. A normal exit, a deliberate stop (TaskStop included) and a transcript continued in a new session are not shown. Tasks inside subagents, CronCreate and launchd jobs are not collected.</p></div>
    <div class="legend">${legend}</div></div>`;
  const failed = src.ok === false ? `<div class="card"><div class="empty-chart">${tag("collection failed", "warn", "alert")} Could not read the monitor list: ${esc(src.error || "")}. Below is from ${rel(src.updated_at)}.</div></div>`
    : src.warning ? `<div class="card"><div class="empty-chart">${tag("partial", "warn", "alert")} Sessions whose transcript was not found are left out: ${esc(src.warning)}</div></div>` : "";
  const rows = mons.map((m) => {
    const [glyph, tone, label] = MON[monStatus(m)] || ["?", "", m.status], s = byId(st)[m.session];
    const result = m.status === "dead" ? `${m.exit != null ? `exit ${esc(m.exit)} · ` : ""}${esc(m.summary || "")}` : m.status === "lost" ? "no end record, no process" : esc(m.event || "");
    return `<tr><td class="nowrap"><span class="toned tone-${tone}">${glyph} ${label}</span></td>
      <td><b>${esc(m.description || "")}</b> ${tag(m.kind === "monitor" ? "Monitor" : "background")}<div class="mono dim ellipsis cmd" title="${esc(m.command)}">${esc(m.command)}</div></td>
      <td>${s ? `<a href="${sessionHref(s.id)}">${phaseDot(s)} ${esc(s.name)}</a>` : ""}<div class="mono dim nowrap">${esc(m.session_name || "")}</div></td>
      <td class="nowrap muted">${relSpan(m.started_at)}</td><td class="nowrap ${tone === "bad" ? "toned tone-bad" : "muted"}">${m.last_at ? relSpan(m.last_at) : "–"}</td>
      <td class="dim"><span class="ellipsis" style="display:block">${result || '<span class="muted">–</span>'}</span></td></tr>`;
  }).join("");
  const table = mons.length ? `<div class="card table-wrap" data-src="monitors"><table class="autos monitors"><thead><tr><th>Status</th><th>What</th><th>Session</th><th>Started</th><th>Last wake</th><th>Result · last event</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="card"><div class="empty-chart">No monitors in live sessions.</div></div>';
  return headHtml + failed + table;
}

// Instructions sent out of a session (sent.py via /api/fleet/sent): the `orch sent add` log, lines typed on the
// dashboard, and what live sessions sent by Agent, SendMessage or orca. Read on every poll while the page is open.
const SENT_SRC = { log: ["log", "accent"], transcript: ["transcript", ""], dashboard: ["dashboard", "good"] };
const SENT_DAYS = [["1", "1d"], ["7", "7d"], ["30", "30d"]];
const SENT_SHOWN = 300;
const sentDays = () => (SENT_DAYS.some(([d]) => d === S.filters.sent_days) ? S.filters.sent_days : "1");
async function pollSent() {
  if (F.sentBusy) return;
  F.sentBusy = true;
  const days = sentDays();
  try {
    const fresh = F.sentDays === days;
    const r = await fetch(`/api/fleet/sent?days=${days}`, { cache: "no-store", headers: fresh && F.sentEtag ? { "If-None-Match": F.sentEtag } : {} });
    if (r.status !== 304) {
      F.sent = r.ok ? await r.json() : { error: `HTTP ${r.status}` };
      F.sentEtag = r.ok ? r.headers.get("ETag") : null;
      F.sentDays = days;
      if (isFleet() && S.section === "sent") renderView();
    }
  } catch (e) { F.sent = { error: String(e.message || e) }; F.sentEtag = null; }
  F.sentBusy = false;
}
const sentKey = (r) => `${r.ts}|${r.source}|${r.to}|${(r.text || "").length}`;
function fleetSent(st) {
  if (!F.sent || F.sentDays !== sentDays()) pollSent();
  const d = F.sent, src = (st && st.sources || {}).sent || {};
  const headHtml = `<div class="view-head"><div><h2>Sent</h2><p>Instructions sessions sent to other sessions, cards, subagents, inboxes, Slack or the tracker, with the full text. Three sources: the log written with <span class="mono">orch sent add</span> at send time, lines typed into a session from this dashboard, and the Agent, SendMessage, <span class="mono">orca terminal send</span>, <span class="mono">orca worktree create --prompt</span> and <span class="mono">orca orchestration send</span> calls in live Claude Code session transcripts. An instruction in both the log and a transcript shows as one row. What an ended session sent survives only in the log. Tokens and passwords are masked.</p></div>
    <div class="legend">${Object.values(SENT_SRC).map(([l, tone]) => tag(l, tone)).join(" ")}</div></div>`;
  if (!d) return headHtml + '<div class="loading">Loading…</div>';
  if (d.error) return headHtml + `<div class="card"><div class="empty-chart">${esc(d.error)}</div></div>`;
  const rows = d.rows || [], q = (S.filters.sent_q || "").toLowerCase();
  const f = SENT_SRC[S.filters.sent_src] ? S.filters.sent_src : "all";
  const bySrc = (r) => f === "all" || r.source === f || (r.also || []).includes(f);
  const hit = (r) => !q || [r.from, r.to, r.kind, r.channel, r.repo, r.subject, r.text].some((v) => (v || "").toLowerCase().includes(q));
  const shown = rows.filter((r) => bySrc(r) && hit(r));
  const failed = src.ok === false ? `<div class="card"><div class="empty-chart">${tag("collection failed", "warn", "alert")} Could not read transcripts: ${esc(src.error || "")}. Only the log and dashboard sources are shown.</div></div>` : "";
  const body = shown.slice(0, SENT_SHOWN).map((r) => {
    const text = r.text || "", long = text.split("\n").length > 6 || text.length > 480, k = sentKey(r);
    const srcs = [r.source, ...(r.also || [])].map((x) => tag((SENT_SRC[x] || [x])[0], (SENT_SRC[x] || [, ""])[1])).join(" ");
    const pre = `<pre class="sent-text">${esc(text)}</pre>`;
    return `<tr><td class="nowrap muted">${relSpan(r.ts)}</td>
      <td><div class="nowrap">${esc(r.from || "?")} <span class="muted">→</span> <b>${esc(r.to || "?")}</b></div>
        <div class="dim">${r.kind ? tag(r.kind) : ""} <span class="mono">${esc(r.channel || "")}</span>${r.repo ? ` · <span class="mono">${esc(r.repo)}</span>` : ""}</div></td>
      <td class="sent-cell">${r.subject ? `<div><b>${esc(r.subject)}</b></div>` : ""}${long
        ? `<details data-sent-key="${esc(k)}" ${F.sentOpen && F.sentOpen.has(k) ? "open" : ""}><summary class="dim ellipsis">${esc(text.split("\n").find((l) => l.trim()) || "")} <span class="muted">(${text.length} chars)</span></summary>${pre}</details>` : pre}</td>
      <td class="nowrap">${srcs}</td></tr>`;
  }).join("");
  const srcChips = chips("sent_src", [["all", "all sources", rows.length], ...Object.entries(SENT_SRC).map(([id, [l]]) => [id, l, rows.filter((r) => r.source === id || (r.also || []).includes(id)).length])], f);
  const toolbar = `<div class="toolbar">${chips("sent_days", SENT_DAYS, sentDays())}${srcChips}
    <input class="search" id="sent-search" type="search" placeholder="Search recipient, sender, text" value="${esc(S.filters.sent_q || "")}" aria-label="Search sent instructions"></div>`;
  const cut = d.total > rows.length ? `Fetched the latest ${rows.length} of ${d.total}. ` : "";
  const more = shown.length > SENT_SHOWN ? `Showing ${SENT_SHOWN} of ${shown.length} matches. ` : "";
  const note = cut || more ? `<p class="muted">${cut}${more}Narrow with search or source.</p>` : "";
  const table = shown.length ? `<div class="card table-wrap" data-src="sent"><table class="sent"><thead><tr><th>When</th><th>From → to</th><th>Text</th><th>Source</th></tr></thead><tbody>${body}</tbody></table></div>`
    : `<div class="card"><div class="empty-chart">${rows.length ? "No matching instructions." : "Nothing sent in this period."}</div></div>`;
  return headHtml + failed + toolbar + note + table;
}
document.addEventListener("input", (e) => { if (e.target.id === "sent-search") { S.filters.sent_q = e.target.value; renderView(); } });
document.addEventListener("toggle", (e) => {
  const k = e.target.dataset && e.target.dataset.sentKey;
  if (k) { F.sentOpen ||= new Set(); e.target.open ? F.sentOpen.add(k) : F.sentOpen.delete(k); }
}, true);

// Who holds a landing lane or an `orch hold` resource (state.json holds), longest held first.
function holdsCard(st) {
  const hs = [...(st.holds || [])].sort((a, b) => (a.at || "").localeCompare(b.at || ""));
  const rows = hs.map((h) => {
    const s = byId(st)[h.session], long = Date.now() - ms(h.at) > 3600e3;
    return `<tr><td class="mono">${esc(h.resource)}</td><td>${h.kind === "lane" ? tag("landing lane", "accent", "merge") : tag("resource")}</td>
      <td>${s ? `<a href="${sessionHref(s.id)}">${phaseDot(s)} ${esc(s.name)}</a>` : `<span class="mono">${esc(h.by || "?")}</span>`}</td>
      <td class="dim">${esc(h.note || "")}</td><td class="nowrap ${long ? "toned tone-warn" : "muted"}" title="held since">${relSpan(h.at)}</td></tr>`;
  }).join("");
  return `<div class="card table-wrap"><div class="card-head"><h3>Shared resources</h3><span class="aside">${hs.length} held</span></div>
    ${hs.length ? `<table><thead><tr><th>Resource</th><th>Kind</th><th>Held by</th><th>Note</th><th>Since</th></tr></thead><tbody>${rows}</tbody></table>`
      : '<div class="empty-chart">Nothing held. Take a shared resource (a browser profile, a login) with <span class="mono">orch hold &lt;resource&gt;</span>.</div>'}</div>`;
}

function fleetGraph(st) {
  return `<div class="view-head"><div><h2>Graph</h2><p>Edge colour is the reviewer's latest state; the bar on each PR is its CI. Only sessions with a linked pull request are drawn.</p></div></div>
    ${projectChips(st)}<div class="card" data-src="github">${relGraph(st)}</div>`;
}

// A session's started subagents (subagents.jsonl, attached by the collector) as rows under it. Not sessions: no link,
// PR or Needs you of their own, and not counted with the sessions. A stale one has no stop on record for
// board.subagent_stale_h hours, or its session is offline: "check", not running.
function subagentRows(s, d, hidden = false) {
  const ids = subRowIds(s);
  return (s.subagents || []).map((a, i) => `<tr${hidden ? " hidden" : ""} class="dim" id="${ids[i]}"><td><span class="tree-cell" style="--d:${Math.min(d + 1, 4)}">${esc(a.desc || a.type || "subagent")}</span></td>
      <td>${tag("subagent")}${a.type ? ` <span class="muted">${esc(a.type)}</span>` : ""}</td>
      <td>${a.stale ? tag("check: no stop recorded", "warn") : "running"}</td>
      <td class="hide-md"></td><td class="hide-sm"></td><td></td><td class="num"></td><td class="num hide-sm when">${relSpan(a.started_at)}</td></tr>`).join("");
}

// A lead's rows fold on the Sessions page. Which leads are folded is this browser's, kept across reloads when storage
// works; F.leadFold holds it for the page either way, so a redraw keeps it and blocked storage still lets it fold.
const FOLD_KEY = "orch-dash:lead-fold";
function leadFolds() {
  if (F.leadFold) return F.leadFold;
  let ids = null;
  try { ids = JSON.parse(store.get(FOLD_KEY)); } catch (e) { /* not ours, or cut short: nothing is folded */ }
  return (F.leadFold = new Set(Array.isArray(ids) ? ids : []));
}
function foldLead(id) {
  const fold = leadFolds();
  if (!fold.delete(id)) fold.add(id);
  store.set(FOLD_KEY, JSON.stringify([...fold]));
}
// Each lead's sessions: what follows it in the depth-first tree, deeper than it. Subagents are not sessions: a lead's
// own stay on screen, a worker's fold with it.
function leadKids(tree) {
  const out = new Map();
  tree.forEach(([s, d], i) => {
    if (s.kind !== "orchestration") return;
    const end = tree.findIndex(([, e], j) => j > i && e <= d);
    out.set(s.id, tree.slice(i + 1, end < 0 ? tree.length : end).map(([k]) => k).filter((k) => inProject(k.project)));
  });
  return out;
}
const MARK_RANK = ["🙋", "🔴", "❔", "🔄", "⏳", "⏸️", "📅"];  // rowstate.ROW_ORDER; 📅 is a held row a to-do holds
// A folded lead's worst session: its brief rows' marks, and 🙋 for one waiting on a person, which may have no row.
// A finished row has no session in table_rows and nothing urgent to say.
function urgentMark(kids) {
  const marks = new Set(kids.flatMap((k) => [...sessionRowsOf(k.id).map((x) => x.status), ...(k.phase === "waiting" || k.unread ? ["🙋"] : [])]));
  return MARK_RANK.find((m) => marks.has(m)) || "";
}
const domId = (id) => encodeURIComponent(id).replace(/[^\w-]/g, "_");  // renderView finds the focused id by selector
const subRowIds = (s) => (s.subagents || []).map((a, i) => `row-${domId(s.id)}-sub-${i}`);
function leadToggle(s, kids, folded) {
  const ids = kids.flatMap((k) => [`row-${domId(k.id)}`, ...subRowIds(k)]).join(" ");
  return `<button class="fold-btn" type="button" id="fold-${domId(s.id)}" data-fold-lead="${esc(s.id)}" aria-expanded="${!folded}" aria-controls="${esc(ids)}"
    aria-label="${esc(`${folded ? "Show" : "Hide"} the ${kids.length} session${kids.length === 1 ? "" : "s"} under ${s.name}`)}"><span class="chev" aria-hidden="true"></span></button>`;
}
function foldSummary(kids) {
  const mark = urgentMark(kids);
  return `<span class="fold-sum muted" title="Most urgent status under this lead">${kids.length} session${kids.length === 1 ? "" : "s"}${mark ? ` · ${briefInline(mark)}` : ""}</span>`;
}

function fleetSessions(st) {
  if (!F.brief) pollBrief();
  const tree = sessionTree(st), kidsOf = leadKids(tree), fold = leadFolds();
  const hidden = new Set([...kidsOf].filter(([id, kids]) => fold.has(id) && kids.length && inProject(byId(st)[id].project)).flatMap(([, kids]) => kids.map((k) => k.id)));
  const rows = tree.filter(([s]) => inProject(s.project)).map(([s, d]) => {
    const pr = (st.prs || []).find((p) => p.session === s.id);
    // Orca's own link when the collector holds no PR for the session: number, state and CI as Orca observed them.
    const lp = !pr && s.linked_pr && s.linked_pr.number ? s.linked_pr : null;
    const prCell = pr ? link(pr.url, `#${esc(pr.number)}`)
      : lp ? `${lp.url ? link(lp.url, `#${esc(lp.number)}`) : `#${esc(lp.number)}`} <span class="muted">${esc([lp.state, lp.checksStatus].filter((x) => x && x !== "unknown").join(" · "))}</span>` : "";
    const tickets = (s.refs || []).filter((r) => r.type !== "pr" && r.url).map((r) => link(r.url, esc(r.key || r.provider || "ticket"))).join(" ");
    const board = s.workspace_status && s.workspace_status !== "in-progress" ? ` <span class="tag">${esc(s.workspace_status)}</span>` : "";
    const kids = kidsOf.get(s.id), folded = fold.has(s.id) && kids && kids.length > 0;
    const open = `<a class="${kids ? "lead-link" : "tree-cell"}"${kids ? "" : ` style="--d:${Math.min(d, 4)}"`} href="${sessionHref(s.id)}">${phaseDot(s)}<b>${esc(s.name)}</b></a>`;
    const name = kids ? `<span class="tree-cell lead-cell" style="--d:${Math.min(d, 4)}">${kids.length ? leadToggle(s, kids, folded) : ""}${open}${folded ? foldSummary(kids) : ""}</span>` : open;
    return `<tr${hidden.has(s.id) ? " hidden" : ""} id="row-${domId(s.id)}"><td>${name}</td>
      <td>${kindTag(st, s)}</td><td>${esc(s.phase)}${board}</td>
      <td class="hide-md">${projBadge(s.project)}<span class="mono dim">${s.branch ? esc(s.branch) : ""}</span></td>
      <td class="hide-sm">${tickets}</td><td>${prCell}</td><td class="num">${badge(s.unread, s.missed)}</td><td class="num hide-sm when">${relSpan(s.last_activity)}</td></tr>`
      + subagentRows(s, d, hidden.has(s.id));
  }).join("");
  return `<div class="view-head"><div><h2>Sessions</h2><p>Every Orca worktree, placed under the orchestrator or lead whose run dispatched it, else under its Orca parent, else under the root. A lead is a session under the root with its own run; the sessions under it are its workers. Ticket, PR and the board column (shown when not in-progress) are the card's own Orca fields. A subagent a session started and has not been seen to stop sits under it, dimmed.</p></div>${phaseLegend()}</div>
    ${projectChips(st)}<div class="card table-wrap"><table><thead><tr><th>Session</th><th>Kind</th><th>Phase</th><th class="hide-md">Project · branch</th><th class="hide-sm">Ticket</th><th>PR</th><th class="num">Needs you</th><th class="num hide-sm">Active</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${holdsCard(st)}`;
}

function fleetSession(st) {
  const s = byId(st)[F.sessionId];
  if (!s) return `<div class="loading">This session is gone from Orca. <a class="link" href="#/fleet/sessions">All sessions</a></div>`;
  const parent = byId(st)[s.parent];
  const agents = (s.agents || []).map((a) => `<li class="agent"><div class="row">${tag(a.state, a.state === "waiting" ? "warn" : a.state === "working" ? "accent" : "good")}
      <span class="mono dim">${esc(a.type || "")}${a.mode ? ` · ${esc(a.mode)}` : ""}</span><span class="grow"></span>${relSpan(a.updated || a.since)}</div>
      ${a.prompt ? `<div class="kv"><span class="eyebrow">prompt</span><div class="pre">${esc(a.prompt)}</div></div>` : ""}
      ${a.state === "working" && a.tool ? `<div class="kv"><span class="eyebrow">now</span><div class="mono">${esc(a.tool)} ${esc(a.detail || "")}</div></div>` : ""}
      ${a.last ? `<div class="kv"><span class="eyebrow">last reply</span><div class="pre">${esc(a.last)}</div></div>` : ""}</li>`).join("");
  const items = (st.items || []).filter((i) => i.session === s.id);
  const tl = (st.timeline || []).filter((e) => e.session === s.id);
  return `<div class="view-head"><div><h2>${phaseDot(s)} ${esc(s.name)}</h2>
      <p>${kindTag(st, s)} ${esc(s.phase)}${parent ? ` · under <a class="link" href="${sessionHref(parent.id)}">${esc(parent.name)}</a>` : ""}
      ${s.task_title ? ` · task: ${esc(s.task_title)}` : ""} · ${projBadge(s.project)}<span class="mono">${s.branch ? esc(s.branch) : ""}</span></p></div></div>
    ${chatCard(s)}
    <div class="grid">
      <div class="card"><div class="card-head"><h3>Needs you</h3><span class="aside">${items.length}</span></div>${itemList(st, items, { showSession: false })}</div>
      ${briefRowsCard(s, st)}
      <div class="card"><div class="card-head"><h3>Agents</h3><span class="aside">${(s.agents || []).length}</span></div><ul class="rows agents">${agents || '<li class="muted">No agent in this worktree.</li>'}</ul></div>
      <div class="card wide" data-src="github"><div class="card-head"><h3>Pull requests</h3></div>${relGraph(st, s.id)}</div>
      <div class="card wide"><div class="card-head"><h3>Timeline</h3></div>${timelineList(st, tl, { fold: `session:${s.id}`, here: s.id })}</div>
    </div>`;
}

function fleetTimeline(st) {
  return `<div class="view-head"><div><h2>Timeline</h2><p>Prompts, finished turns, PR review and CI changes, and orchestration mail, newest first.</p></div></div>
    <div class="card">${timelineList(st, st.timeline || [], { fold: "all" })}</div>`;
}

const agentTerms = (s) => (s.terminals || []).filter((t) => t.agent && t.connected && t.writable);

function chatCard(s) {
  const terms = agentTerms(s);
  if (!terms.length) return `<div class="card"><div class="card-head"><h3>Send</h3></div><div class="empty">No connected agent terminal in this session.</div></div>`;
  const st = F.chat[s.id] || {}, handle = st.handle || terms[0].handle;
  const pick = terms.length > 1 ? `<select id="chat-handle" class="search" style="min-width:0">${terms.map((t) =>
    `<option value="${esc(t.handle)}" ${t.handle === handle ? "selected" : ""}>${esc(t.title || t.handle)}</option>`).join("")}</select>` : "";
  const c = F.confirm && F.confirm.session === s.id ? F.confirm : null, off = c || st.busy ? "disabled" : "";
  const tone = st.ok ? "good" : st.typed ? "warn" : "bad";
  const result = st.ok ? "Sent; the session took it."
    : st.typed ? `Typed and submitted, but not confirmed on screen (${st.reason}). Check the terminal before sending again.`
    : `Not sent: ${st.reason}.${st.fallback === "copy" ? " Use Copy and paste it yourself." : ""}`;
  return `<div class="card chat"><div class="card-head"><h3>Send</h3><span class="aside">one line · sent only when the session is idle at an empty prompt</span></div>
    <div class="card-body"><div class="chat-row">${pick}<input id="chat-input" class="search grow" maxlength="2000" autocomplete="off"
        placeholder="Message to ${esc(s.name)}" value="${esc(F.draft[s.id] || "")}" ${off}>
      <button class="chip" data-chat="ask" ${off}>${icon("send")}Send</button>
      <button class="chip" data-chat="copy" title="Copy, then paste into the terminal yourself">${icon("copy")}Copy</button></div>
      ${c ? `<div class="confirm tone-warn">${icon("alert")}<span class="grow">Type <b>${esc(c.text)}</b> into <b>${esc(s.name)}</b> and press Enter?</span>
        <button class="chip" data-chat="send">Confirm</button><button class="chip" data-chat="cancel">Cancel</button></div>` : ""}
      ${st.busy ? '<div class="muted">Checking the screen and sending…</div>' : "reason" in st || st.ok ? `<div class="toned tone-${tone}">${esc(result)}</div>` : ""}</div></div>`;
}

async function chatAction(act) {
  const s = byId(S.state || {})[F.sessionId];
  if (!s) return;
  const text = (F.draft[s.id] || "").trim(), handle = $("#chat-handle")?.value || agentTerms(s)[0]?.handle;
  if (act === "copy") { try { await navigator.clipboard.writeText(text); } catch (err) { /* blocked: the text is in the box */ } return; }
  if (act === "cancel") { F.confirm = null; renderView(); return; }
  if (act === "ask") { if (text) { F.confirm = { session: s.id, text, handle }; F.chat[s.id] = { handle }; renderView(); } return; }
  if (act !== "send" || !F.confirm) return;
  const c = F.confirm;
  F.confirm = null; F.chat[s.id] = { handle: c.handle, busy: true }; renderView();
  let out;
  try { out = await fleetPost("/api/fleet/send", { session: c.session, handle: c.handle, text: c.text }); } catch (err) { out = { ok: false, reason: String(err.message || err), fallback: "copy" }; }
  F.chat[s.id] = { handle: c.handle, ...out };
  if (out.typed) F.draft[s.id] = "";
  renderView();
}

// My pull requests over time (pr_stats.py via /api/fleet/stats): PRs I authored plus those agents opened for me,
// searched on GitHub every 6 h. Charts are plain SVG strings drawn at the view's pixel width (a resize renders the view
// again), so a wide window gets wider bars, not taller charts and larger text. A bar's tooltip is the shared #tip, not
// an SVG <title>: the poll re-renders the view every few seconds, which closes a native tooltip mid-hover.
async function pollStats() {
  const failed = (r) => r.error && !r.views;  // a collect error over last good counts is shown, not retried
  if (F.statsBusy || (F.stats && Date.now() - F.statsAt < (failed(F.stats) ? 5e3 : 60e3))) return;
  F.statsBusy = true;
  try {
    const r = await fetch("/api/fleet/stats", { cache: "no-store" });
    F.stats = r.ok ? await r.json() : { error: r.status === 503 ? "The first PR stats collect has not finished (it runs dozens of GitHub searches)." : `HTTP ${r.status}` };
  } catch (e) { F.stats = { error: String(e.message || e) }; }
  F.statsAt = Date.now(); F.statsBusy = false;
  if (failed(F.stats)) setTimeout(() => { if (S.section === "stats") renderView(); }, 5e3);
  if (S.section === "stats") renderView();
}
const STAT_STATE = [["merged", "Merged", "var(--accent)"], ["open", "Open", "var(--good)"], ["closed", "Closed", "var(--bad)"]];
// The newest month runs to hundreds while the first ones hold a handful, so the charts open on a recent window and the
// y-axis fits it. Six months keeps enough monthly bars to read a direction; the KPIs above stay whole-period.
const STAT_RANGES = [["90d", "90 days", 90], ["6m", "6 months", 182], ["12m", "12 months", 365], ["all", "All", 0]];
const STAT_SRC_COLORS = ["var(--ink-3)", "var(--derived)", "var(--warn)", "var(--good)", "var(--accent)"];
// Stacked bars per bucket, with optional lines over them on the same y-scale.
function statWidth() {
  const view = $("#view"), cs = getComputedStyle(view);
  return Math.max(320, view.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 2);
}
function statBars({ labels, titles, series, lines = [], height = 220, every }) {
  const W = statWidth(), H = height, m = { l: 34, r: 8, t: 8, b: 22 };
  const tot = labels.map((_, i) => series.reduce((a, s) => a + s.values[i], 0));
  const max0 = Math.max(1, ...tot, ...lines.flatMap((l) => l.values.filter((v) => v != null)));
  const step = niceStep(max0), max = Math.max(step, Math.ceil(max0 / step) * step);
  const band = (W - m.l - m.r) / labels.length, bw = Math.max(1, Math.min(40, band * 0.72));
  const y = (v) => H - m.b - (v / max) * (H - m.t - m.b), cx = (i) => m.l + band * i + band / 2;
  const nth = every || Math.max(1, Math.ceil(labels.length / 14));
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="group" aria-label="${esc(series.map((s) => s.name).join(", "))}"><g class="axis">${yAxis(max, m.l, W - m.r, y)}`;
  labels.forEach((l, i) => { if ((labels.length - 1 - i) % nth === 0) svg += `<text x="${cx(i)}" y="${H - 6}" text-anchor="middle">${esc(l)}</text>`; });
  svg += "</g>";
  const row = (name, color, v) => `<div class="row"><span class="sw" style="background:${color}"></span>${esc(name)}<b>${v ?? "—"}</b></div>`;
  labels.forEach((_, i) => {
    let acc = 0;
    const tip = `<div class="tt">${esc(titles[i])}</div>${series.map((s) => row(s.name, s.color, s.values[i])).join("")}${row("Total", "transparent", tot[i])}`
      + lines.map((l) => row(l.name, l.color, l.values[i])).join("");
    const said = [`${titles[i]}: ${tot[i]}`, ...series.map((s) => `${s.name} ${s.values[i]}`), ...lines.map((l) => `${l.name} ${l.values[i] ?? "none"}`)].join(", ");
    for (const s of series) {
      const v = s.values[i];
      if (v) svg += `<rect x="${cx(i) - bw / 2}" y="${y(acc + v)}" width="${bw}" height="${y(acc) - y(acc + v)}" style="fill:${s.color}"/>`;
      acc += v;
    }
    svg += `<rect x="${m.l + band * i}" y="${m.t}" width="${band}" height="${H - m.t - m.b}" fill="transparent" role="img" aria-label="${esc(said)}" data-tip="${esc(tip)}"/>`;
  });
  for (const l of lines) {
    const pts = l.values.map((v, i) => (v == null ? null : `${cx(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean);
    if (pts.length > 1) svg += `<polyline points="${pts.join(" ")}" fill="none" style="stroke:${l.color}" stroke-width="2" ${l.dash ? 'stroke-dasharray="6 4"' : ""} pointer-events="none"/>`;
  }
  return `<div class="chart">${svg}</svg></div>`;
}
const statLegend = (items) => `<div class="legend">${items.map(([name, color, line]) => `<span><span class="key ${line ? "" : "sq"}" style="background:${color}"></span>${esc(name)}</span>`).join("")}</div>`;
const statMonth = (s) => (s.endsWith("-01") ? `${s.slice(2, 4)}/1` : String(+s.slice(5)));
function fleetStats() {
  pollStats();
  const rep = F.stats;
  const headHtml = `<div class="view-head"><div><h2>PR stats</h2><p>Pull requests I opened, and those the agents in the config opened for me, by when they were created. Searched on GitHub every 6 hours from <span class="mono">${esc(rep && rep.since || "since")}</span>; days and hours are this machine's time zone. A campaign is one title on ${rep && rep.batch_min || 10} or more PRs.</p></div>
    ${rep && rep.views ? `<div class="aside muted">Collected ${relSpan(rep.generated_at)}</div>` : ""}</div>`;
  if (!rep) return headHtml + '<div class="loading">Loading…</div>';
  if (!rep.views) return headHtml + `<div class="card"><div class="empty-chart">${esc(rep.error || "No report yet.")}</div></div>`;
  const failure = rep.error ? `<div class="card"><div class="sub" style="color:var(--bad)">Last collect failed ${relSpan(rep.failed_at)}: ${esc(rep.error)}. Showing the counts from ${relSpan(rep.generated_at)}; retried hourly.</div></div>` : "";
  const nb = S.filters.pr_stats === "no_batch", v = rep.views[nb ? "no_batch" : "all"];
  const st = v.states, srcs = rep.sources, years = Object.entries(v.years);
  const srcColor = (i) => STAT_SRC_COLORS[i % STAT_SRC_COLORS.length];
  const kpis = `<div class="kpis" style="--n:4">
    ${kpi("Pull requests", `${v.total}`, `${years.map(([y, n]) => `${y}: ${n}`).join(" · ")}`)}
    ${kpi("Merged", `${st.merged}`, `${v.total ? Math.round((100 * st.merged) / v.total) : 0}% · ${st.open} open · ${st.closed} closed`)}
    ${kpi("By source", srcs.map((s) => v.by_source[s]).join(" / "), srcs.map(esc).join(" / "))}
    ${kpi("Weekly trend", `${v.weekly.slope >= 0 ? "+" : ""}${v.weekly.slope}<small>/wk</small>`, "slope of the weekly fit, current week left out")}</div>`;
  const range = STAT_RANGES.find(([id]) => id === S.filters.pr_range) || STAT_RANGES[1], span = range[2];
  const toolbar = `<div class="toolbar">${chips("pr_stats", [["all", "All PRs", rep.views.all.total], ["no_batch", "Without campaigns", rep.views.no_batch.total]], nb ? "no_batch" : "all")}
    ${chips("pr_range", STAT_RANGES.map(([id, label]) => [id, label]), range[0])}</div>`;
  const since = (n) => (span ? new Date(Date.now() - n * 864e5).toISOString().slice(0, 10) : "");
  // A month or week stays while any of its days is in range; "-31" sorts after every real day of the month.
  const from = (keys, end, n) => Math.min(keys.length - 1, Math.max(0, keys.findIndex((k) => end(k) >= since(n))));
  const mi = from(v.months, (m) => `${m}-31`, span), wi = from(v.weeks, (w) => w, span + 6);
  const stateSeries = (b, i) => STAT_STATE.map(([k, name, color]) => ({ name, color, values: b[k].slice(i) }));
  const months = v.months.slice(mi).map(statMonth), mTitles = v.months.slice(mi);
  const monthly = statBars({ labels: months, titles: mTitles, series: stateSeries(v.monthly, mi), lines: [{ name: "Trend", values: v.monthly.trend.slice(mi), color: "var(--ink-2)", dash: true }], every: 1 });
  const bySource = statBars({ labels: months, titles: mTitles, series: srcs.map((s, i) => ({ name: s, color: srcColor(i), values: v.monthly.by_source[s].slice(mi) })), height: 180, every: 1 });
  const weeks = v.weeks.slice(wi).map((w) => `${+w.slice(5, 7)}/${+w.slice(8)}`);
  const weekly = statBars({ labels: weeks, titles: v.weeks.slice(wi).map((w) => `week of ${w}`), series: stateSeries(v.weekly, wi),
    lines: [{ name: "4-week average", values: v.weekly.ma4.slice(wi), color: "var(--warn)" }, { name: "Trend", values: v.weekly.trend.slice(wi), color: "var(--ink-2)", dash: true }] });
  const pmax = Math.max(1, ...v.punch.flat()), days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const punch = `<table class="punch"><thead><tr><th></th>${[...Array(24).keys()].map((h) => `<th>${h % 3 ? "" : h}</th>`).join("")}</tr></thead><tbody>${v.punch.map((row, d) =>
    `<tr><th>${days[d]}</th>${row.map((n, h) => `<td title="${days[d]} ${h}:00 · ${n}" style="${n ? `background:color-mix(in srgb, var(--accent) ${Math.round(12 + (88 * n) / pmax)}%, transparent)` : ""}"></td>`).join("")}</tr>`).join("")}</tbody></table>`;
  const rmax = Math.max(1, ...v.repos.map((r) => r[1]));
  const repos = v.repos.map(([r, n]) => `<div class="repo-row"><span class="mono ellipsis">${esc(r)}</span><span class="repo-bar"><span style="width:${(100 * n) / rmax}%"></span></span><span class="num">${n}</span></div>`).join("");
  const card = (title, aside, body, wide) => `<div class="card ${wide ? "wide" : ""}"><div class="card-head"><h3>${title}</h3><span class="aside">${aside}</span></div>${body}</div>`;
  const stateLegend = STAT_STATE.map(([, name, color]) => [name, color]);
  return headHtml + failure + kpis + toolbar + `<div class="grid">
    ${card("Monthly", statLegend([...stateLegend, ["Trend", "var(--ink-2)", true]]), monthly, true)}
    ${card("By source", statLegend(srcs.map((s, i) => [s, srcColor(i)])), bySource, true)}
    ${card("Weekly", statLegend([...stateLegend, ["4-week average", "var(--warn)", true], ["Trend", "var(--ink-2)", true]]), weekly, true)}</div>
    <div class="grid">${card("Weekday × hour", "", punch)}
    ${card("Repositories", "top 10", `<div class="repo-list">${repos || '<div class="empty-chart">No pull requests.</div>'}</div>`)}</div>`;
}
// The view is an HTML string, so a bar has no listener of its own; statTip hides only what this handler showed, since
// the program charts in app.js share #tip.
document.addEventListener("pointermove", (e) => {
  const band = e.target.closest?.(".chart [data-tip]");
  if (band) { showTip(band.dataset.tip, e.clientX, e.clientY); F.statTip = true; }
  else if (F.statTip) { hideTip(); F.statTip = false; }
});
document.addEventListener("pointerout", (e) => { if (!e.relatedTarget && F.statTip) { hideTip(); F.statTip = false; } });
const FLEET_VIEWS = { overview: fleetOverview, inbox: fleetInbox, prs: fleetPrs, brief: fleetBrief, stages: fleetStages, todo: fleetTodo, automations: fleetAutomations, monitors: fleetMonitors, sent: fleetSent, skills: fleetSkills, changes: fleetChanges, tokens: fleetTokens, flow: fleetFlow, stats: fleetStats, graph: fleetGraph, sessions: fleetSessions, session: fleetSession, timeline: fleetTimeline };

document.addEventListener("input", (e) => {
  const key = e.target.id === "chat-input" ? F.sessionId : e.target.dataset.decideInput;
  if (key) F.draft[key] = e.target.value.replace(/[\r\n]+/g, " ");
});
// A clipped description opens in place and closes again; one that already fits has nothing to open.
function tlToggle(desc) {
  const id = desc.dataset.tlOpen, open = !F.tlOpen.has(id);
  if (open && desc.scrollHeight <= desc.clientHeight + 1) return false;
  if (open) F.tlOpen.add(id); else F.tlOpen.delete(id);
  desc.classList.toggle("open", open); desc.setAttribute("aria-expanded", String(open));
  return true;
}

document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.dataset?.tlOpen != null) { e.preventDefault(); tlToggle(e.target); return; }
  if (e.key !== "Enter" || e.isComposing) return;
  if (e.target.id === "chat-input") { e.preventDefault(); chatAction("ask"); } else if (e.target.dataset.decideInput) { e.preventDefault(); decideAction(e.target.dataset.decideInput, "text"); }
});
document.addEventListener("click", async (e) => {
  const chat = e.target.closest("[data-chat]");
  if (chat) { chatAction(chat.dataset.chat); return; }
  const opener = e.target.closest("[data-open-session]");
  if (opener) { openSession(opener); return; }
  if (e.target.closest("[data-open-close]")) { F.open = null; showOpenNote(); return; }
  const lead = e.target.closest("[data-fold-lead]");
  if (lead) { foldLead(lead.dataset.foldLead); renderView(); return; }
  const answer = e.target.closest("[data-prompt]");
  if (answer) { promptAction(answer.dataset.key, answer.dataset.prompt); return; }
  const decide = e.target.closest("[data-decide]");
  if (decide) { decideAction(decide.dataset.key, decide.dataset.decide); return; }
  const more = e.target.closest("[data-tl-more]");
  if (more) { const k = more.dataset.tlMore; F.tlShow[k] = (F.tlShow[k] || TL_STEP) + TL_STEP; renderView(); return; }
  const desc = e.target.closest("[data-tl-open]");
  if (desc && !String(getSelection()) && tlToggle(desc)) return;
  // A click on the row opens its session; its own controls, links and a text selection keep theirs.
  const go = e.target.closest("[data-go]");
  if (go && !e.target.closest("a,button,input,textarea,select") && !String(getSelection())) { location.hash = go.dataset.go; return; }
  const t = e.target.closest("[data-copy],[data-dismiss]");
  if (!t) return;
  if (t.dataset.copy != null) {
    try { await navigator.clipboard.writeText(t.dataset.copy); t.classList.add("done"); setTimeout(() => t.classList.remove("done"), 1200); } catch (err) { /* clipboard blocked: the text is on screen */ }
    return;
  }
  t.disabled = true;
  try { await fleetPost("/api/fleet/dismiss", { key: t.dataset.dismiss }); t.closest("li").remove(); } catch (err) { t.disabled = false; }
});
