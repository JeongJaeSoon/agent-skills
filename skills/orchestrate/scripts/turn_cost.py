#!/usr/bin/env python3
"""Per-turn cost of one long-lived session (the orchestrator) from its Claude Code transcript, so a change that
claims to cut tokens is compared before and after with the same numbers.

Usage:
  turn_cost.py TRANSCRIPT.jsonl [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--json]
               [--what-if stream,restart] [--restart-at 130000] [--fresh 77000] [--debounce 30]

A turn starts at a user row that is not a tool result and ends at the next one. Each turn is classified by what woke
it (`turnOrigin`; a task notification is further split by what its follow-up did) and counts its model calls, the
context each call read, and the input-equivalent weight (docs/token-usage.md: cache read 0.1, 5-minute cache write
1.25, 1-hour cache write 2, output 5). Only numbers, tool names, command heads and file paths are read; nothing of a
message is printed but the notification summary's first words.

--what-if replays the same turns under a change and prints the weight it would have cost. A replayed call keeps its
own output and cache writes and reads the replayed context instead of the original one.
  stream   the inbox wait is an `orch inbox --stream` Monitor. In the wake of a background inbox wait or card watcher,
           a call is dropped when every tool in it read a background task's output file or ran only inbox/wait/card
           watch commands (each `&&`/`;`/`|` part). A wake within --debounce seconds of the wake that opened its
           window shares that notice, which saves its closing reply. The stream ends every 29 minutes of wall time,
           busy or not, and each end costs two calls (the notice and the Monitor started again).
  restart  the session is cleared and rebooted at the first turn boundary where its replayed context passed
           --restart-at and at least QUIET_S passed since the turn before, and starts again at --fresh tokens: a cache
           write of all but the cached prefix, plus REBOOT_CALLS calls at it. The replay starts at the first context
           it sees, and the original compactions give way to these restarts.
"""
import argparse, collections, datetime as dt, json, re, statistics, sys

W = {"cache_read_input_tokens": 0.1, "output_tokens": 5, "input_tokens": 1}
REARM = re.compile(r"^(?:cd\s+\S+|(?:\S*/)?(?:orch|prog\.py)\s+(?:inbox|wait|watch-cards)\b.*|.*\s--ack\s+\S+.*)$")
PARTS = re.compile(r"&&|\|\||[;|\n]")
TASK_OUTPUT = re.compile(r"/tasks/[^/]+\.output$")
# The wake of a background inbox wait or card watcher, by the description the session gave the command.
INBOX_WAKE = re.compile(r"inbox|re-?arm|card watcher|watch-cards|orchestrat\w* (?:message|wait)", re.I)
STREAM_TTL_S = 1740  # orch inbox --stream ends a minute before Claude Code's 30-minute Monitor limit
REBOOT_CALLS = 6
QUIET_S = 120
# A /clear keeps the system prompt and tools, and its first call read them from the cache (29k of 33k, measured).
CACHED_PREFIX = 29_000
COMPACTED = 0.6  # a call whose context fell below this share of the previous one follows a compaction


def weight(u):
    cc = u.get("cache_creation") or {}
    w = sum(v * (u.get(k) or 0) for k, v in W.items())
    one, five = cc.get("ephemeral_1h_input_tokens") or 0, cc.get("ephemeral_5m_input_tokens") or 0
    if not (one or five):
        five = u.get("cache_creation_input_tokens") or 0
    return w + 2 * one + 1.25 * five


def ctx(u):
    return (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)


def text_of(content):
    if isinstance(content, str):
        return content
    return " ".join(x.get("text", "") for x in content if isinstance(x, dict))


def is_tool_result(content):
    return isinstance(content, list) and bool(content) and isinstance(content[0], dict) and content[0].get("type") == "tool_result"


def rows(path, since, until):
    seen = set()
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") not in ("assistant", "user"):
                continue
            day = (d.get("timestamp") or "")[:10]
            if (since and day < since) or (until and day >= until):
                continue
            mid = (d.get("message") or {}).get("id")
            if d["type"] == "assistant" and mid:
                # One API response is written once per content block; count its usage once.
                d["_dup"] = mid in seen
                seen.add(mid)
            yield d


def turns(path, since=None, until=None):
    """[{origin, summary, ts, calls: [{ctx, weight, tools}]}]; a call's tools are (name, command or file path) of the
    tool_use blocks of its response."""
    cur, out, by_id = None, [], {}
    for d in rows(path, since, until):
        m = d.get("message") or {}
        if d["type"] == "user":
            if is_tool_result(m.get("content")):
                continue
            txt = text_of(m.get("content"))
            origin = d.get("turnOrigin") or ("task_notification" if txt.lstrip().startswith("<task-notification>") else "unknown")
            if isinstance(origin, dict):
                origin = origin.get("kind") or "unknown"
            summ = re.search(r"<summary>(.*?)</summary>", txt, re.S)
            cur = {"origin": origin, "summary": (summ.group(1).strip()[:60] if summ else ""), "calls": [], "ts": d.get("timestamp")}
            out.append(cur)
        elif cur is not None:
            mid = m.get("id")
            u = m.get("usage") or {}
            if u and not d.get("_dup"):
                call = {"ctx": ctx(u), "weight": weight(u), "tools": []}
                cur["calls"].append(call)
                by_id[mid] = call
            call = by_id.get(mid)
            for b in m.get("content") or []:
                if call is not None and isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    call["tools"].append((b.get("name") or "", str(inp.get("command") or inp.get("file_path") or "")[:300]))
    return out


def rearm(tool):
    """A tool call the stream makes unnecessary: reading a background task's output file, or a Bash command whose
    every part is an inbox wait, an ack, a card watch or a cd."""
    name, arg = tool
    if name == "Read":
        return bool(TASK_OUTPUT.search(arg))
    if name != "Bash":
        return False
    parts = [p.strip() for p in PARTS.split(arg) if p.strip()]
    return bool(parts) and all(REARM.match(p) for p in parts)


def kind(t):
    if t["origin"] != "task_notification":
        return t["origin"]
    tools = [x for c in t["calls"] for x in c["tools"]]
    if not tools:
        return "notification_empty"
    if all(rearm(x) for x in tools):
        return "notification_rearm_only"
    return "notification_work"


def summarize(ts):
    by = collections.defaultdict(lambda: {"turns": 0, "calls": 0, "weight": 0.0})
    for t in ts:
        b = by[kind(t)]
        b["turns"] += 1
        b["calls"] += len(t["calls"])
        b["weight"] += sum(c["weight"] for c in t["calls"])
    ctxs = [c["ctx"] for t in ts for c in t["calls"]]
    total = sum(b["weight"] for b in by.values())
    return {"turns": len(ts), "calls": len(ctxs), "weight": round(total),
            "ctx_median": int(statistics.median(ctxs)) if ctxs else 0,
            "ctx_p90": int(statistics.quantiles(ctxs, n=10)[-1]) if len(ctxs) >= 10 else (max(ctxs) if ctxs else 0),
            "compactions": sum(1 for a, b in zip(ctxs, ctxs[1:]) if b < a * COMPACTED),
            "by_kind": {k: {**v, "weight": round(v["weight"]), "share": round(v["weight"] / total, 3) if total else 0}
                        for k, v in sorted(by.items(), key=lambda kv: -kv[1]["weight"])}}


def when(t):
    try:
        return dt.datetime.fromisoformat((t["ts"] or "").replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def what_if(ts, changes, restart_at=130_000, fresh=77_000, debounce=30):
    """{weight, calls, ctx_median, restarts} of the same turns replayed under `changes` ({"stream", "restart"})."""
    out_w, out_ctx, restarts = 0.0, [], 0
    level, prev_ctx, prev_at, window, next_end = None, None, None, None, None
    for t in ts:
        at = when(t)
        keep = t["calls"]
        if "stream" in changes:
            if at and next_end is None:
                next_end = at + STREAM_TTL_S
            while at and next_end and at >= next_end:  # the stream's own end, busy or not
                cost_at = level if level is not None else (prev_ctx or fresh)
                out_w += 2 * 0.1 * cost_at
                out_ctx += [cost_at] * 2
                next_end += STREAM_TTL_S
            if t["origin"] == "task_notification" and INBOX_WAKE.search(t["summary"]):
                kept = [c for c in keep if not (c["tools"] and all(rearm(x) for x in c["tools"]))]
                keep = kept or keep[-1:]
                if at and window and at - window < debounce:
                    keep = keep[:-1] if keep and not keep[-1]["tools"] else keep
                else:
                    window = at
        if ("restart" in changes and level is not None and level > restart_at and at and prev_at
                and at - prev_at >= QUIET_S):
            restarts += 1
            level = fresh
            out_w += 2 * max(fresh - CACHED_PREFIX, 0) + REBOOT_CALLS * 0.1 * fresh
            out_ctx += [fresh] * REBOOT_CALLS
        kept_ids = {id(c) for c in keep}
        for c in t["calls"]:
            # The context a call adds is the step from the previous call; a step down is a compaction, not growth.
            step = c["ctx"] - prev_ctx if prev_ctx is not None and c["ctx"] >= prev_ctx * COMPACTED else 0
            prev_ctx = c["ctx"]
            if level is None:
                level = c["ctx"]  # the replay starts where the transcript does, not at --fresh
                step = 0
            if id(c) not in kept_ids:
                continue
            if "restart" in changes:
                level += max(step, 0)
                read = level
            else:
                # Without restarts a kept call reads its original context: what the dropped calls added would have
                # been shrunk by the next compaction anyway, so no saving is claimed for it.
                read = level = c["ctx"]
            out_w += max(c["weight"] + 0.1 * (read - c["ctx"]), 0)
            out_ctx.append(read)
        prev_at = at or prev_at
    return {"weight": round(out_w), "calls": len(out_ctx), "restarts": restarts,
            "ctx_median": int(statistics.median(out_ctx)) if out_ctx else 0}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("transcript")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--what-if", default="")
    ap.add_argument("--restart-at", type=int, default=130_000)
    ap.add_argument("--fresh", type=int, default=77_000)
    ap.add_argument("--debounce", type=int, default=30)
    a = ap.parse_args(argv)
    changes = {x for x in a.what_if.split(",") if x}
    if bad := changes - {"stream", "restart"}:
        ap.error(f"unknown --what-if: {', '.join(sorted(bad))}")
    ts = turns(a.transcript, a.since, a.until)
    s = summarize(ts)
    if changes:
        s["what_if"] = {"changes": sorted(changes), **what_if(ts, changes, a.restart_at, a.fresh, a.debounce)}
    if a.json:
        print(json.dumps(s, indent=1))
        return 0
    print(f"turns {s['turns']}  calls {s['calls']}  weighted {s['weight']:,}  ctx median {s['ctx_median']:,} "
          f"p90 {s['ctx_p90']:,}  compactions {s['compactions']}")
    for k, v in s["by_kind"].items():
        print(f"  {k:26} turns {v['turns']:6}  calls {v['calls']:6}  weighted {v['weight']:>14,}  {v['share']:.1%}")
    if w := s.get("what_if"):
        ratio = w["weight"] / s["weight"] if s["weight"] else 0
        print(f"what-if {'+'.join(w['changes'])}: calls {w['calls']}  weighted {w['weight']:,} ({ratio:.1%} of now)  "
              f"ctx median {w['ctx_median']:,}  restarts {w['restarts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
