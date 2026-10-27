#!/usr/bin/env python3
"""Change the user's agent-skills.json keys, permissions.allow and autoMode rules. The user runs --apply, never the agent.

  config_emit.py emit --spec SPEC --out FILE   check SPEC, print the diff, write a standalone copy with SPEC embedded
  config_emit.py --spec SPEC [--apply HASH]    dry run (default) or apply
  FILE [--apply HASH]                          the emitted copy: same, with its embedded spec

SPEC: {"agent_skills": {"set": {"prs.after_hours": "send-now"}, "unset": ["prs.cron"], "allow_unknown": ["prs.new_key"]},
       "permissions": {"add": ["Edit(//abs/path/**)"], "remove": ["..."]},
       "autoMode": <a tune-automode spec: add / replace / remove>,
       "settings": ["profile", "home"]}

"settings" picks the settings.json files permissions and autoMode go to: "profile" is $CLAUDE_CONFIG_DIR/settings.json
(as Claude Code reads it), "home" is ~/.claude/settings.json; default ["profile"]; the same real file is written once.
agent-skills.json is one file per user, ~/.claude/agent-skills.json ($AGENT_SKILLS_CONFIG moves it).
HASH is the spec hash printed with the diff; a spec edited after review no longer matches it.
"""
import argparse
import ast
import difflib
import hashlib
import json
import os
import pathlib
import pprint
import re
import shlex
import shutil
import sys
import tempfile
import time
import types

SPEC = None  # emit replaces this line
AUTOMODE_SOURCE = None  # emit replaces this line

HOME_DIR = pathlib.Path.home() / ".claude"
CONFIG_DIR = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME_DIR).expanduser()
SETTINGS = {"profile": CONFIG_DIR / "settings.json", "home": HOME_DIR / "settings.json"}
SKILLS_JSON = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
HERE = pathlib.Path(__file__).resolve()
# An emitted copy can sit anywhere, even at /tmp/x.py; it never reads the repo.
REPO = HERE.parents[3] if len(HERE.parents) > 3 else HERE.parent
AUTOMODE_PATH = REPO / "skills" / "tune-automode" / "scripts" / "automode_rule.py"

# Where each agent-skills.json section is documented (key names, "a | b | c" values) and where its DEFAULTS live
# (types). A dotted prefix maps a nested section to its own DEFAULTS.
SECTIONS = {
    "prs": (["skills/watch-prs/SKILL.md"],
            {"": "skills/watch-prs/scripts/prs.py", "notify": "skills/watch-prs/scripts/notify.py"}),
    "mentions": (["skills/watch-mentions/SKILL.md"], {"": "skills/watch-mentions/scripts/mentions.py"}),
    "reap": (["skills/reap-resources/SKILL.md", "docs/pressure.md"],
             {"pressure": "skills/reap-resources/scripts/pressure.py"}),
    "tracker": (["skills/use-tracker/SKILL.md", "skills/use-tracker/references/linear.md"], {}),
    "notes": (["skills/use-notes/SKILL.md"], {}),
    "reflect": (["skills/reflect/SKILL.md"], {}),
    "brief": (["skills/brief/SKILL.md"], {}),
    "orchestrator_guard": (["docs/orchestrator-guard.md"], {}),
    "preview-watch": (["docs/preview-watch.md"], {"": "scripts/preview_watch.py"}),
    "share-server": (["docs/share-server.md"], {"": "scripts/share_server.py"}),
    "reap-hard": (["docs/reap-hard.md"], {"": "scripts/reap_hard.py"}),
}
PATH_TOOLS = ("Edit", "Write", "Read", "MultiEdit", "NotebookEdit")
# A prefix rule on one of these runs anything.
RUNNERS = {"bash", "sh", "zsh", "env", "sudo", "xargs", "eval", "exec", "python", "python3", "node", "ruby", "perl",
           "npx", "uvx", "osascript"}
SECRET = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|xox[abposr]-|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}"
                    r"|-----BEGIN|(?<![A-Za-z0-9/])(?=[A-Za-z0-9_+=-]*\d)(?=[A-Za-z0-9_+=-]*[A-Za-z])[A-Za-z0-9_+=-]{40,}")
SECRET_KEY = re.compile(r"token|secret|passw|api_?key|credential", re.I)


class Refused(Exception):
    pass


def automode():
    mod = types.ModuleType("automode_rule")
    exec(compile(AUTOMODE_SOURCE or AUTOMODE_PATH.read_text(), "automode_rule.py", "exec"), mod.__dict__)
    return mod


def check_spec(spec, am):
    if not isinstance(spec, dict) or set(spec) - {"agent_skills", "permissions", "autoMode", "settings"}:
        raise Refused("spec must be an object with only 'agent_skills', 'permissions', 'autoMode' and 'settings'")
    keys, perms = spec.get("agent_skills", {}), spec.get("permissions", {})
    if not isinstance(keys, dict) or set(keys) - {"set", "unset", "allow_unknown"}:
        raise Refused("agent_skills takes only 'set', 'unset' and 'allow_unknown'")
    if not isinstance(keys.get("set", {}), dict) or not all(isinstance(k, list) for k in
                                                              (keys.get("unset", []), keys.get("allow_unknown", []))):
        raise Refused("agent_skills.set must be an object, unset and allow_unknown lists")
    for key in [*keys.get("set", {}), *keys.get("unset", [])]:
        if not isinstance(key, str) or not re.fullmatch(r"[\w-]+(\.[\w-]+)*", key):
            raise Refused(f"agent_skills key must be a dotted path, got {key!r}")
        if SECRET_KEY.search(key):
            raise Refused(f"{key}: secrets do not go in agent-skills.json; keep them in the keychain or ~/.config")
    for key, value in keys.get("set", {}).items():
        check_secret(value, key)
    if not isinstance(perms, dict) or set(perms) - {"add", "remove"} or \
            not all(isinstance(perms.get(k, []), list) for k in ("add", "remove")):
        raise Refused("permissions takes only 'add' and 'remove' lists")
    for rule in [*perms.get("add", []), *perms.get("remove", [])]:
        check_permission(rule)
    if "autoMode" in spec:
        try:
            am.check_spec(spec["autoMode"])
        except am.Refused as e:
            raise Refused(f"autoMode: {e}")
        check_secret(spec["autoMode"], "autoMode")
    targets = spec.get("settings", ["profile"])
    if not isinstance(targets, list) or not targets or set(targets) - set(SETTINGS):
        raise Refused(f"settings must be a non-empty list of {', '.join(SETTINGS)}")
    if not keys.get("set") and not keys.get("unset") and not perms.get("add") and not perms.get("remove") \
            and "autoMode" not in spec:
        raise Refused("spec changes nothing")


def check_secret(value, where):
    if isinstance(value, dict):
        for k, v in value.items():
            check_secret(v, f"{where}.{k}")
    elif isinstance(value, list):
        for v in value:
            check_secret(v, where)
    elif isinstance(value, str) and SECRET.search(value):
        raise Refused(f"{where}: the value looks like a secret; keep it out of settings files")


def check_permission(rule):
    if not isinstance(rule, str) or not rule.strip():
        raise Refused(f"a permission rule must be a non-empty string, got {rule!r}")
    check_secret(rule, "permissions")
    m = re.fullmatch(r"([\w-]+)(?:\((.*)\))?", rule.strip(), re.S)
    if not m:
        raise Refused(f"not a permission rule: {rule!r}; use Tool or Tool(specifier)")
    tool, spec = m.groups()
    if spec is None:
        if not re.fullmatch(r"mcp__[\w-]+__[\w-]+", tool):
            raise Refused(f"{rule}: a bare tool name allows every use of it; add a specifier")
        return
    if not spec.strip() or not re.sub(r"domain:|[*/:~.\s]", "", spec):
        raise Refused(f"{rule}: the specifier matches everything")
    if tool == "Bash":
        head = re.split(r"[\s:]", spec.strip(), maxsplit=1)[0]
        rest = spec.strip()[len(head):].strip(" :*")
        if head in RUNNERS and not rest:
            raise Refused(f"{rule}: {head} runs any command; name the command it runs")
    if tool in PATH_TOOLS:
        path = rule_path(spec)
        literal = pathlib.Path(re.split(r"[*?\[{]", path, maxsplit=1)[0] or "/")
        home = pathlib.Path.home()
        if literal in (home, *home.parents) or literal == pathlib.Path("/"):
            raise Refused(f"{rule}: covers the whole home directory or more")
        for protected in protected_paths():
            if glob_match(path, str(protected)) or glob_match(path, os.path.realpath(protected)):
                raise Refused(f"{rule}: matches {protected}; settings, instructions, skills and hooks are the user's to edit")


def rule_path(spec):
    if spec.startswith("//"):
        return spec[1:]
    if spec.startswith("~/"):
        return str(pathlib.Path.home()) + spec[1:]
    raise Refused(f"{spec}: use an absolute path (//abs/path or ~/path) in user settings")


def protected_paths():
    out = [SKILLS_JSON]
    for cfg in {CONFIG_DIR, HOME_DIR}:
        out += [cfg / "settings.json", cfg / "settings.local.json", cfg / "CLAUDE.md", cfg / "agents" / "a.md",
                cfg / "skills" / "s" / "SKILL.md", cfg / "hooks" / "h.py", cfg / "plugins" / "p" / "x.json"]
    return out


def glob_match(pattern, path):
    rx = "".join(".*" if t == "**" else "[^/]*" if t == "*" else "[^/]" if t == "?" else re.escape(t)
                 for t in re.split(r"(\*\*|\*|\?)", pattern))
    return re.fullmatch(rx, path) is not None


def with_twins(rules):
    """Each path rule, plus the same rule on the real path when its literal part goes through a symlink:
    a permission rule matches the path as the tool sees it, which can be either one."""
    out = []
    for rule in rules:
        out.append(rule)
        m = re.fullmatch(r"([\w-]+)\((//.*)\)", rule)
        if not m or m.group(1) not in PATH_TOOLS:
            continue
        path = rule_path(m.group(2))
        literal = re.split(r"[*?\[{]", path, maxsplit=1)[0]
        base = literal if literal.endswith("/") else os.path.dirname(literal) + "/"
        real = os.path.realpath(base).rstrip("/") + "/"
        if real != base:
            out.append(f"{m.group(1)}(/{real}{path[len(base):]})")
    return list(dict.fromkeys(out))


def check_keys(keys):
    """Every key set or unset is documented for its section; enum and type follow the docs and DEFAULTS."""
    allowed = set(keys.get("allow_unknown", []))
    for key in [*keys.get("set", {}), *keys.get("unset", [])]:
        if key in allowed:
            continue
        section, *rest = key.split(".")
        if section not in SECTIONS:
            raise Refused(f"{key}: no skill documents a {section!r} section; list it in agent_skills.allow_unknown "
                          f"if it is meant")
        docs, default_files = SECTIONS[section]
        text = "\n".join((REPO / d).read_text() for d in docs)
        node = defaults(REPO / default_files[""]) if "" in default_files else {}
        for i, part in enumerate(rest):
            if i and ".".join(rest[:i]) in default_files:
                node = defaults(REPO / default_files[".".join(rest[:i])])
            known = isinstance(node, dict) and part in node or \
                re.search(rf'"{re.escape(part)}"\s*:|`{re.escape(part)}`|\.{re.escape(part)}\b', text)
            if not known:
                raise Refused(f"{key}: {part!r} is not a documented key of {section}; list it in "
                              f"agent_skills.allow_unknown if it is meant")
            node = node.get(part) if isinstance(node, dict) else None
            if node == {} and i < len(rest) - 1:
                node = None  # a free-form map: its keys are the user's
                break
        if key in keys.get("set", {}):
            check_value(key, keys["set"][key], node, text)


def check_value(key, value, default, text):
    leaf = key.rsplit(".", 1)[-1]
    m = re.search(rf'"{re.escape(leaf)}"\s*:\s*"([^"<]*\|[^"<]*)"', text)
    if m and value not in [v.strip() for v in m.group(1).split("|")]:
        raise Refused(f"{key}: {value!r} is not one of {m.group(1)}")
    if default is None or value is None:
        return
    kind = (bool,) if isinstance(default, bool) else (int, float) if isinstance(default, (int, float)) \
        else (type(default),)
    if not isinstance(value, kind) or isinstance(value, bool) != isinstance(default, bool):
        raise Refused(f"{key}: expected {type(default).__name__} like the default {default!r}, got {value!r}")


def defaults(path):
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "DEFAULTS" for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]


def load(path, missing_ok=False):
    if not path.is_file():
        if missing_ok:
            return {}
        raise Refused(f"{path} does not exist")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise Refused(f"{path} is not valid JSON ({e}); fix it by hand first")
    if not isinstance(data, dict):
        raise Refused(f"{path} is not a JSON object")
    return data


def set_keys(data, keys, notes):
    out = json.loads(json.dumps(data))
    for key in keys.get("unset", []):
        *parents, last = key.split(".")
        node = out
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict) or last not in node:
            # A typo and an earlier apply look the same here, so neither writes anything.
            raise Refused(f"unset: {key} is not set (already removed?)")
        del node[last]
    for key, value in keys.get("set", {}).items():
        *parents, last = key.split(".")
        node = out
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise Refused(f"set: {key} goes through {part}, which is not an object in agent-skills.json")
        if node.get(last, object()) == value:
            notes.append(f"already set: {key}")
        node[last] = value
    return out


def merge_settings(data, spec, am, notes, path):
    out = json.loads(json.dumps(data))
    perms = spec.get("permissions", {})
    if perms.get("add") or perms.get("remove"):
        block = out.setdefault("permissions", {})
        if not isinstance(block, dict) or not isinstance(block.setdefault("allow", []), list):
            raise Refused(f"permissions.allow in {path} is not a list")
        allow = block["allow"]
        for rule in perms.get("remove", []):
            if rule not in allow:
                raise Refused(f"remove: not in permissions.allow of {path} (already removed?): {rule[:80]}")
            allow.remove(rule)
        for rule in perms.get("add", []):
            if rule in allow:
                notes.append(f"already allowed: {rule[:80]}")
            else:
                allow.append(rule)
    if "autoMode" in spec:
        current = out.get("autoMode", {})
        if not isinstance(current, dict):
            raise Refused(f"autoMode in {path} is not an object")
        try:
            out["autoMode"], am_notes = am.merge(current, spec["autoMode"])
        except am.Refused as e:
            raise Refused(f"autoMode in {path}: {e}")
        notes += am_notes
    return out


def diff(before, after, label):
    def text(d):
        return (json.dumps(d, ensure_ascii=False, indent=2) + "\n").splitlines(True)
    if label == "settings":
        keys = [k for k in ("permissions", "autoMode") if before.get(k) != after.get(k)]
        before, after = {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}
    return "".join(difflib.unified_diff(text(before), text(after), f"{label} (current)", f"{label} (new)"))


def targets(spec, settings_override):
    files = settings_override or [SETTINGS[name] for name in spec.get("settings", ["profile"])]
    return list({p.resolve(): p for p in files}.values())


def plan(spec, settings_override, skills_json):
    am = automode()
    out = []
    keys = spec.get("agent_skills", {})
    if keys.get("set") or keys.get("unset"):
        notes = []
        before = load(skills_json, missing_ok=True)
        out.append((skills_json, before, set_keys(before, keys, notes), notes, "agent-skills.json"))
    if spec.get("permissions") or "autoMode" in spec:
        for path in targets(spec, settings_override):
            notes = []
            before = load(path)
            out.append((path, before, merge_settings(before, spec, am, notes, path), notes, "settings"))
    return out


def backup(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target, n = path.with_name(f"{path.name}.bak-skills-config-{stamp}"), 1
    while target.exists():
        target, n = path.with_name(f"{path.name}.bak-skills-config-{stamp}-{n}"), n + 1
    shutil.copy2(path, target)
    return target


def write(path, data):
    real = path.resolve()  # a dotfiles symlink stays a symlink
    saved = backup(real) if real.exists() else None
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}-")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if saved:
        shutil.copymode(saved, tmp)
    os.replace(tmp, real)
    undo = f"cp {shlex.quote(str(saved))} {shlex.quote(str(real))}" if saved else f"rm {shlex.quote(str(real))}"
    if json.loads(real.read_text()) != data:
        raise Refused(f"{real} did not read back as written; restore it: {undo}")
    return real, saved, undo


def run(spec, settings_override, skills_json, apply):
    digest = spec_hash(spec)
    if apply and apply != digest:
        raise Refused(f"--apply {apply} does not match this spec ({digest}); review the diff again")
    changes = []
    for path, before, after, notes, label in plan(spec, settings_override, skills_json):
        print(f"== {label}: {path}" + (f" -> {path.resolve()}" if path.resolve() != path.absolute() else ""))
        for note in notes:
            print(note)
        text = diff(before, after, label)
        print(text or "no change", end="" if text else "\n")
        if text:
            changes.append((path, after))
    if not changes:
        print("\nno change: every file already has this spec")
        return
    if not apply:
        print(f"\n(dry run; nothing written; spec hash {digest})")
        return
    print()
    for path, after in changes:
        real, saved, undo = write(path, after)
        print(f"written: {real}\nbackup:  {saved or '(new file)'}\nrollback: {undo}")
    print("Settings changes reach sessions started after this; restart a session that still sees the old ones.")


def emit(spec, out, settings_override, skills_json):
    source = pathlib.Path(__file__).read_text()
    markers = ("SPEC = None  # emit replaces this line\n", "AUTOMODE_SOURCE = None  # emit replaces this line\n")
    if not all(m in source for m in markers):
        raise Refused("this copy is already an emitted script; emit from the skill's config_emit.py")
    out = out.expanduser().absolute()
    for cfg in {CONFIG_DIR, HOME_DIR}:
        if out.resolve().is_relative_to(cfg.resolve()):
            raise Refused(f"--out must be outside {cfg}; the agent does not write there")
    run(spec, settings_override, skills_json, apply=None)
    body = source.replace(markers[0], f"SPEC = {pprint.pformat(spec, width=110, sort_dicts=False)}\n", 1)
    body = body.replace(markers[1], f"AUTOMODE_SOURCE = {AUTOMODE_PATH.read_text()!r}\n", 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    out.chmod(0o600)
    extra = "".join(f" --settings {shlex.quote(str(p.absolute()))}" for p in settings_override or [])
    if skills_json != SKILLS_JSON:
        extra += f" --skills-json {shlex.quote(str(skills_json.absolute()))}"
    print(f"\nwrote {out}\nThe user reviews it and runs:\n  ! python3 {shlex.quote(str(out))}{extra} --apply {spec_hash(spec)}")


def prepare(spec):
    """Check a spec read from --spec against the docs and add symlink twins; an emitted spec is already final."""
    check_spec(spec, automode())
    check_keys(spec.get("agent_skills", {}))
    perms = spec.get("permissions")
    if perms:
        spec["permissions"] = {k: with_twins(v) for k, v in perms.items()}
        for rule in [r for r in spec["permissions"].get("add", []) if r not in perms.get("add", [])]:
            check_permission(rule)
            print(f"symlink twin added: {rule}")
    return spec


def main(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=["emit"])
    p.add_argument("--spec", type=pathlib.Path)
    p.add_argument("--out", type=pathlib.Path)
    p.add_argument("--settings", type=pathlib.Path, action="append", help="settings.json to use instead of the spec's")
    p.add_argument("--skills-json", type=pathlib.Path, default=SKILLS_JSON)
    p.add_argument("--apply", metavar="HASH")
    a = p.parse_args(argv)
    try:
        if a.spec:
            try:
                spec = prepare(json.loads(a.spec.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                raise Refused(f"cannot read spec {a.spec}: {e}")
        elif SPEC is not None:
            spec = SPEC
            check_spec(spec, automode())
        else:
            p.error("--spec is required")
        if a.command == "emit":
            if not a.out or a.apply or not a.spec:
                p.error("emit needs --spec and --out and never takes --apply")
            emit(spec, a.out, a.settings, a.skills_json)
        else:
            run(spec, a.settings, a.skills_json, a.apply)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
