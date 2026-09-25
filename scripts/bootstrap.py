#!/usr/bin/env python3
"""Set this PC up to load this checkout live and keep it in sync (docs/platform.md §5).

Usage:
  python3 scripts/bootstrap.py            # dry run: print what would change
  python3 scripts/bootstrap.py --write    # apply

Run it from the checkout that sessions should load (the main one, on `main`), not a worktree. Running it
again changes nothing that is already in place.
"""
import json, os, pathlib, platform, plistlib, shutil, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = pathlib.Path(os.environ.get("CLAUDE_SETTINGS", "~/.claude/settings.json")).expanduser()
LABEL = "io.github.jeongjaesoon.agent-skills-sync"
PLIST = pathlib.Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
STATE = pathlib.Path("~/.local/state/agent-skills").expanduser()
ENV_KEY = "CLAUDE_CODE_PLUGIN_DIRS"
MARKET_ID = "agent-skills@jeongjaesoon"
INTERVAL_S = 900


def preflight():
    missing = [t for t in ("git", "python3", "claude") if not shutil.which(t)]
    if missing:
        sys.exit(f"missing on PATH: {', '.join(missing)}")
    if (REPO / ".git").is_file():
        sys.exit(f"{REPO} is a worktree; run this from the main checkout")
    branch = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    if branch != "main":
        sys.exit(f"{REPO} is on {branch!r}; switch it to main first")


def settings_change():
    """Return the new settings dict, or None when nothing needs to change."""
    d = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    changed = False
    env = d.setdefault("env", {})
    have = env.get(ENV_KEY)
    if have == str(REPO):
        print(f"ok      env.{ENV_KEY} = {REPO}")
    elif have:
        sys.exit(f"env.{ENV_KEY} is already {have!r}; remove it by hand if this checkout should replace it")
    else:
        print(f"set     env.{ENV_KEY} = {REPO}   (was unset)")
        env[ENV_KEY] = str(REPO)
        changed = True
    enabled = d.get("enabledPlugins", {})
    if enabled.get(MARKET_ID):
        # Loaded from both the marketplace copy and the checkout, every hook would run twice.
        print(f"set     enabledPlugins.{MARKET_ID} = false   (was {enabled[MARKET_ID]!r})")
        enabled[MARKET_ID] = False
        changed = True
    return d if changed else None


def plist():
    log = str(STATE / "sync.log")
    return {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/env", "python3", str(REPO / "scripts" / "sync.py"), "sync"],
        "StartInterval": INTERVAL_S,
        "RunAtLoad": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        # launchd starts with a bare PATH; orca and git from Homebrew live outside it.
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
    }


def scheduler(write):
    if platform.system() != "Darwin":
        print(f"cron    add by hand: */15 * * * * python3 {REPO}/scripts/sync.py sync >> {STATE}/sync.log 2>&1")
        return
    want = plistlib.dumps(plist())
    if PLIST.exists() and PLIST.read_bytes() == want:
        print(f"ok      launchd {LABEL}")
        return
    print(f"write   {PLIST} (every {INTERVAL_S // 60} min and at login)")
    if write:
        STATE.mkdir(parents=True, exist_ok=True)
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        PLIST.write_bytes(want)
        subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)


def main():
    write = "--write" in sys.argv
    preflight()
    new = settings_change()
    if write and new:
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        if SETTINGS.exists():
            shutil.copy2(SETTINGS, backup)
            print(f"backup  {backup}")
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(SETTINGS)
    scheduler(write)
    if not write:
        print("(dry run; add --write to apply)")
        return 0
    subprocess.run([sys.executable, str(REPO / "scripts" / "sync.py"), "sync", "--no-broadcast"])
    print(f"done. New sessions load {REPO}. Running ones pick it up only after a restart.\n"
          f"If `claude` has never run in {REPO}, start it there once and accept the folder trust prompt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
