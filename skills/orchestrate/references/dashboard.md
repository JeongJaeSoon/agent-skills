# Dashboard: live program progress without spending tokens

`scripts/dash.py` is a local, stdlib-only page for every program under `$PROGRAMS_HOME`. It reads the same sources as `orch status` (the ledger, the tracker, GitHub and Orca) and spends no model tokens. Collecting data is deterministic Python. A model is involved only if you add the optional annotator described at the end.

## Start it

`orch init` and `orch status` run `orch-dash ensure`, so a coordinator never starts it by hand. `ensure` asks `/api/health` on the port: this store's server running this code answers and nothing happens; one running older code (a plugin update) is stopped through the pid it reported and started again; a port held by another store or another program is reported, never killed. `serve` holds a lock on `<store>/.dash.lock`, so a second server on the same store exits; its log is `<store>/.dash.log`.

```sh
orch-dash ensure [--port 4780]
orch-dash serve [--port 4780] [--interval 60] [--host 127.0.0.1]
orch-dash collect <slug>   # one collect, prints a one-line summary
orch-dash demo [--port 4780] [--live] [--no-serve]   # fixture programs, no network
```

`serve` collects in the background at three rates:

| Source | How often |
|---|---|
| Tracker and GitHub | Every `--interval` seconds (these use the network and have rate limits) |
| Stages (tracker tree) | Every `--interval` seconds, at most 24 tracker calls each time |
| Orca (`worker-list`, `task-list`, `run-show`) | Every 20 s |
| Ledger | Checked every 3 s; re-collected when it changes, and at least every 30 s |

A closed program (its `predicate_verified` is current) keeps only the ledger check. Its tracker, GitHub and Orca sections stay as last collected, and a landing, a predicate edit or an admitted follow-up after the check reopens it.

The page polls `/api/<slug>/state` every 5 s with `If-None-Match`, so a poll with nothing new gets a 304 and no body. Keep `--host` at 127.0.0.1: the page shows PR titles and worker paths.

Each program's files are in `<program>/dashboard/`:

| File | Contents |
|---|---|
| `state.json` | The page's data, written atomically |
| `history.jsonl` | One row per change in the charted values; rebuilt from the ledger once `orch backfill` puts landings before its first row |
| `notes.jsonl` | Notes (see below) |
| `pr_rows.json` | The last GitHub rows, so the land order can re-rank on a ledger-only refresh |
| `usage-cache.json` | Byte offsets for reading transcripts incrementally, and each session's last state |
| `tree.json` | The tracker's parent/child tree as crawled so far (the stage bars) |

### When a source fails

A failing source never blanks the page:

- Its error is shown.
- The last good section stays on screen.
- Its timestamp stays at the time of its last success.

## Freshness

The bar under the headline shows each source's last success, both as a local time and as a relative time that ticks every second.

| Source | Warn after | Stale after |
|---|---|---|
| Ledger, Orca | 60 s | 180 s |
| Tracker, GitHub | 3 × interval | 6 × interval |

When a source is stale, the panels built from it are greyed out. Treat a grey panel as possibly wrong, not as current. A closed program's unpolled sources are not greyed out.

Every table sorts by a column when its head is clicked. A relative-time column sorts by how long ago, so ascending is newest first. The second click reverses, the third returns to the view's own order, and the browser remembers the choice.

## Panels

- **Headline.** The Orca run objective, with one segment per predicate ticket underneath.
- **Overview**
  - The `next` line mirrors `orch status` step 4, for example "may spawn 1 more" or "SAFETY STOP: main is red".
  - KPI tiles: predicate done (with what the last open item waits on, and the current scope: predicate items plus admitted follow-ups, done out of total, and how many untriaged follow-ups may still join; a count, not a percentage, because the scope can grow), main (with the commit and, when the ledger note names one, its CI run), in-flight/cap with free slots, oldest open PR, PRs awaiting landing, landed in 24 h; human wait only under human-gate.
  - **Now.** Every in-flight worker and what its session is doing, from the tail of its transcript: a tool call (with its description or command), thinking, a finished turn waiting for input, or waiting for a wake-up it armed itself (ScheduleWakeup, Monitor, a background command). A pulse marks the ones moving; a row flashes once when its state changes. Codex workers leave no transcript here and show Orca's activity only.
  - **Stages.** Each top-level issue of the tracker project that has children, with its leaf tickets done (green) and in progress (moving stripes). Linear's list carries no parent links, so the tree is crawled with `tracker.py children` a few nodes per collect and cached in `tree.json`: a new program shows "counting N" for a few minutes. Canceled leaves are left out.
  - **Land order**, from `prog.land_order`, shown only while a PR waits to land or a base's exclusive lane is held. Each row shows:
    - position, PR and ticket
    - an age badge (amber over 2 h, red over 3 h)
    - a state chip: ready, catching up, blocked or waiting
    - the first reason it is not moving
  - **Landing.** Who holds each base's lock, from `lock_acquired`/`lock_released`. If there are no lock events, it reads "no one landing".
  - **Needs attention.** A red main, PRs waiting on the human gate, a worker on a permission prompt, stalled workers (turn ended 15+ min with its task open, nothing written 10+ min after dispatch, one tool call over 45 min), free slots with ready tasks (only when `next` says "may spawn"), landed cards still open, ledger gaps (running dispatches never recorded, merged PRs the ledger never saw, landings without a main CI result), untriaged open follow-ups, the latest risk note, and source errors. `orch status` prints the same stalls, slots and gaps.
  - Burn-up, predicate progress and recent activity. The in-flight chart is under Workers and the derived-vs-done chart under Issues. The charts run from the program's `created_at`, or from 30 days back in a longer program; each line starts at the left edge with the values then in force. The in-flight chart starts at the first live Orca sample: backfilled rows carry none.
- **Issues.** Tracker issues the program touched, with state, class and PR.
- **PRs.** Open and recent PRs, showing:
  - CI. Closed and merged PRs come without checks and reviews (with them, a 200-PR window timed out); they keep what a collect saw while they were open, or read "not fetched"
  - the review verdict against the current head
  - review rounds
  - merge state
  - the PR's position in the land order
- **Tasks.** The Orca task DAG, with `dep` ledger edges added.
  - Node colours show status: done, landing, blocked, in progress, waiting.
  - The critical path is the longest unfinished chain.
  - On narrow screens the DAG becomes a list.
- **Workers.** Orca workers, showing:
  - stage, liveness and model
  - worktree
  - tokens (Claude only)
- **Activity.** Ledger events and notes, newest first. This includes `dep`, `land_check`, `lock_*`, `yield` and `reprioritized`.

### Tokens

Tokens are read from Claude Code transcripts in `~/.claude/projects/<worktree path>/*.jsonl`. Rows are deduplicated by `message.id`, and each run reads only the bytes appended since the last one.

Codex workers show `n/a`. Subagent transcripts in subdirectories are not counted.

## Notes

A note is a short line of human or model judgement that the collectors can't compute. The newest risk note also appears under Needs attention.

```sh
orch-dash note <slug> --kind risk|digest|decision --text "..." [--author name]
```

Notes are appended to `notes.jsonl`, capped at 500 characters, and appear from the next collect, which happens within 3 s while `serve` runs. Use them for:

- **risk:** something the numbers don't show, for example "ACME-107 copy still unapproved".
- **digest:** a one-line summary of the last hour.
- **decision:** a call you made and why.

## Optional: a cheap annotator

The dashboard is complete without one. If you want a periodic digest, schedule a small agent. It should read only `state.json` and write at most three notes:

```sh
orca automations create --name "dash notes: <slug>" --trigger "*/15 * * * *" --provider claude \
  --workspace <selector> --workspace-mode existing \
  --prompt "Read \$PROGRAMS_HOME/<slug>/dashboard/state.json only. If something needs a human (stalled land order, idle worker, stale source, main red), write at most 3 lines with: orch-dash note <slug> --kind risk|digest --text '...' --author annotator. Otherwise write nothing."
```

Before you run this, check `orca automations create --help`. Flags vary by Orca version.

Alternatively, a Haiku worker that the lead spawns on the same prompt does the job. Each run costs a few thousand tokens, mostly for reading `state.json`. Leave the annotator off unless someone reads the notes.
