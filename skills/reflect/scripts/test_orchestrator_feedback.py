"""orchestrator_feedback.py: owner prompts from registered orchestrator transcripts, read once per byte range.
Run: python3 skills/reflect/scripts/test_orchestrator_feedback.py"""
import contextlib, datetime as dt, io, json, os, pathlib, sys, tempfile

TMP = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"AGENT_SKILLS_CONFIG": str(TMP / "cfg.json"), "AGENT_SKILLS_STATE": str(TMP / "state"),
                   "PROGRAMS_HOME": str(TMP / "programs"), "AGENT_SKILLS_TRASH": str(TMP / "trash")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import orchestrator_feedback as of

NOW = dt.datetime(2026, 3, 20, 12, tzinfo=dt.timezone.utc)
HOME = TMP / "programs/_standing/reflect"
REG = TMP / "state/transcripts"
REG.mkdir(parents=True)
HUMAN = {"kind": "human"}


def ts(days_ago, minute=0):
    return (NOW - dt.timedelta(days=days_ago) + dt.timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")


def user(uid, text, when, origin=HUMAN, **extra):
    line = {"type": "user", "uuid": uid, "timestamp": when, "message": {"role": "user", "content": text}, **extra}
    if origin is not None:
        line["origin"] = origin
    return line


def assistant(uid, when, text="", tool=None):
    content = ([{"type": "text", "text": text}] if text else []) + \
              ([{"type": "tool_use", "name": tool[0], "input": tool[1]}] if tool else [])
    return {"type": "assistant", "uuid": uid, "timestamp": when, "message": {"role": "assistant", "content": content}}


def write(path, lines, tail=""):
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines) + tail)


def config(**reflect):
    (TMP / "cfg.json").write_text(json.dumps({"reflect": reflect}))


def register(session, path, since=None, until=None, role="orchestrator"):
    (REG / f"{session}.json").write_text(json.dumps({"role": role, "transcript": str(path), "since": since or ts(60),
                                                     "until": until}))


def run(*argv, now=NOW):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = of.digest(now) if argv == ("digest",) else of.main(list(argv))
    text = out.getvalue().strip()
    try:
        return code, json.loads(text)
    except ValueError:
        return code, text


def rows(result):
    return [json.loads(l) for l in pathlib.Path(result["digest"]).read_text().splitlines()] if result["digest"] else []


def commit(result):
    return run("advance", "--round", result["round"], "--expect", result["expect"] or "none")


# 1. Off, unset, or pointed into the skill repository: nothing is read.
T = TMP / "t1.jsonl"
write(T, [user("u1", "please stop doing that", ts(1))])
register("s1", T)
for cfg, why in (({}, "orchestrator_feedback is off"), ({"orchestrator_feedback": False}, "orchestrator_feedback is off"),
                 ({"orchestrator_feedback": True}, "reflect.ledger is not set")):
    config(**cfg)
    code, out = run("digest")
    assert code == 0 and why in out["skipped"], out
assert not HOME.exists(), "a skipped round writes nothing"
(TMP / "repo").mkdir()
config(orchestrator_feedback=True, ledger=str(TMP / "repo/notes/learnings.md"), skill_repository=str(TMP / "repo"))
code, out = run("digest")
assert code == 2 and "reflect.ledger is inside" in out["skipped"], out
os.environ["PROGRAMS_HOME"] = str(TMP / "repo/programs")
config(orchestrator_feedback=True, ledger="Project/x/learnings.md", skill_repository=str(TMP / "repo"))
code, out = run("digest")
assert code == 2 and "digest folder is inside" in out["skipped"], out
os.environ["PROGRAMS_HOME"] = str(TMP / "programs")
config(orchestrator_feedback=True, ledger="Project/x/learnings.md", skill_repository=str(TMP / "repo"))

# 2. The filter, on line shapes taken from real transcripts with their text replaced.
TAG = "<system-reminder>\nmachine text\n</system-reminder>\n"
lines = [
    assistant("a0", ts(2, 0), "I opened acme/app#1 and ABC-12.", ("Bash", {"command": "git status", "description": "x"})),
    user("u-plain", "do it through a worker, not yourself", ts(2, 1)),
    user("u-meta", "skill body loaded", ts(2, 2), origin=None, isMeta=True),
    user("u-tool", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], ts(2, 3), origin=None),
    user("u-blocks", [{"type": "image", "source": {}}, {"type": "text", "text": "[Image #1] this border is wrong"}],
         ts(2, 4)),
    user("u-tagged", TAG + "and keep the lineage under the run", ts(2, 5)),
    user("u-task", "<task-notification><task-id>b1</task-id></task-notification>", ts(2, 6),
         origin={"kind": "task-notification"}),
    user("u-peer", "report from a subagent", ts(2, 7), origin={"kind": "peer", "from": "a1"}, isMeta=True),
    user("u-chat", '<channel source="chat" chat_id="1">use the recommended defaults</channel>', ts(2, 8),
         origin={"kind": "channel", "server": "chat"}, isMeta=True),
    {"type": "attachment", "uuid": "u-queued", "timestamp": ts(2, 9),
     "attachment": {"type": "queued_command", "prompt": "you merged before review again", "origin": HUMAN}},
    {"type": "attachment", "uuid": "u-queued-task", "timestamp": ts(2, 10),
     "attachment": {"type": "queued_command", "prompt": "<task-notification/>", "origin": {"kind": "task-notification"}}},
    {"type": "queue-operation", "operation": "enqueue", "timestamp": ts(2, 9), "content": "you merged before review again"},
    user("u-summary", "This session is being continued", ts(2, 11), origin=None, isCompactSummary=True),
    user("u-stop", "[Request interrupted by user]", ts(2, 12), origin=None),
    user("u-legacy", "no origin field, an older client", ts(2, 13), origin=None),
    user("u-cmd", "<command-message>brief</command-message>\n<command-name>/brief</command-name>\n"
         "<command-args>table please</command-args>", ts(2, 14)),
    user("u-only-tags", TAG, ts(2, 15)),
]
T2 = TMP / "t2.jsonl"
write(T2, lines)
register("s1", T2)
code, out = run("digest")
got = {r["event"].split(":")[1]: r for r in rows(out)}
assert list(got) == ["u-plain", "u-blocks", "u-tagged", "u-chat", "u-queued", "u-legacy", "u-cmd"], list(got)
assert got["u-blocks"]["prompt"] == "[Image #1] this border is wrong"
assert got["u-tagged"]["prompt"] == "and keep the lineage under the run" and "machine" not in got["u-tagged"]["prompt"]
assert got["u-chat"]["prompt"] == "use the recommended defaults"
assert got["u-cmd"]["prompt"] == "/brief\ntable please", got["u-cmd"]["prompt"]
first = got["u-plain"]
assert first["event"] == "s1:u-plain" and first["line"] == 2 and first["transcript"] == str(T2), first
assert first["before"] == "I opened acme/app#1 and ABC-12." and first["tools"] == ["Bash git status"], first
assert first["context"] == ["acme/app#1", "ABC-12"], first["context"]
assert got["u-blocks"]["tools"] == [] and got["u-blocks"]["before"] == "", "context resets after each owner prompt"
assert out["sessions"]["s1"]["to"] == T2.stat().st_size and not out["sessions"]["s1"]["pending"]
assert commit(out)[0] == 0
assert not pathlib.Path(out["digest"]).exists() and list((TMP / "trash").glob("orchestrator-*.jsonl")), \
    "the digest holds the owner's words and goes to the trash once committed"

# 3. Continuing: a tail with no owner prompt still moves scan_end; a rerun gives no event twice.
with T2.open("a") as f:
    f.write(json.dumps(assistant("a9", ts(1), "done with ABC-77", ("Read", {"file_path": "x/SKILL.md"}))) + "\n")
code, out = run("digest")
assert out["rows"] == 0 and out["digest"] is None and out["sessions"]["s1"]["to"] == T2.stat().st_size, out
assert commit(out)[0] == 0
with T2.open("a") as f:
    f.write(json.dumps(user("u-next", "next correction", ts(0, 1))) + "\n")
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s1:u-next"], rows(out)
nxt = rows(out)[0]
assert nxt["before"] == "done with ABC-77" and nxt["tools"] == ["Read x/SKILL.md"] and nxt["context"] == ["ABC-77"], \
    "context read before the round boundary carries over"
code, again = run("digest")
assert again["digest"] != out["digest"] and again["round"] != out["round"], "each round owns its digest"
assert [r["event"] for r in rows(again)] == ["s1:u-next"], "not committed: the same range is read again"
assert commit(again)[0] == 0
lost = commit(out)
assert lost[0] == 1 and str(lost[1]).startswith("cursor moved"), lost
assert not pathlib.Path(out["digest"]).exists(), "a round that lost still trashes its digest"
assert not list(HOME.glob("orchestrator-next-*.json")), "no proposal is left behind"
assert run("digest")[1]["rows"] == 0

# 4. First round: no further back than 7 days, nor before `since`; nothing at or after `until`.
T4 = TMP / "t4.jsonl"
write(T4, [user("old", "ten days ago", ts(10)), user("pre", "before the role", ts(5)), user("in", "in span", ts(3)),
           user("late", "after ROLE: none", ts(1))])
register("s4", T4, since=ts(4), until=ts(2))
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s4:in"], rows(out)
commit(out)
T5 = TMP / "t5.jsonl"
write(T5, [user("old5", "ten days ago", ts(10)), user("new5", "two days ago", ts(2))])
register("s5", T5, since=ts(30))
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s5:new5"], rows(out)
commit(out)

# 5. A line still being written stops the scan before it.
T6 = TMP / "t6.jsonl"
write(T6, [user("w1", "first", ts(1))], tail=json.dumps(user("w2", "second", ts(1, 1)))[:30])
register("s6", T6)
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s6:w1"] and out["sessions"]["s6"]["to"] < T6.stat().st_size
commit(out)
write(T6, [user("w1", "first", ts(1)), user("w2", "second", ts(1, 1))])
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s6:w2"], rows(out)
commit(out)

# 6. The round's cap: the first prompt that does not fit stays for the next round, marked pending.
for p in REG.glob("*.json"):
    p.unlink()
T7 = TMP / "t7.jsonl"
write(T7, [user(f"c{i}", f"prompt {i} " + "x" * 300, ts(1, i)) for i in range(8)])
register("s7", T7)
saved = of.MAX_BYTES
of.MAX_BYTES = 1500
code, out = run("digest")
taken = [r["event"] for r in rows(out)]
assert 1 <= len(taken) < 8 and out["sessions"]["s7"]["pending"], out
commit(out)
of.MAX_BYTES = saved
code, out = run("digest")
assert [r["event"] for r in rows(out)] == [f"s7:c{i}" for i in range(len(taken), 8)], "the rest, from where it stopped"
assert out["sessions"]["s7"]["pending"] is False
commit(out)

# 7. A replaced or truncated transcript: continue after the cursor's message, or reread from `since`.
T8 = TMP / "t8.jsonl"
write(T8, [user("r1", "one", ts(1, 1)), user("r2", "two", ts(1, 2))])
register("s8", T8, since=ts(20))
commit(run("digest")[1])
T8.unlink()
write(T8, [user("r1", "one", ts(1, 1)), user("r2", "two", ts(1, 2)), user("r3", "three", ts(1, 3))])  # new inode
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s8:r3"] and "continued after" in out["sessions"]["s8"]["note"], out
commit(out)
write(T8, [user("r0", "from long ago", ts(15)), user("r9", "rewritten", ts(1, 9))])  # shorter, cursor message gone
code, out = run("digest")
assert [r["event"] for r in rows(out)] == ["s8:r0", "s8:r9"], "recovery reads from since, not only 7 days"
assert "reading again from `since`" in out["sessions"]["s8"]["note"], out
commit(out)

# 8. Registry edge cases.
for p in REG.glob("*.json"):
    p.unlink()
code, out = run("digest")
assert out["rows"] == 0 and out["note"] == "no orchestrator transcript registered", out
register("s9", TMP / "missing.jsonl")
register("s10", T2, role="lead")
code, out = run("digest")
assert out["sessions"] == {"s9": {"note": "transcript missing"}}, out
assert run("advance", "--round", "orch-20260320T120000Z-00000000", "--expect", out["expect"])[1] \
    .startswith("no proposed cursor")
assert run("advance", "--round", "../x", "--expect", "none")[0] == 2
assert run("bogus")[0] == 2
print("orchestrator_feedback: all pass")
