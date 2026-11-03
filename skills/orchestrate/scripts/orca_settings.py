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


def binding():
    """This terminal's Run (`run-current`): the run dict, None when unbound, or "unknown" outside an Orca terminal or
    when Orca did not answer."""
    if not os.environ.get("ORCA_TERMINAL_HANDLE"):
        return "unknown"
    try:
        p = subprocess.run([os.environ.get("SKILLS_SYNC_ORCA", "orca"), "orchestration", "run-current", "--json"],
                           capture_output=True, text=True, timeout=30)
        return (json.loads(p.stdout).get("result") or {}).get("run") if not p.returncode else "unknown"
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return "unknown"


# Orca 1.4.246 answers worker-list with a concise view unless given --full; an older Orca has no --full and always
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
                       ("agentTerminalHandle", row.get("terminal") if isinstance(row.get("terminal"), str) else None),
                       ("resource", {"worktreeId": row["workspace"]} if isinstance(row.get("workspace"), str) else None)):
        if value is not None and key not in w:
            w[key] = value
    pj = {"outcome": row.get("outcome"),
          "liveness": {"verdict": row["liveness"]} if isinstance(row.get("liveness"), str) else None,
          "provider": {"id": row["agent"]} if isinstance(row.get("agent"), str) else None}
    if pj := {k: v for k, v in pj.items() if v is not None}:
        w["projection"] = pj
    return w


def worker_list(call, *args):
    """Every page of `orca orchestration worker-list <args>` read with --full, as (rows, result, more) per page, the
    rows through worker_row. call(*argv) returns the result object of `orca <argv> --json` and raises or exits on an
    error; an error naming an unknown --full is an Orca from before the flag, asked again without it. One call per
    page either way: --full makes each answer larger, not more numerous. Raises RuntimeError when a page says more
    follow but gives no cursor: a list cut short is not a smaller list."""
    cursor = None
    while True:
        argv = ("orchestration", "worker-list", *args, *(("--cursor", cursor) if cursor else ()))
        try:
            res = call(*argv, *FULL)
        except (Exception, SystemExit) as e:
            if not FULL or not re.search(r"[Uu]nknown (flag|option)\W+--full\b", str(e)):
                raise
            FULL.clear()
            res = call(*argv)
        res = res if isinstance(res, dict) else {}
        page = res.get("page") if isinstance(res.get("page"), dict) else {}
        cursor = page.get("nextCursor", res.get("nextCursor"))  # the concise view has no page object
        more = bool(page.get("hasMore", cursor))
        if more and not cursor:
            raise RuntimeError("worker-list has more pages and no cursor to them")
        run = (res.get("scope") or {}).get("run")
        yield [worker_row(w, run) for w in res.get("workers") or []], res, more
        if not more:
            return


def all_workers(call, *args):
    """worker_list's rows from every page."""
    return [w for rows, _, _ in worker_list(call, *args) for w in rows]


def own_dispatch(orca=None, failed=None):
    """This terminal's own active Dispatch when it is a dispatched worker (a lead started with worker-start, whose
    `run-current` is null), else None, or `failed` when worker-list did not answer. Unbound, worker-list spans every
    Run; only active terminals can be this one."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")

    def call(*argv):
        p = subprocess.run([orca or os.environ.get("SKILLS_SYNC_ORCA", "orca"), *argv, "--json"],
                           capture_output=True, text=True, timeout=30)
        if p.returncode:
            raise RuntimeError(f"{p.stdout} {p.stderr}")
        return json.loads(p.stdout).get("result") or {}
    try:
        for rows, _, _ in worker_list(call, "--terminal-state", "active", "--limit", "100") if me else ():
            mine = next((w for w in rows if w.get("agentTerminalHandle") == me), None)
            if mine:
                return mine
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError, TypeError, RuntimeError):
        return failed
    return None


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
