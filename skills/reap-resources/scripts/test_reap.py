"""Judgement rules of reap.py, plus one real reap of throwaway processes. Run: python3 test_reap.py"""
import json, os, pathlib, re, subprocess, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import reap

H = 3600


def row(pid, cmd, ppid=1, age=10 * H, rss=1024):
    return {"pid": pid, "ppid": ppid, "age": age, "start": "Sat Sep 26 00:00:00 2026", "rss": rss, "cpu": 1.0, "cmd": cmd}


# Orphans: ppid 1 and the executable (or its script) under a configured path; never an argument or a prompt.
rows = {
    1: row(1, "/cache/uv/bin/python /cache/uv/bin/litellm --port 4000"),
    2: row(2, "/cache/uv/bin/python -m worker", ppid=77),
    3: row(3, "/usr/bin/python3 /cache/uv/tool.py"),
    4: row(4, "claude please look at /cache/uv/bin/litellm"),
    5: row(5, "/cache/uv-other/bin/litellm"),
    6: row(6, "/cache/uv/bin/litellm", age=H),
    7: row(7, "/usr/bin/python3 -u /cache/uv/server.py"),
    8: row(8, "/usr/bin/python3 /opt/app.py --data /cache/uv/x"),
    9: row(9, "/usr/bin/node --test-reporter-destination /cache/uv/report.js /opt/app.js"),
}
got = {i["pid"]: i["target"] for i in reap.judge_orphans(rows, ["/cache/uv/"], 6)}
# 7: a system interpreter with flags before a cache script is missed on purpose (see the comment in reap.py).
assert got == {1: True, 3: True, 6: False}, got
assert reap.judge_orphans(rows, [], 6) == []

# A person's shell and what runs under it are never a target, however an orphan's tree reaches them; what an agent
# started from that shell (a Claude session in between) still is.
rows = {
    60: row(60, "/cache/uv/terminal-host"),
    61: row(61, "/usr/bin/login -flpq me /bin/zsh -l", ppid=60),
    62: row(62, "-/bin/zsh -l", ppid=61),
    63: row(63, "bash wait-loop.sh", ppid=62),
    64: row(64, "sleep 30", ppid=63),
    65: row(65, "claude --permission-mode auto", ppid=62),
    66: row(66, "/bin/zsh -c npm run dev", ppid=65),
    67: row(67, "node vite", ppid=66),
    70: row(70, "/cache/uv/tmux-server"),
    71: row(71, "zsh", ppid=70),
    72: row(72, "python3 job.py", ppid=71),
    80: row(80, "/cache/uv/bin/litellm"),
    81: row(81, "/bin/sh -c worker", ppid=80),
}
rows[70]["cmd"] = "tmux new -s x"
assert [p for p in rows if reap.human_owned(p, rows)] == [62, 63, 64, 71, 72]
got = {i["pid"]: (i["target"], i["why"]) for i in reap.judge_orphans(rows, ["/cache/uv/"], 6)}
assert got[60][0] is False and "사람이 띄운 셸" in got[60][1], got
assert got[80] == (True, "ppid 1, 테스트 캐시 경로"), got

# Load: every top consumer gets one class, and its CPU counts its whole tree.
ME = 501
rows = {
    10: {**row(10, "/Library/Agent/agentd --daemon"), "uid": 0, "cpu": 220.0},
    20: {**row(20, "claude --permission-mode auto", ppid=99), "uid": ME, "cpu": 8.0},
    21: {**row(21, "node mcp-server.js", ppid=20), "uid": ME, "cpu": 2.0},
    30: {**row(30, "claude --permission-mode auto", ppid=99), "uid": ME, "cpu": 5.0},
    31: {**row(31, "lockf /tmp/bench.lock python3 bench.py", ppid=30), "uid": ME, "cpu": 95.0},
    40: {**row(40, "/cache/test-worker"), "uid": ME, "cpu": 30.0},
    41: {**row(41, "test-helper", ppid=40), "uid": ME, "cpu": 10.0},
    50: {**row(50, "/usr/local/bin/node server.js", ppid=99), "uid": ME, "cpu": 12.0},
    # Ours even from a system path: the Virtualization framework runs a container VM as this user.
    60: {**row(60, "/System/Library/Frameworks/Virtualization.framework/XPCServices/VirtualMachine", ppid=1),
         "uid": ME, "cpu": 15.0},
    65: {**row(65, "/opt/tools/bin/scanner", ppid=1), "uid": 0, "cpu": 13.0},
    70: {**row(70, "claude -p", ppid=99), "uid": ME, "cpu": 3.0},
}
quiet = {"/w/idle": 3 * H, "/w/busy": 60}.get
got = reap.judge_load(rows, {20: "/w/idle", 30: "/w/busy", 70: "/w/new"}, {40}, quiet, idle_s=H, me=ME, top=6)
assert [(i["pid"], i["cls"]) for i in got] == [(10, "system"), (30, "ours-working"), (40, "leftover"), (60, "ours-working"),
                                               (65, "system"), (50, "ours-working")], got
assert got[1]["cpu"] == 100.0 and got[1]["procs"] == 2 and got[2]["procs"] == 2, got
got = {i["pid"]: i for i in reap.judge_load(rows, {20: "/w/idle", 30: "/w/busy", 70: "/w/new"}, {40}, quiet, idle_s=H, me=ME)}
assert got[20]["cls"] == "ours-idle" and "조용" in got[20]["why"], got[20]
# A session with no transcript on record is not called idle.
assert got[70]["cls"] == "ours-working", got[70]
# A quiet session whose children still burn CPU (a background benchmark) is working.
rows[80] = {**row(80, "claude --permission-mode auto", ppid=99), "uid": ME, "cpu": 1.0}
rows[81] = {**row(81, "python3 bench.py", ppid=80), "uid": ME, "cpu": 60.0}
got = {i["pid"]: i["cls"] for i in reap.judge_load(rows, {20: "/w/idle", 80: "/w/idle"}, set(), quiet, idle_s=H, me=ME)}
assert got[80] == "ours-working" and got[20] == "ours-idle", got
assert set(reap.LOAD_ACTIONS) == {"ours-idle", "ours-working", "leftover", "system"}

# A session is as fresh as its newest transcript, subagents' included; the project dir replaces
# every non-alphanumeric character of the cwd with "-".
with tempfile.TemporaryDirectory() as tmp:
    proj = pathlib.Path(tmp) / "-w-my-repo-x"
    (proj / "sid" / "subagents").mkdir(parents=True)
    main, sub = proj / "sid.jsonl", proj / "sid" / "subagents" / "agent-1.jsonl"
    main.write_text("{}\n")
    sub.write_text("{}\n")
    now = time.time()
    os.utime(main, (now - 3 * H, now - 3 * H))
    os.utime(sub, (now - 60, now - 60))
    q = reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x")
    assert q is not None and q < 120, q
    (proj / "gone.jsonl").symlink_to(proj / "missing")
    assert reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x") == q, "a dangling transcript link is skipped"

# Docker: only dangling volumes of a project with no container at all; a volume named after a live project stays.
now = time.time()
vols = [
    {"name": "e2e-1_data", "labels": {"com.docker.compose.project": "e2e-1"}, "created": now - 30 * H},
    {"name": "live_data", "labels": {"com.docker.compose.project": "live"}, "created": now - 30 * H},
    {"name": "ws-live-abc", "labels": {}, "created": now - 30 * H},
    {"name": "loose", "labels": {}, "created": now - 30 * H},
    {"name": "e2e-2_data", "labels": {"com.docker.compose.project": "e2e-2"}, "created": now - H},
]
got = {v["name"]: (v["target"], v["why"]) for v in reap.judge_volumes(vols, {"live"}, 6, now)}
assert [k for k, (t, _) in got.items() if t] == ["volume e2e-1_data"], got
assert "live" in got["volume ws-live-abc"][1]
imgs = [{"id": "aaa111", "created": now - 30 * H, "size": "1MB"}, {"id": "bbb222", "created": now - 30 * H, "size": "1MB"}]
got = {i["key"]: i["target"] for i in reap.judge_images(imgs, ["sha256:bbb222ffff"], 6, now)}
assert got == {"image:aaa111": True, "image:bbb222": False}, got

# Branches: merged or closed PR, no worktree, tip is what the PR carried.
prs = [
    {"number": 1, "state": "MERGED", "headRefName": "done", "headRefOid": "t1"},
    {"number": 2, "state": "CLOSED", "headRefName": "wt", "headRefOid": "t2"},
    {"number": 3, "state": "OPEN", "headRefName": "open", "headRefOid": "t3"},
    {"number": 4, "state": "MERGED", "headRefName": "ahead", "headRefOid": "t4"},
    {"number": 5, "state": "MERGED", "headRefName": "fork", "headRefOid": "t5", "isCrossRepository": True},
    {"number": 6, "state": "MERGED", "headRefName": "main", "headRefOid": "t6"},
]
branches = {"done": "t1", "wt": "t2", "open": "t3", "ahead": "t4x", "fork": "t5", "main": "t6", "nopr": "t7"}
got = {i["name"]: i["target"] for i in reap.judge_branches("/r", branches, {"wt"}, "main", prs, lambda a, b: False)}
assert got == {"done": True, "open": False, "ahead": False}, got


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(os.path.realpath(tmp))
    # A scratchpad worktree of a session whose transcript went quiet, clean and pushed: removed.
    origin, repo = tmp / "origin.git", tmp / "repo"
    git("init", "--bare", "-q", str(origin), cwd=tmp)
    git("clone", "-q", str(origin), str(repo), cwd=tmp)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c", cwd=repo)
    git("push", "-q", "origin", "HEAD", cwd=repo)
    sid = "0" * 8 + "-0000-0000-0000-" + "0" * 12
    wt = tmp / "claude-501" / "-proj" / sid / "scratchpad" / "wt"
    git("worktree", "add", "-q", "--detach", str(wt), cwd=repo)
    projects = tmp / "projects"
    (projects / "-proj").mkdir(parents=True)
    transcript = projects / "-proj" / f"{sid}.jsonl"
    transcript.write_text("{}\n")
    w = reap.worktrees(str(repo))[1]
    old = time.time() + 7 * H
    assert not reap.judge_worktree(str(repo), w, 6, {}, time.time(), projects)["target"]
    assert not reap.judge_worktree(str(repo), w, 6, {9: str(wt)}, old, projects)["target"]
    assert reap.judge_worktree(str(repo), w, 6, None, old, projects)["why"] == "프로세스 cwd 를 읽지 못함"
    (wt / "dirty").write_text("x")
    assert reap.judge_worktree(str(repo), w, 6, {}, old, projects)["why"] == "변경 있음"
    (wt / "dirty").unlink()
    # Without a transcript nothing proves the session quiet, so it stays.
    transcript.rename(transcript.with_suffix(".bak"))
    assert not reap.judge_worktree(str(repo), w, 6, {}, old, projects)["target"]
    transcript.with_suffix(".bak").rename(transcript)
    # A checkout on a branch may back an open PR: reported only.
    assert reap.judge_worktree(str(repo), {**w, "branch": "refs/heads/x"}, 6, {}, old, projects)["why"].startswith("브랜치 x")
    item = reap.judge_worktree(str(repo), w, 6, {}, old, projects)
    assert item["target"] and item["tip"].startswith(w["HEAD"] + ":"), item
    reap.act(item, [])
    assert not wt.exists() and len(reap.worktrees(str(repo))) == 1
    # Without origin/HEAD the default branch is unknown, so no branch is judged.
    git("branch", "feat", cwd=repo)
    notes = []
    assert reap.branch_scan(str(repo), notes) == [] and "기본 브랜치" in notes[0], notes
    # A merged branch is deleted with its restore command; a branch that moved after the scan stays.
    tip = subprocess.run(["git", "rev-parse", "feat"], cwd=repo, capture_output=True, text=True).stdout.strip()
    pr = [{"number": 9, "state": "MERGED", "headRefName": "feat", "headRefOid": tip}]
    [item] = reap.judge_branches(str(repo), {"feat": tip}, set(), "main", pr, lambda a, b: False)
    assert reap.act({**item, "tip": "0" * 40}, []) == "tip 이 바뀜, 남김"
    assert reap.act(item, []).startswith("삭제 (복구: git -C")
    assert subprocess.run(["git", "rev-parse", "--verify", "-q", "feat"], cwd=repo).returncode != 0
    # A vanished worktree goes alone; another vanished one that is not in the plan stays.
    for name in ("gone1", "gone2"):
        git("worktree", "add", "-q", "--detach", str(tmp / name), cwd=repo)
        subprocess.run(["rm", "-rf", str(tmp / name)], check=True)
    gone = [reap.judge_worktree(str(repo), w, 6, {}, old, projects) for w in reap.worktrees(str(repo))[1:]]
    assert all(g["target"] for g in gone) and len(gone) == 2
    # A vanished checkout outside a temp dir may be Orca's; not this skill's.
    assert reap.judge_worktree(str(repo), {"path": "/Users/nobody/wt", "prunable": "gone"}, 6, {}, old, projects) is None
    # An Orca worktree is the steward's (orca worktree rm); unreadable Orca state keeps them all.
    w1 = reap.worktrees(str(repo))[1]
    assert reap.judge_worktree(str(repo), w1, 6, {}, old, projects, orca={w1["path"]}) is None
    assert not reap.judge_worktree(str(repo), w1, 6, {}, old, projects, orca=None)["target"]
    reap.act(gone[0], [])
    assert [w["path"] for w in reap.worktrees(str(repo))[1:]] == [gone[1]["name"]]
    git("worktree", "remove", gone[1]["name"], cwd=repo)
    # A non-scratchpad worktree is not this skill's.
    git("worktree", "add", "-q", "--detach", str(tmp / "other"), cwd=repo)
    assert reap.judge_worktree(str(repo), reap.worktrees(str(repo))[1], 6, {}, old, projects) is None

    # The CLI round trip: scan writes the plan, reap acts only on the plan's targets that are still targets.
    git("worktree", "add", "-q", "--detach", str(wt), cwd=repo)
    os.utime(transcript, (time.time() - 7 * H,) * 2)
    plan, script = tmp / "plan.json", str(pathlib.Path(__file__).parent / "reap.py")
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp)}
    cli = lambda *a: subprocess.run([sys.executable, script, *a], env=env, capture_output=True, text=True, check=True).stdout
    out = cli("scan", "--kinds", "worktree", "--repo", str(repo), "--plan", str(plan))
    assert "정리 대상 1개" in out and wt.exists(), out
    data = json.loads(plan.read_text())
    data["items"].append({**data["items"][0], "key": "worktree:/nowhere:/nowhere", "name": "/nowhere"})
    plan.write_text(json.dumps(data))
    out = cli("reap", "--plan", str(plan))
    assert not wt.exists() and "건너뜀 worktree [repo] /nowhere" in out, out

    # One real reap: a broker tree whose cwd is gone and an orphan under a cache path, both reparented to 1.
    cache = tmp / "uv-cache"
    cache.mkdir()
    sleeper = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"
    (cache / "litellm.py").write_text(sleeper)
    (cache / "second-worker.py").write_text(sleeper)
    py = sys.executable
    subprocess.run(["sh", "-c", f"'{py}' '{cache}/litellm.py' --port 1 &"], check=True)
    subprocess.run(["sh", "-c", f"'{py}' '{cache}/second-worker.py' serve --cwd {tmp}/gone-wt --pid-file x &"], check=True)
    time.sleep(1)
    rows = reap.processes()
    orphan = [i for i in reap.judge_orphans(rows, [str(cache)], 0) if i["target"]]
    # Both cache workers are orphans; tree identity protects against PID reuse.
    assert len(orphan) == 2 and all(i["procs"] == 2 for i in orphan), orphan
    pids = [p for i in orphan for p in reap.tree(i["pid"], rows)]
    # Same pid and command but another start time is another process.
    assert reap.kill_tree({**orphan[0], "start": "Thu Jan  1 00:00:00 1970"}, []) == "사라졌거나 다른 프로세스"
    assert all(reap.kill_tree(i, []) == "종료" for i in orphan)
    assert not set(pids) & set(reap.processes())
    assert reap.kill_tree(orphan[0], []) == "사라졌거나 다른 프로세스"
    assert len(pids) == 4

# Retired scope flags must fail before any inventory or mutation.
saved_argv = sys.argv
try:
    for scope in (["--under", "/example"], ["--under=/example"]):
        sys.argv = ["reap.py", "scan", *scope]
        try:
            reap.main()
            raise AssertionError("retired scope was silently ignored")
        except SystemExit as e:
            assert "no longer supported" in str(e)
finally:
    sys.argv = saved_argv

# Unknown, retired, empty and mixed kinds must fail before scanning even a valid subset.
for args in (["--kinds"], ["--kinds", ""], ["--kinds", "retired-provider"],
             ["--kinds", "orphan,retired-provider"], ["--kinds", "orphan,"],
             ["--kinds=retired-provider"], ["--kinds=orphan"], ["--kinds="],
             ["--kinds", "orphan", "--kinds=retired-provider"]):
    original_config = reap.config
    try:
        reap.config = lambda: (_ for _ in ()).throw(AssertionError("configuration read before validation"))
        sys.argv = ["reap.py", "scan", *args]
        try:
            reap.main()
            raise AssertionError("invalid kinds silently accepted")
        except SystemExit as e:
            assert "supported kinds" in str(e), e
    finally:
        sys.argv = saved_argv
        reap.config = original_config

# No personal folder scan without an explicit list, including malformed configuration.
for cfg in ({}, {"user_folders": "~/Downloads"}, {"user_folders": [None]}):
    items, notes = reap.scan({"user-folder"}, 6, cfg, [])
    assert items == [] and any("user_folders" in n for n in notes)

# Janitor kinds: the ledger appends, the last line per id wins, and a PR link decides.
PRS = {"acme/app#1": {"state": "OPEN", "headRefOid": "h1"}, "acme/app#2": {"state": "MERGED", "headRefOid": "h2"},
       "acme/app#3": {"state": "CLOSED", "headRefOid": "h3"}}
with tempfile.TemporaryDirectory() as tmp:
    led = pathlib.Path(tmp) / "state" / "ledger.jsonl"
    reap.ledger_add(led, "tmpdir", "/t/a", pr="acme/app#1", by="w1")
    reap.ledger_add(led, "tmpdir", "/t/a", pr="acme/app#2", by="w1")
    with led.open("a") as f:
        f.write('[]\n"x"\n{"kind": "tmpdir"}\n{torn\n\xff\n')
    entries = reap.ledger_read(led)
    assert len(entries) == 2 and set(entries[0]) == {"ts", "run", "kind", "id", "links", "by"}, entries
    assert [e["links"]["pr"] for e in reap.latest(entries, "tmpdir")] == ["acme/app#2"]
    try:
        reap.ledger_add(led, "docker", "x")
        raise AssertionError("an unknown kind is refused")
    except SystemExit:
        pass
assert reap.parse_pr("acme/app#12") == ("acme/app", 12) and reap.parse_pr("12") is None
assert reap.link_verdict({"pr": "acme/app#1"}, PRS.get) == (False, "열린 PR acme/app#1")
assert reap.link_verdict({"pr": "acme/app#3"}, PRS.get)[0] is True
assert reap.link_verdict({"pr": "acme/app#9"}, PRS.get)[0] is False, "an unreadable PR keeps it"
assert reap.link_verdict({"worktree": "/wt/x"}, PRS.get, exists=lambda p: False) == (True, "worktree /wt/x 없어짐")
assert reap.link_verdict({}, PRS.get)[0] is None

e = lambda kind, rid, **links: {"kind": kind, "id": rid, "links": links}
entries = [e("tmpdir", "/t/open", pr="acme/app#1"), e("tmpdir", "/t/merged", pr="acme/app#2"),
           e("tmpdir", "/t/busy", pr="acme/app#2"), e("tmpdir", "/t/gone", pr="acme/app#2"),
           e("tmpdir", "/t/wtgone", worktree="/wt/gone"), e("tmpdir", "/t/nolink")]
exists = lambda p: p not in ("/t/gone", "/wt/gone")
got = {i["name"]: i for i in reap.judge_tmpdirs(entries, PRS.get, {7: "/t/busy/sub"}, {}, exists, lambda p: [p + "/X.app"], ("/t",))}
assert "/t/gone" not in got, "a path already gone is not reported"
assert {k: v["target"] for k, v in got.items()} == {"/t/open": False, "/t/merged": True, "/t/busy": False,
                                                       "/t/wtgone": True, "/t/nolink": False}, got
assert got["/t/open"]["why"] == "열린 PR acme/app#1" and "pid 7" in got["/t/busy"]["why"]
# LaunchServices registrations under the path go before the directory.
assert got["/t/merged"]["how"][0].endswith("lsregister -u /t/merged/X.app") and got["/t/merged"]["how"][-1] == "trash /t/merged"
assert not any(i["target"] for i in reap.judge_tmpdirs(entries, PRS.get, None, {}, exists, lambda p: [], ("/t",)))
# An app started from a bundle inside runs with cwd /; its executable still keeps the directory.
got = reap.judge_tmpdirs(entries[1:2], PRS.get, {}, {8: row(8, "/t/merged/X.app/Contents/MacOS/X")}, exists, lambda p: [], ("/t",))
assert not got[0]["target"] and "pid 8" in got[0]["why"], got
# Outside a temp dir (a worktree registered by mistake) it is never a target.
got = reap.judge_tmpdirs(entries[1:2], PRS.get, {}, {}, exists, lambda p: [], ("/elsewhere",))
assert not got[0]["target"] and "임시 디렉터리 밖" in got[0]["why"], got
got = reap.judge_tmpdirs([e("tmpdir", "/t", pr="acme/app#2")], PRS.get, {}, {}, exists, lambda p: [], ("/t",))
assert not got[0]["target"], "a temp root itself is never a target"
got = reap.judge_tmpdirs([e("tmpdir", "/private/tmp/b x", pr="acme/app#2")], PRS.get, {},
                         {9: row(9, "/tmp/b x/My App.app/Contents/MacOS/My App")}, lambda p: True, lambda p: [], ("/private/tmp",))
assert not got[0]["target"] and "pid 9" in got[0]["why"], got
with tempfile.TemporaryDirectory() as tmp:
    (pathlib.Path(tmp) / "a" / "Foo.app" / "Contents").mkdir(parents=True)
    (pathlib.Path(tmp) / "b").mkdir()
    assert reap.apps_under(tmp) == [os.path.join(tmp, "a", "Foo.app")]

# evidence: a local copy of a PR's screenshots or recordings goes once that PR has been merged or closed for N hours.
NOW = reap.parse_ts("2026-10-08T12:00:00Z")
EPRS = {"acme/app#1": {"state": "OPEN", "closedAt": None}, "acme/app#2": {"state": "MERGED", "closedAt": "2026-10-07T12:00:00Z"},
        "acme/app#3": {"state": "CLOSED", "closedAt": "2026-10-08T10:00:00Z"}, "acme/app#4": {"state": "MERGED"}}
entries = [e("evidence", "/t/old.gif", pr="acme/app#2"), e("evidence", "/t/open.png", pr="acme/app#1"),
           e("evidence", "/t/recent.mov", pr="acme/app#3"), e("evidence", "/t/nopr.png", worktree="/wt/gone"),
           e("evidence", "/t/unread.png", pr="acme/app#9"), e("evidence", "/t/noclose.png", pr="acme/app#4"),
           e("evidence", "/t/mixed", pr="acme/app#2"), e("evidence", "/t/gone.png", pr="acme/app#2")]
files = lambda p: ([p + "/a.png"], [p + "/notes.txt"]) if p == "/t/mixed" else ([p], [])
got = {i["name"]: i for i in reap.judge_evidence(entries, EPRS.get, 6, NOW, lambda p: p != "/t/gone.png", files)}
assert "/t/gone.png" not in got
assert {k: v["target"] for k, v in got.items()} == {"/t/old.gif": True, "/t/open.png": False, "/t/recent.mov": False,
                                                       "/t/nopr.png": False, "/t/unread.png": False,
                                                       "/t/noclose.png": False, "/t/mixed": False}, got
assert got["/t/old.gif"]["how"] == ["trash /t/old.gif"] and "6시간 미만" in got["/t/recent.mov"]["why"]
assert "읽지 못함" in got["/t/unread.png"]["why"] and "읽지 못함" in got["/t/noclose.png"]["why"]
assert "PR 없음" in got["/t/nopr.png"]["why"], "a worktree link does not prove the evidence was uploaded"
with tempfile.TemporaryDirectory() as tmp:
    tmp, NOW = os.path.realpath(tmp), time.time()
    for rel in ("a.png", "shots/b.gif", "shots/c.MP4", "shots/notes.txt", "shots/deep/d.png", "fresh/e.png", "led/f.webm"):
        f = pathlib.Path(tmp, rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 10)
        if not rel.startswith("fresh/"):
            os.utime(f, (NOW - 7 * H, NOW - 7 * H))
    os.symlink(tmp + "/shots", tmp + "/link")
    media, other = reap.media_files(tmp + "/shots")
    assert sorted(map(os.path.basename, media)) == ["b.gif", "c.MP4", "d.png"] and len(other) == 1
    got = {i["name"]: i for i in reap.stray_evidence([tmp], {tmp + "/led"}, 6, NOW)}
    # Directly in a root or one level below, old enough, not registered, never through a symlink; deeper is left alone.
    assert set(got) == {tmp, tmp + "/shots"}, got
    assert "2개" in got[tmp + "/shots"]["why"] and not any(i["target"] or i["how"] for i in got.values())
    assert all(pathlib.Path(tmp, r).exists() for r in ("a.png", "shots/b.gif", "led/f.webm"))
    got = reap.janitor_scan({"evidence"}, [], [e("evidence", tmp + "/shots", pr="acme/app#1"), e("tmpdir", tmp + "/led")], [], EPRS.get,
                            hours=6, now=NOW, evidence_roots=[tmp])
    assert [(i["name"], i["target"]) for i in got] == [(tmp + "/shots", False), (tmp, False)], got
    assert got[1]["key"] == f"evidence-stray:{tmp}"

# gui-app: the registered pid with the same start time only.
rows = {40: row(40, "/Applications/Sim.app/Contents/MacOS/Sim"), 41: row(41, "/x/other")}
entries = [e("gui-app", "40@" + rows[40]["start"], pr="acme/app#2"), e("gui-app", "41@Thu Jan  1 00:00:00 1970", pr="acme/app#2"),
           e("gui-app", "42@" + rows[40]["start"], pr="acme/app#2")]
got = reap.judge_gui_apps(entries, PRS.get, rows)
assert [(i["key"], i["target"]) for i in got] == [("gui-app:40@" + rows[40]["start"], True)], got
# chrome-window: no script closes it; the plan says the agent does, and only for the recorded URL (Chrome reuses ids).
tab = lambda rid, pr, **kw: {**e("chrome-window", rid, pr=pr), **kw}
got = reap.judge_chrome([tab("tab:5", "acme/app#3", url="http://localhost:3000/x"), tab("tab:6", "acme/app#1", url="http://a"),
                         tab("tab:7", "acme/app#3")], PRS.get)
assert [i["target"] for i in got] == [True, False, False], got
assert "에이전트가 처리" in got[0]["how"][0] and "http://localhost:3000/x" in got[0]["how"][0] and "URL 기록 없음" in got[2]["why"]
# A retired line drops the id from every later scan.
assert reap.judge_chrome([tab("tab:5", "acme/app#3", url="http://a"), tab("tab:5", "acme/app#3", url="http://a", retired=True)],
                         PRS.get) == []
assert [i["target"] for i in reap.judge_remote_branches([e("remote-branch", "acme/app:feat")])] == [False]
got = reap.judge_orca_worker([{"dispatchId": "ctx_1", "taskId": "task_1"}])
assert got[0]["target"] and got[0]["how"] == ["orca orchestration worker-release --dispatch ctx_1 --json"]

# orca-worktree: a merged or closed PR, clean, nothing unpushed (unless the PR carried HEAD), no live turn.
ok = {"pr": "acme/app#2", "live": None, "dirty": False, "unpushed": False, "head": "h2"}
judge = lambda **kw: reap.judge_orca_worktree("/wt/a", "ledger", {**ok, **kw}, PRS.get)
assert judge()["target"] and judge()["how"][-1] == "orca worktree rm --worktree path:/wt/a --run-hooks --json" and "휴지통" in judge()["how"][0]
assert judge(unpushed=True)["target"], "a squash-merged PR carried HEAD"
assert judge(unpushed=True, head="h9")["why"] == "원격에 없는 커밋"
assert judge(pr="acme/app#1")["why"] == "열린 PR acme/app#1"
assert judge(dirty=True)["why"].startswith("변경 있음")
assert judge(live="살아 있는 워커 턴(ctx_1)")["why"].startswith("살아 있는")
assert judge(pr=None, landed=False)["why"] == "연결된 PR 없음" and judge(pr="acme/app#3")["target"]
# No PR: it goes only when HEAD is on the remote default branch (end-session §4) and its sessions are quiet long enough.
nopr = lambda **kw: reap.judge_orca_worktree("/wt/a", "오케스트레이션 워커", {**ok, "pr": None, "landed": True, "worker_card": True, "idle": 7 * 3600, **kw},
                                             PRS.get, 6 * 3600)
assert nopr()["target"] and nopr()["why"] == "PR 없음, HEAD 가 기본 브랜치에 있고 세션이 7h0m 조용 (오케스트레이션 워커)", nopr()
assert nopr(landed=False)["why"] == "연결된 PR 없음" and "origin/HEAD" in nopr(landed=None)["why"]
assert nopr(idle=3600)["target"] is False and "만 조용" in nopr(idle=3600)["why"], "a lead or a new card sits on the default branch too"
assert nopr(idle=None)["target"] is False
assert nopr(worker_card=False)["why"] == "연결된 PR 없음(워커가 만든 카드가 아님)"
assert nopr(pr_unread=True)["target"] is False and "읽지 못함" in nopr(pr_unread=True)["why"], "a failed gh lookup is not no PR"
assert nopr(dirty=True)["why"].startswith("변경 있음") and nopr(unreadable=True)["why"] == "git 상태를 읽지 못함"
assert nopr(live="살아 있는 워커 턴(ctx_1)")["target"] is False

# Cards an agent made with `orca worktree create --parent-worktree` count; the app's own New Worktree does not.
card = lambda path, origin, **kw: {"path": path, "isMainWorktree": False,
                                   "lineage": {"parentWorktreeId": "r::/r", "origin": origin}, **kw}
assert reap.cli_cards({"worktrees": [card("/w/cli", "cli"), card("/w/ui", "manual"), {"path": "/w/none", "lineage": None},
                                     card("/r", "cli", isMainWorktree=True),
                                     {"path": "/w/orphan", "lineage": {"origin": "cli", "parentWorktreeId": None}}]}) == {"/w/cli"}
assert reap.cli_cards({"worktrees": [card("/w/cli", "cli")], "truncated": True}) is None and reap.cli_cards(None) is None
assert reap.cli_cards({"worktrees": [card("/w/pin", "cli", isPinned=True), card("/w/far", "cli", hostId="ssh-1"),
                                     {"lineage": {"origin": "cli", "parentWorktreeId": "r::/r"}}]}) == set()
assert reap.cli_cards(["not", "a", "dict"]) is None
# `--no-parent` leaves lineage null; Orca still records the CLI as the creator.
nop = lambda path, **kw: {"path": path, "isMainWorktree": False, "lineage": None, "cliProvenance": {"kind": "created-by-cli"}, **kw}
assert reap.cli_cards({"worktrees": [nop("/w/nop"), nop("/w/pin", isPinned=True), nop("/w/far", hostId="ssh-1"),
                                     nop("/r", isMainWorktree=True), {"path": "/w/app", "lineage": None, "cliProvenance": None}]}) == {"/w/nop"}

# Only worktrees a worker made count; a retained row (context-only dispatch, user takeover) marks the user's.
ws = [{"dispatchId": "d1", "terminalState": "released", "resource": {"worktreeId": "r::/w/made"}},
      {"dispatchId": "d2", "terminalState": "active", "resource": {"worktreeId": "r::/w/live"},
       "projection": {"outcome": "in_progress"}},
      {"dispatchId": "d3", "terminalState": "retained", "resource": {"worktreeId": "r::/w/mine"}},
      {"dispatchId": "d4", "terminalState": "released", "resource": {"worktreeId": "r::/w/took", "retainedReason": "user_takeover"}},
      {"terminalState": "released", "resource": {}}]
live, made, users = reap.worker_worktrees(ws)
assert made == {"/w/made", "/w/live"} and users == {"/w/mine", "/w/took"} and list(live) == ["/w/live"], (made, users, live)

# The branch's PR is looked up without a ledger link; a fork's PR with the same head name does not count.
def fake_git(prs):
    def git(cmd, cwd=None, check=True):
        if cmd[0] == "gh":
            return json.dumps(prs)
        return {"--git-common-dir": "/r/.git\n", "--show-current": "fix\n", "HEAD": "h2\n"}.get(cmd[-1], "")
    return git
fork = {"number": 7, "url": "https://github.com/other/app/pull/7", "isCrossRepository": True}
mine = {"number": 2, "url": "https://github.com/acme/app/pull/2", "isCrossRepository": False}
assert reap.worktree_facts("/r-wt", None, None, {}, git=fake_git([fork]))["pr"] is None
assert reap.worktree_facts("/r-wt", None, None, {}, git=fake_git([fork, mine]))["pr"] == "acme/app#2"
assert reap.worktree_facts("/r", None, None, {}, git=fake_git([mine])) == {"main": True}, "the main checkout is skipped"
# Without a PR, HEAD is checked against origin/HEAD; an unknown default branch or a failed check is not landed.
def landed_git(default, ancestor):
    def git(cmd, cwd=None, check=True):
        if "symbolic-ref" in cmd:
            return default
        if "--is-ancestor" in cmd:
            return "" if ancestor and cmd[-1] == "origin/main" else None
        return fake_git([])(cmd, cwd, check)
    return git
assert reap.worktree_facts("/r-wt", None, None, {}, git=landed_git("origin/main\n", True))["landed"] is True
assert reap.worktree_facts("/r-wt", None, None, {}, git=landed_git("origin/main\n", False))["landed"] is False
assert reap.worktree_facts("/r-wt", None, None, {}, git=landed_git(None, True))["landed"] is None
def gh_down(cmd, cwd=None, check=True):
    return None if cmd[0] == "gh" else fake_git([])(cmd, cwd, check)
assert reap.worktree_facts("/r-wt", None, None, {}, git=gh_down)["pr_unread"] is True
assert reap.worktree_facts("/r-wt", None, None, {}, git=fake_git([]))["pr_unread"] is False
# A git read that fails is not a clean checkout: the worktree is kept with the reason.
def failing_git(cmd, cwd=None, check=True):
    return None if cmd[-2:] in (["status", "--porcelain"], ["--not", "--remotes"]) else fake_git([mine])(cmd, cwd, check)
facts = reap.worktree_facts("/r-wt", None, None, {}, git=failing_git)
assert reap.judge_orca_worktree("/r-wt", "ledger", facts, PRS.get) == {
    **judge(), "why": "git 상태를 읽지 못함", "target": False, "key": "orca-worktree:/r-wt", "name": "/r-wt",
    "how": ["git -C /r-wt ls-files --others --ignored --exclude-standard --directory 의 항목을 휴지통으로",
            "orca worktree rm --worktree path:/r-wt --run-hooks --json"]}, facts

# An unreadable worker list is not an empty one: a ledger worktree may be a user's takeover, so it is kept.
saved = reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which, reap.orca_json
with tempfile.TemporaryDirectory() as tmp:
    wt = os.path.realpath(tmp)
    reap.cwds, reap.shutil.which = (lambda: {}), (lambda name: "/bin/" + name)
    reap.orca_json = lambda *a: {"worktrees": []}
    reap.worktree_facts = lambda path, pr, live, cwd_of, **kw: {"main": False, "repo": "/r", **ok, "live": live}
    for workers, target, why in ((None, False, "워커 목록을 읽지 못함"), ([], True, "PR acme/app#2 merged (ledger)")):
        reap.orca_workers = lambda state=None: workers
        got = reap.janitor_scan({"orca-worktree"}, [], [e("orca-worktree", wt, pr="acme/app#2")], [], PRS.get)
        assert [(i["target"], i["why"]) for i in got] == [(target, why)], (workers, got)
    # An unregistered card made through the CLI is seen; one the user's takeover retained is not.
    reap.orca_workers = lambda state=None: []
    reap.orca_json = lambda *a: {"worktrees": [card(wt, "cli")]}
    got = reap.janitor_scan({"orca-worktree"}, [], [], [], PRS.get, quiet=lambda p: 7 * 3600)
    assert [(i["target"], i["why"]) for i in got] == [(True, "PR acme/app#2 merged (CLI 로 만든 카드)")], got
    # A `--no-parent` card is a CLI card too: its merged PR makes it a target, and a session holding it is asked to close.
    reap.orca_json = lambda *a: {"worktrees": [nop(wt)]}
    got = reap.janitor_scan({"orca-worktree"}, [], [], [], PRS.get, quiet=lambda p: 7 * 3600)
    assert [(i["target"], i["why"]) for i in got] == [(True, "PR acme/app#2 merged (CLI 로 만든 카드)")], got
    reap.cwds = lambda: {77: wt}
    reap.worktree_facts = lambda path, pr, live, cwd_of, **kw: {"main": False, "repo": "/r", **ok, "turn": live, "users": [77],
                                                                "live": live or "pid 77 이 이 worktree 를 cwd 로 사용 중"}
    got = reap.janitor_scan({"orca-worktree", "done-card"}, [], [], [], PRS.get, quiet=lambda p: 7 * 3600)
    assert [(i["kind"], i["target"]) for i in got] == [("orca-worktree", False), ("done-card", True)], got
    reap.cwds = lambda: {}
    reap.worktree_facts = lambda path, pr, live, cwd_of, **kw: {"main": False, "repo": "/r", **ok, "live": live}
    reap.orca_json = lambda *a: {"worktrees": [card(wt, "cli")]}
    # Known only by lineage, it may be a lead whose branch name an old merged PR used: it waits for quiet too.
    got = reap.janitor_scan({"orca-worktree"}, [], [], [], PRS.get, quiet=lambda p: 3600)
    assert [(i["target"], i["why"]) for i in got] == [(False, "PR acme/app#2 merged, 세션이 1h0m 만 조용")], got
    got = reap.janitor_scan({"orca-worktree"}, [], [e("orca-worktree", wt, pr="acme/app#2")], [], PRS.get, quiet=lambda p: 60)
    assert [i["target"] for i in got] == [True], "a ledger card with its PR link needs no quiet"
    got = reap.janitor_scan({"orca-worktree"}, [], [e("orca-worktree", wt)], [], PRS.get, quiet=lambda p: 60)
    assert [i["target"] for i in got] == [False], "a ledger card without a PR link (dispatch-card §3) waits like a cli card"
    reap.orca_workers = lambda state=None: [{"terminalState": "retained", "resource": {"worktreeId": "r::" + wt}}]
    assert reap.janitor_scan({"orca-worktree"}, [], [], [], PRS.get) == []
    # A no-PR card is judged by how long its sessions have been quiet, floored at NO_PR_MIN_HOURS even under --hours 0.
    reap.orca_workers = lambda state=None: [{"dispatchId": "d1", "terminalState": "released", "resource": {"worktreeId": "r::" + wt}}]
    reap.orca_json = lambda *a: {"worktrees": []}
    reap.worktree_facts = lambda path, pr, live, cwd_of, **kw: {"main": False, "repo": "/r", **ok, "pr": None, "landed": True, "live": live}
    for idle, hours, target in ((3 * 3600, 0, True), (3600, 0, False), (3 * 3600, 6, False), (7 * 3600, 6, True)):
        got = reap.janitor_scan({"orca-worktree"}, [], [], [], PRS.get, hours=hours, quiet=lambda p, i=idle: i)
        assert [i["target"] for i in got] == [target], (idle, hours, got)
    # A no-PR card a worker did not make (a lead, a card opened by hand) is never a target, however quiet.
    reap.orca_workers = lambda state=None: []
    reap.orca_json = lambda *a: {"worktrees": [card(wt, "cli")]}
    got = reap.janitor_scan({"orca-worktree"}, [], [e("orca-worktree", wt)], [], PRS.get, hours=6, quiet=lambda p: 99 * 3600)
    assert [(i["target"], i["why"]) for i in got] == [(False, "연결된 PR 없음(워커가 만든 카드가 아님)")], got
    reap.orca_json = lambda *a: None
    notes = []
    assert reap.janitor_scan({"orca-worktree"}, [], [], notes, PRS.get) == [] and "orca worktree list" in notes[0], notes
    # A worktree an automation runs in (`--workspace`) is never a candidate, whatever its lineage or PR.
    listing = {"worktree": {"worktrees": [card(wt, "cli")]}, "automations": {"automations": [{"workspaceId": "r::" + wt}]}}
    reap.orca_json = lambda *a: listing[a[0]]
    assert reap.janitor_scan({"orca-worktree"}, [], [e("orca-worktree", wt, pr="acme/app#2")], [], PRS.get) == []
    reap.orca_json = lambda *a: listing[a[0]] if a[0] == "worktree" else None
    notes = []
    # Unreadable, the list keeps every card that round: an automation's worktree would look like a finished one.
    got = reap.janitor_scan({"orca-worktree", "done-card"}, [], [], notes, PRS.get, quiet=lambda p: 7 * 3600)
    assert [(i["kind"], i["target"], i["why"]) for i in got] == [("orca-worktree", False, "automation 목록을 읽지 못함")] \
        and "orca automations list" in notes[0], (got, notes)
assert reap.automation_workspaces({"automations": [{"workspaceId": "r::/w/run"}, {"workspaceId": None}, {}]}) == {"/w/run"}
assert reap.automation_workspaces({"worktrees": []}) == set() and reap.automation_workspaces(None) is None
reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which, reap.orca_json = saved

# done-card: a settled card that only its idle Claude session keeps is asked once to close itself.
H = 3600
done = lambda quiet=3 * H, asked=None, **kw: reap.judge_done_card(
    "/wt/a", "ledger", {**ok, "turn": None, "users": [77], **kw}, PRS.get, lambda p: quiet, 2 * H, asked)
assert done()["target"] and done()["kind"] == "done-card" and "end-session §8" in done()["line"], done()
assert "PR acme/app#2 merged" in done()["line"] and "\n" not in done()["line"]
assert done(quiet=H)["target"] is False and "만 조용" in done(quiet=H)["why"]
assert done(quiet=None)["target"] is False
assert done(asked="2026-10-10T12:00")["target"] is False and "이미 요청함" in done(asked="2026-10-10T12:00")["why"]
for kw in ({"pr": "acme/app#1"}, {"dirty": True}, {"unpushed": True, "head": "h9"}, {"pr": None}, {"turn": "살아 있는 워커 턴(d1)"},
           {"users": []}, {"unreadable": True}, {"pr": None, "landed": True, "idle": 9 * H}):
    assert done(**kw) is None, kw

saved = reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which, reap.orca_json
with tempfile.TemporaryDirectory() as tmp:
    wt = os.path.realpath(tmp)
    reap.cwds, reap.shutil.which = (lambda: {77: wt}), (lambda name: "/bin/" + name)
    reap.orca_json = lambda *a: {"worktrees": []}
    reap.worktree_facts = lambda path, pr, live, cwd_of, **kw: {"main": False, "repo": "/r", **ok, "turn": live, "users": [77],
                                                          "live": live or "pid 77 이 이 worktree 를 cwd 로 사용 중"}
    entries = [e("orca-worktree", wt, pr="acme/app#2")]
    scan_dc = lambda workers, entries=entries: (setattr(reap, "orca_workers", lambda state=None: workers) or
                                                reap.janitor_scan({"orca-worktree", "done-card"}, [], entries, [], PRS.get,
                                                                  quiet=lambda p: 5 * H))
    got = scan_dc([])
    assert [(i["kind"], i["target"]) for i in got] == [("orca-worktree", False), ("done-card", True)], got
    assert [i["kind"] for i in scan_dc(None)] == ["orca-worktree"], "an unreadable worker list asks nobody"
    asked = scan_dc([], entries + [{"ts": "t", "kind": "done-card", "id": wt}])
    assert [(i["kind"], i["target"]) for i in asked] == [("orca-worktree", False), ("done-card", False)], asked
    assert reap.precheck(got, pathlib.Path(tmp) / "state")[0] == 0
reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which, reap.orca_json = saved

# The nudge goes to the card's one Claude terminal; a refusal fails the item so precheck holds it back a day.
saved = reap.orca_json, reap.subprocess.run, reap.ledger_path
with tempfile.TemporaryDirectory() as tmp:
    reap.ledger_path = lambda: pathlib.Path(tmp) / "ledger.jsonl"
    sent = []
    reap.orca_json = lambda *a: {"terminals": [{"handle": "term_sh", "agentIdentity": None},
                                               {"handle": "term_c", "agentIdentity": "claude"}]}
    for code, out in ((0, "sent"), (1, "not idle")):
        reap.subprocess.run = lambda argv, **kw: (sent.append(argv) or
                                                  subprocess.CompletedProcess(argv, code, stdout=out, stderr=""))
        item = done()
        if code:
            try:
                reap.act({**item, "name": "/wt/b"}, [])
                raise AssertionError("a refused nudge must fail")
            except RuntimeError as err:
                assert "not idle" in str(err)
        else:
            assert "term_c" in reap.act(item, [])
    assert sent[0][1:3] == ["nudge", "term_c"] and sent[0][3] == done()["line"], sent
    assert [x["id"] for x in reap.ledger_read(reap.ledger_path())] == ["/wt/a"], "only a sent nudge is recorded"
    reap.orca_json = lambda *a: {"terminals": []}
    try:
        reap.act(done(), [])
        raise AssertionError("no claude terminal must fail")
    except RuntimeError:
        pass
reap.orca_json, reap.subprocess.run, reap.ledger_path = saved

# precheck: 0 while targets wait; a run that never reaped leaves nothing recorded, so the next precheck runs again.
# Only what reap failed on is held back, and only for RETRY_AFTER.
with tempfile.TemporaryDirectory() as tmp:
    st = pathlib.Path(tmp)
    items = [{"key": "tmpdir:/a", "target": True}, {"key": "tmpdir:/b", "target": False}]
    assert reap.precheck(items, st) == (0, ["tmpdir:/a"]) and reap.precheck(items, st)[0] == 0
    reap.record_reap(st, ["tmpdir:/a"], now=1000)
    assert reap.precheck(items, st, now=1000 + 3600)[0] == 1
    assert reap.precheck(items, st, now=1000 + reap.RETRY_AFTER)[0] == 0
    assert reap.precheck(items + [{"key": "tmpdir:/c", "target": True}], st, now=1000)[0] == 0
    reap.record_reap(st, [], now=1000)
    assert reap.precheck(items, st, now=1000)[0] == 0
    assert reap.precheck([], st)[0] == 1

# A plan that keeps not running (a refused reap) stops waking the model after `repeat` rounds, retries once a day,
# and is announced once per set; a reap or a new set starts the count over.
with tempfile.TemporaryDirectory() as tmp:
    st, sent = pathlib.Path(tmp), []
    items = [{"key": "tmpdir:/a", "target": True}]
    ok = lambda n: sent.append(n) or True
    assert [reap.precheck(items, st, now=1000 + k, notify=ok)[0] for k in range(4)] == [0, 0, 1, 1]
    assert sent == [2], sent
    assert reap.precheck(items, st, now=1001 + reap.RETRY_AFTER, notify=ok)[0] == 0
    assert reap.precheck(items, st, now=1002 + reap.RETRY_AFTER, notify=ok)[0] == 1 and sent == [2], sent
    # A new set counts from zero and is announced again.
    other = items + [{"key": "tmpdir:/b", "target": True}]
    assert [reap.precheck(other, st, now=2000 + k, notify=ok)[0] for k in range(3)] == [0, 0, 1] and sent == [2, 2], sent
    # Any reap starts over, even one that failed on a different set.
    reap.record_reap(st, [], now=3000)
    assert reap.precheck(other, st, now=3001, notify=ok)[0] == 0
    # A send that failed is tried on the next round.
    with tempfile.TemporaryDirectory() as tmp2:
        st2, tries = pathlib.Path(tmp2), []
        fail = lambda n: tries.append(n) and False
        assert [reap.precheck(items, st2, now=k, repeat=1, notify=fail)[0] for k in (1, 2, 3)] == [0, 1, 1] and tries == [1, 1]
# notify_unrun sends one escalation to the configured address; without one it prints only.
calls = []
send = lambda argv, check: calls.append(argv) or "{}"
assert reap.notify_unrun("run:r1", "python3 reap.py reap --plan /s/p.json", send)(2) is True
assert calls[0][:5] == ["orca", "orchestration", "send", "--to", "run:r1"] and "reap --plan /s/p.json" in calls[0][-2], calls
assert reap.notify_unrun(None, "x", send)(2) is False and len(calls) == 1
assert reap.notify_unrun("run:r1", "x", lambda argv, check: None)(2) is False

# Finder's trash auto-empty keeps every user-folder target unless a plain trash folder is set.
saved = reap.user_folder_scan, reap.trash_auto_empty, os.environ.pop("AGENT_SKILLS_TRASH", None)
reap.user_folder_scan = lambda *a, **k: [{"kind": "user-folder", "key": "user-folder:/d/x", "name": "/d/x", "target": True, "why": "old"}]
try:
    for auto, folder, target in ((True, None, False), (True, "/t", True), (False, None, True)):
        reap.trash_auto_empty = lambda: auto
        if folder:
            os.environ["AGENT_SKILLS_TRASH"] = folder
        got = reap.scan({"user-folder"}, 6, {"user_folder_days": 7, "user_folders": ["/d"]}, [])[0]
        os.environ.pop("AGENT_SKILLS_TRASH", None)
        assert got[0]["target"] is target and (target or "휴지통 자동 비움" in got[0]["why"]), (auto, folder, got)
finally:
    reap.user_folder_scan, reap.trash_auto_empty = saved[:2]
    if saved[2] is not None:
        os.environ["AGENT_SKILLS_TRASH"] = saved[2]

# trash: only /usr/bin/trash, never another `trash` on PATH (one reads -s as "empty the trash"), and a hung call fails.
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(tmp)
    (t / "bin").mkdir()
    (t / "bin" / "trash").write_text(f"#!/bin/sh\ntouch {t}/WRONG\n")
    (t / "sys-trash").write_text(f"#!/bin/sh\necho \"$@\" > {t}/args\n")
    (t / "slow-trash").write_text("#!/bin/sh\nsleep 5\n")
    for f in ("bin/trash", "sys-trash", "slow-trash"):
        os.chmod(t / f, 0o755)
    saved = (reap.SYSTEM_TRASH, reap.TRASH_TIMEOUT, os.environ.get("PATH"), os.environ.pop("AGENT_SKILLS_TRASH", None))
    os.environ["PATH"] = f"{t}/bin:{saved[2]}"
    try:
        reap.SYSTEM_TRASH = str(t / "sys-trash")
        assert reap.trash("/x/a", "/x/b") == "휴지통으로 옮김"
        assert (t / "args").read_text().split() == ["-s", "/x/a", "/x/b"] and not (t / "WRONG").exists()
        reap.SYSTEM_TRASH, reap.TRASH_TIMEOUT = str(t / "slow-trash"), 1
        try:
            reap.trash("/x/a")
            raise AssertionError("a hung trash must fail")
        except RuntimeError as err:
            assert "1초" in str(err), err
    finally:
        reap.SYSTEM_TRASH, reap.TRASH_TIMEOUT = saved[0], saved[1]
        os.environ["PATH"] = saved[2]
        if saved[3] is not None:
            os.environ["AGENT_SKILLS_TRASH"] = saved[3]

# orca-worktree: ignored files (.env.local, caches) go to the trash before `orca worktree rm`, which would delete them.
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(os.path.realpath(tmp))
    repo, wt = t / "repo", t / "wt"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / ".gitignore").write_text(".env.local\ncache/\n")
    git("add", ".", cwd=repo)
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "i", cwd=repo)
    git("worktree", "add", "-q", "-b", "feat", str(wt), cwd=repo)
    (wt / ".env.local").write_text("TOKEN=x")
    (wt / "cache").mkdir()
    (wt / "cache" / "big.bin").write_text("x")
    calls, real_run = [], reap.run
    reap.run = lambda cmd, cwd=None, check=True: calls.append(cmd) or "" if cmd[0] == "orca" else real_run(cmd, cwd, check)
    os.environ["AGENT_SKILLS_TRASH"] = str(t / "trash")
    try:
        item = reap.judge_orca_worktree(str(wt), "ledger", {"pr": "acme/app#2", "head": "x"}, PRS.get)
        assert item["target"], item
        out = reap.act(item, [])
    finally:
        reap.run = real_run
        del os.environ["AGENT_SKILLS_TRASH"]
    assert sorted(os.listdir(t / "trash")) == [".env.local", "cache"] and (t / "trash" / "cache" / "big.bin").exists(), out
    assert calls == [["orca", "worktree", "rm", "--worktree", f"path:{wt}", "--run-hooks", "--json"]], calls
    assert "ignored 항목 2개" in out, out
argv = reap.schedule_cmd("/abs/reap.py", pathlib.Path("/s"), "path:/repo", ledger=pathlib.Path("/l/ledger.jsonl"))
assert argv[:3] == ["orca", "automations", "create"] and "--write" not in argv
assert argv[argv.index("--workspace-mode") + 1] == "existing" and argv[argv.index("--trigger") + 1] == "7,37 * * * *"
# The automation does not inherit the shell that ran schedule, so a state or ledger off the default goes in as flags,
# never an `env` or `VAR=` prefix, which a scheduled session may refuse as a command of its own.
assert argv[argv.index("--precheck") + 1] == "python3 /abs/reap.py precheck --state /s --ledger /l/ledger.jsonl"
joined = " ".join(argv)
assert "env " not in joined and not re.search(r"(^|[\s`])[A-Z_]{2,}=", joined), joined
assert "`python3 /abs/reap.py reap --plan /s/janitor-plan.json --state /s --ledger /l/ledger.jsonl`" in joined, joined
# Orca's 60-second default cut off every precheck once the cards passed 40.
assert argv[argv.index("--precheck-timeout") + 1] == "300"
default = reap.schedule_cmd("/abs/reap.py", reap.STATE_HOME, "path:/repo", ledger=reap.STATE_HOME / "ledger.jsonl")
assert default[default.index("--precheck") + 1] == "python3 /abs/reap.py precheck", default

# CLI: ledger add, precheck twice, reap moves a settled tmpdir to the trash and skips what no longer holds, schedule only prints.
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(os.path.realpath(tmp))
    d = tmp / "build"
    d.mkdir()
    env = {**os.environ, "AGENT_SKILLS_STATE": str(tmp / "state"), "AGENT_SKILLS_TRASH": str(tmp / "trash")}
    script = str(pathlib.Path(__file__).parent / "reap.py")
    cli = lambda *a: subprocess.run([sys.executable, script, *a], env=env, capture_output=True, text=True)
    assert cli("ledger", "add", "--kind", "tmpdir", "--path", str(d), "--worktree", str(tmp / "gone-wt")).returncode == 0
    assert str(d) in cli("ledger", "list").stdout
    first, second = cli("precheck", "--kinds", "tmpdir"), cli("precheck", "--kinds", "tmpdir")
    # Nothing reaped yet, so the target is still due.
    assert (first.returncode, second.returncode) == (0, 0), (first.stdout, second.stdout)
    out = cli("reap", "--plan", str(tmp / "state" / "janitor-plan.json")).stdout
    assert "tmpdir" in out and "휴지통으로 옮김" in out and not d.exists() and (tmp / "trash" / "build").is_dir(), out
    assert cli("precheck", "--kinds", "tmpdir").returncode == 1
    # A manual kind written into the janitor plan is refused there: the round's rule covers only the scheduled kinds.
    jplan = tmp / "state" / "janitor-plan.json"
    plan = json.loads(jplan.read_text())
    plan["items"] = [{"kind": "branch", "key": "branch:/r:feature", "repo": "/r", "name": "feature", "tip": "x", "target": True}]
    jplan.write_text(json.dumps(plan))
    out = cli("reap", "--plan", str(jplan)).stdout
    assert "건너뜀 branch:/r:feature: janitor 계획에 들어갈 수 없는 종류" in out, out
    shot = tmp / "shot.png"
    shot.write_bytes(b"x")
    assert cli("ledger", "add", "--kind", "evidence", "--path", str(shot), "--pr", "acme/app#1").returncode == 0
    plan = json.loads((tmp / "state" / "janitor-plan.json").read_text())
    plan["items"] = [{"kind": "evidence", "key": f"evidence:{shot}", "name": str(shot), "target": True, "how": []}]
    (tmp / "plan.json").write_text(json.dumps(plan))
    out = cli("reap", "--plan", str(tmp / "plan.json")).stdout
    # gh cannot read acme/app#1, so the re-scan no longer finds a target.
    assert "건너뜀 evidence" in out and shot.exists(), out
    out = cli("schedule", "--workspace", "path:/repo")
    assert out.returncode == 0 and out.stdout.startswith("orca automations create") and "--write" in out.stdout, out
    assert "automations-runner" not in out.stdout, "a chosen workspace needs no runner hint"
    out = cli("schedule")
    assert "--no-parent" in out.stdout and "settings.local.json" in out.stdout, out

# user-folder: top-level entries untouched for `days`, not open, not a download in progress.
D = 86400
with tempfile.TemporaryDirectory() as tmp:
    f = pathlib.Path(os.path.realpath(tmp))
    now = time.time() + 8 * D  # every file made here counts as 8 days old: ctime cannot be set back
    for n in ("old.pdf", "recent.png", "dl.crdownload", "a.part", "Safari.download", "open.txt", ".DS_Store"):
        (f / n).write_text("x")
    os.utime(f / "recent.png", (now - D, now - D))
    (f / "olddir").mkdir()
    (f / "olddir" / "fresh.txt").write_text("x")
    os.utime(f / "olddir" / "fresh.txt", (now - D, now - D))  # a recent file deep inside keeps the folder
    (f / "held").mkdir()
    (f / "held" / "in.txt").write_text("x")
    got = {os.path.basename(i["name"]): i for i in reap.judge_user_folder(str(f), 7, now, {str(f / "open.txt"), str(f / "held" / "in.txt")})}
    assert ".DS_Store" not in got, got
    assert {n for n, i in got.items() if i["target"]} == {"old.pdf"}, got
    assert got["old.pdf"]["how"] == [f"trash {f / 'old.pdf'}"] and got["old.pdf"]["tip"]
    assert got["recent.png"]["why"] == "7일 안에 바뀜" and got["olddir"]["why"] == "7일 안에 바뀜"
    assert all(got[n]["why"] == "다운로드 중" for n in ("dl.crdownload", "a.part", "Safari.download"))
    assert got["open.txt"]["why"] == "열려 있음" and got["held"]["why"] == "열려 있음"
    # Without the open-file list nothing old goes.
    assert not any(i["target"] for i in reap.judge_user_folder(str(f), 7, now, None))
    # The boundary: exactly 7 days goes, a second less stays.
    for t, target in ((now - 7 * D, True), (now - 7 * D + 1, False)):
        got = {os.path.basename(i["name"]): i["target"] for i in reap.judge_user_folder(str(f), 7, now, set(), touched=lambda p, n: t)}
        assert got["old.pdf"] is target, (t, got)
    # A directory it cannot read all of, or one too big to walk, stays: an unseen file may be a recent one.
    (f / "locked").mkdir()
    (f / "locked" / "inner").mkdir()
    os.chmod(f / "locked" / "inner", 0)
    try:
        got = {os.path.basename(i["name"]): i for i in reap.judge_user_folder(str(f), 7, now, set())}
    finally:
        os.chmod(f / "locked" / "inner", 0o755)
    assert not got["locked"]["target"] and got["locked"]["why"] == "안을 다 읽지 못함(남김)", got["locked"]
    try:
        reap.last_touch(str(f / "olddir"), now + D, cap=0)  # one entry inside, cap 0
        raise AssertionError("past the cap must raise")
    except reap.TooBig:
        pass
    # The day count: a number above 0; anything else turns the kind off with a note.
    for v, want in ((None, None), ("x", None), (0, None), (-1, None), (float("nan"), None), (3, 3.0)):
        notes = []
        cfg = {} if v is None else {"user_folder_days": v}
        assert reap.user_folder_days(cfg, notes) == (7.0 if v is None else want), (v, notes)
    # Screenshots and screen recordings at the top level use their own count (1 day); everything else keeps 7.
    sh = f / "shots"
    sh.mkdir()
    import unicodedata
    names = (unicodedata.normalize("NFD", "스크린샷 2026-10-05 01.41.55.png"), "Screenshot 2026-10-05 at 1.png", "화면 기록 2026-10-05 01.mov",
             "Screen Recording 2026-10-05.mov", "스크린샷 메모.txt", "screenshot-tool.png", "report.png")
    for n in names:
        (sh / n).write_text("x")
    (sh / "스크린샷 폴더.png").mkdir()  # a directory is never a screenshot
    for t_, shots in ((now - D, True), (now - D + 1, False)):
        got = {i["name"].rsplit("/", 1)[1]: i for i in
               reap.judge_user_folder(str(sh), 7, now, set(), touched=lambda p, n: t_, shot_days=1)}
        assert {n for n, i in got.items() if i["target"]} == (set(names[:4]) if shots else set()), (t_, got)
        assert got["report.png"]["why"] == got["스크린샷 메모.txt"]["why"] == got["스크린샷 폴더.png"]["why"] == "7일 안에 바뀜"
    # Without shot_days a screenshot waits the general count.
    got = {i["name"].rsplit("/", 1)[1]: i["target"] for i in
           reap.judge_user_folder(str(sh), 7, now, set(), touched=lambda p, n: now - 2 * D)}
    assert not any(got.values()), got
    for v, want in ((None, 1.0), ("x", None), (0, None), (float("nan"), None), (0.5, 0.5)):
        notes = []
        cfg = {} if v is None else {"user_folder_screenshot_days": v}
        assert reap.user_folder_days(cfg, notes, "user_folder_screenshot_days", 1, "스크린샷도 7일 기준") == want, (v, notes)
        assert (want is None) == bool(notes) and all("스크린샷도 7일 기준" in n for n in notes), notes
    # Once a day: a sweep 20 hours ago is not due, one 24 hours ago is.
    st = f / "state"
    st.mkdir()
    assert reap.user_folder_due(st)
    (st / "user-folder-last.json").write_text(json.dumps({"at": now - 20 * H}))
    assert not reap.user_folder_due(st, now) and reap.user_folder_due(st, now + 4 * H)

# CLI with a temp HOME: precheck plans the old entries of ~/Downloads and ~/Desktop, reap moves them to the trash,
# keeps a download in progress and an open file, and the next precheck skips user-folder for the day.
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(os.path.realpath(tmp))
    (home / ".claude").mkdir()
    (home / ".claude" / "agent-skills.json").write_text(json.dumps({"reap": {"user_folder_days": 1e-6, "user_folders": [str(home / "Downloads"), str(home / "Desktop")]}}))
    for sub_dir in ("Downloads", "Desktop"):
        (home / sub_dir).mkdir()
    (home / "Downloads" / "a.pdf").write_text("x")
    (home / "Downloads" / "b.crdownload").write_text("x")
    (home / "Desktop" / "shot.png").write_text("x")
    (home / "Desktop" / "busy.txt").write_text("x")
    (home / "Documents").mkdir()
    (home / "Documents" / "keep.txt").write_text("x")
    holder = subprocess.Popen([sys.executable, "-c", "import sys,time; f=open(sys.argv[1]); time.sleep(60)", str(home / "Desktop" / "busy.txt")])
    try:
        time.sleep(0.5)
        env = {**os.environ, "HOME": str(home), "AGENT_SKILLS_STATE": str(home / "state"), "AGENT_SKILLS_TRASH": str(home / "trash")}
        script = str(pathlib.Path(__file__).parent / "reap.py")
        cli = lambda *a: subprocess.run([sys.executable, script, *a], env=env, capture_output=True, text=True)
        first = cli("precheck", "--kinds", "user-folder")
        assert first.returncode == 0 and "정리 대상 2개" in first.stdout, (first.stdout, first.stderr)
        out = cli("reap", "--plan", str(home / "state" / "janitor-plan.json")).stdout
        assert out.count("휴지통으로 옮김") == 2, out
        assert sorted(os.listdir(home / "trash")) == ["a.pdf", "shot.png"], os.listdir(home / "trash")
        assert (home / "Downloads" / "b.crdownload").exists() and (home / "Desktop" / "busy.txt").exists()
        assert (home / "Documents" / "keep.txt").exists()
        again = cli("precheck", "--kinds", "user-folder")
        assert again.returncode == 1 and "하루 한 번" in again.stderr, (again.stdout, again.stderr)
        # A check that finds nothing is that day's sweep too.
        (home / "state" / "user-folder-last.json").unlink()
        holder.kill()
        (home / "Desktop" / "busy.txt").unlink()
        (home / "Downloads" / "b.crdownload").unlink()
        empty = cli("precheck", "--kinds", "user-folder")
        assert empty.returncode == 1 and (home / "state" / "user-folder-last.json").exists(), (empty.stdout, empty.stderr)
    finally:
        holder.kill()

# Closed programs: the same rule as orchestrate's, and only these move to <home>/_archive.
sys.path.insert(0, str(pathlib.Path(__file__).parents[2] / "orchestrate" / "scripts"))
import prog
for evs in ([], [{"ev": "predicate_verified"}], [{"ev": "predicate_verified"}, {"ev": "landed"}],
            [{"ev": "predicate_verified"}, {"ev": "spawned"}], [{"ev": "predicate_verified"}, {"ev": "admitted", "ticket": "ABC-1"}],
            [{"ev": "predicate_verified"}, {"ev": "admitted", "pr": 3}], [{"ev": "predicate_verified"}, {"ev": "config", "note": "predicate=x"}],
            [{"ev": "landed"}, {"ev": "predicate_verified"}]):
    assert reap.final_check_current(evs) == prog.final_check_current(evs), evs

D = 86400
with tempfile.TemporaryDirectory() as tmp:
    home, now = pathlib.Path(tmp), time.time()

    def program(slug, evs, *extra):
        d = home / slug
        (d / "dashboard").mkdir(parents=True)
        (d / "program.json").write_text("{}")
        (d / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs) + "torn{\n")
        for n in extra:
            (d / n).write_text("x")
        old = now - 8 * D
        for f in d.iterdir():
            os.utime(f, (old, old))
        return d
    closed = [{"ev": "landed"}, {"ev": "predicate_verified"}]
    program("done", closed)
    program("open", [{"ev": "landed"}])
    program("parked", closed, "resume-state.md")
    program("handed", closed, "msg-HANDOFF-1.md")
    lead = program("lead", closed)
    (lead / "briefs").mkdir()
    (lead / "briefs" / "lead-resume.md").write_text("x")
    os.utime(lead / "briefs", (now - 8 * D, now - 8 * D))
    fresh = program("fresh", closed)
    os.utime(fresh / "ledger.jsonl")
    # The dashboard and `orch status` keep rewriting these on every program; they say nothing about the program.
    busy = program("busy", closed, "stuck.json")
    (busy / "dashboard" / "state.json").write_text("{}")
    os.utime(busy / "stuck.json")
    (home / "_archive").mkdir()
    (home / "_archive" / "program.json").write_text("{}")
    (home / "notes.md").write_text("x")
    (home / "noprog").mkdir()
    # last_touch also reads ctime, which utime cannot set back; mtime stands in for it here.
    got = {pathlib.Path(i["name"]).name: i for i in reap.judge_programs(home, 7, now, touched=lambda p, n: os.stat(p).st_mtime)}
    assert set(got) == {"done", "open", "parked", "handed", "lead", "fresh", "busy"}, got
    assert {n for n, i in got.items() if i["target"]} == {"done", "busy"}, got
    assert "닫히지 않음" in got["open"]["why"] and "재개 노트" in got["parked"]["why"] and "재개 노트" in got["handed"]["why"]
    assert "lead-resume.md" in got["lead"]["why"], got["lead"]
    assert "7일 안" in got["fresh"]["why"], got["fresh"]
    (home / "_archive" / "done").mkdir()
    out = reap.archive_program(got["done"]["name"])
    assert not (home / "done").exists() and "보관함" in out, out
    assert [p.name for p in (home / "_archive").iterdir() if p.name.startswith("done-")], os.listdir(home / "_archive")

# Hook state: reorient files past N days as one item, sync.log past the size; both re-checked and acted on by reap.
with tempfile.TemporaryDirectory() as tmp:
    st, now = pathlib.Path(tmp) / "state", time.time()
    (st / "reorient").mkdir(parents=True)
    for i, a in enumerate((8, 8, 6)):
        f = st / "reorient" / f"s{i}.json"
        f.write_text("{}")
        os.utime(f, (now - a * D, now - a * D))
    (st / "sync.log").write_text("x" * 600)
    got = {i["key"]: i for i in reap.judge_state_files(st, 7, 1, now)}
    assert set(got) == {"state-file:reorient"} and len(got["state-file:reorient"]["paths"]) == 2, got
    (st / "sync.log").write_text("x" * 2048)
    (st / "sync.log.1").write_text("old")
    trash_dir = pathlib.Path(tmp) / "trash"
    os.environ["AGENT_SKILLS_TRASH"] = str(trash_dir)
    try:
        for i in reap.judge_state_files(st, 7, 1, now):
            reap.reap_state_file(i)
    finally:
        del os.environ["AGENT_SKILLS_TRASH"]
    assert sorted(p.name for p in (st / "reorient").iterdir()) == ["s2.json"]
    assert not (st / "sync.log").exists() and (st / "sync.log.1").stat().st_size == 2048
    bundled = [p for p in trash_dir.iterdir() if p.name.startswith("reorient-")]
    assert len(bundled) == 1 and sorted(os.listdir(bundled[0])) == ["s0.json", "s1.json"], os.listdir(trash_dir)
    assert (trash_dir / "sync.log.1").read_text() == "old"

# CLI: precheck only plans (programs and state files stay as they were), reap acts, and both kinds wait a day after.
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(os.path.realpath(tmp))
    (home / ".claude").mkdir()
    (home / ".claude" / "agent-skills.json").write_text(json.dumps({"reap": {"log_max_kb": 1, "program_days": 1e-6}}))
    pd = home / "programs" / "done"
    pd.mkdir(parents=True)
    (pd / "program.json").write_text("{}")
    (pd / "ledger.jsonl").write_text(json.dumps({"ev": "predicate_verified"}) + "\n")
    old = time.time() - 8 * D
    st = home / "state"
    (st / "reorient").mkdir(parents=True)
    (st / "reorient" / "s.json").write_text("{}")
    os.utime(st / "reorient" / "s.json", (old, old))
    (st / "sync.log").write_text("x" * 2048)
    env = {**os.environ, "HOME": str(home), "AGENT_SKILLS_STATE": str(st), "AGENT_SKILLS_TRASH": str(home / "trash"),
           "PROGRAMS_HOME": str(home / "programs")}
    cli = lambda *a: subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "reap.py"), *a], env=env,
                                    capture_output=True, text=True)
    time.sleep(0.2)  # past program_days
    first = cli("precheck", "--kinds", "program,state-file")
    assert first.returncode == 0 and "정리 대상 3개" in first.stdout, (first.stdout, first.stderr)
    assert pd.is_dir() and (st / "reorient" / "s.json").exists() and (st / "sync.log").stat().st_size == 2048
    out = cli("reap", "--plan", str(st / "janitor-plan.json"))
    assert "보관함" in out.stdout and "회전" in out.stdout and out.stdout.count("휴지통") == 1, (out.stdout, out.stderr)
    assert (home / "programs" / "_archive" / "done" / "program.json").exists() and not pd.exists()
    assert not (st / "reorient" / "s.json").exists() and (st / "sync.log.1").exists()
    again = cli("precheck", "--kinds", "program,state-file")
    assert again.returncode == 1 and again.stderr.count("하루 한 번") == 2, (again.stdout, again.stderr)

# Orphans whose directory was removed: lsof still prints the old path, so its absence or another inode decides.
with tempfile.TemporaryDirectory() as tmp:
    live = os.path.realpath(tmp)
    ino = os.stat(live).st_ino
    rows = {1: row(1, "/usr/bin/node server.js"), 2: row(2, "/usr/bin/node a.js"), 3: row(3, "/usr/bin/node b.js"),
            4: row(4, "/usr/bin/node c.js", age=H), 5: row(5, "/usr/bin/node d.js", ppid=77), 6: row(6, "/usr/bin/node e.js")}
    cwd_of = {1: ("/nowhere/gone-card", 9), 2: (live, ino), 3: (live, ino + 1), 4: ("/nowhere/gone-card", 9),
              5: ("/nowhere/gone-card", 9), 6: (live, None), 7: ("/Users/a/\\xed\\x95\\x9c dir", 9)}
    rows[7] = row(7, "/usr/bin/node f.js")  # lsof's escape of a non-ASCII name is not a removed directory
    got = {i["pid"]: (i["target"], i["why"]) for i in reap.judge_orphans(rows, [], 6, cwd_of)}
    assert got == {1: (True, "ppid 1, cwd /nowhere/gone-card 가 사라짐"), 3: (True, f"ppid 1, cwd {live} 가 사라짐"),
                   4: (False, "6시간 미만")}, got

# A real orphan left in a removed directory is found from lsof and reaped by pid and start time.
with tempfile.TemporaryDirectory() as tmp:
    gone = pathlib.Path(os.path.realpath(tmp)) / "card"
    gone.mkdir()
    subprocess.run(["sh", "-c", f"cd '{gone}' && '{sys.executable}' -c 'import time; time.sleep(60)' &"], check=True)
    time.sleep(1)
    rows = reap.processes()
    mine = [r for r in rows.values() if r["ppid"] == 1 and "time.sleep(60)" in r["cmd"]
            and (reap.cwds() or {}).get(r["pid"]) == str(gone)]
    assert len(mine) == 1, mine
    gone.rmdir()
    got = [i for i in reap.judge_orphans(rows, [], 0, reap.cwds(inodes=True)) if i["pid"] == mine[0]["pid"]]
    assert len(got) == 1 and got[0]["target"] and "사라짐" in got[0]["why"], got
    assert reap.kill_tree(got[0], []) == "종료" and mine[0]["pid"] not in reap.processes()

# Regenerated copies: an untracked .agents/skills/<name>/ of a tracked .claude/skills/<name> is not a change.
with tempfile.TemporaryDirectory() as tmp:
    t = pathlib.Path(os.path.realpath(tmp))
    repo, wt = t / "repo", t / "wt"
    (repo / ".claude" / "skills" / "verify").mkdir(parents=True)
    (repo / ".claude" / "skills" / "verify" / "SKILL.md").write_text("x")
    git("init", "-q", "-b", "main", cwd=repo)
    git("add", ".", cwd=repo)
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "i", cwd=repo)
    git("worktree", "add", "-q", "-b", "feat", str(wt), cwd=repo)
    copy = wt / ".agents" / "skills" / "verify"
    copy.mkdir(parents=True)
    (copy / "SKILL.md").write_text("x")
    gh = lambda cmd, cwd=None, check=True: "[]" if cmd[0] == "gh" else reap.run(cmd, cwd, check)
    facts = reap.worktree_facts(str(wt), "acme/app#2", None, {}, git=gh)
    assert not facts["dirty"] and facts["regenerated"] == [".agents/skills/verify"], facts
    # The throwaway repo has no remote; what is under test is the copy, not the push.
    item = reap.judge_orca_worktree(str(wt), "ledger", {**facts, "unpushed": False}, PRS.get)
    assert item["target"] and item["regenerated"] == [".agents/skills/verify"] and ".agents/skills/verify" in item["how"][0], item
    off = reap.worktree_facts(str(wt), "acme/app#2", None, {}, git=gh, regenerated=False)
    assert off["dirty"] and off["regenerated"] == [], "reap.regenerated_untracked false keeps the old rule"
    # Anything else untracked keeps the card: a copy of a skill the repo does not track, or a note beside the copy.
    (wt / ".agents" / "skills" / "other").mkdir()
    (wt / ".agents" / "skills" / "other" / "SKILL.md").write_text("x")
    assert reap.worktree_facts(str(wt), "acme/app#2", None, {}, git=gh)["dirty"]
    (wt / ".agents" / "skills" / "other" / "SKILL.md").unlink()
    (wt / ".agents" / "skills" / "other").rmdir()
    (wt / "notes.md").write_text("mine")
    mixed = reap.worktree_facts(str(wt), "acme/app#2", None, {}, git=gh)
    assert mixed["dirty"] and reap.judge_orca_worktree(str(wt), "ledger", {**mixed, "unpushed": False}, PRS.get)["why"] == "변경 있음(추적 안 되는 파일 포함)"
    # reap judges again just before it acts: a file written since the scan stops it with nothing moved.
    calls, real_run = [], reap.run
    reap.run = lambda cmd, cwd=None, check=True: calls.append(cmd) or "" if cmd[0] == "orca" else real_run(cmd, cwd, check)
    os.environ["AGENT_SKILLS_TRASH"] = str(t / "trash")
    try:
        try:
            reap.act(item, [])
            raise AssertionError("a new untracked file must stop the reap")
        except RuntimeError as err:
            assert "남김" in str(err) and copy.exists() and calls == [], err
        (wt / "notes.md").unlink()
        out = reap.act(item, [])
    finally:
        reap.run = real_run
        del os.environ["AGENT_SKILLS_TRASH"]
    assert not copy.exists() and (t / "trash" / "verify" / "SKILL.md").exists() and "재생성 사본 1개" in out, out
    assert calls == [["orca", "worktree", "rm", "--worktree", f"path:{wt}", "--run-hooks", "--json"]], calls

# Facts are read in parallel; the items and their order are what one reader gives, in less time.
saved = reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which, reap.orca_json, reap.FACT_WORKERS
with tempfile.TemporaryDirectory() as tmp:
    paths = [os.path.join(os.path.realpath(tmp), f"wt{n:02d}") for n in range(16)]
    for path in paths:
        os.mkdir(path)
    reap.cwds, reap.shutil.which = (lambda: {}), (lambda name: "/bin/" + name)
    reap.orca_workers = lambda state=None: []
    reap.orca_json = lambda *a: {"worktrees": []}
    def slow_facts(path, pr, live, cwd_of, **kw):
        time.sleep(0.2)
        n = int(path[-2:])
        return {"main": False, "repo": "/r", "pr": pr, "live": live, "dirty": n % 3 == 0, "unpushed": False, "head": "h2"}
    reap.worktree_facts = slow_facts
    entries = [e("orca-worktree", p, pr="acme/app#1" if int(p[-2:]) % 4 == 0 else "acme/app#2") for p in paths]
    runs = []
    for workers in (1, reap.FACT_WORKERS):
        reap.FACT_WORKERS = workers
        t0 = time.time()
        runs.append((reap.janitor_scan({"orca-worktree"}, [], entries, [], PRS.get), time.time() - t0))
    assert runs[0][0] == runs[1][0] and [i["name"] for i in runs[1][0]] == paths, runs
    assert runs[1][1] < runs[0][1] / 3, [r[1] for r in runs]
reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which, reap.orca_json, reap.FACT_WORKERS = saved

prompt = reap.schedule_prompt("/abs/reap.py", pathlib.Path("/s"))
assert "reap --plan /s/janitor-plan.json" in prompt and "휴지통" in prompt and "report-only" not in prompt, prompt
assert "우회하지 않는다" in prompt and "orchestration send" not in prompt, prompt
assert "--to run:r1" in reap.schedule_prompt("/abs/reap.py", pathlib.Path("/s"), inbox="run:r1")


# compose-stack: only registered stacks; the owner done, then stopped or idle for N hours; preview ports wait for a record.
NOW = reap.parse_ts("2026-10-25T12:00:00+00:00")
REG = "2026-10-25T00:00:00+00:00"
stack = lambda running=0, last=NOW - 8 * H, first=NOW - 13 * H, ports=(): {
    "running": running, "stopped": 3, "first": first, "last": last, "ports": set(ports)}
ce = lambda name, ts=REG, **kw: {"ts": ts, "kind": "compose-stack", "id": name,
                                 "links": {k: kw.pop(k) for k in ("pr", "worktree") if k in kw}, **kw}
stacks = {"merged-stopped": stack(), "merged-running-old": stack(running=5), "merged-running-new": stack(running=5, last=NOW - H),
          "open": stack(), "nolink": stack(), "preview": stack(ports=(35000, 4501)), "previewed": stack(ports=(4501,)),
          "kept": stack(), "kept-expired": stack(), "busy-pid": stack(), "reused": stack(first=NOW - H), "wt-gone": stack(),
          "wt-merged": stack(), "wt-nopr": stack(), "wt-unread": stack(), "unregistered": stack()}
entries = [ce("merged-stopped", pr="acme/app#2"), ce("merged-running-old", pr="acme/app#2"),
           ce("merged-running-new", pr="acme/app#2"), ce("open", pr="acme/app#1"), ce("nolink"),
           ce("preview", pr="acme/app#2"), ce("previewed", pr="acme/app#2"),
           ce("previewed", ts="2026-10-25T10:00:00+00:00", previewed=True),
           ce("kept", pr="acme/app#2"), ce("kept", keep="사용자 시연", until="2026-10-26T00:00:00+00:00"),
           ce("kept-expired", pr="acme/app#3", keep="시연", until="2026-10-25T06:00:00+00:00"),
           ce("busy-pid", pr="acme/app#2", pid=4242), ce("reused", pr="acme/app#2"),
           ce("wt-gone", worktree="/wt/gone"), ce("wt-merged", worktree="/wt/m"), ce("wt-nopr", worktree="/wt/n"),
           ce("wt-unread", worktree="/wt/u"), ce("gone-stack", pr="acme/app#2"),
           ce("retired", pr="acme/app#2"), ce("retired", retired=True),
           # Downed by reap, then brought up again and registered by the card's next heavy run.
           ce("again", pr="acme/app#2"), ce("again", retired=True), ce("again", ts="2026-10-25T11:00:00+00:00", pr="acme/app#2"),
           ce("wt-moved", worktree="/wt/moved")]
stacks.update(retired=stack(), again=stack(first=NOW - 1.5 * H), **{"wt-moved": stack()})
prs_of = {"/wt/m": ("acme/app#3", False), "/wt/n": (None, False), "/wt/u": (None, True), "/wt/moved": ("acme/app#3", False)}
heads = {"/wt/m": "h3", "/wt/moved": "h9"}
owner = lambda links: reap.stack_owner(links, PRS.get, pr_of=prs_of.get, exists=lambda p: p != "/wt/gone", head=heads.get)
got = {i["name"]: i for i in reap.judge_compose(entries, stacks, owner, 6, NOW)}
targets = sorted(n for n, i in got.items() if i["target"])
# busy-pid: the pid is provenance only; heavy exits right after registering, and a reused pid would hold it forever.
assert targets == ["again", "busy-pid", "kept-expired", "merged-running-old", "merged-stopped", "previewed", "wt-gone",
                   "wt-merged"], targets
# A closed PR found by branch name that did not carry the worktree's HEAD is an old one: the card is still working.
assert "HEAD 가 바뀜" in got["wt-moved"]["why"], got["wt-moved"]
assert "unregistered" not in got and "gone-stack" not in got and "retired" not in got, sorted(got)
assert got["merged-stopped"]["how"] == ["docker compose -p merged-stopped down"] and "-v" not in got["merged-stopped"]["how"][0]
assert "모두 멈춤" in got["merged-stopped"]["why"] and "6시간 넘게" in got["merged-running-old"]["why"], got
assert "6시간 미만" in got["merged-running-new"]["why"] and got["open"]["why"] == "열린 PR acme/app#1", got
assert "보고만" in got["nolink"]["why"] and "미리보기 포트 4501" in got["preview"]["why"], got
assert "사용자 시연" in got["kept"]["why"] and "다시 만들어진" in got["reused"]["why"], got
assert got["wt-nopr"]["why"].endswith("PR 없음") and "읽지 못함" in got["wt-unread"]["why"], got
# The identity reap compares: a restart since the scan changes it.
assert got["merged-stopped"]["start"] != reap.judge_compose(entries, {"merged-stopped": stack(last=NOW - 7 * H)}, owner, 6, NOW)[0]["start"]
# compose_stacks counts only containers compose created: an app's worker with copied labels survives `compose down`.
c = lambda proj, svc, status, started, ports=None: {"Created": "2026-10-25T01:00:00.5Z", "Config": {"Labels": {
    "com.docker.compose.project": proj, "com.docker.compose.service": svc or "worker",
    **({"com.docker.compose.oneoff": "False"} if svc else {})}},
    "State": {"Status": status, "StartedAt": started, "FinishedAt": "0001-01-01T00:00:00Z"},
    "NetworkSettings": {"Ports": ports or {}}}
fake = [c("s1", "web", "running", "2026-10-25T02:00:00.1Z", {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "4501"}], "81/tcp": None}),
        c("s1", "init", "exited", "2026-10-25T01:30:00Z"), c("w1", None, "exited", "2026-10-25T01:00:00Z")]
saved = reap.run, reap.shutil.which
reap.shutil.which = lambda name: "/bin/" + name
reap.run = lambda cmd, cwd=None, check=True: "a\nb\nc\n" if cmd[1] == "ps" else json.dumps(fake)
got = reap.compose_stacks()
assert set(got) == {"s1"} and got["s1"]["running"] == 1 and got["s1"]["stopped"] == 1 and got["s1"]["ports"] == {4501}, got
assert got["s1"]["last"] == reap.parse_ts("2026-10-25T02:00:00+00:00"), got
reap.run = lambda cmd, cwd=None, check=True: None
assert reap.compose_stacks() is None
reap.run, reap.shutil.which = saved
assert reap.docker_ts("2026-10-25T06:49:59.921306307Z") == reap.parse_ts("2026-10-25T06:49:59+00:00")
assert "compose-stack" in reap.JANITOR and "compose-stack" in reap.SCHEDULED

# ledger add: a keep needs a deadline, a pid is a number; folded lines keep the owner link.
with tempfile.TemporaryDirectory() as tmp:
    led = os.path.join(tmp, "ledger.jsonl")
    cli = lambda *a: subprocess.run([sys.executable, reap.__file__, "ledger", "add", "--kind", "compose-stack", *a,
                                     "--ledger", led], capture_output=True, text=True)
    assert cli("--id", "s1", "--pr", "acme/app#2", "--pid", "77").returncode == 0
    assert cli("--id", "s1", "--keep", "시연").returncode != 0
    assert cli("--id", "s1", "--keep", "시연", "--until", "nope").returncode != 0
    assert cli("--id", "s1", "--pid", "x").returncode != 0
    assert cli("--id", "s1", "--previewed").returncode == 0
    (f,) = reap.folded(reap.ledger_read(pathlib.Path(led)), "compose-stack")
    assert f["links"]["pr"] == "acme/app#2" and f["pid"] == 77 and f["previewed"] is True and f["registered"], f


print("ok")
