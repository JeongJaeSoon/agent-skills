"""`skills-sync card` against a fake orca, a fake orch and the pressure gate on canned load. Run: python3 scripts/test_card.py"""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-card-")).resolve()
CALLS = tmp / "calls.jsonl"
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_CARD_CALLS"], "a") as f:
    f.write(json.dumps({"bin": os.path.basename(sys.argv[0]), "argv": a, "stdin": sys.stdin.read() if "-" in a else None}) + "\n")
mode = os.environ.get("FAKE_CARD_MODE", "")
if a[:2] == ["worktree", "create"]:
    if mode == "create-fails":
        print(json.dumps({"ok": False, "error": {"code": "repo_not_found", "message": "no repo x"}})); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"worktree": {"path": os.environ["FAKE_CARD_PATH"]}, "agentTerminalHandle": "term_new"}}))
elif a[:2] == ["sent", "add"] and mode == "sent-fails":
    print("disk full", file=sys.stderr); sys.exit(1)
elif a[:2] == ["orchestration", "run-current"]:
    print(json.dumps({"ok": True, "result": {"run": None if mode in ("no-run", "worker-lead") else {"id": "run_me"}}}))
elif a[:2] == ["orchestration", "worker-list"]:
    # A lead started with worker-start: unbound (run-current null), its own Dispatch on its coordinator's Run.
    mine = [{"agentTerminalHandle": "term_me", "runId": "run_top", "dispatchId": "ctx_lead"}] if mode == "worker-lead" else []
    print(json.dumps({"ok": True, "result": {"workers": [{"agentTerminalHandle": "term_other", "runId": "run_x"}] + mine}}))
elif a[:2] == ["orchestration", "worker-start"]:
    if mode == "unmanaged" and "new-child" in a:
        print(json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "The dispatching terminal's "
              "workspace could not be resolved to a git worktree Orca manages, which --worktree new-child needs"}}))
        sys.exit(1)
    if mode == "start-fails":
        print(json.dumps({"ok": False, "error": {"code": "outcome_unknown", "message": "turn not seen",
                                                 "data": {"orchestrationRequestId": "req_1"}},
                          "result": {"failedStage": "turn_start", "recovery": ["orca orchestration worker-stop --dispatch ctx_1"]}}))
        sys.exit(1)
    # Orca 1.4.239's receipt: the worktree and the agent terminal appear only as effects
    effects = [] if mode == "no-path" else [{"kind": "worktree", "action": "created_top_level",
                                             "id": "repo1::" + os.environ["FAKE_CARD_PATH"]}]
    effects.append({"kind": "terminal", "role": "agent", "action": "created", "id": "term_w"})
    print(json.dumps({"ok": True, "result": {"runId": "run_me", "dispatchId": "ctx_1", "state": "ready", "effects": effects}}))
elif a[:2] == ["reference", "add"] and mode == "ref-fails":
    print("not a url", file=sys.stderr); sys.exit(1)
'''
for name in ("orca", "orch"):
    (tmp / name).write_text(FAKE)
    (tmp / name).chmod(0o755)
os.environ.update(SKILLS_SYNC_ORCA=str(tmp / "orca"), SKILLS_SYNC_ORCH=str(tmp / "orch"), FAKE_CARD_CALLS=str(CALLS))
os.environ.pop("AGENT_SKILLS_PRESSURE_GATE", None)
CARD_DIR = tmp / "card"  # the created card: a checkout whose origin names the GitHub repo for the Sent log
subprocess.run(["git", "init", "-q", str(CARD_DIR)], check=True)
subprocess.run(["git", "-C", str(CARD_DIR), "remote", "add", "origin", "git@github.com:acme/app.git"], check=True)
os.environ["FAKE_CARD_PATH"] = str(CARD_DIR)

# The real pressure module and its queue in a throwaway state dir; only the measurement is canned.
os.environ.update(AGENT_SKILLS_STATE=str(tmp / "state"), AGENT_SKILLS_CONFIG=str(tmp / "none.json"),
                  ORCA_TERMINAL_HANDLE="term_me")
sys.path.insert(0, str(HERE.parent / "skills" / "reap-resources" / "scripts"))
import pressure  # noqa: E402
LEVEL = {"now": "ok"}
pressure.snapshot = lambda cfg, st, *a, **k: {"load_per_cpu": 3.1}
pressure.judge = lambda m, cfg: (LEVEL["now"], ["load_per_cpu 3.1"])
pressure.blocked_by = lambda m, cfg: ["load_per_cpu"]
sys.path.insert(0, str(HERE))
import card  # noqa: E402
import sqlite3  # noqa: E402

ORCA_DATA = tmp / "orca-data"  # never the real Orca profile store
os.environ["AGENT_SKILLS_ORCA_DATA_DIR"] = str(ORCA_DATA)


def orca_depth(depth):
    """Orca's settings store with Nested worker depth `depth`, or none at all for None."""
    db = ORCA_DATA / "profiles" / "local-default" / "profile-state.db"
    if db.exists():
        db.unlink()
    if depth is None:
        return
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table profile_state_documents (domain text primary key, payload text)")
    con.execute("insert into profile_state_documents values ('settings', ?)", (json.dumps({"nestedWorkerMaxDepth": depth}),))
    con.commit()
    con.close()

CLOSE = "CLOSE: 완료 기준을 채우고 결과를 적으면 end-session §8대로 같은 턴에 스스로 닫는다"
WORKER = f"# card brief\n\n/goal ship the helper and open its PR\n\nAPPROVED: push and PR (owner, 2026-01-01)\n\n{CLOSE}\n"
LEAD = """acme: lead

/goal run the acme program to its predicate
MODE        Run `orchestrate` in program mode for this project.
ROLE: lead acme
INBOX       Keep exactly one background inbox wait running (`orch wait acme`).
DELEGATE    You do not investigate or implement yourself.
            Each goes one layer down.
PREDICATE   3 PRs merged.
TASKS       `task-create --deps`, then `task-list --ready`, then `worker-start --task`.
GATE        A human decision is `gate-create` on its task.
MODEL CALLS As in "Single worker".
CLOSE       Once the predicate holds: `end-session` §8.
"""


def brief(text, name="brief.md"):
    p = tmp / name
    p.write_text(text)
    return str(p)


def go(*argv, mode=""):
    os.environ["FAKE_CARD_MODE"] = mode
    CALLS.write_text("")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = card.main(list(argv))
    calls = [json.loads(l) for l in CALLS.read_text().splitlines()]
    return rc, out.getvalue(), err.getvalue(), calls


def prompt_of(calls):
    a = next(c["argv"] for c in calls if c["argv"][:2] == ["worktree", "create"])
    return a[a.index("--prompt") + 1]


# A worker brief: the /goal line with the brief path, APPROVED and CLOSE written out; logged, named, JSON printed.
path = brief(WORKER)
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--display-name", "ABC-1 helper")
assert rc == 0, err
p = prompt_of(calls)
assert p.splitlines()[0] == f"/goal ship the helper and open its PR 브리프: {path}", p
assert "APPROVED: push and PR (owner, 2026-01-01)" in p and CLOSE in p and "# card brief" not in p, p
create = calls[0]["argv"]
assert create[create.index("--parent-worktree") + 1] == "active" and "--json" in create
assert calls[1]["bin"] == "orch" and calls[1]["argv"][:4] == ["sent", "add", "--to", "acme-helper"] and calls[1]["stdin"] == p
assert calls[1]["argv"][calls[1]["argv"].index("--repo") + 1] == "acme/app", calls[1]
assert calls[2]["argv"][:4] == ["worktree", "set", "--worktree", f"path:{CARD_DIR}"], calls[2]
assert json.loads(out)["agentTerminalHandle"] == "term_new"

# A lead brief: every lead row, continuation lines kept, MODEL CALLS not mistaken for MODE.
rc, out, err, calls = go(brief(LEAD, "lead.md"), "--repo", "name:acme", "--name", "acme-lead", "--no-parent")
assert rc == 0, err
p = prompt_of(calls)
for line in ("MODE        Run", "ROLE: lead acme", "INBOX ", "            Each goes one layer down.", "PREDICATE ",
             "TASKS ", "GATE ", "CLOSE       Once"):
    assert line in p, (line, p)
assert "MODEL CALLS" not in p and "--parent-worktree" not in calls[0]["argv"] and "--no-parent" in calls[0]["argv"]

# Missing lines: nothing created, exit 2, the missing line named.
for text, want in [(WORKER.replace(CLOSE, "## Close"), "CLOSE or KEEP"),
                   (WORKER.replace("/goal ", "goal: "), "/goal"),
                   (LEAD.replace("GATE ", "NOTE "), "GATE row"),
                   (LEAD.replace("TASKS ", "NOTE "), "TASKS row"),
                   (LEAD.replace("ROLE: lead acme", "ROLE: lead <slug>"), "ROLE: lead <slug>")]:
    rc, out, err, calls = go(brief(text, "bad.md"), "--repo", "name:acme", "--name", "x")
    assert rc == 2 and want in err and not calls, (want, rc, err, calls)
assert go(str(tmp / "absent.md"), "--repo", "r", "--name", "x")[0] == 2
assert go(path, "--name", "x")[0] == 2  # --repo is required
assert go(path, "--repo", "r", "--name", "x", "--bogus")[0] == 2

# KEEP stands in for CLOSE.
assert go(brief(WORKER.replace(CLOSE, "KEEP: reused for the next ticket"), "keep.md"), "--repo", "r", "--name", "x")[0] == 0

# The pressure gate's real decision on the composed command: danger refuses with exit 3 and creates nothing.
# The deny queues it like the hook does; the same `skills-sync card` run again once load drops finds that entry.
LEVEL["now"] = "danger"
rc, out, err, calls = go(path, "--repo", "r", "--name", "x")
assert rc == 3 and "[gate:pressure]" in err and err.startswith("[skills-sync card]") and not calls, (rc, err, calls)
(entry,) = pressure.queue_read().values()
assert entry["state"] == "waiting" and entry["session"] == "term_me" and f"queue wait {entry['id']}" in err, (entry, err)
os.environ["AGENT_SKILLS_PRESSURE_GATE"] = "0"
assert go(path, "--repo", "r", "--name", "y")[0] == 0
del os.environ["AGENT_SKILLS_PRESSURE_GATE"]
LEVEL["now"] = "ok"
assert go(path, "--repo", "r", "--name", "x")[0] == 0
assert pressure.queue_read()[entry["id"]]["state"] == "done", pressure.queue_read()

# A failed create is exit 4 with orca's reason; a failed log after a create is exit 5, the create output still printed.
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="create-fails")
assert rc == 4 and "no repo x" in err and len(calls) == 1, (rc, err)
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="sent-fails")
assert rc == 5 and "disk full" in err and json.loads(out)["worktree"]["path"] == str(CARD_DIR), (rc, err)

# An Approved heading or bullet (dispatch-card §2's prose form) without an `APPROVED:` row would drop the approval.
for prose in ("- **Approved** — merge (owner, 2026-01-01)", "## Approved\nmerge"):
    rc, out, err, calls = go(brief(WORKER.replace("APPROVED: push and PR (owner, 2026-01-01)", prose), "bad.md"), "--repo", "r", "--name", "x")
    assert rc == 2 and "APPROVED:" in err and not calls, (prose, rc, err)

# Prose that opens with a key is not a row: only `KEY:` or the template's wide gap counts.
rc, out, err, calls = go(brief(WORKER.replace(CLOSE, "KEEP the old API until v2 ships."), "bad.md"), "--repo", "r", "--name", "x")
assert rc == 2 and "CLOSE or KEEP" in err, err

# The Lead template quoted in a fence is neither the brief's rows nor a lead head.
quoted = WORKER + "\nThe lead's template, for reference:\n```\nacme: lead\n" + LEAD + "```\n"
rc, out, err, calls = go(brief(quoted, "quoted.md"), "--repo", "r", "--name", "x")
p = prompt_of(calls)
assert rc == 0 and "MODE" not in p and "TASKS" not in p and p.count("CLOSE") == 1, (rc, err, p)

# PROGRAM goes in after the goal; a /goal line that already names its brief gets no second one.
rc, out, err, calls = go(brief("/goal fix X. 브리프: ~/notes/x.md\nPROGRAM: acme\n" + CLOSE + "\n", "prog.md"), "--repo", "r", "--name", "x")
p = prompt_of(calls)
assert rc == 0 and p.splitlines()[:2] == ["/goal fix X. 브리프: ~/notes/x.md", "PROGRAM: acme"], p

# A brief reporting to a Run, from a session bound to one: a supervised worker. Its spec drops the leading /goal, the
# display name goes on the start, the ticket is linked on that new card only, and no Sent line is written.
SUPERVISED = WORKER + "REPORT: orca orchestration send --to run:run_me --type status at each milestone\n"
URL = "https://linear.app/acme/issue/ABC-1"
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "name:acme", "--name", "abc-1", "--display-name", "ABC-1 helper",
                         "--reference", URL)
assert rc == 0, err
start = next(c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"])
spec = start[start.index("--spec") + 1]
assert spec.startswith("ship the helper and open its PR 브리프:") and "/goal" not in spec.splitlines()[0], spec
assert start[start.index("--worktree") + 1] == "new-child" and start[start.index("--display-name") + 1] == "ABC-1 helper"
assert start[start.index("--timeout-ms") + 1] == "840000", start
assert [c["argv"] for c in calls if c["argv"][:2] == ["reference", "add"]] == [["reference", "add", URL, "--worktree", f"path:{CARD_DIR}", "--json"]]
assert not any(c["bin"] == "orch" for c in calls) and not any(c["argv"][:2] == ["worktree", "create"] for c in calls)
assert json.loads(out)["card"] == {"kind": "worker", "path": str(CARD_DIR), "handle": "term_w", "run": {"id": "run_me"}}, out
# A failed reference after the start is exit 5: the worker runs, so it is not started again.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="ref-fails")
assert rc == 5 and "Do not create it again" in err and json.loads(out)["card"]["kind"] == "worker", (rc, err)
# A start Orca reports done whose receipt names no worktree is still a running worker: exit 5, not 4.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="no-path")
assert rc == 5 and "Do not create it again" in err and json.loads(out)["card"]["handle"] == "term_w", (rc, err)
assert not any(c["argv"][:2] == ["reference", "add"] for c in calls)
# A REPORT row continued onto a second line is read whole.
assert card.report_to("REPORT: orca orchestration send \\\n  --to term_x --type status\n") == ["term_x"]
# From a terminal outside a worktree Orca manages, new-child is refused before anything exists: new-top-level instead.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="unmanaged")
starts = [c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"]]
assert rc == 0 and [s[s.index("--worktree") + 1] for s in starts] == ["new-child", "new-top-level"], (rc, err, starts)
# --no-parent asks for no lineage: new-top-level from the start.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--no-parent")
starts = [c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"]]
assert rc == 0 and [s[s.index("--worktree") + 1] for s in starts] == ["new-top-level"] and "--no-parent" not in starts[0], starts
# Any other refusal is not retried: a second start could duplicate the worker.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="start-fails")
assert rc == 4 and sum(c["argv"][:2] == ["orchestration", "worker-start"] for c in calls) == 1, (rc, calls)
# A failed start keeps Orca's whole receipt (stage, recovery commands) on stderr.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="start-fails")
assert rc == 4 and "worker-start failed: outcome_unknown" in err and "worker-stop --dispatch ctx_1" in err, err
# The mutation's request id is Orca's own answer to "did it start?": request-show reads it without starting anything.
assert "orca orchestration request-show --request req_1 --json" in err.splitlines()[-1], err
# A create failure carries no request id, so no request-show line.
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="create-fails")
assert rc == 4 and "request-show" not in err, err
# No Run bound: a /goal card with a warning, the reference on the create. --card forces a card too.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="no-run")
create = next(c["argv"] for c in calls if c["argv"][:2] == ["worktree", "create"])
assert rc == 0 and "bound to none" in err and create[:2] == ["worktree", "create"] and create[create.index("--reference") + 1] == URL, (err, calls)
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--card")
assert rc == 0 and calls[0]["argv"][:2] == ["worktree", "create"] and json.loads(out)["card"]["handle"] == "term_new"
# A lead brief reporting to a Run: a dispatched worker once Nested worker depth reaches 3 (its workers' helpers sit
# at depth 3), its spec carrying every lead row; below that, or with Orca's settings unreadable, a /goal card that
# says why and what to change.
LEAD_RUN = LEAD.replace("CLOSE       Once", "PARENT      orca orchestration send --to run:run_top\nCLOSE       Once")
orca_depth(3)
rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead")
start = next(c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"])
spec = start[start.index("--spec") + 1]
assert rc == 0 and start[start.index("--worktree") + 1] == "new-child", (rc, err, calls)
assert spec.startswith("run the acme program") and "ROLE: lead acme" in spec and "DELEGATE " in spec and "CLOSE " in spec, spec
for depth, want in ((2, "Nested worker depth"), (None, "unread")):
    orca_depth(depth)
    rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead", "--no-parent")
    assert rc == 0 and calls[0]["argv"][:2] == ["worktree", "create"] and want in err, (depth, err, calls)
assert "3으로" in card.orca_settings.suggestions({"nestedWorkerMaxDepth": 2})[0]
orca_depth(3)
rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead", "--card")
assert rc == 0 and calls[0]["argv"][:2] == ["worktree", "create"], calls
# A human-gate program lands through Orca gates, which a dispatched lead cannot create: a card at any depth.
rc, out, err, calls = go(brief(LEAD_RUN + "Merge policy: human-gate.\n", "lead-gate.md"), "--repo", "r", "--name", "acme-lead")
assert rc == 0 and calls[0]["argv"][:2] == ["worktree", "create"] and "worker_cannot_coordinate_own_run" in err, (err, calls)
assert card.HUMAN_GATE.search("--merge-policy human-gate") and card.HUMAN_GATE.search("Merge policy is human-gate.") and not card.HUMAN_GATE.search("No human-gate here; merge policy: auto.")
# A lead that is itself a dispatched worker (run-current null) starts its units as workers on the Run it works for.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="worker-lead")
assert rc == 0 and any(c["argv"][:2] == ["orchestration", "worker-start"] for c in calls), (err, calls)
assert json.loads(out)["card"]["run"] == {"id": "run_top", "via": "own_dispatch"}, out
# A report address that is a terminal handle dies with its terminal: refused before anything runs, in any line.
rc, out, err, calls = go(brief(WORKER + "REPORT: `orca orchestration send --to term_abc --type status`\n", "term.md"), "--repo", "r", "--name", "x")
assert rc == 2 and "term_abc" in err and not calls, (rc, err)
rc, out, err, calls = go(brief(WORKER + "- 보고: orca orchestration send --to term_abc (실패하면 orch inbox)\n", "term2.md"), "--repo", "r", "--name", "x")
assert rc == 2 and "term_abc" in err and "terminal_not_found" in err and not calls, (rc, err)
assert card.report_to("REPORT  a digest to ~/r.md\n") == [] and card.report_to("PARENT: send --to run:x\n") == []

print("ok")
