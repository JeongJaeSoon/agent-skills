"""Offline tests for fleet.prompt_records: the 2 hour cap and records of terminals that are gone.

Run: python3 test_prompt_ttl.py
"""
import datetime as dt, json, os, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-prompt-ttl-"))
os.environ.update(dash_demo.build(root))
import fleet  # noqa: E402  after the env points the state directory at root

prompts = fleet.state_dir() / "prompts"
now = fleet.utcnow()


def put(handle, age_s):
    fleet.write_atomic(prompts / f"{handle}.json", json.dumps(
        {"v": 1, "id": f"p-{handle}", "handle": handle, "at": fleet.iso(now - dt.timedelta(seconds=age_s)), "tool": "Bash"}))


def left():
    return sorted(p.stem for p in prompts.glob("*.json"))


assert fleet.PROMPT_KEEP_S == 7200
put("term_fresh", 60)
put("term_old", 7200 + 120)
put("term_gone", 600)
assert sorted(fleet.prompt_records()) == ["term_fresh", "term_gone"], "a record past two hours goes"
assert left() == ["term_fresh", "term_gone"], "and its file with it"

# A terminal Orca no longer lists has no record in the result, but the file stays: a short list must be undoable.
got = fleet.prompt_records({"term_fresh"})
assert sorted(got) == ["term_fresh"] and left() == ["term_fresh", "term_gone"], (sorted(got), left())
assert sorted(fleet.prompt_records({"term_fresh", "term_gone"})) == ["term_fresh", "term_gone"], "back when listed again"

# A record under the grace is returned even when the list lacks its terminal: the list may predate it.
put("term_new", 30)
assert sorted(fleet.prompt_records({"term_fresh"})) == ["term_fresh", "term_new"]

# Nothing known about the terminals (an empty read) hides nothing.
assert sorted(fleet.prompt_records(set())) == ["term_fresh", "term_gone", "term_new"]

# The age limit holds for a listed terminal too.
put("term_fresh", 7300)
assert sorted(fleet.prompt_records({"term_fresh", "term_gone", "term_new"})) == ["term_gone", "term_new"]
assert left() == ["term_gone", "term_new"], left()
print("ok")
