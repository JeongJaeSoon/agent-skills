"""Judgement rules of reap.py, plus one real reap of throwaway processes. Run: python3 test_reap.py"""
import json, os, pathlib, subprocess, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import reap

H = 3600


def row(pid, cmd, ppid=1, age=10 * H, rss=1024):
    return {"pid": pid, "ppid": ppid, "age": age, "start": "Sat Sep 26 00:00:00 2026", "rss": rss, "cpu": 1.0, "cmd": cmd}


BROKER = "/usr/bin/node /x/scripts/app-server-broker.mjs serve --endpoint unix:/t/b.sock --cwd {} --pid-file /t/b.pid"
rows = {
    10: row(10, BROKER.format("/wt/live")),
    11: row(11, "codex app-server", ppid=10),
    12: row(12, "SkyComputerUseClient event-stream mcp", ppid=11),
    20: row(20, BROKER.format("/wt/dead")),
    30: row(30, BROKER.format("/wt/gone")),
    40: row(40, BROKER.format("/wt/fresh"), age=H),
    50: row(50, "claude --permission-mode auto", ppid=99),
    # A session whose prompt quotes a broker command line is not a broker.
    60: row(60, "claude --permission-mode auto " + BROKER.format("/wt/gone")),
}
got = {i["pid"]: i for i in reap.judge_brokers(rows, {50: "/wt/live/sub", 60: "/home"}, 6, exists=lambda p: p != "/wt/gone")}
# One own claude missing from the cwd listing blinds every idle verdict.
blind = {i["pid"]: i["target"] for i in reap.judge_brokers(rows, {50: "/wt/live/sub"}, 6, exists=lambda p: p != "/wt/gone")}
assert blind == {10: False, 20: False, 30: False, 40: False}, blind
assert set(got) == {10, 20, 30, 40}, got
assert not got[10]["target"] and "claude" in got[10]["why"] and got[10]["procs"] == 3
assert got[20]["target"] and got[20]["why"] == "cwd에 claude 없음"
assert got[30]["target"] and got[30]["why"] == "cwd 없음"
assert not got[40]["target"]
# Without cwd data nothing proves a broker idle, not even a vanished cwd.
got = {i["pid"]: i["target"] for i in reap.judge_brokers(rows, None, 6, exists=lambda p: p != "/wt/gone")}
assert got == {10: False, 20: False, 30: False, 40: False}, got
# A live claude still in a vanished cwd keeps its broker.
got = {i["pid"]: i["target"] for i in reap.judge_brokers(rows, {50: "/wt/gone"}, 6, exists=lambda p: p != "/wt/gone")}
assert got[30] is False, got
# A claude installed through npm runs as node; it still protects its cwd.
assert reap.is_claude("node /n/lib/node_modules/@anthropic-ai/claude-code/cli.js -p")
assert not reap.is_claude("node /x/app-server-broker.mjs serve") and not reap.is_claude("claudette")

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

# Load: every top consumer gets one class, and its CPU counts its whole tree.
ME = 501
rows = {
    10: {**row(10, "/Library/Agent/agentd --daemon"), "uid": 0, "cpu": 220.0},
    20: {**row(20, "claude --permission-mode auto", ppid=99), "uid": ME, "cpu": 8.0},
    21: {**row(21, "node mcp-server.js", ppid=20), "uid": ME, "cpu": 2.0},
    30: {**row(30, "claude --permission-mode auto", ppid=99), "uid": ME, "cpu": 5.0},
    31: {**row(31, "lockf /tmp/bench.lock python3 bench.py", ppid=30), "uid": ME, "cpu": 95.0},
    40: {**row(40, BROKER.format("/wt/gone")), "uid": ME, "cpu": 30.0},
    41: {**row(41, "codex app-server", ppid=40), "uid": ME, "cpu": 10.0},
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
    (cache / "app-server-broker.mjs").write_text(sleeper)
    py = sys.executable
    subprocess.run(["sh", "-c", f"'{py}' '{cache}/litellm.py' --port 1 &"], check=True)
    subprocess.run(["sh", "-c", f"'{py}' '{cache}/app-server-broker.mjs' serve --cwd {tmp}/gone-wt --pid-file x &"], check=True)
    time.sleep(1)
    rows = reap.processes()
    orphan = [i for i in reap.judge_orphans(rows, [str(cache)], 0) if i["target"]]
    broker = [i for i in reap.judge_brokers(rows, reap.cwds(), 0) if i["name"] == f"{tmp}/gone-wt"]
    assert len(broker) == 1 and broker[0]["target"] and broker[0]["procs"] == 2, broker
    # The broker also runs from the cache path, so it counts as an orphan too; the plain sleeper is the other one.
    assert len(orphan) == 2 and all(i["procs"] == 2 for i in orphan), orphan
    pids = [p for i in orphan for p in reap.tree(i["pid"], rows)]
    # Same pid and command but another start time is another process.
    assert reap.kill_tree({**broker[0], "start": "Thu Jan  1 00:00:00 1970"}, []) == "사라졌거나 다른 프로세스"
    assert all(reap.kill_tree(i, []) == "종료" for i in orphan)
    assert not set(pids) & set(reap.processes())
    assert reap.kill_tree(orphan[0], []) == "사라졌거나 다른 프로세스"
    assert len(pids) == 4

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
assert got["/t/merged"]["how"][0].endswith("lsregister -u /t/merged/X.app") and got["/t/merged"]["how"][-1] == "rm -rf /t/merged"
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

# gui-app: the registered pid with the same start time only.
rows = {40: row(40, "/Applications/Sim.app/Contents/MacOS/Sim"), 41: row(41, "/x/other")}
entries = [e("gui-app", "40@" + rows[40]["start"], pr="acme/app#2"), e("gui-app", "41@Thu Jan  1 00:00:00 1970", pr="acme/app#2"),
           e("gui-app", "42@" + rows[40]["start"], pr="acme/app#2")]
got = reap.judge_gui_apps(entries, PRS.get, rows)
assert [(i["key"], i["target"]) for i in got] == [("gui-app:40@" + rows[40]["start"], True)], got
# chrome-window: no script closes it; the plan says the agent does.
got = reap.judge_chrome([e("chrome-window", "tab:5", pr="acme/app#3"), e("chrome-window", "tab:6", pr="acme/app#1")], PRS.get)
assert [i["target"] for i in got] == [True, False] and "에이전트가 처리" in got[0]["how"][0]
assert [i["target"] for i in reap.judge_remote_branches([e("remote-branch", "acme/app:feat")])] == [False]
got = reap.judge_orca_worker([{"dispatchId": "ctx_1", "taskId": "task_1"}])
assert got[0]["target"] and got[0]["how"] == ["orca orchestration worker-release --dispatch ctx_1 --json"]

# orca-worktree: a merged or closed PR, clean, nothing unpushed (unless the PR carried HEAD), no live turn.
ok = {"pr": "acme/app#2", "live": None, "dirty": False, "unpushed": False, "head": "h2"}
judge = lambda **kw: reap.judge_orca_worktree("/wt/a", "ledger", {**ok, **kw}, PRS.get)
assert judge()["target"] and judge()["how"] == ["orca worktree rm --worktree path:/wt/a --run-hooks --json"]
assert judge(unpushed=True)["target"], "a squash-merged PR carried HEAD"
assert judge(unpushed=True, head="h9")["why"] == "원격에 없는 커밋"
assert judge(pr="acme/app#1")["why"] == "열린 PR acme/app#1"
assert judge(dirty=True)["why"].startswith("변경 있음")
assert judge(live="살아 있는 워커 턴(ctx_1)")["why"].startswith("살아 있는")
assert judge(pr=None)["why"] == "연결된 PR 없음" and judge(pr="acme/app#3")["target"]

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
# A git read that fails is not a clean checkout: the worktree is kept with the reason.
def failing_git(cmd, cwd=None, check=True):
    return None if cmd[-2:] in (["status", "--porcelain"], ["--not", "--remotes"]) else fake_git([mine])(cmd, cwd, check)
facts = reap.worktree_facts("/r-wt", None, None, {}, git=failing_git)
assert reap.judge_orca_worktree("/r-wt", "ledger", facts, PRS.get) == {
    **judge(), "why": "git 상태를 읽지 못함", "target": False, "key": "orca-worktree:/r-wt", "name": "/r-wt",
    "how": ["orca worktree rm --worktree path:/r-wt --run-hooks --json"]}, facts

# An unreadable worker list is not an empty one: a ledger worktree may be a user's takeover, so it is kept.
saved = reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which
with tempfile.TemporaryDirectory() as tmp:
    wt = os.path.realpath(tmp)
    reap.cwds, reap.shutil.which = (lambda: {}), (lambda name: "/bin/" + name)
    reap.worktree_facts = lambda path, pr, live, cwd_of: {"main": False, "repo": "/r", **ok, "live": live}
    for workers, target, why in ((None, False, "워커 목록을 읽지 못함"), ([], True, "PR acme/app#2 merged (ledger)")):
        reap.orca_workers = lambda state=None: workers
        got = reap.janitor_scan({"orca-worktree"}, [], [e("orca-worktree", wt, pr="acme/app#2")], [], PRS.get)
        assert [(i["target"], i["why"]) for i in got] == [(target, why)], (workers, got)
reap.orca_workers, reap.worktree_facts, reap.cwds, reap.shutil.which = saved

# precheck: exit 0 once per new non-empty target set, 1 otherwise.
with tempfile.TemporaryDirectory() as tmp:
    st = pathlib.Path(tmp)
    items = [{"key": "tmpdir:/a", "target": True}, {"key": "tmpdir:/b", "target": False}]
    assert reap.precheck(items, st) == (0, ["tmpdir:/a"])
    assert reap.precheck(items, st)[0] == 1
    assert reap.precheck(items + [{"key": "tmpdir:/c", "target": True}], st)[0] == 0
    assert reap.precheck([], st)[0] == 1 and reap.precheck([], st)[0] == 1
argv = reap.schedule_cmd("/abs/reap.py", pathlib.Path("/s"), "path:/repo", ledger=pathlib.Path("/l/ledger.jsonl"))
assert argv[:3] == ["orca", "automations", "create"] and "--write" not in argv
assert argv[argv.index("--workspace-mode") + 1] == "existing" and argv[argv.index("--trigger") + 1] == "17 */3 * * *"
# The automation does not inherit the shell that ran schedule, so the precheck names the state and ledger itself.
assert argv[argv.index("--precheck") + 1] == \
    "env AGENT_SKILLS_STATE=/s AGENT_SKILLS_LEDGER=/l/ledger.jsonl python3 /abs/reap.py precheck"

# CLI: ledger add, precheck twice, reap leaves report-only kinds alone, schedule only prints.
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(os.path.realpath(tmp))
    d = tmp / "build"
    d.mkdir()
    env = {**os.environ, "AGENT_SKILLS_STATE": str(tmp / "state")}
    script = str(pathlib.Path(__file__).parent / "reap.py")
    cli = lambda *a: subprocess.run([sys.executable, script, *a], env=env, capture_output=True, text=True)
    assert cli("ledger", "add", "--kind", "tmpdir", "--path", str(d), "--worktree", str(tmp / "gone-wt")).returncode == 0
    assert str(d) in cli("ledger", "list").stdout
    first, second = cli("precheck", "--kinds", "tmpdir"), cli("precheck", "--kinds", "tmpdir")
    assert (first.returncode, second.returncode) == (0, 1), (first.stdout, second.stdout)
    out = cli("reap", "--plan", str(tmp / "state" / "janitor-plan.json")).stdout
    assert "보고만 tmpdir" in out and d.exists(), out
    out = cli("schedule", "--workspace", "path:/repo")
    assert out.returncode == 0 and out.stdout.startswith("orca automations create") and "--write" in out.stdout, out

print("ok")
