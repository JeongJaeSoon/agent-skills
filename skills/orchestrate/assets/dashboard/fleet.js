"use strict";
// Fleet views: every Orca session on this machine as orchestrator -> orchestrations -> tasks, plus standalone sessions.
// Loaded before app.js and only called after it, so it may use app.js helpers ($, esc, icon, tag, relSpan, kpi, S...).
// Everything comes from /api/fleet/state, written by fleet.py.

const FLEET_SECTIONS = [
  { id: "overview", label: "Overview", icon: "overview" },
  { id: "inbox", label: "Inbox", icon: "inbox" },
  { id: "graph", label: "Graph", icon: "graph" },
  { id: "sessions", label: "Sessions", icon: "workers" },
  { id: "timeline", label: "Timeline", icon: "activity" },
];

// Freshness thresholds in seconds, [warn, stale], in the shape app.js SOURCE_META uses.
const FLEET_SOURCES = [
  ["orca", "Orca", () => [30, 90]],
  ["runs", "Runs", () => [300, 900]],
  ["github", "GitHub", () => [600, 1800]],
];

// type -> [label, tone, icon]
const ITEM = {
  prompt: ["Pending confirmation", "warn", "lock"],
  permission: ["Waiting on a prompt", "warn", "lock"],
  approval: ["Approval", "warn", "user"],
  question: ["Question", "accent", "chat"],
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
};
const itemMeta = (t) => ITEM[t] || [t, "", "dot"];

// phase -> what its dot means; the colour is .ph-<phase> in style.css, the same on every screen.
const PHASE = { working: "working", waiting: "waiting on you", idle: "idle, turn ended", open: "open, no agent", offline: "offline" };
// Reviewer edge status -> [css class, label]
const EDGE = {
  approved: ["tone-good", "approved"], approved_stale: ["tone-warn", "approved, old commit"], changes_requested: ["tone-bad", "changes requested"],
  commented: ["tone-accent", "commented"], requested: ["req", "review requested"], none: ["", "—"],
};
const CI_TONE = { success: "good", failure: "bad", pending: "warn" };

const F = { seenSent: {}, token: null, draft: {}, confirm: null, chat: {}, arm: null, prompt: {} };

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
const badge = (n, missed) => n ? `<span class="badge ${missed ? "missed" : ""}" title="${n} unread${missed ? `, ${missed} missed` : ""}">${n}</span>` : "";

async function fleetPost(path, body, retry = true) {
  if (!F.token) F.token = (await (await fetch("/api/fleet/token", { cache: "no-store" })).json()).token;
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Dash-Token": F.token }, body: JSON.stringify(body) });
  // A server restart (ensure restarts on a code change) rotates the token: fetch it again once.
  if (r.status === 403 && retry) { F.token = null; return fleetPost(path, body, false); }
  const out = await r.json().catch(() => ({}));
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
  const counts = st ? { inbox: (st.items || []).length || null, sessions: (st.sessions || []).length, graph: (st.prs || []).length || null } : {};
  $("#section-list").innerHTML = FLEET_SECTIONS.map((s) => `<li><a class="nav-item" href="#/fleet/${s.id}"
      ${s.id === S.section ? 'aria-current="page"' : ""} title="${s.label}">${icon(s.icon)}<span class="sb-text">${s.label}</span>
      ${counts[s.id] != null ? `<span class="count">${counts[s.id]}</span>` : ""}</a></li>`).join("");
  const group = $("#session-group");
  group.hidden = !st;
  if (!st) return;
  const live = sessionTree(st).filter(([s]) => s.phase !== "offline" || s.unread || s.kind === "orchestrator");
  $("#session-list").innerHTML = live.map(([s, d]) => `<li><a class="nav-item tree" style="--d:${Math.min(d, 3)}" href="${sessionHref(s.id)}"
      ${F.sessionId === s.id ? 'aria-current="page"' : ""} title="${esc(s.name)} · ${esc(s.phase)}">${phaseDot(s)}
      <span class="sb-text">${esc(s.name)}</span><span class="slot">${badge(s.unread, s.missed)}</span></a></li>`).join("") || `<li class="sb-text muted" style="padding:0 8px">No sessions</li>`;
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
  const errs = Object.entries(st.sources || {}).filter(([, v]) => v.ok === false).length;
  $("#top-right").innerHTML = [missed ? `<a href="#/fleet/inbox">${tag(`${missed} missed`, "bad", "alert")}</a>` : "",
    errs ? tag(`${errs} source error${errs > 1 ? "s" : ""}`, "warn", "alert") : ""].join("");
}

// ------------------------------------------------------------ pieces

function itemRow(st, it, { showSession = true } = {}) {
  const [label, tone, ic] = itemMeta(it.type), s = byId(st)[it.session];
  const copy = it.command || it.url || "";
  return `<li class="item ${it.missed ? "is-missed" : ""}"><span class="ic tone-${tone}">${icon(ic)}</span>
    <div class="grow"><div class="ellipsis"><b>${esc(label)}</b> <span class="dim">${esc(it.title || "")}</span></div>
      <div class="sub muted ellipsis">${showSession && s ? `<a href="${sessionHref(s.id)}">${esc(s.name)}</a> · ` : ""}${it.pr ? `<span class="mono">${esc(it.pr)}</span> · ` : ""}${esc(it.source || "")}
      ${it.command ? ` · <span class="mono">${esc(it.command)}</span>` : it.detail ? ` · ${esc(String(it.detail).split("\n")[0])}` : ""}</div>
      ${it.type === "prompt" ? promptActs(it) : ""}</div>
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

async function promptAction(key, action) {
  const it = ((S.state || {}).items || []).find((i) => i.key === key);
  if (!it) return;
  if (action !== "open" && !(F.arm && F.arm.key === key && F.arm.action === action)) {
    F.arm = { key, action };
    clearTimeout(F.armTimer);
    F.armTimer = setTimeout(() => { F.arm = null; renderView(); }, 5000);
    renderView();
    return;
  }
  F.arm = null; F.prompt[key] = { busy: true }; renderView();
  let out;
  try { out = await fleetPost("/api/fleet/prompt", { session: it.session, prompt: it.prompt, action }); } catch (err) { out = { ok: false, reason: String(err.message || err) }; }
  F.prompt[key] = { action, ...out };
  renderView();
}

function itemList(st, items, opts) {
  const pinned = [...items].sort((a, b) => b.missed - a.missed);
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

function avatarNode(x, y, r, login) {
  const init = esc(login.replace(/\[bot\]$/, "").slice(0, 2));
  return `<g><circle cx="${x}" cy="${y}" r="${r}" class="av-bg"/><text x="${x}" y="${y + 3.5}" text-anchor="middle" class="av-tx">${init}</text>
    <image href="/api/fleet/avatar/${encodeURIComponent(login)}" x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" clip-path="circle(${r}px)" onerror="this.remove()"/></g>`;
}

// Session -> PR -> reviewer, three columns; each PR is as tall as its reviewer list.
function relGraph(st, sessionId, openOnly = false) {
  const ss = byId(st);
  const prs = (st.prs || []).filter((p) => (!sessionId || p.session === sessionId) && (!openOnly || p.state === "OPEN"));
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
      <text x="${X[0] + 26}" y="${sy + 4}">${esc((s.name || "").slice(0, 26))}</text></a><title>${esc(s.name)} · ${esc(s.kind || "")} · ${esc(s.phase)}</title></g>`);
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
    <div class="grid">
      <div class="card"><div class="card-head"><h3>Needs you</h3><span class="aside"><a class="go" href="#/fleet/inbox">Inbox ${icon("arrow")}</a></span></div>${itemList(st, items.slice(0, 8))}</div>
      <div class="card" data-src="orca"><div class="card-head"><h3>Moving now</h3><span class="aside">${n("working")} working</span></div>${sessionRows(st, ss.filter((s) => ["working", "waiting"].includes(s.phase)))}</div>
      <div class="card wide" data-src="github"><div class="card-head"><h3>Open pull requests</h3><span class="aside"><a class="go" href="#/fleet/graph">All ${icon("arrow")}</a></span></div>${relGraph(st, null, true)}</div>
      <div class="card wide"><div class="card-head"><h3>Timeline</h3><span class="aside"><a class="go" href="#/fleet/timeline">All ${icon("arrow")}</a></span></div>${timelineList(st, (st.timeline || []).slice(0, 12))}</div>
    </div>`;
}

function sessionRows(st, list) {
  return `<ul class="rows">${list.map((s) => {
    const a = (s.agents || [])[0];
    const doing = a ? (a.state === "working" ? `${a.tool || ""} ${a.detail || ""}` : a.state === "waiting" ? "waiting on a prompt" : "") : "";
    return `<li>${phaseDot(s)}<a class="grow ellipsis" href="${sessionHref(s.id)}"><b>${esc(s.name)}</b> <span class="muted">${esc(doing.trim())}</span></a>
      <span class="acts"><span class="slot">${badge(s.unread, s.missed)}</span><span class="when">${relSpan(s.last_activity)}</span></span></li>`;
  }).join("") || '<li class="muted">Nothing running.</li>'}</ul>`;
}

function fleetInbox(st) {
  const items = st.items || [], counts = st.counts || {};
  const type = counts[S.filters.fleet_inbox] ? S.filters.fleet_inbox : "all";
  const shown = type === "all" ? items : items.filter((i) => i.type === type);
  return `<div class="view-head"><div><h2>Inbox</h2><p>Things a session is waiting on you for. Missed items (raised before the session's latest prompt) stay pinned until dismissed.</p></div></div>
    ${chips("fleet_inbox", [["all", "All", items.length], ...Object.entries(counts).map(([t, n]) => [t, itemMeta(t)[0], n])], type)}
    <div class="card">${itemList(st, shown)}</div>`;
}

function fleetGraph(st) {
  return `<div class="view-head"><div><h2>Graph</h2><p>Edge colour is the reviewer's latest state; the bar on each PR is its CI. Only sessions with a linked pull request are drawn.</p></div></div>
    <div class="card" data-src="github">${relGraph(st)}</div>`;
}

function fleetSessions(st) {
  const rows = sessionTree(st).map(([s, d]) => {
    const pr = (st.prs || []).find((p) => p.session === s.id);
    return `<tr><td><a class="tree-cell" style="--d:${Math.min(d, 4)}" href="${sessionHref(s.id)}">${phaseDot(s)}<b>${esc(s.name)}</b></a></td>
      <td>${tag(s.kind, s.kind === "orchestrator" ? "accent" : "", "")}</td><td>${esc(s.phase)}</td>
      <td class="mono dim hide-md">${esc(s.repo_name || "")}${s.branch ? ` · ${esc(s.branch)}` : ""}</td>
      <td>${pr ? link(pr.url, `#${esc(pr.number)}`) : ""}</td><td class="num">${badge(s.unread, s.missed)}</td><td class="num hide-sm when">${relSpan(s.last_activity)}</td></tr>`;
  }).join("");
  return `<div class="view-head"><div><h2>Sessions</h2><p>Every Orca worktree, placed under the orchestrator by run membership. Standalone sessions you opened yourself sit under the root.</p></div>${phaseLegend()}</div>
    <div class="card table-wrap"><table><thead><tr><th>Session</th><th>Kind</th><th>Phase</th><th class="hide-md">Repo · branch</th><th>PR</th><th class="num">Unread</th><th class="num hide-sm">Active</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function fleetSession(st) {
  const s = byId(st)[F.sessionId];
  if (!s) return `<div class="loading">This session is gone from Orca. <a class="link" href="#/fleet/sessions">All sessions</a></div>`;
  if (s.unread && F.seenSent[s.id] !== s.unread) {
    F.seenSent[s.id] = s.unread;
    fleetPost("/api/fleet/seen", { session: s.id }).catch(() => { delete F.seenSent[s.id]; });
  }
  const parent = byId(st)[s.parent];
  const agents = (s.agents || []).map((a) => `<li class="agent"><div class="row">${tag(a.state, a.state === "waiting" ? "warn" : a.state === "working" ? "accent" : "good")}
      <span class="mono dim">${esc(a.type || "")}${a.mode ? ` · ${esc(a.mode)}` : ""}</span><span class="grow"></span>${relSpan(a.updated || a.since)}</div>
      ${a.prompt ? `<div class="kv"><span class="eyebrow">prompt</span><div class="pre">${esc(a.prompt)}</div></div>` : ""}
      ${a.state === "working" && a.tool ? `<div class="kv"><span class="eyebrow">now</span><div class="mono">${esc(a.tool)} ${esc(a.detail || "")}</div></div>` : ""}
      ${a.last ? `<div class="kv"><span class="eyebrow">last reply</span><div class="pre">${esc(a.last)}</div></div>` : ""}</li>`).join("");
  const items = (st.items || []).filter((i) => i.session === s.id);
  const tl = (st.timeline || []).filter((e) => e.session === s.id).slice(0, 30);
  return `<div class="view-head"><div><h2>${phaseDot(s)} ${esc(s.name)}</h2>
      <p>${tag(s.kind, "", "")} ${esc(s.phase)}${parent ? ` · under <a class="link" href="${sessionHref(parent.id)}">${esc(parent.name)}</a>` : ""}
      ${s.task_title ? ` · task: ${esc(s.task_title)}` : ""} · <span class="mono">${esc(s.repo_name || "")}${s.branch ? ` · ${esc(s.branch)}` : ""}</span></p></div></div>
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

const FLEET_VIEWS = { overview: fleetOverview, inbox: fleetInbox, graph: fleetGraph, sessions: fleetSessions, session: fleetSession, timeline: fleetTimeline };

document.addEventListener("input", (e) => { if (e.target.id === "chat-input" && F.sessionId) F.draft[F.sessionId] = e.target.value.replace(/[\r\n]+/g, " "); });
document.addEventListener("keydown", (e) => { if (e.target.id === "chat-input" && e.key === "Enter" && !e.isComposing) { e.preventDefault(); chatAction("ask"); } });
document.addEventListener("click", async (e) => {
  const chat = e.target.closest("[data-chat]");
  if (chat) { chatAction(chat.dataset.chat); return; }
  const answer = e.target.closest("[data-prompt]");
  if (answer) { promptAction(answer.dataset.key, answer.dataset.prompt); return; }
  const t = e.target.closest("[data-copy],[data-dismiss]");
  if (!t) return;
  if (t.dataset.copy != null) {
    try { await navigator.clipboard.writeText(t.dataset.copy); t.classList.add("done"); setTimeout(() => t.classList.remove("done"), 1200); } catch (err) { /* clipboard blocked: the text is on screen */ }
    return;
  }
  t.disabled = true;
  try { await fleetPost("/api/fleet/dismiss", { key: t.dataset.dismiss }); t.closest("li").remove(); } catch (err) { t.disabled = false; }
});
