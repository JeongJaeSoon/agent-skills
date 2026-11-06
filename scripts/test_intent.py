"""intent.md's LaunchAgent exception table is the allowlist for code that writes a LaunchAgent plist.

A tracked non-test .py file that calls plistlib.dump/dumps must be a row of the table (status `exception` or
`migrating`), and an `exception` row must still write one. Run: python3 scripts/test_intent.py"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WRITES = re.compile(r"\bplistlib\.dumps?\(")
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|[^|]*\|\s*(exception|migrating)\s*\|\s*$")


def allowed(text):
    """{path: status} from the table rows of intent.md."""
    return {m.group(1): m.group(2) for m in map(ROW.match, text.splitlines()) if m}


def writers(files, read):
    """Non-test .py paths whose source writes a plist."""
    return sorted(f for f in files if f.endswith(".py") and not pathlib.PurePath(f).name.startswith("test_")
                  and WRITES.search(read(f)))


def problems(table, found):
    out = [f"{f}: writes a LaunchAgent plist but is not a row of intent.md's exception table" for f in found if f not in table]
    out += [f"{f}: an `exception` row, but the file no longer writes a plist (delete the row)"
            for f, s in table.items() if s == "exception" and f not in found]
    return out


# the rule on fixed input: one case allowed, one refused
SAMPLE = "| `a/ok.py` | reason | exception |\n| `a/old.py` | gone soon | migrating |\n| not a row | x | exception |\n"
assert allowed(SAMPLE) == {"a/ok.py": "exception", "a/old.py": "migrating"}
src = {"a/ok.py": "plistlib.dumps(x)", "b/new.py": "p.write_bytes(plistlib.dumps(spec))", "a/test_ok.py": "plistlib.dumps(1)",
       "c/read.py": "plistlib.loads(b)", "a/old.py": "pass"}
found = writers(src, src.__getitem__)
assert found == ["a/ok.py", "b/new.py"], found
assert problems(allowed(SAMPLE), found) == ["b/new.py: writes a LaunchAgent plist but is not a row of intent.md's exception table"]
assert problems(allowed(SAMPLE), ["a/ok.py"]) == []  # a migrating row may have no writer; the allowed one passes
assert problems(allowed(SAMPLE), []) and "no longer writes" in problems(allowed(SAMPLE), [])[0]  # a stale exception row

files = [f for f in subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True,
                                   check=True).stdout.split("\0") if f]
bad = problems(allowed((ROOT / "intent.md").read_text()),
               writers(files, lambda f: (ROOT / f).read_text(encoding="utf-8", errors="replace")))
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
print("ok")
