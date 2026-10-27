"""Rules of skill_tokens.py on a fake plugin, and a pass over this checkout. Run: python3 scripts/test_skill_tokens.py"""
import contextlib, io, json, os, pathlib, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import skill_tokens as st  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp())


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


SHARED = "Every change is checked against the skills already there before a new one is written down."
root = tmp / "plugin"
write(root, ".claude-plugin/plugin.json", json.dumps({"name": "demo"}))
write(root, "skills/alpha/SKILL.md", '---\nname: alpha\ndescription: "Use for \\"x\\" or 한글 phrase"\n---\n\n'
      f"# Alpha\n\nRead [references/own.md](references/own.md) first. {SHARED}\n"
      "Sort as `other`'s `references/far.md` does; `references/missing.md` is gone.\n")
write(root, "skills/alpha/references/own.md", "own reference text\n")
write(root, "skills/beta/SKILL.md", "---\nname: beta\ndescription: Beta skill\ndisable-model-invocation: true\n---\n"
      f"- {SHARED}\n")
write(root, "skills/other/SKILL.md", "---\nname: other\ndescription: Other\n---\nbody\n")
write(root, "skills/other/references/far.md", "far reference text that is longer than the own one\n")
write(root, "agents/worker.md", "---\nname: worker\ndescription: Does work\ntools: Read, Bash\n---\nAgent body.\n")
write(root, "agents/plain.md", "---\nname: plain\ndescription: Plain\n---\n")
write(root, "hooks/hooks.json", json.dumps({"hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/onboard.py"'}]}],
    "Stop": [{"hooks": [{"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/new_hook.py"'}]}]}}))
write(root, "CLAUDE.md", "@AGENTS.md\n")

# Character classes and display width: a Hangul syllable is wide and costs more than an ASCII letter.
assert st.class_counts("ab, 한!") == {"alnum": 2, "punct": 2, "space": 1, "hangul": 1, "cjk": 0, "other": 0}
assert st.display_width("ab한") == 4
assert st.approx_tokens("한") > st.approx_tokens("a") and st.approx_tokens("") == 0

# Frontmatter: a double-quoted description is unescaped the way YAML does it.
fm, body = st.split_frontmatter((root / "skills/alpha/SKILL.md").read_text())
assert fm["description"] == 'Use for "x" or 한글 phrase' and body.startswith("\n# Alpha"), fm

rep = st.measure(root)
skills = {s["name"]: s for s in rep["skills"]}
line = '- demo:alpha: Use for "x" or 한글 phrase'
assert skills["alpha"]["width"] == st.display_width(line) and skills["alpha"]["tokens"] == st.approx_tokens(line)
# disable-model-invocation lists the name only.
assert skills["beta"]["width"] == len("- demo:beta"), skills["beta"]
agents = {a["name"]: a for a in rep["agents"]}
assert agents["worker"]["width"] == len("- demo:worker: Does work (Tools: Read, Bash)")
assert agents["plain"]["width"] == len("- demo:plain: Plain (Tools: All tools)")

# References: one level, own and other skills' files, a missing one skipped, each attributed to the skill that names it.
inv = {x["skill"]: x for x in rep["invoke"]}
assert [(r["path"], r["cross"]) for r in inv["alpha"]["refs"]] == [
    ("skills/alpha/references/own.md", False), ("skills/other/references/far.md", True)], inv["alpha"]["refs"]
assert inv["alpha"]["refs_tokens"] == sum(r["tokens"] for r in inv["alpha"]["refs"])
assert inv["alpha"]["tokens"] == st.approx_tokens((root / "skills/alpha/SKILL.md").read_text())
assert rep["totals"]["refs_tokens"] == inv["alpha"]["refs_tokens"]

# Duplicates: the shared sentence, list marker stripped, found in both skills.
dup = [d for d in rep["dups"] if d["text"] == SHARED.lower()]
assert dup and dup[0]["files"] == ["skills/alpha/SKILL.md", "skills/beta/SKILL.md"], rep["dups"]
assert dup[0]["save"] == dup[0]["tokens"]

# Hooks are listed with their events, not run; one with no entry in HOOKS says so.
hooks = {h["name"]: h for h in rep["hooks"]}
assert hooks["onboard.py"]["events"] == ["SessionStart"] and hooks["onboard.py"]["when"] == st.HOOKS["onboard.py"]
assert hooks["new_hook.py"]["when"].startswith("unknown"), hooks
assert rep["repo"] == [{"path": "CLAUDE.md", "tokens": st.approx_tokens("@AGENTS.md\n")}]
assert rep["totals"]["always_width"] == sum(x["width"] for x in rep["skills"] + rep["agents"])

text = st.render(rep, 5, budget=1000)
assert "of listing budget 1,000" in text and "new_hook.py" in text and "skills/other/references/far.md" not in text

# Diff: a longer description and a grown reference show up; an unchanged run says no change.
old = json.loads(json.dumps(rep))
d = st.diff(old, rep, 10)
assert not d["items"] and not any(d["hooks"].values()) and "no change" in st.render_diff(d, "x")
write(root, "skills/alpha/references/own.md", "own reference text, now with a lot more words in it\n")
write(root, "skills/other/SKILL.md", "---\nname: other\ndescription: Other skill with a longer description\n---\nbody\n")
new = st.measure(root)
d = st.diff(old, new, 10)
kinds = {(k, n) for k, n, _, _ in d["items"]}
assert ("skills", "other") in kinds and ("refs", "skills/alpha/references/own.md") in kinds, kinds
assert d["totals"]["always_tokens"] > 0 and d["totals"]["refs_tokens"] > 0
assert "+" in st.render_diff(d, "base")

# --base reads a git ref through `git archive`, without touching the working tree.
git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false"]
subprocess.run(git + ["init", "-q"], check=True)
subprocess.run(git + ["add", "-A"], check=True)
subprocess.run(git + ["commit", "-q", "-m", "base"], check=True)
write(root, "skills/beta/SKILL.md", "---\nname: beta\ndescription: Beta skill\n---\nshort\n")
d = st.diff(st.measure_ref("HEAD", repo=root), st.measure(root), 10)
assert ("skills", "beta") in {(k, n) for k, n, _, _ in d["items"]}, d
assert (root / "skills/beta/SKILL.md").read_text().endswith("short\n")

# The CLI: table, JSON, and a baseline file.
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert st.main(["static", "--root", str(root), "--json"]) == 0
(tmp / "base.json").write_text(out.getvalue())
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert st.main(["static", "--root", str(root), "--baseline", str(tmp / "base.json")]) == 0
assert "no change" in out.getvalue(), out.getvalue()

# --exact needs a key and never falls back to the approximation.
saved = os.environ.pop("ANTHROPIC_API_KEY", None)
try:
    st.main(["static", "--root", str(root), "--exact"])
    raise AssertionError("--exact ran without a key")
except SystemExit as e:
    assert "ANTHROPIC_API_KEY" in str(e)
finally:
    if saved is not None:
        os.environ["ANTHROPIC_API_KEY"] = saved

# Calibration recovers the coefficients behind the counts it is given.
truth = {"alnum": 0.3, "punct": 0.6, "space": 0.05, "hangul": 1.1, "cjk": 1.2, "other": 1.0}


def fake_count(text):
    return sum(truth[k] * v for k, v in st.class_counts(text).items())


cal = st.calibrate(pathlib.Path(__file__).resolve().parents[1], fake_count)
for k in ("alnum", "punct", "space", "hangul"):
    assert abs(cal["coefficients"][k] - truth[k]) < 0.01, cal
assert cal["error_fitted"]["max"] < 1 and cal["files"] > 10

# This checkout: every hook in hooks.json has a `when`, and the measure runs.
here = st.measure(st.REPO)
assert not [h["name"] for h in here["hooks"] if h["when"].startswith("unknown")], here["hooks"]
assert here["skills"] and here["totals"]["always_tokens"] > 0
print("ok")
