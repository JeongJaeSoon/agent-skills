"""Instructions sent out of a session: to another session or card, a subagent, an inbox, Slack or a tracker. The
dashboard's Sent page shows them so the human can read what was asked of whom.

Four sources, each row tagged with its own:
- log: `orch sent add`, written by the sender at the moment it sends. One file a day (UTC) under
  <fleet state>/sent/YYYY-MM-DD.jsonl, one JSON object a line: ts, from, to, kind, channel, repo, subject, text.
  With Orca's own, the only record that outlives the sending session, and the only one for Slack, tracker and
  other non-shell sends.
- dashboard: a line typed from the dashboard's session page (sends.jsonl, fleet.send).
- transcript: what the live Claude Code sessions sent by Agent, SendMessage or an orca send in Bash (fleet.fetch_sent).
- orca: what Orca keeps itself — every inbox message but heartbeats (fleet.orca_messages_sent) and each Run Task's
  spec (fleet.orca_sent). An `orchestration send` or `reply` therefore needs no log line.
A send seen in two sources shows once, as its log row or else its Orca message, naming both sources.
The log keeps the text as sent, so its files are owner-only (0600 in a 0700 directory); everything shown goes
through fleet.mask.
"""
import datetime as dt, fcntl, json, os, re, threading

import fleet

KINDS = ("card", "terminal", "inbox", "subagent", "agent", "slack", "tracker", "other")
TEXT_MAX = 20000  # shown in full under a fold; longer is cut
SAME_SEND = dt.timedelta(minutes=10)  # a logged send and its transcript copy are this close
DAYS_MAX = fleet.SENT_KEEP.days
LOG_SCAN = {}  # day file -> {"offset", "rows"}: the log only grows, so a poll reads what was appended since
LOG_LOCK = threading.Lock()  # the dashboard serves polls on threads, and two must not advance one offset


def sent_dir():
    return fleet.state_dir() / "sent"


def add(to, text, kind=None, channel=None, repo=None, subject=None, sender=None, now=None):
    if not (to or "").strip() or not (text or "").strip():
        raise ValueError("a sent instruction needs --to and its text")
    if kind and kind not in KINDS:
        raise ValueError(f"--kind is one of {', '.join(KINDS)}")
    t = now or fleet.utcnow()
    row = {"ts": fleet.iso(t), "from": sender, "to": to.strip(), "kind": kind, "channel": channel, "repo": repo,
           "subject": subject, "text": text}
    row = {k: v for k, v in row.items() if v is not None}
    sent_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(sent_dir(), 0o700)  # mode= applies only to a directory it creates
    # Under a lock rather than fleet.append_line: a prompt can pass the 4 KiB a single O_APPEND write keeps whole.
    fd = os.open(sent_dir() / f"{t:%Y-%m-%d}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        os.fchmod(fd, 0o600)
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_log(since):
    """The log rows from `since` on. A line without a string ts, to and text is skipped, so one bad line cannot take
    the page down."""
    rows, seen = [], set()
    with LOG_LOCK:
        for p in sorted(sent_dir().glob("*.jsonl")):
            if p.stem < f"{since:%Y-%m-%d}":
                continue
            seen.add(str(p))
            c = LOG_SCAN.setdefault(str(p), {"offset": 0, "rows": []})
            for raw in fleet.appended(p, c, lambda: c.update(rows=[])):
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("ts", "to", "text")):
                    c["rows"].append(r)
            rows += [{**r, "source": "log"} for r in c["rows"] if r["ts"] >= fleet.iso(since)]
        for k in set(LOG_SCAN) - seen:
            del LOG_SCAN[k]
    return rows


def dashboard_sends(since):
    rows, path = [], fleet.state_dir() / "sends.jsonl"
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if (isinstance(r, dict) and r.get("typed") and isinstance(r.get("at"), str) and isinstance(r.get("text"), str)
                and r["at"] >= fleet.iso(since)):
            rows.append({"ts": r["at"], "from": "dashboard", "to": r.get("session"), "kind": "terminal",
                         "channel": "dashboard", "text": r["text"], "source": "dashboard"})
    return rows


def key(text):
    return " ".join(re.sub(r"[\\'\"]", " ", text or "").split())[:200]


ORCA_ADDRESS = ("run:", "dispatch:", "term_")
LIMIT = 3000  # a day of Orca messages alone passes 1000


def same_to(a, b):
    """Two rows can be one send only when their receivers agree. Only Orca addresses of one form are compared: a log
    line may name a card where its transcript copy has the command's own --to, and Orca stores a send to a Run's
    coordinator terminal under `run:<id>`."""
    pa, pb = (next((p for p in ORCA_ADDRESS if str(x).startswith(p)), None) for x in (a, b))
    return pa is None or pa != pb or a == b


MESSAGE_FIELDS = ("message", "thread", "run")


def page(rows, days=1, now=None, limit=LIMIT, inbox_since=None):
    """{"rows", "total", "days", "inbox_since"}: the sent instructions of the last `days` days, newest first, one row
    per send, masked, at most `limit` of them. `rows` are the transcript and Orca rows (fleet.fetch_sent,
    fleet.orca_messages_sent, fleet.orca_sent); they are not changed. An Orca message is a record of its own, like a
    log line: a copy from a transcript or the dashboard folds into it, and it folds into a log line, which then
    carries its ids. Two Orca messages never fold. inbox_since (fleet.inbox_cut): where a full inbox read starts,
    None when the read held every message."""
    since = (now or fleet.utcnow()) - dt.timedelta(days=days)
    logged = read_log(since)
    by_key = {}
    for r in logged:
        by_key.setdefault(key(r.get("text")), []).append(r)
    out = list(logged)
    recent = [dict(r) for r in rows if (r.get("ts") or "9") >= fleet.iso(since)]
    messages = [r for r in recent if r.get("message")]
    for r in messages + dashboard_sends(since) + [r for r in recent if not r.get("message")]:
        t = fleet.parse(r.get("ts"))
        twin = next((x for x in by_key.get(key(r.get("text")), []) if t and fleet.parse(x.get("ts"))
                     and abs(fleet.parse(x["ts"]) - t) <= SAME_SEND and same_to(x.get("to"), r.get("to"))
                     and not (x.get("message") and r.get("message"))), None)
        if twin:
            twin["also"] = [*twin.get("also", []), r["source"]]
            twin.setdefault("claude_session", r.get("claude_session"))
            for k in MESSAGE_FIELDS:
                if r.get(k) is not None:
                    twin.setdefault(k, r[k])
        else:
            out.append(r)
            if r.get("message"):
                by_key.setdefault(key(r.get("text")), []).append(r)
    out.sort(key=lambda r: r.get("ts") or "", reverse=True)
    shown = [{**r, "text": fleet.mask(r.get("text"), TEXT_MAX),
              **{k: fleet.mask(r.get(k), n) or None for k, n in (("subject", 200), ("to", 120), ("from", 120),
                                                                   ("channel", 120), ("repo", 120), ("cwd", 300))}}
             for r in out[:limit]]
    return {"rows": shown, "total": len(out), "days": days, "inbox_since": inbox_since}
