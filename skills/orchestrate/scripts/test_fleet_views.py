#!/usr/bin/env python3
"""The fleet menu's merged pages: an old address opens the view it moved to, the view switch marks the current view,
and the Pull requests graph view keeps what the Graph page did (a PR no longer open is dimmed, a project chip
filters). Runs fleet.js in node, as test_sessions_layout does."""
import json, pathlib, shutil, subprocess, sys

HERE = pathlib.Path(__file__).parent

if not shutil.which("node"):
    print("test_fleet_views: ok (node missing, page checks skipped)")
    sys.exit(0)

assets = HERE.parent / "assets" / "dashboard"
app = (assets / "app.js").read_text().splitlines()


def take(name):
    i = next(i for i, l in enumerate(app) if l.startswith((f"const {name} =", f"function {name}(")))
    if app[i].rstrip().endswith("{"):
        return "\n".join(app[i:next(j for j in range(i, len(app)) if app[j] in ("}", "};")) + 1])
    return app[i]


helpers = "\n".join(take(n) for n in ("ICON", "ESC", "esc", "safeUrl", "icon", "ms", "rel", "relSpan", "tag", "link", "store",
                                      "chips", "sorted", "th", "num", "ago", "isFleet", "route"))
SESSIONS = [
    {"id": "sa", "name": "alpha work", "kind": "task", "phase": "working", "project": "alpha"},
    {"id": "sb", "name": "beta work", "kind": "task", "phase": "idle", "project": "beta"},
]
PRS = [
    {"key": "o/alpha#1", "repo": "o/alpha", "number": 1, "title": "open one", "state": "OPEN", "session": "sa", "ci": "success", "url": "https://example.test/1"},
    {"key": "o/alpha#2", "repo": "o/alpha", "number": 2, "title": "merged one", "state": "MERGED", "session": "sa", "ci": "success", "url": "https://example.test/2"},
    {"key": "o/beta#3", "repo": "o/beta", "number": 3, "title": "closed one", "state": "CLOSED", "session": "sb", "ci": "failure", "url": "https://example.test/3"},
    {"key": "o/beta#4", "repo": "o/beta", "number": 4, "title": "no session", "state": "OPEN", "ci": "success", "url": "https://example.test/4"},
]
js = f"""{helpers}
const S = {{slug: "fleet", filters: {{}}, state: null, fleet: null, sort: {{}}}};
{(assets / "fleet.js").read_text()}
const st = {{sessions: {json.dumps(SESSIONS)}, prs: {json.dumps(PRS)}}};
const out = {{}};
const open = (section, hash) => {{ const s = fleetSection(section, undefined, hash); return [s, F.view]; }};
out.routes = {{
  graph: open("graph", "#/fleet/graph"), work: open("work", "#/fleet/work"), plain: open("prs", "#/fleet/prs"),
  queried: open("prs", "#/fleet/prs?view=graph"), unknown: open("prs", "#/fleet/prs?view=nope"),
  suffixed: open("prs", "#/fleet/prs?view=graph-extra"), digit: open("prs", "#/fleet/prs?view=graph1"), more: open("prs", "#/fleet/prs?view=graph&x=1"),
}};
out.suffixedView = (F.view = "graph-extra", viewOf(PRS_VIEWS));
out.briefRoutes = {{ sessions: open("sessions", "#/fleet/sessions"), plain: open("brief", "#/fleet/brief"), queried: open("brief", "#/fleet/brief?view=sessions") }};
F.brief = {{message: "Nothing to report."}}; F.units = {{units: []}};
F.view = null; out.brief = fleetBrief(st);
F.view = "sessions"; out.sessions = fleetBrief(st);
F.view = "nope"; out.briefFallback = fleetBrief(st);
S.section = "brief"; F.view = "sessions"; out.onSessions = [isFleet(), onSessions()];
F.view = null; out.offSessions = onSessions();
S.section = "prs"; F.view = "sessions"; out.otherPage = onSessions();
// The real route() from app.js over real addresses: what the sidebar and the view switch link to.
const VIEWS = {{}}, render = () => {{}}, renderView = () => {{}}, pollState = () => {{}};
document.querySelector = () => ({{ scrollTo() {{}} }});
const through = (hash) => {{ location.hash = hash; route(); return [S.section, F.view, viewOf(PRS_VIEWS), viewOf(BRIEF_VIEWS)]; }};
out.real = Object.fromEntries(["#/fleet/prs", "#/fleet/prs?view=graph", "#/fleet/graph", "#/fleet/prs?view=graph-extra", "#/fleet/brief",
  "#/fleet/brief?view=sessions", "#/fleet/sessions", "#/fleet/work"].map((h) => [h, through(h)]));
out.ids = FLEET_SECTIONS.map((s) => s.id);
out.groups = FLEET_SECTIONS.filter((s) => s.group).map((s) => [s.id, s.group]);
// U3: the old Monitors, Skill changes and PR stats addresses, and the polls that redraw only their own view
const through3 = (hash) => {{ location.hash = hash; route(); return [S.section, F.view, viewOf(PRS_VIEWS), viewOf(AUTO_VIEWS), viewOf(SKILLS_VIEWS)]; }};
out.real3 = Object.fromEntries(["#/fleet/stats", "#/fleet/monitors", "#/fleet/changes", "#/fleet/prs?view=stats", "#/fleet/automations",
  "#/fleet/automations?view=monitors", "#/fleet/skills", "#/fleet/skills?view=changes"].map((h) => [h, through3(h)]));
out.onView = [["prs", "stats", PRS_VIEWS, "#/fleet/stats"], ["prs", "stats", PRS_VIEWS, "#/fleet/prs"], ["skills", "changes", SKILLS_VIEWS, "#/fleet/changes"],
  ["skills", "usage", SKILLS_VIEWS, "#/fleet/skills"], ["skills", "usage", SKILLS_VIEWS, "#/fleet/changes"]].map(([s, v, vs, h]) => {{ through3(h); return onView(s, v, vs); }});
const st3 = {{ ...st, automations: [], monitors: [], sources: {{}} }};
F.view = "monitors"; out.monitors = fleetAutomations(st3);
F.view = null; out.automations = fleetAutomations(st3);
F.view = "changes"; out.changes = fleetSkills();
F.view = null; out.skills = fleetSkills();
F.view = "stats"; out.stats = fleetPrs(st3);
F.view = null; out.table = fleetPrs(st);
F.view = "graph"; out.graph = fleetPrs(st);
F.view = "nope"; out.fallback = fleetPrs(st);
S.filters.fleet_project = "alpha"; F.view = "graph"; out.alpha = fleetPrs(st);
S.filters.fleet_project = "beta"; out.beta = fleetPrs(st);
process.stdout.write(JSON.stringify(out));"""
sandbox = ("const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
           "{document: {addEventListener() {}}, process, setTimeout() {}, location: {hash: '#/fleet/prs', replace() {}}, window: {}, fetch() { throw new Error('no fetch'); }});")
r = subprocess.run(["node", "-e", sandbox], input=js, capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-1500:]
out = json.loads(r.stdout)

# old addresses open the view they moved to; a view the address makes up falls back to the first
assert out["routes"] == {"graph": ["prs", "graph"], "work": ["stages", None], "plain": ["prs", None],
                         "queried": ["prs", "graph"], "unknown": ["prs", "nope"],
                         "suffixed": ["prs", "graph-extra"], "digit": ["prs", "graph1"], "more": ["prs", "graph"]}, out["routes"]
assert out["suffixedView"] == "table", "a value that only starts like a view is not that view"
assert "graph" not in out["ids"] and "prs" in out["ids"], out["ids"]

# the same addresses through app.js route(): [section, view, PRS view, Brief view]
assert out["real"] == {
    "#/fleet/prs": ["prs", None, "table", "brief"], "#/fleet/prs?view=graph": ["prs", "graph", "graph", "brief"],
    "#/fleet/graph": ["prs", "graph", "graph", "brief"], "#/fleet/prs?view=graph-extra": ["prs", "graph-extra", "table", "brief"],
    "#/fleet/brief": ["brief", None, "table", "brief"], "#/fleet/brief?view=sessions": ["brief", "sessions", "table", "sessions"],
    "#/fleet/sessions": ["brief", "sessions", "table", "sessions"], "#/fleet/work": ["stages", None, "table", "brief"]}, out["real"]

# the switch: the table is the default and carries no query; the current view is marked
for page, cur, other in ((out["table"], "테이블", "그래프"), (out["fallback"], "테이블", "그래프"), (out["graph"], "그래프", "테이블")):
    sw = page.split('<nav class="view-switch"')[1].split("</nav>")[0]
    assert f'aria-current="page">{cur}</a>' in sw and f'aria-current="page">{other}</a>' not in sw, sw
assert 'href="#/fleet/prs"' in out["table"] and 'href="#/fleet/prs?view=graph"' in out["table"]
assert "<table>" in out["table"] and "relgraph" not in out["table"]
assert "relgraph" in out["graph"] and "<table>" not in out["graph"]

# the graph keeps its meaning: sessions with a PR only, a PR no longer open is dimmed, a project chip filters
def prs_in(page):
    return {n: closed for n, closed in ((g.split('<tspan class="num">#')[1].split("<")[0], g.startswith(" closed")) for g in page.split('<g class="pr')[1:])}


assert prs_in(out["graph"]) == {"1": False, "2": True, "3": True}, prs_in(out["graph"])  # #4 has no session
assert prs_in(out["alpha"]) == {"1": False, "2": True} and prs_in(out["beta"]) == {"3": True}, (prs_in(out["alpha"]), prs_in(out["beta"]))
assert 'data-group="fleet_project" data-val="alpha"' in out["graph"]
# the chip of the chosen project is the pressed one, and "All projects" is not
chips_alpha = out["alpha"].split('aria-label="Project"')[1]
assert 'data-val="alpha" aria-pressed="true"' in chips_alpha and 'data-val="all" aria-pressed="false"' in chips_alpha, chips_alpha[:400]
assert 'data-val="all" aria-pressed="true"' in out["graph"].split('aria-label="Project"')[1]

# Brief | Sessions: the old Sessions address opens the Sessions view; the poll for units follows the view, not a section
assert out["briefRoutes"] == {"sessions": ["brief", "sessions"], "plain": ["brief", None], "queried": ["brief", "sessions"]}, out["briefRoutes"]
assert "sessions" not in out["ids"] and "brief" in out["ids"], out["ids"]
assert out["onSessions"] == [True, True] and out["offSessions"] is False and out["otherPage"] is False, (out["onSessions"], out["offSessions"], out["otherPage"])
for page, cur, other in ((out["brief"], "Brief", "Sessions"), (out["briefFallback"], "Brief", "Sessions"), (out["sessions"], "Sessions", "Brief")):
    sw = page.split('<nav class="view-switch"')[1].split("</nav>")[0]
    assert f'aria-current="page">{cur}</a>' in sw and f'aria-current="page">{other}</a>' not in sw, sw
assert 'href="#/fleet/brief"' in out["brief"] and 'href="#/fleet/brief?view=sessions"' in out["brief"]
assert '<table class="sess">' in out["sessions"] and 'class="sess"' not in out["brief"] and "Nothing to report." in out["brief"]

# U3: the menu is the twelve rows in three groups; the old addresses open their view of the merged page
assert out["ids"] == ["overview", "inbox", "brief", "todo", "prs", "stages", "sent", "timeline", "automations", "skills", "tokens", "flow"], out["ids"]
assert out["groups"] == [["automations", "무인"], ["skills", "진단"]], out["groups"]
assert out["real3"] == {
    "#/fleet/stats": ["prs", "stats", "stats", "automations", "usage"], "#/fleet/prs?view=stats": ["prs", "stats", "stats", "automations", "usage"],
    "#/fleet/monitors": ["automations", "monitors", "table", "monitors", "usage"], "#/fleet/automations": ["automations", None, "table", "automations", "usage"],
    "#/fleet/automations?view=monitors": ["automations", "monitors", "table", "monitors", "usage"],
    "#/fleet/changes": ["skills", "changes", "table", "automations", "changes"], "#/fleet/skills": ["skills", None, "table", "automations", "usage"],
    "#/fleet/skills?view=changes": ["skills", "changes", "table", "automations", "changes"]}, out["real3"]
assert out["onView"] == [True, False, True, True, False], out["onView"]


def switch(page):
    sw = page.split('<nav class="view-switch"')[1].split("</nav>")[0]
    return [(a.split(">")[1].split("<")[0], 'aria-current="page"' in a) for a in sw.split("<a ")[1:]]


assert switch(out["automations"]) == [("자동화", True), ("모니터", False)] and switch(out["monitors"]) == [("자동화", False), ("모니터", True)]
assert switch(out["skills"]) == [("사용", True), ("변경", False)] and switch(out["changes"]) == [("사용", False), ("변경", True)]
assert switch(out["stats"]) == [("테이블", False), ("그래프", False), ("통계", True)] and switch(out["table"]) == [("테이블", True), ("그래프", False), ("통계", False)]
assert "<h2>Automations</h2>" in out["automations"] and "<h2>Automations</h2>" in out["monitors"] and "<h2>Monitors</h2>" not in out["monitors"]
assert "<h2>Skills</h2>" in out["skills"] and "<h2>Skills</h2>" in out["changes"] and "Skill changes" not in out["changes"].split("</p>")[0].split("<h2>")[1]
assert "<h2>Pull requests</h2>" in out["stats"] and "PR stats" not in out["stats"].split("</h2>")[0]

# The polls redraw only the view that asked for them: a response that lands after the reader moved to another view of the
# same page must not replace the DOM (a text selection, a hover). fetch answers at once; the checks await its microtasks.
helpers2 = helpers + "\n" + take("redrawCharts")
js2 = f"""{helpers2}
const S = {{slug: "fleet", filters: {{}}, state: {{}}, fleet: null, sort: {{}}}};
{(assets / "fleet.js").read_text()}
const VIEWS = {{}}, pollState = () => {{}}, render = () => {{}};
let renders = 0;
const renderView = () => {{ renders++; }};
document.querySelector = () => ({{ scrollTo() {{}} }});
globalThis.fetch = async () => ({{ ok: true, json: async () => ({{ error: "stub" }}) }});
const go = (hash) => {{ location.hash = hash; route(); }};
const settle = async () => {{ for (let i = 0; i < 12; i++) await null; }};
// start a poll on startHash, move to endHash before it answers, and count the redraws
const race = async (startHash, endHash, start) => {{
  go(startHash); renders = 0; F.stats = F.skills = F.changes = F.jobs = null; F.statsAt = F.skillsAt = F.changesAt = F.jobsAt = 0;
  start(); go(endHash); await settle(); return renders;
}};
const out = {{}};
out.stats = [await race("#/fleet/prs?view=stats", "#/fleet/prs?view=stats", fleetStats), await race("#/fleet/prs?view=stats", "#/fleet/prs", fleetStats)];
out.skills = [await race("#/fleet/skills", "#/fleet/skills", fleetSkills), await race("#/fleet/skills", "#/fleet/skills?view=changes", fleetSkills)];
out.changes = [await race("#/fleet/skills?view=changes", "#/fleet/skills?view=changes", fleetChanges), await race("#/fleet/skills?view=changes", "#/fleet/skills", fleetChanges)];
const automations = () => fleetAutomations({{}});
out.jobs = [await race("#/fleet/automations", "#/fleet/automations", automations), await race("#/fleet/automations", "#/fleet/automations?view=monitors", automations)];
go("#/fleet/prs?view=stats"); renders = 0; redrawCharts(); out.resizeStats = renders;
go("#/fleet/prs"); renders = 0; redrawCharts(); out.resizeTable = renders;
process.stdout.write(JSON.stringify(out));"""
# an async body in the sandbox: the script awaits the fetch stub's microtasks, so it runs as an async function
sandbox2 = ("const vm = require('vm'); const ctx = vm.createContext({document: {addEventListener() {}}, process, setTimeout() {}, "
            "location: {hash: '#/fleet/prs', replace() {}}, window: {}}); ctx.globalThis = ctx; "
            "vm.runInContext('(async () => {' + require('fs').readFileSync(0, 'utf8') + '})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exitCode = 1; })', ctx);")
r2 = subprocess.run(["node", "-e", sandbox2], input=js2, capture_output=True, text=True)
assert r2.returncode == 0, r2.stderr[-1500:]
polls = json.loads(r2.stdout)
assert polls == {"stats": [1, 0], "skills": [1, 0], "changes": [1, 0], "jobs": [1, 0], "resizeStats": 1, "resizeTable": 0}, polls

print("test_fleet_views: ok")
