# Dashboard: live program progress without spending tokens

`scripts/dash.py` is a local, stdlib-only page for every program under `$PROGRAMS_HOME`. It reads the same sources as `prog.py status` (the ledger, the tracker, GitHub and Orca) and spends no model tokens. Collecting data is deterministic Python. A model is involved only if you add the optional annotator described at the end.

## Start it

```sh
python3 ~/.claude/skills/orchestrate/scripts/dash.py serve [--port 4780] [--interval 60] [--host 127.0.0.1]
python3 ~/.claude/skills/orchestrate/scripts/dash.py collect <slug>   # one collect, prints a one-line summary
python3 ~/.claude/skills/orchestrate/scripts/dash.py demo [--port 4780] [--live] [--no-serve]   # fixture programs, no network
```

`serve` collects in the background at three rates:

| Source | How often |
|---|---|
| Tracker and GitHub | Every `--interval` seconds (these use the network and have rate limits) |
| Orca (`worker-list`, `task-list`, `run-show`) | Every 20 s |
| Ledger | Checked every 3 s; re-collected when it changes, and at least every 30 s |

The page polls `/api/<slug>/state` every 5 s with `If-None-Match`, so a poll with nothing new gets a 304 and no body. Keep `--host` at 127.0.0.1: the page shows PR titles and worker paths.

Each program's files are in `<program>/dashboard/`:

| File | Contents |
|---|---|
| `state.json` | The page's data, written atomically |
| `history.jsonl` | One row per change in the charted values |
| `notes.jsonl` | Notes (see below) |
| `pr_rows.json` | The last GitHub rows, so the land order can re-rank on a ledger-only refresh |
| `usage-cache.json` | Byte offsets for reading transcripts incrementally |

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

When a source is stale, the panels built from it are greyed out. Treat a grey panel as possibly wrong, not as current.

## Panels

- **Headline.** The Orca run objective, with one segment per predicate ticket underneath.
- **Overview**
  - The `next` line mirrors `prog.py status` step 4, for example "may spawn 1 more" or "SAFETY STOP: main is red".
  - KPI tiles: predicate done, main, in-flight/cap, ready PRs, landed, oldest open PR, follow-ups admitted/parked, and tokens.
  - **Land order**, from `prog.land_order`. Each row shows:
    - position, PR and ticket
    - an age badge (amber over 2 h, red over 3 h)
    - a state chip: ready, catching up, blocked or waiting
    - the first reason it is not moving
  - **Landing.** Who holds each base's lock, from `lock_acquired`/`lock_released`. If there are no lock events, it reads "no one landing".
  - **Needs attention.** A red main, PRs waiting on the human gate, idle workers (often a permission prompt), untriaged follow-ups, the latest risk note, and source errors.
  - Charts, predicate progress and recent activity.
- **Issues.** Tracker issues the program touched, with state, class and PR.
- **PRs.** Open and recent PRs, showing:
  - CI
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
dash.py note <slug> --kind risk|digest|decision --text "..." [--author name]
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
  --prompt "Read \$PROGRAMS_HOME/<slug>/dashboard/state.json only. If something needs a human (stalled land order, idle worker, stale source, main red), write at most 3 lines with: python3 ~/.claude/skills/orchestrate/scripts/dash.py note <slug> --kind risk|digest --text '...' --author annotator. Otherwise write nothing."
```

Before you run this, check `orca automations create --help`. Flags vary by Orca version.

Alternatively, a Haiku worker that the lead spawns on the same prompt does the job. Each run costs a few thousand tokens, mostly for reading `state.json`. Leave the annotator off unless someone reads the notes.
