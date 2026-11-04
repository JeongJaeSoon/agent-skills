"""Judgement rules of reap.py, plus one real reap of throwaway processes. Run: python3 test_reap.py"""
import contextlib, io, json, os, pathlib, re, subprocess, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import reap

H = 3600
# The volume rules' owner judgment has its own section at the end; the sections before it test the rest of each rule.
saved_owner = getattr(reap, "owner_facts", None), getattr(reap, "owner_hold", None)
reap.owner_facts, reap.owner_hold = dict, lambda owners, facts: None


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
    # An idle Claude session appends untimed lines (`atis-latch`) every ~30 minutes: the mtime is fresh, the work is
    # not. Measured on a card idle for 2d22h that read as 43 minutes quiet.
    main.write_text('{"type":"user","timestamp":"2026-10-25T07:20:36Z"}\n' + '{"type":"atis-latch"}\n' * 3)
    sub.unlink()
    os.utime(main, (now - 60, now - 60))
    assert reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x") == now - reap.parse_ts("2026-10-25T07:20:36Z")
    main.write_text('{"type":"user","timestamp":"2026-10-25T07:20:36Z"}\n{"type":"x","timestamp":"2026-10-24T07:20:36Z"}\n')
    assert reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x") == now - reap.parse_ts("2026-10-25T07:20:36Z"), \
        "an older time appended last does not make the session quieter"
    # /reload-* rows the skills-sync broadcast typed, and the pr-link row a reload rewrites, are not work.
    assert reap.reload_row.__module__ == "sync", "the repo's scripts/sync.py must load, not the fallback"
    tail = (pathlib.Path(__file__).resolve().parents[2] / "orchestrate" / "scripts" / "testdata" / "reload-tail.jsonl").read_text()
    main.write_text(tail)
    assert reap.last_entry_time(main) == reap.parse_ts("2026-11-03T10:05:01.000Z"), reap.last_entry_time(main)
    lines = tail.splitlines(keepends=True)
    main.write_text("".join(lines[4:]))
    os.utime(main, (now - 60, now - 60))
    assert reap.last_entry_time(main) is None, "reloads only: unknown, not the last reload's mtime"
    assert reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x") is None, "an unknown time keeps nothing by itself"
    main.write_text("".join(lines) + '{"type":"user","timestamp":"2026-11-03T14:00:00Z","message":{"content":"<command-name>/compact</command-name>"}}\n')
    assert reap.last_entry_time(main) == reap.parse_ts("2026-11-03T14:00:00Z"), "any other slash command is work"
    main.write_text('{"type":"user","timestamp":"soon"}\n')
    assert reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x") is None, "an unreadable time is unknown, not quiet"
    assert reap.transcript_quiet(pathlib.Path(tmp), now, reasons=True)("/w/my_repo.x").endswith("을 읽지 못함")
    # Path.is_dir and glob swallow a PermissionError: a folder that cannot be listed must not read as no transcript.
    main.write_text('{"type":"user","timestamp":"2026-10-25T07:20:36Z"}\n')
    for locked in (proj, proj / "sid" / "subagents", pathlib.Path(tmp)) if os.geteuid() else ():
        locked.chmod(0o000)
        try:
            assert reap.transcript_quiet(pathlib.Path(tmp), now)("/w/my_repo.x") is None, locked
            why = reap.transcript_quiet(pathlib.Path(tmp), now, reasons=True)("/w/my_repo.x")
            assert why == "세션 기록 폴더를 읽지 못함(PermissionError)", (locked, why)
        finally:
            locked.chmod(0o700)
    assert reap.transcript_quiet(pathlib.Path(tmp), now, reasons=True)("/w/other") is None, "no folder is no transcript"
    assert reap.transcript_quiet(pathlib.Path(tmp) / "absent", now, reasons=True)("/w/my_repo.x") is None

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
# A card that pushed HEAD under another branch name has its PR only there: found by HEAD, and only the PR whose head
# commit is HEAD (the search also matches a PR that merely contains the commit).
def by_head(found):
    def git(cmd, cwd=None, check=True):
        if cmd[0] == "gh":
            return json.dumps(found) if "--search" in cmd else "[]"
        return fake_git([])(cmd, cwd, check)
    return git
other = {**mine, "headRefOid": "h2"}
assert reap.worktree_facts("/r-wt", None, None, {}, git=by_head([other]))["pr"] == "acme/app#2"
assert reap.worktree_facts("/r-wt", None, None, {}, git=by_head([{**other, "headRefOid": "h9"}]))["pr"] is None
assert reap.worktree_facts("/r-wt", None, None, {}, git=by_head([{**other, "isCrossRepository": True}]))["pr"] is None
def search_down(cmd, cwd=None, check=True):
    return None if "--search" in cmd else by_head([])(cmd, cwd, check)
assert reap.worktree_facts("/r-wt", None, None, {}, git=search_down)["pr_unread"] is True, "a failed search is not 'no PR'"
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
# A HEAD on the default branch is the head of any PR merged by fast-forward: no search by HEAD there.
def landed_by_head(cmd, cwd=None, check=True):
    return by_head([other])(cmd, cwd, check) if cmd[0] == "gh" else landed_git("origin/main\n", True)(cmd, cwd, check)
f = reap.worktree_facts("/r-wt", None, None, {}, git=landed_by_head)
assert f["pr"] is None and f["landed"] is True, f
def gh_down(cmd, cwd=None, check=True):
    return None if cmd[0] == "gh" else fake_git([])(cmd, cwd, check)
assert reap.worktree_facts("/r-wt", None, None, {}, git=gh_down)["pr_unread"] is True
assert reap.worktree_facts("/r-wt", None, None, {}, git=fake_git([]))["pr_unread"] is False
# A git read that fails is not a clean checkout: the worktree is kept with the reason.
def failing_git(cmd, cwd=None, check=True):
    return None if cmd[-2:] in (["status", "--porcelain"], ["--not", "--remotes"]) else fake_git([mine])(cmd, cwd, check)
facts = reap.worktree_facts("/r-wt", None, None, {}, git=failing_git)
assert facts["unreadable"] is True, facts

assert reap.automation_workspaces({"automations": [{"workspaceId": "r::/w/run"}, {"workspaceId": None}, {}]}) == {"/w/run"}
assert reap.automation_workspaces({"worktrees": []}) == set() and reap.automation_workspaces(None) is None

H = 3600

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
# notify_unrun sends one status message (an automation round has no Dispatch for an escalation) to the configured
# address; without one it prints only.
calls = []
send = lambda argv, check: calls.append(argv) or "{}"
assert reap.notify_unrun("run:r1", "python3 reap.py reap --plan /s/p.json", send)(2) is True
assert calls[0][:5] == ["orca", "orchestration", "send", "--to", "run:r1"] and "reap --plan /s/p.json" in calls[0][-2], calls
assert calls[0][calls[0].index("--type") + 1] == "status", calls
assert "--type status" in reap.schedule_prompt("reap.py", pathlib.Path("/s"), inbox="run:r1")
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

# reap.act's orca-worktree removal, which cards.py and reap_hard.py call: ignored files (.env.local, caches) go to the
# trash before `orca worktree rm`, which would delete them.
wt_item = lambda path, **kw: {"kind": "orca-worktree", "key": f"orca-worktree:{path}", "name": str(path), **kw}
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
        out = reap.act(wt_item(wt), [])
    finally:
        reap.run = real_run
        del os.environ["AGENT_SKILLS_TRASH"]
    assert sorted(os.listdir(t / "trash")) == [".env.local", "cache"] and (t / "trash" / "cache" / "big.bin").exists(), out
    assert calls == [["orca", "worktree", "rm", "--worktree", f"path:{wt}", "--run-hooks", "--json"]], calls
    assert "ignored 항목 2개" in out, out
    # A card held only by idle shells: its terminals are closed first, or the rm would leave them running in a gone dir.
    calls.clear()
    reap.run = lambda cmd, cwd=None, check=True: calls.append(cmd) or "" if cmd[0] == "orca" else real_run(cmd, cwd, check)
    os.environ["AGENT_SKILLS_TRASH"] = str(t / "trash2")
    try:
        reap.act(wt_item(wt, shells=[61, 62], terminals=["t1", "t2"]), [])
    finally:
        reap.run = real_run
        del os.environ["AGENT_SKILLS_TRASH"]
    # Only the listed terminals: one opened since the scan is not closed with them.
    assert [c[:4] for c in calls] == [["orca", "terminal", "close", "--terminal"]] * 2 + [["orca", "worktree", "rm", "--worktree"]], calls
    assert [c[4] for c in calls[:2]] == ["t1", "t2"], calls
argv = reap.schedule_cmd("/abs/reap.py", pathlib.Path("/s"), "path:/repo", ledger=pathlib.Path("/l/ledger.jsonl"))
assert argv[:3] == ["orca", "automations", "create"] and "--write" not in argv
assert argv[argv.index("--workspace-mode") + 1] == "existing" and argv[argv.index("--trigger") + 1] == "17 */3 * * *"
# The load job: its own name and trigger, the same round prompt, and a precheck that first reads the load.
load = reap.schedule_cmd("/abs/reap.py", reap.STATE_HOME, "path:/repo", load=True)
assert load[load.index("--name") + 1] == reap.LOAD_NAME and load[load.index("--trigger") + 1] == reap.LOAD_TRIGGER
assert load[load.index("--precheck") + 1] == "python3 /abs/reap.py precheck --load"
same = reap.schedule_cmd("/abs/reap.py", reap.STATE_HOME, "path:/repo")
assert load[load.index("--prompt") + 1] == same[same.index("--prompt") + 1]
with tempfile.TemporaryDirectory() as tmp:
    s = pathlib.Path(tmp)
    on = {"automations": [{"name": reap.JANITOR_NAME, "enabled": True}]}
    assert reap.load_due(s, {"load_limit": 8}, now=1e6, load5=7.9) == (False, "load5 7.9 < 한계 8")
    assert reap.load_due(s, {"load_limit": 8}, now=1e6, load5=8.0, autos=on)[0], "no round on record: due"
    # The janitor turned off or removed (setup disable, or in the Orca app) stops the load job too.
    for autos in ({"automations": [{"name": reap.JANITOR_NAME, "enabled": False}]}, {"automations": []}):
        assert reap.load_due(s, {"load_limit": 8}, now=1e6, load5=12, autos=autos) == (False, "janitor 가 꺼져 있거나 없음")
    saved_oj = reap.orca_json
    reap.orca_json = lambda *a: None
    assert not reap.load_due(s, {"load_limit": 8}, now=1e6, load5=12)[0], "an unreadable list reaps nothing"
    reap.orca_json = saved_oj
    reap.stamp_round(s, now=1e6 - 30 * 60)
    due, why = reap.load_due(s, {"load_limit": 8}, now=1e6, load5=12, autos=on)
    assert not due and "0h30m 전" in why, why
    assert reap.load_due(s, {"load_limit": 8, "load_cooldown_minutes": 20}, now=1e6, load5=12, autos=on)[0]
    due, why = reap.load_due(s, {"load_limit": 8, "load_cooldown_minutes": None}, now=1e6, load5=12, autos=on)
    assert not due and "0h30m 전" in why, "null is the default cooldown, not a TypeError"
    for bad in ("1h", "nan", "inf", -5):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            due, why = reap.load_due(s, {"load_limit": 8, "load_cooldown_minutes": bad}, now=1e6, load5=12, autos=on)
        assert not due and "0h30m 전" in why, f"{bad!r} falls back to the default cooldown, never to none"
        assert "load_cooldown_minutes" in err.getvalue(), err.getvalue()
    reap.stamp_round(s, now=1e6 - 61 * 60)
    assert reap.load_due(s, {"load_limit": 8}, now=1e6, load5=12, autos=on)[0], "past the cooldown: due again"
    reap.stamp_round(s, now=1e6 + 10 * 60)
    assert reap.load_due(s, {"load_limit": 8}, now=1e6, load5=12, autos=on)[0], "a stamp from the future holds nothing off"
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
    # The card kinds before `card` are refused, never run as nothing.
    for kind in reap.RETIRED_KINDS:
        assert cli("ledger", "add", "--kind", kind, "--path", str(d)).returncode != 0, kind
        r = cli("scan", "--kinds", f"tmpdir,{kind}")
        assert r.returncode and "--kinds card" in r.stderr, r.stderr
        plan["items"] = [{"kind": kind, "key": f"{kind}:{d}", "name": str(d), "target": True, "how": []},
                         {"kind": "evidence", "key": f"evidence:{shot}", "name": str(shot), "target": True, "how": []}]
        for saved_plan in (tmp / "old-plan.json", tmp / "old" / "janitor-plan.json"):
            saved_plan.parent.mkdir(exist_ok=True)
            saved_plan.write_text(json.dumps(plan))
            r = cli("reap", "--plan", str(saved_plan))
            assert r.returncode and f"{kind}:{d}" in r.stderr and "scan --kinds card" in r.stderr and r.stdout == "", r
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
    item = wt_item(wt, regenerated=facts["regenerated"])
    # Anything else untracked keeps the card: a copy of a skill the repo does not track, or a note beside the copy.
    (wt / ".agents" / "skills" / "other").mkdir()
    (wt / ".agents" / "skills" / "other" / "SKILL.md").write_text("x")
    assert reap.worktree_facts(str(wt), "acme/app#2", None, {}, git=gh)["dirty"]
    (wt / ".agents" / "skills" / "other" / "SKILL.md").unlink()
    (wt / ".agents" / "skills" / "other").rmdir()
    (wt / "notes.md").write_text("mine")
    mixed = reap.worktree_facts(str(wt), "acme/app#2", None, {}, git=gh)
    assert mixed["dirty"] and mixed["regenerated"] == [], mixed
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
fake = [{**x, "Id": i} for i, x in zip("abc", fake)]
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

# stack-volume: only volumes owned by a stack under a configured prefix, with no container of that stack in any state.
assert "stack-volume" in reap.SCHEDULED and "stack-volume" not in reap.LEDGER_KINDS
assert isinstance(reap.stack_volume_spec({}), str)
for bad in ({"prefixes": ["v-"]}, {"prefixes": ["verify"]}, {"prefixes": []}, {"prefixes": "verify-x-"},
            {"prefixes": ["verify-x-"], "label": "com.docker.compose.project"}, {"prefixes": ["verify-x-"], "label": 'a"b'},
            {"prefixes": ["verify-x-"], "protect": "main"}, {"prefixes": ["verify-x-"], "protect": None}, ["verify-x-"]):
    assert isinstance(reap.stack_volume_spec({"stack_volumes": bad}), str), bad
SPEC = reap.stack_volume_spec({"stack_volumes": {"prefixes": ["verify-x-"], "label": "app.installation",
                                                  "protect": ["verify-x-keep"]}})
assert SPEC == (("verify-x-",), "app.installation", frozenset({"verify-x-keep"})), SPEC
old = NOW - 7 * H
vol = lambda name, created=old, **labels: {"name": name, "labels": labels, "created": created}
P = "com.docker.compose.project"
vols = [
    vol("verify-x-1_db", **{P: "verify-x-1"}),                                  # project volume, stack gone
    vol("ws-verify-x-1-s1", **{"app.installation": "verify-x-1"}),              # workspace volume, stack gone
    vol("verify-x-2_db", **{P: "verify-x-2"}),                                  # an exited container still there
    vol("ws-verify-x-3-s1", **{"app.installation": "verify-x-3"}),              # a running worker still there
    vol("verify-x-4_db", NOW - H, **{P: "verify-x-4"}),                          # too young
    vol("verify-x-keep_db", **{P: "verify-x-keep"}),                            # protected
    vol("main_db", **{P: "main"}),                                              # another prefix
    vol("ws-main-s1", **{"app.installation": "main"}),
    vol("main-verify-x-1_db", **{P: "main-verify-x-1"}),                        # prefix only inside the name
    vol("unrelated", **{P: "verify-x-1"}),                                      # label without the owner in its name
    vol("anon123"),
    vol("verify-x-5_db", **{P: "verify-x-5", "app.installation": "verify-x-keep"}),   # second owner protected
    vol("verify-x-6_db", **{P: "verify-x-6", "app.installation": "verify-x-2"}),      # second owner has a container
    vol("ws-verify-x-7-s1", **{P: "main", "app.installation": "verify-x-7"}),         # owners disagree
]
got = {i["name"]: (i["target"], i["why"]) for i in reap.judge_stack_volumes(vols, {"verify-x-2", "verify-x-3", "main"}, SPEC, 6, NOW)}
assert {n for n, (t, _) in got.items() if t} == {"verify-x-1_db", "ws-verify-x-1-s1"}, got
assert set(got) == {"verify-x-1_db", "ws-verify-x-1-s1", "verify-x-2_db", "ws-verify-x-3-s1", "verify-x-4_db",
                    "verify-x-keep_db", "verify-x-5_db", "verify-x-6_db", "ws-verify-x-7-s1"}, got
assert "보호" in got["verify-x-keep_db"][1] and "컨테이너" in got["ws-verify-x-3-s1"][1], got
assert "보호" in got["verify-x-5_db"][1] and "컨테이너" in got["verify-x-6_db"][1] and "엇갈림" in got["ws-verify-x-7-s1"][1], got

# The scan reads containers of any state with both owner labels, and the janitor acts with one exact-name rm.
saved = reap.run, reap.shutil.which
calls = []
alive = {}
def fake_run(cmd, cwd=None, check=True):
    calls.append(cmd)
    if cmd[:3] == ["docker", "ps", "-aq"]:
        return alive.get(cmd[-1], "")
    if cmd[:2] == ["docker", "ps"]:
        return "verify-x-2\t\n\tverify-x-3\n"
    if cmd[:3] == ["docker", "volume", "ls"]:
        return "verify-x-1_db\nverify-x-2_db\n" if cmd[-1] == f"label={P}" else "ws-verify-x-1-s1\nws-verify-x-3-s1\n"
    if cmd[:3] == ["docker", "volume", "inspect"]:
        return json.dumps([{"Name": v["name"], "Labels": v["labels"], "CreatedAt": "2026-10-25T00:00:00Z"}
                           for v in vols if v["name"] in cmd[3:]])
reap.shutil.which = lambda name: "/bin/" + name
reap.run = fake_run
notes = []
got = reap.janitor_scan({"stack-volume"}, [], [], notes, pr_state=lambda r: None, hours=6, now=NOW,
                        cfg={"stack_volumes": {"prefixes": ["verify-x-"], "label": "app.installation"}})
assert [i["name"] for i in got if i["target"]] == ["verify-x-1_db", "ws-verify-x-1-s1"], got
assert calls[0][:3] == ["docker", "ps", "-a"] and "app.installation" in calls[0][-1], calls[0]
assert not any(c[:2] == ["docker", "volume"] and c[2] in ("prune", "rm") for c in calls), calls
# Right before the rm, the volume and its owner's containers are read again.
saved_config = reap.config
reap.config = lambda: {"stack_volumes": {"prefixes": ["verify-x-"], "label": "app.installation"}}
target = next(i for i in got if i["target"])
calls.clear()
assert reap.act(target, []) == "docker volume rm"
assert calls[-1] == ["docker", "volume", "rm", "verify-x-1_db"], calls
assert ["docker", "ps", "-aq", "--filter", f"label={P}=verify-x-1"] in calls and \
    ["docker", "ps", "-aq", "--filter", "label=app.installation=verify-x-1"] in calls, calls
alive["label=app.installation=verify-x-1"] = "c1\n"
calls.clear()
assert reap.act(target, []).startswith("건너뜀: 컨테이너가 생긴"), calls
assert not any(c[:3] == ["docker", "volume", "rm"] for c in calls), calls
alive.clear()
assert reap.act({**target, "created": target["created"] - 1}, []).startswith("건너뜀: 다시 만들어졌"), target
two = next(i for i in got if i["target"] and i["name"].startswith("ws-"))
alive["label=com.docker.compose.project=verify-x-1"] = "c2\n"
assert reap.act(two, []).startswith("건너뜀: 컨테이너가 생긴"), two
alive.clear()
reap.run = lambda cmd, cwd=None, check=True: None
assert reap.act(target, []) == "건너뜀: 볼륨을 다시 읽지 못함"
reap.run = fake_run
reap.config = lambda: {}
assert reap.act(target, []).startswith("건너뜀: reap.stack_volumes"), target
reap.config = saved_config
notes = []
assert reap.janitor_scan({"stack-volume"}, [], [], notes, pr_state=lambda r: None, cfg={}) == [] and notes, notes
reap.run = lambda cmd, cwd=None, check=True: None
notes = []
assert reap.stack_volume_scan(SPEC, 6, notes) == [] and "읽지 못함" in notes[0], notes
reap.run, reap.shutil.which = saved

# stopped-container: a stopped container outside compose under a configured name prefix. Unset is off.
assert "stopped-container" in reap.SCHEDULED and "stopped-container" not in reap.LEDGER_KINDS
assert "labeled-volume" in reap.SCHEDULED and "labeled-volume" not in reap.LEDGER_KINDS
assert reap.stopped_container_spec({}) is None and reap.labeled_volume_spec({}) is None
for bad in ([], "x", {"prefixes": []}, {"prefixes": ["ab-"]}, {"prefixes": ["example"]}, {"prefixes": ["example-*"]},
            {"prefixes": ["example-"], "protect": "x"}):
    assert bad == [] or isinstance(reap.stopped_container_spec({"stopped_containers": bad}), str), bad
CSPEC = reap.stopped_container_spec({"stopped_containers": {"prefixes": ["example-verify-"], "protect": ["example-verify-keep"]}})
T0, T1 = "2026-10-25T00:00:00.123456789Z", "2026-10-25T01:00:00Z"
ZERO = "0001-01-01T00:00:00Z"
AGO = reap.docker_ts(T1) + 7 * H


def ctr(name, status="exited", labels=None, finished=T1, created=T0, ports=(), mounts=()):
    return {"Id": name + "-id", "Name": "/" + name, "Created": created, "State": {"Status": status, "FinishedAt": finished},
            "Config": {"Labels": labels or {}}, "HostConfig": {"PortBindings": {f"{p}/tcp": [{"HostPort": str(p)}] for p in ports}},
            "Mounts": [{"Type": "volume", "Name": m} for m in mounts]}


PROJ = reap.COMPOSE_PROJECT
fleet = [ctr("example-verify-1"), ctr("example-verify-new", finished="2026-10-25T07:00:00Z"),
         ctr("example-verify-run", "running"), ctr("example-verify-pause", "paused"), ctr("example-verify-dead", "dead"),
         ctr("example-verify-made", "created", finished=ZERO), ctr("example-verify-keep"),
         ctr("example-verify-held"), ctr("example-verify-port", ports=(4999,)),
         ctr("example-verify-zero", finished=ZERO),
         ctr("example-verify-compose", labels={PROJ: "stack", "com.docker.compose.oneoff": "False"}),
         ctr("example-verify-copied", labels={PROJ: "stack"}), ctr("example-web", "running", labels={PROJ: "stack"}),
         ctr("example-verify-lone", labels={PROJ: "gone"}), ctr("unrelated")]
rows = [reap.container_row(c) for c in fleet]
got = {i["name"]: (i["target"], i["why"]) for i in
       reap.judge_stopped_containers(rows, CSPEC, 6, AGO, keep={"example-verify-held"}, ports={4999})}
assert "unrelated" not in got and "example-web" not in got, got
assert sorted(k for k, v in got.items() if v[0]) == ["example-verify-1", "example-verify-lone", "example-verify-made"], got
assert "6시간 미만" in got["example-verify-new"][1] and "멈추지 않음(running)" in got["example-verify-run"][1], got
assert "paused" in got["example-verify-pause"][1] and "dead" in got["example-verify-dead"][1], got
assert got["example-verify-keep"][1] == got["example-verify-held"][1] == "보호 목록·상시 서비스", got
assert got["example-verify-port"][1] == "보호 포트 4999" and "시각" in got["example-verify-zero"][1], got
assert "compose 가 만든" in got["example-verify-compose"][1] and "아직 돎" in got["example-verify-copied"][1], got

# labeled-volume: the label is the proof; no label is never listed, however unreferenced.
KEY = "example.agent.origin"
for bad in ({"namespace": "Bad Space", "origins": ["verify-example"]}, {"namespace": "com.docker.x", "origins": ["a"]},
            {"namespace": "com.docker", "origins": ["a"]}, {"namespace": "example.agent"},
            {"namespace": "example.agent", "origins": []}, {"namespace": "example.agent", "origins": ["a"], "protect": 1}):
    assert isinstance(reap.labeled_volume_spec({"labeled_volumes": bad}), str), bad
LV = {"namespace": "example.agent", "origins": ["verify-example"], "protect": ["example-held"]}
VSPEC = reap.labeled_volume_spec({"labeled_volumes": LV})
assert VSPEC[0] == KEY and reap.origin_key({"labeled_volumes": {"namespace": "example.agent"}}) == KEY
assert reap.origin_key({}) is None


def vol(name, origin="verify-example", project=None, created=NOW - 10 * H):
    labels = {KEY: origin} if origin else {}
    return {"name": name, "labels": {**labels, **({PROJ: project} if project else {})}, "created": created}


vols = [vol("a" * 64), vol("example_db", project="done-stack"), vol("no-label", origin=None), vol("other", origin="verify-other"),
        vol("used"), vol("used-stopped"), vol("young", created=NOW - H), vol("held", project="example-held"),
        vol("kept", project="always-on"), vol("sibling", project="alive-stack")]
rows = [reap.container_row(c) for c in (ctr("c1", "running", mounts=("used",)), ctr("c2", mounts=("used-stopped",)),
                                        ctr("c3", labels={PROJ: "alive-stack"}))]
got = {i["name"]: (i["target"], i["why"]) for i in reap.judge_labeled_volumes(vols, rows, VSPEC, 6, NOW, keep={"always-on"})}
assert sorted(k for k, v in got.items() if v[0]) == ["a" * 64, "example_db"], got
assert "no-label" not in got and "보고만" in got["other"][1], got
assert got["used"][1] == got["used-stopped"][1] == "컨테이너가 참조함(멈춘 것 포함)", got
assert got["held"][1] == got["kept"][1] == "보호 목록·상시 서비스" and "미만" in got["young"][1], got
assert "컨테이너가 남은 스택" in got["sibling"][1], got

# The whole path with Docker replaced: every delete names one exact id or name, and is read again first.
calls, docker = [], {"containers": [ctr("example-verify-1")], "volumes": [vol("example_db", project="done-stack")]}


def docker_run(cmd, cwd=None, check=True, env=None, timeout=None):
    calls.append(cmd)
    if cmd[:2] == ["docker", "ps"]:
        return "\n".join(c["Id"] for c in docker["containers"]) + "\n"
    if cmd[:2] == ["docker", "inspect"]:
        return json.dumps([c for c in docker["containers"] if c["Id"] in cmd[2:]])
    if cmd[:3] == ["docker", "volume", "ls"]:
        return "\n".join(v["name"] for v in docker["volumes"]) + "\n"
    if cmd[:3] == ["docker", "volume", "inspect"]:
        return json.dumps([{"Name": v["name"], "Labels": v["labels"], "CreatedAt": "2026-10-25T00:00:00Z"}
                           for v in docker["volumes"] if v["name"] in cmd[3:]])
    return ""


saved = reap.run, reap.shutil.which, reap.config
reap.run, reap.shutil.which = docker_run, lambda name: "/bin/" + name
import guard  # noqa: E402
saved_guard = guard.protect_file, guard.always_on
protect_dir = tempfile.TemporaryDirectory()
protect_txt = pathlib.Path(protect_dir.name, "protect.txt")
guard.protect_file, guard.always_on = lambda: str(protect_txt), set
CFG = {"stopped_containers": {"prefixes": ["example-verify-"]}, "labeled_volumes": LV}
reap.config = lambda: CFG
docker["volumes"][0]["created"] = reap.parse_ts("2026-10-25T00:00:00Z")
LATE = docker["volumes"][0]["created"] + 7 * H
notes = []
got = reap.leftovers(CFG, 6, LATE, notes)
assert [(i["kind"], i["target"]) for i in got] == [("stopped-container", True), ("labeled-volume", True)] and not notes, (got, notes)
assert ["docker", "volume", "ls", "-q", "--filter", f"label={KEY}"] in calls, calls
assert reap.janitor_scan({"stopped-container", "labeled-volume"}, [], [], [], pr_state=lambda r: None, hours=6, now=LATE,
                         cfg={}) == []  # unset: nothing is read or listed
box, shelf = got
calls.clear()
assert reap.act(box, []) == "docker rm example-verify-1-id (example-verify-1)", calls
assert calls[-1] == ["docker", "rm", "example-verify-1-id"], calls
calls.clear()
assert reap.act(shelf, []) == "docker volume rm example_db", calls
assert calls[-1] == ["docker", "volume", "rm", "example_db"], calls
# What changed between the scan and the rm keeps the target; nothing is removed then.
for change in ("running", "again", "gone"):
    was = docker["containers"]
    docker["containers"] = {"running": [ctr("example-verify-1", "running")], "gone": [],
                            "again": [ctr("example-verify-1", finished="2026-10-25T05:00:00Z")]}[change]
    calls.clear()
    assert reap.act(box, []).startswith("건너뜀:") and calls and not any(c[:2] == ["docker", "rm"] for c in calls), (change, calls)
    docker["containers"] = was
for change in (ctr("late", mounts=("example_db",)), ctr("late", labels={PROJ: "done-stack"})):
    was = docker["containers"]
    docker["containers"] = was + [change]  # a new container of that stack keeps the volume even before it mounts it
    calls.clear()
    assert reap.act(shelf, []).startswith("건너뜀:") and not any(c[:3] == ["docker", "volume", "rm"] for c in calls), calls
    docker["containers"] = was
assert reap.act({**shelf, "created": 1.0}, []).startswith("건너뜀: 다시 만들어졌"), shelf
# A protect line added after the scan, or a list that can no longer be read, keeps the target. reap-hard requires the file.
for line in ("project example-verify-1", "project done-stack", "project example_db"):
    protect_txt.write_text(line + "\n")
    wanted = shelf if line != "project example-verify-1" else box
    calls.clear()
    assert reap.act(wanted, []) == "건너뜀: 보호 목록·상시 서비스", (line, calls)
    assert not any(c[:2] == ["docker", "rm"] or c[:3] == ["docker", "volume", "rm"] for c in calls), calls
protect_txt.write_text("not a line\n")
calls.clear()
assert reap.act(box, []).startswith("건너뜀: 보호 목록을 다시 읽지 못함") and reap.act(shelf, []).startswith("건너뜀: 보호 목록을 다시 읽지 못함")
assert not calls, calls
protect_txt.unlink()
assert reap.remove_leftover(box, CFG, str(protect_txt)).startswith("건너뜀: 보호 목록을 다시 읽지 못함")
assert not any(c[:2] == ["docker", "rm"] for c in calls), calls
reap.config = lambda: {}
assert reap.act(box, []).startswith("건너뜀: reap.stopped_containers") and reap.act(shelf, []).startswith("건너뜀: reap.labeled_volumes")
reap.config = lambda: CFG
# Unreadable docker lists nothing and removes nothing.
reap.run = lambda cmd, cwd=None, check=True: None
notes = []
assert reap.leftovers(CFG, 6, LATE, notes) == [] and "읽지 못함" in notes[0], notes
assert reap.act(box, []).startswith("건너뜀: 컨테이너를 다시 읽지 못함")
reap.run = docker_run
# A reply that succeeded but is not whole reads as unreadable, never as no reference: nothing is a target.
whole = ctr("example-verify-1")
for reply in ("[]", "{}", "not json", json.dumps([{"Id": "other"}]),
              *(json.dumps([{k: v for k, v in whole.items() if k != gap}])
                for gap in ("Id", "Name", "State", "Config", "HostConfig", "Mounts")),
              json.dumps([{**whole, "Config": {}}]), json.dumps([{**whole, "HostConfig": {}}]),
              json.dumps([{**whole, "Mounts": [{"Name": "example_db"}]}])):
    reap.run = lambda cmd, cwd=None, check=True, env=None, timeout=None: reply if cmd[:2] == ["docker", "inspect"] else docker_run(cmd)
    notes = []
    assert reap.leftovers(CFG, 6, LATE, notes) == [] and "읽지 못함" in notes[0], (reply, notes)
    assert reap.act(shelf, []).startswith("건너뜀: 컨테이너를 다시 읽지 못함"), reply
    assert reap.unlabeled_volumes() is None, reply
for reply in ("[]", "not json", json.dumps([{"Name": "example_db"}]), json.dumps([{"Name": "other", "CreatedAt": "2026-10-25T00:00:00Z"}])):
    reap.run = lambda cmd, cwd=None, check=True, env=None, timeout=None: reply if cmd[:3] == ["docker", "volume", "inspect"] else docker_run(cmd)
    notes = []
    assert [i["kind"] for i in reap.leftovers(CFG, 6, LATE, notes)] == ["stopped-container"] and "볼륨을 읽지 못함" in notes[0], (reply, notes)
    assert reap.act(shelf, []).startswith("건너뜀: 볼륨을 다시 읽지 못함"), reply
reap.run = docker_run

# No bulk delete anywhere: the source names none, and a full path above only ever ran single-target removals.
SRC = pathlib.Path(reap.__file__).read_text()
assert not re.search(r'"docker", *"(system|container|volume|image)", *"prune"|"rm", *"-f"|"down", *"-v"', SRC), "bulk delete in reap.py"

# The inventory is read only: unlabeled anonymous volumes are counted and never reach a plan.
ANON = ["b" * 64, "c" * 64, "d" * 64]
docker["volumes"] = [vol(ANON[0], origin=None), vol(ANON[1], origin=None), {**vol(ANON[2]), "labels": {KEY: "verify-example"}},
                     vol("named", origin=None)]
docker["containers"] = [ctr("user", mounts=(ANON[1],))]
calls.clear()
got = reap.unlabeled_volumes()
assert got["total"] == 2 and got["referenced"] == 1 and [v["name"] for v in got["volumes"]] == [ANON[0]], got
assert all(c[:3] in (["docker", "volume", "ls"], ["docker", "volume", "inspect"]) or c[:2] in (["docker", "ps"], ["docker", "inspect"])
           for c in calls), calls
assert not [i for i in reap.leftovers(CFG, 6, LATE, []) if i["kind"] == "labeled-volume" and i["name"] == ANON[0]]
reap.run = lambda cmd, cwd=None, check=True: None
assert reap.unlabeled_volumes() is None
reap.run, reap.shutil.which, reap.config = saved
guard.protect_file, guard.always_on = saved_guard
protect_dir.cleanup()

with tempfile.TemporaryDirectory() as tmp:
    cli = lambda *a: subprocess.run([sys.executable, reap.__file__, *a], capture_output=True, text=True,
                                    env={**os.environ, "HOME": tmp, "PATH": tmp})
    got = cli("inventory")
    assert got.returncode != 0, got
    assert cli("inventory", "--unlabeled-volumes").returncode != 0  # no docker on this PATH
    assert cli("origin-label", "verify-example").returncode != 0  # no namespace set
    os.makedirs(os.path.join(tmp, ".claude"))
    pathlib.Path(tmp, ".claude", "agent-skills.json").write_text(json.dumps({"reap": {"labeled_volumes": LV}}))
    got = cli("origin-label", "verify-example")
    assert got.returncode == 0 and got.stdout.strip() == f"{KEY}=verify-example", got
    assert cli("origin-label", "bad value").returncode != 0

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

# The owner of a stack volume, the judgment every volume rule goes through. The first fixture is the incident: a
# verification stack brought down with no -v, older than the hours, its card's worktree there and its PR open, the
# ledger line carrying the worktree and no PR.
reap.owner_facts, reap.owner_hold = saved_owner
guard.protect_file, guard.always_on = (lambda: str(protect_txt)), set
PROJECT = "com.docker.compose.project"
OWNER = "app.installation"
ORIGIN = "example.agent.origin"
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    live = root / "workspaces" / "app" / "card-open"
    live.mkdir(parents=True)
    gone = str(root / "workspaces" / "app" / "card-closed")
    other_gone = str(root / "workspaces" / "app" / "card-closed-2")
    led = root / "ledger.jsonl"
    line = lambda stack, kind="compose-stack", pr=None, wt=gone, **extra: json.dumps(
        {"kind": kind, "id": stack, "links": {"pr": pr, "worktree": wt}, **extra})
    good = [
        line("verify-app-incident", wt=str(live)),
        line("verify-app-closed"),
        line("verify-app-merged", pr="acme/app#1"),
        line("verify-app-pr-open", pr="acme/app#2"),
        line("verify-app-pr-unread", pr="acme/app#3"),
        line("verify-app-retired"), line("verify-app-retired", wt=None, retired=True),
        line("verify-app-pr-only", pr="acme/app#1", wt=None),
        line("verify-app-reused"), line("verify-app-reused", kind="stack-seen", wt=str(live)),
        line("verify-app-relinked", wt=str(live)), line("verify-app-relinked", pr="acme/app#1", wt=None),
        line("verify-app-reregistered", wt=other_gone), line("verify-app-reregistered"),
    ]
    led.write_text("\n".join(good) + "\n")
    os.environ["AGENT_SKILLS_LEDGER"] = str(led)
    stacks = ["incident", "closed", "merged", "pr-open", "pr-unread", "retired", "pr-only", "reused", "relinked",
              "reregistered", "unregistered"]
    old_time = "2026-10-25T00:00:00Z"
    late = reap.parse_ts(old_time) + 7 * H
    compose = [{"Name": f"verify-app-{s}_db", "Labels": {PROJECT: f"verify-app-{s}"}, "CreatedAt": old_time} for s in stacks]
    workspace = [{"Name": f"ws-verify-app-{s}-s1", "Labels": {OWNER: f"verify-app-{s}"}, "CreatedAt": old_time} for s in stacks]
    labeled = [{"Name": f"verify-app-{s}_data", "Labels": {ORIGIN: "verify-example", PROJECT: f"verify-app-{s}"},
                "CreatedAt": old_time} for s in stacks]
    labeled.append({"Name": "no-owner", "Labels": {ORIGIN: "verify-example"}, "CreatedAt": old_time})
    split = {"Name": "split-owner_db", "Labels": {PROJECT: "verify-app-closed", OWNER: "verify-app-incident"}, "CreatedAt": old_time}
    orca = {"reply": json.dumps({"result": {"worktrees": [{"path": str(live)}]}})}
    prs = {"1": "MERGED", "2": "OPEN"}
    procs, missing = {1: "/"}, set()

    def world(cmd, cwd=None, check=True):
        if cmd[:2] == ["orca", "worktree"]:
            return orca["reply"]
        if cmd[:3] == ["gh", "pr", "view"]:
            state = prs.get(cmd[3])
            return state if state == "not json" else state and json.dumps({"state": state, "headRefOid": "x", "closedAt": None})
        if cmd[:3] == ["docker", "volume", "ls"]:
            if cmd[-1] == f"label={ORIGIN}":
                return "\n".join(v["Name"] for v in labeled)
            return "\n".join(v["Name"] for v in (compose if cmd[-1] == f"label={PROJECT}" else workspace))
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return json.dumps([v for v in compose + workspace + labeled + [split] if v["Name"] in cmd[3:]])
        if cmd[:2] == ["docker", "ps"] or cmd[:2] == ["docker", "inspect"]:
            return ""
        raise AssertionError(cmd)

    saved = reap.run, reap.shutil.which, reap.pr_reader, reap.cwds, reap.config
    reader = reap.pr_reader
    SV = {"prefixes": ["verify-app-"], "label": OWNER}
    CFG2 = {"stack_volumes": SV, "labeled_volumes": {"namespace": "example.agent", "origins": ["verify-example"]}}
    reap.run, reap.shutil.which = world, lambda name: None if name in missing else "/bin/" + name
    reap.pr_reader, reap.cwds, reap.config = (lambda: reader(world)), (lambda: procs), (lambda: CFG2)
    CLOSED = {"closed", "merged", "retired", "reregistered"}

    def scanned(cfg=CFG2):
        notes = []
        items = reap.janitor_scan({"stack-volume", "labeled-volume"}, [], [], notes, pr_state=lambda r: None, hours=6,
                                  now=late, cfg=cfg)
        return items, notes

    items, notes = scanned()
    want = {f"verify-app-{s}_db" for s in CLOSED} | {f"ws-verify-app-{s}-s1" for s in CLOSED} | {f"verify-app-{s}_data" for s in CLOSED}
    assert {i["name"] for i in items if i["target"]} == want, sorted(i["name"] for i in items if i["target"])
    why = {i["name"]: i["why"] for i in items}
    assert "카드" in why["verify-app-incident_db"] and "카드" in why["ws-verify-app-incident-s1"], why
    assert "카드" in why["verify-app-incident_data"] and "열린 PR" in why["verify-app-pr-open_db"], why
    assert "읽지 못함" in why["verify-app-pr-unread_db"] and "기록이 없음" in why["verify-app-unregistered_db"], why
    assert "worktree 연결" in why["verify-app-pr-only_db"] and "카드" in why["verify-app-reused_db"], why
    assert "카드" in why["verify-app-relinked_db"] and "소유" in why["no-owner"], why
    closed_db = "verify-app-closed_db"
    is_target = lambda name: name in {i["name"] for i in scanned()[0] if i["target"]}
    assert is_target(closed_db)

    # A session still running in a card whose folder went, with the card gone from Orca.
    procs[2] = gone + "/sub"
    assert not is_target(closed_db)
    del procs[2]
    procs[3] = gone + "-other"  # a sibling path is not inside it
    assert is_target(closed_db)
    del procs[3]
    # lsof prints a non-ASCII path byte by byte; bytes that are no text cannot be told from a session's folder.
    hangul = str(root / "카드")
    led.write_text("\n".join(good + [line("verify-app-hangul", wt=hangul)]) + "\n")
    assert reap.owner_hold(["verify-app-hangul"], reap.owner_facts()) is None
    procs[2] = "".join(chr(b) if b < 128 else f"\\x{b:02x}" for b in hangul.encode())
    assert "프로세스" in reap.owner_hold(["verify-app-hangul"], reap.owner_facts())
    procs[2] = "/tmp/\\xff"
    assert not is_target(closed_db)
    del procs[2]
    # A folder of a card still open, deleted on its own, is not the card closing.
    led.write_text("\n".join(good + [line("verify-app-sub", wt=str(live / "backend"))]) + "\n")
    assert "카드" in reap.owner_hold(["verify-app-sub"], reap.owner_facts())
    led.write_bytes("\n".join(good).encode() + b'\n{"kind": "compose\xff-stack", "id": "verify-app-closed", "links": {}}\n')
    assert not is_target(closed_db), "a damaged byte is damage, not another kind"
    led.write_text("\n".join(good) + "\n")

    # A damaged line, wherever it stands and however little of its id survived, holds everything: it may be an owner.
    for bad in ('{"kind": "compose-stack", "id": "verify-', '{"ts": "2026', json.dumps(["not", "an", "object"]),
                line("verify-app-other", wt=["a", "list"]), line("verify-app-other", pr=7),
                json.dumps({"kind": "stack-seen", "id": 7, "links": {}}), json.dumps({"kind": "compose-stack", "id": "x"})):
        led.write_text("\n".join(good) + "\n" + bad)
        items, notes = scanned()
        assert not [i for i in items if i["target"]] and notes, bad
    led.write_text(line("unrelated", kind="tmpdir", wt=7) + "\n" + "\n".join(good) + "\n")
    assert is_target(closed_db), "a line about something else is not this judgment's to read"
    led.write_text("\n".join(good) + "\n")

    # What cannot be read holds every volume, with a note, whichever rule listed it.
    orca["reply"] = None
    items, notes = scanned()
    assert not [i for i in items if i["target"]] and any("카드 목록" in n for n in notes), (items, notes)
    orca["reply"] = json.dumps({"result": {"worktrees": [{"path": str(live)}], "truncated": True}})
    assert not [i for i in scanned()[0] if i["target"]]
    orca["reply"] = json.dumps({"result": None})
    assert not [i for i in scanned()[0] if i["target"]]
    orca["reply"] = json.dumps({"result": {"worktrees": [{"path": str(live)}]}})
    missing.add("orca")  # a scheduler's PATH without orca: no card list, not an empty one
    assert not [i for i in scanned()[0] if i["target"]]
    missing.clear()
    saved_cwds = reap.cwds
    reap.cwds = lambda: None
    assert not [i for i in scanned()[0] if i["target"]]
    reap.cwds = saved_cwds
    real_facts = reap.owner_facts
    reap.owner_facts = lambda: (_ for _ in ()).throw(ValueError("x"))  # a crash holds the volumes, and the round goes on
    assert not [i for i in scanned()[0] if i["target"]]
    reap.owner_facts = real_facts
    for state in (None, "DRAFT", "not json"):  # only a state that proves it closed counts
        prs["1"] = state
        items, notes = scanned()
        assert "verify-app-merged_db" not in {i["name"] for i in items if i["target"]}, state
        assert "verify-app-closed_db" in {i["name"] for i in items if i["target"]}, state
    prs["1"] = "MERGED"
    os.environ["AGENT_SKILLS_LEDGER"] = str(root)  # a directory: there, and not readable as a ledger
    assert not [i for i in scanned()[0] if i["target"]]
    os.environ["AGENT_SKILLS_LEDGER"] = str(root / "missing.jsonl")
    assert not [i for i in scanned()[0] if i["target"]]
    os.environ["AGENT_SKILLS_LEDGER"] = str(led)

    # A card Orca still lists holds its stack with its folder out of sight, as does a path that cannot be looked at.
    orca["reply"] = json.dumps({"result": {"worktrees": [{"path": gone}]}})
    assert not {f"verify-app-{s}_db" for s in CLOSED} & {i["name"] for i in scanned()[0] if i["target"]}
    orca["reply"] = json.dumps({"result": {"worktrees": [{"path": str(live)}]}})
    facts = reap.owner_facts()
    saved_stat = reap.os.stat
    reap.os.stat = lambda p, **k: (_ for _ in ()).throw(PermissionError(p)) if str(p) == gone else saved_stat(p, **k)
    assert "카드" in reap.owner_hold(["verify-app-closed"], facts)
    reap.os.stat = saved_stat

    # The last check, inside the lock, reads everything again, whichever cleaner and kind it is.
    plan = {i["name"]: i for i in scanned()[0] if i["target"]}
    empty = {"port": set(), "pid": set(), "path": [], "project": set()}
    stale = plan["verify-app-closed_db"]
    hard = {**plan["verify-app-closed_data"], "kind": "volume", "id": "verify-app-closed_data"}
    manual = {"kind": "docker", "key": "volume:verify-app-closed_db", "name": "volume verify-app-closed_db",
              "owners": ["verify-app-closed"]}
    for target, cleaner in ((stale, "reap"), (plan["ws-verify-app-closed-s1"], "reap"), (plan["verify-app-closed_data"], "reap"),
                            (hard, "hard"), (manual, "reap")):
        assert guard.item(target, empty, cleaner) is None, target
    pathlib.Path(gone).mkdir()
    for target, cleaner in ((stale, "reap"), (hard, "hard"), (manual, "reap")):
        assert "카드" in guard.item(target, empty, cleaner), target
    pathlib.Path(gone).rmdir()
    orca["reply"] = None
    assert "읽지 못함" in guard.keep(stale, empty) and "읽지 못함" in guard.keep(hard, empty, "hard")
    orca["reply"] = json.dumps({"result": {"worktrees": [{"path": str(live)}]}})
    assert guard.item({**stale, "owners": []}, empty) and guard.item({k: v for k, v in manual.items() if k != "owners"}, empty)
    # The protect file's project lines hold a stack volume too.
    assert "보호" in guard.item(stale, {**empty, "project": {"verify-app-closed"}})

    # The manual docker kind carries the same owners and goes through the same judgment.
    now_read = {"names": [v["Name"] for v in compose[:2]] + [split["Name"]]}

    def dangling(cmd, cwd=None, check=True):
        if cmd[:3] == ["docker", "volume", "ls"]:
            return "\n".join(now_read["names"])
        if cmd[:2] == ["docker", "images"]:
            return ""
        return world(cmd, cwd, check)

    reap.run = dangling
    notes = []
    got = {i["key"]: i for i in reap.docker_scan(0, notes)}
    assert got["volume:verify-app-closed_db"]["target"] and got["volume:verify-app-closed_db"]["owners"] == ["verify-app-closed"], got
    assert not got["volume:verify-app-incident_db"]["target"] and "카드" in got["volume:verify-app-incident_db"]["why"], got
    assert not got["volume:split-owner_db"]["target"] and got["volume:split-owner_db"]["owners"] == ["verify-app-closed", "verify-app-incident"], got
    reap.run = world

    # A working directory seen on a stack's containers is recorded once, so its volumes outlive the labels.
    seen = {"verify-app-closed": {"dirs": {str(live), "relative/dir"}}, "verify-app-incident": {"dirs": {str(live)}}}
    led.write_text("\n".join(good) + "\n")
    before = len(good)
    reap.note_stack_dirs(seen)
    reap.note_stack_dirs(seen)
    assert len(led.read_text().splitlines()) == before + 1, led.read_text()
    assert "카드" in reap.owner_hold(["verify-app-closed"], reap.owner_facts())
    entries = reap.ledger_read(led)
    assert "verify-app-closed" in {e["id"] for e in reap.folded(entries, "compose-stack")}
    assert not reap.folded(entries, "compose-stack")[0].get("kind") == "stack-seen"
    reap.run, reap.shutil.which, reap.pr_reader, reap.cwds, reap.config = saved
    os.environ.pop("AGENT_SKILLS_LEDGER")

guard.protect_file, guard.always_on = saved_guard

print("ok")
