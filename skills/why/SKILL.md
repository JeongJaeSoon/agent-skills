---
name: why
description: "Use for why code is the way it is or why something was decided: \"why is this like this\", \"why did we do X\", \"why was Y picked over Z\", \"왜 이렇게 됐어\", \"이거 왜 이렇게 짰어\", \"이 결정 배경이 뭐야\", \"왜 이렇게 했지\", \"이 값은 어디서 나왔어\", \"히스토리 좀 찾아줘\". Also design rationale, the history behind a regression or a revert, incident-driven or defensive code, where a threshold or magic number came from, and recovering the reasons before changing code. For what the code does or how it works, use how."
---

# Why

Investigate the motivation and intent behind code.

Companion to the `how` skill. `how` answers what the code does and how it works. `why` answers what forces led to its shape.

## Operating Posture

Operate as a careful, cautious, and precise investigator. Be honest about what you know vs what you're inferring. Read `references/epistemics.md` for the full confidence framework and phrasing guide. The synthesis must follow it.

## Step 1. Understand the Target and the Question

Parse what the user is asking. The **target** is usually a chunk of code, a pattern, a feature, or a named design decision. The **question** is usually a design rationale, a tradeoff, a motivating edge case, an external constraint, dead code, or a broad history sweep.

If the target is vague ("why do we do it this way?" with no clear referent), make your best guess from conversation context (open files, recent edits, what was just discussed). State your interpretation briefly so the user can redirect if you're off, then proceed.

## Step 2. Establish the Code Anchor

Before spawning investigators, anchor the investigation in concrete code. You need:

- The relevant file path(s) and line range(s)
- The key symbols (function names, class names, constants)
- An initial commit list. The last few commits touching the target.
- PR numbers from merge commits (pattern `(#1234)` in the subject line)

Build this inline.

```bash
# Blame target lines for last-touch commits
git blame -L <start>,<end> <file>

# Full file history, with patches, through renames
git log --follow -p -- <file>

# Last N commits touching the file, PR numbers visible
git log --oneline -20 -- <file>

# Extract PR numbers from a commit message
git log -1 --format=%B <commit>
```

Pull PR bodies and discussion via `gh` for any substantive commits:

```bash
gh pr view <number> --json title,body,author,createdAt,mergedAt,labels,closingIssuesReferences,comments,reviews
```

Capture this as seed context (file paths, symbols, commits, PR numbers, linked ticket IDs). Pass it to the investigators.

## Step 3. Pick the Mode

**Narrow mode.** For a line, a constant, or a single commit ("why this line", "why is this timeout 30s"), where the blame → commit → PR chain from Step 2 already holds an explicit, cited answer, answer inline from git and `gh` alone. Say that you used narrow mode and which sources you didn't search, so the user can ask for the full sweep. Keep the confidence tiers and the output sections, dropping the empty ones, and list the unsearched sources under What We Don't Know.

If the chain dead-ends (no PR body, "misc fixes", a squash with no description) or the question is about a design decision rather than a line, run the full investigation.

## Step 4. Spawn Parallel Investigators (full investigation)

### Sources

Pick sources from the MCP tools present in this session's tool list. Each source maps to one evidence category and one playbook in `references/sources/`:

| Category | Source | Playbook |
|---|---|---|
| Source control history | git and `gh` (always available) | `code-archaeology.md` |
| Issue / ticket tracker | Linear MCP; Jira through the `use-tracker` skill | `linear.md` |
| Long-form documents | Notion MCP | `notion.md` |
| Long-form documents | Google Drive MCP | `google-drive.md` |
| Long-form documents | Obsidian notes through the `use-notes` skill (design docs, worklogs) | `obsidian.md` |
| Real-time team chat | Slack MCP | `slack.md` |

Infrastructure observability, error tracking, and product analytics (Datadog, Sentry, a data warehouse) have no connection here. Don't spawn for them; list each in Sources Consulted as not searched for lack of access, so the gap stays visible. If an MCP for one of those categories is in the tool list, give it an investigator with the base prompt alone.

Google Calendar is not an evidence source. Use it only to narrow a date range (when a design review or incident call happened) and pass that range to the investigators.

Spawn one investigator per connected source that plausibly holds evidence about this target, each owning exactly that source. "Probably empty" is not a reason to skip: a searched source that comes back empty is a finding, so document the null rather than skip the search. Skip a connected source only when it can't hold evidence for this target, and write the reason into Sources Consulted.

Launch all investigators in a single message so they run concurrently. Each is an `Agent` call with `subagent_type: "general-purpose"` (it keeps MCP access). Tell each one in its prompt that it only reads: no file edits, commits, comments, messages, or ticket changes, since the same MCPs can post to Slack or Linear.

Each investigator gets:
1. The base prompt from `references/investigator-prompt.md`
2. The playbook `references/sources/<source>.md` for its source
3. The cross-cutting `references/sources/incident-postmortem.md` if the target code looks defensive (null checks, retry logic, timeout handling, rate limiting, feature flags, egress guards, OOM handlers)
4. The code anchor from Step 2 (file paths, symbols, commit hashes, PR numbers, ticket IDs), plus any date range
5. The user's original question

### What each category surfaces

Use this to know what to expect back and how to name a gap when a category returns empty.

- **Source control.** Always spawn; the only guaranteed source. Best at *implementation-time rationale captured during review*.
- **Issue / ticket tracker.** Best at *the product or business forcing function*. Strongest when the why is external to engineering.
- **Long-form documents.** Best at *long-form design rationale*, where the why is written out before it becomes code.
- **Real-time team chat.** Best at *deliberation that never reached a doc*. Especially important when the git, ticket, and doc trail is thin.

## Step 5. Synthesize

Once every investigator has returned, synthesize once. Do it yourself, or hand it to one `Agent` (`subagent_type: "general-purpose"`, `model: "opus"`, told it must not write files or change external state) when the findings are too large to hold alongside the conversation.

The synthesis works from:
1. The investigator findings, including null results and the sources skipped with their reasons
2. The code anchor from Step 2
3. The user's original question
4. The epistemics framework from `references/epistemics.md`
5. The synthesizer prompt template from `references/synthesizer-prompt.md`, including its quality check

## Step 6. Present

Present the synthesis to the user. You may lightly edit for clarity or add context from the conversation, but do not rewrite the confidence language.

## Output Format

The output structure is the one in `references/synthesizer-prompt.md`: The Question, The Code in Question, What We Found, What We Can Reasonably Infer, Competing Hypotheses, What We Don't Know, Sources Consulted, Confidence Summary. Adapt as needed, but keep the confidence separation intact, and keep Sources Consulted as one line per source, including the ones that returned nothing or were skipped, with the reason.

After the Sources Consulted block, if the user's `why` question is a precursor to actually changing this code, convert the lineage findings into a Preserve / Change / Avoid / Risk constraint set suitable for planning the change.

## Common Failure Modes to Avoid

- **Recency bias**. Assuming the most recent commit is authoritative. The current shape is often the accretion of many earlier decisions. Trace back.

## Reference Files

- `references/epistemics.md`. Confidence tiers and phrasing guide. The synthesis must follow it.
- `references/investigator-prompt.md`. Base prompt template for investigator subagents.
- `references/source-playbook.md`. Index of the source playbooks.
- `references/sources/*.md`. One playbook per source, plus cross-cutting `incident-postmortem.md`. Give an investigator the single file that matches its source.
- `references/synthesizer-prompt.md`. Prompt template for the synthesis, including the output format.
