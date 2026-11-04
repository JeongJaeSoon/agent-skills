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
F.brief = {{table_rows: [{{rows: [{{label: "XYZ-7 brief row", status: "x", session: {{id: "w3"}}}}]}}]}};
const out = {{}};
const ids = (s, rows) => ticketIds(s, rows).map((t) => t.key + (t.url ? "@" : ""));
out.t1 = ids(st.sessions[2], []);
out.t2 = ids(st.sessions[3], []);
out.t3 = ids(st.sessions[4], [{{label: "XYZ-7 brief row"}}]);
out.t4 = ids({{name: "UTF-8 and SHA-256 with XYZ-3, ABC-12 ABC-12"}}, []);
out.t5 = ids({{name: "x", branch: "feat/ABC-12_fix"}}, []).concat(ids({{name: "feat_ABC-3", branch: "xABC-9 ABC-9x"}}, []));
out.twoPrs = finished({{prs: [{{session: "w9", state: "MERGED"}}, {{session: "w9", state: "OPEN"}}]}}, {{id: "w9", kind: "task", phase: "offline"}});
out.root = finished(st, {{kind: "orchestrator", phase: "offline", linked_pr: {{state: "MERGED"}}}});
out.fin = sessions.map((s) => finished(st, s));
F.doneOpen = false;
out.closed = fleetSessions(st);
F.doneOpen = true;
out.open = fleetSessions(st);
F.leadFold = new Set(["lead"]);
out.foldedOpen = fleetSessions(st);
F.doneOpen = false;
out.foldedClosed = fleetSessions(st);
process.stdout.write(JSON.stringify(out));"""
sandbox = ("const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
           "{document: {addEventListener() {}}, process, location: {hash: '#/fleet/sessions'}, window: {}, fetch() { throw new Error('no fetch'); }});")
r = subprocess.run(["node", "-e", sandbox], input=js, capture_output=True, text=True)
assert r.returncode == 0, r.stderr[-1500:]
out = json.loads(r.stdout)

# ticket ids: the card's own link first (with its url), then the name, branch and brief rows; no duplicates; only upper case
assert out["t1"] == ["ABC-12@", "94S-1336"], out["t1"]
assert out["t2"] == [], out["t2"]
assert out["t3"] == ["XYZ-7"], out["t3"]
assert out["t4"] == ["XYZ-3", "ABC-12"], out["t4"]  # a standard's name ("UTF-8", "SHA-256") is not a ticket

assert out["t5"] == ["ABC-12", "ABC-3"], out["t5"]  # an underscore is a boundary, a letter glued to the id is not
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
