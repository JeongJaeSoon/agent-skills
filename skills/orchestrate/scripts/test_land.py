"""`prog.py land` end to end against a fake gh. Run: python3 test_land.py"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path))
a = sys.argv[1:]
def save(): json.dump(st, open(path, "w"))
def pr(n): return st["prs"][str(n)]
if a[:2] == ["pr", "list"]:
    print(json.dumps([dict(p, number=int(n)) for n, p in st["prs"].items() if p["state"] == "OPEN"]))
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


def pr(n, mss="CLEAN", base="main", stacked=False):
    return {"state": "OPEN", "isDraft": False, "headRefOid": f"h{n}", "mergeStateStatus": mss, "baseRefName": base,
            "statusCheckRollup": [{"name": "check", "status": "COMPLETED", "conclusion": "SUCCESS",
                                   "detailsUrl": f"https://github.com/o/r/actions/runs/{n}00/job/1"}],
            "diff": diff(n), "stacked": stacked}


def setup(prs, stacks=()):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "bin").mkdir()
    (d / "bin" / "gh").write_text(FAKE_GH)
    (d / "bin" / "gh").chmod(0o755)
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


# 1. A ready PR first in line lands; the ledger records the merge commit and the lock is released.
d, env = setup({"1": pr(1)})
verdict(d, 1, "T-1")
code, out = prog(env, "land", "t", "--pr", "1")
assert code == 0 and "landed #1" in out, out
evs = [e["ev"] for e in ledger(d)]
assert evs[-3:] == ["lock_acquired", "landed", "lock_released"], evs
assert not list((d / "programs" / "t" / "locks").iterdir())

# 2. The dependency root lands before the dependent; the dependent yields, then lands.
d, env = setup({"2": pr(2), "3": pr(3)})
verdict(d, 2, "T-root"); verdict(d, 3, "T-dep")
prog(env, "dep", "t", "--ticket", "T-dep", "--after", "T-root")
code, out = prog(env, "land", "t", "--pr", "3")
assert code == 2 and "lands after T-root" in out, out
assert prog(env, "land", "t", "--pr", "2")[0] == 0
code, out = prog(env, "land", "t", "--pr", "3")
assert code == 0, out

# 3. Behind and first: act (update the branch); behind and not first: yield without updating.
d, env = setup({"4": pr(4, "BEHIND"), "5": pr(5, "BEHIND")})
verdict(d, 4, "T-4"); verdict(d, 5, "T-5")
code, out = prog(env, "land", "t", "--pr", "4")
assert code == 3 and "update-branch 4" in out, out
code, out = prog(env, "land", "t", "--pr", "5")
assert code == 2 and "#4" in out and "Do not update your branch yet" in out, out

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

print("prog.py land against fake gh: all pass")
