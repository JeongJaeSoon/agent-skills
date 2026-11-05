"""turn_cost.py: turns, kinds and the --what-if replay on a small synthetic transcript; no real transcript is read.

Run: python3 test_turn_cost.py
"""
import json, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import turn_cost as tc  # noqa: E402

N = [0]


def user(ts, text, origin=None):
    return {"type": "user", "timestamp": ts, "message": {"content": text}, **({"turnOrigin": origin} if origin else {})}


def call(ts, ctx, tools=(), out=100):
    N[0] += 1
    content = [{"type": "tool_use", "name": n, "input": {"command" if n == "Bash" else "file_path": a}} for n, a in tools]
    return {"type": "assistant", "timestamp": ts, "message": {"id": f"m{N[0]}", "content": content, "usage": {
        "input_tokens": 0, "cache_read_input_tokens": ctx - 1000, "cache_creation_input_tokens": 1000,
        "cache_creation": {"ephemeral_1h_input_tokens": 1000}, "output_tokens": out}}}


def note(ts, summary):
    return user(ts, f"<task-notification>\n<output-file>/tmp/x/tasks/b1.output</output-file>\n<summary>{summary}</summary>",
                "task_notification")


def write(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("\n".join(json.dumps(r) for r in rows) + "\n")
    f.close()
    return f.name


# A wake that only read the task output and re-armed: rearm_only, and the stream keeps one call of it.
rows = [user("2026-01-01T00:00:00Z", "hi", "human"), call("2026-01-01T00:00:01Z", 200_000),
        note("2026-01-01T00:10:00Z", 'Background command "Ack inbox message and re-arm the wait" completed'),
        call("2026-01-01T00:10:01Z", 201_000, [("Read", "/tmp/x/tasks/b1.output")]),
        call("2026-01-01T00:10:02Z", 202_000, [("Bash", "orch inbox --ack dv1 --wait")]),
        call("2026-01-01T00:10:03Z", 203_000)]
p = write(rows)
ts = tc.turns(p)
assert [tc.kind(t) for t in ts] == ["human", "notification_rearm_only"], [tc.kind(t) for t in ts]
assert tc.summarize(ts)["calls"] == 4
w = tc.what_if(ts, {"stream"})
assert w["calls"] == 2, w  # the human call and the wake's closing reply

# Only a task's output file is a removable Read, and a Bash call doing real work beside the ack stays.
assert tc.rearm(("Read", "/tmp/x/tasks/b1.output")) and not tc.rearm(("Read", "/repo/SKILL.md"))
assert tc.rearm(("Bash", "cd /w && orch inbox --ack dv1 --wait")) and tc.rearm(("Bash", "orch watch-cards --self term_a"))
assert not tc.rearm(("Bash", "gh pr merge 1 --squash && orch inbox --ack dv1 --wait"))
assert not tc.rearm(("Grep", "inbox"))

# The replay starts at the first context it sees: no free drop to --fresh before a restart happened.
first = tc.what_if(tc.turns(write([user("2026-01-01T00:00:00Z", "hi", "human"), call("2026-01-01T00:00:01Z", 200_000)])),
                   {"restart"}, restart_at=130_000, fresh=62_000)
assert first["restarts"] == 0 and first["ctx_median"] == 200_000, first

# A restart happens at a quiet turn boundary past the threshold, and costs its cache write and reboot calls.
rows = [user("2026-01-01T00:00:00Z", "a", "human"), call("2026-01-01T00:00:01Z", 140_000),
        user("2026-01-01T00:00:30Z", "b", "human"), call("2026-01-01T00:00:31Z", 141_000),  # 29 s later: not quiet
        user("2026-01-01T00:10:00Z", "c", "human"), call("2026-01-01T00:10:01Z", 142_000)]
r = tc.what_if(tc.turns(write(rows)), {"restart"}, restart_at=130_000, fresh=62_000)
assert r["restarts"] == 1 and r["calls"] == 3 + tc.REBOOT_CALLS, r
base = tc.summarize(tc.turns(write(rows)))["weight"]
assert r["weight"] > 0 and r["weight"] != base

# The stream ends every 29 minutes of wall time: two calls each, busy or not.
rows = [user("2026-01-01T00:00:00Z", "a", "human"), call("2026-01-01T00:00:01Z", 100_000),
        user("2026-01-01T01:00:00Z", "b", "human"), call("2026-01-01T01:00:01Z", 100_000)]
s = tc.what_if(tc.turns(write(rows)), {"stream"})
assert s["calls"] == 2 + 2 * 2, s  # 60 minutes hold two 29-minute ends

# Debounce: a wake inside the window of the wake that opened it saves its closing reply; the window does not slide.
wake = 'Background command "Ack inbox message and re-arm the wait" completed'
rows = [note("2026-01-01T00:00:00Z", wake), call("2026-01-01T00:00:01Z", 100_000, [("Bash", "gh pr view 1 --json state")]),
        call("2026-01-01T00:00:02Z", 100_000),
        note("2026-01-01T00:00:20Z", wake), call("2026-01-01T00:00:21Z", 100_000, [("Bash", "gh pr view 2 --json state")]),
        call("2026-01-01T00:00:22Z", 100_000),
        note("2026-01-01T00:00:40Z", wake), call("2026-01-01T00:00:41Z", 100_000, [("Bash", "gh pr view 3 --json state")]),
        call("2026-01-01T00:00:42Z", 100_000)]
d = tc.what_if(tc.turns(write(rows)), {"stream"}, debounce=30)
assert d["calls"] == 5, d  # the second wake (20 s) shares the first's notice; the third (40 s) opens a new window

assert tc.main([p, "--json", "--what-if", "stream,restart"]) == 0
try:
    tc.main([p, "--what-if", "nope"])
    raise AssertionError("an unknown --what-if must fail")
except SystemExit as e:
    assert e.code == 2
print("test_turn_cost: ok")
