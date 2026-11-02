#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: look at the machine before a heavy start.

A Bash call that starts something heavy (`orch heavy`, `docker compose … up`, `docker-compose … up`,
`orca worktree create`, `orca orchestration worker-start` into a new worktree) is denied while the machine is at warn or danger by reap-resources' pressure check
(skills/reap-resources/scripts/pressure.py). At warn it goes through once a pressure round swept the safe targets in the
last `gate_fresh_minutes`: the safe targets are already gone, and warn alone is no reason to wait. At danger it waits:
the command goes into pressure.py's queue, shown on the dashboard, until the same session runs it again once
`pressure.py queue wait <id>` says the hook would let it through, or the deadline passes; a queue that cannot be
written falls back to waiting on `pressure.py check`. The same script, run as PostToolUse and PostToolUseFailure,
records that retry's outcome.

Two more holds. A line that runs a whole test suite, an E2E or a headless browser outside `orch heavy` is denied with
the same line wrapped in it, whatever the level: the machine-wide slots are the cap, and tests run by live sessions
saturated the CPU while the gate watched only starts. A new card (`orca worktree create`, a worker in a new worktree)
is denied while Orca shows `turn_cap` agent turns in progress; the deny names `pressure.py turns --wait`.

`AGENT_SKILLS_PRESSURE_GATE=0`, in the hook's environment or before the heavy command itself, turns it off.
The level is the one `pressure.py check` reports and `queue wait` judges (pressure.snapshot + judge, gate_open), so
either wait ends when this would let the command through.
Any failure to measure gives no decision.
"""
import json, os, pathlib, re, shlex, sys, time

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
SCRIPT = ROOT / "skills" / "reap-resources" / "scripts" / "pressure.py"
WRAPPERS = {"sudo", "env", "nohup", "time", "command", "exec", "nice"}
SHELLS = {"bash", "sh", "zsh"}
SHELL_VALUE = {"-o", "+o", "-O", "+O"}  # shell options whose next word is their value
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
NEW_CARD = ("orca worktree create", "orca orchestration worker-start")


# Wrapper options whose next word is their value: `nice -n 5`, `sudo -u app`, `env -u HOME`, `time -o out`.
WRAPPER_VALUE = {"nice": {"-n"}, "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-U"}, "env": {"-u", "-C", "-S", "-P"},
                 "time": {"-o", "-f"}}
OVERRIDE = "AGENT_SKILLS_PRESSURE_GATE=0"


def skip_prefix(words, keywords=False):
    """How many leading words come before the program: assignments, wrappers with their options and values, and with
    keywords the shell words a loop or branch starts with."""
    i, wrapper = 0, None
    while i < len(words):
        w = words[i]
        if ASSIGN.match(w) or keywords and w in KEYWORDS:
            i += 1
        elif w in WRAPPERS:
            wrapper, i = w, i + 1
        elif wrapper and w.startswith("-"):
            i += 2 if w in WRAPPER_VALUE.get(wrapper, ()) else 1
        else:
            break
    return min(i, len(words))


SUBST = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`")
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)(\w+)\2")  # not a here-string (`<<<word`)


def heredoc_at(line, quote):
    """(the first here-document operator outside quotes, comments and arithmetic (`$((1<<20))`) on line, or None;
    the quote still open at its end; where the simple command holding the operator starts)."""
    i = start = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t;&|("):
            return None, quote, start
        elif line.startswith("((", i):
            end = line.find("))", i + 2)
            i = len(line) if end < 0 else end + 1
        elif c in ";&|(":
            start = i + 1
        elif m := HEREDOC.match(line, i):
            return m, quote, start
        i += 1
    return None, quote, start


def drop_heredocs(cmd, slotted=False):
    """cmd without the bodies of its here-documents, which are data (`cat > a.md <<EOF`), except where a shell
    reads the body as commands (`bash <<EOF`) or an unquoted body runs a substitution (`$(…)`, a backquote).
    With slotted, a body a shell reads inside `orch heavy` (`orch heavy - -- bash <<EOF`) goes too: it runs in
    the slot. A shell body under the user's go-ahead (`AGENT_SKILLS_PRESSURE_GATE=0 bash <<EOF`) is read as that
    shell's `-c` text, so the go-ahead covers each of its commands as it does for `bash -c`."""
    out, lines, quote = [], iter(cmd.split("\n")), ""
    for line in lines:
        out.append(line)
        m, quote, start = heredoc_at(line, quote)
        # Only the simple command that receives the body counts: `orch heavy - -- true; bash <<EOF` runs it outside.
        words = [w.strip("'\"") for w in line[start:m.start()].split()] if m else []
        shell = next((k for k, w in enumerate(words) if os.path.basename(w) in SHELLS), None)
        if shell is not None and slotted and any(words[k:k + 2] == ["orch", "heavy"] for k in range(shell)):
            shell = None  # `orch heavy - -- bash <<EOF`: the body runs in the slot
        if not m or shell is not None and OVERRIDE not in words[:shell]:
            continue
        body, end = [], None
        for row in lines:
            if (row.lstrip("\t") if m.group(1) else row) == m.group(3):
                end = row
                break
            body.append(row)
        if shell is not None:
            out.append(shlex.join([OVERRIDE, "bash", "-c", "\n".join(body)]))
        elif not m.group(2):  # only what a substitution runs: `$(date)` runs, the text around it is data
            out += ["".join(g) for g in SUBST.findall("\n".join(body))]
        if end is not None:
            out.append(end)
    return "\n".join(out)


def segments(cmd, slotted=False):
    """The simple commands of a shell line as argv lists, split on unquoted ; & | ( ) and newlines.
    A `bash -c '<text>'` is read as its own line; a here-document body is not read. Unparsable text gives no
    segments."""
    lex = shlex.shlex(drop_heredocs(cmd, slotted), posix=True, punctuation_chars="();&|\n")
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
        words = argv[skip_prefix(argv, keywords=True):]  # `FOO=1 env bash -c`, `do nice -n 5 bash -c`
        j, run_c = 1, False
        while words and os.path.basename(words[0]) in SHELLS and j < len(words) and words[j][:1] in "-+":
            run_c |= words[j][:2] != "--" and words[j][:1] == "-" and "c" in words[j][1:]  # `-c`, `-lc`, `-l -c`
            j += 2 if words[j] in SHELL_VALUE else 1  # `-o pipefail`
        if run_c and j < len(words):
            # The user's go-ahead before `bash -c` covers every command of its body.
            lead = [w for w in argv[:len(argv) - len(words)] if w == OVERRIDE]
            yield from (lead + inner for inner in segments(words[j], slotted))
        else:
            yield argv


def starts_heavy(argv):
    """`orch heavy`, `orca worktree create` or `docker compose … up` for one simple command, else None.
    A leading AGENT_SKILLS_PRESSURE_GATE=0 or a --help also gives None."""
    words, i = list(argv), 0
    i = skip_prefix(words, keywords=True)
    if OVERRIDE in words[:i]:
        return None
    words = words[i:]
    if not words or "--help" in words:
        return None
    head = os.path.basename(words[0])
    if head == "orch" and words[1:2] == ["heavy"]:
        return "orch heavy"
    if head == "orca" and words[1:3] == ["worktree", "create"]:
        return "orca worktree create"
    # A worker in a new worktree is a new agent like a created card; one on an existing worktree is not.
    if head == "orca" and words[1:3] == ["orchestration", "worker-start"] and any(
            w in ("new-top-level", "new-child") or w.startswith(("--worktree=new-top-level", "--worktree=new-child"))
            for w in words[3:]):
        return "orca orchestration worker-start"
    rest = words[2:] if head == "docker" and words[1:2] == ["compose"] else words[1:] if head == "docker-compose" else None
    j = 0
    while rest is not None and j < len(rest) and rest[j].startswith("-"):
        j += 2 if rest[j] in COMPOSE_VALUE else 1
    if rest is not None and j < len(rest) and rest[j] == "up":
        return "docker compose up"
    return None


def heavy(cmd):
    return next(filter(None, map(starts_heavy, segments(cmd))), None)


def new_card(cmd):
    """The new-card start a line runs anywhere in it, the command `orch heavy` runs included; None when it runs none."""
    for seg in segments(cmd):
        what = starts_heavy(seg)
        if what in NEW_CARD:
            return what
        if what == "orch heavy" and "--" in seg and (inner := new_card(shlex.join(seg[seg.index("--") + 1:]))):
            return inner
    return None


# A suite script (`tests.sh`, `run-tests.sh`), a test file (`test_x.py`, `x_test.go`, `x.spec.ts`) and the package
# script names an E2E or browser run goes by.
SUITE = re.compile(r"(run[-_])?tests?([-_]all)?\.sh")
TEST_FILE = re.compile(r"(.*/)?(test_[^/]*\.py|[^/]*_test\.(py|go)|[^/]*\.(test|spec)\.[cm]?[jt]sx?)")
E2E = re.compile(r"(.*[-_:./])?(e2e|playwright|cypress)([-_:.].*)?", re.I)
RUNNERS = {"npm", "pnpm", "yarn", "bun"}
LAUNCHER_VALUE = {"--package", "-p", "--cache", "--userconfig", "--registry"}
RUNNER_VALUE = {"--prefix", "-C", "--dir", "--cwd", "--filter", "-F", "--workspace", "-w"}
# This many test files in a `for` list or on one runner's line is a suite, the CONTRIBUTING loop among them;
# `git add` or `cat` of as many is not.
SUITE_FILES = 5
LOOPERS = {"for", "python", "python3", "pytest", "node", "bun", "deno", "go"}
TEST_RUNNERS = LOOPERS - {"for"} | {"jest", "vitest", "playwright", "mocha"} | {"npm", "pnpm", "yarn"}
# A redirection (`> out.txt`, `2>`, `&>`) and its target select no test.
REDIRECT = re.compile(r"\d*(>>?|<<?|&>>?|>&)")
# A runner given a test path (`tests/`, `tests/unit`, `a.test.ts`, `x.py::t`) or a name filter runs a subset.
FILTERS = {"-k", "-t", "-g", "--grep", "--testNamePattern", "--test-name-pattern", "-run", "--filter",
           "--testPathPattern", "--testPathPatterns", "--findRelatedTests", "--changed", "--onlyChanged"}
TEST_DIR = re.compile(r"(.*/)?(tests?|specs?|__tests__)(/.*)?", re.I)
# Options of pytest, bun, jest and vitest whose next word is a value (an output path, a count), never a test.
VALUE_OPTS = {"-n", "-m", "-p", "-c", "-o", "-W", "--junitxml", "--junit-xml", "--cov", "--cov-report",
              "--basetemp", "--rootdir", "--durations", "--maxfail", "--tb", "--log-file", "--html", "--timeout",
              "--coverage-dir", "--coverage-reporter", "--preload", "--rerun-each", "--reporter", "--reporter-outfile",
              "--outputFile", "--coverageDirectory", "--config", "--workers", "--maxWorkers", "--confcutdir",
              "--ignore", "--ignore-glob", "--deselect", "--cov-config", "--override-ini", "--import-mode", "--color",
              "--capture", "-r", "--cache-dir", "--junit-prefix", "--log-level", "--log-cli-level", "--setup-timeout"}
# Shell words before the command a loop or branch runs: `for d in a b; do npm test; done`.
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{"}


def narrowed(rest, any_path=False):
    """Whether a runner's arguments pick tests: a test file or directory, `x.py::t`, or a name filter; with any_path
    (pytest, which runs whatever path it is given) any path-shaped argument, never an option's value like `yes`. An option's value (`-n 8`,
    `--junitxml test-results.xml`) is neither, so it leaves a whole suite."""
    skip = False
    for w in rest:
        if skip:
            skip = False
        elif w.split("=", 1)[0] in FILTERS:
            return True
        elif w in VALUE_OPTS:
            skip = True
        elif not w.startswith("-") and (any_path and ("/" in w or w.endswith(".py")) or "::" in w or TEST_FILE.fullmatch(w) or TEST_DIR.fullmatch(w.rstrip("/"))):
            return True
    return False


def runs_tests(argv):
    """'test suite', 'E2E' or 'browser' for one simple command that runs a whole suite, an E2E or a headless browser,
    else None. One named test file, or a runner given a path, is not a suite."""
    words, i = list(argv), 0
    i = skip_prefix(words, keywords=True)
    if OVERRIDE in words[:i]:
        return None
    words, kept = words[i:], []
    for w in words:
        if kept and kept[-1] is None:
            kept[-1] = ""  # the redirection's target
        elif m := REDIRECT.match(w):
            kept.append(None if m.end() == len(w) else "")
        else:
            kept.append(w)
    words = [w for w in kept if w]
    if not words or {"--help", "--version"} & set(words):
        return None
    head = ""
    while head != (head := os.path.basename(words[0])):  # peel launchers until the program shows: `uv run python -m pytest`
        if head in SHELLS and len(words) > 1:  # `bash -eux tests.sh`; a `-c` body was read by segments()
            rest = words[1:]
            while rest and rest[0].startswith("-") and not (rest[0][1:2] != "-" and "c" in rest[0][1:]):
                rest = rest[2 if rest[0] in SHELL_VALUE else 1:]
            words = rest if rest and not rest[0].startswith("-") else words
        elif head in ("python", "python3") and words[1:3] == ["-m", "pytest"]:
            words = ["pytest"] + words[3:]
        elif head in ("uv", "poetry", "pipenv") and words[1:2] == ["run"] and len(words) > 2:
            words = words[2:]
        elif head in ("npx", "bunx", "pnpx") or head in RUNNERS and words[1:2] in (["exec"], ["dlx"], ["x"]):
            # `pnpm exec playwright test`, `npx --package @playwright/test playwright test`
            rest = words[1 if head in ("npx", "bunx", "pnpx") else 2:]
            while rest and rest[0].startswith("-"):
                rest = rest[2 if rest[0] in LAUNCHER_VALUE else 1:]
            words = rest or words
    if head in TEST_RUNNERS and sum(bool(TEST_FILE.fullmatch(w)) for w in words[1:]) >= SUITE_FILES:
        return "test suite"  # `npx vitest run a.test.ts … e.test.ts`
    args = [w for w in words[1:] if not w.startswith("-")]
    if SUITE.fullmatch(head):
        return "test suite"
    if head in ("playwright", "cypress"):
        return "E2E" if args[:1] in (["test"], ["run"]) else None
    if E2E.fullmatch(head):
        return "E2E"
    if "chrom" in head.lower() and any(w.startswith("--headless") for w in words):
        return "browser"
    if head in RUNNERS:  # `npm --prefix app test`: an option's value is not the script
        args, k = [], 1
        while k < len(words):
            if words[k] in RUNNER_VALUE:
                k += 1
            elif not words[k].startswith("-"):
                args.append(words[k])
            k += 1
        script = args[1] if args[:1] == ["run"] and len(args) > 1 else args[0] if args else ""
        if E2E.fullmatch(script):
            return "E2E"
        after = words[words.index(script) + 1:] if script else []
        if (script in ("test", "test:all") or head == "bun" and args[:1] == ["test"]) and not narrowed(after):
            return "test suite"
    if head == "make" and args:
        return "E2E" if any(E2E.fullmatch(a) for a in args) else "test suite" if "test" in args else None
    if head in ("jest", "vitest") and not narrowed([w for w in words[1:] if w not in ("run", "watch")]):
        return "test suite"
    if head == "pytest" and not narrowed(words[1:], any_path=True) or head == "go" and args[:2] == ["test", "./..."]:
        return "test suite"
    return None


def head_word(argv):
    """The program a simple command runs, past assignments, wrappers and shell keywords; "" when none."""
    words = argv[skip_prefix(argv, keywords=True):]
    return os.path.basename(words[0]) if words else ""


def loop_runs(rest):
    """Whether the body of a `for` loop, the segments up to its `done`, runs a program on its files."""
    for seg in rest:
        if seg and seg[0] == "done":
            return False
        h = head_word(seg)
        if h in LOOPERS - {"for"} or h.startswith("$"):
            return True
    return False


def unwrapped_tests(cmd):
    """What a line runs that belongs in an `orch heavy` slot, None when it runs none or already runs in one."""
    segs = list(segments(cmd, slotted=True))  # a command passed to `orch heavy` is that segment's words, never a segment of its own
    found = next(filter(None, map(runs_tests, segs)), None)
    # A `for` list counts only when its own body (up to `done`) runs a program on the files, not `cat "$f"`;
    # a glob (`hooks/test_*.py`) may expand to any number of files, so it counts as a whole list.
    if not found and sum(TEST_FILE.fullmatch(w) and (SUITE_FILES if set(w) & set("*?[") else 1) or 0
                         for k, s in enumerate(segs) if s and (s[0] == "for" and loop_runs(segs[k + 1:]) or
                                                               os.path.basename(s[0]) in LOOPERS - {"for"})
                         for w in s) >= SUITE_FILES:
        found = "test suite"
    return found


def wrap_reason(cmd, what):
    line = shlex.join(["orch", "heavy", "-", "--", "bash", "-c", cmd])
    return (f"{MARK} {SOURCE} {what} 실행은 머신 전체 무거운 실행 슬롯(`orch heavy`)을 거친다. 여러 카드가 동시에 전체 테스트·"
            f"E2E·브라우저를 돌리면 CPU 가 포화된다. 같은 명령을 슬롯 안에서 다시 실행한다: `{line}` "
            "슬롯이 다 차 있으면 빌 때까지 기다리므로 Bash 의 run_in_background 로 실행하고 끝나기를 기다린다. "
            "파일 하나만 도는 테스트는 슬롯 없이 돌려도 된다. 사용자가 지금 그대로 실행하라고 했을 때만 명령 앞에 "
            "AGENT_SKILLS_PRESSURE_GATE=0 을 붙인다.")


def gate(cmd, metrics, state, cfg, now=None, p=None, session="", cwd=""):
    """(decision, reason) or None. p is the pressure module. A danger deny puts the command in p's queue."""
    what = heavy(cmd)
    if not what:
        return None
    level, why = p.judge(metrics, cfg)
    now = now or time.time()
    if p.gate_open(level, state, cfg, now):
        what = new_card(cmd)
        if not what:
            return None
        n, cap = p.active_turns(), p.turn_cap(cfg)
        if None in (n, cap) or n < cap:
            return None
        py = shlex.join(["python3", str(SCRIPT)])
        return ("deny", f"{MARK} {SOURCE} 진행 중인 에이전트 턴이 {n}개로 상한 {cap}개(`reap.pressure.active_turns_per_cpu`)에 "
                        f"닿아 `{what}` 로 새 카드를 시작하지 않는다. 카드마다 테스트·hook·브라우저가 같이 돌아 CPU 가 포화된다. "
                        f"Monitor 로 `{py} turns --wait {float(cfg['queue_minutes']):g}` 가 0 으로 끝나면 같은 명령을 다시 "
                        "실행하고, 3 이면 시작하지 말고 그 출력을 보고한다(foreground sleep 루프 금지). 남의 카드·프로세스는 "
                        "종료하지 않는다. 사용자가 지금 시작하라고 했을 때만 명령 앞에 AGENT_SKILLS_PRESSURE_GATE=0 을 붙인다.")
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
    try:
        e = p.enqueue(session, cwd, cmd, what, why, cfg, now)
    except Exception:  # an unwritable queue still holds the start: only a failed measurement lets it through
        return ("deny", f"{MARK} {SOURCE} PC 사용량 위험({', '.join(why)})이라 `{what}` 를 지금 시작하지 않는다(대기열에 넣지 못함). "
                        f"{first}위험이 이어지면 Monitor 로 `{py} check` 가 0(정상) 또는 1(경고)로 끝날 때까지 최대 "
                        f"{float(cfg['queue_minutes']):g}분 기다린 뒤 다시 시도한다(foreground sleep 루프 금지). 그래도 2면 대기를 "
                        "멈추고 check 출력(이유·측정값)을 보고한다. " + tail)
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
    if event.get("tool_name") != "Bash":
        return 0
    session, kind = str(event.get("session_id") or ""), event.get("hook_event_name") or "PreToolUse"
    if kind == "PreToolUse" and (tests := unwrapped_tests(cmd)):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": wrap_reason(cmd, tests)}}, ensure_ascii=False))
        return 0
    if not heavy(cmd):
        return 0
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
