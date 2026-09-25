---
name: interrogate
description: "Use for \"interrogate\", \"adversarial review\", \"multi-model review\", \"codex 교차 검증\", \"적대적 리뷰\", \"codex 로 설계안/계획 점검\", \"문제없는지 codex 에게 리뷰\", \"challenge this\", or \"find blind spots\" on a design, plan or diff. Multiple LLM reviewers challenge it from independent angles. A PR on its way to main goes through deliver-ticket's Codex review instead."
---

# Interrogate

Spawn one reviewer per configured model to adversarially review code changes. Each model gets the same prompt and rubric. The adversarial signal comes from model diversity, not assigned personas.

The deliverable is a synthesized verdict. Do NOT auto-apply changes.

## Step 1, Determine Scope

Identify what to review from context:

- If the user points at specific files or a diff, use that
- If on a feature branch, run `git diff main...HEAD` (or the appropriate base branch) for the full changeset
- If the user's message references recent work, gather the relevant files

Package the diff (or file contents) plus any surrounding context files the reviewers need to understand the code.

## Step 2, State the Intent

Before spawning reviewers, state the intent explicitly. Derive this from:

- The user's message
- Commit messages
- PR description if one exists
- The code itself

Write one clear paragraph. If the intent is genuinely ambiguous, write your best reading and mark it as assumed, so the verdict can be re-read against it.

## Step 3, Spawn Reviewers

One reviewer per model family, launched in the same message so they run in parallel:

| Reviewer | How to run it |
|----------|---------------|
| Reviewer A (Claude) | `Agent` tool, `subagent_type: "general-purpose"`, `model: "opus"`. Tell it in the prompt that it only reads and reports; it must not edit files. |
| Reviewer B (Codex) | `node <codex plugin>/scripts/codex-companion.mjs task --background "$(cat <filled prompt file>)"` — the configured default model, as in `deliver-ticket` §3; add `--model gpt-6-astra --effort medium` (or `low` for a bounded question) only when the change is a hard design question, then wait with `status <job id> --wait --timeout-ms 1800000` as a Bash call with `run_in_background`, which wakes you when the job ends, and read `result <job id>`. Ending the turn to wait instead ends an unattended run for good. `task` without `--write` is read-only. Find the script with `ls ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`. |

Run one Codex job at a time; two concurrent jobs kill each other. If a model name is rejected, use the closest available tier of the same family and say which one ran. Do not block the review on it.

Read `references/reviewer-prompt.md` and fill in the template with:
1. The stated intent
2. The diff or file contents
3. The review rubric from `references/rubric.md`
4. The code-quality lens from `references/code-quality-review.md`

The same filled template goes to all reviewers, so every model applies the code-quality lens.

## Step 4, Synthesize

Merge the reviewers' findings into one list. Different models describe the same issue differently: merge those and note which models raised each. A finding raised independently by 2+ models is the strongest signal; a lone-model finding still gets read, weighted accordingly. Where one model explicitly contradicts another, keep both sides for the verdict and the Agreement Map.

## Step 5, Lead Judgment

You are the lead reviewer, a pragmatic senior engineer, not a neutral aggregator.

Read `references/lead-judgment.md` for the full framework.

Categorize every finding using these buckets:

- **Act on**. Real issues affecting correctness, security, or maintainability given the actual goals. These would block a real PR.
- **Consider**. Legitimate points, but you're not sure they outweigh the cost of addressing them right now. Worth the user's attention.
- **Noted**. Technically valid but not actionable. Context-dependent, premature optimization, or low-impact given the current stage.
- **Dismissed**. Wrong, nitpicky, or missing context. Brief explanation why.

For each finding, include:
- Which model(s) raised it
- The category (act on / consider / noted / dismissed)
- A one-line rationale for the categorization

## Output Format

Present the verdict in this structure:

### Intent
> [The stated intent paragraph from Step 2]

### Reviewers
- Reviewer [label]: [model name], [N findings] (one bullet per reviewer)

### Act On
[Findings that should be addressed. For each: description, which models raised it, why it matters.]

### Consider
[Findings worth thinking about. For each: description, which models raised it, tradeoff involved.]

### Noted
[Valid but low-priority. Brief list.]

### Dismissed
[Rejected findings with brief rationale.]

### Agreement Map
[Where did models agree, where did they diverge, and what does the pattern of agreement/disagreement tell us?]
