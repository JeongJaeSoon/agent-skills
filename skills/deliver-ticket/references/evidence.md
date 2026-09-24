# Evidence procedures

The rules in §2 decide what ships. This file holds the procedures behind them, by kind of work.

## Bug fix

1. Reproduce it yourself on the surface the user saw it on: Aside for anything in a browser,
   the CLI or `curl` for a backend. Ask the user only for access you cannot get, after driving
   the surface as far as it goes. If it will not reproduce directly, synthesize the trigger,
   tighten the conditions, or add logging until it fires.
2. Binary-search the cause. List the candidate hypotheses, seeded with `how` over the affected
   subsystem and `why` for the regression history. Each pass, take the split that rules out the
   most, get runtime evidence, and eliminate. When program state is unclear, add logging and
   read it as the code runs; do not guess. Confirm the surviving mechanism with runtime evidence
   before planning the fix.
3. Plan the fix. If it crosses a function boundary, sketch it with `architect` first. The
   smallest change the evidence justifies ships; a guard that "might help" does not.
4. Write the failing test that reproduces the bug (`tdd`), watch it fail, then fix. When a local
   test would be expensive or unclear, the step-1 reproduction is the proof instead.
5. Verify on the same surface. The original reproduction now passes. A unit test shows branch
   behavior, not that the bug is gone.
6. Commit the failing test before the fix, so the history shows red then green (§4).

Report what was broken, the root cause, the fix, and the failing-then-passing output verbatim.

## Refactor

The structure changes; the behavior does not. A cleanup that turns up a real bug or a missing
feature splits: ship the structural change first against the pinned behavior, then the fix or
feature as its own change.

1. Pin the behavior before any structure moves. Learn the contract with `how`, then write a
   characterization test, a snapshot, or an old-vs-new output diff. Type check and lint are not
   a pin.
2. Name the target shape: the module layout, types and call graph you would build today. If it
   crosses a function boundary, sketch it with `architect`. The reshape must delete branches or
   invalid states, not add indirection.
3. Subtract before you add: delete dead code, one-caller wrappers and redundant validators
   before the new shape goes in.
4. Move in small steps that keep the pin green. For an API reshape, migrate every caller and
   delete the old API in the same change: no compatibility shim, no parallel old and new paths.
   Check every rename against the files; renames miss usages in strings, docs and
   back-references.
5. Prove behavior is unchanged on the real artifact, not "it compiles". For a large reshape, run
   the old-vs-new diff or replay a recorded baseline.
6. Keep it only if it lowers reader load somewhere. Otherwise revert it.
7. Commit in order: the subtraction, the reshape, then any follow-on cleanup (§4).

Report the structure that changed, the pin, the equivalence proof, and what was reverted.

## A fix that already exists

If an open PR or a merged commit already claims the fix, verify it instead of writing a
competing one. Run the reported path on the baseline (the PR's base, or the commit before the
fix) and on the patched build, twice each with the same data. The fix holds only when the
baseline shows the symptom both times and the patch neither time. Otherwise report which half
failed or could not run.
