#!/usr/bin/env python3
"""Fleet view for orch-dash: every Orca session under one top-level orchestrator, the PRs they own with their
reviewers, and what the human has to act on. Deterministic and model-free, like the program view.

Sources are local CLIs only (`orca`, `gh`, `git`). Nothing is sent anywhere but GitHub's own API through `gh`,
and the state lives outside the repository:

  $ORCH_FLEET_STATE  (default ~/.local/state/agent-skills/dashboard)   state.json, prs.json, inbox.json, events,
                                                                       prompts/<terminal>.json (hooks/permission.py),
                                                                       decisions.json (`orch decide`)
  $PROGRAMS_HOME/_locks (default ~/.claude/programs/_locks)            who holds a landing lane or a shared resource
                                                                       (`orch hold`), listed into state.json
  $ORCH_FLEET_CONFIG (default ~/.config/agent-skills/dashboard/config.json)

Everything written is masked first (tokens, auth headers, *_TOKEN=...): prompts and tool inputs are raw text.
The one exception is the `orch sent add` log (sent/, sent.py): it keeps the text as sent, owner-only (0600 in a 0700
directory), and is masked when shown.
"""
import collections, concurrent.futures, contextlib, datetime as dt, fcntl, glob, hashlib, io, json, os, pathlib, re, shlex, subprocess, sys, tempfile, threading, time

ORCA_EVERY = 10          # worktree ps, terminal list, inbox: three local calls, ~0.5 s together
INBOX_LIMIT = 2000       # every message Orca keeps: at 100, heartbeats pushed unanswered questions out of view
RUNS_EVERY = 120         # runs, workers, tasks, gates
PR_TICK = 90             # PR probe tick; each PR is due per its tier below
SEARCH_PAGES = 3         # 100 PRs a page
PR_TICK_VIEWED = 30      # the same while a page is open: the search finds a new or merged PR within this
DISCOVER_EVERY = 600     # re-ask GitHub which PR a branch has, for sessions that had none
TIER_EVERY = {"hot": 90, "warm": 300, "cold": 900, "done": 86400}  # while no page is open; an open page probes
                                                                   # every OPEN PR each PR tick (one query per 50)
RATE_FLOOR = 300         # GraphQL points left below which only a forced PR tick runs, until the hour resets
EVENTS_KEEP = 400
ROTATE_BYTES = 5 * 1024 * 1024  # events.jsonl and pr-events.jsonl move to *.1 past this
STICKY_TYPES = ("approval", "run_command", "login", "verify_failed", "question")
SETTLED = ("completed", "failed", "abandoned")  # dispatch statuses whose worker has stopped
AGENT_GONE_S = 600       # a session without an agent this long closes its turn items
BRANCH_SKIP = {"main", "master", "develop", "trunk"}
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")


class SourceError(Exception):
    pass


# ---------------------------------------------------------------- paths, time, io

def state_dir():
    return pathlib.Path(os.environ.get("ORCH_FLEET_STATE", "~/.local/state/agent-skills/dashboard")).expanduser()


def config_path():
    return pathlib.Path(os.environ.get("ORCH_FLEET_CONFIG", "~/.config/agent-skills/dashboard/config.json")).expanduser()


def config():
    return read_json(config_path(), {}) or {}


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def ms_iso(v):
    return iso(dt.datetime.fromtimestamp(v / 1000, dt.timezone.utc)) if isinstance(v, (int, float)) and v else None


def parse(s):
    # Before 3.11 fromisoformat takes neither `Z` nor skills-sync's `+0900`.
    try:
        return dt.datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", str(s).replace("Z", "+00:00")))
    except ValueError:
        return None


def read_json(path, default=None):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_atomic(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb" if isinstance(text, bytes) else "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def append_line(path, row):
    """One write() under O_APPEND and under 4 KiB, so a reader never sees half a line."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(line) >= 4096:
        raise ValueError("event line over 4 KiB")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def rotate(path):
    try:
        if path.stat().st_size > ROTATE_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def read_lines(path, keep=None):
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-keep:] if keep else lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def digest(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- masking

SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-ant-[A-Za-z0-9_-]{10,}"
    r"|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")
ASSIGN_RE = re.compile(r"\b([A-Z0-9_-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|CUSTOM_HEADERS)[A-Z0-9_-]*)"
                       r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)", re.I)
AUTH_RE = re.compile(r"\b(Authorization|Proxy-Authorization|X-Api-Key|Bearer|Basic)(\s*[:=]?\s+)([^\s\"']+)", re.I)
URL_PW_RE = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]*:)([^\s/@]+)(@)")
# --password x, and a password glued to -p (mysql -pX); -pv and the like are flags, not passwords.
FLAG_PW_RE = re.compile(r"((?<!\S)--pass(?:word|wd)?(?:\s+|=)|(?<!\S)-p(?![A-Za-z]{1,2}(?:\s|$)))([^\s\"']+)", re.I)


def mask(text, limit=None):
    """Hide credentials in free text. Applied to everything collected before it is written or shown."""
    if not text:
        return text
    s = SECRET_RE.sub("[masked]", str(text))
    s = ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[masked]", s)
    s = AUTH_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[masked]", s)
    s = URL_PW_RE.sub(lambda m: f"{m.group(1)}[masked]{m.group(3)}", s)
    s = FLAG_PW_RE.sub(lambda m: f"{m.group(1)}[masked]", s)
    if limit and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


# ---------------------------------------------------------------- CLI sources

def run_json(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SourceError(f"{cmd[0]} not found")
    except subprocess.TimeoutExpired:
        raise SourceError(f"{cmd[0]} timed out after {timeout}s")
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        out = None
    if r.returncode and out is None:
        tail = (r.stderr or r.stdout).strip().splitlines()
        # A Node crash ends in "Node.js vNN"; the cause is its Error line (or its code:/syscall: fields).
        cause = next((l for pat in (r"\s*\w*Error\b", r"\s*(code|syscall):") for l in tail if re.match(pat, l)),
                     tail[-1] if tail else None)
        raise SourceError(mask(cause.strip()[:300]) if cause else f"{cmd[0]} exit {r.returncode}")
    if out is None:
        raise SourceError(f"{cmd[0]}: output was not JSON")
    return out


def orca(*args, timeout=30):
    out = run_json(["orca", *args, "--json"], timeout)
    if isinstance(out, dict) and out.get("ok") is False:
        raise SourceError(mask(json.dumps(out.get("error"))[:300]))
    return out.get("result", out) if isinstance(out, dict) else {}


def fetch_fast():
    calls = {"worktrees": ("worktree", "ps", "--limit", "500"), "terminals": ("terminal", "list"),
             "messages": ("orchestration", "inbox", "--limit", str(INBOX_LIMIT))}
    with concurrent.futures.ThreadPoolExecutor(len(calls)) as pool:
        futures = {k: pool.submit(orca, *args) for k, args in calls.items()}
        return {k: f.result().get(k, []) for k, f in futures.items()}


RUN_CACHE = {}  # run id -> (monotonic time read, {"workers", "tasks", "gates"}) for runs that had stopped moving
SETTLED_EVERY = 900  # a settled run whose coordinator is off screen cannot move by itself; re-read it this often anyway


def settled_run(got):
    return (all(w.get("dispatchStatus") in SETTLED for w in got["workers"])
            and all(t.get("status") in SETTLED for t in got["tasks"])
            and not any(g.get("status") in ("pending", "open") for g in got["gates"]))


def fetch_runs():
    listed = orca("orchestration", "run-list").get("runs", [])
    runs = [r for r in listed if not r.get("legacy") and r.get("id") != "run_legacy_local"]
    live = {t.get("handle") for t in orca("terminal", "list").get("terminals", [])}
    out = {"runs": runs, "workers": [], "tasks": [], "gates": []}
    for r in listed:
        hit = RUN_CACHE.get(r["id"])
        if hit and r.get("coordinator_handle") not in live and time.monotonic() - hit[0] < SETTLED_EVERY:
            got = hit[1]
        else:
            got = fetch_run(r, r in runs)
            RUN_CACHE.pop(r["id"], None)
            if settled_run(got):
                RUN_CACHE[r["id"]] = (time.monotonic(), got)
        for k in ("workers", "tasks", "gates"):
            out[k] += got[k]
    return out


def fetch_run(r, full):
    # Without --run, worker-list is scoped to the Run bound to the calling terminal: a server started from a
    # coordinator's terminal saw only that Run's workers, and mail from every other Run's settled worker stayed open.
    workers, cursor = [], None
    while True:
        res = orca("orchestration", "worker-list", "--run", r["id"], "--limit", "100",
                   *(["--cursor", cursor] if cursor else []))
        workers += res.get("workers", [])
        cursor = (res.get("page") or {}).get("nextCursor")
        if not (res.get("page") or {}).get("hasMore") or not cursor:
            break
    if not full:
        return {"workers": workers, "tasks": [], "gates": []}
    return {"workers": workers, "tasks": orca("orchestration", "task-list", "--run", r["id"]).get("tasks", []),
            "gates": orca("orchestration", "gate-list", "--run", r["id"]).get("gates", [])}


AUTOMATION_RECENT = 6  # runs kept per automation: the dashboard's Automations page draws them as a row of blocks


def fetch_automations():
    """Orca's scheduled automations, each with its last runs newest first. Only Orca's scheduler is visible here: a
    session's own loop (CronCreate) or a launchd job leaves nothing to collect."""
    out = []
    for a in orca("automations", "list").get("automations", []):
        runs = sorted(orca("automations", "runs", "--id", a["id"]).get("runs", []),
                      key=lambda r: r.get("runNumber") or 0, reverse=True)[:AUTOMATION_RECENT]
        out.append({"id": a["id"], "name": mask(a.get("name"), 80), "rrule": a.get("rrule"), "timezone": a.get("timezone"),
                    "enabled": bool(a.get("enabled")), "next_run_at": ms_iso(a.get("nextRunAt")),
                    "last_run_at": ms_iso(a.get("lastRunAt")),
                    # The output can name people and channels: masked, first line only, never cut short.
                    "recent": [{"status": r.get("status"), "trigger": r.get("trigger"),
                                "at": ms_iso(r.get("startedAt") or r.get("scheduledFor")),
                                "summary": first_line((r.get("outputSnapshot") or {}).get("content") or r.get("error"), None)}
                               for r in runs]})
    return out


MONITOR_KEEP = 6 * 3600  # an ended or lost monitor stays on the Monitors page this long, unless its command ran again
MONITOR_SCAN = {}  # transcript path -> {"offset", "pending", "tasks"}: each tick reads only what was appended
NOTICE_RE = re.compile(r"<(task-id|status|summary|output-file|event)>(.*?)</\1>", re.S)
EXIT_RE = re.compile(r"\bexit(?: code)? (-?\d+)")  # "(exit code 1)", "failed with exit code 1", "(exit 127)"
EVAL_RE = re.compile(r"\beval '(.*)' < /dev/null", re.S)  # how Claude Code's shell wraps the command it runs
BORN_SLACK = dt.timedelta(seconds=15)  # a task's shell starts this close to the result that reported it


def claude_home():
    return pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()


# Orca starts each agent with a profile's own CLAUDE_CONFIG_DIR, and a session registers under that dir only.
CLAUDE_HOMES = ("~/.claude", "~/Library/Application Support/orca/claude-profiles/*/home")


def claude_homes():
    """Config dirs whose sessions/ list live Claude Code sessions: ORCH_CLAUDE_HOMES (os.pathsep list, globs allowed)
    when set, else this process's, ~/.claude and each Orca profile's."""
    pats = os.environ.get("ORCH_CLAUDE_HOMES")
    pats = pats.split(os.pathsep) if pats else [str(claude_home()), *CLAUDE_HOMES]
    return list(dict.fromkeys(pathlib.Path(h) for pat in pats for h in sorted(glob.glob(os.path.expanduser(pat)))))


def ps_table():
    """pid -> (ppid, start, command). UTC, the clock Claude Code writes a session's procStart in."""
    try:
        out = subprocess.run(["ps", "-axwwo", "pid=,ppid=,lstart=,command="], capture_output=True, text=True, timeout=10,
                             env={**os.environ, "TZ": "UTC", "LC_ALL": "C"}).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SourceError(f"ps: {e}")
    procs = {}
    for line in out.splitlines():
        f = line.split(None, 7)
        if len(f) == 8 and f[0].isdigit():
            procs[int(f[0])] = (int(f[1]), " ".join(f[2:7]), f[7])
    return procs


def ps_time(lstart):
    try:
        return dt.datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def scan_transcript(path):
    """The transcript's background tasks so far: a Bash run with run_in_background or a Monitor, its start, and the
    task-notification that ended it or carried its last event, or the TaskStop that stopped it (which writes no
    notification). Agent and workflow tasks are left out."""
    c = MONITOR_SCAN.setdefault(str(path), {"offset": 0, "pending": {}, "tasks": {}, "continued": False})
    for raw in appended(path, c, lambda: c.update(pending={}, tasks={}, continued=False)):
        scan_line(c, raw)
    return c["tasks"]


def appended(path, c, reset):
    """Each complete line written to path since c["offset"]; the offset moves past a line once the caller took it.
    A file that shrank was rewritten: reset() clears what the caller kept and it is read from the start."""
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size < c["offset"]:
                c["offset"] = 0
                reset()
            f.seek(c["offset"])
            for raw in iter(f.readline, b""):
                if not raw.endswith(b"\n"):
                    break  # a line still being written is read next time
                yield raw
                c["offset"] = f.tell()
    except OSError:
        pass


def scan_line(c, raw):
    if not any(k in raw for k in (b"run_in_background", b'"Monitor"', b"backgroundTaskId", b'"taskId"',
                                  b"<task-notification>", b'"TaskStop"', b'"task_id"', b'"continued-in"')):
        return
    try:
        d = json.loads(raw)
    except ValueError:
        return
    if not isinstance(d, dict):
        return
    if d.get("type") == "continued-in":
        c["continued"] = True  # the session goes on in a new transcript, which repeats what this one started
        return
    text = notice_text(d)
    if text:
        return scan_notice(c, d, text)
    if not isinstance(d.get("message"), dict):
        return
    content = d["message"].get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    if d.get("type") == "assistant":
        for b in blocks:
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            if b.get("type") == "tool_use" and (b.get("name") == "Monitor" or b.get("name") == "Bash" and inp.get("run_in_background")):
                c["pending"][b.get("id")] = {"kind": "monitor" if b["name"] == "Monitor" else "bash",
                                             "command": str(inp.get("command") or ""), "description": inp.get("description")}
            elif b.get("type") == "tool_use" and b.get("name") == "TaskStop":
                c["pending"][b.get("id")] = {"stop": inp.get("task_id")}
    elif d.get("type") == "user" and blocks:
        r = d.get("toolUseResult") if isinstance(d.get("toolUseResult"), dict) else {}
        tid = r.get("backgroundTaskId") or r.get("taskId")
        result = next((b for b in blocks if b.get("type") == "tool_result"), {})
        use = c["pending"].pop(result.get("tool_use_id"), None)
        stopped = use and c["tasks"].get(use.get("stop"))
        if stopped and not result.get("is_error") and not stopped.get("ended_at"):
            stopped.update(ended_at=stamp(d), end_status="stopped", exit=None, summary=None)
        elif tid and use and not use.get("stop"):
            m = re.search(r"Output is being written to: (\S+?)\.?(?:\s|$)", str(result.get("content") or ""))
            c["tasks"][tid] = {**use, "id": tid, "started_at": stamp(d), "output": m and m.group(1),
                               "timeout_ms": None if r.get("persistent") else r.get("timeoutMs")}


def notice_text(d):
    """A task-notification from whichever line carried it: a user turn, the queue it waited in, or the attachment it
    became when a turn absorbed it. Most notifications are written two or three of these ways; some only as the last two."""
    if d.get("type") == "queue-operation":
        text = d.get("content") if d.get("operation") == "enqueue" else None  # its remove line repeats it
    elif d.get("type") == "attachment":
        a = d.get("attachment") if isinstance(d.get("attachment"), dict) else {}
        text = a.get("prompt") if a.get("type") == "queued_command" else None
    else:
        m = d.get("message") if isinstance(d.get("message"), dict) else {}
        text = m.get("content") if d.get("type") == "user" else None
    return text if isinstance(text, str) and text.startswith("<task-notification>") else None


def scan_notice(c, d, text):
    n = dict(NOTICE_RE.findall(text))
    t = c["tasks"].get(n.get("task-id"))
    if not t:
        return
    at = stamp(d)
    if n.get("status") and n["status"] != "running" and d.get("type") != "queue-operation":
        t["seen"] = True  # a user turn or an attachment: the end reached the session's model, not only its queue
    t["output"] = t.get("output") or n.get("output-file")
    if n.get("event") and (at or "") >= (t.get("event_at") or ""):
        t["event_at"], t["event"] = at, n["event"]
    if n.get("status") and n["status"] != "running" and not t.get("ended_at"):
        codes = EXIT_RE.findall(n.get("summary") or "")  # the last: the quoted description may say "exit" too
        t.update(ended_at=at, end_status=n["status"], exit=int(codes[-1]) if codes else None, summary=n.get("summary"))


def stamp(d):
    t = parse(d.get("timestamp")) if d.get("timestamp") else None
    return iso(t.astimezone(dt.timezone.utc)) if t and t.tzinfo else None


def squash(cmd):
    """A command as both a transcript and ps print it: quotes, backslashes and newlines become spaces."""
    return " ".join(re.sub(r"\\012|[\\'\"]", " ", cmd or "").split())


def fetch_monitors(procs=None, now=None, warn=None):
    """Background watches the live Claude Code sessions started: every Bash run in the background and every Monitor,
    from each session's transcript, checked against the process table. A task with no end record is running only if a
    shell its session started within BORN_SLACK of it runs exactly its command; otherwise it is lost (the UI, a
    Monitor's expiry or a teardown stopped it without a record, or it died with its shell). Ended tasks are kept
    MONITOR_KEEP when they failed or were lost, and dropped once the same command runs again in that session. A failure
    whose notification reached the session's model is marked seen: that session has it. A transcript that was continued
    in another one is skipped, since the new one repeats its tasks.
    warn: a list that gets one line per live session whose transcript was not found."""
    try:
        return collect_monitors(ps_table() if procs is None else procs, now or utcnow(), [] if warn is None else warn)
    except SourceError:
        raise
    except Exception as e:  # a transcript shape not foreseen fails this source only, not the rest of the tick
        raise SourceError(f"monitors: {type(e).__name__}: {e}")


def live_sessions(procs):
    """session id -> session record with its live "pids" and the config "home" it registered under, for each live
    Claude Code session."""
    sessions = {}
    for home in claude_homes():
        for p in sorted((home / "sessions").glob("*.json")):
            s = read_json(p, {}) or {}
            proc = procs.get(s.get("pid"))
            if s.get("sessionId") and s.get("cwd") and proc and proc[1] == " ".join(str(s.get("procStart")).split()):
                # Resuming a session in a second terminal leaves two live processes writing one transcript.
                sessions.setdefault(s["sessionId"], {**s, "pids": [], "home": home})["pids"].append(s["pid"])
    return sessions


def declared_roles(procs):
    """[(cwd, session name, "orchestrator" | "lead")] for each live Claude Code session that declared a role: the
    `ROLE:` line hooks/reorient.py recorded in roles/<session id>.json, else a `--name orchestrator` or `lead:<slug>`."""
    base = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()  # as reorient.py
    out = []
    for sid, s in live_sessions(procs).items():
        name = s.get("name") or ""
        role = (read_json(base / "roles" / f"{sid}.json", {}) or {}).get("role") \
            or ("orchestrator" if name == "orchestrator" else "lead" if name.startswith("lead:") else None)
        if role in ("orchestrator", "lead"):
            out.append((s["cwd"], name, role))
    return out


def live_transcripts(procs, warn):
    """(session id, session record with its live "pids", transcript path) for each live Claude Code session."""
    out = []
    for sid, s in live_sessions(procs).items():
        home = s["home"]
        path = home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", s["cwd"]) / f"{sid}.jsonl"
        if not path.exists():  # a long cwd gets a shortened directory name
            path = next(iter(sorted((home / "projects").glob(f"*/{sid}.jsonl"))), path)
        if not path.exists():
            warn.append(f"{s.get('name') or sid}: transcript not found")
            continue
        out.append((sid, s, path))
    return out


def collect_monitors(procs, now, warn):
    out, scanned = [], set()
    for sid, s, path in live_transcripts(procs, warn):
        scanned.add(str(path))
        tasks = scan_transcript(path)
        if MONITOR_SCAN[str(path)]["continued"]:
            continue
        # Only the session's own shells: a $(…) inside one forks a shell with the same command line.
        free = {}
        for pid, (ppid, start, cmd) in procs.items():
            m = ppid in s["pids"] and EVAL_RE.search(cmd)
            if m:
                free[pid] = (squash(m.group(1)), ps_time(start))
        rows = sorted(tasks.values(), key=lambda t: t.get("started_at") or "", reverse=True)
        seen = set()
        for t in rows:
            key, began = squash(t["command"]), parse(t.get("started_at"))
            again = key in seen
            seen.add(key)
            if t.get("ended_at"):
                # killed and stopped are TaskStop or the UI ending it on purpose
                ok = t["end_status"] in ("killed", "stopped") or t["end_status"] == "completed" and not t.get("exit")
                status, at = ("ended" if ok else "dead"), t["ended_at"]
            else:
                # A foreground run of the same command, or a later copy, is not this task's shell.
                pid = next((pid for pid, (cmd, born) in free.items() if key and cmd == key
                            and began and born and abs(born - began) <= BORN_SLACK), None)
                if pid:
                    del free[pid]
                status, at = ("running", None) if pid else ("lost", None)
            last = max(filter(None, [t.get("event_at"), at, mtime_iso(t.get("output"))]), default=t.get("started_at"))
            if status == "lost" and t.get("timeout_ms") and began and now - began > dt.timedelta(milliseconds=t["timeout_ms"]):
                status = "ended"  # a Monitor that reached the expiry it was given
            # One with no time at all stays: its age is unknown, not old.
            if status == "ended" or status != "running" and (again or last and (now - parse(last)).total_seconds() > MONITOR_KEEP):
                continue
            out.append({"id": t["id"], "kind": t["kind"], "status": status, "exit": t.get("exit"),
                        "seen": status == "dead" and bool(t.get("seen")),
                        "command": mask(t["command"], 300), "description": mask(t.get("description"), 120),
                        "claude_session": sid, "session_name": mask(s.get("name"), 80), "cwd": s["cwd"],
                        "started_at": t.get("started_at"), "last_at": last, "ended_at": at,
                        "summary": first_line(t.get("summary"), 160), "event": first_line(t.get("event"), 160)})
    for k in set(MONITOR_SCAN) - scanned:
        del MONITOR_SCAN[k]  # its session ended
    out.sort(key=lambda m: m["last_at"] or m["started_at"] or "", reverse=True)
    return sorted(out, key=lambda m: m["status"] == "running")  # the ones to look at first


def mtime_iso(path):
    try:
        return iso(dt.datetime.fromtimestamp(os.stat(path).st_mtime, dt.timezone.utc)) if path else None
    except OSError:
        return None


SENT_SCAN = {}  # transcript path -> {"offset", "rows"}: what a live session sent, read like MONITOR_SCAN
SENT_KEEP = dt.timedelta(days=30)  # the Sent page's longest window; older rows leave SENT_SCAN
# orca <noun> <verb> -> (the flag naming the receiver, the flag carrying the text, the receiver's kind)
ORCA_SENDS = {("terminal", "send"): ("--terminal", "--text", "terminal"), ("worktree", "create"): ("--name", "--prompt", "card"),
              ("orchestration", "send"): ("--to", "--body", "inbox")}
SHELL_OPS = {"&&", "||", ";", "|", "&", ";;"}
ORCA_MARKS = tuple(f"{noun} {verb}".encode() for noun, verb in ORCA_SENDS)  # every transcript line names the cwd, often under orca/


def orca_sends(command):
    """[(kind, to, subject, text, channel)] for each `orca terminal send --text`, `orca worktree create --prompt` and
    `orca orchestration send` in one Bash command. One shlex cannot split, or without its text (--help), is skipped."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        words = list(lex)
    except ValueError:
        return []
    out = []
    for i, w in enumerate(words):
        spec = ORCA_SENDS.get(tuple(words[i + 1:i + 3])) if w == "orca" or w.endswith("/orca") else None
        if not spec:
            continue
        opts, args = {}, words[i + 3:]
        for j, a in enumerate(args):
            if a in SHELL_OPS:
                break
            if a.startswith("--"):
                k, eq, v = a.partition("=")
                nxt = args[j + 1] if j + 1 < len(args) else None
                opts[k] = v if eq else nxt if nxt is not None and not nxt.startswith("--") and nxt not in SHELL_OPS else True
        to_flag, text_flag, kind = spec
        text, subject = opts.get(text_flag), opts.get("--subject")
        if opts.get("--type") == "heartbeat":
            continue  # one every 5 min per worker
        if kind == "inbox" and not isinstance(text, str):
            text = subject  # a message with only a subject
        if isinstance(text, str) and text.strip():
            to = opts.get(to_flag)
            out.append((kind, to if isinstance(to, str) else None, subject if isinstance(subject, str) else None, text,
                        f"orca {words[i + 1]} {words[i + 2]}"))
    return out


def sent_line(raw):
    """What one assistant transcript line sent: a subagent's prompt (Agent), a message to an agent (SendMessage), or
    an orca send in a Bash command."""
    if not any(k in raw for k in (b'"Agent"', b'"Task"', b'"SendMessage"', *ORCA_MARKS)):
        return []
    try:
        d = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(d, dict) or d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
        return []
    content = d["message"].get("content")
    out = []
    for b in content if isinstance(content, list) else []:
        inp = b.get("input") if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("input"), dict) else None
        name = inp is not None and b.get("name")
        if name in ("Agent", "Task") and isinstance(inp.get("prompt"), str):
            out.append(("subagent", inp.get("subagent_type") or "general-purpose", inp.get("description"), inp["prompt"], name))
        elif name == "SendMessage" and isinstance(inp.get("message"), str):
            out.append(("agent", inp.get("to"), inp.get("summary"), inp["message"], name))
        elif name == "Bash" and any(k.decode() in str(inp.get("command")) for k in ORCA_MARKS):
            out.extend(orca_sends(str(inp["command"])))
    return [{"ts": stamp(d), "kind": k, "to": to, "subject": subj, "text": text, "channel": ch} for k, to, subj, text, ch in out]


def fetch_sent(procs=None, now=None):
    """What the live Claude Code sessions sent, read from their transcripts (see sent_line). An ended session's rows
    leave with it, and a row older than SENT_KEEP: the durable record is the `orch sent add` log (sent.py)."""
    try:
        out, scanned, cutoff = [], set(), iso((now or utcnow()) - SENT_KEEP)
        for sid, s, path in live_transcripts(ps_table() if procs is None else procs, []):
            scanned.add(str(path))
            c = SENT_SCAN.setdefault(str(path), {"offset": 0, "rows": []})
            for raw in appended(path, c, lambda: c.update(rows=[])):
                c["rows"].extend(sent_line(raw))
            c["rows"] = [r for r in c["rows"] if (r["ts"] or "") >= cutoff]
            out += [{**r, "from": s.get("name") or sid, "claude_session": sid, "cwd": s["cwd"], "source": "transcript"}
                    for r in c["rows"]]
        for k in set(SENT_SCAN) - scanned:
            del SENT_SCAN[k]
        return out
    except SourceError:
        raise
    except Exception as e:
        raise SourceError(f"sent: {type(e).__name__}: {e}")


def graphql(query):
    """(data, errors). gh exits non-zero when any alias errors, even with the rest of the data present, so one
    inaccessible PR must not blank the others."""
    out = run_json(["gh", "api", "graphql", "-f", f"query={query}"], timeout=60)
    data = out.get("data") if isinstance(out, dict) else None
    errors = [mask(e.get("message", "")[:200]) for e in (out.get("errors") or [])] if isinstance(out, dict) else []
    if data is None:
        raise SourceError("; ".join(errors) or "GraphQL returned no data")
    return data, errors


def gql_str(s):
    return json.dumps(str(s))


WORKSPACE_RE = re.compile(r"/orca/workspaces/([^/]+)/")


def project_of(path, orca_repo=None, gh_repo=None):
    """The project a worktree belongs to, from live state: Orca's own project name, else the workspace directory Orca
    put it under (~/orca/workspaces/<project>/<worktree>), else the origin repository's name, else the directory."""
    m = WORKSPACE_RE.search(f"{path or ''}/")
    return orca_repo or (m and m.group(1)) or (gh_repo or "").partition("/")[2] or pathlib.Path(path or "").name or None


def repo_of(path, cache):
    """owner/name of the worktree's GitHub origin, or None."""
    if path in cache:
        return cache[path]
    slug = None
    try:
        url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$", url)
        slug = f"{m.group(1)}/{m.group(2)}" if m else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    cache[path] = slug
    return slug


# ---------------------------------------------------------------- sessions and hierarchy

def short_branch(ref):
    return (ref or "").removeprefix("refs/heads/")


def agent_row(a):
    return {"pane": a.get("paneKey"), "state": a.get("state"), "type": a.get("agentType"),
            "mode": a.get("workingMode"), "tool": a.get("toolName"), "detail": mask(a.get("toolInput"), 140),
            "since": ms_iso(a.get("stateStartedAt")), "updated": ms_iso(a.get("updatedAt")),
            "title": mask(a.get("taskTitle") or a.get("displayName"), 120),
            "prompt": mask(a.get("prompt"), 400), "last": mask(a.get("lastAssistantMessage"), 1200),
            "interrupted": bool(a.get("interrupted"))}


def phase_of(agents, live_terms):
    states = {a["state"] for a in agents}
    if "waiting" in states:
        return "waiting"
    if "working" in states:
        return "working"
    if "done" in states:
        return "idle"
    return "open" if live_terms else "offline"


def build_sessions(fast, runs, cfg, declared=()):
    terms_by_wt = {}
    for t in fast["terminals"]:
        terms_by_wt.setdefault(t.get("worktreeId"), []).append(
            {"handle": t.get("handle"), "pane": f"{t.get('tabId')}:{t.get('leafId')}",  # an agent's paneKey
             "title": mask(t.get("title"), 120), "agent": t.get("agentIdentity"),
             "writable": bool(t.get("writable")), "connected": bool(t.get("connected")),
             "last_output": ms_iso(t.get("lastOutputAt"))})
    wt_of_handle = {t.get("handle"): t.get("worktreeId") for t in fast["terminals"]}
    sessions = {}
    for w in fast["worktrees"]:
        if w.get("isArchived"):
            continue
        agents = [agent_row(a) for a in w.get("agents") or []]
        terms = terms_by_wt.get(w["worktreeId"], [])
        if w.get("isMainWorktree") and not agents and not cfg.get("include_main_worktrees"):
            continue
        sessions[w["worktreeId"]] = {
            "id": w["worktreeId"], "name": mask(w.get("displayName") or pathlib.Path(w.get("path", "")).name, 120),
            "repo_name": w.get("repo"), "branch": short_branch(w.get("branch")), "path": w.get("path"),
            "status": w.get("status"), "orca_unread": bool(w.get("unread")), "comment": mask(w.get("comment"), 200),
            # Orca's worktree time does not move while an agent in it works, so the agents' own times count too.
            "last_activity": max(filter(None, [ms_iso(w.get("lastActivityAt"))] + [a["updated"] for a in agents]), default=None),
            "orca_parent": w.get("parentWorktreeId"), "created": ms_iso(w.get("createdAt")),
            "linked_pr": w.get("linkedPR"), "agents": agents, "terminals": terms,
            "phase": phase_of(agents, terms), "kind": "task", "parent": None, "run": None, "dispatch": None,
            "dispatch_status": None, "task": None, "task_title": None}

    # Orchestration: a Run's coordinator worktree, found through the coordinator's live terminal.
    coord_of_run = {r["id"]: wt_of_handle.get(r.get("coordinator_handle")) for r in runs["runs"]}
    tasks = {t["id"]: t for t in runs["tasks"]}
    by_wt = {}
    for w in runs["workers"]:
        wt = (w.get("resource") or {}).get("worktreeId")
        if not wt:
            continue
        prev = by_wt.get(wt)
        live = w.get("dispatchStatus") not in SETTLED
        if not prev or (live and not prev[1]):
            by_wt[wt] = (w, live)

    def dispatcher(sid):
        """The on-screen coordinator whose Run dispatched this worktree, if another session is."""
        w = by_wt.get(sid, (None, False))[0]
        up = w and coord_of_run.get(w.get("runId"))
        return up if up in sessions and up != sid else None

    def orca_parent(s):
        """The parent set in Orca (`orca worktree set --parent-worktree`), e.g. by a lead that made its workers with
        `orca worktree create` rather than through a Run."""
        p = s["orca_parent"]
        return p if p in sessions else None

    # declared (declared_roles) names a cwd, and folder contexts share one: the session name picks among them.
    role_of = {}
    for cwd, name, role in declared:
        here = [sid for sid, s in sessions.items() if s["path"] == cwd]
        here = [sid for sid in here if sessions[sid]["name"] == mask(name, 120)] or here
        if len(here) == 1 and role_of.get(here[0]) != "orchestrator":
            role_of[here[0]] = role
    declared_top = [sid for sid, role in role_of.items() if role == "orchestrator"]

    root = cfg.get("root_worktree") if cfg.get("root_worktree") in sessions else None
    if not root and len(declared_top) == 1:
        root = declared_top[0]  # a top level without a Run of its own lost the root to a lead's newer Run
    if not root:
        # A lead started with worker-start opens a newer Run of its own, so the newest Run is not necessarily the
        # orchestrator's: take the newest Run's coordinator that no other coordinator dispatched, never a declared lead.
        coords = [coord_of_run[r["id"]] for r in sorted(runs["runs"], key=lambda r: r.get("updated_at") or "", reverse=True)
                  if coord_of_run.get(r["id"]) in sessions and role_of.get(coord_of_run[r["id"]]) != "lead"]
        root = next((c for c in coords if not dispatcher(c)), coords[0] if coords else None)

    for sid, s in sessions.items():
        w = by_wt.get(sid, (None, False))[0]
        if w:
            t = tasks.get(w.get("taskId")) or {}
            s.update(run=w.get("runId"), dispatch=w.get("dispatchId"), dispatch_status=w.get("dispatchStatus"),
                     task=w.get("taskId"), task_title=task_name(t),
                     dispatch_live=by_wt[sid][1], terminal=w.get("agentTerminalHandle"))
        if sid == root:
            s["kind"] = "orchestrator"
        elif sid in coord_of_run.values():
            s["kind"] = "orchestration"
            s["parent"] = dispatcher(sid) or orca_parent(s) or root
            s["run"] = next(rid for rid, wt in coord_of_run.items() if wt == sid)
        elif w and coord_of_run.get(w.get("runId")) in sessions:
            s["parent"] = coord_of_run[w["runId"]]
        else:
            s["parent"] = orca_parent(s) or root
            s["kind"] = "task" if w else "orchestration" if role_of.get(sid) == "lead" else "standalone"
    # Coordinators that dispatched each other would make a loop the tree never leaves; hang such a one on the root.
    for sid, s in sessions.items():
        seen, p = {sid}, s["parent"]
        while p and p not in seen:
            seen.add(p)
            p = sessions[p]["parent"]
        if p == sid:
            s["parent"] = root
    return sessions, root


# ---------------------------------------------------------------- card reports

# A brief's REPORT line (dispatch-card §2, orchestrate references/brief.md) names the file the card appends its
# milestones to, or says "this note": the brief itself. A line that only names `orca orchestration send` has no file.
REPORT_LINE_RE = re.compile(r"^\W{0,4}(?:REPORT|Report|보고|報告)\b(.*)$", re.M)
REPORT_PATH_RE = re.compile(r"(~?/[^\s`'\"()（）、，,]+\.md)\b")
THIS_NOTE_RE = re.compile(r"this (?:brief's )?note|this brief|이 노트|이 브리프|この(?:ノート|メモ|ブリーフ)", re.I)
NEXT_RE = re.compile(r"(?:다음|Next|次)\s*[:：]\s*(.+)")
BRIEF_HEAD, REPORT_TAIL = 32 * 1024, 8 * 1024
_reports = {}  # path -> (signature, parsed): a brief or report is read again only once it changed


def _sig(path):
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


def report_path(comment):
    """The report file a session's brief names, from the brief path in its Orca comment; None when it names none."""
    m = REPORT_PATH_RE.fullmatch((comment or "").strip())
    if not m:
        return None
    brief = pathlib.Path(m.group(1)).expanduser()
    sig = _sig(brief)
    hit = _reports.get(("brief", str(brief)))
    if hit and hit[0] == sig:
        return hit[1]
    try:
        with brief.open("rb") as f:
            head = f.read(BRIEF_HEAD).decode("utf-8", "replace")
    except OSError:
        return None
    out = None
    for line in REPORT_LINE_RE.findall(head):
        found = REPORT_PATH_RE.search(line)
        if found or THIS_NOTE_RE.search(line):
            out = pathlib.Path(found.group(1)).expanduser() if found else brief
            break
    _reports[("brief", str(brief))] = (sig, out)
    return out


def read_report(path):
    """{path, at, next, pr} from the tail of a report file: the newest milestone (the block from the last heading or
    top-level bullet), its `다음:`/`Next:`/`次:` line when it has one, and the last PR it links."""
    sig = _sig(path)
    if not sig:
        return None
    hit = _reports.get(str(path))
    if hit and hit[0] == sig:
        return hit[1]
    try:
        with path.open("rb") as f:
            f.seek(max(0, sig[0] - REPORT_TAIL))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = [l.rstrip() for l in tail.splitlines()[1 if sig[0] > REPORT_TAIL else 0:] if l.strip()]
    start = max((i for i, l in enumerate(lines) if l.startswith(("#", "- ", "* "))), default=0)
    block = lines[start:]
    nxt = next((m.group(1) for l in reversed(block) for m in [NEXT_RE.search(l)] if m), None)
    latest = re.sub(r"^[#*\-\s]+", "", block[0]) if block else ""
    refs = [m for m in PR_REF_RE.finditer(tail)]
    pr = refs and (pr_key(refs[-1][1], refs[-1][2]) if refs[-1][1] else pr_key(refs[-1][3], refs[-1][4]))
    out = {"path": str(path), "at": iso(dt.datetime.fromtimestamp(sig[1] / 1e9, dt.timezone.utc)), "size": sig[0],
           "next": mask(" ".join((nxt or latest).split()), 160) or None, "pr": pr or None}
    _reports[str(path)] = (sig, out)
    return out


def report_of(comment):
    p = report_path(comment)
    return read_report(p) if p else None


# ---------------------------------------------------------------- inbox classification

RULES = [  # (type, regex) on the end of a finished turn; the first match wins
    ("verify_failed", re.compile(r"(동작\s*확인|검증|verif|E2E|smoke|테스트)[^\n]{0,80}(실패|failed|fail\b|❌|깨졌|안\s*됨)"
                                 r"|(실패|failed|❌)[^\n]{0,60}(동작\s*확인|검증|verif|E2E)", re.I)),
    ("login", re.compile(r"(로그인|login|log in|sign in|재인증|인증해|authori[sz]e|SSO|OAuth)[^\n]{0,80}"
                         r"(해\s*주세요|필요|부탁|please|required|열어)", re.I)),
    ("run_command", re.compile(r"(^|\s)`?!\s?[a-z][\w.-]+ |직접\s*(실행|입력|쳐)|실행해\s*주세요|run (this|the following)"
                               r"|터미널에서[^\n]{0,40}(실행|입력)", re.I | re.M)),
    ("approval", re.compile(r"(승인|괜찮을까요|진행할까요|진행해도\s*될까요|할까요\?|OK\?|확인\s*부탁|결정해\s*주세요|골라\s*주세요"
                            r"|進めてよい|よろしいですか|approve|shall I|should I)", re.I)),
    ("verify_ok", re.compile(r"(동작\s*확인|검증|verif|E2E)[^\n]{0,60}(완료|통과|passed|green|✅|성공)", re.I)),
]


def classify(text):
    tail = (text or "")[-1500:]
    for kind, rx in RULES:
        if rx.search(tail):
            return kind
    return "fyi"


def task_name(t):
    """An Orca task's name. Orca cuts display_name and task_title to a head and "...": the spec's first line, which
    that head starts, is the whole name."""
    name = t.get("display_name") or t.get("task_title")
    head = next((l.strip() for l in (t.get("spec") or "").splitlines() if l.strip()), "")
    if name and name.endswith("...") and head.startswith(name[:-3].rstrip()):
        name = head
    return mask(name)


def first_line(text, limit=160):
    for line in (text or "").splitlines():
        line = line.strip(" #*>-\t")
        if line:
            return mask(line, limit)
    return ""


# ---------------------------------------------------------------- permission prompts (recorded by hooks/permission.py)

QUESTION_RE = re.compile(r"Do you want to .+\?")
OPTION_RE = re.compile(r"(?:❯\s*)?(\d)\.\s+(.+)")
RULE_RE = re.compile(r"─{10,}")
PROMPT_KEEP_S = 86400


def parse_dialog(lines):
    """The permission dialog at the bottom of a rendered Claude Code screen, or None. Only the shape measured on
    2.1.282 counts: a rule, the request, "Do you want to …?", numbered options, and "Esc to cancel …" as the last
    line. A question list (AskUserQuestion) or the folder trust dialog has no such question and does not match."""
    body = [l.replace("\xa0", " ").strip() for l in lines]
    while body and not body[-1]:
        body.pop()
    if not body or not body[-1].startswith("Esc to cancel"):
        return None
    q = next((i for i in range(len(body) - 2, -1, -1) if QUESTION_RE.fullmatch(body[i])), None)
    if q is None:
        return None
    options = []
    for line in filter(None, body[q + 1:-1]):
        m = OPTION_RE.fullmatch(line)
        if m:
            options.append([m.group(1), m.group(2)])
        elif options:  # a long label wraps onto the next line
            options[-1][1] += " " + line
        else:
            return None
    if not options:
        return None
    top = max((i for i in range(q) if RULE_RE.fullmatch(body[i])), default=-1)
    return {"question": body[q], "request": [l for l in body[top + 1:q] if l], "lines": body[top + 1:],
            # "Yes, and switch to auto mode · auto mode handles these prompts for you": the label is before " · "
            "options": [(d, label.split(" · ")[0].strip()) for d, label in options]}


def prompt_needles(rec):
    """What the dialog must show of the recorded request: the command, the file name, the URL host, or else any
    string argument (an MCP tool's dialog lists them)."""
    inp = rec.get("input") or {}
    if inp.get("command"):
        return [inp["command"]]
    if inp.get("file_path") or inp.get("notebook_path"):
        return [pathlib.PurePath(inp.get("file_path") or inp["notebook_path"]).name]
    if inp.get("url"):
        return [re.sub(r"^\w+://([^/]+).*", r"\1", inp["url"])]
    return [v for v in inp.values() if v]


def prompt_verdict(lines, rec):
    """(dialog, None) when the screen shows the permission dialog for the recorded request, else (None, reason)."""
    d = parse_dialog(lines)
    if not d:
        return None, "no permission dialog on screen"
    # Whitespace is dropped on both sides: the dialog wraps a long command wherever the terminal width falls.
    shown = "".join("".join(d["request"] + [d["question"]]).split())
    for n in prompt_needles(rec):
        n = "".join(n.split())[:40]
        if len(n) >= 3 and n in shown:
            return d, None
    return None, "the dialog on screen is not the recorded request"


def option_for(dialog, action):
    """The digit that answers this one request: the bare "Yes" to approve, "No" to deny. An option that saves a
    rule or changes the mode ("don't ask again", "always allow", "switch to auto mode") is never chosen."""
    want = (lambda l: l == "Yes") if action == "approve" else (lambda l: l == "No" or l.startswith("No,"))
    return next((d for d, label in dialog["options"] if want(label)), None)


def prompt_records():
    """{terminal handle: record} from prompts/, dropping records older than a day."""
    out, cutoff = {}, iso(utcnow() - dt.timedelta(seconds=PROMPT_KEEP_S))
    for path in (state_dir() / "prompts").glob("*.json"):
        rec = read_json(path)
        if not isinstance(rec, dict) or (rec.get("at") or "") < cutoff:
            path.unlink(missing_ok=True)
        elif rec.get("handle") == path.stem:
            out[path.stem] = rec
    return out


def read_screen(handle):
    t = orca("terminal", "read", "--terminal", handle, "--screen", timeout=10).get("terminal") or {}
    return (t.get("tail") or []) if t.get("source") in (None, "screen") else []


# ---------------------------------------------------------------- PRs

# The probe carries everything the PR page shows except reviewers and threads, and its signature covers the fields
# that change without moving updatedAt (merge state after a base push, CodeRabbit's status).
PROBE = ("number state isDraft updatedAt createdAt mergedAt headRefOid url title mergeStateStatus reviewDecision"
         " baseRefName headRefName mergeCommit{oid}"
         " author{login} commits(last:1){nodes{commit{statusCheckRollup{state}"
         ' status{context(name:"CodeRabbit"){state description}}}}}')
DETAIL = PROBE + (
    " reviewRequests(first:20){nodes{requestedReviewer{__typename ... on User{login avatarUrl} ... on Team{slug}"
    " ... on Bot{login avatarUrl}}}}"
    " latestOpinionatedReviews(first:20){nodes{state submittedAt author{__typename login avatarUrl} commit{oid}}}"
    " reviews(last:30){nodes{id state submittedAt author{__typename login avatarUrl}}}"
    " reviewThreads(first:100){totalCount nodes{id isResolved comments(last:1){nodes{id createdAt url"
    " author{__typename login}}}}}")


def pr_key(repo, number):
    return f"{repo}#{number}"


# A bare #n is left out: "step #2" or a PR in another repository would read as a PR of the session's own. Only
# held_refs() reads one, and only when the number is a single known PR's.
PR_REF_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)|\b([\w.-]+/[\w.-]+)#(\d+)")
BARE_REF_RE = re.compile(r"(?<![\w/#])#(\d+)\b")


def pr_refs(text):
    return sorted({pr_key(m[1], m[2]) if m[1] else pr_key(m[3], m[4]) for m in PR_REF_RE.finditer(text or "")})


def held_refs(text, prs):
    """pr_refs(), plus each bare #n that exactly one PR in prs has as its number. Only for what a hold covers: a
    bare #n never lifts a hold, as a wrong match there would close work early."""
    by_n = {}
    for k in prs:
        by_n.setdefault(k.rsplit("#", 1)[-1], []).append(k)
    bare = {by_n[n][0] for n in BARE_REF_RE.findall(PR_REF_RE.sub("", text or "")) if len(by_n.get(n, ())) == 1}
    return sorted(set(pr_refs(text)) | bare)


def board_prs():
    """The PR keys board.json's rows name, and every full owner/repo#N in its doing, later, todo and owner_todo text and in
    the external inbox."""
    b = read_json(state_dir() / "board.json", {}) or {}
    text = [f"{d.get('title')} {d.get('next')}" for d in b.get("doing") or []]
    text += [f"{l.get('title')} {l.get('trigger')}" for l in b.get("later") or []] + list(b.get("owner_todo") or [])
    text += [f"{t.get('title')} {t.get('after') or ''}" for t in b.get("todo") or []]
    text += [f"{i.get('title')} {i.get('url')} {i.get('command')}" for i in external_items({})]
    return ({r.get("pr") for r in (b.get("rows") or {}).values() if isinstance(r, dict)} | set(pr_refs(" ".join(text)))) - {None}


def program_prs(now):
    """The PR keys named in the ledgers of the programs the stage table shows (stage_auto.py)."""
    import stage_auto  # beside this file; it imports this module
    return {pr_key(cfg["repo"], e["pr"]) for cfg, events in stage_auto.load_programs()
            if cfg.get("repo") and stage_auto.recent(cfg, events, now) for e in events if e.get("pr") is not None}


def pr_query(keys, body):
    parts = []
    for i, k in enumerate(keys):
        repo, n = k.split("#")
        owner, name = repo.split("/")
        parts.append(f"p{i}: repository(owner:{gql_str(owner)},name:{gql_str(name)})"
                     f"{{pullRequest(number:{int(n)}){{{body}}}}}")
    return "query{" + " ".join(parts) + " rateLimit{cost remaining}}"


def discover_query(pairs):
    parts = []
    for i, (repo, branch) in enumerate(pairs):
        owner, name = repo.split("/")
        parts.append(f"b{i}: repository(owner:{gql_str(owner)},name:{gql_str(name)}){{ref(qualifiedName:"
                     f"{gql_str('refs/heads/' + branch)}){{associatedPullRequests(first:3,orderBy:{{field:UPDATED_AT,"
                     f"direction:DESC}}){{nodes{{number state}}}}}}}}")
    return "query{" + " ".join(parts) + " rateLimit{cost remaining}}"


def search_query(q, after=None):
    """One page of a PR search: each PR's key and updated time. Probe fields on a hundred search nodes made GitHub
    time out (HTTP 502), so the PRs are probed like any other, when due or when their updated time moved."""
    cursor = f",after:{gql_str(after)}" if after else ""
    return (f"query{{s: search(query:{gql_str(q)},type:ISSUE,first:100{cursor}){{pageInfo{{hasNextPage endCursor}}"
            "nodes{... on PullRequest{repository{nameWithOwner} number updatedAt}}} rateLimit{cost remaining}}")


def head_commit(pr):
    nodes = (pr.get("commits") or {}).get("nodes") or []
    return (nodes[0] if nodes else {}).get("commit") or {}


def rollup(pr):
    st = (head_commit(pr).get("statusCheckRollup") or {}).get("state")
    return {"SUCCESS": "success", "FAILURE": "failure", "ERROR": "failure", "PENDING": "pending",
            "EXPECTED": "pending"}.get(st)


def coderabbit(pr):
    """CodeRabbit's commit status: "in_progress", "reviewed", "rate_limited", "skipped", "failed" or None (no status)."""
    c = (head_commit(pr).get("status") or {}).get("context") or {}
    d = (c.get("description") or "").lower()
    if not c:
        return None
    if "rate limit" in d:
        return "rate_limited"
    if "skip" in d:
        return "skipped"
    if c.get("state") in ("PENDING", "EXPECTED"):
        return "in_progress"
    return "reviewed" if c.get("state") == "SUCCESS" else "failed"


def probe_sig(pr):
    return [pr.get("state"), pr.get("updatedAt"), pr.get("headRefOid"), rollup(pr), pr.get("mergeStateStatus"),
            coderabbit(pr), pr.get("isDraft"), pr.get("reviewDecision"), pr.get("baseRefName"), pr.get("headRefName")]


def is_bot(author):
    a = author or {}
    return a.get("__typename") == "Bot" or str(a.get("login", "")).endswith("[bot]")


def shape_pr(repo, pr, me):
    """What the page and the event diff need from one PR's detail."""
    head = pr.get("headRefOid")
    reviewers = {}
    for n in (pr.get("reviewRequests") or {}).get("nodes") or []:
        r = n.get("requestedReviewer") or {}
        login = r.get("login") or (f"team:{r['slug']}" if r.get("slug") else None)
        if login:
            reviewers[login] = {"login": login, "avatar": r.get("avatarUrl"), "requested": True, "state": None,
                                "at": None, "stale": False, "team": bool(r.get("slug"))}
    for n in (pr.get("latestOpinionatedReviews") or {}).get("nodes") or []:
        a = n.get("author") or {}
        if not a.get("login") or a.get("login") == me:
            continue
        r = reviewers.setdefault(a["login"], {"login": a["login"], "avatar": a.get("avatarUrl"), "requested": False,
                                              "team": False})
        r.update(state=n.get("state"), at=n.get("submittedAt"), avatar=r.get("avatar") or a.get("avatarUrl"),
                 stale=n.get("state") == "APPROVED" and (n.get("commit") or {}).get("oid") != head,
                 bot=is_bot(a))
    for n in (pr.get("reviews") or {}).get("nodes") or []:  # commented-only reviewers never show in "latest"
        a = n.get("author") or {}
        if a.get("login") and a["login"] != me and a["login"] not in reviewers and n.get("state") == "COMMENTED":
            reviewers[a["login"]] = {"login": a["login"], "avatar": a.get("avatarUrl"), "requested": False,
                                     "state": "COMMENTED", "at": n.get("submittedAt"), "stale": False, "team": False,
                                     "bot": is_bot(a)}
    threads = []
    for t in (pr.get("reviewThreads") or {}).get("nodes") or []:
        c = ((t.get("comments") or {}).get("nodes") or [{}])[-1]
        threads.append({"id": t.get("id"), "resolved": bool(t.get("isResolved")), "last_id": c.get("id"),
                        "last_at": c.get("createdAt"), "last_url": c.get("url"),
                        "last_by": (c.get("author") or {}).get("login"), "last_bot": is_bot(c.get("author"))})
    for r in reviewers.values():
        r["status"] = edge_status(r)
    return {"key": pr_key(repo, pr["number"]), "repo": repo, "number": pr["number"], "url": pr.get("url"),
            "title": mask(pr.get("title"), 200), "state": pr.get("state"), "draft": bool(pr.get("isDraft")),
            "updated": pr.get("updatedAt"), "head": head, "ci": rollup(pr), "decision": pr.get("reviewDecision"),
            "created": pr.get("createdAt"), "merged_at": pr.get("mergedAt"), "merge_state": pr.get("mergeStateStatus"),
            "base": pr.get("baseRefName"), "merge_sha": (pr.get("mergeCommit") or {}).get("oid"), "branch": pr.get("headRefName"),
            "coderabbit": coderabbit(pr),
            "author": (pr.get("author") or {}).get("login"), "reviewers": sorted(reviewers.values(), key=lambda r: r["login"]),
            "threads": threads, "reviews": [{"id": n.get("id"), "state": n.get("state"), "at": n.get("submittedAt"),
                                             "by": (n.get("author") or {}).get("login"),
                                             "bot": is_bot(n.get("author"))}
                                            for n in (pr.get("reviews") or {}).get("nodes") or []]}


def edge_status(r):
    """The reviewer edge's colour class. Slack evidence is not read yet, so "requested" is one state."""
    if r.get("state") == "APPROVED":
        return "approved_stale" if r.get("stale") else "approved"
    if r.get("state") == "CHANGES_REQUESTED":
        return "changes_requested"
    if r.get("state") == "COMMENTED":
        return "commented"
    return "requested" if r.get("requested") else "none"


def open_threads(p, me):
    return [t for t in p["threads"] if not t["resolved"] and t["last_by"] and t["last_by"] != me]


def pr_items(p, me, cfg):
    """Live inbox items for one PR: open while the condition holds."""
    if p["state"] != "OPEN":
        return []
    out, bots = [], cfg.get("bots_in_inbox", False)
    base = {"pr": p["key"], "url": p["url"]}
    if any(r.get("state") == "CHANGES_REQUESTED" for r in p["reviewers"]):
        who = ", ".join(r["login"] for r in p["reviewers"] if r.get("state") == "CHANGES_REQUESTED")
        out.append({**base, "type": "changes_requested", "title": f"Changes requested: {who}"})
    threads = [t for t in open_threads(p, me) if bots or not t["last_bot"]]
    if threads:
        out.append({**base, "type": "review_comment_received", "title": f"Open review threads: {len(threads)}",
                    "url": threads[-1]["last_url"] or p["url"]})
    if p["ci"] == "failure":
        out.append({**base, "type": "ci_failed", "title": "CI failed"})
    stale = [r["login"] for r in p["reviewers"] if r.get("stale")]
    fresh = [r for r in p["reviewers"] if r.get("state") == "APPROVED" and not r.get("stale")]
    if stale and not fresh:
        out.append({**base, "type": "approval_stale", "title": f"Approved on an older commit: {', '.join(stale)}"})
    if fresh and p["ci"] in ("success", None) and not p["draft"] and p["decision"] in ("APPROVED", None):
        out.append({**base, "type": "ready_to_merge", "title": "Approved, CI green: waiting on a merge call"})
    return out


def pr_events(prev, cur, me):
    """Events between two detail snapshots of one PR (prev may be None: then only the state is recorded)."""
    if prev is None:
        return []
    ev = []
    add = lambda kind, sid, actor=None, url=None: ev.append({"kind": kind, "source": sid, "actor": actor,
                                                              "url": url or cur["url"]})
    seen = {r["id"] for r in prev.get("reviews", [])}
    for r in cur["reviews"]:
        if r["id"] in seen or r["by"] == me:
            continue
        kind = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested", "COMMENTED": "review_comment"}.get(r["state"])
        if kind:
            add(kind, r["id"], r["by"])
    known = {t["id"]: t for t in prev.get("threads", [])}
    for t in cur["threads"]:
        old = known.get(t["id"], {})
        if t["last_id"] and t["last_id"] != old.get("last_id") and t["last_by"] != me:
            add("review_comment", t["last_id"], t["last_by"], t["last_url"])
    req_prev = {r["login"] for r in prev.get("reviewers", []) if r.get("requested")}
    req_cur = {r["login"] for r in cur["reviewers"] if r.get("requested")}
    for login in sorted(req_cur - req_prev):
        add("review_requested", f"req:{login}:{cur['updated']}", login)
    if prev.get("head") and cur["head"] != prev["head"]:
        add("head_pushed", cur["head"])
        if any(r.get("state") == "APPROVED" for r in prev.get("reviewers", [])):
            add("approval_stale", f"stale:{cur['head']}")
    if cur["ci"] == "failure" and prev.get("ci") != "failure":
        add("checks_failed", f"ci:{cur['head']}:fail")
    if prev.get("ci") == "failure" and cur["ci"] == "success":
        add("checks_recovered", f"ci:{cur['head']}:ok")
    if cur["state"] != prev.get("state") and cur["state"] in ("MERGED", "CLOSED"):
        add(cur["state"].lower(), f"state:{cur['state']}")
    return ev


def tier(p, session_phase, now):
    if p["state"] != "OPEN":
        return "done"
    upd = parse(p.get("updated"))
    if (p.get("ci") == "pending" or p.get("decision") == "APPROVED" or session_phase == "working"
            or (upd and now - upd < dt.timedelta(hours=2))):
        return "hot"
    return "warm" if upd and now - upd < dt.timedelta(hours=24) else "cold"


# ---------------------------------------------------------------- the collector

class Fleet:
    """Holds the caches between ticks and writes state.json. One instance per server process."""

    def __init__(self, fetch_fast=fetch_fast, fetch_runs=fetch_runs, graphql=graphql, now=utcnow, read_screen=read_screen,
                 fetch_automations=fetch_automations, fetch_monitors=fetch_monitors, fetch_sent=fetch_sent, ps_table=ps_table,
                 declared_roles=declared_roles):
        self.fetch_fast, self.fetch_runs, self.graphql, self.now = fetch_fast, fetch_runs, graphql, now
        self.ps_table = ps_table  # read once a runs cycle for both monitors and sent
        self.fetch_automations, self.automations = fetch_automations, []
        self.fetch_monitors, self.monitors = fetch_monitors, []
        self.fetch_sent, self.sent = fetch_sent, []  # served by /api/fleet/sent with the `orch sent` log, not in state.json
        self.declared_roles, self.declared = declared_roles, []
        self.read_screen = read_screen
        self.lock = threading.Lock()
        d = state_dir()
        self.fast, self.runs = None, {"runs": [], "workers": [], "tasks": [], "gates": []}
        self.sources = {k: {"ok": None, "updated_at": None, "error": None, "warning": None} for k in ("orca", "runs", "github", "automations", "monitors", "sent")}
        self.repos = {}
        saved = read_json(d / "prs.json", {}) or {}
        self.prs = saved.get("prs", {})           # key -> {"probe": sig, "detail": shaped, "probed_at": iso}
        self.branch_pr = saved.get("branch_pr", {})  # "repo@branch" -> {"key": key|None, "at": iso}
        self.me = saved.get("me")
        self.mem = read_json(d / "memory.json", {}) or {}  # prompt hashes, last states, sticky inbox
        self.last = {"runs": 0.0, "prs": 0.0}
        self.rate = None  # GraphQL points left, from the last query's rateLimit
        self.state, self.owner_of, self.saved = None, {}, {}
        self.checks = []  # selfcheck.SelfCheck's items: where this state disagreed with a fresh read
        self.events = collections.deque(read_lines(d / "events.jsonl", EVENTS_KEEP), maxlen=EVENTS_KEEP)

    # -- persistence

    def save(self, **files):
        """Write each {name: text} whose text differs from the last write (a tick usually changes nothing)."""
        for name, text in files.items():
            if self.saved.get(name) != text:
                write_atomic(state_dir() / name, text)
                self.saved[name] = text

    def event(self, row):
        path = state_dir() / "events.jsonl"
        rotate(path)
        append_line(path, row)
        self.events.append(row)

    def ok(self, name, err=None, warning=None):
        s = self.sources[name]
        s["ok"], s["error"], s["warning"] = err is None, err, warning
        if err is None:
            s["updated_at"] = iso(self.now())

    # -- ticks

    def tick(self, force=False, viewed=False):
        """Refresh whatever is due and rewrite state.json. Returns the new state. force: refresh runs and probe every
        PR now (the page was just opened); viewed: a page is open, so every OPEN PR is probed each PR tick."""
        with self.lock:
            mono = time.monotonic()
            try:
                self.fast = self.fetch_fast()
                self.ok("orca")
            except SourceError as e:
                self.ok("orca", str(e))
            if force or not self.last["runs"] or mono - self.last["runs"] >= RUNS_EVERY:
                try:
                    self.runs = self.fetch_runs()
                    self.ok("runs")
                except SourceError as e:
                    self.ok("runs", str(e))
                try:
                    self.automations = self.fetch_automations()
                    self.ok("automations")
                except SourceError as e:
                    self.ok("automations", str(e))
                try:
                    procs = self.ps_table()
                except SourceError as e:
                    self.ok("monitors", str(e))
                    self.ok("sent", str(e))
                else:
                    try:
                        warn = []
                        self.monitors = self.fetch_monitors(procs=procs, warn=warn)
                        self.ok("monitors", warning="; ".join(warn) or None)
                    except SourceError as e:
                        self.ok("monitors", str(e))
                    try:
                        self.sent = self.fetch_sent(procs=procs)
                        self.ok("sent")
                    except SourceError as e:
                        self.ok("sent", str(e))
                    self.declared = self.declared_roles(procs)
                self.last["runs"] = mono
            if self.fast is None:
                return self.state
            cfg = config()
            sessions, root = build_sessions(self.fast, self.runs, cfg, self.declared)
            for s in sessions.values():
                s["gh_repo"] = repo_of(s["path"], self.repos) if s.get("path") else None
                s["project"] = project_of(s.get("path"), s.get("repo_name"), s["gh_repo"])
                s["report"] = report_of(s.get("comment"))
            # A session whose phase changed (a turn ended, a worker started) may just have merged, pushed or
            # answered a review: its PRs are probed now rather than when their tier comes round.
            phases = self.mem.setdefault("session_phase", {})
            moved = {sid for sid, s in sessions.items() if phases.get(sid) not in (None, s["phase"])}
            self.mem["session_phase"] = {sid: s["phase"] for sid, s in sessions.items()}
            touched = {k for k, sid in self.owner_of.items() if sid in moved}
            low = self.rate is not None and self.rate < RATE_FLOOR and mono - self.last["prs"] < 900  # one try per 15 min
            # 0.0 is "never": the monotonic clock's origin is unspecified, and macOS's 3.9 starts it near 0.
            if force or touched or (not self.last["prs"] or mono - self.last["prs"] >= (PR_TICK_VIEWED if viewed
                                                                                          else PR_TICK)) and not low:
                try:
                    self.refresh_prs(sessions, cfg, force=force, viewed=viewed, now_due=touched)
                    self.ok("github")
                except SourceError as e:
                    self.ok("github", str(e))
                self.last["prs"] = mono
            self.state = self.compose(sessions, root, cfg)
            self.save(**{"state.json": json.dumps(self.state, ensure_ascii=False),
                         "prs.json": json.dumps({"prs": self.prs, "branch_pr": self.branch_pr, "me": self.me}),
                         "memory.json": json.dumps(self.mem, ensure_ascii=False)})
            self.stage_rows()
            return self.state

    def stage_rows(self):
        """The stage table's generated rows, from this state and the program ledgers (stage_auto.py)."""
        try:
            import stage_auto  # beside this file; it imports this module
            stage_auto.refresh(self.state)
            self.mem.pop("stage_auto_error", None)
        except Exception as e:
            self.stage_auto_failed(e)

    def stage_auto_failed(self, e):
        """A program file it cannot read must not take the dashboard down with it: kept once, as the stage table's."""
        if self.mem.get("stage_auto_error") != (err := mask(repr(e), 200)):
            self.mem["stage_auto_error"] = err
            self.event({"at": iso(utcnow()), "kind": "stage_auto_error", "text": err})

    def refresh_prs(self, sessions, cfg, force=False, viewed=False, now_due=()):
        now = self.now()
        if not self.me:
            try:
                self.me = run_json(["gh", "api", "user"]).get("login")
            except SourceError:
                self.me = None
        # 1. Which PR does each session's branch have (cached; a branch without one is re-asked every 10 min).
        ask, owner_of = [], {}
        for s in sessions.values():
            repo = s["gh_repo"]
            if not repo:
                continue
            if isinstance(s.get("linked_pr"), dict) and s["linked_pr"].get("number"):
                k = pr_key(repo, s["linked_pr"]["number"])
                owner_of.setdefault(k, s["id"])
            if not s["branch"] or s["branch"] in BRANCH_SKIP:
                continue
            bk = f"{repo}@{s['branch']}"
            hit = self.branch_pr.get(bk)
            # A branch whose PR was merged or closed may get a new one: it is asked again like a branch without one.
            known_open = hit and hit.get("key") and ((self.prs.get(hit["key"]) or {}).get("detail") or {"state": "OPEN"})["state"] == "OPEN"
            if not hit or (not known_open and now - (parse(hit.get("at")) or now) >= dt.timedelta(seconds=DISCOVER_EVERY)):
                ask.append((repo, s["branch"]))
            elif hit.get("key"):
                owner_of.setdefault(hit["key"], s["id"])
        errors = []
        for i in range(0, len(ask), 40):
            chunk = ask[i:i + 40]
            data, errs = self.graphql(discover_query(chunk))
            errors += errs
            self.note_rate(data)
            for j, (repo, branch) in enumerate(chunk):
                nodes = ((((data.get(f"b{j}") or {}).get("ref") or {}).get("associatedPullRequests") or {})
                         .get("nodes") or [])
                open_first = sorted(nodes, key=lambda n: n.get("state") != "OPEN")
                key = pr_key(repo, open_first[0]["number"]) if open_first else None
                self.branch_pr[f"{repo}@{branch}"] = {"key": key, "at": iso(now)}
                if key:
                    owner_of.setdefault(key, next(s["id"] for s in sessions.values()
                                                  if s.get("gh_repo") == repo and s["branch"] == branch))
        self.owner_of = owner_of
        # 2. The search: every PR of mine open, or merged in the last day, whether or not a live worktree still has
        # its branch. A failed search keeps what the last one found rather than emptying the page.
        swept, searched = {}, False
        if self.me:
            try:
                for q in (f"is:pr is:open archived:false author:{self.me}",
                          f"is:pr is:merged author:{self.me} merged:>={iso(now - dt.timedelta(hours=24))}"):
                    after = None
                    for _ in range(SEARCH_PAGES):
                        data, errs = self.graphql(search_query(q, after))
                        if errs or not (data or {}).get("s"):
                            raise SourceError("; ".join(errs) or "search returned no data")
                        self.note_rate(data)
                        swept |= {pr_key(n["repository"]["nameWithOwner"], n["number"]): n.get("updatedAt")
                                  for n in data["s"].get("nodes") or [] if n.get("number") and n.get("repository")}
                        page = data["s"].get("pageInfo") or {}
                        if not page.get("hasNextPage"):
                            break
                        after = page.get("endCursor")
                searched = True
            except SourceError as e:
                errors.append(f"search: {e}")
        live = {f"{s['gh_repo']}@{s['branch']}" for s in sessions.values()}
        # A PR the board or the inbox names stays known after the search stops finding it, so a row about it shows
        # its state, not "?", and tidy.py can close what it carried.
        # So does a PR a program's ledger names: the stage table reads its branch and merge commit from here.
        try:
            ledger_prs = program_prs(now)
        except Exception as e:
            ledger_prs = set()
            self.stage_auto_failed(e)
        named = (board_prs() | ledger_prs) - set(owner_of) - set(swept)
        self.prs = {k: v for k, v in self.prs.items() if k in owner_of or k in swept or k in named or not searched}
        self.branch_pr = {k: v for k, v in self.branch_pr.items() if k in live}
        # 3. Probe every PR whose tier says it is due, or that the search saw updated; 4. detail for the ones that
        # changed. An open page puts a session's PRs on the hot tier and opening it probes them at once; the rest of
        # mine (a hundred or more, 5 s per 25) keep their tier, which is there for CI moves that leave updatedAt alone.
        due = []
        for k in list(owner_of) + [k for k in swept if k not in owner_of] + sorted(named):
            sid, entry = owner_of.get(k), self.prs.get(k)
            det = (entry or {}).get("detail")
            # A detail read before "base" or "branch" was collected is read again now, so a merged PR does not wait a
            # day for it.
            if det and "base" in det and "branch" in det and k not in now_due and swept.get(k, det["updated"]) == det["updated"]:
                t = tier(det, sessions.get(sid, {}).get("phase"), now)
                last = parse(entry.get("probed_at"))
                every = TIER_EVERY["hot"] if (viewed or force) and sid and t != "done" else TIER_EVERY[t]
                if last and (now - last).total_seconds() < every - 5 and not (force and sid and t != "done"):
                    continue
            due.append(k)
        changed, new_sig = [], {}
        for i in range(0, len(due), 25):  # 50 probes to a query took over 10 s and at times timed out
            chunk = due[i:i + 25]
            try:
                data, errs = self.graphql(pr_query(chunk, PROBE))
            except SourceError as e:  # one slow chunk must not cost the others their read
                errors.append(str(e))
                continue
            errors += errs
            self.note_rate(data)
            for j, k in enumerate(chunk):
                pr = (data.get(f"p{j}") or {}).get("pullRequest")
                if not pr:
                    continue
                entry = self.prs.setdefault(k, {})
                sig = probe_sig(pr)
                if sig != entry.get("probe") or not entry.get("detail"):
                    changed.append(k)
                    new_sig[k] = sig  # kept only once the full read lands: a failed read is retried next probe
                entry["probed_at"] = iso(now)
        for i in range(0, len(changed), 25):
            chunk = changed[i:i + 25]
            try:
                data, errs = self.graphql(pr_query(chunk, DETAIL))
            except SourceError as e:
                errors.append(str(e))
                continue
            errors += errs
            self.note_rate(data)
            for j, k in enumerate(chunk):
                pr = (data.get(f"p{j}") or {}).get("pullRequest")
                if not pr:
                    continue
                cur = shape_pr(k.split("#")[0], pr, self.me)
                prev = self.prs[k].get("detail")
                # Events (the timeline, pr-events.jsonl) stay with the PRs a session owns.
                for e in pr_events(prev, cur, self.me) if k in owner_of else ():
                    self.emit_pr_event(cur, e, sessions.get(owner_of.get(k)), now)
                self.prs[k]["detail"], self.prs[k]["probe"] = cur, new_sig[k]
                self.fetch_avatars(cur)
        if errors:
            raise SourceError("; ".join(errors[:3]))

    def note_rate(self, data):
        rate = (data or {}).get("rateLimit") or {}
        if isinstance(rate.get("remaining"), int):
            self.rate = rate["remaining"]
            self.sources["github"]["remaining"] = rate["remaining"]

    def emit_pr_event(self, pr, e, session, now):
        owner, kind = None, None
        if session:
            term = next((t["handle"] for t in session["terminals"] if t.get("agent")), None)
            if session.get("dispatch") and session.get("dispatch_live"):
                owner, kind = session["dispatch"], "dispatch"
            elif session["kind"] == "standalone":
                owner, kind = term, "human_session"
            elif term:
                owner, kind = term, "terminal"
        row = {"v": 1, "id": digest(pr["key"], e["kind"], e["source"]), "at": iso(now), "repo": pr["repo"],
               "pr": pr["number"], "kind": e["kind"], "url": e["url"], "owner": owner, "owner_kind": kind,
               "actor": e.get("actor"), "checks": pr["ci"], "head": pr["head"]}
        path = state_dir() / "pr-events.jsonl"
        rotate(path)
        append_line(path, row)
        self.event({"at": row["at"], "kind": f"pr_{e['kind']}", "session": session and session["id"],
                    "pr": pr["key"], "actor": e.get("actor"), "url": e["url"],
                    "text": f"#{pr['number']} {pr['title'] or ''}" + (f" · {e['actor']}" if e.get("actor") else "")})

    def fetch_avatars(self, pr):
        d = state_dir() / "avatars"
        for r in pr["reviewers"]:
            url, login = r.get("avatar"), r["login"]
            if not url or not LOGIN_RE.fullmatch(login) or (d / f"{login}.png").exists():
                continue
            if not url.startswith("https://avatars.githubusercontent.com/"):
                continue
            # curl, not urllib: it uses the system trust store, which a TLS-inspecting proxy's CA is added to.
            try:
                r = subprocess.run(["curl", "-sfL", "--max-time", "10", "--max-filesize", "524288",
                                    url + ("&" if "?" in url else "?") + "s=64"], capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if r.returncode == 0 and r.stdout:
                write_atomic(d / f"{login}.png", r.stdout)

    # -- composing the page's state

    def pending_prompt(self, s, agent, records):
        """Fields that make a waiting item a pending confirmation the page can answer: the agent's terminal has a
        recorded permission request, and its screen shows that request's dialog now."""
        terms = [t for t in s["terminals"] if t.get("agent") and t.get("connected") and t.get("writable")]
        t = next((t for t in terms if t["pane"] == agent["pane"]), terms[0] if len(terms) == 1 else None)
        rec = t and records.get(t["handle"])
        if not rec:
            return {}
        try:
            d, _ = prompt_verdict(self.read_screen(t["handle"]), rec)
        except SourceError:
            d = None
        if not d:
            return {}
        return {"type": "prompt", "title": f"{rec['tool']} · {d['question']}", "detail": mask(prompt_needles(rec)[0], 300),
                "handle": t["handle"], "prompt": rec["id"], "answers": [x for x in ("approve", "deny") if option_for(d, x)]}

    def resolve(self, item, rule, now):
        item.update(open=False, resolved=rule)
        self.event({"at": iso(now), "kind": "item_resolved", "session": item.get("session"), "text": f"{rule}: {item['title']}"})

    def compose(self, sessions, root, cfg):
        now, mem = self.now(), self.mem
        prompts, phases = mem.setdefault("prompts", {}), mem.setdefault("phases", {})
        sticky = mem.setdefault("sticky", {})
        items, records = [], prompt_records()
        # Prompts and finished turns, per agent pane.
        for s in sessions.values():
            last_prompt = None
            for a in s["agents"]:
                pane = a["pane"] or s["id"]
                h = digest(a["prompt"]) if a["prompt"] else None
                old = prompts.get(pane)
                if h and (not old or old["h"] != h):
                    # A prompt first seen at collector start has no known time; stamping it "now" would mark
                    # every open item as read on a fresh install.
                    prompts[pane] = {"h": h, "at": iso(now) if old else None}
                    if old:
                        self.event({"at": iso(now), "kind": "prompt", "session": s["id"], "text": mask(a["prompt"], 160)})
                if (prompts.get(pane) or {}).get("at"):
                    last_prompt = max(filter(None, [last_prompt, prompts[pane]["at"]]))
                prev_phase = phases.get(pane)
                if prev_phase and prev_phase != a["state"]:
                    if a["state"] == "done":
                        self.event({"at": iso(now), "kind": "turn_done", "session": s["id"], "text": first_line(a["last"])})
                phases[pane] = a["state"]
                if a["state"] == "done" and a["last"]:
                    k = f"msg:{pane}:{digest(a['last'])}"
                    kind = None if k in sticky else classify(a["last"])
                    if kind and kind != "fyi":
                        sticky[k] = {"key": k, "type": kind, "session": s["id"], "title": first_line(a["last"]),
                                     "detail": a["last"][-600:], "at": iso(now), "source": "turn", "open": kind != "verify_ok",
                                     "prs": pr_refs(a["last"])}
                        self.event({"at": iso(now), "kind": f"item_{kind}", "session": s["id"], "text": first_line(a["last"])})
                    # The agent has finished a later turn, so what an earlier one asked for has been dealt with or
                    # been asked again (and raised again) at the end of this one.
                    for it in sticky.values():
                        if it.get("open") and it["key"] != k and it["key"].startswith(f"msg:{pane}:"):
                            self.resolve(it, "superseded", now)
                # Someone has already replied in this pane: a prompt after the item, or a turn under way (items are
                # raised only on a finished turn, so any other state means a later one has begun).
                replied = (prompts.get(pane) or {}).get("at") or ""
                for it in sticky.values():
                    if (it.get("open") and it["key"].startswith(f"msg:{pane}:")
                            and (a["state"] in ("working", "waiting") or replied > (it.get("at") or ""))):
                        self.resolve(it, "answered", now)
                if a["state"] == "waiting":
                    items.append({"key": f"wait:{pane}:{a['since']}", "type": "permission", "session": s["id"],
                                  "title": "Waiting on an input or permission prompt", "detail": a["detail"], "at": a["since"] or iso(now),
                                  "source": "orca", **self.pending_prompt(s, a, records)})
            s["last_prompt_at"] = last_prompt
        if sessions:  # an empty list is more likely a bad read than every worktree gone at once
            for it in sticky.values():
                if it.get("open") and it.get("source") == "turn" and it.get("session") not in sessions:
                    self.resolve(it, "session_gone", now)
        # A session with no agent left has no one to answer its items. Only a read that found some agent can say so,
        # and only once it has held for AGENT_GONE_S: a single read can miss one worktree's agent.
        agentless = mem.setdefault("agentless", {})
        if any(s["agents"] for s in sessions.values()):
            for sid, s in sessions.items():
                if s["agents"]:
                    agentless.pop(sid, None)
                else:
                    agentless.setdefault(sid, iso(now))
            for sid in [sid for sid in agentless if sid not in sessions]:
                del agentless[sid]
        gone_before = iso(now - dt.timedelta(seconds=AGENT_GONE_S))
        for it in sticky.values():
            s = sessions.get(it.get("session"))
            if not (it.get("open") and it.get("source") == "turn" and s):
                continue
            refs = it["prs"] if "prs" in it else pr_refs(f"{it['title']}\n{it.get('detail') or ''}")
            if agentless.get(s["id"], gone_before) < gone_before:
                self.resolve(it, "agent_gone", now)
            elif refs and all(((self.prs.get(r) or {}).get("detail") or {}).get("state") in ("MERGED", "CLOSED") for r in refs):
                self.resolve(it, "prs_closed", now)
        # Orchestration mail asking a coordinator something: open until someone replies. Unread mail stays; mail the
        # coordinator acked (its inbox loop acks at once, before the human has answered) stays for a day.
        coord_handles = {t["handle"] for s in sessions.values() if s["kind"] in ("orchestrator", "orchestration")
                         for t in s["terminals"]}
        coord_runs = {r["id"] for r in self.runs["runs"] if r.get("coordinator_handle") in coord_handles}  # the root's too
        by_handle = {t["handle"]: s["id"] for s in sessions.values() for t in s["terminals"]}
        answered, day_ago = answered_mail(self.fast["messages"]) | superseded_mail(self.fast["messages"]), iso(now - dt.timedelta(hours=24))
        settled = {w.get("dispatchId") for w in self.runs["workers"] if w.get("dispatchStatus") in SETTLED} - {None}
        for m in self.fast["messages"]:
            to = m.get("to_handle") or ""
            to_coord = to in coord_handles or (to.startswith("run:") and to[4:] in coord_runs)
            if (to_coord and m.get("type") in ("question", "escalation", "decision_gate") and m["id"] not in answered
                    and mail_dispatch(m) not in settled and (not m.get("read") or (m.get("created_at") or "") >= day_ago)):
                items.append({"key": f"mail:{m['id']}", "type": "question", "session": by_handle.get(m.get("from_handle")),
                              "title": mask(m.get("subject"), 160), "detail": mask(m.get("body"), 600),
                              "at": m.get("created_at"), "source": "orca-mail"})
        items += decision_items(by_handle, root)
        # A task's project is the worktree its latest dispatch runs in (a released one is gone from Orca's list,
        # but its worktree id still carries the path).
        task_project = {}
        for w in sorted(self.runs["workers"], key=lambda w: w.get("dispatchId") or ""):
            wt = (w.get("resource") or {}).get("worktreeId") or ""
            ws = sessions.get(wt)
            task_project[w.get("taskId")] = ws["project"] if ws else project_of(wt.partition("::")[2] or None)
        for g in self.runs["gates"]:
            if g.get("status") not in ("resolved", "cancelled"):
                items.append({"key": f"gate:{g['id']}", "type": "approval", "session": root, "title": mask(g.get("question"), 160),
                              "project": task_project.get(g.get("task_id")),
                              "detail": ", ".join(map(str, g.get("options") or [])), "at": g.get("created_at"), "source": "orca-gate"})
        # PRs: every one found, with the session that owns it when one does. Only an owned PR raises items: the
        # search finds every PR of mine, and a PR no session works on is the PR page's to show, not the inbox's.
        prs = []
        for k, entry in self.prs.items():
            det, sid = entry.get("detail"), self.owner_of.get(k)
            if not det:
                continue
            prs.append({f: det.get(f) for f in ("key", "repo", "number", "url", "title", "state", "draft", "author", "ci",
                                                "decision", "merge_state", "coderabbit", "reviewers", "created", "updated",
                                                "merged_at", "merge_sha", "branch")}
                       | {"session": sid, "unresolved": sum(not t["resolved"] for t in det["threads"])})
            if not sid:
                continue
            for it in pr_items(det, self.me, cfg):
                items.append({**it, "key": f"pr:{k}:{it['type']}", "session": sid, "at": det["updated"], "source": "github"})
        # Skill-side items and sync state.
        terms = {t["handle"]: (s["id"], t["title"]) for s in sessions.values() for t in s["terminals"]}
        added, kept = external_items(), external_items(now)
        aged = [i for i in added if i not in kept]
        told = set(mem.get("external_aged") or ())
        for i in aged:
            if i["key"] not in told:
                self.event({"at": iso(now), "kind": "inbox_aged_out", "session": i.get("session"),
                            "text": mask(f"{i['title']} (added {EXTERNAL_KEEP_DAYS}+ days ago; orch-dash inbox list)", 200)})
        mem["external_aged"] = [i["key"] for i in aged]
        syncs, tries = sync_items(now, terms), cfg.get("reload_alert_tries", RELOAD_ALERT_TRIES)
        failed = {i["terminal"] for i in syncs if i["type"] == "reload_pending"}
        for h in [h for h in RELOAD_FAILS if h not in failed]:
            del RELOAD_FAILS[h]
        for h, title in (mem.get("reload_shown") or {}).items():
            if h not in failed:
                self.event({"at": iso(now), "kind": "item_resolved", "session": (terms.get(h) or (None,))[0],
                            "text": f"{'reload_delivered' if h in terms else 'reload_dropped'}: {title}"})
        syncs = [i for i in syncs if i["type"] != "reload_pending" or RELOAD_FAILS.get(i["terminal"], 0) >= tries]
        mem["reload_shown"] = {i["terminal"]: i["title"] for i in syncs if i["type"] == "reload_pending"}
        items += kept + syncs + list(self.checks)
        for it in sticky.values():
            if it.get("open"):
                items.append(it)
        dismissed, raw_keys = mem.setdefault("dismissed", {}), {it["key"] for it in items}
        items = [it if it.get("project") else {**it, "project": (sessions.get(it.get("session")) or {}).get("project")}
                 for it in items if it["key"] not in dismissed]
        import tidy
        held = held_prs(read_json(state_dir() / "board.json", {}) or {}, tidy.closed(),
                        {k: e["detail"] for k, e in self.prs.items() if e.get("detail")}, now,
                        tidy.cards_of(read_json(state_dir() / "state.json", {})))
        for i in items:
            if i.get("source") == "github" and i.get("pr") in held:
                i["held"] = held[i["pr"]]
        items.sort(key=needs_order)
        # A session's badge counts exactly the Needs you items on it, so the badges and the inbox always agree:
        # the badges plus the items that belong to no session are the inbox. No local "seen" mark hides any.
        for i in items:
            s = sessions.get(i.get("session"))
            i["missed"] = bool(s and i["type"] in STICKY_TYPES and s.get("last_prompt_at")
                               and (i.get("at") or "") < s["last_prompt_at"])
        for s in sessions.values():
            mine = [i for i in items if i.get("session") == s["id"]]
            s["unread"] = len(mine)
            s["missed"] = sum(i["missed"] for i in mine)
        # Keep the sticky store bounded: closed ones older than a week go.
        cutoff = iso(now - dt.timedelta(days=7))
        for k in [k for k, v in sticky.items() if not v.get("open") and (v.get("at") or "") < cutoff]:
            del sticky[k]
        for k in [k for k, at in dismissed.items() if at < cutoff and k not in raw_keys]:
            del dismissed[k]
        panes = {a["pane"] or s["id"] for s in sessions.values() for a in s["agents"]}
        for store in (prompts, phases):
            for k in [k for k in store if k not in panes]:
                del store[k]
        timeline = list(self.events) + tidy.timeline()
        timeline += [{"at": m.get("created_at"), "kind": f"mail_{m.get('type')}", "session": by_handle.get(m.get("from_handle")),
                      "text": mask(m.get("subject"), 160), "read": bool(m.get("read"))} for m in self.fast["messages"]
                     if m.get("type") != "heartbeat"]  # one every 5 min per worker: it would bury everything else
        timeline.sort(key=lambda e: e.get("at") or "", reverse=True)
        paths = sorted(((s["path"].rstrip("/"), sid) for sid, s in sessions.items() if s.get("path")), reverse=True)
        monitors = [{**m, "session": next((sid for p, sid in paths if m["cwd"] == p or m["cwd"].startswith(p + "/")), None)}
                    for m in self.monitors]
        import holds  # beside this file; it imports this module, so not at the top
        try:
            held = [{**h, "session": by_handle.get(h["by"])} for h in holds.current()]
        except (OSError, ValueError):
            held = []
        import acks, board, jobs, owner_requests
        asked, by_key, have = owner_requests.fold(owner_requests.read()), {p["key"]: p for p in prs}, acks.acked()
        try:
            written = []
            if rows := owner_requests.reconcile(asked, now, by_key, sessions, board.read()["rows"], by_handle,
                                                mem.setdefault("request_cards", {}), mem.setdefault("request_gone", {}),
                                                self.sources["orca"]["ok"]):
                written, asked = owner_requests.apply(rows)
                for r in written:
                    self.event({"at": iso(now), "kind": f"request_{r['ev']}", "session": by_handle.get(asked[r["id"]].get("handle")),
                                "text": mask(f"{r['id']}: {r.get('to') or r.get('evidence') or r.get('reason')}", 200)})
            n = collections.Counter(r["ev"] for r in written)
            jobs.record("requests-reconcile", n=len(written), now=now,
                        result=f"linked {n['link']} · done {n['done']} · dropped {n['drop']}")
        except Exception as e:  # a ledger it cannot settle must not take the dashboard down with it
            # one timeline line, not one every tick it keeps failing
            jobs.record("requests-reconcile", error=repr(e), event=self.event, kind="requests_error", now=now)
        return {"generated_at": iso(now), "root": root, "sessions": sorted(sessions.values(), key=lambda s: s.get("last_activity") or "", reverse=True),
                "runs": [{"id": r["id"], "objective": mask(r.get("objective"), 200), "updated_at": r.get("updated_at")} for r in self.runs["runs"]],
                "tasks": [{"id": t["id"], "run": t.get("run_id"), "title": task_name(t),
                           "project": task_project.get(t["id"]),
                           "status": t.get("status"), "deps": t.get("deps")} for t in self.runs["tasks"]],
                "prs": sorted(prs, key=lambda p: p.get("updated") or "", reverse=True), "items": items,
                "timeline": timeline[:300], "holds": held, "automations": self.automations, "monitors": monitors, "sources": self.sources, "me": self.me,
                "requests": {**owner_requests.summary(now, {k: q for k, q in asked.items() if f"req:{k}" not in have}),
                             "items": [{**q, "session": by_handle.get(q.get("handle")), "acked": f"req:{q['id']}" in have,
                                        "closed": [to for to in q["links"] if (pr := owner_requests.link_pr(to, by_key))
                                                   and pr[1].get("state") == "CLOSED"]}
                                       for q in asked.values() if owner_requests.visible(q, now)]},
                "counts": {t: sum(1 for i in items if i["type"] == t) for t in {i["type"] for i in items}}}


def answered_mail(messages):
    """Ids of messages someone else replied to. `orca orchestration reply` gives the reply the thread_id of what it
    answers: the message's own id, or the thread the message was already in (`ask` opens a thread on itself). A reply
    is newer than its message and the inbox lists newest first, so a message on the page has its reply there too."""
    by_thread = collections.defaultdict(list)
    for r in messages:
        if r.get("thread_id"):
            by_thread[r["thread_id"]].append(r)
    return {m["id"] for m in messages for t in {m["id"], m.get("thread_id")} - {None} for r in by_thread[t]
            if r["id"] != m["id"] and r.get("from_handle") != m.get("from_handle")
            and (r.get("created_at") or "") >= (m.get("created_at") or "")}


def superseded_mail(messages):
    """Ids of messages whose sender later reported to the same recipient outside their thread: it has moved past what
    it asked. A report inside the thread adds to the question, a heartbeat comes from a worker still blocked, and a
    newer question may sit beside the older one, so none of those count."""
    reports = collections.defaultdict(list)
    for r in messages:
        if r.get("type") in ("status", "worker_done"):
            reports[(r.get("from_handle"), r.get("to_handle"))].append(r)
    return {m["id"] for m in messages for r in reports[(m.get("from_handle"), m.get("to_handle"))]
            if (r.get("created_at") or "") > (m.get("created_at") or "")
            and (not r.get("thread_id") or r["thread_id"] not in (m["id"], m.get("thread_id")))}


def mail_dispatch(m):
    """The dispatch a worker's mail came from: `ask` and `send` put it in the payload, and `ask` also sends as
    dispatch:<id>. None for mail from a terminal outside any dispatch."""
    try:
        d = json.loads(m.get("payload") or "{}")
    except ValueError:
        d = None
    h = m.get("from_handle") or ""
    return (d.get("dispatchId") if isinstance(d, dict) else None) or (h[9:] if h.startswith("dispatch:") else None)


def decision_items(by_handle, root):
    """Open decisions from `orch decide`, on the session whose terminal registered them (else the root)."""
    out = []
    for d in (read_json(state_dir() / "decisions.json", {}) or {}).get("decisions", {}).values():
        if d.get("status") != "open":
            continue
        sid = by_handle.get(d.get("handle"))
        out.append({"key": f"decision:{d['id']}", "type": "decision", "session": sid or root, "handle": d.get("handle") if sid else None,
                    "decision": d["id"], "title": mask(d.get("title"), 200), "detail": first_line(d.get("body")),
                    "body": mask(d.get("body"), DECISION_BODY_MAX), "options": d.get("options") or [],
                    "recommend": d.get("recommend"), "url": d.get("link"), "at": d.get("created_at"), "source": "orch decide"})
    return out


EXTERNAL_KEEP_DAYS = 7  # an added item nobody resolved leaves the inbox this long after it was added


def external_items(now=None):
    """Items the orchestrator skill added with `orch-dash inbox add` (or by appending the same line), less resolved
    ones and, given now, those added more than EXTERNAL_KEEP_DAYS before it."""
    items = {}
    for row in read_lines(state_dir() / "inbox-external.jsonl"):
        key = f"ext:{row.get('key')}"
        if row.get("op") == "resolve":
            items.pop(key, None)
        elif row.get("op") == "add" and row.get("type"):
            items[key] = {"key": key, "type": row["type"], "session": row.get("session"), "title": mask(row.get("title"), 200),
                          "url": row.get("url"), "command": mask(row.get("command"), 500), "at": row.get("at"),
                          "source": row.get("source") or "skill"}
    cutoff = iso(now - dt.timedelta(days=EXTERNAL_KEEP_DAYS)) if now else ""
    return [i for i in items.values() if (i["at"] or "") >= cutoff]


def sync_items(now, terms=None):
    """Skill sync health, written by the platform side's skills-sync. A reload not sent sits on its terminal's
    session; one for a terminal Orca no longer lists is dropped, as there is nothing left to reload."""
    base, out = state_dir().parent, []
    sync = read_json(base / "sync.json")
    if isinstance(sync, dict):
        at, every = parse(sync.get("at")), sync.get("interval") or 900
        if sync.get("ok") is False or (at and (now - at).total_seconds() > 2 * every):
            out.append({"key": f"sync:{sync.get('at')}", "type": "sync_stalled", "session": None, "at": sync.get("at"),
                        "title": "Skills sync stopped", "detail": mask(sync.get("message"), 300), "source": "skills-sync"})
    pending = read_json(base / "reload-pending.json")
    if isinstance(pending, dict):
        for handle, why in (pending.get("failed") or {}).items():
            if terms is not None and handle not in terms:
                continue
            sid, title = (terms or {}).get(handle, (None, None))
            out.append({"key": f"reload:{handle}:{pending.get('since')}", "type": "reload_pending", "session": sid,
                        "terminal": handle, "at": pending.get("since"), "source": "skills-sync",
                        "title": f"Reload not sent: {mask(title or handle, 80)}", "detail": mask((why or {}).get("reason"), 200)})
    return out


RELOAD_RETRY_S = 120
RELOAD_TRIED = {}  # handle -> monotonic time of the last retry
RELOAD_FAILS = {}  # handle -> the dashboard's retries in a row the terminal refused, though idle
RELOAD_ALERT_TRIES = 3  # config reload_alert_tries: refused this often, a reload needs a person. Until then it is
# being retried (a busy terminal is not retried at all, and gets the reload once it is idle), not waiting on anyone

# Needs you order, newest first within a rank: what only the owner can settle, then a merge call waiting on them, then
# the rest; a PR item a held board item names is last, until the hold lifts.
NEEDS_RANK = {"decision": 0, "approval": 0, "permission": 0, "prompt": 0, "question": 0, "login": 0, "run_command": 0,
              "ready_to_merge": 1}
HELD_RANK = 3
DATE_RE = re.compile(r"(?<![\d.])(?:(\d{4})-)?(\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?(?![\d/])")


def needs_rank(item):
    return HELD_RANK if item.get("held") else NEEDS_RANK.get(item["type"], 2)


def needs_order(item):
    """Rank, then newest first by the moment, not the string: skills-sync writes `+0900` where the rest write `Z`."""
    at = parse(item.get("at")) if item.get("at") else None
    return needs_rank(item), -(at.timestamp() if at else 0)


def trigger_at(text, now):
    """The moment a held item's words name, M/D or YYYY-MM-DD with an optional HH:MM in local time, or None. A M/D
    half a year behind now is next year's."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    y, mo, d, h, mi = m.groups()
    local = now.astimezone()
    try:
        t = local.replace(year=int(y) if y else local.year, month=int(mo), day=int(d), hour=int(h or 0),
                          minute=int(mi or 0), second=0, microsecond=0)
    except ValueError:
        return None
    return t.replace(year=t.year + 1) if not y and t < local - dt.timedelta(days=180) else t


def due_at(due, text, now):
    """The moment an item's --due names (ISO8601, or what trigger_at reads), else the date in text, else None."""
    at = parse(due) or trigger_at(due, now) or trigger_at(text, now)
    return at and (at if at.tzinfo else at.astimezone())  # a bare date is local time


def todo_dates(t, now):
    """A to-do's (--due, the date in --after) as moments, each None without one. A M/D reads against when the to-do
    was added, not now: read against now, one that came last year comes again next year."""
    base = parse(t.get("added")) or now
    return due_at(t.get("due"), None, base), trigger_at(t.get("after"), base)


def todo_come(t, now, prs, cards=None):
    """Whether a to-do's time has come, whichever is first: its --due or the date in its --after has passed, or, with
    no date there, every PR its --after names (and the PR of the card it names) has merged. The one rule for the
    report's ⏰ and for lifting the to-do's hold on its PRs in Needs you."""
    import tidy
    due, on = todo_dates(t, now)
    if any(x and x <= now for x in (due, on)):
        return True
    after = t.get("after") or ""
    card = not on and after and tidy.named(after, cards or {})
    return bool(not on and after and tidy.refs_over(pr_refs(after) + [card and card.get("pr")], prs))


def todo_start(t, now):
    """The first of a to-do's dates, in UTC, or None when it has neither."""
    at = min(filter(None, todo_dates(t, now)), default=None)
    return at and at.astimezone(dt.timezone.utc)


WEEKDAY = "월화수목금토일"


def todo_parts(t, now):
    """A to-do's (date, condition) as the report says them: its --due as a local date (and time, unless midnight), and
    its --after without a date that only repeats the --due (with its weekday), None when nothing else is left."""
    at, on = todo_dates(t, now)
    at = at and at.astimezone()
    due = at and f"{at.month}/{at.day}({WEEKDAY[at.weekday()]})" + (f" {at:%H:%M}" if (at.hour, at.minute) != (0, 0) else "")
    after = t.get("after") or ""
    if at and on and f"{on.astimezone():%m%d%H%M}" == f"{at:%m%d%H%M}":  # an M/D's year is a guess
        m = DATE_RE.search(after)
        day = f"[{WEEKDAY}]"
        after = (re.sub(rf"(?:{day}(?:요일)?\s*)?$", "", after[:m.start()].rstrip())
                 + re.sub(rf"^\s*\({day}\)", "", after[m.end():]))
        after = after if re.search(r"\w", after) else ""
    return due, after.strip() or None


def todo_when(t, now):
    """When a to-do starts, as the report says it: its date, then its condition (todo_parts)."""
    return " · ".join(x for x in todo_parts(t, now) if x)


def held_prs(store, shut, prs, now, cards=None):
    """PR key -> the hold, for each PR a held board item or a to-do names in its title (held_refs). The hold lifts
    when the item is dropped or closed (tidy), when a to-do's time comes (todo_come), or when a held item's --due or
    the date in its trigger comes or, with no date, every PR its trigger names has merged. cards: tidy.cards_of()."""
    import tidy
    out = {}
    items = [("later", l, l["trigger"]) for l in store.get("later") or []]
    items += [("do", t, t.get("after") or "") for t in store.get("todo") or []]
    for kind, l, trigger in items:
        if tidy.item_id(kind, l) in shut:
            continue
        if kind == "do":
            if todo_come(l, now, prs, cards):
                continue
        else:
            at = due_at(l.get("due"), trigger, now)
            if at and at <= now or not at and tidy.refs_over(pr_refs(trigger), prs):
                continue
        why = todo_when(l, now) if kind == "do" else " · ".join(x for x in (trigger, l.get("due")) if x)
        for k in held_refs(l["title"], prs):
            out.setdefault(k, why)
    return out


def retry_reloads(sync=None):
    """Send a reload skills-sync could not deliver again once its session is idle, through skills-sync's own
    broadcast (idle check, lock, reload-pending.json). skills-sync retries only every 15 minutes, and a coordinator
    is rarely idle at that moment. Called after each collect; returns the handles reloaded."""
    pending = read_json(state_dir().parent / "reload-pending.json")
    failed = pending.get("failed") if isinstance(pending, dict) else None
    sync = failed and (sync or load_sync())
    if not sync or not hasattr(sync, "broadcast"):
        return []
    state = read_json(state_dir() / "state.json", {}) or {}
    idle = {t["handle"] for s in state.get("sessions", []) if s.get("phase") == "idle" for t in s.get("terminals", [])}
    mono, done = time.monotonic(), []
    for h in [h for h in failed if h in idle and mono - RELOAD_TRIED.get(h, -RELOAD_RETRY_S) >= RELOAD_RETRY_S]:
        RELOAD_TRIED[h] = mono
        with contextlib.redirect_stdout(io.StringIO()):
            if sync.broadcast(None, False, only=h) == 0:
                done.append(h)
                RELOAD_FAILS.pop(h, None)
            else:
                RELOAD_FAILS[h] = RELOAD_FAILS.get(h, 0) + 1
    return done


# ---------------------------------------------------------------- actions from the page and the CLI

def load_sync():
    """The platform's idle-checked sender (scripts/sync.py at the repository root), or None when absent."""
    root = str(pathlib.Path(__file__).resolve().parents[3] / "scripts")
    if root not in sys.path:
        sys.path.append(root)
    try:
        import sync
        return sync if hasattr(sync, "try_send") else None
    except ImportError:
        return None


def send(session_id, text, handle=None, sender=None):
    """Type one line into a fleet session's agent terminal. Returns (status, body) for the HTTP reply.
    Only agent terminals of sessions in the current state are reachable; every attempt is logged."""
    state = read_json(state_dir() / "state.json", {}) or {}
    s = next((x for x in state.get("sessions") or [] if x["id"] == session_id), None)
    terms = [t for t in (s or {}).get("terminals") or [] if t.get("agent") and t.get("connected") and t.get("writable")]
    handles = [t["handle"] for t in terms]
    if handle is None and len(handles) == 1:
        handle = handles[0]
    row = {"at": iso(utcnow()), "session": session_id, "handle": handle, "text": mask(text, 500)}
    if not s or handle not in handles:
        code, out = 403, {"ok": False, "reason": "not a connected agent terminal of a fleet session"}
    elif not text.strip() or "\n" in text or "\r" in text or len(text) > 2000:
        code, out = 400, {"ok": False, "reason": "one non-empty line, at most 2000 characters"}
    else:
        sender = sender or load_sync()
        if sender is None:
            code, out = 200, {"ok": False, "reason": "safety check unavailable (scripts/sync.py not found)", "fallback": "copy"}
        else:
            ok, reason = sender.try_send(handle, text)
            # This one reason means the line was typed and Enter pressed: a retry would send it twice.
            typed = ok or (reason or "").startswith("sent, but")
            code, out = 200, {"ok": ok, "reason": reason, "typed": typed, "fallback": None if typed else "copy"}
    append_line(state_dir() / "sends.jsonl", {**row, **out, "code": code})
    return code, out


def answer_prompt(session_id, prompt_id, action, sender=None):
    """Answer a pending permission dialog the way the human chose on the page: approve, deny, or open (switch Orca
    to that terminal). Returns (status, body) for the HTTP reply; every attempt is logged."""
    state = read_json(state_dir() / "state.json", {}) or {}
    s = next((x for x in state.get("sessions") or [] if x["id"] == session_id), None)
    handles = {t["handle"] for t in (s or {}).get("terminals") or [] if t.get("agent") and t.get("connected") and t.get("writable")}
    rec = next((r for h, r in prompt_records().items() if h in handles and r.get("id") == prompt_id), None)
    row = {"at": iso(utcnow()), "session": session_id, "handle": rec and rec["handle"], "text": f"prompt {action}"}
    if action not in ("approve", "deny", "open"):
        code, out = 400, {"ok": False, "reason": "action is approve, deny or open"}
    elif not rec:
        code, out = 409, {"ok": False, "reason": "no pending prompt with that id in this session's agent terminals"}
    elif action == "open":
        try:
            orca("terminal", "switch", "--terminal", rec["handle"])
            code, out = 200, {"ok": True, "reason": None}
        except SourceError as e:
            code, out = 200, {"ok": False, "reason": str(e)}
    else:
        sender = sender or load_sync()
        reason, key = press_option(sender, rec, action) if sender else ("safety check unavailable (scripts/sync.py not found)", None)
        code, out = 200, {"ok": reason is None, "reason": reason, "typed": key is not None, "key": key,
                          "fallback": None if key else "open"}
        if key:
            (state_dir() / "prompts" / f"{rec['handle']}.json").unlink(missing_ok=True)
    append_line(state_dir() / "sends.jsonl", {**row, **out, "code": code})
    return code, out


def press_option(sync, rec, action):
    """(reason, key). Under the lock the reload broadcast and the chat box share, the screen is read twice and both
    reads must show the same dialog for the recorded request; only then is the one digit pressed.
    reason is None once the dialog has left the screen; key is the digit pressed, or None when nothing was."""
    handle, key = rec["handle"], None
    with sync.locked(10) as got:
        if not got:
            return "a sync or broadcast is running; try again shortly", None
        try:
            first = sync.screen(handle)[0]
            time.sleep(sync.SETTLE_S)
            second = sync.screen(handle)[0]
            if not second:
                return "not a connected, writable Claude terminal", None
            d, why = prompt_verdict(second, rec)
            if not d:
                return why, None
            # Only the dialog is compared: above it the pending tool's ⏺ blinks between reads (measured).
            if d != parse_dialog(first):
                return "the dialog changed between two reads (someone may be answering it)", None
            digit = option_for(d, action)
            if not digit:
                return ("no one-time Yes option: every Yes saves a rule or changes the mode, answer in the terminal"
                        if action == "approve" else "no No option on the dialog, answer in the terminal"), None
            sync.orca("terminal", "send", "--terminal", handle, "--text", digit)  # a digit picks the option, no Enter
            key = digit
            time.sleep(sync.CONFIRM_S)
            after = sync.screen(handle)[0]
        except sync.Stop as e:
            return str(e), key
    if prompt_verdict(after, rec)[0]:
        return "sent, but the dialog is still on screen", key
    return None, key


def dismiss(fleet, key):
    with fleet.lock:
        fleet.mem.setdefault("dismissed", {})[key] = iso(utcnow())
        st = fleet.mem.get("sticky", {}).get(key)
        if st:
            st["open"] = False
        fleet.save(**{"memory.json": json.dumps(fleet.mem, ensure_ascii=False)})


def inbox_add(kind, title, session=None, url=None, command=None, key=None, source="orchestrator"):
    row = {"v": 1, "op": "add", "key": key or digest(kind, title, session), "type": kind, "title": title[:200],
           "session": session, "url": url, "command": command, "at": iso(utcnow()), "source": source}
    append_line(state_dir() / "inbox-external.jsonl", row)
    return row


def inbox_resolve(key):
    row = {"v": 1, "op": "resolve", "key": key, "at": iso(utcnow())}
    append_line(state_dir() / "inbox-external.jsonl", row)
    return row


# ------------------------------------------------------- decisions the orchestrator or lead waits on the human for

DECISION_BODY_MAX = 4000
DECISION_ANSWER_MAX = 1900  # the page's answer box; "decision <id>: " plus this stays inside send()'s 2000
DECISION_KEEP_S = 7 * 86400  # closed decisions are kept this long, then dropped from the file


def change_decisions(change):
    """Run change(store) on decisions.json under a lock and replace the file whole; returns what change returns."""
    path = state_dir() / "decisions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with (state_dir() / "decisions.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        store = read_json(path, {}) or {}
        out = change(store)
        write_atomic(path, json.dumps(store, ensure_ascii=False, indent=1) + "\n")
        return out


def decision_add(title, body="", options=(), recommend=None, link=None, handle=None):
    """Register a decision; options are "label::description". The label is what the page sends back, so one line."""
    one_line = lambda v, n: mask(" ".join(v.split()), n)
    opts = []
    for o in options:
        label, _, desc = o.partition("::")
        if not label.strip():
            raise ValueError(f"--option {o!r} has no label before '::'")
        opts.append({"label": one_line(label, 120), "description": one_line(desc, 300)})
    if recommend is not None and not 1 <= recommend <= len(opts):
        raise ValueError(f"--recommend is an option number from 1 to {len(opts)}" if opts else "--recommend needs --option")
    if not title.strip():
        raise ValueError("--title is empty")
    now = utcnow()

    def add(store):
        n = store.get("next", 1)
        d = {"id": f"d{n}", "title": one_line(title, 200), "body": mask(body.strip(), DECISION_BODY_MAX), "options": opts,
             "recommend": recommend, "link": mask(link, 500), "handle": handle, "created_at": iso(now), "status": "open"}
        cutoff = iso(now - dt.timedelta(seconds=DECISION_KEEP_S))
        kept = {k: v for k, v in store.get("decisions", {}).items() if v.get("status") == "open" or (v.get("closed_at") or "") >= cutoff}
        store.update(next=n + 1, decisions={**kept, d["id"]: d})
        return d
    return change_decisions(add)


def decision_close(did, status, answer=None, relay=False):
    """Mark an open decision done (answered) or dropped (no longer needed). relay: answered on the page, so the answer
    still has to reach the coordinator's terminal (delivered stays False until relay_decision types it). Such a decision
    is already done when the coordinator gets to it: its `done` then only stops the relay, and `drop` cancels it.
    `drop` also takes back any done decision, e.g. one the prompt hook closed on a misread answer."""
    def close(store):
        d = store.get("decisions", {}).get(did)
        if not d:
            raise ValueError(f"no decision {did}")
        if d.get("status") == "open":
            d.update(status=status, answer=mask(answer, DECISION_ANSWER_MAX), closed_at=iso(utcnow()))
            if relay:
                d["delivered"] = False
        elif d.get("status") == "done" and d.get("delivered") is False and not relay:
            d.update(delivered=True) if status == "done" else d.update(status=status)
        elif d.get("status") == "done" and status == "dropped" and not relay:
            d.update(status=status)
        else:
            raise ValueError(f"{did} is already {d.get('status')}")
        return d
    return change_decisions(close)


def open_decisions():
    return [d for d in (read_json(state_dir() / "decisions.json", {}) or {}).get("decisions", {}).values() if d.get("status") == "open"]


def pending_relays():
    """Decisions answered on the page whose answer has not reached the coordinator's terminal yet."""
    return [d for d in (read_json(state_dir() / "decisions.json", {}) or {}).get("decisions", {}).values()
            if d.get("status") == "done" and d.get("delivered") is False]


def decision_target(d, state):
    """(session, handle) the answer goes to: the terminal that registered it while its session is in the fleet, else
    the root's first agent terminal (the same place the page shows the item)."""
    sessions = state.get("sessions") or []
    owner = next((s for s in sessions if any(t.get("handle") == d.get("handle") for t in s.get("terminals") or [])), None)
    if owner:
        return owner, d["handle"]
    root = next((s for s in sessions if s["id"] == state.get("root")), None)
    handle = next((t["handle"] for t in (root or {}).get("terminals") or []
                   if t.get("agent") and t.get("connected") and t.get("writable")), None)
    return root, handle


RELAY_LOCK = threading.Lock()  # the page's answer and the collector's retry must not both type the same line


def relay_decision(did, sender=None, skip_working=False):
    """Type `decision <id>: <answer>` into the coordinator's terminal through send()'s idle check, once.
    Returns send()'s body, or None when there was nothing to relay. delivered turns True once the line was typed,
    even unconfirmed: a retry could send it twice."""
    d = next((x for x in pending_relays() if x["id"] == did), None)
    if not d:
        return None
    state = read_json(state_dir() / "state.json", {}) or {}
    session, handle = decision_target(d, state)
    if not session:
        return {"ok": False, "reason": "no orchestrator or lead session in the fleet state", "typed": False}
    if skip_working and session.get("phase") == "working":
        return {"ok": False, "reason": "the orchestrator or lead is working", "typed": False}
    _, out = send(session["id"], f"decision {did}: {d.get('answer')}", handle, sender)
    if out.get("typed"):
        def mark(store):
            (store.get("decisions", {}).get(did) or {}).update(delivered=True, delivered_at=iso(utcnow()))
        change_decisions(mark)
    return out


def decision_answer(did, answer, sender=None):
    """The page's answer: recorded and closed first, as `orch decide done` would, then relayed to the coordinator's
    terminal if it is idle. A busy coordinator is not a failure: relay_decisions retries on a later collect."""
    answer = " ".join((answer or "").split())
    if not answer or len(answer) > DECISION_ANSWER_MAX:
        return 400, {"ok": False, "reason": f"an answer of 1 to {DECISION_ANSWER_MAX} characters"}
    try:
        decision_close(did, "done", answer, relay=True)
    except ValueError as e:
        return 409, {"ok": False, "reason": str(e)}
    with RELAY_LOCK:
        out = relay_decision(did, sender) or {}
    return 200, {"ok": True, "recorded": True, "delivered": bool(out.get("typed")), "reason": out.get("reason"),
                 "line": f"decision {did}: {mask(answer, DECISION_ANSWER_MAX)}"}


def relay_decisions(sender=None):
    """Retry every answer the coordinator has not received, skipping a coordinator whose session is working (its
    title would refuse anyway). Called after each collect; returns the ids delivered."""
    if not pending_relays() or not RELAY_LOCK.acquire(blocking=False):
        return []
    try:
        sender = sender or load_sync()
        return [d["id"] for d in pending_relays() if sender and (relay_decision(d["id"], sender, skip_working=True) or {}).get("typed")]
    finally:
        RELAY_LOCK.release()


# ---------------------------------------------------------------- adoption into Orca's own lineage (opt-in)

def adopt(apply=False, undo=None):
    """Print each session's proposed Orca parent. With --apply (and config adopt.write_orca_parent), set it on
    sessions that have no parent yet, logging the previous value; --undo restores logged values."""
    log_path = state_dir() / "adoption.jsonl"
    if undo:
        latest = {}
        for row in read_lines(log_path):
            latest[row.get("worktree")] = row
        done = 0
        for wt, row in latest.items():
            if row.get("op") != "set" or undo not in ("all", wt):
                continue
            before = row.get("before")
            args = ["--parent-worktree", f"id:{before}"] if before else ["--no-parent"]
            orca("worktree", "set", "--worktree", f"id:{wt}", *args)
            append_line(log_path, {"at": iso(utcnow()), "op": "undo", "worktree": wt, "restored": before})
            done += 1
        print(f"restored {done} worktree(s)")
        return 0
    cfg = config()
    sessions, root = build_sessions(fetch_fast(), fetch_runs(), cfg, declared_roles(ps_table()))
    if not root:
        print("no orchestrator found: set root_worktree in the config, or start a Run from the orchestrator")
        return 1
    plan = [(s, s["parent"]) for s in sessions.values()
            if s["parent"] and s["parent"] != s["id"] and not s["orca_parent"]]
    for s, parent in plan:
        print(f"{'set ' if apply else 'would set'} {s['name'][:40]:40}  parent -> {sessions[parent]['name'][:40]}")
    kept = sum(1 for s in sessions.values() if s["orca_parent"])
    print(f"{len(plan)} to adopt, {kept} already have a parent (left as they are)")
    if not apply:
        return 0
    if not (cfg.get("adopt") or {}).get("write_orca_parent"):
        print(f"not written: set adopt.write_orca_parent to true in {config_path()} first")
        return 1
    for s, parent in plan:
        append_line(log_path, {"at": iso(utcnow()), "op": "set", "worktree": s["id"], "before": s["orca_parent"],
                               "after": parent})
        orca("worktree", "set", "--worktree", f"id:{s['id']}", "--parent-worktree", f"id:{parent}")
    print(f"wrote {len(plan)}; undo with: orch-dash adopt --undo all")
    return 0
