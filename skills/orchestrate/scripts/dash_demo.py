"""Fake programs for `dash.py demo` and test_dash.py: a store, fixtures, and shims for gh, orca and the tracker.

build(root) writes everything under root and returns the env that points dash.py at it.
"""
import datetime as dt, json, os, pathlib, sys, time

SHIM = r'''#!/usr/bin/env python3
# Replays fixtures for dash.py demo/tests. A fixture {"fail": "..."} makes the call fail like the real CLI.
import json, os, pathlib, sys
fx = pathlib.Path(os.environ["DASH_DEMO_FIXTURES"])
name, a = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
opt = lambda n: a[a.index(n) + 1] if n in a else ""
if name == "gh" and a[:2] == ["pr", "list"]:
    f = fx / "gh" / (opt("--repo").replace("/", "__") + ".json")
elif name == "orca" and a[:2] == ["worktree", "list"]:
    print(json.dumps({"ok": True, "result": {"worktrees": [w for f in sorted((fx / "orca").glob("*.json"))
                                                          for w in json.loads(f.read_text()).get("worktrees") or []]}}))
    sys.exit(0)
elif name == "orca" and a[:1] == ["orchestration"] and a[1:2] in (["worker-list"], ["task-list"], ["run-show"]):
    f = fx / "orca" / ((opt("--run") or opt("--id")) + ".json")
elif name == "tracker.py" and a[:1] == ["children"]:
    root = pathlib.Path(os.environ["TRACKER_FIXTURES"])
    parent = a[1] if len(a) > 1 and not a[1].startswith("--") else None
    files = [root / opt("--project") / "issues.json"] if opt("--project") else list(root.glob("*/issues.json"))
    print(json.dumps([i for f in files if f.exists() for i in json.loads(f.read_text()) if i.get("parent") == parent]))
    sys.exit(0)
elif name == "tracker.py" and a and a[0] in ("list", "get"):
    root = pathlib.Path(os.environ["TRACKER_FIXTURES"])
    f = root / opt("--project") / "issues.json" if a[0] == "list" else None
    if a[0] == "list" and not f.exists():
        f = root / "issues.json"
    if a[0] == "get":
        sys.exit("fixture tracker: get unsupported")
else:
    sys.exit(f"fake {name}: unsupported {' '.join(a[:3])}")
if not f.exists():
    sys.exit(f"fake {name}: no fixture {f.name}")
data = json.loads(f.read_text())
if isinstance(data, dict) and "fail" in data:
    sys.exit(data["fail"])
if name == "gh":
    fields = opt("--json").split(",")
    if "statusCheckRollup" in fields and opt("--state") != "open":
        # What agent-platform's GitHub did to rollups and reviews over a 200-PR window.
        sys.exit("HTTP 504: We couldn't respond to your request in time. (https://api.github.com/graphql)")
    data = [{k: p[k] for k in fields if k in p} for p in data if opt("--state") != "open" or p["state"] == "OPEN"]
if name == "orca" and data.get("ok") is not False:
    # One fixture per run: {"workers": [...], "tasks": [...], "run": {...}}
    key = {"worker-list": "workers", "task-list": "tasks", "run-show": "run"}[a[1]]
    data = {"ok": True, "result": {key: data.get(key) or ([] if key != "run" else {})}}
print(json.dumps(data))
'''


def _iso(t):
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ago(now, h):
    return _iso(now - dt.timedelta(hours=h))


def _issue(now, iid, title, state_type, created_h, done_h=None, labels=(), assignee=None, prio=2, desc=""):
    state = {"completed": "Done", "started": "In Progress", "unstarted": "Todo", "backlog": "Backlog",
             "canceled": "Canceled", "triage": "Triage"}[state_type]
    return {"id": iid, "title": title, "url": f"https://tracker.example/{iid}", "state": state,
            "state_type": state_type, "created_at": _ago(now, created_h),
            "updated_at": _ago(now, done_h if done_h is not None else min(created_h, 1.5)),
            "completed_at": _ago(now, done_h) if done_h is not None else None, "canceled_at": None,
            "labels": list(labels), "parent": None, "description": desc, "assignee": assignee, "priority": prio}


def _checks(kind):
    if kind == "none":
        return []
    names = ["lint", "typecheck", "unit", "e2e"]
    out = []
    for i, n in enumerate(names):
        pending = kind == "pending" and i >= 2
        failed = kind == "fail" and n == "e2e"
        out.append({"__typename": "CheckRun", "name": n, "workflowName": "CI",
                    "status": "IN_PROGRESS" if pending else "COMPLETED",
                    "conclusion": "" if pending else "FAILURE" if failed else "SUCCESS",
                    "detailsUrl": f"https://github.com/acme/x/actions/runs/{9000 + i}/job/1"})
    return out


def _pr(now, repo, n, title, branch, state, created_h, merged_h=None, ci="pass", head=None, reviews=(), draft=False,
        merge_state=None):
    return {"number": n, "title": title, "url": f"https://github.com/{repo}/pull/{n}", "state": state,
            "isDraft": draft, "headRefOid": head or f"{n:x}".ljust(40, "a"), "headRefName": branch, "baseRefName": "main",
            "mergeStateStatus": merge_state or ("CLEAN" if state == "OPEN" else "UNKNOWN"),
            "createdAt": _ago(now, created_h), "mergedAt": _ago(now, merged_h) if merged_h is not None else None,
            "statusCheckRollup": _checks(ci),
            "reviews": [{"state": "COMMENTED", "submittedAt": _ago(now, h), "commit": {"oid": oid}} for oid, h in reviews]}


def _worker(now, ctx, ticket_wt, outcome, activity, model, observed_h, liveness="live", attention=()):
    detail = {"in_progress": "input_accepted", "succeeded": "settled", "failed": "process_stopped"}[outcome]
    return {"dispatchId": ctx, "taskId": ctx.replace("ctx_", "task_"), "runId": "run_demo", "workerState": "ready",
            "resource": {"worktreeId": f"repo::/workspaces/{ticket_wt}"},
            "projection": {"outcome": outcome, "provider": {"id": "claude", "model": model},
                           "stage": {"detail": detail, "activity": activity},
                           "liveness": {"verdict": liveness,
                                        "reason": "stale_status" if "stale" in attention else None,
                                        "observedAt": int((now - dt.timedelta(hours=observed_h)).timestamp() * 1000)},
                           "attention": {"categories": list(attention)}}}


def alpha(now):
    repo, created = "acme/launchpad", 40
    L = []

    def ev(h, kind, **kw):
        L.append({"ts": _ago(now, h), "ev": kind, **kw})

    def land(h, pr, ticket, green=True, note=None):
        ev(h + 1.0, "ready", pr=pr, ticket=ticket)
        ev(h + 0.5, "verdict", pr=pr, sha=f"{pr:x}".ljust(40, "a"), patch_id="p", source="codex-review", result="pass")
        ev(h, "landed", pr=pr, sha=f"m{pr}")
        ev(h - 0.4, "main_green" if green else "main_red", sha=f"m{pr}", note=note)

    ev(39.5, "spawned", ticket="ACME-101", note="ctx_a1000001 pilot")
    land(36, 201, "ACME-101")
    ev(35, "spawned", ticket="ACME-102", note="ctx_a1000002")
    ev(35, "spawned", ticket="ACME-103", note="ctx_a1000003")
    land(30, 202, "ACME-102")
    ev(29, "parked", ticket="ACME-120", note="polish; blocks nothing")
    ev(27, "spawned", ticket="ACME-104", note="ctx_a1000004")
    land(25, 203, "ACME-103", green=False, note="e2e: login redirect loop")
    ev(24.3, "admitted", ticket="ACME-121", note="defect in #203, merged by this program")
    ev(24.2, "spawned", ticket="ACME-121", note="ctx_a1000005")
    land(21.5, 204, "ACME-121")
    ev(19, "parked", ticket="ACME-122")
    ev(19, "parked", ticket="ACME-123")
    land(16.5, 205, "ACME-104")
    ev(15.5, "spawned", ticket="ACME-105", note="ctx_a1000006")
    ev(15.5, "spawned", ticket="ACME-106", note="ctx_a1000007")
    ev(11, "parked", ticket="ACME-124")
    ev(10, "ready", pr=206, ticket="ACME-105")
    ev(9.5, "verdict", pr=206, sha="ce".ljust(40, "a"), patch_id="p0", source="codex-review", result="fail",
       note="missing retry test")
    ev(7, "verdict", pr=206, sha="ce".ljust(40, "b"), patch_id="p1", source="codex-review", result="pass")
    ev(6.5, "landed", pr=206, sha="m206")
    ev(6.1, "main_green", sha="m206")
    ev(5, "admitted", ticket="ACME-125", note="blocks ACME-106 (schema change)")
    ev(5, "spawned", ticket="ACME-125", note="ctx_a1000008")
    ev(4, "ready", pr=207, ticket="ACME-106")
    ev(3.5, "verdict", pr=207, sha="cf".ljust(40, "a"), patch_id="p", source="verifier:codex", result="pass")
    ev(3.3, "dep", ticket="ACME-107", after=["ACME-106"])
    ev(2.5, "spawned", ticket="ACME-107", note="ctx_a1000009")
    ev(1.2, "ready", pr=208, ticket="ACME-125")
    ev(0.3, "parked", ticket="ACME-126", note="nice-to-have")
    ev(0.2, "reprioritized", pr=207, klass="urgent", note="waited 4h and unblocks ACME-107")
    ev(0.16, "land_check", pr=207, outcome="act", note="clean at head; taking the lock")
    ev(0.15, "lock_acquired", pr=207, base="main", ticket="ACME-106")
    ev(0.1, "yield", pr=208, note="#207")

    fu = ["follow-up"]
    issues = [
        _issue(now, "ACME-101", "Stream build logs to the deploy page over SSE", "completed", 44, 36, assignee="worker"),
        _issue(now, "ACME-102", "Retry policy for the flaky artifact upload step", "completed", 44, 30),
        _issue(now, "ACME-103", "Sign-in with the org SSO provider", "completed", 44, 25),
        _issue(now, "ACME-104", "Environment promotion: staging → production", "completed", 44, 16.5),
        _issue(now, "ACME-105", "Rollback button with a dry-run preview", "completed", 44, 6.5),
        _issue(now, "ACME-106", "Per-environment secrets editor", "started", 44),
        _issue(now, "ACME-107", "Quickstart: first deploy in under five minutes", "started", 44),
        _issue(now, "ACME-120", "Tighten empty-state copy on the deploy list", "backlog", 29.2, labels=fu),
        _issue(now, "ACME-121", "Fix login redirect loop after SSO callback", "completed", 24.4, 21.5, labels=fu, prio=1),
        _issue(now, "ACME-122", "Cache the environment list between page loads", "backlog", 19.3, labels=fu),
        _issue(now, "ACME-123", "Audit log entry for rollbacks", "backlog", 19.2, labels=fu),
        _issue(now, "ACME-124", "Keyboard shortcuts for the log viewer", "backlog", 11.2, labels=fu, prio=4),
        _issue(now, "ACME-125", "Secrets schema: per-environment scoping", "started", 5.1, labels=fu, prio=1),
        _issue(now, "ACME-126", "Dark-mode contrast on status badges", "backlog", 0.4, labels=fu, prio=4),
        _issue(now, "ACME-127", "Upload step ignores proxy settings", "triage", 0.2, labels=fu, prio=2),
        _issue(now, "ACME-090", "Old backlog item this program never touched", "backlog", 400),
        _issue(now, "ACME-100", "M1 Deploy basics", "completed", 60, 21),
        _issue(now, "ACME-110", "M2 Safe releases", "started", 60),
        _issue(now, "ACME-111", "M3 First run", "started", 60),
    ]
    # Stages: top-level issues with children. ACME-121 sits one level deeper, under ACME-103.
    for parent, kids in (("ACME-100", ("ACME-101", "ACME-102", "ACME-103")), ("ACME-103", ("ACME-121",)),
                         ("ACME-110", ("ACME-104", "ACME-105", "ACME-106", "ACME-125")), ("ACME-111", ("ACME-107",))):
        for i in issues:
            if i["id"] in kids:
                i["parent"] = parent
    prs = [
        _pr(now, repo, 209, "WIP: try a websocket log stream", "acme-101-ws", "CLOSED", 38, ci="none", draft=True),
        _pr(now, repo, 208, "Secrets schema: per-environment scoping", "acme-125-secrets-schema", "OPEN", 1.4, ci="pending",
            merge_state="BEHIND"),
        _pr(now, repo, 207, "Per-environment secrets editor", "acme-106-secrets-editor", "OPEN", 12, ci="pass",
            head="cf".ljust(40, "a"), reviews=[("c1", 8), ("c2", 5), ("cf".ljust(40, "a"), 3.6)]),
        _pr(now, repo, 206, "Rollback button with a dry-run preview", "acme-105-rollback", "MERGED", 12, 6.5,
            head="ce".ljust(40, "b"), reviews=[("ce".ljust(40, "a"), 9.6), ("ce".ljust(40, "b"), 7.1)]),
        _pr(now, repo, 205, "Environment promotion: staging → production", "acme-104-promotion", "MERGED", 24, 16.5,
            reviews=[("x", 17.2)]),
        _pr(now, repo, 204, "Fix login redirect loop after SSO callback", "acme-121-redirect-loop", "MERGED", 23.5, 21.5,
            reviews=[("y", 22.1)]),
        _pr(now, repo, 203, "Sign-in with the org SSO provider", "acme-103-sso", "MERGED", 31, 25,
            reviews=[("z1", 27), ("z2", 25.6)]),
        _pr(now, repo, 202, "Retry policy for the flaky artifact upload step", "acme-102-retry", "MERGED", 33, 30,
            reviews=[("w", 30.6)]),
        _pr(now, repo, 201, "Stream build logs to the deploy page over SSE", "acme-101-sse-logs", "MERGED", 38.5, 36,
            reviews=[("v", 36.6)]),
    ]
    workers = [
        _worker(now, "ctx_a1000009", "acme-107", "in_progress", "waiting", "sonnet", 0.7),
        _worker(now, "ctx_a1000008", "acme-125", "in_progress", "working", "opus", 0.02),
        _worker(now, "ctx_a1000010", "acme-verify-main", "in_progress", "unknown", "haiku", 1.4, "unverifiable", ("stale",)),
        _worker(now, "ctx_a1000007", "acme-106", "succeeded", "unknown", "gpt-6-sol", 3.9, "exited"),
        _worker(now, "ctx_a1000006", "acme-105", "succeeded", "unknown", "opus", 9.9, "exited"),
        _worker(now, "ctx_a1000005", "acme-121", "succeeded", "unknown", "opus", 21.9, "exited"),
        _worker(now, "ctx_a1000004", "acme-104", "succeeded", "unknown", "sonnet", 17.4, "exited"),
        _worker(now, "ctx_a1000003", "acme-103", "failed", "unknown", "sonnet", 26.5, "exited"),
    ]
    notes = [
        {"ts": _ago(now, 20), "kind": "digest", "author": "coordinator",
         "text": "main went red after #203 (login redirect loop); ACME-121 admitted as the fix and landed. Cap back to 2."},
        {"ts": _ago(now, 4.9), "kind": "decision", "author": "coordinator",
         "text": "ACME-125 admitted: ACME-106 cannot land without the per-environment secrets schema."},
        {"ts": _ago(now, 0.4), "kind": "risk", "author": "annotator",
         "text": "ACME-107 worker has shown 'waiting' for 40 min — likely a permission prompt; needs a human in its terminal."},
    ]
    cfg = {"slug": "launchpad-ga", "repo": repo, "run": "run_demo_alpha", "linear_project": "Launchpad GA",
           "predicate": [f"ACME-{n}" for n in range(101, 108)], "merge_policy": "autonomous", "ceiling": 5,
           "deadline": _ago(now, -32), "created_at": _ago(now, created), "skills_commit": None, "note": None}
    def task(tid, ticket, status, deps=(), created_h=30, done_h=None, dispatch=None):
        return {"id": tid, "run_id": cfg["run"], "parent_id": None, "task_title": ticket, "display_name": ticket,
                "status": status, "deps": json.dumps(list(deps)), "dispatch_id": dispatch,
                "created_at": _ago(now, created_h).replace("T", " ").rstrip("Z"),
                "completed_at": _ago(now, done_h).replace("T", " ").rstrip("Z") if done_h is not None else None}
    tasks = [
        task("task_101", "ACME-101", "completed", (), 39.5, 36, "ctx_a1000001"),
        task("task_102", "ACME-102", "completed", ("task_101",), 35, 30, "ctx_a1000002"),
        task("task_103", "ACME-103", "completed", ("task_101",), 35, 25, "ctx_a1000003"),
        task("task_121", "ACME-121", "completed", ("task_103",), 24.2, 21.5, "ctx_a1000005"),
        task("task_104", "ACME-104", "completed", ("task_102",), 27, 16.5, "ctx_a1000004"),
        task("task_105", "ACME-105", "completed", ("task_104",), 15.5, 6.5, "ctx_a1000006"),
        task("task_106", "ACME-106", "completed", ("task_104",), 15.5, 3.9, "ctx_a1000007"),
        task("task_125", "ACME-125", "dispatched", (), 5, None, "ctx_a1000008"),
        task("task_107x", "ACME-107", "failed", ("task_106",), 3, 2.7),
        task("task_107", "ACME-107", "dispatched", ("task_106",), 2.5, None, "ctx_a1000009"),
        task("task_ver", "verify launchpad quickstart on main", "pending", ("task_107", "task_125"), 2.5),
    ]
    task_of = {t["dispatch_id"]: t["id"] for t in tasks}
    for w in workers:
        w["taskId"] = task_of.get(w["dispatchId"], w["taskId"])
    # ACME-105 landed but its card is still on disk; ACME-107 is still being worked.
    worktrees = [{"id": "repo::/workspaces/repo", "path": "/workspaces/repo", "isMainWorktree": True}] + [
        {"id": f"repo::/workspaces/{wt}", "path": f"/workspaces/{wt}", "isMainWorktree": False}
        for wt in ("acme-105", "acme-107")]
    orca = {"workers": workers, "tasks": tasks, "worktrees": worktrees,
            "run": {"id": cfg["run"], "objective": "Launchpad GA — ship SSO, promotion, rollback, secrets and the "
                                                    "five-minute quickstart to design partners"}}
    return cfg, L, issues, prs, orca, notes


def beta(now):
    repo = "acme/billing"
    L = []

    def ev(h, kind, **kw):
        L.append({"ts": _ago(now, h), "ev": kind, **kw})

    ev(19, "spawned", ticket="BILL-11")
    ev(15, "ready", pr=301, ticket="BILL-11")
    ev(14.5, "verdict", pr=301, sha="12d".ljust(40, "a"), patch_id="p", source="codex-review", result="pass")
    ev(14, "approved", pr=301)
    ev(13.8, "landed", pr=301, sha="m301")
    ev(13.5, "main_green", sha="m301")
    ev(13, "spawned", ticket="BILL-12")
    ev(13, "spawned", ticket="BILL-13")
    ev(6, "ready", pr=302, ticket="BILL-12")
    ev(5.5, "verdict", pr=302, sha="12e".ljust(40, "a"), patch_id="p", source="codex-review", result="pass")
    ev(4, "ready", pr=303, ticket="BILL-13")
    ev(3.5, "verdict", pr=303, sha="12f".ljust(40, "a"), patch_id="p", source="codex-review", result="pass")
    ev(3, "approved", pr=302)
    ev(2.8, "landed", pr=302, sha="m302")
    ev(2.4, "main_red", sha="m302", note="invoice migration test fails on main")
    ev(1, "parked", ticket="BILL-20")
    issues = [
        _issue(now, "BILL-11", "Proration for mid-cycle plan changes", "completed", 30, 13.8),
        _issue(now, "BILL-12", "Invoice PDF v2 layout", "completed", 30, 2.8),
        _issue(now, "BILL-13", "Tax ID validation for EU customers", "started", 30),
        _issue(now, "BILL-20", "Currency symbol spacing in invoice footer", "backlog", 1.1, labels=["follow-up"]),
    ]
    workers = [_worker(now, "ctx_b1000003", "bill-13", "in_progress", "working", "opus", 0.1)]
    cfg = {"slug": "billing-q4", "repo": repo, "run": "run_demo_beta", "linear_project": "Billing Q4",
           "predicate": ["BILL-11", "BILL-12", "BILL-13"], "merge_policy": "human-gate", "ceiling": 3,
           "deadline": _ago(now, -10), "created_at": _ago(now, 20), "skills_commit": None, "note": None}
    gh_fail = {"fail": "GraphQL: API rate limit exceeded for user ID 1234."}
    orca = {"workers": workers, "tasks": [], "run": {"id": cfg["run"], "objective": "Billing Q4: EU-ready invoicing"}}
    return cfg, L, issues, gh_fail, orca, []


def write_program(root, cfg, ledger, issues, prs, orca, notes):
    store, fx = root / "programs", root / "fixtures"
    d = store / cfg["slug"]
    (d / "briefs").mkdir(parents=True, exist_ok=True)
    (d / "program.json").write_text(json.dumps(cfg, indent=2) + "\n")
    (d / "ledger.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ledger))
    if notes:
        (d / "dashboard").mkdir(exist_ok=True)
        (d / "dashboard" / "notes.jsonl").write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes))
    for sub, name, data in (("tracker/" + cfg["linear_project"], "issues.json", issues),
                            ("gh", cfg["repo"].replace("/", "__") + ".json", prs),
                            ("orca", cfg["run"] + ".json", orca)):
        (fx / sub).mkdir(parents=True, exist_ok=True)
        (fx / sub / name).write_text(json.dumps(data, ensure_ascii=False))


def _transcripts(root, now, workers):
    """Claude Code-shaped transcripts for the demo workers, with repeated rows per message like the real ones."""
    base = root / "claude-projects"
    for i, w in enumerate(workers):
        if (w["projection"]["provider"]["model"] or "").startswith("gpt"):
            continue
        wt = w["resource"]["worktreeId"].split("::", 1)[1]
        d = base / wt.replace("/", "-").replace(".", "-")
        d.mkdir(parents=True, exist_ok=True)
        start = w["projection"]["liveness"]["observedAt"] / 1000 - 3 * 3600
        rows = []
        for m in range(40 + 7 * i):
            t = dt.datetime.fromtimestamp(start + m * 240, dt.timezone.utc)
            if t > now:
                break
            usage = {"input_tokens": 3 + m % 5, "output_tokens": 400 + (m * 97) % 1800,
                     "cache_creation_input_tokens": 1500 + (m * 131) % 6000, "cache_read_input_tokens": 30000 + m * 900}
            for _ in range(1 + m % 3):
                rows.append({"type": "assistant", "timestamp": _iso(t),
                             "message": {"id": f"msg_{i}_{m}", "model": "claude-opus", "usage": usage}})
        # How the session's last turn stands: a tool running, a tool waiting on a prompt, or a finished turn.
        act, last = w["projection"]["stage"]["activity"], rows[-1]["timestamp"] if rows else _iso(now)
        if w["projection"]["outcome"] == "in_progress" and act == "working":
            rows.append({"type": "assistant", "timestamp": _ago(now, 0.05), "message": {"id": f"msg_{i}_t", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "bun test", "description": "Run the unit tests"}}]}})
        elif w["projection"]["outcome"] == "in_progress" and act == "waiting":
            rows.append({"type": "assistant", "timestamp": _ago(now, 0.7), "message": {"id": f"msg_{i}_t", "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "git push --force-with-lease"}}]}})
        else:
            rows.append({"type": "assistant", "timestamp": last, "message": {"id": f"msg_{i}_e", "stop_reason": "end_turn",
                                                                         "content": [{"type": "text", "text": "done"}]}})
        (d / "session.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return base


def build(root, now=None):
    root = pathlib.Path(root)
    now = now or dt.datetime.now(dt.timezone.utc)
    bin_ = root / "bin"
    bin_.mkdir(parents=True, exist_ok=True)
    for name in ("gh", "orca", "tracker.py"):
        (bin_ / name).write_text(SHIM)
        (bin_ / name).chmod(0o755)
    a, b = alpha(now), beta(now)
    write_program(root, *a)
    write_program(root, *b)
    projects = _transcripts(root, now, a[4]["workers"] + b[4]["workers"])
    return {"PROGRAMS_HOME": str(root / "programs"), "CLAUDE_PROJECTS_DIR": str(projects), "DASH_DEMO_FIXTURES": str(root / "fixtures"),
            "TRACKER_FIXTURES": str(root / "fixtures" / "tracker"), "TRACKER_PY": str(bin_ / "tracker.py"),
            "PATH": f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"}


def live(root, period=20):
    """Append a few plausible events so the page visibly updates without a reload."""
    ledger = pathlib.Path(root) / "programs" / "launchpad-ga" / "ledger.jsonl"
    notes = pathlib.Path(root) / "programs" / "launchpad-ga" / "dashboard" / "notes.jsonl"
    steps = [
        (ledger, {"ev": "verdict", "pr": 208, "sha": "d0".ljust(40, "a"), "patch_id": "p", "source": "codex-review",
                  "result": "pass"}),
        (ledger, {"ev": "landed", "pr": 207, "sha": "m207"}),
        (ledger, {"ev": "lock_released", "pr": 207, "base": "main", "ticket": "ACME-106"}),
        (ledger, {"ev": "main_green", "sha": "m207"}),
        (notes, {"kind": "digest", "author": "annotator",
                 "text": "ACME-106 landed and main is green; 6 of 7 predicate tickets are waiting on the tracker to close."}),
    ]
    for path, row in steps:
        time.sleep(period)
        row = {"ts": _iso(dt.datetime.now(dt.timezone.utc)), **row}
        with path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[demo] appended {row.get('ev') or row.get('kind')}", file=sys.stderr, flush=True)
