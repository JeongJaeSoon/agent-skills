"""`prog.py land` end to end against a fake gh. Run: python3 test_land.py"""
import json, os, pathlib, subprocess, sys, tempfile

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
if a[:2] == ["pr", "list"]:
    print(json.dumps([dict(p, number=int(n)) for n, p in st["prs"].items() if p["state"] == "OPEN"]))
elif a[:2] == ["pr", "view"] and a[-1] == "files":
    print(json.dumps({"files": [{"path": f} for f in pr(a[2])["files"]]}))
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
               PROGRAMS_HOME=str(d / "programs"))
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
assert any("merge-async" in " ".join(c) for c in json.loads((d / "state.json").read_text())["log"])
# The bottom layer's own land loop then finds it merged and does not record it a second time.
code, out = prog(env, "land", "t", "--pr", "7")
assert code == 0 and "already landed" in out, out
assert len([e for e in ledger(d) if e["ev"] == "landed"]) == 2

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

# wait reads the coordinator's Run inbox: a worker of the Run (a standing role, say) is refused.
st = json.loads((d / "state.json").read_text()); st["workers"] = [{"agentTerminalHandle": "term_role"}]
(d / "state.json").write_text(json.dumps(st))
code, out = prog(dict(env, ORCA_TERMINAL_HANDLE="term_role"), "wait", "t", "--rounds", "1")
assert code == 1 and "coordinator's Run inbox" in out, out

print("prog.py land against fake gh: all pass")
