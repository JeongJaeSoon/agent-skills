#!/usr/bin/env python3
"""Set this PC up to load this checkout live and turn on the default-on automations, which include the sync job
(docs/platform.md §5, docs/automation.md).

Usage:
  python3 scripts/bootstrap.py            # dry run: print what would change
  python3 scripts/bootstrap.py --write    # apply

Run it from the checkout that sessions should load (the main one, on `main`), not a worktree. Running it
again changes nothing that is already in place.
"""
import json, os, pathlib, shutil, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
# The file Claude Code reads, as setup.py picks it: an Orca profile sets CLAUDE_CONFIG_DIR.
SETTINGS = pathlib.Path(os.environ.get("CLAUDE_SETTINGS")
                        or pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude") / "settings.json").expanduser()
LABEL = "io.github.jeongjaesoon.agent-skills-sync"
PLIST = pathlib.Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
STATE = pathlib.Path("~/.local/state/agent-skills").expanduser()
ENV_KEY = "CLAUDE_CODE_PLUGIN_DIRS"
MARKET_ID = "agent-skills@jeongjaesoon"


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


def legacy_agent():
    """The LaunchAgent plist an older bootstrap wrote, if it is still there. Nothing here creates or removes it:
    the sync job is the `sync` Orca automation (automations.py), and the owner removes the old plist by hand."""
    return PLIST if PLIST.exists() else None


def main():
    write = "--write" in sys.argv
    preflight()
    new = settings_change()
    if write and new:
        target = SETTINGS.resolve()  # a dotfiles symlink stays a symlink
        backup = target.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        if target.exists():
            shutil.copy2(target, backup)
            print(f"backup  {backup}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
        if target.exists():
            shutil.copymode(target, tmp)
        tmp.replace(target)
    if old := legacy_agent():
        print(f"legacy  {old} is a LaunchAgent from an older setup; the sync job is now an Orca automation, so remove "
              f"the old one yourself: launchctl bootout gui/{os.getuid()}/{LABEL} && rm {old}")
    sys.stdout.flush()  # keep this output ahead of the children's
    automations = [sys.executable, str(REPO / "scripts" / "automations.py")]
    if not write:
        subprocess.run([*automations, "status"])
        print("(dry run; add --write to apply, which also runs `skills-sync setup apply`)")
        return 0
    subprocess.run([sys.executable, str(REPO / "scripts" / "sync.py"), "sync", "--no-broadcast"])
    subprocess.run([*automations, "apply"])
    print(f"done. New sessions load {REPO}. Restart the running ones: a /reload-plugins there drops the\n"
          f"marketplace copy without adding the checkout, and leaves them with no agent-skills at all.\n"
          f"If `claude` has never run in {REPO}, start it there once and accept the folder trust prompt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
