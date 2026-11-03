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
if a[:2] == ["repo", "show"]:
    print(json.dumps({"ok": True, "result": {"repo": {"id": "repo1"}}})); sys.exit(0)
if a[:2] == ["worktree", "current"]:
    print(json.dumps({"ok": True, "result": {"worktree": {"repoId": "repo1", "path": "/wt/me"}}})); sys.exit(0)
if a[:2] == ["worktree", "show"]:
    print(json.dumps({"ok": True, "result": {"worktree": {"parentWorktreeId": "repo1::/wt/lead"}}})); sys.exit(0)
if a[:2] == ["worktree", "ps"]:
    if mode == "ps-down":
        print("connect ECONNREFUSED", file=sys.stderr); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"worktrees": json.loads(os.environ.get("FAKE_CARD_PS", "[]"))}})); sys.exit(0)
if a[:2] == ["terminal", "list"]:
    print(json.dumps({"ok": True, "result": {"terminals": [{"handle": "term_old", "agentIdentity": "claude"}]}})); sys.exit(0)
if a[:2] == ["terminal", "wait"]:
    print(json.dumps({"ok": True, "result": {}})); sys.exit(0)
if a[:2] == ["terminal", "show"] and a[a.index("--terminal") + 1] == "term_new":
    print(json.dumps({"ok": True, "result": {"terminal": {"title": "✳ Claude"}}})); sys.exit(0)
if a[:2] == ["terminal", "read"]:
    rule = "─" * 20
    taken = ["❯ /goal ABC-2. CLOSE: …", "", "⏺ Reading the ticket."] if mode != "trust" else []
    box = ["Do you trust this folder?", "❯ 1. Yes, proceed"] if mode == "trust" else [rule, "❯ ", rule]
    print(json.dumps({"ok": True, "result": {"terminal": {"tail": taken + box}}})); sys.exit(0)
if a[:2] == ["worktree", "create"]:
    if mode == "parent-missing":
        print(json.dumps({"ok": False, "error": {"code": "LINEAGE_PARENT_NOT_FOUND", "message": "Parent selector was not found."}})); sys.exit(1)
    if mode == "create-fails":
        print(json.dumps({"ok": False, "error": {"code": "repo_not_found", "message": "no repo x"}})); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"worktree": {"path": os.environ["FAKE_CARD_PATH"]}, "agentTerminalHandle": "term_new"}}))
elif a[:2] == ["sent", "add"] and mode == "sent-fails":
    print("disk full", file=sys.stderr); sys.exit(1)
elif a[:2] == ["terminal", "show"]:
    if mode == "stale-terminal":
        print(json.dumps({"ok": False, "error": {"code": "terminal_handle_stale", "message": "gone"}})); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"terminal": {"handle": a[a.index("--terminal") + 1], "worktreeId": "repo1::/wt/me"}}}))
elif a[:2] == ["orchestration", "run-current"]:
    if mode == "orca-down":
        print("connect ECONNREFUSED", file=sys.stderr); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"run": None if mode in ("no-run", "worker-lead", "worker-lead-terminal", "worker-list-down") else {"id": "run_me"}}}))
elif a[:2] == ["orchestration", "worker-list"] and mode == "worker-list-down":
    print("connect ECONNREFUSED", file=sys.stderr); sys.exit(1)
elif a[:2] == ["orchestration", "worker-list"]:
    # A lead started with worker-start: unbound (run-current null), its own Dispatch on its coordinator's Run.
    mine = [{"agentTerminalHandle": "term_me", "runId": "run_top", "dispatchId": "ctx_lead"}] if mode.startswith("worker-lead") else []
    rows = [{"agentTerminalHandle": "term_other", "runId": "run_x"}] + mine
    if mode == "worker-lead-terminal":  # Orca 1.4.246 names the handle `terminal`
        rows = [{"terminal": r.pop("agentTerminalHandle"), **r} for r in rows]
    print(json.dumps({"ok": True, "result": {"workers": rows}}))
elif a[:2] == ["orchestration", "worker-start"]:
    if mode == "unmanaged" and "new-child" in a:
        print(json.dumps({"ok": False, "error": {"code": "invalid_argument", "message": "The dispatching terminal's "
              "workspace could not be resolved to a git worktree Orca manages, which --worktree new-child needs"}}))
        sys.exit(1)
    if mode == "start-fails-dispatch":
        print(json.dumps({"ok": False, "error": {"code": "outcome_unknown", "message": "turn not seen", "data": {
            "orchestrationRequestId": "req_2", "dispatchId": "ctx_2", "recovery": {"queryCommand": [
                "orca", "orchestration", "worker-show", "--dispatch", "ctx_2", "--json"]}}}}))
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
SNAP = []  # the kwargs each gate check measured with
pressure.snapshot = lambda cfg, st, *a, **k: SNAP.append(k) or {"load_per_cpu": 3.1}
pressure.judge = lambda m, cfg: (LEVEL["now"], ["load_per_cpu 3.1"])
pressure.blocked_by = lambda m, cfg: ["load_per_cpu"]
pressure.active_turns = lambda run=None: 0  # the new-card turn cap would read this machine's Orca
sys.path.insert(0, str(HERE))
import card  # noqa: E402
import sqlite3  # noqa: E402

# The background close-setup would poll the fake orca after the test moved on: recorded instead.
SETUPS = []
REAL_START_CLOSE_SETUP = card.start_close_setup
card.start_close_setup = lambda path, handle, name: SETUPS.append((path, handle, name)) or {"log": f"{name}.log"}

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
MERGE       merge_policy autonomous
TASKS      `task-create --deps`, then `task-list --ready`, then `worker-start --task`.
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


def creates(calls):
    return [c["argv"] for c in calls if c["argv"][:2] in (["worktree", "create"], ["orchestration", "worker-start"])]


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
# The parent is this terminal's own worktree, looked up by its handle: not `active`, not a path.
assert calls[0]["argv"] == ["terminal", "show", "--terminal", "term_me", "--json"], calls[0]
create = calls[1]["argv"]
assert create[create.index("--parent-worktree") + 1] == "id:repo1::/wt/me" and "--json" in create
assert calls[2]["bin"] == "orch" and calls[2]["argv"][:4] == ["sent", "add", "--to", "acme-helper"] and calls[2]["stdin"] == p
assert calls[2]["argv"][calls[2]["argv"].index("--repo") + 1] == "acme/app", calls[2]
assert calls[3]["argv"][:4] == ["worktree", "set", "--worktree", f"path:{CARD_DIR}"], calls[3]
assert json.loads(out)["agentTerminalHandle"] == "term_new" and json.loads(out)["card"]["parent"] == "id:repo1::/wt/me"
# A lookup that fails, or no handle at all, creates nothing and prints the choices.
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="stale-terminal")
assert rc == 6 and not creates(calls) and "terminal_handle_stale" in err and "--no-parent" in err and "  1. " in err, (rc, err)
os.environ.pop("ORCA_TERMINAL_HANDLE")
rc, out, err, calls = go(path, "--repo", "r", "--name", "x")
os.environ["ORCA_TERMINAL_HANDLE"] = "term_me"
assert rc == 6 and not calls and "ORCA_TERMINAL_HANDLE" in err, (rc, err)
# An explicit id parent is passed as given; a path parent is refused before any call.
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", "--parent-worktree", "id:repo1::/wt/lead")
assert rc == 0 and creates(calls)[0][creates(calls)[0].index("--parent-worktree") + 1] == "id:repo1::/wt/lead", calls
assert not any(c["argv"][:2] == ["terminal", "show"] for c in calls)
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="parent-missing")
assert rc == 4 and len(creates(calls)) == 1 and "Nothing was created" in err and "--no-parent" in err, (rc, err)
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", "--parent-worktree", "path:/srv/shared")
assert rc == 2 and "id:<worktree id>" in err and not calls, (rc, err)

# A lead brief: every lead row, continuation lines kept, MODEL CALLS not mistaken for MODE.
rc, out, err, calls = go(brief(LEAD, "lead.md"), "--repo", "name:acme", "--name", "acme-lead", "--no-parent")
assert rc == 0, err
p = prompt_of(calls)
for line in ("MODE        Run", "ROLE: lead acme", "INBOX ", "            Each goes one layer down.", "PREDICATE ",
             "MERGE       merge_policy autonomous", "TASKS ", "GATE ", "CLOSE       Once"):
    assert line in p, (line, p)
assert "MODEL CALLS" not in p and "--parent-worktree" not in calls[0]["argv"] and "--no-parent" in calls[0]["argv"]

# Missing lines: nothing created, exit 2, the missing line named.
for text, want in [(WORKER.replace(CLOSE, "## Close"), "CLOSE or KEEP"),
                   (WORKER.replace("/goal ", "goal: "), "/goal"),
                   (LEAD.replace("GATE ", "NOTE "), "GATE row"),
                   (LEAD.replace("TASKS ", "NOTE "), "TASKS row"),
                   (LEAD.replace("ROLE: lead acme", "ROLE: lead <slug>"), "ROLE: lead <slug>"),
                   # the merge policy is set before the lead starts: no MERGE row, or one naming both values or none
                   (LEAD.replace("MERGE ", "NOTE "), "MERGE row"),
                   (LEAD.replace("merge_policy autonomous", "merge_policy autonomous, or merge_policy human-gate"),
                    "exactly one of `merge_policy autonomous` or `merge_policy human-gate`"),
                   (LEAD.replace("merge_policy autonomous", "TBD"),
                    "exactly one of `merge_policy autonomous` or `merge_policy human-gate`")]:
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
assert rc == 3 and "[gate:pressure]" in err and err.startswith("[skills-sync card]") and not creates(calls), (rc, err, calls)
(entry,) = pressure.queue_read().values()
assert entry["state"] == "waiting" and entry["session"] == "term_me" and f"queue wait {entry['id']}" in err, (entry, err)
# It measures CPU idle now, as the hook and `queue wait` do, so a dispatch the queue released is not refused on a stale rate.
assert SNAP and callable(SNAP[-1].get("idle_fn")), SNAP
os.environ["AGENT_SKILLS_PRESSURE_GATE"] = "0"
assert go(path, "--repo", "r", "--name", "y")[0] == 0
del os.environ["AGENT_SKILLS_PRESSURE_GATE"]
LEVEL["now"] = "ok"
assert go(path, "--repo", "r", "--name", "x")[0] == 0
assert pressure.queue_read()[entry["id"]]["state"] == "done", pressure.queue_read()

# A failed create is exit 4 with orca's reason; a failed log after a create is exit 5, the create output still printed.
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="create-fails")
assert rc == 4 and "no repo x" in err and len(creates(calls)) == 1, (rc, err)
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
assert json.loads(out)["card"] == {"kind": "worker", "path": str(CARD_DIR), "handle": "term_w", "run": {"id": "run_me"},
                                   "parent": "id:repo1::/wt/me", "forbidden": "inserted",
                                   "close_setup": {"log": "abc-1.log"}}, out
# A failed reference after the start is exit 5: the worker runs, so it is not started again.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="ref-fails")
assert rc == 5 and "Do not create it again" in err and json.loads(out)["card"]["kind"] == "worker", (rc, err)
# A start Orca reports done whose receipt names no worktree is still a running worker: exit 5, not 4.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="no-path")
assert rc == 5 and "Do not create it again" in err and json.loads(out)["card"]["handle"] == "term_w", (rc, err)
assert not any(c["argv"][:2] == ["reference", "add"] for c in calls)
# A REPORT row continued onto a second line is read whole.
assert card.report_to("REPORT: orca orchestration send \\\n  --to term_x --type status\n") == ["term_x"]
# From a terminal outside a worktree Orca manages, new-child is refused before anything exists: it stops there with
# the choices, never retried as new-top-level (a card with no parent).
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="unmanaged")
starts = [c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"]]
assert rc == 4 and [s[s.index("--worktree") + 1] for s in starts] == ["new-child"], (rc, err, starts)
assert "Nothing was created" in err and "--no-parent" in err and "request-show" not in err, err
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
# When Orca names the Dispatch, its own recovery query (worker-show) is printed, not request-show.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="start-fails-dispatch")
last = err.splitlines()[-1]
assert rc == 4 and "orca orchestration worker-show --dispatch ctx_2 --json" in last and "request-show" not in last, err
# A start Orca never answered (the subprocess timed out) may still be starting: look before starting it again.
real_run = card.run
card.run = lambda cmd, **k: (None, "timed out after 900 seconds") if cmd[1:3] == ["orchestration", "worker-start"] else real_run(cmd, **k)
try:
    rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1")
finally:
    card.run = real_run
assert rc == 4 and "timed out" in err and "worker-list" in err.splitlines()[-1], err
# A create failure carries no request id, so no request-show line.
rc, out, err, calls = go(path, "--repo", "r", "--name", "x", mode="create-fails")
assert rc == 4 and "request-show" not in err, err
# No Run bound, another Run than the brief's, or Orca silent: nothing created, the choices printed. Only --card makes
# a /goal card, with the reference on the create.
for mode, brief_text, want in (("no-run", SUPERVISED, "run-use --id run_me"),
                               ("", SUPERVISED.replace("run:run_me", "run:run_other"), "works for run:run_me"),
                               ("orca-down", SUPERVISED, "did not say"),
                               ("worker-list-down", SUPERVISED, "did not answer whether it is a dispatched worker"),
                               ("", SUPERVISED + "PARENT: orca orchestration send --to run:run_other\n", "run:run_me and run:run_other")):
    rc, out, err, calls = go(brief(brief_text, "sup-x.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode=mode)
    assert rc == 6 and want in err and "--card" in err and not creates(calls) and not out, (mode, rc, err, calls)
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, "--card", mode="no-run")
create = creates(calls)[0]
assert rc == 0 and create[:2] == ["worktree", "create"] and create[create.index("--reference") + 1] == URL, (err, calls)
assert json.loads(out)["card"]["handle"] == "term_new" and json.loads(out)["card"]["run"] is None
assert not any(c["argv"][:2] == ["orchestration", "run-current"] for c in calls)
# A lead brief reporting to a Run: a dispatched worker once Nested worker depth reaches 3 (its workers' helpers sit
# at depth 3), its spec carrying every lead row; below that, or with Orca's settings unreadable, a /goal card that
# says why and what to change.
LEAD_RUN = LEAD.replace("CLOSE       Once", "PARENT      orca orchestration send --to run:run_me\nCLOSE       Once")
orca_depth(3)
rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead")
start = next(c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"])
spec = start[start.index("--spec") + 1]
assert rc == 0 and start[start.index("--worktree") + 1] == "new-child", (rc, err, calls)
assert spec.startswith("run the acme program") and "ROLE: lead acme" in spec and "DELEGATE " in spec and "CLOSE " in spec, spec
assert "MERGE       merge_policy autonomous" in spec, spec
for depth, want in ((2, "Nested worker depth"), (None, "unread")):
    orca_depth(depth)
    rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead", "--no-parent")
    assert rc == 0 and creates(calls)[0][:2] == ["worktree", "create"] and want in err, (depth, err, calls)
assert "3으로" in card.orca_settings.suggestions({"nestedWorkerMaxDepth": 2})[0]
orca_depth(3)
rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead", "--card")
assert rc == 0 and creates(calls)[0][:2] == ["worktree", "create"], calls
# A human-gate program lands through Orca gates, which a dispatched lead cannot create: its MERGE row makes it a card
# at any depth, with that row in its first prompt. Prose elsewhere in the brief does not decide the form.
LEAD_GATE = LEAD_RUN.replace("merge_policy autonomous", "merge_policy human-gate")
rc, out, err, calls = go(brief(LEAD_GATE, "lead-gate.md"), "--repo", "r", "--name", "acme-lead")
assert rc == 0 and creates(calls)[0][:2] == ["worktree", "create"] and "worker_cannot_coordinate_own_run" in err, (err, calls)
assert "MERGE       merge_policy human-gate" in prompt_of(calls)
rc, out, err, calls = go(brief(LEAD_RUN + "Merge policy: human-gate, as discussed.\n", "lead-prose.md"), "--repo", "r",
                         "--name", "acme-lead")
assert rc == 0 and creates(calls)[0][:2] == ["orchestration", "worker-start"], (err, calls)
# A lead that will start as a /goal card still reports to the Run: it is checked before the form is picked.
rc, out, err, calls = go(brief(LEAD_GATE.replace("run:run_me", "run:run_other"), "lead-gate.md"),
                         "--repo", "r", "--name", "acme-lead")
assert rc == 6 and "works for run:run_me" in err and not creates(calls), (rc, err)
orca_depth(2)
rc, out, err, calls = go(brief(LEAD_RUN, "lead-run.md"), "--repo", "r", "--name", "acme-lead", mode="orca-down")
assert rc == 6 and "did not say" in err and not creates(calls), (rc, err)
orca_depth(3)
# The MERGE row is read once, whitespace normalised, for both the check and the form: a value wrapped onto a
# continuation line counts, two values across lines are refused, and other text naming human-gate is not a value.
assert card.merge_policy("MERGE  merge_policy\n       human-gate") == "human-gate"
assert card.merge_policy("MERGE  merge_policy autonomous, or merge_policy\n       human-gate") is None
assert card.merge_policy("MERGE  merge_policy autonomous\n       Do not use --merge-policy human-gate") == "autonomous"
assert card.merge_policy("MERGE  merge policy: human-gate") is None and card.merge_policy(None) is None
rc, out, err, calls = go(brief(LEAD_RUN.replace("merge_policy autonomous",
                                                "merge_policy autonomous\n            (not --merge-policy human-gate)"),
                               "lead-note.md"), "--repo", "r", "--name", "acme-lead")
assert rc == 0 and creates(calls)[0][:2] == ["orchestration", "worker-start"], (err, calls)
rc, out, err, calls = go(brief(LEAD_RUN.replace("merge_policy autonomous", "merge_policy autonomous, or merge_policy\n"
                                                "            human-gate"), "lead-two.md"), "--repo", "r", "--name", "x")
assert rc == 2 and "exactly one of" in err and not calls, (rc, err)
# A lead that is itself a dispatched worker (run-current null) starts its units as workers on the Run it works for.
SUP_TOP = SUPERVISED.replace("run:run_me", "run:run_top")
rc, out, err, calls = go(brief(SUP_TOP, "sup-top.md"), "--repo", "r", "--name", "abc-1", mode="worker-lead")
assert rc == 0 and any(c["argv"][:2] == ["orchestration", "worker-start"] for c in calls), (err, calls)
assert json.loads(out)["card"]["run"] == {"id": "run_top", "via": "own_dispatch"}, out
rc, out, err, calls = go(brief(SUP_TOP, "sup-top2.md"), "--repo", "r", "--name", "abc-2", mode="worker-lead-terminal")
assert rc == 0 and json.loads(out)["card"]["run"] == {"id": "run_top", "via": "own_dispatch"}, (err, out)
# Its own Dispatch's Run is compared too: a brief naming another Run stops.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", mode="worker-lead")
assert rc == 6 and "works for run:run_top" in err and not creates(calls), (rc, err)
# A report address that is a terminal handle dies with its terminal: refused before anything runs, in any line.
rc, out, err, calls = go(brief(WORKER + "REPORT: `orca orchestration send --to term_abc --type status`\n", "term.md"), "--repo", "r", "--name", "x")
assert rc == 2 and "term_abc" in err and not calls, (rc, err)
rc, out, err, calls = go(brief(WORKER + "- 보고: orca orchestration send --to term_abc (실패하면 orch inbox)\n", "term2.md"), "--repo", "r", "--name", "x")
assert rc == 2 and "term_abc" in err and "terminal_not_found" in err and not calls, (rc, err)
assert card.report_to("REPORT  a digest to ~/r.md\n") == [] and card.report_to("PARENT: send --to run:x\n") == []

# ---- FORBIDDEN: the template's paragraph replaces the marked section, never appended to.
PARA = card.forbidden_paragraph()
assert PARA[0].startswith("Do not start other tickets") and PARA[-1].endswith("run it again."), PARA[:1] + PARA[-1:]
assert not any("<repository data-sharing" in l for l in PARA)
SHARE = "            저장소 데이터 공유 제한: acme/app은 public이다."
MARKED = (f"/goal x\n{CLOSE}\nFORBIDDEN   {card.BEGIN}\n            an old paragraph.\n            {card.END}\n"
          f"{SHARE}\nREPORT      worker_done once.\n")
text, how = card.place_forbidden(MARKED, PARA)
assert how == "replaced" and "an old paragraph" not in text and SHARE in text and text.count(card.BEGIN) == 1, text
assert text.index(card.END) < text.index(SHARE) < text.index("REPORT      worker_done"), text
assert card.place_forbidden(text, PARA) == (text, "unchanged")  # a second start adds nothing
# No markers, an old hand-made copy opening the row: replaced through its last sentence, the row's own lines kept.
OLD = (f"/goal x\n{CLOSE}\nFORBIDDEN   Do not start other tickets. An older wording\n            that ends: fix the cause "
       f"and run it again.\n{SHARE}\nREPORT      worker_done once.\n")
text, how = card.place_forbidden(OLD, PARA)
assert how == "inserted" and "An older wording" not in text and SHARE in text and text.count(card.BEGIN) == 1, text
assert card.place_forbidden(text, PARA)[1] == "unchanged"
# A restriction before the old copy in the same row stays; only the copy is replaced.
OWN = "FORBIDDEN   저장소 데이터 공유 제한: acme/app의 로그를 밖에 쓰지 않는다."
BEFORE = (f"/goal x\n{OWN}\n            Do not start other tickets. An older wording\n            that ends: fix the "
          f"cause and run it again.\nREPORT      worker_done once.\n")
text, how = card.place_forbidden(BEFORE, PARA)
assert how == "inserted" and OWN in text and "An older wording" not in text and text.index(OWN) < text.index(card.BEGIN), text
# An unrecognized copy is not cut: the section goes first and the old text stays.
ODD = f"/goal x\nFORBIDDEN   {SHARE.strip()}\n            Some other ban; run it again.\nREPORT      x\n"
text, how = card.place_forbidden(ODD, PARA)
assert how == "inserted" and SHARE.strip() in text and "Some other ban; run it again." in text, text
# No markers and no copy: the section goes first in the row, the row's text kept after it.
text, how = card.place_forbidden(f"/goal x\nFORBIDDEN   Other work only.\n{SHARE}\n", PARA)
assert how == "inserted" and text.index(card.END) < text.index("Other work only.") < text.index(SHARE), text
# No row: a new one before REPORT; a template quoted in a fence is not the brief's row.
FENCED = f"/goal x\n```\nFORBIDDEN   quoted\n```\nREPORT      worker_done once.\n"
text, how = card.place_forbidden(FENCED, PARA)
assert how == "inserted" and "FORBIDDEN   quoted" in text and text.index(card.BEGIN) > text.rindex("```"), text
assert text.index(card.BEGIN) < text.index("REPORT      worker_done"), text
# The card writes it into the brief file; a lead's brief and --no-forbidden are left alone, the latter logged.
path = brief(WORKER + MARKED.split(CLOSE + "\n", 1)[1], "forbid.md")
rc, out, err, calls = go(path, "--repo", "r", "--name", "fb")
assert rc == 0 and json.loads(out)["card"]["forbidden"] == "replaced", (rc, err)
assert "an old paragraph" not in pathlib.Path(path).read_text() and SHARE in pathlib.Path(path).read_text()
path = brief(WORKER, "noforbid.md")
rc, out, err, calls = go(path, "--repo", "r", "--name", "nf", "--no-forbidden")
assert rc == 0 and card.BEGIN not in pathlib.Path(path).read_text() and json.loads(out)["card"]["forbidden"].startswith("skipped")
assert any(c["bin"] == "orch" and c["argv"][:4] == ["sent", "add", "--to", "nf"] and "--no-forbidden" in c["stdin"]
           for c in calls), calls

# ---- One card per (repo, reference, role).
import sync  # noqa: E402
SENDS = []
sync.try_send = lambda h, t, *a, **k: SENDS.append((h, t)) or (True, None)
REF = "https://example.test/ABC-9"
LIVE = [{"path": "/wt/old", "repoId": "repo1", "displayName": "ABC-9 old", "linkedItems": [{"url": REF}]}]
os.environ["FAKE_CARD_PS"] = json.dumps(LIVE)
path = brief(WORKER, "dup.md")
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9", "--reference", REF)
assert rc == 2 and "already live" in err and "/wt/old" in err and not creates(calls), (rc, err)
# Another role on the same ticket is another key.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9-review", "--reference", REF, "--role", "review")
assert rc == 0 and creates(calls), (rc, err)
assert json.loads(card.ledger().read_text().splitlines()[-1])["role"] == "review"
# A card this ledger recorded as review blocks the next review card even without Orca's link.
os.environ["FAKE_CARD_PS"] = json.dumps([{"path": str(CARD_DIR), "repoId": "repo1", "linkedItems": []}])
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9-review2", "--reference", REF, "--role", "review")
assert rc == 2 and "role review" in err, (rc, err)
os.environ["FAKE_CARD_PS"] = json.dumps(LIVE)
# --supersede starts it, tells the old card to stop, logs and records that.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9b", "--reference", REF, "--supersede")
assert rc == 0 and creates(calls), (rc, err)
assert SENDS and SENDS[-1][0] == "term_old" and SENDS[-1][1].startswith("superseded by abc-9b"), SENDS
assert any(c["bin"] == "orch" and c["argv"][c["argv"].index("--kind") + 1] == "supersede" for c in calls if c["bin"] == "orch")
assert card.ledger_rows()["/wt/old"]["superseded_by"] == str(CARD_DIR)
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9c", "--reference", REF)
assert rc == 0, "a superseded card no longer holds the key"
# A path Orca reuses for a new card starts its record over: the old superseded_by does not carry over.
card.ledger_add(repo="repo1", references=[REF], role="impl", path="/wt/old", name="abc-9-again")
assert "superseded_by" not in card.ledger_rows()["/wt/old"]
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9d", "--reference", REF)
assert rc == 2 and "/wt/old" in err, (rc, err)
# A superseded worker is told through its Dispatch, never typed into (Orca would mark it taken over).
card.ledger_add(repo="repo1", references=[REF], role="impl", path="/wt/worker", name="w", dispatch="ctx_old")
os.environ["FAKE_CARD_PS"] = json.dumps([{"path": "/wt/worker", "repoId": "repo1", "linkedItems": []}])
SENDS.clear()
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9w", "--reference", REF, "--supersede")
told = [c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "send"]]
assert rc == 0 and not SENDS and told and told[0][told[0].index("--to") + 1] == "dispatch:ctx_old", (rc, err, told)
os.environ["FAKE_CARD_PS"] = json.dumps(LIVE)
# An unreadable card list warns and lets the card through.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "abc-9d", "--reference", "https://example.test/ABC-10", mode="ps-down")
assert rc == 0 and "duplicate-card check was skipped" in err, (rc, err)
os.environ["FAKE_CARD_PS"] = "[]"

# ---- close-setup: started for every card; a failure to start it is printed and never fails the card.
SETUPS.clear()
rc, out, err, calls = go(brief(WORKER, "cs.md"), "--repo", "r", "--name", "cs")
assert rc == 0 and SETUPS == [(str(CARD_DIR), "term_new", "cs")], SETUPS
card.start_close_setup = REAL_START_CLOSE_SETUP
real_python = sys.executable
sys.executable = str(tmp / "no-such-python")
try:
    rc, out, err, calls = go(brief(WORKER, "cs2.md"), "--repo", "r", "--name", "cs2")
finally:
    sys.executable = real_python
    card.start_close_setup = lambda path, handle, name: SETUPS.append((path, handle, name)) or {"log": f"{name}.log"}
assert rc == 0 and "error" in json.loads(out)["card"]["close_setup"] and "close-setup did not start" in err, (rc, err)

# ---- handoff: the next ticket's card under this card's parent, its start read from the screen.
def handoff(*argv, mode=""):
    os.environ["FAKE_CARD_MODE"] = mode
    CALLS.write_text("")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = card.handoff(list(argv))
    return rc, out.getvalue(), err.getvalue(), [json.loads(l) for l in CALLS.read_text().splitlines()]


rc, out, err, calls = handoff("ABC-2", "--title", "next step", "--note", "stack on ABC-1.")
assert rc == 0, (rc, err)
make = creates(calls)[0]
assert make[make.index("--name") + 1] == "abc-2" and make[make.index("--parent-worktree") + 1] == "id:repo1::/wt/lead"
assert make[make.index("--prompt") + 1] == f"/goal ABC-2. stack on ABC-1.\n{CLOSE}", make
assert ["worktree", "set", "--worktree", f"path:{CARD_DIR}", "--display-name", "ABC-2 next step", "--json"] in [c["argv"] for c in calls]
assert json.loads(out)["card"]["start"].startswith("started"), out
rc, out, err, calls = handoff("ABC-3", mode="trust")
assert rc == 5 and "start is not confirmed (trust" in err and "Do not create it again" in err, (rc, err)
# The ticket is the key: a second handoff of a ticket whose card is live stops.
os.environ["FAKE_CARD_PS"] = json.dumps([{"path": "/wt/abc-2", "repoId": "repo1", "linkedItems": [{"id": "ABC-2"}]}])
rc, out, err, calls = handoff("ABC-2")
assert rc == 2 and "already live" in err and not creates(calls), (rc, err)
os.environ["FAKE_CARD_PS"] = json.dumps([{"path": "/wt/abc-20", "repoId": "repo1", "linkedItems": [{"id": "ABC-20"}]}])
rc, out, err, calls = handoff("ABC-2", "--name", "abc-2-again")
assert rc == 0, "ABC-20's card is not ABC-2's"
os.environ["FAKE_CARD_PS"] = "[]"

print("ok")
