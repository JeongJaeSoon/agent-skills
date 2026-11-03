#!/usr/bin/env python3
"""Create an Orca card from a brief file, with the lines the card's first prompt must carry written out.

Usage:
  skills-sync card <brief.md> --repo <selector> --name <name> [--display-name <title>] [--comment <text>]
                   [--reference <ticket URL or Jira key>]... [--parent-worktree <selector> | --no-parent] [--card]

Which kind of card: a brief whose REPORT or PARENT line sends to `run:<id>`, from a session bound to that Run or
itself a dispatched worker on it (a lead started with worker-start, whose `run-current` is null), starts a supervised
worker (`orca orchestration worker-start --worktree new-child`, so Orca records this session's worktree as its
parent; `new-top-level` with --no-parent; any other --parent-worktree is ignored, since worker-start takes none): a
Dispatch, so the card's worker_done, escalation and ask reach this session and Orca watches it for prompts. Its spec
is the prompt below without the leading `/goal`. When this session is bound to no Run, works for another Run than the
brief names, or Orca does not answer, nothing is created (exit 6): the lines printed say how to bind it
(`orca orchestration run-use`) or to make a /goal card on purpose (--card). A refused new-child is not retried as
new-top-level.

The parent: the worktree of the terminal running this (`orca terminal show`'s worktreeId, passed as
`--parent-worktree id:<it>`), never the focused one or a `path:` selector, which resolves to a repo's main worktree
when several Orca workspaces share its path. No terminal handle, or no worktree for it, creates nothing (exit 6).
--no-parent skips the lookup; an explicit --parent-worktree other than `path:` is passed as given.
A lead's brief starts that way too when Orca's Nested worker depth is 3 or more (`orch doctor`, orca_settings.py),
so the lead's workers' helpers still fit; below that it is a `/goal` card, with the one-line settings proposal as a
warning. A lead's brief whose MERGE row says `merge_policy human-gate` stays a `/goal` card at any depth: its landings wait on
Orca gates, and Orca lets no dispatched worker create a Task or gate on the Run it works for. Anything else (a handoff, or --card) is a `/goal` card from `orca worktree create`. A brief that sends anything to a `term_…`
handle (`orchestration send --to term_…`, in any line) is refused: a handle dies with its terminal, so the mail is
lost on a restart (`terminal_not_found`), and while it lives the mail is only stored.
--reference links the ticket in Orca (linkedItems): on create for a card, by `orca reference add` right after the
start for a worker, which takes no link flag.

The prompt is the brief's `/goal` line (with ` 브리프: <brief path>` appended unless it already names a brief), then
its PROGRAM, APPROVED, lead and CLOSE/KEEP rows. A row is a line outside a ``` fence that starts with the key and a
colon (`CLOSE: …`, `` `CLOSE`: … ``) or the template's wide gap (`DELEGATE    …`), plus the indented lines under it.
A lead's brief (a MODE row, a `ROLE: lead <slug>` row or a `<project>: lead` head) needs every row of orchestrate
references/brief.md "Lead", its MERGE row naming exactly one merge_policy; any brief needs CLOSE or KEEP, and one with an Approved heading or bullet needs its
`APPROVED:` row. The brief is a local file; a note only the notes store can reach is copied to one first.

`--prompt "$(cat brief.md)"` hides those lines from the guard hook, which reads only the command text; this reads
the file and checks them itself, with the hook's own patterns. The card is created from a subprocess, so no hook
sees that `orca worktree create`: the pressure gate's decision is taken here instead.

The created output is printed with a `card` object: kind (worker | card), path, handle (launch.outcome.handle, else
the worker's terminal field), run and parent (the `id:` selector, or None with --no-parent).

Exit codes: 0 created; 2 usage, unreadable brief, a missing line, a `term_` report address or a `path:` parent;
3 the pressure gate refused (wait as its reason says, then run this again); 4 the create or worker-start failed
(its whole receipt, with any recovery commands and the choices for a refused parent, goes to stderr); 5 the card
exists but linking, logging or naming it failed, or a started worker's receipt names no worktree (the create
output is still printed; do not create it again); 6 nothing created because the Run or the parent did not check
out (the choices are printed).
"""
import itertools, json, os, pathlib, re, shlex, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO / "skills" / "orchestrate" / "scripts"))
import guard  # noqa: E402
import orca_settings  # noqa: E402

ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
ORCH = os.environ.get("SKILLS_SYNC_ORCH", str(REPO / "bin" / "orch"))
GOAL = re.compile(r"(?m)^/goal[ \t]+\S.*$")
LEAD_ROWS = ("MODE", "ROLE", "INBOX", "DELEGATE", "PREDICATE", "MERGE", "TASKS", "GATE")
MERGE_VALUE = re.compile(r"merge_policy (autonomous|human-gate)\b")
ROWS = ("PROGRAM", "APPROVED", *LEAD_ROWS, "CLOSE", "KEEP")
FENCE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[^\n]*$")
# dispatch-card §2's prose form of the approval, which carries nothing into the prompt without an APPROVED row
APPROVED_PROSE = re.compile(r"(?im)^[ \t]*(?:#{1,6}[ \t]*|[-*][ \t]+\*\*)approved\b")
ME = "skills-sync card"
HUMAN_GATE = re.compile(r"(?i)merge[ _-]?policy\W{0,20}(?:(?:is|=|:)\W{0,5})?human-gate|--merge-policy[ =]human-gate")


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
        if rows["MERGE"] and len(set(MERGE_VALUE.findall(rows["MERGE"]))) != 1:
            missing.append("MERGE row with exactly one of `merge_policy autonomous` or `merge_policy human-gate` "
                           "(the template's row names both; keep the one this program lands by)")
    if missing:
        raise Refuse(2, f"{path} has no {', no '.join(missing)}. Add it as a line of its own in the brief "
                        "(dispatch-card §2; a lead's rows are orchestrate references/brief.md \"Lead\").")
    bad = [a for a in SEND_TO.findall(text) if a.startswith("term_")]
    if bad:
        raise Refuse(2, f"{path}: it sends to {bad[0]}, a terminal handle. The handle dies with its terminal, so a "
                        "report sent after a restart is lost (terminal_not_found), and until then the mail is only "
                        "stored. Address the reporting session's Run instead (`orca orchestration send --to "
                        "run:<its run id>`; `orca orchestration run-current` prints it, `orch doctor` says when the "
                        "session has none).")
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
        out = pressure_gate.gate(cmd, pressure.snapshot(cfg, state, idle_fn=pressure.live(cfg)), state, cfg, p=pressure,
                                 session=session, cwd=os.getcwd())
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


# What to do when Orca refuses the start over its parent or depth, by a code or text in its refusal; Orca checks
# both before it creates anything.
REFUSED_UNSTARTED = {
    "could not be resolved to a git worktree Orca manages":
        "this terminal's worktree is not one Orca manages, so new-child has no parent: run this from a terminal in "
        "the worktree that should be the parent, or pass --no-parent for a card with no parent",
    "LINEAGE_PARENT_NOT_FOUND":
        "the parent selector names no worktree Orca knows: pass the right `--parent-worktree id:<worktree id>` "
        "(`orca worktree show --worktree active --json`), or --no-parent for a card with no parent",
    "nested_worker_depth_exceeded":
        "Orca's Nested worker depth leaves no room for this worker: start it as a /goal card with --card, or put "
        "`orch doctor`'s settings line before the human",
}


def run(cmd, stdin=None, timeout=120):
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return p, (p.stderr or p.stdout).strip()[:400]


def stop(why, *choices):
    lines = "\n".join(f"  {i}. {c}" for i, c in enumerate(choices, 1))
    return Refuse(6, f"[{ME}] nothing was created: {why}. Choose one, then run this again:\n{lines}")


def supervisor(wanted):
    """The Run this card's worker goes to: this session's bound Run, else the Run of its own Dispatch. Refuse(6)
    unless that is the one Run the brief reports to, or Orca cannot say."""
    card = "pass --card for a /goal card with no Dispatch (its reports go to the Run as `--type question`)"
    bound = orca_settings.binding()
    if bound == "unknown":
        raise stop("Orca did not say which Run this terminal is bound to (`orca orchestration run-current --json` "
                   "failed, or ORCA_TERMINAL_HANDLE is unset)", "run this from an Orca terminal once that command "
                   "answers", card)
    own = None if bound else orca_settings.own_dispatch(ORCA, failed="unknown")
    if own == "unknown":
        raise stop("this terminal is bound to no Run, and Orca did not answer whether it is a dispatched worker "
                   "(`orca orchestration worker-list --terminal-state active --json` failed)",
                   "run this again once that command answers", card)
    have = (bound or {}).get("id") or (own or {}).get("runId")
    want = " and ".join(f"run:{w}" for w in sorted(wanted))
    if not have:
        raise stop(f"the brief reports to {want}, but this session is bound to no Run and is no dispatched worker",
                   f"bind this terminal: `orca orchestration run-use --id {sorted(wanted)[0]}` "
                   "(orchestrate references/top-level.md \"Your Run\")", card)
    if wanted != {have}:
        raise stop(f"the brief reports to {want}, but this session works for run:{have}",
                   f"fix the brief's REPORT or PARENT line to run:{have}",
                   "run this from the session that coordinates the brief's Run", card)
    return bound or {"id": have, "via": "own_dispatch"}


def caller_worktree():
    """`id:<worktree id>` of the terminal running this, or Refuse(6). Not `active` or a path: the parent is the
    session that makes the card, which `orca terminal show` names for this terminal's handle."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    choices = ("run this from an Orca terminal in the worktree that should be the parent",
               "pass `--parent-worktree id:<worktree id>` (`orca worktree show --worktree active --json`)",
               "pass --no-parent for a card with no parent")
    if not me:
        raise stop("ORCA_TERMINAL_HANDLE is unset, so this terminal's worktree cannot be looked up", *choices)
    p, err = run([ORCA, "terminal", "show", "--terminal", me, "--json"], timeout=30)
    try:
        wt = ((json.loads(p.stdout).get("result") or {}).get("terminal") or {}).get("worktreeId") \
            if p and not p.returncode else None
    except (ValueError, AttributeError):
        wt = None
    if not isinstance(wt, str) or not wt:
        raise stop(f"`orca terminal show` named no worktree for this terminal ({err or 'no answer'})", *choices)
    return f"id:{wt}"


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
    """The new agent's handle: launch.outcome.handle (a chat or a terminal), else the older worker terminal fields."""
    return ((((res.get("launch") or {}).get("outcome") or {}).get("handle")) or orca_settings.worker_terminal(res)
            or orca_settings.worker_terminal(res.get("worker")) or ((res.get("dispatch") or {}).get("assigneeHandle"))
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
    wanted = {a[len("run:"):] for a in report_to(text) if a.startswith("run:")}
    to_run = bool(wanted)
    if (opts.get("--parent-worktree") or "").startswith("path:"):
        raise Refuse(2, f"[{ME}] --parent-worktree {opts['--parent-worktree']}: a path selector resolves to the "
                        "repo's main worktree when several Orca workspaces share that path. Pass `id:<worktree id>` "
                        "(`orca worktree show --worktree <it> --json`), or leave it out for this terminal's worktree.")
    lead = guard.is_lead(text)
    # Checked before the lead's form is picked: a lead started as a /goal card still reports to this Run.
    checked = supervisor(wanted) if to_run and not opts.get("--card") else None
    if lead and to_run and not opts.get("--card"):
        settings, why = orca_settings.read()
        if HUMAN_GATE.search(row(text, "MERGE")):
            to_run = False
            print(f"[{ME}] the lead starts as a /goal card: its program merges through Orca gates (human-gate), and a "
                  "dispatched worker can create no Task or gate on the Run it works for "
                  "(worker_cannot_coordinate_own_run)", file=sys.stderr)
        elif not orca_settings.lead_as_worker(settings):
            to_run = False
            fix = "; ".join(orca_settings.suggestions(settings)) if settings else f"Orca's settings unread: {why}"
            print(f"[{ME}] the lead starts as a /goal card: {fix}", file=sys.stderr)
    run_id = checked if to_run else None
    worker = bool(run_id)
    parent = None if opts.get("--no-parent") else (
        opts["--parent-worktree"] if opts.get("--parent-worktree") and not worker else caller_worktree())
    if worker:
        # Orca delivers a worker's spec as its task, after its own preamble: a leading /goal would not run there.
        spec = re.sub(r"^/goal[ \t]+", "", prompt, count=1)
        # worker-start takes no parent selector: new-child parents it to this session, --no-parent drops lineage
        cmd = [ORCA, "orchestration", "worker-start", "--spec", spec, "--worktree",
               "new-top-level" if opts.get("--no-parent") else "new-child",
               "--repo", opts["--repo"], "--name", opts["--name"], "--agent", "claude",
               # Orca times out before this process does, so a slow start still returns its receipt
               "--timeout-ms", "840000"]
        cmd += ["--display-name", opts["--display-name"]] if opts.get("--display-name") else []
    else:
        cmd = [ORCA, "worktree", "create", "--repo", opts["--repo"], "--name", opts["--name"]]
        cmd += ["--parent-worktree", parent] if parent else ["--no-parent"]
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
    try:
        out = json.loads(p.stdout) if p else None
    except ValueError:
        out = None
    res = (out or {}).get("result") or {}
    path = worktree_path(res)
    started = bool(p and not p.returncode and out and out.get("ok") is not False)
    if worker and started and not path:  # it runs: a retry would start a second worker
        mark("done")
        print(json.dumps({**res, "card": {"kind": "worker", "path": None, "handle": handle_of(res), "run": run_id,
                                          "parent": parent}},
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
        data = e.get("data") or {}
        # Orca's own recovery query: worker-show when it names the Dispatch, request-show otherwise.
        query = (data.get("recovery") or {}).get("queryCommand")
        query = shlex.join(query) if isinstance(query, list) and query else (
            f"orca orchestration worker-show --dispatch {data['dispatchId']} --json" if data.get("dispatchId") else
            f"orca orchestration request-show --request {data['orchestrationRequestId']} --json"
            if data.get("orchestrationRequestId") else None)
        refused = next((v for k, v in REFUSED_UNSTARTED.items() if p and k in p.stdout + p.stderr), None)
        if refused:  # refused before anything was created; a start without the parent is the caller's choice
            hint = f"Nothing was created: {refused}"
        elif worker and p is None:
            hint = ("Orca did not answer in time, so the worker may be starting: read `orca orchestration "
                    "worker-list` and `orca worktree ps` before starting it again")
        else:
            hint = f"Before starting it again, ask Orca whether this start took effect: {query}" if query else ""
        raise Refuse(4, f"[{ME}] {verb} failed: {err}" + (f". {hint}" if hint else ""))
    mark("done")
    print(json.dumps({**res, "card": {"kind": "worker" if worker else "card", "path": path, "handle": handle_of(res),
                                      "run": run_id, "parent": parent}}, ensure_ascii=False, indent=1))
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
