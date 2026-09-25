# Dashboard: live program progress without spending tokens

`scripts/dash.py` is a local, stdlib-only page for every program under `$PROGRAMS_HOME`, and for every Orca session on the machine (the fleet view, below). It reads the same sources as `orch status` (the ledger, the tracker, GitHub and Orca) and spends no model tokens. Collecting data is deterministic Python. A model is involved only if you add the optional annotator described at the end.

## Start it

`orch init` and `orch status` run `orch-dash ensure`, so a coordinator never starts it by hand. `ensure` asks `/api/health` on the port: this store's server running this code answers and nothing happens; one running older code (a plugin update) is stopped through the pid it reported and started again; a port held by another store or another program is reported, never killed. `serve` holds a lock on `<store>/.dash.lock`, so a second server on the same store exits; its log is `<store>/.dash.log`.

```sh
orch-dash ensure [--port 4780]
orch-dash serve [--port 4780] [--interval 60] [--host 127.0.0.1]
orch-dash collect <slug>   # one collect, prints a one-line summary
orch-dash demo [--port 4780] [--live] [--no-serve]   # fixture programs and an invented fleet, no network
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

## Fleet: every session on this machine

The first screen (`#/fleet/overview`) is not a program. It is every Orca worktree on this machine, arranged as the orchestrator above orchestrations above tasks, with the sessions you opened yourself under the orchestrator too. Program pages stay one click away in the sidebar. `fleet.py` collects it inside the same `serve` process, reads only local CLIs (`orca`, `gh`, `git`), and never calls a model.

### Where it comes from

| What | Source | How often |
|---|---|---|
| Sessions, agents, terminals, orchestration mail | `orca worktree ps`, `orca terminal list`, `orca orchestration inbox` (read without consuming) | Every 10 s |
| Runs, workers, tasks, decision gates | `orca orchestration run-list`, `worker-list`, `task-list`, `gate-list` | Every 120 s |
| Which PR a session's branch has | One GraphQL query per 40 branches (`associatedPullRequests`); a branch without a PR is asked again after 10 min | With the PR tick |
| PR reviews, review threads, requested reviewers, CI | A cheap probe (state, head, updated time, CI rollup) for every PR that is due, then a full read only for the PRs whose probe changed | Every 90 s; a PR is due every 90 s while hot (CI running, its session working, or updated in the last 2 h), 5 min when warm, 15 min when cold, once a day after it is merged or closed |

A PR that has not changed costs one alias in one probe query; only a changed PR is read in full. Avatars are downloaded once per reviewer, only from `avatars.githubusercontent.com`, into the state directory; the page never loads an image from outside this server.

The hierarchy comes from Orca's own records. The root is `root_worktree` from the config or, without one, the worktree whose terminal coordinates the newest Run. A Run's coordinator worktree is an orchestration. A dispatched worker's worktree is a task under its coordinator. Anything else is standalone, under the root. Nothing is written to Orca to build it (see adoption below).

### Screens

- **Overview.** Session counts by phase (working, waiting on a prompt, idle, open, offline), what needs you, open PRs and runs. Below: the top of the inbox, the sessions moving now, the open-PR graph and the latest timeline.
- **Inbox.** Everything a session is waiting on you for, filterable by type. Each row links to its session, opens the PR or copies the command, and can be dismissed.
- **Graph.** Session → pull request → reviewer. The bar on a PR is its CI (green, red, amber). A reviewer edge is green for approved, dashed amber for an approval on an older commit, red for changes requested, blue for commented, grey dotted for requested and not yet answered.
- **Sessions.** The whole tree as a table: kind, phase, repository and branch, PR, unread count, last activity.
- **Session.** One session's inbox, agents (current prompt, the tool running now, the last reply, all masked), its PRs and its timeline, plus the send box.
- **Timeline.** Prompts, finished turns, PR review and CI changes, and orchestration mail, newest first.

Freshness works as for programs, with Orca warning after 30 s and stale after 90 s, runs after 5 and 15 min, GitHub after 10 and 30 min. The sidebar lists the live part of the tree (offline sessions without unread items are left to the Sessions table), each with a red badge for unread items.

### Inbox types

| Type | Raised when |
|---|---|
| `permission` | An agent sits on a permission or input prompt (Orca reports it as waiting) |
| `question` | An unread `question`, `escalation` or `decision_gate` message to a coordinator |
| `approval` | A pending Orca decision gate, or a finished turn that ends asking for a decision |
| `login`, `run_command`, `verify_failed`, `verify_ok` | A finished turn that ends asking you to log in, to run something yourself, or reports a verification that failed or passed |
| `changes_requested`, `review_comment_received`, `ci_failed`, `approval_stale`, `ready_to_merge` | A PR owned by a session has a change request, unresolved review threads from a person (bots are left out unless `bots_in_inbox`), failing CI, only approvals on an older commit, or a current approval with passing CI |
| `sync_stalled`, `reload_pending` | Skill sync reported failure or went quiet for two intervals, or a reload could not be sent to a session |
| anything else | Added by the orchestrator with `orch-dash inbox add` |

The turn-based types read only the last lines of the agent's final message that Orca already reports, with fixed patterns, so they are a hint, not a verdict. Items backed by a live condition (a waiting prompt, unread mail, a pending gate, a PR's state) disappear when it clears; turn-based and added items stay until dismissed or resolved. An `approval`, `question`, `login`, `run_command` or `verify_failed` item raised before the session's latest prompt is **missed**: the inbox pins it to the top with a red edge, because typing the next prompt usually means the question above it went unanswered.

**Unread** is per session: items raised after the later of the last time you opened the session page and the last prompt typed into it. A fresh install shows every open item as unread.

### PR events for the rest of the platform

Every change a full read finds is appended to `pr-events.jsonl` in the state directory, one JSON line (under 4 KiB, one write) per event: `{v, id, at, repo, pr, kind, url, owner, owner_kind, actor, checks, head}`. `kind` is one of `review_comment`, `changes_requested`, `approved`, `review_requested`, `head_pushed`, `approval_stale`, `checks_failed`, `checks_recovered`, `merged`, `closed`; readers ignore kinds they don't know. `owner` is the live dispatch that owns the PR's session (`owner_kind: dispatch`), or the session's agent terminal (`terminal`, or `human_session` for a standalone session). The file rotates to `pr-events.jsonl.1` past 5 MB. The dashboard itself only displays these; whether a session is told about them is up to the orchestrator.

### Sending a line to a session

The session page has a send box for its agent terminal. It is off the network by construction and guarded on every step:

- The server answers only requests whose `Host` (and `Origin`, if any) is this server on 127.0.0.1 or localhost, and POSTs need a per-process token the page fetches from the same server.
- The target must be a connected, writable agent terminal of a session in the current fleet state; a plain shell is never a target.
- One line, at most 2000 characters. The page shows exactly what will be typed and where, and sends only after you confirm.
- The line is typed only if the platform's idle check (`scripts/sync.py` `try_send` at the repository root) finds the session idle at an empty prompt: not busy, no dialog open, nothing typed in the composer. Without that script, or when the check refuses, nothing is typed and the page offers Copy instead.
- "Sent, but the screen does not show it was taken" means the line was typed and Enter pressed. The page says so and does not offer a retry, so nothing is sent twice.
- Every attempt, refused or not, is appended to `sends.jsonl` with the text masked.

The dashboard never answers a permission prompt for a session.

### Adoption into Orca's parent field

The tree above is computed; Orca's `parentWorktreeId` is left alone by default. `orch-dash adopt` prints the parent each session would get. `orch-dash adopt --apply` writes it with `orca worktree set --parent-worktree`, only for sessions that have no parent yet, only when the config sets `adopt.write_orca_parent` to true, and logs each write with the previous value to `adoption.jsonl`. `orch-dash adopt --undo all` (or `--undo <worktree id>`) restores the logged values.

### Files and configuration

State lives outside every repository, in `$ORCH_FLEET_STATE` or `~/.local/state/agent-skills/dashboard/`:

| File | Contents |
|---|---|
| `state.json` | The page's data, written atomically |
| `prs.json` | Per-PR probe signature and last full read; branch → PR cache |
| `memory.json` | Last prompt and phase per agent, open turn-based items, dismissed keys |
| `events.jsonl` | The timeline |
| `pr-events.jsonl` | PR events (above) |
| `seen.json` | When each session page was last opened |
| `inbox-external.jsonl` | Items added and resolved with `orch-dash inbox` |
| `sends.jsonl`, `adoption.jsonl` | Send attempts; adoption writes and undos |
| `avatars/` | Reviewer avatars |

The optional config is `$ORCH_FLEET_CONFIG` or `~/.config/agent-skills/dashboard/config.json`:

```json
{"root_worktree": "<orca worktree id>", "adopt": {"write_orca_parent": false},
 "include_main_worktrees": false, "bots_in_inbox": false}
```

Everything collected passes through one masking step before it is written or shown: GitHub, Slack, Anthropic, OpenAI and AWS token shapes, JWTs, `Authorization`/`Bearer` values, and `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_API_KEY`, `*_CUSTOM_HEADERS` assignments. No credential is read or stored: `gh` uses its own login. `ORCH_FLEET=off` turns the fleet collector off; `orch-dash demo` and the tests point `ORCH_FLEET_STATE` at a temporary directory and replay an invented machine.

```sh
orch-dash fleet                          # one collect, prints a summary
orch-dash inbox add --type login --title "Log in to the registry" [--session <worktree id>] [--url URL] [--command CMD] [--key KEY]
orch-dash inbox resolve --key KEY
orch-dash adopt [--apply] [--undo all|<worktree id>]
```

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
