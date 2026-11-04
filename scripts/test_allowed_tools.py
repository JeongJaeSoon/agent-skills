"""Rules for `allowed-tools` in skills/*/SKILL.md, on fixtures and on this checkout. Run: python3 scripts/test_allowed_tools.py

A grant skips the auto-mode classifier, and a pattern cannot exclude a flag: `*` takes whatever follows. So every
pattern is pinned here, and widening one means changing this table in review.
"""
import pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from skill_tokens import split_frontmatter  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

GRANTS = {
    "end-session": ["orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json"],
}
# Commands a skill documents, in a family it grants, that stay with the classifier on purpose.
UNGRANTED = {"orca terminal close --worktree current --all --json"}

INTERPRETERS = {"python", "python3", "bash", "sh", "zsh", "node", "ruby", "perl", "env", "uv", "uvx", "npx", "npm"}
# Bundled paths only: a script under the skill or the plugin. A path of one machine is not portable.
BUNDLED = re.compile(r'^"?\$\{CLAUDE_(SKILL_DIR|PLUGIN_ROOT)\}/')
FORBIDDEN = re.compile(
    r"(^|\s)(rm|rmdir|kill|pkill|curl|wget|gh|git|land|merge|push|delete|send|prune)(\s|$)|--force|--all\b|--admin"
    r"|~/|/Users/|/home/")


def grant_value(text):
    """(value, errors) of the `allowed-tools` key. YAML can spell a grant in forms a line parser misses (a quoted or
    escaped key, a continuation, a list), and a missed grant still applies, so every frontmatter line must be a plain
    `key: value` on one line."""
    head = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not head:
        return "", ["frontmatter must open and close with a bare --- line"]
    lines = head.group(1).split("\n")
    odd =[line for line in lines if not re.fullmatch(r"[a-z][a-z-]*: \S.*", line)]
    if odd:
        return "", [f"frontmatter line is not a plain `key: value`: {odd[0]!r}"]
    grants = [line[len("allowed-tools: "):] for line in lines if line.startswith("allowed-tools: ")]
    if len(grants) > 1 or (grants and not grants[0].startswith("Bash(")):
        return "", ["allowed-tools must be one line of Bash(...) entries"]
    return (grants[0] if grants else ""), []


def entries(value):
    """The tool entries of an `allowed-tools` value, keeping the spaces inside `Bash(...)`."""
    return re.findall(r"[\w-]+(?:\([^)]*\))?", value)


def pattern_errors(entry):
    """Why one `allowed-tools` entry is refused, as a list."""
    m = re.fullmatch(r"Bash\((.+)\)", entry)
    if not m:
        return [f"{entry}: only narrow Bash(...) entries are granted"]
    cmd = m.group(1)
    words = cmd.split()
    errors = []
    if len([w for w in words if "*" not in w]) < 2:
        errors.append(f"{entry}: names no subcommand")
    if words[0].rstrip(":*") in INTERPRETERS and not (len(words) > 1 and BUNDLED.match(words[1])):
        errors.append(f"{entry}: an interpreter without a bundled script runs anything")
    if FORBIDDEN.search(cmd):
        errors.append(f"{entry}: deletion, an outward write, a merge or a machine path")
    return errors


def matches(pattern, command):
    """Claude Code's Bash rule: literal text and `*` for any text. A ` *` that is the only wildcard and ends the rule
    also matches the bare command."""
    if pattern.endswith(" *") and pattern.count("*") == 1:
        body = re.escape(pattern[:-2]) + "( .*)?"
    else:
        body = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(body, command, re.S) is not None


def family(pattern):
    """The first three literal words of a pattern: what names the command it grants."""
    return " ".join(pattern.split("*")[0].split()[:3])


def occurrences(name, body):
    """[(command, is a call)] for each place the body writes the command `name`, however its words are spaced, up to
    a backtick or the end of its indented lines, spacing kept: the rule matches literally, so a respaced command must
    not read as the same one. A call starts a line or a code span, so `echo <name>` is not one. A code span holding
    only the name is prose naming the command, and is skipped."""
    words = r"\s+".join(re.escape(w) for w in name.split())
    out = []
    for m in re.finditer(rf"(?<![\w-]){words}(?![\w-])[^`\n]*(?:\n[ \t]+[^`\s][^`\n]*)*", body):
        if m.group(0) == name and body[m.end():m.end() + 1] == "`":
            continue
        before = body[:m.start()].rsplit("\n", 1)[-1]
        # A span opened before the command must close right after it, and one that did not open must not follow it.
        whole = (before.count("`") % 2 == 1) == (body[m.end():m.end() + 1] == "`")
        out.append((m.group(0).rstrip() + ("" if whole else " …"), re.search(r"(^|`)\s*$", before) is not None))
    return out


def body_errors(granted, body):
    """A pattern with no command behind it is stale; another command of its family that misses it is a drift."""
    errors = []
    for p in granted:
        found = occurrences(family(p), body)
        if not any(call and matches(p, c) for c, call in found):
            errors.append(f"{p}: no command in the body matches it")
        for c, _ in found:
            if c not in UNGRANTED and not any(matches(q, c) for q in granted):
                errors.append(f"{c}: documented but outside every pattern")
    return errors


def skill_errors(name, text):
    value, errors = grant_value(text)
    body = split_frontmatter(text)[1]
    found = entries(value)
    if " ".join(found) != value:
        errors.append(f"{name}: allowed-tools holds text outside its entries: {value}")
    errors += [e for entry in found for e in pattern_errors(entry)]
    want = [f"Bash({p})" for p in GRANTS.get(name, [])]
    if found != want:
        errors.append(f"{name}: allowed-tools is {found}, GRANTS pins {want}")
    return errors + body_errors([e[5:-1] for e in found if e.startswith("Bash(")], body)


def fixture(value, body="`orca thing go now`"):
    return f"---\nname: demo\nallowed-tools: {value}\n---\n\n{body}\n"


# The refused shapes.
for bad in ("Bash", "Bash(*)", "Read", "Bash(orch *)", "Bash(python3:*)", "Bash(python3 *)", "Bash(bash x.sh)",
            "Bash(python3 /tmp/x.py run)", "Bash(orca worktree rm --worktree *)", "Bash(orca terminal send *)",
            "Bash(orch land *)", "Bash(gh pr merge *)", "Bash(git push *)", "Bash(orca worktree rm --force x)",
            "Bash(orca terminal close --all)", "Bash(tool run ~/x)", "Bash(tool run /home/me)"):
    assert pattern_errors(entries(bad)[0]), bad
for good in ('Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/x.py" scan *)', "Bash(tool sub --flag *)"):
    assert not pattern_errors(entries(good)[0]), good
assert entries("Bash(a b *) Bash(c d) Read") == ["Bash(a b *)", "Bash(c d)", "Read"]

# The matcher agrees with what auto mode did on a throwaway plugin: literal text, quotes included.
assert matches("tool ok *", "tool ok --force x") and matches("tool ok *", "tool ok")
assert not matches("tool plan --plan *", "tool plan --apply --plan x")
assert not matches('python3 "$R/x.py" ok *', "python3 $R/x.py ok")
assert matches("tool card *", "tool card brief\n --repo x")
# Only a lone trailing ` *` matches the bare command, and a space in a rule is a space.
assert not matches("* --help *", "tool --help") and matches("* --help *", "tool --help x")
assert not matches("tool ok x", "tool\tok x") and not matches("tool ok *", "tool ok\tx")
assert matches("close --terminal $H --json", "close --terminal $H --json")
assert not matches("close --terminal $H --json", 'close --terminal "$H" --json')
assert not matches("close --terminal $H --json", "close --terminal t1 --json")

# The pinned close stays bound to this session's handle: no form that rebinds the variable matches its text.
CLOSE = GRANTS["end-session"][0]
for rebound in ("ORCA_TERMINAL_HANDLE=t1 ", "ORCA_TERMINAL_HANDLE=t1; ", "export ORCA_TERMINAL_HANDLE=t1 && "):
    assert not matches(CLOSE, rebound + CLOSE), rebound
assert not matches(CLOSE, CLOSE.replace("$ORCA_TERMINAL_HANDLE", "t1"))
assert not matches(CLOSE, CLOSE + " --all")
assert any("orca terminal close --terminal t1" in e for e in skill_errors(
    "end-session", fixture(f"Bash({CLOSE})", f"`{CLOSE}` and `orca terminal close --terminal t1 --json`")))

# A stale pattern and a drifted command fail, and an entry GRANTS does not pin fails.
assert body_errors(["orca thing stop *"], "`orca thing go now`")
assert body_errors(["orca thing go *"], "```bash\norca thing other\n```\n`orca thing go now`") == []
assert body_errors(["orca thing go now"], "```bash\norca thing go later\n```\n`orca thing go now`")
assert any("GRANTS pins" in e for e in skill_errors("demo", fixture("Bash(orca thing go *)")))
assert family("tool sub --flag x *") == "tool sub --flag" and family("tool run *") == "tool run"

# A drifted inline command fails beside a matching one; a family is whole words; a bare family name is prose.
assert body_errors(["orca thing go now"], "`orca thing go now` and `orca thing go now --all`")
assert not body_errors(["orca thing go now"], "`orca thing go now` and `orca thing gone x`")
assert not body_errors(["orca thing go now"], "`orca thing go now` beside `orca thing go`")
for drift in ("`orca thing go\tlater`", "`orca thing go\n  later`", "`orca  thing go now`", "`orca\tthing go now`",
              "~~~bash\norca thing go later\n~~~", "  ```bash\n  orca thing go later\n  ```", "    orca thing go later",
              "``orca thing go ` later``"):
    assert body_errors(["orca thing go now"], f"`orca thing go now`\n{drift}\n"), drift
# A span's next line is part of its command, and a command named as another's argument is no call.
assert body_errors(["orca thing go now"], "`orca thing go now\n  --all`")
assert body_errors(["orca thing go now"], "`orca thing go now\n--all`")
assert body_errors(["orca thing go now"], "```bash\norca thing go now `printf %s --all`\n```\n")
assert not body_errors(["orca thing go now"], "text `orca thing go now` text `other` text\n")
assert body_errors(["orca thing go now"], "```bash\necho orca thing go now\n```\n")
assert not body_errors(["orca thing go now"], "```bash\n  orca thing go now\n```\n")
assert any("outside its entries" in e for e in skill_errors("demo", fixture("Bash(orca thing go now) *")))
assert any("outside its entries" in e for e in skill_errors("demo", fixture("Bash(orca thing go now) (*)")))

# Every other YAML spelling of the key is refused, so a grant cannot sit where this file's parser misses it.
GOOD = "Bash(orca terminal close --terminal $ORCA_TERMINAL_HANDLE --json)"
assert grant_value(f"---\nname: a\nallowed-tools: {GOOD}\ndescription: b\n---\n") == (GOOD, [])
assert grant_value("---\nname: a\ndescription: mentions allowed-tools only\n---\n") == ("", [])
# Frontmatter this file cannot read fails: the plugin loader takes a BOM, a spaced fence and CRLF, so a grant there applies.
SEND = "allowed-tools: Bash(orca terminal send *)"
for unread in (f"﻿---\n{SEND}\n---\n", f"--- \n{SEND}\n---\n", f"---\r\n{SEND}\r\n---\r\n", f"---\n{SEND}\n--- \n", "no frontmatter\n"):
    assert skill_errors("demo", unread), repr(unread)
for spelling in (f"allowed-tools: {GOOD}\n  Bash(orca terminal send *)", '"allowed-tools": Bash(orca terminal send *)',
                 "'allowed-tools': Bash(orca terminal send *)", "allowed-tools:\n  - Bash(orca terminal send *)",
                 f"allowed-tools:\n- {GOOD}", f"allowed-tools: [{GOOD}]", f'allowed-tools: "{GOOD}"',
                 "allowed-tools: Read", f"allowed-tools: {GOOD}\nallowed-tools: {GOOD}",
                 "allowed-tools: >\n  Bash(orca terminal send *)", f"allowed-tools: {GOOD}\n\n  Bash(orca terminal send *)",
                 '"allowed-\\u0074ools": Bash(orca terminal send *)', "? allowed-tools\n: Bash(orca terminal send *)"):
    assert grant_value(f"---\nname: a\n{spelling}\n---\n")[1], spelling
    assert skill_errors("demo", f"---\nname: a\n{spelling}\n---\n"), spelling

# This checkout.
seen = {}
errors = []
for path in sorted((REPO / "skills").glob("*/SKILL.md")):
    name = path.parent.name
    text = path.read_text(encoding="utf-8")
    seen[name] = bool(grant_value(text)[0])
    errors += skill_errors(name, text)
assert not errors, "\n".join(errors)
assert {n for n, has in seen.items() if has} == set(GRANTS), seen
print("test_allowed_tools: ok")
