# Moving orchestration state to Orca (2026-11-01)

This release reads a card's state from Orca instead of from lines a model wrote by hand. This page covers what moved, how sessions started before the release keep working, the switch-over order and how to undo it.

## What moved

| Before | Now |
|---|---|
| A board doing line per card, written by the orchestrator | The card's Orca worktree: `workspaceStatus`, `linkedPR`, `linkedItems`, and its Run Task when it has one. Doing lines are for waits no card covers |
| `orch board lead add` for every lead | The lead's row comes from its session. `lead add` stays for a lead the fleet cannot see |
| `orch record spawned` for every card | The ledger reads Orca's Tasks and Dispatches. `spawned` stays for a sub-unit ticket |
| `orch sent add` for every card | The Sent table reads the Run's Tasks. `orch sent` records only paths outside Orca |
| Wait detection from the screen | Orca's `agentWait` first; the screen when Orca has not looked |
| Supervised cards as plain worktrees | `skills-sync card` starts a card whose `REPORT` is `run:<id>` with `worker-start` |
| Ticket and PR links in the card comment | `--reference <URL>` stored as Orca links |
| A decision record and a gate for the same question | A gate when the decision blocks a Task, `orch decide` otherwise |

Heavy work, holds, requests, the stage table, to-dos and the now-list, the brief renderer, heartbeats and the collector's side effects stay in orch.

## Running beside older sessions

- **Cards started before the release** keep running on the code they loaded. Their doing lines stay open until `orch migrate --apply` or tidy closes them. Tidy finds a card by its display name or its worktree directory, ignoring case, and also accepts Orca's merged `linkedPR` as merge evidence. The ticket ID alone and the branch name are not keys, so a follow-up item that cites its parent's ticket stays open.
- **Leads started before the release** may still run `orch board lead add` and `orch sent add`. The fleet already merges a board lead with its session by terminal handle, so the lead shows once.
- **Briefs that report to a `term_` address** are refused by the new `skills-sync card` (exit 2): `worker_done` does not reach a terminal outside a Run. Change the line to `run:<id>`, or leave it out to report in the card's own chat.
- **Orca hosts that do not report `agentWait`** fall back to reading the screen, as before.
- Old and new code read and write the same files. `orch migrate` adds only ordinary tidy lines, so older code reads them too.

## Switch-over order

Do this when no merge or card start is in flight. Nothing here touches a running card.

1. Fast-forward the loaded main checkout to the release (see [platform.md](platform.md), Korean).
2. In the orchestrator, then in each lead, at a turn boundary: `/reload-plugins`. Running cards need nothing.
3. In the orchestrator: `orca orchestration run-current --json`. If it is null, `orca orchestration run-use <id>` with the Run that earlier briefs report to. Create a Run only when there is none.
4. `orch migrate` (dry-run). Check the first list: doing lines that name exactly one card. Lines waiting on a person, the owner's, ones the owner reopened, and ones whose card merged but which name a step left (a release, a deploy) are not in it; the last are listed apart and stay.
5. `orch migrate --apply`. Note the backup directory and the `orch board reopen` line it prints.
6. Check the dashboard: the Sessions table shows the Ticket and PR columns from Orca, and the board's open doing list is the waits that remain.

## Undoing it

- **State:** `orch migrate --restore <backup dir>` reopens what that run closed and leaves anything the owner changed since. With older code loaded, run the `orch board reopen` line `--apply` printed instead. The backup holds the `board.json`, `tidy.jsonl` and `decisions.json` from before, as 0600 files.
- **Code:** check out the previous release tag in the loaded checkout and `/reload-plugins` again.
- **Orca values:** a card sets its own `workspaceStatus` (`in-review` once its PR is open, `completed` when it closes), and links go only on cards the new code starts. To put a status back, `orca worktree set --worktree <sel> --workspace-status in-progress`.
