---
name: arena
description: "Run several candidates at one task, pick a base, graft in the best of the rest, and return one artifact. Use for /arena, \"경쟁시켜줘\", \"여러 버전 만들어서 비교해줘\", or a design where one attempt would lock in the wrong shape. Split-up work ending in a report is swarm."
---

# Arena

Fan out N parallel attempts at the same task. Read every candidate end to end. Pick the strongest as the base. Graft the best ideas from the others into it. Verify the synthesized result.

A **<name>** principle skill below is `references/principle-<name>.md` in the **principles** skill.

## Phase A: Frame

Every candidate gets the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric, 3-6 concrete gradeable criteria for what success looks like on *this* task. It is the picker's tool in Phase D. Candidates only see the task.
3. Pick available runners suited to the task and authorized environment. Independent attempts
   may use the same model. Use the host's supported delegation API and configured defaults;
   do not require a particular vendor, model family, plugin, or global installation path.
4. Assign output paths. Each candidate writes to its own location (a git worktree through `isolation: "worktree"` where the artifact lives in the repo, otherwise its own folder in the scratchpad), per the **separate-before-serializing-shared-state** principle skill.

## Phase B: Fan out

Launch every candidate in one message, each with the task, the path to the shared grounding, its own output path, and instructions to produce the artifact and a short rationale naming the alternatives it considered and what it rejected.

If a candidate produces no output, proceed with N-1 and note the dropout.

## Phase C: Cross-judge

After every Phase B candidate completes, spawn one read-only judge who did not produce the candidates. It sees the rubric and the candidates by path label, scores each criterion, and recommends a base with rationale, while you do your own reading in Phase D.

## Phase D: Pick a base

Read every candidate end to end before picking. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.

Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API, per the **minimize-reader-load** principle skill.

## Phase E: Graft

Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand, per the **redesign-from-first-principles** principle skill, so the result stays coherent under one mental model. Don't paste mechanically.

If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape. No graft is needed. If they wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.

## Phase F: Verify

Verify the synthesized artifact like any other output, per the **prove-it-works** principle skill. If verification finds a problem the arena missed, either Phase A was wrong (re-frame and re-run) or a candidate caught it and you missed the graft (go back to Phase E). Don't paper over it.

## Outputs

One synthesized artifact, with a short synthesis note alongside naming the base and why, the cross-judge's verdict, each graft with its source candidate, what was rejected and why, any convergence or dropouts, and the verification result.
