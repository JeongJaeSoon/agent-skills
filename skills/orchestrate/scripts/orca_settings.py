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
import json, os, pathlib, sqlite3, subprocess, sys

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
        profile = json.loads((base / "orca-profile-index.json").read_text()).get("activeProfileId") or "local-default"
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


def own_dispatch(orca=None):
    """This terminal's own active Dispatch when it is a dispatched worker (a lead started with worker-start, whose
    `run-current` is null), else None. Unbound, worker-list spans every Run; only active terminals can be this one."""
    me, cursor = os.environ.get("ORCA_TERMINAL_HANDLE"), None
    while me:
        try:
            p = subprocess.run([orca or os.environ.get("SKILLS_SYNC_ORCA", "orca"), "orchestration", "worker-list",
                                "--terminal-state", "active", "--limit", "100", *(["--cursor", cursor] if cursor else []),
                                "--json"], capture_output=True, text=True, timeout=30)
            res = (json.loads(p.stdout).get("result") or {}) if not p.returncode else {}
        except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
            return None
        if mine := next((w for w in res.get("workers") or [] if w.get("agentTerminalHandle") == me), None):
            return mine
        cursor = (res.get("page") or {}).get("nextCursor")
        if not (res.get("page") or {}).get("hasMore") or not cursor:
            return None
    return None


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
