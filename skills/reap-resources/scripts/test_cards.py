"""Card verdicts of cards.py from fake git, gh and orca output. Run: python3 test_cards.py"""
import json, os, pathlib, sys, tempfile

# reap resolves its state directory (logs, locks) when imported: point it at a throwaway one first, so no section
# below can write the owner's real <state>/reap-actions.jsonl or sweep.log.
_STATE = tempfile.mkdtemp(prefix="test-cards-state-")
os.environ["AGENT_SKILLS_STATE"] = _STATE
os.environ["AGENT_SKILLS_SWEEP"] = os.environ["AGENT_SKILLS_PATROL"] = "off"
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cards, reap
assert str(reap.STATE) == _STATE, reap.STATE

NOW = 2_000_000_000.0
IDLE = 30 * 60
HEAD, PRHEAD = "a" * 40, "b" * 40
tmp = tempfile.mkdtemp()
CARD = reap.real(tmp)


def row(pid, cmd, ppid=1):
    return {"pid": pid, "ppid": ppid, "start": "Sat Sep 26 00:00:00 2026", "cmd": cmd}


def machine(rows=None, cwd_of=None, quiet=3 * 3600, prs=None, **kw):
    return {"now": NOW, "st": cards.settings({}), "rows": rows or {}, "cwd_of": cwd_of or {}, "live": {}, "patrol": {}, "refused": {}, "workers_unread": False,
            "runners": set(), "self": None, "projects": pathlib.Path(tmp) / "projects",
            "pr_state": (prs or {}).get, "quiet": lambda p: quiet, **kw}


def term(handle, agent, silent_s, screen=None):
    return {"handle": handle, "agentIdentity": "claude" if agent else None, "lastOutputAt": (NOW - silent_s) * 1000,
            "_screen": screen}


def fake(branch_prs=(), terminals=(), local_landed=False, compare=None, ahead="", remote_log="", patches=None,
         status="", calls=None, behind_pr=False):
    """A runner answering the commands gather() issues. compare: GitHub's ahead_by, or "404"."""
    screens = {t["handle"]: t.pop("_screen") for t in terminals}
    patches = patches or {}

    def run(cmd, cwd=None, input=None, errors=None):
        calls is not None and calls.append(cmd)
        c = " ".join(cmd)
        if cmd[:1] == ["git"] and "patch-id" in cmd:
            return f"{input.strip()} 0000\n"
        if cmd[0] == "git":
            if "rev-parse" in cmd and "HEAD" in cmd:
                return HEAD + "\n"
            if "status" in cmd:
                return status
            if "branch" in cmd and "--show-current" in cmd:
                return "feature\n"
            if "symbolic-ref" in cmd:
                return "origin/main\n"
            if "merge-base" in cmd:
                if "--is-ancestor" in cmd and cmd[-1] == PRHEAD:
                    return "" if behind_pr else None
                return "" if (cmd[-1] == "origin/main" and local_landed) else None
            if "remote" in cmd:
                return "https://github.com/acme/app.git\n"
            if "rev-list" in cmd:
                return ahead
            if "log" in cmd and "-1" in cmd:
                return "fix: thing\n"
            if "log" in cmd and "--remotes" in cmd:
                return remote_log
            if "show" in cmd:
                return patches.get(cmd[-1], "patch-" + cmd[-1])
            if "ls-files" in cmd:
                return ""
        if cmd[0] == "gh":
            if "pr" in cmd and "list" in cmd and "--head" in cmd:
                return json.dumps(list(branch_prs))
            if "pr" in cmd and "--search" in cmd:
                return "[]"
            if "compare" in c:
                if compare == "404":
                    errors is not None and errors.append("gh api: Not Found (HTTP 404)")
                    return None
                return f"{compare}\n"
        if cmd[:3] == ["orca", "terminal", "list"]:
            return json.dumps({"result": {"terminals": list(terminals), "truncated": False}})
        if cmd[:3] == ["orca", "terminal", "read"]:
            if screens.get(cmd[cmd.index("--terminal") + 1]) == "UNREADABLE":
                return None
            return json.dumps({"result": {"terminal": {"tail": screens.get(cmd[cmd.index("--terminal") + 1]) or ["❯"]}}})
        raise AssertionError(f"unexpected command {cmd}")
    return run


def verdict(m=None, w=None, **kw):
    f = cards.gather({"path": CARD, **(w or {})}, m or machine(), fake(**kw))
    return cards.judge(f, IDLE)


PR = {"url": "https://github.com/acme/app/pull/7", "state": "MERGED", "number": 7}
MERGED = {"acme/app#7": {"state": "MERGED", "headRefOid": HEAD}}

# The plain case: merged PR that carried HEAD, clean, nothing running → removed.
assert verdict(machine(prs=MERGED), branch_prs=[PR])[0] == cards.REMOVE

# A card whose `orca worktree rm` was refused is not removed again: the verdict is a hand-off to a person with the one
# command to run. Before rm_refused existed this same card read 삭제.
import rm_refused  # noqa: E402
refused_state = pathlib.Path(tempfile.mkdtemp())
rm_refused.add(CARD, refused_state)
v = verdict(machine(prs=MERGED, refused=rm_refused.read(refused_state)), branch_prs=[PR])
assert v[0] == cards.ASK and v[2] == [] and "사람에게 넘김" in v[1] and "worktree rm 이 거부" in v[1], v
assert rm_refused.command(CARD) in v[1] and "orca worktree rm --worktree path:" in v[1] and "--run-hooks" in v[1], v
# An unreadable record keeps the card; a card made again on that path (another inode) is not the refused one.
v = verdict(machine(prs=MERGED, refused=None), branch_prs=[PR])
assert v[0] == cards.KEEP and "거부 기록을 읽지 못함" in v[1], v
(refused_state / rm_refused.NAME).write_text(json.dumps({"path": CARD, "ino": 1, "t": 0}) + "\n")
assert rm_refused.read(refused_state) == {}
(refused_state / rm_refused.NAME).write_text("{not json\n")
assert rm_refused.read(refused_state) is None and rm_refused.held(CARD, refused_state) is None
# Codex round 1: a folder that exists but whose identity cannot be read stays recorded; a recycled inode with another
# birth time is a new card; an entry that recorded no identity is kept; the folder gone releases the record.
(refused_state / rm_refused.NAME).unlink()
ino, born = rm_refused.identity(CARD)
record = lambda **kw: (refused_state / rm_refused.NAME).write_text(json.dumps({"path": CARD, "t": 0, **kw}) + "\n")
record(ino=ino, born=(born or 0) + 5.0) if born is not None else record(ino=ino)
assert (rm_refused.held(CARD, refused_state) is False) == (born is not None)
record(ino=ino, born=born)
real_identity = rm_refused.identity
rm_refused.identity = lambda p: (_ for _ in ()).throw(PermissionError("denied"))
assert rm_refused.held(CARD, refused_state), "an unreadable folder is not proof the card is new"
rm_refused.identity = real_identity
record(ino=None)
assert rm_refused.held(CARD, refused_state)
record(ino=ino, born=born)
os.rename(CARD, CARD + "-moved")
assert rm_refused.held(CARD, refused_state) is False
os.rename(CARD + "-moved", CARD)
try:
    rm_refused.add(CARD + "-missing", refused_state)
except SystemExit as e:
    assert "nothing recorded" in str(e)
else:
    raise AssertionError("add must not record a folder it cannot read")
# reap.act reads the record before it trashes anything and again right before `orca worktree rm`.
saved_run, saved_state = reap.run, reap.STATE
reap.STATE, ran = refused_state, []
reap.run = lambda cmd, **k: ran.append(cmd) or ""
out = reap.act({"kind": "orca-worktree", "key": "orca-worktree:" + CARD, "name": CARD}, [])
assert out.startswith("건너뜀: 사람에게 넘김") and rm_refused.command(CARD) in out and not ran, (out, ran)
# Codex round 2: a record that arrives while the ignored files go to the trash stops the rm (the second read).
(refused_state / rm_refused.NAME).unlink()
subprocess_ran = []
saved_trash, saved_ls = reap.trash, reap.run
reap.run = lambda cmd, **k: subprocess_ran.append(cmd) or ("a\0" if "ls-files" in cmd else "")
reap.trash = lambda *paths: rm_refused.add(CARD, refused_state)
out = reap.act({"kind": "orca-worktree", "key": "orca-worktree:" + CARD, "name": CARD}, [])
assert out.startswith("건너뜀: 사람에게 넘김") and not any(c[:3] == ["orca", "worktree", "rm"] for c in subprocess_ran), (out, subprocess_ran)
reap.trash, reap.run = saved_trash, saved_ls
reap.run = lambda cmd, **k: ran.append(cmd) or ""
# A record that is a link to nothing, or a row with a path that is not text, cannot be read.
(refused_state / rm_refused.NAME).unlink()
os.symlink(refused_state / "nowhere", refused_state / rm_refused.NAME)
assert rm_refused.read(refused_state) is None
(refused_state / rm_refused.NAME).unlink()
(refused_state / rm_refused.NAME).write_text(json.dumps({"path": None, "ino": 1}) + "\n")
assert rm_refused.read(refused_state) is None
for bad in ({"ino": str(ino)}, {"ino": ino, "born": "yesterday"}, {"ino": True}, {"ino": ino + 0.5},
            {"ino": ino, "born": float("nan")}):  # rounds 3-4: wrong-typed identity (NaN is written as a bare token)
    (refused_state / rm_refused.NAME).write_text(json.dumps({"path": CARD, **bad}) + "\n")
    assert rm_refused.read(refused_state) is None, bad
for raw in ("[]", "null", '{"path": "' + CARD + '", "ino": 1, "born": ' + "9" * 400 + "}"):  # round 5: no crash either
    (refused_state / rm_refused.NAME).write_text(raw + "\n")
    assert rm_refused.read(refused_state) is None, raw
(refused_state / rm_refused.NAME).write_text("{not json\n")
out = reap.act({"kind": "orca-worktree", "key": "orca-worktree:" + CARD, "name": CARD}, [])
assert out.startswith("건너뜀:") and "읽지 못함" in out and not ran, (out, ran)
reap.run, reap.STATE = saved_run, saved_state

# Open PR: the worktree stays, only its idle agent terminal is closed.
OPEN = {"acme/app#7": {"state": "OPEN", "headRefOid": HEAD}}
v = verdict(machine(prs=OPEN), branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 2 * 3600)])
assert v[0] == cards.CLOSE and v[2] == ["t1"], v
v = verdict(machine(prs=OPEN), branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 60)])
assert v[0] == cards.KEEP and "진행 중" in v[1], v

# Trap 1: a failed fetch leaves origin/main stale, so a no-PR card whose HEAD is already on main reads as off it
# locally. GitHub's compare decides: ahead_by 0 → on main; a 404 → the commit never reached GitHub.
calls = []
v = verdict(local_landed=False, compare="0", calls=calls)
assert v[0] == cards.REMOVE and "GitHub compare main" in v[1], v
assert any("repos/acme/app/compare/main..." + HEAD in " ".join(c) for c in calls), calls
v = verdict(local_landed=False, compare="3")
assert v[0] == cards.KEEP and "기본 브랜치에 없음" in v[1], v
v = verdict(local_landed=False, compare="404")
assert v[0] == cards.KEEP and "GitHub 에 없는 커밋" in v[1], v
assert verdict(local_landed=True)[0] == cards.REMOVE

# Trap 2: an unpushed commit whose message is on the remote. Same patch → the same work, removable; another patch →
# a copy from before a rebase, for a person to decide; no such message → local work, kept.
CLOSED_OLD = {"acme/app#7": {"state": "MERGED", "headRefOid": PRHEAD}}
same = dict(ahead="c1\n", remote_log="r1\0fix: thing\n", patches={"c1": "P", "r1": "P"})
other = dict(ahead="c1\n", remote_log="r1\0fix: thing\n", patches={"c1": "P", "r1": "Q"})
m = machine(prs=CLOSED_OLD)
assert verdict(m, branch_prs=[PR], compare="5", **same)[0] == cards.KEEP  # HEAD not under the PR head: moved on
ancestor = lambda **kw: fake(branch_prs=[PR], compare="5", **kw)


def with_carried(**kw):
    run = ancestor(**kw)
    return lambda cmd, **k: "" if ("merge-base" in cmd and cmd[-1] == PRHEAD) else run(cmd, **k)


f = cards.gather({"path": CARD}, m, with_carried(**same))
assert cards.judge(f, IDLE)[0] == cards.REMOVE, cards.judge(f, IDLE)
f = cards.gather({"path": CARD}, m, with_carried(**other))
v = cards.judge(f, IDLE)
assert v[0] == cards.ASK and "patch" in v[1], v
f = cards.gather({"path": CARD}, m, with_carried(ahead="c1\n", remote_log=""))
assert cards.judge(f, IDLE)[:2] == (cards.KEEP, "원격에 없는 커밋"), cards.judge(f, IDLE)
# A PR that carried HEAD needs no patch check: a squash merge and a deleted branch leave its commits on no remote.
assert verdict(machine(prs=MERGED), branch_prs=[PR], ahead="c1\n")[0] == cards.REMOVE

# Trap 3: an Orca terminal's shell left at its prompt (a setup shell) is not "a process in the card": it is closed
# with the card. A shell that ran something recently, or a process a person started from it, keeps the card.
rows = {10: row(10, "/Applications/Orca.app/orca-terminal-host"), 11: row(11, "/usr/bin/login -flpq me /bin/zsh", 10),
        12: row(12, "-zsh", 11), 13: row(13, "node server.js", 12)}
m = machine(rows={k: rows[k] for k in (10, 11, 12)}, cwd_of={12: CARD}, prs=MERGED)
v = cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR], terminals=[term("s1", False, 2 * 3600)])), IDLE)
assert v[0] == cards.REMOVE and v[2] == ["s1"], v
v = cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR], terminals=[term("s1", False, 60)])), IDLE)
assert v[0] == cards.KEEP and "셸 터미널" in v[1], v
m = machine(rows=rows, cwd_of={12: CARD, 13: CARD}, prs=MERGED)
v = cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR], terminals=[term("s1", False, 2 * 3600)])), IDLE)
assert v[0] == cards.KEEP and "pid 13" in v[1], v
# A Claude session's own shell child is a turn still running (a Bash call, a background shell).
rows2 = {20: row(20, "claude --permission-mode auto"), 21: row(21, "/bin/zsh -c sleep 600", 20)}
m = machine(rows=rows2, cwd_of={20: CARD}, prs=MERGED)
v = cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR], terminals=[term("a1", True, 2 * 3600)])), IDLE)
assert v[0] == cards.KEEP and "진행 중" in v[1], v

# A Claude process with no Orca agent terminal (headless `claude -p`, a session another tool started) mid-turn, its
# transcript written a minute ago: it is a live session, not an empty card. With no transcript it is never idle.
rows3 = {30: row(30, "claude -p review this"), 31: row(31, "node /some/runner.js")}
rows3[30]["ppid"] = 31
m = machine(rows=rows3, cwd_of={30: CARD}, prs=MERGED, quiet=60)
v = cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR])), IDLE)
assert v[0] == cards.KEEP and "조용하지 않음" in v[1], v
m = machine(rows=rows3, cwd_of={30: CARD}, quiet=60)
v = cards.judge(cards.gather({"path": CARD}, m, fake(local_landed=True)), IDLE)
assert v[0] == cards.KEEP and "Claude 세션이 있음" in v[1], v
m = machine(rows=rows3, cwd_of={30: CARD}, prs=MERGED, quiet=None)
assert cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR])), IDLE)[0] == cards.KEEP
m = machine(rows=rows3, cwd_of={30: CARD}, prs=MERGED, quiet=3 * 3600)
assert cards.judge(cards.gather({"path": CARD}, m, fake(branch_prs=[PR])), IDLE)[0] == cards.REMOVE

# Trap 4: the agent's screen. "끝났다, 닫아도 된다" on an idle screen is evidence the session is done even before its
# transcript has been quiet for idle_minutes; a question on screen goes to a person; neither overrides changes.
m = machine(quiet=10 * 60)
done = ["작업을 끝냈다. 이 카드는 닫아도 된다.", "❯"]
v = verdict(m, local_landed=True, terminals=[term("a1", True, 20 * 60, done)])
assert v[0] == cards.REMOVE and "화면에 끝났다고 씀" in v[1] and v[2] == ["a1"], v
v = verdict(m, local_landed=True, terminals=[term("a1", True, 20 * 60, ["다음 단계로 갈까?", "Enter to select · Esc to cancel"])])
assert v[0] == cards.ASK, v
v = verdict(m, local_landed=True, terminals=[term("a1", True, 20 * 60, ["❯"])])
assert v[0] == cards.KEEP and "Claude 세션이 있음" in v[1], v
v = verdict(m, local_landed=True, status=" M a.py\n", terminals=[term("a1", True, 20 * 60, done)])
assert v[0] == cards.KEEP and "변경" in v[1], v

# Class 3: a card whose PR another card carries on. The open PR keeps this one forever otherwise. Only the facts together
# (no terminal, clean, nothing local, HEAD behind the PR's head, another card AT the PR's head) make it a question for
# a person: ASK, never a delete. A missing fact leaves it the plain keep.
THEIRS = "/work/app/other-card"
handed = lambda **kw: verdict(machine(prs=OPEN_PRHEAD, heads={THEIRS: PRHEAD}, **kw.pop("m", {})),  # noqa: E731
                              branch_prs=[{**PR, "state": "OPEN"}], terminals=kw.pop("terminals", []), behind_pr=True, **kw)
OPEN_PRHEAD = {"acme/app#7": {"state": "OPEN", "headRefOid": PRHEAD}}
v = handed()
assert v[0] == cards.ASK and "other-card" in v[1] and v[2] == [], v
plain = verdict(machine(prs=OPEN_PRHEAD), branch_prs=[{**PR, "state": "OPEN"}], terminals=[], behind_pr=True)
assert plain[0] == cards.KEEP and "열린 PR" in plain[1], "no other card at the head: the plain keep"
assert verdict(machine(prs=OPEN_PRHEAD, heads={THEIRS: HEAD}), branch_prs=[{**PR, "state": "OPEN"}], terminals=[], behind_pr=True)[0] == cards.KEEP
assert verdict(machine(prs=OPEN_PRHEAD, heads={THEIRS: PRHEAD}), branch_prs=[{**PR, "state": "OPEN"}], terminals=[]) [0] == cards.KEEP, \
    "HEAD not behind the PR's head: its own work"
assert handed(status=" M f.py\n")[0] == cards.KEEP, "uncommitted work"
assert handed(ahead=HEAD + "\n")[0] == cards.KEEP, "a commit only here"
assert handed(terminals=[term("t1", True, 2 * 3600)])[0] == cards.CLOSE, "a terminal left: the terminal rule, not this one"
assert handed(m={"rows": {42: row(42, "python3 serve.py")}, "cwd_of": {42: CARD}})[0] == cards.KEEP, "a process in the card"
assert verdict(machine(prs=OPEN_PRHEAD, heads={CARD: PRHEAD}), branch_prs=[{**PR, "state": "OPEN"}], terminals=[], behind_pr=True)[0] == cards.KEEP, \
    "the card itself at the PR's head is no other card"

# F3: an agent screen that could not be read may hide a question: the card stays, whatever else says it is done.
v = verdict(machine(prs=MERGED), branch_prs=[PR], terminals=[term("a1", True, 2 * 3600, "UNREADABLE")])
assert v[0] == cards.KEEP and "화면을 읽지 못함" in v[1], v
v = verdict(machine(prs=OPEN), branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("a1", True, 2 * 3600, "UNREADABLE")])
assert v[0] == cards.KEEP, "not even its terminals close"
assert verdict(machine(prs=MERGED), branch_prs=[PR], terminals=[term("a1", True, 2 * 3600)])[0] == cards.REMOVE

# F2: a merged-PR card whose session only armed a wakeup (no shell child, transcript quiet) reads idle here; patrol
# judged it working, waiting or a Run worker, so it stays. reap.patrol_working feeds m["patrol"].
parked = lambda: dict(branch_prs=[PR], terminals=[term("a1", True, 2 * 3600)])
assert verdict(machine(prs=MERGED), **parked())[0] == cards.REMOVE
v = verdict(machine(prs=MERGED, patrol={CARD: "run"}), **parked())
assert v[0] == cards.KEEP and "orch patrol" in v[1] and "Run 워커" in v[1], v
# Class 4: a patrol verdict is a reading of a terminal. Once the card has none (the lead released it, the janitor closed
# it), a `working` or `run` verdict up to an hour old is the terminal's last word, not a live session: the rest of the
# rule decides. What patrol cannot tell (parked, unknown) or what waits on a human still keeps a card with no terminal.
bare = dict(branch_prs=[PR], terminals=[])
for state in ("working", "run"):
    assert verdict(machine(prs=MERGED, patrol={CARD: state}), **bare)[0] == cards.REMOVE, state
    assert verdict(machine(prs=MERGED, patrol={CARD: state}), **parked())[0] == cards.KEEP, "a terminal is still there"
for state in ("waiting", "parked", "unknown"):
    assert verdict(machine(prs=MERGED, patrol={CARD: state}), **bare)[0] == cards.KEEP, state
# ... and the rest of the rule still runs: a stale verdict never lifts a dirty tree, an unpushed commit or a live process.
assert verdict(machine(prs=MERGED, patrol={CARD: "run"}), branch_prs=[PR], terminals=[], status=" M f.py\n")[0] == cards.KEEP
assert verdict(machine(prs=MERGED, patrol={CARD: "run"}, rows={42: row(42, "python3 serve.py")}, cwd_of={42: CARD}), **bare)[0] == cards.KEEP
# patrol_states: a card both lists name keeps the stronger reason, parked or unknown, over working or run.
reap.STATE_old = reap.STATE
reap.STATE = pathlib.Path(tmp) / "state-merge"
reap.STATE.mkdir()
(reap.STATE / "patrol-verdicts.json").write_text(json.dumps(
    {"t": NOW, "cards": {CARD: "working"}, "activity": {CARD: {"state": "parked", "why": "x"}}}))
assert reap.patrol_states(NOW, activity=True) == {CARD: "parked"}
reap.STATE = reap.STATE_old
# read_machine takes m["patrol"] from patrol-verdicts.json itself, once per read.
stubs = {n: getattr(reap, n) for n in ("orca_workers", "processes", "cwds", "orca_json", "pr_reader", "transcript_quiet",
                                        "STATE", "patrol_states")}
reap.orca_workers, reap.processes, reap.cwds = lambda: [], lambda: {}, lambda: {}
reap.orca_json, reap.pr_reader, reap.transcript_quiet = lambda *a: {"automations": []}, lambda: {}.get, lambda *a, **k: None
reap.STATE = pathlib.Path(tmp) / "state"
reap.STATE.mkdir()
(reap.STATE / "patrol-verdicts.json").write_text(json.dumps({"t": NOW, "cards": {CARD: "waiting"}}))
reads = []
reap.patrol_states = lambda now=None, activity=False: reads.append((now, activity)) or stubs["patrol_states"](now, activity)
m = cards.read_machine({}, now=NOW)
assert m["patrol"] == {CARD: "waiting"} and reads == [(NOW, True)], (m["patrol"], reads)
assert cards.judge(cards.gather({"path": CARD}, {**m, "pr_state": MERGED.get}, fake(**parked())), IDLE)[0] == cards.KEEP
for n, f in stubs.items():
    setattr(reap, n, f)

# F5: scan and apply read the one patrol file the one way. A finished card with no terminal that patrol's v2 `activity`
# keeps (parked, unknown) is a keep in the scan too, with patrol's own word for it, so apply never meets a refusal the
# plan did not show. Shaped like the owner's 17: PR merged, nothing running, dispatch settled and released.
import cardguard  # noqa: E402
scanned = lambda m: {**m, "pr_state": MERGED.get, "quiet": lambda p: 3 * 3600}
stubs = {n: getattr(reap, n) for n in ("orca_workers", "processes", "cwds", "orca_json", "pr_reader", "transcript_quiet",
                                        "STATE")}
reap.orca_workers, reap.processes, reap.cwds = lambda: [], lambda: {}, lambda: {}
reap.orca_json, reap.pr_reader, reap.transcript_quiet = lambda *a: {"automations": []}, lambda: {}.get, lambda *a, **k: None
reap.STATE = pathlib.Path(tmp) / "state5"
reap.STATE.mkdir()
saved_workers = cardguard.worker_pages
cardguard.worker_pages = lambda *a, **k: []
for state in ("parked", "unknown"):
    (reap.STATE / "patrol-verdicts.json").write_text(json.dumps(
        {"t": NOW, "cards": {}, "activity": {CARD: {"state": state, "why": "x"}}}))
    m = cards.read_machine({}, now=NOW)
    scan_v = cards.judge(cards.gather({"path": CARD}, scanned(m), fake(branch_prs=[PR])), IDLE)
    guard_ok, guard_why = cardguard.check({"patrol": reap.patrol_states(NOW, activity=True).get(CARD), "head": HEAD,
                                           "terminals": [], "ino": None}, HEAD, set())
    assert not guard_ok, "the guard keeps this card"
    assert scan_v[0] == cards.KEEP, f"scan said {scan_v[0]} but apply's guard refuses: {guard_why}"
    assert state in scan_v[1] and scan_v[1] == "보호: " + guard_why, (scan_v, guard_why)
# A patrol file that cannot be read keeps every card in the scan, as it does in the guard.
(reap.STATE / "patrol-verdicts.json").write_text("{")
m = cards.read_machine({}, now=NOW)
v = cards.judge(cards.gather({"path": CARD}, scanned(m), fake(branch_prs=[PR])), IDLE)
assert v[0] == cards.KEEP and "읽지 못함" in v[1], v
# No file, or a round with the card in neither list: the card goes, and the guard agrees.
(reap.STATE / "patrol-verdicts.json").write_text(json.dumps({"t": NOW, "cards": {}, "activity": {}}))
m = cards.read_machine({}, now=NOW)
assert cards.judge(cards.gather({"path": CARD}, scanned(m), fake(branch_prs=[PR])), IDLE)[0] == cards.REMOVE
assert cardguard.check({"patrol": reap.patrol_states(NOW, activity=True).get(CARD, False), "head": HEAD, "terminals": [],
                        "ino": None}, HEAD, set())[0]
# A settled dispatch whose terminal nobody released is printed apart, with the Run that owns it and the command; an
# in_progress one still keeps the card and names its Run.
rows = [{"dispatchId": "ctx_a", "runId": "run_a", "terminalState": "reclaimable", "resource": {"worktreeId": "w::" + CARD},
         "projection": {"outcome": "succeeded"}},
        # No leak: retained (context-only or taken over), no resource left, another host's, a retained reason.
        {"dispatchId": "ctx_r", "runId": "run_a", "terminalState": "retained", "resource": {"worktreeId": "w::" + CARD},
         "projection": {"outcome": "succeeded"}},
        {"dispatchId": "ctx_n", "runId": "run_a", "terminalState": "release_unknown", "resource": None,
         "projection": {"outcome": "failed", "workspace": {"id": "w::" + CARD}}},
        {"dispatchId": "ctx_h", "runId": "run_a", "terminalState": "reclaimable", "resource": {"worktreeId": "w::" + CARD},
         "projection": {"outcome": "succeeded", "host": {"kind": "remote"}}},
        {"dispatchId": "ctx_k", "runId": "run_a", "terminalState": "reclaimable",
         "resource": {"worktreeId": "w::" + CARD, "retainedReason": "user"}, "projection": {"outcome": "succeeded"}},
        {"dispatchId": "ctx_b", "runId": "run_b", "terminalState": "released", "resource": {"worktreeId": "w::" + CARD},
         "projection": {"outcome": "succeeded"}},
        {"dispatchId": "ctx_c", "runId": "run_c", "terminalState": "retained", "resource": {"worktreeId": "w::/other"},
         "projection": {"outcome": "in_progress"}}]
assert reap.unreleased_settled(rows) == {CARD: [{"dispatch": "ctx_a", "run": "run_a", "outcome": "succeeded"}]}
assert "run_c" in reap.worker_worktrees(rows)[0][reap.real("/other")]
f = cards.gather({"path": CARD}, {**machine(prs=MERGED), "unreleased": reap.unreleased_settled(rows)}, fake(branch_prs=[PR]))
assert f["release"] == [{"dispatch": "ctx_a", "run": "run_a", "outcome": "succeeded"}]
out = cards.release_lines([{"name": "c1", "release": f["release"]}])
assert "c1" in out[0] and "run_a" in out[0] and "orca orchestration worker-release --dispatch ctx_a" in out[0], out
assert cards.release_lines([{"name": "c1", "release": []}]) == []
for n, fn in stubs.items():
    setattr(reap, n, fn)
cardguard.worker_pages = saved_workers

# Protected cards are never read further: the main checkout, a pinned card, a lead by name, this session's own card,
# an automation's runner, a live worker turn.
assert verdict(w={"isPinned": True})[0] == cards.KEEP
assert "lead-" in cards.judge(cards.gather({"path": CARD, "displayName": "lead-x"}, machine(), fake()), IDLE)[1]
assert "이 정리" in cards.judge(cards.gather({"path": CARD}, machine(self=CARD), fake()), IDLE)[1]
assert "runner" in cards.judge(cards.gather({"path": CARD}, machine(runners={CARD}), fake()), IDLE)[1]
assert "워커" in cards.judge(cards.gather({"path": CARD}, machine(live={CARD: "살아 있는 워커 턴(d1)"}), fake()), IDLE)[1]

# A failed gh lookup is a reason with gh's own words, never "no PR".
def gh_down(cmd, cwd=None, input=None, errors=None):
    if cmd[0] == "gh":
        errors is not None and errors.append("gh pr list: HTTP 401")
        return None
    return fake()(cmd, cwd=cwd, input=input, errors=errors)


errs = []
f = cards.gather({"path": CARD}, machine(), lambda c, cwd=None, input=None, errors=None: gh_down(c, cwd, input, errs if errors is None else errors))
v = cards.judge(f, IDLE)
assert v[0] == cards.KEEP and "PR 을 읽지 못함" in v[1], v

# apply acts only when the card reads the same as in the plan: a new commit or a changed verdict skips it.
import cardguard  # noqa: E402
reap.STATE = pathlib.Path(tmp) / "state"  # card locks
saved_observe = cardguard.observe
cardguard.observe = lambda path: {"ino": reap.dir_ino(path), "patrol": False, "head": HEAD, "terminals": [{"handle": "t1"}], "waits": {}, "screens": {}}
saved = cards.read_machine, cards.cards_listing, cards.gather, reap.run
closed = []
cards.read_machine = lambda cfg: machine(prs=OPEN)
cards.cards_listing = lambda repo_list=(): [{"path": CARD}]
cards.gather = lambda w, m: saved[2](w, m, fake(branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 2 * 3600)]))
reap.run = lambda cmd, **kw: closed.append(cmd) or "{}"
item = {"path": CARD, "verdict": cards.CLOSE, "head": HEAD, "close": ["t1"]}
assert cards.act(item, {}).startswith("터미널 1개 닫음") and closed[0][:4] == ["orca", "terminal", "close", "--terminal"]
closed.clear()
got = cards.act({**item, "head": PRHEAD}, {})
assert got.startswith("건너뜀") and "HEAD aaaaaaaa→bbbbbbbb" not in got and "HEAD bbbbbbbb→aaaaaaaa" in got and not closed, got
assert cards.act({**item, "verdict": cards.REMOVE}, {}).startswith("건너뜀") and not closed
# F4: a re-read that cannot see what scan needs keeps the card, before any terminal closes.
cards.cards_listing = lambda repo_list=(): None
assert cards.reread(CARD, {})[:2] == (cards.KEEP, cards.BLIND), "unreadable, not gone"
assert cards.act(item, {}).startswith("건너뜀") and not closed
cards.cards_listing = lambda repo_list=(): [{"path": CARD}]
for broken in ({"cwd_of": None}, {"workers_unread": True}, {"runners": None}):
    cards.read_machine = lambda cfg, b=broken: {**machine(prs=OPEN), **b}
    assert cards.reread(CARD, {})[:2] == (cards.KEEP, cards.BLIND), broken
    assert cards.act(item, {}).startswith("건너뜀") and not closed, broken
# F4: HEAD moving between the terminals closing and the removal stops the removal.
heads = iter([HEAD, PRHEAD])
cards.read_machine = lambda cfg: machine(prs=MERGED)
cards.gather = lambda w, m: {**saved[2](w, m, fake(branch_prs=[PR], terminals=[term("t1", True, 2 * 3600)])),
                             "head": next(heads)}
removed = []
saved_act, reap.act = reap.act, lambda *a, **k: removed.append(a) or "removed"
got = cards.act({**item, "verdict": cards.REMOVE}, {}, wait=0)
assert "HEAD 가 바뀜" in got and not removed and closed, got
reap.act = saved_act
# PR 3: the guard sees what the cleaner's own rule does not, and every close holds the card's lock.
closed.clear()
cards.read_machine = lambda cfg: machine(prs=OPEN)
cards.gather = lambda w, m: saved[2](w, m, fake(branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 2 * 3600)]))
cardguard.observe = lambda path: {"ino": reap.dir_ino(path), "patrol": False, "head": HEAD, "terminals": [{"handle": "t1", "agentIdentity": "claude"}],
                                  "waits": {"t1": {"evidence": "hook"}}, "screens": {"t1": ["❯"]}, "motion": {}}
got = cards.act(item, {})
assert got.startswith("건너뜀") and "사람의 답" in got and not closed, got
cardguard.observe = lambda path: {"ino": reap.dir_ino(path), "patrol": False, "head": HEAD, "terminals": [{"handle": "t1"}, {"handle": "t2"}]}
assert "새 터미널 t2" in cards.act(item, {}) and not closed
cardguard.observe = lambda path: {"ino": reap.dir_ino(path), "patrol": False, "head": HEAD, "terminals": [{"handle": "t1"}]}
# Another cleaner's process holds the card's lock (inside this process the same lock is re-entrant).
import guard, subprocess  # noqa: E402
lock = guard.lock_file("card", reap.real(CARD))
lock.parent.mkdir(parents=True, exist_ok=True)
holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys; f=open(sys.argv[1],'w'); fcntl.flock(f, fcntl.LOCK_EX); "
                           "print('held', flush=True); sys.stdin.read()", str(lock)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == "held"
assert cards.act(item, {}) == "건너뜀: 다른 정리가 이 카드를 처리 중" and not closed
holder.stdin.close()
holder.wait(10)
with cardguard.card_lock(CARD) as outer:
    assert outer and cards.act(item, {}).startswith("터미널 1개 닫음") and closed
closed.clear()
assert cards.act(item, {}).startswith("터미널 1개 닫음") and closed
cardguard.observe = saved_observe
cards.read_machine, cards.cards_listing, cards.gather, reap.run = saved

# apply's exit code follows reap's: a pre-act check that could not run leaves the card and the round ends 1 (after the
# others ran); a guard that decides to leave a card is a normal 0.
plan = pathlib.Path(tmp) / "plan.json"
plan.write_text(json.dumps({"items": [{"name": "c1", "path": CARD, "verdict": cards.REMOVE, "head": HEAD, "close": []}]}))
saved_keep, saved_own, saved_act, saved_argv = guard.keep, guard.ownership, cards.act, sys.argv
guard.ownership = lambda: None
cards.act = lambda *a, **k: "acted"
sys.argv = ["cards.py", "apply", "--plan", str(plan)]
for why, code in (("검사를 마치지 못함(PermissionError: x)", 1), ("보호 목록의 경로", 0), (None, 0)):
    guard.keep = lambda *a, w=why, **k: w
    try:
        cards.main(sys.argv)
    except SystemExit as e:
        assert e.code == code, (why, e.code)
    else:
        raise AssertionError("apply must exit")
guard.keep, guard.ownership, cards.act, sys.argv = saved_keep, saved_own, saved_act, saved_argv

# A restart abandons the Dispatches whose terminals it took; their cards wait for a resume, so the janitor must not
# act on them: the resumed session's terminal looks idle (it waits for its instruction), and closing it or reading
# its state as finished is what the recovery needs undone.
def dispatch(d, status, wt=CARD, **kw):
    return {"dispatchId": d, "runId": "run_a", "dispatchStatus": status, "terminalState": "retained",
            "resource": {"worktreeId": "w::" + wt}, "projection": {"outcome": "failed" if status == "abandoned" else "succeeded"}, **kw}


assert reap.recovering_worktrees([dispatch("d_new", "abandoned")]) == {CARD: "d_new"}
assert reap.recovering_worktrees([dispatch("d_int", "interrupted")]) == {CARD: "d_int"}
assert reap.recovering_worktrees([dispatch("d_ok", "completed")]) == {}
# worker-list is newest first: a later Dispatch on the card (the resume, or finished work) ends the wait.
assert reap.recovering_worktrees([dispatch("d_resume", "dispatched"), dispatch("d_old", "abandoned")]) == {}
assert reap.recovering_worktrees([dispatch("d_done", "completed"), dispatch("d_old", "abandoned")]) == {}
assert reap.recovering_worktrees([dispatch("d_other", "abandoned", wt="/other-card"), dispatch("d_ok", "completed")]) == \
    {reap.real("/other-card"): "d_other"}
# Orca may suffix the worktree id (`<repo>::<path>::workspace:<n>`): the card is the path alone.
assert reap.recovering_worktrees([dispatch("d_ws", "abandoned", wt=CARD + "::workspace:1")]) == {CARD: "d_ws"}
recovering = {"recovering": {CARD: "d_old"}}
v = verdict(machine(prs=OPEN, **recovering), branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 2 * 3600)])
assert v[0] == cards.KEEP and v[2] == [] and "d_old" in v[1], v  # an open PR: without the guard this closes t1
v = verdict(machine(prs=MERGED, **recovering), branch_prs=[PR], status=" M a.py\n")
assert v[0] == cards.KEEP and "d_old" in v[1], v
# Nothing left to lose once the PR merged and the tree is clean: the card is cleaned as before.
assert verdict(machine(prs=MERGED, **recovering), branch_prs=[PR])[0] == cards.REMOVE
# A card whose Dispatch is not waiting for a resume keeps the old verdict.
v = verdict(machine(prs=OPEN, recovering={}), branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 2 * 3600)])
assert v[0] == cards.CLOSE, v

# janitor integration: card is the scheduled kind, the old card kinds are not.
assert "card" in reap.SCHEDULED and "orca-worktree" not in reap.SCHEDULED and "done-card" not in reap.SCHEDULED
prompt = reap.schedule_prompt(pathlib.Path("/x/reap.py"), pathlib.Path("/s"))
assert "사람 결정" in prompt, prompt

print("ok")
