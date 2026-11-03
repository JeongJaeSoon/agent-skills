"""What both card cleaners read of a card, and the guard and lock every close goes through.

The janitor (cards.py) and reap-hard (scripts/reap_hard.py) keep their own rules for which card is done. They share
the facts those rules read (busy shells, child cards, the worker list, the transcript's motion) and, right before
closing anything, guard() and card_lock() here:

  with card_lock(path) as got:          # one cleaner per card at a time, across processes
      if not got: skip
      judge again with the cleaner's own rule
      guard(path, head, handles)         # patrol, HEAD, new terminals, a human's prompt, a parked session
      close each handle seen at the judgement (never --all)
      guard(path, head, handles, final=True)
      orca worktree rm

guard() keeps the card whenever it cannot see what it checks.
"""
import contextlib, fcntl, hashlib, json, os, pathlib, re, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reap  # noqa: E402

ORCH_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "orchestrate" / "scripts"
REPO = pathlib.Path(__file__).resolve().parents[3]
# A working Claude screen redraws its spinner every second; an idle one still writes about every 30 minutes.
AGENT_BUSY_S = 5 * 60
TAIL_BYTES = 262144
WORKER_PAGES = 50
# An AskUserQuestion dialog, a permission dialog, a y/n prompt.
QUESTION_PATTERNS = (r"Enter to select", r"Do you want to (proceed|make this edit|create|allow)", r"\(y/n\)",
                     r"Esc to cancel")


# ---------------------------------------------------------------- facts

def claude_in(path, rows, cwd_of):
    """(pids whose cwd lies in path, the Claude sessions among them)."""
    inside = [p for p, c in cwd_of.items() if reap.under(reap.real(c), path) and p in rows]
    return inside, {p for p in inside if reap.is_claude(rows[p]["cmd"])}


def busy_shells(path, rows, cwd_of):
    """Shells a live Claude session in path still runs: every Bash call, background shell and Monitor is a shell child
    of the session. Its other children (MCP servers, caffeinate) live as long as the session and say nothing."""
    _, claude = claude_in(path, rows, cwd_of)
    return sorted(q for q, r in rows.items() if r["ppid"] in claude
                  and os.path.basename((r["cmd"].split() or [""])[0]).lstrip("-") in reap.SHELLS)


def child_cards(w):
    """Paths of the cards a row of `orca worktree list` made (a lead's) that are still on disk. An id is
    `<repo>::<path>[::workspace:<id>]`."""
    ids = w.get("childWorktreeIds") or ()
    return sorted(c for c in ((i or "").split("::")[1] if "::" in (i or "") else "" for i in ids) if c and os.path.isdir(c))


def _orca_every_run(args):
    """An Orca listing over every Run: with this terminal's handle set, worker-list scopes to its bound Run. Run from
    the checkout: outside a git directory (launchd starts in /) Orca was seen binding the list to one Run anyway."""
    p = subprocess.run(["orca", *args, "--json"], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "ORCA_TERMINAL_HANDLE": ""}, cwd=REPO)
    out = json.loads(p.stdout or "{}")
    if p.returncode or not isinstance(out, dict) or out.get("ok") is False:
        raise RuntimeError(f"orca {' '.join(args[:2])} failed: {str(out.get('error') if isinstance(out, dict) else out)[:120]}"
                           f" (exit {p.returncode})")
    return out.get("result", out)


def _orca_as_seen(*args):
    """An Orca listing as this terminal sees it; raises with Orca's error, which worker_list reads."""
    p = subprocess.run(["orca", *args, "--json"], capture_output=True, text=True, timeout=30)
    try:
        out = json.loads(p.stdout or "{}")
    except ValueError:
        out = None
    if p.returncode or not isinstance(out, dict) or out.get("ok") is False:
        raise RuntimeError(f"orca {' '.join(args[:2])} failed: {str(out.get('error') if isinstance(out, dict) else p.stderr)[:120]}"
                           f" (exit {p.returncode})")
    return out.get("result", out)


def worker_pages(state=None, every_run=True, deadline=None):
    """Every page of `orca orchestration worker-list`. Raises when a page fails, the budget runs out (deadline; with
    none, the page cap), or (every_run) the list is scoped to one Run: a list cut short is a failure, not a smaller
    fleet. every_run=False reads as the caller's terminal sees it, unchecked."""
    if deadline and time.time() > deadline - 30:
        raise TimeoutError("worker-list pages left unread")
    rows, call = [], (lambda *a: _orca_every_run(a)) if every_run else _orca_as_seen
    for n, (page, res, more) in enumerate(_orch("orca_settings").worker_list(
            call, "--limit", "100", *(["--terminal-state", state] if state else [])), 1):
        if not isinstance(res.get("workers"), list):
            raise RuntimeError("worker-list answered without a workers list")
        if every_run and (res.get("scope") or {}).get("source", "all") != "all":
            raise RuntimeError(f"worker-list scoped to {res['scope']}, not every Run")
        rows += page
        if not more:
            return rows
        if deadline and time.time() > deadline - 30:
            raise TimeoutError("worker-list pages left unread")
        if not deadline and n >= WORKER_PAGES:
            raise RuntimeError(f"worker-list has more than {WORKER_PAGES} pages")
    return rows


def _orch(name):
    if str(ORCH_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(ORCH_SCRIPTS))
    return __import__(name)


def transcript_dirs(path):
    """Where a card's transcripts may be: dash's (CLAUDE_PROJECTS_DIR) and the cleaners' (CLAUDE_CONFIG_DIR/projects)."""
    mangled = re.sub(r"[^A-Za-z0-9]", "-", path)
    config = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "projects" / mangled
    return list(dict.fromkeys([_orch("dash").transcript_dir(path), config]))


def transcript_tail(path, strict=False, folder=None):
    """(motion, last row) from the tail of the newest transcript of a card in folder (dash's by default), or
    (None, None) without one. motion is dash.step_motion's: {"state": "idle"|..., "since", "parked"}. A transcript or
    a folder that cannot be read is (None, None) too, or raises with strict."""
    dash = _orch("dash")
    folder = folder or dash.transcript_dir(path)
    try:
        try:
            files = [folder / n for n in os.listdir(folder) if n.endswith(".jsonl")]  # glob hides a denied folder
        except (FileNotFoundError, NotADirectoryError):
            files = []
        if not files:
            return None, None
        newest = max(files, key=lambda f: f.stat().st_mtime)
        with newest.open("rb") as f:
            f.seek(max(0, newest.stat().st_size - TAIL_BYTES))
            raw = f.read().splitlines()[1:]
    except OSError:
        if strict:
            raise
        return None, None
    motion, last = {}, None
    for line in raw:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("timestamp"):
            motion, last = dash.step_motion(motion, row), row
    return motion or None, last


def parked(path):
    """Whether the newest transcript in any of the card's transcript folders armed its own wake-up; raises when one
    cannot be read. Each folder counts on its own, so a second folder never hides what the first one shows."""
    return any((transcript_tail(path, True, d)[0] or {}).get("parked") for d in transcript_dirs(path))


def observe(path):
    """What guard() checks, read now. A part that could not be read is None."""
    def attempt(fn):
        try:
            return fn()
        except (SystemExit, Exception):  # any failure is "could not see", which keeps the card
            return None
    kept = reap.patrol_read()
    f = {"patrol": None if kept is None else path in kept,
         "head": attempt(lambda: reap.run(["git", "-C", path, "rev-parse", "HEAD"]).strip() or None)}
    res = reap.orca_json("terminal", "list", "--worktree", f"path:{path}")
    f["terminals"] = res["terminals"] if isinstance(res, dict) and not res.get("truncated") \
        and isinstance(res.get("terminals"), list) else None
    agents = [t["handle"] for t in f["terminals"] or () if t.get("agentIdentity")]
    if agents:  # a session with no agent terminal left can neither answer a prompt nor wake itself
        prog = attempt(lambda: _orch("prog"))
        f["waits"] = prog and attempt(lambda: prog.agent_waits(strict=True))
        f["screens"] = {h: prog and attempt(lambda h=h: prog.read_screen(h)) for h in agents}
        f["motion"] = attempt(lambda: {"parked": parked(path)})
    return f


# ---------------------------------------------------------------- the guard

QUESTION = [re.compile(p) for p in QUESTION_PATTERNS]


def check(f, head, handles, final=False):
    """(ok, why) for closing what was judged at HEAD head with its terminals handles, from observe()'s facts. final:
    after those terminals were closed, before `worktree rm`: their sessions are gone, so only patrol, HEAD and new
    terminals are looked at."""
    if f.get("patrol") is None:
        return False, "순찰 판정 파일을 읽지 못함"
    if f["patrol"]:
        return False, "순찰 판정: 작업 중·사람 대기·Run 워커(orch patrol)"
    if not f.get("head"):
        return False, "HEAD 를 읽지 못함"
    if f["head"] != head:
        return False, "판정 뒤 HEAD 가 바뀜"
    if f.get("terminals") is None:
        return False, "터미널 목록을 읽지 못함"
    if (new := [t.get("handle") for t in f["terminals"] if t.get("handle") not in handles]):
        return False, f"판정 뒤 새 터미널 {new[0]}"
    if final:
        return True, "확인함"
    agents = [t["handle"] for t in f["terminals"] if t.get("agentIdentity")]
    if agents and f.get("waits") is None:
        return False, "Orca agent-wait 를 읽지 못함"
    for h in agents:
        if isinstance(f["waits"].get(h), dict):
            return False, f"에이전트 {h} 가 사람의 답을 기다림(Orca)"
        lines = (f.get("screens") or {}).get(h)
        if not lines:
            return False, f"에이전트 {h} 화면을 읽지 못함"
        if any(p.search("\n".join(lines[-40:])) for p in QUESTION):
            return False, f"에이전트 {h} 화면에 질문"
    if agents and f.get("motion") is None:
        return False, "세션 기록을 읽지 못함"
    if agents and f["motion"].get("parked"):
        return False, "세션이 스스로 깨울 예약을 걸어 둠(parked)"
    return True, "확인함"


def guard(path, head, handles, final=False):
    """check() over facts observe() reads now."""
    return check(observe(path), head, set(handles), final)


@contextlib.contextmanager
def card_lock(path):
    """<state>/locks/card-<hash>.lock, held from the judgement again to `worktree rm`. Yields False when another
    cleaner holds it: that card is skipped this pass, never waited on."""
    d = reap.STATE / "locks"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"card-{hashlib.sha1(reap.real(path).encode()).hexdigest()[:16]}.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
