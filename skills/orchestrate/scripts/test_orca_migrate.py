"""Offline tests for orca_migrate.py: the dry-run writes nothing, --apply backs up and closes only card doing lines
and is idempotent, --restore reopens what it closed and nothing the owner touched since.

Run: python3 test_orca_migrate.py
"""
import json, os, pathlib, stat, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-migrate-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import fleet, orca_migrate, tidy  # noqa: E402

sd = fleet.state_dir()
sd.mkdir(parents=True)


def card(sid, name, phase, slug=None, **kw):
    slug = slug or name
    return {"id": sid, "name": name, "kind": "standalone", "phase": phase, "parent": "s-coord", "task_title": name,
            "path": f"/w/{slug}", "branch": f"u/{slug}", "terminals": [{"handle": f"term_{sid}"}], **kw}


state = {"sessions": [card("s1", "ABC-1 로그인", "working", "abc-1-login"), card("s2", "abc-2-pay", "offline",
                                                                  linked_pr={"key": "acme/app#2", "state": "merged"}),
                      {"id": "s-lead", "name": "lead:x", "kind": "lead", "phase": "working", "terminals": [{"handle": "term_lead"}]}],
         "prs": []}
doing = [{"title": "ABC-1 로그인 카드 진행", "next": "리뷰"},                 # names one card: migrate closes it
         {"title": "abc-2-pay 결제", "next": ""},                             # its card's PR merged: the tick closes it
         {"title": "디자인 확인", "next": "", "waiting": "디자이너"},           # waits on a person: stays
         {"title": "abc-1-login 오너 확인", "owner": "오너"},                  # the owner's: stays
         {"title": "인프라 정리", "next": ""},                                 # names no card: stays
         {"title": "결제 화면", "owner": "abc-1-login"}]                       # --owner held the card's name: closes
board_store = {"v": 1, "rows": {}, "order": [], "doing": doing, "later": [], "todo": [], "owner_todo": [],
               "leads": {"lead:x": {"name": "lead:x", "handle": "term_lead", "repo": "acme/app"},
                         "lead:old": {"name": "lead:old", "handle": "term_gone", "repo": "acme/app", "closed_at": "2026-01-01T00:00:00Z"}}}
(sd / "state.json").write_text(json.dumps(state))
(sd / "board.json").write_text(json.dumps(board_store, ensure_ascii=False))
(sd / "decisions.json").write_text(json.dumps({"decisions": {"d1": {"id": "d1", "title": "배포 시각", "status": "open"}}}))
first, owned = tidy.item_id("doing", doing[0]), tidy.item_id("doing", doing[5])


def run(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), "migrate", *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


def snapshot():
    return {p.name: p.read_bytes() for p in sd.iterdir() if p.is_file()}


# Dry-run: lists, writes nothing.
before = snapshot()
code, out, err = run()
assert code == 0, err
assert snapshot() == before, "dry-run wrote a file"
p = orca_migrate.plan()
assert [c["id"] for c in p["close"]] == [first, owned], p
assert [c["id"] for c in p["tick"]] == [tidy.item_id("doing", doing[1])], p
assert [l["name"] for l in p["leads"]] == ["lead:x"] and p["leads"][0]["in_fleet"], p
assert [d["id"] for d in p["decisions"]] == ["d1"], p
assert "Orca 카드 ABC-1 로그인 (working)" in out and "dry-run" in out, out

# Apply: backup first, then one done line by migrate; board.json untouched.
code, out, err = run("--apply")
assert code == 0, err
backups = [d for d in sd.iterdir() if d.name.startswith("backup-orca-v2-")]
assert len(backups) == 1, backups
bk = backups[0]
assert stat.S_IMODE(bk.stat().st_mode) == 0o700
for name in ("board.json", "decisions.json"):
    assert (bk / name).read_bytes() == before[name], name
    assert stat.S_IMODE((bk / name).stat().st_mode) == 0o600
assert not (bk / "tidy.jsonl").exists()  # there was none before
assert json.loads((bk / "migrate.json").read_text())["closed"] == [first, owned]
assert (sd / "board.json").read_bytes() == before["board.json"]
cur = tidy.latest()[first]
assert cur["ev"] == "done" and cur["by"] == "migrate" and cur["rule"] == "moved_to_orca", cur
assert f"--restore {bk}" in out, out

# Idempotent: nothing left, no second backup.
code, out, err = run("--apply")
assert code == 0 and "nothing to migrate" in out, (out, err)
assert len([d for d in sd.iterdir() if d.name.startswith("backup-orca-v2-")]) == 1

# Restore: reopens it; the board reads the line open again.
code, out, err = run("--restore", str(bk))
assert code == 0, err
assert tidy.latest()[first]["ev"] == "reopen" and first in out and "orch board reopen" in out, out
# A reopened line is the owner's call from now on: a new apply leaves it.
assert orca_migrate.plan()["close"] == []
code, out, err = run("--restore", str(bk))
assert code == 0 and "reopened 0" in out and first in out, out  # nothing closed by migrate now: skipped

# A close the owner made after migrate is not undone by restore.
tidy.append([{"ev": "done", "id": first, "ts": fleet.iso(fleet.utcnow()), "by": "owner", "rule": "manual",
              "evidence": "오너", "label": "x"}], tidy.latest())
ids, skipped = orca_migrate.restore(bk)
assert ids == [] and skipped == [first, owned]

# Bad input.
code, out, err = run("--restore", str(tmp))
assert code != 0 and "migrate.json" in err, err
code, out, err = run("--bogus")
assert code != 0 and "usage" in err, err
print("ok")
