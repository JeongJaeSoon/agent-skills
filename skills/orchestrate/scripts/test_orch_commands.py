"""Every `orch <command>` the skills tell a session to run exists in prog.py. Run: python3 test_orch_commands.py"""
import pathlib, re, subprocess, sys
HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import prog

ROOT = HERE.parents[2]
docs = [*ROOT.glob("skills/*/SKILL.md"), *ROOT.glob("skills/*/references/*.md"), *ROOT.glob("agents/*.md")]
named = {}
for p in docs:
    for cmd in re.findall(r"`orch ([a-z][a-z-]*)", p.read_text()):
        named.setdefault(cmd, p.relative_to(ROOT))
assert {"watch-cards", "land", "wait", "decide"} <= set(named), sorted(named)
missing = {c: str(p) for c, p in named.items() if c not in prog.COMMANDS}
assert not missing, f"skills name orch commands prog.py does not have: {missing}"

# The dispatcher used to answer `--help` after any command with the global usage, which read as "no such command".
r = subprocess.run([sys.executable, str(HERE / "prog.py"), "watch-cards", "--help"], capture_output=True, text=True)
assert r.returncode == 0 and "--idle-min" in r.stdout, (r.returncode, r.stdout, r.stderr)
r = subprocess.run([sys.executable, str(HERE / "prog.py"), "land", "--help"], capture_output=True, text=True)
assert r.returncode == 1 and "Usage: orch <command>" in r.stderr, (r.returncode, r.stderr)

print(f"ok: {len(named)} orch commands named in {len(docs)} docs all exist")
