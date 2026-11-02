"""Obsidian and Linear MCP tools arrive under two prefixes: a local server (mcp__obsidian__, mcp__linear__)
or the claude.ai connector (mcp__claude_ai_Obsidian__, mcp__claude_ai_Linear__). A file that names one
prefix must name the other, so no skill, agent or hook works for only one of them.
Run: python3 scripts/test_mcp_prefixes.py"""
import pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAIRS = [("mcp__obsidian__", "mcp__claude_ai_Obsidian__"), ("mcp__linear__", "mcp__claude_ai_Linear__")]

files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "skills", "agents", "hooks", "scripts", "docs"],
                       capture_output=True, text=True, check=True).stdout.split()
bad = []
for rel in files:
    if rel == "scripts/test_mcp_prefixes.py":
        continue
    try:
        text = (ROOT / rel).read_text()
    except (UnicodeDecodeError, FileNotFoundError):
        continue
    for local, connector in PAIRS:
        if (local in text) != (connector in text):
            bad.append(f"{rel}: names only {local if local in text else connector}")
assert not bad, "\n".join(bad)
seen = [rel for rel in files if any(c in (ROOT / rel).read_text(errors="ignore") for _, c in PAIRS)]
assert "skills/use-tracker/SKILL.md" in seen and "skills/use-notes/references/obsidian.md" in seen, seen
print("test_mcp_prefixes: ok")
