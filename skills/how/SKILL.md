---
name: how
description: "Explain how code works by reading it: a subsystem's runtime flow, its architecture, where something should live. Use for \"how does X work\", \"X 는 어떻게 동작해\", \"이 구조 설명해줘\", \"이건 어디에 둬야 해\", or a walkthrough before changing code. Why it is this way, or why it was decided, is why."
---

# How

Explore the codebase to answer "how does X work?" questions. Produce architectural explanations at the level of a senior engineer onboarding onto a subsystem, enough to build a working mental model, not so much that it reads like annotated source code.

Explorers are the built-in read-only `Explore` subagent. The explainer is one `general-purpose` `Agent` on `opus`, told in its prompt that it only reads and writes the explanation and must not edit files.

## 1. Assess complexity

If the scope is ambiguous, state your interpretation and explore. The user can redirect.

- **Simple** (a single module, a small utility, a narrow question such as "how does function X work"): no explorers and no explainer. Explore and explain it yourself in one pass, following the Instructions and Output Format in `references/explainer-prompt.md` (skip its explorer-findings parts). Go to step 4.
- **Complex** (a subsystem spanning multiple files or services, a cross-cutting feature, a full architectural overview): go to step 2.

When in doubt, take the simple path.

## 2. Explore (complex only)

Decompose the question into 2 to 4 angles, each a distinct slice of the subsystem. Spawn all explorers in a single message, each with `references/explorer-prompt.md` and its angle filled in.

## 3. Synthesize (complex only)

Once all explorers have returned, spawn one explainer with `references/explainer-prompt.md` and every explorer's findings filled in. Synthesize once; do not re-run it per explorer.

## 4. Present

Present the explainer's output. Light edits for clarity or context from the conversation are fine. Do not substantially rewrite it.

## Output format

The explanation uses the sections defined in `references/explainer-prompt.md`, dropping any that do not apply: Overview, Key Concepts, How It Works, Where Things Live, Gotchas.
