#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: look at the machine before a heavy start.

A Bash call that starts something heavy (`orch heavy`, `docker compose … up`, `docker-compose … up`,
`orca worktree create`) is denied while the machine is at warn or danger by reap-resources' pressure check
(skills/reap-resources/scripts/pressure.py). At warn it goes through once a pressure round swept the safe targets in the
last `gate_fresh_minutes`: the safe targets are already gone, and warn alone is no reason to wait. At danger it waits.

`AGENT_SKILLS_PRESSURE_GATE=0`, in the hook's environment or as the command's first word, turns it off.
Any failure to measure gives no decision.
"""
import json, os, pathlib, re, shlex, sys, time

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
SCRIPT = ROOT / "skills" / "reap-resources" / "scripts" / "pressure.py"
# In command position only: a grep or echo that quotes the words starts nothing.
HEAVY = re.compile(r"(?:^|[;&|(]|\bdo\b|\bthen\b)\s*(?:\S+=\S*\s+)*(?:\S*/)?"
                   r"(orch\s+heavy\b|docker\s+compose\b[^;&|\n]*?\sup\b|docker-compose\b[^;&|\n]*?\sup\b"
                   r"|orca\s+worktree\s+create\b)", re.M)
BYPASS = re.compile(r"^\s*AGENT_SKILLS_PRESSURE_GATE=0\s")


def heavy(cmd):
    m = HEAVY.search(cmd)
    return m and " ".join(m[1].split()[:2])


def gate(cmd, metrics, state, cfg, now=None, judge=None):
    """(decision, reason) or None."""
    what = heavy(cmd)
    if not what or BYPASS.match(cmd) or "--help" in cmd:
        return None
    level, why = judge(metrics, cfg)
    if level == "ok":
        return None
    now = now or time.time()
    fresh = bool(state.get("scan_t")) and now - state["scan_t"] < float(cfg["gate_fresh_minutes"]) * 60
    py = shlex.join(["python3", str(SCRIPT)])
    if level == "warn" and fresh:
        return None
    if level == "warn":
        return ("deny", f"PC 사용량 경고({', '.join(why)})라 `{what}` 전에 안전 대상부터 정리한다: `{py} round` 를 "
                        "한 번 실행하고 같은 명령을 다시 시도한다. 그 회차는 끝난 카드의 스택·고아 프로세스처럼 reap 목록에 오른 것만 "
                        "정리하고, 경고만 남으면 이 hook 은 다음 시도를 통과시킨다.")
    first = "" if fresh else f"`{py} round` 를 먼저 한 번 실행한다(안전 대상을 정리하고, 없으면 최대 소비자를 보고한다). "
    return ("deny", f"PC 사용량 위험({', '.join(why)})이라 `{what}` 를 지금 시작하지 않는다. {first}위험이 이어지면 Monitor 로 "
                    f"`{py} check` 가 0(정상) 또는 1(경고)로 끝날 때까지 기다린 뒤 다시 시도한다(foreground sleep 루프 금지). "
                    "남의 카드·프로세스는 종료하지 않는다. 사용자가 지금 실행하라고 했을 때만 명령 앞에 "
                    "AGENT_SKILLS_PRESSURE_GATE=0 을 붙인다.")


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
    try:
        sys.path.insert(0, str(SCRIPT.parent))
        import pressure
        cfg = pressure.config()
        out = gate(cmd, pressure.measure(), pressure.load_state(), cfg, judge=pressure.judge)
    except Exception:  # a broken measurement must not block work
        return 0
    if out:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": out[0],
                                                 "permissionDecisionReason": out[1]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
