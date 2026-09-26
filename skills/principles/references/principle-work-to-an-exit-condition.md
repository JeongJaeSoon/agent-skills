---
name: principle-work-to-an-exit-condition
description: "Apply before starting or growing a body of work (an epic, a program, a batch of tickets), whenever work turns up a follow-up, and at every status report or wrap-up. Write down what done means first, file a ticket only for what that needs, and report progress against it."
disable-model-invocation: true
---

# Work to an Exit Condition

A body of work ends when its exit condition holds, not when the ticket list runs dry. Write the exit condition before the work starts, measure every discovered follow-up against it, and close the work when it holds.

**Why:** Every piece of work turns up more work. When each finding becomes a ticket, the epic grows as fast as it is worked, nothing closes, and "12 tickets created" reads like progress while the finish line moves away. A written exit condition gives each finding a question with an answer: does done need this?

**Pattern:**

1. **Write the exit condition first.** Before starting or growing an epic, program or batch, write:
   - **Exit items:** a numbered list of what done means, each checkable against evidence (a merged PR, a command's output, a ticket in `completed`). "Works well" is not checkable.
   - **Out of scope:** what this work will not do, even if it comes up. Write "none" rather than leaving it blank.
   - **Deferred:** empty at first. LATER items go here.

   Where these live: `use-tracker`, "Exit condition". Inside a program, the predicate is the exit items (`orchestrate` Frame).

2. **Classify every follow-up now.** At the moment a finding turns up, give it one class:

   | Class | When | What happens |
   |---|---|---|
   | NOW | An exit item cannot be met without it, or it is a reproduced correctness, security or data defect in what this work shipped | File a ticket (or fix it in the current diff if it fits there). Name the exit item it serves |
   | LATER | Real and worth doing, but done holds without it | One line on the deferred list: what, and the revisit trigger (the event that makes it worth doing). No ticket |
   | DROP | Speculative, not reproduced, already covered, or out of scope | One line with the reason, in the report or worklog. No ticket |

   Only NOW files a ticket; the deferred list is the record that keeps a LATER finding. A revisit trigger is an event, not a date: "when a second caller needs it", "if the nightly job fails again".

3. **Work NOW in dependency order, then close.** Finish each NOW item before starting ones that depend on it. When every exit item is met, close the epic or program, even if the deferred list is long. The deferred list stays with the closed epic; a new body of work starts from it with its own exit condition.

4. **Report against the exit condition.** Progress is "N of M exit items done", with the item that is still open named. Never report "tickets created" or "tickets closed" as progress.

5. **Re-check at every status and wrap-up.** Compare the exit items and the NOW set with the last report. For each NOW item added since, name the exit item it serves; if you cannot, move it to LATER or DROP. An exit item added since the start needs the human's words behind it, quoted in the report; otherwise it is LATER.

**Example:** exit condition "1. export runs nightly without manual steps; 2. failures page the on-call". A finding "the CSV header is misspelled" is DROP if no consumer reads the header, NOW if exit item 1's consumer parses it. "Add a retry dashboard" is LATER, trigger "first repeated failure after launch".

**Anti-patterns:**
- Filing a ticket for every finding "so it is not lost" (the deferred list is the record)
- Growing the exit items to fit the work already done or found
- Keeping the epic open because the deferred list is not empty
- Reporting activity (tickets filed, PRs opened) instead of exit items met
