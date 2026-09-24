---
name: figure-it-out
description: "Design an auditable playbook before any code when no narrower procedure fits: a large migration, an ambitious multi-part change, or work the human reviews after stepping away. Use for /figure-it-out, 'figure it out', \"알아서 설계해서 진행해줘\", \"큰 마이그레이션\", \"자리 비운 동안 끝내줘\", or when deliver-ticket's Plan finds work of that size. Several tickets run on parallel cards are orchestrate."
---

# Figure it out

When the task matches no narrower procedure, design one. The deliverable before any code is the workflow itself: a sequence of phases that scales rigor to the task, runs each unit as an experiment, and leaves a decision trail a human can audit after stepping away.

A **<name>** principle skill below is `references/principle-<name>.md` in the **principles** skill.

## Phase A: Frame

Ground first, then commit. Don't start the run until you can state:

- The definition of done as a falsifiable predicate (the **prove-it-works** principle skill).
- Scope, quantified: rough units and effort, plus the blockers grounding surfaced.
- The rigor level, biased high. One-way doors and high blast radius get more. Reversible low-stakes steps get less. Rigor is gates and artifacts, not "try harder".

Write the framing and its tradeoffs where the human reads it: the ticket's plan comment, or the worklog when there is no ticket. Reversible work proceeds without waiting (the **never-block-on-the-human** principle skill). A step that cannot be undone, such as a destructive migration or a backfill with no rollback, waits for one explicit approval at that step.

## Phase B: Design the workflow

Decompose into atomic, independently landable units. Sequence the riskiest unknown first. Scaffold and verification come before features (the **foundational-thinking** principle skill).

- Build the verification harness before the work, with the baseline captured from the pre-change state, so each check reads as "old value vs new value".
- For a one-way-door design decision, run the **architect** skill (it runs **arena**). Skip it for mechanical work whose shape is already concrete. A second arena over a settled design is over-engineering (the **laziness-protocol** principle skill).
- Decide what fans out. Parallelize only across seams, and give each worker its own worktree or branch (the **separate-before-serializing-shared-state** principle skill). Don't over-fan. When the units are separate tickets that should run on parallel cards, hand the program to **orchestrate** instead.
- Write the phase list into the plan from Phase A. That list is what the human reviews.

Then execute the design, one unit at a time under the Phase C loop, adding a Phase D row as each unit lands rather than saving the trail for the end. Each unit still goes through **deliver-ticket**'s review, commit and ship rules.

## Phase C: Run the loop

Each unit is an experiment. State the hypothesis, make the smallest change, measure against the predicate on the real artifact, keep it if it advanced, revert it if it didn't. Verify each unit before starting the next instead of batching checks at the end (the **sequence-verifiable-units** principle skill).

- Verify by inspecting the artifact, never a self-report. When something passes too easily, suspect the observation method before the system.
- Pair delegated work with a judge: read the subagent's artifact yourself or have Codex check it. If a worker games the gate, reset and harden the contract. If the gate itself is wrong, fix the gate in its own change rather than routing around it.
- A verdict is VERIFIED, NOT VERIFIED, or INCONCLUSIVE. Inconclusive is not a pass. Don't hide a negative.

## Phase D: Keep the audit trail

Log the run with the **show-me-your-work** skill. Work of this size usually earns a committed trail, so the reviewer can read it in the PR. The trail plus the diff is what lets the human come back and trust the work.

## Phase E: Verify and hand back

Check the whole against the Phase A predicate on the real product, not only the harness. Encode any recurring correction as a gate, a lint rule, a check, or a script (the **encode-lessons-in-structure** principle skill).

**Reply:** the playbook you designed, the rigor level and why, the decision-trail path, what is verified against the predicate, and what is still open.
