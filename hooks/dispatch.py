#!/usr/bin/env python3
"""One hook process per event: runs the modules ENTRIES lists for this tool and prints their merged output.

Each module exposes run(event) -> dict | None, the JSON object its main() used to print. Modules run in order, every
one to the end (a deny does not stop the rest, as when the hooks ran in parallel), each under its own timeout. A module
that raises, exits, prints, times out or cannot be imported loses only its own output. Before a call,
pressure_gate stays its own hooks.json entry: it can hold a start for its queue and fails closed, so it keeps a
process of its own. artifact_archive stays apart too: it leaves a recovery marker a cancelled run would delete.

`hooks.dispatch: false` in ~/.claude/agent-skills.json runs every module as its own process instead (the old shape)
and merges the same way; a session that loaded this file before a rollback keeps working through it.
"""
import concurrent.futures, contextlib, importlib, io, json, os, pathlib, re, signal, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib import config  # noqa: E402

BASH = "Bash"
TRACKERS = "mcp__.*([Ll]inear|[Jj]ira|[Aa]tlassian).*"
TELEGRAM = "mcp__.*[Tt]elegram.*"
# (module, matcher, timeout seconds): the matchers and timeouts the per-module hooks.json entries had. Order is the
# order reasons and messages are joined in.
ENTRIES = {
    "PreToolUse": [
        ("delegate_gate", None, 5),
        ("guard", "Bash|Monitor|Skill|Edit|Write|MultiEdit|NotebookEdit|AskUserQuestion", 5),
        ("self_land_gate", BASH, 10),
        ("land_route_gate", BASH, 20),
        ("lifecycle", "Bash|Agent|Task", 5),
        ("prose_check", "Bash|mcp__.*([Ll]inear|[Jj]ira|atlassian).*", 5),
        ("tracker_gate", TRACKERS, 5),
        ("stack_gate", BASH, 10),
        ("record_check", BASH, 5),
        ("decision_relay", BASH, 5),
        ("artifact_send_check", "Bash|" + TELEGRAM, 5),
        ("turn_check", TELEGRAM, 5),
    ],
    # After the call pressure_gate only marks the outcome of a retry the queue let through: nothing waits there.
    "PostToolUse": [("pressure_gate", BASH, 5), ("record_check", BASH, 5)],
    "PostToolUseFailure": [("pressure_gate", BASH, 5), ("record_check", BASH, 5)],
    "Stop": [("record_check", None, 5), ("claim_check", None, 5), ("owner_check", None, 5),
             ("artifact_links_stop", None, 5), ("turn_check", None, 30)],
    "SubagentStop": [("record_check", None, 5), ("claim_check", None, 5), ("owner_check", None, 5),
                     ("subagent_log", None, 3), ("turn_check", None, 30)],
}
# The matcher of the one hooks.json entry per event: where every module has a matcher, the event is Bash only.
HOOK_MATCHER = {"PreToolUse": None, "PostToolUse": BASH, "PostToolUseFailure": BASH, "Stop": None, "SubagentStop": None}
MARGIN = 5       # seconds the hooks.json timeout holds beyond the module limits: imports and the merge
CAP = 9500       # Claude Code replaces a hook's string over 10,000 characters with a file path and a preview
RANK = {"deny": 3, "ask": 2, "allow": 1}
SPEC_KEYS = {"hookEventName", "permissionDecision", "permissionDecisionReason", "additionalContext"}


class Skipped(Exception):
    """A module whose checks did not run for this call: the reason is shown to the model."""


class _Timeout(BaseException):  # not an Exception: a module's own `except Exception` must not swallow it
    pass


def matches(matcher, tool):
    """Claude Code's matcher rules: none matches everything, a plain name list is exact, anything else is a regex search."""
    if matcher is None:
        return True
    if re.fullmatch(r"[A-Za-z0-9_|]+", matcher):
        return tool in matcher.split("|")
    return re.search(matcher, tool) is not None


def selected(event, tool, entries=None):
    return [(mod, timeout) for mod, matcher, timeout in (ENTRIES.get(event, []) if entries is None else entries)
            if matches(matcher, tool)]


def budget(event):
    """The hooks.json timeout of this event's dispatch entry."""
    return int(sum(t for _, _, t in ENTRIES[event]) + MARGIN + 0.999)


def note(why):
    return f"agent-skills hooks/dispatch.py: {why}, so its checks did not run for this call."


def call(module, event, timeout):
    """module.run(event) under a timeout, or Skipped. A module that raises or exits yields None, as its hook did."""
    armed = [True]

    def alarm(*_):
        if armed[0]:
            raise _Timeout()

    old = signal.signal(signal.SIGALRM, alarm)
    # File descriptor 1 goes to a scratch file while the module runs: a raw os.write or a child it starts cannot
    # reach the real output. The real one is kept by run_event.
    sink, saved = tempfile.TemporaryFile(), os.dup(1)
    os.dup2(sink.fileno(), 1)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    buf, out, loading = io.StringIO(), None, True
    try:
        with contextlib.redirect_stdout(buf):  # the import is inside the limit, the redirect and the isolation
            run = importlib.import_module(module).run
            loading = False
            out = run(event)
        armed[0] = False
    except _Timeout:
        raise Skipped(note(f"{module} ran past its {timeout:g}s limit"))
    except (Exception, SystemExit) as e:
        if loading:
            config.log_error(module, e if isinstance(e, Exception) else RuntimeError("exited while loading"))
            raise Skipped(note(f"{module} could not be loaded ({type(e).__name__})"))
        if isinstance(e, Exception):
            config.log_error(module, e)
        out = None
    finally:
        armed[0] = False
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        try:  # text still in the process's own stdout buffer must land in the sink, not after the restore
            sys.__stdout__.flush()
        except Exception:
            pass
        os.dup2(saved, 1)
        os.close(saved)
        raw = sink.seek(0, os.SEEK_END)
        sink.close()
    if buf.getvalue() or raw:  # a module that wrote to stdout is not trusted for its return value either
        config.log_error(module, RuntimeError("wrote to stdout"))
        return None
    if out is not None and not isinstance(out, dict):
        config.log_error(module, TypeError("run() did not return a dict"))
        return None
    return out or None


def add(items, text):
    if isinstance(text, str) and text and text not in items:
        items.append(text)


def fit(parts):
    """The parts joined by newlines; whole trailing parts go (and are counted) when the text passes CAP."""
    if len("\n".join(parts)) <= CAP:
        return "\n".join(parts)
    config.log_error("dispatch", RuntimeError("merged text passed the cap"))
    kept = list(parts)
    while len(kept) > 1 and len("\n".join(kept)) + 40 > CAP:
        kept.pop()
    if len(kept[0]) + 60 > CAP:  # one entry alone is over: cut it
        cut = len(kept[0]) - (CAP - 60)
        kept[0] = kept[0][:CAP - 60] + f"... ({cut} characters cut)"
    if len(kept) < len(parts):
        kept.append(f"({len(parts) - len(kept)} more omitted)")
    return "\n".join(kept)


def take(module, out, part):
    """One module's output sorted into `part`; raises on a value of the wrong type."""
    for key, value in out.items():
        if key == "systemMessage":
            add(part["systems"], value)
        elif key == "decision" and value == "block":
            add(part["blocks"], out.get("reason") or "")
        elif key == "reason" and out.get("decision") == "block":
            pass
        elif key == "hookSpecificOutput" and isinstance(value, dict):
            for k, v in value.items():
                if k == "permissionDecision" and v in RANK:
                    text = value.get("permissionDecisionReason")
                    part["reasons"][v].append(text if isinstance(text, str) and text else None)
                elif k == "additionalContext":
                    add(part["contexts"], v)
                elif k not in SPEC_KEYS:
                    config.log_error(module, KeyError(k))
        else:
            config.log_error(module, KeyError(key))


def merge(name, results):
    """results: [(module, dict)] in module order. Pre: deny > ask > allow with the reasons of the deny and ask modules
    joined; Stop: a block from any module blocks with every reason; systemMessage and additionalContext joined."""
    systems, contexts, blocks = [], [], []
    reasons = {"deny": [], "ask": [], "allow": []}
    for module, out in results:
        part = {"systems": [], "contexts": [], "blocks": [], "reasons": {"deny": [], "ask": [], "allow": []}}
        try:  # a module whose output is malformed loses only its own part of the merge
            take(module, out or {}, part)
        except Exception as e:
            config.log_error(module, e)
            continue
        systems += [s for s in part["systems"] if s not in systems]
        contexts += [c for c in part["contexts"] if c not in contexts]
        blocks += [b for b in part["blocks"] if b not in blocks]
        for level, items in part["reasons"].items():
            reasons[level] += [r for r in items if r is None or r not in reasons[level]]
    spec = {}
    decided = [d for d in RANK if reasons[d]]
    if decided:
        win = max(decided, key=RANK.get)
        text = [r for d in ("deny", "ask") if RANK[d] <= RANK[win] for r in reasons[d] if r] if win != "allow" else \
            [r for r in reasons["allow"] if r]
        spec["permissionDecision"] = win
        if text:
            spec["permissionDecisionReason"] = "\n".join(text)
    if contexts:
        spec["additionalContext"] = fit(contexts)
    out = {}
    if spec:
        out["hookSpecificOutput"] = {"hookEventName": name, **spec}
    if systems:
        out["systemMessage"] = fit(systems)
    if blocks:
        out["decision"], out["reason"] = "block", "\n".join(blocks)
    return out or None


def legacy(event, chosen):
    """Every module as its own process, started together: the shape before this file, for `hooks.dispatch: false`."""
    def collect(module, timeout, p):
        try:
            stdout, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            try:  # a descendant that kept the pipe open must not hold this wait, or the other children's results
                p.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                p.stdout.close()
            return Skipped(note(f"{module} ran past its {timeout:g}s limit"))
        try:  # bytes, decoded here: one child's bad bytes cost only that child
            out = json.loads(stdout.decode("utf-8")) if stdout.strip() else None
        except ValueError as e:
            config.log_error(module, e)
            return None
        if out is not None and not isinstance(out, dict):
            config.log_error(module, TypeError("output is not an object"))
            return None
        return out

    # The input is a file, not a pipe: no child waits on another to be fed, and a child that reads late blocks no writer.
    with tempfile.NamedTemporaryFile("w+") as data, contextlib.ExitStack() as opened, \
            concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(chosen))) as pool:
        data.write(json.dumps(event))
        data.flush()
        waits = []
        for module, timeout in chosen:  # one open file each: the children must not share a read offset
            own = opened.enter_context(open(data.name))
            p = subprocess.Popen([sys.executable, str(HERE / f"{module}.py")], stdin=own, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
            # Each child is watched from its own start, so a late one is stopped on time whoever is waited on first.
            waits.append((module, pool.submit(collect, module, timeout, p)))
        for module, future in waits:
            yield module, future.result()


def dispatch(event, entries=None, results=None):
    """The merged output for this event, or None. `results` collects each module's output as it finishes."""
    name = event.get("hook_event_name")
    results = [] if results is None else results
    chosen = selected(name, str(event.get("tool_name") or ""), entries)
    if entries is None and config.get(config.load(), "hooks.dispatch") is False:
        pairs = legacy(event, chosen)
    else:
        pairs = ((m, _guarded(m, event, t)) for m, t in chosen)
    for module, out in pairs:
        results.append(("dispatch", {"systemMessage": str(out)}) if isinstance(out, Skipped) else (module, out))
    return merge(name, results)


def _guarded(module, event, timeout):
    try:
        return call(module, event, timeout)
    except Skipped as s:
        return s


def run_event(event, entries=None, grace=3):
    """Print dispatch(event). A watchdog prints what finished and ends the process if a module hangs past every limit
    (a stuck system call ignores the alarm), so a deny that was already decided still reaches Claude Code."""
    name = event.get("hook_event_name")
    chosen = ENTRIES.get(name, []) if entries is None else entries
    if not chosen:
        return
    # A module runs with descriptor 1 pointed elsewhere, so the output goes through a copy of the real one.
    results, lock, printed, real = [], threading.Lock(), [], os.dup(1)
    # Descriptor 1 stays on /dev/null for the rest of the process: a buffer a module kept for itself, or one of C stdio,
    # flushes at exit into nothing instead of after the answer.
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)

    def emit(final):
        with lock:
            if printed:
                return
            printed.append(1)
            out = merge(name, list(results))
            if out:
                with open(real, "w", encoding="utf-8", closefd=False) as stream:
                    stream.write(json.dumps(out, ensure_ascii=False) + "\n")
            if final:
                os._exit(0)

    timer = threading.Timer(sum(t for _, _, t in chosen) + grace, emit, args=(True,))
    timer.daemon = True
    timer.start()
    dispatch(event, entries, results)
    timer.cancel()
    emit(False)


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if isinstance(event, dict):
        try:
            run_event(event)
        except Exception as e:
            config.log_error("dispatch", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
