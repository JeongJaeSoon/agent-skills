"""One automatic wake for a worker whose turn ended with its task open, run from `orch wait`.

`orchestrate.auto_wake_min` in ~/.claude/agent-skills.json turns it on; with no key `orch wait` only marks the stall
and says auto-wake is off. N minutes after `orch wait` first saw an idle row, a dispatch gets the content by
`orchestration send`, then `skills-sync wake-target` and the typed signal (references/waking.md). A dispatch is woken
once: the ledger says so, and a second stop goes to the human's Needs you. `auto-wake.json` holds only the first-seen
times, which the ledger has no row for until the wake.

Rows, each with `dispatch`: auto_wake_claimed (written before the act, so a crash can never mean two wakes),
auto_wake (dispatch, ticket, reason, marked_at, woken_at, route), auto_wake_failed, auto_wake_escalated.
"""
import dataclasses, datetime as dt, json, os, pathlib, re, subprocess, sys, tempfile, time

import prog

KEY = "auto_wake_min"
OFF_LINE = (f"auto-wake is off: set orchestrate.{KEY} in ~/.claude/agent-skills.json to wake an idle worker once "
            "(references/waking.md)")
SYNC = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "sync.py"
SIGNAL = "run your orchestration check"
BUSY = ("title shows the agent is busy", "turn in progress")  # sync.classify's refusals for a running turn
ROWS = ("auto_wake_claimed", "auto_wake", "auto_wake_failed", "auto_wake_escalated")
INBOX_LIMIT = 5000  # Orca's inbox spans every Run with no cursor; 1000 rows covered under a day of a busy machine
READY = re.compile(r"\bREADY\b")
OPEN_MAIL = ("question", "escalation", "decision_gate")
STOP_NOTICE = "msg_stop_"  # the escalation Orca itself sends when a worker stops: the stop, not a question it waits on
SYNC_TIMEOUT_S = 60


@dataclasses.dataclass
class Round:
    lines: list = dataclasses.field(default_factory=list)
    skip: set = dataclasses.field(default_factory=set)  # dispatches `orch wait` must not tell the lead to wake
    next_s: float = None  # seconds to the nearest deadline
    stop: bool = False  # the lead has something to read now

    def hold(self, dispatch, seconds):
        self.skip.add(dispatch)
        self.next_s = seconds if self.next_s is None else min(self.next_s, seconds)

    def timeout_ms(self, default):
        return default if self.next_s is None else str(min(int(default), max(1000, int(self.next_s * 1000))))


def minutes(key=KEY):
    """N, or None for off. Read as no_ci_repo reads it; anything but a positive int is off, never a guess."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        cfg = json.loads(path.read_text())
        n = (cfg.get("orchestrate") or {}).get(key)
    except (OSError, ValueError, AttributeError):
        return None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def orca(*args):
    return prog.orca_json(*args)


def sync(*args):
    r = subprocess.run([sys.executable, str(SYNC), *args], capture_output=True, text=True, timeout=SYNC_TIMEOUT_S)
    return r.returncode, (r.stdout or r.stderr).strip()


def stamp(t):
    return t.isoformat()


def load_marks(p):
    try:
        marks = json.loads((p.dir / "auto-wake.json").read_text())
    except (OSError, ValueError):
        return {}
    return marks if isinstance(marks, dict) else {}


def save_marks(p, marks):
    fd, tmp = tempfile.mkstemp(dir=p.dir, prefix=".auto-wake-")
    with os.fdopen(fd, "w") as f:
        json.dump(marks, f)
    os.replace(tmp, p.dir / "auto-wake.json")


class Facts:
    """Why a dispatch must not be woken, read once per round and only when one is due. state.json can be 3 minutes
    old, so everything here is read fresh."""

    def __init__(self, p, events):
        self.p, self.events = p, events
        self.workers = self.mail = self.answered = self.gates = None

    def why_not(self, d, ticket):
        try:
            return self._why_not(d, ticket)
        except (SystemExit, Exception) as e:  # what cannot be read is not a yes; the next round reads it again
            return f"could not read ({type(e).__name__}: {e})"

    def _why_not(self, d, ticket):
        if prog.stopped(self.events):
            return "the program is stopped"
        if self.workers is None:
            self.workers = prog.all_workers(self.p.cfg["run"])
        w = next((x for x in self.workers if x.get("dispatchId") == d), None)
        # retained: a kept-open worker, still dispatched and in progress, as most live workers of a real Run are
        if not w or w.get("terminalState") not in ("active", "retained"):
            return "released"
        pj = w.get("projection") or {}
        if pj.get("outcome") != "in_progress":
            return f"settled ({pj.get('outcome')})"
        if (pj.get("liveness") or {}).get("verdict") == "exited":
            return "its agent exited"
        # done with the outcome still in_progress is how a Claude worker that stopped early reads
        if not self.stopped_to_orca(pj):
            return "neither Orca nor its transcript shows it stopped"
        if not prog.worker_terminal(w):
            return "no terminal"
        if (pj.get("nextAction") or {}).get("kind") not in (None, "none") or (pj.get("attention") or {}).get("requiresAction"):
            return "waiting on an answer (Orca's next action)"
        if why := self.ledger_why_not(ticket):
            return why
        if d in self.stuck():
            return "STUCK: a prompt a wake cannot answer"
        if self.gate_open(w.get("taskId")):
            return "a decision gate is open on its task"
        if why := self.mail_why_not(d):
            return why
        if not (self.p.dir / "briefs" / f"{ticket}.md").exists():
            return "no brief file to point it at"
        return None

    @staticmethod
    def stopped_to_orca(pj):
        stage = pj.get("stage") or {}
        if stage.get("activity") in ("idle", "done"):
            return True
        # Orca stops refreshing a long-idle Claude worker's status: activity unknown, liveness stale_status, worker
        # still ready. sweep only asks about a dispatch the collector's transcript motion shows idle (not parked),
        # so that is the evidence of a stop. state.json can be minutes old: a turn resumed since is caught by
        # `skills-sync nudge`'s busy check, as it is for an idle projection.
        return (stage.get("activity") == "unknown" and stage.get("worker") == "ready"
                and (pj.get("liveness") or {}).get("reason") == "stale_status")

    def ledger_why_not(self, ticket):
        import dash
        if ticket in dash.parked_tickets(self.events):
            return "held"
        prs = [pr for pr, t in prog.pr_ticket(self.events).items() if t == ticket]
        if any(prog.on_hold(self.events, pr) for pr in prs):
            return "held"
        # A verdict alone counts: a READY stop on a PR that never reached `land-check` has no `ready` row. A row
        # from before the worker went back to work holds it too; that costs a hand wake, never a wrong one.
        state = prog.pr_state(self.events)
        if any("ready" in state[pr] or "verdict" in state[pr] for pr in prs):
            return "READY reported"
        return None

    def stuck(self):
        try:
            return json.loads((self.p.dir / "stuck.json").read_text())
        except (OSError, ValueError):
            return {}

    def gate_open(self, task):
        if self.gates is None:
            self.gates = orca("orchestration", "gate-list", "--run", self.p.cfg["run"], "--status", "pending").get("gates") or []
        return any((g.get("taskId") or g.get("task_id") or task) == task for g in self.gates)

    def mail_why_not(self, d):
        import fleet, orca_settings
        if self.mail is None:
            self.mail = orca_settings.with_full(orca, "orchestration", "inbox", "--limit", str(INBOX_LIMIT)).get("messages") or []
            self.answered = fleet.answered_mail(self.mail)
        if len(self.mail) >= INBOX_LIMIT:
            at = orca_settings.with_full(orca, "orchestration", "worker-show", "--dispatch", d)["dispatch"]["dispatchedAt"]
            start = prog.parse_ts(at)
            start = start if start.tzinfo else start.replace(tzinfo=dt.timezone.utc)  # Orca writes UTC with no zone
            if min(prog.parse_ts(m["created_at"]) for m in self.mail) > start:
                return "the inbox page does not reach back to its start"
        for m in self.mail:
            h = m.get("from_handle") or ""
            if d not in (prog.payload_of(m).get("dispatchId"), h[9:] if h.startswith("dispatch:") else None):
                continue
            if str(m.get("id")).startswith(STOP_NOTICE):
                continue
            if m.get("type") in OPEN_MAIL and m.get("id") not in self.answered:
                return "waiting on an answer (open question)"
            if READY.search(f"{m.get('subject') or ''} {m.get('body') or ''}"):
                return "READY reported"
        return None


def human(title, d):
    import fleet
    fleet.inbox_add("approval", title, key=f"auto-wake:{d}")


class Failed(Exception):
    pass


def last_output(d):
    """When the dispatch's terminal last wrote, or None when Orca cannot say."""
    import orca_settings
    try:
        return orca_settings.with_full(orca, "orchestration", "worker-show", "--dispatch", d)["terminal"]["lastOutputAt"]
    except (SystemExit, Exception):
        return None


def attempt(d, handle, message):
    """Content, then the signal; the route's name, or raises Failed. What waking.md asks of a session, except that
    this process has no SendMessage, which its step 3 sends to wake-reset and the typed signal."""
    before = last_output(d)
    orca("orchestration", "send", "--to", f"dispatch:{d}", "--type", "status", "--subject", "continue", "--body", message)
    code, out = sync("wake-target", handle)
    said = dict(l.split(": ", 1) for l in out.splitlines() if ": " in l)
    route = said.get("route")
    if code != 0 or route not in ("terminal", "sendmessage"):
        raise Failed(f"wake-target route {route}: {said.get('reason') or out[:200]}")
    if route == "sendmessage":
        sync("wake-reset", handle)
    # The mail often starts the turn itself. If that turn is already over, the prompt reads ready and a typed signal
    # would wake its second stop past every check; a wrong "mail" here only sends the stop to the human.
    after = last_output(d)
    if None not in (before, after) and after != before:
        return "mail"
    code, out = sync("nudge", handle, SIGNAL)
    if code != 0 and out.startswith(BUSY):  # it read idle just before the send, so the mail alone started its turn
        return "mail"
    if code != 0:
        raise Failed(f"the signal was not sent: {out[:200]}")
    return "typed" if route == "terminal" else "typed, sendmessage route"


def wake(p, d, x, mark, n, clock, r, workers):
    ticket = x.get("ticket")
    marked = mark["at"]
    if not p.append_once("auto_wake_claimed", lambda e: e["ev"] in ROWS and e.get("dispatch") == d,
                         dispatch=d, ticket=ticket, marked_at=marked, claimed_at=stamp(clock())):
        r.hold(d, n * 60)  # another `orch wait` took it this moment
        return
    handle = next(prog.worker_terminal(w) for w in workers if w.get("dispatchId") == d)
    message = f"Brief: {p.dir / 'briefs' / f'{ticket}.md'}. Continue with the next step from where you stopped."
    try:
        route = attempt(d, handle, message)
    except (Failed, OSError, subprocess.SubprocessError, SystemExit) as e:
        human(f"자동 깨우기 실패: {ticket} ({d})", d)  # before the row: an inbox that fails leaves second_stop to retry
        p.append("auto_wake_failed", dispatch=d, ticket=ticket, why=str(e)[:300])
        r.lines.append(f"AUTO-WAKE {d} ({ticket}): failed, not retried: {str(e)[:200]}; it is on the human's Needs you")
        r.stop = True
        return
    p.append("auto_wake", dispatch=d, ticket=ticket, reason=f"turn ended {mark.get('idle_min')} min before the mark, task open",
             marked_at=marked, woken_at=stamp(clock()), route=route)
    r.lines.append(f"AUTO-WAKE {d} ({ticket}): woken once ({route}); a second stop goes to the human")
    r.hold(d, n * 60)


def second_stop(p, d, x, done, n, now, r, facts):
    if "auto_wake_failed" in done or "auto_wake_escalated" in done:
        return
    since = done["auto_wake"]["woken_at"] if "auto_wake" in done else done["auto_wake_claimed"]["claimed_at"]
    left = (prog.parse_ts(since) + dt.timedelta(minutes=n) - now).total_seconds()
    if left > 0:  # the collector may not have seen the wake yet
        r.hold(d, left)
        return
    ticket = x.get("ticket")
    if facts.why_not(d, ticket):
        return
    human(f"자동 깨우기 뒤 다시 멈춤: {ticket} ({d})", d)  # the key makes a repeat the same item
    p.append("auto_wake_escalated", dispatch=d, ticket=ticket, reason="stopped again after its one wake")
    r.lines.append(f"AUTO-WAKE {d} ({ticket}): stopped again after its one wake; not woken, it is on the human's Needs you")
    r.stop = True


def sweep(p, n, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    began = time.monotonic()
    clock = lambda: now + dt.timedelta(seconds=time.monotonic() - began)
    r = Round()
    state = prog.dash_state(p.dir)
    if state is None:  # a stale collector says nothing about anyone: the marks wait for a fresh one
        return r
    stalls = {x["dispatch"]: x for x in (state.get("summary") or {}).get("stalls") or [] if x.get("kind") in ("idle", "silent")}
    before = load_marks(p)
    marks = {d: dict(m) for d, m in before.items() if d in stalls and stalls[d]["kind"] == "idle"}
    events = p.events()
    acted = {}
    for e in events:
        if e["ev"] in ROWS and e.get("dispatch"):
            acted.setdefault(e["dispatch"], {})[e["ev"]] = e
    facts = Facts(p, events)
    for d, x in stalls.items():
        if d in acted:
            second_stop(p, d, x, acted[d], n, now, r, facts)
            continue
        if x["kind"] != "idle":
            continue
        mark = marks.get(d)
        if mark is None:
            mark = marks[d] = {"at": stamp(now), "idle_min": x.get("minutes")}
            due = now + dt.timedelta(minutes=n)
            r.lines.append(f"AUTO-WAKE {d} ({x.get('ticket')}): idle; wakes once at {due:%H:%M} UTC unless it may not be woken")
        left = (prog.parse_ts(mark["at"]) + dt.timedelta(minutes=n) - now).total_seconds()
        if left > 0:
            r.hold(d, left)
            continue
        if why := facts.why_not(d, x.get("ticket")):
            if mark.get("skip") != why:
                mark["skip"] = why
                r.lines.append(f"AUTO-WAKE {d} ({x.get('ticket')}): not woken: {why}")
            continue
        save_marks(p, marks)
        wake(p, d, x, mark, n, clock, r, facts.workers)
        facts = Facts(p, p.events())  # a wake takes seconds; what it read may have changed for the next dispatch
    if marks != before:
        save_marks(p, marks)
    return r
