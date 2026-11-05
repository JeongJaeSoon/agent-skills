#!/usr/bin/env python3
"""The Sessions page: ticket ids found in names, branches and brief rows, and the finished group (an offline worker
whose PR merged, a subagent with no stop recorded). Runs fleet.js in node, as test_subagent_rows does."""
import json, pathlib, shutil, subprocess, sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import fleet  # noqa: E402

# project_of: the origin repository's name wins over the name Orca or the workspace directory kept
assert fleet.project_of("/h/orca/workspaces/old/wt-1", "old", "acme/renamed") == "renamed"

if not shutil.which("node"):
    print("test_sessions_layout: ok (node missing, page checks skipped)")
    sys.exit(0)

assets = HERE.parent / "assets" / "dashboard"
app = (assets / "app.js").read_text().splitlines()


def take(name):
    i = next(i for i, l in enumerate(app) if l.startswith((f"const {name} =", f"function {name}(")))
    if app[i].rstrip().endswith("{"):
        return "\n".join(app[i:next(j for j in range(i, len(app)) if app[j] in ("}", "};")) + 1])
    return app[i]


helpers = "\n".join(take(n) for n in ("ICON", "ESC", "esc", "safeUrl", "icon", "ms", "rel", "relSpan", "tag", "link", "store"))
STATE = {
    "sessions": [
        {"id": "root", "name": "orchestrator", "kind": "orchestrator", "phase": "working", "project": "p", "parent": None},
        {"id": "lead", "name": "lead one", "kind": "orchestration", "phase": "working", "project": "p", "parent": "root",
         "subagents": [{"agent": "a1", "stale": True, "started_at": "2026-01-01T00:00:00Z"},
                       {"agent": "a2", "stale": False, "started_at": "2026-01-01T00:00:00Z"}]},
        {"id": "w1", "name": "94S-1336 fix", "kind": "task", "phase": "working", "project": "p", "parent": "lead",
         "branch": "user/ABC-12-long-branch", "refs": [{"type": "issue", "key": "ABC-12", "url": "https://example.test/ABC-12"}]},
        {"id": "w2", "name": "no id here", "kind": "task", "phase": "offline", "project": "p", "parent": "lead",
         "branch": "user/feat-9", "linked_pr": {"number": 5, "state": "merged"}},
        {"id": "w3", "name": "offline, PR open", "kind": "task", "phase": "offline", "project": "p", "parent": "lead"},
    ],
    "prs": [{"session": "w3", "state": "OPEN", "number": 6, "url": "https://example.test/6"}],
}
js = f"""{helpers}
const S = {{filters: {{}}, state: {{sessions: []}}, fleet: null}};
{(assets / "fleet.js").read_text()}
const sessions = {json.dumps(STATE)}.sessions;
const by = Object.fromEntries(sessions.map((s) => [s.id, s]));
const st = {json.dumps(STATE)};
F.brief = {{table_rows: [{{rows: [{{label: "XYZ-7 brief row", status: "🙋", session: {{id: "w3"}}}}]}}]}};
// What units.py wrote: the page only reads it. w1 has a ticket link, w3 a brief row and a PR, w2 owes the owner an answer.
F.units = {{units: [
  {{id: "card:w1", session: {{id: "w1"}}, tickets: [{{id: "ABC-12", url: "https://example.test/ABC-12", from: "refs"}}, {{id: "94S-1336", url: null, from: "name"}}], rows: [], prs: [], pr_unknown: [], owner_turn: false}},
  {{id: "card:w2", session: {{id: "w2"}}, tickets: [], rows: [], prs: [{{key: "a/b#5", number: 5, url: "https://example.test/5", via: "orca", state: "merged", ci: "unknown"}}], pr_unknown: ["a/b#7"], owner_turn: true}},
  {{id: "card:w3", session: {{id: "w3"}}, tickets: [{{id: "XYZ-7", url: null, from: "row"}}], pr_unknown: [], owner_turn: false,
    rows: [{{id: "r1", title: "XYZ-7 brief row", state: {{status: "owner"}}}}, {{id: "r2", title: "done one", state: {{status: "done"}}}}],
    prs: [{{key: "a/b#6", number: 6, url: "https://example.test/6", via: "session"}}]}}]}};
const out = {{}};
out.cells = {{w1: unitTickets(unitOf("w1")), none: unitTickets(unitOf("nobody"))}};
out.twoPrs = finished({{prs: [{{session: "w9", state: "MERGED"}}, {{session: "w9", state: "OPEN"}}]}}, {{id: "w9", kind: "task", phase: "offline"}});
out.root = finished(st, {{kind: "orchestrator", phase: "offline", linked_pr: {{state: "MERGED"}}}});
out.fin = sessions.map((s) => finished(st, s));
// two sessions claimed one merged PR: the unit links it to neither, so neither is finished because of it
F.units = {{units: [...F.units.units, {{id: "card:w9", session: {{id: "w9"}}, tickets: [], rows: [], prs: [], pr_unknown: ["a/b#1"], owner_turn: false}}]}};
out.shared = finished({{prs: [{{session: "w9", state: "MERGED"}}]}}, {{id: "w9", kind: "task", phase: "offline"}});
F.doneOpen = false;
out.closed = fleetSessions(st);
F.doneOpen = true;
out.open = fleetSessions(st);
F.leadFold = new Set(["lead"]);
out.foldedOpen = fleetSessions(st);
F.doneOpen = false;
out.foldedClosed = fleetSessions(st);
F.units = null;
out.unloaded = finished(st, sessions[3]);
F.units = {{error: "units.py timed out after 30 s", units: []}};
out.failed = fleetSessions(st);
out.failedFinished = finished(st, sessions[3]);
process.stdout.write(JSON.stringify(out));"""
sandbox = ("const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
           "{document: {addEventListener() {}}, process, location: {hash: '#/fleet/sessions'}, window: {}, fetch() { throw new Error('no fetch'); }});")
r = subprocess.run(["node", "-e", sandbox], input=js, capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-1500:]
out = json.loads(r.stdout)

# tickets, brief rows, PRs and the owner's turn come from the unit (units.py finds the ticket ids; test_units covers that)
assert out["cells"] == {"w1": [{"key": "ABC-12", "url": "https://example.test/ABC-12"}, {"key": "94S-1336", "url": None}], "none": []}, out["cells"]
assert "#6</a>" in out["closed"] and "#5</a>" in out["closed"] and "merged" in out["closed"], "a unit's PRs, Orca's own link with its state"
assert "PR link unknown" in out["closed"] and "a/b#7" in out["closed"], "a PR several sessions claimed is linked to none"
assert out["closed"].count('title="Waits on you"') == 1 and "XYZ-7 brief row" in out["closed"], "one owner's turn mark (w2), and the brief row"
assert out["shared"] is False, "a PR several sessions claimed does not finish the first claimant"
assert out["unloaded"] is False and out["failedFinished"] is False, "without a unit, nothing says a session is finished"
assert "units unavailable" in out["failed"] and "timed out after 30 s" in out["failed"] and "units unavailable" not in out["closed"]
assert out["root"] is False  # the orchestrator is never a finished worker
assert out["twoPrs"] is False  # an earlier PR merged while a later one is still open
# finished: offline and a merged PR (the collector's, else Orca's own link, either case); never a lead or a live worker
assert out["fin"] == [False, False, False, True, False], out["fin"]


def rows(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    return [r for r in body.split("<tr")[1:]]


closed, opened = rows(out["closed"]), rows(out["open"])
hidden = lambda rs: [r for r in rs if r.startswith(" hidden")]
# 5 sessions + 2 subagent rows + the Finished row; closed: the merged worker and the stale subagent are out of sight
assert len(closed) == len(opened) == 8, (len(closed), len(opened))
assert len(hidden(closed)) == 2 and not hidden(opened), (len(hidden(closed)), len(hidden(opened)))
assert "Finished · 1 offline with a merged PR · 1 subagent with no stop recorded" in out["closed"]
assert 'aria-expanded="false"' in out["closed"].split("fold-done")[-1] and 'aria-expanded="true"' in out["open"].split("fold-done")[-1]
# the lead's own count leaves the finished worker out, and each cell stays on one line
assert "the 2 sessions under lead one" in out["closed"], out["closed"][:600]
assert 'class="cut"' in out["closed"] and "XYZ-7" in out["closed"]

# A folded lead keeps its live workers and their subagents out of sight whatever the Finished line does; opening
# Finished shows the finished worker even there, and the count leaves out the subagent no one can reach.
def row_of(html, rid):
    return next(r for r in rows(html) if f'id="{rid}"' in r)


for page in (out["foldedOpen"], out["foldedClosed"]):
    assert row_of(page, "row-w1").startswith(" hidden") and row_of(page, "row-w3").startswith(" hidden"), page[:300]
    assert row_of(page, "row-lead-sub-0").startswith(" hidden") is (page is out["foldedClosed"])  # the lead's own stale subagent
assert not row_of(out["foldedOpen"], "row-w2").startswith(" hidden") and row_of(out["foldedClosed"], "row-w2").startswith(" hidden")
assert 'aria-controls="row-w2 row-lead-sub-0"' in out["foldedOpen"], out["foldedOpen"]

print("test_sessions_layout: ok")
