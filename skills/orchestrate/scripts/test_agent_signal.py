"""agent_signal.py and what watch-cards and patrol do with it. Run: python3 test_agent_signal.py"""
import contextlib, fcntl, io, json, os, pathlib, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
tmp = tempfile.TemporaryDirectory()
os.environ["AGENT_SKILLS_STATE"] = tmp.name
os.environ["ORCH_FLEET_STATE"] = tmp.name
import agent_signal as a, card_watch, patrol, prog  # noqa: E402

LOG, LOCK = pathlib.Path(tmp.name) / "agent-signal.jsonl", pathlib.Path(tmp.name) / "agent-signal.lock"
a.LOG, a.LOCK = LOG, LOCK
assert LOG.parent == pathlib.Path(tmp.name)


def term(h, agent="claude", path=None):
    return {"handle": h, "worktreePath": path or f"/w/{h}", "agentIdentity": agent, "connected": True, "lastOutputAt": 0}


def row(sid, **kw):
    return {"sessionId": sid, "cwd": "/w/x", "kind": "interactive", "name": "n", "pid": 1, "startedAt": 0, **kw}


def proc(stdout="", code=0, stderr=""):
    return lambda *x, **k: subprocess.CompletedProcess(x, code, stdout, stderr)


def log():
    return [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []


# read: never raises; every failure is a problem, with stdout's head kept.
assert a.read(proc('[{"sessionId":"s"}]'))[:2] == ([{"sessionId": "s"}], None)
assert a.read(proc("", 1, "boom"))[1] == "exit 1: boom"
assert a.read(proc("nope")) == ([], "not JSON", "nope")
assert a.read(proc('{"sessions":[]}')) == ([], "not a JSON array", '{"sessions":[]}')
def gone(*x, **k):
    raise FileNotFoundError("claude")
assert a.read(gone)[1].startswith("FileNotFoundError")
def slow(*x, **k):
    raise subprocess.TimeoutExpired("claude", 10)
assert a.read(slow)[1].startswith("TimeoutExpired")

# The real roles.session_for: the SessionStart record is the only tie, and a row with the card's cwd is no match.
import roles  # noqa: E402
assert a.session_for is roles.session_for
started = 1_000_000  # ms
roles.record_terminal("term_a", "s-a", "/w/a", 2000.0)
roles.record_terminal("term_b", "s-b", "/w/b", 2000.0)
roles.record_terminal("term_c", "s-c", "/w/c", 1.0)
real = [row("s-a", status="busy", startedAt=started), row("s-b", state="blocked", kind="background", startedAt=started),
        row("s-c", status="idle", startedAt=started), {**row("s-z", status="idle"), "cwd": "/w/term_d"}, "x"]
got, warned = a.readings([term(h) for h in ("term_a", "term_b", "term_c", "term_d", "term_f")], real, None)
assert got["term_a"]["agents"] == "busy", got
assert not any(got[h]["agents"] for h in ("term_b", "term_c", "term_d", "term_f")), got
assert all(a.NO_SIGNAL in a.evidence(got[h]) for h in ("term_b", "term_c", "term_d", "term_f")), got

# From here a stand-in with the same contract: (row, None) or (None, why).
rec = {"h1": "s1", "h2": "s2", "h3": "s3", "h4": "s4", "h5": "gone"}


def session_for(handle, rows):
    if handle not in rec:
        return None, "no session record"
    row_ = next((r for r in rows if r.get("sessionId") == rec[handle]), None)
    return (row_, None) if row_ else (None, "not listed")


a.session_for = session_for
rows = [row("s1", status="busy"), row("s2", status="idle"), row("s3", state="blocked"),
        row("s4", status="waiting"), row("s6")]
terms = [term(h) for h in ("h1", "h2", "h3", "h4", "h5", "h9")] + [term("cx", "codex")]
reads, warned = a.readings(terms, rows, None)
assert (reads["h1"]["agents"], reads["h2"]["agents"], reads["h3"]["agents"]) == ("busy", "idle", "blocked")

# unknown value: raw passes through, no state settled, one warning.
assert reads["h4"] == {"agents": None, "raw": "waiting", "why": "unknown state waiting", "applies": True}, reads["h4"]
assert warned == ["claude agents: a state outside busy, idle, blocked; passed through"], warned
assert a.evidence(reads["h4"]) == "신호 없음 (unknown state waiting)"
assert a.evidence(reads["h9"]) == "신호 없음 (no session record)"
assert a.evidence(reads["h5"]) == "신호 없음 (not listed)"

# A missing field passes the raw row with exactly one warning line, however many rows lack it.
reads2, warned2 = a.readings([term("h1"), term("h2")], [row("s1"), row("s2")], None)
assert reads2["h1"]["agents"] is None and reads2["h1"]["why"] == "no status field", reads2
assert json.loads(reads2["h1"]["raw"])["sessionId"] == "s1", "the row itself is passed through"
assert warned2 == ["claude agents: a row has neither status nor state; passed through"], warned2
assert a.readings([], ["x", {"status": "idle"}], None)[1] == [
    "claude agents: a row is not an object; passed through", "claude agents: a row has no sessionId; passed through"]

# A state of the wrong type is unknown too, and a non-object row never reaches session_for.
for odd in (["busy"], {"a": 1}, 3, None):
    r_, _ = a.readings([term("h1")], [row("s1", status=odd), "x"], None)
    assert r_["h1"]["agents"] is None and r_["h1"]["why"].startswith("unknown state"), (odd, r_)

# A failed query reads as no signal on every Claude card, never as a state.
reads3, warned3 = a.readings([term("h1")], [], "not JSON")
assert reads3["h1"]["agents"] is None and a.evidence(reads3["h1"]) == "신호 없음 (claude agents failed: not JSON)"
assert warned3 == ["claude agents: not JSON; screen only this round"]

# Codex: screen only, and no gap.
assert reads["cx"]["applies"] is False and a.evidence(reads["cx"]) == "" and "cx" not in a.gaps(reads)
assert a.compare(reads["cx"], "idle")["differs"] == []
assert set(a.gaps(reads)) == {"h4", "h5", "h9"}

# compare: names the sides that disagree; unknown compares with nothing.
busy = {"agents": "busy", "raw": "busy", "why": None}
assert a.compare(busy, "idle")["differs"] == ["screen"]
assert a.compare({**busy, "agents": "idle"}, "busy")["differs"] == ["screen"]
assert a.compare(busy, "blocked", "a permission prompt")["differs"] == ["screen"]
assert a.compare(busy, "busy")["differs"] == [] and a.compare(busy, "unknown")["differs"] == []
assert a.compare({"agents": None, "why": "not listed"}, "idle")["differs"] == [], "no signal is no disagreement"
assert a.compare(busy, "unknown", transcript="idle", wait="none")["differs"] == ["transcript"]
assert a.compare(busy, "unknown", transcript="idle", wait="not looked")["differs"] == [], "a transcript cannot see a prompt"
assert a.compare(busy, "unknown", transcript="tool", wait="none")["differs"] == []
assert a.evidence({**busy, "applies": True}, "blocked") == "busy (screen blocked)"
assert a.evidence({**busy, "applies": True}, "busy") == "busy"

# screen_state: idle needs positive evidence.
spin = ["⏺ Running", "✻ Cogitating… (esc to interrupt)", "❯ "]
assert card_watch.screen_state(spin, 0) == ("busy", "spinner")
assert card_watch.screen_state(["done.", "❯ "], 5) == ("idle", "quiet prompt")
bg = ["⏺ text", "✻ Cooked for 41s", "─" * 20, "❯ ", "─" * 20, "  ⏵⏵ auto mode on · 1 shell · ← 1 agent"]
assert card_watch.screen_state(bg, 30) == ("busy", "background running"), "claude agents says busy for this turn-ended session"
assert card_watch.screen_state(["⏺ Both 2 shells have finished.", "❯ "], 5) == ("idle", "quiet prompt"), "a turn's words are not the status bar"
assert card_watch.screen_state(["done.", "❯ ", "  ⏵⏵ auto mode on · ← 1 agent"], 30) == ("idle", "quiet prompt"), \
    "the status line's agent count shows on sessions claude agents calls idle"
assert card_watch.screen_state(["done.", "❯ ", "  ⏵⏵ auto mode on · 1 monitor · ← 1 agent"], 30) == ("busy", "background running")
assert card_watch.screen_state(["done.", "❯ "], 0) == ("unknown", "no evidence"), "a prompt that just drew is no idle"
assert card_watch.FALLBACK_BACKGROUND.pattern == card_watch.BACKGROUND.pattern, "the copy for an install without scripts/ follows sync"
assert card_watch.screen_state(["❯ half typed"], 5) == ("unknown", "no evidence")
assert card_watch.screen_state([], 5) == ("unknown", "no evidence")
assert card_watch.screen_state(["❯ "], 5, "auth") == ("blocked", "auth")

# The log: a round row always, a card row on change, gone when a handle leaves, a baseline on a new file.
c1 = {"h1": a.compare(busy, "idle"), "h2": a.compare({"agents": "idle", "raw": "idle", "why": None}, "idle")}
m = a.log_round("watch-cards", "me", "r1", c1, None, names={"h1": "card-a"}, now=1)
got = log()
assert [r["kind"] for r in got] == ["round", "card", "card"], got
assert got[0]["differs"] == 1 and got[0]["terminals"] == 2 and got[0]["no_signal"] == 0, got[0]
assert got[1]["card"] == "card-a" and got[1]["screen"] == "idle" and got[1]["agents"] == "busy" and got[1]["differs"] == ["screen"]
m = a.log_round("watch-cards", "me", "r2", c1, m, now=2)
assert [r["kind"] for r in log()[3:]] == ["round"], "an unchanged card adds only the heartbeat"
c2 = {"h1": a.compare(busy, "busy")}
m = a.log_round("watch-cards", "me", "r3", c2, m, now=3)
assert [(r["kind"], r.get("handle")) for r in log()[4:]] == [("round", None), ("card", "h1"), ("gone", "h2")]

# Survives the JSON round trip of the state file: an unchanged card is still unchanged.
m = json.loads(json.dumps(m))
n = len(log())
m = a.log_round("watch-cards", "me", "r4", c2, m, now=4)
assert [r["kind"] for r in log()[n:]] == ["round"]

# A problem's round keeps the raw text and the warning.
m = a.log_round("watch-cards", "me", "r5", c2, m, "not JSON", "<html>", ["w"], now=5)
assert log()[-1]["raw"] == "<html>" and log()[-1]["problem"] == "not JSON" and log()[-1]["warnings"] == ["w"]

# Rotation: the writer that rotated, and any other, write a baseline into the new file.
a.ROTATE_BYTES, keep = 1, a.ROTATE_BYTES
other = a.log_round("patrol", None, "p0", c2, None, now=6)  # keeps the memo of a writer that has seen this file
assert pathlib.Path(f"{LOG}.1").exists() and [r["kind"] for r in log()] == ["round", "card"], "a new file opens on a baseline"
other = a.log_round("patrol", None, "p1", c2, other, now=6)
a.ROTATE_BYTES = keep
assert [r["kind"] for r in log()] == ["round", "card"], "the rotating writer writes its baseline into the new file"
m = a.log_round("watch-cards", "me", "r6", c2, m, now=7)
assert [r["source"] for r in log() if r["kind"] == "card"][-1] == "watch-cards", "another watcher rewrites its baseline"
other = a.log_round("patrol", None, "p2", c2, other, now=8)
assert log()[-1]["kind"] == "round" and log()[-1]["source"] == "patrol", "the rotating writer owes no second baseline"
assert a.log_round("x", None, "r", {}, {"gen": 1}, path="/dev/null/nope", lock="/dev/null/nope") == {"gen": 1}, \
    "a failed write costs the rows, never the round"

# The rows are chosen under the lock: a rotation by another writer just before this one still gets its baseline.
seen_gens = []
a.append(lambda gen: seen_gens.append(gen) or [{"kind": "round"}])
assert seen_gens == [os.stat(LOG).st_ino]
seen_gens.clear()
os.replace(LOG, f"{LOG}.moved")
a.append(lambda gen: seen_gens.append(gen) or [])
assert seen_gens == [None], "the inode is read under the lock, after any rotation"
os.replace(f"{LOG}.moved", LOG)

# A lock another process holds costs this round's rows, never the watcher.
a.LOCK_WAIT_S, wait = 0.2, a.LOCK_WAIT_S
with open(LOCK, "w") as held:
    fcntl.flock(held, fcntl.LOCK_EX)
    before = len(log())
    assert a.log_round("x", None, "r", c2, {"gen": 7}) == {"gen": 7}
    assert len(log()) == before
a.LOCK_WAIT_S = wait

# watch-cards end to end: findings and outputs unchanged by the signal; evidence line and PARTIAL added.
NOW = 10_000_000.0
listing = [term("me"), term("h1"), term("h2"), term("h3"), term("cx", "codex")]
for t in listing:
    t["lastOutputAt"] = NOW
menu = ["Which base branch should the PR target?", "❯ 1. main", "  2. develop", "Enter to select · ↑/↓ to navigate · Esc to cancel"]
idle = ["done.", "❯ "]
screens = {"h1": idle, "h2": idle, "h3": idle, "cx": idle}
prog.orca_json = lambda *x: {"terminals": listing}
prog.read_screen = lambda h: screens[h]
prog.agent_waits = lambda: {}


def watch(rows, problem=None, state=None):
    a.read = lambda **k: (rows, problem, "raw")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        card_watch.main(["--self", "me", "--min-free-gb", "0", "--state", str(state), "--once"])
    return out.getvalue().splitlines()


all_ok = [row("s1", status="idle"), row("s2", status="idle"), row("s3", status="idle")]
d = pathlib.Path(tmp.name)
assert watch(all_ok, state=d / "w1.json")[0].startswith("OK 4 agent terminals"), "Codex's blank is no gap"
assert watch(all_ok[:2], state=d / "w2.json")[0] == "PARTIAL 4 agent terminals, nothing new; 신호 없음 1: h3 (not listed)"
out = watch([], "not JSON", d / "w3.json")
assert out[0] == "PARTIAL 4 agent terminals, nothing new; 신호 없음: claude agents failed: not JSON", out
assert out[1] == "claude agents: not JSON; screen only this round" and len(out) == 2
listing[:] = [term("me"), term("cx", "codex")]
assert watch([], "exit 1", d / "w4.json")[0].startswith("PARTIAL 1 agent terminals"), "no card to judge is still not OK"
listing[:] = [term("h1"), term("h2")]
for t in listing:
    t["lastOutputAt"] = NOW
screens["h1"] = menu
out = watch([row("s1", status="busy"), row("s2", status="busy")], state=d / "w5.json")
assert out[:3] == ['STUCK h1 (a select menu): "Which base branch should the PR target?"', "  agents: busy (screen blocked)",
                   "  orca terminal read --terminal h1 --screen"], out
out = watch([row("s1", status="busy")], state=d / "w5b.json")
assert "PARTIAL 신호 없음 1: h2 (not listed)" in out, "a finding on one card does not hide the gap on another: " + str(out)
rec_rows = [r for r in log() if r.get("handle") == "h1" and r["kind"] == "card"]
assert rec_rows[-1]["differs"] == ["screen"] and rec_rows[-1]["finding"] == "a select menu", rec_rows[-1]
screens["h1"] = idle
out = watch([row("s1", status="busy"), row("s2", status="idle")], state=d / "w6.json")
last = [r for r in log() if r.get("handle") == "h1" and r["kind"] == "card"][-1]
assert (last["screen"], last["basis"], last["finding"], last["differs"]) == ("idle", "quiet prompt", "", ["screen"]), last
state_doc = json.loads((d / "w6.json").read_text())
assert state_doc["signals"]["cards"]["h1"]["agents"] == "busy" and "h1" not in {k for k in state_doc if k != "signals"}

# An Orca wait on a pane that cannot be read: reported, and still not a full view, also once the finding is deduped.
listing[:] = [term("h1")]
listing[0]["lastOutputAt"] = NOW
def unreadable(h):
    raise SystemExit("gone")
prog.read_screen = unreadable
prog.agent_waits = lambda: {"h1": {"prompt": "go?"}}
assert watch([row("s1", status="busy")], state=d / "w7.json")[0].startswith("STUCK h1 (waiting on a prompt")
assert watch([row("s1", status="busy")], state=d / "w7.json")[0].startswith("PARTIAL 1 agent terminals, nothing new; 화면 판정 없음 1: h1 (unreadable)")
prog.read_screen = lambda h: screens[h]
prog.agent_waits = lambda: {}

# A STUCK card is the same with and without a signal.
plain = card_watch.scan([term("h1")], {}, lambda h: menu, NOW, 30, 100.0, 15)
assert plain == ['STUCK h1 (a select menu): "Which base branch should the PR target?"', "  orca terminal read --terminal h1 --screen"]

# patrol: same judgement, signal beside it. A round's cards verdicts do not move with the signal.
now = 1_000_000.0


def facts(**over):
    base = {"now": now, "terminals": [], "waits": {}, "workers": [], "sessions": [], "prs": {}, "board": {}, "state": {},
            "supersedes": [], "level": "ok", "consumers": [], "tails": {}, "screens": {}, "watchers": {}, "timings": {},
            "failed": set(), "unread": set()}
    return {**base, **over}


def pround(f, rows, problem=None, memo=None, dry=False):
    f["agents_fn"] = lambda: (rows, problem, "raw")
    found, verdicts, memo2 = patrol.judge(f, memo or {})
    before = json.dumps(verdicts["cards"], sort_keys=True), [x["id"] for x in found]
    notes = []
    partial = patrol.signal_round(f, found, verdicts, memo or {}, memo2, notes, dry, "r")
    assert before == (json.dumps(verdicts["cards"], sort_keys=True), [x["id"] for x in found]), "the signal changes no judgement"
    return found, verdicts, memo2, notes, partial


t1 = {**term("h1"), "lastOutputAt": now * 1000}
since = "1970-01-01T00:00:00Z"
f = facts(terminals=[t1], waits={"h1": None}, screens={"h1": spin}, tails={"/w/h1": ({"state": "working", "since": since}, None)})
_, v, _, _, partial = pround(f, [row("s1", status="busy")])
c = v["signals"]["cards"]["h1"]
assert (c["screen"], c["agents"], c["transcript"], c["wait"], c["differs"]) == ("busy", "busy", "working", "none", [])
assert partial == "" and v["failed"] == [], "the signal stays out of failed"

# Not looked differs from none; a screen not read is not a failure.
f = facts(terminals=[t1], tails={"/w/h1": ({"state": "idle", "since": since}, None)})
_, v, _, _, partial = pround(f, [row("s1", status="busy")])
c = v["signals"]["cards"]["h1"]
assert (c["screen"], c["basis"], c["wait"], c["differs"]) == ("unknown", "not read", "not looked", []), c
assert partial == ""
f = facts(terminals=[t1], waits={"h1": None}, tails={"/w/h1": ({"state": "idle", "since": since}, None)})
c = pround(f, [row("s1", status="busy")])[1]["signals"]["cards"]["h1"]
assert c["differs"] == ["transcript"], c

# Orca's wait stands without a screen.
f = facts(terminals=[t1], waits={"h1": {"prompt": "go?"}})
c = pround(f, [row("s1", status="busy")])[1]["signals"]["cards"]["h1"]
assert (c["screen"], c["wait"], c["differs"]) == ("blocked", "found", ["screen"]), c

# What the round missed is PARTIAL: a failed source, an unreadable screen, a screen with no evidence, no signal.
assert "못 읽은 곳 1: waits" in pround(facts(terminals=[t1], failed={"waits"}), [row("s1", status="busy")])[4]
assert "못 읽은 곳 1: h1" in pround(facts(terminals=[t1], unread={"h1"}), [row("s1", status="busy")])[4]
assert "screens cut short" in pround(facts(terminals=[t1], cut_short=True), [row("s1", status="busy")])[4]
assert "못 읽은 곳 1: h1" in pround(facts(terminals=[t1], screens={"h1": ["⏺ text"]}), [row("s1", status="busy")])[4]
assert pround(facts(terminals=[t1]), [], "not JSON")[4] == "신호 없음: claude agents failed: not JSON"
assert pround(facts(terminals=[]), [], "exit 1")[4] == "신호 없음: claude agents failed: exit 1", "no card to judge is still not OK"
assert pround(facts(terminals=[t1]), [])[4] == "신호 없음 1: h1 (not listed)"
cx = term("cx", "codex")
assert pround(facts(terminals=[cx], screens={"cx": ["⏺ text"]}), [])[4] == "", "Codex: screen only, and no gap"
assert pround(facts(terminals=[t1]), [row("s1")])[3] == ["claude agents: a row has neither status nor state; passed through"]

# Too little budget left: no query, so the alarm cannot cost the verdicts already judged.
assert patrol.agents_read(100, read=lambda: 1 / 0, clock=lambda: 90)[1].startswith("skipped"), "under the margin it does not ask"
assert patrol.agents_read(100, read=lambda: "asked", clock=lambda: 80) == "asked"

# report: OK only when nothing was missed, also when there are findings to show.
res = {"at": "t", "seconds": 1, "terminals": 1, "workers": 0, "screens": 0, "level": "ok", "findings": [], "new": [],
       "cleared": [], "verdicts": {"cards": {}}, "notes": [], "dry_run": False}
assert patrol.report({**res, "partial": ""})[-1] == "OK nothing found"
assert patrol.report({**res, "partial": "신호 없음 1: h1 (not listed)"})[-1] == "PARTIAL nothing found; 신호 없음 1: h1 (not listed)"

# A finding's title ends in the signal's word; its id does not change.
f = facts(terminals=[t1], waits={"h1": None}, tails={"/w/h1": ({"state": "idle", "since": since, "parked": False}, None)},
          read=lambda h: ["❯ "])
found = pround(f, [])[0]
assert found and found[0]["title"].endswith("· agents: 신호 없음 (not listed)"), found

# dry-run writes nothing.
n = len(log())
pround(facts(terminals=[t1]), [row("s1", status="busy")], dry=True)
assert len(log()) == n
pround(facts(terminals=[t1]), [row("s1", status="busy")])
assert len(log()) > n and log()[-1]["source"] == "patrol"
print("ok")
