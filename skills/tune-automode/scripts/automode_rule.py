#!/usr/bin/env python3
"""Merge autoMode rules into Claude Code user settings. The user runs --apply, never the agent.

  automode_rule.py emit --spec SPEC --out FILE   write a standalone copy with SPEC embedded, print the diff
  automode_rule.py --spec SPEC [--apply]         dry run (default) or apply
  FILE [--apply]                                 the emitted copy: same, with its embedded spec

SPEC: {"add": {"allow": [...], "soft_deny": [...], "environment": [...]},
       "replace": [{"section": "allow", "old": "...", "new": "..."}]}
"""
import argparse
import difflib
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

SPEC = None  # emit replaces this line

SECTIONS = ("allow", "soft_deny", "environment")
DEFAULTS = "$defaults"
DEFAULT_SETTINGS = pathlib.Path.home() / ".claude" / "settings.json"


class Refused(Exception):
    pass


def check_spec(spec):
    if not isinstance(spec, dict) or set(spec) - {"add", "replace"}:
        raise Refused("spec must be an object with only 'add' and 'replace'")
    add, replace = spec.get("add", {}), spec.get("replace", [])
    if not isinstance(add, dict) or not isinstance(replace, list):
        raise Refused("'add' must be an object and 'replace' a list")
    for section, rules in add.items():
        if section not in SECTIONS:
            raise Refused(f"unknown section {section!r}; allowed: {', '.join(SECTIONS)}")
        if not isinstance(rules, list):
            raise Refused(f"add.{section} must be a list")
        for rule in rules:
            check_rule(rule)
    for item in replace:
        if not isinstance(item, dict) or set(item) != {"section", "old", "new"}:
            raise Refused("each replace item needs exactly section, old, new")
        if item["section"] not in SECTIONS:
            raise Refused(f"unknown section {item['section']!r}; allowed: {', '.join(SECTIONS)}")
        check_rule(item["old"])
        check_rule(item["new"])
    if not any(add.values()) and not replace:
        raise Refused("spec changes nothing")


def check_rule(rule):
    if not isinstance(rule, str) or not rule.strip():
        raise Refused(f"a rule must be a non-empty string, got {rule!r}")
    if rule == DEFAULTS:
        raise Refused(f"{DEFAULTS} is kept as is; a spec may not add, remove or replace it")


def merge(automode, spec):
    result = {k: list(v) if isinstance(v, list) else v for k, v in automode.items()}
    notes = []
    for item in spec.get("replace", []):
        rules = section(result, item["section"])
        if item["old"] in rules:
            rules[rules.index(item["old"])] = item["new"]
        elif item["new"] in rules:
            notes.append(f"already replaced in {item['section']}: {item['new'][:60]}")
        else:
            raise Refused(f"replace: rule not found in {item['section']}: {item['old'][:80]}")
    for name, new_rules in spec.get("add", {}).items():
        if not new_rules:
            continue
        rules = section(result, name)
        for rule in new_rules:
            if rule in rules:
                notes.append(f"already in {name}: {rule[:60]}")
            else:
                rules.append(rule)
    return result, notes


def section(automode, name):
    # A list without "$defaults" replaces the built-in rules, so a new list starts with it.
    rules = automode.setdefault(name, [DEFAULTS])
    if not isinstance(rules, list):
        raise Refused(f"autoMode.{name} in settings is not a list")
    return rules


def load(path):
    if not path.is_file():
        raise Refused(f"{path} does not exist")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise Refused(f"{path} is not valid JSON ({e}); fix it by hand first")
    if not isinstance(data, dict):
        raise Refused(f"{path} is not a JSON object")
    automode = data.get("autoMode", {})
    if not isinstance(automode, dict):
        raise Refused(f"autoMode in {path} is not an object")
    return data, automode


def diff(before, after):
    def text(d):
        return json.dumps(d, ensure_ascii=False, indent=2).splitlines(True)
    return "".join(difflib.unified_diff(text(before), text(after), "autoMode (current)", "autoMode (new)"))


def backup(path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target, n = path.with_name(f"{path.name}.bak-automode-{stamp}"), 1
    while target.exists():
        target, n = path.with_name(f"{path.name}.bak-automode-{stamp}-{n}"), n + 1
    shutil.copy2(path, target)
    return target


def write(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(text)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def run(spec, settings, apply):
    check_spec(spec)
    data, before = load(settings)
    after, notes = merge(before, spec)
    for note in notes:
        print(note)
    changes = diff(before, after)
    if not changes:
        print(f"no change: {settings} already has these rules")
        return
    print(changes, end="")
    if not apply:
        print("\n(dry run; nothing written)")
        return
    real = settings.resolve()
    saved = backup(real)
    data["autoMode"] = after
    write(real, data)
    print(f"\nwritten: {settings}\nbackup:  {saved}")
    print(f"rollback: cp '{saved}' '{settings}'")
    print("check:   claude auto-mode config   (restart the session if the same denial comes back)")


def emit(spec, out, settings):
    check_spec(spec)
    source = pathlib.Path(__file__).read_text()
    marker = "SPEC = None  # emit replaces this line\n"
    if marker not in source:
        raise Refused("this copy is already an emitted script; emit from the skill's automode_rule.py")
    body = source.replace(marker, f"SPEC = {json.dumps(spec, ensure_ascii=False, indent=2)}\n", 1)
    load(settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body)
    out.chmod(0o600)
    run(spec, settings, apply=False)
    target = "" if settings == DEFAULT_SETTINGS else f" --settings {settings}"
    print(f"\nwrote {out}\nThe user reviews it and runs:\n  ! python3 {out}{target} --apply")


def main(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=["emit"])
    p.add_argument("--spec", type=pathlib.Path)
    p.add_argument("--out", type=pathlib.Path)
    p.add_argument("--settings", type=pathlib.Path, default=DEFAULT_SETTINGS)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.spec:
            try:
                spec = json.loads(a.spec.read_text())
            except (OSError, json.JSONDecodeError) as e:
                raise Refused(f"cannot read spec {a.spec}: {e}")
        elif SPEC is not None:
            spec = SPEC
        else:
            p.error("--spec is required")
        if a.command == "emit":
            if not a.out or a.apply:
                p.error("emit needs --out and never takes --apply")
            emit(spec, a.out.expanduser(), a.settings)
        else:
            run(spec, a.settings, a.apply)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
