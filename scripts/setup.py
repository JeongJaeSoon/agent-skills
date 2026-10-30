#!/usr/bin/env python3
"""Set up agent-skills on this PC in one run: load this checkout, the recommended user settings, the minimal
agent-skills.json keys, sync and the default-on automations. The human runs --apply, never the agent.

Usage:
  python3 scripts/setup.py [options]                dry run: a diff per file, then the --apply line
  python3 scripts/setup.py [options] --apply HASH   back up, merge, replace atomically, read back, then
                                                    sync, automations and setup-pc's check table
Options:
  --update          with --apply, fast-forward this checkout first (`skills-sync sync`) and check the plan again;
                    the dry run only lists that step
  --language NAME   `language` when it is unset (default: from the system locale)
  --settings-only   only the files: no launchd job, sync, automations, Orca or check
  --orca-run        bind this terminal to a new Orca Run when it has none (`run-create`)
  --actions-only    only the steps after the files: launchd job, sync, automations, Orca and check. Writes no
                    settings and takes no HASH: the agent runs it, and the plugin's guard hook asks the human first
  --replace-drifted [LABEL ...]
                    plan only this: the recommended text in place of each drifted autoMode rule (below), or only
                    of those with these labels. Nothing else is added and no step runs; still a dry run until --apply
  --settings FILE   the settings.json to plan for (default: $CLAUDE_SETTINGS, else $CLAUDE_CONFIG_DIR/settings.json)
  --bundle FILE     another recommended-settings file (tests)
  --here            allow a worktree checkout; implies --settings-only (tests)

HASH is printed with the diff and covers every planned write and action, so anything that changed since the
review no longer matches. Merging adds only what is missing and never overwrites a value, except that an enabled
marketplace copy of agent-skills is turned off; a recommended item applied once and later removed by the user is
not offered again (the record is in the agent-skills state dir).

An autoMode rule is drifted when it starts with the same label as a recommended rule (the text before the first
colon, e.g. "Scheduled janitor round:") but its text differs, as when a release rewrote the recommended one.
setup never adds the recommended text next to it; the dry run names it with a `drifted` line, and only
--replace-drifted puts the recommended text, placeholders filled, in its place (all of them, or only the labels
it names). Rules with a label of the user's own are never touched, and two rules sharing one recommended label are
left for the user to fix by hand.

The personal folder ($AGENT_SKILLS_CONFIG_DIR/personal, default ~/.config/agent-skills/personal; setup-pc clones the
user's private repo there) joins the plan when it exists: its agent-skills.json keys are added where missing, like
the recommended items, and win over a recommended default; a value already on this PC is kept and shown as a `kept`
line. A relative tracker.rules_file points into that folder. Personal autoMode rules are not carried; each PC keeps
its own.

settings.json follows the Claude Code profile ($CLAUDE_CONFIG_DIR, as Claude Code reads it); agent-skills.json is
one file per user, ~/.claude/agent-skills.json, where the skills' scripts read it (skills_json_path).
"""
import argparse, difflib, hashlib, json, locale, os, pathlib, shlex, shutil, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "skills" / "tune-automode" / "scripts"))
from automode_rule import merge as automode_merge  # noqa: E402

BUNDLE = HERE / "recommended-settings.json"
ENV_KEY = "CLAUDE_CODE_PLUGIN_DIRS"
MARKET_ID = "agent-skills@jeongjaesoon"
SECTIONS = ("allow", "soft_deny", "environment")
RULES_FILE = "tracker.rules_file"
FAST_FORWARD = "skills-sync sync --no-broadcast (fast-forward this checkout; the plan is checked again after it)"
LANGUAGES = {"ko": "Korean", "ja": "Japanese", "en": "English", "zh": "Chinese", "es": "Spanish", "fr": "French",
             "de": "German", "pt": "Portuguese"}


class Refused(Exception):
    pass


def paths():
    cfg = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    state = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
    return {"settings": pathlib.Path(os.environ.get("CLAUDE_SETTINGS") or cfg / "settings.json").expanduser(),
            "skills_json": skills_json_path(), "state": state, "record": state / "setup-applied.json",
            "personal": pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG_DIR") or "~/.config/agent-skills").expanduser()
            / "personal"}


def skills_json_path():
    """The agent-skills config: one file per user, where the skills' scripts read it, unlike settings.json, which
    follows the Claude Code profile (CLAUDE_CONFIG_DIR). AGENT_SKILLS_CONFIG moves it for setup, setup_check and
    automations.py only; the other readers keep ~/.claude/agent-skills.json."""
    return pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()


def sh(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def read_json(path, default):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"{path} is not valid JSON ({e}); fix it by hand first")
    if not isinstance(data, dict):
        raise Refused(f"{path} is not a JSON object")
    return data


def system_language():
    tags = [os.environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    if sys.platform == "darwin":
        tags.append(sh(["defaults", "read", "-g", "AppleLocale"], timeout=5)[1])
    tags.append((locale.getlocale()[0] or ""))
    for tag in tags:
        name = LANGUAGES.get((tag or "").split("_")[0].split("-")[0].lower())
        if name:
            return name
    return None


def gh_login():
    if not shutil.which("gh"):
        return ""
    rc, out = sh(["gh", "auth", "status", "--active", "--hostname", "github.com", "--json", "hosts",
                  "--jq", '.hosts["github.com"][] | select(.active) | .login'], timeout=15)
    return out if rc == 0 and out and not any(c.isspace() for c in out) else ""


def fill(value, values):
    """Placeholders filled in; None when one has no value, so the item is left out."""
    if isinstance(value, str):
        for k, v in values.items():
            if "{" + k + "}" in value:
                if not v:
                    return None
                value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [x for x in (fill(v, values) for v in value) if x is not None]
    if isinstance(value, dict):
        return {k: x for k, x in ((k, fill(v, values)) for k, v in value.items()) if x is not None}
    return value


def dig(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def put(d, dotted, value):
    *parents, last = dotted.split(".")
    for part in parents:
        d = d.setdefault(part, {})
        if not isinstance(d, dict):
            raise Refused(f"{dotted}: {part} is not an object in the existing file")
    d[last] = value


def leaves(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict) and v:
            yield from leaves(v, f"{prefix}{k}.")
        else:
            yield f"{prefix}{k}", v


def label(rule):
    head, colon, _ = rule.partition(":")
    return head if colon else None


def drifted(automode, rec):
    """(section, recommended rule, user rules with its label) for each recommended rule not in the user's list."""
    out = []
    for name in SECTIONS:
        have = automode.get(name) if isinstance(automode.get(name), list) else []
        for rule in rec.get(name) or []:
            same = [r for r in have if isinstance(r, str) and label(r) == label(rule)]
            if rule not in have and label(rule) and same:
                out.append((name, rule, same))
    return out


def drift_plan(automode, rec, notes, replace):
    """(replace items for the drifted rules `replace` names, None: none, empty: all; recommended rules whose label
    the user's list already has, which are never added next to it)."""
    drift = drifted(automode, rec)
    swap = []
    for name, rule, same in drift:
        if len(same) > 1:
            notes.append(f"kept    {len(same)} rules in autoMode.{name} start with {label(rule) + ':'!r}; "
                         "leave one, then --replace-drifted can update it")
        elif replace is not None and (not replace or label(rule) in replace):
            swap.append({"section": name, "old": same[0], "new": rule})
            notes.append(f"replace autoMode.{name} {label(rule)!r}: the recommended text takes its place")
        elif replace is None:
            notes.append(f"drifted autoMode.{name} {label(rule)!r} differs from the recommended text; "
                         "--replace-drifted shows the change")
    unknown = set(replace or ()) - {label(rule) for _, rule, _ in drift}
    if unknown:
        raise Refused(f"--replace-drifted: no drifted rule labelled {', '.join(map(repr, sorted(unknown)))}")
    return swap, {(name, rule) for name, rule, _ in drift}


def merge_settings(current, rec, skip, notes):
    """Return (new settings, item ids now present). Never overwrites; `skip` holds ids offered before."""
    new, offered = json.loads(json.dumps(current)), set()
    env = new.setdefault("env", {})
    have = env.get(ENV_KEY)
    if have and pathlib.Path(have).expanduser() != REPO:
        raise Refused(f"env.{ENV_KEY} is already {have!r}; remove it by hand if {REPO} should replace it")
    env[ENV_KEY] = str(REPO)
    if (new.get("enabledPlugins") or {}).get(MARKET_ID):
        # Loaded from both the marketplace copy and the checkout, every hook would run twice.
        new["enabledPlugins"][MARKET_ID] = False
        notes.append(f"changed enabledPlugins.{MARKET_ID} = false (the one existing value setup changes: "
                     "with the checkout loaded too, every hook would run twice)")
    for key, value in leaves({k: v for k, v in rec.items() if k != "autoMode"}):
        if isinstance(value, list):
            items = [x for x in value if f"{key}\t{x}" not in skip]
            offered |= {f"{key}\t{x}" for x in value}
            have = dig(new, key)
            if have is None:
                put(new, key, have := [])
            if not isinstance(have, list):
                raise Refused(f"{key} in settings is not a list")
            have += [x for x in items if x not in have]
        else:
            offered.add(key)
            if dig(new, key) is None and key not in skip:
                put(new, key, value)
            elif dig(new, key) not in (None, value):
                notes.append(f"kept    {key} = {dig(new, key)!r} (recommended {value!r})")
    automode = new.get("autoMode", {})
    if not isinstance(automode, dict):
        raise Refused("autoMode in settings is not an object")
    labelled = drift_plan(automode, rec.get("autoMode") or {}, notes, None)[1]
    add = {}
    for name in SECTIONS:
        rules = (rec.get("autoMode") or {}).get(name) or []
        add[name] = [r for r in rules if f"autoMode.{name}\t{r}" not in skip and (name, r) not in labelled]
        offered |= {f"autoMode.{name}\t{r}" for r in rules}
    if any(add.values()):
        new["autoMode"], _ = automode_merge(automode, {"add": add})
    return new, offered


def merge_keys(current, rec, skip, notes, personal):
    new, offered = json.loads(json.dumps(current)), set()
    for key, value in leaves(rec):
        offered.add(key)
        if dig(new, key) is None and key not in skip:
            put(new, key, value)
        elif key not in skip and dig(new, key) not in (None, value):
            notes.append(f"kept    {key} = {dig(new, key)!r} ({'personal' if key in personal else 'recommended'} {value!r})")
    return new, offered


def plan(args):
    p = paths()
    bundle = json.loads(pathlib.Path(args.bundle or BUNDLE).read_text())
    values = {"checkout": str(REPO), "state": str(p["state"]), "gh_login": gh_login()}
    rec = fill(bundle.get("settings") or {}, values)
    language = args.language or system_language()
    if language:
        rec = {"language": language, **rec}
    keys = fill(bundle.get("agent_skills_json") or {}, values)
    mine = p["personal"]
    personal = dict(leaves(read_json(mine / "agent-skills.json", {})))
    for key, value in personal.items():
        if key == RULES_FILE and isinstance(value, str) and not pathlib.Path(value).expanduser().is_absolute():
            value = str(mine / value)  # the site rules Markdown lives in the personal repo next to this file
        put(keys, key, value)  # the user's own value wins over the recommended default
    record = read_json(p["record"], {})
    skip = set(record.get("offered") or [])
    notes = []
    settings = read_json(p["settings"], {})
    if args.replace_drifted is not None:
        return replace_plan(args, p, settings, rec, notes)
    new_settings, offered = merge_settings(settings, rec, skip, notes)
    skills_json = read_json(p["skills_json"], None)
    new_skills, offered_keys = merge_keys(skills_json or {}, keys, skip, notes, personal)
    files = []
    if new_settings != settings:
        files.append((p["settings"], settings if p["settings"].exists() else None, new_settings))
    if new_skills != (skills_json or {}):
        files.append((p["skills_json"], skills_json, new_skills))
    all_offered = sorted(skip | offered | offered_keys)
    if files or all_offered != sorted(skip):
        files.append((p["record"], record or None, {"offered": all_offered}))
    actions = [FAST_FORWARD] if args.update else []
    if not args.settings_only:
        actions.append("launchd sync job (bootstrap.py)" if sys.platform == "darwin" else "cron line to add by hand")
        if not args.update:
            actions.append("skills-sync sync --no-broadcast")
        actions.append("skills-sync setup apply")
        if args.orca_run:
            actions.append("orca orchestration run-create, if this terminal has no Run")
        actions.append("setup-pc check")
    if not language:
        notes.append("language: no supported system locale; pass --language <name> to set it")
    if not values["gh_login"]:
        notes.append("gh: not logged in, so the rules about the user's repositories are left out; "
                     "after `gh auth login`, `--update` offers them")
    return files, actions, notes


def replace_plan(args, p, settings, rec, notes):
    """Only the drifted rules' replacement in settings.json: nothing added, no record, no steps."""
    automode = settings.get("autoMode", {})
    if not isinstance(automode, dict):
        raise Refused("autoMode in settings is not an object")
    swap, _ = drift_plan(automode, rec.get("autoMode") or {}, notes, {x.rstrip(":") for x in args.replace_drifted})
    if not swap:
        return [], [], notes
    new = json.loads(json.dumps(settings))
    new["autoMode"], _ = automode_merge(automode, {"replace": swap})
    return [(p["settings"], settings, new)], [FAST_FORWARD] if args.update else [], notes


def text(d):
    return json.dumps(d, ensure_ascii=False, indent=2) + "\n"


def digest(files, actions):
    body = json.dumps({"files": [[str(path), new] for path, _, new in files], "actions": actions}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def show(files, actions, notes):
    print(f"target  {paths()['settings']}")
    for note in notes:
        print(note)
    if FAST_FORWARD in actions:
        print(f"first   {FAST_FORWARD}")
    for path, old, new in files:
        if path == paths()["record"]:
            print(f"record  {path} ({len(new['offered'])} recommended items, so a removed one is not offered again)")
            continue
        before = text(old).splitlines(True) if old is not None else []
        label = str(path) if old is not None else f"{path} (new file)"
        print("".join(difflib.unified_diff(before, text(new).splitlines(True), label, label)), end="")
    for action in actions:
        if action != FAST_FORWARD:
            print(f"then    {action}")


def write(path, data):
    real = path.resolve()  # a dotfiles symlink stays a symlink
    real.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if real.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup, n = real.with_name(f"{real.name}.bak-setup-{stamp}"), 1
        while backup.exists():
            backup, n = real.with_name(f"{real.name}.bak-setup-{stamp}-{n}"), n + 1
        shutil.copy2(real, backup)
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}-")
    with os.fdopen(fd, "w") as f:
        f.write(text(data))
    if backup:
        shutil.copymode(backup, tmp)
    os.replace(tmp, real)
    if json.loads(real.read_text()) != data:
        undo = f"cp {shlex.quote(str(backup))} {shlex.quote(str(real))}" if backup else f"rm {shlex.quote(str(real))}"
        raise Refused(f"{real} did not read back as written; restore it: {undo}")
    return real, backup


def run_actions(args):
    """Names of the steps that failed."""
    sys.path.insert(0, str(HERE))
    import bootstrap
    failed = []
    sys.stdout.flush()
    try:
        bootstrap.scheduler(True)
    except SystemExit as e:
        failed.append(f"launchd sync job ({e})")
    steps = [] if args.update else [("skills-sync sync", "sync.py", "sync", "--no-broadcast")]
    for name, script, *rest in steps + [("skills-sync setup apply", "automations.py", "apply")]:
        sys.stdout.flush()
        if subprocess.run([sys.executable, str(HERE / script), *rest]).returncode:
            failed.append(name)
    if args.orca_run:
        orca_run()
    sys.stdout.flush()
    subprocess.run([sys.executable, str(REPO / "skills" / "setup-pc" / "scripts" / "setup_check.py"), "check"])
    return failed


def orca_run():
    if not shutil.which("orca") or not os.environ.get("ORCA_TERMINAL_HANDLE"):
        print("orca    not in an Orca terminal; bind the orchestrator's terminal later (orchestrate, \"Your Run\")")
        return
    rc, out = sh(["orca", "orchestration", "run-current", "--json"])
    try:
        bound = (json.loads(out).get("result") or {}).get("run")
    except ValueError:
        print(f"orca    run-current failed (rc {rc}); run it by hand: orca orchestration run-current --json")
        return
    if bound:
        print(f"orca    this terminal is already bound to {bound.get('id')}")
        return
    rc, out = sh(["orca", "orchestration", "run-create", "--objective", "orchestrator"])
    print(f"orca    run-create: {'ok' if rc == 0 else 'failed'} {out[:200]}")


def preflight(args):
    need = ("git", "python3") if args.settings_only else ("git", "python3", "claude")
    missing = [t for t in need if not shutil.which(t)]
    if missing:
        raise Refused(f"missing on PATH: {', '.join(missing)}")
    if args.here:
        return
    if (REPO / ".git").is_file():
        raise Refused(f"{REPO} is a worktree; run this from the main checkout")
    _, branch = sh(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "main":
        raise Refused(f"{REPO} is on {branch!r}; switch it to main first")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", metavar="HASH")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--language")
    ap.add_argument("--settings-only", action="store_true")
    ap.add_argument("--orca-run", action="store_true")
    ap.add_argument("--actions-only", action="store_true")
    ap.add_argument("--replace-drifted", nargs="*", metavar="LABEL")
    ap.add_argument("--settings", type=pathlib.Path)
    ap.add_argument("--bundle", type=pathlib.Path)
    ap.add_argument("--here", action="store_true")
    args = ap.parse_args(argv)
    if args.actions_only and (args.apply or args.settings_only or args.update or args.here
                              or args.replace_drifted is not None):
        ap.error("--actions-only takes none of --apply, --settings-only, --update, --here, --replace-drifted")
    args.settings_only = args.settings_only or args.here or args.replace_drifted is not None
    if args.settings:
        os.environ["CLAUDE_SETTINGS"] = str(args.settings.expanduser().absolute())
    try:
        preflight(args)
        if args.actions_only:
            failed = run_actions(args)
            if failed:
                raise Refused(f"these steps failed: {', '.join(failed)}; fix the cause, then run it again")
            return 0
        if args.update and args.apply:
            sys.stdout.flush()
            if subprocess.run([sys.executable, str(HERE / "sync.py"), "sync", "--no-broadcast"]).returncode:
                raise Refused("skills-sync sync failed; `skills-sync status` shows why")
        files, actions, notes = plan(args)
        h = digest(files, actions)
        if args.apply and args.apply != h:
            moved = "the fast-forward brought a different plan; " if args.update else ""
            raise Refused(f"--apply {args.apply} does not match the current plan ({h}); {moved}review the diff again")
        show(files, actions, notes)
        if not files:
            print("no change: the settings files already have everything recommended"
                  + ("; the steps above still run" if actions else ""))
        if not args.apply:
            if files or actions:
                print(f"\n(dry run; nothing written; plan hash {h})\nApply with:\n"
                      f"  ! python3 -I {' '.join(map(shlex.quote, [str(HERE / 'setup.py'), *argv]))} --apply {h}")
            return 0
        print()
        undo = []
        try:
            for path, _, new in files:
                try:
                    real, backup = write(path, new)
                except OSError as e:
                    raise Refused(f"writing {path} failed ({e})")
                print(f"written {real}" + (f"   (backup {backup})" if backup else ""))
                undo.append(f"cp {shlex.quote(str(backup))} {shlex.quote(str(real))}" if backup else f"rm {shlex.quote(str(real))}")
        finally:
            if undo:
                print("rollback:\n" + "\n".join(f"  {u}" for u in undo))
        failed = run_actions(args) if not args.settings_only else []
        if failed:
            raise Refused(f"the settings files are written, but these steps failed: {', '.join(failed)}; "
                          "fix the cause, then dry run again")
        print("done. New sessions read the replaced rules; restart the ones already running." if args.replace_drifted is not None
              else f"done. New sessions load {REPO}; restart the ones already running.")
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
