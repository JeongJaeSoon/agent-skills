"""Offline tests for the subagent ledger (fleet.subagents, fleet.attach_subagents) and the brief's subagent rows: under
the parent's lead or on their own, ❔ once stale and never counted as moving, and counted by the missing-session
headline as standing for their session (names invented).

Run: python3 test_subagent_rows.py
"""
import datetime as dt, json, os, pathlib, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-subagent-rows-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "config.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs"),
       "ORCA_TERMINAL_HANDLE": "term_root"}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import fleet, store  # noqa: E402

store.import_apply(checks=False)
now = fleet.utcnow()
sd = fleet.state_dir()
sd.mkdir(parents=True, exist_ok=True)
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731
LOG = sd / fleet.SUBAGENTS

# ---- the ledger: folded per agent in file order, bad lines skipped
lines = [
    {"v": 1, "event": "start", "at": ago(1), "agent": "a1", "claude_session": "cs1", "type": "Explore",
     "handle": "term_lead", "cwd": "/w/lead", "desc": "Map the board code"},
    {"v": 1, "event": "start", "at": ago(2), "agent": "a2", "handle": "term_lead", "type": "general-purpose"},
    {"v": 1, "event": "stop", "at": ago(0.5), "agent": "a2", "handle": "term_lead"},
    {"v": 1, "event": "stop", "at": ago(0.5), "agent": "a9"},          # a stop with no start: ended, never shown
    {"v": 2, "event": "start", "at": ago(1), "agent": "future"},       # another version: skipped
    {"v": 1, "event": "start", "at": 5, "agent": "badtime"},           # a time that is not one: skipped
    {"v": 1, "event": "begin", "at": ago(1), "agent": "badkind"},
    [1, 2], "text",
]
LOG.write_text("".join(json.dumps(l) + "\n" for l in lines) + "{not json\n")
led = fleet.subagents()
assert set(led) == {"a1", "a2", "a9"}, led
assert led["a1"]["started_at"] == ago(1) and led["a1"]["stopped_at"] is None and led["a1"]["desc"] == "Map the board code"
assert led["a2"]["stopped_at"] == ago(0.5) and led["a9"]["started_at"] is None, led
# A start after a stop (a resumed agent) is running again.
fleet.append_line(LOG, {"v": 1, "event": "start", "at": ago(0.2), "agent": "a2", "handle": "term_lead"})
assert fleet.subagents()["a2"]["stopped_at"] is None

# ---- attach: by the terminal handle only; stale after stale_s or under an offline session
sessions = {"lead": {"id": "lead", "phase": "working", "path": "/w/lead", "terminals": [{"handle": "term_lead"}]},
            "twin": {"id": "twin", "phase": "working", "path": "/w/lead", "terminals": [{"handle": "term_twin"}]}}
fleet.attach_subagents(sessions, {**fleet.subagents(), "lost": {
    "agent": "lost", "handle": "term_gone", "cwd": "/w/lead", "started_at": ago(0.1), "stopped_at": None}}, now, 7200)
assert [a["agent"] for a in sessions["lead"]["subagents"]] == ["a2", "a1"], sessions["lead"]
assert sessions["twin"]["subagents"] == []  # the agent whose terminal is gone is not guessed onto a folder twin
assert [a["stale"] for a in sessions["lead"]["subagents"]] == [False, False]
fleet.attach_subagents(sessions, fleet.subagents(), now, 1800)
assert [a["stale"] for a in sessions["lead"]["subagents"]] == [False, True]
sessions["lead"]["phase"] = "offline"
fleet.attach_subagents(sessions, fleet.subagents(), now, 1e9)
assert all(a["stale"] for a in sessions["lead"]["subagents"])
# Once its terminals are gone (Orca still lists the worktree, offline) the agent stays with the session it was seen
# under, as stale; once it stops, the link is forgotten.
known = {}
sessions["lead"]["phase"] = "working"
fleet.attach_subagents(sessions, fleet.subagents(), now, 1e9, known)
assert known == {"a1": "lead", "a2": "lead"}, known
sessions["lead"].update(phase="offline", terminals=[])
fleet.attach_subagents(sessions, fleet.subagents(), now, 1e9, known)
assert [(a["agent"], a["stale"]) for a in sessions["lead"]["subagents"]] == [("a2", True), ("a1", True)]
fleet.append_line(LOG, {"v": 1, "event": "stop", "at": ago(0.1), "agent": "a2"})
fleet.attach_subagents(sessions, fleet.subagents(), now, 1e9, known)
assert known == {"a1": "lead"} and [a["agent"] for a in sessions["lead"]["subagents"]] == ["a1"]
assert fleet.subagent_stale_s({}) == fleet.SUBAGENT_STALE_H * 3600
assert fleet.subagent_stale_s({"board": {"subagent_stale_h": 0.5}}) == 1800


# ---- the brief
def sess(sid, kind, phase, **kw):
    return {"id": sid, "name": sid, "kind": kind, "phase": phase, "parent": "root", "orca_parent": None,
            "task_title": sid, "gh_repo": "acme/app", "project": "app", "terminals": [{"handle": f"term_{sid}"}],
            "last_activity": ago(0.1), "report": None, "dispatch": None, "run": None, "subagents": [], **kw}


def agent(aid, desc, hours, type_="Explore"):
    return {"agent": aid, "type": type_, "desc": desc, "started_at": ago(hours), "stale": False}


def write_state(sessions_):
    fleet.write_atomic(sd / "state.json", json.dumps({
        "generated_at": fleet.iso(now), "root": "root", "sources": {}, "prs": [],
        "sessions": [sess("root", "orchestrator", "working", parent=None,
                          subagents=[agent("r1", "Find unsent text", 0.2), agent("r2", "Old sweep", 5)])]
        + sessions_}))


def render(*extra):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--json", "--no-refresh", *extra],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return json.loads(r.stdout)


write_state([
    sess("lead-x", "orchestration", "working", orca_parent="root", run="run_x", subagents=[agent("l1", "Read the plan", 0.3)]),
    sess("card-y", "task", "working", parent="lead-x", dispatch="ctx_y", subagents=[agent("c1", "Run the suite", 0.1, "general-purpose")]),
    # A worker whose dispatch completed with no PR has no row of its own, but still runs a subagent.
    sess("worker-z", "task", "working", parent="lead-x", dispatch="ctx_z", dispatch_status="completed",
         subagents=[agent("z1", "Collect logs", 0.1)]),
    sess("top-card", "standalone", "working", orca_parent="root", subagents=[agent("t1", "Check the docs", 0.1)]),
])
out = render()
text, snap = out["full"], out["snapshot"]["rows"]  # the tables with every cell whole, whatever their width
# The orchestrator's own subagents are rows of their own: one moving, one with no stop for over 3 h ❔.
assert snap["agent:r1"]["status"] == "moving" and snap["agent:r2"]["status"] == "check", snap
assert "| 🔄 | Find unsent text |" in text and "| ❔ | Old sweep |" in text, text
assert "- [" not in text and "…[" not in text, text
full = out["full"]  # no cell cut: the whole next step
assert "| 서브에이전트 작업 중 (Explore) |" in full, full
assert "| 에이전트 조치 필요: 종료 기록 없음 · 5h 전 |" in full, full
assert "| 🔄 | Old sweep" not in text, text  # stale is not counted as moving
# A lead's own subagent and its card's hang under the lead's row; a top-level card's is a row of its own.
assert "| 🔄 | └ Read the plan |" in text and "| 🔄 | └ Run the suite |" in text, text
assert "| 🔄 | Check the docs |" in text, text
# A session with no row of its own (its dispatch completed with no PR) puts its subagent under the nearest session
# above it that has one, and the subagent row stands for it: the worker is not reported missing.
assert "| 🔄 | └ Collect logs |" in text, text  # alone on its session, and still named for its task
assert not any(f.startswith("보고서에 없는 세션") for f in out["headline_facts"]), out["headline_facts"]
# Without its subagent the same worker is missing, so the count above came from the subagent row.
write_state([sess("lead-x", "orchestration", "working", orca_parent="root", run="run_x"),
             sess("worker-z", "task", "working", parent="lead-x", dispatch="ctx_z", dispatch_status="completed")])
assert "보고서에 없는 세션 1 (worker-z)" in render()["headline_facts"]

# A top-level card's subagent stays in the report of the program the card's board row is in.
import board  # noqa: E402
write_state([sess("top-card", "standalone", "working", orca_parent="root", subagents=[agent("t1", "Check the docs", 0.1)])])
board.change(lambda s: board.put(s, "ABC-2", dict(title="top-card 문서", status="moving", next="작업 중", repo="acme/app", program="prog-a")))  # as before A-ANNOT
scoped = render("--scope", "prog-a")["snapshot"]["rows"]
assert "agent:t1" in scoped and "agent:r1" not in scoped, scoped
board.drop("ABC-2")

# The stale cut-off is the dashboard config's board.subagent_stale_h; a ❔ subagent shows its age even under row_min.
(tmp / "config.json").write_text(json.dumps({"board": {"subagent_stale_h": 0.1}}))
write_state([])
out = render()
snap = out["snapshot"]["rows"]
assert snap["agent:r1"]["status"] == "check" and snap["agent:r2"]["status"] == "check", snap
assert "| ❔ | Find unsent text |" in out["full"] and "에이전트 조치 필요: 종료 기록 없음 · 12분 전" in out["full"], out["full"]
(tmp / "config.json").write_text(json.dumps({"board": {"subagent_stale_h": 10}}))
assert render()["snapshot"]["rows"]["agent:r2"]["status"] == "moving"
# --no-fill leaves every derived row out, subagents too.
assert not any(k.startswith("agent:") for k in render("--no-fill")["snapshot"]["rows"])

# ---- the Sessions page: a dimmed row per subagent under its session, its text escaped (run in node, as test_fleet)
if shutil.which("node"):
    assets = HERE.parent / "assets" / "dashboard"
    app = (assets / "app.js").read_text().splitlines()

    def take(name):
        i = next(i for i, l in enumerate(app) if l.startswith((f"const {name} =", f"function {name}(")))
        if app[i].rstrip().endswith("{"):
            return "\n".join(app[i:next(j for j in range(i, len(app)) if app[j] in ("}", "};")) + 1])
        return app[i]
    helpers = "\n".join(take(n) for n in ("ICON", "ESC", "esc", "safeUrl", "icon", "ms", "rel", "relSpan", "tag", "link"))
    s = {"id": "wt1", "subagents": [{"agent": "a1", "type": "Explore", "desc": "<img src=x onerror=alert(1)>",
                                     "started_at": ago(0.2), "stale": False},
                                    {"agent": "a2", "type": None, "desc": None, "started_at": ago(5), "stale": True}]}
    js = f"""{helpers}
{(assets / "fleet.js").read_text()}
process.stdout.write(subagentRows({json.dumps(s)}, 1) + "\\n-----\\n" + subagentRows({{id: "wt2"}}, 0));"""
    html, empty = subprocess.run(["node", "-e", "const vm = require('vm'); vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), "
                                  "{document: {addEventListener() {}}, process});"],
                                 input=js, capture_output=True, text=True, check=True).stdout.split("\n-----\n")
    assert html.count("<tr") == 2 and "<img" not in html and "&lt;img" in html, html
    assert "--d:2" in html and "running" in html and "check: no stop recorded" in html and ">subagent<" in html, html
    assert empty == "", empty

print("test_subagent_rows: ok")
