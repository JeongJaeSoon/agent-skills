#!/usr/bin/env python3
"""Bookkeeping for watch-mentions: run lock, cursor, seen ledger, the coordinator message.

Usage:
  python3 mentions.py precheck
  python3 mentions.py plan [--now UNIX]
  python3 mentions.py ingest CANDIDATES.json
  python3 mentions.py bundle ITEMS.json
  python3 mentions.py commit [ITEMS.json]
  python3 mentions.py schedule [--workspace SELECTOR] [--provider claude] [--write]

Slack is read by the agent through its Slack MCP tools; this script never talks to Slack.
plan takes the run lock and prints the searches to run. ingest drops what must not be reported
(the owner's own messages, non-public channels, excluded channels, anything seen before or older
than the window). bundle renders the one coordinator message. commit marks the items seen,
moves the cursor to the run's start and releases the lock; with no file it only does the last two.
Config: ~/.claude/agent-skills.json → "mentions" (see SKILL.md). State: $AGENT_SKILLS_STATE/mentions
(default ~/.local/state/agent-skills/mentions). The ledger holds ids and categories, never message text.
"""
import datetime as dt, json, os, pathlib, shlex, subprocess, sys, time

CONFIG = pathlib.Path("~/.claude/agent-skills.json").expanduser()
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "mentions"
CATEGORIES = {"review": "리뷰 의뢰", "work": "작업 의뢰", "question": "질문", "decision": "결정·공유", "chat": "잡담"}
KINDS = ("mention", "name", "thread")
DEFAULTS = {"names": [], "channels": [], "exclude_channels": [], "read_private": False, "lookback_hours": 24,
            "thread_lookback_days": 7, "max_threads": 30, "overlap_minutes": 10, "lock_minutes": 60,
            "keep_days": 30, "summary_chars": 120, "cron": "*/20 9-20 * * 1-5", "timezone": None, "inbox_to": None}


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def config(path=CONFIG):
    try:
        raw = json.loads(pathlib.Path(path).read_text()).get("mentions") or {}
    except (OSError, ValueError):
        raw = {}
    return {**DEFAULTS, **raw}


def read_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def key(item):
    return f"{item['channel_id']}/{item['ts']}"


def day_before(unix):
    # Slack's after:D is day-granular and excludes D itself; the ts check in ingest does the exact cut.
    return (dt.datetime.fromtimestamp(float(unix), dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")


def lock_held(state, cfg, now):
    lock = read_json(state / "lock.json", None)
    return bool(lock) and now - lock.get("run", 0) < cfg["lock_minutes"] * 60


def precheck(cfg, state, now):
    """Exit code for the Orca precheck. Slack is reachable only through MCP, so this cannot count new messages."""
    if not cfg.get("user_id"):
        return 1, "mentions.user_id 가 설정되지 않음"
    if lock_held(state, cfg, now):
        return 1, "이전 회차가 아직 실행 중(lock)"
    return 0, "실행"


def plan(cfg, state, now):
    cursor = read_json(state / "cursor.json", {})
    if "until" in cursor:
        oldest = cursor["until"] - cfg["overlap_minutes"] * 60
    else:
        oldest = now - cfg["lookback_hours"] * 3600
    write_json(state / "lock.json", {"run": now, "oldest": oldest})
    uid = cfg["user_id"]
    scope = " ".join(f"in:<#{c}>" for c in cfg["channels"])
    after = f"after:{day_before(oldest)}"
    searches = [{"kind": "mention", "keywords": [f"<@{uid}>"], "filters": f"{after} {scope}".strip()}]
    searches += [{"kind": "name", "keywords": [n], "filters": f"{after} -from:<@{uid}> {scope}".strip()} for n in cfg["names"]]
    thread_after = day_before(now - cfg["thread_lookback_days"] * 86400)
    searches.append({"kind": "thread", "keywords": [], "filters": f"from:<@{uid}> is:thread after:{thread_after} {scope}".strip(),
                     "then": f"slack_read_thread per distinct (channel, thread_ts), oldest={oldest:.6f}, at most {cfg['max_threads']} threads"})
    return {"run": now, "oldest": oldest, "user_id": uid, "read_private": bool(cfg["read_private"]),
            "max_threads": cfg["max_threads"], "searches": searches}


def ingest(candidates, cfg, state):
    lock = read_json(state / "lock.json", None)
    if not lock:
        raise SystemExit("ingest: no run in progress; run plan first")
    seen = read_json(state / "seen.json", {})
    allow, deny = set(cfg["channels"]), set(cfg["exclude_channels"])
    kept, dropped = {}, []
    for c in candidates:
        k, why = key(c), None
        if c.get("author_id") == cfg["user_id"]:
            why = "본인 발언"
        elif not c["channel_id"].startswith("C") or c.get("is_private"):
            why = "공개 채널 아님"
        elif c["channel_id"] in deny or (allow and c["channel_id"] not in allow):
            why = "대상 채널 아님"
        elif float(c["ts"]) < lock["oldest"]:
            why = "창 이전"
        elif k in seen:
            why = "이미 보고함"
        if why:
            dropped.append({"key": k, "why": why})
        elif k in kept:
            kept[k]["kinds"] = sorted(set(kept[k]["kinds"]) | {c["kind"]})
        else:
            kept[k] = {**{f: c.get(f) for f in ("channel_id", "ts", "thread_ts", "permalink", "author_id")}, "kinds": [c["kind"]]}
    return list(kept.values()), dropped


def bundle(items, cfg):
    bad = [i.get("permalink") for i in items if i.get("category") not in CATEGORIES]
    if bad:
        raise SystemExit(f"bundle: unknown category for {bad}; use one of {', '.join(CATEGORIES)}")
    counts = {c: sum(i["category"] == c for i in items) for c in CATEGORIES}
    head = " / ".join(f"{CATEGORIES[c]} {n}" for c, n in counts.items() if n)
    lines = [f"Slack 새 항목 {len(items)}건 ({head})"]
    for c in CATEGORIES:
        for i in (x for x in items if x["category"] == c):
            summary = " ".join(str(i["summary"]).split())[: cfg["summary_chars"]]
            extra = ""
            if i.get("pr"):
                extra += f" · PR {i['pr']}"
            if i.get("draft"):
                d = i["draft"]
                dup = ", ".join(d.get("duplicates") or []) or "없음"
                extra += f" · 기표 후보: {d['title']} (의뢰자 {d.get('requester') or '?'}, 중복 후보 {dup})"
            lines.append(f"- [{CATEGORIES[c]}] {summary}{extra} — {i['permalink']}")
    return "\n".join(lines)


def commit(items, cfg, state, now):
    lock = read_json(state / "lock.json", None)
    if not lock:
        raise SystemExit("commit: no run in progress")
    seen = read_json(state / "seen.json", {})
    for i in items:
        seen[key(i)] = {"at": lock["run"], "category": i.get("category"), "permalink": i.get("permalink")}
    horizon = now - cfg["keep_days"] * 86400
    seen = {k: v for k, v in seen.items() if v["at"] >= horizon}
    write_json(state / "seen.json", seen)
    write_json(state / "cursor.json", {"until": lock["run"]})
    (state / "lock.json").unlink()
    return len(items)


def schedule_cmd(script, cfg, workspace, provider="claude"):
    prompt = (f"watch-mentions 회차다. watch-mentions 스킬의 Run 절차를 따른다: python3 {script} plan 으로 시작해 "
              "공개 채널만 검색하고, 새 항목이 없으면 commit 만 하고 아무것도 보내지 않는다.")
    cmd = ["orca", "automations", "create", "--name", "agent-skills watch-mentions", "--trigger", cfg["cron"],
           "--provider", provider, "--workspace-mode", "existing", "--workspace", workspace,
           "--precheck", " ".join(["env", f"AGENT_SKILLS_STATE={shlex.quote(str(STATE.parent))}",
                                   "python3", shlex.quote(str(script)), "precheck"]),
           "--prompt", prompt, "--json"]
    if cfg.get("timezone"):
        cmd[cmd.index("--provider"):cmd.index("--provider")] = ["--timezone", cfg["timezone"]]
    return cmd


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = config(opt("--config", CONFIG))
    now = float(opt("--now", time.time()))
    if cmd == "precheck":
        code, why = precheck(cfg, STATE, now)
        print(why, file=sys.stderr)
        sys.exit(code)
    if cmd == "plan":
        if not cfg.get("user_id"):
            sys.exit("plan: set mentions.user_id in ~/.claude/agent-skills.json")
        if lock_held(STATE, cfg, now):
            sys.exit("plan: another run holds the lock")
        print(json.dumps(plan(cfg, STATE, now), ensure_ascii=False, indent=1))
    elif cmd == "ingest":
        kept, dropped = ingest(read_json(sys.argv[2], None) or [], cfg, STATE)
        print(json.dumps({"new": kept, "dropped": dropped}, ensure_ascii=False, indent=1))
    elif cmd == "bundle":
        print(bundle(read_json(sys.argv[2], None) or [], cfg))
    elif cmd == "commit":
        items = read_json(sys.argv[2], []) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else []
        print(f"commit: {commit(items, cfg, STATE, now)} items marked seen")
    elif cmd == "schedule":
        script = pathlib.Path(__file__).resolve()
        line = schedule_cmd(script, cfg, opt("--workspace") or f"path:{script.parents[3]}", opt("--provider", "claude"))
        print(shlex.join(line))
        if "--write" in sys.argv:
            sys.exit(subprocess.run(line).returncode)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
