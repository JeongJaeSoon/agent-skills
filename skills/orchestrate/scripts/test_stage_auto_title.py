"""A predicate item whose PR the ledger never ties to its ticket (the program's events name the PR on `landed` and the
ticket only in `config`) still gets that PR when the PR's title or branch names the ticket, so a merged one fills the
merge cell and the row ends. Invented data.

Run: python3 test_stage_auto_title.py
"""
import datetime as dt, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-stage-auto-title-"))
os.environ.update({"ORCH_FLEET_STATE": str(tmp / "dashboard"), "PROGRAMS_HOME": str(tmp / "programs"),
                   "ORCH_FLEET_CONFIG": str(tmp / "config.json"), "AGENT_SKILLS_STATE": str(tmp / "state")})
sys.path.insert(0, str(HERE))
import fleet, stage_auto, stages  # noqa: E402

now = fleet.utcnow().replace(microsecond=0)
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731

d = tmp / "programs" / "kappa"
d.mkdir(parents=True)
(d / "program.json").write_text(json.dumps({"slug": "kappa", "repo": "acme/app", "run": "run_k", "created_at": ago(48),
                                            "predicate": ["K-1", "K-2", "K-3", "K-4"]}))
events = [{"ts": ago(40), "ev": "config", "note": "predicate=K-1,K-2,K-3,K-4"},
          {"ts": ago(20), "ev": "landed", "pr": 21, "sha": "a" * 40}]  # PR only: no ticket on the event
(d / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

pr = lambda n, state, title, branch="dev/misc", created=10, **kw: {  # noqa: E731
    "key": f"acme/app#{n}", "repo": "acme/app", "state": state, "title": title, "branch": branch, "created": ago(created), **kw}
state = {"tasks": [], "sessions": [], "prs": [
    pr(21, "MERGED", "K-1: add the thing", merged_at=ago(20), merge_sha="a" * 40),
    pr(22, "MERGED", "K-2: first half", merged_at=ago(15), merge_sha="b" * 40),
    pr(23, "OPEN", "K-2: second half"),
    pr(24, "MERGED", "K-30: another ticket whose key starts with K-3", merged_at=ago(5), merge_sha="c" * 40),
    pr(25, "MERGED", "K-4: older than the program", created=500, merged_at=ago(400), merge_sha="d" * 40)]}
# A PR the ledger (lambda) or a session (mu) already gave to one row is not also given to the item its title leads with.
for slug, run, evs in (("lambda", "run_l", [{"ts": ago(9), "ev": "ready", "pr": 31, "ticket": "L-2"},
                                            {"ts": ago(8), "ev": "landed", "pr": 31, "sha": "e" * 40}]),
                       ("mu", "run_m", [{"ts": ago(9), "ev": "config"}])):
    p = tmp / "programs" / slug
    p.mkdir()
    (p / "program.json").write_text(json.dumps({"slug": slug, "repo": "acme/app", "run": run, "created_at": ago(48),
                                                "predicate": [f"{slug[0].upper()}-1", f"{slug[0].upper()}-2"]}))
    (p / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in evs))
state["tasks"] = [{"id": "task_m", "run": "run_m", "title": "M-2: the work", "status": "dispatched"}]
state["sessions"] = [{"id": "s-m", "run": "run_m", "task": "task_m"}]
state["prs"] += [pr(31, "MERGED", "L-1: umbrella title", merged_at=ago(8), merge_sha="e" * 40),
                 pr(32, "MERGED", "M-1: umbrella title", session="s-m", merged_at=ago(8), merge_sha="f" * 40)]
rows = stage_auto.refresh(state, now)
assert rows["lambda/L-2"]["pr"] == "acme/app#31" and rows["lambda/L-1"]["pr"] is None, rows["lambda/L-1"]
assert rows["mu/M-2"]["pr"] == "acme/app#32" and rows["mu/M-1"]["pr"] is None, rows["mu/M-1"]

assert rows["kappa/K-1"]["pr"] == "acme/app#21", rows["kappa/K-1"]
assert rows["kappa/K-1"]["cells"]["merge"]["mark"] == "ok"
assert rows["kappa/K-2"]["pr"] == "acme/app#23" and "merge" not in rows["kappa/K-2"]["cells"], "an open PR keeps it open"
assert rows["kappa/K-3"]["pr"] is None, "K-3 is not K-30"
assert rows["kappa/K-4"]["pr"] is None, "a PR from before the program is not its PR"

table = {r["id"]: r for r in stages.table(state)["rows"]}
assert table["kappa/K-1"]["done_at"], "merged, dev and prod 해당 없음: the row ends"
assert not table["kappa/K-2"].get("done_at") and not table["kappa/K-3"].get("done_at")

print("test_stage_auto_title: ok")
