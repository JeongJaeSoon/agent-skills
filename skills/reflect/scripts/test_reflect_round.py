"""reflect_round.py: the standing round's lock, cursor, last standing PR and waiting.json.
Run: python3 skills/reflect/scripts/test_reflect_round.py"""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile, time

TMP = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"AGENT_SKILLS_CONFIG": str(TMP / "cfg.json"), "PROGRAMS_HOME": str(TMP / "programs")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reflect_round as rr
import skill_usage as su

HOME = TMP / "programs/_standing/reflect"


def run(*argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = rr.settle(**kw) if argv == ("settle",) else rr.main(list(argv))
    return code, out.getvalue()


def begin():
    code, out = run("begin")
    assert code == 0, out
    return out, next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("round: "))


def ledger(slug, *rows):
    p = TMP / "programs" / slug / "ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


ledger("acme", {"ev": "signal", "ts": "2026-09-22T00:00:00+00:00", "evidence": "msg_old"},
       {"ev": "landed", "ts": "2026-09-23T00:00:00+00:00", "pr": 4})
ledger("_skill-usage", {"ev": "signal", "ts": "2026-09-23T01:00:00Z", "evidence": "skill-usage:x:misses@w1"})
HOME.mkdir(parents=True)
(HOME / "cursor.json").write_text(json.dumps({"acme": "2026-09-21T00:00:00+00:00"}))

# begin: takes the lock, lists the precheck's new rows with a pointer each, and snapshots every ledger's newest ts.
out, r1 = begin()
assert (HOME / "lock").is_dir() and "new: acme 1 (signal)" in out and "rows:" in out, out
rows = [json.loads(l) for l in (HOME / "round-rows.jsonl").read_text().splitlines()]
assert {(r["ledger"], r["pointer"]) for r in rows} == {("acme", "msg_old"), ("_skill-usage", "skill-usage:x:misses@w1")}, rows
snap = json.loads((HOME / "round.json").read_text())
assert snap == {"round": r1, "newest": {"acme": "2026-09-23T00:00:00+00:00", "_skill-usage": "2026-09-23T01:00:00Z"}}, snap
assert su.precheck(TMP / "programs", collect_first=False, orchestrator=lambda h: []) == 1, "the precheck sees the lock"

# A second begin while the lock is fresh skips with exit 1 and leaves the round's files alone.
code, out = run("begin")
assert code == 1 and "holds the lock" in out, out
assert json.loads((HOME / "round.json").read_text()) == snap

# A row with no evidence field is pointed to as <slug>:<ev>:<ts>:<pr or sha>.
assert rr.pointer("acme", {"ev": "main_red", "ts": "T", "sha": "abc"}) == "acme:main_red:T:abc"

# end with another round's id writes nothing and keeps the lock.
code, out = run("end", "--round", "someone-else", "--advance", "--waiting", "u")
assert code == 2 and (HOME / "lock").is_dir() and not (HOME / "waiting.json").exists(), out
assert json.loads((HOME / "cursor.json").read_text()) == {"acme": "2026-09-21T00:00:00+00:00"}

# A row appended during the round is after begin's snapshot: end --advance leaves it for the next round.
ledger("acme", {"ev": "land_failed", "ts": "2026-09-24T00:00:00+00:00", "pr": 5})
code, out = run("end", "--round", r1, "--advance", "--waiting", "https://github.com/acme/app/pull/9")
assert code == 0 and "lock released" in out and out.index("waiting:") < out.index("cursor:"), out
assert json.loads((HOME / "cursor.json").read_text()) == snap["newest"]
assert json.loads((HOME / "waiting.json").read_text()) == {"pr": "https://github.com/acme/app/pull/9"}
assert not (HOME / "lock").exists() and not (HOME / "round.json").exists() and not (HOME / "round-rows.jsonl").exists()
assert set(su.new_findings(TMP / "programs")) == {"acme"}, "the row appended mid-round is still new"

# The snapshot and the new rows come from one read: a ledger the cursor does not list yet, whose newest row is a
# finding, gives that finding and that ts, not a later non-finding row's.
ledger("fresh", {"ev": "signal", "ts": "2026-09-25T00:00:00Z", "evidence": "msg_fresh"})
read = su.ledgers(TMP / "programs")
ledger("fresh", {"ev": "landed", "ts": "2026-09-26T00:00:00Z", "pr": 1})
assert su.new_findings(TMP / "programs", read)["fresh"][0]["evidence"] == "msg_fresh"
(TMP / "programs" / "fresh" / "ledger.jsonl").unlink()

# The cursor never moves back.
(HOME / "cursor.json").write_text(json.dumps({"acme": "2026-10-01T00:00:00+00:00"}))
_, r2 = begin()
code, out = run("end", "--round", r2, "--advance", "--clear-waiting")
assert code == 0 and "waiting: cleared" in out and not (HOME / "waiting.json").exists(), out
assert json.loads((HOME / "cursor.json").read_text())["acme"] == "2026-10-01T00:00:00+00:00"

# A round that outlived its lock cannot finish the round that replaced it.
_, r3 = begin()
old = time.time() - (su.LOCK_STALE_HOURS + 1) * 3600
os.utime(HOME / "lock", (old, old))
out, r4 = begin()
assert "stale lock" in out and r4 != r3, out
code, out = run("end", "--round", r3, "--advance")
assert code == 2 and (HOME / "lock").is_dir() and json.loads((HOME / "round.json").read_text())["round"] == r4, out
assert run("end", "--round", r4)[0] == 0 and not (HOME / "lock").exists()

# An older round's lock directory with a start-time file in it is replaced once stale.
(HOME / "lock").mkdir()
(HOME / "lock" / "started").write_text("2026-01-01T00:00:00Z")
os.utime(HOME / "lock", (old, old))
out, r5 = begin()
assert "stale lock" in out and not (HOME / "lock" / "started").exists(), out
assert run("end", "--round", r5)[0] == 0

# settle: the newest reflect/standing-* PR's state, or none; gh failing or no repository is exit 2.
prs = [{"number": 1, "state": "MERGED", "headRefName": "reflect/standing-20260901T000000Z",
        "createdAt": "2026-09-01T00:00:00Z", "url": "u1"},
       {"number": 2, "state": "CLOSED", "headRefName": "reflect/standing-20260910T000000Z",
        "createdAt": "2026-09-10T00:00:00Z", "url": "u2"},
       {"number": 3, "state": "OPEN", "headRefName": "feature/x", "createdAt": "2026-09-20T00:00:00Z", "url": "u3"}]
seen = []
code, out = run("settle", repo="acme/app", list_prs=lambda repo, cwd: (seen.append((repo, cwd)), (prs, None))[1])
assert code == 0 and out.strip() == "closed u2" and seen == [("acme/app", None)], (out, seen)
code, out = run("settle", repo="acme/app", list_prs=lambda repo, cwd: (prs[2:], None))
assert code == 0 and out.strip() == "none", out
code, out = run("settle", repo="acme/app", list_prs=lambda repo, cwd: (None, "HTTP 401"))
assert code == 2 and "HTTP 401" in out, out
code, out = run("settle", list_prs=lambda repo, cwd: (prs, None))
assert code == 2 and "reflect.skill_repository" in out, out
(TMP / "cfg.json").write_text(json.dumps({"reflect": {"skill_repository": "~/src/skills"}}))
code, out = run("settle", list_prs=lambda repo, cwd: (seen.append((repo, cwd)), (prs[:1], None))[1])
assert code == 0 and out.strip() == "merged u1" and seen[-1] == (None, str(pathlib.Path("~/src/skills").expanduser())), seen


# gh_prs: the repository is checked first, the PR search is by branch, and every gh failure is an error, not "none".
def fake(*results):
    calls = []

    def go(cmd, **kw):
        calls.append(cmd)
        r = results[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return subprocess.CompletedProcess(cmd, r[0], r[1], r[2])
    return go, calls


go, calls = fake((0, "{}", ""), (0, json.dumps(prs), ""))
assert rr.gh_prs("acme/app", None, go) == (prs, None)
assert calls[0][:4] == ["gh", "repo", "view", "acme/app"] and "head:reflect/standing-" in calls[1] and calls[1][-2:] == ["-R", "acme/app"], calls
go, calls = fake((1, "", "Could not resolve to a Repository"))
assert rr.gh_prs("acme/none", None, go) == (None, "Could not resolve to a Repository") and len(calls) == 1
go, _ = fake((0, "{}", ""), (0, "not json", ""))
assert rr.gh_prs(None, "/x", go)[0] is None
go, _ = fake((0, "{}", ""), (0, "{}", ""))
assert rr.gh_prs(None, "/x", go) == (None, "gh printed no PR list")
go, _ = fake(OSError("no gh"))
assert rr.gh_prs(None, "/x", go) == (None, "no gh")

# Usage errors exit 2 before anything is written: a missing --round, a flag where a value goes, an unknown flag.
for argv in (("end",), ("end", "--advance"), ("end", "--round", "x", "--waiting", "--advance"),
             ("end", "--round", "x", "--waiting", "u", "--clear-waiting"), ("end", "--round", "x", "--force"),
             ("begin", "--now"), ("settle", "--repo"), ("nope",)):
    assert run(*argv)[0] == 2, argv
assert not (HOME / "waiting.json").exists()

print("test_reflect_round: ok")
