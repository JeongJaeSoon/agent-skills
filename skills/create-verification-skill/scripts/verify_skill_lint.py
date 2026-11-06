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
CLEANUP_WORDS = ("cleanup", "clean-up", "clean_up", "teardown")
SHELL_ARG_GUARD = re.compile(r"\$\{1:\?|\$#\s*-(?:lt|eq|ne|le)\b|-z\s+\"?\$\{?1\b|\bset\s+-[a-z]*u[a-z]*\b[\s\S]*\$\{?1\b")
PY_ARG_GUARD = re.compile(r"required=True|add_argument\(\s*[\"'][^-\"']|sys\.argv\[1\]|len\(sys\.argv\)")
PY_OPTIONAL_ARG = re.compile(r"nargs\s*=\s*[\"']\?[\"']")
KILL_AT_COMMAND = re.compile(r"(?:^|[;&|(`]|\$\()\s*(?:sudo\s+)?(?:pkill|killall)\b|\|\s*xargs\s+(?:-\S+\s+)*kill\b")
KILL_PLAIN = re.compile(r"(?:^|[;&|(`]|\$\()\s*(?:sudo\s+)?kill\b")
PID_DISCOVERY = re.compile(r"\bpgrep\b|\blsof\b[^\n]*\s-\w*t")
STATE_CONTEXT = re.compile(r"state|\bRUN_?(?:DIR|ID|FILE)\b|\$RUN", re.I)
LATEST = re.compile(r"\bln\b[^\n]*[\s/]latest\b|\blatest\.(?:json|env|state|txt|pid)\b|\w=\S*\blatest\b")
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
    for ln in m.group(1).splitlines():
        k, sep, v = ln.partition(":")
        if sep and k.strip() and not ln.startswith(" "):
            v = re.sub(r"\s#.*$", "", v).strip().strip("\"'")
            out[k.strip()] = "" if v in ("null", "~", ">", "|", ">-", "|-") else v
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
    return [m.group(1).strip().strip("*").lower() for _, ln in outside_fences(text)
            if (m := re.match(r"#{2,3}\s+(.*)", ln))]


def is_script(path):
    if path.suffix in SCRIPT_SUFFIXES:
        return True
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def code_lines(path, text):
    if is_script(path):
        return [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if not ln.lstrip().startswith("#")]
    return list(fenced_lines(text))


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
        shared = LATEST.search(ln) or (LATEST_PATH.search(ln) and STATE_CONTEXT.search(ln))
        if shared and not LATEST_SKIP.search(ln):
            out.append(finding(rel, i, "shared-latest", "a shared `latest` link resolves to another agent's instance"))
    discovered = False
    for i, ln in code_lines(path, text):
        discovered = discovered or bool(PID_DISCOVERY.search(ln))
        if KILL_AT_COMMAND.search(ln) or (discovered and KILL_PLAIN.search(ln)):
            out.append(finding(rel, i, "pattern-kill", "kill what this run started, by the recorded PID or ID"))
    return out


def check_cleanup_script(path, rel, text):
    code = "\n".join(ln for _, ln in code_lines(path, text))
    if path.suffix == ".py" or code.startswith("import") or "python" in text.split("\n", 1)[0]:
        ok = PY_ARG_GUARD.search(code) and not PY_OPTIONAL_ARG.search(code)
    else:
        ok = SHELL_ARG_GUARD.search(code)
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
        if not os.access(p, os.X_OK):
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
    present = {p.name for p in feats.glob("*.md")} - {"README.md"}
    for name in sorted(linked - present):
        out.append(finding("features/README.md", 1, "feature-index", f"links {name}, which does not exist"))
    for name in sorted(present - linked):
        out.append(finding("features/README.md", 1, "feature-index", f"does not list {name}"))
    for name in sorted(present):
        body = read(feats / name) or ""
        h2 = [m.lower() for m in re.findall(r"^##\s+(.*)$", body, re.M)]
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
