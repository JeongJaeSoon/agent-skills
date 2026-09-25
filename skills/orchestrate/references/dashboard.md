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
| Sessions, agents, terminals, orchestration mail | `orca worktree ps`, `orca terminal list`, `orca orchestration inbox --limit 2000` (read without consuming; every message Orca keeps, heartbeats included, so no unanswered question falls out of the window) | Every 10 s while a page is open (it polled in the last 5 min), every 60 s otherwise; opening the page, and every action on it (answer, approve, send, dismiss), refreshes at once, and the page fetches the result about 1.5 s later instead of at its next 5 s poll |
| Runs, workers, tasks, decision gates | `orca orchestration run-list`, `worker-list`, `task-list`, `gate-list` | Every 120 s, and at once when the page is opened after 5 min away |
| Which PR a session's branch has | One GraphQL query per 40 branches (`associatedPullRequests`); a branch without an open PR (none yet, or the one it had was merged or closed) is asked again after 10 min | With the PR tick |
| PR reviews, review threads, requested reviewers, CI | A cheap probe (state, head, updated time, CI rollup) for every PR that is due, then a full read only for the PRs whose probe changed | Every 90 s. While a page is open every open PR is probed on each tick (one query per 50 PRs, about 40 queries an hour for any fleet under 50 PRs). With no page open a PR is due every 90 s while hot (CI running, approved, its session working, or updated in the last 2 h), 5 min when warm (updated in the last day) and 15 min when cold; once a day after it is merged or closed. Opening the page after 5 min away probes every open PR at once, and a session whose phase changes (a turn ends, a worker starts) has its PRs probed on that same tick. A probe that saw a change is recorded only once the full read lands, so a failed read is retried on the next probe. Each query's `rateLimit` is kept; below 300 points left, the regular PR tick runs at most once per 15 min until the hour resets (a phase change or opening the page still probes) |

A PR that has not changed costs one alias in one probe query; only a changed PR is read in full. Avatars are downloaded once per reviewer, only from `avatars.githubusercontent.com`, into the state directory; the page never loads an image from outside this server. The page does not ask for a team reviewer's avatar (there is none), and one that failed to load is not asked for again until the page is reloaded.

The hierarchy comes from Orca's own records. The root is `root_worktree` from the config or, without one, the worktree whose terminal coordinates the newest Run among those whose coordinator no other on-screen coordinator dispatched (a program coordinator started with `worker-start` opens a newer Run of its own, so the newest Run alone is not the top level). A Run's coordinator worktree is an orchestration, under the coordinator whose Run dispatched it, else under the root. A dispatched worker's worktree is a task under its coordinator. Coordinators that dispatched each other are cut loose onto the root, so the tree never loops. Anything else is standalone, under the root. Nothing is written to Orca to build it (see adoption below).

### Screens

- **Overview.** Session counts by phase (working, waiting on a prompt, idle, open, offline), what needs you, open PRs and runs. Below: the top of the inbox, the sessions moving now, the open-PR graph and the latest timeline.
- **Inbox.** Everything a session is waiting on you for, filterable by type. A click on a row (outside its buttons, links and answer box) opens its session's page; an item whose session Orca no longer lists, or that has none, and a row on its own session's page stay put. Each row links to its session, opens the PR or copies the command, and can be dismissed.
- **Graph.** Session → pull request → reviewer. The bar on a PR is its CI (green, red, amber). A reviewer edge is green for approved, dashed amber for an approval on an older commit, red for changes requested, blue for commented, grey dotted for requested and not yet answered.
- **Sessions.** The whole tree as a table: kind, phase, project and branch, PR, Needs you count, last activity.
- **Session.** One session's inbox, agents (current prompt, the tool running now, the last reply, all masked), its PRs and its timeline, plus the send box.
- **Timeline.** Prompts, finished turns, PR review and CI changes, and orchestration mail, newest first.

Every row that belongs to a session names its project in a small badge: Needs you items (a decision takes the terminal that registered it, a decision gate the worktree its task's dispatch runs in), Moving now, the Sessions table, the session page and the graph's session nodes. The project comes from live state on every collect: Orca's own project name for the worktree, else the workspace directory Orca put it under (`~/orca/workspaces/<project>/<worktree>`), else the origin repository's name, else the worktree directory's name. Only the origin lookup is cached, per path, so a session whose path changes is looked up again. Clicking a badge, or a chip in the project row of Overview, Inbox, Graph and Sessions, shows only that project; "All projects" clears it.

Freshness works as for programs, with Orca warning after 30 s and stale after 90 s, runs after 5 and 15 min, GitHub after 10 and 30 min. The sidebar lists the live part of the tree (offline sessions with nothing in Needs you are left to the Sessions table), each with a red badge counting its Needs you items.

A session's dot has one colour per phase on every screen (sidebar, Moving now, Sessions table, graph): blue with a growing ring while working, amber while waiting on you, green when idle after a finished turn, grey when open with no agent, a hollow ring when offline. The Overview's session count, the Sessions page and the graph legend spell this out, and every dot names its phase on hover. The same blue ring marks a moving worker on program pages.

### Inbox types

| Type | Raised when |
|---|---|
| `prompt` | A `permission` item whose terminal shows, right now, the permission dialog `hooks/permission.py` recorded: it carries Approve, Deny and Open terminal (below) |
| `permission` | An agent sits on a permission or input prompt (Orca reports it as waiting) |
| `question` | A `question`, `escalation` or `decision_gate` message to a coordinator that no one has replied to. It stays after the coordinator acks it (its inbox loop acks at once, before you have answered) until a message in its thread comes from someone else, or for a day; an unread one stays until it is answered. Mail from a worker whose dispatch has settled (completed, failed or abandoned; the dispatch is read from the payload or a `dispatch:` sender) goes at once |
| `decision` | A decision the coordinator registered with `orch decide add` and has not closed (below) |
| `approval` | A pending Orca decision gate, or a finished turn that ends asking for a decision |
| `login`, `run_command`, `verify_failed`, `verify_ok` | A finished turn that ends asking you to log in, to run something yourself, or reports a verification that failed or passed |
| `changes_requested`, `review_comment_received`, `ci_failed`, `approval_stale`, `ready_to_merge` | A PR owned by a session has a change request, unresolved review threads from a person (bots are left out unless `bots_in_inbox`), failing CI, only approvals on an older commit, or a current approval with passing CI |
| `sync_stalled`, `reload_pending` | Skill sync reported failure or went quiet for two intervals, or a reload could not be sent to a session |
| anything else | Added by the orchestrator with `orch-dash inbox add` |

The turn-based types read only the last lines of the agent's final message that Orca already reports, with fixed patterns, so they are a hint, not a verdict. Items backed by a live condition (a waiting prompt, unanswered mail, an open decision, a pending gate, a PR's state) disappear when it clears; added items stay until dismissed or resolved. A turn-based item goes when the same agent finishes a later turn (what it asked for was dealt with, or the new turn raises it again) or when Orca no longer lists its worktree; each such close is an `item_resolved` event on the timeline naming the rule (`superseded`, `session_gone`). An `approval`, `question`, `login`, `run_command` or `verify_failed` item raised before the session's latest prompt is **missed**: the inbox pins it to the top with a red edge, because typing the next prompt usually means the question above it went unanswered.

A session's **badge** is the number of Needs you items on it, the same list its session page shows. The badges plus the items that belong to no session (sync health, items added without a session) are exactly the Inbox count, and every item is a live condition (or an open turn-based item), so the two never disagree. Nothing local such as a "last opened" mark hides an item from the count: an item leaves only when what it points at settles, it is answered, or it is dismissed; the human never has to clear what reality already settled. A `reload_pending` item sits on the session of the terminal the reload could not reach, titled with that terminal; one for a terminal Orca no longer lists is dropped. skills-sync retries only every 15 minutes, so after each collect the dashboard sends the reload again itself, through skills-sync's own `broadcast --only` (same lock and idle checks), to each pending terminal whose session is idle, at most once every 2 minutes per terminal; once it lands, skills-sync takes it out of `reload-pending.json` and the item goes. Orca's own per-worktree unread flag is a yes/no without a count, so it is not used.

### PR events for the rest of the platform

Every change a full read finds is appended to `pr-events.jsonl` in the state directory, one JSON line (under 4 KiB, one write) per event: `{v, id, at, repo, pr, kind, url, owner, owner_kind, actor, checks, head}`. `kind` is one of `review_comment`, `changes_requested`, `approved`, `review_requested`, `head_pushed`, `approval_stale`, `checks_failed`, `checks_recovered`, `merged`, `closed`; readers ignore kinds they don't know. `owner` is the live dispatch that owns the PR's session (`owner_kind: dispatch`), or the session's agent terminal (`terminal`, or `human_session` for a standalone session). The file rotates to `pr-events.jsonl.1` past 5 MB. The dashboard itself only displays these; whether a session is told about them is up to the orchestrator.

### Decisions the coordinator waits on you for

A decision the coordinator reaches in its own analysis, or hears from a subagent, never becomes Orca mail. The coordinator registers it with `orch decide` (`scripts/prog.py`, stored in `decisions.json` in the state directory, masked on write):

```sh
orch decide add --title "Export format for the first release" --body-file /tmp/why.md \
  --option "CSV::finance opens it in a spreadsheet" --option "JSON::one format for both" --recommend 1 [--link URL]   # prints d7
orch decide list
orch decide done d7 --answer CSV     # answered
orch decide drop d7                  # no longer needed
```

The item sits on the session whose terminal ran `add` (`$ORCA_TERMINAL_HANDLE`), or on the root. It shows the title, its age, the body's first line, and one button per option, the recommended one with a check; an option's description is its button's tooltip. Nothing expands in place. An option button, or the answer box, takes a second click and then answers it through `POST /api/fleet/decide` (same Host, Origin and token guard as the send box). The answer is recorded first: the decision is closed as `done` with that answer, as `orch decide done` would, and leaves Needs you on the next poll. Then `decision <id>: <answer>` goes to that terminal (or the root's agent terminal) through the send box's idle check and log below. A busy coordinator does not fail the answer: the page says "Recorded; will relay to the coordinator when it is idle", the decision keeps `delivered: false`, and each later collect retries the line, skipping while the session is working, until it is typed once (a line typed but not confirmed counts as delivered, so it is never sent twice). `orch decide list` prints such answers, the coordinator's own `done` stops the retry, and `drop` cancels it. Dismiss only hides the item on this page.

An answer typed into the coordinator's terminal closes the decision too. The plugin's `UserPromptSubmit` hook (`hooks/decision.py`) reads each prompt of the terminal whose handle registered the decision, and of no other: a worker's prompt never closes the coordinator's decision. It closes one as `done` only when the prompt is unambiguous: its first line starts with the id (`decision d3: yes`, `d3: yes`, `d3 yes`; the rest of the line is the answer), or starts with an option label of two or more characters, not followed by a letter or digit, that exactly one of the terminal's open decisions has (the longest matching label wins). The hook then tells the coordinator the answer was recorded. Any other prompt closes nothing and only adds a reminder listing that terminal's open decisions (at most five) to the prompt's context. Decisions registered outside Orca have no handle and are never closed this way. The hook is loaded when a session starts: a session that was already running needs `/reload-plugins` or a restart first. Any error exits 0, so a prompt is never blocked.

### Sending a line to a session

The session page has a send box for its agent terminal. It is off the network by construction and guarded on every step:

- The server answers only requests whose `Host` (and `Origin`, if any) is this server on 127.0.0.1 or localhost, and POSTs need a per-process token the page fetches from the same server.
- The target must be a connected, writable agent terminal of a session in the current fleet state; a plain shell is never a target.
- One line, at most 2000 characters. The page shows exactly what will be typed and where, and sends only after you confirm.
- The line is typed only if the platform's idle check (`scripts/sync.py` `try_send` at the repository root) finds the session idle at an empty prompt: not busy, no dialog open, nothing typed in the composer. Without that script, or when the check refuses, nothing is typed and the page offers Copy instead.
- "Sent, but the screen does not show it was taken" means the line was typed and Enter pressed. The page says so and does not offer a retry, so nothing is sent twice.
- Every attempt, refused or not, is appended to `sends.jsonl` with the text masked.

The send box never answers a dialog: its idle check refuses while one is open.

### Answering a permission prompt

The human decides; nothing answers a prompt on its own. The plugin's `PermissionRequest` hook (`hooks/permission.py`) writes the tool name and its masked string arguments to `prompts/<terminal handle>.json`, keyed by `$ORCA_TERMINAL_HANDLE`. It prints no decision, so the permission flow is unchanged. `PermissionRequest` rather than the `Notification` hook's `permission_prompt`: the notification fires only after about six seconds without typing and carries only "Claude needs your permission", while `PermissionRequest` fires when the dialog opens and carries the tool input to show and to match.

On each collect, a session Orca reports as waiting whose agent terminal has a record gets its screen read (`orca terminal read --screen`). When the screen ends in a permission dialog (a rule, the request, `Do you want to …?`, numbered options, `Esc to cancel` last) that shows the recorded request (its command, file name, URL host or a string argument, whitespace ignored), the item becomes `prompt` with the command and up to three buttons. A question list, the folder-trust dialog or an unmatched dialog stays a plain `permission` item. When the dialog closes, Orca stops reporting the wait and the item goes.

Approve and Deny take a second click, then POST `/api/fleet/prompt` under the same Host, Origin and token guard as the send box. The server:

- takes the terminal from the prompt record of that session's agent terminal, never from the page;
- under the lock `scripts/sync.py` uses for reloads and the send box, reads the screen twice 1.5 s apart and refuses unless both show the same dialog for the recorded request (only the dialog is compared: the pending tool's bullet above it blinks);
- presses one digit and no Enter: the option labelled exactly `Yes` to approve, `No` to deny. An option that saves a rule or changes the mode (`don't ask again`, `always allow`, `switch to auto mode`) is never chosen; when every Yes is one of those, Approve is not offered and the server refuses, so answer in the terminal (Open terminal switches Orca to it);
- reads the screen again: the dialog gone is success. Still showing means the digit was pressed but not confirmed, and the page does not offer a retry.

Every attempt, refused or not, is appended to `sends.jsonl` (`text: "prompt approve"` and the like, with the digit pressed). The hook needs `/reload-plugins` in sessions started before it landed. Dialog shapes were measured on Claude Code 2.1.282; a changed shape makes the item a plain `permission` again rather than a wrong answer.

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
| `inbox-external.jsonl` | Items added and resolved with `orch-dash inbox` |
| `decisions.json` | Decisions from `orch decide`; closed ones are dropped after a week |
| `sends.jsonl`, `adoption.jsonl` | Send and prompt-answer attempts; adoption writes and undos |
| `prompts/` | The latest permission request per terminal, from `hooks/permission.py`; dropped after a day or once answered |
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
