#!/usr/bin/env python3
"""Serve a share folder read-only on loopback through Caddy's file_server.

Usage:
  share_server.py serve [--root DIR] [--port N]   # foreground; defaults from config
  share_server.py install [--here]                # macOS: keep `serve` running as a LaunchAgent
  share_server.py uninstall

`serve` runs share-caddy/Caddyfile (listing sorted newest first with a grid view, .md rendered unless ?raw=1,
text files as UTF-8, no write methods) under share-caddy/sandbox.sb, which keeps caddy from reading outside the
share folder through a symlink. caddy comes from config "caddy", else PATH (`brew install caddy`).

Binds 127.0.0.1 only; publishing on a network (e.g. `tailscale serve`) is left to the owner. Hidden paths do not
exist. Settings live under the "share-server" key of ~/.claude/agent-skills.json (see DEFAULTS); "label" lets an
existing LaunchAgent be taken over in place, and the plist it replaces is kept next to it as <name>.plist.bak-<time>.
"""
import json, os, pathlib, plistlib, re, shutil, subprocess, sys, time

CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
STATE_DIR = pathlib.Path(os.environ.get("SHARE_SERVER_STATE", "~/.local/state/agent-skills")).expanduser()
LOG = STATE_DIR / "share-server.log"
HOST = "127.0.0.1"
DEFAULTS = {"root": "~/share", "port": 13201, "label": "io.github.jeongjaesoon.share-server", "caddy": ""}
CADDY_DIR = pathlib.Path(__file__).resolve().with_name("share-caddy")
CADDY_STATE = STATE_DIR / "share-caddy"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def config():
    try:
        mine = json.loads(CONFIG.read_text()).get("share-server") or {}
    except (OSError, ValueError):
        mine = {}
    return {**DEFAULTS, **mine}


def caddy_bin(cfg):
    """The configured caddy, else the one on PATH or in Homebrew's prefixes (launchd's PATH has neither)."""
    candidates = [cfg["caddy"]] if cfg.get("caddy") else \
        [shutil.which("caddy"), "/opt/homebrew/bin/caddy", "/usr/local/bin/caddy"]
    return next((c for c in candidates if c and os.path.isfile(c) and os.access(c, os.X_OK)), "")


def ere_escape(path):
    return re.sub(r"([\\.\[\](){}*+?|^$])", r"\\\1", path)


def caddy_command(cfg, root, port):
    """(argv, env) that run caddy on the share folder inside the sandbox profile."""
    root = os.path.realpath(pathlib.Path(root).expanduser())
    caddy = caddy_bin(cfg)
    argv = [SANDBOX_EXEC, "-f", str(CADDY_DIR / "sandbox.sb"), "-D", f"ROOT={root}", "-D", f"ROOT_RE={ere_escape(root)}",
            "-D", f"CONF={CADDY_DIR}", "-D", f"STATE={CADDY_STATE}", "-D", f"CADDY={os.path.realpath(caddy)}",
            caddy, "run", "--config", str(CADDY_DIR / "Caddyfile"), "--adapter", "caddyfile"]
    # XDG_* keep caddy's data and autosave dirs inside STATE, the only place the sandbox lets it write.
    env = {"SHARE_ROOT": root, "SHARE_PORT": str(int(port)), "SHARE_CADDY": str(CADDY_DIR),
           "XDG_DATA_HOME": str(CADDY_STATE), "XDG_CONFIG_HOME": str(CADDY_STATE)}
    return argv, env


def caddy_missing(cfg, root=None):
    root = pathlib.Path(root or cfg["root"]).expanduser()
    if any(c in f"{root}{os.path.realpath(root)}" for c in '*?[]\\{}"'):
        return f"{root}: caddy reads * ? [ ] \\ {{ }} \" in the share folder's path as a glob or placeholder; rename it"
    if not os.access(SANDBOX_EXEC, os.X_OK):
        return f"{SANDBOX_EXEC} not found; it keeps symlinks inside the share folder"
    if not caddy_bin(cfg):
        return "caddy not found or not executable; `brew install caddy` (or set share-server.caddy to its path)"
    return ""


def serve(root, port):
    root = str(pathlib.Path(root).expanduser())
    if not os.path.isdir(root):
        print(f"{root} is not a directory", file=sys.stderr)
        return 1
    cfg = config()
    missing = caddy_missing(cfg, root)
    if missing:
        print(missing, file=sys.stderr)
        return 1
    argv, env = caddy_command(cfg, root, port)
    CADDY_STATE.mkdir(parents=True, exist_ok=True)
    os.execve(argv[0], argv, {**os.environ, **env})


# ---------------------------------------------------------------- launchd

def plist_path(cfg=None):
    return pathlib.Path(f"~/Library/LaunchAgents/{(cfg or config())['label']}.plist").expanduser()


def agent_target(cfg=None):
    return f"gui/{os.getuid()}/{(cfg or config())['label']}"


def spec(cfg=None):
    """`serve` resolves caddy and the sandbox paths at each start, so a `brew upgrade caddy` needs no reinstall."""
    cfg = cfg or config()
    return {"Label": cfg["label"],
            "ProgramArguments": [shutil.which("python3") or sys.executable, "-I", str(pathlib.Path(__file__).resolve()),
                                 "serve", "--root", str(pathlib.Path(cfg["root"]).expanduser()),
                                 "--port", str(int(cfg["port"]))],
            "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Background",
            "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG)}


def plist_spec():
    try:
        return plistlib.loads(plist_path().read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def loaded():
    return subprocess.run(["launchctl", "print", agent_target()], capture_output=True).returncode == 0


def cmd_install(here_ok):
    if sys.platform != "darwin":
        print("no launchd on this OS; run `share_server.py serve` under your service manager instead", file=sys.stderr)
        return 1
    repo = pathlib.Path(__file__).resolve().parents[1]
    if not (repo / ".git").is_dir() and not here_ok:  # a worktree goes away under launchd
        print(f"{repo} is not a main git checkout; install from the checkout sessions load (or pass --here)",
              file=sys.stderr)
        return 1
    cfg = config()
    missing = caddy_missing(cfg)
    if missing:
        print(missing, file=sys.stderr)
        return 1
    plist, want = plist_path(), spec(cfg)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CADDY_STATE.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    if plist.exists() and plist_spec() != want:
        backup = plist.with_name(f"{plist.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(plist, backup)
        print(f"kept the previous plist as {backup}")
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    plist.write_bytes(plistlib.dumps(want))
    for _ in range(5):  # bootstrap right after bootout can fail with error 5 until the old job is gone
        p = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        if p.returncode == 0:
            print(f"wrote {plist} and loaded {agent_target()} (http://{HOST}:{config()['port']}/)")
            return 0
        time.sleep(1)
    print(f"launchctl bootstrap failed: {p.stderr.strip()}", file=sys.stderr)
    return 1


def cmd_uninstall():
    subprocess.run(["launchctl", "bootout", agent_target()], capture_output=True)
    plist_path().unlink(missing_ok=True)
    print(f"removed {agent_target()}")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "serve":
        cfg = config()
        opts = dict(zip(argv[2::2], argv[3::2]))
        return serve(opts.get("--root", cfg["root"]), opts.get("--port", cfg["port"]))
    if cmd == "install":
        return cmd_install("--here" in argv)
    if cmd == "uninstall":
        return cmd_uninstall()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
