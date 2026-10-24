"""`prog.py land` end to end against a fake gh. Run: python3 test_land.py"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
FAKE_ORCA = r'''#!/usr/bin/env python3
import json, os
st = json.load(open(os.environ["FAKE_GH_STATE"]))
print(json.dumps({"ok": True, "result": {"tasks": st.get("tasks", []), "workers": st.get("workers", [])}}))
'''
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path))
a = sys.argv[1:]
def save(): json.dump(st, open(path, "w"))
def pr(n): return st["prs"][str(n)]
flaky = st.get("flaky", {})
if flaky.get(" ".join(a[:2])):
    flaky[" ".join(a[:2])] -= 1; save()
    sys.exit('Post "https://api.github.com/graphql": net/http: TLS handshake timeout')
if a[:2] == ["pr", "list"]:
    print(json.dumps([dict(p, number=int(n)) for n, p in st["prs"].items() if p["state"] == "OPEN"]))
elif a[:2] == ["pr", "view"] and a[-1] == "headRefName,labels,comments,commits":
    p = pr(a[2])
    print(json.dumps({"headRefName": f"b{a[2]}", "commits": [{"oid": f"h{a[2]}"}, {"oid": f"old{a[2]}"}],
                      "labels": [{"name": l} for l in p.get("labels", [])], "comments": [{"body": b} for b in p.get("comments", [])]}))
elif a[:1] == ["api"] and a[1].endswith("/files"):
    print("\n".join(pr(a[1].split("/pulls/")[1].split("/")[0])["files"]))
elif a[:2] == ["pr", "view"] and a[-1] == "files":
    print(json.dumps({"files": [{"path": f} for f in pr(a[2])["files"]]}))
elif a[:2] == ["pr", "view"] and "--jq" in a and a[a.index("--jq") + 1] == ".state":
    print(pr(a[2])["state"])
elif a[:2] == ["pr", "view"]:
    p = pr(a[2]); print(json.dumps(dict(p, mergeCommit={"oid": p.get("merge_sha")} if p.get("merge_sha") else None,
                                        url=f"https://x/pull/{a[2]}", title="t", headRefName=f"b{a[2]}")))
elif a[:2] == ["pr", "diff"]:
    print(pr(a[2])["diff"])
elif a[:2] == ["pr", "merge"]:
    p = pr(a[2])
    if p.get("stacked"):
        sys.exit("GraphQL: stacked pull requests cannot be merged this way")
    p["state"], p["merge_sha"] = "MERGED", f"m{a[2]}"; save(); st["log"] = st.get("log", []) + [a]; save()
elif a[:1] == ["api"] and "/compare/" in a[1]:
    left, right = a[1].split("/compare/")[1].split("...")
    if left == "mb":
        print(json.dumps({"files": [{"filename": f} for f in st.get("base_changed", [])]}))
    else:
        p = pr(right[1:])
        print(json.dumps({"behind_by": p.get("behind", 0), "merge_base_commit": {"sha": "mb"},
                          "files": [{"filename": f} for f in p["files"]]}))
elif a[:2] == ["run", "list"] and "--branch" in a:
    print(json.dumps(st.get("branch_runs", {}).get(a[a.index("--branch") + 1], [])))
elif a[:2] == ["run", "list"]:
    print(json.dumps(st.get("runs", {}).get(a[a.index("--commit") + 1], [{"name": "CI", "status": "completed"}])))
elif a[:1] == ["api"] and a[-1].endswith("cli_internal/pulls/stacks"):
    print(json.dumps(st.get("stacks", [])))
elif a[:3] == ["api", "-X", "PUT"] and "merge-async" in a[3]:
    top = a[3].split("/pulls/")[1].split("/")[0]
    for s in st.get("stacks", []):
        if int(top) in s["pull_requests"]:
            for n in s["pull_requests"][: s["pull_requests"].index(int(top)) + 1]:
                pr(n)["state"], pr(n)["merge_sha"] = "MERGED", f"m{n}"
    st["log"] = st.get("log", []) + [a]; save(); print("{}")
else:
    sys.exit(f"fake gh: unhandled {a}")
'''


def diff(n):
    return f"diff --git a/f{n} b/f{n}\n--- a/f{n}\n+++ b/f{n}\n@@ -1 +1 @@\n-a\n+b{n}\n"


def pid(n):
    return subprocess.run(["git", "patch-id", "--verbatim"], input=diff(n), capture_output=True, text=True).stdout.split()[0]


def pr(n, mss="CLEAN", base="main", stacked=False, files=None, behind=0):
    return {"state": "OPEN", "isDraft": False, "headRefOid": f"h{n}", "mergeStateStatus": mss, "baseRefName": base,
            "statusCheckRollup": [{"name": "check", "status": "COMPLETED", "conclusion": "SUCCESS",
                                   "detailsUrl": f"https://github.com/o/r/actions/runs/{n}00/job/1"}],
            "diff": diff(n), "stacked": stacked, "files": files or [f"src/f{n}.py"], "behind": behind}


def setup(prs, stacks=()):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "bin").mkdir()
    (d / "bin" / "gh").write_text(FAKE_GH)
    (d / "bin" / "gh").chmod(0o755)
    (d / "bin" / "orca").write_text(FAKE_ORCA)
    (d / "bin" / "orca").chmod(0o755)
    (d / "state.json").write_text(json.dumps({"prs": prs, "stacks": [{"id": i, "pull_requests": s} for i, s in enumerate(stacks)]}))
    env = dict(os.environ, PATH=f"{d / 'bin'}:{os.environ['PATH']}", FAKE_GH_STATE=str(d / "state.json"),
               PROGRAMS_HOME=str(d / "programs"), ORCH_DASH="off")
    subprocess.run([sys.executable, HERE / "prog.py", "init", "t", "--repo", "o/r", "--run", "run_x"], env=env, check=True,
                   capture_output=True)
    return d, env


def prog(env, *args):
    r = subprocess.run([sys.executable, HERE / "prog.py", *args], env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def ledger(d):
    return [json.loads(l) for l in (d / "programs" / "t" / "ledger.jsonl").read_text().splitlines()]


def verdict(d, n, ticket):
    with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "ready", "pr": n, "ticket": ticket}) + "\n")
        f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "verdict", "pr": n, "sha": f"h{n}",
                            "patch_id": pid(n), "result": "pass"}) + "\n")


# 1. A ready PR lands at once; the ledger records the merge commit.
d, env = setup({"1": pr(1)})
verdict(d, 1, "T-1")
code, out = prog(env, "land", "t", "--pr", "1")
assert code == 0 and "landed #1" in out, out
assert [e["ev"] for e in ledger(d)][-1] == "landed"
# An exclusive PR takes the lane and frees it when it lands.
d, env = setup({"11": pr(11, files=["compose.yaml"])})
verdict(d, 11, "T-11")
code, out = prog(env, "land", "t", "--pr", "11")
assert code == 0, out
evs = [e["ev"] for e in ledger(d)]
assert evs[-3:] == ["lock_acquired", "landed", "lock_released"], evs
assert not list((d / "programs" / "_locks").iterdir())

# 1b. The exclusive lane is per repo and base across programs: another program holding it makes this one yield.
d, env = setup({"1": pr(1, files=[".github/workflows/ci.yml"])})
verdict(d, 1, "T-1")
(d / "programs" / "_locks").mkdir()
(d / "programs" / "_locks" / "o__r@main.lock").write_text(json.dumps({"pr": 50, "ts": "2999-01-01T00:00:00+00:00"}))
code, out = prog(env, "land", "t", "--pr", "1")
assert code == 2 and "held by #50" in out, out

# 1b'. A holder whose PR was closed never releases; the next lander frees the lane instead of waiting 90 min.
closed = pr(51); closed["state"] = "CLOSED"
d, env = setup({"1": pr(1, files=[".github/workflows/ci.yml"]), "51": closed})
verdict(d, 1, "T-1")
(d / "programs" / "_locks").mkdir()
(d / "programs" / "_locks" / "o__r@main.lock").write_text(json.dumps({"pr": 51, "program": "other", "ts": "2999-01-01T00:00:00+00:00"}))
code, out = prog(env, "land", "t", "--pr", "1")
assert code == 0 and "landed #1" in out, out
assert any(e["ev"] == "lock_released" and e.get("pr") == 51 and "CLOSED" in e.get("note", "") for e in ledger(d))

# 1c. A head with no checks reported yet is not green.
p0 = pr(10); p0["statusCheckRollup"] = []
d, env = setup({"10": p0})
verdict(d, 10, "T-10")
code, out = prog(env, "land", "t", "--pr", "10")
assert code == 2 and "CI pending" in out, out

# 2. The dependency root lands before the dependent; the dependent yields, then lands.
d, env = setup({"2": pr(2), "3": pr(3)})
verdict(d, 2, "T-root"); verdict(d, 3, "T-dep")
prog(env, "dep", "t", "--ticket", "T-dep", "--after", "T-root")
code, out = prog(env, "land", "t", "--pr", "3")
assert code == 2 and "lands after T-root" in out, out
assert prog(env, "land", "t", "--pr", "2")[0] == 0
code, out = prog(env, "land", "t", "--pr", "3")
assert code == 0, out

# 2b. Orca task deps hold the dependent the same way.
d, env = setup({"12": pr(12), "13": pr(13)})
st = json.loads((d / "state.json").read_text())
st["tasks"] = [{"id": "a", "display_name": "T-12 root", "status": "dispatched", "deps": "[]"},
               {"id": "b", "display_name": "T-13 dep", "status": "dispatched", "deps": '["a"]'}]
(d / "state.json").write_text(json.dumps(st))
verdict(d, 12, "T-12"); verdict(d, 13, "T-13")
code, out = prog(env, "land", "t", "--pr", "13")
assert code == 2 and "lands after T-12" in out, out

# 3. Normal lane is parallel: behind PRs land without updating unless the base changed their files.
d, env = setup({"4": pr(4, behind=3), "5": pr(5, behind=3, files=["src/shared.py"])})
st = json.loads((d / "state.json").read_text()); st["base_changed"] = ["src/shared.py"]; (d / "state.json").write_text(json.dumps(st))
verdict(d, 4, "T-4"); verdict(d, 5, "T-5")
code, out = prog(env, "land", "t", "--pr", "5")
assert code == 3 and "src/shared.py" in out and "update-branch 5" in out, out
code, out = prog(env, "land", "t", "--pr", "4")
assert code == 0 and "landed #4" in out, out
# A strict base (GitHub says BEHIND) still asks for the update.
d, env = setup({"14": pr(14, "BEHIND")})
verdict(d, 14, "T-14")
code, out = prog(env, "land", "t", "--pr", "14")
assert code == 3 and "update-branch 14" in out, out

# 3b. Exclusive lane: a migration takes the lane in order, lands only on the latest base, then frees it.
d, env = setup({"15": pr(15, files=["db/migrations/1.sql"], behind=1), "16": pr(16, files=["db/migrations/2.sql"])})
verdict(d, 15, "T-15"); verdict(d, 16, "T-16")
code, out = prog(env, "land", "t", "--pr", "16")  # same age: #15 is first in order and takes the lane first
assert code == 2 and "#15" in out, out
code, out = prog(env, "land", "t", "--pr", "15")
assert code == 3 and "hold the exclusive lane" in out, out
code, out = prog(env, "land", "t", "--pr", "16")
assert code == 2 and "held by #15" in out, out
st = json.loads((d / "state.json").read_text()); st["prs"]["15"]["behind"] = 0; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "15")
assert code == 0, out
code, out = prog(env, "land", "t", "--pr", "16")
assert code == 0, out

# 4. A changed patch voids the verdict at landing time.
d, env = setup({"6": pr(6)})
verdict(d, 6, "T-6")
st = json.loads((d / "state.json").read_text()); st["prs"]["6"]["diff"] = diff(66); (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "6")
assert code == 3 and "patch of #6 changed" in out, out

# 5. A stack lands from its top in one merge-async; the bottom yields to it.
d, env = setup({"7": pr(7, stacked=True), "8": pr(8, base="b7", stacked=True)}, stacks=[[7, 8]])
verdict(d, 7, "T-7"); verdict(d, 8, "T-8")
code, out = prog(env, "land", "t", "--pr", "7")
assert code == 2 and "lands with its stack from #8" in out, out
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 0 and "#7 #8" in out, out
landed = [e for e in ledger(d) if e["ev"] == "landed"]
assert [e["pr"] for e in landed] == [7, 8] and landed[1]["sha"] == "m8"
assert landed[0]["stack_top"] == 8 and "stack_top" not in landed[1], landed
code, out = prog(env, "record", "t", "main_green", "--pr", "7", "--sha", "m7")
assert code != 0 and "lower stack layer" in out, out
assert any("merge-async" in " ".join(c) for c in json.loads((d / "state.json").read_text())["log"])
# The bottom layer's own land loop then finds it merged and does not record it a second time.
code, out = prog(env, "land", "t", "--pr", "7")
assert code == 0 and "already landed" in out, out
assert len([e for e in ledger(d) if e["ev"] == "landed"]) == 2

# A lower layer whose upper is outside the program lands alone, still through merge-async: gh pr merge refuses it.
d, env = setup({"30": pr(30, stacked=True), "31": pr(31, base="b30", stacked=True)}, stacks=[[30, 31]])
verdict(d, 30, "T-30")
code, out = prog(env, "land", "t", "--pr", "30")
assert code == 0 and "landed #30" in out, out
st = json.loads((d / "state.json").read_text())
assert [c[3] for c in st["log"]] == ["repos/o/r/pulls/30/merge-async"], st["log"]
assert st["prs"]["31"]["state"] == "OPEN"
landed = [e for e in ledger(d) if e["ev"] == "landed"]
assert [e["pr"] for e in landed] == [30] and landed[0]["sha"] == "m30" and "stack_top" not in landed[0], landed
# A PR in no stack keeps gh pr merge.
d, env = setup({"32": pr(32), "33": pr(33)}, stacks=[[33, 34]])
verdict(d, 32, "T-32")
code, out = prog(env, "land", "t", "--pr", "32")
assert code == 0 and "landed #32" in out, out
assert [c[:2] for c in json.loads((d / "state.json").read_text())["log"]] == [["pr", "merge"]]

# A behind exclusive stack is updated bottom-up: update-branch on the top only pulls the layer below.
d, env = setup({"7": pr(7, stacked=True, files=["migrations/3.sql"]),
                "8": pr(8, base="b7", stacked=True, files=["migrations/4.sql"], behind=1)}, stacks=[[7, 8]])
verdict(d, 7, "T-7"); verdict(d, 8, "T-8")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 3 and "update-branch 7 --repo o/r; gh pr update-branch 8" in out, out

# 6. Red main stops everything but the fix.
d, env = setup({"9": pr(9)})
verdict(d, 9, "T-9")
with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-24T00:00:01+00:00", "ev": "landed", "pr": 99, "sha": "bad"}) + "\n")
    f.write(json.dumps({"ts": "2026-09-24T00:00:02+00:00", "ev": "main_red", "pr": 99, "sha": "bad"}) + "\n")
code, out = prog(env, "land", "t", "--pr", "9")
assert code == 1 and "main is red" in out, out
code, out = prog(env, "land", "t", "--pr", "9", "--class", "main-fix")
assert code == 0, out

# A held PR does not land until the hold is released.
d, env = setup({"9": pr(9)})
verdict(d, 9, "T-9")
code, out = prog(env, "record", "t", "parked", "--pr", "9", "--note", "hold")
code, out = prog(env, "land", "t", "--pr", "9")
assert code == 1 and "on hold" in out, out
code, out = prog(env, "record", "t", "admitted", "--pr", "9")
code, out = prog(env, "land", "t", "--pr", "9")
assert code == 0, out

# A held exclusive PR never takes the lane from the next one.
d, env = setup({"1": pr(1, files=["migrations/1.sql"]), "2": pr(2, files=["migrations/2.sql"])})
verdict(d, 1, "T-1"); verdict(d, 2, "T-2")
prog(env, "record", "t", "parked", "--pr", "1", "--note", "hold")
code, out = prog(env, "land", "t", "--pr", "2")
assert code == 0, out

# 7. heavy: slots are shared machine-wide; a full set makes the next caller wait, a dead holder frees its slot.
d, env = setup({})
env["PROGRAMS_HOME"] = str(d / "programs")
with (d / "programs" / "t" / "program.json").open("r+") as f:
    cfg = json.load(f); cfg["landing"] = {"heavy_slots": 1}; f.seek(0); json.dump(cfg, f); f.truncate()
holder = subprocess.Popen([sys.executable, HERE / "prog.py", "heavy", "t", "--", "sleep", "3"], env=env)
import time; time.sleep(1)
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "true")
assert code == 1 and "every slot busy" in out, out
holder.wait()
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "sh", "-c", "exit 7")
assert code == 7, out
(d / "programs" / "_heavy" / "slot-0").write_text(json.dumps({"pid": 999999}))
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "true")
assert code == 0 and not (d / "programs" / "_heavy" / "slot-0").exists(), out
code, out = prog(env, "heavy", "-", "--", "true")
assert code == 0, out

# status and queue run end to end on the same fakes.
d, env = setup({"17": pr(17), "18": pr(18, files=["db/migrations/9.sql"])})
verdict(d, 17, "T-17"); verdict(d, 18, "T-18")
code, out = prog(env, "status", "t")
assert code == 0 and "land order (2" in out and "next:" in out, out
code, out = prog(env, "queue", "t")
assert code == 0 and "#17" in out, out
# What only the dashboard's collector sees comes back in status while its state is fresh.
(d / "programs" / "t" / "dashboard").mkdir()
(d / "programs" / "t" / "dashboard" / "state.json").write_text(json.dumps({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "summary": {
    "stalls": [{"dispatch": "ctx_1", "ticket": "T-17", "kind": "idle", "minutes": 22}],
    "spare": {"slots": 2, "ready": ["T-20"]}, "gaps": {"spawns": ["ctx_9"], "landings": [], "ci": []}}}))
code, out = prog(env, "status", "t")
assert "STALLED T-17 (ctx_1): turn ended 22 min ago" in out and "SPARE 2 slot(s)" in out and "ctx_9" in out, out

# wait reads the coordinator's Run inbox: a worker of the Run (a standing role, say) is refused.
st = json.loads((d / "state.json").read_text()); st["workers"] = [{"agentTerminalHandle": "term_role"}]
(d / "state.json").write_text(json.dumps(st))
code, out = prog(dict(env, ORCA_TERMINAL_HANDLE="term_role"), "wait", "t", "--rounds", "1")
assert code == 1 and "coordinator's Run inbox" in out, out

# A GitHub hiccup is a yield, not a refusal: on the merge call and on a read; the next pass lands.
for call in ("pr merge", "pr view"):
    d, env = setup({"21": pr(21)})
    verdict(d, 21, "T-21")
    st = json.loads((d / "state.json").read_text()); st["flaky"] = {call: 1}; (d / "state.json").write_text(json.dumps(st))
    code, out = prog(env, "land", "t", "--pr", "21")
    assert code == 2 and "TLS handshake timeout" in out and "land again" in out, (call, out)
    assert "land_failed" not in [e["ev"] for e in ledger(d)], call
    code, out = prog(env, "land", "t", "--pr", "21")
    assert code == 0 and "landed #21" in out, (call, out)
# A green rollup is not CI done: a review bot's status before Actions starts, or jobs behind `needs` not yet
# created. land yields until every workflow run at the head has finished, and merges nothing meanwhile.
for runs, why in (([], "no workflow run"), ([{"name": "CI", "status": "queued"}], "CI queued")):
    d, env = setup({"23": pr(23)})
    verdict(d, 23, "T-23")
    st = json.loads((d / "state.json").read_text()); st["runs"] = {"h23": runs}; (d / "state.json").write_text(json.dumps(st))
    code, out = prog(env, "land", "t", "--pr", "23")
    assert code == 2 and why in out and "land again" in out, (runs, out)
    assert json.loads((d / "state.json").read_text())["prs"]["23"]["state"] == "OPEN", out
    st = json.loads((d / "state.json").read_text()); st["runs"] = {"h23": [{"name": "CI", "status": "completed"}]}
    (d / "state.json").write_text(json.dumps(st))
    code, out = prog(env, "land", "t", "--pr", "23")
    assert code == 0 and "landed #23" in out, out
# A refusal that is not a hiccup still stops.
d, env = setup({"22": pr(22)})
verdict(d, 22, "T-22")
st = json.loads((d / "state.json").read_text()); st["prs"]["22"]["stacked"] = True; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "22")
assert code == 1 and "merge refused" in out, out

# A landing on a feature base says how to start its CI when push CI covers only the default branch.
d, env = setup({"23": pr(23, base="feature/x")})
verdict(d, 23, "T-23")
code, out = prog(env, "land", "t", "--pr", "23")
assert code == 0 and "gh workflow run <ci workflow file> --repo o/r --ref feature/x" in out, out
d, env = setup({"24": pr(24)})
verdict(d, 24, "T-24")
code, out = prog(env, "land", "t", "--pr", "24")
assert code == 0 and "workflow run" not in out, out

# A PR based on another tracked PR's branch, outside a GitHub stack, is told to land the lower one first.
low = pr(26); low["headRefName"] = "b26"
up = pr(27, base="b26")
d, env = setup({"26": low, "27": up})
verdict(d, 26, "T-26"); verdict(d, 27, "T-27")
code, out = prog(env, "land", "t", "--pr", "27")
assert code == 3 and "based on #26's branch b26" in out and "--base main" in out, out
code, out = prog(env, "land", "t", "--pr", "27", "--onto-lower")
assert code == 0 and "landed #27" in out, out
# The fold is marked: #27 reaches the base only with #26, so its ticket is not finished.
assert [e.get("onto") for e in ledger(d) if e["ev"] == "landed"] == [26], ledger(d)
# An untracked PR whose head is the base (the integration branch's own PR to main) does not block.
feat = pr(28); feat["headRefName"] = "feature/x"
d, env = setup({"28": feat, "29": pr(29, base="feature/x")})
verdict(d, 29, "T-29")
code, out = prog(env, "land", "t", "--pr", "29")
assert code == 0, out

# Evidence: a screen change lands only with an image rendered in its test-results comment.
SHOT = "<!-- test-results -->\n![members 1280](https://github.com/user-attachments/assets/abc)"
def shown(n, comments=(), labels=(), files=("apps/web/src/members.tsx",)):
    p = pr(n, files=list(files)); p["comments"] = list(comments); p["labels"] = list(labels)
    return p
d, env = setup({"40": shown(40)})
verdict(d, 40, "T-40")
code, out = prog(env, "land", "t", "--pr", "40")
assert code == 3 and "changes the screen (apps/web/src/members.tsx)" in out and "does not exist" in out, out
assert json.loads((d / "state.json").read_text())["prs"]["40"]["state"] == "OPEN"
code, out = prog(env, "land-check", "t", "--pr", "40")
assert code == 1 and "HOLD" in out and "changes the screen" in out, out
# Words about screenshots, an image inside a code block or a comment, and a relative path render nothing.
for body in ("<!-- test-results -->\n1280·390 캡처 40장", "<!-- test-results -->\n```\n![x](https://x/a.png)\n```",
             "<!-- test-results -->\n<!-- ![x](https://x/a.png) -->", "<!-- test-results -->\n![x](shots/a.png)"):
    d, env = setup({"41": shown(41, [body])})
    verdict(d, 41, "T-41")
    code, out = prog(env, "land", "t", "--pr", "41")
    assert code == 3 and "renders no image" in out, (body, out)
# Another comment's image does not count; the test-results one does, markdown or <img>.
for comments in ([SHOT], ["<!-- test-results -->\n<img width=600 src=\"https://x/a.png\">"]):
    d, env = setup({"42": shown(42, comments)})
    verdict(d, 42, "T-42")
    code, out = prog(env, "land", "t", "--pr", "42")
    assert code == 0 and "landed #42" in out, (comments, out)
d, env = setup({"43": shown(43, ["![x](https://x/a.png)", "<!-- test-results -->\nok"])})
verdict(d, 43, "T-43")
assert prog(env, "land", "t", "--pr", "43")[0] == 3
# Of two marker comments, land reads the first: the one sticky-comment.sh updates. A later one cannot pass it.
d, env = setup({"43": shown(43, ["<!-- test-results -->\nok", SHOT])})
verdict(d, 43, "T-43")
assert prog(env, "land", "t", "--pr", "43")[0] == 3
d, env = setup({"43": shown(43, files=["src/x.py"] * 120 + ["packages/ui/late.tsx"])})
verdict(d, 43, "T-43")
code, out = prog(env, "land", "t", "--pr", "43")
assert code == 3 and "packages/ui/late.tsx" in out, out
# Every default visual path counts, and visual_paths replaces them.
for f in ("packages/ui/button.tsx", "src/app.css", "src/Button.stories.tsx"):
    d, env = setup({"44": shown(44, files=[f])})
    verdict(d, 44, "T-44")
    assert prog(env, "land", "t", "--pr", "44")[0] == 3, f
d, env = setup({"45": shown(45, files=["site/page.tsx"])})
verdict(d, 45, "T-45")
code, out = prog(env, "set", "t", "visual_paths", ",")
assert code == 1 and "cannot be empty" in out, out
prog(env, "set", "t", "visual_paths", "site/*")
code, out = prog(env, "land", "t", "--pr", "45")
assert code == 3 and "changes the screen (site/page.tsx)" in out, out
# A person's no-visual-change label exempts it, and the output says so.
d, env = setup({"46": shown(46, labels=["no-visual-change"])})
verdict(d, 46, "T-46")
code, out = prog(env, "land", "t", "--pr", "46")
assert code == 0 and "exempt from screenshots by label no-visual-change" in out and "landed #46" in out, out
code, out = prog(env, "land-check", "t", "--pr", "46")
assert "note: #46 changes the screen" in out and "exempt" in out, out
# A failed CI run of the branch needs a line with its id and what resolved it, visual change or not.
for body, ok in (("", False), ("<!-- test-results -->\nrun 555 failed", False),
                 ("<!-- test-results -->\n- run 15559: flaky docker pull, rerun passed", False),
                 ("<!-- test-results -->\n- https://github.com/o/r/actions/runs/555: flaky docker pull, rerun 556 passed", True)):
    p = pr(47); p["comments"] = [body] if body else []
    d, env = setup({"47": p})
    st = json.loads((d / "state.json").read_text())
    st["branch_runs"] = {"b47": [{"databaseId": 555, "conclusion": "failure", "name": "CI", "headSha": "old47"},
                                 {"databaseId": 556, "conclusion": "success", "name": "CI", "headSha": "h47"},
                                 {"databaseId": 557, "conclusion": "cancelled", "name": "CI", "headSha": "h47"},
                                 # Same branch name, another PR's commit (a fork's main, a reused branch).
                                 {"databaseId": 558, "conclusion": "failure", "name": "CI", "headSha": "elsewhere"}]}
    (d / "state.json").write_text(json.dumps(st))
    verdict(d, 47, "T-47")
    code, out = prog(env, "land", "t", "--pr", "47")
    if ok:
        assert code == 0 and "landed #47" in out, out
    else:
        assert code == 3 and "555 (CI failure)" in out and "557" not in out and "558" not in out, (body, out)
# A stack checks every layer it lands.
d, env = setup({"48": dict(shown(48), stacked=True),
                "49": dict(pr(49, base="b48", stacked=True), comments=[], labels=[])}, stacks=[[48, 49]])
verdict(d, 48, "T-48"); verdict(d, 49, "T-49")
code, out = prog(env, "land", "t", "--pr", "49")
assert code == 3 and "#48 changes the screen" in out, out

# dep is per ticket: a self edge is refused, and one already in the ledger does not hold the ticket.
d, env = setup({"25": pr(25)})
verdict(d, 25, "T-25")
code, out = prog(env, "dep", "t", "--ticket", "T-25", "--after", "T-25")
assert code == 1 and "wait on itself" in out, out
with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "dep", "ticket": "T-25", "after": ["T-25"]}) + "\n")
code, out = prog(env, "land", "t", "--pr", "25")
assert code == 0 and "landed #25" in out, out

# A flag whose value an empty shell variable swallowed fails with its name, not a traceback, and records nothing.
d, env = setup({})
led = d / "programs" / "t" / "ledger.jsonl"
n = len(ledger(d)) if led.exists() else 0
code, out = prog(env, "record", "t", "spawned", "--ticket", "T-1 ctx_1 task_1", "--note")
assert code == 1 and "--note needs a value" in out and "Traceback" not in out, out
code, out = prog(env, "record", "t", "spawned", "--note", "--ticket", "T-1")
assert code == 1 and "--note needs a value" in out, out
assert (len(ledger(d)) if led.exists() else 0) == n

print("prog.py land against fake gh: all pass")
