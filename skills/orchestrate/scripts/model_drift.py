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

It only shows. Typing `/model` into a session is not an option: it writes the user's default model for every new
session. The finding carries the session-only fix, `claude --resume <id> --model <want>`, for whoever owns the session.
"""
import json, os, pathlib, re, shlex, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parents[2] / "hooks"))
import fleet, roles  # noqa: E402

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
