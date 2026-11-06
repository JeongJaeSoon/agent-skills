"""compactions.py: the restart verdict from fake transcripts, the cursor cache and the policy keys. Orca is never called.

Run: python3 test_compactions.py
"""
import builtins, json, os, pathlib, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-compactions-"))
os.environ.update({"AGENT_SKILLS_STATE": str(tmp / "state"), "AGENT_SKILLS_CONFIG": str(tmp / "agent-skills.json"),
                   "CLAUDE_PROJECTS_DIR": str(tmp / "projects")})
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import compactions as cx  # noqa: E402

S = "00000000-0000-4000-8000-0000000000aa"
proj = tmp / "projects" / "p1"
proj.mkdir(parents=True)
path = proj / f"{S}.jsonl"
P = cx.Policy


def stamp(minutes):
    return f"2026-01-01T{minutes // 60:02d}:{minutes % 60:02d}:00Z"


def boundary(minutes, pre=343_000, trigger="auto", **extra):
    return json.dumps({"type": "system", "subtype": "compact_boundary", "timestamp": stamp(minutes), "sessionId": S,
                       "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": 9000}, **extra})


def usage(total, **extra):
    return json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": total - 10,
                                                                    "cache_creation_input_tokens": 0}}, **extra})


def write(*rows, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        for r in rows:
            f.write(r + "\n")


def got():
    return [(c.pre, c.after) for c in cx.history(S)]


def cfg(text):
    (tmp / "agent-skills.json").write_text(text)


# 1. No boundary: no history, not due; no transcript at all is unreadable.
write(usage(50_000))
assert cx.history(S) == [] and cx.due(S, 50_000, P()) == ""
try:
    cx.history("00000000-0000-4000-8000-0000000000bb")
    raise AssertionError("a missing transcript must raise")
except cx.TranscriptUnreadable:
    pass

# 2. Three auto compactions are due, two are not.
write(boundary(0), usage(80_000), boundary(100), usage(80_000))
assert cx.due(S, 80_000, P()) == ""
write(boundary(200), usage(80_000), mode="a")
assert cx.due(S, 80_000, P()) == "3 auto-compactions"
assert cx.due(S, 80_000, P(after=4)) == ""

# 3. A subagent's boundary neither counts nor closes the window; a manual one does not count but closes it.
write(boundary(0), boundary(10, agentId="a1"), boundary(20, isSidechain=True), usage(200_000, agentId="a1"),
      usage(60_000))
assert got() == [(343_000, 60_000)]
write(boundary(0), boundary(50, trigger="manual"), usage(200_000))
assert got() == [(343_000, None)], "auto -> manual -> usage: the usage belongs to no auto window"
write(boundary(0), boundary(100), usage(200_000))
assert got() == [(343_000, None), (343_000, 200_000)], "auto -> auto -> usage: only the last window is measured"

# 4. Stuck: the first main call after the last compaction is at or above the ratio of what it compressed.
write(boundary(0), usage(200_000, isSidechain=True), usage(200_000))
assert cx.due(S, 1, P()) == "compaction left 58%", cx.due(S, 1, P())
write(boundary(0), usage(100_000))  # 0.29
assert cx.due(S, 1, P()) == ""
write(boundary(0), usage(0, agentId="x"), boundary(500))  # no main usage yet: the rule is skipped
assert cx.due(S, 1, P()) == ""

# 5. Gap: the last two auto compactions 20 minutes apart are due, 40 are not.
write(boundary(0), usage(60_000), boundary(20), usage(60_000))
assert cx.due(S, 1, P()) == "last 2 compactions 20 min apart"
write(boundary(0), usage(60_000), boundary(40), usage(60_000))
assert cx.due(S, 1, P()) == ""
assert cx.due(S, 1, P(min_gap_min=45)) == "last 2 compactions 40 min apart"

# 6. Reading on: a second call starts at the cached offset, and a window left open is filled by a later usage.
write(boundary(0), usage(60_000))
cx.history(S)
size = path.stat().st_size
write(boundary(100), mode="a")
reads = []
real_open = builtins.open


def spy(file, mode="r", *a, **k):
    f = real_open(file, mode, *a, **k)
    if str(file) == str(path) and "b" in mode:
        seek = f.seek
        f.seek = lambda off, *w: (reads.append(off), seek(off, *w))[1]
    return f


builtins.open = spy
try:
    assert got() == [(343_000, 60_000), (343_000, None)]
finally:
    builtins.open = real_open
assert size in reads, reads  # the scan began at the cached offset (a rebuild would only ever seek 0)
write(usage(90_000), mode="a")
assert got() == [(343_000, 60_000), (343_000, 90_000)], "the open window continues from the cache"

# 7. Cache invalidation rebuilds from byte 0 whatever the reason.
cache = cx.cache_path(S)
for name, damage in (
    ("truncated", lambda: write(boundary(0))),
    ("other inode", lambda: (path.rename(proj / "old.jsonl"), write(boundary(0), usage(60_000), boundary(10), usage(1_000),
                                                                   boundary(30), usage(70_000)))),
    ("same inode regrown", lambda: None),
    ("version", lambda: cache.write_text(json.dumps({**json.loads(cache.read_text()), "v": 0}))),
    ("garbage", lambda: cache.write_text("{not json")),
):
    write(boundary(0), usage(60_000), boundary(100), usage(60_000))
    cx.history(S)
    damage()
    if name == "same inode regrown":
        old_len = path.stat().st_size
        write(boundary(5, pre=111_000), usage(5_000), boundary(6, pre=111_000), usage(5_000))  # truncating rewrite
        with real_open(path, "a") as f:  # regrown past the old offset with other bytes
            f.write(("x" * 200 + "\n") * (old_len // 200 + 2))
    fresh = got()
    cache.unlink()
    assert fresh == got(), name  # the same as a clean read of the file as it is
    if name == "same inode regrown":
        assert fresh[0][0] == 111_000, fresh
    for f in proj.glob("old.jsonl"):
        f.unlink()

# 8. Chunk boundaries: a row longer than a chunk, rows cut by a chunk edge, and a multi-byte character cut by one.
cx.CHUNK = 64
write(boundary(0), json.dumps({"type": "user", "text": "k" * 300 + "한글" * 40}), usage(60_000), boundary(100),
      usage(70_000, note="한" * 50))
cache.unlink(missing_ok=True)
assert got() == [(343_000, 60_000), (343_000, 70_000)]
for chunk in range(23, 70, 7):
    cx.CHUNK = chunk
    cache.unlink(missing_ok=True)
    assert got() == [(343_000, 60_000), (343_000, 70_000)], chunk
cx.CHUNK = 1 << 20
# A row with no newline yet is left for the next call, and counted once when it is complete.
write(boundary(0))
with real_open(path, "a") as f:
    f.write(boundary(100)[:40])
cache.unlink(missing_ok=True)
assert got() == [(343_000, None)]
with real_open(path, "a") as f:
    f.write(boundary(100)[40:] + "\n")
assert got() == [(343_000, None), (343_000, None)]
assert got() == [(343_000, None), (343_000, None)]
# A broken JSON row is skipped.
write(boundary(0), '{"subtype": "compact_boundary" broken', usage(60_000))
cache.unlink(missing_ok=True)
assert got() == [(343_000, 60_000)]

# 9. Failures: a cache that cannot be written, and a transcript that is gone, cost only what they should.
write(boundary(0), usage(60_000), boundary(10), usage(60_000), boundary(20), usage(60_000))
cache.unlink(missing_ok=True)
real_replace = os.replace
os.replace = lambda *a: (_ for _ in ()).throw(OSError("read-only"))
try:
    assert cx.due(S, 1, P()) == "3 auto-compactions"
finally:
    os.replace = real_replace
assert not list(cache.parent.glob("*.tmp")), "the temporary file is removed when the replace fails"
(tmp / "state" / "compact-history").chmod(0o500)
try:
    assert cx.due(S, 1, P()) == "3 auto-compactions"
finally:
    (tmp / "state" / "compact-history").chmod(0o700)
gone = "00000000-0000-4000-8000-0000000000cc"
assert cx.due(gone, 400_000, P(ceiling=360_000)) == "context 400k > ceiling 360k", "the ceiling needs no transcript"
assert cx.due(gone, 100_000, P(ceiling=360_000)) == ""
assert cx.due(S, 400_000, P(ceiling=360_000)) == "context 400k > ceiling 360k", "the ceiling comes first"

# 10. explain's key stays the same while the same reason holds, and changes when it is a new one.
k1 = cx.explain(S, 1, P())[0]
write(boundary(30), usage(60_000), mode="a")
assert cx.explain(S, 1, P())[0] != k1
assert cx.explain(S, 500_000, P(ceiling=360_000))[0] == cx.explain(S, 600_000, P(ceiling=360_000))[0]

# 11. policy(): defaults, the switch to turn the rules off, the legacy `restart_at: 0`, bad types.
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "missing.json")
assert cx.policy() == P(), "no file: defaults, ceiling 0, nothing automatic"
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "agent-skills.json")
cfg("{}")
assert cx.policy() == P()
cfg('{"orchestrate": {"compact_restart": false}}')
assert cx.policy().compact is False
cfg('{"orchestrate": {"compact_restart": false, "restart_at": 360000}}')
assert cx.policy().compact is False and cx.policy().ceiling == 360_000
assert cx.due(S, 400_000, cx.policy()) == "context 400k > ceiling 360k", "a ceiling works with the rules off"
cfg('{"orchestrate": {"restart_at": 0}}')
assert cx.policy().compact is False and cx.policy().ceiling == 0, "an explicit 0 keeps meaning: no suggestion"
cfg('{"orchestrate": {"restart_at": 0, "compact_restart": {}}}')
assert cx.policy().compact is True
cfg('{"orchestrate": {"restart_at": 200000}}')
assert cx.policy() == P(ceiling=200_000)
cfg('{"orchestrate": {"compact_restart": {"after": 5, "stuck_ratio": 0.7, "min_gap_min": 10, "auto_clear": true},'
    ' "auto_clear": true, "restart_at": 300000}}')
assert cx.policy() == P(True, 5, 0.7, 10, 300_000, True, True)
cfg('{"orchestrate": {"compact_restart": {"after": "x", "stuck_ratio": true, "min_gap_min": -1, "auto_clear": "yes"}, '
    '"restart_at": "big", "auto_clear": 1}}')
assert cx.policy() == P(), cx.policy()
cfg('{"orchestrate": {"compact_restart": "yes", "restart_at": true}}')
assert cx.policy() == P()
cfg('{"orchestrate": []}')
assert cx.policy() == P()
cfg("{broken")
assert cx.policy() == P()
# The stream acts on a rule family only when its own auto switch is on.
assert P(auto_clear=True, ceiling=9, auto_compact=False).streamed() == P(compact=False, ceiling=9, auto_clear=True)
assert P(ceiling=9, auto_compact=True).streamed() == P(ceiling=0, auto_compact=True)

# 12. usage_context counts the three fields and skips a subagent's row.
assert cx.usage_context(json.loads(usage(1000))) == 1000
assert cx.usage_context(json.loads(usage(1000, agentId="a"))) is None
assert cx.usage_context(json.loads(usage(1000, isSidechain=True))) is None
assert cx.usage_context({"message": {"usage": {}}}) is None and cx.usage_context({"message": "x"}) is None
print("test_compactions: ok")
