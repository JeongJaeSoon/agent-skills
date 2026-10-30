#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: look at the machine before a heavy start.

A Bash call that starts something heavy (`orch heavy`, `docker compose … up`, `docker-compose … up`,
`orca worktree create`) is denied while the machine is at warn or danger by reap-resources' pressure check
(skills/reap-resources/scripts/pressure.py). At warn it goes through once a pressure round swept the safe targets in the
last `gate_fresh_minutes`: the safe targets are already gone, and warn alone is no reason to wait. At danger it waits:
the command goes into pressure.py's queue, shown on the dashboard, until the same session runs it again once
`pressure.py queue wait <id>` says the hook would let it through, or the deadline passes. The same script, run as
PostToolUse and PostToolUseFailure, records that retry's outcome.

`AGENT_SKILLS_PRESSURE_GATE=0`, in the hook's environment or before the heavy command itself, turns it off.
The level is the one `pressure.py check` reports (pressure.snapshot + judge), so waiting on check ends when this would.
Any failure to measure gives no decision.
"""
import json, os, pathlib, re, shlex, sys, time

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
SCRIPT = ROOT / "skills" / "reap-resources" / "scripts" / "pressure.py"
WRAPPERS = {"sudo", "env", "nohup", "time", "command", "exec", "nice"}
SHELLS = {"bash", "sh", "zsh"}
# docker compose global options that take a value: `docker compose -f a.yml --profile x up`.
COMPOSE_VALUE = {"-f", "--file", "-p", "--project-name", "--profile", "--env-file", "--project-directory",
                 "--ansi", "--progress", "--parallel"}
ASSIGN = re.compile(r"[A-Za-z_]\w*=")
SEPARATORS = set("();&|\n")
# The orchestrate refused-act rule lets a session follow only a block carrying this marker.
MARK = "[gate:pressure]"
# The hook's name after the marker: the model sees only the reason text, not which hook wrote it.
SOURCE = "(agent-skills hooks/pressure_gate.py)"
# PostToolUse and PostToolUseFailure: a Bash exit other than 0 is the failure event, with "Exit code N" as its error.
POST = {"PostToolUse": "done", "PostToolUseFailure": "failed"}


def segments(cmd):
    """The simple commands of a shell line as argv lists, split on unquoted ; & | ( ) and newlines.
    A `bash -c '<text>'` is read as its own line. Unparsable text gives no segments."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars="();&|\n")
    lex.whitespace, lex.whitespace_split = " \t\r", True
    out, cur = [], []
    try:
        for tok in lex:
            if tok and set(tok) <= SEPARATORS:
                out.append(cur)
                cur = []
            else:
                cur.append(tok)
    except ValueError:
        return []
    out.append(cur)
    for argv in [a for a in out if a]:
        words = argv[:]
        while words and ASSIGN.match(words[0]):
            words.pop(0)
        if len(words) >= 3 and words[0] in SHELLS and words[1] == "-c":
            yield from segments(words[2])
        else:
            yield argv


def starts_heavy(argv):
    """`orch heavy`, `orca worktree create` or `docker compose … up` for one simple command, else None.
    A leading AGENT_SKILLS_PRESSURE_GATE=0 or a --help also gives None."""
    words, i = list(argv), 0
    while i < len(words) and (ASSIGN.match(words[i]) or words[i] in WRAPPERS or
                              (i and words[i - 1] in WRAPPERS and words[i].startswith("-"))):
        if words[i] == "AGENT_SKILLS_PRESSURE_GATE=0":
            return None
        i += 1
    words = words[i:]
    if not words or "--help" in words:
        return None
    head = os.path.basename(words[0])
    if head == "orch" and words[1:2] == ["heavy"]:
        return "orch heavy"
    if head == "orca" and words[1:3] == ["worktree", "create"]:
        return "orca worktree create"
    rest = words[2:] if head == "docker" and words[1:2] == ["compose"] else words[1:] if head == "docker-compose" else None
    j = 0
    while rest is not None and j < len(rest) and rest[j].startswith("-"):
        j += 2 if rest[j] in COMPOSE_VALUE else 1
    if rest is not None and j < len(rest) and rest[j] == "up":
        return "docker compose up"
    return None


def heavy(cmd):
    return next(filter(None, map(starts_heavy, segments(cmd))), None)


def gate(cmd, metrics, state, cfg, now=None, p=None, session="", cwd=""):
    """(decision, reason) or None. p is the pressure module. A danger deny puts the command in p's queue."""
    what = heavy(cmd)
    if not what:
        return None
    level, why = p.judge(metrics, cfg)
    now = now or time.time()
    if p.gate_open(level, state, cfg, now):
        return None
    fresh = p.fresh(state, cfg, now)
    py = shlex.join(["python3", str(SCRIPT)])
    if level == "warn":
        return ("deny", f"{MARK} {SOURCE} PC 사용량 경고({', '.join(why)})라 `{what}` 전에 안전 대상부터 정리한다: `{py} round` 를 "
                        "한 번 실행하고 같은 명령을 다시 시도한다. 그 회차는 끝난 카드의 스택·고아 프로세스처럼 reap 목록에 오른 것만 "
                        "정리하고, 경고만 남으면 이 hook 은 다음 시도를 통과시킨다.")
    first = "" if fresh else f"`{py} round` 를 먼저 한 번 실행한다(안전 대상을 정리하고, 없으면 최대 소비자를 보고한다). "
    tail = ("남의 카드·프로세스는 종료하지 않는다. 사용자가 지금 실행하라고 했을 때만 명령 앞에 "
            "AGENT_SKILLS_PRESSURE_GATE=0 을 붙인다.")
    if p.blocked_by(metrics, cfg) == ["disk_free_gb"]:  # waiting frees no disk
        return ("deny", f"{MARK} {SOURCE} 디스크 여유가 위험 수준({', '.join(why)})이라 `{what}` 를 지금 시작하지 않는다. "
                        f"{first}기다려도 풀리지 않으니 대기하지 말고, 사용자에게 디스크 정리를 요청하고 멈춘다. {tail}")
    e = p.enqueue(session, cwd, cmd, what, why, cfg, now)
    return ("deny", f"{MARK} {SOURCE} PC 사용량 위험({', '.join(why)})이라 `{what}` 를 지금 시작하지 않고 대기열 {e['id']} 에 "
                    f"넣었다(최대 {float(cfg['queue_minutes']):g}분, 기한 {p.hhmm(e['deadline'])}, 대시보드 Needs you 에 남는다). "
                    f"{first}Monitor 로 `{py} queue wait {e['id']}` 가 끝날 때까지 기다린다(foreground sleep 루프 금지). "
                    "0 으로 끝나면 이 세션에서 같은 명령을 그대로 다시 실행한다. 0 이 아니면(기한이 지나 포기로 기록됐거나 이미 재시도됨) "
                    "다시 실행하지 말고 그 출력(이유·측정값)을 보고한다. " + tail)


def main():
    if os.environ.get("AGENT_SKILLS_PRESSURE_GATE") == "0":
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    cmd = (event.get("tool_input") or {}).get("command") or ""
    if event.get("tool_name") != "Bash" or not heavy(cmd):
        return 0
    session, kind = str(event.get("session_id") or ""), event.get("hook_event_name") or "PreToolUse"
    try:
        sys.path.insert(0, str(SCRIPT.parent))
        import pressure
        if outcome := POST.get(kind):  # the outcome of a retry the queue let through; nothing to measure
            pressure.queue_mark(session, cmd, outcome, event.get("error"))
            return 0
        cfg = pressure.config()
        state = pressure.load_state()
        out = gate(cmd, pressure.snapshot(cfg, state), state, cfg, p=pressure, session=session,
                   cwd=str(event.get("cwd") or ""))
        if not out:
            pressure.queue_mark(session, cmd, "retrying")
    except Exception:  # a broken measurement must not block work
        return 0
    if out:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": out[0],
                                                 "permissionDecisionReason": out[1]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
