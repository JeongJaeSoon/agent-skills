#!/usr/bin/env python3
"""Skill usage from local Claude Code transcripts: counts, how each skill was triggered, and heuristic misses.

Usage: skill_usage.py [--out PATH] [--signals PATH | --no-signals] [--misses N]

Writes an aggregate report (default <fleet state>/skills.json) and appends one `signal` row per flagged skill
per ISO week to the signals ledger (default $PROGRAMS_HOME/_skill-usage/ledger.jsonl) for the improvement loop.
Aggregates only: no prompt or message text leaves the transcripts, and working directories are kept as hashes.
"""
import datetime as dt, hashlib, json, os, pathlib, re, sys, time

HERE = pathlib.Path(__file__).resolve().parent
REPO_PLUGIN = "agent-skills"
EDITABLE = (REPO_PLUGIN, "user")
SHORT, LONG = 7, 30
MISS_THRESHOLD = 3
SCAN_VERSION = 3  # raise when scan_file counts differently, so cached transcripts are read again
PHRASE_RE = re.compile(r'"([^"\n]{2,80})"|「([^」\n]{2,80})」|(?:^|[\s(,])\'([^\'\n]{2,80})\'')
RULED_OUT_SENTENCE_RE = re.compile(r"(?:not|don't|do not|never)\b", re.I)
SUGGEST = {"unused_30d": ["rewrite-description", "merge", "retire"], "slash_only": ["rewrite-description"],
           "misses": ["rewrite-description"]}


def iso(t):
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def state_dir():
    return pathlib.Path(os.environ.get("ORCH_FLEET_STATE", "~/.local/state/agent-skills/dashboard")).expanduser()


def signals_path():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser() / "_skill-usage" / "ledger.jsonl"


def frontmatter(path):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    out, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith('"'):
                try:
                    val = json.loads(val)
                except ValueError:
                    val = val.strip('"')
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1].replace("''", "'")
            elif val in (">", "|", ">-", "|-"):
                val = ""
            out[key] = val
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def is_specific_phrase(p):
    return len(p.split()) >= 2 or (not p.isascii() and len(p) >= 4)


def phrase_in(p, text):
    # Only ASCII letters bound an ASCII phrase, so a Korean particle right after it ("ship it을") still matches.
    return re.search(r"(?<![a-z0-9_])" + re.escape(p) + r"(?![a-z0-9_])", text) is not None if p.isascii() else p in text


def phrases(desc):
    found = set()
    desc = " ".join(s for s in re.split(r"(?<=[.;])\s+", desc or "") if not RULED_OUT_SENTENCE_RE.match(s))
    for m in PHRASE_RE.finditer(desc):
        p = next(g for g in m.groups() if g).strip().lower()
        if is_specific_phrase(p):
            found.add(p)
    return sorted(found)


def _skills_under(d, plugin, source, inv):
    for f in sorted(pathlib.Path(d).glob("*/SKILL.md")):
        fm = frontmatter(f)
        short = fm.get("name") or f.parent.name
        name = f"{plugin}:{short}" if plugin else short
        inv.setdefault(name, {"name": name, "source": source, "phrases": phrases(fm.get("description", ""))})


def inventory(plugins_json=None, user_dir=None, repo_dir=None):
    plugins_json = pathlib.Path(plugins_json or "~/.claude/plugins/installed_plugins.json").expanduser()
    user_dir = pathlib.Path(user_dir or "~/.claude/skills").expanduser()
    repo_dir = pathlib.Path(repo_dir or os.environ.get("SKILL_USAGE_REPO") or HERE.parents[1])
    inv = {}
    _skills_under(repo_dir, REPO_PLUGIN, REPO_PLUGIN, inv)
    try:
        installed = json.loads(plugins_json.read_text()).get("plugins", {})
    except (OSError, ValueError):
        installed = {}
    for key in sorted(installed):
        plugin = key.split("@", 1)[0]
        if plugin == REPO_PLUGIN:
            continue
        for entry in installed[key]:
            if entry.get("installPath"):
                _skills_under(pathlib.Path(entry["installPath"]) / "skills", plugin, plugin, inv)
    _skills_under(user_dir, None, "user", inv)
    return inv


def resolver(inv):
    by_short = {}
    for name in inv:
        by_short.setdefault(name.rsplit(":", 1)[-1], []).append(name)

    def resolve(raw):
        raw = (raw or "").strip().lstrip("/")
        if raw in inv:
            return raw
        hits = by_short.get(raw.rsplit(":", 1)[-1], [])
        return hits[0] if len(hits) == 1 else raw
    return resolve


def _content(d):
    m = d.get("message")
    return m.get("content") if isinstance(m, dict) else None


def _prompt(d):
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
        return None, None
    c = _content(d)
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return None, None
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return None, None
    m = re.search(r"<command-name>([^<]*)</command-name>", c)
    if m:
        return "command", m.group(1)
    head = c.lstrip()
    # Task notifications, reminders and local command output are user rows the harness writes.
    if (head.startswith("<") and not head.startswith("<pasted_content")) or head.startswith("[Request interrupted"):
        return None, None
    return "human", c


def _hash(s):
    return hashlib.sha1((s or "").encode()).hexdigest()[:12]


def scan_file(path, inv, resolve):
    prompted_by_model = "subagents" in pathlib.Path(path).parts
    events, misses = [], []
    turn = {"ts": None, "hits": set(), "loaded": set()}

    def close():
        for s in turn["hits"] - turn["loaded"]:
            misses.append([turn["ts"], s])

    with open(path, errors="replace") as f:
        for line in f:
            if '"Skill"' not in line and "<command-name>" not in line and not (
                    '"type":"user"' in line and '"tool_use_id"' not in line):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            ts, session, cwd = d.get("timestamp"), d.get("sessionId"), _hash(d.get("cwd"))
            kind, val = _prompt(d)
            if kind:
                close()
                turn = {"ts": ts, "hits": set(), "loaded": set()}
                if kind == "command":
                    name = resolve(val)
                    turn["loaded"].add(name)
                    events.append([ts, name, "slash", session, cwd])
                elif not prompted_by_model:
                    low = re.sub(r"<pasted_content[^>]*>.*?</pasted_content>", " ", val, flags=re.S).lower()
                    turn["hits"] = {n for n, s in inv.items() if any(phrase_in(p, low) for p in s["phrases"])}
                continue
            if d.get("type") != "assistant":
                continue
            content = _content(d)
            for b in content if isinstance(content, list) else []:
                if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill"):
                    continue
                name = resolve((b.get("input") or {}).get("skill"))
                if not name:
                    continue
                # attributionSkill names the skill in effect; it lingers past its turn, so only a skill loaded
                # in this same turn counts as the one that loaded this call.
                by = resolve(d.get("attributionSkill")) if d.get("attributionSkill") else None
                trigger = "chained" if by and by != name and by in turn["loaded"] else "auto"
                turn["loaded"].add(name)
                events.append([ts, name, trigger, session, cwd])
    close()
    return {"events": events, "misses": misses}


def transcripts(projects_dir):
    root = pathlib.Path(projects_dir)
    return sorted([*root.glob("*/*.jsonl"), *root.glob("*/*/subagents/*.jsonl")])


def collect(projects_dir=None, inv=None, cache_path=None, now=None, miss_threshold=MISS_THRESHOLD):
    t0 = time.monotonic()
    projects_dir = pathlib.Path(projects_dir or os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()
    inv = inventory() if inv is None else inv
    cache_path = pathlib.Path(cache_path or state_dir() / "skill-usage-cache.json")
    now = now or dt.datetime.now(dt.timezone.utc)
    resolve = resolver(inv)
    inv_hash = _hash(json.dumps([SCAN_VERSION, inv], sort_keys=True))
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    old = cache.get("files", {}) if cache.get("inv") == inv_hash else {}
    files, parsed = {}, 0
    for p in transcripts(projects_dir):
        try:
            st = p.stat()
        except OSError:
            continue
        key, sig = _hash(str(p)), [st.st_size, int(st.st_mtime)]
        hit = old.get(key)
        if hit and hit["sig"] == sig:
            files[key] = hit
            continue
        try:
            files[key] = {"sig": sig, **scan_file(p, inv, resolve)}
        except OSError:
            continue
        parsed += 1
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(cache_path, json.dumps({"inv": inv_hash, "files": files}))

    events = [e for f in files.values() for e in f["events"]]
    misses = [m for f in files.values() for m in f["misses"]]
    called = {e[1] for e in events if e[2] != "slash"}
    rows = {n: _row(n, s["source"]) for n, s in inv.items()}
    for ts, name, trig, session, cwd in events:
        if name not in rows:
            builtin_command = trig == "slash" and name not in called
            if builtin_command:
                continue
            rows[name] = _row(name, "other")
        t = parse_ts(ts)
        if not t:
            continue
        r = rows[name]
        r["uses_total"] += 1
        r["last_used"] = max(r["last_used"] or "", ts)
        age = (now - t).days
        if age < LONG:
            r["uses_30d"] += 1
            r[f"{trig}_30d"] += 1
            r["_sessions"].add(session)
            r["_repos"].add(cwd)
            if age < SHORT:
                r["uses_7d"] += 1
    for ts, name in misses:
        t = parse_ts(ts)
        if t and name in rows and (now - t).days < LONG:
            rows[name]["misses_30d"] += 1
    for r in rows.values():
        r["sessions_30d"], r["repos_30d"] = len(r.pop("_sessions")), len(r.pop("_repos"))
        r["flags"] = flags(r, miss_threshold)

    order = [REPO_PLUGIN] + sorted({r["source"] for r in rows.values()} - {REPO_PLUGIN, "user", "other"}) + ["user", "other"]
    rank = {s: i for i, s in enumerate(order)}
    skills = sorted(rows.values(), key=lambda r: (rank[r["source"]], -r["uses_30d"], r["name"]))
    sources = [{"source": s, "skills": sum(r["source"] == s for r in skills),
                "uses_30d": sum(r["uses_30d"] for r in skills if r["source"] == s),
                "flagged": sum(bool(r["flags"]) for r in skills if r["source"] == s)}
               for s in order if any(r["source"] == s for r in skills)]
    return {"generated_at": iso(now), "windows": {"short": SHORT, "long": LONG}, "miss_threshold": miss_threshold,
            "editable": list(EDITABLE), "files": len(files), "files_parsed": parsed,
            "scan_seconds": round(time.monotonic() - t0, 2), "sources": sources, "skills": skills}


def _row(name, source):
    return {"name": name, "source": source, "uses_7d": 0, "uses_30d": 0, "uses_total": 0, "auto_30d": 0,
            "slash_30d": 0, "chained_30d": 0, "last_used": None, "misses_30d": 0, "_sessions": set(), "_repos": set()}


def flags(r, miss_threshold=MISS_THRESHOLD):
    out = []
    if r["source"] != "other" and r["uses_30d"] == 0:
        out.append("unused_30d")
    if r["uses_30d"] and r["auto_30d"] + r["chained_30d"] == 0:
        out.append("slash_only")
    if r["misses_30d"] > miss_threshold:
        out.append("misses")
    return out


def signal_rows(report, now, editable=EDITABLE):
    """The lessons ledger in the notes store is reflect's to write, never a script's."""
    y, w, _ = now.isocalendar()
    rows = []
    for r in report["skills"]:
        if r["source"] not in editable:
            continue
        for fl in r["flags"]:
            rows.append({"ts": iso(now), "ev": "signal", "kind": "skill_usage", "skill": r["name"], "source": r["source"],
                         "flag": fl, "suggest": SUGGEST[fl], "evidence": f"skill-usage:{r['name']}:{fl}@{y}-W{w:02d}",
                         "note": f"{fl}; suggest: {'|'.join(SUGGEST[fl])}; {r['uses_30d']} uses in 30 days ({r['auto_30d']} auto, {r['slash_30d']} slash, "
                                 f"{r['chained_30d']} chained), {r['misses_30d']} misses, last used {r['last_used'] or 'never'}"})
    return rows


def write_signals(path, report, now, editable=EDITABLE):
    path = pathlib.Path(path)
    seen = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                seen.add(row.get("evidence"))
    new = [r for r in signal_rows(report, now, editable) if r["evidence"] not in seen]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in new)
    return new


def write_atomic(path, text):
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def refresh(out=None, signals=True, miss_threshold=MISS_THRESHOLD):
    rep = collect(miss_threshold=miss_threshold)
    out = pathlib.Path(out or state_dir() / "skills.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    if signals:
        path = signals_path() if signals is True else pathlib.Path(signals)
        rep["signals"] = {"added": len(write_signals(path, rep, parse_ts(rep["generated_at"])))}
    write_atomic(out, json.dumps(rep, ensure_ascii=False))
    return rep


def main(argv):
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default
    signals = False if "--no-signals" in argv else (opt("--signals") or True)
    rep = refresh(opt("--out"), signals, int(opt("--misses", MISS_THRESHOLD)))
    flagged = [r for r in rep["skills"] if r["flags"]]
    print(f"skills: {len(rep['skills'])} across {len(rep['sources'])} sources · {rep['files']} transcripts "
          f"({rep['files_parsed']} parsed in {rep['scan_seconds']}s) · {len(flagged)} flagged"
          + (f" · {rep['signals']['added']} new signals" if rep.get("signals") else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
