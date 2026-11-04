#!/usr/bin/env python3
"""Read-only check of what a session restart, an Orca restart or a compaction may have left broken (reboot SKILL.md).

Usage:
  reboot.py [--slug SLUG | --top] [--run RUN_ID] [--json]

  --slug SLUG   this session is the lead of program SLUG: expects one `orch wait SLUG` and a bound Run
  --top         this session is the orchestrator: expects one `orch inbox --wait`, one `orch watch-cards`,
                a bound Run and the tab title `orchestrator`
  --run RUN_ID  the Run this session coordinates, when the program record does not name it
  neither       a worker or a plain session: only the handle, stray waits and the pressure queue are checked

One line per finding: `ok …`, `info …`, or `do …` with the command to run. Nothing is started, stopped or changed.
Exit 0 when no line says `do`, 1 when one does, 2 when Orca did not answer.
"""
import datetime as dt, json, os, pathlib, re, shlex, subprocess, sys

sys.dont_write_bytecode = True  # read-only: importing the shared helpers must not write __pycache__ either
HERE = pathlib.Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
PRESSURE = SKILLS / "reap-resources" / "scripts" / "pressure.py"
sys.path.insert(0, str(SKILLS / "orchestrate" / "scripts"))
import orca_settings  # noqa: E402  Orca's concise/full answers and worker rows are read there, once for every script
import rowstate  # noqa: E402  when an ended queue entry stops being listed is the row's ❔ rule, not a second one

PROGRAMS = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
GONE = re.compile(r"stale|not_found|not found", re.I)
INBOX = re.compile(r"(?:\borch|prog\.py)\s+(?:wait\b|inbox\b.*--wait\b)")
WAIT_SLUG = re.compile(r"(?:\borch|prog\.py)\s+wait\s+([^\s'\"]+)")
CARDS = re.compile(r"(?:\borch|prog\.py)\s+watch-cards\b(?!.*--once)")
ACTIVE_QUEUE = ("waiting", "retrying", "failed", "gave_up")


class OrcaError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def orca(*argv):
    """The result object of `orca <argv> --json`; OrcaError with Orca's error code when it says not ok."""
    try:
        p = subprocess.run([os.environ.get("SKILLS_SYNC_ORCA", "orca"), *argv, "--json"],
                           capture_output=True, text=True, timeout=30)
        data = json.loads(p.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise OrcaError("no_answer", str(e)) from None
    if not data.get("ok"):
        err = data.get("error") or {}
        raise OrcaError(err.get("code") or "error", err.get("message") or p.stderr.strip())
    return data.get("result") or {}


def ps_table():
    """{pid: (ppid, command)} for every process of this machine."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=30).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return rows


def session_pid(table, pid):
    """The nearest ancestor of pid whose program is `claude`: the session whose background tasks these are."""
    seen = set()
    while pid in table and pid not in seen:
        seen.add(pid)
        ppid, cmd = table[pid]
        if os.path.basename((cmd.split() or [""])[0]) == "claude":
            return pid
        pid = ppid
    return None


def waits(table, root):
    """(inbox waits, card watches) under root as lists of (pid, command): only the innermost match of a chain, so a
    shell wrapping `orch wait` and the python it runs count once."""
    kids = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    found = {"inbox": [], "cards": []}

    def walk(pid):
        hit = False
        for child in kids.get(pid, ()):
            hit = walk(child) or hit
        if hit or pid == root:
            return hit
        cmd = table[pid][1]
        for kind, pattern in (("inbox", INBOX), ("cards", CARDS)):
            if pattern.search(cmd):
                found[kind].append((pid, cmd))
                return True
        return False

    walk(root)
    return found["inbox"], found["cards"]


def program_run(slug):
    for path in PROGRAMS.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("slug") == slug:
            return cfg.get("run")
    return None


def worktree_root(cwd):
    p = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else cwd


def check_handle(handle, call, root):
    """(lines, terminal dict or None, live handle to speak for this terminal or None)."""
    if not handle:
        return [("info", "not an Orca terminal (no ORCA_TERMINAL_HANDLE): the Run, tab and worker checks are skipped")], None, None
    try:
        return [("ok", f"terminal handle {handle} is live")], call("terminal", "show", "--terminal", handle).get("terminal") or {}, handle
    except OrcaError as e:
        if not GONE.search(e.code):
            raise
    here = [t for t in (call("terminal", "list").get("terminals") or []) if t.get("worktreePath") == root]
    names = ", ".join(f"{t.get('handle')} ({t.get('title') or 'untitled'})" for t in here) or "none"
    return [("do", f"terminal handle {handle} is stale: Orca restarted under this session, so ORCA_TERMINAL_HANDLE "
                   f"keeps the old one. Live terminals in this worktree: {names}. Pass the one this session runs in "
                   "as --from/--terminal on every orca call, or start the session again in it (`claude --continue`) "
                   "so the variable is renewed")], None, None


def check_run(role, run, handle, call):
    """(lines, the Run whose workers to check or None, this terminal's own Dispatch when it is a dispatched lead)."""
    if not role or not handle:
        return [], None, None
    bound_id = (orca_settings.run_current(call, "--from", handle) or {}).get("id")
    if bound_id and (not run or bound_id == run):
        return [("ok", f"bound to Run {bound_id}")], bound_id, None
    if not bound_id and role == "lead":
        mine = orca_settings.terminal_dispatch(call, handle)
        if mine:  # a lead started as a dispatched worker: Orca refuses it a binding to the Run it works for
            return [("ok", f"dispatched lead ({mine.get('dispatchId')} on Run {mine.get('runId')}): binds no Run")], \
                mine.get("runId"), mine.get("dispatchId")
    target = run or "<the Run your briefs report to>"
    why = f"bound to Run {bound_id}, not {run}" if bound_id else "this terminal is bound to no Run"
    # No worker check against a Run this terminal should not hold: run the check again once bound.
    return [("do", f"{why}: `orca orchestration run-use --id {target}`, then `orca orchestration run-current --json` "
                   "to confirm, and run this check again for the workers")], None, None


def check_tab(role, terminal, handle):
    if role != "top" or terminal is None:
        return []
    if terminal.get("title") == "orchestrator":
        return [("ok", "tab titled orchestrator")]
    return [("do", f"`orca terminal rename --terminal {handle} --title orchestrator`")]


def check_waits(role, slug, table, pid):
    root = session_pid(table, pid)
    if root is None:
        return [("info", "this session's claude process was not found among the ancestors; waits not checked")]
    inbox, cards = waits(table, root)
    out = []
    start = f"orch wait {slug}" if role == "lead" else "orch inbox --wait"
    # A lead's one wait is `orch wait <its slug>`, the orchestrator's `orch inbox --wait`; any other is a wrong one.
    want = slug if role == "lead" else None
    wrong = [(p, c) for p, c in inbox if role and (WAIT_SLUG.findall(c) or [None])[0] != want]
    right = [w for w in inbox if w not in wrong]
    if wrong:
        then = "" if right else f", then start `{start}` under run_in_background"
        out.append(("do", f"an inbox wait other than `{start}` runs: stop "
                          + ", ".join(f"pid {p}" for p, _ in wrong) + f" with TaskStop{then}"))
    if len(right) > 1:
        out.append(("do", f"{len(right)} inbox waits run where one should: stop "
                          + ", ".join(f"pid {p}" for p, _ in right[1:])
                          + " with TaskStop on the background task that started it"))
    elif right:
        out.append(("ok", "1 inbox wait running"))
    elif role and not wrong:
        out.append(("do", f"no inbox wait runs: start `{start}` under run_in_background"))
    if len(cards) > 1:
        out.append(("do", f"{len(cards)} `orch watch-cards` run: stop all but one with TaskStop ("
                          + ", ".join(f"pid {p}" for p, _ in cards[1:]) + ")"))
    elif cards:
        out.append(("ok", "1 orch watch-cards running"))
    elif role == "top":
        out.append(("do", "no `orch watch-cards` runs: start it under run_in_background, no pipe"))
    elif role == "lead":
        out.append(("info", "no `orch watch-cards` runs: start one only if you started cards no Run lists"))
    return out


def check_workers(run, call, lead_dispatch=None, me=None):
    if not run:
        return []
    out = []
    workers = orca_settings.lead_scope(orca_settings.all_workers(call, "--run", run), lead_dispatch)
    by_task = {w.get("taskId"): w for w in workers if w.get("taskId")}
    own_task = next((w.get("taskId") for w in workers if w.get("dispatchId") == lead_dispatch), None)
    for w in workers:
        h = orca_settings.worker_terminal(w)
        if w.get("terminalState") != "active" or (h and h == me):
            continue
        if h:
            try:
                call("terminal", "show", "--terminal", h)
                continue
            except OrcaError as e:
                if not GONE.search(e.code):
                    raise
        d = w.get("dispatchId") or "<dispatch>"
        task = w.get("taskId") or "<task>"
        if not h:  # a worker can run without a terminal, so this alone is no reason to restart it
            out.append(("info", f"worker {d} (task {task}) has no terminal on record: "
                                f"`{shlex.join(['orca', 'orchestration', 'worker-show', '--dispatch', d])}` shows "
                                "whether it is live; restart it only when that shows it is not"))
            continue
        parent = by_task.get(((w.get("projection") or {}).get("parent") or {}).get("taskId"))
        if parent and parent.get("taskId") != own_task:  # a unit under another lead is that lead's to restart
            out.append(("do", f"worker {d} (task {task}) lost its terminal {h}; it sits under "
                              f"{parent.get('dispatchId')}: send that lead this line, and it restarts its own worker"))
            continue
        card = (w.get("resource") or {}).get("worktreeId") or "<its worktree>"
        provider = (w.get("projection") or {}).get("provider") or {}
        # A retry inherits neither placement nor model, so both are named again.
        argv = ["orca", "orchestration", "worker-start", "--retry-of", d, "--task", task, "--worktree", f"id:{card}",
                "--agent", provider.get("id") or "<agent>"] + (["--model", provider["model"]] if provider.get("model") else [])
        note = "" if provider.get("model") else (" (Orca recorded no model: add `--model <id>` when the worker ran one, "
                                                 "from its brief or the program note)")
        out.append(("do", f"worker {d} (task {task}) lost its terminal {h}: restart it on its card, "
                          f"`{shlex.join(['orca', 'orchestration', 'worker-stop', '--dispatch', d])}`, then "
                          f"`{shlex.join(argv)}`{note}"))
    if not any(k == "do" for k, _ in out):
        out.append(("ok", f"every active worker with a terminal on record{' under ' + lead_dispatch if lead_dispatch else ''} of Run {run} "
                          "has a live terminal"))
    return out


def check_queue(root, read=None):
    if read is None:
        sys.path.insert(0, str(SKILLS / "reap-resources" / "scripts"))
        import pressure
        read = pressure.queue_read
    out, now = [], dt.datetime.now(dt.timezone.utc)
    for e in sorted(read().values(), key=lambda e: e.get("added") or 0):
        cwd = e.get("cwd") or ""
        if e.get("state") not in ACTIVE_QUEUE or rowstate.queue_over(e, now) \
                or not (cwd == root or cwd.startswith(root.rstrip("/") + "/")):
            continue
        head = f"pressure queue {e['id']} ({e['what']}) {e['state']}"
        if e["state"] == "waiting":
            out.append(("do", f"{head}: watch `python3 {shlex.quote(str(PRESSURE))} queue wait {e['id']}` with "
                              f"Monitor again; once it exits 0, run the held command again from {shlex.quote(cwd)}: "
                              f"`{e.get('command')}`"))
        elif e["state"] == "gave_up":
            out.append(("do", f"{head}: its deadline passed before it ran; your brief's rule for a held start "
                              "decides, otherwise report it"))
        else:  # retrying or failed: the retry was started, so its result comes first
            out.append(("do", f"{head}: its retry was started; find that command's result (its output, "
                              f"`python3 {shlex.quote(str(PRESSURE))} queue` for the error) before running it again"))
    return out


def run_checks(role, slug, run, handle, cwd, call=orca, table=None, pid=None, queue=None):
    root = worktree_root(cwd)
    lines, terminal, live = check_handle(handle, call, root)
    run_lines, bound, lead_dispatch = check_run(role, run or (program_run(slug) if slug else None), live, call)
    lines += run_lines
    lines += check_tab(role, terminal, live)
    lines += check_waits(role, slug, ps_table() if table is None else table, os.getpid() if pid is None else pid)
    if live:
        lines += check_workers(bound, call, lead_dispatch, live)
    lines += check_queue(root, queue)
    return lines


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        return 0
    opts = {"--slug": None, "--run": None}
    flags = set()
    it = iter(argv)
    for a in it:
        if a in opts:
            opts[a] = next(it, None)
        elif a in ("--top", "--json"):
            flags.add(a)
        else:
            print(f"unknown argument {a}\n\n{__doc__.strip()}", file=sys.stderr)
            return 2
    if opts["--slug"] and "--top" in flags:
        print("--slug and --top name different roles; pass one", file=sys.stderr)
        return 2
    role = "top" if "--top" in flags else "lead" if opts["--slug"] else None
    try:
        lines = run_checks(role, opts["--slug"], opts["--run"], os.environ.get("ORCA_TERMINAL_HANDLE"), os.getcwd())
    except OrcaError as e:
        print(f"Orca did not answer ({e}); nothing was checked past this point", file=sys.stderr)
        return 2
    if "--json" in flags:
        print(json.dumps([{"kind": k, "text": t} for k, t in lines], ensure_ascii=False))
    else:
        for kind, text in lines:
            print(f"{kind:4} {text}")
    return 1 if any(k == "do" for k, _ in lines) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
