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
    """Claude Code's Bash rule: literal text, `*` for any text, a trailing ` *` also matching nothing."""
    body = re.escape(pattern).replace(r"\*", ".*")
    if body.endswith(r"\ .*"):
        body = body[:-len(r"\ .*")] + r"(\s.*)?"
    return re.fullmatch(body, command, re.S) is not None


def fenced_commands(body):
    """Each command in the fenced blocks, continuation lines joined, comments off."""
    out = []
    for block in re.findall(r"^```[^\n]*\n(.*?)^```", body, re.S | re.M):
        joined = re.sub(r"\\\n\s*", "", block)
        out += [line.split("  #")[0].strip() for line in joined.splitlines() if line.strip() and not line.startswith("#")]
    return out


def inline_commands(body):
    return re.findall(r"`([^`\n]+)`", body)


def body_errors(granted, body):
    """A pattern with no command behind it is stale; a documented command in its family that misses it is a drift."""
    errors, fenced = [], fenced_commands(body)
    documented = fenced + inline_commands(body)
    for p in granted:
        if not any(matches(p, c) for c in documented):
            errors.append(f"{p}: no command in the body matches it")
    families = {" ".join(p.split()[:3]) for p in granted}
    for c in fenced:
        if any(c.startswith(f) for f in families) and c not in UNGRANTED and not any(matches(p, c) for p in granted):
            errors.append(f"{c}: documented but outside every pattern")
    return errors


def skill_errors(name, text):
    fm, body = split_frontmatter(text)
    found = entries(fm.get("allowed-tools", ""))
    errors = [e for entry in found for e in pattern_errors(entry)]
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
            "Bash(orca terminal close --all)", "Bash(tool run ~/x)", "Bash(tool run /Users/x)"):
    assert pattern_errors(entries(bad)[0]), bad
for good in ('Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/x.py" scan *)', "Bash(tool sub --flag *)"):
    assert not pattern_errors(entries(good)[0]), good
assert entries("Bash(a b *) Bash(c d) Read") == ["Bash(a b *)", "Bash(c d)", "Read"]

# The matcher agrees with what auto mode did on a throwaway plugin: literal text, quotes included.
assert matches("tool ok *", "tool ok --force x") and matches("tool ok *", "tool ok")
assert not matches("tool plan --plan *", "tool plan --apply --plan x")
assert not matches('python3 "$R/x.py" ok *', "python3 $R/x.py ok")
assert matches("tool card *", "tool card brief\n --repo x")
assert matches("close --terminal $H --json", "close --terminal $H --json")
assert not matches("close --terminal $H --json", 'close --terminal "$H" --json')
assert not matches("close --terminal $H --json", "close --terminal t1 --json")

# The pinned close stays bound to this session's handle: no form that rebinds the variable matches its text.
CLOSE = GRANTS["end-session"][0]
for rebound in ("ORCA_TERMINAL_HANDLE=t1 ", "ORCA_TERMINAL_HANDLE=t1; ", "export ORCA_TERMINAL_HANDLE=t1 && "):
    assert not matches(CLOSE, rebound + CLOSE), rebound
assert not matches(CLOSE, CLOSE.replace("$ORCA_TERMINAL_HANDLE", "t1"))
assert not matches(CLOSE, CLOSE + " --all")

# A stale pattern and a drifted command fail, and an entry GRANTS does not pin fails.
assert body_errors(["orca thing stop *"], "`orca thing go now`")
assert body_errors(["orca thing go *"], "```bash\norca thing other\n```\n`orca thing go now`") == []
assert body_errors(["orca thing go now"], "```bash\norca thing go later\n```\n`orca thing go now`")
assert any("GRANTS pins" in e for e in skill_errors("demo", fixture("Bash(orca thing go *)")))
assert not fenced_commands("no block") and fenced_commands("```bash\na b \\\n  c  # note\n# only\n```\n") == ["a b c"]

# This checkout.
seen = {}
errors = []
for path in sorted((REPO / "skills").glob("*/SKILL.md")):
    name = path.parent.name
    text = path.read_text(encoding="utf-8")
    seen[name] = "allowed-tools" in split_frontmatter(text)[0]
    errors += skill_errors(name, text)
assert not errors, "\n".join(errors)
assert {n for n, has in seen.items() if has} == set(GRANTS), seen
print("test_allowed_tools: ok")
