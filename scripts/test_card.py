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
if a[:2] == ["order", "show"]:
    if mode == "order-unknown":
        print("no order " + a[2], file=sys.stderr); sys.exit(1)
    seen = sum(json.loads(l)["argv"][:2] == ["order", "show"] for l in open(os.environ["FAKE_CARD_CALLS"]))
    closed = mode == "order-closed" or (mode == "order-closes-in-lock" and seen > 1)
    print(json.dumps({"id": a[2], "status": "done" if closed else "open", "links": [],
                      "text": "첫 줄을 한다\n\n둘째 줄도 한다"})); sys.exit(0)
if a[:2] == ["order", "link"] and mode == "link-fails":
    print("orders are not available", file=sys.stderr); sys.exit(1)
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
    if mode == "old-orca" and "--full" in a:
        print("error: invalid_argument: Unknown flag --full for command: orchestration run-current", file=sys.stderr); sys.exit(1)
    if mode == "concise-only" or (mode == "concise" and "--full" not in a):
        print(json.dumps({"ok": True, "result": {"runId": "run_me"}})); sys.exit(0)
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
    if mode == "concise-receipt":
        print(json.dumps({"ok": True, "result": {"dispatchId": "ctx_1", "taskId": "task_1", "runId": "run_me", "state": "ready",
                                                 "agent": "codex", "mode": "terminal", "terminal": "term_w", "keptOpen": True, "next": [],
                                                 "workspace": "repo1::" + os.environ["FAKE_CARD_PATH"]}})); sys.exit(0)
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

# The detached /goal helper would outlive the test: recorded instead, and run in-process by the tests of goal_after_idle.
GOALS = []
REAL_START_GOAL_INPUT = card.start_goal_input
card.start_goal_input = lambda handle, line, name, worktree=None: GOALS.append((handle, line, name, worktree)) or {"log": f"{name}.goal.log"}

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

# A brief reporting to a Run, from a session bound to one: a supervised worker. Its spec keeps the /goal line, the
# display name goes on the start, the ticket is linked on that new card only, and no Sent line is written.
SUPERVISED = WORKER + "REPORT: orca orchestration send --to run:run_me --type status at each milestone\n"
URL = "https://linear.app/acme/issue/ABC-1"
GOALS.clear()
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "name:acme", "--name", "abc-1", "--display-name", "ABC-1 helper",
                         "--reference", URL)
assert rc == 0, err
start = next(c["argv"] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"])
spec = start[start.index("--spec") + 1]
assert spec.splitlines()[0] == f"/goal ship the helper and open its PR 브리프: {tmp / 'sup.md'}", spec
assert start[start.index("--worktree") + 1] == "new-child" and start[start.index("--display-name") + 1] == "ABC-1 helper"
assert start[start.index("--timeout-ms") + 1] == "840000", start
assert [c["argv"] for c in calls if c["argv"][:2] == ["reference", "add"]] == [["reference", "add", URL, "--worktree", f"path:{CARD_DIR}", "--json"]]
assert not any(c["bin"] == "orch" for c in calls) and not any(c["argv"][:2] == ["worktree", "create"] for c in calls)
assert json.loads(out)["card"] == {"kind": "worker", "path": str(CARD_DIR), "handle": "term_w", "run": {"id": "run_me"},
                                   "parent": "id:repo1::/wt/me", "forbidden": "inserted",
                                   "close_setup": {"log": "abc-1.log"}, "model": None, "model_from": None,
                                   "goal_input": {"log": "abc-1.goal.log"}}, out
# The worker's /goal line is not left to the spec: a detached helper types it once the first turn ends.
assert GOALS == [("term_w", f"/goal ship the helper and open its PR 브리프: {tmp / 'sup.md'}", "abc-1", str(CARD_DIR))], GOALS
# Regression (a): a brief whose REPORT is `run:` goes out as a worker, and the spec it hands worker-start keeps the
# /goal line as its first line. (b): the FORBIDDEN section (markers included, a stale copy replaced by the current
# paragraph, a line after a blank line too) and every REPORT row reach the prompt, on both paths.
GOAL_LINE = "/goal ship the helper and open its PR"
PARA_NOW = card.forbidden_paragraph()
FULL = (f"# card brief\n\n{GOAL_LINE}\n\nAPPROVED: push and PR (owner, 2026-01-01)\n\n{CLOSE}\n"
        f"FORBIDDEN   {card.BEGIN}\n            a stale paragraph.\n            {card.END}\n"
        "            A repository's own ban.\n\n            Never run the thing; wait instead.\n\n\n"
        "            Never publish data, even after two blank lines.\n"
        "REPORT      worker_done once, at READY, to run:run_me.\n            Body: what changed.\n"
        "REPORT: orca orchestration send --to run:run_me --type status\n")
rc, out, err, calls = go(brief(FULL, "full.md"), "--repo", "r", "--name", "full")
assert rc == 0, err
spec = next(c["argv"][c["argv"].index("--spec") + 1] for c in calls if c["argv"][:2] == ["orchestration", "worker-start"])
rc, out, err, calls = go(brief(FULL, "full-card.md"), "--repo", "r", "--name", "full-card", "--card")
assert rc == 0, err
for how, text in (("worker spec", spec), ("card prompt", prompt_of(calls))):
    assert text.splitlines()[0].startswith(GOAL_LINE), (how, text)
    assert all(l.strip() in text for l in PARA_NOW), (how, "the current FORBIDDEN paragraph is cut", text)
    assert card.BEGIN in text and card.END in text and "a stale paragraph" not in text, (how, text)
    assert "A repository's own ban." in text and "Never run the thing; wait instead." in text, (how, text)
    assert "Never publish data, even after two blank lines." in text, (how, text)
    assert text.count("REPORT") >= 2 and "Body: what changed." in text, (how, text)
    assert "orca orchestration send --to run:run_me --type status" in text, (how, text)
# A failed reference after the start is exit 5: the worker runs, so it is not started again.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="ref-fails")
assert rc == 5 and "Do not create it again" in err and json.loads(out)["card"]["kind"] == "worker", (rc, err)
# A start Orca reports done whose receipt names no worktree is still a running worker: exit 5, not 4.
rc, out, err, calls = go(brief(SUPERVISED, "sup.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="no-path")
assert rc == 5 and "Do not create it again" in err and json.loads(out)["card"]["handle"] == "term_w", (rc, err)
assert not any(c["argv"][:2] == ["reference", "add"] for c in calls)
# The concise receipt names the worktree as `workspace`. worker-start stays concise: --full fails a start Orca has not
# seen yet, which concise reports as started, and a retry would start a second worker.
rc, out, err, calls = go(brief(SUPERVISED, "sup-concise.md"), "--repo", "r", "--name", "abc-1", "--reference", URL, mode="concise-receipt")
assert rc == 0 and json.loads(out)["card"]["path"] == str(CARD_DIR) and json.loads(out)["card"]["handle"] == "term_w", (rc, err, out)
assert "--full" not in creates(calls)[0] and any(c["argv"][:2] == ["reference", "add"] for c in calls), calls
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
assert spec.startswith("/goal run the acme program") and "ROLE: lead acme" in spec and "DELEGATE " in spec and "CLOSE " in spec, spec
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
# A bound session is bound however Orca words it: the full record, a concise runId, or an Orca without --full.
for mode in ("concise", "concise-only", "old-orca"):
    rc, out, err, calls = go(brief(SUPERVISED, f"sup-{mode}.md"), "--repo", "r", "--name", "abc-1", mode=mode)
    assert rc == 0 and json.loads(out)["card"]["run"] == {"id": "run_me"}, (mode, rc, err)
# A lead that is itself a dispatched worker (run-current null) starts its units as workers on the Run it works for.
SUP_TOP =SUPERVISED.replace("run:run_me", "run:run_top")
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
# No markers, an unmarked copy that is the current paragraph word for word (wrapped otherwise): wrapped in markers,
# not doubled; the row's own lines kept.
PASTED = "\n".join("            " + l for l in " ".join(PARA).replace(". ", ".\n").splitlines())
EXACT = f"/goal x\n{CLOSE}\nFORBIDDEN   {PASTED.strip()}\n{SHARE}\nREPORT      worker_done once.\n"
text, how = card.place_forbidden(EXACT, PARA)
assert how == "inserted" and text.count("Do not start other tickets") == 1 and SHARE in text, text
assert card.place_forbidden(text, PARA)[1] == "unchanged"
# An older or edited copy may hold a repository's own restriction: it stays, and the section goes first in the row.
OLD = (f"/goal x\n{CLOSE}\nFORBIDDEN   Do not start other tickets. 저장소 데이터 공유 제한: acme/app의 로그를 밖에 "
       f"쓰지 않는다.\n            that ends: fix the cause and run it again.\n{SHARE}\nREPORT      worker_done once.\n")
text, how = card.place_forbidden(OLD, PARA)
assert how == "inserted" and "acme/app의 로그" in text and SHARE in text and text.count(card.BEGIN) == 1, text
assert text.index(card.END) < text.index("acme/app의 로그"), text
assert card.place_forbidden(text, PARA)[1] == "unchanged"
# A restriction line before the copy in the same row stays first.
OWN = "FORBIDDEN   저장소 데이터 공유 제한: acme/app의 로그를 밖에 쓰지 않는다."
BEFORE = f"/goal x\n{OWN}\n{PASTED}\nREPORT      worker_done once.\n"
text, how = card.place_forbidden(BEFORE, PARA)
assert how == "inserted" and OWN in text and text.count("Do not start other tickets") == 1, text
assert text.index(OWN) < text.index(card.BEGIN), text
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
assert make[make.index("--prompt") + 1] == "\n".join(["/goal ABC-2. stack on ABC-1.", CLOSE, card.LOAD_LINE,
                                                      *card.FIXED_LINES]), make
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


# ---- --agent: which CLI the card runs. Default claude; every brief check holds for either.
def agent_of(calls):
    make = creates(calls)[0]
    return make[make.index("--agent") + 1]


rc, out, err, calls = go(brief(WORKER, "a1.md"), "--repo", "r", "--name", "a1")
assert rc == 0 and agent_of(calls) == "claude", (rc, err)
SETUPS.clear()
rc, out, err, calls = go(brief(SUPERVISED, "a3.md"), "--repo", "r", "--name", "a3", "--agent", "codex")
assert rc == 0 and creates(calls)[0][:2] == ["orchestration", "worker-start"] and agent_of(calls) == "codex", (rc, err)
# close-setup reads Claude's screen, so a Codex worker's start is Orca's receipt alone and the helper gets no handle.
assert SETUPS == [(str(CARD_DIR), None, "a3")], SETUPS
# Every other form is refused with its reason, nothing created and never switched to claude.
for why, argv in (("/goal card", (brief(WORKER, "a2.md"), "--repo", "r", "--name", "a2")),
                  ("/goal card", (brief(SUPERVISED, "a6.md"), "--repo", "r", "--name", "a6", "--card")),
                  ("lead", (brief(LEAD_RUN, "a7.md"), "--repo", "r", "--name", "a7")),
                  ("lead", (brief(LEAD_GATE, "a8.md"), "--repo", "r", "--name", "a8")),
                  ("lead", (brief(LEAD_RUN.replace("run:run_me", "run:run_other"), "a9.md"), "--repo", "r", "--name", "a9"))):
    before = pathlib.Path(argv[0]).read_text()
    rc, out, err, calls = go(*argv, "--agent", "codex")
    assert rc == 2 and why in err and len(err.splitlines()) == 1 and not creates(calls), (why, rc, err)
    assert pathlib.Path(argv[0]).read_text() == before, argv
# A REPORT row naming a Run without the send form is no report address: the refusal says which row form counts and
# what it found; for Claude the /goal card still starts, with one line saying why it is not a worker.
BARE_RUN = WORKER + "REPORT: worker_done to run:run_me\n"
rc, out, err, calls = go(brief(BARE_RUN, "a13.md"), "--repo", "r", "--name", "a13", "--agent", "codex")
assert rc == 2 and len(err.splitlines()) == 1 and not creates(calls), (rc, err)
assert "`orca orchestration send --to run:<id>`" in err and "run:run_me" in err and "REPORT or PARENT" in err, err
rc, out, err, calls = go(brief(BARE_RUN, "a17.md"), "--repo", "r", "--name", "a17", "--agent", "codex", "--card")
assert rc == 2 and "names run:run_me" not in err, (rc, err)
rc, out, err, calls = go(brief(BARE_RUN, "a14.md"), "--repo", "r", "--name", "a14")
assert rc == 0 and creates(calls)[0][:2] == ["worktree", "create"] and "run:run_me" in err and "send --to" in err, (rc, err)
rc, out, err, calls = go(brief(WORKER, "a15.md"), "--repo", "r", "--name", "a15")
assert rc == 0 and "send --to" not in err, err
rc, out, err, calls = go(brief(SUPERVISED, "a16.md"), "--repo", "r", "--name", "a16")
assert rc == 0 and "send --to" not in err, err
# A Run report only between the FORBIDDEN markers is gone once they are refreshed: still no Codex /goal card.
MARKED = f"{WORKER}FORBIDDEN   {card.BEGIN}\n  REPORT: orca orchestration send --to run:run_me\n  {card.END}\n"
rc, out, err, calls = go(brief(MARKED, "a10.md"), "--repo", "r", "--name", "a10", "--agent", "codex")
assert rc == 2 and "/goal card" in err and not creates(calls), (rc, err, calls)
assert pathlib.Path(tmp / "a10.md").read_text() == MARKED, "a refused start leaves the brief as it was"
# A quoted lead template skips the refresh, so that same REPORT stays and the worker starts.
QUOTED = f"```\nMODE example\n```\n{MARKED}"
assert card.guard.is_lead(QUOTED), "the fence must be what skips the refresh"
rc, out, err, calls = go(brief(QUOTED, "a12.md"), "--repo", "r", "--name", "a12", "--agent", "codex")
assert rc == 0 and creates(calls)[0][:2] == ["orchestration", "worker-start"], (rc, err, calls)
# A row the refresh splits off its FORBIDDEN head turns the brief into a lead's.
SPLIT = SUPERVISED + "FORBIDDEN   MODE example\n"
rc, out, err, calls = go(brief(SPLIT, "a11.md"), "--repo", "r", "--name", "a11", "--agent", "codex")
assert rc == 2 and "lead" in err and not creates(calls) and (tmp / "a11.md").read_text() == SPLIT, (rc, err, calls)
rc, out, err, calls = handoff("ABC-9", "--agent", "codex")
assert rc == 2 and "/goal card" in err and len(err.splitlines()) == 1 and not creates(calls), (rc, err)
rc, out, err, calls = handoff("ABC-9", "--agent", "claude")
assert rc == 0 and agent_of(calls) == "claude", (rc, err)
rc, out, err, calls = go(brief(WORKER, "a4.md"), "--repo", "r", "--name", "a4", "--agent", "cursor")
assert rc == 2 and "--agent is one of claude, codex" in err and not creates(calls), (rc, err)
for bad, why in (("/goal x\n", "no CLOSE or KEEP row"), (f"{CLOSE}\n", "no `/goal <goal>` line"),
                 (f"/goal x\n{CLOSE}\nREPORT: orca orchestration send --to term_abc\n", "a terminal handle")):
    results = [go(brief(bad, "a5.md"), "--repo", "r", "--name", "a5", *extra) for extra in ([], ["--agent", "codex"])]
    assert [r[0] for r in results] == [2, 2] and why in results[1][2] and not creates(results[1][3]), results
    assert results[0][2] == results[1][2], results


# LOAD: a brief's own row is copied (a continuation line with it), a missing one becomes the default for a worker
# brief, a lead's brief gets none, and a quoted or duplicate row does not stand in for the real one.
def load_of(text, name="load.md"):
    return card.compose(brief(text, name))


own = "LOAD: none (read-only)\n    nothing to edit\n"
p = load_of(f"/goal x\n{CLOSE}\n{own}REPORT: r\n")
assert "LOAD: none (read-only)\n    nothing to edit" in p and card.LOAD_LINE not in p, p
assert card.LOAD_LINE in load_of(f"/goal x\n{CLOSE}\n"), "an older brief gets the default"
assert load_of(f"/goal x\n{CLOSE}\n").count("LOAD:") == 2  # the default says `LOAD: none` once more in its own text
assert card.LOAD_LINE in load_of(f"/goal x\nKEEP: next task\n"), "a KEEP card edits too"
assert load_of(f"/goal x\n{CLOSE}\nLOAD  none\n").count("LOAD  none") == 1 and card.LOAD_LINE not in load_of(f"/goal x\n{CLOSE}\nLOAD  none\n")
fenced = f"/goal x\n{CLOSE}\n```\nLOAD: none\n```\n"
assert card.LOAD_LINE in load_of(fenced) and "LOAD: none\n" not in load_of(fenced).split(card.LOAD_LINE)[0], "a quoted template row"
two = load_of(f"/goal x\n{CLOSE}\nLOAD: first\nLOAD: none\n")
assert "LOAD: first" in two and card.LOAD_LINE not in two
assert card.LOAD_LINE not in load_of(LEAD, "lead-load.md"), "a lead's prompt has no default LOAD"
# A LOAD row right after FORBIDDEN is its own row, not part of the ban section.
adj = load_of(f"/goal x\n{CLOSE}\nFORBIDDEN   {card.BEGIN}\n            a ban.\n            {card.END}\nLOAD: none\n")
assert adj.index("LOAD: none") < adj.index("a ban.") and adj.count("LOAD: none") == 1 and adj.rstrip().endswith(card.END), adj

# TEARDOWN and DOCS: every prompt carries them once, after LOAD and ahead of FORBIDDEN, a lead's too, whatever the
# brief says; the rules they hold are the ones a brief writer kept forgetting.
teardown, docs = card.FIXED_LINES
for words in ("같은 턴에 내린다", "`-v`는 붙이지 않는다", "PID를 기록한 것만 그 PID로 kill한다", "pkill·killall 같은 패턴 kill은 쓰지 않는다"):
    assert words in teardown, (words, teardown)
for words in ("Artifact로만 게시한다", "로컬 .md 사본을 만들지 않고", "파일 경로를 적지 않는다"):
    assert words in docs, (words, docs)
for p in (load_of(WORKER), load_of(LEAD, "lead-fixed.md"), adj,
          load_of(f"/goal x\n{CLOSE}\nTEARDOWN: keep my stack\n")):
    assert all(p.count(line) == 1 for line in card.FIXED_LINES), p
assert adj.index(teardown) < adj.index(card.BEGIN) and adj.index(docs) > adj.index("LOAD: none"), adj
assert "keep my stack" not in load_of(f"/goal x\n{CLOSE}\nTEARDOWN: keep my stack\n"), "a brief row does not replace it"

# APPROVED cites the owner's words by their `orch decide` id: an id the store holds as answered passes, an unknown or
# unanswered one is exit 2 with nothing created, a row with no id only warns. The store is a throwaway decisions.json.
os.environ["ORCH_FLEET_STATE"] = str(tmp / "fleet")
(tmp / "fleet").mkdir()
import fleet  # noqa: E402
said = fleet.decision_quote(None, "머지까지 진행해", by="term_me", title="merge approval")["id"]
asked = fleet.change_decisions(lambda s: fleet._new_decision(s, "still open"))["id"]
cited = lambda ids: WORKER.replace("(owner, 2026-01-01)", f"오너({ids}, 2026-01-01): \"머지까지 진행해\"")  # noqa: E731
rc, out, err, calls = go(brief(cited(said), "ap1.md"), "--repo", "r", "--name", "ap1")
assert rc == 0 and "cites no" not in err, (rc, err)
for ids, want in ((f"{said}, 2026-01-01) 오너(d999", "d999 (no such decision)"), (asked, f"{asked} (open, no recorded answer)")):
    rc, out, err, calls = go(brief(cited(ids), "ap2.md"), "--repo", "r", "--name", "ap2")
    assert rc == 2 and want in err and "orch decide answer" in err and not creates(calls), (ids, rc, err)
rc, out, err, calls = go(brief(WORKER, "ap3.md"), "--repo", "r", "--name", "ap3")
assert rc == 0 and "APPROVED row cites no `orch decide` id" in err, (rc, err)
rc, out, err, calls = go(brief(WORKER.replace("APPROVED: push and PR (owner, 2026-01-01)\n", ""), "ap4.md"), "--repo", "r", "--name", "ap4")
assert rc == 0 and "APPROVED" not in err, (rc, err)
# A bare d<N> is not a citation (a library, a path): only `(d<N>,` or `(d<N>)` is looked up.
rc, out, err, calls = go(brief(WORKER.replace("push and PR", "upgrade d3 to v7, see docs/d12"), "ap5.md"), "--repo", "r", "--name", "ap5")
assert rc == 0 and "cites no `orch decide` id" in err, (rc, err)
# A handoff's --note carries APPROVED too, and is checked the same way before anything is created.
for note in ("context.\nAPPROVED: merge — 오너(d999, 2026-01-01)", "APPROVED: merge — 오너(d999, 2026-01-01)"):
    rc, out, err, calls = handoff("ABC-7", "--note", note)
    assert rc == 2 and "d999 (no such decision)" in err and not creates(calls), (note, rc, err)
rc, out, err, calls = handoff("ABC-8", "--note", f"context.\nAPPROVED: merge — 오너({said}, 2026-01-01)")
assert rc == 0 and creates(calls), (rc, err)
# A store that cannot be read leaves a cited id unchecked: refused, never let through.
os.environ["ORCH_FLEET_STATE"] = str(tmp / "no-such-state")
rc, out, err, calls = go(brief(cited(said), "ap6.md"), "--repo", "r", "--name", "ap6")
assert rc == 2 and "cannot be read" in err and not creates(calls), (rc, err)
del os.environ["ORCH_FLEET_STATE"]


# The session's model.
def model_of(calls):
    argv = creates(calls)[0]
    return argv[argv.index("--model") + 1] if "--model" in argv else None


rc, out, err, calls = go(brief(WORKER, "m.md"), "--repo", "r", "--name", "m0", "--no-parent")
assert rc == 0 and model_of(calls) is None and "orchestrate.models.worker is not set" in err, err

models = tmp / "models.json"
models.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": "sonnet"}}}))
os.environ["AGENT_SKILLS_CONFIG"] = str(models)
rc, out, err, calls = go(brief(WORKER, "m.md"), "--repo", "r", "--name", "m3", "--no-parent")
assert rc == 0 and model_of(calls) == "sonnet" and json.loads(out)["card"]["model"] == "sonnet", calls
assert "is not set" not in err, err
rc, out, err, calls = go(brief(LEAD, "ml.md"), "--repo", "r", "--name", "m4", "--no-parent")
assert rc == 0 and model_of(calls) == "opus", calls
rc, out, err, calls = go(brief(WORKER, "m.md"), "--repo", "r", "--name", "m5", "--no-parent", "--model", "opus")
assert rc == 0 and model_of(calls) == "opus", calls
arn = "arn:aws:bedrock:us-east-1:000000000000:inference-profile/acme.model-v1:0"
models.write_text(json.dumps({"orchestrate": {"models": {"worker": arn, "lead": "two words"}}}))
assert card.orca_settings.role_model("worker") == arn and card.orca_settings.role_model("lead") is None
models.write_text(json.dumps({"orchestrate": {"models": {"lead": "opus", "worker": "sonnet"}}}))
worker_brief = brief(f"{WORKER}REPORT: orca orchestration send --to run:run_me --type status\n", "mw.md")
rc, out, err, calls = go(worker_brief, "--repo", "r", "--name", "m6")
assert rc == 0 and creates(calls)[0][:2] == ["orchestration", "worker-start"] and model_of(calls) == "sonnet", calls
rc, out, err, calls = go(worker_brief, "--repo", "r", "--name", "m7", "--agent", "codex")
assert rc == 0 and model_of(calls) is None and "is not set" not in err, (rc, err, calls)
rc, out, err, calls = handoff("ABC-8", "--title", "next", "--note", "x")
assert model_of(calls) == "sonnet" and json.loads(out)["card"]["model_from"] == "orchestrate.models.worker", out
# A KIND row picks the model by the work's kind; --model wins over it, a lead keeps models.lead, Codex gets none.
# Its model and where it came from go in the card object and the cards.jsonl row.
for kind, want in (("design", "opus"), ("edit", "sonnet"), ("lookup", "haiku")):
    rc, out, err, calls = go(brief(f"{WORKER}KIND: {kind} — the reason\n", f"k-{kind}.md"), "--repo", "r",
                             "--name", f"k-{kind}", "--no-parent", "--reference", f"KIND-{kind}")
    got = json.loads(out)["card"]
    assert rc == 0 and model_of(calls) == want and (got["model"], got["model_from"]) == (want, f"KIND: {kind}"), (rc, err, got)
    last = json.loads(card.ledger().read_text().splitlines()[-1])
    assert (last["model"], last["model_from"]) == (want, f"KIND: {kind}"), last
KINDED = f"{WORKER}KIND: lookup — read two files\n"
rc, out, err, calls = go(brief(KINDED, "k1.md"), "--repo", "r", "--name", "k1", "--no-parent", "--model", "opus")
assert rc == 0 and model_of(calls) == "opus" and json.loads(out)["card"]["model_from"] == "--model", (rc, err)
rc, out, err, calls = go(brief(LEAD + "KIND: lookup — x\n", "k2.md"), "--repo", "r", "--name", "k2", "--no-parent")
assert rc == 0 and model_of(calls) == "opus" and json.loads(out)["card"]["model_from"] == "orchestrate.models.lead", (rc, err)
rc, out, err, calls = go(brief(KINDED + "REPORT: orca orchestration send --to run:run_me\n", "k3.md"), "--repo", "r",
                         "--name", "k3", "--agent", "codex")
assert rc == 0 and model_of(calls) is None and json.loads(out)["card"]["model_from"] is None, (rc, err, calls)
rc, out, err, calls = go(brief(WORKER, "k4.md"), "--repo", "r", "--name", "k4", "--no-parent")
assert rc == 0 and json.loads(out)["card"]["model_from"] == "orchestrate.models.worker", out
# An unknown kind or a missing reason is refused before anything happens: no create, the brief left as it was.
for bad in ("KIND: refactor — x", "KIND: edit", "KIND: edit —", "KIND:"):
    text = f"{WORKER}{bad}\n"
    rc, out, err, calls = go(brief(text, "k5.md"), "--repo", "r", "--name", "k5", "--no-parent", "--model", "opus")
    assert rc == 2 and "KIND" in err and "design|edit|lookup" in err and not calls, (bad, rc, err, calls)
    assert (tmp / "k5.md").read_text() == text, bad
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "none.json")
rc, out, err, calls = go(brief(WORKER, "k6.md"), "--repo", "r", "--name", "k6", "--no-parent")
assert rc == 0 and json.loads(out)["card"]["model_from"] is None, out

# ---- the worker's /goal line: typed after the first turn ends, never before, never after a timed-out wait.
GOALS.clear()
rc, out, err, calls = go(worker_brief, "--repo", "r", "--name", "g1", "--agent", "codex")
assert rc == 0 and not GOALS, ("a Codex worker has no /goal command", GOALS)
# Orca bracketed-pastes a /goal card's first prompt and Claude Code folds a long paste into <pasted_content>, so the
# line never runs as the command there either: a Claude /goal card and a handoff get the helper too, with the worktree
# whose transcript it reads.
rc, out, err, calls = go(brief(WORKER, "g2.md"), "--repo", "r", "--name", "g2", "--card")
assert rc == 0 and GOALS == [("term_new", f"/goal ship the helper and open its PR 브리프: {tmp / 'g2.md'}", "g2",
                              str(CARD_DIR))], GOALS
assert json.loads(out)["card"]["goal_input"] == {"log": "g2.goal.log"}, out
GOALS.clear()
rc, out, err, calls = handoff("ABC-7", "--title", "next")
assert rc == 0 and GOALS == [("term_new", "/goal ABC-7.", "abc-7", str(CARD_DIR))], GOALS
assert json.loads(out)["card"]["goal_input"] == {"log": "abc-7.goal.log"}, out
GOALS.clear()
rc, out, err, calls = go(worker_brief, "--repo", "r", "--name", "g3")
assert rc == 0 and len(GOALS) == 1 and GOALS[0][0] == "term_w" and GOALS[0][1].startswith("/goal "), GOALS
assert not any(c["argv"][:2] in (["terminal", "wait"], ["terminal", "send"]) for c in calls), \
    ("card itself neither waits nor types: the helper is detached", calls)

LINE = "/goal ship the helper and open its PR 브리프: /b.md"
import sync  # noqa: E402  the helper types through sync.try_send; its answers are canned here
REAL_TRY_SEND, REAL_SLEEP, SENDS, SLEPT = sync.try_send, card.time.sleep, [], []
card.time.sleep = lambda s: SLEPT.append(s)


REAL_TYPE = card.type_when_idle


def run_helper(answers, timeout_s=3600, worktree=None):
    """goal_after_idle with type_when_idle answering from `answers` in turn: None (typed) or a reason."""
    queue = list(answers)
    SENDS.clear()
    card.type_when_idle = lambda handle, line, deadline=None: SENDS.append((handle, line)) or queue.pop(0)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        rc = card.goal_after_idle("term_w", LINE, timeout_s, worktree)
    return rc, err.getvalue()


BUSY_REASON = "turn in progress: ✶ Crunching… (running UserPromptSubmit hooks…)"
# Idle only after two busy reads: nothing is typed before that, and the line goes out once.
SLEPT.clear()
rc, err = run_helper([BUSY_REASON, BUSY_REASON, None])
assert rc == 0 and len(SENDS) == 3 and SLEPT == [card.GOAL_POLL_S] * 2 and not err, (rc, SENDS, SLEPT, err)
assert all(s == ("term_w", LINE) for s in SENDS)
# Past the limit: nothing more is tried, and the warning carries the manual command.
real_monotonic = card.time.monotonic
ticks = iter([0, 1, 2, 3, 100])  # the deadline, then one check before each attempt
card.time.monotonic = lambda: next(ticks)
try:
    rc, err = run_helper([BUSY_REASON] * 5, timeout_s=50)
finally:
    card.time.monotonic = real_monotonic
assert rc == 1 and len(SENDS) == 3 and "not typed into term_w" in err and "waited up to 50s" in err, (rc, SENDS, err)
assert f"terminal send --terminal term_w --text '{LINE}' --enter" in err, err
# A reason waiting will not change stops at once; a screen that never confirmed the line is not retyped.
rc, err = run_helper(["orca terminal read failed: terminal_handle_stale"] * 3)
assert rc == 1 and len(SENDS) == 1 and "not typed into term_w" in err, (rc, SENDS, err)
rc, err = run_helper([sync.UNCONFIRMED, None])
assert rc == 1 and len(SENDS) == 1 and "Read the screen before typing it again" in err, (rc, SENDS, err)
# With the card's worktree, its session's transcript (the one whose user rows hold the goal text) decides first: the
# /goal command already ran (the paste was short, or someone typed it), so nothing is typed; no such session yet, so
# the first prompt has not landed and nothing is typed; otherwise (the folded paste) the line goes out once the first
# turn ends.
PROJECTS = tmp / "projects"
os.environ["CLAUDE_PROJECTS_DIR"] = str(PROJECTS)
WT = "/wt/acme.card_1"
TDIR = PROJECTS / "-wt-acme-card-1"  # Claude Code's folder: every non-alphanumeric character of the path as `-`


def session_rows(*rows, name="s1.jsonl"):
    TDIR.mkdir(parents=True, exist_ok=True)
    (TDIR / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


META = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<local-command-caveat>x</local-command-caveat>"}}
PASTED = {"type": "user", "message": {"role": "user", "content": f'\n\n<pasted_content id="a1">\n{LINE}\nCLOSE: x\n</pasted_content>'}}
def ran(goal):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": (
        "<command-name>/goal</command-name>\n            <command-message>goal</command-message>\n"
        f"            <command-args>{goal}</command-args>")}]}}


RAN = ran(LINE.split(None, 1)[1])
RELOAD = {"type": "user", "message": {"role": "user", "content": "<command-name>/reload-plugins</command-name>"}}
ticks = iter([0, 1, 2, 3, 100])
card.time.monotonic = lambda: next(ticks)
try:
    SLEPT.clear()
    rc, err = run_helper([None], timeout_s=50, worktree=WT)  # no transcript folder at all
finally:
    card.time.monotonic = real_monotonic
assert rc == 1 and not SENDS and len(SLEPT) == 3 and "not typed into term_w" in err, (rc, SENDS, SLEPT, err)
session_rows({"type": "summary"}, META, RELOAD)
ticks = iter([0, 1, 100])
card.time.monotonic = lambda: next(ticks)
try:
    rc, err = run_helper([None], timeout_s=50, worktree=WT)  # a meta row and a broadcast's reload: not the prompt
finally:
    card.time.monotonic = real_monotonic
assert rc == 1 and not SENDS, (rc, SENDS, err)
session_rows(META, PASTED)
rc, err = run_helper([BUSY_REASON, None], worktree=WT)
assert rc == 0 and SENDS == [("term_w", LINE)] * 2 and not err, (rc, SENDS, err)
session_rows(META, PASTED, {"type": "assistant", "message": {"content": []}}, RAN)
rc, err = run_helper([None], worktree=WT)
assert rc == 0 and not SENDS and not err, ("the command ran: nothing typed", rc, SENDS, err)
session_rows(RAN)
rc, err = run_helper([None], worktree=WT)
assert rc == 0 and not SENDS, ("a /goal first prompt that ran as the command", SENDS)
# The prompt lands while the helper waits: it starts looking at the screen only then.
(TDIR / "s1.jsonl").unlink()
card.time.sleep = lambda s: SLEPT.append(s) or session_rows(META, PASTED)
try:
    SLEPT.clear()
    rc, err = run_helper([None], worktree=WT)
finally:
    card.time.sleep = lambda s: SLEPT.append(s)
assert rc == 0 and SENDS == [("term_w", LINE)] and SLEPT == [card.GOAL_POLL_S], (rc, SENDS, SLEPT)
# Several sessions in the folder: the card's is the one holding its goal text, whichever file is newest.
session_rows(ran("another goal"), name="other.jsonl")
os.utime(TDIR / "s1.jsonl", (1, 1))
rc, err = run_helper([None], worktree=WT)
assert rc == 0 and SENDS == [("term_w", LINE)], ("another session's /goal is not this one's", SENDS)
session_rows(PASTED, name="other.jsonl")
session_rows(META, PASTED, RAN)
os.utime(TDIR / "s1.jsonl", (1, 1))
rc, err = run_helper([None], worktree=WT)
assert rc == 0 and not SENDS, ("an older file that is this session, with /goal run", SENDS)
session_rows(META, PASTED)
(TDIR / "other.jsonl").unlink()
# A short handoff goal named in another session's prompt, beside an unrelated /goal: not this card's session.
session_rows({"type": "user", "message": {"content": "Please review ABC-7. today"}}, ran("unrelated"), name="other.jsonl")
assert card.goal_state(WT, "/goal ABC-7.") == "none"
session_rows({"type": "user", "message": {"content": "/goal ABC-7. review only\nCLOSE: x"}}, name="other.jsonl")
assert card.goal_state(WT, "/goal ABC-7.") == "none", "a longer goal that starts the same is another card's"
session_rows({"type": "user", "message": {"content": "\n\n<pasted_content>\n/goal ABC-7.\nCLOSE: x"}}, name="other.jsonl")
assert card.goal_state(WT, "/goal ABC-7.") == "other"
session_rows(ran(" ABC-7. "), name="other.jsonl")
assert card.goal_state(WT, "/goal ABC-7.") == "ran", "args padded with spaces"
(TDIR / "other.jsonl").unlink()
# Without CLAUDE_PROJECTS_DIR the config homes are read as they are written: brackets in a path are no pattern.
literal = tmp / "profile[1]"
(literal / "projects" / TDIR.name).mkdir(parents=True)
(literal / "projects" / TDIR.name / "s.jsonl").write_text(json.dumps(RAN) + "\n")
del os.environ["CLAUDE_PROJECTS_DIR"]
real_homes, sync.HOMES = sync.HOMES, (str(literal), str(tmp / "orca-homes" / "*" / "home"))
try:
    assert card.goal_state(WT, LINE) == "ran"
finally:
    sync.HOMES = real_homes
    os.environ["CLAUDE_PROJECTS_DIR"] = str(PROJECTS)
# The KIND row's kind is the whole word before the dash.
for bad in ("KIND: design-extra — reason", "KIND: edit-typo — x", "KIND: edit -x"):
    try:
        card.brief_kind(brief(f"{WORKER}{bad}\n", "k7.md"))
        raise AssertionError(bad)
    except card.Refuse as e:
        assert e.code == 2, bad
assert card.brief_kind(brief(f"{WORKER}KIND: edit - rename two flags\n", "k8.md")) == "edit"
card.type_when_idle = lambda handle, line, deadline=None: None
with contextlib.redirect_stdout(io.StringIO()):
    assert card.main(["goal-after-idle", "term_w", LINE]) == 0  # the entry point start_goal_input launches
    assert card.main(["goal-after-idle", "term_w", LINE, WT]) == 0  # and with the card's worktree
card.type_when_idle = REAL_TYPE

# type_when_idle on canned screens, through the real sync.classify. The tab title is the session's topic, not `✳`.
RULE = "─" * 40
STATUS = "  ⏵⏵ auto mode on (shift+tab to cycle)"
IDLE = ["⏺ GOAL-DEMO-OK", "✻ Churned for 17s · done 12:08 AM", RULE, "❯ ", RULE, STATUS]
RUNNING = ["⏺ Reading the brief.", "✶ Crunching… (running UserPromptSubmit hooks… 3/4 · 0s)", RULE, "❯ ", RULE, STATUS]
TAKEN = ["❯ " + LINE, "✶ Thinking… (1s)", RULE, "❯ ", RULE, STATUS]
SCREENS, TYPED, SEND_FAILS = [], [], []


def fake_orca(*args):
    if args[:2] == ("terminal", "show"):
        return {"terminal": {"agentIdentity": "claude", "connected": True, "writable": True,
                             "title": "GOAL-DEMO-OK 워커 브리프 실행"}}
    if args[:2] == ("terminal", "read"):
        return {"terminal": {"tail": SCREENS.pop(0) if len(SCREENS) > 1 else SCREENS[0]}}
    if SEND_FAILS:
        raise sync.Stop("orca terminal send failed: timed out")
    TYPED.append(args)
    return {}


real_orca = sync.orca
sync.orca = fake_orca
sync.SETTLE_S = sync.CONFIRM_S = 0
try:
    TYPED.clear(); SCREENS[:] = [RUNNING]
    assert card.type_when_idle("term_w", LINE).startswith("turn in progress") and not TYPED, TYPED  # first turn still runs
    SCREENS[:] = [IDLE, IDLE, TAKEN]
    assert card.type_when_idle("term_w", LINE) is None and len(TYPED) == 1, TYPED  # the title alone does not block it
    assert TYPED[0] == ("terminal", "send", "--terminal", "term_w", "--text", LINE, "--enter"), TYPED
    TYPED.clear(); SCREENS[:] = [IDLE, IDLE, IDLE]
    assert card.type_when_idle("term_w", LINE) == sync.UNCONFIRMED and len(TYPED) == 1  # typed, never shown taken
    TYPED.clear(); SCREENS[:] = [IDLE, IDLE]  # a send whose answer was lost may have been taken: never "not typed"
    SEND_FAILS.append(1)
    try:
        assert card.type_when_idle("term_w", LINE) == sync.UNCONFIRMED and not TYPED
    finally:
        SEND_FAILS.clear()
    SCREENS[:] = [IDLE, IDLE]  # idle only after the limit: nothing is typed
    assert card.type_when_idle("term_w", LINE, deadline=0).startswith("time limit") and not TYPED, TYPED
    TYPED.clear(); SCREENS[:] = [IDLE, IDLE[:1] + ["✻ Churned for 18s · done 12:09 AM"] + IDLE[2:]]
    assert "changed between two reads" in card.type_when_idle("term_w", LINE) and not TYPED
    # The lock sync, broadcast and nudge share: a busy lock means nothing is read or typed, and it is held from the
    # first read to the confirming one.
    EVENTS = []
    real_locked = sync.locked

    @contextlib.contextmanager
    def recording_lock(wait_s=0.0, free=True):
        EVENTS.append("lock")
        yield free
        EVENTS.append("unlock")

    sync.locked = lambda wait_s=0.0: recording_lock(wait_s, free=False)
    TYPED.clear(); SCREENS[:] = [IDLE, IDLE, TAKEN]
    assert card.type_when_idle("term_w", LINE) == "a sync or broadcast is running" and not TYPED, TYPED
    sync.locked = recording_lock
    EVENTS.clear()
    sync_orca = sync.orca
    sync.orca = lambda *a: EVENTS.append(a[1]) or sync_orca(*a)
    SCREENS[:] = [IDLE, IDLE, TAKEN]
    assert card.type_when_idle("term_w", LINE) is None
    assert EVENTS[0] == "lock" and EVENTS[-1] == "unlock" and EVENTS.count("send") == 1 and "read" in EVENTS[-2], EVENTS
    sync.locked, sync.orca = real_locked, sync_orca
finally:
    sync.orca = real_orca
card.time.sleep = REAL_SLEEP
card.start_goal_input = REAL_START_GOAL_INPUT
spawned = []


# The card's comment says where its brief is, unless the caller gave one.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper")
create = creates(calls)[0]
assert rc == 0 and create[create.index("--comment") + 1] == str(pathlib.Path(path).resolve()), create
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--comment", "내 댓글")
assert rc == 0 and creates(calls)[0][creates(calls)[0].index("--comment") + 1] == "내 댓글", err


def order_calls(calls):
    return [c["argv"] for c in calls if c["bin"] == "orch" and c["argv"][:1] == ["order"]]


# --order oN: the order's words are appended to the prompt as a copy, the new worktree is linked, nothing is rewritten.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--order", "o7")
assert rc == 0, err
p = prompt_of(calls)
assert p.endswith("ORDER: o7\n오너 지시 o7 원문(카드를 시작한 시점의 사본):\n> 첫 줄을 한다\n>\n> 둘째 줄도 한다"), p
assert p.splitlines()[0].startswith("/goal ship the helper") and CLOSE in p
# show twice: before the lock, and again inside it
assert order_calls(calls) == [["order", "show", "o7", "--json"]] * 2 + [["order", "link", "o7", "--unit", f"card:repo1::{CARD_DIR}"]], order_calls(calls)
assert "ORDER" not in pathlib.Path(path).read_text() and "첫 줄을 한다" not in pathlib.Path(path).read_text(), "the order is not written into the brief"
sent = next(c for c in calls if c["argv"][:2] == ["sent", "add"])
assert "ORDER: o7" in sent["stdin"], "the Sent log keeps the copy"

# The brief's own `ORDER: oN` row does the same; the template's `ORDER  Starts after …` row is not an order.
with_row = brief(WORKER + "ORDER: o8\nORDER       Starts after: none. Class: normal.\n", "with-row.md")
rc, out, err, calls = go(with_row, "--repo", "name:acme", "--name", "acme-helper")
assert rc == 0 and ["order", "link", "o8", "--unit", f"card:repo1::{CARD_DIR}"] in order_calls(calls), (err, calls)
assert "ORDER: o8\n" in prompt_of(calls) + "\n" and prompt_of(calls).count("ORDER:") == 1, prompt_of(calls)
rc, out, err, calls = go(brief(WORKER + "ORDER       Starts after: none. Class: normal.\n", "starts-after.md"),
                         "--repo", "name:acme", "--name", "acme-helper")
assert rc == 0 and not order_calls(calls), "a Starts-after row names no order"
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper")
assert rc == 0 and not order_calls(calls), "no order named: no order call"
# Both named, and different: nothing is created.
rc, out, err, calls = go(with_row, "--repo", "name:acme", "--name", "acme-helper", "--order", "o9")
assert rc == 2 and "one order id" in err and not creates(calls), (rc, err)
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--order", "seven")
assert rc == 2 and not creates(calls), (rc, err)
# An unknown or closed order stops before the card exists.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--order", "o7", mode="order-unknown")
assert rc == 2 and "cannot be read" in err and not creates(calls), (rc, err)
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--order", "o7", mode="order-closed")
assert rc == 2 and "o7 is done" in err and not creates(calls), (rc, err)
# A link that fails after the start: the card exists (exit 5), and the error names the command to run.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--order", "o7", mode="link-fails")
assert rc == 5 and "orch order link o7 --unit card:repo1::" in err and "Do not create it again" in err and creates(calls), (rc, err)
# The order closes while this start waits on the card lock: the start looks again and creates nothing.
rc, out, err, calls = go(path, "--repo", "name:acme", "--name", "acme-helper", "--order", "o7", mode="order-closes-in-lock")
assert rc == 2 and "o7 is done" in err and not creates(calls), (rc, err)
# The brief gains an ORDER row while this start waits on the lock: the card is made from the brief as it is then.
late_brief = brief(WORKER, "late-order.md")
real_lock = card.card_lock


@contextlib.contextmanager
def lock_adding_order():
    with real_lock():
        with open(late_brief, "a") as f:
            f.write("ORDER: o7\n")
        yield


card.card_lock = lock_adding_order
try:
    rc, out, err, calls = go(late_brief, "--repo", "name:acme", "--name", "acme-helper")
finally:
    card.card_lock = real_lock
assert rc == 0 and "ORDER: o7\n오너 지시 o7" in prompt_of(calls) and any(c[:2] == ["order", "link"] for c in order_calls(calls)), (err, order_calls(calls))
# The default comment is what fleet.report_target reads: the bare path of a brief whose REPORT names a Run.
import fleet  # noqa: E402
run_brief = brief(WORKER + "REPORT      orca orchestration send --to run:run_me --type question\n", "report-run.md")
rc, out, err, calls = go(run_brief, "--repo", "name:acme", "--name", "acme-helper", "--card")
assert rc == 0 and fleet.report_target(creates(calls)[0][creates(calls)[0].index("--comment") + 1]) == ("run", "run_me"), (err, calls)

# A supervised worker (REPORT to a Run) gets the same: the id comes from worker-start's worktree effect.
WORKER_RUN = WORKER + "REPORT      orca orchestration send --to run:run_me --type question\n"
rc, out, err, calls = go(brief(WORKER_RUN, "worker-order.md"), "--repo", "name:acme", "--name", "w-order", "--order", "o7")
assert rc == 0, err
start = creates(calls)[0]
assert start[start.index("--spec") + 1].endswith("> 둘째 줄도 한다") and ["order", "link", "o7", "--unit", f"card:repo1::{CARD_DIR}"] in order_calls(calls), calls


class FakePopen:
    def __init__(self, cmd, **kw):
        spawned.append((cmd, kw))
        self.pid = 4242


real_popen, card.subprocess.Popen = card.subprocess.Popen, FakePopen
try:
    log = card.start_goal_input("term_w", LINE, "g4")
    card.start_goal_input("term_w", LINE, "g5", "/wt/x")
finally:
    card.subprocess.Popen = real_popen
cmd, kw = spawned[0]
assert cmd[1:] == [str(HERE / "card.py"), "goal-after-idle", "term_w", LINE] and kw["start_new_session"], spawned
assert spawned[1][0][1:] == [str(HERE / "card.py"), "goal-after-idle", "term_w", LINE, "/wt/x"], spawned
assert pathlib.Path(log["log"]).parent.name == "goal-input" and log["pid"] == 4242, log

print("ok")
