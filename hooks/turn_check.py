#!/usr/bin/env python3
"""Stop, SubagentStop and PreToolUse (Telegram reply) hook of the agent-skills plugin: end-of-turn checks for habits the
skills only state in prose (design M3). Every check is a warning and every key is off until set.

Each check has a key `turn_check.<name>` in ~/.claude/agent-skills.json ($AGENT_SKILLS_CONFIG). No key, `false` or
`off` is off, with no output at all. `true`, `warn`, `ask` or `deny` all act as `warn`: this hook never blocks. A
firing check prints one `systemMessage` line for the human (the model does not see it) and adds a row to
$AGENT_SKILLS_STATE/turn-warnings.jsonl, so false positives can be counted before a key is ever raised.

  wait_alive          orchestrator, lead   no background inbox wait or watch-cards is running when the turn ends
  self_close          worker, CLOSE card   worker_done went out and `skills-sync end-check` says the card may close
  review_idle         worker, subagent     the turn ends on a review report or a background-task notice, no next call
  decision_registered orchestrator, lead   the reply asks for a decision and the turn ran no `orch decide add`
  offer_phrase        any main session     the reply ends offering to do what it should just do
  stacks_down         any main session     a compose project this session brought up has no `down` and no keep
  brief_render        orchestrator, lead   the reply has a status report's shape and the turn ran no `orch brief render`
  prs_actions         any main session     `prs.py actions` printed lines and the turn logged none with `prs.py log`
  telegram_render     orchestrator, lead   a Telegram reply (PreToolUse) has a report's shape, no `orch brief render` ran

Which roles a check reaches is hooks/roles.py MATRIX. A Stop check cannot undo a send, so telegram_render looks before
the send. The wait and compose checks read the whole transcript (capped at TRANSCRIPT_MAX bytes: a larger one is not
read); the rest read only the turn, everything after the last prompt a person typed or the last task notice.
Heuristics on text and on tool names: a wrapped wait or compose call under another name goes unseen.
Any error exits 0 silently (and logs to guard-errors.jsonl).
"""
import datetime, json, os, pathlib, re, subprocess, sys

import claim_check, roles
from lib import config, shell

HERE = pathlib.Path(__file__).resolve().parent
END_CHECK = HERE.parents[0] / "scripts" / "end_check.py"
CHECKS = ("wait_alive", "self_close", "review_idle", "decision_registered", "offer_phrase", "stacks_down",
          "brief_render", "prs_actions", "telegram_render")
TRANSCRIPT_MAX = 64 << 20

WAIT = re.compile(r"\borch(?:\.py)?\s+(?:inbox|wait|watch-cards)\b")
TASK_END = re.compile(r"<status>\s*(?:completed|failed|killed|stopped|cancelled)\b")
TASK_ID = re.compile(r"<tool-use-id>([^<\s]+)</tool-use-id>")
NOTICE_TASK = re.compile(r"<task-id>([^<\s]+)</task-id>")
BG_ID = re.compile(r"(?:background with ID:|[Tt]ask(?: ID)?[: ])\s*([\w-]+)")
REVIEW_SKILLS = {"code-review", "review", "security-review", "interrogate", "blast-radius", "ui-review", "simplify"}
REVIEW_TEXT = re.compile(r"review|리뷰|audit|interrogate|adversarial", re.I)
CODEX = re.compile(r"\bcodex(?:-companion)?\b.*\b(?:review|adversarial)\b", re.S)
DECISION = re.compile(
    r"(?:결정|선택|승인|판단)\S{0,6}\s*(?:이\s*|가\s*)?(?:필요|부탁|해\s*주세요|바랍니다)|어느\s*(?:쪽|안)[^\n]{0,30}[?？]"
    r"|(?:할까요|드릴까요|갈까요|좋을까요|괜찮을까요)\s*[?？]|\bwhich (?:option|one|approach)\b[^\n]*\?"
    r"|\bshould (?:we|I)\b[^\n]*\?", re.I)
OFFER = re.compile(
    r"말씀\s*(?:해\s*)?(?:주시면|해주시면)|알려\s*(?:주시면|주세요)[^\n]{0,20}(?:진행|티켓|만들|올리)"
    r"|원하(?:시면|신다면)[^\n]{0,40}(?:만들|올리|진행|티켓|처리|해\s*드리)"
    r"|\bsay the word\b|\blet me know if you(?:'d| would)? (?:like|want)\b|\bwant me to (?:file|create|open|go ahead)\b"
    r"|\bif you(?:'d| would)? like,? I (?:can|could|will)\b", re.I)
REPORT_MARKS = ("🙋 지금 오너가 할 일", "⏳ 다른 사람 대기", "🧭 우선순위", "## 보류", "🔄 진행", "## 요약")
STACK_DOWN = {"down"}


def option(words, name):
    """The value of `--name value` or `--name=value` in a command's words, None when absent."""
    for i, w in enumerate(words):
        if w == name and i + 1 < len(words):
            return words[i + 1]
        if w.startswith(name + "="):
            return w[len(name) + 1:]
    return None


def commands(raw):
    return shell.commands(str(raw or ""))


def project_of(words):
    """The compose project a command names (`-p`, `--project-name`), None when it names none."""
    for flag in ("--project-name", "-p"):
        got = option(words, flag)
        if got:
            return got
    return None


def has_run(entries, test):
    """A Bash command of the turn that was not refused and satisfies test(words)."""
    skip = claim_check.refused(entries)
    for entry in entries:
        for b in claim_check.blocks(entry) if entry.get("type") == "assistant" else []:
            if b.get("type") == "tool_use" and b.get("name") == "Bash" and b.get("id") not in skip:
                if any(test(c.words) for c in commands((b.get("input") or {}).get("command"))):
                    return True
    return False


def final_text(entries, last_message):
    if isinstance(last_message, str):
        return last_message
    final = []
    for entry in entries:
        if entry.get("type") == "user":
            final = []
        elif entry.get("type") == "assistant":
            final += [b.get("text") or "" for b in claim_check.blocks(entry) if b.get("type") == "text"]
    return "\n".join(final)


def turn(path):
    """(the entry that opened the turn, the entries after it). A task notice opens a turn like a prompt does."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - claim_check.TAIL_BYTES))
        lines = f.read().splitlines()
    out, start = [], None
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if claim_check.is_prompt(entry):
            start = entry
            break
        out.append(entry)
    return start, out[::-1]


def whole(path, needles):
    """(raw line, entry) of the transcript's lines that hold one of the needles or a failed tool result (a refused call
    is told by its result); None when the file is over TRANSCRIPT_MAX."""
    if os.path.getsize(path) > TRANSCRIPT_MAX:
        return None
    needles = (*needles, '"is_error":true', '"is_error": true')
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            if any(n in raw for n in needles):
                try:
                    out.append((raw, json.loads(raw)))
                except ValueError:
                    out.append((raw, None))
    return out


def uses(entries):
    """tool_use id -> (name, input) for the not-refused calls of these entries."""
    skip = claim_check.refused(entries)
    return {b["id"]: (b.get("name") or "", b.get("input") or {}) for e in entries if e.get("type") == "assistant"
            for b in claim_check.blocks(e) if b.get("type") == "tool_use" and b.get("id") not in skip}


def results(entries):
    """tool_use id -> (text, is_error) for the tool results of these entries."""
    out = {}
    for e in entries:
        for b in claim_check.blocks(e) if e.get("type") == "user" else []:
            if b.get("type") == "tool_result":
                c = b.get("content")
                text = c if isinstance(c, str) else " ".join(x.get("text") or "" for x in c or [] if isinstance(x, dict))
                out[b.get("tool_use_id")] = (text, bool(b.get("is_error")))
    return out


# ---- the checks: each returns the message to show, or None --------------------------------------------------------

def check_wait_alive(path):
    got = whole(path, ("inbox", "orch wait", "orch.py wait", "watch-cards", "task-notification", "TaskStop",
                       "background with ID"))
    if got is None:
        return None
    started, ended, ended_tasks, task_of, stopped = {}, set(), set(), {}, set()
    for raw, entry in got:
        if "<task-notification>" in raw and TASK_END.search(raw):
            ended |= set(TASK_ID.findall(raw))
            ended_tasks |= set(NOTICE_TASK.findall(raw))
        for b in claim_check.blocks(entry or {}) if (entry or {}).get("type") in ("assistant", "user") else []:
            if b.get("type") == "tool_result":
                text, err = results([entry]).get(b.get("tool_use_id"), ("", False))
                if err:
                    ended.add(b.get("tool_use_id"))
                if m := BG_ID.search(text):
                    task_of[b.get("tool_use_id")] = m.group(1)
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                if b.get("name") == "TaskStop":
                    stopped.add(str(inp.get("task_id") or inp.get("shell_id") or ""))
                elif (b.get("name") == "Monitor" or b.get("name") == "Bash" and inp.get("run_in_background")) \
                        and WAIT.search(str(inp.get("command") or "")):
                    started[b.get("id")] = inp.get("command")
    alive = [i for i in started if i not in ended and task_of.get(i) not in stopped | ended_tasks]
    if alive:
        return None
    return "백그라운드 inbox 대기·watch-cards가 없다 (orchestrate Drain: 턴을 끝내기 전에 다시 띄운다)"


def worker_done_sent(path):
    got = whole(path, ("worker_done",))
    if got is None:
        return False
    entries = [e for _, e in got if e]
    skip = claim_check.refused(entries)
    res = results(entries)
    for e in entries:
        for b in claim_check.blocks(e) if e.get("type") == "assistant" else []:
            if b.get("type") != "tool_use" or b.get("name") != "Bash" or b.get("id") in skip or res.get(b["id"], ("", 0))[1]:
                continue
            for c in commands((b.get("input") or {}).get("command")):
                w = c.words
                if os.path.basename(w[0]) == "orca" and w[1:3] == ["orchestration", "send"] \
                        and option(w, "--type") == "worker_done" and option(w, "--outcome") == "succeeded":
                    return True
    return False


def end_check_verdict(mode, keep, cwd):
    """The `skills-sync end-check` verdict, None when it cannot be read."""
    cmd = [sys.executable, str(END_CHECK), "--" + mode, "--keep" if keep else "--no-keep"]
    if mode == "worker":
        cmd += ["--outcome", "succeeded"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, cwd=cwd or None, stdin=subprocess.DEVNULL)
        doc, _ = json.JSONDecoder().raw_decode(p.stdout.lstrip())
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        config.log_error("turn_check.end_check", exc)
        return None
    return doc.get("verdict") if isinstance(doc, dict) else None


def check_self_close(path, session, cwd):
    if session.supervised:
        if not worker_done_sent(path):
            return None
        mode = "worker"
    elif session.close:
        mode = "card"
    else:
        return None
    # Only a verdict that lets the card go counts; keep, ask and an unread fact are all "stay".
    if end_check_verdict(mode, session.keep, cwd) in ("close-terminal", "remove"):
        return "끝낼 수 있는 카드가 열려 있다 (end-check가 닫아도 된다고 한다 · end-session §7-8)"
    return None


def reviewish(name, inp):
    if name == "Skill":
        return str(inp.get("skill") or "").split(":")[-1] in REVIEW_SKILLS
    if name in ("Agent", "Task"):
        return bool(REVIEW_TEXT.search(f"{inp.get('subagent_type') or ''} {inp.get('description') or ''}"))
    if name == "Bash":
        return bool(CODEX.search(str(inp.get("command") or "")))
    return False


def check_review_idle(start, entries):
    called, res = uses(entries), results(entries)
    last = None  # the last tool result's id
    for e in entries:
        if e.get("type") == "assistant" and any(b.get("type") == "tool_use" for b in claim_check.blocks(e)):
            last = None
        for b in claim_check.blocks(e) if e.get("type") == "user" else []:
            if b.get("type") == "tool_result":
                last = b.get("tool_use_id")
    # A review call that failed produced no report to act on.
    if last is not None and not res.get(last, ("", False))[1] and reviewish(*called.get(last, ("", {}))):
        return "리뷰 보고를 받고 다음 행동 없이 턴이 끝났다 (deliver-ticket §3: 같은 턴에 반영하고 이어 간다)"
    notice = start and "<task-notification>" in json.dumps(start.get("message") or "", ensure_ascii=False)
    if notice and TASK_END.search(json.dumps(start["message"], ensure_ascii=False)) and not called:
        return "백그라운드 완료 알림을 받고 아무 도구도 부르지 않고 턴이 끝났다"
    return None


def decision_recorded(w):
    if not w:
        return False
    head, args = os.path.basename(w[0]), w[1:]
    return (head in ("orch", "orch.py", "prog.py") and (args[:2] == ["decide", "add"] or args[:1] == ["gate"])) \
        or (head == "orca" and args[:2] == ["orchestration", "gate-create"])


def check_decision(entries, text):
    lines = [l for l in claim_check.strip(text).splitlines() if l.strip() and not l.lstrip().startswith("|")]
    if not DECISION.search("\n".join(lines)) or has_run(entries, decision_recorded):
        return None
    return "답에 결정 질문이 있는데 이번 턴에 `orch decide add`가 없다"


def check_offer(text):
    tail = [l for l in claim_check.strip(text).splitlines() if l.strip()][-3:]
    if OFFER.search("\n".join(tail)):
        return "떠넘기는 문구로 끝났다 (\"말씀 주시면 …\": 하기로 한 일은 묻지 않고 한다)"
    return None


def compose_action(words):
    """('up'|'down'|'keep', project) of a docker compose or ledger command, None otherwise."""
    base = [os.path.basename(w) for w in words[:3]]
    if "reap.py" in base and "ledger" in words and "add" in words and option(words, "--kind") == "compose-stack" \
            and option(words, "--keep"):
        return "keep", option(words, "--id")
    # The command itself is docker compose, bare or under `orch heavy`: `echo compose up` is text.
    runs = base[0] in ("docker", "docker-compose") or base[0] in ("orch", "orch.py") and "heavy" in words[1:3]
    i = next((k for k, w in enumerate(words) if os.path.basename(w) == "docker-compose"
              or w == "compose" and k and os.path.basename(words[k - 1]) == "docker"), None)
    if i is None or not runs or not (project := project_of(words)):
        return None
    rest = words[i + 1:]
    if "up" in rest:
        return "up", project
    if STACK_DOWN & set(rest):
        return "down", project
    return None


def check_stacks(path):
    got = whole(path, ("compose",))
    if got is None:
        return None
    entries = [e for _, e in got if e]
    skip, res = claim_check.refused(entries), results(entries)
    up = {}
    for e in entries:
        for b in claim_check.blocks(e) if e.get("type") == "assistant" else []:
            if b.get("type") != "tool_use" or b.get("name") != "Bash" or b.get("id") in skip:
                continue
            failed = res.get(b.get("id"), ("", False))[1]
            for c in commands((b.get("input") or {}).get("command")):
                act = compose_action(c.words)
                if act and act[0] == "up":
                    up[act[1]] = True  # a failed `up` may still have started containers
                elif act and not failed:  # a `down` or keep that failed took nothing down
                    up.pop(act[1], None)
    if up:
        return f"띄운 compose 스택이 내려가지 않았다: {', '.join(sorted(up))} (`docker compose -p <project> down`)"
    return None


def report_shaped(text):
    return sum(m in claim_check.strip(text) for m in REPORT_MARKS) >= 2


def render_ran(w):
    return bool(w) and os.path.basename(w[0]) in ("orch", "orch.py") and w[1:3] == ["brief", "render"]


def check_brief(entries, text):
    if report_shaped(text) and not has_run(entries, render_ran):
        return "현황 보고 모양의 답인데 이번 턴에 `orch brief render`가 없다 (brief §1: 손으로 쓰지 않는다)"
    return None


def check_prs_actions(entries):
    called, res, skip = uses(entries), results(entries), claim_check.refused(entries)
    lines, logged = 0, False
    for i, (name, inp) in called.items():
        if name != "Bash":
            continue
        for c in commands(inp.get("command")):
            w = c.words
            script = next((os.path.basename(x) for x in w[:3] if os.path.basename(x) == "prs.py"), None)
            if not script:
                continue
            if "actions" in w and i in res and not res[i][1]:
                lines += sum(1 for l in res[i][0].splitlines() if l.strip())
            elif "log" in w and i in res and not res[i][1]:
                logged = True
    if lines and not logged:
        return f"`prs.py actions`가 {lines}줄을 냈는데 `prs.py log`로 기록한 것이 없다 (watch-prs review-wait)"
    return None


# ---- events -------------------------------------------------------------------------------------------------------

def enabled(cfg):
    return {k: config.level(cfg, f"turn_check.{k}", "off") != "off" for k in CHECKS}


def emit(event, hits, session_id, agent):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    for name, _ in hits:
        config.append_warning({"at": now, "event": event, "session": session_id, "agent": agent, "check": name,
                               "level": "warn"})
    return {"systemMessage": "turn_check: " + " · ".join(msg for _, msg in hits)}


def stop(event, cfg):
    on = enabled(cfg)
    if not any(on.values()):
        return None
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return None
    s = roles.session(event)
    start, entries = turn(path)
    text = final_text(entries, event.get("last_assistant_message"))
    hits = []

    def run(name, rule, fn, *args):
        if on[name] and roles.applies(rule, s):
            try:
                msg = fn(*args)
            except Exception as exc:  # one check failing must not hide the others
                config.log_error(f"turn_check.{name}", exc)
                return
            if msg:
                hits.append((name, msg))

    run("wait_alive", "turn_wait", check_wait_alive, path)
    run("self_close", "turn_close", check_self_close, path, s, event.get("cwd"))
    run("review_idle", "turn_review", check_review_idle, start, entries)
    run("review_idle", "turn_review_sub", check_review_idle, start, entries)
    run("decision_registered", "turn_decision", check_decision, entries, text)
    run("offer_phrase", "turn_main", check_offer, text)
    run("stacks_down", "turn_main", check_stacks, path)
    run("brief_render", "turn_brief", check_brief, entries, text)
    run("prs_actions", "turn_main", check_prs_actions, entries)
    return emit(event.get("hook_event_name"), hits, event.get("session_id"), event.get("agent_id") or "") if hits else None


def telegram(event, cfg):
    if not enabled(cfg)["telegram_render"] or not re.search(r"telegram.*reply$", event.get("tool_name") or "", re.I):
        return None
    text = (event.get("tool_input") or {}).get("text")
    path = event.get("transcript_path")
    if not isinstance(text, str) or not report_shaped(text) or not path or not os.path.isfile(path):
        return None
    if not roles.applies("turn_brief", roles.session(event)):
        return None
    if has_run(turn(path)[1], render_ran):
        return None
    msg = "Telegram 보고가 현황 보고 모양인데 이번 턴에 `orch brief render`가 없다 (brief §1)"
    config.append_warning({"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                           "event": "PreToolUse", "session": event.get("session_id"), "agent": event.get("agent_id") or "",
                           "check": "telegram_render", "level": "warn"})
    return {"systemMessage": f"turn_check: {msg}"}


def main():
    event = json.load(sys.stdin)
    cfg = config.load()
    if event.get("hook_event_name") == "PreToolUse":
        out = telegram(event, cfg)
    elif event.get("stop_hook_active"):
        return
    else:
        out = stop(event, cfg)
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        config.log_error("turn_check", exc)
    sys.exit(0)
