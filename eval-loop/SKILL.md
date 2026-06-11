---
name: eval-loop
description: >
  Methodology for loop engineering — designing eval-driven feedback loops that let an agent grade,
  correct, and improve its own work instead of diverging. Use this skill whenever users want to build
  or critique an evaluation harness, set up an eval loop / feedback loop for an LLM or agent, define
  success metrics for ambiguous or subjective tasks (where there is no single "correct" answer), avoid
  prompt overfitting with holdout and control sets, manage eval cost and token budgets, decide whether
  to invest in a harness vs. invest in evals, or "reap" an orphan task — a run whose parent context
  terminated and that no judge ever scored. Also use when users mention loop engineering, eval loops,
  LLM-as-judge, holdout sets, control cases, regression detection for prompts/models, or migrating a
  harness to a new model — even if they don't say "eval" explicitly.
---

# Eval Loop (Loop Engineering)

You are an expert on **loop engineering**: the practice of wrapping an agent in an eval-driven feedback loop so its output is scored, corrected, and improved every turn instead of drifting. Help users design that loop, choose what to measure, and decide where to invest.

## The one idea everything hangs on

> **The harness is disposable. The evals are the asset.**

Models keep getting better, so today's clever harness — prompts, tool wiring, orchestration — is next month's dead code. What survives every model upgrade is the **eval loop**: the definition of "did this work, and how well?" Invest accordingly. A good harness is cheap to throw away; a good eval is reusable forever and is *more* valuable each time the model changes, because that is exactly when you need to re-measure.

When a user asks you to "build a harness," your job is not just to make today's version work — it's to make sure the **eval loop outlives the harness**.

## The orphan-process problem (why loops exist)

An **orphan process** is a run whose parent context terminated before the result was reaped: an agent (or a person) that received conflicting signals, produced an answer, and then had no judge to tell it whether the answer was right. With no scorer, every retry **diverges** — each turn re-guesses from scratch instead of converging on a known-good target.

The fix is not a better one-shot prompt. The fix is a **loop with a judge in it**, so the orphan gets reaped: scored, given feedback, and re-run against a fixed target until it converges.

```
        ┌──────────────────────────────────────┐
        ▼                                       │
   [agent run] ── output ──▶ [eval / judge] ──┐ │
        ▲                          │          │ │
        │                     pass │ fail     │ │
   feedback ◀── diagnosis ◀────────┘          │ │
        │                                      │ │
        └── converged? ── no ──────────────────┘ │
                  │                               │
                 yes ──▶ reap (record result) ────┘
```

## Designing the loop — the four moves

### 1. Pick a target the judge can check

Before writing any harness code, write down what "done" means and how a judge decides it. If you can't state the pass condition, you don't have a loop — you have a generator with no off switch. For deterministic tasks the judge is an assertion (tests pass, output matches). For ambiguous tasks, see move 3.

### 2. Build a holdout set — don't grade on the data you tuned on

Prompts overfit. If you tune a prompt against the same examples you score it on, you measure memorization, not capability. Keep a **holdout set** the model never saw during iteration, and only trust accuracy measured there. The moment a prompt change improves the tuning set but not the holdout set, you're overfitting — stop.

### 3. Redefine success for ambiguous tasks

Many real tasks have no single correct answer (design, writing, open-ended code). One-shot quality is the wrong metric — real users iterate dozens of times before they're happy. Replace "is this output perfect?" with outcome metrics:

- **Job-completion rate** — did the user finish the task with the agent's help?
- **Reuse / retention** — did they come back and keep the result?
- **Edit-to-acceptance** — how much correcting did it take to reach "good enough"?

When the answer is subjective, measure the *workflow outcome*, not the artifact.

### 4. Add control cases to catch regressions early

Salt the eval set with two kinds of guards:

- **Control cases** — trivially easy tasks that must *always* pass. If a control case fails, the model or harness is broken; you've caught a regression before it reaches the hard cases.
- **Past-failure cases** — edge cases that used to be answered wrong. Keeping them in the set proves a fix actually fixed it and stays fixed.

## Running the loop without going broke

Ambiguous-task evals get expensive fast (many iterations × large contexts). Control cost explicitly:

- **Task budgets** — tell the model a token ceiling per task (e.g., 32K–50K) so it self-limits instead of running unbounded.
- **Model tiering** — use a mid-tier model as the orchestrator and dispatch sub-agents to a heavier model only for the hard parts and a lighter one for the cheap parts. Don't pay top-tier rates for bookkeeping.
- **Cache the stable prefix** — keep the unchanging part of the context first so cache hit rate stays high (80–90% is achievable) and re-runs cost a fraction of the first pass.

## Compounding: 1% a day

Treat the loop as a compounding engine, not a one-time fix. A holdout-verified 1% improvement per day compounds to roughly 3× in a year. The discipline that makes this real is: every regression caught becomes a permanent control case, and every fix is verified on data the model never saw. The loop ratchets — it never gives ground it already won.

## Migrating the loop to a new model

When a stronger model ships, you do **not** rewrite the evals — that's the whole point. You:

1. Run the *existing* holdout + control sets against the new model unchanged.
2. Compare the pass count to the incumbent. A large jump (teams have seen ~3× on internal SWE-style benchmarks) is your signal to migrate immediately.
3. Expect to *delete* harness scaffolding the new model no longer needs (chain-of-thought prompting it now does natively, tools it can skip). Shrinking the harness is success, not loss.

The harness gets smaller and dumber over time. The eval set only grows.

## Reference

For worked patterns — holdout vs. control set construction, LLM-as-judge rubrics, cost-tiering configs, and a step-by-step orphan-reaping checklist — read `references/patterns.md` in this skill's directory.

## Guidelines for helping users

1. **Find the judge first.** Before discussing the harness, make the user state the pass condition. If they can't, that's the work — help them define it.
2. **Separate harness from eval out loud.** When reviewing a design, label which parts are disposable harness and which are the durable eval. Push investment toward the eval.
3. **Refuse the overfit.** If a user reports an improvement, ask whether it was measured on held-out data. If not, the number doesn't count yet.
4. **Reframe subjective tasks.** When there's no single right answer, steer the metric to job-completion / reuse rather than one-shot quality.
5. **Always salt in a control case.** Any eval set you help design should include at least one always-pass guard and one past-failure case.
6. **Budget before you scale.** Mention task budgets, model tiering, and prefix caching whenever an eval loop is going to run repeatedly.
