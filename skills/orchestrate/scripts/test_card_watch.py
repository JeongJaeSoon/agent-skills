"""card_watch.py: which screens wake the orchestrator or lead, and which do not twice. Run: python3 test_card_watch.py"""
import contextlib, io, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import agent_signal, card_watch, prog

SIGNAL_DIR = tempfile.TemporaryDirectory()
agent_signal.LOG = pathlib.Path(SIGNAL_DIR.name) / "agent-signal.jsonl"
agent_signal.LOCK = pathlib.Path(SIGNAL_DIR.name) / "agent-signal.lock"
agent_signal.read = lambda **k: ([{"sessionId": f"s-{h}", "kind": "interactive", "status": "idle"}
                                  for h in ("me", "h1", "h2", "h3", "h4")], None, "[]")
agent_signal.session_for = lambda h, rows: (next((r for r in rows if r["sessionId"] == f"s-{h}"), None), None)

NOW = 10_000_000.0
menu = ["Which base branch should the PR target?", "❯ 1. main", "  2. develop", "Enter to select · ↑/↓ to navigate · Esc to cancel"]
perm = ["Bash command", "  rm -rf build", "Do you want to proceed?", "❯ 1. Yes", "  2. No, and tell Claude what to do differently (esc)"]
retry = ["⏺ Running tests", "✻ Waiting for API response · will retry in 3m 39s · check your network"]
quoted = ["⏺ Near the limit several cards stop at 「Waiting for API response」 together.", "❯ "]
goal = ["⏺ Report sent.", "❯ ", "                                     ◎ /goal active (2h)"]
idle = ["done.", "❯ ", "  ⏵⏵ auto mode on"]


def term(h, path, last=NOW, agent="claude"):
    return {"handle": h, "worktreePath": path, "lastOutputAt": last, "agentIdentity": agent, "connected": True}


listing = [term("me", "/w/me"), term("h1", "/w/card-a"), term("h2", "/w/card-b"), term("sh", "/w/card-a", agent=None),
           term("h3", "/w/card-c", last=NOW - 31 * 60000), term("h4", "/w/card-d", last=NOW - 5 * 60000)]
terms = card_watch.agent_terminals(listing, "me")
assert [t["handle"] for t in terms] == ["h1", "h2", "h3", "h4"], "this session and plain shells are not watched"
screens = {"h1": menu, "h2": idle, "h3": goal, "h4": goal}
read = lambda h: screens[h]
scan = lambda seen, free=100.0: card_watch.scan(terms, seen, read, NOW, 30, free, 15)

seen = {}
first = scan(seen)
assert first[0] == 'STUCK card-a (a select menu): "Which base branch should the PR target?"', first
assert "  orca terminal read --terminal h1 --screen" in first
assert any(l.startswith("STUCK card-c (a /goal card silent 31 min)") for l in first), first
assert not any("card-b" in l or "card-d" in l for l in first), "an idle prompt and a /goal card that spoke 5 min ago are fine"
assert scan(seen) == [], "a finding still on screen is not reported again"
screens["h1"] = menu + ["  Session: 23hr 4m"]
assert scan(seen) == [], "a ticking status bar is the same prompt"

screens["h1"] = perm
assert scan(seen)[0].startswith("STUCK card-a (a permission prompt)"), "a new prompt on the same card is new"
screens["h1"] = idle
scan(seen)
screens["h1"] = perm
assert scan(seen), "a prompt that cleared and came back is new"

screens = {"h1": retry, "h2": quoted, "h3": idle, "h4": idle}
assert scan({}) == ["STUCK card-a (an API retry loop): read its screen", "  orca terminal read --terminal h1 --screen"], \
    "a card quoting the retry text is not retrying"
screens = {"h1": retry, "h2": retry, "h3": retry, "h4": idle}
grouped = scan({})
assert len(grouped) == 1 and grouped[0].startswith("STUCK 3 cards in an API retry loop at once"), grouped

screens = {"h1": ["npm ERR! ENOSPC: no space left", "❯ "], "h2": idle, "h3": idle, "h4": idle}
assert scan({})[0] == "STUCK card-a (a full disk): read its screen"

seen = {}
low = scan(seen, free=9.5)
assert low[-1] == "STUCK disk (free 9.5 GiB, under 15): run reap-resources", low
assert scan(seen, free=9.0) == [] and seen["disk"] == "low", "low disk is reported once while it lasts"
scan(seen, free=40)
assert "disk" not in seen

# A lost login or a goal Claude Code stopped is STUCK (auth) once the card is quiet, whatever glyph the line opens with.
terms = card_watch.agent_terminals([term(h, f"/w/card-{c}", last=NOW - 3 * 60000) for h, c in zip(("h1", "h2", "h3", "h4"), "abcd")], "me")
dead403 = ["⏺ Bash(gh pr view 1)", "  ⎿  Please run /login · API Error: 403 Forbidden", "─" * 40, "❯", "─" * 40,
           "  ⏵⏵ auto mode on · 1 shell", "  ⏺ main", "  ◯ general-purpose  reading notes"]  # the status area lists agents with ⏺
cleared = ["⏺ Running tests", '⚠ Goal cleared after an unrecoverable error (authentication failed): "ship it". Run /goal again to continue.', "❯ "]
paused = ["⏺ Report sent.", "Goal paused · usage limit reached · send a message after it resets", "❯ "]
expired = ["⏺ Login expired · Please run /login", "❯ "]
screens = {"h1": dead403, "h2": cleared, "h3": paused, "h4": expired}
seen = {}
found = scan(seen)
assert found[0] == 'STUCK card-a (auth): "Please run /login · API Error: 403 Forbidden"', found
assert found[2].startswith('STUCK card-b (auth): "Goal cleared after an unrecoverable error (authentication failed)'), found
assert found[4] == 'STUCK card-c (auth): "Goal paused · usage limit reached · send a message after it resets"', found
assert found[6] == 'STUCK card-d (auth): "Login expired · Please run /login"', found
assert scan(seen) == [], "an auth screen still showing is not reported again"

# Quoting the text is not hitting it: mid-sentence, in a later turn, or a session that went on after it.
mention = ["⏺ The lead stopped on 「Please run /login · API Error: 403」, so I restarted it.", "❯ "]
said = ["⏺ Cards hit API Error: 403 and Goal paused · usage limit yesterday.", "❯ "]
recovered = ["  ⎿  Please run /login · API Error: 403 Forbidden", "> /login", "⏺ Login successful. Continuing.", "❯ "]
listed = ["⏺ Three screens mean the login is gone:", "  - Please run /login · API Error: 403 Forbidden",
          "  ⎿  「Please run /login」 was on screen", "❯"]
screens = {"h1": mention, "h2": said, "h3": recovered, "h4": listed}
assert scan({}) == [], scan({})
ticked = ["⏺ Notes:", "  `Please run /login · API Error: 403` is the text to watch", "> Goal paused · usage limit reached", "❯"]
running = ["⏺ Bash(grep -rn 'Please run /login' logs/)", "  ⎿  Please run /login · API Error: 403 Forbidden",
           "✶ Grepping… (12s · esc to interrupt)", "❯"]  # tool output under ⎿ reads like a real 403
screens = {"h1": ticked, "h2": running, "h3": idle, "h4": idle}
assert scan({}) == [], "a backtick or > quote, and a turn still running, are not a lost login"
cooked_below = ["⏺ Bash(gh pr view 1)", "  ⎿  Please run /login · API Error: 403 Forbidden", "✻ Cooked for 41s", "❯"]
cooked_above = ["✻ Worked for 2m 3s", "⏺ Bash(gh pr view 1)", "  ⎿  Please run /login · API Error: 403 Forbidden", "❯"]
screens = {"h1": cooked_below, "h2": cooked_above, "h3": idle, "h4": idle}
assert [l for l in scan({}) if l.startswith("STUCK")] == ['STUCK card-a (auth): "Please run /login · API Error: 403 Forbidden"',
                                                        'STUCK card-b (auth): "Please run /login · API Error: 403 Forbidden"'], \
    "a finished turn's summary line is not a live spinner"
terms = card_watch.agent_terminals([term("h1", "/w/card-a", last=NOW - 60000)], "me")
screens = {"h1": dead403}
assert scan({}) == [], "a card that wrote output a minute ago is still working"

# A card once seen under /goal that sits on an empty prompt without it has lost its goal.
lost = ["⏺ Report sent.", "❯ ", "  ⏵⏵ auto mode on"]
terms = card_watch.agent_terminals([term("h1", "/w/card-a", last=NOW - 16 * 60000), term("h2", "/w/card-b", last=NOW - 16 * 60000),
                                    term("h3", "/w/card-c", last=NOW - 5 * 60000)], "me")
screens = {"h1": goal, "h2": idle, "h3": goal}
seen = {}
assert scan(seen) == [] and seen["goals"] == ["h1", "h3"], seen
screens = {"h1": lost, "h2": idle, "h3": lost}
found = scan(seen)
assert found == ["STUCK card-a (goal lost, idle 16 min): read its screen", "  orca terminal read --terminal h1 --screen"], \
    "a session never under /goal, and a goal card idle 5 min, are fine"
assert scan(seen) == [] and seen["goals"] == ["h1", "h3"], "reported once; the card stays known"
screens["h1"] = ["⏺ Working", "❯ draft text", ""]
assert scan(seen) == [], "text in the input box is not an empty prompt"
terms = terms[1:]
scan(seen)
assert seen["goals"] == ["h3"], "a card that left the listing is forgotten"
terms = card_watch.agent_terminals([term("h1", "/w/card-a", last=NOW - 16 * 60000)], "me")
assert scan({"h1": "x"}) == [], "a state file from before goals were kept loads as none"

# --card judges a new card's start at the end of the window.
class Clock:
    t = NOW / 1000
    def __call__(self): return self.t
    def sleep(self, s): self.t += s
def start(screen, out_at, seen_term=True):
    """screen and seen_term may be functions of the seconds since the check began: the card changes in the window."""
    clock = Clock()
    t0, t = clock.t, term("hn", "/w/card-n", last=out_at(clock))
    at = lambda v: v(clock.t - t0) if callable(v) else v
    return card_watch.check_start("hn", lambda h: at(screen), lambda: [t] if at(seen_term) else [], 240, 30, clock, clock.sleep)
later = lambda c: (c.t + 200) * 1000
assert start(goal, later) == ["STARTED card-n: /goal active, output since the check began"]
assert start(goal, lambda c: c.t * 1000 - 1)[0].startswith("STUCK card-n (not started)"), "/goal active but silent since create"
shell = ["Last login: Thu", "user@host card-n % /goal ship it"]
assert start(shell, later)[0] == 'STUCK card-n (not started): no /goal active with new output; last line "user@host card-n % /goal ship it"'
assert start(dead403, lambda c: c.t * 1000 - 3 * 60000)[0] == 'STUCK card-n (auth): "Please run /login · API Error: 403 Forbidden"', \
    "a finding ends the check early"
done = ["⏺ PR opened and reported.", "❯"]
assert start(lambda s: goal if s < 120 else done, later)[0].startswith("ENDED card-n: /goal was active and is off now"), \
    "a short task that met its goal inside the window ended; it did not fail to start"
assert start(lambda s: goal if s < 120 else cleared, later)[0].startswith('STUCK card-n (auth): "Goal cleared after'), \
    "a goal a 403 cleared under two minutes before the deadline is not ENDED"
assert start(goal, later, seen_term=lambda s: s < 120)[0].startswith("CLOSED card-n: its terminal is gone"), \
    "a card that closed itself inside the window is not a failed start"
assert start(goal, later, seen_term=False) == ["STUCK hn (not started): no agent terminal with this handle"]

# The watch exits on a finding and remembers it across restarts; a terminal it cannot read is skipped.
screens = {"h1": menu, "h2": idle, "h3": idle, "h4": idle}
def read_or_gone(h):
    if h == "h2":
        raise SystemExit("terminal gone")
    return screens[h]
prog.orca_json = lambda *a: {"terminals": listing}
prog.read_screen = read_or_gone
with tempfile.TemporaryDirectory() as d:
    state = pathlib.Path(d) / "s.json"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        card_watch.main(["--self", "me", "--min-free-gb", "0", "--state", str(state), "--once"])
    assert out.getvalue().startswith("STUCK card-a (a select menu)"), out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        card_watch.main(["--self", "me", "--min-free-gb", "0", "--state", str(state), "--once"])
    assert out.getvalue().startswith("PARTIAL 4 agent terminals, nothing new; 화면 판정 없음 1: card-b (unreadable)"), \
        "a screen it could not read is not OK, even with a busy-or-idle signal for that card: " + out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        card_watch.main(["--self", "me", "--min-free-gb", "0", "--state", str(state), "--interval", "1", "--timeout-ms", "500"])
    assert out.getvalue().startswith("QUIET nothing new; start the watch again; 화면 판정 없음 1"), out.getvalue()

    # Claude Code's `waiting` (a dialog open) is classified, and a state nobody classified only adds a warning line:
    # neither ends the watch before its timeout.
    real_read = agent_signal.read
    for status, warn in (("waiting", False), ("hibernating", True)):
        agent_signal.read = lambda status=status, **k: (
            [{"sessionId": f"s-{h}", "kind": "interactive", "status": status, "waitingFor": "dialog open"}
             for h in ("h1", "h2", "h3", "h4")], None, "")
        screens = {"h1": idle, "h2": idle, "h3": idle, "h4": idle}
        prog.read_screen = lambda h: screens[h]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            card_watch.main(["--self", "me", "--min-free-gb", "0", "--state", str(pathlib.Path(d) / f"w-{status}.json"),
                             "--interval", "1", "--timeout-ms", "500"])
        assert out.getvalue().startswith("QUIET nothing new; start the watch again"), (status, out.getvalue())
        assert ("a state outside busy, idle, blocked" in out.getvalue()) == warn, (status, out.getvalue())
    agent_signal.read = real_read

    # Without --self the watch skips the terminal named by ORCA_TERMINAL_HANDLE, so it never reports its own session.
    screens = {"me": menu, "h1": idle, "h2": idle, "h3": idle, "h4": idle}
    prog.read_screen = lambda h: screens[h]
    for own, want in (("me", "OK 4 agent terminals"), ("", "STUCK me (a select menu)")):
        os.environ["ORCA_TERMINAL_HANDLE"] = own
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            card_watch.main(["--min-free-gb", "0", "--state", str(pathlib.Path(d) / f"self-{own}.json"), "--once"])
        assert out.getvalue().startswith(want), (own, out.getvalue())

# Every agent terminal, a Run's worker or not, is judged by Orca's agentWait: a menu Orca saw no wait in is not
# reported, a wait Orca found is, and a terminal Orca never looked at falls back to the screen. The auth, goal and
# disk checks Orca does not make still read the screen.
screens = {"h1": menu, "h2": idle, "h3": idle, "h4": idle}
w_scan = lambda waits: card_watch.scan(terms, {}, read, NOW, 30, 100.0, 15, waits=waits)
assert w_scan({"h1": None}) == [], "Orca looked and found no human-only prompt"
assert w_scan({"h1": {"evidence": "prompt-text", "prompt": "Which base?"}})[0].startswith(
    'STUCK card-a (waiting on a prompt (Orca agentWait, prompt-text)): "Which base?"')
assert w_scan({})[0].startswith("STUCK card-a (a select menu)"), "no verdict for h1: the screen decides"
screens["h1"] = ["Waiting for API response · will retry in 30s", "❯ "]
assert w_scan({"h1": None})[0].startswith("STUCK card-a (an API retry loop)"), "Orca does not judge API retries"
screens["h1"] = idle


def unreadable(h):
    if h == "h1":
        raise SystemExit("orca terminal read: pane gone")
    return screens[h]


# A screen that cannot be read still reports the wait Orca already found; with no wait from Orca it says nothing.
waited = {"h1": {"evidence": "prompt-text", "prompt": "Which base?"}}
seen = {}
lost = card_watch.scan(terms, seen, unreadable, NOW, 30, 100.0, 15, waits=waited)
assert lost == ['STUCK card-a (waiting on a prompt (Orca agentWait, prompt-text)): "Which base?"',
                "  orca terminal read --terminal h1 --screen"], lost
assert card_watch.scan(terms, seen, unreadable, NOW, 30, 100.0, 15, waits=waited) == [], "reported once while it lasts"
assert card_watch.scan(terms, {}, unreadable, NOW, 30, 100.0, 15, waits={}) == []
assert card_watch.scan(terms, {}, unreadable, NOW, 30, 100.0, 15, waits={"h1": None}) == []

# A design doc's diff on screen quotes ENOSPC; the same text from a command is a real full disk.
terms = card_watch.agent_terminals(listing, "me")
read = lambda h: screens[h]
diff = ["⏺ Update(docs/design.md)", "  ⎿  Added 2 lines", "     41 +- ENOSPC (a full disk) is caught by the watcher",
        "     42      No space left on device is the second spelling", "❯ "]
screens = {"h1": diff, "h2": idle, "h3": idle, "h4": idle}
assert scan({}) == [], "ENOSPC inside a drawn diff is not a full disk"
screens["h1"] = diff + ["npm ERR! ENOSPC: no space left on device, write", "❯ "]
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "a real ENOSPC beside a diff still counts"
screens["h1"] = ["⏺ Bash(make)", "  ⎿  28  No space left on device", "❯ "]
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "numbered command output with no +/- line is not a diff"
screens["h1"] = ["⏺ Bash(build)", "  ⎿  27 - writing output", "     28  No space left on device", "❯ "]
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "numbered output beside a '-' line, no diff header"
screens["h1"] = ["⏺ Update(src/errors.ts)", "  ⎿  Added 1 line", '     41 +  const m = "the filesystem returned',
                  '       ENOSPC: No space left on device";', "❯ "]
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "known limit: a wrapped diff line is read as output"
for after in ("npm ERR! ENOSPC: no space left on device, write", "     npm ERR! ENOSPC: no space left on device, write"):
    screens["h1"] = ["⏺ Update(src/main.py)", "  ⎿  Added 1 line", "     41 +  enabled = True", after, "❯ "]
    assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "output right after a diff is not part of it"
screens["h1"] = ["⏺ Update(src/main.py)", "     41 +  x = 1", "✻ Waiting for API response · will retry in 3m", "❯ "]
assert scan({})[0] == "STUCK card-a (an API retry loop): read its screen", "a retry line after a diff still counts"
screens["h1"] = ["npm ERR! ENOSPC: no space left on device, write", "⏺ Disk space recovered.", "⏺ Update(src/app.ts)",
                  *[f"     {i} +  line {i}" for i in range(1, 31)], "❯ "]
assert scan({}) == [], "a message that scrolled past the 25-line tail is not read again after a long diff"
screens["h1"] = ["⏺ Update(src/errors.ts)", "     41 +  x = 1", "⏺ Bash(make)", "  ⎿  npm ERR! ENOSPC: no space left", "❯ "]
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "the next turn after a diff is not part of it"
screens["h1"] = ["⏺ Update(src/errors.ts)", "  ⎿  Added 2 lines", '     41 +  const m = "a long ordinary message',
                  '       continues here";', '     42 +  const d = "ENOSPC";', "❯ "]
assert scan({}) == [], "a wrapped tail leaves the diff header standing for the lines after it"
screens["h1"] = ["⏺ Update(src/errors.ts)", "  ⎿  Added 1 line", "     41 +  x = 1", "  ⎿  1 - writing output",
                  "     2  No space left on device", "❯ "]
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "a new ⎿ result after a diff starts its own block"
long_build = ["⏺ Bash(build)"] + [f"     {i} - writing output" for i in range(1, 25)] + ["     25  No space left on device", "❯ "]
screens["h1"] = long_build
assert scan({})[0] == "STUCK card-a (a full disk): read its screen", "a long numbered log pushes its header out of the tail"
screens["h1"] = ["⏺ Update(docs/d.md)"] + [f"     {i} +  line {i}" for i in range(1, 30)] + ["     30 +  ENOSPC quoted", "❯ "]
assert scan({}) == [], "a diff longer than the tail is still dropped when the screen holds its header"

# A /goal card silent past --idle-min is stuck unless it is busy: a background shell, or claude agents says busy.
shell = ["⏺ Waiting on the build.", "❯ ", "  ⏵⏵ auto mode on · 1 shell still running", "  ◎ /goal active (2h)"]
screens = {"h1": idle, "h2": idle, "h3": shell, "h4": idle}
assert scan({}) == [], "a silent card with a shell running is busy"
screens["h3"] = shell[:2] + shell[3:]
assert scan({})[0].startswith("STUCK card-c (a /goal card silent 31 min)"), "the shell line is what suppresses it"
screens["h3"] = goal
assert scan({})[0].startswith("STUCK card-c (a /goal card silent 31 min)"), "silent and nothing running is stuck"
assert card_watch.scan(terms, {}, read, NOW, 30, 100.0, 15, agents={"h3": {"agents": "busy"}}) == [], \
    "claude agents busy suppresses the silent finding"
assert card_watch.scan(terms, {}, read, NOW, 30, 100.0, 15, agents={"h3": {"agents": "idle"}})[0].startswith("STUCK card-c")

# --handle keeps the watch on its own cards: a finding on any other card cannot end it.
screens = {"h1": menu, "h2": idle, "h3": idle, "h4": idle}
assert [t["handle"] for t in card_watch.agent_terminals(listing, "me", ["h2", "h4"])] == ["h2", "h4"]
assert [t["handle"] for t in card_watch.agent_terminals(listing, "me", ["me", "sh"])] == [], "self and plain shells stay out"
mine = card_watch.agent_terminals(listing, "me", ["h2", "h4"])
assert card_watch.scan(mine, {}, read, NOW, 30, 100.0, 15) == [], "h1's menu is outside the filter"
assert card_watch.scan(card_watch.agent_terminals(listing, "me", ["h1"]), {}, read, NOW, 30, 100.0, 15)[0].startswith("STUCK card-a")

# A card whose start helper failed (close-setup, the /goal input) is SETUP FAILED while its screen looks idle.
SETUP = tempfile.TemporaryDirectory()
card_watch.SETUP_STATE = pathlib.Path(SETUP.name)
def helper_log(helper, card, *lines):
    path = pathlib.Path(SETUP.name) / helper / f"{card}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(l + "\n" for l in lines))
terms = card_watch.agent_terminals([term(h, f"/w/card-{c}", last=NOW - 60000) for h, c in zip(("h1", "h2", "h3", "h4"), "abcd")], "me")
screens = {"h1": idle, "h2": idle, "h3": idle, "h4": idle}
helper_log("close-setup", "card-a", "term_x  agent needs input: a folder trust dialog", "result: failed")
helper_log("goal-input", "card-b", "[skills-sync card] warning: the /goal line was not typed into h2 (waited up to 900s). Once the session is idle, run: orca terminal send --terminal h2 --text '/goal x' --enter")
helper_log("close-setup", "card-c", "term_y  closed", "result: ok")
helper_log("goal-input", "card-c", "[skills-sync card] typed the /goal line into h3: /goal x")
seen = {}
found = scan(seen)
assert found[0] == "SETUP FAILED card-a: close-setup (term_x  agent needs input: a folder trust dialog)", found
assert found[1] == "  skills-sync close-setup /w/card-a --agent h1   # then: orca terminal read --terminal h1 --screen", found
assert found[2].startswith("SETUP FAILED card-b: goal-input (the /goal line was not typed into h2"), found
assert "orca terminal send --terminal h2 --text" in found[3], found
assert len(found) == 4, "a result: ok log and a typed /goal line are not failures; no log is none"
assert scan(seen) == [], "a failure still logged is not reported again"
helper_log("goal-input", "card-b", "[skills-sync card] warning: the /goal line was not typed into h2 (waited up to 900s).",
           "[skills-sync card] typed the /goal line into h2: /goal x")
screens["h1"] = menu
again = scan(seen)
assert again[0].startswith("STUCK card-a (a select menu)") and not any("card-b" in l for l in again), again

# The helper's verdict prefix decides, not the goal text it quotes; an unconfirmed send ends the "not typed" report.
helper_log("goal-input", "card-b", "[skills-sync card] typed the /goal line into h2: /goal why the /goal line was not typed")
assert card_watch.setup_failure("card-b") is None, "a quoted goal text is not a verdict"
helper_log("goal-input", "card-b", "[skills-sync card] warning: the /goal line was not typed into h2 (waited up to 900s).",
           "[skills-sync card] warning: the /goal line may have reached h2, but its screen does not show it. Read the screen first: x")
assert card_watch.setup_failure("card-b") is None, "the newest verdict says it may have been typed"

# An unreadable pane neither hides a failure nor makes it come back after the pane reads again.
helper_log("close-setup", "card-a", "term_x  agent needs input: a folder trust dialog", "result: failed")
def flaky(h):
    if flaky.mode == "bad":
        raise SystemExit(1)
    return idle
terms = card_watch.agent_terminals([term("h1", "/w/card-a", last=NOW - 60000)], "me")
seen = {}
flaky.mode = "bad"
assert card_watch.scan(terms, seen, flaky, NOW, 30, 100.0, 15)[0].startswith("SETUP FAILED card-a"), "reported with no screen"
flaky.mode = "ok"
assert card_watch.scan(terms, seen, flaky, NOW, 30, 100.0, 15) == [], "the same failure after the pane reads again"
flaky.mode = "bad"
assert card_watch.scan(terms, seen, flaky, NOW, 30, 100.0, 15) == [], "and again when it stops reading"
print("ok")

