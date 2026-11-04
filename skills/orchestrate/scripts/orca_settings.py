"""Orca's orchestration settings, read from its profile store, and what this plugin's layout needs from them.

Usage: orch doctor [--json]

Orca 1.4.245 has no CLI that prints its settings. The app keeps them in the active profile's
`profile-state.db` (`profile_state_documents`, domain `settings`, a JSON payload); the active profile is
`orca-profile-index.json`'s `activeProfileId`. The database is opened read-only (`mode=ro`): this never writes a
setting, it proposes the change for the human to make in Orca's Settings screen.

The layout is orchestrator (its own Run, depth 0) → lead → the lead's workers → a worker's helpers. A lead started
as a dispatched worker is depth 1, so its helpers need Nested worker depth 3; a lead started as a `/goal` card binds
its own Run, so the same tree needs only 2.
"""
import json, os, pathlib, re, sqlite3, subprocess, sys

# A key the settings document lacks holds Orca's default: depth 1 refuses every worker's own worker-start.
DEFAULTS = {"nestedWorkerMaxDepth": 1, "orchestrationHeartbeatsEnabled": True,
            "orchestrationHeartbeatIntervalMinutes": 5, "orchestrationChatErrorEscalationEnabled": True}
LEAD_AS_WORKER_DEPTH = 3
LEAD_AS_CARD_DEPTH = 2
# Orca's Settings → Orchestration labels, English UI then Korean UI, so the human finds the control in either.
LABELS = {"nestedWorkerMaxDepth": "Nested worker depth (하위 에이전트 최대 생성 단계)",
          "orchestrationHeartbeatsEnabled": "Worker heartbeats (워커 하트비트)",
          "orchestrationChatErrorEscalationEnabled": "Chat error escalation (채팅 오류 알림)"}


def role_model(role):
    """`orchestrate.models.<role>` of agent-skills.json, or None: unset, unreadable, or not one model name."""
    try:
        cfg = json.loads(pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json")
                         .expanduser().read_text())
        value = cfg["orchestrate"]["models"][role]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) and re.fullmatch(r"[\w.:/\[\]-]+", value) else None


def data_dir():
    if os.environ.get("AGENT_SKILLS_ORCA_DATA_DIR"):
        return pathlib.Path(os.environ["AGENT_SKILLS_ORCA_DATA_DIR"])
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "orca"
    if sys.platform.startswith("win"):
        return pathlib.Path(os.environ.get("APPDATA", home)) / "orca"
    return pathlib.Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "orca"


def read(base=None):
    """({key: value} for DEFAULTS' keys, None) or (None, why it could not be read). A key the store lacks is
    Orca's default."""
    base = pathlib.Path(base) if base else data_dir()
    try:
        index = json.loads((base / "orca-profile-index.json").read_text())
        profile = (index.get("activeProfileId") if isinstance(index, dict) else None) or "local-default"
    except (OSError, ValueError):
        profile = "local-default"
    db = base / "profiles" / profile / "profile-state.db"
    if not db.is_file():
        return None, f"no Orca profile store at {db}"
    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("select payload from profile_state_documents where domain = 'settings'").fetchone()
        finally:
            con.close()
        stored = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError) as e:
        return None, f"cannot read {db}: {e}"
    if not isinstance(stored, dict):
        return None, f"{db}: the settings document is not an object"
    return {k: stored.get(k, d) for k, d in DEFAULTS.items()}, None


def depth(settings):
    try:
        return int(settings["nestedWorkerMaxDepth"])
    except (TypeError, ValueError, KeyError):
        return DEFAULTS["nestedWorkerMaxDepth"]


def lead_as_worker(settings):
    """Whether a lead may start as a dispatched worker: its helpers then sit at depth 3."""
    return bool(settings) and depth(settings) >= LEAD_AS_WORKER_DEPTH


def suggestions(settings):
    """One line per Orca setting this layout needs changed, in Korean for the human; empty when none."""
    out, d = [], depth(settings)
    if d < LEAD_AS_WORKER_DEPTH:
        now = ("리드를 /goal 카드로 띄운다" if d >= LEAD_AS_CARD_DEPTH
               else "리드를 /goal 카드로 띄우고, 리드의 워커도 보조 워커를 띄우지 못한다")
        out.append(f"Orca 설정 > Orchestration > {LABELS['nestedWorkerMaxDepth']} 를 {LEAD_AS_WORKER_DEPTH}으로 "
                   f"(지금 {d}: {now}. {LEAD_AS_WORKER_DEPTH}이면 리드를 오케스트레이터 Run 아래 dispatched worker로 띄운다)")
    if settings.get("orchestrationHeartbeatsEnabled") is False:
        out.append(f"Orca 설정 > Orchestration > {LABELS['orchestrationHeartbeatsEnabled']} 를 켬으로 "
                   "(꺼져 있으면 멈춘 워커를 Orca가 알리지 못한다)")
    if settings.get("orchestrationChatErrorEscalationEnabled") is False:
        out.append(f"Orca 설정 > Orchestration > {LABELS['orchestrationChatErrorEscalationEnabled']} 를 켬으로 "
                   "(꺼져 있으면 워커의 API 오류가 코디네이터에게 escalation으로 오지 않는다)")
    return out


def orca_call(orca=None):
    """call(*argv): the result object of `orca <argv> --json`; RuntimeError with Orca's output when it fails, by exit
    code or by `ok: false`."""
    def call(*argv):
        p = subprocess.run([orca or os.environ.get("SKILLS_SYNC_ORCA", "orca"), *argv, "--json"],
                           capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(f"{p.stdout} {p.stderr}")
        out = json.loads(p.stdout)
        if out.get("ok") is False:
            raise RuntimeError(p.stdout)
        return out.get("result") or {}
    return call


def binding():
    """This terminal's Run (run_current): the run dict, None when unbound, or "unknown" outside an Orca terminal or
    when Orca did not answer."""
    if not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return "unknown"
    try:
        return run_current(orca_call())
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError, RuntimeError):
        return "unknown"


def run_of(result):
    """The Run in a `run-current` result as a dict, None when unbound: `run` is the full record, `runId` the concise
    view, and either one null is Orca saying unbound. A result with neither key is read as unbound too, with one line
    on stderr so a changed answer shows."""
    result = result if isinstance(result, dict) else {}
    if "run" not in result and "runId" not in result:
        print(f"[orca_settings] run-current named neither run nor runId (keys: {sorted(result)}): read as unbound",
              file=sys.stderr)
    run = result.get("run") or result.get("runId")
    return run if isinstance(run, dict) else {"id": run} if isinstance(run, str) else None


def run_current(call, *argv):
    """run_of(`orchestration run-current <argv>`) read with --full. call as for with_full."""
    return run_of(with_full(call, "orchestration", "run-current", *argv))


def worker_terminal(row):
    """The agent terminal handle of a worker row (worker-list, worker-show, worker-start): `agentTerminalHandle` on
    older Orca, `terminal` from 1.4.246. Only a string counts: `orca terminal show` puts an object under `terminal`."""
    for key in ("agentTerminalHandle", "terminal"):
        if isinstance(h := (row or {}).get(key), str) and h:
            return h
    return None


# Orca 1.4.246 answers worker-list, run-list and check with a concise view unless given --full; an older Orca has no --full and always
# answers with the full record. Emptied once an Orca refuses the flag, so the rest of the process asks without it.
FULL = ["--full"]


def worker_row(row, run=None):
    """A worker-list row in the full record's names (workerState, agentTerminalHandle, resource.worktreeId,
    projection.outcome and .liveness.verdict). A full record is returned as it is. A concise row carries no
    dispatchStatus, retainedReason, projection.parent, stage or nextAction, so those stay absent: worker_list asks
    for --full. run: the Run the listing was scoped to, which a concise row does not name."""
    if not isinstance(row, dict) or "workerState" in row or "projection" in row:
        return row
    w = dict(row)
    for key, value in (("runId", run), ("workerState", row.get("state")),
                       ("agentTerminalHandle", worker_terminal(row)),
                       ("resource", {"worktreeId": row["workspace"]} if isinstance(row.get("workspace"), str) else None)):
        if value is not None and key not in w:
            w[key] = value
    pj = {"outcome": row.get("outcome"),
          "liveness": {"verdict": row["liveness"]} if isinstance(row.get("liveness"), str) else None,
          "provider": {"id": row["agent"]} if isinstance(row.get("agent"), str) else None}
    if pj := {k: v for k, v in pj.items() if v is not None}:
        w["projection"] = pj
    return w


def with_full(call, *argv):
    """call(*argv, "--full"): the full record. An error naming an unknown --full is an Orca from before the flag,
    whose default answer is already the full record: asked again without it, and FULL emptied for the process."""
    flags = tuple(FULL)  # another thread may empty FULL while this call is out
    try:
        return call(*argv, *flags)
    except (Exception, SystemExit) as e:
        if not flags or not re.search(r"[Uu]nknown (flag|option)\W+--full\b", str(e)):
            raise
        FULL.clear()
        return call(*argv)


def pages(call, command, *args, error=RuntimeError):
    """Every page of `orca orchestration <command> <args>` read with --full, as (result, more) per page.
    call(*argv) returns the result object of `orca <argv> --json` and raises or exits on an error (with_full). One
    call per page either way: --full
    makes each answer larger, not more numerous. The page is `page.nextCursor`/`hasMore`, or the concise view's
    top-level `nextCursor`. Raises error (the caller's own failure type) when a page says more follow but gives no
    cursor: a list cut short is not a smaller list."""
    cursor = None
    while True:
        res = with_full(call, "orchestration", command, *args, *(("--cursor", cursor) if cursor else ()))
        res = res if isinstance(res, dict) else {}
        page = res.get("page") if isinstance(res.get("page"), dict) else {}
        cursor = page.get("nextCursor", res.get("nextCursor"))
        more = bool(page.get("hasMore", cursor))
        if more and not cursor:
            raise error(f"{command} has more pages and no cursor to them")
        yield res, more
        if not more:
            return


def worker_list(call, *args, error=RuntimeError):
    """pages() of worker-list as (rows, result, more), the rows through worker_row."""
    for res, more in pages(call, "worker-list", *args, error=error):
        run = (res.get("scope") or {}).get("run")
        yield [worker_row(w, run) for w in res.get("workers") or []], res, more


def all_workers(call, *args, error=RuntimeError):
    """worker_list's rows from every page."""
    return [w for rows, _, _ in worker_list(call, *args, error=error) for w in rows]


def run_list(call, error=RuntimeError):
    """run-list's first page (Orca's default size, newest first) read with --full: from 1.4.246 only --full carries
    coordinator_handle and legacy. One page, as before: every listed Run costs fleet a worker-list, task-list and
    gate-list."""
    return next(pages(call, "run-list", error=error))[0].get("runs") or []


SETTLED = ("completed", "failed", "abandoned")  # dispatch statuses whose worker has stopped


def terminal_dispatch(call, handle, settled=False):
    """The Dispatch of the terminal `handle`, else None. A person typing into a worker's terminal, or keeping it open,
    moves its Dispatch from `active` to `retained` and it is still this terminal's. A settled retained row is not: Orca
    keeps those for a terminal it reused or that stayed open, and reading one as current would let a finished Dispatch
    vouch for closing the terminal or name the Run of a new card. Active goes first for the same reuse; a settled active
    row counts, since a worker that just reported still has to find itself. worker-list is newest first.
    settled=True counts settled retained rows too, for a caller asking whether this terminal was dispatched at all."""
    for state in ("active", "retained"):
        for rows, _, _ in worker_list(call, "--terminal-state", state, "--limit", "100"):
            mine = next((w for w in rows if worker_terminal(w) == handle and
                         (settled or state == "active" or w.get("dispatchStatus") not in SETTLED)), None)
            if mine:
                return mine
    return None


def own_dispatch(orca=None, failed=None):
    """This terminal's own Dispatch when it is a dispatched worker (a lead started with worker-start, whose
    `run-current` is null), else None, or `failed` when worker-list did not answer. Unbound, worker-list spans every
    Run."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    try:
        return terminal_dispatch(orca_call(orca), me) if me else None
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError, TypeError, RuntimeError):
        return failed


def lead_scope(workers, lead_dispatch):
    """A program's Dispatches. With no lead_dispatch (a lead that coordinates its own Run), every one. A lead started
    as a dispatched worker shares its coordinator's Run, so only the Dispatches whose Task descends from the lead
    Dispatch's Task (`projection.parent.taskId`), and none at all when that Dispatch is not listed: the rest of the Run
    is the coordinator's and other leads' work."""
    if not lead_dispatch:
        return workers
    mine = next((w for w in workers if w.get("dispatchId") == lead_dispatch), None)
    if not mine:
        return []
    parent = {w.get("taskId"): ((w.get("projection") or {}).get("parent") or {}).get("taskId") for w in workers}

    def under(task, seen=()):
        up = parent.get(task)
        return up == mine.get("taskId") or bool(up) and up not in seen and under(up, (*seen, task))
    return [w for w in workers if under(w.get("taskId"))]


def scope_tasks(tasks, scoped_workers, lead_dispatch):
    """The Run's tasks a program owns: every one for a coordinating lead; for a worker lead, which can create no task
    of its own, the tasks its units run."""
    if not lead_dispatch:
        return tasks
    ids = {w.get("taskId") for w in scoped_workers}
    return [t for t in tasks if t.get("id") in ids]


def report(settings, why, run):
    s = settings or {}
    return {"settings": settings, "unreadable": why, "lead_as_worker": lead_as_worker(settings),
            "suggestions": suggestions(s) if settings else [], "run": run if isinstance(run, dict) else None,
            "run_unknown": run == "unknown"}


def main(argv):
    settings, why = read(data_dir())
    r = report(settings, why, binding())
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    if why:
        print(f"Orca 설정을 읽지 못했다: {why}. 리드는 /goal 카드로 띄운다")
    else:
        print("Orca 설정: " + ", ".join(f"{k}={v}" for k, v in settings.items()))
        print("리드: " + ("dispatched worker (worker-start --worktree new-child)" if r["lead_as_worker"]
                         else "/goal 카드 (dispatch-card)"))
        print("\n".join(f"제안: {line}" for line in r["suggestions"]) or "제안: 없음")
    if r["run"]:
        print(f"이 터미널의 Run: {r['run'].get('id')} (보고 주소 run:{r['run'].get('id')})")
    elif not r["run_unknown"] and (own := own_dispatch()):
        print(f"이 터미널은 Run {own.get('runId')} 의 워커다 (dispatch {own.get('dispatchId')}): 보고 주소는 그 Run, "
              f"이 세션이 받을 주소는 dispatch:{own.get('dispatchId')}")
    elif not r["run_unknown"]:
        print("이 터미널은 Run에 묶여 있지 않다: 이 세션 앞으로 온 메일은 터미널이 닫히면 terminal_not_found로 끊긴다. "
              "코디네이터라면 `orca orchestration run-use --id <run>` (없으면 run-create)로 묶는다")
