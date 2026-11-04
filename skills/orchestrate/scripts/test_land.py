"""`prog.py land` end to end against a fake gh. Run: python3 test_land.py"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile
os.environ.pop("ORCH_HEAVY_SLOT", None)  # this suite may itself run inside `orch heavy`; its slots are its own

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
if st.get("hard_fail") == " ".join(a[:2]):
    sys.exit("gh: HTTP 403 forbidden")
if a[:2] == ["pr", "list"]:
    print(json.dumps([dict(p, number=int(n)) for n, p in st["prs"].items() if p["state"] == "OPEN"]))
elif a[:2] == ["pr", "view"] and a[-1] == "headRefName,labels,comments":
    p = pr(a[2])
    print(json.dumps({"headRefName": f"b{a[2]}",
                      "labels": [{"name": l} for l in p.get("labels", [])], "comments": [{"body": b} for b in p.get("comments", [])]}))
elif a[:1] == ["api"] and a[1].endswith("/commits"):
    n = a[1].split("/pulls/")[1].split("/")[0]; print(f"old{n}\nh{n}")
elif a[:1] == ["api"] and a[1].endswith("/files"):
    print("\n".join(pr(a[1].split("/pulls/")[1].split("/")[0])["files"]))
elif a[:2] == ["pr", "view"] and a[-1] == "files":
    print(json.dumps({"files": [{"path": f} for f in pr(a[2])["files"]]}))
elif a[:2] == ["pr", "view"] and "--jq" in a and a[a.index("--jq") + 1] == ".state":
    print(pr(a[2])["state"])
elif a[:2] == ["pr", "view"]:
    # Like real gh, only the fields asked for with --json come back.
    p = pr(a[2]); full = dict(p, mergeCommit={"oid": p.get("merge_sha")} if p.get("merge_sha") else None,
                              url=f"https://x/pull/{a[2]}", title=p.get("title", "t"), headRefName=f"b{a[2]}")
    print(json.dumps({k: full[k] for k in a[a.index("--json") + 1].split(",") if k in full and k not in p.get("drop", [])}))
elif a[:2] == ["pr", "diff"]:
    print(pr(a[2])["diff"])
elif a[:2] == ["pr", "merge"]:
    p = pr(a[2])
    if p.get("stacked"):
        sys.exit("GraphQL: This pull request is part of a stack and must be merged using the asynchronous merge REST API."
                 " For more information, see https://docs.github.com/rest/pulls/pulls#merge-a-pull-request-asynchronously"
                 " (mergePullRequest)")
    p["state"], p["merge_sha"] = "MERGED", f"m{a[2]}"; save(); st["log"] = st.get("log", []) + [a]; save()
elif a[:2] == ["api", "graphql"]:
    n = next(x for x in a if x.startswith("n="))[2:]
    if st.get("threads_fail"):
        sys.exit("gh: graphql: HTTP 403 rate limited")
    pages = st.get("threads", {}).get(n, [[]])
    i = int(next((x for x in a if x.startswith("after=")), "after=0")[6:])
    print(json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "nodes": [{"isResolved": r} for r in pages[i]],
        "pageInfo": {"hasNextPage": i + 1 < len(pages), "endCursor": str(i + 1)}}}}}}))
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
elif a[:1] == ["api"] and a[1].startswith("repos/o/r/stacks"):
    # Bodies copied from a real stack: `pull_requests` holds objects, merged stacks stay listed with open false.
    if st.get("stacks_fail"):
        sys.exit("gh: Not Found (HTTP 404)")
    assert "--paginate" in a and "--slurp" in a and "per_page=100" in a[1], a
    for s in st.get("stacks", []):
        for x in s["pull_requests"]:
            x["state"] = "open" if st["prs"].get(str(x["number"]), {}).get("state", "OPEN") == "OPEN" else "closed"
        s["open"] = s["open"] and any(x["state"] == "open" for x in s["pull_requests"])
    print(json.dumps([st.get("stacks", [])]))
elif a[:3] == ["api", "-X", "PUT"] and "merge-async" in a[3]:
    top = a[3].split("/pulls/")[1].split("/")[0]
    mode = st.get("merge_async", "merged")
    st["log"] = st.get("log", []) + [a]; save()
    if st.get("conflict"):  # a 409 returns the running request's uuid; its other fields are not documented
        print(json.dumps({"details": {"uuid": "u-1", "merge_method": "squash"}}))
        if mode == "merged":
            for n in top, "7":
                pr(n)["state"], pr(n)["merge_sha"] = "MERGED", f"m{n}"
            save()
        sys.exit("gh: A merge request is already enqueued for this pull request (HTTP 409)")
    if mode == "stale":  # a real HTTP 400: gh prints the body on stdout and its own line on stderr
        print(json.dumps({"status": "failed", "details": {"message": "Pull request head branch was modified."}}))
        sys.exit("gh: HTTP 400")
    if mode == "queued409":
        print(json.dumps({"message": "A merge request is already enqueued for this pull request"}))
        sys.exit("gh: A merge request is already enqueued for this pull request (HTTP 409)")
    if mode == "enqueued200":
        print(json.dumps({"status": "enqueued", "details": {"message": "Pull request is already queued to merge."}}))
        sys.exit(0)
    if mode in ("merged", "junk"):
        for s in st.get("stacks", []):
            members = [x["number"] for x in s["pull_requests"]]
            if int(top) in members:
                for n in members[: members.index(int(top)) + 1]:
                    pr(n)["state"], pr(n)["merge_sha"] = "MERGED", f"m{n}"
        save()
    if mode == "junk":
        print("{}")
    else:
        print(json.dumps({"status": "pending", "details": {"message": "Merge request enqueued.", "uuid": "u-1",
                                                           "merge_method": "squash", "merge_action": "default",
                                                           "expected_head_sha": f"h{top}", "bypass_rules": False}}))
elif a[:1] == ["api"] and "/merge-async/" in a[1]:
    mode = st.get("merge_async", "merged")
    if st.get("poll_blips"):
        st["poll_blips"] -= 1; save()
        sys.exit("gh: Bad Gateway (HTTP 502)")
    print(json.dumps({"merged": {"status": "merged", "details": {"message": "Pull request was merged.", "sha": "m"}},
                      "failed": {"status": "failed", "details": {"message": "Cannot perform a merge queue merge for a"
                                                                            " branch with no merge queue"}},
                      "enqueued": {"status": "enqueued", "details": {"message": "Added to the merge queue."}},
                      "stuck": {"status": "pending", "details": {"message": "Merge request enqueued."}},
                      "nonlinear": {"status": "failed", "details": {"message": "Stack needs to be rebased: PR #8's branch"
                                                                               " is not a linear descendant of PR #7's branch."}}}[mode]))
elif a[:1] == ["api"] and "/pulls/" in a[1] and a[1].split("/pulls/")[1].isdigit():
    print(json.dumps(st.get("pr_stack", {}).get(a[1].split("/pulls/")[1])))
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


FAKE_DOCKER = r'''#!/bin/sh
[ -n "$FAKE_DOCKER_FAIL" ] && exit 1
cat "$FAKE_DOCKER_STATE" 2>/dev/null || echo "[]"
'''


def stack_rows(stacks):
    return [{"id": 900 + i, "number": 100 + i, "open": True, "base": {"ref": "main"},
             "pull_requests": [{"number": n, "state": "open", "draft": False, "merged_at": None,
                                "head": {"ref": f"b{n}", "sha": f"h{n}"}} for n in s]} for i, s in enumerate(stacks)]


def setup(prs, stacks=()):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "bin").mkdir()
    (d / "bin" / "gh").write_text(FAKE_GH)
    (d / "bin" / "gh").chmod(0o755)
    (d / "bin" / "orca").write_text(FAKE_ORCA)
    (d / "bin" / "orca").chmod(0o755)
    (d / "bin" / "docker").write_text(FAKE_DOCKER)
    (d / "bin" / "docker").chmod(0o755)
    (d / "state.json").write_text(json.dumps({"prs": prs, "stacks": stack_rows(stacks)}))
    env = dict(os.environ, PATH=f"{d / 'bin'}:{os.environ['PATH']}", FAKE_GH_STATE=str(d / "state.json"),
               PROGRAMS_HOME=str(d / "programs"), ORCH_DASH="off",
               AGENT_SKILLS_LEDGER=str(d / "reap-ledger.jsonl"))
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


def no_ci_setup(*prs_, **state):
    """A repo declared CI-less: an empty rollup, no run at any head, and the key on."""
    prs = {}
    for p_ in prs_:
        p_["statusCheckRollup"] = []
        prs[p_["headRefOid"][1:]] = p_
    d, env = setup(prs)
    st = json.loads((d / "state.json").read_text())
    st.update(runs={p_["headRefOid"]: [] for p_ in prs_}, **state)
    (d / "state.json").write_text(json.dumps(st))
    (d / "cfg.json").write_text(json.dumps({"orchestrate": {"no_ci_repos": ["o/r"]}}))
    return d, dict(env, AGENT_SKILLS_CONFIG=str(d / "cfg.json"))


def merged(d):
    return [a for a in json.loads((d / "state.json").read_text()).get("log", []) if a[:2] == ["pr", "merge"]]


# 1d. The no-CI lane: with the key on, a verdict at the head, a head that contains its base and no open thread land,
# pinned to that head, through the exclusive lane, and the message does not send anyone to a main CI that never runs.
d, env = no_ci_setup(pr(70))
verdict(d, 70, "T-70")
code, out = prog(env, "land-check", "t", "--pr", "70")
assert code == 0 and "READY" in out, out
code, out = prog(env, "land", "t", "--pr", "70")
assert code == 0 and "no-CI repo" in out and "VERIFY" in out and "gh run list" not in out, out
assert merged(d) and merged(d)[0][merged(d)[0].index("--match-head-commit") + 1] == "h70", merged(d)
evs = [e["ev"] for e in ledger(d)]
assert "lock_acquired" in evs and evs[-2:] == ["landed", "lock_released"], evs

# Without the key nothing changes: the same PR waits on CI that will never come.
d, env = no_ci_setup(pr(70))
verdict(d, 70, "T-70")
(d / "cfg.json").write_text("{}")
code, out = prog(env, "land", "t", "--pr", "70")
assert code == 2 and "CI pending" in out and not merged(d), out
# A rollup gh did not return is unknown, never "no checks".
d, env = no_ci_setup(pr(70))
verdict(d, 70, "T-70")
st = json.loads((d / "state.json").read_text()); st["prs"]["70"]["drop"] = ["statusCheckRollup"]
(d / "state.json").write_text(json.dumps(st))
assert prog(env, "land-check", "t", "--pr", "70")[0] == 1 and not merged(d)

# No verdict: refused.
d, env = no_ci_setup(pr(71))
with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "ready", "pr": 71, "ticket": "T-71"}) + "\n")
code, out = prog(env, "land", "t", "--pr", "71")
assert code == 3 and "verdict" in out and not merged(d), out

# A verdict on another head with the same patch-id passes the normal lane, not this one.
d, env = no_ci_setup(pr(72))
verdict(d, 72, "T-72")
st = json.loads((d / "state.json").read_text()); st["prs"]["72"]["headRefOid"] = "h72"
(d / "programs" / "t" / "ledger.jsonl").write_text((d / "programs" / "t" / "ledger.jsonl").read_text().replace('"sha": "h72"', '"sha": "old72"'))
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "72")
assert code == 3 and "not the head" in out and not merged(d), out
assert prog(env, "land-check", "t", "--pr", "72")[0] == 1

# A head that does not contain its base.
d, env = no_ci_setup(pr(73, behind=2))
verdict(d, 73, "T-73")
code, out = prog(env, "land", "t", "--pr", "73")
assert code == 3 and "latest base" in out and "exact head" in out and not merged(d), out

# A check that did report still counts: the key does not switch off a failed one.
p_ = pr(74); d, env = no_ci_setup(p_)
verdict(d, 74, "T-74")
st = json.loads((d / "state.json").read_text())
st["prs"]["74"]["statusCheckRollup"] = [{"name": "bot", "status": "COMPLETED", "conclusion": "FAILURE"}]
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "74")
assert code == 3 and "CI failed" in out and not merged(d), out

# A stack lands in this lane when every layer meets it; any one layer short of it holds the whole unit.
def no_ci_stack(**lower):
    d, env = no_ci_setup(dict(pr(75, stacked=True), **lower), pr(76, base="b75", stacked=True))
    st = json.loads((d / "state.json").read_text()); st["stacks"] = stack_rows([[75, 76]])
    (d / "state.json").write_text(json.dumps(st))
    verdict(d, 75, "T-75"); verdict(d, 76, "T-76")
    return d, env

d, env = no_ci_stack()
code, out = prog(env, "land", "t", "--pr", "76")
assert code == 0 and "main_green" in out, out
assert [e["pr"] for e in ledger(d) if e["ev"] == "landed"] == [75, 76], ledger(d)
assert not list((d / "programs" / "_locks").glob("*.lock"))

d, env = no_ci_stack(behind=1)
code, out = prog(env, "land", "t", "--pr", "76")
assert code == 3 and "#75's head does not contain its base" in out and not merged(d), out
assert "update-branch 75 --rebase --repo o/r; gh pr update-branch 76 --rebase --repo o/r" in out, out

d, env = no_ci_stack()
ledger_file = d / "programs" / "t" / "ledger.jsonl"
ledger_file.write_text(ledger_file.read_text().replace('"sha": "h75"', '"sha": "old75"'))
code, out = prog(env, "land", "t", "--pr", "76")
assert code == 3 and "#75's verdict" in out and not merged(d), out

d, env = no_ci_stack()
st = json.loads((d / "state.json").read_text()); st["threads"] = {"75": [[False]]}
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "76")
assert code == 3 and "#75 has 1 unresolved" in out and not merged(d), out

# 1e. Every lane reads the threads: one open thread holds a CI-green PR, all resolved past the first page does not,
# and a thread read that fails holds it without calling it landed.
d, env = setup({"77": pr(77)})
verdict(d, 77, "T-77")
st = json.loads((d / "state.json").read_text()); st["threads"] = {"77": [[True, False]]}
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "77")
assert code == 3 and "1 unresolved" in out and not merged(d), out
code, out = prog(env, "land-check", "t", "--pr", "77")
assert code == 1 and "1 unresolved" in out, out
st["threads"] = {"77": [[True] * 100, [True]]}
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "77")
assert code == 0, "every thread resolved, past the first page: " + out
d, env = setup({"78": pr(78)})
verdict(d, 78, "T-78")
st = json.loads((d / "state.json").read_text()); st["threads_fail"] = True
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "78")
assert code == 2 and "unreadable" in out and not merged(d), out
assert prog(env, "land-check", "t", "--pr", "78")[0] == 1

# 1f. Any exit 1 frees the PR's own lock: main went red after the PR took the lane, and another PR's lock stays.
d, env = setup({"79": pr(79, files=["compose.yaml"], behind=1), "80": pr(80)})
verdict(d, 79, "T-79")
assert prog(env, "land", "t", "--pr", "79")[0] == 3  # took the lane, told to update
lock = d / "programs" / "_locks" / "o__r@main.lock"
assert lock.exists()
with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "landed", "pr": 80, "sha": "m80"}) + "\n")
    f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "main_red", "sha": "m80"}) + "\n")
code, out = prog(env, "land", "t", "--pr", "79")
assert code == 1 and "main is red" in out and not lock.exists(), out
d, env = setup({"81": pr(81)})
verdict(d, 81, "T-81")
with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "stop"}) + "\n")
(d / "programs" / "_locks").mkdir()
other = d / "programs" / "_locks" / "o__r@main.lock"
other.write_text(json.dumps({"pr": 50, "program": "other", "ts": "2999-01-01T00:00:00+00:00"}))
assert prog(env, "land", "t", "--pr", "81")[0] == 1 and other.exists(), "another PR's lock is not this land's to free"
# A hard gh failure ends the land the same way, a transient one does not.
d, env = setup({"84": pr(84, files=["compose.yaml"], behind=1)})
verdict(d, 84, "T-84")
assert prog(env, "land", "t", "--pr", "84")[0] == 3
lock = d / "programs" / "_locks" / "o__r@main.lock"
assert lock.exists()
st = json.loads((d / "state.json").read_text()); st["hard_fail"] = "pr list"
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "84")
assert code == 1 and "403" in out and not lock.exists(), out

# A lock deletion rereads the lane: a holder gh called closed may have handed it to another PR during that call.
sys.path.insert(0, str(HERE))
import prog as prog_module
prog_module.HOME = d / "programs"
class Stub:
    cfg, slug, logged = {"repo": "o/r"}, "t", []
    def append(self, ev, **kw): self.logged.append(ev)
lane = prog_module.ExclusiveLock(Stub(), "main")
gone = {"pr": 84, "program": "t", "ts": "2999-01-01T00:00:00+00:00"}
taken = {"pr": 85, "program": "t", "ts": "2999-01-01T00:00:00+00:00"}
def handed_over(*_a, **_k):
    lock.write_text(json.dumps(taken))
    return subprocess.CompletedProcess([], 0, "MERGED\n", "")
real_run, real_knob = prog_module.run, prog_module.knob
prog_module.run, prog_module.knob = handed_over, lambda *_: 240
lock.write_text(json.dumps(gone))
assert lane.break_if_closed(gone) == taken and json.loads(lock.read_text()) == taken and not Stub.logged
prog_module.run = lambda *_a, **_k: subprocess.CompletedProcess([], 0, "MERGED\n", "")
assert lane.break_if_closed(taken) is None and not lock.exists() and Stub.logged == ["lock_released"]
prog_module.run, prog_module.knob = real_run, real_knob

# 1g. A merge GitHub refused in the exclusive lane leaves the lane free and passes the next PR through: parked.
refused = pr(82, files=["compose.yaml"]); refused["stacked"] = True
d, env = setup({"82": refused, "83": pr(83, files=["compose.yaml"])})
verdict(d, 82, "T-82"); verdict(d, 83, "T-83")
st = json.loads((d / "state.json").read_text()); st["stacks"] = []
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "82")
assert code == 1 and "merge refused" in out, out
assert not list((d / "programs" / "_locks").glob("*.lock")) and any(e["ev"] == "parked" and e["pr"] == 82 for e in ledger(d))
code, out = prog(env, "land", "t", "--pr", "83")
assert code == 0, "the refused PR holds neither the lock nor its turn: " + out

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
# land-check says the same, stacked or not, and with no stack listing to read.
code, out = prog(env, "land-check", "t", "--pr", "14")
assert code == 1 and "HOLD" in out and "update-branch 14" in out and "Traceback" not in out, out
st = json.loads((d / "state.json").read_text()); st["stacks_fail"] = True; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land-check", "t", "--pr", "14")
assert code == 1 and "HOLD" in out and "update-branch 14" in out and "Traceback" not in out, out

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

# merge-async's result is read from its uuid, not guessed from the PR after 15 minutes.
def stack_of_two(mode, **state):
    d, env = setup({"7": pr(7, stacked=True), "8": pr(8, base="b7", stacked=True)}, stacks=[[7, 8]])
    verdict(d, 7, "T-7"); verdict(d, 8, "T-8")
    st = json.loads((d / "state.json").read_text()); st["merge_async"] = mode; st.update(state)
    (d / "state.json").write_text(json.dumps(st))
    return d, env


# A failed merge ends at once, with GitHub's own message.
d, env = stack_of_two("failed")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 1 and "no merge queue" in out, out
assert [e["note"] for e in ledger(d) if e["ev"] == "land_failed"] == [
    "Cannot perform a merge queue merge for a branch with no merge queue"], ledger(d)
# Enqueued is the normal end on a queue branch, not a failure: yield, and no landed row until the queue merges it.
d, env = stack_of_two("enqueued")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 2 and "merge queue" in out, out
evs = ledger(d)
assert not [e for e in evs if e["ev"] in ("landed", "land_failed")], evs
assert [e["note"] for e in evs if e["ev"] == "yield"][-1].startswith("in the merge queue"), evs
# Landing again while queued answers 200 enqueued by the same yield.
d, env = stack_of_two("enqueued200")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 2 and "merge queue" in out, out
assert not [e for e in ledger(d) if e["ev"] == "land_failed"], ledger(d)
# The queue merged the stack: the next land records every layer, bottom first, with no stack_top.
st = json.loads((d / "state.json").read_text())
st["prs"]["7"].update(state="MERGED", merge_sha="m7"); st["prs"]["8"].update(state="MERGED", merge_sha="m8")
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 0 and "merge queue" in out, out
landed = [e for e in ledger(d) if e["ev"] == "landed"]
assert [(e["pr"], e["sha"]) for e in landed] == [(7, "m7"), (8, "m8")] and not any("stack_top" in e for e in landed), landed
# A queue that put the layers in separate groups: the top still queued yields, and its merge records both.
d, env = stack_of_two("enqueued")
prog(env, "land", "t", "--pr", "8")
st = json.loads((d / "state.json").read_text())
st["prs"]["7"].update(state="MERGED", merge_sha="m7"); (d / "state.json").write_text(json.dumps(st))
assert prog(env, "land", "t", "--pr", "8")[0] == 2
st = json.loads((d / "state.json").read_text())
st["prs"]["8"].update(state="MERGED", merge_sha="m8"); (d / "state.json").write_text(json.dumps(st))
assert prog(env, "land", "t", "--pr", "8")[0] == 0
assert [e["pr"] for e in ledger(d) if e["ev"] == "landed"] == [7, 8], ledger(d)
# The queue took the lower layer and the retry merges the top itself: the lower layer is still recorded.
d, env = stack_of_two("enqueued")
prog(env, "land", "t", "--pr", "8")
st = json.loads((d / "state.json").read_text())
st["prs"]["7"].update(state="MERGED", merge_sha="m7"); st["merge_async"] = "merged"; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 0 and "landed #8" in out, out
assert [e["pr"] for e in ledger(d) if e["ev"] == "landed"] == [7, 8], ledger(d)
# A 409 names the request already running: its result is followed, and a failure there is seen.
d, env = stack_of_two("failed", conflict=True)
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 1 and "no merge queue" in out, out
d, env = stack_of_two("merged", conflict=True)
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 0 and "#7 #8" in out, out
# A 409 that names none is a yield, and not called a queue.
d, env = stack_of_two("queued409")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 2 and "already running" in out and "queue" not in out, out
assert not [e for e in ledger(d) if e["ev"] == "land_failed"], ledger(d)
# A stack that is not linear gets the bottom-up rebase, not the refusal alone.
d, env = stack_of_two("nonlinear")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 3 and "update-branch 7 --rebase --repo o/r; gh pr update-branch 8 --rebase" in out, out
# The stack listing failed: a PR whose own field says stacked yields rather than merge layers it cannot see...
d, env = stack_of_two("merged", stacks_fail=True, pr_stack={"8": {"number": 100}})
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 2 and "stack listing did not answer" in out and "could not be read" in out, out
assert not json.loads((d / "state.json").read_text()).get("log"), out
# ...and one that says no lands as before, unchecked, with one warning line (a server with no stacks endpoint).
d, env = setup({"70": pr(70)})
verdict(d, 70, "T-70")
st = json.loads((d / "state.json").read_text()); st["stacks_fail"] = True; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "70")
assert code == 0 and "landed #70" in out and out.count("could not be read") == 1, out
# A lower layer outside the list of open PRs would merge unchecked: merge-async takes every open layer below.
d, env = setup({"8": pr(8, base="b7", stacked=True)}, stacks=[[7, 8]])
verdict(d, 8, "T-8")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 3 and "#7 under #8" in out, out
assert not json.loads((d / "state.json").read_text()).get("log"), out


# A land-after edge outside a stack: a warning at warn, exit 3 at deny, off at off.
def dependent(level=None, stacks=()):
    d, env = setup({"71": pr(71), "72": pr(72)}, stacks=stacks)
    verdict(d, 71, "T-low"); verdict(d, 72, "T-up")
    prog(env, "dep", "t", "--ticket", "T-up", "--after", "T-low")
    (d / "skills.json").write_text(json.dumps({"orchestrate": {"stack_dependent": level}}) if level is not None else "{}")
    return d, dict(env, AGENT_SKILLS_CONFIG=str(d / "skills.json"))


d, env = dependent()
code, out = prog(env, "land", "t", "--pr", "72")
assert code == 2 and "lands after T-low" in out and "warning: #72 lands after #71" in out and "gh stack link 71 72" in out, out
code, out = prog(env, "land", "t", "--pr", "71")  # the lower end sees it too: landing alone would end the chance
assert code == 0 and "warning: #72 lands after #71" in out, out
d, env = dependent("deny")
code, out = prog(env, "land", "t", "--pr", "72")
assert code == 3 and "gh stack link 71 72 --base main" in out and "orchestrate.stack_dependent" in out, out
code, out = prog(env, "land", "t", "--pr", "71")
assert code == 3 and "gh stack link 71 72" in out, out
code, out = prog(env, "land-check", "t", "--pr", "71")
assert code == 1 and "HOLD" in out and "gh stack link 71 72" in out, out
d, env = dependent("deny")
code, out = prog(env, "land-check", "t", "--pr", "71", "--main-fix")  # the diagnosis agrees with the land below
assert code == 0 and "READY" in out and "warning: #72 lands after #71" in out, out
code, out = prog(env, "land", "t", "--pr", "71", "--class", "main-fix")  # a red main's repair is not held back
assert code == 0 and "warning: #72 lands after #71" in out, out
# The open PR listing unread: land-check stays a diagnosis, one warning and no judgement of the edge.
d, env = dependent("deny")
st = json.loads((d / "state.json").read_text()); st["flaky"] = {"pr list": 1}; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land-check", "t", "--pr", "71")
assert code == 0 and "READY" in out and out.count("could not be read") == 1 and "gh stack link" not in out, out
d, env = dependent(False)
code, out = prog(env, "land", "t", "--pr", "71")
assert code == 0 and "gh stack link" not in out, out
# Stacked in the right order passes at deny. Stacked upside down can never land: said at any level, even off.
d, env = dependent("deny", stacks=[[71, 72]])
code, out = prog(env, "land", "t", "--pr", "71")
assert code == 2 and "lands with its stack from #72" in out and "gh stack link" not in out, out
d, env = dependent("off", stacks=[[72, 71]])
code, out = prog(env, "land", "t", "--pr", "71")
assert code == 3 and "#72 below #71" in out, out
# The stack list cannot be read: nothing is judged, one warning, and the deny does not fire.
d, env = dependent("deny")
st = json.loads((d / "state.json").read_text()); st["stacks_fail"] = True; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "72")
assert code == 2 and "lands after T-low" in out and out.count("could not be read") == 1 and "gh stack link" not in out, out
# A head that moved under the request is the ordinary "moved" yield, read from the body of the HTTP 400.
d, env = stack_of_two("stale")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 2 and "moved" in out, out
assert not [e for e in ledger(d) if e["ev"] == "land_failed"], ledger(d)
# One poll that gets a 502 is not the merge's result.
d, env = stack_of_two("merged", poll_blips=1)
real_sleep = "import time; time.sleep = lambda s: None; import runpy, sys; sys.argv = ['prog.py'] + sys.argv[1:]; "
r = subprocess.run([sys.executable, "-c", real_sleep + f"runpy.run_path({str(HERE / 'prog.py')!r}, run_name='__main__')",
                    "land", "t", "--pr", "8"], env=env, capture_output=True, text=True)
assert r.returncode == 0 and "#7 #8" in r.stdout, r.stdout + r.stderr
# A request still pending when the polls run out is not a failure, and the layers it covers are recorded later.
d, env = stack_of_two("stuck")
r = subprocess.run([sys.executable, "-c", real_sleep + f"runpy.run_path({str(HERE / 'prog.py')!r}, run_name='__main__')",
                    "land", "t", "--pr", "8"], env=env, capture_output=True, text=True)
assert r.returncode == 2 and "still pending" in r.stdout, r.stdout + r.stderr
assert not [e for e in ledger(d) if e["ev"] in ("land_failed", "landed")], ledger(d)
st = json.loads((d / "state.json").read_text())
st["prs"]["7"].update(state="MERGED", merge_sha="m7"); st["merge_async"] = "merged"; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 0 and [e["pr"] for e in ledger(d) if e["ev"] == "landed"] == [7, 8], out
# An answer with no status or uuid falls back to reading the PRs, with one warning line.
d, env = stack_of_two("junk")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 0 and "#7 #8" in out and out.count("warning: merge-async") == 1, out
# Merged stacks stay in the listing with open false: they must not count as a stack.
d, env = stack_of_two("merged")
st = json.loads((d / "state.json").read_text()); st["stacks"][0]["open"] = False
st["prs"]["7"]["stacked"] = False; (d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "7")
assert code == 0 and [c[:2] for c in json.loads((d / "state.json").read_text())["log"]] == [["pr", "merge"]], out

# A behind exclusive stack is rebased bottom-up: a merge commit, or a lower layer alone, leaves it not linear.
d, env = setup({"7": pr(7, stacked=True, files=["migrations/3.sql"]),
                "8": pr(8, base="b7", stacked=True, files=["migrations/4.sql"], behind=1)}, stacks=[[7, 8]])
verdict(d, 7, "T-7"); verdict(d, 8, "T-8")
code, out = prog(env, "land", "t", "--pr", "8")
assert code == 3 and "update-branch 7 --rebase --repo o/r; gh pr update-branch 8 --rebase" in out, out

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
holder = subprocess.Popen([sys.executable, HERE / "prog.py", "heavy", "t", "--", "sleep", "2"], env=env)
time.sleep(1)
code, out = prog(env, "heavy", "t", "--wait-minutes", "1", "--", "true")  # says it waits, then runs once the slot frees
assert code == 0 and "모두 사용 중이라" in out and "sleep 2" in out, out
holder.wait()
# A command already in a slot that calls `orch heavy` again runs at once instead of waiting on its own slot.
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", sys.executable, str(HERE / "prog.py"), "heavy", "t",
                 "--wait-minutes", "0", "--", "sh", "-c", "exit 5")
assert code == 5, out
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "sh", "-c", "exit 7")
assert code == 7, out
# A slot variable left over from a run that ended (a background child) is no slot: it waits like anyone else.
holder = subprocess.Popen([sys.executable, HERE / "prog.py", "heavy", "t", "--", "sleep", "3"], env=env)
time.sleep(1)
stale = d / "programs" / "_heavy" / "slot-gone"
stale.write_text(json.dumps({"pid": 999999}))
code, out = prog(dict(env, ORCH_HEAVY_SLOT=str(stale)), "heavy", "t", "--wait-minutes", "0", "--", "true")
assert code == 1 and "every slot busy" in out, out
# A nested run's stack list is no holder. The value parses as JSON but not as a holder dict, and it is too long
# to turn up by chance in the real pid and time the message prints.
stack_entry = "9876543210987"
(d / "programs" / "_heavy" / "slot-0.stacks").write_text(f"{stack_entry}\n")
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "true")
assert code == 1 and "every slot busy" in out and "Traceback" not in out, out
assert stack_entry not in out and "slot-0.stacks" not in out, out
code, out = prog(dict(env, ORCH_HEAVY_SLOT=str(d / "programs" / "_heavy" / "slot-0")), "heavy", "t", "--wait-minutes", "0",
                 "--", "true")  # a live slot that is not an ancestor's
assert code == 1 and "every slot busy" in out, out
# A list a killed run left behind is dropped when the slot is taken again, so it hides no new stack.
holder.wait()
(d / "programs" / "_heavy" / "slot-0.stacks").write_text("old\n")
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "true")
assert code == 0 and not (d / "programs" / "_heavy" / "slot-0.stacks").exists(), out
stale.unlink()
(d / "programs" / "_heavy" / "slot-0").write_text(json.dumps({"pid": 999999}))
code, out = prog(env, "heavy", "t", "--wait-minutes", "0", "--", "true")
assert code == 0 and not (d / "programs" / "_heavy" / "slot-0").exists(), out
code, out = prog(env, "heavy", "-", "--", "true")
assert code == 0, out

# heavy registers the compose projects its command left, with a config file in this worktree, in the reap ledger.
wt = d / "card"
wt.mkdir()
subprocess.run(["git", "init", "-q", str(wt)], check=True)
wt = pathlib.Path(os.path.realpath(wt))
after = json.dumps([{"Name": "verify-1", "Status": "running(3)", "ConfigFiles": f"{wt}/compose.yaml,/tmp/ports.yml"},
                    {"Name": "other-card", "Status": "running(2)", "ConfigFiles": "/elsewhere/compose.yaml"}])
heavy_env = dict(env, FAKE_DOCKER_STATE=str(d / "docker.json"))
r = subprocess.run([sys.executable, HERE / "prog.py", "heavy", "-", "--pr", "acme/app#5", "--", "sh", "-c",
                    f"printf '%s' '{after}' > {d / 'docker.json'}"], env=heavy_env, cwd=wt, capture_output=True, text=True)
assert r.returncode == 0, r.stderr
rows = [json.loads(l) for l in (d / "reap-ledger.jsonl").read_text().splitlines()]
assert [(x["kind"], x["id"], x["links"]["worktree"], x["links"]["pr"]) for x in rows] == \
    [("compose-stack", "verify-1", str(wt), "acme/app#5")], rows
assert isinstance(rows[0]["pid"], int) and "down" in r.stderr and "-v 금지" in r.stderr and "other-card" in r.stderr, r.stderr
# A project that was there before is not this run's.
r = subprocess.run([sys.executable, HERE / "prog.py", "heavy", "-", "--", "true"], env=heavy_env, cwd=wt, capture_output=True, text=True)
assert r.returncode == 0 and len((d / "reap-ledger.jsonl").read_text().splitlines()) == 1, r.stderr
# A nested `orch heavy` inside a slot takes no slot but still registers the stacks its own command starts.
after2 = json.dumps(json.loads(after) + [{"Name": "verify-2", "Status": "running(1)", "ConfigFiles": f"{wt}/compose.yaml"}])
r = subprocess.run([sys.executable, HERE / "prog.py", "heavy", "-", "--pr", "acme/app#6", "--", "sh", "-c",
                    f"printf '%s' '{after2}' > {d / 'docker.json'}"], env=dict(heavy_env, ORCH_HEAVY_SLOT=str(d / "slot-x")),
                   cwd=wt, capture_output=True, text=True)
rows = [json.loads(l) for l in (d / "reap-ledger.jsonl").read_text().splitlines()]
assert r.returncode == 0 and [(x["id"], x["links"]["pr"]) for x in rows[1:]] == [("verify-2", "acme/app#6")], (rows, r.stderr)
# The outer run sees the inner run's stack as new too, and leaves its registration (and PR link) alone.
after3 = json.dumps(json.loads(after2) + [{"Name": "verify-3", "Status": "running(1)", "ConfigFiles": f"{wt}/compose.yaml"}])
inner = [sys.executable, str(HERE / "prog.py"), "heavy", "-", "--pr", "acme/app#7", "--", "sh", "-c",
         f"printf '%s' '{after3}' > {d / 'docker.json'}"]
r = subprocess.run([sys.executable, HERE / "prog.py", "heavy", "-", "--wait-minutes", "0", "--", *inner],
                   env=heavy_env, cwd=wt, capture_output=True, text=True)
rows = [json.loads(l) for l in (d / "reap-ledger.jsonl").read_text().splitlines()]
assert r.returncode == 0 and [(x["id"], x["links"]["pr"]) for x in rows[2:]] == [("verify-3", "acme/app#7")], (rows, r.stderr)
assert not list((d / "programs" / "_heavy").glob("*.stacks")), "the outer run removes the inner run's list"
# Three levels: the middle run, with its own PR, leaves the deepest run's registration alone too.
after4 = json.dumps(json.loads(after3) + [{"Name": "verify-4", "Status": "running(1)", "ConfigFiles": f"{wt}/compose.yaml"}])
deep = [sys.executable, str(HERE / "prog.py"), "heavy", "-", "--pr", "acme/app#9", "--", "sh", "-c",
        f"printf '%s' '{after4}' > {d / 'docker.json'}"]
middle = [sys.executable, str(HERE / "prog.py"), "heavy", "-", "--pr", "acme/app#8", "--", *deep]
r = subprocess.run([sys.executable, HERE / "prog.py", "heavy", "-", "--wait-minutes", "0", "--", *middle],
                   env=heavy_env, cwd=wt, capture_output=True, text=True)
rows = [json.loads(l) for l in (d / "reap-ledger.jsonl").read_text().splitlines()]
assert r.returncode == 0 and [(x["id"], x["links"]["pr"]) for x in rows[3:]] == [("verify-4", "acme/app#9")], (rows, r.stderr)
# With Docker unreadable a nested run still ends with its command's exit code.
r = subprocess.run([sys.executable, HERE / "prog.py", "heavy", "-", "--", sys.executable, str(HERE / "prog.py"), "heavy", "-",
                    "--", "sh", "-c", "exit 3"], env=dict(heavy_env, FAKE_DOCKER_FAIL="1"), cwd=wt, capture_output=True, text=True)
assert r.returncode == 3 and "Traceback" not in r.stderr, r.stderr

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

# wait reads the lead's Run inbox: a worker of the Run (a standing role, say) is refused.
st = json.loads((d / "state.json").read_text()); st["workers"] = [{"agentTerminalHandle": "term_role"}]
(d / "state.json").write_text(json.dumps(st))
code, out = prog(dict(env, ORCA_TERMINAL_HANDLE="term_role"), "wait", "t", "--rounds", "1")
assert code == 1 and "lead's inbox" in out, out

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
        assert code == 3 and "555 (CI failure)" in out and "557 (CI" not in out and "558 (CI" not in out, (body, out)
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

# A dispatched lead (its terminal is a worker on the Run) cannot land under human-gate: no Orca gate, so no
# `approved`. init refuses before writing program.json; `orch decide add` is no landing path, so no message offers it.
d, env = setup({})
st = json.loads((d / "state.json").read_text())
(d / "state.json").write_text(json.dumps(dict(st, workers=[{"dispatchId": "ctx_lead", "terminal": "term_lead"}])))
lead_env = dict(env, ORCA_TERMINAL_HANDLE="term_lead")
code, out = prog(lead_env, "init", "hg", "--repo", "o/r", "--run", "run_x", "--merge-policy", "human-gate")
assert code != 0 and "/goal card" in out and "autonomous" in out, out
assert "decide add" not in out, out
assert not (d / "programs" / "hg" / "program.json").exists()
code, out = prog(lead_env, "init", "au", "--repo", "o/r", "--run", "run_x")
assert code == 0 and "lead is dispatch ctx_lead" in out, out
# A program.json from before the init refusal (a dispatched lead on human-gate): gate refuses and points at a card lead.
cfg_path = d / "programs" / "au" / "program.json"
cfg_path.write_text(json.dumps(dict(json.loads(cfg_path.read_text()), merge_policy="human-gate")))
code, out = prog(lead_env, "gate", "au", "--pr", "1")
assert code != 0 and "/goal card" in out and "decide add" not in out, out
src =(HERE / "prog.py").read_text()
assert "decide add` per PR" not in src, "prog.py still offers `orch decide add` per PR as a way to land"

# `orch gate`, the step land-check's HOLD names, asks the user with the PR title and records the gate it opened.
FAKE_ORCA_GATE = r'''#!/usr/bin/env python3
import json, os, sys
st = json.load(open(os.environ["FAKE_GH_STATE"]))
a = sys.argv[1:]
st["orca_log"] = st.get("orca_log", []) + [a]; json.dump(st, open(os.environ["FAKE_GH_STATE"], "w"))
reply = st.get("orca_reply", {})
if a[:2] == ["orchestration", "task-create"]:
    print(json.dumps({"ok": True, "result": reply.get("task", {"task": {"id": "task_g"}})}))
elif a[:2] == ["orchestration", "gate-create"] and reply.get("gate_error"):
    print(json.dumps({"ok": False, "error": reply["gate_error"]}))
elif a[:2] == ["orchestration", "gate-create"]:
    print(json.dumps({"ok": True, "result": reply.get("gate", {"gate": {"id": "gate_g"}})}))
else:
    print(json.dumps({"ok": True, "result": {"tasks": [], "workers": []}}))
'''


def gate_setup(prs, orca_reply=None):
    d, env = setup(prs)
    (d / "bin" / "orca").write_text(FAKE_ORCA_GATE)
    st = json.loads((d / "state.json").read_text())
    (d / "state.json").write_text(json.dumps(dict(st, orca_reply=orca_reply or {})))
    env = {k: v for k, v in env.items() if k != "ORCA_TERMINAL_HANDLE"}
    code, out = prog(env, "set", "t", "merge_policy", "human-gate")
    assert code == 0, out
    return d, env


def orca_calls(d, verb):
    return [c for c in json.loads((d / "state.json").read_text()).get("orca_log", []) if c[1:2] == [verb]]


# land itself refuses what land-check holds: no gate yet, or a gate still open, merges nothing.
d, env = gate_setup({"59": pr(59)})
verdict(d, 59, "T-59")
code, out = prog(env, "land", "t", "--pr", "59")
assert code == 1 and "open the gate first" in out and not merged(d), out
assert prog(env, "gate", "t", "--pr", "59")[0] == 0
code, out = prog(env, "land", "t", "--pr", "59")
assert code == 1 and "waiting for the user" in out and not merged(d), out

# The gate guards a merge land would make: a PR a person already merged is still recorded.
d, env = gate_setup({"58": pr(58)})
verdict(d, 58, "T-58")
st = json.loads((d / "state.json").read_text()); st["prs"]["58"].update(state="MERGED", merge_sha="m58")
(d / "state.json").write_text(json.dumps(st))
code, out = prog(env, "land", "t", "--pr", "58")
assert code == 0 and "merged outside land" in out and any(e["ev"] == "landed" and e["pr"] == 58 for e in ledger(d)), out

# Every layer a stack lands needs its own approval, and a newer gate voids an older approval.
d, env = gate_setup({"56": pr(56, stacked=True), "57": pr(57, base="b56", stacked=True)})
st = json.loads((d / "state.json").read_text()); st["stacks"] = stack_rows([[56, 57]])
(d / "state.json").write_text(json.dumps(st))
verdict(d, 56, "T-56"); verdict(d, 57, "T-57")
def add_row(**row):
    with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", **row}) + "\n")
add_row(ev="approved", pr=57)
code, out = prog(env, "land", "t", "--pr", "57")
assert code == 1 and "#56: human-gate" in out and not merged(d), out
add_row(ev="approved", pr=56)
add_row(ev="gate_opened", pr=57, task="task_g", gate="gate_g")
code, out = prog(env, "land", "t", "--pr", "57")
assert code == 1 and "waiting for the user" in out and not merged(d), out
assert prog(env, "land-check", "t", "--pr", "57")[0] == 1
add_row(ev="approved", pr=57)
assert prog(env, "land", "t", "--pr", "57")[0] == 0

d, env = gate_setup({"60": dict(pr(60), title="Add the widget")})
code, out = prog(env, "gate", "t", "--pr", "60")
assert code == 0 and "Traceback" not in out, out
q = orca_calls(d, "gate-create")[0]
assert q[q.index("--question") + 1] == "Land PR #60: Add the widget?", q
assert [e for e in ledger(d) if e["ev"] == "gate_opened"][-1]["gate"] == "gate_g"

# A PR view without a title (gh left the field out) still opens the gate, with the PR URL in the question and one
# warning line; a closed PR, or an Orca reply without an id, opens no gate and records none.
d, env = gate_setup({"61": dict(pr(61), drop=["title"])})
code, out = prog(env, "gate", "t", "--pr", "61")
assert code == 0 and "Traceback" not in out, out
assert sum("warning:" in l and "title" in l for l in out.splitlines()) == 1, out
q = orca_calls(d, "gate-create")[0]
assert q[q.index("--question") + 1] == "Land PR #61 (https://x/pull/61)?", q
d, env = gate_setup({"62": dict(pr(62), state="MERGED")})
code, out = prog(env, "gate", "t", "--pr", "62")
assert code != 0 and "MERGED" in out and "Traceback" not in out, out
assert not orca_calls(d, "task-create") and not [e for e in ledger(d) if e["ev"] == "gate_opened"]
d, env = gate_setup({"63": pr(63)}, orca_reply={"gate": {"gate": {}}})
code, out = prog(env, "gate", "t", "--pr", "63")
assert code != 0 and "Traceback" not in out and not [e for e in ledger(d) if e["ev"] == "gate_opened"], out
assert [c[c.index("--status") + 1] for c in orca_calls(d, "task-update")] == ["failed"], orca_calls(d, "task-update")
d, env = gate_setup({"64": pr(64)}, orca_reply={"gate_error": {"code": "boom"}})
code, out = prog(env, "gate", "t", "--pr", "64")
assert code != 0 and "boom" in out and "marked failed" in out and "Traceback" not in out, out
assert not [e for e in ledger(d) if e["ev"] == "gate_opened"]
assert [c[c.index("--status") + 1] for c in orca_calls(d, "task-update")] == ["failed"], orca_calls(d, "task-update")

# A gate_opened row naming no task or gate is no gate: land-check warns once and asks for a gate, and orch gate opens one.
d, env = gate_setup({"65": pr(65)})
verdict(d, 65, "T-65")
with (d / "programs" / "t" / "ledger.jsonl").open("a") as f:
    f.write(json.dumps({"ts": "2026-09-24T00:00:00+00:00", "ev": "gate_opened", "pr": 65}) + "\n")
code, out = prog(env, "land-check", "t", "--pr", "65")
assert code == 1 and "open the gate first" in out and "Traceback" not in out, out
assert sum(l.startswith("warning:") and "gate_opened" in l for l in out.splitlines()) == 1, out
code, out = prog(env, "gate", "t", "--pr", "65")
assert code == 0 and [e for e in ledger(d) if e["ev"] == "gate_opened"][-1]["gate"] == "gate_g", out
code, out = prog(env, "land-check", "t", "--pr", "65")
assert "gate open in Orca, waiting for the user" in out and "Traceback" not in out, out
assert sum(l.startswith("warning:") and "gate_opened" in l for l in out.splitlines()) == 1, out
code, out = prog(env, "gate", "t", "--pr", "65")
assert code != 0 and "already open" in out and "gate_g" in out, out
assert sum(l.startswith("warning:") and "gate_opened" in l for l in out.splitlines()) == 1, out

# land-check on a PR view without its state holds with one warning line, never READY and never a traceback.
d, env = gate_setup({"66": dict(pr(66), drop=["state"])})
code, out = prog(env, "land-check", "t", "--pr", "66")
assert code == 1 and "HOLD: PR state unknown" in out and "READY" not in out and "Traceback" not in out, out
assert sum(l.startswith("warning:") for l in out.splitlines()) == 1, out

print("prog.py land against fake gh: all pass")
