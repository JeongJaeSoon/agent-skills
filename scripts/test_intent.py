"""intent.md's LaunchAgent exception table is the allowlist for code that writes a LaunchAgent plist.

A tracked non-test .py file that calls plistlib.dump/dumps must be a row of the table (status `exception` or
`migrating`), and an `exception` row must still write one. Run: python3 scripts/test_intent.py"""
import ast, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|[^|]*\|\s*(exception|migrating)\s*\|\s*$")


def allowed(text):
    """{path: status} from the table rows of intent.md."""
    return {m.group(1): m.group(2) for m in map(ROW.match, text.splitlines()) if m}


def writes_plist(source):
    """True when the code calls plistlib.dump/dumps, as `plistlib.dumps(...)`, `import plistlib as p; p.dumps(...)` or
    `from plistlib import dumps` (comments and strings do not count). An unparsable file is read as writing one."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    mods, funcs = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.asname or a.name for a in n.names if a.name == "plistlib"}
        elif isinstance(n, ast.ImportFrom) and n.module == "plistlib":
            funcs |= {a.asname or a.name for a in n.names if a.name in ("dump", "dumps")}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in ("dump", "dumps") and isinstance(f.value, ast.Name) \
                    and f.value.id in mods:
                return True
            if isinstance(f, ast.Name) and f.id in funcs:
                return True
    return False


def writers(files, read):
    """Non-test .py paths whose source writes a plist."""
    return sorted(f for f in files if f.endswith(".py") and not pathlib.PurePath(f).name.startswith("test_")
                  and writes_plist(read(f)))


def problems(table, found):
    out = [f"{f}: writes a LaunchAgent plist but is not a row of intent.md's exception table" for f in found if f not in table]
    out += [f"{f}: an `exception` row, but the file no longer writes a plist (delete the row)"
            for f, s in table.items() if s == "exception" and f not in found]
    return out


# the rule on fixed input: one case allowed, one refused
SAMPLE = "| `a/ok.py` | reason | exception |\n| `a/old.py` | gone soon | migrating |\n| not a row | x | exception |\n"
assert allowed(SAMPLE) == {"a/ok.py": "exception", "a/old.py": "migrating"}
src = {"a/ok.py": "import plistlib\nplistlib.dumps(x)", "b/new.py": "import plistlib\np.write_bytes(plistlib.dumps(spec))",
       "a/test_ok.py": "import plistlib\nplistlib.dumps(1)", "c/read.py": "import plistlib\nplistlib.loads(b)", "a/old.py": "pass"}
# spellings that must still count, and mentions that must not
for yes in ("import plistlib\nplistlib.dumps (spec)", "from plistlib import dumps\ndumps(spec)",
            "from plistlib import dump as d\nd(spec, f)", "import plistlib as pl\npl.dump(spec, f)", "def broken(:"):
    assert writes_plist(yes), yes
for no in ("import plistlib\n# plistlib.dumps(spec)\nplistlib.loads(b)", "x = 'plistlib.dumps(spec)'", "dumps(spec)",
           "import json\njson.dumps(spec)"):
    assert not writes_plist(no), no
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
