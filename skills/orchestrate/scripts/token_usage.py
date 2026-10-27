#!/usr/bin/env python3
"""Token use from local Claude Code transcripts, attributed to what spent it (docs/token-usage.md §2).

Usage: token_usage.py [--days N] [--json] [--no-collect]
       (also `python3 scripts/skill_tokens.py usage …`)

collect() reads each transcript from where the last collect stopped (byte offset, inode and mtime per file in
<fleet state>/token-usage-cache.json) and adds every API call's usage into day buckets on two axes:
  who   subagent:<type> > automation:<name> (first prompt matches a registered Orca automation's prompt) >
        orchestrator / lead:<slug> (the role record reorient.py keeps) > interactive; unclassified before a prompt
  what  skill:<name> (attributionSkill, else the last Skill call of the turn) and origin:<turnOrigin>
plus this plugin's hook injections (count × approximate size) and, per automation session, whether it did anything
outward. report() turns the buckets into tables, trends and warnings. Only numbers, names and hashes are kept: no
prompt or message text is stored or printed. Nothing here calls a model.
A file whose inode changed or that shrank is read again from the start while the days already added stay, so a
file replaced by the same content counts twice; transcripts are append-only, so this is rare.
"""
import datetime as dt, hashlib, json, os, pathlib, re, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))
import fleet  # noqa: E402
try:
    import skill_tokens  # noqa: E402
except ImportError:  # an install without the repo's scripts/: no static sizes, no hook sizes
    skill_tokens = None

CACHE_VERSION = 2
# Input-token equivalents of each usage field, from list prices: a cache read costs a tenth of input, a cache write
# 1.25 (5 min) or 2 (1 h) times, output five times.
WEIGHTS = {"input": 1.0, "cache_5m": 1.25, "cache_1h": 2.0, "cache_read": 0.1, "output": 5.0}
KEEP_DAYS = 120
IDS_KEPT = 64  # a message's usage repeats on each of its content-block lines, which come in a row
PROMPT_KEY_CHARS = 120
NOT_FEATURES = ("interactive", "unclassified")
DEFAULTS = {"daily_share": 0.3, "daily_min": 50_000_000, "surge": 1.5, "surge_min": 20_000_000,
            "empty_runs": 7, "empty_window": 10}
OUTWARD_BASH = re.compile(r"\borca\s+orchestration\s+(?:send|reply)\b|\bgh\s+pr\s+(?:merge|comment|review|edit|create)\b"
                          r"|\bgh\s+api\b[^\n]*-X\s*(?:POST|PATCH|PUT|DELETE)|\bgit\s+(?:commit|push)\b"
                          r"|\borca\s+(?:worktree\s+rm|terminal\s+close)\b")
# The janitor's chrome-window targets are closed by the agent with its browser tools, not by reap.
OUTWARD_TOOL = re.compile(r"slack.*(?:send|schedule)|(?:save|create|add)_?(?:comment|issue|message)|tabs_close", re.I)
# A janitor round's work is `reap.py reap --plan`, which prints one `- <target>: <outcome>` line per target whether it
# acted or not, so the call counts only when its output has a line that is none of these (reap-resources reap.py).
REAP_CALL = re.compile(r"(?:\breap\.py[\"']?|\"\$R\")\s+reap\b")
REAP_NOT_DONE_LINES = ("건너뜀 ", "실패 ", "메모: ")
REAP_NOT_DONE_OUTCOMES = ("건너뜀", "사라졌거나 다른 프로세스", "tip 이 바뀜", "거부됨")
REAP_DONE = re.compile(r"^- (?!%s)(?![^\n]*: (?:%s))[^\n]*: \S" % ("|".join(map(re.escape, REAP_NOT_DONE_LINES)),
                                                       "|".join(map(re.escape, REAP_NOT_DONE_OUTCOMES))), re.M)
SLASH = re.compile(r"<command-name>/?([^<]*)</command-name>(?:.*?<command-args>(.*?)</command-args>)?", re.S)
LINE_KEYS = ('"usage"', '"type":"user"', "hook_success")


def projects_dir():
    d = os.environ.get("CLAUDE_PROJECTS_DIR")
    if d:
        return pathlib.Path(d).expanduser()
    return pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "projects"


def cache_path():
    return fleet.state_dir() / "token-usage-cache.json"


def out_path():
    return fleet.state_dir() / "tokens.json"


def roles_dir():
    return pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "roles"


def settings():
    p = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    cfg = (fleet.read_json(p, {}) or {}).get("token_usage") or {}
    return {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS and isinstance(v, (int, float))}}


def prompt_key(text):
    return hashlib.sha1(re.sub(r"\s+", " ", text or "").strip()[:PROMPT_KEY_CHARS].encode()).hexdigest()[:16]


def automations(skill_names, run=subprocess.run):
    """{prompt key: [automation name, the skill its prompt names first]} from `orca automations list`; {} without Orca."""
    try:
        r = run(["orca", "automations", "list", "--json"], capture_output=True, text=True, timeout=15)
        rows = ((json.loads(r.stdout) or {}).get("result") or {}).get("automations") or [] if r.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    found = {}
    for a in rows:
        prompt, name = a.get("prompt") or "", a.get("name") or a.get("id") or "?"
        hits = [(m.start(), s) for s in skill_names for m in [re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", prompt[:80])] if m]
        skill = min(hits)[1] if hits else None
        found[prompt_key(prompt)] = [name, skill]
    return found


def static_report():
    return skill_tokens.measure(REPO) if skill_tokens else {"skills": [], "invoke": []}


def hook_names(root=REPO):
    return set(skill_tokens.hook_scripts(root)) if skill_tokens else set()


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return None


def _usage(u):
    cc = u.get("cache_creation") or {}
    c5, c1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
    if c5 is None and c1 is None:
        c5, c1 = u.get("cache_creation_input_tokens") or 0, 0
    return {"input": u.get("input_tokens") or 0, "cache_5m": c5 or 0, "cache_1h": c1 or 0,
            "cache_read": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0}


def _add(bucket, key, raw, weighted):
    b = bucket.setdefault(key, [0, 0.0, 0])
    b[0] += raw
    b[1] += weighted
    b[2] += 1


def new_state(path):
    st = {"off": 0, "who": "unclassified", "origin": "unknown", "skill": None, "ids": [], "prompted": False, "sid": None}
    if "subagents" in path.parts:
        meta = fleet.read_json(path.with_suffix(".meta.json"), {}) or {}
        st["who"] = "subagent:" + str(meta.get("agentType") or "unknown")
    return st


def who_of(text, sid, autos):
    a = autos.get(prompt_key(text))
    if a:
        return "automation:" + a[0]
    role = fleet.read_json(roles_dir() / f"{sid}.json", None) if sid else None
    if isinstance(role, dict) and role.get("role") == "orchestrator":
        return "orchestrator"
    if isinstance(role, dict) and role.get("role") == "lead":
        return "lead:" + str(role.get("slug") or "?")
    return "interactive"


def scan(path, st, days, sessions, autos, hooks):
    """Reads path from st['off'] to its last complete line, adding into days and sessions. Returns lines parsed."""
    n = 0
    with open(path, "rb") as f:
        f.seek(st["off"])
        data = f.read()
    end = data.rfind(b"\n") + 1
    for raw in data[:end].splitlines():
        line = raw.decode("utf-8", "replace")
        if not any(k in line for k in LINE_KEYS):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        n += 1
        st["sid"] = st["sid"] or d.get("sessionId")
        kind = d.get("type")
        if kind == "user" and not d.get("isMeta"):
            content = (d.get("message") or {}).get("content")
            if st.get("reaps") and isinstance(content, list):
                for b in content:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in st["reaps"]):
                        continue
                    st["reaps"].remove(b["tool_use_id"])
                    out = _text(b.get("content")) if isinstance(b.get("content"), list) else b.get("content")
                    if REAP_DONE.search(str(out or "")) and st["sid"] in sessions:
                        sessions[st["sid"]]["outward"] = True
            text = _text(content)
            if text is None:
                continue
            head = text.lstrip()
            st["origin"] = d.get("turnOrigin") or ("task_notification" if head.startswith("<task-notification") else
                                                   "subagent" if st["who"].startswith("subagent:") else "unknown")
            st["skill"] = None
            command = SLASH.search(head) if head.startswith("<command-") else None
            if command:  # a slash command: the prompt is the command line, its expansion comes as a meta line
                head = "/" + " ".join(x.strip() for x in command.groups() if x and x.strip())
            prompt = head and (not head.startswith("<") or head.startswith("<pasted_content"))
            if prompt and st["who"] in NOT_FEATURES:
                # Only the first prompt can name an automation; a role can be taken later, so it is looked up again.
                first = not st["prompted"]
                st["who"] = who_of(head if first else "", st["sid"], autos if first else {})
                st["prompted"] = True
        elif kind == "assistant":
            msg = d.get("message") or {}
            if st["who"].startswith("automation:") and st["sid"]:
                session = sessions.setdefault(st["sid"], {"who": st["who"], "start": d.get("timestamp"), "outward": False,
                                                          "weighted": 0.0})
            else:
                session = None
            for b in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                name, inp = b.get("name") or "", b.get("input") or {}
                if name == "Skill" and inp.get("skill"):
                    st["skill"] = str(inp["skill"])
                command = str(inp.get("command") or "") if name == "Bash" else ""
                outward = OUTWARD_BASH.search(command) or OUTWARD_TOOL.search(name)
                if outward and session:
                    session["outward"] = True
                elif session and REAP_CALL.search(command) and b.get("id"):
                    st["reaps"] = (st.get("reaps", []) + [b["id"]])[-IDS_KEPT:]
            u, mid = msg.get("usage"), msg.get("id")
            if not isinstance(u, dict) or not mid or mid in st["ids"]:
                continue
            st["ids"] = (st["ids"] + [mid])[-IDS_KEPT:]
            parts = _usage(u)
            total = sum(parts.values())
            weighted = sum(WEIGHTS[k] * v for k, v in parts.items())
            day = str(d.get("timestamp") or "")[:10] or "unknown"
            b = days.setdefault(day, {"who": {}, "skill": {}, "origin": {}, "hooks": {}, "fields": {}})
            skill = d.get("attributionSkill") or st["skill"]
            _add(b["who"], st["who"], total, weighted)
            _add(b["skill"], "skill:" + skill if skill else "none", total, weighted)
            _add(b["origin"], st["origin"], total, weighted)
            for k, v in parts.items():
                b["fields"][k] = b["fields"].get(k, 0) + v
            if session:
                session["weighted"] += weighted
        elif kind == "attachment":
            a = d.get("attachment") or {}
            m = re.search(r"hooks/([\w-]+\.py)", str(a.get("command") or ""))
            if a.get("type") != "hook_success" or not m or m.group(1) not in hooks:
                continue
            try:
                ctx = ((json.loads(a.get("stdout") or "{}") or {}).get("hookSpecificOutput") or {}).get("additionalContext")
            except (ValueError, AttributeError):
                ctx = None
            if ctx:
                day = str(d.get("timestamp") or "")[:10] or "unknown"
                b = days.setdefault(day, {"who": {}, "skill": {}, "origin": {}, "hooks": {}, "fields": {}})
                h = b["hooks"].setdefault(m.group(1), [0, 0])
                h[0] += 1
                h[1] += skill_tokens.approx_tokens(ctx)
    st["off"] += end
    return n


def transcripts(root):
    """The real path of each transcript once: each Claude profile reaches the same files through its own symlinked
    projects folder, and a key by the path it was reached through counted one file once per profile."""
    return sorted({p.resolve() for p in [*root.glob("*/*.jsonl"), *root.glob("*/*/subagents/*.jsonl")]})


def collect(root=None, path=None, now=None, autos=None, hooks=None):
    """Reads what is new in every transcript into the cache and returns it."""
    t0 = time.monotonic()
    root, path = pathlib.Path(root or projects_dir()), pathlib.Path(path or cache_path())
    now = now or fleet.utcnow()
    cache = fleet.read_json(path, {}) or {}
    if cache.get("v") != CACHE_VERSION:
        cache = {"v": CACHE_VERSION, "files": {}, "days": {}, "sessions": {}, "automations": {}}
    if autos is None:
        names = [x["name"] for x in static_report()["skills"]]
        autos = automations(names) or cache.get("automations") or {}
    cache["automations"] = autos
    hooks = hook_names() if hooks is None else hooks
    files, parsed, lines = cache["files"], 0, 0
    for p in transcripts(root):
        try:
            stat = p.stat()
        except OSError:
            continue
        key = hashlib.sha1(str(p).encode()).hexdigest()[:16]
        st = files.get(key)
        if st is None or st.get("ino") != stat.st_ino or stat.st_size < st.get("off", 0):
            st = new_state(p)
        if stat.st_size == st["off"]:
            files[key] = st
            continue
        try:
            lines += scan(p, st, cache["days"], cache["sessions"], autos, hooks)
        except OSError:
            continue
        st["ino"], st["mtime"] = stat.st_ino, int(stat.st_mtime)
        files[key] = st
        parsed += 1
    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    cache["days"] = {k: v for k, v in cache["days"].items() if k >= cutoff}
    cache["sessions"] = {k: v for k, v in cache["sessions"].items() if str(v.get("start") or "")[:10] >= cutoff}
    cache["collected_at"] = fleet.iso(now)
    cache["last"] = {"files": len(files), "parsed": parsed, "lines": lines, "seconds": round(time.monotonic() - t0, 2)}
    fleet.write_atomic(path, json.dumps(cache, separators=(",", ":")))
    return cache


def _window(cache, start, end, axis):
    out = {}
    for day, b in cache["days"].items():
        if start <= day < end:
            for k, (raw, w, n) in b.get(axis, {}).items():
                o = out.setdefault(k, [0, 0.0, 0])
                o[0] += raw
                o[1] += w
                o[2] += n
    return out


def _static_of(key, cache, static):
    skill = None
    if key.startswith("skill:"):
        skill = key.split(":")[-1]
    elif key.startswith("automation:"):
        skill = next((s for n, s in cache.get("automations", {}).values() if "automation:" + n == key), None)
    elif key == "orchestrator" or key.startswith("lead:"):
        skill = "orchestrate"
    listing = {s["name"]: s for s in static.get("skills", [])}.get(skill)
    body = {x["skill"]: x for x in static.get("invoke", [])}.get(skill)
    if not listing:
        return None
    return {"skill": skill, "listing_tokens": listing["tokens"], "listing_width": listing["width"],
            "skill_md_tokens": body["tokens"] if body else 0}


def report(cache, days=7, now=None, static=None, cfg=None):
    now = now or fleet.utcnow()
    cfg = cfg or settings()
    static = static if static is not None else static_report()
    day = lambda n: (now - dt.timedelta(days=n)).strftime("%Y-%m-%d")
    end, start, prev = day(-1), day(days - 1), day(2 * days - 1)

    def table(axis):
        cur, old = _window(cache, start, end, axis), _window(cache, prev, start, axis)
        tot = sum(v[1] for v in cur.values()) or 1
        rows = [{"key": k, "tokens": v[0], "weighted": round(v[1]), "calls": v[2], "share": round(v[1] / tot, 4),
                 "prev_weighted": round(old.get(k, [0, 0.0])[1]),
                 "trend": round(v[1] / old[k][1] - 1, 3) if old.get(k) and old[k][1] else None}
                for k, v in cur.items()]
        return sorted(rows, key=lambda r: -r["weighted"])

    who, skill, origin = table("who"), table("skill"), table("origin")
    hooks, fields = {}, {}
    for d, b in cache["days"].items():
        if start <= d < end:
            for k, (n, t) in b.get("hooks", {}).items():
                h = hooks.setdefault(k, {"name": k, "count": 0, "tokens": 0})
                h["count"] += n
                h["tokens"] += t
            for k, v in b.get("fields", {}).items():
                fields[k] = fields.get(k, 0) + v
    runs = {}
    for s in sorted(cache["sessions"].values(), key=lambda s: str(s.get("start"))):
        runs.setdefault(s["who"], []).append(s)
    autos = []
    for k, rs in sorted(runs.items()):
        recent, inwin = rs[-int(cfg["empty_window"]):], [s for s in rs if start <= str(s.get("start"))[:10] < end]
        autos.append({"key": k, "rounds": len(inwin), "recent": len(recent), "empty_recent": sum(not s["outward"] for s in recent),
                      "weighted_per_round": round(sum(s["weighted"] for s in inwin) / len(inwin)) if inwin else 0})
    warnings = []
    for n in (1, 0):  # yesterday, and today so far
        d = day(n)
        who_d, skill_d = _window(cache, d, day(n - 1), "who"), _window(cache, d, day(n - 1), "skill")
        tot = sum(v[1] for v in who_d.values()) or 1
        for k, v in [*who_d.items(), *skill_d.items()]:
            if k not in NOT_FEATURES and k != "none" and v[1] >= cfg["daily_min"] and v[1] / tot >= cfg["daily_share"]:
                warnings.append({"kind": "daily", "key": k, "day": d, "text": f"{k} {d} {round(100 * v[1] / tot)}% of the day"})
    for r in who + skill:
        if r["key"] in NOT_FEATURES or r["key"] == "none":
            continue
        if r["weighted"] >= cfg["surge_min"] and r["prev_weighted"] and r["weighted"] >= cfg["surge"] * r["prev_weighted"]:
            warnings.append({"kind": "surge", "key": r["key"], "text": f"{r['key']} {r['trend']:+.0%} vs the {days} days before"})
    for a in autos:
        if a["recent"] >= cfg["empty_window"] and a["empty_recent"] >= cfg["empty_runs"]:
            warnings.append({"kind": "empty_runs", "key": a["key"],
                             "text": f"{a['key']} {a['empty_recent']}/{a['recent']} recent rounds did nothing outward"})
    seen = set()
    warnings = [w for w in warnings if not ((w["kind"], w["key"]) in seen or seen.add((w["kind"], w["key"])))]
    for w in warnings:
        w["static"] = _static_of(w["key"], cache, static)
    total = sum(r["weighted"] for r in who) or 1
    unclassified = {"who": round(sum(r["weighted"] for r in who if r["key"] == "unclassified") / total, 4),
                    "origin": round(sum(r["weighted"] for r in origin if r["key"] == "unknown") / total, 4)}
    return {"generated_at": fleet.iso(now), "days": days, "window": [start, end], "weights": WEIGHTS,
            "totals": {"tokens": sum(r["tokens"] for r in who), "weighted": round(sum(r["weighted"] for r in who)),
                       "calls": sum(r["calls"] for r in who), "fields": fields},
            "unclassified": unclassified, "who": who, "skill": skill, "origin": origin,
            "hooks": sorted(hooks.values(), key=lambda h: -h["tokens"]), "automations": autos, "warnings": warnings,
            "collect": cache.get("last"), "collected_at": cache.get("collected_at")}


def human(n):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= size:
            return f"{n / size:.1f}{unit}"
    return str(round(n))


def render(rep, top=10):
    t = rep["totals"]
    out = [f"{rep['window'][0]} .. {rep['window'][1]} (UTC days, end exclusive) · {human(t['tokens'])} tokens, "
           f"{human(t['weighted'])} weighted (input-equivalent), {t['calls']:,} API calls",
           f"unclassified: who {rep['unclassified']['who']:.1%}, origin {rep['unclassified']['origin']:.1%}", ""]
    for axis, label in (("who", "WHO"), ("skill", "SKILL IN EFFECT"), ("origin", "TURN ORIGIN")):
        out.append(f"{label:<34} {'tokens':>8} {'weighted':>9} {'share':>6} {'trend':>7}")
        for r in rep[axis][:top]:
            trend = f"{r['trend']:+.0%}" if r["trend"] is not None else "new"
            out.append(f"  {r['key'][:32]:<32} {human(r['tokens']):>8} {human(r['weighted']):>9} {r['share']:>6.1%} {trend:>7}")
        out.append("")
    if rep["automations"]:
        out.append("AUTOMATION ROUNDS                  in window  empty (recent)  weighted/round")
        out += [f"  {a['key'][:32]:<32} {a['rounds']:>9}  {a['empty_recent']:>5}/{a['recent']:<8} {human(a['weighted_per_round']):>12}"
                for a in rep["automations"]]
        out.append("")
    if rep["hooks"]:
        out.append("HOOK INJECTIONS                    count  tokens (approx.)")
        out += [f"  {h['name']:<32} {h['count']:>5}  {h['tokens']:>8,}" for h in rep["hooks"]]
        out.append("")
    out.append(f"WARNINGS ({len(rep['warnings'])})")
    for w in rep["warnings"]:
        s = w.get("static")
        tail = (f" — static: {s['skill']} listing {s['listing_tokens']} tokens/turn, SKILL.md {s['skill_md_tokens']:,} per load"
                if s else "")
        out.append(f"  {w['kind']:<10} {w['text']}{tail}")
    if not rep["warnings"]:
        out.append("  none")
    return "\n".join(out)


def refresh(days=7):
    """collect() then report(), written to tokens.json for the dashboard and the brief (board.token_line)."""
    cache = collect()
    rep = report(cache, days)
    fleet.write_atomic(out_path(), json.dumps(rep))  # ASCII: read back under any locale
    return rep


def main(argv):
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 7
    cache = fleet.read_json(cache_path(), None) if "--no-collect" in argv else collect()
    if not cache:
        print("no token-usage cache yet: run without --no-collect", file=sys.stderr)
        return 1
    rep = report(cache, days)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if "--json" in argv else render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
