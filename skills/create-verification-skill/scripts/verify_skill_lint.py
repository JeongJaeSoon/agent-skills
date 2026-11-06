#!/usr/bin/env python3
"""Static lint for a project-local verify-* skill. Reads files only; it never launches or drives the app.

Usage: verify_skill_lint.py SKILL_DIR

Prints one `path:line: rule: message` per finding. Exit 0 clean, 1 findings, 2 SKILL_DIR is not a skill directory.
A line-pattern lint: it catches the common shapes, not every way to write them.

Checks (rule ids):
  frontmatter      SKILL.md opens with name (verify-<app>, equal to the directory name) and a non-empty description
  sections         Launch, Doctor, Drive, Evidence and Cleanup headings exist outside code fences
  state-secret     a state-file path whose name matches a secret-file pattern (a hook may refuse to read it)
  shared-latest    a shared `latest` link, file or directory, which resolves to another agent's instance
  cleanup-arg      a cleanup/teardown script that runs without being told which instance to remove
  pattern-kill     pkill, killall, or kill of PIDs found by pgrep/lsof, in a fenced block or a script
  helper-mode      a script that is not executable, or that SKILL.md never shows in a code span or fence
  feature-index    features/README.md missing, a feature file it does not list, a dead link,
                   or a feature file without its four H2 sections
"""
import fnmatch
import os
import pathlib
import re
import sys

REQUIRED_SECTIONS = ("launch", "doctor", "drive", "evidence", "cleanup")
FEATURE_H2 = ("sub-features", "how to get to it", "driving it with", "gotchas")
SECRET_NAMES = (".env*", "*.env", "*secret*", "*credential*", "*token*", "*password*", "*.pem", "*.key", "id_rsa*")
SCRIPT_SUFFIXES = (".sh", ".py")
INTERPRETED_SUFFIXES = (".js", ".mjs", ".cjs", ".ts")  # run as `node x.mjs`, so no executable bit is required
CLEANUP_WORDS = ("cleanup", "clean-up", "clean_up", "teardown")
SHELL_ARG_GUARD = re.compile(r"\$\{1:?\?|\$#\"?\s*-(?:lt|eq|ne|le)\b|-[zn]\s+\"?\$\{?1\b|\bset\s+-[a-z]*u[a-z]*\b[\s\S]*\$(?:1\b|\{1\})")
SHELL_ARG_COPY = re.compile(r"\b(\w+)=\"?\$\{?1\b")
PY_ADD_ARGUMENT = re.compile(r"add_argument\(([^)]*)\)")
ARGV_USE = {"py": re.compile(r"sys\.argv\[1\]"), "js": re.compile(r"process\.argv\[2\]")}
ARGV_DEFAULT = re.compile(r"\bif\b|\blen\(|\.length|\?|\|\|")  # a line that falls back instead of refusing
AT_COMMAND = r"(?:^|[;&|(`{]|\$\(|\b(?:then|do|else)\b)\s*(?:sudo\s+)?"
KILL_AT_COMMAND = re.compile(AT_COMMAND + r"(?:pkill|killall)\b|(?:pgrep|lsof)\b[^\n]*\|\s*xargs\s+(?:-\S+\s+)*kill\b|\bkill\b[^\n]*\$\(\s*(?:pgrep|lsof)|[\[(]\s*[\"'](?:pkill|killall)[\"']")
KILL_PLAIN = re.compile(AT_COMMAND + r"kill\b([^\n]*)")
PID_DISCOVERY = re.compile(r"\bpgrep\b|\blsof\b[^\n]*\s-\w*t")
PID_VARIABLE = re.compile(r"\b(\w+)=\"?\$\([^\n]*(?:\bpgrep\b|\blsof\b)")
STATE_CONTEXT = re.compile(r"state|\bRUN_?(?:DIR|ID|FILE)\b|\$RUN", re.I)
LATEST = re.compile(r"\bln\b[^\n]*[\s/]latest\b|\blatest\.(?:json|env|state|txt|pid)\b|\w=\S*/latest\b")
LATEST_PATH = re.compile(r"/latest\b")
LATEST_SKIP = re.compile(r"https?://|\b(?:never|not|no|avoid|don't|without)\b", re.I)


def finding(path, line, rule, msg):
    return f"{path}:{line}: {rule}: {msg}"


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    out = {}
    lines = m.group(1).splitlines()
    for n, ln in enumerate(lines):
        k, sep, v = ln.partition(":")
        if sep and k.strip() and not ln.startswith(" "):
            v = re.sub(r"\s#.*$", "", v).strip().strip("\"'")
            if v in (">", "|", ">-", "|-", ">+", "|+"):
                block = []
                for c in lines[n + 1:]:
                    if c.strip() and not c.startswith(" "):
                        break
                    block.append(c.strip())
                v = " ".join(b for b in block if b)
            out[k.strip()] = "" if v in ("null", "~") else v
    return out


def outside_fences(text):
    """(line number, line) for lines outside ``` fences."""
    inside = False
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.lstrip().startswith("```"):
            inside = not inside
        elif not inside:
            yield i, ln


def fenced_lines(text):
    inside = False
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.lstrip().startswith("```"):
            inside = not inside
        elif inside:
            yield i, ln


def headings(text):
    found = [m.group(1).strip().strip("*").lower() for _, ln in outside_fences(text) if (m := re.match(r"#{2,3}\s+(.*)", ln))]
    return [re.sub(r"^\d+[.)]\s*", "", h) for h in found]


def is_script(path):
    if path.suffix in SCRIPT_SUFFIXES + INTERPRETED_SUFFIXES:
        return True
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def join_continuations(lines):
    """Merge a line that ends in `|` or a backslash with the next one, so a split pipeline reads as one command."""
    out = []
    for i, ln in lines:
        if out and out[-1][1].rstrip().endswith(("|", "\\")):
            out[-1] = (out[-1][0], out[-1][1].rstrip().rstrip("\\") + " " + ln.strip())
        else:
            out.append((i, ln))
    return out


def code_lines(path, text):
    if is_script(path):
        lines = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if not ln.lstrip().startswith(("#", "//"))]
    else:
        lines = list(fenced_lines(text))
    return join_continuations(lines)


def file_like_tokens(line):
    for tok in re.split(r"[\s\"'`=:,()<>\[\]]+", line):
        tok = tok.rstrip(".;")
        if "/" in tok or "." in tok:
            yield tok


def check_skill_md(root, skill_md, text):
    out = []
    rel = skill_md.name
    fm = frontmatter(text)
    if fm is None:
        out.append(finding(rel, 1, "frontmatter", "no frontmatter block; without it the skill never registers"))
    else:
        name = fm.get("name", "")
        if not re.fullmatch(r"verify-[a-z0-9][a-z0-9-]*", name):
            out.append(finding(rel, 1, "frontmatter", f"name {name!r} is not verify-<app>"))
        elif name != root.name:
            out.append(finding(rel, 1, "frontmatter", f"name {name!r} differs from the directory {root.name!r}"))
        if not fm.get("description"):
            out.append(finding(rel, 1, "frontmatter", "description is empty"))
    have = headings(text)
    for want in REQUIRED_SECTIONS:
        if not any(h.startswith(want) for h in have):
            out.append(finding(rel, 1, "sections", f"no {want.capitalize()} heading"))
    return out


def check_text_rules(path, rel, text):
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        if STATE_CONTEXT.search(ln):
            for tok in file_like_tokens(ln):
                base = tok.rsplit("/", 1)[-1].lower()
                if base and any(fnmatch.fnmatch(base, p) for p in SECRET_NAMES):
                    out.append(finding(rel, i, "state-secret", f"{tok} matches a secret-file name; name the state file after its run id"))
                    break
        run = re.sub(r"\s#.*$", "", ln)  # a trailing comment is not what runs
        shared = LATEST.search(run) or (LATEST_PATH.search(run) and STATE_CONTEXT.search(run))
        if shared and not LATEST_SKIP.search(run):
            out.append(finding(rel, i, "shared-latest", "a shared `latest` link resolves to another agent's instance"))
    found_pids = set()
    for i, ln in code_lines(path, text):
        found_pids |= set(PID_VARIABLE.findall(ln))
        kill = KILL_PLAIN.search(ln)
        from_found = kill and any(re.search(r"\$\{?" + v + r"\b", kill.group(1)) for v in found_pids)
        if KILL_AT_COMMAND.search(ln) or from_found:
            out.append(finding(rel, i, "pattern-kill", "kill what this run started, by the recorded PID or ID"))
    return out


def uses_argv_without_default(code, kind):
    return any(ARGV_USE[kind].search(ln) and not ARGV_DEFAULT.search(ln) for ln in code.splitlines())


def shell_requires_argument(code):
    if SHELL_ARG_GUARD.search(code):
        return True
    # state="${1:-}" is no refusal until a check on that variable ends in an exit
    return any(re.search(r"-[zn]\s+\"?\$\{?" + v + r"\b[\s\S]{0,80}?\bexit\b", code) for v in SHELL_ARG_COPY.findall(code))


def python_requires_argument(code):
    if uses_argv_without_default(code, "py"):
        return True
    for args in PY_ADD_ARGUMENT.findall(code):
        first = args.split(",")[0].strip().strip("\"'")
        optional = re.search(r"nargs\s*=\s*[\"'][?*]", args)
        if (re.search(r"required\s*=\s*True", args) or not first.startswith("-")) and not optional:
            return True
    return False


def check_cleanup_script(path, rel, text):
    code = "\n".join(ln for _, ln in code_lines(path, text))
    if path.suffix == ".py" or code.startswith("import") or "python" in text.split("\n", 1)[0]:
        ok = python_requires_argument(code)
    elif path.suffix in INTERPRETED_SUFFIXES:
        ok = uses_argv_without_default(code, "js") or bool(re.search(r"if\s*\(\s*!\s*process\.argv\[2\]", code))
    else:
        ok = shell_requires_argument(code)
    if ok:
        return []
    return [finding(rel, 1, "cleanup-arg", "cleanup must require the instance to remove (run id or state file) and refuse without it")]


def shown(name, skill_text):
    """SKILL.md shows the helper in a code span or a fenced block, not just in a sentence."""
    spans = re.findall(r"`([^`\n]+)`", "\n".join(ln for _, ln in outside_fences(skill_text)))
    return any(name in s for s in spans) or any(name in ln for _, ln in fenced_lines(skill_text))


def check_helpers(root, skill_text):
    out = []
    for p in sorted(root.rglob("*")):
        rel_parts = p.relative_to(root).parts
        if not p.is_file() or rel_parts[0] in ("features", "references", "evidence") or p.name == "SKILL.md":
            continue
        if not is_script(p):
            continue
        rel = str(p.relative_to(root))
        if p.suffix not in INTERPRETED_SUFFIXES and not os.access(p, os.X_OK):
            out.append(finding(rel, 1, "helper-mode", "not executable"))
        if not shown(p.name, skill_text):
            out.append(finding(rel, 1, "helper-mode", "SKILL.md never shows how to invoke it in a code span or block"))
        text = read(p)
        if text is None:
            continue
        out += check_text_rules(p, rel, text)
        if any(w in p.stem.lower() for w in CLEANUP_WORDS):
            out += check_cleanup_script(p, rel, text)
    return out


def check_features(root):
    feats = root / "features"
    index = feats / "README.md"
    if not index.is_file():
        return [finding("features/README.md", 1, "feature-index", "missing")]
    out = []
    text = read(index) or ""
    linked = set(re.findall(r"\]\((?:\./)?([^)#\s]+\.md)", text))
    linked |= set(re.findall(r"^\s*\[[^\]]+\]:\s*(?:\./)?(\S+\.md)", text, re.M))
    linked = {n for n in linked if "/" not in n}  # a link out of features/ (../SKILL.md) is navigation, not an entry
    present = {p.name for p in feats.glob("*.md")} - {"README.md"}
    for name in sorted(linked - present):
        out.append(finding("features/README.md", 1, "feature-index", f"links {name}, which does not exist"))
    for name in sorted(present - linked):
        out.append(finding("features/README.md", 1, "feature-index", f"does not list {name}"))
    for name in sorted(present):
        body = read(feats / name) or ""
        h2 = [m.group(1).lower() for _, ln in outside_fences(body) if (m := re.match(r"##\s+(.*)$", ln))]
        for want in FEATURE_H2:
            if not any(h.startswith(want) for h in h2):
                out.append(finding(f"features/{name}", 1, "feature-index", f"no '{want}' H2 section"))
    return out


def lint(root):
    root = pathlib.Path(root)
    skill_md = root / "SKILL.md"
    text = read(skill_md)
    if text is None:
        return None
    out = check_skill_md(root, skill_md, text)
    out += check_text_rules(skill_md, "SKILL.md", text)
    out += check_helpers(root, text)
    out += check_features(root)
    return out


def main(argv):
    if len(argv) != 2:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    found = lint(argv[1])
    if found is None:
        print(f"{argv[1]}: no readable SKILL.md", file=sys.stderr)
        return 2
    print("\n".join(found) if found else "clean")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
