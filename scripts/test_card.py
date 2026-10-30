"""`skills-sync card` against a fake orca, a fake orch and a stub pressure module. Run: python3 scripts/test_card.py"""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile, types

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

LEVEL = {"now": "ok"}
sys.modules["pressure"] = types.SimpleNamespace(
    config=lambda: {"gate_fresh_minutes": 10}, load_state=lambda: {}, snapshot=lambda cfg, st: {},
    judge=lambda m, cfg: (LEVEL["now"], ["load_per_cpu 3.1"]), blocked_by=lambda m, cfg: ["load_per_cpu"])
sys.path.insert(0, str(HERE))
import card  # noqa: E402

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
LEVEL["now"] = "danger"
rc, out, err, calls = go(path, "--repo", "r", "--name", "x")
assert rc == 3 and "[gate:pressure]" in err and err.startswith("[skills-sync card]") and not calls, (rc, err, calls)
os.environ["AGENT_SKILLS_PRESSURE_GATE"] = "0"
assert go(path, "--repo", "r", "--name", "x")[0] == 0
del os.environ["AGENT_SKILLS_PRESSURE_GATE"]
LEVEL["now"] = "ok"

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

print("ok")
