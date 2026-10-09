"use strict";
// Fleet views: every Orca session on this machine as orchestrator -> orchestrations -> tasks, plus standalone sessions.
// Loaded before app.js and only called after it, so it may use app.js helpers ($, esc, icon, tag, relSpan, kpi, S...).
// Everything comes from /api/fleet/state, written by fleet.py.

const FLEET_SECTIONS = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "prs", label: "Pull requests", icon: "prs" },
  { id: "work", label: "작업 진행", icon: "flag" },
  { id: "automations", label: "자동화", icon: "clock" },
  { id: "skills", label: "Skills", icon: "spark" },
  { id: "graph", label: "Graph", icon: "graph" },
  { id: "sessions", label: "Sessions", icon: "workers" },
  { id: "timeline", label: "Timeline", icon: "activity" },
];

// Freshness thresholds in seconds, [warn, stale], in the shape app.js SOURCE_META uses.
const FLEET_SOURCES = [
  ["orca", "Orca", () => [30, 90]],
  ["runs", "Runs", () => [300, 900]],
  ["github", "GitHub", () => [600, 1800]],
  ["automations", "Automations", () => [300, 900]],
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
};
const itemMeta = (t) => ITEM[t] || [t, "", "dot"];
// What a session's kind reads as on screen -> [label, tone, icon]. kind itself stays as fleet.py wrote it (sort, adopt).
// A lead is a coordinator with a Run of its own under the root; a standalone it made with `orca worktree create` is its worker.
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

const F = { token: null, draft: {}, confirm: null, chat: {}, arm: null, prompt: {}, decide: {} };

// route() hands over "#/fleet/<section>[/<arg>]"; the only argument is a session id.
function fleetSection(section, arg) {
  F.sessionId = section === "session" && arg ? arg : null;
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
    prs: (st.prs || []).filter((p) => p.state === "OPEN").length || null, work: workOpen() || null, automations: autoTroubled(st).length || null, graph: (st.prs || []).filter((p) => p.session).length || null } : {};
  $("#section-list").innerHTML = FLEET_SECTIONS.map((s) => `<li><a class="nav-item" href="#/fleet/${s.id}"
      ${s.id === S.section ? 'aria-current="page"' : ""} title="${s.label}">${icon(s.icon)}<span class="sb-text">${s.label}</span>
      ${counts[s.id] != null ? `<span class="count">${counts[s.id]}</span>` : ""}</a></li>`).join("");
  const group = $("#session-group");
  group.hidden = !st;
  if (!st) return;
  const live = sessionTree(st).filter(([s]) => s.phase !== "offline" || s.unread || s.kind === "orchestrator");
  const kids = (s) => (st.sessions || []).filter((x) => x.parent === s.id).length;
  $("#session-list").innerHTML = live.map(([s, d]) => `<li><a class="nav-item tree ${s.kind === "orchestration" ? "lead" : ""}" style="--d:${Math.min(d, 3)}" href="${sessionHref(s.id)}"
      ${F.sessionId === s.id ? 'aria-current="page"' : ""} title="${esc(s.name)} · ${esc(kindLabel(st, s)[0])} · ${esc(s.phase)}">${phaseDot(s)}
      <span class="sb-text">${esc(s.name)}</span>${s.kind === "orchestration" ? `<span class="tag tone-accent" title="lead · ${kids(s)} session${kids(s) === 1 ? "" : "s"} under it">lead${kids(s) ? ` ${kids(s)}` : ""}</span>` : ""}<span class="slot">${badge(s.unread, s.missed)}</span></a></li>`).join("") || `<li class="sb-text muted" style="padding:0 8px">No sessions</li>`;
}

function fleetHeader() {
  const st = S.state;
  if (!st) { $("#headline").innerHTML = "<h1>Fleet</h1>"; $("#top-right").innerHTML = ""; return; }
  const root = byId(st)[st.root];
  const n = (p) => (st.sessions || []).filter((s) => s.phase === p).length;
  $("#headline").innerHTML = `<h1>Fleet${root ? ` · <span class="dim">${esc(root.name)}</span>` : ""}</h1>
    <div class="meta"><span>${(st.sessions || []).length} sessions</span><span>${n("working")} working</span><span>${n("waiting")} waiting</span>
      <span>${(st.runs || []).length} runs</span>${st.me ? `<span class="mono">@${esc(st.me)}</span>` : ""}</div>`;
  const missed = (st.items || []).filter((i) => i.missed).length;
  const errs = Object.entries(st.sources || {}).filter(([, v]) => v.ok === false).length, troubled = autoTroubled(st).length;
  $("#top-right").innerHTML = [missed ? `<a href="#/fleet/inbox">${tag(`${missed} missed`, "bad", "alert")}</a>` : "",
    troubled ? `<a href="#/fleet/automations">${tag(`자동화 ▲${troubled}`, "bad", "alert")}</a>` : "",
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
    <div class="grow"><div class="ellipsis"><b>${esc(label)}</b> <span class="dim">${esc(it.title || "")}</span></div>
      <div class="sub muted ellipsis">${projBadge(it.project)}${showSession && s ? `<a href="${sessionHref(s.id)}">${esc(s.name)}</a> · ` : ""}${it.pr ? `<span class="mono">${esc(it.pr)}</span> · ` : ""}${esc(it.source || "")}
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
// records the answer and closes the decision first, then types "decision <id>: <answer>" into the coordinator's terminal
// now if it is idle, or on a later collect once it is: a busy coordinator never makes the answer fail.
function decisionActs(st, it) {
  const r = F.decide[it.key], armed = F.arm && F.arm.key === it.key ? F.arm.action : "", off = r && r.busy ? "disabled" : "";
  const opts = it.options || [];
  const chip = (action, label, extra = "") => `<button class="chip ${armed === action ? "armed tone-warn" : ""}" data-decide="${action}" data-key="${esc(it.key)}" ${extra} ${off}>${label}</button>`;
  const buttons = opts.map((o, i) => chip(`opt:${i}`, `${it.recommend === i + 1 ? icon("check") : ""}${armed === `opt:${i}` ? "Confirm " : ""}${esc(o.label)}`,
    `title="${esc(o.description || o.label)}${it.recommend === i + 1 ? " (recommended)" : ""}"`)).join("");
  const line = armed ? `decision ${it.decision}: ${armed === "text" ? (F.draft[it.key] || "").trim() : (opts[+armed.slice(4)] || {}).label}` : "";
  const msg = !r || r.busy ? "" : !r.ok ? `Not recorded: ${r.reason}.`
    : r.delivered ? "Recorded and sent to the coordinator." : "Recorded; will relay to the coordinator when it is idle.";
  const hint = `<div class="sub muted">Or answer in the coordinator's terminal: <span class="mono">${esc(it.decision)}: ${esc((opts[it.recommend - 1] || {}).label || "<answer>")}</span></div>`;
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

function timelineList(st, rows) {
  const ss = byId(st);
  let day = "";
  return `<ul class="rows feed">${rows.map((e) => {
    const d = e.at ? dayLabel(e.at) : "", head = d !== day ? `<li class="day eyebrow">${esc((day = d))}</li>` : "";
    const s = ss[e.session], tone = /fail|changes|ci_/.test(e.kind) ? "bad" : /approved|done|ok/.test(e.kind) ? "good" : /mail|prompt/.test(e.kind) ? "accent" : "";
    return `${head}<li class="ev note ${tone ? "tone-" + tone : ""}"><span class="tm">${e.at ? hhmm(e.at) : ""}</span>
      <span class="tx"><span class="mono muted">${esc(e.kind)}</span> ${esc(e.text || "")}${s ? `<a class="by" href="${sessionHref(s.id)}">${esc(s.name)}</a>` : ""}</span></li>`;
  }).join("") || '<li class="muted">No events yet.</li>'}</ul>`;
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
        <text x="${X[1] + 12}" y="${mid + 4}"><tspan class="num">#${esc(p.number)}</tspan> ${esc((p.title || "").slice(0, 34))}</text></a>
        <title>${esc(p.key)} · ${esc(p.state)} · CI ${esc(p.ci || "none")} · ${esc(p.decision || "no decision")}</title></g>`);
      revs.forEach((r, i) => {
        const ry = y + i * RH + 13, [cls, label] = EDGE[r.status] || EDGE.none;
        out.push(`<path class="edge rv ${cls}" d="M${X[1] + 300} ${mid} C ${X[1] + 360} ${mid}, ${X[2] - 60} ${ry}, ${X[2] - 12} ${ry}"><title>${esc(r.login)}: ${esc(label)}</title></path>
          ${avatarNode(X[2], ry, 10, r.login)}<text class="rv-name" x="${X[2] + 16}" y="${ry + 4}">${esc(r.login)} <tspan class="muted">${esc(label)}</tspan></text>`);
      });
      if (!revs.length) out.push(`<text class="rv-name muted" x="${X[2] - 12}" y="${mid + 4}">no reviewer</text>`);
      y += h + 6;
      return [mid, ci];
    });
    const sy = (top + y - 6) / 2 - 1;
    mids.forEach(([mid, ci]) => out.push(`<path class="edge tone-${ci}" d="M${X[0] + 232} ${sy} C ${X[0] + 262} ${sy}, ${X[1] - 30} ${mid}, ${X[1]} ${mid}"/>`));
    out.push(`<g class="sess"><a href="${sessionHref(sid)}"><rect class="box" x="${X[0]}" y="${sy - 15}" width="232" height="30" rx="6"/>
      <circle class="ph ph-${esc(s.phase)}" cx="${X[0] + 14}" cy="${sy}" r="4"/>
      <text x="${X[0] + 26}" y="${sy + 4}">${s.project ? `<tspan class="proj-tx">${esc(s.project.slice(0, 14))}</tspan> ` : ""}${esc((s.name || "").slice(0, s.project ? 24 - Math.min(14, s.project.length) : 26))}</text></a>
      <title>${esc(s.project || "")} · ${esc(s.name)} · ${esc(s.kind || "")} · ${esc(s.phase)}</title></g>`);
    y += 10;
  }
  return `<div class="relgraph"><svg viewBox="0 0 ${W} ${y}" style="min-width:720px" role="img" aria-label="Sessions, pull requests and reviewers">${out.join("")}</svg></div>
    <div class="legend" style="padding:0 var(--s4) var(--s3)">${Object.entries(EDGE).filter(([k]) => k !== "none")
      .map(([, [cls, label]]) => `<span><i class="key ${cls}"></i>${label}</span>`).join("")}${phaseKeys()}</div>`;
}

// ------------------------------------------------------------ views

function fleetOverview(st) {
  const ss = st.sessions || [], items = st.items || [];
  const n = (p) => ss.filter((s) => s.phase === p).length;
  const open = (st.prs || []).filter((p) => p.state === "OPEN");
  const missed = items.filter((i) => i.missed).length;
  return `<div class="kpis" style="--n:4">
      ${kpi("Sessions", `${ss.length}`, phaseLegend(["working", "waiting", "idle"], n), { src: "orca" })}
      ${kpi("Needs you", `${items.length}${missed ? ` ${tag(`${missed} missed`, "bad", "alert")}` : ""}`, Object.entries(st.counts || {}).map(([t, c]) => `${itemMeta(t)[0]} ${c}`).join(" · ") || "inbox empty")}
      ${kpi("Open PRs", `${open.length}`, `${open.filter((p) => p.ci === "failure").length} CI failing · ${open.filter((p) => p.decision === "APPROVED").length} approved`, { src: "github" })}
      ${kpi("Runs", `${(st.runs || []).length}`, `${(st.tasks || []).filter((t) => t.status !== "completed").length} open tasks`, { src: "runs" })}
    </div>
    ${projectChips(st)}
    <div class="grid">
      <div class="card"><div class="card-head"><h3>Needs you</h3><span class="aside"><a class="go" href="#/fleet/inbox">Inbox ${icon("arrow")}</a></span></div>${itemList(st, items, { limit: 8 })}</div>
      <div class="card" data-src="orca"><div class="card-head"><h3>Moving now</h3><span class="aside">${n("working")} working</span></div>${sessionRows(st, ss.filter((s) => ["working", "waiting"].includes(s.phase)))}</div>
      ${leadCard(st)}
      <div class="card wide" data-src="github"><div class="card-head"><h3>Open pull requests</h3><span class="aside">${prUpdated(st)}<a class="go" href="#/fleet/prs">All ${open.length} ${icon("arrow")}</a></span></div>${prRows(st, open.slice(0, 8))}</div>
      <div class="card wide"><div class="card-head"><h3>Timeline</h3><span class="aside"><a class="go" href="#/fleet/timeline">All ${icon("arrow")}</a></span></div>${timelineList(st, (st.timeline || []).slice(0, 12))}</div>
    </div>`;
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
  return `<div class="card wide" data-src="orca"><div class="card-head"><h3>Leads</h3><span class="aside">${leads.length}</span></div><ul class="rows">${rows}</ul></div>`;
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
  misses: ["misses", "bad", "Prompts carried one of its quoted trigger phrases and it did not fire (heuristic)"],
};
const SKILL_FILTERS = {
  all: [() => true, "All"],
  flagged: [(r) => r.flags.length, "Flagged"],
  unused_30d: [(r) => r.flags.includes("unused_30d"), "Unused 30 d"],
  slash_only: [(r) => r.flags.includes("slash_only"), "Slash only"],
  misses: [(r) => r.flags.includes("misses"), "Misses"],
};
const SKILL_SORT = {
  name: (r) => r.name, d7: (r) => r.uses_7d, d30: (r) => r.uses_30d, mix: (r) => r.auto_30d + r.chained_30d,
  last: (r) => ago(r.last_used), reach: (r) => r.sessions_30d, misses: (r) => r.misses_30d, flags: (r) => r.flags.length,
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
      <td class="num">${r.misses_30d ? `<span class="${r.flags.includes("misses") ? "tone-bad" : ""}">${r.misses_30d}</span>` : '<span class="muted">0</span>'}</td>
      <td>${r.flags.map((f) => `<span title="${esc((SKILL_FLAG[f] || [])[2] || f)}">${tag((SKILL_FLAG[f] || [f])[0], (SKILL_FLAG[f] || [])[1] || "")}</span>`).join(" ")}</td></tr>`).join("");
  return `<table><thead><tr>${head.map(([c, l, cls]) => th("fleet_skills", c, l, cls)).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}
function fleetSkills() {
  pollSkills();
  const rep = F.skills;
  const headHtml = `<div class="view-head"><div><h2>Skills</h2><p>How often each installed skill ran in local Claude Code transcripts, and how it was triggered: on its own (auto), loaded by another skill (chained), or typed as /name (slash). Misses are prompts that carried a quoted trigger phrase from the skill's description while it did not fire: a heuristic. Counts only; no transcript text leaves the machine.</p></div>
    ${rep && !rep.error ? `<div class="aside muted">Collected ${relSpan(rep.generated_at)} · ${rep.files} transcripts</div>` : ""}</div>`;
  if (!rep) return headHtml + '<div class="loading">Loading…</div>';
  if (rep.error) return headHtml + `<div class="card"><div class="empty-chart">${esc(rep.error)}</div></div>`;
  const all = rep.skills || [];
  const f = SKILL_FILTERS[S.filters.fleet_skills] ? S.filters.fleet_skills : "all";
  const srcs = (rep.sources || []).map((x) => x.source), src = srcs.includes(S.filters.fleet_skills_src) ? S.filters.fleet_skills_src : "all";
  const shown = all.filter((r) => SKILL_FILTERS[f][0](r) && (src === "all" || r.source === src));
  const srcChip = (v, label, n) => `<button class="chip" type="button" data-group="fleet_skills_src" data-val="${esc(v)}" aria-pressed="${v === src}">${esc(label)}${n != null ? `<span class="n">${n}</span>` : ""}</button>`;
  const editable = new Set(rep.editable || []);
  const mine = all.filter((r) => editable.has(r.source));
  const kpis = `<div class="kpis" style="--n:4">
    ${kpi("Skills used in 30 d", `${all.filter((r) => r.uses_30d).length}<small>/${all.length}</small>`, `${all.reduce((a, r) => a + r.uses_30d, 0)} invocations`)}
    ${kpi("Fire on their own", `${all.filter((r) => r.auto_30d + r.chained_30d).length}`, `${all.filter((r) => r.flags.includes("slash_only")).length} slash only`)}
    ${kpi("Flagged, editable", `${mine.filter((r) => r.flags.length).length}<small>/${mine.length}</small>`, `sources ${[...editable].join(", ")}`)}
    ${kpi("Improvement signals", `${rep.signals ? rep.signals.added : "—"}`, rep.signals ? "new this collect, one per skill and flag per week" : "signals off")}</div>`;
  const groups = {};
  shown.forEach((r) => (groups[r.source] ||= []).push(r));
  return headHtml + kpis + `<div class="toolbar">${chips("fleet_skills", Object.entries(SKILL_FILTERS).map(([id, [fn, label]]) => [id, label, all.filter(fn).length]), f)}</div>
    ${srcs.length > 1 ? `<div class="chips proj-chips" role="group" aria-label="Source">${srcChip("all", "All sources")}${(rep.sources || []).map((x) => srcChip(x.source, x.source, x.skills)).join("")}</div>` : ""}
    ${srcs.filter((x) => groups[x]).map((x) => `<div class="card table-wrap"><div class="card-head"><h3 class="mono">${esc(x)}</h3><span class="aside">${editable.has(x) ? tag("editable", "accent", "") + " " : ""}${groups[x].length} skills</span></div>${skillTable(groups[x])}</div>`).join("")
      || '<div class="card"><div class="empty-chart">No skill matches.</div></div>'}`;
}

// The `orch stage` table (stages.json, via /api/fleet/work): read on every poll while the page is open, so a cell
// written by the coordinator shows within one poll. The merge cell arrives already filled from the fleet PR list.
const WORK_MARK = { ok: ["✅", "good", "완료·확인"], fail: ["❌", "bad", "미완료·실패"], partial: ["⚠️", "warn", "일부·추정"],
  checking: ["🔄", "accent", "확인 중"], na: ["–", "", "해당 없음"] };
const WORK_DONE_KEEP = 7 * 86400e3;  // stages.py DONE_KEEP_S: a file nobody wrote to since can still hold older rows
const workShown = (w) => (w.rows || []).filter((r) => !r.done_at || Date.now() - ms(r.done_at) < WORK_DONE_KEEP);
const workOpen = () => F.work && !F.work.error ? workShown(F.work).filter((r) => !r.group && !r.done_at).length : 0;
async function pollWork() {
  if (F.workBusy) return;
  F.workBusy = true;
  try {
    const r = await fetch("/api/fleet/work", { cache: "no-store", headers: F.workEtag ? { "If-None-Match": F.workEtag } : {} });
    if (r.status !== 304) {
      F.work = r.ok ? await r.json() : { error: `HTTP ${r.status}` };
      F.workEtag = r.ok ? r.headers.get("ETag") : null;
      if (isFleet() && S.section === "work") renderView();
      if (isFleet()) fleetSidebar();
    }
  } catch (e) { F.work = { error: String(e.message || e) }; F.workEtag = null; }
  F.workBusy = false;
}
// owner/repo#N becomes a link; esc() runs first and the pattern holds no markup characters.
const linkRefs = (text) => esc(text).replace(/\b([\w.-]+\/[\w.-]+)#(\d+)\b/g, (m, repo, n) => link(`https://github.com/${repo}/pull/${n}`, m));
function workCell(c) {
  if (!c) return "";
  const [mark, tone, label] = WORK_MARK[c.mark] || [c.mark, "", c.mark];
  const text = [c.evidence && linkRefs(c.evidence), c.by && `<span class="muted">(${esc(c.by)})</span>`].filter(Boolean).join(" ");
  return `<div class="wcell ${tone ? "tone-" + tone : ""}" title="${esc(label)}"><span class="wmark">${mark}</span>${text ? `<span class="wtext">${text}</span>` : ""}</div>`;
}
function fleetWork() {
  if (!F.work) pollWork();
  const w = F.work;
  const headHtml = `<div class="view-head"><div><h2>작업 진행</h2><p><span class="mono">orch stage</span> 로 기록한 작업별 단계 표. 머지 칸은 PR 목록에서 채우고, dev·prod 칸은 코디네이터가 확인한 때 근거와 함께 쓴다. 끝난 행은 7일 동안 흐리게 남는다.</p></div>
    <div class="legend">${Object.values(WORK_MARK).map(([m, tone, l]) => `<span><span class="wmark">${m}</span>${l}</span>`).join("")}</div></div>`;
  if (!w) return headHtml + '<div class="loading">Loading…</div>';
  if (w.error) return headHtml + `<div class="card"><div class="empty-chart">${esc(w.error)}</div></div>`;
  const all = workShown(w), done = all.filter((r) => r.done_at && !r.group).length;
  const rows = F.workHideDone ? all.filter((r) => !r.done_at) : all;
  const byId = Object.fromEntries(all.map((r) => [r.id, r]));
  // A group's own rows sit flush under it; each level below them gets one ㄴ step.
  const level = {};
  rows.forEach((r) => { const p = byId[r.parent]; level[r.id] = !p || p.group ? 0 : (level[p.id] ?? 0) + 1; });
  const body = rows.map((r) => {
    if (r.group) return `<tr class="wgroup ${r.done_at ? "is-done" : ""}"><td colspan="6"><b>${esc(r.title)}</b></td></tr>`;
    const lv = level[r.id];
    return `<tr class="${r.done_at ? "is-done" : ""}"><td class="wfeat">${r.parent ? "" : '<span class="muted">–</span>'}</td>
      <td class="wtitle" style="--lv:${lv}">${lv ? '<span class="muted">ㄴ </span>' : ""}${esc(r.title)}${r.done_at ? ` <span class="muted">· 끝남 ${relSpan(r.done_at)}</span>` : ""}</td>
      <td class="mono nowrap">${r.pr ? linkRefs(r.pr) : '<span class="muted">–</span>'}</td>
      ${["merge", "dev", "prod"].map((c) => `<td>${workCell((r.cells || {})[c])}</td>`).join("")}</tr>`;
  }).join("");
  const { env = {} } = w;
  const envLine = ["prod", "dev"].filter((k) => env[k]).map((k) => `<span><b>${k}</b> <span class="mono">${esc(env[k])}</span></span>`).join("");
  const toggle = done ? `<button class="chip" type="button" data-work-done aria-pressed="${!!F.workHideDone}">끝난 행 ${done}개 접기</button>` : "";
  const table = all.length ? `<div class="card table-wrap"><table class="work"><thead><tr><th>기능/epic</th><th>작업</th><th>PR</th><th>PR 머지</th><th>dev 확인</th><th>prod 확인</th></tr></thead><tbody>${body}</tbody></table></div>`
    : '<div class="card"><div class="empty-chart">기록된 작업이 없다. <span class="mono">orch stage add</span> 로 행을 만든다.</div></div>';
  const next = (w.next || []).length ? `<div class="card"><div class="card-head"><h3>다음 순서</h3></div><ol class="card-body wnext">${w.next.map((n) => `<li>${linkRefs(n)}</li>`).join("")}</ol></div>` : "";
  return headHtml + `<div class="toolbar wenv">${envLine ? `<div class="meta">${envLine}${env.at ? `<span class="muted">${relSpan(env.at)}</span>` : ""}</div>` : ""}${toggle}</div>` + table + next;
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-work-done]")) { F.workHideDone = !F.workHideDone; renderView(); } });

// Orca automations (state.json automations). An enabled one is ▲ when its last run failed or its next run is 10 min
// overdue: the scheduler stopped or the app was closed. A switched-off one is never a problem.
const AUTO_RUN = { completed: ["■", "good", "완료"], failed: ["■", "bad", "실패"], error: ["■", "bad", "실패"], running: ["◧", "warn", "실행 중"],
  dispatched: ["◧", "warn", "실행 중"], skipped: ["□", "", "건너뜀"] };
const AUTO_STALL = 10 * 60e3;
function autoIssue(a) {
  if (!a.enabled) return "";
  if (/fail|error/.test(((a.recent || [])[0] || {}).status || "")) return "마지막 실행 실패";
  return a.next_run_at && Date.now() - ms(a.next_run_at) > AUTO_STALL ? "예정 시각 지남" : "";
}
const autoTroubled = (st) => (st.automations || []).filter(autoIssue);
// The few cron shapes automations use, in words; anything else stays as written.
function cadence(rrule) {
  const r = (rrule || "").trim(), m = (re) => r.match(re), pad = (x) => x.padStart(2, "0");
  let g;
  if ((g = m(/^(\d+) \*\/(\d+) \* \* \*$/))) return `${g[2]}시간마다 :${pad(g[1])}`;
  if ((g = m(/^\*\/(\d+) (\d+)-(\d+) \* \* 1-5$/))) return `평일 ${g[2]}–${g[3]}시 ${g[1]}분마다`;
  if ((g = m(/^\*\/(\d+) \* \* \* \*$/))) return `${g[1]}분마다`;
  if ((g = m(/^(\d+) (\d+) \* \* \*$/))) return `매일 ${g[2]}:${pad(g[1])}`;
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
  const headHtml = `<div class="view-head"><div><h2>자동화</h2><p>Orca automation 마다 주기, 다음·마지막 실행, 최근 6회 결과(왼쪽이 오래된 것). 마지막 실행이 실패했거나 예정 시각을 10분 넘기면 ▲ 로 표시한다. 세션 안의 CronCreate 와 launchd 작업은 수집하지 않는다.</p></div>
    <div class="legend">${legend}</div></div>`;
  const failed = src.ok === false ? `<div class="card"><div class="empty-chart">${tag("수집 실패", "warn", "alert")} Orca automation 목록을 읽지 못했다: ${esc(src.error || "")}. 아래는 ${rel(src.updated_at)} 값이다.</div></div>` : "";
  const rows = autos.map((a) => {
    const issue = autoIssue(a), last = (a.recent || [])[0];
    return `<tr class="${a.enabled ? "" : "is-done"}"><td><b>${esc(a.name)}</b>${a.enabled ? "" : ` ${tag("꺼짐")}`}${issue ? ` ${tag(`▲ ${issue}`, "bad", "")}` : ""}</td>
      <td class="nowrap">${esc(cadence(a.rrule))}</td><td class="nowrap ${issue === "예정 시각 지남" ? "toned tone-bad" : ""}">${a.enabled ? relSpan(a.next_run_at) : '<span class="muted">–</span>'}</td>
      <td class="nowrap muted">${a.last_run_at ? relSpan(a.last_run_at) : "–"}</td><td class="runs nowrap">${[...(a.recent || [])].reverse().map(runBlock).join("")}</td>
      <td class="dim"><span class="ellipsis" style="display:block">${last ? esc(last.summary || last.status || "") : '<span class="muted">실행 기록 없음</span>'}</span></td></tr>`;
  }).join("");
  const table = autos.length ? `<div class="card table-wrap" data-src="automations"><table class="autos"><thead><tr><th>이름</th><th>주기</th><th>다음</th><th>마지막</th><th>최근 6회</th><th>마지막 결과</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="card"><div class="empty-chart">등록된 Orca automation 이 없다.</div></div>';
  return headHtml + failed + table;
}

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

function fleetSessions(st) {
  const rows = sessionTree(st).filter(([s]) => inProject(s.project)).map(([s, d]) => {
    const pr = (st.prs || []).find((p) => p.session === s.id);
    return `<tr><td><a class="tree-cell" style="--d:${Math.min(d, 4)}" href="${sessionHref(s.id)}">${phaseDot(s)}<b>${esc(s.name)}</b></a></td>
      <td>${kindTag(st, s)}</td><td>${esc(s.phase)}</td>
      <td class="hide-md">${projBadge(s.project)}<span class="mono dim">${s.branch ? esc(s.branch) : ""}</span></td>
      <td>${pr ? link(pr.url, `#${esc(pr.number)}`) : ""}</td><td class="num">${badge(s.unread, s.missed)}</td><td class="num hide-sm when">${relSpan(s.last_activity)}</td></tr>`;
  }).join("");
  return `<div class="view-head"><div><h2>Sessions</h2><p>Every Orca worktree, placed under the coordinator whose run dispatched it, else under its Orca parent, else under the root. A lead is a coordinator with its own run; the sessions under it are its workers.</p></div>${phaseLegend()}</div>
    ${projectChips(st)}<div class="card table-wrap"><table><thead><tr><th>Session</th><th>Kind</th><th>Phase</th><th class="hide-md">Project · branch</th><th>PR</th><th class="num">Needs you</th><th class="num hide-sm">Active</th></tr></thead><tbody>${rows}</tbody></table></div>
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
  const tl = (st.timeline || []).filter((e) => e.session === s.id).slice(0, 30);
  return `<div class="view-head"><div><h2>${phaseDot(s)} ${esc(s.name)}</h2>
      <p>${kindTag(st, s)} ${esc(s.phase)}${parent ? ` · under <a class="link" href="${sessionHref(parent.id)}">${esc(parent.name)}</a>` : ""}
      ${s.task_title ? ` · task: ${esc(s.task_title)}` : ""} · ${projBadge(s.project)}<span class="mono">${s.branch ? esc(s.branch) : ""}</span></p></div></div>
    ${chatCard(s)}
    <div class="grid">
      <div class="card"><div class="card-head"><h3>Needs you</h3><span class="aside">${items.length}</span></div>${itemList(st, items, { showSession: false })}</div>
      <div class="card"><div class="card-head"><h3>Agents</h3><span class="aside">${(s.agents || []).length}</span></div><ul class="rows agents">${agents || '<li class="muted">No agent in this worktree.</li>'}</ul></div>
      <div class="card wide" data-src="github"><div class="card-head"><h3>Pull requests</h3></div>${relGraph(st, s.id)}</div>
      <div class="card wide"><div class="card-head"><h3>Timeline</h3></div>${timelineList(st, tl)}</div>
    </div>`;
}

function fleetTimeline(st) {
  return `<div class="view-head"><div><h2>Timeline</h2><p>Prompts, finished turns, PR review and CI changes, and orchestration mail, newest first.</p></div></div>
    <div class="card">${timelineList(st, st.timeline || [])}</div>`;
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

const FLEET_VIEWS = { overview: fleetOverview, inbox: fleetInbox, prs: fleetPrs, work: fleetWork, automations: fleetAutomations, skills: fleetSkills, graph: fleetGraph, sessions: fleetSessions, session: fleetSession, timeline: fleetTimeline };

document.addEventListener("input", (e) => {
  const key = e.target.id === "chat-input" ? F.sessionId : e.target.dataset.decideInput;
  if (key) F.draft[key] = e.target.value.replace(/[\r\n]+/g, " ");
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.isComposing) return;
  if (e.target.id === "chat-input") { e.preventDefault(); chatAction("ask"); } else if (e.target.dataset.decideInput) { e.preventDefault(); decideAction(e.target.dataset.decideInput, "text"); }
});
document.addEventListener("click", async (e) => {
  const chat = e.target.closest("[data-chat]");
  if (chat) { chatAction(chat.dataset.chat); return; }
  const answer = e.target.closest("[data-prompt]");
  if (answer) { promptAction(answer.dataset.key, answer.dataset.prompt); return; }
  const decide = e.target.closest("[data-decide]");
  if (decide) { decideAction(decide.dataset.key, decide.dataset.decide); return; }
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
