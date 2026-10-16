#!/usr/bin/env python3
"""Screen watch over every agent terminal, for the cards no Run lists.

`orch wait` reads the screens of a program's own workers. A card started with `orca worktree create` (a
`dispatch-card` card, a lead) is not a worker, and a session with no program waits on its inbox with
`check --peek --wait`, which reads no screen: such cards sat for hours on a prompt only a human could answer.

Usage: orch watch-cards [--interval 180] [--idle-min 30] [--min-free-gb 15] [--timeout-ms 3300000] [--once]
                        [--self HANDLE] [--state PATH]

Each round reads every agent terminal but this session's own and exits on a new finding: a select menu, a
permission prompt, an interrupted turn, a full disk, an API retry loop, a `/goal` card with no output for
--idle-min minutes, or free disk under --min-free-gb. A finding still on screen is not reported again, also
across restarts (--state); once it clears, the next one is new. It never types into a terminal: what to
answer is the coordinator's call. Exit 0 always; the output says why it ended (STUCK lines, QUIET or OK).
"""
import argparse, hashlib, json, os, pathlib, shutil, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog

STATE_DIR = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "card-watch"


def agent_terminals(listing, self_handle):
    return [t for t in listing if t.get("agentIdentity") and t.get("connected", True) and t.get("handle") != self_handle]


def scan(terms, seen, read, now_ms, idle_min, free_gb, min_free_gb):
    """Lines for findings not in seen; seen becomes this round's findings, so a cleared one can come back."""
    out, now, retrying = [], {}, []
    for t in terms:
        h = t["handle"]
        try:
            lines = read(h)
        except (SystemExit, Exception):
            continue
        name = (t.get("worktreePath") or "").rstrip("/").rsplit("/", 1)[-1] or h
        hit = prog.screen_prompt(lines)
        idle = (now_ms - (t.get("lastOutputAt") or now_ms)) / 60000
        if hit:
            kind, question = hit
            key = kind + ":" + hashlib.sha1("\n".join(prog.prompt_block(lines[-25:])).encode()).hexdigest()[:12]
        elif idle > idle_min and any("/goal active" in l for l in lines[-25:]):
            kind, question, key = f"a /goal card silent {int(idle)} min", "", f"idle:{t.get('lastOutputAt')}"
        else:
            continue
        now[h] = key
        if seen.get(h) == key:
            continue
        if kind == "an API retry loop":
            retrying.append((name, h))
        else:
            out += [f"STUCK {name} ({kind}): " + (f'"{question}"' if question else "read its screen"),
                    f"  orca terminal read --terminal {h} --screen"]
    if len(retrying) >= 3:
        # Cards share one account's usage window: near its end they all retry at once, and no card is at fault.
        out.append(f"STUCK {len(retrying)} cards in an API retry loop at once (a usage limit, likely): "
                   + ", ".join(n for n, _ in retrying))
    else:
        for n, h in retrying:
            out += [f"STUCK {n} (an API retry loop): read its screen", f"  orca terminal read --terminal {h} --screen"]
    if free_gb < min_free_gb:
        now["disk"] = "low"
        if seen.get("disk") != "low":
            out.append(f"STUCK disk (free {free_gb:.1f} GiB, under {min_free_gb}): run reap-resources")
    seen.clear()
    seen.update(now)
    return out


def load(path):
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return seen if isinstance(seen, dict) else {}


def save(path, seen):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".card-watch-")
    with os.fdopen(fd, "w") as f:
        json.dump(seen, f)
    os.replace(tmp, path)


def main(argv):
    ap = argparse.ArgumentParser(prog="orch watch-cards", description=__doc__.split("\n\n")[0])
    ap.add_argument("--interval", type=int, default=180, help="seconds between rounds")
    ap.add_argument("--idle-min", type=int, default=30)
    ap.add_argument("--min-free-gb", type=float, default=15)
    ap.add_argument("--timeout-ms", type=int, default=3300000)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--self", dest="self_handle", default=os.environ.get("ORCA_TERMINAL_HANDLE"))
    ap.add_argument("--state", type=pathlib.Path)
    a = ap.parse_args(argv)
    state = a.state or STATE_DIR / f"{a.self_handle or 'default'}.json"
    seen, deadline = load(state), time.time() + a.timeout_ms / 1000
    while True:
        terms = agent_terminals(prog.orca_json("terminal", "list").get("terminals", []), a.self_handle)
        free_gb = shutil.disk_usage(pathlib.Path.home()).free / 2**30
        lines = scan(terms, seen, prog.read_screen, time.time() * 1000, a.idle_min, free_gb, a.min_free_gb)
        save(state, seen)
        if lines:
            print("\n".join(lines), flush=True)
            return
        if a.once:
            print(f"OK {len(terms)} agent terminals, nothing new", flush=True)
            return
        if time.time() + a.interval > deadline:
            print("QUIET nothing new; start the watch again", flush=True)
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main(sys.argv[1:])
