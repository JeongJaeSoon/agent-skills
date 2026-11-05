"""reflect_round.py: the standing round's lock, cursor, last standing PR and waiting.json.
Run: python3 skills/reflect/scripts/test_reflect_round.py"""
import contextlib, io, json, os, pathlib, sys, tempfile, time

TMP = pathlib.Path(tempfile.mkdtemp())
os.environ.update({"AGENT_SKILLS_CONFIG": str(TMP / "cfg.json"), "PROGRAMS_HOME": str(TMP / "programs")})
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reflect_round as rr
import skill_usage as su

HOME = TMP / "programs/_standing/reflect"


def run(*argv, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = rr.settle(**kw) if argv == ("settle",) else rr.main(list(argv))
    return code, out.getvalue()


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
code, out = run("begin")
assert code == 0 and (HOME / "lock").is_dir(), out
assert "new: acme 1 (signal)" in out and "rows:" in out, out
rows = [json.loads(l) for l in (HOME / "round-rows.jsonl").read_text().splitlines()]
assert {(r["ledger"], r["pointer"]) for r in rows} == {("acme", "msg_old"), ("_skill-usage", "skill-usage:x:misses@w1")}, rows
snap = json.loads((HOME / "round.json").read_text())
assert snap["newest"] == {"acme": "2026-09-23T00:00:00+00:00", "_skill-usage": "2026-09-23T01:00:00Z"}, snap
assert su.precheck(TMP / "programs", collect_first=False, orchestrator=lambda h: []) == 1, "the precheck sees the lock"

# A second begin while the lock is fresh skips with exit 1 and leaves the round's files alone.
code, out = run("begin")
assert code == 1 and "holds the lock" in out, out
assert json.loads((HOME / "round.json").read_text()) == snap

# A row with no evidence field is pointed to as <slug>:<ev>:<ts>:<pr or sha>.
assert rr.pointer("acme", {"ev": "main_red", "ts": "T", "sha": "abc"}) == "acme:main_red:T:abc"

# A row appended during the round is after begin's snapshot: end --advance leaves it for the next round.
ledger("acme", {"ev": "land_failed", "ts": "2026-09-24T00:00:00+00:00", "pr": 5})
code, out = run("end", "--advance", "--waiting", "https://github.com/acme/app/pull/9")
assert code == 0 and "lock released" in out, out
cursor = json.loads((HOME / "cursor.json").read_text())
assert cursor == snap["newest"], cursor
assert json.loads((HOME / "waiting.json").read_text()) == {"pr": "https://github.com/acme/app/pull/9"}
assert not (HOME / "lock").exists() and not (HOME / "round.json").exists() and not (HOME / "round-rows.jsonl").exists()
assert set(su.new_findings(TMP / "programs")) == {"acme"}, "the row appended mid-round is still new"

# The cursor never moves back.
(HOME / "cursor.json").write_text(json.dumps({"acme": "2026-10-01T00:00:00+00:00"}))
assert run("begin")[0] == 0
code, out = run("end", "--advance", "--clear-waiting")
assert code == 0 and "waiting: cleared" in out and not (HOME / "waiting.json").exists(), out
assert json.loads((HOME / "cursor.json").read_text())["acme"] == "2026-10-01T00:00:00+00:00"

# end --advance with no begin moves nothing and keeps any lock; plain end only releases.
(HOME / "lock").mkdir()
code, out = run("end", "--advance")
assert code == 2 and (HOME / "lock").is_dir(), out
code, out = run("end")
assert code == 0 and not (HOME / "lock").exists(), out

# A stale lock, including an older round's directory with a start-time file in it, is replaced.
(HOME / "lock").mkdir()
(HOME / "lock" / "started").write_text("2026-01-01T00:00:00Z")
old = time.time() - (su.LOCK_STALE_HOURS + 1) * 3600
os.utime(HOME / "lock", (old, old))
code, out = run("begin")
assert code == 0 and "stale lock" in out and not (HOME / "lock" / "started").exists(), out
assert run("end")[0] == 0

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

# Usage errors exit 2.
assert run("end", "--waiting")[0] == 2 and run("end", "--waiting", "u", "--clear-waiting")[0] == 2 and run("nope")[0] == 2

print("test_reflect_round: ok")
