#!/usr/bin/env python3
"""When an orchestrator session should restart, judged from the compactions in its own transcript.

Usage: compactions.py SESSION [CONTEXT]      (prints the compactions read and the verdict; reads the cache)

Claude Code writes a `compact_boundary` row into the transcript at each compaction. `history` reads those rows on from
a cached byte offset, so a session that compacted 100 times is not reread whole every minute. `due` says why a restart
is due now ("" when it is not): a ceiling on the context (`orchestrate.restart_at`, 0 = off), or, from the history,
too many auto compactions, a compaction that no longer shrinks the context, or two close together
(`orchestrate.compact_restart`). The clear band and the inbox stream judge with the same function.

The cache (S/compact-history/<session>.json) holds the compactions read, the offset after the last complete line and
the hash of the 4 KB before it. It is dropped and rebuilt from byte 0 when the transcript is another file, shorter than
the offset, or its last 4 KB changed. It assumes the transcript is append-only, as Claude Code writes it. A cache that
cannot be written costs only the next read.
"""
import dataclasses, datetime as dt, hashlib, json, os, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "hooks"))
import roles  # noqa: E402

CACHE_VERSION = 1
CHUNK = 1 << 20
TAIL = 4096
USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


class TranscriptUnreadable(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Compaction:
    ts: float                # boundary timestamp, epoch seconds (0.0 when the row had none)
    pre: int                 # compactMetadata.preTokens
    after: "int | None"      # context of the first main-chain call after it and before the next boundary


@dataclasses.dataclass(frozen=True)
class Policy:
    compact: bool = True         # the compaction rules are on
    after: int = 3               # auto compactions in this session
    stuck_ratio: float = 0.5     # after / pre at or above: compaction no longer shrinks the context
    min_gap_min: float = 30      # the last two auto compactions closer than this: they come close together
    ceiling: int = 0             # orchestrate.restart_at; 0 = off; independent of `compact`
    auto_clear: bool = False     # orchestrate.auto_clear: the stream may restart on the ceiling
    auto_compact: bool = False   # orchestrate.compact_restart.auto_clear: the stream may restart on the compaction rules

    def streamed(self):
        """What the inbox stream acts on without a human: each rule family only when its own switch is on."""
        return dataclasses.replace(self, compact=self.compact and self.auto_compact,
                                   ceiling=self.ceiling if self.auto_clear else 0)


def _number(v, floor, default, kind=float):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v > floor
    return kind(v) if ok else default


def policy():
    """From ~/.claude/agent-skills.json; a value of the wrong type falls back to its default. Never raises."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        cfg = json.loads(path.read_text()).get("orchestrate") or {}
        raw, cr = cfg.get("restart_at"), cfg.get("compact_restart")
        auto_clear = cfg.get("auto_clear") is True
    except (OSError, ValueError, AttributeError):
        return Policy()
    ceiling = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0
    d = Policy()
    if cr is False:
        return Policy(compact=False, ceiling=ceiling, auto_clear=auto_clear)
    if isinstance(cr, dict):
        return Policy(True, _number(cr.get("after"), 0, d.after, int), _number(cr.get("stuck_ratio"), 0, d.stuck_ratio),
                      _number(cr.get("min_gap_min"), 0, d.min_gap_min), ceiling, auto_clear, cr.get("auto_clear") is True)
    # No usable compact_restart: on, except for an explicit `restart_at: 0`, which has always meant "no suggestion".
    off = isinstance(raw, int) and not isinstance(raw, bool) and raw == 0 and cr is None
    return Policy(compact=not off, ceiling=ceiling, auto_clear=auto_clear)


def transcript(session):
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECTS_DIR") or "~/.claude/projects").expanduser()
    return next(iter(root.glob(f"*/{session}.jsonl")), None) if roles.SESSION.fullmatch(session or "") else None


def usage_context(record):
    """Tokens one main-chain model call read (input + cache read + cache write); None for a subagent's row, a row with
    no usage, or an empty one."""
    if record.get("agentId") or record.get("isSidechain") is True:
        return None
    u = (record.get("message") or {}).get("usage") if isinstance(record.get("message"), dict) else None
    if not isinstance(u, dict):
        return None
    total = sum(u.get(k) or 0 for k in USAGE_KEYS if isinstance(u.get(k), (int, float)))
    return int(total) or None


def _epoch(ts):
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def _main(rec):
    return not rec.get("agentId") and rec.get("isSidechain") is not True


def _feed(line, items, open_):
    """One transcript row into `items` ([ts, pre, after] lists); returns whether a measuring window is open."""
    boundary, usage = b'"compact_boundary"' in line, open_ and b'"usage"' in line
    if not (boundary or usage):
        return open_
    try:
        rec = json.loads(line)
    except ValueError:
        return open_
    if not isinstance(rec, dict):
        return open_
    if boundary and rec.get("type") == "system" and rec.get("subtype") == "compact_boundary":
        if not _main(rec):
            return open_
        meta = rec.get("compactMetadata") if isinstance(rec.get("compactMetadata"), dict) else {}
        pre = meta.get("preTokens")
        if meta.get("trigger") == "auto":  # any main-chain boundary closes the window; only auto opens one
            items.append([_epoch(rec.get("timestamp")), pre if isinstance(pre, int) and not isinstance(pre, bool) else 0, None])
            return True
        return False
    if open_:
        got = usage_context(rec)
        if got is not None:
            items[-1][2] = got
            return False
    return open_


def _scan(f, offset, items, open_):
    """Reads whole rows from `offset`; returns (offset after the last complete row, open). A tail with no newline is left."""
    f.seek(offset)
    parts, pos = [], offset
    while chunk := f.read(CHUNK):
        if b"\n" not in chunk:
            parts.append(chunk)
            continue
        *rows, tail = (b"".join(parts) + chunk).split(b"\n")
        parts = [tail] if tail else []
        for row in rows:
            pos += len(row) + 1
            open_ = _feed(row, items, open_)
    return pos, open_


def _tail_sha(f, offset):
    f.seek(max(offset - TAIL, 0))
    return hashlib.sha256(f.read(offset - max(offset - TAIL, 0))).hexdigest()


def cache_path(session):
    return roles.state() / "compact-history" / f"{session}.json"


def _load(session, path, st):
    """The cached (items, offset, open, tail_sha), or None when it does not describe this file as it is now."""
    try:
        c = json.loads(cache_path(session).read_text())
        items = [[float(a), int(b), None if c_ is None else int(c_)] for a, b, c_ in c["items"]]
        ok = (c["v"] == CACHE_VERSION and c["path"] == str(path) and c["dev"] == st.st_dev and c["ino"] == st.st_ino
              and 0 <= c["offset"] <= st.st_size)
        return (items, c["offset"], bool(c["open"]), c["tail_sha"]) if ok else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save(session, path, st, items, offset, open_, sha):
    target = cache_path(session)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"v": CACHE_VERSION, "path": str(path), "dev": st.st_dev, "ino": st.st_ino,
                                   "offset": offset, "tail_sha": sha, "items": items, "open": open_}))
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def history(session):
    """The auto compactions of the session's own main chain, in order. Raises TranscriptUnreadable when the transcript
    is missing or cannot be read."""
    path = transcript(session)
    if path is None:
        raise TranscriptUnreadable(f"no transcript for session {session!r}")
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            items, offset, open_ = [], 0, False
            cached = _load(session, path, st)
            if cached:
                items, offset, open_, sha = cached
                if _tail_sha(f, offset) != sha:
                    items, offset, open_ = [], 0, False
            offset, open_ = _scan(f, offset, items, open_)
            sha = _tail_sha(f, offset)
    except OSError as e:
        raise TranscriptUnreadable(str(e)) from e
    _save(session, path, st, items, offset, open_, sha)
    return [Compaction(*i) for i in items]


def explain(session, context, p, hist=history):
    """(key, why): why a restart is due now, "" when it is not, and a key that stays equal while the same reason holds
    (the stream announces each key once). The ceiling needs no transcript; an unreadable one turns only the compaction
    rules off."""
    if p.ceiling and context > p.ceiling:
        return ("ceiling",), f"context {context // 1000}k > ceiling {p.ceiling // 1000}k"
    if not p.compact:
        return (), ""
    try:
        h = hist(session)
    except (TranscriptUnreadable, OSError):
        return (), ""
    n = len(h)
    if n >= p.after:
        return ("count", n), f"{n} auto-compactions"
    last = h[-1] if h else None
    if last and last.after is not None and last.pre > 0 and last.after / last.pre >= p.stuck_ratio:
        return ("stuck", n), f"compaction left {round(100 * last.after / last.pre)}%"
    if n >= 2 and h[-1].ts > 0 and h[-2].ts > 0 and (h[-1].ts - h[-2].ts) < p.min_gap_min * 60:
        return ("gap", n), f"last 2 compactions {round((h[-1].ts - h[-2].ts) / 60)} min apart"
    return (), ""


def due(session, context, p, hist=history):
    return explain(session, context, p, hist)[1]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    pol = policy()
    try:
        for c in history(sys.argv[1]):
            print(c)
    except TranscriptUnreadable as e:
        print(f"unreadable: {e}")
    print("due:", due(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0, pol) or "no")
