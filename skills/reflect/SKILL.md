---
name: reflect
description: "Spawn three parallel review subagents over the active transcript, surface learnings, and route each to a concrete edit on an existing skill. Use when the user says reflect, \"스킬에 반영해줘\", \"스킬이 왜 안 떴어\", \"스킬 갱신이 필요해\", or \"이 세션 돌아보고 스킬 개선해줘\"."
---

# Reflect

Mine the current conversation for durable learnings, then route them into skill edits.

## When to invoke

Invoke when the user says "reflect" or "/reflect". Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings.

## Process

### 1. Locate the active transcript

The parent finds its own transcript file before fanning out. Claude Code writes it under `~/.claude/projects/<cwd with every / and . replaced by ->/`, for this session's working directory. Use that path. Do not glob across `~/.claude/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.

```bash
ls -t <project-dir>/*.jsonl <project-dir>/*/subagents/*.jsonl 2>/dev/null | head -10
```

Two transcript layouts: session (`<session-id>.jsonl`) and subagent (`<session-id>/subagents/agent-<id>.jsonl`).

For each candidate, find the first JSONL line with `"type":"user"` and check that its `message.content` contains the conversation's opening user prompt. Take the matching path. If no path resolves, write a tight digest of the session and pass that instead.

### 2. Spawn three reviewers in parallel

One message, three reviewers launched together. Claude reviewers use the `Agent` tool with `subagent_type: "general-purpose"` and an explicit `model`, not the read-only `Explore`/`Plan` agents: reviewers need MCP access for context lookups (tickets, chat threads, observability traces referenced in the transcript), and the read-only agents lack it. The templates already forbid edits.

| Lens | Runner | Prompt template |
|---|---|---|
| Judgment | `Agent`, `model: "opus"` | `references/judgment-reviewer.md` |
| Tooling | Codex: `node <codex plugin>/scripts/codex-companion.mjs task --background "$(cat <filled prompt file>)"`, the configured default model. Read-only without `--write`. Find the script with `ls ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`. Codex sees only its own MCP servers, so inline any ticket or thread it will need. | `references/tooling-reviewer.md` |
| Divergent | `Agent`, `model: "opus"` | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the transcript path or digest where marked. Reviewers return findings in their final response (Codex: `result <job id>`).

### 3. Synthesize

One `Agent` call, `subagent_type: "general-purpose"`, `model: "opus"`. The synthesizer's quality check includes spot-verifying citations, which can require MCP access. The read-only agents lack it. Use `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. The synthesizer returns a structured Accepted / Rejected / Backlog list.

### 4. Structural enforcement check

Sanity-check the synthesizer's Accepted list. For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. See the **encode-lessons-in-structure** principle skill.

### 5. Apply

Before applying any Accepted edit, present the synthesizer's full Accepted/Rejected/Backlog output to the user and wait for explicit approval. The user picks which subset to apply and may redirect routings. Skill changes affect every future agent in the org. Do not auto-apply.

Backlog items file to the user's ticket tracker (the **use-tracker** skill) automatically. Only the Accepted list waits for approval.

This plugin's skills (`agent-skills`) come from the `JeongJaeSoon/agent-skills` repo. Make every edit in a checkout of it, on a worktree branch, never in the installed copy under `~/.claude/plugins/cache/`, which an update replaces. `claude --plugin-dir <checkout>` loads the edited copy for a test. A skill from another plugin is not edited in place; record the finding as Backlog instead.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the **skill-creator** skill and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to **skill-creator** and run its description-optimization loop.
- `new skill via skill-creator: <kebab-name>`: hand creation to **skill-creator**. Do not invent the shape ad hoc.

Before declaring done, run `claude plugin validate <checkout>` and fix what it reports.

### 6. Summarize for the user

Short list, no preamble:

- Edits applied: `<skill path>`. What changed, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the devex tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
