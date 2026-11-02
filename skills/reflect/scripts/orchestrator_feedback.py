#!/usr/bin/env python3
"""The owner's prompts from orchestrator transcripts, for reflect's standing mode.

Usage: orchestrator_feedback.py digest
       orchestrator_feedback.py advance --round ROUND --expect PREVIOUS_ROUND
       orchestrator_feedback.py wake      why a standing round should read orchestrator feedback now; exit 0 if any

An orchestrator session runs for weeks and is never closed, so the session-mode reflect that runs at the end of a
session never sees the owner's corrections there. digest reads the transcripts the reorient hook registered
($AGENT_SKILLS_STATE/transcripts/<session_id>.json, only while `reflect.orchestrator_feedback` is on), within the span
each session held the orchestrator role and after the byte each was reviewed up to, and writes one JSONL row per owner
prompt with the context a reviewer needs to route it: the previous assistant text, the tools called since the last
prompt, and the card, ticket and PR identifiers seen. It never globs ~/.claude/projects.

The cursor, $PROGRAMS_HOME/_standing/reflect/orchestrator-cursor.json, keeps per session the end of the contiguous
range already reviewed (`scan_end`), not the last row written: a tail with no owner prompt moves it too, and a round
that runs out of room stops at the first prompt it could not take (`pending`). digest proposes the next cursor in a file
of its own round; advance commits that proposal only when the cursor still names the round digest read
(compare-and-swap under a file lock, since a stale standing lock lets two rounds overlap), then moves the round's digest
to the trash: it holds the owner's words, and only pointers go to the lessons ledger.

With the feature off, or with no `reflect.ledger`, digest prints the reason and reads nothing. It stops when the
digest or the ledger would land inside `reflect.skill_repository`.
"""
import datetime as dt, fcntl, json, os, pathlib, re, secrets, shutil, subprocess, sys

MAX_ROWS, MAX_BYTES = 200, 200_000  # per round, shared across sessions
FIRST_DAYS = 7  # how far back a session's first round reads
STALE_DAYS = 30  # a session fully read and untouched this long is not opened again
CATCH_UP_HOURS = 24  # a session the cursor has caught up with is read at most this often
PROMPT_CHARS, BEFORE_CHARS, ARG_CHARS, CONTEXT_IDS, TOOLS = 2000, 600, 120, 20, 30
HUMAN = {"human", "channel"}  # origin kinds the owner typed; a channel is the owner's chat bridge
# Machine-written elements removed with their content; the owner's text around them stays.
DROP = ("system-reminder", "task-notification", "local-command-stdout", "local-command-stderr", "local-command-caveat",
        "bash-input", "bash-stdout", "bash-stderr", "command-message", "user-prompt-submit-hook", "agent-message",
        "artifact-content-authored-by-others")
# Wrappers around text the owner wrote or chose: the tags go, the text stays.
UNWRAP = ("command-name", "command-args", "channel", "pasted_content")
DROP_RE = re.compile(r"<(%s)\b[^>]*/>|<(%s)\b[^>]*>.*?</\2>" % ("|".join(DROP), "|".join(DROP)), re.S)
UNWRAP_RE = re.compile(r"</?(?:%s)\b[^>]*>" % "|".join(UNWRAP))
INTERRUPT_RE = re.compile(r"^\[Request interrupted by user[^\]]*\]$")
ROUND_RE = re.compile(r"orch-\d{8}T\d{6}Z-[0-9a-f]{8}")
CONTEXT_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+|\b[\w.-]+/[\w.-]+#\d+\b"
                        r"|\b[A-Z][A-Z0-9]{1,9}-\d+\b|\blead:[\w.-]+|\brun_[0-9a-f]{6,}\b")


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def config():
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
    try:
        cfg = json.loads(path.read_text()).get("reflect")
    except (OSError, ValueError, AttributeError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def standing():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser() / "_standing" / "reflect"


def registry():
    state = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
    return state / "transcripts"


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def gate(cfg):
    """(exit code, reason) when digest must not read; None when it may."""
    if cfg.get("orchestrator_feedback") is not True:
        return 0, "reflect.orchestrator_feedback is off: orchestrator feedback skipped"
    if not cfg.get("ledger"):
        return 0, "reflect.ledger is not set: orchestrator feedback skipped"
    repo = cfg.get("skill_repository")
    if repo:
        root = pathlib.Path(repo).expanduser().resolve()
        # A relative ledger is a notes-store path (a vault note), not a file this script or the cwd decides.
        ledger = pathlib.Path(cfg["ledger"]).expanduser()
        for label, path in (("the digest folder", standing()), ("reflect.ledger", ledger if ledger.is_absolute() else None)):
            if path is not None and (path.resolve() == root or root in path.resolve().parents):
                return 2, f"stop: {label} is inside reflect.skill_repository, which may be public"
    return None


def clean(text):
    text = UNWRAP_RE.sub("", DROP_RE.sub("", text)).strip()
    return "" if INTERRUPT_RE.match(text) else text


def owner_text(o):
    """The owner's words in one transcript line, "" when the line holds none.

    A line with `origin` is the owner's only when its kind is in HUMAN, whatever isMeta says (a chat-bridge message is
    marked meta); a line without it (an older Claude Code) is judged by shape. A prompt typed while the session was busy
    arrives as a `queued_command` attachment.
    """
    if o.get("type") == "attachment":
        a = o.get("attachment") or {}
        if a.get("type") != "queued_command" or (a.get("origin") or {}).get("kind") not in HUMAN:
            return ""
        prompt = a.get("prompt")
        if isinstance(prompt, list):  # a prompt sent with an image arrives as content blocks
            prompt = "\n".join(b.get("text") or "" for b in prompt if isinstance(b, dict) and b.get("type") == "text")
        return clean(prompt if isinstance(prompt, str) else "")
    if o.get("type") != "user" or o.get("isCompactSummary") or o.get("isSidechain"):
        return ""
    content = (o.get("message") or {}).get("content")
    if isinstance(content, str):
        blocks = [content]
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        blocks = [b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return ""
    if "origin" in o:
        if (o.get("origin") or {}).get("kind") not in HUMAN:
            return ""
    elif o.get("isMeta"):
        return ""
    return "\n".join(t for t in (clean(b) for b in blocks) if t)


def first_arg(value):
    if isinstance(value, dict):
        value = next(iter(value.values()), "")
    return " ".join(str(value).split())[:ARG_CHARS]


def line_at(path, offset):
    """Line count before byte offset, for a `transcript:line` pointer a person can open."""
    n, left = 0, offset
    with open(path, "rb") as f:
        while left > 0:
            chunk = f.read(min(left, 1 << 20))
            if not chunk:
                break
            n, left = n + chunk.count(b"\n"), left - len(chunk)
    return n


def uuid_line(path, uuid):
    """(start, end) of the line carrying uuid, or None."""
    needle, pos = json.dumps(uuid).encode(), 0
    with open(path, "rb") as f:
        for raw in f:
            if needle in raw:
                try:
                    if json.loads(raw).get("uuid") == uuid:
                        return pos, pos + len(raw)
                except ValueError:
                    pass
            pos += len(raw)
    return None


def resume(path, mark, st):
    """Where to continue a session the cursor knows: (byte, note, anchor), anchor being the cursor's uuid and its line
    start in the file as it is now. byte is None when nothing places it."""
    end = mark.get("scan_end", 0)
    if mark.get("inode") == st.st_ino and st.st_size >= end:
        if not mark.get("uuid"):
            return end, "", (None, None)
        try:
            with open(path, "rb") as f:
                f.seek(mark.get("uuid_at", 0))
                ok = json.loads(f.readline()).get("uuid") == mark["uuid"]
        except (OSError, ValueError):
            ok = False
        if ok:
            return end, "", (mark["uuid"], mark.get("uuid_at", 0))
    found = uuid_line(path, mark["uuid"]) if mark.get("uuid") else None
    if found:
        return found[1], "recovered: the transcript changed; continued after the cursor's last message", \
            (mark["uuid"], found[0])
    return None, "recovered: the transcript changed and the cursor's message is gone; reading again from `since`", \
        (None, None)


def scan(session, path, start, lo, hi, max_rows, max_bytes, seen, budget, carry=None, line=None):
    """Rows of owner prompts from byte start, and the cursor mark for the range reviewed.

    max_rows and max_bytes are this session's share; budget ({"rows", "bytes"}) is what the whole round has left, and
    only a round's very first row may exceed a share. carry is the context gathered since the last owner prompt before
    start, so a prompt right after a round's boundary keeps the assistant text, tools and identifiers that led to it.
    line is the number of lines before start when the cursor knows it."""
    rows, used, pos, pending = [], 0, start, False
    last = (None, None)  # uuid and start byte of the last message line reviewed
    carry = carry or {}
    before, tools, ids = carry.get("before", ""), list(carry.get("tools", [])), list(carry.get("ids", []))
    count = line_at(path, start) if line is None else line  # lines before pos
    with open(path, "rb") as f:
        f.seek(start)
        while True:
            raw = f.readline()
            if not raw.endswith(b"\n"):  # end of file, or a line still being written
                break
            at, line = pos, count + 1
            try:
                o = json.loads(raw)
            except ValueError:
                o = None
            if not isinstance(o, dict):
                pos, count = pos + len(raw), count + 1
                continue
            text = owner_text(o)
            ts = parse_ts(o.get("timestamp"))
            if o.get("type") == "assistant":
                for b in (o.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        before = b["text"]
                        ids += CONTEXT_RE.findall(b["text"])
                    elif b.get("type") == "tool_use":
                        tools.append(f"{b.get('name')} {first_arg(b.get('input') or {})}".strip())
                        ids += CONTEXT_RE.findall(json.dumps(b.get("input") or {}, ensure_ascii=False))
            elif text:
                uuid = o.get("uuid")
                event = f"{session}:{uuid}"
                if uuid and ts and (lo is None or ts >= lo) and (hi is None or ts < hi) and event not in seen:
                    ids += CONTEXT_RE.findall(text)
                    row = {"event": event, "ts": iso(ts), "transcript": str(path), "line": line,
                           "prompt": text[:PROMPT_CHARS], "before": before[-BEFORE_CHARS:], "tools": tools[-TOOLS:],
                           "context": list(dict.fromkeys(ids))[:CONTEXT_IDS]}
                    size = len(json.dumps(row, ensure_ascii=False).encode()) + 1
                    first = not seen  # the round's first row always fits, so a round always moves
                    if not first and (len(rows) >= max_rows or used + size > max_bytes
                                      or budget["rows"] < 1 or size > budget["bytes"]):
                        pending = True
                        break
                    rows.append(row)
                    seen.add(event)
                    used += size
                    budget["rows"], budget["bytes"] = budget["rows"] - 1, budget["bytes"] - size
                before, tools, ids = "", [], []
            if o.get("uuid"):
                last = (o["uuid"], at)
            pos, count = pos + len(raw), count + 1
    carry = {"before": before[-BEFORE_CHARS:], "tools": tools[-TOOLS:], "ids": list(dict.fromkeys(ids))[-CONTEXT_IDS:]}
    return rows, {"scan_end": pos, "line": count, "uuid": last[0], "uuid_at": last[1], "pending": pending,
                  "carry": carry}


def span_done(reg, mark):
    """The span closed (`until`) and a later scan read past it, so the transcript's growth is not the orchestrator's."""
    until, scanned = parse_ts(reg.get("until")), parse_ts((mark or {}).get("scanned_at"))
    return bool(until and scanned and scanned >= until and not mark.get("pending"))


def digest(now=None):
    now = now or now_utc()
    cfg = config()
    stop = gate(cfg)
    if stop:
        print(json.dumps({"skipped": stop[1]}))
        return stop[0]
    home = standing()
    cursor = read_json(home / "orchestrator-cursor.json", {})
    marks = dict(cursor.get("sessions") or {})
    entries = []
    for file in sorted(registry().glob("*.json")):
        reg = read_json(file, {})
        if isinstance(reg, dict) and reg.get("role") == "orchestrator" and reg.get("transcript"):
            entries.append((file.stem, reg))
    report, rows, seen, live = {}, [], set(), []
    for session, reg in entries:
        path = pathlib.Path(reg["transcript"]).expanduser()
        try:
            st = path.stat()
        except OSError:
            report[session] = {"note": "transcript missing"}
            continue
        mark = marks.get(session)
        same = mark and mark.get("inode") == st.st_ino and st.st_size >= mark.get("scan_end", 0)
        if same and (span_done(reg, mark) or (not mark.get("pending") and mark.get("scan_end") == st.st_size
                                              and now.timestamp() - st.st_mtime > STALE_DAYS * 86400)):
            continue
        live.append((session, reg, path, st, mark))
    # The session that moved least recently goes first, so a share too small for one row cannot starve a session.
    live.sort(key=lambda x: (x[4] or {}).get("moved_at") or "")
    share, budget = max(len(live), 1), {"rows": MAX_ROWS, "bytes": MAX_BYTES}
    for session, reg, path, st, mark in live:
        since, until = parse_ts(reg.get("since")), parse_ts(reg.get("until"))
        note, carry, anchor, line = "", None, (None, None), None
        if mark:
            start, note, anchor = resume(path, mark, st)
            lo, carry = since, mark.get("carry")
            line = None if note else mark.get("line")
            if start is None:
                start, carry = 0, None
        else:
            start, first = 0, now - dt.timedelta(days=FIRST_DAYS)
            lo = max(since, first) if since else first
        found, new = scan(session, path, start, lo, until, MAX_ROWS // share, MAX_BYTES // share, seen, budget,
                          carry, line)
        rows += found
        if not new["uuid"]:  # no message read: keep the anchor that places scan_end
            new["uuid"], new["uuid_at"] = anchor
        moved = new["scan_end"] != start or not mark
        new["moved_at"] = iso(now) if moved else (mark or {}).get("moved_at")
        marks[session] = {**new, "inode": st.st_ino, "scanned_at": iso(now)}
        report[session] = {"from": start, "to": new["scan_end"], "rows": len(found), "pending": new["pending"],
                           **({"note": note} if note else {})}
    prev = cursor.get("round")
    round_id = f"orch-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    out = None
    if rows:
        out = home / f"orchestrator-{round_id}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    write_atomic(home / f"orchestrator-next-{round_id}.json",
                 {"round": round_id, "sessions": marks, "digest": str(out) if out else None})
    print(json.dumps({"round": round_id, "expect": prev, "digest": str(out) if out else None, "rows": len(rows),
                      "sessions": report, **({} if entries else {"note": "no orchestrator transcript registered"})},
                     ensure_ascii=False))
    return 0


def wake(now=None, home=None):
    """Reasons to wake a standing round for orchestrator feedback, from file metadata only; [] with the feature off.

    A pending session, or one the cursor does not know yet, wakes at once; a session that grew wakes once its last scan
    is CATCH_UP_HOURS old; a replaced or shrunk transcript wakes so the round recovers it."""
    now = now or now_utc()
    if gate(config()):
        return []
    base = home / "_standing" / "reflect" if home else standing()
    marks = read_json(base / "orchestrator-cursor.json", {}).get("sessions") or {}
    out = []
    for file in sorted(registry().glob("*.json")):
        reg = read_json(file, {})
        if not (isinstance(reg, dict) and reg.get("role") == "orchestrator" and reg.get("transcript")):
            continue
        try:
            st = pathlib.Path(reg["transcript"]).expanduser().stat()
        except OSError:
            continue
        session, mark = file.stem, marks.get(file.stem)
        if not mark:
            out.append(f"{session}: new session")
        elif mark.get("pending"):
            out.append(f"{session}: pending")
        elif mark.get("inode") != st.st_ino or st.st_size < mark.get("scan_end", 0):
            out.append(f"{session}: transcript replaced or truncated")
        elif st.st_size > mark.get("scan_end", 0) and not span_done(reg, mark):
            scanned = parse_ts(mark.get("scanned_at"))
            if not scanned or now - scanned >= dt.timedelta(hours=CATCH_UP_HOURS):
                out.append(f"{session}: grew since {mark.get('scanned_at')}")
    return out


def trash(path):
    folder = os.environ.get("AGENT_SKILLS_TRASH")
    if not folder and os.access("/usr/bin/trash", os.X_OK):
        subprocess.run(["/usr/bin/trash", str(path)], capture_output=True, check=True, stdin=subprocess.DEVNULL)
        return
    dest = pathlib.Path(folder or "~/.Trash").expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / path.name
    if target.exists():
        target = dest / f"{path.name}.{os.getpid()}"
    shutil.move(str(path), target)


def advance(round_id, expect):
    """Commits round_id's proposed cursor if the cursor still names expect. Either way the round is over: its proposal
    goes, and its digest goes to the trash (its findings are in the lessons ledger, or the next round reads again)."""
    if not ROUND_RE.fullmatch(round_id):
        print(f"not a round id: {round_id!r}")
        return 2
    home = standing()
    cursor_path, next_path = home / "orchestrator-cursor.json", home / f"orchestrator-next-{round_id}.json"
    proposal = read_json(next_path, {})
    if proposal.get("round") != round_id:
        print(f"no proposed cursor for round {round_id}; nothing written")
        return 1
    home.mkdir(parents=True, exist_ok=True)
    with open(home / "orchestrator-cursor.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # held until the file closes, so check and replace are one step
        found = read_json(cursor_path, {}).get("round")
        moved = (None if expect in ("", "none") else expect) != found
        if not moved:
            write_atomic(cursor_path, {k: v for k, v in proposal.items() if k != "digest"})
        next_path.unlink()
    digest_path = proposal.get("digest")
    if moved:
        print(f"cursor moved: expected round {expect}, found {found}; nothing written, the next round reads again")
    if digest_path and pathlib.Path(digest_path).exists():
        try:
            trash(pathlib.Path(digest_path))
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"the digest stays at {digest_path} ({e})")
    if moved:
        return 1
    print(f"cursor advanced to round {round_id}")
    return 0


def main(argv):
    if argv[:1] == ["digest"]:
        return digest()
    if argv[:1] == ["wake"]:
        stop = gate(config())
        if stop:
            print(f"skipped: {stop[1]}")
            return stop[0] or 1
        reasons = wake()
        print("\n".join(reasons) or "nothing to read")
        return 0 if reasons else 1
    if argv[:1] == ["advance"] and "--round" in argv and "--expect" in argv:
        opt = lambda k: argv[argv.index(k) + 1] if argv.index(k) + 1 < len(argv) else ""
        return advance(opt("--round"), opt("--expect"))
    print("\n".join(__doc__.splitlines()[2:5]), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
