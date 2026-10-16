"""card_watch.py: which screens wake the coordinator, and which do not twice. Run: python3 test_card_watch.py"""
import contextlib, io, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import card_watch, prog

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
    assert out.getvalue().startswith("OK 4 agent terminals"), out.getvalue()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        card_watch.main(["--self", "me", "--min-free-gb", "0", "--state", str(state), "--interval", "1", "--timeout-ms", "500"])
    assert out.getvalue().startswith("QUIET"), out.getvalue()
print("ok")
