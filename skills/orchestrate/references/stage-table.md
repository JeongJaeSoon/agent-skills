# Closing what is over, and the stage table

What the `brief` and `stage` skills point to. Acks and the rest of the dashboard are in [dashboard.md](dashboard.md).

## Closing what is over

An ack hides a row that is already ✅. What is over but never turns ✅ by itself would otherwise stay listed: a doing item whose card's PR merged, a card with no agent left listed under 보류 as `세션 꺼짐`, a held item whose trigger already fired, a moving row nobody has touched in half a day, an inbox item about a PR that has merged. `scripts/tidy.py` closes these on evidence, and the owner closes the rest by hand. Every close goes to `agent-skills.db` through `scripts/store.py`, with its event in the same transaction: a board item's in its `item` row, a row's, card's or inbox item's in `closure` (`{unit, state: done|reopen, ts, by: auto|owner, rule, evidence, label}`). Labels and evidence are masked, never shortened; a `--why` of more than about 4 KiB is refused, so the close can still be exported to the older format.

```sh
orch board open [--json]                     # what can be closed now, with the 확인 필요 reason where there is one
orch board done row:lead-a card:<session> b12 [--why TEXT]
orch board reopen b12 ext:<key>              # undo; the tick does not close it again on the same evidence
orch board tidy [--dry-run] [--json]         # one pass of what the collector runs after every tick
orch board closed                            # every close with its rule and evidence
```

Ids: `row:<board row>`, `card:<session id>` for a row built from a session, and `b<N>` for a doing, held, to-do or now-list item, which it keeps through rewording and moves and never gives to other work. An old `doing:`, `later:`, `do:` or `todo:` id still reaches the item it had at the import. The CLI is the record; on the Brief page every open repository table row the board can close has `Done` at its right end, whatever its status, with the same `Undo` after it, the Open items card has a `Done` button per row, card and board item, and its Closed in the last 7 days card a `Reopen` per close that can be undone, both through `POST /api/fleet/done` (same Host, Origin and token guard as acks). Only what `orch board open` lists can be closed; anything else is refused with the reason. The tick also closes `ext:<inbox key>`, without writing `inbox-external.jsonl`: `closure.ext_mark` says which of the key's lines came before the close, so a reopen shows the item again and a newer line of that key decides over both. One whose inbox line is gone has no `Reopen`, and neither does a `doing:`-style id whose item had left the board before the import. A `req:` close an older version recorded cannot be reopened; the owner-request ledger was removed.

The collector runs `tidy.run()` after each tick, so nobody has to ask. It closes only on evidence it can check, and a text names a PR only by its full `owner/repo#N` (a bare `#N` could be any repository's) and a card only by its hyphenated name standing alone in the text, one card at most:

| What | Closed when | Only marked 확인 필요 when |
|---|---|---|
| A doing item | every PR it names, or its card's PR, merged | the card still works, or its next step has a step after the merge (a release, a deploy, an apply) |
| A card row (no agent, or its session off) | its PR closed unmerged | — |
| A line of the owner's now-list (`orch board now`) | every PR it names merged or closed | — |
| A pressure queue entry that ended `gave_up` or `failed` | its row's ❔ and its line in the `reboot` check, 3 hours after it ended (`rowstate.queue_over`, with `rowstate.QUEUE_ENDED_H`, decides both): a retry under other text is another entry, so nothing else closes it. `pressure.py queue` keeps it 7 days | — |
| A to-do (`orch board todo`) | never: when its date or condition comes it moves to 지금 오너가 할 일 or 카드 없는 일 and waits for Done | — |
| An inbox item from `orch-dash inbox add`, of a type the owner does not act on | every PR it names merged or closed, or its session is gone from Orca's list | — |
| A held item | never: a fired trigger means the work is due | its trigger says merge and every PR or card it names merged; it moves from 보류 to 카드 없는 일 |
| A moving board row | never | no word for 8 hours, no PR, and no card that has one or still works |

What the owner or another person acts on is never closed by the tick: a doing item waiting on a person (`--waiting`) or the owner's, a decision, an added inbox item of type `approval`, `run_command`, `login`, `verify_failed`, `question` or `decision` (whatever its PR or session), and a row whose status is the owner's turn or another person's wait. Anything less certain stays, marked 확인 필요 at most, since closing something still open costs more than listing something finished. A decision closes when it is answered and a Needs you item backed by a live condition (a prompt, mail, a gate, a PR) leaves when the condition clears. A `run_command` item stays until resolved: nothing here can tell that a command was run.

The report reads the same file and evidence. A closed item leaves 카드 없는 일, 보류, 지금 오너가 할 일 and the To-do page; a closed row or card is ✅, so the ack and the done-row rules take it from there. A card with no agent and an open PR shows what the PR waits on instead of `⏸️ 세션 꺼짐`, by the brief's rules (`rowstate.judge`): the owner as a requested reviewer as 🙋, another person or a team as ⏳ under 다른 사람 대기, a draft nobody works on as ❔ `초안 · 세션 없음` (resume it, or write the held item that holds it), a ready PR by who merges it, and the agent's work left on the PR (CI, a conflict, a review to answer) as 🔄. A row whose open PR a held item names (as Needs you's held rank reads it) is ⏸️ unless only the owner can move it (a card stopped at a prompt stays 🙋), with one reference line under 보류 on that item's condition and the row's name. A board row about a card (its id or title is the card's name) takes the card's PR, so the card is not listed a second time, and a doing item about a card that has a row is that row's line, with the doing item's next step when the card's says only `작업 중` or `턴 종료`. The collector keeps every PR the board's rows, its open items or the inbox names in `prs.json` after the search stops finding it, so such a row shows its state instead of `?`; an item or row tidy closed no longer holds its PR there. Each close is a `tidy_done_auto` (or `tidy_done`, `tidy_reopen`) line on the timeline, naming the item, the rule and the evidence.

## The stage table

The per-item stage table (merge / dev check / prod check) that the `stage` skill prints with `orch stage show` and the `brief` skill points to in one line is a record, not something rebuilt for each report. Most of it is generated; the rest is written by hand where only a person or agent can check it.

**Generated rows.** After each collect the fleet tick rebuilds the rows of `stages-auto.json` in the state directory (`scripts/stage_auto.py`) from what is already recorded, for every program under `$PROGRAMS_HOME` whose ledger had an event in the last 7 days (`_`-prefixed directories are not programs). It rewrites the file only when its rows, order or summary changed; the collector's `memory.json` `stage_auto_at` says when it last built them:

| Row or cell | Evidence |
|---|---|
| Group row `<slug>` | the program |
| Row `<slug>/<item>` | each `predicate` item in `program.json` (`item` true) |
| Row `<slug>/<ticket>` | each task of the program's Orca Run (`run`) whose ticket is not a predicate item; the ticket is the task title's first word before `:` or a ticket-ID-shaped first word, `<ticket>-<second word>` when the ledger names that ticket (`ABC-1 K4: …` spawned as `ABC-1-K4`). A task with no ticket, or a second task of a ticket another task already holds, is `<slug>/<task id>`: two cards of one ticket are two rows. A `<ticket>-<second word>` whose second word is a word of exactly one predicate item (`K4` and `P6-K4-ready`) is that item's row, with the ledger's PRs for `<ticket>-<second word>`. A task keeps the row it got first: `stages-auto.json` stores each task's row (`programs.<slug>.cards`), so a task that leaves the Run keeps its row, and a later task of its ticket keys by its own task id instead of taking that row's id, ack and PR. The row's `title` is cut at 120 characters for the text brief; the page shows `title_full`, or a recorded title over it |
| PR | the ledger's PR for the ticket (an event with `pr` and `ticket`), a PR the collector saw the card's worker session own (the session matched to its task by task id, else by task title when no other task shares it), or a PR of the program's repository created after the program started whose head branch ends in `/<ticket>`; with several, an open one before a merged one. Cards are archived once their work lands, so the collector keeps every PR a program's ledger names, with its branch, after its search stops finding it |
| merge | ✅ with the 7-character merge sha: a ledger `landed` event's `sha` (a fold onto another PR's branch, `onto`, does not count), else the PR's merge commit in the collector's list. An open or closed PR, or none, leaves it empty |
| dev, prod | `na` (해당 없음) unless `program.json` `stages` names the column (`orch set <slug> stages dev,prod`; unset by default, since most programs have no deploy step the stage table tracks). A named column stays empty: nothing the tick reads records a release or a deploy |
| `done_at`, `stopped` | below |

A cell with no evidence stays empty; the tick never infers one. A generated row's merge cell is evidence or nothing, unlike a recorded row's (below), which says what holds the PR.

**Each row ends on its own.** A row's `done_at` is the first of these that holds:

- Every column is ✅ or 해당 없음 (recorded cells included): the latest cell's time. Without `stages`, a merge alone ends the row at its merge time. `stages.table()` stamps this on each read, for recorded rows too.
- It stopped (`stopped`, shown as `중단`), with no merge ✅ and either no PR or one the collector lists as closed (a PR whose state the tick does not have may be open, so it never stops a row): the ticket's last ledger word is `released` (with the ticket, or the dispatch id its `spawned` row noted) or `parked`, not a later `spawned`, `admitted` or `ready` (which reopens it); the event's time. Or, for a card row that is not a predicate item, its card ended without landing: Orca status `completed` or `failed`, or gone from the Run while the Run read succeeded. That time is the first tick that saw it, kept in `programs.<slug>.cards.<task>.ended`; a failed Run read keeps it, and the card read working again lifts it. A hand-written merge cell on a generated row does not lift a stop: that cell is refused (below).
- The program's current `predicate_verified` (one after its last landing, as `orch status` counts Close) ends the rows nothing else ended.

A finished row folds and leaves after 7 days.

**Recorded cells win.** `orch stage set <slug>/<item> --col dev …` writes a cell over a generated row (stored in `stages.json` as a cells-only overlay), and in the table it replaces the generated cell of that column. The `merge` column is the exception: a generated row's merge cell comes from the ledger and GitHub, so `orch stage set … --col merge` is refused for a generated row, and for a recorded row that folds into one (same id, same PR, or the item match below), and the table drops a recorded merge cell already in `stages.json` there (the file keeps it) and says how many it dropped. Only a recorded row with neither a generated row nor a PR (work that ended outside GitHub) takes a hand-written merge cell, with `--evidence`. A person or agent writes a cell only after checking what the tick cannot see (a deploy, a release on another system) or to correct it, and the tick rewrites its rows every minute, so the other way round would erase that check within a minute. An overlay whose generated row has left (its program went quiet) draws nothing. A group row takes no cells.

**One row per piece of work.** A recorded row (`orch stage add … --pr`) whose PR is a generated row's folds into that row: its title and cells over the generated ones, the rows under it moved along, and a recorded group left with no row of its own goes. So does a recorded row with no PR under a program's group whose id, past the group's, names words of exactly one predicate item (`<slug>-release` under `<slug>` folds into `<slug>/P1-release`); when no item or several match, or another recorded row already took that item, both rows stay. Writing cells on the generated id (`orch stage set <slug>/<item>`) needs no matching and is the way to record against a predicate item. The fold happens on each read, so `orch stage set` on the recorded id still works.

**One count.** The Stages page and the brief's `단계표 N행 (접음 M)` line count the same rows: the page's answer comes from `stages.counted()`, which runs the `stages.current()` the brief runs and marks each row `open`. Open rows show; finished, stopped and `board.stage_idle_d` days unchanged rows fold behind each program's `▸ N folded`.

**When the table falls behind.** On each prompt of a declared orchestrator or lead, `hooks/reorient.py` appends one line to its role reminder when an active program (the lead's own; any for the orchestrator) has no stage row or its rows were not built (the later of `generated_at` and `stage_auto_at`) in `board.stage_warn_h` hours of the dashboard config (default 6), which means the dashboard server is not running. Active: generated in the last tick, not finished and not stopped (no ledger `stop` after its last `resume`). The hook reads files only.

**Rows by hand.** Work outside any program gets rows of its own. The orchestrator or lead writes a cell at the moment it verifies it, with the evidence, and `stages.json` keeps it (`scripts/stages.py`, masked on write):

```sh
orch stage add chat --group --title "Chat revamp"                  # a heading: no PR, no cells
orch stage add body --parent chat --title "Main view" --pr acme/web#51
orch stage add search --title "Search revamp" --pr acme/web#42
orch stage add reindex --parent search --title "Reindex" --pr acme/ops#88
orch stage set search --col dev --mark ok --evidence v1.8.0         # ok needs --evidence
orch stage set search --col prod --mark checking --evidence "QA in progress" --by qa
orch stage set reindex --col prod --mark na
orch stage env --prod v1.7.2 --dev v1.8.0
orch stage next "acme/web#45 review → merge (agent, today)"
orch stage show [--program P] [--all] [--md | --telegram | --json]   # without --all, as the brief's line counts
orch stage done search    # stays marked finished for 7 days; `drop` removes a row and its children
```

A `--group` row is the feature or epic a few rows belong to. It takes no `--pr`, no `--parent` and no cells; `--parent` the rows to it. A row under a group may hold children of its own, as a top-level row does. A row without `--pr` is a full row whose PR and merge cells stay empty. Marks are `ok`, `fail`, `partial`, `checking` and `na` (or ✅ ❌ ⚠️ 🔄). The merge cell of a row with `--pr` is not written: `show` fills it from `state.json`'s PR list (merged with its date, else CI failure, changes requested, draft or waiting for review). The fleet dashboard's Stages page shows the table in full, generated rows first. It lives in the fleet state directory rather than a program's ledger because work outside a program needs rows too.
