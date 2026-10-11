---
name: record-progress
description: "Use when work reaches a moment the shared record should show — a PR opened, review requested, a merge or deploy verified, an investigation or design reached its conclusion, follow-up work turned up — and when the user asks for the record without ending the session: \"티켓 갱신해줘\", \"노트에 남겨줘\", \"작업 기록 남겨줘\", \"KB에 정리해줘\", bare \"정리해줘\"/\"마무리해줘\", \"update the ticket\", \"log this\". Decides what goes to the tracker (state), what goes to the notes store (reasoning), and what is not recorded at all. Ending the session is end-session."
---

# Record progress

The tracker and the notes store outlive this session; the transcript does not. This skill
decides, at each moment that matters, what each of them gets. The writes themselves go through
`use-tracker` and `use-notes`.

| | Tracker (`use-tracker`) | Notes store (`use-notes`) |
|---|---|---|
| Holds | the work's **state** and its evidence: started, in review, completed, the PR link, what verified it | the **reasoning**: why this design, what was ruled out, the trap, the measured numbers, how to reproduce |
| Grouped by | parent epic, the active cycle or sprint | the store's own layout (its rules file) |

Never copy a ticket's state into a note; link the ticket. Never put the reasoning only in a
ticket comment; the note is where the next person looks for it.

## 0. Is there anything to record

Record nothing, and say so in one line, when the session only read, answered a question, or is
still investigating. Record when one of these happened:

- Code or config changed: a commit, a PR opened, updated or merged, a deploy or sync.
- A review was requested, or review comments were answered.
- An investigation or design reached a conclusion: a choice made and the options it beat.
- A ticket's state no longer matches what happened.
- Follow-up work turned up.

## 1. Moments and what each gets

| Moment | Tracker | Notes |
|---|---|---|
| PR opened | ticket to `started`, PR linked (`use-tracker` "Milestones"); no ticket for real work → file one (`write-ticket`) | — |
| Review requested | `review` with the review line | — |
| Merged, or deployed and verified | the completion comment with the evidence and `completed` when the ticket's own done holds; else one comment naming the open criterion (`use-tracker` "Reconcile") | the worklog line, if the work kept one |
| Investigation or design concluded | a comment linking the note, if a ticket exists | the conclusion: chosen, ruled out, why, the trap |
| Follow-up found | NOW only, classified below | LATER and DROP reasons, if not in the tracker |
| Session wrapping up | `use-tracker` "Reconcile" over the tickets touched | one worklog line on what this session did |

## 2. Rules

- **Evidence before state.** State comes from output read in this turn: `gh pr view`, the CI or
  deploy log, the check the ticket names. Another agent's report or a green badge is not
  evidence. No evidence → say so and leave the state where it is.
- **Do it, then report it.** These moments are the request to move a ticket, comment on it, or
  write a note; do not ask first. Ask only when the evidence points two ways, the ticket is
  someone else's (comment only), or the change touches another team's epic. Filing a new
  ticket keeps `write-ticket`'s approval rule, which the user's own instructions may waive.
- **Follow-ups against the exit condition.** Classify each one NOW / LATER / DROP (principle
  **Work to an Exit Condition**, `use-tracker` "Exit condition"): only NOW becomes a ticket,
  LATER goes on the deferred list with its revisit trigger, DROP gets its reason. Report epics
  as "N/M 완료 조건 충족", never as tickets filed.
- **Epic upkeep is part of the job** outside a program. A ticket with no parent that belongs under an epic gets
  one; an epic whose exit condition is met gets closed. List each change in the report so it
  can be undone.
- **In the background.** When the user's instructions or the config name a delegate agent for
  the tracker or the notes store (`tracker.delegate_agent`, `notes.delegate_agent`), start both
  in one message with the facts, absolute dates, `owner/repo#n` with its URL, and the commands
  and output that verified each claim. Keep working while they run.
- **Inside a program or brief,** follow what it says the worker records. A worker still makes
  its milestone moves (`deliver-ticket`), files no follow-ups (they go in its `worker_done`),
  and leaves Reconcile and epic upkeep to the coordinator.
- **Once.** When the latest comment already says it, do not post it again; `deliver-ticket`
  may have recorded the milestone already.

## 3. Report

One message after the writes land: per epic "N/M 완료 조건 충족", each ticket with its key and
transition, each note with its path and a one-line summary, LATER and DROP one line each, and
what was not recorded and why. A bare "done" fails this skill.
