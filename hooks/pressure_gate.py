#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: look at the machine before a heavy start.

A Bash call that starts something heavy (`orch heavy`, `docker compose … up`, `docker-compose … up`,
`orca worktree create`) is denied while the machine is at warn or danger by reap-resources' pressure check
(skills/reap-resources/scripts/pressure.py). At warn it goes through once a pressure round swept the safe targets in the
last `gate_fresh_minutes`: the safe targets are already gone, and warn alone is no reason to wait. At danger it waits.

`AGENT_SKILLS_PRESSURE_GATE=0`, in the hook's environment or before the heavy command itself, turns it off.
Swap and disk raise the gate to warn at most: they stay high for hours.
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


def gate(cmd, metrics, state, cfg, now=None, judge=None):
    """(decision, reason) or None."""
    what = heavy(cmd)
    if not what:
        return None
    # Swap drains slowly and disk is freed by hand: hours of danger would hold every heavy start, so they warn only.
    level, why = judge(metrics, {**cfg, "swap_gb": [cfg["swap_gb"][0], float("inf")],
                                 "disk_free_gb": [cfg["disk_free_gb"][0], float("-inf")]})
    if level == "ok":
        return None
    now = now or time.time()
    fresh = bool(state.get("scan_t")) and now - state["scan_t"] < float(cfg["gate_fresh_minutes"]) * 60
    py = shlex.join(["python3", str(SCRIPT)])
    if level == "warn" and fresh:
        return None
    if level == "warn":
        return ("deny", f"{MARK} {SOURCE} PC 사용량 경고({', '.join(why)})라 `{what}` 전에 안전 대상부터 정리한다: `{py} round` 를 "
                        "한 번 실행하고 같은 명령을 다시 시도한다. 그 회차는 끝난 카드의 스택·고아 프로세스처럼 reap 목록에 오른 것만 "
                        "정리하고, 경고만 남으면 이 hook 은 다음 시도를 통과시킨다.")
    first = "" if fresh else f"`{py} round` 를 먼저 한 번 실행한다(안전 대상을 정리하고, 없으면 최대 소비자를 보고한다). "
    return ("deny", f"{MARK} {SOURCE} PC 사용량 위험({', '.join(why)})이라 `{what}` 를 지금 시작하지 않는다. {first}위험이 이어지면 Monitor 로 "
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
        out = gate(cmd, pressure.measure(always_on=cfg["always_on"]), pressure.load_state(), cfg, judge=pressure.judge)
    except Exception:  # a broken measurement must not block work
        return 0
    if out:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": out[0],
                                                 "permissionDecisionReason": out[1]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
