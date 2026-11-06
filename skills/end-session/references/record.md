# Recording progress

The tracker and the notes store outlive this session; the transcript does not. This file
decides, at each moment that matters, what each of them gets. Read it and act on it at that
moment: `deliver-ticket` at each milestone, `orchestrate` on a `worker_done` and on the human's
decision, `use-notes` when an investigation or design concludes, `end-session` §3 before the
session goes, and on its own when the user asks only for the record ("정리해줘", "마무리해줘"
with no object, "작업 기록 남겨줘", "log this"): then do this file and report, and do not run
the ending steps. The writes themselves go through `use-tracker` and `use-notes`. Use only
destinations and external writes authorized by the user or the selected workflow. Without that
authorization, prepare the proposed record in the current task.

| | Tracker (`use-tracker`) | Notes store (`use-notes`) |
|---|---|---|
| Holds | the work's **state** and its evidence: started, in review, completed, the PR link, what verified it | the **reasoning**: why this design, what was ruled out, the trap, the measured numbers, how to reproduce |
| Grouped by | parent epic, the active cycle or sprint | the store's own layout (its rules file) |

Never copy a ticket's state into a note; link the ticket. Never put the reasoning only in a
ticket comment; the note is where the next person looks for it. The active tracker is the only
place the work's history goes: no issues in a second system (GitHub Issues beside Jira) for it.

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
| PR opened | ticket to `started`, PR linked (`use-tracker` "Milestones"); no ticket → propose one if the workflow needs it (`write-ticket`) | — |
| Review requested | `review` with the review line | — |
| Merged, or deployed and verified | the completion comment with the evidence and `completed` when the ticket's own done holds; else one comment naming the open criterion (`use-tracker` "Reconcile") | the worklog line, if the work kept one |
| Investigation or design concluded | a comment linking the note, if a ticket exists | the conclusion: chosen, ruled out, why, the trap |
| Follow-up found | NOW only, classified below | LATER and DROP reasons, if not in the tracker |
| Session wrapping up | `use-tracker` "Reconcile" over the tickets touched | one worklog line on what this session did |

## 2. Rules

- **Evidence before state.** Start from what happened, not from memory:
  `gh pr list --author @me --state all --limit 10 --json number,title,state,mergedAt,url` and
  `git log --oneline origin/<default branch>..HEAD` in each worktree touched. State comes from output read
  in this turn: `gh pr view`, the CI or deploy log, the check the ticket names. Another agent's
  report or a green badge is not evidence. No evidence → say so and leave the state where it is.
- **Do it, then report it.** When the selected workflow authorizes these writes, perform them without asking again. Ask only when the evidence points two ways, the ticket is
  someone else's (comment only), or the change touches another team's epic. Filing a new
  ticket follows `write-ticket` §4.
- **Follow-ups against the exit condition.** Classify each one NOW / LATER / DROP as `use-tracker`
  [references/exit-condition.md](../../use-tracker/references/exit-condition.md) says (principle
  **Work to an Exit Condition**). An epic with no exit condition gets one written first. Each
  NOW item added since the last report names the exit item it serves, or moves to LATER (the
  principle's step 5).
- **Epic upkeep is part of the job** outside a program. A ticket with no parent that belongs under an epic gets
  one; an epic whose exit condition is met gets closed, even with deferred lines left. List
  each change in the report so it can be undone.
- **In the background.** When a delegate agent writes to the tracker or the notes store
  (`use-tracker`, `use-notes`: `agent-skills:task-tracker` and `agent-skills:notes-writer` unless
  `tracker.delegate_agent`, `notes.delegate_agent` or the user's instructions say otherwise), start both
  in one message, one call each covering every ticket or note of the session, not one per
  ticket. Hand over the facts, absolute dates, `owner/repo#n` with its URL, each follow-up's
  class and the exit item it serves, and the commands and output that verified each claim; a
  claim with no evidence is handed over marked as such. Leave any footer or model attribution
  line out of the text you hand over: where the user's rules call for one, the delegate adds it,
  naming the model it runs on. Keep working while they run. A delegate
  that comes back asking for facts gets them and runs again; one that stopped on configuration
  (the notes access mode unset, the vault tools missing, a rules file unreadable) is not re-run:
  report it to the user and ask.
- **Announcing is separate.** Recording never posts to chat. Telling the team is its own
  decision, made under the user's chat rules.
- **Inside a program or brief,** follow what it says the worker records. A worker still makes
  its milestone moves (`deliver-ticket`), files no follow-ups (they go in its `worker_done`),
  and leaves Reconcile and epic upkeep to the lead.
- **Once.** When the latest comment already says it, do not post it again; `deliver-ticket`
  may have recorded the milestone already.

## 3. Report

One message after the writes land, merging the delegates' returns: per epic "N/M 완료 조건 충족"
first, each ticket with its key and transition, each note with its path and a one-line summary,
LATER and DROP one line each, and what was not recorded and why. A bare "done" fails this.

## 4. The record line

A report that follows a PR opened, a merge or a land, and every `worker_done` body, carries
one line at the start of a line, outside code blocks and quotes:

- `기록: <ticket key and transition> · <note path>`, or `기록: 없음 (<why nothing applies>)`
- `기록 못 함: <reason>` when a write could not happen: the notes store's tools are not
  connected or not authenticated, the workflow does not authorize the write, the merge is
  still running in the background. Name what the record would have said.

`Recorded:` and `Not recorded:` are the same lines in English. The plugin's
`hooks/record_check.py` reads this line, and only this line: it reminds after `gh pr merge`,
`gh pr create` and `orch land`, and at `guard.record_check: deny` holds back a `worker_done`,
a card's close or the turn's end that lacks it. It checks that the outcome was reported, not
that the record was written; the lead's reconcile checks that.
