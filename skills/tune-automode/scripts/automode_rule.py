#!/usr/bin/env python3
"""Merge autoMode rules into Claude Code user settings. The user runs --apply, never the agent.

  automode_rule.py emit --spec SPEC --out FILE   write a standalone copy with SPEC embedded, print the diff
  automode_rule.py --spec SPEC [--apply HASH]    dry run (default) or apply
  FILE [--apply HASH]                            the emitted copy: same, with its embedded spec

HASH is the spec hash printed with the diff; a spec edited after review no longer matches it.

SPEC: {"add": {"allow": [...], "soft_deny": [...], "environment": [...]},
       "replace": [{"section": "allow", "old": "...", "new": "..."}],
       "remove": [{"section": "allow", "rule": "..."}],
       "refusal_category": "..."}

emit also lints the spec. It refuses a spec without refusal_category (the classifier's category quoted as given, or
`user-request`; `no-verdict` is refused) and an allow rule without `Not covered:`. It warns, above the diff, on
blanket wording (all, any, every, *), ticket or PR numbers, token-like strings, and a rule that nearly repeats one
already in the settings. Applying an emitted copy does not lint again.
"""
import argparse
import difflib
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import sys
import tempfile
import time

SPEC = None  # emit replaces this line

SECTIONS = ("allow", "soft_deny", "environment")
DEFAULTS = "$defaults"
CONFIG_DIR = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude").expanduser()
DEFAULT_SETTINGS = CONFIG_DIR / "settings.json"


class Refused(Exception):
    pass


def check_spec(spec):
    if not isinstance(spec, dict) or set(spec) - {"add", "replace", "remove", "refusal_category"}:
        raise Refused("spec must be an object with only 'add', 'replace', 'remove' and 'refusal_category'")
    if "refusal_category" in spec and (not isinstance(spec["refusal_category"], str) or not spec["refusal_category"].strip()):
        raise Refused("refusal_category must be a non-empty string")
    add, replace, remove = spec.get("add", {}), spec.get("replace", []), spec.get("remove", [])
    if not isinstance(add, dict) or not isinstance(replace, list) or not isinstance(remove, list):
        raise Refused("'add' must be an object, 'replace' and 'remove' lists")
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
        if item["old"] == item["new"]:
            raise Refused("a replace item has the same old and new text")
    for item in remove:
        if not isinstance(item, dict) or set(item) != {"section", "rule"}:
            raise Refused("each remove item needs exactly section, rule")
        if item["section"] not in SECTIONS:
            raise Refused(f"unknown section {item['section']!r}; allowed: {', '.join(SECTIONS)}")
        check_rule(item["rule"])
    if not any(add.values()) and not replace and not remove:
        raise Refused("spec changes nothing")


def check_rule(rule):
    if not isinstance(rule, str) or not rule.strip():
        raise Refused(f"a rule must be a non-empty string, got {rule!r}")
    try:
        rule.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused(f"a rule is not valid UTF-8 text: {rule!r}")
    if rule == DEFAULTS:
        raise Refused(f"{DEFAULTS} is kept as is; a spec may not add, remove or replace it")


NOT_COVERED = "Not covered:"
NO_VERDICT = ("no-verdict", "no verdict", "none")
BLANKET = re.compile(r"\b(?:all|any|every|anything|everything|anywhere)\b|모든|어떤|\*", re.I)
TICKET = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|(?:^|[\s(])#\d+\b|\b[\w.-]+/[\w.-]+#\d+\b")
TOKEN = re.compile(r"\b(?:gh[pousr]_\w{20,}|github_pat_\w{20,}|sk-[\w-]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
                   r"|eyJ[\w-]{10,}\.[\w-]{10,}|[0-9a-fA-F]{32,}|(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{32,})\b")
SIMILAR = 0.5


def new_rules(spec):
    """(section, rule) for each rule the spec writes: added ones and the new text of a replacement."""
    for section, rules in spec.get("add", {}).items():
        for rule in rules:
            yield section, rule
    for item in spec.get("replace", []):
        yield item["section"], item["new"]


def words(rule):
    """Words of the part that grants, in any script; `Not covered:` is shared boilerplate and would make unrelated rules alike."""
    return set(re.findall(r"[^\W\d_][\w-]+", rule.split(NOT_COVERED)[0].lower()))


def lint(spec, automode):
    """(refusals, warnings) for an emit. A refusal stops it; a warning is printed above the diff."""
    refuse, warn = [], []
    category = spec.get("refusal_category", "")
    if not category.strip() or category.strip().lower() in NO_VERDICT:
        refuse.append("refusal_category is required: the classifier's category quoted as given, or `user-request` when "
                      "the user asked for the rule without a denial; a denial with no verdict is no input for a rule")
    gone = {i["old"] for i in spec.get("replace", [])} | {i["rule"] for i in spec.get("remove", [])}
    for section, rule in new_rules(spec):
        head = rule.split(NOT_COVERED)[0]
        label = f"{section}: {rule[:60]}"
        if section == "allow" and NOT_COVERED not in rule:
            refuse.append(f"allow rule has no `{NOT_COVERED}` part for the nearby actions that stay blocked: {label}")
        if BLANKET.search(head):
            warn.append(f"blanket wording ({BLANKET.search(head).group(0)!r}) before `{NOT_COVERED}`; name the narrower class: {label}")
        if TICKET.search(rule):
            warn.append(f"ticket or PR number in the rule; state the lasting condition instead: {label}")
        if TOKEN.search(rule):
            warn.append(f"token-like string in the rule; a secret never belongs in a rule: {label}")
        for other in automode.get(section, []):
            if isinstance(other, str) and other not in (DEFAULTS, rule) and other not in gone:
                a, b = words(rule), words(other)
                if a and b and len(a & b) / len(a | b) >= SIMILAR:
                    warn.append(f"near-duplicate of an existing {section} rule; `replace` it instead of adding: {other[:70]}")
    return refuse, warn


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]


def merge(automode, spec):
    result = {k: list(v) if isinstance(v, list) else v for k, v in automode.items()}
    notes = []
    for item in spec.get("replace", []):
        rules = section(result, item["section"])
        if item["old"] in rules and item["new"] in rules:
            rules.remove(item["old"])
        elif item["old"] in rules:
            rules[rules.index(item["old"])] = item["new"]
        elif item["new"] in rules:
            notes.append(f"already replaced in {item['section']}: {item['new'][:60]}")
        else:
            raise Refused(f"replace: rule not found in {item['section']}: {item['old'][:80]}")
    for item in spec.get("remove", []):
        rules = result.get(item["section"])
        if not isinstance(rules, list) or item["rule"] not in rules:
            # A typo and an earlier apply look the same here, so neither writes anything.
            raise Refused(f"remove: rule not found in {item['section']} (already removed?): {item['rule'][:80]}")
        rules.remove(item["rule"])
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
        return (json.dumps(d, ensure_ascii=False, indent=2) + "\n").splitlines(True)
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
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return json.loads(path.read_text()) == data


def run(spec, settings, apply):
    check_spec(spec)
    digest = spec_hash(spec)
    if apply and apply != digest:
        raise Refused(f"--apply {apply} does not match this spec ({digest}); review the diff again")
    data, before = load(settings)
    after, notes = merge(before, spec)
    print(f"settings: {settings.resolve()}")
    for note in notes:
        print(note)
    changes = diff(before, after)
    if not changes:
        print(f"no change: {settings} already has these rules")
        return
    print(changes, end="")
    if not apply:
        print(f"\n(dry run; nothing written; spec hash {digest})")
        return
    real = settings.resolve()
    saved = backup(real)
    data["autoMode"] = after
    if not write(real, data):
        raise Refused(f"{real} did not read back as written; restore it: cp {shlex.quote(str(saved))} {shlex.quote(str(real))}")
    print(f"\nwritten: {real}\nbackup:  {saved}")
    print(f"rollback: cp {shlex.quote(str(saved))} {shlex.quote(str(real))}")
    print("check:   claude auto-mode config   (restart the session if the same denial comes back)")


def emit(spec, out, settings):
    check_spec(spec)
    source = pathlib.Path(__file__).read_text()
    marker = "SPEC = None  # emit replaces this line\n"
    if marker not in source:
        raise Refused("this copy is already an emitted script; emit from the skill's automode_rule.py")
    out = out.expanduser().absolute()
    if out.resolve() == settings.resolve() or out.resolve().is_relative_to(CONFIG_DIR.resolve()):
        raise Refused(f"--out must be outside {CONFIG_DIR}; the agent does not write there")
    body = source.replace(marker, f"SPEC = {json.dumps(spec, ensure_ascii=False, indent=2)}\n", 1)
    refuse, warn = lint(spec, load(settings)[1])
    if refuse:
        raise Refused("lint: " + "; ".join(refuse))
    for w in warn:
        print(f"warning: {w}")
    run(spec, settings, apply=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(body)
    out.chmod(0o600)
    target = "" if settings == DEFAULT_SETTINGS else f" --settings {shlex.quote(str(settings.absolute()))}"
    print(f"\nwrote {out}\nThe user reviews it and runs:\n  ! python3 -I {shlex.quote(str(out))}{target} --apply {spec_hash(spec)}")


def main(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=["emit"])
    p.add_argument("--spec", type=pathlib.Path)
    p.add_argument("--out", type=pathlib.Path)
    p.add_argument("--settings", type=pathlib.Path, default=DEFAULT_SETTINGS)
    p.add_argument("--apply", metavar="HASH")
    a = p.parse_args(argv)
    try:
        if a.spec:
            try:
                spec = json.loads(a.spec.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                raise Refused(f"cannot read spec {a.spec}: {e}")
        elif SPEC is not None:
            spec = SPEC
        else:
            p.error("--spec is required")
        if a.command == "emit":
            if not a.out or a.apply:
                p.error("emit needs --out and never takes --apply")
            emit(spec, a.out, a.settings)
        else:
            run(spec, a.settings, a.apply)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
