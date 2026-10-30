#!/usr/bin/env python3
"""Create an Orca card from a brief file, with the lines the card's first prompt must carry written out.

Usage:
  skills-sync card <brief.md> --repo <selector> --name <name> [--display-name <title>] [--comment <text>]
                   [--parent-worktree <selector> | --no-parent]

The prompt is the brief's `/goal` line (with ` 브리프: <brief path>` appended unless it already names a brief), then
its PROGRAM, APPROVED, lead and CLOSE/KEEP rows. A row is a line outside a ``` fence that starts with the key and a
colon (`CLOSE: …`, `` `CLOSE`: … ``) or the template's wide gap (`DELEGATE    …`), plus the indented lines under it.
A lead's brief (a MODE row, a `ROLE: lead <slug>` row or a `<project>: lead` head) needs every row of orchestrate
references/brief.md "Lead"; any brief needs CLOSE or KEEP, and one with an Approved heading or bullet needs its
`APPROVED:` row. The brief is a local file; a note only the notes store can reach is copied to one first.

`--prompt "$(cat brief.md)"` hides those lines from the guard hook, which reads only the command text; this reads
the file and checks them itself, with the hook's own patterns. The card is created from a subprocess, so no hook
sees that `orca worktree create`: the pressure gate's decision is taken here instead.

Exit codes: 0 created; 2 usage, unreadable brief or a missing line; 3 the pressure gate refused (wait as its
reason says, then run this again); 4 `orca worktree create` failed; 5 the card exists but logging it or naming it
failed (the create output is still printed).
"""
import json, os, pathlib, re, shlex, subprocess, sys

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


def run(cmd, stdin=None, timeout=120):
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return p, (p.stderr or p.stdout).strip()[:400]


def create(argv):
    if not argv or argv[0].startswith("-"):
        raise Refuse(2, __doc__)
    brief, rest, opts = argv[0], argv[1:], {}
    valued = {"--repo", "--name", "--display-name", "--comment", "--parent-worktree"}
    i = 0
    while i < len(rest):
        if rest[i] in valued and i + 1 < len(rest):
            opts[rest[i]], i = rest[i + 1], i + 2
        elif rest[i] == "--no-parent":
            opts["--no-parent"], i = True, i + 1
        else:
            raise Refuse(2, f"unexpected argument {rest[i]!r}\n{__doc__}")
    for k in ("--repo", "--name"):
        if not opts.get(k):
            raise Refuse(2, f"{k} is required (dispatch-card §3)")
    prompt = compose(brief)
    cmd = [ORCA, "worktree", "create", "--repo", opts["--repo"], "--name", opts["--name"]]
    cmd += ["--no-parent"] if opts.get("--no-parent") else ["--parent-worktree", opts.get("--parent-worktree", "active")]
    cmd += ["--agent", "claude", "--prompt", prompt]
    if opts.get("--comment"):
        cmd += ["--comment", opts["--comment"]]
    cmd.append("--json")
    reason, mark = pressure_check(shlex.join(["orca", *cmd[1:]]))
    if reason:
        raise Refuse(3, f"[{ME}] not created, the pressure gate refused it (for `queue wait`, run this same "
                        f"`skills-sync card` command again once it ends with 0): {reason}")
    p, err = run(cmd)
    try:
        out = json.loads(p.stdout) if p else None
    except ValueError:
        out = None
    if not p or p.returncode or not out or out.get("ok") is False or not (out.get("result") or {}).get("worktree"):
        e = (out or {}).get("error") or {}
        err = f"{e.get('code')}: {e.get('message')}" if e else err
        mark("failed", err)
        raise Refuse(4, f"[{ME}] orca worktree create failed: {err}")
    mark("done")
    res = out["result"]
    print(json.dumps(res, ensure_ascii=False, indent=1))
    failed = []
    path = res["worktree"].get("path")
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
        raise Refuse(5, f"[{ME}] the card was created but: {'; '.join(failed)}")
    return 0


def main(argv):
    try:
        return create(argv)
    except Refuse as e:
        print(e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
