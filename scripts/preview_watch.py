#!/usr/bin/env python3
"""Publish loopback preview servers on this machine's tailnet, and take down only what this opened.

Usage:
  preview_watch.py run          # one pass: serve new allowed listeners, turn off the ones this opened that are gone
  preview_watch.py links        # print every tailnet serve link, marking the ones this opened
  preview_watch.py install      # macOS: run `run` every StartInterval seconds as a LaunchAgent
  preview_watch.py uninstall    # unload the LaunchAgent; turns off every entry this opened

No model or network API is called. One pass runs `lsof` once, and `tailscale` only when a listener appeared
or went away. Settings live under the "preview-watch" key of ~/.claude/agent-skills.json (see DEFAULTS).
"""
import fcntl, json, os, pathlib, plistlib, shutil, socket, subprocess, sys, time

LABEL = "io.github.jeongjaesoon.preview-watch"
PLIST = pathlib.Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
STATE_DIR = pathlib.Path(os.environ.get("PREVIEW_WATCH_STATE", "~/.local/state/agent-skills")).expanduser()
STATE = STATE_DIR / "preview-watch.json"
LOG = STATE_DIR / "preview-watch.log"
APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

DEFAULTS = {
    "interval": 15,
    "ranges": [
        # Hand-started previews: docs static servers, dev servers.
        {"from": 4500, "to": 4599},
        # Docker publishes `127.0.0.1::<port>` (no host port) from the Linux ephemeral range; only ports the
        # Docker or OrbStack daemon holds, so a desktop app's own loopback port in that range stays private.
        {"from": 32768, "to": 60999, "commands": ["OrbStack", "com.docker"]},
    ],
    "tailscale": None,
}
RETRY_NON_HTTP_S = 120  # a server that listens before it answers HTTP gets another probe after this


def config():
    try:
        mine = json.loads(CONFIG.read_text()).get("preview-watch") or {}
    except (OSError, ValueError):
        mine = {}
    return {**DEFAULTS, **mine}


def log(msg, once=False):
    """once: skip the line when the log already ends with the same message (a failure repeating every pass)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a+", errors="replace") as f:  # the tail seek can land inside a multi-byte character
        if once and f.tell():
            f.seek(max(0, f.tell() - 1024))
            if f.read().rstrip("\n").rpartition("\n")[2].partition(" ")[2] == msg:
                return
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")


def tailscale_cli(cfg):
    return cfg.get("tailscale") or shutil.which("tailscale") or (APP_CLI if os.path.exists(APP_CLI) else None)


# ---------------------------------------------------------------- what is listening

def parse_lsof(out):
    """lsof -F cn output -> {port: (host, command)}. 127.0.0.1 wins over ::1 for a port bound to both."""
    found, cmd = {}, ""
    for line in out.splitlines():
        if line.startswith("c"):
            cmd = line[1:]
        elif line.startswith("n"):
            host, _, port = line[1:].rpartition(":")
            if host in ("127.0.0.1", "[::1]") and port.isdigit():
                p = int(port)
                if p not in found or host == "127.0.0.1":
                    found[p] = (host, cmd)
    return found


def listeners():
    # Not netstat, though it is ~20x faster: run from a Homebrew python it prints nothing at all.
    out = subprocess.run(["lsof", "-nP", "-F", "cn", "-iTCP@127.0.0.1", "-iTCP@[::1]", "-sTCP:LISTEN"],
                         capture_output=True, text=True, timeout=10).stdout
    return parse_lsof(out)


def allowed(port, command, ranges):
    for r in ranges:
        if r["from"] <= port <= r["to"]:
            cmds = r.get("commands")
            if not cmds or any(command.startswith(c) for c in cmds):
                return True
    return False


def target(port, host):
    return f"http://{host}:{port}"


def speaks_http(port, host):
    try:
        with socket.create_connection((host.strip("[]"), port), timeout=0.3) as s:
            s.settimeout(0.3)
            s.sendall(b"HEAD / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            return s.recv(5) == b"HTTP/"
    except OSError:
        return False


# ---------------------------------------------------------------- state

def load_state():
    """{"owned": {port: target}, "skip": {port: retry_at or 0}}. A file that does not parse is moved aside and
    nothing is owned: an entry this cannot prove it opened is never turned off."""
    try:
        raw = STATE.read_text()
    except FileNotFoundError:
        return {"owned": {}, "skip": {}}
    try:
        d = json.loads(raw)
        owned = {int(p): str(t) for p, t in d["owned"].items()}
        skip = {int(p): float(v) for p, v in d.get("skip", {}).items()}
        return {"owned": owned, "skip": skip}
    except (ValueError, KeyError, TypeError, AttributeError):
        aside = STATE.with_name(f"{STATE.name}.corrupt-{int(time.time())}")
        STATE.rename(aside)
        log(f"state did not parse; moved to {aside} and starting with nothing owned")
        return {"owned": {}, "skip": {}}


def save_state(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"owned": {str(p): t for p, t in sorted(st["owned"].items())},
                               "skip": {str(p): v for p, v in sorted(st["skip"].items())}}, indent=1))
    tmp.replace(STATE)


# ---------------------------------------------------------------- tailscale serve

def ts(cli, *args):
    # Without TERM (as under launchd) the Tailscale.app binary tries to start its GUI instead of acting as the CLI.
    # The timeout keeps a CLI waiting on a prompt from holding the lock and stopping every later pass.
    return subprocess.run([cli, *args], capture_output=True, text=True, timeout=20,
                          env={**os.environ, "TERM": os.environ.get("TERM") or "dumb"})


def serve_status(cli):
    p = ts(cli, "serve", "status", "--json")
    if p.returncode:
        raise RuntimeError(f"tailscale serve status: {p.stderr.strip()}")
    # Empty or non-object output is a failed call, not an empty config: read as "no entries", every
    # foreign entry would look free to take.
    d = json.loads(p.stdout)
    if not isinstance(d, dict):
        raise RuntimeError(f"tailscale serve status: unexpected output {p.stdout[:80]!r}")
    return d


def entries(status):
    """{https_port: proxy target or None} for every TCP port in the serve config."""
    tcp = {int(p) for p in (status.get("TCP") or {})}
    out = {p: None for p in tcp}
    for hostport, web in (status.get("Web") or {}).items():
        p = int(hostport.rpartition(":")[2])
        out[p] = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy")
    return out


def pending(listening, st, ranges, now):
    """(candidates {port: (host, cmd)}, new ports, gone ports) from what is listening and what this owns."""
    owned, skip = st["owned"], st["skip"]
    cand = {p: hc for p, hc in listening.items() if allowed(p, hc[1], ranges)}
    new = sorted(p for p in cand if p not in owned and not (p in skip and (skip[p] == 0 or skip[p] > now)))
    gone = sorted(p for p in owned if p not in cand)
    return cand, new, gone


def decide(cand, new, gone, owned, live):
    """Against the live serve config {port: proxy}: (opens [(port, target)], closes, forget, foreign)."""
    opens = [(p, target(p, cand[p][0])) for p in new if p not in live]
    foreign = [p for p in new if p in live]  # someone else's entry on this port: never ours while it listens
    # Turn off only an entry that still points where this pointed it; a replaced one is someone else's now.
    closes = [p for p in gone if live.get(p) == owned[p]]
    forget = [p for p in gone if live.get(p) != owned[p]]
    return opens, closes, forget, foreign


def run_once(cfg):
    cli = tailscale_cli(cfg)
    if not cli:
        raise RuntimeError("tailscale CLI not found; set \"tailscale\" under \"preview-watch\" in " + str(CONFIG))
    st = load_state()
    listening = listeners()
    if not listening:
        # A Mac always has some loopback listener; an empty scan is a broken scan, and acting on it would turn
        # off every entry this opened.
        raise RuntimeError("no loopback listener at all; not trusting this scan")
    now = time.time()
    skip = {p: v for p, v in st["skip"].items() if p in listening}
    changed, st["skip"] = skip != st["skip"], skip
    cand, new, gone = pending(listening, st, cfg["ranges"], now)
    if not new and not gone:
        if changed:
            save_state(st)
        return
    opens, closes, forget, foreign = decide(cand, new, gone, st["owned"], entries(serve_status(cli)))
    st["skip"].update({p: 0 for p in foreign})
    for p, t in opens:
        if not speaks_http(p, listening[p][0]):
            st["skip"][p] = now + RETRY_NON_HTTP_S
            continue
        if p in entries(serve_status(cli)):  # taken since the first read (the probe took time)
            st["skip"][p] = 0
            continue
        try:
            r = ts(cli, "serve", "--bg", f"--https={p}", t)
        except subprocess.TimeoutExpired:
            # It may have applied the entry anyway; claim it only if it points where this pointed it.
            if entries(serve_status(cli)).get(p) == t:
                st["owned"][p] = t
                save_state(st)
            raise
        if r.returncode:
            log(f"open {p} failed: {r.stderr.strip()}")
            st["skip"][p] = now + RETRY_NON_HTTP_S
            continue
        st["owned"][p] = t
        save_state(st)  # record before the next command, so a crash never leaves an entry this does not know
        log(f"open  https :{p} -> {t} ({listening[p][1]})")
    for p in closes:
        if entries(serve_status(cli)).get(p) != st["owned"][p]:  # re-pointed since the first read
            forget.append(p)
            continue
        r = ts(cli, "serve", f"--https={p}", "off")
        if r.returncode:
            log(f"close {p} failed: {r.stderr.strip()}")
            continue
        del st["owned"][p]
        log(f"close https :{p} (listener gone)")
    for p in forget:
        del st["owned"][p]
        log(f"forget :{p} (entry changed by someone else; left as is)")
    save_state(st)


def locked(blocking):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(STATE_DIR / "preview-watch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        lock.close()
        return None
    return lock


def cmd_run():
    lock = locked(blocking=False)
    if not lock:
        return 0  # another pass is running
    with lock:
        try:
            run_once(config())
        except Exception as e:  # launchd reruns in seconds; leave one line instead of a traceback per pass
            log(f"pass failed: {type(e).__name__}: {e}", once=True)
            return 1
    return 0


def cmd_links():
    cli = tailscale_cli(config())
    if not cli:
        print("tailscale CLI not found", file=sys.stderr)
        return 1
    owned = load_state()["owned"]
    status = serve_status(cli)
    rows = []
    for hostport, web in sorted((status.get("Web") or {}).items(), key=lambda kv: int(kv[0].rpartition(":")[2])):
        host, _, port = hostport.rpartition(":")
        url = f"https://{host}/" if port == "443" else f"https://{host}:{port}/"
        proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy") or "?"
        mark = "  (watch)" if owned.get(int(port)) == proxy else ""
        rows.append(f"{url}  -> {proxy}{mark}")
    print("\n".join(rows) if rows else "no tailnet serve entries")
    return 0


# ---------------------------------------------------------------- launchd

def agent_target():
    return f"gui/{os.getuid()}/{LABEL}"


def spec(interval):
    here = pathlib.Path(__file__).resolve()
    return {"Label": LABEL,
            # The PATH symlink, not sys.executable: a versioned Homebrew path breaks on the next python upgrade.
            "ProgramArguments": [shutil.which("python3") or sys.executable, "-I", str(here), "run"],
            "StartInterval": int(interval), "RunAtLoad": True,
            "ProcessType": "Background", "LowPriorityIO": True, "Nice": 10,
            "EnvironmentVariables": {"PATH": "/usr/sbin:/usr/bin:/bin", "HOME": str(pathlib.Path.home())},
            "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG)}


def cmd_install(here_ok):
    if sys.platform != "darwin":
        print("no launchd on this OS; run `preview_watch.py run` from cron instead", file=sys.stderr)
        return 1
    repo = pathlib.Path(__file__).resolve().parents[1]
    if not (repo / ".git").is_dir() and not here_ok:  # a worktree goes away under launchd
        print(f"{repo} is not a main git checkout; install from the checkout sessions load (or pass --here)",
              file=sys.stderr)
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    PLIST.write_bytes(plistlib.dumps(spec(config()["interval"])))
    for _ in range(5):  # bootstrap right after bootout can fail with error 5 until the old job is gone
        p = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], capture_output=True, text=True)
        if p.returncode == 0:
            print(f"wrote {PLIST} and loaded {agent_target()} (every {config()['interval']}s)")
            return 0
        time.sleep(1)
    print(f"launchctl bootstrap failed: {p.stderr.strip()}", file=sys.stderr)
    return 1


def close_owned(cli):
    """Turn off every entry this opened that still points where it was pointed. Returns the ports whose `off`
    failed; they stay in the state file so a later run or uninstall can retry them."""
    st, failed = load_state(), []
    live = entries(serve_status(cli)) if st["owned"] else {}
    for p, t in sorted(st["owned"].items()):
        if live.get(p) == t:
            if ts(cli, "serve", f"--https={p}", "off").returncode:
                failed.append(p)
                continue
            print(f"turned off https :{p}")
        del st["owned"][p]
    save_state(st)
    return failed


def cmd_uninstall():
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print(f"removed {agent_target()}")
    cli = tailscale_cli(config())
    if not cli:
        print("tailscale CLI not found; entries this opened are left on", file=sys.stderr)
        return 1
    with locked(blocking=True):
        failed = close_owned(cli)
    if failed:
        print(f"could not turn off https {', '.join(map(str, failed))}; rerun uninstall", file=sys.stderr)
        return 1
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "run":
        return cmd_run()
    if cmd == "links":
        return cmd_links()
    if cmd == "install":
        return cmd_install("--here" in argv)
    if cmd == "uninstall":
        return cmd_uninstall()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
