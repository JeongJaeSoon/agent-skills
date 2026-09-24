#!/bin/bash
# Sends one prompt to throwaway headless sessions and prints which skills each run invoked.
# User settings are skipped so the installed copy of the plugin does not load beside <plugin-dir>.
# dontAsk denies every write, shell command and MCP call except the read-only git/ls/grep below.
set -euo pipefail
usage='usage: trigger-probe.sh <plugin-dir> <prompt> [runs=3] [repo-to-copy]'
plugin=$(cd "${1:?$usage}" && pwd)
prompt=${2:?$usage}
runs=${3:-3}
src=${4:-}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for i in $(seq "$runs"); do
  work=$tmp/run$i
  mkdir -p "$work"
  if [ -n "$src" ]; then cp -R "$src"/. "$work"/; else git -C "$work" init -q; fi
  (cd "$work" && claude -p "$prompt" --output-format stream-json --verbose \
    --setting-sources project --plugin-dir "$plugin" --no-session-persistence \
    --permission-mode dontAsk --allowedTools "Bash(git log:*)" "Bash(git show:*)" \
    "Bash(git blame:*)" "Bash(git status:*)" "Bash(ls:*)" "Bash(grep:*)" \
    < /dev/null > "$tmp/run$i.jsonl" 2> /dev/null || true) &
done
wait

python3 - "$tmp" "$runs" <<'EOF'
import json, sys

tmp, runs = sys.argv[1], int(sys.argv[2])
total = 0.0
for i in range(1, runs + 1):
    skills, agents, asks, cost, result = [], 0, 0, 0.0, ""
    for line in open(f"{tmp}/run{i}.jsonl"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant" and not d.get("parent_tool_use_id"):
            for b in d["message"].get("content", []):
                if b.get("type") != "tool_use":
                    continue
                if b["name"] == "Skill":
                    skills.append(b["input"].get("skill", "?"))
                agents += b["name"] == "Agent"
                asks += b["name"] == "AskUserQuestion"
        if d.get("type") == "result":
            cost = d.get("total_cost_usd") or 0.0
            result = (d.get("result") or "").replace("\n", " ")[:140]
    total += cost
    print(f"run {i}: skills={skills or '-'} agents={agents} asks={asks} ${cost:.2f}")
    print(f"  {result}")
print(f"total ${total:.2f}")
EOF
