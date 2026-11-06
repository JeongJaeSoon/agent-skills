# Moving orchestration state to Orca (2026-11-01)

This release reads a card's state from Orca instead of from lines a model wrote by hand. This page covers what moved, how sessions started before the release keep working, the switch-over order and how to undo it.

## What moved

| Before | Now |
|---|---|
| A board doing line per card, written by the orchestrator | The card's Orca worktree: `workspaceStatus`, `linkedPR`, `linkedItems`, and its Run Task when it has one. Doing lines are for waits no card covers |
| `orch board lead add` for every lead | The lead's row comes from its session. `lead add` stays for a lead the fleet cannot see, and is refused for one it can (2026-11-04) |
| `orch record spawned` for every card | The ledger reads Orca's Tasks and Dispatches. `spawned` stays for a sub-unit ticket |
| `orch sent add` for every card | The Sent table reads the Run's Tasks. `orch sent` records only paths outside Orca |
| `orch sent add` for an `orca orchestration send` or `reply` (2026-11-02) | The Sent table reads Orca's inbox, every message but heartbeats |
| Wait detection from the screen | Orca's `agentWait` first; the screen when Orca has not looked |
| Supervised cards as plain worktrees | `skills-sync card` starts a card whose `REPORT` is `run:<id>` with `worker-start` |
| Ticket and PR links in the card comment | `--reference <URL>` stored as Orca links |
| A decision record and a gate for the same question | A gate when the decision blocks a Task, `orch decide` otherwise |

Heavy work, holds, requests, the stage table, to-dos and the now-list, the brief renderer, heartbeats and the collector's side effects stay in orch.

## Running beside older sessions

- **Cards started before the release** keep running on the code they loaded. Their doing lines stay open until `orch migrate --apply` or tidy closes them. Tidy finds a card by its display name or its worktree directory, ignoring case, and also accepts Orca's merged `linkedPR` as merge evidence. The ticket ID alone and the branch name are not keys, so a follow-up item that cites its parent's ticket stays open.
- **Leads started before the release** may still run `orch sent add`. Their `orch board lead add` is refused once the board is in `agent-skills.db`; the message names the `orch board set` line that links a row instead.
- **Briefs that report to a `term_` address** are refused by the new `skills-sync card` (exit 2): `worker_done` does not reach a terminal outside a Run. Change the line to `run:<id>`, or leave it out to report in the card's own chat.
- **Orca hosts that do not report `agentWait`** fall back to reading the screen, as before.
- Old and new code read and write the same files. `orch migrate` adds only ordinary tidy lines, so older code reads them too.

## Switch-over order

Do this when no merge or card start is in flight. Nothing here touches a running card.

1. Fast-forward the loaded main checkout to the release (see [platform.md](platform.md), Korean).
2. In the orchestrator, then in each lead, at a turn boundary: `/reload-plugins`. Running cards need nothing.
3. In the orchestrator: `orch doctor`. Only if it says this terminal is bound to no Run (`이 터미널은 Run에 묶여 있지 않다`), `orca orchestration run-use --id <id>` with the Run that earlier briefs report to. Create a Run only when there is none. Reading `orca orchestration run-current --json` yourself: `result.runId` or `result.run.id` set means bound.
4. `orch migrate` (dry-run). Check the first list: doing lines that name exactly one card. Lines waiting on a person, the owner's, ones the owner reopened, and ones whose card merged but which name a step left (a release, a deploy) are not in it; the last are listed apart and stay.
5. `orch migrate --apply`. Note the backup directory and the `orch board reopen` line it prints.
6. Check the dashboard: the Sessions table shows the Ticket and PR columns from Orca, and the board's open doing list is the waits that remain.

## Undoing it

- **State:** `orch migrate --restore <backup dir>` reopens what that run closed and leaves anything the owner changed since. With older code loaded, run the `orch board reopen` line `--apply` printed instead. The backup holds a copy of `agent-skills.db` from before the run as a 0600 file.
- **Code:** check out the previous release tag in the loaded checkout and `/reload-plugins` again.
- **Orca values:** a card sets its own `workspaceStatus` (`in-review` once its PR is open, `completed` when it closes), and links go only on cards the new code starts. To put a status back, `orca worktree set --worktree <sel> --workspace-status in-progress`.

# Decisions move into agent-skills.db (2026-11-04)

From this release `orch decide` and the dashboard's answers keep decisions in `agent-skills.db`, a SQLite file in the fleet state directory (`$ORCH_FLEET_STATE`), instead of `decisions.json`. The next section moves the board and tidy files into the same file.

## What changes

- Closed decisions are no longer deleted after 7 days. They stay in the table and every reader filters on their status.
- Each change to a decision writes its before and after into the `event` table in the same transaction.
- Only `orch db import` creates the file. Until it has run, `orch decide` stops with a pointer to `orch db cutover-plan 4a`, the decision hook does nothing, and the brief's headline shows `이행 대기: 결정 DB`.
- `orch db dump [table]` prints the tables as JSON. Open the file yourself only with `sqlite3 -readonly`.
- Copy the database with `orch db backup <file>`, never with `cp`: a copy of the file without its `-wal` can lose the latest writes. Keep the state directory on a local disk, not in a synced folder.

## Switch-over order

Older code still writes `decisions.json`, so it must not run while the data moves. `orch db cutover-plan 4a` prints these steps as the commands to run on your machine; it runs none of them.

1. Stop the dashboard server (it relays answers from the page). On macOS that is `launchctl bootout` of its LaunchAgent.
2. Fast-forward the loaded main checkout.
3. End every live Claude session and start a new process (neither `/reload-plugins` nor `/restart` passes the check: they keep the pid and start time), because each one runs the decision hook it loaded.
4. `orch db import --dry-run` shows how many decisions move and whether `--apply` would refuse.
5. `orch db import --apply`. It refuses while a process runs the scripts, wrappers or hooks from another checkout or from before the fast-forward, while a live session in any Claude home started before it, while the plugin loads a copy at another commit or a session loads another checkout with `--plugin-dir`, while a LaunchAgent is still loaded (even without a pid) that is the dashboard's under this shell's label or is any agent in `~/Library/LaunchAgents` running an agent-skills script, wrapper or hook, or while any uncommented crontab line runs one of them, even from the updated checkout (comment it out for the cutover). When it cannot tell, it refuses too: `ps` or `crontab -l` fails (a user with no crontab, or no `crontab` command, is fine), or a process's directory or the checkout's update time cannot be read. Then it takes the board, tidy and decision locks, copies `decisions.json` to `backup-db-<time>/` (0700, files 0600) with its sha256, builds the database under a temporary name, checks it, renames it into place, and leaves an empty `decisions.json` directory in its place so older code fails to write there.
6. Start the dashboard server again (`orch-dash install-agent` on macOS).

Running `--apply` again is safe. If the import committed but the empty directory was not laid yet, the re-run lays it only when `decisions.json` still has the sha256 it had at import; if the file changed in between, it stops and names the conflict, and nothing is replaced. If a file appears where the empty directory was after the switch, the brief shows `옛 코드가 쓴 흔적`.

## Undoing it

1. Stop the writers as in step 1, then `orch db backup <file>`. The backup keeps everything, including the event history the old format has no place for.
2. `orch db export --legacy --target pre-4a <dir>` writes the `decisions.json` older code reads, and reports whether the database's write-ahead log was checkpointed. If it reports busy, a connection is still open: stop it first.
3. Move `agent-skills.db` together with its `-wal` and `-shm` out of the state directory, remove the empty `decisions.json` directory, and put `<dir>/decisions.json` in its place.
4. Revert to the previous release, fast-forward, and start the writers again.

To move the state to another computer, `orch db backup <file>` there and `orch db restore <file>` on the new one. `restore` runs the same checks as `--apply`, and refuses while a `decisions.json`, `board.json` or `tidy.jsonl` file sits in the new state directory.

# The board moves into agent-skills.db (2026-11-04)

From this release the board, the to-do and held lists, leads and every close live in `agent-skills.db` too, instead of `board.json` and `tidy.jsonl`. A machine that has not moved decisions yet does both steps in one run.

## What changes

- Every doing, held, to-do and now-list item has a stable id `b<N>`. It stays through rewording and moves, and no other work ever gets it. Old `doing:`, `later:`, `do:` and `todo:` ids keep reaching the item they named at the import.
- Removing an item from a list keeps its row as `dropped`, with its last value in `event`.
- `board.prev.json` and `tidy.jsonl.1` are gone; each change's before and after is in `event`. The import moves the two files into its backup.
- `orch board doing add` is refused for a title that names a live card or subagent, since those have their own rows; `--force --why <reason>` adds it anyway and keeps the reason in the event.
- `orch board set --pr` is refused on a row linked to a card, as is `--worktree` to another card. The row keeps its link and shows Orca's PR. An unlinked row may link once with `--worktree`.
- `orch board lead add` is refused for a lead Orca lists. `orch board set <lead> --kind lead --repo <repo> --worktree <worktree>` links a row to it. `lead add` stays for a lead Orca cannot see.
- Closing an inbox item no longer writes `inbox-external.jsonl`. The closure records which line it saw, so a new line of that key decides afterwards, and a reopen needs no new line. If the file was rewritten and the line is gone, the last line decides and the brief's headline says `인박스 기록 어긋남`.
- Until `orch db import` has run, board commands stop with a pointer to `orch db migrate` (the main checkout's absolute path), `orch board open` prints the same line on stderr, and the brief's headline shows `이행 대기: 보드·결정 DB` with every row at ❔.

## One command (`orch db migrate`)

The owner types the main checkout's `bin/orch db migrate` in a plain terminal (the board commands and `orch db cutover-plan 4b` print the absolute path). It refuses inside a Claude session: stopping and starting services is the owner's act. What it does:

1. Already migrated: it says "already migrated" and changes nothing, however many times it runs. If older code has put a legacy file back after the cutover, it says that and stops.
2. It stops what writes the state: the dashboard LaunchAgent, agent-skills-sync, a dashboard server started by `ensure`, and the janitor and watch-prs automations. Before the first stop it writes what to put back into `cutover-recover.json`. It reads `ORCH_*` and `PROGRAMS_HOME` from a LaunchAgent plist (never writes the plist) and uses them for the run when this shell does not set them; a value that differs from the shell's is refused.
3. If a reason to refuse remains (a session, process or cron line still running older code) it prints a table, imports nothing and puts back what it stopped. A session does not pass with `/reload-plugins` or `/restart`; only one that ended and started again after the update does.
4. It runs the same import as `orch db import`, then checks the `migration` steps and `orch board show`. (`orch board open` exits cleanly before the cutover, so it proves nothing.)
5. When it ends, fails or is killed, it puts back what it stopped: each LaunchAgent is loaded again from its existing plist with `launchctl bootstrap` (`install-agent` would rewrite the plist), and an automation that was off stays off. A step that could not be put back stays in `cutover-recover.json` and the next run does it first.
6. While it runs, `cutover.lock` is held, so a restarted session's `orch wait` or `ensure` cannot bring the dashboard server back.

`orch db migrate --check` lists what it would stop and what stands in the way, and writes nothing. Another PC that pulls main (agent-skills-sync does within 15 minutes) gets this one line from the board commands and the brief. A new PC needs no cutover: `skills-sync setup` runs `orch db init`, which creates an empty database with both steps done, and does nothing on a PC that already has state.

## Switch-over order by hand

`orch db cutover-plan 4b` prints these steps as the commands to run on your machine; it runs none of them. The one command above replaces this order.

1. Stop the dashboard server and turn the janitor off: `launchctl bootout` of the LaunchAgents on macOS, and `skills-sync setup disable janitor`.
2. Fast-forward the loaded main checkout.
3. End every live Claude session and start a new process (neither `/reload-plugins` nor `/restart` passes the check).
4. `orch db import --dry-run` shows what moves: items per list, closures, rows and leads, which leads and row PRs leave because Orca holds them, and whether `--apply` would refuse.
5. `orch db import --apply`, which runs the same checks as the decisions step. The board's backup goes to `backup-db-*`, with the sha256 of each file in the `migration` table, and an empty directory replaces `board.json` and `tidy.jsonl`, so code older than the decisions release fails to write. Code from the decisions release stops on the `4b-board` row.
6. Start the dashboard server and the janitor again.

Running `--apply` again is safe, as it is for decisions: the tombstone is laid only when each file still has its sha256 from the import, and a changed file stops with the conflict named. A failed import changes nothing: the whole step is one transaction.

## Undoing it

1. Stop the writers as in step 1, then `orch db backup <file>`.
2. `orch db export --legacy --target 4a <dir>` writes `board.json`, `tidy.jsonl`, an `inbox-external.jsonl` with the closures written out as lines, and `export.json`. Same words in two states have no old form: the export names them and stops, so close or reword one first.
3. `orch db rollback 4b <dir>` checks that the DB and the inbox have not changed since the export, puts the three files in place of the tombstones, and takes the `4b-board` row out last. A crash leaves it to run again.
4. Revert to the previous release, fast-forward, and start the writers again.

A later import takes the files back in. The tables are replaced, and numbering carries on from where it was: a `b<N>` from before the rollback is never reused. To go back past the decisions step as well, finish this first, then follow "Undoing it" for decisions.

# Needs you and Sent read their sources (2026-11-06)

## What changes

- Needs you reads `pressure-queue.json` and `patrol.jsonl` itself. The held commands that wait or retry are one item ("기계 대기 N건"); one that gave up or failed, and each open patrol finding, is an item of its own. Neither writes to `inbox-external.jsonl` any more. Dismissing and closing work per queue id and per finding (`patrol:<id>:<detected_at>`), as before.
- A line typed from the dashboard is a row of the `orch sent` log (`kind: dashboard`). Sent shows the log and Orca's own records. A send found only in a live session's transcript is not a row: a warning counts it ("N sends match no log or Orca row").
- PR events are written to `events.jsonl` only (`kind: pr_*`, with the owner, checks and head `pr-events.jsonl` carried).

## Switch-over order

1. Fast-forward the loaded checkout. The dashboard restarts itself; new code needs no state change to start.
2. `orch-dash migrate-inputs` prints what it would do and writes nothing. Check the counts.
3. `orch-dash migrate-inputs --apply` appends a `resolve` line (reason `migrated`) after each open patrol and pressure-queue line in `inbox-external.jsonl`, copies the typed lines of `sends.jsonl` into the sent log and keeps that file read-only as `sends.archive.jsonl`, and makes `pr-events.jsonl` read-only. It deletes nothing, and running it again finds nothing left.

Until step 3 the old lines are already hidden, and the sent log lacks the lines typed before the move.

## Undoing it

Revert to the previous release and fast-forward. The old code adds its own pressure and patrol lines again on its next round. To read the archived files again, make them writable (`chmod u+w`) and move `sends.archive.jsonl` back to `sends.jsonl`.
