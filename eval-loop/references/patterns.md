# Eval Loop — Worked Patterns

Detailed patterns referenced by `SKILL.md`. Read the relevant section when a user needs concrete construction details rather than the high-level method.

---

## 1. Holdout set construction

Goal: measure capability, not memorization.

**Split discipline**
- Partition examples into a **tuning set** (you may inspect, iterate, and overfit against it) and a **holdout set** (the model and the prompt author never see it during iteration).
- Keep the holdout set sealed. The moment you read a holdout example to debug a prompt, it's tuning data — retire it and cut a fresh holdout.
- Size the holdout to be representative, not large. 20–50 diverse, well-labeled cases beat 1,000 near-duplicates.

**Reading the signal**
| Tuning ↑, Holdout ↑ | Real improvement — keep it |
| Tuning ↑, Holdout flat/↓ | Overfitting — revert the change |
| Tuning flat, Holdout ↑ | Lucky/noisy — re-run to confirm |
| Both ↓ | Regression — bisect recent changes |

**Refreshing**
- After a fix ships, fold the holdout into the tuning pool and cut a new holdout from fresh real-world data. This keeps the "unseen" guarantee honest over time.

---

## 2. Control cases and past-failure cases

Two guard types belong in every eval set.

**Control cases (always-pass)**
- Trivially easy tasks the system must never fail (e.g., "echo back this string", "2+2", a hello-world build).
- Purpose: detect that the *model or harness itself* broke, before you waste a run on the hard cases.
- If a control case ever fails, stop the loop and fix the infrastructure — do not trust any other number from that run.

**Past-failure cases (regression anchors)**
- Every time the system gets something wrong and you fix it, add that exact case to the eval set permanently.
- Purpose: prove the fix worked, and prove it stays fixed across future model/harness changes.
- This is the ratchet that makes 1%/day compounding real: won ground is never given back.

---

## 3. LLM-as-judge rubric

When the target is ambiguous and no assertion can score it, use a model as the judge — but constrain it.

**Make the rubric concrete and discrete**
- Replace "rate quality 1–10" (high variance) with explicit pass/fail criteria the judge checks one at a time, e.g.:
  - [ ] Does the output address every requirement in the prompt?
  - [ ] Are there factual errors contradicting the source?
  - [ ] Would the stated user accept this without major edits?
- Aggregate the checkboxes into a verdict deterministically; don't ask the judge for an overall vibe.

**Reduce judge bias**
- Hide which system produced the output (don't tell the judge "this is the new prompt").
- For A/B comparisons, randomize order and run both orderings to cancel position bias.
- Spot-check the judge against human labels periodically; a drifting judge silently corrupts every downstream number.

**Anchor on outcomes, not artifacts**
- Where possible, prefer measuring job-completion / reuse / edit-to-acceptance (real signal) over judge opinion (proxy signal). Use the judge only when real outcome data isn't available.

---

## 4. Cost-tiering configuration

A reference shape for keeping a repeated eval loop affordable.

```
orchestrator: mid-tier model        # plans, routes, bookkeeping
  ├─ sub-agent (hard subtask) → heavy model   # only where capability is needed
  ├─ sub-agent (bulk subtask) → light model   # cheap, parallelizable work
  └─ judge → mid-tier model with fixed rubric

per-task token budget: 32K–50K       # model self-limits instead of running unbounded
context layout: [stable prefix | volatile suffix]   # maximizes cache hit rate
target cache hit rate: 80–90%        # re-runs cost a fraction of the first pass
```

Principles:
- Don't pay top-tier rates for orchestration and bookkeeping.
- Parallelize independent sub-agents instead of running one long serial chain.
- Put the unchanging context first so the cache absorbs it across runs.

---

## 5. Orphan-reaping checklist

Use this when a task is "orphaned" — it ran, produced an answer, and no judge ever scored it, so retries keep diverging.

1. **Name the conflicting signals.** Write down what the run was reacting to (ambiguous prompt, multiple plausible interpretations). Divergence usually traces to an undefined target.
2. **Define the pass condition.** State the single target the answer must converge on. If you can't, the task is under-specified — fix the spec before looping.
3. **Attach a judge.** Pick the cheapest judge that can check the pass condition (assertion > rubric-driven LLM-as-judge > human).
4. **Add a control case.** Confirm the loop machinery works on a trivial input before trusting it on the hard one.
5. **Run the loop to convergence.** Score → diagnose → feed back → re-run against the *fixed* target (not a fresh guess each time).
6. **Reap and record.** On convergence, record the result and promote the original failure into a permanent past-failure case so the orphan can never recur.

---

## 6. Harness lifecycle (disposable by design)

- **Build the harness to be deleted.** Keep prompts, tool definitions, and orchestration in clearly separable modules so scaffolding can be removed when the model no longer needs it.
- **On every model upgrade:** run the unchanged eval set first, then *subtract* harness the new model made redundant (native reasoning replacing CoT prompting, fewer tools, smaller system prompt).
- **Track the trend:** a healthy system's harness shrinks over time while its eval set grows. If the harness is growing faster than the evals, you're investing in the disposable thing.
