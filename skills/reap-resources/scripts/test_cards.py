"""Card verdicts of cards.py from fake git, gh and orca output. Run: python3 test_cards.py"""
import json, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cards, reap

NOW = 2_000_000_000.0
IDLE = 30 * 60
HEAD, PRHEAD = "a" * 40, "b" * 40
tmp = tempfile.mkdtemp()
CARD = reap.real(tmp)


def row(pid, cmd, ppid=1):
    return {"pid": pid, "ppid": ppid, "start": "Sat Sep 26 00:00:00 2026", "cmd": cmd}


def machine(rows=None, cwd_of=None, quiet=3 * 3600, prs=None, **kw):
    return {"now": NOW, "st": cards.settings({}), "rows": rows or {}, "cwd_of": cwd_of or {}, "live": {},
            "runners": set(), "self": None, "projects": pathlib.Path(tmp) / "projects",
            "pr_state": (prs or {}).get, "quiet": lambda p: quiet, **kw}


def term(handle, agent, silent_s, screen=None):
    return {"handle": handle, "agentIdentity": "claude" if agent else None, "lastOutputAt": (NOW - silent_s) * 1000,
            "_screen": screen}


def fake(branch_prs=(), terminals=(), local_landed=False, compare=None, ahead="", remote_log="", patches=None,
         status="", calls=None):
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
saved = cards.read_machine, cards.cards_listing, cards.gather, reap.run
closed = []
cards.read_machine = lambda cfg: machine(prs=OPEN)
cards.cards_listing = lambda repo_list=(): [{"path": CARD}]
cards.gather = lambda w, m: saved[2](w, m, fake(branch_prs=[{**PR, "state": "OPEN"}], terminals=[term("t1", True, 2 * 3600)]))
reap.run = lambda cmd, **kw: closed.append(cmd) or "{}"
item = {"path": CARD, "verdict": cards.CLOSE, "head": HEAD, "close": ["t1"]}
assert cards.act(item, {}).startswith("터미널 1개 닫음") and closed[0][:4] == ["orca", "terminal", "close", "--terminal"]
closed.clear()
assert cards.act({**item, "head": PRHEAD}, {}).startswith("건너뜀") and not closed
assert cards.act({**item, "verdict": cards.REMOVE}, {}).startswith("건너뜀") and not closed
cards.read_machine, cards.cards_listing, cards.gather, reap.run = saved

# janitor integration: card is the scheduled kind, the old card kinds are not.
assert "card" in reap.SCHEDULED and "orca-worktree" not in reap.SCHEDULED and "done-card" not in reap.SCHEDULED
prompt = reap.schedule_prompt(pathlib.Path("/x/reap.py"), pathlib.Path("/s"))
assert "사람 결정" in prompt, prompt

print("ok")
