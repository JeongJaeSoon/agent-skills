"""Model drift: a live Claude session whose model differs from its role's `orchestrate.models.<role>`.

Patrol runs this once per round, with the `claude agents --json` rows it already read. Per Claude terminal:

  role    hooks/roles.py `session()` (`agent_type` from the transcript's agent-setting row, the ROLE record, the
          Dispatch preamble, a card's CLOSE or KEEP row); an orchestrator or a lead reads `orchestrate.models.lead`, a
          worker `.worker`; `general` is never judged
  actual  the model of the transcript's last assistant row (it names the last turn's model, so a session that was
          just restarted on another model shows the old one until it answers once)
  want    orca_settings.role_model(); no key means no judgement for that role, with one note

`sonnet`, `opus` and `haiku` (with a `[1m]` suffix) name a model family and are compared with the actual id; a full
`claude-…` id must equal it. Any other value (`opusplan`, `default`) cannot be resolved here and is not judged.

By default it only shows. Typing `/model` into a session is not an option: it writes the user's default model for
every new session. The finding carries the session-only fix, `claude --resume <id> --model <want>`, for whoever owns
the session.

`orchestrate.model_autoswitch: true` lets patrol run that fix itself, once per session, for an idle worker: `/exit`,
wait for the shell prompt, then the resume line in the same terminal. Measured on a live helper: the terminal handle,
the Run binding and the conversation survive; the next turn runs on the new model. What a restart ends (background
shells, subagents, `orch wait`) is why a session showing any of them is skipped. See `autoswitch`.
"""
import json, os, pathlib, re, shlex, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parents[2] / "hooks"))
import agent_signal, fleet, roles  # noqa: E402

KEY = {"orchestrator": "lead", "lead": "lead", "worker": "worker"}
TAIL_STEPS = (256 * 1024, 2**20, 4 * 2**20, 16 * 2**20)  # a last assistant row can sit under a huge tool result
FAMILIES = ("sonnet", "opus", "haiku")


def transcript_of(session):
    """The session's transcript under any Claude config home (fleet.claude_homes: this process's, ~/.claude and each
    Orca profile's), or None."""
    for home in fleet.claude_homes():
        for hit in home.glob(f"projects/*/{session}.jsonl"):
            return hit
    return None


def last_model(path):
    """(model, complete): the model of the transcript's last assistant row that names one, and whether the search
    was whole. (None, True) is a session with no turn yet; (None, False) is a file whose last 16 MiB hold no such
    row; (None, None) is a file that could not be read. A row Claude Code wrote itself (`<synthetic>`) names none."""
    try:
        size = os.stat(path).st_size
        with open(path, "rb") as f:
            for step in TAIL_STEPS:
                f.seek(max(0, size - step))
                raw = f.read().splitlines()
                for line in reversed(raw[1:] if size > step else raw):  # the first line may be cut
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    msg = row.get("message") if isinstance(row, dict) and row.get("type") == "assistant" else None
                    model = msg.get("model") if isinstance(msg, dict) else None
                    if isinstance(model, str) and model and not model.startswith("<"):
                        return model, True
                if size <= step:
                    return None, True
    except OSError:
        return None, None
    return None, False


TAG = {"opus": "O", "sonnet": "S", "haiku": "H"}
SEEN = {}  # newest transcript -> ((mtime, size), model): the collector reads every card each tick


def dir_model(folders):
    """The last assistant model of the newest transcript in a card's transcript folders (one per Claude config home),
    else None. A file that could not be read is not remembered, so the next tick tries again; one that was read and
    holds no model in its last 16 MiB is, so an unchanged file costs one scan, not one a tick."""
    files = []
    for folder in folders:
        try:
            files += [f for f in pathlib.Path(folder).iterdir() if f.suffix == ".jsonl"]
        except OSError:
            pass
    try:
        newest = max(files, key=lambda f: f.stat().st_mtime)
        stamp = (newest.stat().st_mtime_ns, newest.stat().st_size)
    except (OSError, ValueError):
        return None
    if SEEN.get(newest, (None,))[0] != stamp:
        model, complete = last_model(newest)
        if complete is None:
            return None
        SEEN[newest] = (stamp, model)
    return SEEN[newest][1]


def model_tag(model, want):
    """The one-cell mark of a session's model: O, S or H (`?` for another), then `!` when `want` (the role's
    configured model) says another. "" when the model is unknown."""
    if not model:
        return ""
    family = next((TAG[f] for f in TAG if f in model.lower()), "?")
    return family + ("!" if want and matches(model, want) is False else "")


def agent_type(path):
    """The `--agent` the session runs under (the last of Claude Code's agent-setting rows: a resume with another
    `--agent` writes a new one mid-file), else None. Only lines that name the row type are parsed."""
    found = None
    try:
        with open(path, "rb") as f:
            for line in f:
                if b'"agent-setting"' in line:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("type") == "agent-setting":
                        found = row.get("agentSetting")
    except OSError:
        pass
    return found


def matches(have, want):
    """True when the model id `have` is the configured `want`, False when it is not, None when `want` cannot be
    resolved here."""
    base, have = want.split("[")[0].lower(), have.lower()
    if base in FAMILIES:
        return base in have
    if base.startswith("claude-"):
        return have == base or re.fullmatch(re.escape(base) + r"-\d{8}", have) is not None  # or its dated form
    return None


def inspect(handle, rows, models, transcript=transcript_of):
    """(drift, note, blind): drift is {role, want, have, session} for a session off its role's model. note is a line
    worth showing; blind says a bound session could not be judged this round, so a finding must not be cleared."""
    row, _ = roles.session_for(handle, rows)
    if row is None:
        return None, None, False
    path = transcript(row["sessionId"])
    try:
        open(path, "rb").close()  # an unreadable file would also read as a session no role claims
    except (OSError, TypeError):
        return None, None, True
    # `session_title` is the name the session carries, which roles.py reads as `orchestrator` or `lead:<slug>`.
    event = {"session_id": row["sessionId"], "transcript_path": str(path), "hook_event_name": "SessionStart",
             "session_title": row.get("name"), "agent_type": agent_type(path)}
    role = roles.session(event).role
    key = KEY.get(role)
    if key is None:
        return None, None, False  # a session a human opened is theirs
    want = models.get(key)
    if want is None:
        return None, f"orchestrate.models.{key} is not set: no model drift judged for that role", False
    have, complete = last_model(path)
    if have is None:
        return None, None, not complete  # complete: no turn yet, so nothing to be off
    same = matches(have, want)
    if same is None:
        return None, f"orchestrate.models.{key} is {want!r}: not a model family or id, so not compared", False
    if same:
        return None, None, False
    return {"role": role, "want": want, "have": have, "session": row["sessionId"]}, None, False


def run(terminals, rows, problem, models, me=None, transcript=transcript_of):
    """(drifts, notes, blind): each drift as {handle, role, want, have, session, line}, note lines, and whether any
    session could not be judged (the round then leaves earlier findings standing). `me` is the patrol's own
    terminal handle."""
    if problem:
        return [], [f"model drift not judged: claude agents {problem}"], True
    if not rows:  # an empty listing is an answer: no session is left to be off
        return [], [], False
    drifts, notes, blind = [], [], False
    for t in terminals:
        handle = t.get("handle")
        if not handle or handle == me:
            continue
        d, note, cut = inspect(handle, rows, models, transcript)
        blind = blind or cut
        if note and note not in notes:
            notes.append(note)
        if d:
            fix = f"claude --resume {d['session']} --model {shlex.quote(d['want'])}"
            drifts.append({**d, "handle": handle, "line": f"{d['role']} runs on {d['have']}, orchestrate.models says "
                                                          f"{d['want']}: its owner restarts it with `{fix}`"})
    return drifts, notes, blind


SWITCH_KEY = "model_autoswitch"
EXIT_WAIT_S, RESUME_WAIT_S, POLL_S = 15.0, 30.0, 0.5


def autoswitch_on():
    """True only for `orchestrate.model_autoswitch: true`. No key, an unreadable file or any other value is off."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        return (json.loads(path.read_text()).get("orchestrate") or {}).get(SWITCH_KEY) is True
    except (OSError, ValueError, AttributeError):
        return False


def _shell_prompt(session, lines):
    """The shell Claude exited to: this session's resume hint, then one unindented line ending in a prompt character
    and nothing after it. A live Claude screen draws indented status lines below its prompt box, so it never fits."""
    shown = [l for l in lines if l.strip()]
    at = max((i for i, l in enumerate(shown) if l.strip() == f"claude --resume {session}"), default=None)
    return at is not None and at == len(shown) - 2 and not shown[-1].startswith(" ") and shown[-1].rstrip()[-1] in "%$#"


def _claude_back(sync, lines):
    """A Claude prompt box is drawn and its indented status line is last, so the shell prompt is gone."""
    shown = [l for l in lines if l.strip()]
    return bool(shown) and sync.prompt_box(lines) is not None and shown[-1].startswith(" ")


def _wait(sync, handle, ok, limit_s, sleep, clock):
    """Poll the rendered screen until `ok(lines)`, for limit_s of the clock (a read itself may take a while, so the
    limit is a deadline and not a count). Reads at least once."""
    end = clock() + limit_s
    while True:
        if ok(sync.norm(sync.orca("terminal", "read", "--terminal", handle, "--screen")["terminal"].get("tail") or [])):
            return True
        if clock() >= end:
            return False
        sleep(POLL_S)


def _put(path, record, exclusive=False):
    """Write a claim whole or not at all: a temp file, then a link (fails if the claim exists) or a replace."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record))
    try:
        if exclusive:
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def autoswitch(d, rows, claims, sync=None, sleep=time.sleep, read=agent_signal.read, clock=time.monotonic):
    """Restart one drifted session on its role's model: ("switched", ""), ("failed", why) or ("skip", why).

    Only an idle worker; a lead or an orchestrator is shown, never restarted. A skip types nothing and keeps the
    session eligible. Before any key: the screen is an empty Claude prompt (sync.screen, which refuses a dialog, a
    running turn and a draft) with no background work (sync.background_running, which also refuses a screen it cannot
    read), and, under the sync lock, a fresh `claude agents` listing still binds this terminal to the same idle
    worker session, off its model (`rows` is minutes old). The claim file is created right after that and never
    removed: from the first keystroke on, a session is tried once whatever happens, and a failure goes to the human
    (`unreported`), not to a retry. The resume line is typed only once this session's shell prompt is on screen."""
    if d["role"] != "worker":
        return "skip", "only a worker is switched"
    claims = pathlib.Path(claims)
    claim, handle = claims / f"{d['session']}.json", d["handle"]
    if claim.exists():
        return "skip", "already tried once"
    sync = sync or fleet.load_sync()
    if sync is None:
        return "skip", "scripts/sync.py not found"
    try:
        tail, reason = sync.screen(handle)  # nothing is sent yet
    except sync.Stop as e:
        return "skip", str(e)[:200]
    reason = reason or sync.background_running(tail)
    if reason:
        return "skip", reason
    resume = f"claude --resume {shlex.quote(d['session'])} --model {shlex.quote(d['want'])}"
    def bound():
        """A reason the terminal is not, right now, an idle worker session off its model: the one asked about."""
        fresh, problem, _ = read()
        if problem:
            return f"claude agents failed: {problem}"
        row, _ = roles.session_for(handle, fresh)
        again = inspect(handle, fresh, {"worker": d["want"]})[0]
        if again is None or again["session"] != d["session"] or again["role"] != "worker" \
                or row is None or agent_signal.state_of(row) != "idle":
            return "the terminal no longer holds an idle worker session off its model"
        return None

    with sync.locked(10) as got:
        if not got:
            return "skip", "a sync or broadcast is running"
        if reason := bound():
            return "skip", reason
        claims.mkdir(parents=True, exist_ok=True)
        record = {"session": d["session"], "handle": handle, "have": d["have"], "want": d["want"], "at": time.time()}
        try:
            _put(claim, record, exclusive=True)
        except FileExistsError:
            return "skip", "already tried once"

        def finish(status, why=""):
            _put(claim, {**record, "result": status, "why": why})
            return status, why

        try:
            # The last check before the key reads the listing again: a session may have replaced the one judged above.
            reason = sync.send_to(handle, "/exit", lambda before, after, text: True,
                                  lambda lines: sync.background_running(lines) or bound())
            if reason and reason != sync.UNCONFIRMED:  # refused with the screen changed or busy: no key was typed
                claim.unlink()
                return "skip", reason
            if not _wait(sync, handle, lambda lines: _shell_prompt(d["session"], lines), EXIT_WAIT_S, sleep, clock):
                return finish("failed", "no shell prompt after /exit; the resume line was not typed")
            sync.orca("terminal", "send", "--terminal", handle, "--text", resume, "--enter")
            if not _wait(sync, handle, lambda lines: _claude_back(sync, lines), RESUME_WAIT_S, sleep, clock):
                return finish("failed", f"typed `{resume}`, but no Claude prompt came back")
        except sync.Stop as e:  # a send or a read failed: a key may have been typed, so the claim stays
            return finish("failed", str(e)[:200])
        except BaseException as e:  # patrol's budget alarm: keep the claim, say why, let it through
            finish("failed", f"interrupted by {type(e).__name__}")
            raise
    return finish("switched")


def unreported(claims, now=None, stale_s=600):
    """Claims whose attempt failed, or began and left no result for stale_s (a crash), or cannot be read, and that the
    human was not told of yet, as dicts with `session`, `handle` and `why`. The caller tells the human, then calls
    `mark_reported`, so a failed notice is offered again next round."""
    now, out = now or time.time(), []
    for path in sorted(pathlib.Path(claims).glob("*.json")):
        try:
            c = json.loads(path.read_text())
            if not isinstance(c, dict):
                raise ValueError("not an object")
            lost = not c.get("result") and now - c.get("at", now) > stale_s
            if c.get("reported") or not (c.get("result") == "failed" or lost):
                continue
            c = {**c, "why": c.get("why") or "no result was recorded (patrol was interrupted)"}
        except OSError:
            continue
        except ValueError:  # a claim cut short: it still means a session was tried or about to be
            if now - path.stat().st_mtime <= stale_s:
                continue
            c = {"session": path.stem, "handle": "", "why": "the claim file is unreadable"}
        out.append(c)
    return out


def mark_reported(claims, c):
    path = pathlib.Path(claims) / f"{c['session']}.json"
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = {k: c[k] for k in ("session", "handle") if k in c}
    _put(path, {**(old if isinstance(old, dict) else {}), "reported": True, "why": c["why"]})
