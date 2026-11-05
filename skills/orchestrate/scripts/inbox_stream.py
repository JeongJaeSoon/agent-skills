#!/usr/bin/env python3
"""`orch inbox --stream`: the top-level session's inbox wait and card watch as one Monitor event stream.

Usage (the Monitor tool, never Bash): orch inbox --stream [--debounce 300] [--ttl-ms 1740000] [--no-cards] [--key KEY]

The background `orch inbox --wait` cost three model calls per wake: its completion notice carries no output, so the
session read the output file, then acked and started the wait again, then answered. Under Monitor every stdout line
reaches the session as the notice itself, and the process keeps running, so a wake costs the call that acts on it.

Each round takes the inbox batch. A consuming check marks the batch handled when it returns it (orca orchestration
check --help); `orch inbox` takes it the same way, so the stream holds mail no shorter than the wait did, and what a
crash loses after the print is still in `orca orchestration inbox`, which reboot --top lists since the last handoff.
Heartbeats and Orca's refusals of a released worker's repeat are dropped; the rest is printed as `INBOX` lines in one
write, which Monitor delivers as one notice. A batch with nothing urgent (a question, an escalation, a worker_done, a
subject saying READY, BLOCKED or STUCK) waits up to --debounce seconds for more mail first: most mail is status, and a
third of the wakes came within two minutes of the one before. That wait is a `check --peek --wait`
with no --types, as is every wait between batches: it keeps Orca from typing its "You have N orchestration
message(s)" notice into the composer.

Beside the inbox:
- `orch watch-cards --self <handle>` runs as a child; a round's findings are printed with a `CARDS:` line and the
  watch starts again by itself, so no "restart the card watcher" turn is left.
- `orch wait`'s WAKE lines and watch_lines (WATCH-CARDS DOWN, PATROL) are printed, each line at most every 30 minutes.
- With `orchestrate.auto_clear: true` only, `RESTART DUE` when this session's context passed `orchestrate.restart_at`
  in ~/.claude/agent-skills.json (default 130000, 0 turns it off): with a batch, or alone after five quiet minutes;
  again for each 25k more. Without it nothing here wakes the model for a restart: the clear band (hooks/clear_band.tsx)
  tells the human.
- A restart the session asked for (`orch handoff save --restart`) is typed here: `/clear` once the screen is idle by
  the reload broadcast's checks (scripts/card.py type_when_idle), never twice. Expired after 30 minutes unsent.
- `SESSION CLEARED` when this terminal's session id changes under the stream (that /clear, or one the human typed):
  the notice starts the new session's first turn, which runs agent-skills:reboot. One path for both.
It ends by itself before Claude Code's 30-minute Monitor limit, between rounds, with a `STREAM END` line.
"""
import contextlib, datetime as dt, fcntl, json, os, pathlib, re, signal, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))
import prog  # noqa: E402
import roles  # noqa: E402

TTL_MS = 1_740_000  # Claude Code kills a Monitor at 30 minutes; end a minute before, never inside a round
TICK_S = 60
URGENT = {"question", "escalation", "worker_done"}
URGENT_SUBJECT = re.compile(r"\b(READY|BLOCKED|STUCK)\b")


def urgent(msgs):
    """Mail that goes out at once, without the debounce: someone waits on the answer or the next step."""
    return any(m.get("type") in URGENT or URGENT_SUBJECT.search(str(m.get("subject") or "")) for m in msgs)
QUIET_S = 300
RESTART_AT = 130_000
RESTART_STEP = 25_000
REQUEST_TTL_S = 1800
REPEAT_S = 1800
ERRORS_MAX = 5
TAIL_BYTES = 400_000


def restart_at():
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        v = (json.loads(path.read_text()).get("orchestrate") or {}).get("restart_at", RESTART_AT)
    except (OSError, ValueError, AttributeError):
        return RESTART_AT
    return v if isinstance(v, int) and not isinstance(v, bool) else RESTART_AT


def transcript(session):
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()
    return next(iter(root.glob(f"*/{session}.jsonl")), None) if roles.SESSION.fullmatch(session or "") else None


def context_of(path):
    """Tokens the newest model call of the transcript read (input + cache read + cache write), or None."""
    try:
        with open(path, "rb") as f:
            f.seek(max(f.seek(0, 2) - TAIL_BYTES, 0))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            u = (json.loads(line).get("message") or {}).get("usage") or {}
        except ValueError:
            continue
        if u:
            return sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return None


def session_of(me):
    """The session the reorient hook last recorded for this terminal; a /clear records the new id."""
    try:
        return (roles.read_terminal(me) or {}).get("session_id") if me else None
    except (OSError, ValueError):
        return None


def context(session):
    path = transcript(session)
    return context_of(path) if path else None


def type_clear(me):
    """None once /clear was typed and taken, else why not (sync.UNCONFIRMED when it may have been typed)."""
    import card
    return card.type_when_idle(me, "/clear")


def ts_age(ts, now):
    try:
        return now - dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


class Stream:
    def __init__(self, me, key="orchestrator", debounce=300, ttl_ms=TTL_MS, cards=True, threshold=None, out=None,
                 clock=time.monotonic, now=time.time, session=session_of, ctx=context, spawn=None, clear=type_clear,
                 store=None):
        self.me, self.key, self.debounce, self.ttl, self.cards = me, key, debounce, ttl_ms / 1000, cards
        if threshold is None:
            import handoff
            threshold = restart_at() if handoff.auto_clear_on() else 0
        self.threshold = threshold
        self.out, self.clock, self.now = out or sys.stdout, clock, now
        self.session_of, self.ctx, self.clear = session, ctx, clear
        self.spawn = spawn or self._spawn
        if store is None:
            import store
        self.store = store
        self.child, self.child_fails, self.shown, self.announced = None, 0, {}, {}
        self.last_mail, self.session = clock(), session(me)

    def emit(self, lines):
        if lines:
            self.out.write("\n".join(lines) + "\n")
            self.out.flush()

    def take(self):
        did, msgs = prog.batch(prog.check())
        work = [m for m in msgs if m.get("type") != "heartbeat" and not prog.revoked_echo(m)]
        return did, work, [prog.revoked_echo(m) for m in msgs if prog.revoked_echo(m)]

    def wait(self, seconds):
        prog.check("--peek", "--wait", "--timeout-ms", str(max(int(seconds * 1000), 1000)))

    def mail(self, deadline):
        did, work, revoked = self.take()
        dids = [did] if did else []
        if work and not urgent(work):
            until = min(self.clock() + self.debounce, deadline)
            while self.clock() < until:
                self.wait(until - self.clock())
                d2, w2, r2 = self.take()
                dids += [d2] if d2 else []
                work, revoked = work + w2, revoked + r2
                if urgent(w2):
                    break
        lines = [f"ignored: Orca refused a repeat from released dispatch {d}" for d in revoked]
        if work:
            lines += list(map(one_line, work))
            lines.append(f"{len(work)} message(s) to act on, already taken from the inbox: handle each now. The stream "
                         "keeps running; start no other wait.")
            self.last_mail = self.clock()
            lines += self.due(force=True)
        self.emit(lines)
        for d in dids:  # an older Orca marks a batch read only on --ack; a newer one treats it as a no-op
            with contextlib.suppress(SystemExit):
                prog.check("--ack", d)
        return bool(work)

    def _spawn(self, left_ms):
        return subprocess.Popen([sys.executable, str(HERE / "prog.py"), "watch-cards", "--self", self.me or "",
                                 "--timeout-ms", str(left_ms)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, text=True)

    def watch_cards(self, deadline):
        if not self.cards or not self.me:
            return
        left_ms = int((deadline - self.clock()) * 1000)
        if self.child is None:
            if self.child_fails < 3 and left_ms > TICK_S * 1000:
                self.child = self.spawn(left_ms)
            return
        if self.child.poll() is None:
            return
        out, err = self.child.communicate()
        code, self.child = self.child.returncode, None
        lines = [x for x in (out or "").splitlines() if x.strip()]
        if code:
            self.child_fails += 1
            tail = (err or "").strip().splitlines()[-1:] or ["no output"]
            self.emit([f"CARDS: orch watch-cards exited {code} ({tail[0][:200]})"
                       + ("; started again" if self.child_fails < 3 else "; not started again until the next stream")])
        elif lines and not lines[0].startswith("QUIET") or any(x.startswith("PARTIAL") for x in lines[1:]):
            self.child_fails = 0
            self.emit(lines + ["CARDS: act on the card lines above; the card watch started again by itself"])
        self.watch_cards(deadline)

    def due(self, force=False):
        """RESTART DUE once the context passed the threshold, again for each RESTART_STEP more; with a batch (force),
        or alone once the inbox was quiet QUIET_S."""
        if not self.threshold or not (force or self.clock() - self.last_mail >= QUIET_S):
            return []
        ctx = self.ctx(self.session) if self.session else None
        if not ctx or ctx <= self.threshold or ctx < self.announced.get(self.session, 0) + RESTART_STEP:
            return []
        self.announced[self.session] = ctx
        return [f"RESTART DUE: context {ctx // 1000}k > {self.threshold // 1000}k. Once nothing waits on you in this "
                "turn (a question, a subagent, a reply), end it with `orch handoff save --next \"<what to do first>\" "
                "--restart` (orchestrate references/top-level.md, \"Restart\")"]

    def restart(self):
        last = self.store.last_session_event(self.key, "restart")
        if not last or not isinstance(last[1], dict):
            return []
        ts, req = last
        if req.get("stage") != "requested" or req.get("handle") != self.me or req.get("session") != self.session:
            return []
        if (age := ts_age(ts, self.now())) is not None and age > REQUEST_TTL_S:
            self.store.session_event(self.key, "restart", {**req, "stage": "expired"}, by="orch inbox --stream")
            return [f"RESTART EXPIRED: the screen was not idle for {REQUEST_TTL_S // 60} min; ask again with "
                    "`orch handoff save --restart` when the turn is quiet"]
        why = self.clear(self.me)
        if why is None or "taken" in why:
            self.store.session_event(self.key, "restart", {**req, "stage": "sent", "confirmed": why is None},
                                     by="orch inbox --stream")
        return []

    def cleared(self):
        now = self.session_of(self.me)
        if not now or now == self.session:
            return []
        before, self.session = self.session, now
        if before is None:
            return []
        return [f"SESSION CLEARED: this terminal's session is now {now[:8]} (was {before[:8]}). Run "
                "agent-skills:reboot --top first; the handoff the reorient hook gave you says what comes next, and "
                "this stream and its card watch keep running."]

    def side(self):
        now, lines = self.now(), []
        for line in prog.wake_lines(self.me) + prog.watch_lines(self.me, bool(self.me and _scanned(self.me))):
            if line not in self.shown or now - self.shown[line] >= REPEAT_S:
                self.shown[line] = now
                lines.append(line)
        try:
            lines += self.restart()
        except Exception as e:  # noqa: BLE001  a broken DB must not end the inbox stream
            lines.append(f"RESTART: could not read or record the request ({type(e).__name__}: {e})")
        self.emit(lines + self.cleared() + self.due())

    def run(self):
        start = self.clock()
        deadline, errors = start + self.ttl, 0
        while self.clock() < deadline:
            try:
                got = self.mail(deadline)
                self.watch_cards(deadline)
                self.side()
                if not got and self.clock() < deadline:
                    self.wait(min(deadline - self.clock(), TICK_S))
                errors = 0
            except SystemExit as e:
                errors += 1
                self.emit([f"STREAM ERROR {errors}/{ERRORS_MAX}: {str(e)[:300]}"])
                if errors >= ERRORS_MAX:
                    self.stop()
                    self.emit(["STREAM END: Orca kept failing; check `orca status`, then start the stream again"])
                    return 1
                time.sleep(min(30, max(deadline - self.clock(), 0)))
        self.stop()
        self.emit([f"STREAM END after {int((self.clock() - start) // 60)} min: start it again now with the Monitor "
                   "tool, command `orch inbox --stream`, timeout_ms 1800000"])
        return 0

    def stop(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.child.wait(10)
        self.child = None


def one_line(m):
    """inbox_line with the body's newlines as ' ⏎ ': a message stays whole and on one line, so a long one can never be
    split across two notices."""
    first, *rest = prog.inbox_line(m).split("\n")
    return " ⏎ ".join([first] + [line[4:] for line in rest])


def lock_path(me):
    return roles.state() / "inbox-stream" / f"{me or 'none'}.lock"


@contextlib.contextmanager
def only_stream(me):
    """One stream per terminal: a second one would take mail the first never shows. Yields False when one runs."""
    path = lock_path(me)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def running(me):
    """Whether a stream holds this terminal's lock (`orch handoff save --restart` needs one to type its /clear)."""
    try:
        with open(lock_path(me)) as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def _scanned(me):
    import card_watch
    return card_watch.last_scan(me)


def main(argv):
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    if not me or not roles.SESSION.fullmatch(me):
        sys.exit("orch inbox --stream reads this terminal's inbox: run it in an Orca terminal (ORCA_TERMINAL_HANDLE)")
    stream = Stream(me, key=prog.opt(argv, "--key", "orchestrator"), debounce=int(prog.opt(argv, "--debounce", "300")),
                    ttl_ms=int(prog.opt(argv, "--ttl-ms", str(TTL_MS))), cards="--no-cards" not in argv)

    def on_term(*_):
        stream.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
    with only_stream(me) as got:
        if not got:
            print("STREAM END: another `orch inbox --stream` already runs for this terminal; keep that one", flush=True)
            return 0
        try:
            return stream.run()
        finally:
            stream.stop()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
