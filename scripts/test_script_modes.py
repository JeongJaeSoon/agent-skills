"""Every .sh under a scripts/ directory is committed executable, and every path hooks.json loads exists.

A skill documents `bash <script>` or runs the file directly; a file committed as 100644 fails with exit 126 on the
second form. The mode is read from the git index, so the check does not depend on the checkout's filesystem.
Run: python3 scripts/test_script_modes.py"""
import json, pathlib, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def executable_bit_missing(entries):
    """Paths from `git ls-files -s` lines whose mode is not 100755."""
    bad = []
    for line in entries:
        meta, path = line.split("\t", 1)
        if meta.split()[0] != "100755":
            bad.append(path)
    return bad


def tracked_scripts(repo):
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-s", "-z", "--", "scripts/*.sh", "skills/*/scripts/*.sh"],
                         capture_output=True, text=True, check=True).stdout
    return [e for e in out.split("\0") if e]


def missing_hook_modules(hooks_json):
    """Entries of hooks.json `modules` that do not exist next to it."""
    base = hooks_json.parent
    modules = json.loads(hooks_json.read_text()).get("modules") or []
    return [m for m in modules if not (base / m).is_file()]


def selftest():
    assert executable_bit_missing(["100755 abc 0\ta.sh", "100644 abc 0\tb c.sh"]) == ["b c.sh"]
    assert executable_bit_missing([]) == []


selftest()
entries = tracked_scripts(REPO)
assert entries, "no tracked .sh found: the pathspec no longer matches the layout"
bad = executable_bit_missing(entries)
assert not bad, f"committed without the executable bit (git update-index --chmod=+x): {bad}"
gone = missing_hook_modules(REPO / "hooks" / "hooks.json")
assert not gone, f"hooks.json modules point at missing files: {gone}"
print(f"script modes: {len(entries)} scripts executable, hooks.json modules present")
sys.exit(0)
