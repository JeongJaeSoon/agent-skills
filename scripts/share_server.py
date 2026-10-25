#!/usr/bin/env python3
"""Serve a share folder read-only on loopback through copyparty: image thumbnails and viewer, markdown, search.

Usage:
  share_server.py serve [--root DIR] [--port N]   # foreground; defaults from config
  share_server.py install [--here]                # macOS: build the copyparty venv, keep `serve` running as a LaunchAgent
  share_server.py uninstall

copyparty and its dependencies are pinned in PINS and installed into a venv under VENVS (venv_dir()). Everyone
gets read access and nothing else: no accounts, uploads, deletes, moves or WebDAV. A folder's index.html opens
instead of its listing, folders open as a thumbnail grid, and search covers file names and the first 4 KiB of
text files (share_text_tag.py); a browser's plain link to a .md file opens rendered (share_copyparty.py). The
search index and thumbnails live in HIST and copyparty's salts in STATE_DIR, both outside the shared folder.

Binds 127.0.0.1 only; publishing on a network (e.g. `tailscale serve`) is left to the owner. Settings live under
the "share-server" key of ~/.claude/agent-skills.json (see DEFAULTS); "label" lets an existing LaunchAgent be taken
over in place, and the plist it replaces is kept next to it as <name>.plist.bak-<time>.
"""
import hashlib, json, os, pathlib, plistlib, re, shutil, subprocess, sys, time

CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()
STATE_DIR = pathlib.Path(os.environ.get("SHARE_SERVER_STATE", "~/.local/state/agent-skills")).expanduser()
VENVS = pathlib.Path("~/.local/share/agent-skills").expanduser()
HIST = pathlib.Path("~/.cache/agent-skills/copyparty").expanduser()
LOG = STATE_DIR / "share-server.log"
HOST = "127.0.0.1"
DEFAULTS = {"root": "~/share", "port": 13201, "label": "io.github.jeongjaesoon.share-server", "origins": []}
ORIGIN = re.compile(r"https?://[^/,\s]+")
# Each pin was released at least 14 days before it was adopted.
PINS = ("copyparty==1.20.25", "Jinja2==3.1.6", "MarkupSafe==3.0.4", "Pillow==12.3.0", "mutagen==1.48.1")
LAUNCHER = pathlib.Path(__file__).resolve().with_name("share_copyparty.py")
TEXT_TAG = LAUNCHER.with_name("share_text_tag.py")
TEXT_EXT = ("md", "markdown", "txt", "html", "htm", "json", "csv", "log", "yaml", "yml")
# Source and config files copyparty would send as a download (or, for ts, as video); show them as text instead.
PLAIN_EXT = ("ts", "tsx", "jsx", "mjs", "py", "sh", "go", "rs", "yaml", "yml", "toml", "log", "diff", "patch", "tsv")


def config():
    try:
        mine = json.loads(CONFIG.read_text()).get("share-server") or {}
    except (OSError, ValueError):
        mine = {}
    return {**DEFAULTS, **mine}


def venv_dir():
    """One venv per (python minor version, PINS), so a new set is built beside the one a running server uses."""
    key = f"{sys.version_info.major}.{sys.version_info.minor}\n" + "\n".join(PINS)
    return VENVS / f"copyparty-{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def ready(venv):
    marker = venv / ".pins"
    return marker.is_file() and marker.read_text() == "\n".join(PINS) and (venv / "bin" / "python").exists()


def ensure_venv():
    """The python of venv_dir(), building that venv when it is missing or incomplete."""
    venv = venv_dir()
    if not ready(venv):
        shutil.rmtree(venv, ignore_errors=True)
        venv.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "--only-binary", ":all:", *PINS], check=True)
        (venv / ".pins").write_text("\n".join(PINS))
    return venv / "bin" / "python"


def prune_venvs(keep):
    for d in VENVS.glob("copyparty-*"):
        if d != keep and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def copyparty_args(root, port, origins=()):
    mtp = f"body=an,{','.join('e' + e for e in TEXT_EXT)},t10,{TEXT_TAG}"
    return ["-I", str(LAUNCHER), "-i", HOST, "-p", str(int(port)), "--http-only", "-q",
            # index names and text bodies for search; skip content hashes, which only serve upload dedup
            "--hist", str(HIST), "-e2dsa", "-e2ts", "--no-hash", ".", "--re-maxage", "60",
            "-mtp", mtp, "-mte", "+body", "-mth", "body",
            *[a for e in PLAIN_EXT for a in ("--mime", f"{e}=text/plain")],
            "--ih", "--grid", "--xvol", "--no-robots", "--no-logues",
            # read-only beyond the volume's r permission: no WebDAV, no writes through any other door
            "--no-dav", "--no-del", "--no-mv", "--no-cp", "--unpost", "0", "--urlform", "get",
            # keep the hostname, free disk space and other visitors' activity off the page
            "-nih", "-nid", "--dl-list", "0", "--ups-who", "0",
            # a TLS-terminating proxy in front (tailscale serve) sends X-Forwarded-For but no X-Forwarded-Proto; without
            # these, copyparty takes the page for http and rejects the https Origin of its own search and zip POSTs
            "--rproxy", "-1", "--xf-proto-fb", "https",
            # copyparty's default --acao * lets any website read files and listings from a visitor's browser; allow only
            # the page's own origin plus the configured ones (a proxy chain can hide the public host from copyparty)
            "--acao", ",".join(origins),
            "-v", f"{root}::r"]


def serve(root, port, venv=None, origins=()):
    root = str(pathlib.Path(root).expanduser().resolve())
    if not os.path.isdir(root):
        print(f"{root} is not a directory", file=sys.stderr)
        return 1
    bad = [o for o in origins if not isinstance(o, str) or not ORIGIN.fullmatch(o)]
    if bad:
        print(f"share-server.origins wants entries like https://files.example.test, not {bad}", file=sys.stderr)
        return 1
    if ":" in root:  # copyparty's -v splits on it
        print(f"{root}: a root path with ':' is not supported", file=sys.stderr)
        return 1
    if venv:  # launchd passes the venv install built; never pip-install inside a KeepAlive restart loop
        py = pathlib.Path(venv) / "bin" / "python"
        if not ready(pathlib.Path(venv)):
            print(f"{venv} is missing or incomplete; run `share_server.py install` again", file=sys.stderr)
            return 1
    else:
        try:
            py = ensure_venv()
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"could not build the copyparty venv at {venv_dir()}: {e}", file=sys.stderr)
            return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HIST.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(STATE_DIR)  # copyparty keeps its salts and session db in <this>/copyparty
    print(f"serving {root} read-only on http://{HOST}:{port}/", flush=True)
    os.execv(py, [str(py), *copyparty_args(root, port, origins)])


# ---------------------------------------------------------------- launchd

def plist_path(cfg=None):
    return pathlib.Path(f"~/Library/LaunchAgents/{(cfg or config())['label']}.plist").expanduser()


def agent_target(cfg=None):
    return f"gui/{os.getuid()}/{(cfg or config())['label']}"


def spec(cfg=None):
    cfg = cfg or config()
    return {"Label": cfg["label"],
            "ProgramArguments": [shutil.which("python3") or sys.executable, "-I", str(pathlib.Path(__file__).resolve()),
                                 "serve", "--root", str(pathlib.Path(cfg["root"]).expanduser()),
                                 "--port", str(int(cfg["port"])), "--venv", str(venv_dir())],
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
    try:  # before bootout, so a failed build leaves the running server alone
        ensure_venv()
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"could not build the copyparty venv at {venv_dir()}: {e}", file=sys.stderr)
        return 1
    plist, want = plist_path(), spec()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
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
            prune_venvs(venv_dir())
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
        return serve(opts.get("--root", cfg["root"]), opts.get("--port", cfg["port"]), opts.get("--venv"), cfg["origins"])
    if cmd == "install":
        return cmd_install("--here" in argv)
    if cmd == "uninstall":
        return cmd_uninstall()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
