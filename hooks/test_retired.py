"""Every hook script hooks.json ever ran still exists, and a retired one passes.

A running session keeps the hooks.json it loaded until /reload-plugins or a restart, while the scripts are read
from disk on every call. A script removed from under it makes `python3 <missing file>` exit 2, which Claude Code
reads as a block: every Bash, Read or Edit of that session fails. So a hook that leaves hooks.json keeps a no-op
file at its old path. Run: python3 hooks/test_retired.py"""
import json, pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPT = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/(hooks/[\w./-]+\.py)")


def commands(text):
    """Each hook command in a hooks.json text; a version that is not valid JSON falls back to its raw text."""
    try:
        data = json.loads(text)
    except ValueError:
        return [text]
    return [h.get("command", "") for groups in (data.get("hooks") or {}).values() for g in groups
            for h in g.get("hooks") or []]


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


now = commands((HERE / "hooks.json").read_text())
for c in now:
    assert c.startswith('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/'), \
        f"a hook command runs a script under the plugin root, so a session resolves it to the loaded copy: {c}"

try:
    shas = git("log", "--full-history", "--format=%H", "--", "hooks/hooks.json").split()  # every side of a merge
    shallow = git("rev-parse", "--is-shallow-repository").strip() == "true"
except (OSError, subprocess.CalledProcessError) as e:
    shas, shallow = [], False
    print(f"retired: no git history ({e}); only the current hooks.json is checked")
if shallow:
    print("retired: a shallow clone; paths removed before its first commit are not checked")
ever = {p for c in now for p in SCRIPT.findall(c)}
for sha in shas:
    try:
        text = git("show", f"{sha}:hooks/hooks.json")
    except subprocess.CalledProcessError:  # the commit that deleted or renamed the file
        continue
    ever |= {p for c in commands(text) for p in SCRIPT.findall(c)}

missing = sorted(p for p in ever if not (REPO / p).is_file())
assert not missing, (f"hooks.json once ran {missing}, and a session started before that change still runs it: "
                     "keep a no-op script at that path (see hooks/orchestrator_guard.py)")

retired = sorted(ever - {p for c in now for p in SCRIPT.findall(c)})
for p in retired:
    r = subprocess.run([sys.executable, REPO / p], input='{"hook_event_name":"PreToolUse","tool_name":"Bash"}',
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and not r.stdout.strip(), f"{p}: a retired hook passes silently ({r.returncode}, {r.stdout!r})"
print(f"retired: all pass ({len(ever)} paths ever run, {len(retired)} retired: {', '.join(retired) or 'none'})")
