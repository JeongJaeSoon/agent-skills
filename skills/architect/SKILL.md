---
name: architect
description: "Sketch types, signatures, and module structure before code, then stay in the loop while implementation fills in. Use for /architect, 'architect this', 'design this', \"상세 설계안 작성해줘\", \"설계안 다듬어줘\", \"구현 계획 짜줘\", or non-trivial work where jumping to code would lock in the wrong shape."
---

# Architect

Design before implementing. Sketch types, function signatures, class shapes, and module boundaries with `not implemented` bodies and pseudocode. Synthesize across multiple model perspectives, then fill in code against the chosen sketch. If implementation proves the sketch wrong, throw it out and redesign.

A **<name>** principle skill below is `references/principle-<name>.md` in the **principles** skill.

## Phase A: Ground the problem

Build a real mental model of every system the new code touches. Run the **how** skill over the relevant subsystems.

Naming a file isn't grounding. Produce the traced model `how` prescribes. If the design redefines ownership or layering, also recover why the existing shape is the way it is (`git log -S` / `git blame` on the load-bearing lines, and the PRs and tickets they cite) so the rationale becomes a constraint, not a guess.

Skip Phase A only when the work is genuinely greenfield with no surrounding system to integrate.

## Phase B: Sketch

Fan out one runner per row below, launched in the same message, with the design-sketch task and the Phase A grounding artifacts. Pass `references/runner-prompt.md` as each runner's prompt, with the absolute paths of this `SKILL.md` and `references/rationale-template.md` so a runner can read them, and a separate output path per runner so candidates stay independent. Each candidate produces a design package shaped per `references/rationale-template.md`.

| Runner | How to run it |
|--------|---------------|
| Claude (opus) | `Agent` tool, `subagent_type: "general-purpose"`, `model: "opus"`, `isolation: "worktree"` when it writes sketch files into the repo. |
| Claude (fable) | Same, with `model: "fable"`. |
| Codex | `node <codex plugin>/scripts/codex-companion.mjs task --background "$(cat <filled prompt file>)"` — the configured default model; add `--model gpt-6-astra --effort medium` only for the hardest design question in the work (never `high`/`xhigh`). Then `status` / `result` with the job id. `task` without `--write` is read-only, so it returns the package in its result and you save it. Find the script with `ls ~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs`. |

Run one Codex job at a time; two concurrent jobs kill each other. If a model name is rejected, use the closest available tier of the same family and say which one ran.

Design it twice. Require at least two structurally distinct candidates before synthesis, even when the first looks sufficient. This is the **exhaust-the-design-space** principle skill made concrete. Whole-shape alternatives, not point fixes inside one shape.

Screen every candidate against [`references/design-red-flags.md`](references/design-red-flags.md) before synthesis. Reject or revise shallow modules, information leakage, temporal decomposition, and pass-through methods.

Compare viable candidates on interface depth. Prefer the design that hides more complexity behind a smaller, simpler public surface. A rich interface can keep call chains short by concentrating capability instead of scattering it across layers.

You synthesize the viable candidates into one design package: pick the base, graft what each other candidate did better, and record the choice in the rationale's "Synthesis decision" section.

## Phase C: Agree (opt-in)

Default: proceed directly to implementation with the synthesized design. No human checkpoint.

Opt in to a checkpoint when the invoker explicitly asks: "/architect with checkpoint," "stop and show me before implementing," or similar. Then surface the synthesized design and pause for sign-off.

The synthesis can ship as its own commit either way, as the "scaffold first" mode of the **foundational-thinking** principle skill. Planned and scoped breakage during fill-in is fine, per the **outcome-oriented-execution** principle skill. For adversarial pressure on the design before implementing, run the **interrogate** skill on the synthesized sketch.

If the human pushes back on the shape (in a checkpoint or after the fact), treat that as Phase A evidence. Re-ground and re-run Phase B before writing more code.

## Phase D: Implement against the sketch

Replace `not implemented` bodies with code, pseudocode with logic. The synthesized sketch is the contract.

Deviations from the sketch are signal worth surfacing, not friction to absorb silently. If a function needs a parameter the sketch didn't anticipate, ask whether the sketch was wrong, the requirement was missed, or the implementation is overreaching.

## Phase E: Scrap when the architecture is wrong

If implementation keeps producing friction the sketch can't absorb, throw the sketch out. Don't bolt fixes onto a wrong design, per the **redesign-from-first-principles** and **fix-root-causes** principle skills.

The signal is a *pattern*, not single instances. Tells:

- The same shape of workaround appearing repeatedly across unrelated code.
- Multiple unrelated edge cases that all need special-case branches.
- Types that need escape hatches (`any`, casts, optional fields always set in practice) to compile.
- The "we need a lock" reflex when the sketch said the state wasn't shared.
- Callers having to know the abstraction's internal rules to use it.
- Two or more independent Phase D deviations of the same shape across the implementation.

Use judgment. A few edge cases don't condemn an architecture. Some problems are legitimately complex. Complexity in the data is not complexity in the design.

When you scrap:

1. Re-run the **how** skill over what's been built.
2. Redesign as if the new constraints had been day-one assumptions, per redesign-from-first-principles.
3. Subtract before adding, per the **subtract-before-you-add** principle skill. The new sketch should be smaller than the old one before it grows.
4. Return to Phase B and re-run the fan-out.

## Outputs

The caller's usage is written first and the type sketch derived from it. One file with new types and signatures for small changes. Module map plus type definitions for larger work. The rationale ships alongside, shaped per `references/rationale-template.md`, including the usage sketch and the synthesis decision.
