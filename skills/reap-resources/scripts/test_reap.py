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
    9: row(9, "/usr/bin/node --test-reporter-destination /cache/uv/report.txt /opt/app.js"),
}
got = {i["pid"]: i["target"] for i in reap.judge_orphans(rows, ["/cache/uv/"], 6)}
assert got == {1: True, 3: True, 6: False, 7: True}, got
assert reap.judge_orphans(rows, [], 6) == []

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

print("ok")
