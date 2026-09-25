---
name: principles
description: Use when a design, refactor, verification, or delegation decision needs a named engineering principle — sizing a diff, choosing where validation goes, deciding whether work is proven, splitting work across agents, or noticing the same instruction being written a second time.
---

# Engineering principles

Each principle lives in its own file under `references/`. Read the leaf file in full for any principle you apply; the line below only says when it applies.

**Core**

- **Laziness Protocol** (`references/principle-laziness-protocol.md`). Refactoring, sizing a diff, or tempted to add abstractions, layers, or signal threading.
- **Foundational Thinking** (`references/principle-foundational-thinking.md`). Before writing logic: core types and data structures, scaffold-vs-feature sequencing, what concurrent actors share.
- **Redesign from First Principles** (`references/principle-redesign-from-first-principles.md`). Integrating a new requirement into an existing design.
- **Attack the Premise** (`references/principle-attack-the-premise.md`). Two or more fixes that share one premise have failed the same gate.
- **Subtract Before You Add** (`references/principle-subtract-before-you-add.md`). Sequencing an addition, refactor, or rewrite.
- **Minimize Reader Load** (`references/principle-minimize-reader-load.md`). Code that's hard to trace: count layers and hidden state, collapse one-caller wrappers.
- **Outcome-Oriented Execution** (`references/principle-outcome-oriented-execution.md`). Planned rewrites and migrations with explicit phase boundaries.
- **Experience First** (`references/principle-experience-first.md`). Product, UX, or feature-scope tradeoffs.
- **Exhaust the Design Space** (`references/principle-exhaust-the-design-space.md`). A novel interaction or architectural decision with no precedent.
- **Build the Lever** (`references/principle-build-the-lever.md`). Any non-trivial work: build the tool that does or proves it, not the result by hand.

**Architecture**

- **Model the Domain** (`references/principle-model-the-domain.md`). Stateful logic, heavy branching, or a shape assumption repeated across files.
- **Boundary Discipline** (`references/principle-boundary-discipline.md`). Wiring validation, error handling, or framework adapters.
- **Type System Discipline** (`references/principle-type-system-discipline.md`). Designing types or a signature in a typed language.
- **Make Operations Idempotent** (`references/principle-make-operations-idempotent.md`). Commands, lifecycle steps, or loops that run amid crashes and retries.
- **Migrate Callers Then Delete Legacy APIs** (`references/principle-migrate-callers-then-delete-legacy-apis.md`). A new internal API while old callers exist.
- **Separate Before Serializing Shared State** (`references/principle-separate-before-serializing-shared-state.md`). Concurrent actors might write the same file, branch, key, or object.

**Verification**

- **Prove It Works** (`references/principle-prove-it-works.md`). After a task, before declaring done; and when trusting a delegate's report.
- **Sequence Work into Verifiable Units** (`references/principle-sequence-verifiable-units.md`). Multi-step work and how commits and PRs stack.
- **Test Behavior, Not Implementation** (`references/principle-test-behavior-not-implementation.md`). Writing, changing, or keeping a test.
- **Fix Root Causes** (`references/principle-fix-root-causes.md`). Debugging: trace each symptom to its root cause and fix the pattern there, not the instance.

**Delegation**

- **Guard the Context Window** (`references/principle-guard-the-context-window.md`). Large outputs, long files, repeated reads, fan-out planning.
- **Never Block on the Human** (`references/principle-never-block-on-the-human.md`). Tempted to ask "should I do X?" on reversible work; irreversible actions still need confirmation.
- **Stay Answerable** (`../orchestrate/SKILL.md`, "Stay answerable"). You are the session the human talks to: route and answer; every piece of work runs in a background subagent or another session.

**Meta**

- **Encode Lessons in Structure** (`references/principle-encode-lessons-in-structure.md`). You catch yourself writing the same instruction a second time.

A link to `../principle-<name>/SKILL.md` inside a leaf file means `references/principle-<name>.md` here. A skill a leaf file names that is not installed here is background, not a skill to load.
