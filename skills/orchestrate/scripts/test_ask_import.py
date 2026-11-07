"""Offline tests for `orch ask import`: a legacy review-outbox log on invented lines becomes asks, once, and nothing that ever
left the queue is queued again.

Run: python3 test_ask_import.py
"""
import json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-ask-import-"))
os.environ.update({"ORCH_BRIEF_LAST": str(tmp / "brief-last"), "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "TZ": "UTC"})
sys.path.insert(0, str(HERE))
import ask, board, store  # noqa: E402

LOG = """\
2026-01-05T10:14:00Z acme/app-a#101 head a1b2c3d reviewer=alice(al; team-x) queued; 1 pending for al, not urgent
2026-01-05T10:14:00Z acme/app-b#102 head b2c3d4e reviewer=bob(bo) NOT asked: reviewDecision APPROVED
2026-01-05T11:19:36Z acme/app-a#103 head c3d4e5f reviewer=alice(al) SENT CFAKE ts 1700000100.000200 (new top-level)
2026-01-05T12:47:21Z acme/app-a#105 head d4e5f6a reviewer=bob(bo) BATCH Mon 2026-01-12: compose; SCHEDULED CFAKE id Q1 post_at 1700100000
2026-01-06T14:06:30Z acme/app-a#106 head e5f6a7b reviewer=bob(bo) ON HOLD 2026-01-08 owner: only when told; queued, NOT sent (was: reviewer=alice(al) queued)
2026-01-06T23:26:14Z acme/app-a#107 head 0a1b2c3 reviewer=alice(al) WITHDRAWN 2026-01-08 owner: moved (was: queued, NOT sent)
2026-01-07 09:00 acme/app-c#108 https://example.test/pull/108 → <!subteam^SFAKE> (team-x) planned
2026-01-07 09:00 CHECK acme/app-b#109 → bob: no review since request
2026-01-07T04:40:30Z acme/app-c#108 team-x queued ask SUPERSEDED, sent to bo instead

2026-01-09 DROPPED (all MERGED): app-a#101 (1/7)
2026-01-10T09:00:00Z acme/app-d#110 head 1111111 reviewer=carol(ca) queued
"""

root = tmp / "pc"
(root / "fleet").mkdir(parents=True)
(root / "shared" / "prs").mkdir(parents=True)
os.environ["ORCH_FLEET_STATE"] = str(root / "fleet")
os.environ["AGENT_SKILLS_STATE"] = str(root / "shared")
cfg = root / "agent-skills.json"
cfg.write_text(json.dumps({"brief": {"sendlog": str(root / "shared" / "prs" / "sendlog.jsonl")}}))
os.environ["AGENT_SKILLS_CONFIG"] = str(cfg)
board.SKILLS_CONFIG = cfg
store.import_apply(checks=False)
store.state_setup()
(root / "shared" / "prs" / "sendlog.jsonl").write_text("")
store.sendlog_setup()
log = tmp / "review-outbox.log"
log.write_text(LOG)

# only the two plain queued lines are asks; every other line is counted with its reason
rows, skipped = ask.import_outbox(log, apply=False)
assert [(pr, who) for pr, who, *_ in rows] == [("acme/app-a#101", "alice"), ("acme/app-d#110", "carol")], rows
assert skipped == {"not asked": 1, "sent": 1, "batch": 1, "on hold": 1, "withdrawn": 1, "unparsed": 2, "check": 1,
                   "superseded": 1, "blank": 1}, skipped
# "ON HOLD" must beat the later "queued, NOT sent" text, and the "(was: ...)" history must not count
assert store.asks() == [], "a dry run writes nothing"

rows, _ = ask.import_outbox(log, apply=True)
assert [r[3] for r in rows] == [True, True]
assert sorted(a["pr"] for a in store.asks(status="queued")) == ["acme/app-a#101", "acme/app-d#110"]
rows, _ = ask.import_outbox(log, apply=True)
assert [r[3] for r in rows] == [False, False] and len(store.asks()) == 2, "importing twice adds nothing"
assert store.asks()[0]["by"] == "orch ask import"

# a line that says queued but also carries a later SCHEDULED is not queued
assert ask.outbox_line("2026-01-05T10:14:00Z acme/app-a#1 head a1b2c3d reviewer=al(a) queued, NOT sent; SCHEDULED later")[2] == "scheduled"

print("test_ask_import: ok")
