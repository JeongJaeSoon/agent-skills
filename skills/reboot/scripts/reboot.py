#!/usr/bin/env python3
"""Read-only check of what a session restart, an Orca restart or a compaction may have left broken (reboot SKILL.md).

Usage:
  reboot.py [--slug SLUG | --top] [--run RUN_ID] [--json]
  reboot.py sessions PATH... [--under] [--since ISO_DATE] [--limit N]

  --slug SLUG   this session is the lead of program SLUG: expects one `orch wait SLUG` and a bound Run
  --top         this session is the orchestrator: expects one `orch inbox --wait`, one `orch watch-cards`,
                a bound Run and the tab title `orchestrator`
  --run RUN_ID  the Run this session coordinates, when the program record does not name it
  neither       a worker or a plain session: only the handle, stray waits and the pressure queue are checked

One line per finding: `ok …`, `info …`, or `do …` with the command to run. Nothing is started, stopped or changed.
The first check is `orca status`. Exit 0 when no line says `do`, 1 when one does, 2 when Orca did not answer or is
not ready (outside an Orca terminal, a not-ready Orca is an `info` line and the rest is checked).

`sessions` lists the Claude Code sessions that ran in each PATH, newest first: last write, session id, transcript, and
the first prompt. --under also takes the paths below each PATH (removed cards of a worktree root). Exit 1 when none.
"""
import datetime as dt, json, os, pathlib, re, shlex, subprocess, sys

sys.dont_write_bytecode = True  # read-only: importing the shared helpers must not write __pycache__ either
HERE = pathlib.Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
PRESSURE = SKILLS / "reap-resources" / "scripts" / "pressure.py"
sys.path.insert(0, str(SKILLS / "orchestrate" / "scripts"))
import orca_settings  # noqa: E402  Orca's concise/full answers and worker rows are read there, once for every script
import rowstate  # noqa: E402  when an ended queue entry stops being listed is the row's ❔ rule, not a second one
sys.path.insert(0, str(SKILLS.parent / "hooks"))
import roles  # noqa: E402  the session's SessionStart record (terminals/<handle>.json) is read there

PROGRAMS = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
GONE = re.compile(r"stale|not_found|not found", re.I)
INBOX = re.compile(r"(?:\borch|prog\.py)\s+(?:wait\b|inbox\b.*--(?:wait|stream)\b)")
STREAM = re.compile(r"(?:\borch|prog\.py)\s+inbox\b.*--stream\b")
WAIT_SLUG = re.compile(r"(?:\borch|prog\.py)\s+wait\s+([^\s'\"]+)")
CARDS = re.compile(r"(?:\borch|prog\.py)\s+watch-cards\b(?!.*--once)")
FINISHED = ("completed", "failed")  # dispatch statuses whose worker reported; "abandoned" is not one, a restart makes it
RESTART_LOST = ("abandoned", "interrupted")  # what Orca calls a Dispatch whose terminal a restart took
ACTIVE_QUEUE =("waiting", "retrying", "failed", "gave_up")


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
    """(inbox waits, card watches) under root as lists of (pid, command): only the innermost match of each kind in a
    chain, so a shell wrapping `orch wait` and the python it runs count once, and an inbox stream counts though the
    card watch it runs as its child matched below it."""
    kids = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    found = {"inbox": [], "cards": []}

    def walk(pid):
        hit = set()
        for child in kids.get(pid, ()):
            hit |= walk(child)
        if pid == root:
            return hit
        cmd = table[pid][1]
        for kind, pattern in (("inbox", INBOX), ("cards", CARDS)):
            if kind not in hit and pattern.search(cmd):
                found[kind].append((pid, cmd))
                return hit | {kind}
        return hit

    walk(root)
    return found["inbox"], found["cards"]


def program_cfg(slug):
    for path in PROGRAMS.glob("*/program.json"):
        try:
            cfg = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("slug") == slug:
            return cfg
    return {}


def worktree_root(cwd):
    p = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else cwd


def check_status(call, strict=True):
    """`orca status` first, so an Orca that is down or still starting is named as such (exit 2) instead of surfacing
    as whichever terminal or Run read happens to fail. strict False (no Orca terminal, so nothing else here reads
    Orca): an `info` line instead."""
    try:
        st = call("status")
        app, rt = st.get("app") or {}, st.get("runtime") or {}
        if not (rt.get("reachable") and rt.get("state") == "ready"):
            raise OrcaError("not_ready", f"app running: {app.get('running')}, runtime state: {rt.get('state')}")
    except OrcaError as e:
        if strict:
            raise
        return [("info", f"Orca is not ready ({e}); this session is no Orca terminal, so nothing else here needs it")]
    return [("ok", f"Orca {rt.get('appVersion') or '?'} is ready")]


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


def card_agents(call, worktree_id):
    """The handles of the agent terminals open in the worktree. Raises OrcaError when the list cannot be read:
    an unreadable list is not an empty one."""
    if not worktree_id:
        return []
    res = call("terminal", "list", "--worktree", f"id:{worktree_id}")
    if res.get("truncated"):
        raise OrcaError("truncated", "the terminal list is cut short")
    terms = res.get("terminals") or []
    return [t["handle"] for t in terms if t.get("agentIdentity") and t.get("handle")]


def observed(w):
    return ((w.get("projection") or {}).get("evidence") or {}).get("lastObservedAt") or 0


def has_dispatch(call, handles, worktree_id, since=0):
    """Whether a later Dispatch of any Run is on one of the terminals or on the worktree. worker-list is scoped to this
    terminal's Run, so every page of run-list is read, Run by Run: the resume may have been made from another Run, and
    its worker may have finished and closed its terminal since. OrcaError when a read fails."""
    for res, _ in orca_settings.pages(call, "run-list", error=OrcaError):
        for r in res.get("runs") or []:
            for w in orca_settings.all_workers(call, "--run", r.get("id"), "--limit", "100", error=OrcaError):
                on_terminal = orca_settings.worker_terminal(w) in handles
                on_card = bool(worktree_id) and (w.get("resource") or {}).get("worktreeId") == worktree_id
                if not (on_terminal or on_card) or (not on_terminal and not (since and observed(w) > since)) or w.get("dispatchStatus") in RESTART_LOST or w.get("terminalState") == "released" and \
                        w.get("dispatchStatus") not in FINISHED:
                    continue
                # A Dispatch that already reported counts only when Orca saw it after the abandoned one: rows carry no
                # creation time, and an older finished worker proves nothing about this resume.
                if w.get("dispatchStatus") not in FINISHED or (since and observed(w) > since):
                    return True
    return False


def own_dispatch(call, handle):
    """This terminal's Dispatch while it still works for a Run. An abandoned one is the Dispatch the restart took: Orca
    keeps its row on the terminal, but the lead has lost it."""
    mine = orca_settings.terminal_dispatch(call, handle, settled=True)
    return None if mine and mine.get("dispatchStatus") in RESTART_LOST else mine


def check_run(role, run, handle, call, lead_dispatch=None, slug=None):
    """(lines, the Run whose workers to check or None, this terminal's own Dispatch when it is a dispatched lead).
    lead_dispatch: the Dispatch program.json says this lead worked under."""
    if not role or not handle:
        return [], None, None
    bound_id = (orca_settings.run_current(call, "--from", handle) or {}).get("id")
    if bound_id and role == "lead" and lead_dispatch and run and bound_id != run \
            and not own_dispatch(call, handle):
        # run-create was done and the session ended before `orch set`: the program still names its coordinator's Run.
        return [("do", f"this dispatched lead lost its Dispatch ({lead_dispatch}) and is bound to its own Run {bound_id}, "
                       f"but the program still names {run}: `orch set {slug or '<slug>'} run {bound_id}` (it also drops "
                       "lead_dispatch), and run this check again")], None, None
    if bound_id and (not run or bound_id == run):
        return [("ok", f"bound to Run {bound_id}")], bound_id, None
    if not bound_id and role == "lead":
        mine = own_dispatch(call, handle)
        # A Dispatch other than the one program.json names (the coordinator resumed the lead with worker-start) is the
        # same loss for the program: `orch wait` refuses a terminal that is not its lead_dispatch.
        if mine and lead_dispatch and mine.get("dispatchId") != lead_dispatch:
            mine = None
        if mine:  # a lead started as a dispatched worker: Orca refuses it a binding to the Run it works for
            return [("ok", f"dispatched lead ({mine.get('dispatchId')} on Run {mine.get('runId')}): binds no Run")], \
                mine.get("runId"), mine.get("dispatchId")
    if not bound_id and role == "lead" and lead_dispatch:
        # The restart cost it the Dispatch, and the program's Run is its coordinator's: run-use there would take that
        # Run's binding from the coordinator, so the lead coordinates a Run of its own.
        whose = f"the Run {run}" if run else "the Run it worked on"
        return [("do", f"this dispatched lead lost its Dispatch ({lead_dispatch}) and {whose} is its coordinator's, "
                       "so do not bind this terminal to it: `orca orchestration run-create --objective <the program's goal>`, then "
                       f"`orch set {slug or '<slug>'} run <the new Run>` (it also drops lead_dispatch), and run this "
                       "check again")], None, None
    target = run or "<the Run your briefs report to>"
    why = f"bound to Run {bound_id}, not {run}" if bound_id else "this terminal is bound to no Run"
    # No worker check against a Run this terminal should not hold: run the check again once bound.
    return [("do", f"{why}: `orca orchestration run-use --id {target}`, then `orca orchestration run-current --json` "
                   "to confirm, and run this check again for the workers")], None, None


def own_session(table, pid):
    """This check's own Claude Code session id, found through the process tree and the sessions Claude Code registers;
    None when it cannot be told."""
    claude = session_pid(table, pid)
    try:
        import fleet  # beside orca_settings, which imports it anyway
        # fleet's process table also carries each start time, which a live session's registration is checked against.
        return next((sid for sid, s in fleet.live_sessions(fleet.ps_table()).items() if claude in s["pids"]), None)
    except Exception:  # the line then says nothing rather than guess
        return None


def check_reload(handle, root=None, session=None):
    """A line when this session loaded another hooks/hooks.json than the checkout has now. `info`, never `do`: the
    check only reports (the session restarts itself or not), and a missing record is not stale. The terminal's record
    names the session that started last in it, so it counts only when that is this session (`session`)."""
    if not handle:  # the record is keyed by terminal handle; check_handle already says Orca checks are skipped
        return []
    current = roles.hooks_sha(root or SKILLS.parent)
    if not current:
        return [("info", f"{root or SKILLS.parent}/hooks/hooks.json is unreadable: whether this session needs a reload is unknown")]
    if not session:
        return [("info", "this session's id could not be told, so its SessionStart record cannot be matched: reload need unknown")]
    state = roles.reload_state([handle], current, {session})
    if state == "unreadable":
        return [("info", f"the SessionStart record of terminal {handle} is unreadable: reload need unknown")]
    if state == "stale":
        return [("info", "reload needed: this session loaded an older hooks/hooks.json than the checkout has now, so "
                         "it runs without the newer hooks. Restart it (`claude --continue` in this terminal)")]
    if state == "unknown":
        return [("info", "no SessionStart record of the hooks this session loaded: reload need unknown, not assumed")]
    return [("ok", "hooks/hooks.json is the one this session loaded")]


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
    how, streamed = "under run_in_background", False
    if role == "top":
        start, how = "orch inbox --stream", "with the Monitor tool, timeout_ms 1800000 (not Bash)"
        streamed = any(STREAM.search(c) for _, c in inbox)
        if inbox and not streamed:
            out.append(("info", "the inbox wait is `orch inbox --wait`: at its next wake, stop it and start "
                                "`orch inbox --stream` with the Monitor tool instead (one call per wake, not three)"))
    want = slug if role == "lead" else None
    wrong = [(p, c) for p, c in inbox if role and (WAIT_SLUG.findall(c) or [None])[0] != want]
    right = [w for w in inbox if w not in wrong]
    if wrong:
        then = "" if right else f", then start `{start}` {how}"
        out.append(("do", f"an inbox wait other than `{start}` runs: stop "
                          + ", ".join(f"pid {p}" for p, _ in wrong) + f" with TaskStop{then}"))
    if len(right) > 1:
        out.append(("do", f"{len(right)} inbox waits run where one should: stop "
                          + ", ".join(f"pid {p}" for p, _ in right[1:])
                          + " with TaskStop on the background task that started it"))
    elif right:
        out.append(("ok", "1 inbox wait running"))
    elif role and not wrong:
        out.append(("do", f"no inbox wait runs: start `{start}` {how}"))
    if len(cards) > 1:
        out.append(("do", f"{len(cards)} `orch watch-cards` run: stop all but one with TaskStop ("
                          + ", ".join(f"pid {p}" for p, _ in cards[1:]) + ")"))
    elif cards:
        out.append(("ok", "1 orch watch-cards running"))
    elif streamed:
        out.append(("info", "the inbox stream starts its own card watch; none runs this moment (between rounds, or "
                            "it stopped after three failures: its CARDS: line says which)"))
    elif role == "top":
        out.append(("do", "no `orch watch-cards` runs: start it under run_in_background, no pipe"))
    elif role == "lead":
        out.append(("info", "no `orch watch-cards` runs: start one only if you started cards no Run lists"))
    return out


def resume_line(w, d, task, handle, running=None):
    """The recovery of an abandoned worker (reboot SKILL.md "Resume a worker"): its session resumed in a new terminal
    of its own worktree, then a new Dispatch attached to that terminal. running: the agent terminal already open there
    (a resume cut short), which only needs the Dispatch."""
    card = (w.get("resource") or {}).get("worktreeId") or ""
    sel = shlex.quote(f"id:{card}") if card else "id:<its worktree id>"  # the whole id: workspaces can share a path
    provider = (w.get("projection") or {}).get("provider") or {}
    model = provider.get("model") or "<alias>"
    agent = provider.get("id") or "claude"
    if agent == "claude":
        resume = f"claude --resume <session-id> --model {model}"
    elif agent == "codex":
        resume = f"codex resume <session-id> -m {model}"
    else:
        resume = f"<{agent}'s own command that resumes session <session-id>>"
    if running:
        return (f"worker {d} (task {task}) was abandoned and its worktree runs an agent terminal {running} with no "
                f"Dispatch (a resume cut short): attach one, `orca orchestration worker-start --spec-file <resume "
                f"instruction> --worktree {sel} --terminal {running}` (reboot SKILL.md \"Resume a worker\"; if that "
                "session is not this worker's, leave it)")
    gone = f"its terminal {handle} is gone" if handle else "it has no terminal"
    return (f"worker {d} (task {task}) was abandoned and {gone}: resume it, `orca terminal create --worktree {sel} "
            f"--command \"{resume}\"`, then `orca orchestration worker-start "
            f"--spec-file <resume instruction> --worktree {sel} --terminal <the new handle>` "
            "(reboot SKILL.md \"Resume a worker\"; if you ended it on purpose, leave it)")


def check_workers(run, call, lead_dispatch=None, me=None):
    if not run:
        return []
    out = []
    workers = orca_settings.lead_scope(orca_settings.all_workers(call, "--run", run), lead_dispatch)
    by_task = {w.get("taskId"): w for w in workers if w.get("taskId")}
    own_task = next((w.get("taskId") for w in workers if w.get("dispatchId") == lead_dispatch), None)
    finished, newest = 0, {}
    for w in workers:
        newest.setdefault((w.get("resource") or {}).get("worktreeId"), w)
    for w in workers:
        h = orca_settings.worker_terminal(w)
        state, status = w.get("terminalState"), w.get("dispatchStatus")
        if h and h == me:
            continue
        if state == "released" and status not in RESTART_LOST:
            continue
        if state == "released":  # the terminal resource is freed, which is not a report: unfinished work may remain
            out.append(("info", f"worker {w.get('dispatchId') or '<dispatch>'} is abandoned and its terminal was released: "
                                "resume it (reboot SKILL.md \"Resume a worker\") if its work is unfinished"))
            continue
        if status in FINISHED:  # it reported: a dead terminal is its normal end, not something to wake
            finished += 1
            continue
        # worker-list is newest first: a later Dispatch on the same worktree (the resume) supersedes an abandoned one.
        wt = (w.get("resource") or {}).get("worktreeId")
        abandoned = status in RESTART_LOST
        if abandoned and wt and newest[wt] is not w:
            continue
        if state != "active" and not abandoned:
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
        if not abandoned and not h:  # a worker can run without a terminal, so this alone is no reason to restart it
            out.append(("info", f"worker {d} (task {task}) has no terminal on record: "
                                f"`{shlex.join(['orca', 'orchestration', 'worker-show', '--dispatch', d])}` shows "
                                "whether it is live; restart it only when that shows it is not"))
            continue
        parent = by_task.get(((w.get("projection") or {}).get("parent") or {}).get("taskId"))
        if parent and parent.get("taskId") != own_task:  # a unit under another lead is that lead's to restart
            out.append(("do", f"worker {d} (task {task}) lost its terminal {h or '(none on record)'}; it sits under "
                              f"{parent.get('dispatchId')}: send that lead this line, and it restarts its own worker"))
            continue
        if abandoned:  # Orca settled it when its terminal went: there is nothing to stop or retry, only a session to resume
            # An unreadable list is not an empty one: OrcaError ends the check with exit 2, issuing no command from a guess.
            agents = card_agents(call, wt)
            resumed = has_dispatch(call, agents, wt, observed(w))
            if agents:  # a session runs there: resumed, maybe from another Run
                agent = agents[0] if len(agents) == 1 else "<the handle of the worker's own terminal: " + ", ".join(agents) + ">"
                if resumed:
                    out.append(("info", f"worker {d} (task {task}) is abandoned here, but its worktree has a live agent "
                                        f"terminal ({agent}) with a Dispatch: it was resumed (perhaps on another Run)"))
                else:  # stopped between `terminal create` and `worker-start`: the session has no mailbox
                    out.append(("do", resume_line(w, d, task, h, agent)))
                continue
            if resumed:  # a later Dispatch on the card (from another Run) has taken the work over
                out.append(("info", f"worker {d} (task {task}) is abandoned here, but a later Dispatch of another Run is on "
                                    "its worktree: it was resumed"))
                continue
            out.append(("do", resume_line(w, d, task, h)))
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
    if finished:
        out.append(("info", f"{finished} worker{'s' if finished != 1 else ''} finished (completed or failed) and "
                            "are not restarted, whatever became of their terminals"))
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


def check_handoff(key, run, call=orca, last=None):
    """The handoff the previous session left (`orch handoff save`) and the mail of the Run taken since it was written:
    the stream prints mail as it takes it, so what a restart cut off before it was handled is in Orca's history."""
    if last is None:
        try:
            import store
            last = store.last_session_event(key, "handoff")
        except Exception as e:  # noqa: BLE001  the other checks still run
            return [("info", f"handoff not read ({type(e).__name__}: {e})")]
    if not last:
        return [("info", f"no handoff recorded for {key}")]
    ts, data = last
    out = [("info", f"handoff {ts}: " + "; ".join(f"{k}={v}" for k, v in (data or {}).items() if v and k != "handle")
                    + " (`orch handoff show` adds the open decisions)")]
    if not run:
        return out
    try:
        msgs = call("orchestration", "inbox", "--terminal", f"run:{run}", "--limit", "100", "--full").get("messages") or []
    except OrcaError as e:
        return out + [("info", f"mail since the handoff not read ({e})")]
    since = [m for m in msgs if isinstance(m, dict) and str(m.get("created_at") or "") > ts and m.get("type") != "heartbeat"]
    for m in since[-10:]:
        out.append(("info", f"mail since the handoff: {m.get('id')} {m.get('type')} from {m.get('from_handle')}: "
                            f"{str(m.get('subject') or '')[:80]} (handled before the restart? check before acting twice)"))
    if len(since) > 10:
        out.append(("info", f"{len(since) - 10} more since the handoff: orca orchestration inbox --terminal run:{run} --full"))
    return out


def run_checks(role, slug, run, handle, cwd, call=orca, table=None, pid=None, queue=None, reload_root=None):
    root = worktree_root(cwd)
    lines = check_status(call, strict=bool(handle))
    handle_lines, terminal, live = check_handle(handle, call, root)
    lines += handle_lines
    tbl = ps_table() if table is None else table
    lines += check_reload(handle, reload_root, own_session(tbl, os.getpid() if pid is None else pid) if handle else None)
    cfg = program_cfg(slug) if slug else {}
    run_lines, bound, lead_dispatch = check_run(role, run or cfg.get("run"), live, call, cfg.get("lead_dispatch"), slug)
    lines += run_lines
    lines += check_tab(role, terminal, live)
    lines += check_waits(role, slug, tbl, os.getpid() if pid is None else pid)
    if live:
        lines += check_workers(bound, call, lead_dispatch, live)
    lines += check_queue(root, queue)
    if role:
        lines += check_handoff("orchestrator" if role == "top" else f"lead:{slug}", bound, call)
    return lines


def transcript_cwd(f, lines=200):
    """The working directory a transcript records, None when its first lines name none."""
    try:
        with open(f, encoding="utf-8") as fh:
            for _, line in zip(range(lines), fh):
                rec = json.loads(line)
                if isinstance(rec, dict) and isinstance(rec.get("cwd"), str):
                    return rec["cwd"]
    except (OSError, ValueError):
        pass
    return None


def sessions(paths, under=False, since=None, limit=None, root=None):
    """[(mtime, session id, transcript, first prompt)] of the Claude Code sessions that ran in paths, newest first.
    Claude Code keeps them in projects/<the path with every character but a letter or digit written as ->/; under also
    takes the paths below each one, such as cards already removed from a worktree root. The folder name cannot tell
    /work/app/x from /work/app-x, so each transcript's own cwd decides; one with none counts only in its exact folder."""
    root = pathlib.Path(root or os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()
    paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
    names = {p: re.sub(r"[^A-Za-z0-9]", "-", p) for p in paths}

    def ran_in(f, exact):
        cwd = transcript_cwd(f)
        if cwd is None:
            return exact
        return any(cwd == p or (under and cwd.startswith(p.rstrip("/") + "/")) for p in paths)

    found = []
    for d in (root.iterdir() if root.is_dir() else ()):
        exact = d.name in names.values()
        if not exact and not (under and any(d.name.startswith(n + "-") for n in names.values())):
            continue
        for f in d.glob("*.jsonl"):  # one level only: subagents/ transcripts are not sessions
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if (since is None or m >= since) and ran_in(f, exact):
                found.append((m, f))
    found = sorted(found, key=lambda x: x[0], reverse=True)[:limit]
    return [(m, f.stem, f, roles.first_prompt(str(f))) for m, f in found]


def sessions_main(argv):
    paths, under, since, limit = [], False, None, None
    it = iter(argv)
    try:
        for a in it:
            if a == "--under":
                under = True
            elif a == "--since":
                since = dt.datetime.fromisoformat(next(it)).timestamp()
            elif a == "--limit":
                limit = int(next(it))
                if limit < 1:
                    raise ValueError(f"--limit {limit}: give 1 or more")
            elif a.startswith("-"):
                raise ValueError(a)
            else:
                paths.append(a)
    except (StopIteration, ValueError) as e:
        print(f"bad argument {e}\n\n{__doc__.strip()}", file=sys.stderr)
        return 2
    if not paths:
        print(f"name at least one path\n\n{__doc__.strip()}", file=sys.stderr)
        return 2
    rows = sessions(paths, under, since, limit)
    for m, sid, f, prompt in rows:
        head = " ".join(prompt.split())[:120] or "(no prompt)"
        print(f"{dt.datetime.fromtimestamp(m).isoformat(timespec='minutes')}  {sid}  {f}\n    {head}")
    if not rows:
        print("no session transcript found for " + ", ".join(paths), file=sys.stderr)
    return 0 if rows else 1


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__.strip())
        return 0
    if argv[:1] == ["sessions"]:
        return sessions_main(argv[1:])
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
