#!/usr/bin/env python3
"""Create an Orca card from a brief file, with the lines the card's first prompt must carry written out.

Usage:
  skills-sync card <brief.md> --repo <selector> --name <name> [--display-name <title>] [--comment <text>]
                   [--reference <ticket URL or Jira key>]... [--parent-worktree <selector> | --no-parent] [--card]

Which kind of card: a brief whose REPORT or PARENT line sends to `run:<id>`, from a session bound to a Run, starts
a supervised worker (`orca orchestration worker-start --worktree new-child`, so Orca records this session's worktree
as its parent; `new-top-level` when this session is not in a worktree Orca manages): a Dispatch, so the card's
worker_done, escalation and ask reach the Run and Orca watches it for prompts. Its spec is the prompt below without
the leading `/goal`. Anything else (a handoff, a lead's brief, which must start workers of its own, or --card) is a
`/goal` card from `orca worktree create`. A brief reporting to `run:` from a session with no Run is a `/goal` card,
with a warning. A REPORT or PARENT line that sends to a `term_…` handle is refused: such mail wakes nobody.
--reference links the ticket in Orca (linkedItems): on create for a card, by `orca reference add` right after the
start for a worker, which takes no link flag.

The prompt is the brief's `/goal` line (with ` 브리프: <brief path>` appended unless it already names a brief), then
its PROGRAM, APPROVED, lead and CLOSE/KEEP rows. A row is a line outside a ``` fence that starts with the key and a
colon (`CLOSE: …`, `` `CLOSE`: … ``) or the template's wide gap (`DELEGATE    …`), plus the indented lines under it.
A lead's brief (a MODE row, a `ROLE: lead <slug>` row or a `<project>: lead` head) needs every row of orchestrate
references/brief.md "Lead"; any brief needs CLOSE or KEEP, and one with an Approved heading or bullet needs its
`APPROVED:` row. The brief is a local file; a note only the notes store can reach is copied to one first.

`--prompt "$(cat brief.md)"` hides those lines from the guard hook, which reads only the command text; this reads
the file and checks them itself, with the hook's own patterns. The card is created from a subprocess, so no hook
sees that `orca worktree create`: the pressure gate's decision is taken here instead.

The created output is printed with a `card` object: kind (worker | card), path, handle (launch.outcome.handle, else
agentTerminalHandle) and run.

Exit codes: 0 created; 2 usage, unreadable brief, a missing line or a `term_` report address; 3 the pressure gate
refused (wait as its reason says, then run this again); 4 the create or worker-start failed (its whole receipt,
with any recovery commands, goes to stderr); 5 the card exists but linking, logging or naming it failed, or a
started worker's receipt names no worktree (the create output is still printed; do not create it again).
"""
import itertools, json, os, pathlib, re, shlex, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
import guard  # noqa: E402

ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
ORCH = os.environ.get("SKILLS_SYNC_ORCH", str(REPO / "bin" / "orch"))
GOAL = re.compile(r"(?m)^/goal[ \t]+\S.*$")
LEAD_ROWS = ("MODE", "ROLE", "INBOX", "DELEGATE", "PREDICATE", "TASKS", "GATE")
ROWS = ("PROGRAM", "APPROVED", *LEAD_ROWS, "CLOSE", "KEEP")
FENCE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[^\n]*$")
# dispatch-card §2's prose form of the approval, which carries nothing into the prompt without an APPROVED row
APPROVED_PROSE = re.compile(r"(?im)^[ \t]*(?:#{1,6}[ \t]*|[-*][ \t]+\*\*)approved\b")
ME = "skills-sync card"


class Refuse(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def starts_row(line, key):
    # A colon or the template's column gap: "KEEP the old API" in prose is not a KEEP row.
    return re.match(rf"^[ \t]*`?{key}`?(?:[ \t]*:|[ \t]{{2,}}\S)", line) is not None


def row(text, key):
    """The first row for `key`, with its indented continuation lines, else None."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if starts_row(line, key):
            out = [line.strip()]
            for nxt in lines[i + 1:]:
                if not nxt.strip() or not nxt[:1].isspace() or any(starts_row(nxt, k) for k in ROWS):
                    break
                out.append(nxt.rstrip())
            return "\n".join(out)
    return None


SEND_TO = re.compile(r"orchestration\s+send\b(?:[^\n]|\\\n)*?--to[ =]+[`'\"]?([\w:.@<>-]+)")


def report_to(text):
    """The addresses the brief's REPORT and PARENT rows send to (`orca orchestration send --to <address>`). A row
    that names only a file has none."""
    out = []
    for key in ("REPORT", "PARENT"):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if starts_row(line, key):
                block = [line] + list(itertools.takewhile(lambda l: l.strip() and l[:1].isspace(), lines[i + 1:]))
                out += SEND_TO.findall("\n".join(block))
    return out


def compose(brief_path):
    """The card's first prompt from the brief file, or Refuse(2)."""
    path = pathlib.Path(brief_path).expanduser().resolve()
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise Refuse(2, f"cannot read the brief: {e}")
    text = FENCE.sub("", text)  # a quoted template is not the brief's own rows
    goal = GOAL.search(text)
    rows = {k: row(text, k) for k in ROWS}
    lead = guard.is_lead(text)
    missing = [] if goal else ["`/goal <goal>` line"]
    if not (rows["CLOSE"] or rows["KEEP"]):
        missing.append("CLOSE or KEEP row")
    if APPROVED_PROSE.search(text) and not rows["APPROVED"]:
        missing.append("`APPROVED:` row (an Approved heading or bullet is not copied into the prompt)")
    if lead:
        missing += [f"{k} row" for k in LEAD_ROWS if not rows[k]]
        if rows["ROLE"] and not guard.LEAD_ROLE.search(rows["ROLE"]):
            missing.append("`ROLE: lead <slug>` (the ROLE row names no slug)")
    if missing:
        raise Refuse(2, f"{path} has no {', no '.join(missing)}. Add it as a line of its own in the brief "
                        "(dispatch-card §2; a lead's rows are orchestrate references/brief.md \"Lead\").")
    bad = [a for a in report_to(text) if a.startswith("term_")]
    if bad:
        raise Refuse(2, f"{path}: its REPORT/PARENT line sends to {bad[0]}, a terminal handle. A message to a terminal is "
                        "only stored: nothing wakes the session. Address the reporting session's Run instead "
                        "(`orca orchestration send --to run:<its run id>`; `orca orchestration run-current` prints it).")
    head = goal.group(0).rstrip()
    if not any(s in head for s in ("브리프:", str(path), brief_path)):
        head += f" 브리프: {path}"
    prompt = "\n".join([head, *(rows[k] for k in ROWS if rows[k])])
    # The hook's own test on the result: if its patterns move, this refuses instead of creating what it would refuse.
    if not guard.CLOSE_OR_KEEP.search(prompt) or guard.is_lead(prompt) != lead or lead and (
            not guard.LEAD_ROLE.search(prompt) or any(not re.search(rf"\b{w}\b", prompt) for w in guard.LEAD_LINES)):
        raise Refuse(2, "the composed prompt does not pass hooks/guard.py's card check; fix the brief's rows")
    return prompt


def pressure_check(cmd):
    """(the pressure gate's deny reason or None, a function recording this start's outcome in the gate's queue).
    The hook's own steps for a command it sees: a danger deny queues it, a pass marks that entry's retry. Running the
    same `skills-sync card` again builds the same command, so its entry is found again; no decision on any failure."""
    def none(*_):
        return None
    if os.environ.get("AGENT_SKILLS_PRESSURE_GATE") == "0":
        return None, none
    # The terminal stands in for the hook event's session: the same session's rerun finds its queue entry.
    session = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    try:
        import pressure_gate
        sys.path.insert(0, str(pressure_gate.SCRIPT.parent))
        import pressure
        cfg = pressure.config()
        state = pressure.load_state()
        out = pressure_gate.gate(cmd, pressure.snapshot(cfg, state), state, cfg, p=pressure, session=session,
                                 cwd=os.getcwd())
    except Exception:
        return None, none
    if out and out[0] == "deny":
        return out[1], none

    def mark(outcome, error=None):
        try:
            pressure.queue_mark(session, cmd, outcome, error)
        except Exception:
            pass
    mark("retrying")
    return None, mark


def github_repo(worktree):
    """`owner/name` of the card's origin, for the Sent page; None when it is not a GitHub remote."""
    p, _ = run(["git", "-C", worktree or ".", "remote", "get-url", "origin"], timeout=10) if worktree else (None, "")
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", p.stdout.strip()) if p and not p.returncode else None
    return m and m.group(1)


# Orca's refusal of `--worktree new-child` from a terminal whose workspace is not a git worktree it manages.
UNMANAGED = "could not be resolved to a git worktree Orca manages"


def run(cmd, stdin=None, timeout=120):
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return p, (p.stderr or p.stdout).strip()[:400]


def run_current():
    """The Run this session coordinates (`run-current`), or None: unbound, or Orca did not answer."""
    p, _ = run([ORCA, "orchestration", "run-current", "--json"], timeout=30)
    try:
        return (json.loads(p.stdout).get("result") or {}).get("run") if p and not p.returncode else None
    except (ValueError, AttributeError):
        return None


def worktree_path(res):
    """The new worktree's path in a create or worker-start receipt, wherever this Orca version puts it."""
    if isinstance(res, dict):
        w = res.get("worktree")
        if isinstance(w, dict) and w.get("path"):
            return w["path"]
        # worker-start (1.4.239) names it only as an effect: {"kind": "worktree", "id": "<repo id>::<path>"}
        if res.get("kind") == "worktree" and isinstance(res.get("id"), str) and "/" in res["id"]:
            return res["id"].split("::", 1)[-1]
        for k in ("worktreePath", "worktreeId"):
            if isinstance(res.get(k), str) and "/" in res[k]:
                return res[k].split("::", 1)[-1]
        for v in res.values():
            if isinstance(v, (dict, list)) and (found := worktree_path(v)):
                return found
    elif isinstance(res, list):
        return next(filter(None, map(worktree_path, res)), None)
    return None


def handle_of(res):
    """The new agent's handle: launch.outcome.handle (a chat or a terminal), else the older agentTerminalHandle."""
    return ((((res.get("launch") or {}).get("outcome") or {}).get("handle")) or res.get("agentTerminalHandle")
            or ((res.get("worker") or {}).get("agentTerminalHandle")) or ((res.get("dispatch") or {}).get("assigneeHandle"))
            or next((e.get("id") for e in res.get("effects") or [] if isinstance(e, dict)
                     and e.get("kind") == "terminal" and e.get("role") == "agent"), None))


def parse_args(rest):
    opts, refs = {}, []
    valued = {"--repo", "--name", "--display-name", "--comment", "--parent-worktree"}
    i = 0
    while i < len(rest):
        if rest[i] == "--reference" and i + 1 < len(rest):
            refs.append(rest[i + 1])
            i += 2
        elif rest[i] in valued and i + 1 < len(rest):
            opts[rest[i]], i = rest[i + 1], i + 2
        elif rest[i] in ("--no-parent", "--card"):
            opts[rest[i]], i = True, i + 1
        else:
            raise Refuse(2, f"unexpected argument {rest[i]!r}\n{__doc__}")
    for k in ("--repo", "--name"):
        if not opts.get(k):
            raise Refuse(2, f"{k} is required (dispatch-card §3)")
    return opts, refs


def create(argv):
    if not argv or argv[0].startswith("-"):
        raise Refuse(2, __doc__)
    brief = argv[0]
    opts, refs = parse_args(argv[1:])
    prompt = compose(brief)
    text = FENCE.sub("", pathlib.Path(brief).expanduser().read_text())
    to_run = any(a.startswith("run:") for a in report_to(text))
    lead = guard.is_lead(text)
    run_id = run_current() if to_run and not lead and not opts.get("--card") else None
    if to_run and not lead and not opts.get("--card") and not run_id:
        print(f"[{ME}] warning: the brief reports to a Run but this session is bound to none, so the card starts as a "
              "/goal card with no Dispatch (`orca orchestration run-current`; dispatch-card §3)", file=sys.stderr)
    worker = bool(run_id)
    if worker:
        # Orca delivers a worker's spec as its task, after its own preamble: a leading /goal would not run there.
        spec = re.sub(r"^/goal[ \t]+", "", prompt, count=1)
        cmd = [ORCA, "orchestration", "worker-start", "--spec", spec, "--worktree", "new-child",
               "--repo", opts["--repo"], "--name", opts["--name"], "--agent", "claude",
               # Orca times out before this process does, so a slow start still returns its receipt
               "--timeout-ms", "840000"]
        cmd += ["--display-name", opts["--display-name"]] if opts.get("--display-name") else []
    else:
        cmd = [ORCA, "worktree", "create", "--repo", opts["--repo"], "--name", opts["--name"]]
        cmd += ["--no-parent"] if opts.get("--no-parent") else ["--parent-worktree", opts.get("--parent-worktree", "active")]
        cmd += ["--agent", "claude", "--prompt", prompt]
        for r in refs:
            cmd += ["--reference", r]
    if opts.get("--comment"):
        cmd += ["--comment", opts["--comment"]]
    cmd.append("--json")
    reason, mark = pressure_check(shlex.join(["orca", *cmd[1:]]))
    if reason:
        raise Refuse(3, f"[{ME}] not created, the pressure gate refused it (for `queue wait`, run this same "
                        f"`skills-sync card` command again once it ends with 0): {reason}")
    p, err = run(cmd, timeout=900 if worker else 120)
    if worker and p and p.returncode and UNMANAGED in p.stdout + p.stderr:
        # Refused while resolving the parent, before anything was created: start it without lineage instead.
        cmd[cmd.index("new-child")] = "new-top-level"
        p, err = run(cmd, timeout=900)
    try:
        out = json.loads(p.stdout) if p else None
    except ValueError:
        out = None
    res = (out or {}).get("result") or {}
    path = worktree_path(res)
    started = bool(p and not p.returncode and out and out.get("ok") is not False)
    if worker and started and not path:  # it runs: a retry would start a second worker
        mark("done")
        print(json.dumps({**res, "card": {"kind": "worker", "path": None, "handle": handle_of(res), "run": run_id}},
                         ensure_ascii=False, indent=1))
        raise Refuse(5, f"[{ME}] the worker started but its receipt names no worktree, so nothing was linked. "
                        "Do not create it again; find it with `orca orchestration worker-list`.")
    if not started or not (path if worker else res.get("worktree")):
        e = (out or {}).get("error") or {}
        err = f"{e.get('code')}: {e.get('message')}" if e else err
        mark("failed", err)
        if out:  # a failed or outcome_unknown start names its stage, its effects and the recovery commands
            print(json.dumps(out, ensure_ascii=False, indent=1), file=sys.stderr)
        verb = "orca orchestration worker-start" if worker else "orca worktree create"
        raise Refuse(4, f"[{ME}] {verb} failed: {err}")
    mark("done")
    print(json.dumps({**res, "card": {"kind": "worker" if worker else "card", "path": path, "handle": handle_of(res),
                                      "run": run_id}}, ensure_ascii=False, indent=1))
    failed = []
    if worker and refs and path:  # worker-start takes no link flag; only this new card is written
        p, err = run([ORCA, "reference", "add", *refs, "--worktree", f"path:{path}", "--json"])
        if not p or p.returncode:
            failed.append(f"orca reference add: {err}")
    if not worker:  # a worker's brief is its Orca Task spec, which the Sent page reads
        gh = github_repo(path)
        p, err = run([ORCH, "sent", "add", "--to", opts["--name"], "--kind", "card", "--channel", ME,
                      *(["--repo", gh] if gh else []), "-"], stdin=prompt)
        if not p or p.returncode:
            failed.append(f"orch sent add: {err}")
        if opts.get("--display-name") and path:
            p, err = run([ORCA, "worktree", "set", "--worktree", f"path:{path}", "--display-name", opts["--display-name"], "--json"])
            if not p or p.returncode:
                failed.append(f"orca worktree set: {err}")
    if failed:
        raise Refuse(5, f"[{ME}] the card was created but: {'; '.join(failed)}. Do not create it again.")
    return 0


def main(argv):
    try:
        return create(argv)
    except Refuse as e:
        print(e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
