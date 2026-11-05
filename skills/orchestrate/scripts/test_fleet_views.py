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
                                      "chips", "sorted", "th", "num", "ago"))
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
const S = {{filters: {{}}, state: null, fleet: null, sort: {{}}}};
{(assets / "fleet.js").read_text()}
const st = {{sessions: {json.dumps(SESSIONS)}, prs: {json.dumps(PRS)}}};
const out = {{}};
const open = (section, hash) => {{ const s = fleetSection(section, undefined, hash); return [s, F.view]; }};
out.routes = {{
  graph: open("graph", "#/fleet/graph"), work: open("work", "#/fleet/work"), plain: open("prs", "#/fleet/prs"),
  queried: open("prs", "#/fleet/prs?view=graph"), unknown: open("prs", "#/fleet/prs?view=nope"),
}};
out.ids = FLEET_SECTIONS.map((s) => s.id);
F.view = null; out.table = fleetPrs(st);
F.view = "graph"; out.graph = fleetPrs(st);
F.view = "nope"; out.fallback = fleetPrs(st);
S.filters.fleet_project = "alpha"; F.view = "graph"; out.alpha = fleetPrs(st);
S.filters.fleet_project = "beta"; out.beta = fleetPrs(st);
process.stdout.write(JSON.stringify(out));"""
sandbox = ("const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
           "{document: {addEventListener() {}}, process, location: {hash: '#/fleet/prs'}, window: {}, fetch() { throw new Error('no fetch'); }});")
r = subprocess.run(["node", "-e", sandbox], input=js, capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-1500:]
out = json.loads(r.stdout)

# old addresses open the view they moved to; a view the address makes up falls back to the first
assert out["routes"] == {"graph": ["prs", "graph"], "work": ["stages", None], "plain": ["prs", None],
                         "queried": ["prs", "graph"], "unknown": ["prs", "nope"]}, out["routes"]
assert "graph" not in out["ids"] and "prs" in out["ids"], out["ids"]

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
assert 'data-group="fleet_project" data-val="alpha"' in out["graph"] and 'aria-pressed="true"' in out["alpha"].split('aria-label="Project"')[1]

print("test_fleet_views: ok")
