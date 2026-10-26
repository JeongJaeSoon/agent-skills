---
name: interrogate
description: "Use for \"interrogate\", \"adversarial review\", \"challenge this\", or \"find blind spots\" on a design, plan or decision: several LLM reviewers challenge it from independent angles. A PR on its way to main is deliver-ticket's independent review."
---

# Interrogate

Have independent reviewers challenge the design or changes against the same prompt and rubric. Evidence and distinct reasoning matter more than model identity.

Select reviewers within the repository's data-sharing and permission boundaries. Read those
restrictions first and include them in each review brief; if no permitted reviewer is available,
report the review as unavailable rather than send code to a prohibited service.

The deliverable is a synthesized verdict; this skill changes no files. When `deliver-ticket` calls it, the caller applies the Act on findings.

## Step 1, Scope

Identify what to review from context:

- If the user points at specific files or a diff, use that.
- If on a feature branch, run `git diff main...HEAD` (or the appropriate base branch) for the full changeset.
- If the user's message references recent work, gather the relevant files.

Package the diff or file contents with any surrounding context files the reviewers need to understand the code.

## Step 2, Intent

Before spawning reviewers, state the intent in one clear paragraph from the user's message, commit messages, the PR description if one exists, and the code. If the intent is genuinely ambiguous, write your best reading and mark it as assumed, so the verdict can be re-read against it.

## Step 3, Spawn Reviewers

Use independent read-only reviewers available in the authorized environment. Give each the
same raw scope and rubric, not another reviewer's conclusions. Choose the number by the
change's risk; no particular model or different model family is required. Record unavailable
reviewers and incomplete coverage rather than claiming a pass.

Read `references/reviewer-prompt.md` and fill in the same template for every reviewer with the stated intent, the diff or file contents, the rubric from `references/rubric.md`, and the code-quality lens from `references/code-quality-review.md`.

## Step 4, Synthesize

Merge the reviewers' findings into one list. Different reviewers describe the same issue differently: merge those and note which reviewers raised each. A finding raised independently by 2+ reviewers is the strongest signal; a single-reviewer finding still gets read, weighted accordingly. Where one reviewer explicitly contradicts another, keep both sides for the verdict and the Agreement Map.

## Step 5, Lead Judgment

You are the lead reviewer, a pragmatic senior engineer, not a neutral aggregator. Read `references/lead-judgment.md` for the full framework. Put every finding in one of four buckets, with the reviewer(s) that raised it and a one-line rationale.

- **Act on**. Real issues affecting correctness, security, or maintainability given the actual goals. They would block a real PR.
- **Consider**. Legitimate, but you're not sure they outweigh the cost of addressing them now. Worth the user's attention.
- **Noted**. Technically valid but not actionable. Context-dependent, premature optimization, or low-impact at this stage.
- **Dismissed**. Wrong, nitpicky, or missing context, with a brief reason.

## Output Format

### Intent
> [The stated intent paragraph from Step 2]

### Reviewers
- Reviewer [label]: [reviewer identity], [N findings] (one bullet per reviewer)

### Act On
[Each: description, which reviewers raised it, why it matters.]

### Consider
[Each: description, which reviewers raised it, the tradeoff.]

### Noted
[Brief list.]

### Dismissed
[Each with a brief rationale.]

### Agreement Map
[Where reviewers agreed and diverged, and what that pattern tells us.]
