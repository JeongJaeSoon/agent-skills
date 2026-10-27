# Dashboard: live program progress without spending tokens

`scripts/dash.py` is a local, stdlib-only page for every program under `$PROGRAMS_HOME`, and for every Orca session on the machine (the fleet view, below). It reads the same sources as `orch status` (the ledger, the tracker, GitHub and Orca) and spends no model tokens. Collecting data is deterministic Python. A model is involved only if you add the optional annotator described at the end.

## Start it

`orch init` and `orch status` run `orch-dash ensure`, so an orchestrator or lead never starts it by hand. `ensure` asks `/api/health` on the port: this store's server running this code answers and nothing happens; one running older code (a plugin update) is stopped through the pid it reported and started again; a port held by another store or another program is reported, never killed. The port stays where the human opened it: without `--port`, `ensure` and `serve` use `$ORCH_DASH_PORT`, else the port this store last served on (`<store>/.dash.json`), else 4780, so a restart after a merge or a reboot keeps the bookmarked URL. `ensure --port P` while this store's server answers on another port stops it there and starts it on P, since the store lock would make a second server exit. A running server re-hashes its code (every non-test script beside `dash.py`, the two it imports from elsewhere, and the page assets) every 30 s; once the code on disk differs from what it runs (a merge landed after it started), `/api/health` reports `stale` and the page shows a banner with the `ensure` command that restarts it. The sidebar foot shows the running code version and, in a git checkout, its commit. The `orchestrate` wordmark at the top left links home (`#/fleet/overview`); it is the only home link, so the tree has no Fleet row of its own. `serve` holds a lock on `<store>/.dash.lock`, so a second server on the same store exits; its log is `<store>/.dash.log`.

```sh
orch-dash ensure [--port P]
orch-dash install-agent [--port P]   # macOS: launchd keeps it up; uninstall-agent removes it
orch-dash serve [--port P] [--interval 60] [--host 127.0.0.1]
orch-dash collect <slug>   # one collect, prints a one-line summary
orch-dash demo [--port 4780] [--live] [--no-serve]   # fixture programs and an invented fleet, no network
```

On macOS, `install-agent`, run from the git checkout sessions load (a worktree or the plugin cache is refused, since it goes away), writes `~/Library/LaunchAgents/<label>.plist` (`$ORCH_DASH_LABEL`, default `io.github.jeongjaesoon.orch-dash`) and loads it: `serve` from that checkout with `RunAtLoad`, `KeepAlive` and a 30 s `ThrottleInterval`, the PATH of the `python3`, `git`, `gh` and `orca` found at install, and `$PROGRAMS_HOME` when set. Running it again changes nothing that is in place. While that agent is loaded and serves this store, launchd is the only manager and `ensure` never starts a server itself: it stops this store's servers launchd did not start (identified through their health answer, as above) and runs `launchctl kickstart`, with `-k` when launchd's own server is stale or silent. The port is the plist's. A server launchd starts waits on a held store lock instead of exiting, so a leftover server hands over the moment it goes, and once the code on disk has differed from what it runs on two checks in a row, and `import dash` of it succeeds, it exits so launchd starts the new code within about a minute; code that does not import leaves the old server up with its stale banner. Without a loaded agent (Linux, or after `uninstall-agent`), `ensure` behaves as above. Its log is `<store>/.dash.log` too.

`serve` collects in the background at three rates:

| Source | How often |
|---|---|
| Tracker and GitHub | Every `--interval` seconds (these use the network and have rate limits) |
| Stages (tracker tree) | Every `--interval` seconds, at most 24 tracker calls each time |
| Orca (`worker-list`, `task-list`, `run-show`) | Every 20 s |
| Ledger | Checked every 3 s; re-collected when it changes, and at least every 30 s |

A closed program (its `predicate_verified` is current), or a quiet one (no ledger event for 3 days, the line at which the sidebar folds it away), keeps only the ledger check. Its tracker, GitHub and Orca sections stay as last collected, and a landing, a predicate edit, an admitted follow-up or any other new ledger event reopens it. Most programs on a machine are quiet: polling them every 20 s was most of the collector's Orca calls.

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
| Runs, workers, tasks, decision gates | `orca orchestration run-list`, `worker-list`, `task-list`, `gate-list` | Every 120 s, and at once when the page is opened after 5 min away. A Run whose workers and tasks have all settled, with no open gate and no orchestrator or lead terminal on screen, is read again only every 15 min (`SETTLED_EVERY`): nothing can move it |
| Which PR a session's branch has | One GraphQL query per 40 branches (`associatedPullRequests`); a branch without an open PR (none yet, or the one it had was merged or closed) is asked again after 10 min | With the PR tick |
| Every PR of mine, in any repository | A GraphQL search (up to 3 pages of 100) for the open PRs you authored and those merged in the last 24 h, returning only each PR's key and updated time: probe fields on a hundred search nodes time out. A PR is on the page whether or not a live worktree has its branch, so a PR whose worker was released stays, and a new one appears on the next tick instead of after branch discovery's 10 min. A PR the search saw updated is probed on that tick; otherwise it keeps its tier below | Every 30 s while a page is open, every 90 s otherwise. A failed search keeps the last list |
| PR reviews, review threads, requested reviewers, CI | A cheap probe (state, head, updated time, CI rollup, merge state, review decision, CodeRabbit's commit status) for every PR that is due, then a full read only for the PRs whose probe changed | With the PR tick, 25 PRs to a query (50 took over 10 s and at times timed out); a chunk that fails costs only its own PRs. While a page is open every open PR a session owns is probed at least every 90 s. With no page open a PR is due every 90 s while hot (CI running, approved, its session working, or updated in the last 2 h), 5 min when warm (updated in the last day) and 15 min when cold; once a day after it is merged or closed. Opening the page after 5 min away probes every open PR a session owns at once, and a session whose phase changes (a turn ends, a worker starts) has its PRs probed on that same tick. A probe that saw a change is recorded only once the full read lands, so a failed read is retried on the next probe. Each query's `rateLimit` is kept; below 300 points left, the regular PR tick runs at most once per 15 min until the hour resets (a phase change or opening the page still probes) |

A PR that has not changed costs one alias in one probe query; only a changed PR is read in full. Avatars are downloaded once per reviewer, only from `avatars.githubusercontent.com`, into the state directory; the page never loads an image from outside this server. The page does not ask for a team reviewer's avatar (there is none), and one that failed to load is not asked for again until the page is reloaded.

The hierarchy comes from Orca's own records and the roles sessions declared. The root is `root_worktree` from the config; without one, the worktree of the one live Claude Code session declared orchestrator (`ROLE: orchestrator` recorded by `hooks/reorient.py`, or `--name orchestrator`), matched by its working directory and, where folder contexts share that directory, by its session name; without that, the worktree whose terminal coordinates the newest Run among those whose bound session (Orca's coordinator) no other on-screen bound session dispatched and no session declared a lead (a lead started with `worker-start` opens a newer Run of its own, so the newest Run alone is not the top level; without the declared roles, a top level with no Run of its own would lose the root to a lead's newer Run, and that lead would leave the Brief). A Run's bound session's worktree is an orchestration, under the session whose Run dispatched it, else under its Orca parent, else under the root. A worktree whose session declared a lead (`ROLE: lead <slug>` or `--name lead:<slug>`) is an orchestration too, even with no Run. A dispatched worker's worktree is a task under its orchestrator or lead. Anything else is standalone, under its Orca parent (`parentWorktreeId`, which a lead sets on workers it made with `orca worktree create`) when that parent is on screen, else under the root. A parent loop, from sessions that dispatched each other or from Orca parents, is cut loose onto the root, so the tree never loops. On screen an orchestration reads as a **lead**: the sidebar sets its name in bold with the number of sessions under it in grey beside it, and the Kind column and session page say `lead`. A task, or a standalone under a lead, reads as `worker`; the orchestrator and a standalone under the root keep their kind. Only the label changes: `kind` in `state.json` stays as above. Nothing is written to Orca to build it (see adoption below).

### Screens

- **Overview.** Session counts by phase (working, waiting on a prompt, idle, open, offline), what needs you, open PRs and your open requests (below). Below: the top of the inbox, the sessions moving now, your open requests (while any is open; eight until `Show all N`), one row per lead (its sessions, how many work, and the open PRs of the lead and those sessions; shown only while a lead exists), the eight most recently updated open PRs (linking to Pull requests) and the latest timeline. An inbox item's and a request's text wraps rather than being cut. These cards sit in as many columns as fit at 480 px, at most three, so a wide screen fills its row; the PR card spans two columns once the view is 980 px wide.
- **Pull requests.** Every open PR from the search and every PR a session's branch has, grouped by repository: number, title, author, draft, review decision, CI, merge state (`mergeStateStatus`), CodeRabbit (reviewed, reviewing, rate-limited, skipped or failed, from its commit status) with the unresolved thread count, the owning session when one has the branch, age and last update. Filters: needs review, CI failing, mergeable (`CLEAN`, `HAS_HOOKS` or `UNSTABLE`, not draft), and one repository. The head shows when GitHub was last read, and PRs merged in the last 24 h sit in a collapsed group. Only a PR a session owns raises Needs you items and timeline events; the rest are this page's to show.
- **Brief.** The orchestrator's or lead's periodic report as `orch brief render --periodic` prints it from the board, the fleet's sessions, the PR list and the open decisions. Rows the board lacks are derived (`--no-fill` turns that off): each lead session whose terminal no board lead holds becomes a lead row, the cards under it its `└` rows and the root's own cards rows of their own (a standalone card only with a report file, a PR or a dispatch, so a resident folder such as an automations runner is not one), each with its newest PR, a status from the session's phase (working 🔄 `작업 중`, idle 🔄 `턴 종료`, waiting 🙋 `확인 대기`, the owner's, open or offline ⏸️) and the later of its last activity and its report file's change as the confirmed time, so a quiet one shows its age. The next step is the report file's newest milestone (its `다음:`/`Next:`/`次:` line, else its first line) when the session's brief, the path in its Orca comment, names that file on its REPORT line or says "this note"; otherwise it is the phase's. The fleet collector reads only the brief's first 32 KB and the report's last 8 KB, again only once a file changed, and puts the result on the session as `report`. A lead the board has gets no lead row from its session, but each of its cards whose PR no board row has hangs under its board row as a `└` row. The renderer is the only implementation of the report's rules (rows, order, icons, bold for what changed since the last report), so the page only turns its Markdown (tables, `**bold**`, links, code) into HTML, without cutting anything: a cell the renderer cut to 78 columns (`head…[n]`) gets the whole text of its `- [n]` line back and the list goes; every repository table (`| | 리드/Phase | PR | 진행 |`) shares one set of column widths, with a narrow status column, a shaded header and a cell too long for its column scrolling sideways within itself; the sections under the tables (진행 중, 다른 사람 대기, 결정 필요, 지금 오너가 할 일, 요청, 보류) become tables in the same style, each line split at its section's separators (`→`, ` — `, `· 다음:`, `→ 추천:`) and its status emoji in its own column; ❔ is drawn as a toned `?` mark; and a line's trailing `· coordinator`/`· 코디네이터` reads `orchestrator`/`오케스트레이터`. These are the page's rules only: the terminal text keeps its cuts and `- [n]` lists. The head counts changed and stale rows, flags a table wider than 78 columns and says when the page last read it. The page reads `GET /api/fleet/brief` every 5 s while open and each call runs the renderer with `--no-refresh` (this server's collector keeps state.json fresh), so an `orch board set` shows on the next poll. With neither board rows nor leads and cards it shows the report's headline (`리드·카드 행 없음`) and 지금 오너가 할 일; a checkout without the renderer shows the error. Above the report one line links to the Stages page with its count of open work rows. The report's own text has no stage table: in its place one line, `단계표 N행 (접음 M) · <where>`, counts the rows `orch stage show` prints (finished rows and rows `board.stage_idle_d` days unchanged fold into M), and `<where>` is this page's `#/fleet/stages` URL while the server whose `<store>/.dash.json` pid is alive runs, else `` `orch stage show` ``. On Telegram it is always `orch stage show --telegram`: the server listens on 127.0.0.1 only, so the link is dead on another device. The check reads the pid only, since the server itself runs the renderer. The Brief keeps Open items and Closed in the last 7 days: they are board rows, cards and items `orch board done` closes, not stage rows.
- **Stages.** The stage table whole (see "The stage table" below), generated rows and recorded ones, as work, PR, merged, dev check, prod check, in fixed column widths that fit the card: every cell wraps, none is cut. Under 640 px each row is a block, its title and one labelled line per filled cell. A program (a group row, or a top-level row of its own) is a block: its heading with its open count, its open rows, and its finished rows behind its own `▸ N finished`, which opens them dimmed for their 7 days with an `Ack all N` for that program; programs with open rows come first, a finished program is one dimmed heading line. Rows under a group sit flush and each level below them gets a `└`. A cell shows its mark (✅ ❌ ⚠️ 🔄, `–` for na) and its whole evidence in smaller type; its tooltip names a generated cell's source. Each `owner/repo#N` is a link the server resolved: `/pull/N` for a PR in the collector's list or a row's PR, else `/issues/N`, which GitHub forwards for a PR. The legend and env line sit above the table and the next steps below it; an env line not re-recorded for 7 days folds into `env recorded N ago`, opened on click. Ack hides a finished row for good (see Acks below). The table comes in the `GET /api/fleet/brief` reply (`stages`), read from `stages-auto.json`, `stages.json` and the PR list on each call, so a written cell shows on the next poll; the page polls it as the Brief does. The sidebar's Stages count is its open work rows. A `#/fleet/work` link opens Stages.
- **Automations.** Every Orca automation (see Automations below): name, schedule in words for the common cron shapes, next and last run, the six newest runs as colored blocks (oldest left; a skipped run is hollow) and the last run's first line, whole, wrapped in its cell. An enabled automation whose last run failed, or whose next run is 10 min overdue, is flagged `▲`; their count is the sidebar number. The header on every fleet page always carries an automations tag: `Automations N` (the count listed) in a neutral tone, or `Automations ▲N` when N are flagged. A failed listing is said above the table, which keeps the last good values. Below it, orch-dash's own jobs and the rules it applies on read (orch-dash jobs, below).
- **Monitors.** Every background watch a live Claude Code session started (see Monitors below): status, what it watches (its description and command), the session, start, last wake (its last output or event) and the end summary or last event. A dead or lost one is flagged `▲`; their count is the sidebar number and a `Monitors ▲N` tag in the header on every fleet page.
- **Sent.** Every instruction a session sent out (see Sent instructions below): when, from whom to whom, its kind and channel, the subject and the text, and the sources it came from. A text over six lines or 480 characters folds under its first line. Filters: 1, 7 or 30 days, one source, and a search over sender, receiver, channel, repository, subject and text. The page reads `GET /api/fleet/sent?days=N` every 5 s while open.
- **Skills.** Every installed skill with its uses in 7 and 30 days, how it was triggered, last use, sessions and repositories reached, heuristic misses and flags, grouped by source (see Skill usage below). Filters: flagged, each flag, and one source.
- **Skill changes.** What agent-skills changed about itself in 60 days, newest first, with a per-skill count: improvement signals from the skills ledger (`proposed`), open PRs of this checkout's `origin` in the collector's `prs.json` (`in PR`), and commits on the served checkout touching `skills/`, `agents/` or `hooks/`, `merged` when the subject ends in `(#N)` and `applied` when pushed straight to main. A commit counts for the skill its subject's scope names (`feat(brief): …`), the other skills it touched shown as `+N`; `L-…` lesson ids in the message are its basis beside the PR and commit. The git log is capped at 400 commits and kept until `HEAD` moves, the ledger is read from its last 64 KB, and the lessons ledger (`reflect.ledger`) is counted only when it is a plain file on this machine. `GET /api/fleet/changes`, rebuilt at most once a minute.
- **Data flow.** Who writes each local state file and who reads it: producers (the serve loops, `orch`, hooks, the watch automations, skills-sync, program leads, card sessions), the state files, and consumers (pages, the Brief renderer, reflect, hooks). The map is fixed in `scripts/flow.py`; only the values come from the machine: path, last change, size, entry count for a directory, and for a `.jsonl` its line count (counted from where the last count ended while the file only grows) and its last row's `ev`/`kind`. A file older than its producer's interval is stale, a consumer with none of its inputs present is starved, and a file in the state directories the map does not name is listed as not in the map. The report files the briefs name are one node, from the sessions the collector holds. Each box shows its path, last row and rule on hover, and the Brief's derivation rule sits under the drawing. `GET /api/fleet/flow`, rebuilt at most every 30 s.
- **PR stats.** Pull requests I opened, and those agents opened for me, by month (by state with a trend line, and by source), by week (with a 4-week average), by weekday and hour, and by repository, with a toggle that leaves bulk campaigns out (see PR stats below).
- **Inbox.** Everything a session is waiting on you for, filterable by type. The tab title carries its count, so a background tab shows when something new waits. A click on a row (outside its buttons, links and answer box) opens its session's page; an item whose session Orca no longer lists, or that has none, and a row on its own session's page stay put. Each row links to its session, opens the PR or copies the command, and can be dismissed.
- **Graph.** Session → pull request → reviewer. The bar on a PR is its CI (green, red, amber). A reviewer edge is green for approved, dashed amber for an approval on an older commit, red for changes requested, blue for commented, grey dotted for requested and not yet answered. The drawing never grows past its own width (980 px), so its text stays body size on a wide screen; a long PR title, session or reviewer name is cut with `…` and shown whole on hover.
- **Sessions.** The whole tree as a table: kind, phase, project and branch, PR, Needs you count, last activity (the later of Orca's worktree time and its agents' own, since the worktree time stands still while an agent works). Below it, Shared resources: every `orch hold` resource and landing lane, who holds it (linked to the session when its terminal is known), the note and since when, longest held first; past an hour the time turns amber.
- **Session.** One session's inbox, agents (current prompt, the tool running now, the last reply, all masked), its PRs and its timeline, plus the send box.
- **Timeline.** Prompts, finished turns, PR review and CI changes, and orchestration mail, newest first, as a vertical timeline grouped under day heads (Today, Yesterday, then the date). Each event is one row: the time on the left (`HH:MM` under its day head, the full date and time on hover), a line with the event's state icon, then the event's name in bold with its session and the event text below it, cut at two lines (a click on the cut text, or Enter or Space once it has focus, opens it and closes it again). A PR event links its PR at the right end; the rest of the row opens the session's page. The event kind maps to one of five states (`eventState` in `assets/dashboard/fleet.js` holds the mapping), each with its own icon shape as well as its own colour: done (green check), in progress (blue dots), waiting on you (amber ring), problem (red `!`) and info (small grey circle, also for a kind added later). The page shows 50 events, the session page's timeline 50 of that session's, and **Show more** adds 50 at a time; the overview card keeps the latest 12.

Every row that belongs to a session names its project in a small badge: Needs you items (a decision takes the terminal that registered it, a decision gate the worktree its task's dispatch runs in), Moving now, the Sessions table, the session page and the graph's session nodes. The project comes from live state on every collect: Orca's own project name for the worktree, else the workspace directory Orca put it under (`~/orca/workspaces/<project>/<worktree>`), else the origin repository's name, else the worktree directory's name. Only the origin lookup is cached, per path, so a session whose path changes is looked up again. Clicking a badge, or a chip in the project row of Overview, Inbox, Graph and Sessions, shows only that project; "All projects" clears it.

Freshness works as for programs, with Orca warning after 30 s and stale after 90 s, runs after 5 and 15 min, GitHub after 10 and 30 min. The sidebar lists Sections first and keeps them in place, in three groups split by a rule: the work in flight (Overview, Inbox, Sessions, Pull requests, Brief, Stages, Sent, Timeline), what runs unattended (Automations, Monitors), and how skills and PRs fare (Skills, Skill changes, PR stats, Graph, Data flow); each section has an icon of its own; below them the Worktrees tree, on fleet and program pages alike, scrolls on its own. It is the live part of this tree (offline workers with nothing in Needs you are left to the Sessions table), each row with a red badge counting its Needs you items and its full name on hover. The root sits on top. Every session under it starts a unit that carries everything below it, so a lead keeps its program and cards whatever their own phase, and a unit goes to the group of its most urgent member: `Needs you` (a badge, or waiting on a prompt), `Running`, `Idle` (turn ended, or no agent; folded) and `Offline` (offline leads; folded). Each group shows how many sessions it holds; an empty group is not drawn. A program sits under the orchestrator or lead whose Run is its `run`; a program with no such orchestrator or lead on screen hangs under the root. A closed program (final check current), or one with no orchestrator or lead on screen and no ledger event for 3 days, folds into a collapsed `Archived (N)` at the bottom. A group the human opened or closed stays that way in this browser (`localStorage`); otherwise it takes its default, and a folded one opens while its session or program is the current page. `dash.py demo --crowd N` adds N invented sessions in every phase, to see the sidebar crowded.

A session's dot has one colour per phase on every screen (sidebar, Moving now, Sessions table, graph): blue with a growing ring while working, amber while waiting on you, green when idle after a finished turn, grey when open with no agent, a hollow ring when offline. The Overview's session count, the Sessions page and the graph legend spell this out, and every dot names its phase on hover. The same blue ring marks a moving worker on program pages.

### Inbox types

| Type | Raised when |
|---|---|
| `prompt` | A `permission` item whose terminal shows, right now, the permission dialog `hooks/permission.py` recorded: it carries Approve, Deny and Open terminal (below) |
| `permission` | An agent sits on a permission or input prompt (Orca reports it as waiting) |
| `question` | A `question`, `escalation` or `decision_gate` message to an orchestrator or lead that no one has replied to. It stays after the orchestrator or lead acks it (its inbox loop acks at once, before you have answered) until a message in its thread comes from someone else, or for a day; an unread one stays until it is answered. Mail from a worker whose dispatch has settled (completed, failed or abandoned; the dispatch is read from the payload or a `dispatch:` sender) goes at once; workers are listed per Run with `worker-list --run`, since without it the list is scoped to the Run bound to the terminal that started the server. Mail whose sender later sent the same recipient a `status` or `worker_done` outside the mail's thread goes too: the sender has moved past it (a heartbeat, a report inside the thread or a newer question does not count) |
| `decision` | A decision the orchestrator or lead registered with `orch decide add` and has not closed (below) |
| `approval` | A pending Orca decision gate, or a finished turn that ends asking for a decision |
| `login`, `run_command`, `verify_failed`, `verify_ok` | A finished turn that ends asking you to log in, to run something yourself, or reports a verification that failed or passed |
| `changes_requested`, `review_comment_received`, `ci_failed`, `approval_stale`, `ready_to_merge` | A PR owned by a session has a change request, unresolved review threads from a person (bots are left out unless `bots_in_inbox`), failing CI, only approvals on an older commit, or a current approval with passing CI |
| `sync_stalled`, `reload_pending` | Skill sync reported failure or went quiet for two intervals, or a reload the dashboard's retries could not send to an idle session (below) |
| `selfcheck` | The dashboard's own state disagrees with a fresh read of Orca and GitHub (below) |
| anything else | Added by the orchestrator with `orch-dash inbox add` |

Needs you is sorted by rank, newest first within a rank (`NEEDS_RANK` and `needs_order` in `scripts/fleet.py`). Newest compares the moment, not the text: skills-sync writes `+0900` where the rest write `Z`.

| Rank | Types |
|---|---|
| 0, only you can settle it | `decision`, `approval`, `permission`, `prompt`, `question`, `login`, `run_command` |
| 1, a merge call waiting on you | `ready_to_merge` |
| 2, the rest | every other type |
| 3, held | a PR item whose PR a held board item (`orch board later`) or a to-do (`orch board todo`) names in its title by its full `owner/repo#N`, or by a bare `#N` only one PR in the collector's list has. It shows `held` and the item's trigger, and returns to its own rank when the item is dropped or closed, when its `--due` or the date in its trigger (`M/D` or `YYYY-MM-DD`, optional `HH:MM`, local time) comes, or, with no date, when every PR its trigger names has merged |

The turn-based types read only the last lines of the agent's final message that Orca already reports, with fixed patterns, so they are a hint, not a verdict. Items backed by a live condition (a waiting prompt, unanswered mail, an open decision, a pending gate, a PR's state) disappear when it clears; added items stay until dismissed or resolved, or for 7 days after they were added (`EXTERNAL_KEEP_DAYS` in `scripts/fleet.py`; an `inbox_aged_out` event on the timeline marks the day one leaves); `orch-dash inbox list` prints every unresolved one, older ones included. A turn-based item goes when its agent's pane gets a later prompt or starts a later turn (someone already replied there), when the same agent finishes a later turn (what it asked for was dealt with, or the new turn raises it again), when Orca no longer lists its worktree, when its session has had no agent to answer it for 10 minutes (counted only in reads that found some agent, as an empty read is more likely a bad one), or when every PR its message names (`owner/repo#n` or a PR URL; a bare `#n` may be a step number and names none) is merged or closed in the PR cache (a PR the cache does not know keeps it); each such close is an `item_resolved` event on the timeline naming the rule (`answered`, `superseded`, `session_gone`, `agent_gone`, `prs_closed`). A prompt in another pane of the same session does not close it. An `approval`, `question`, `login`, `run_command` or `verify_failed` item raised before the session's latest prompt is **missed**: the inbox pins it to the top with a red edge, because typing the next prompt usually means the question above it went unanswered.

A session's **badge** is the number of Needs you items on it, the same list its session page shows. The badges plus the items that belong to no session (sync health, items added without a session) are exactly the Inbox count, and every item is a live condition (or an open turn-based item), so the two never disagree. Nothing local such as a "last opened" mark hides an item from the count: an item leaves only when what it points at settles, it is answered, or it is dismissed; the human never has to clear what reality already settled. A reload skills-sync could not send is retried before it is yours. skills-sync retries only every 15 minutes, so after each collect the dashboard sends the reload again itself, through skills-sync's own `broadcast --only` (same lock and idle checks), to each pending terminal whose session is idle, at most once every 2 minutes per terminal. A terminal that stays busy is not retried and gets the reload once it is idle, so it is nobody's to fix. Only when an idle terminal refuses `reload_alert_tries` retries in a row (config, default 3; the count starts over when the collector starts) does the reload become a `reload_pending` item, on the session of the terminal it could not reach and titled with that terminal; one for a terminal Orca no longer lists is dropped. Once it lands, skills-sync takes it out of `reload-pending.json`, the item goes, and the timeline gets an `item_resolved` line (`reload_delivered`, or `reload_dropped` when the terminal is gone). Orca's own per-worktree unread flag is a yes/no without a count, so it is not used.

### Self-check

The server checks its own output on the fleet loop: every 5 minutes `scripts/selfcheck.py` reads Orca's worktrees, terminals and inbox, GitHub's state of every PR the page shows open, and a search for every open PR of mine, and compares them with the state it serves. A difference counts only when a second read 90 s later still shows it, because the collector trails Orca by up to one tick. What counts becomes one `selfcheck` item per class, with no session, listing up to ten subjects; the item's key comes from the subjects it names, so a lasting mismatch stays one item and a new set of subjects is a new one. It goes once a read no longer finds it. The check raises items and nothing else: it never changes the state it checks.

| Class | Counted when |
|---|---|
| `session_missing`, `session_ghost` | A worktree Orca lists (by the collector's own rule for main worktrees) is not a session, or a session's worktree is archived or gone. An empty worktree list is taken for a bad read |
| `tree_parent` | A session's parent is not in the list, the root has a parent, or following parents never reaches the root |
| `pr_state` | A PR shown open (a row or a PR item) is merged or closed on GitHub, and the collector probed it after the change or the change is older than the slowest probe tier plus 90 s |
| `pr_missing` | An open PR of mine that a fresh search returns is not on the page, and it was opened more than one PR tick plus 90 s ago |
| `pr_stale` | A PR shown open has an older last update than GitHub's, and GitHub's is older than one PR tick plus 90 s (the slowest probe tier plus 90 s for a PR that is not mine) |
| `item_answered` | A turn item's pane has started a later turn, or finished one with a different last message |
| `mail_answered` | A question mail item has a reply in its thread |
| `prompt_gone` | A `permission` item's agent is no longer waiting |
| `item_session_gone` | An item names a session that is not in the list |
| `activity_stale` | A session's shown last activity is more than 5 minutes behind its worktree's or agents' own times up to when the state was made |
| `item_text` | An item's title is empty or prints `None`, `null`, `undefined` or `NaN` |

What only the browser shows (the order of the Needs you card, a page still running old code, a failed avatar) is not in this check.

### PR events for the rest of the platform

Every change a full read finds is appended to `pr-events.jsonl` in the state directory, one JSON line (under 4 KiB, one write) per event: `{v, id, at, repo, pr, kind, url, owner, owner_kind, actor, checks, head}`. `kind` is one of `review_comment`, `changes_requested`, `approved`, `review_requested`, `head_pushed`, `approval_stale`, `checks_failed`, `checks_recovered`, `merged`, `closed`; readers ignore kinds they don't know. `owner` is the live dispatch that owns the PR's session (`owner_kind: dispatch`), or the session's agent terminal (`terminal`, or `human_session` for a standalone session). The file rotates to `pr-events.jsonl.1` past 5 MB. The dashboard itself only displays these; whether a session is told about them is up to the orchestrator.

### Decisions the orchestrator or lead waits on you for

A decision the orchestrator or lead reaches in its own analysis, or hears from a subagent, never becomes Orca mail. The orchestrator or lead registers it with `orch decide` (`scripts/prog.py`, stored in `decisions.json` in the state directory, masked on write):

```sh
orch decide add --title "Export format for the first release" --body-file /tmp/why.md \
  --option "CSV::finance opens it in a spreadsheet" --option "JSON::one format for both" --recommend 1 [--link URL]   # prints d7
orch decide list
orch decide done d7 --answer CSV     # answered
orch decide drop d7                  # no longer needed
```

The item sits on the session whose terminal ran `add` (`$ORCA_TERMINAL_HANDLE`), or on the root. It shows the title, its age, the body's first line, and one button per option, the recommended one with a check; an option's description is its button's tooltip. A line under them shows how to answer in the orchestrator's or lead's terminal instead (`d1: <answer>`, with the recommended option as the answer when there is one). Nothing expands in place. An option button, or the answer box, takes a second click and then answers it through `POST /api/fleet/decide` (same Host, Origin and token guard as the send box). The answer is recorded first: the decision is closed as `done` with that answer, as `orch decide done` would, and leaves Needs you on the next poll. Then `decision <id>: <answer>` goes to that terminal (or the root's agent terminal) through the send box's idle check and log below. A busy orchestrator or lead does not fail the answer: the page says "Recorded; will relay to the orchestrator or lead when it is idle", the decision keeps `delivered: false`, and each later collect retries the line, skipping while the session is working, until it is typed once (a line typed but not confirmed counts as delivered, so it is never sent twice). `orch decide list` prints such answers, the orchestrator's or lead's own `done` stops the retry, and `drop` cancels it. Dismiss only hides the item on this page.

An answer typed into the orchestrator's or lead's terminal closes the decision too. The plugin's `UserPromptSubmit` hook (`hooks/decision.py`) reads each prompt of the terminal whose handle registered the decision, and of no other: a worker's prompt never closes the orchestrator's or lead's decision. It closes one as `done` only when the prompt is unambiguous: its first line starts with the id (`decision d3: yes`, `d3: yes`, `d3 yes`; the rest of the line is the answer), or starts with an option label of two or more characters, not followed by a letter or digit, that exactly one of the terminal's open decisions has (the longest matching label wins). The hook then tells the orchestrator or lead the answer was recorded. Any other prompt closes nothing and only adds a reminder listing that terminal's open decisions (at most five) to the prompt's context. Decisions registered outside Orca have no handle and are never closed this way. The hook is loaded when a session starts: a session that was already running needs `/reload-plugins` or a restart first. Any error exits 0, so a prompt is never blocked.

### Owner requests

In a long orchestrator or lead session the human's small requests (check this, merge that, reply there) were lost when several arrived at once. The plugin's `UserPromptSubmit` hook (`hooks/request_log.py`) appends each human prompt of a session declared orchestrator or lead (read as `hooks/reorient.py` reads it, including a `ROLE:` line in that same prompt) to `requests.jsonl` in the state directory (0600): `{ev: "open", id: "r7", ts, session, handle, text}`, the text without its ROLE line, masked and cut to 200 characters. Not logged: harness rows that start with `<` (a `<pasted_content` block is the human's), a slash command as typed (`/brief`), an answer to a dashboard decision (`d3: yes`, recorded by `hooks/decision.py`), a card's relayed report (`[card-name] …`; `[Image #1]` and `[Pasted text #1]` are the human's), the compaction preamble, a prompt that only declares a ROLE, and an empty one. Orca's `You have N orchestration message(s). Run …` nudge is cut out wherever it was typed, mid-sentence too, and the rest is judged as above (`hooks/decision.py` cuts it the same way, so an answer typed after it still closes its decision). The same text again from the same session while its last request is still open is not a new request. The hook answers in one line of ids, about 80 bytes: `Owner request r12 logged · 7 open, oldest 3h05m · unlinked 30m+: 2 (newest: r5 r2)`, the last part counting the open ones left unlinked for 30 minutes (`UNLINKED_MIN` in `scripts/owner_requests.py`) and naming the newest five. The texts are in `orch req list`.

The fleet tick settles what it can prove (`reconcile` in `scripts/owner_requests.py`), appends those rows with `by: "auto"` and puts each on the timeline (`request_link`, `request_done`, `request_drop`):

- **Link.** A card the asking session's worktree parents, a board row its terminal added (`orch board set` stamps new rows with `created_at` and the terminal), or a PR its session owns, made within 30 minutes after a request (`LINK_WINDOW_MIN`). Only when exactly one request from that terminal falls in those 30 minutes: a turn that answered two prompts cannot say which one a card was for, so it links neither. A chatty request, under 20 characters and either a question or an acknowledgement or thanks (`chatty()`), asked for no card: it is never linked and is not counted. A child row added with its parent rides on the parent's link.
- **Done.** Every link of the request is over: `owner/repo#N` merged in the PR cache (one closed unmerged did not land its work, so the request stays open and the dashboard tags that link `closed`), `card:<name>` whose worktree was seen live on an earlier tick and is gone now (a card renamed in Orca keeps its worktree, so it is not gone), a board row (`<id>` or `board:<id>`) marked done. One link in any other form (a ticket, free text) leaves the request to you.
- **Drop.** An unlinked request whose terminal Orca has not listed for 10 minutes over at least two reads (`GONE_MIN`; one read can miss a few terminals), with `reason: "auto: its terminal is gone"`.

A request the owner reopened (the dashboard's `Reopen`, `orch board reopen req:<id>`) is judged only on the links made after the reopen (`live_links()`): the earlier ones were all over when it was reopened, so they would close it again on the next tick. With none, the tick neither closes nor drops it. `tidy.py` judges it the same way.

Nothing card- or terminal-based happens on a tick whose Orca read failed or listed no session. A tick that fails to settle the ledger leaves the dashboard up and puts one `requests_error` on the timeline, again only when the error changes; the error is the `requests-reconcile` job's record (orch-dash jobs, below). An unlinked request open for 12 hours (`TTL_MIN`) expires: no row is written and it can still be linked or closed, but the counts, the hook line, the report line, the dashboard and `orch req list` leave it out (`--all` shows it as `[expired]`; the tile and card say how many expired).

```sh
orch req list [--all] [--json]
orch req link r7 acme/app#12            # where it went: a board row, card:<name>, PR or ticket; may be given more than once
orch req link r7 r8 card:login --done   # several at once; --done closes them too (--evidence, or "settled at <WHERE>")
orch req done r7 r8 --evidence "merged at 1a2b3c4"
orch req drop r9 --reason "a remark, nothing asked"
```

`link`, `done` and `drop` are lines appended to the same file; a request's state is the fold of its lines, and a closed one refuses another close, and an auto row never overwrites one: it is skipped when the file has moved on. Nothing is written to a tracker: a ticket key in `link` is a pointer. The periodic report (`orch brief render --periodic`) adds `📝 요청 — 요청 N건 열림 · 가장 오래된 것 X · 미연결 30분+ M건` while any is open, and the fleet Overview shows a Your requests tile (open count, oldest, unlinked ones in amber, how many expired) and card. A card row gives the request's text the row's width, three lines until clicked (as the Timeline opens a cut line), with its links and age on the line below it; a pasted prompt shows without its `<pasted_content>` tags, which the stored text keeps. There is no Stop hook for requests: a nudge could not be shown never to loop, and a hook dropping a request answered in its turn could not tell an answer from an acknowledgement, so an unlinked request expires instead.

### Acks: hiding what you have checked

Without it, finished rows and requests only pile up: a ✅ row stays in the stage table, and the requests card keeps every open request, long after the owner has looked at them. An ack is the owner's "checked, stop showing it". `scripts/acks.py` keeps them in `acks.jsonl` in the state directory (0600, append-only, `{ev: "ack"|"unack", id, ts}`; the set is the fold of its lines), and the CLI is the record: the orchestrator or lead runs it, and the page's buttons call the same function through `POST /api/fleet/ack` (same Host, Origin and token guard as the send box).

```sh
orch-dash ack stage:release board:lead-a r7     # a stage row, a brief row, a request (rN is req:rN)
orch-dash ack --done-before 24h [--kind req|stage|board]...   # all that finished (a request: was asked) before then
orch-dash unack r7                              # show it again
orch-dash acks [--all] [--json]                 # what is acked; --all adds what could be
```

Only what is over can be acked, and the check is the server's, not the page's: a stage row with `done_at` whose rows below are done too, a brief row that is ✅ and, for a head row, every sub-task row under it. A row in progress, waiting on someone, held (a row whose PR closed unmerged is ⏸️ `PR 닫힘`) or asking for a decision is refused with the reason, and the rest of a batch still goes in (the command exits 1 when any id was refused). Requests are the owner's own, so every one still shown (open and not expired) can be acked. An ack hides, it does not close: an acked request is still open in `orch req list` and in the orchestrator's or lead's hook reply, an acked stage row is still in the stage table until its 7 days are up. Acking a request does not close it; `orch req done r7` (or `drop`) does. An ack holds only while what it hid is still over: a new open row under an acked group or head row shows it again, and it hides again once that row is done too.

Where it shows: the Stages page (`Ack all N` inside an opened program's finished rows, `Show N acked` above the table) and the Your requests card (`Ack all N` and `Show N acked`), each finished row and each request an `Ack` button (`Unack` while shown); acked rows under an acked group hide with it (the brief reply's `stages` marks each row with `ack`, one of `acked`, `ackable` or null, and `hidden`). On the Brief page each ✅ row of a repository table has its `Ack` at the row's right end, and once acked the row leaves the table with an `Undo` above it until the next inline click. The Finished rows card below lists only the finished rows without an `Ack` in the table (acked ones, `Unack` while shown, ✅ rows folded after 24 h, and those of a table that got no buttons), with `Ack all N` over every one. `orch brief render` leaves acked rows out of its tables (a repository left with no rows loses its table), says `확인함으로 숨긴 행 N` in the headline (acked rows leave before the 24 h fold, so `접음 완료 N` counts only rows not acked), counts only unacked requests in `📝 요청`, and its `--json` carries `done`: every ackable row with `id`, `label`, `at` and `acked`, and `table_rows`: per repository table in the text, per row, `{label, ack, done}`, the id that row's button posts or null. The page has only the text, so it places the buttons by position, and a table whose row count differs from its entry gets none.

### Closing what is over

An ack hides a row that is already ✅. What is over but never turns ✅ by itself would otherwise stay listed: a doing item whose card's PR merged, a card with no agent left listed under 보류 as `세션 꺼짐`, a held item whose trigger already fired, a moving row nobody has touched in half a day, an inbox item about a PR that has merged. `scripts/tidy.py` closes these on evidence, and the owner closes the rest by hand. Every close goes to `tidy.jsonl` in the state directory (0600, append-only, `{ev: "done"|"reopen", id, ts, by: "auto"|"owner", rule, evidence, label}`; an id's state is its last line). Labels and evidence are masked, never shortened; a `--why` that would push a line past 4 KiB is refused. Past 5 MB the file moves to `tidy.jsonl.1` and each id's last line is copied into the new file (`carried: true`, left off the timeline), so a close or reopen outlives the rotation.

```sh
orch board open [--json]                     # what can be closed now, with the 확인 필요 reason where there is one
orch board done row:lead-a card:<session> doing:<k> later:<k> do:<k> todo:<k> [--why TEXT]
orch board reopen doing:<k> ext:<key> req:<id> # undo; the tick does not close it again on the same evidence
orch board tidy [--dry-run] [--json]         # one pass of what the collector runs after every tick
orch board closed                            # every close with its rule and evidence
```

Ids: `row:<board row>`, `card:<session id>` for a row built from a session, and `doing:`, `later:`, `do:` (a to-do), `todo:` (a line of the owner's now-list) with a digest of the item's words (rewording an item makes a new one). The CLI is the record; on the Brief page a repository table row that is 🙋, ❔ or 🔴 has `Done` at its right end (🔄 ⏳ ⏸️ rows are still moving and get none; that is the brief's button rule, set where `board.py` builds `table_rows`, not `orch board done`'s: it and `POST /api/fleet/done` close a 🔄 row like any other open one) with the same `Undo` after it, the Open items card has a `Done` button per row, card and board item, and its Closed in the last 7 days card a `Reopen` per close that can be undone, both through `POST /api/fleet/done` (same Host, Origin and token guard as acks). Only what `orch board open` lists can be closed; anything else is refused with the reason. The tick also closes `ext:<inbox key>` and `req:<request id>`: reopening one adds the inbox item again or opens the request again with its links, and one whose inbox line or request is gone has no `Reopen`.

The collector runs `tidy.run()` after each tick, so nobody has to ask. It closes only on evidence it can check, and a text names a PR only by its full `owner/repo#N` (a bare `#N` could be any repository's) and a card only by its hyphenated name standing alone in the text, one card at most:

| What | Closed when | Only marked 확인 필요 when |
|---|---|---|
| A doing item | every PR it names, or its card's PR, merged | the card still works, or its next step has a step after the merge (a release, a deploy, an apply) |
| A card row (no agent, or its session off) | its PR closed unmerged | — |
| A line of the owner's now-list (`orch board now`) | every PR it names merged or closed | — |
| A to-do (`orch board todo`) | never: when its date or condition comes it moves to 지금 오너가 할 일 or 진행 중 and waits for Done | — |
| An inbox item from `orch-dash inbox add`, of a type the owner does not act on | every PR it names merged or closed, or its session is gone from Orca's list | — |
| A request (`orch req`) with links | every link is over and one of them was closed here (a board row or card); the fleet tick's own request pass closes the rest | — |
| A held item | never: a fired trigger means the work is due | its trigger says merge and every PR or card it names merged; it moves from 보류 to 진행 중 |
| A moving board row | never | no word for 8 hours, no PR, and no card that has one or still works |

What the owner or another person acts on is never closed by the tick: a doing item waiting on a person (`--waiting`) or the owner's, a decision, an added inbox item of type `approval`, `run_command`, `login`, `verify_failed`, `question` or `decision` (whatever its PR or session), and a row whose status is the owner's turn or another person's wait. Anything less certain stays, marked 확인 필요 at most, since closing something still open costs more than listing something finished. A decision closes when it is answered and a Needs you item backed by a live condition (a prompt, mail, a gate, a PR) leaves when the condition clears. A `run_command` item stays until resolved: nothing here can tell that a command was run.

The report reads the same file and evidence. A closed item leaves 진행 중, 할 일, 보류 and 지금 오너가 할 일; a closed row or card is ✅, so the ack and the done-row rules take it from there. A card with no agent and an open PR shows what the PR waits on instead of `⏸️ 세션 꺼짐`: a requested reviewer under 다른 사람 대기 with `다음: <reviewer> 리뷰 대기`, a review GitHub requires and nobody was asked for as 🙋 `리뷰 요청`, a draft as 🙋 (make it ready, or write the held item that holds it), anything else as 확인 필요 `세션 꺼짐 · PR ✓`. A row that would be ⏸️ or ❔ and whose open PR a held item names (as Needs you's held rank reads it) is ⏸️ (a card stopped at a prompt stays 🙋), listed once under 보류 on that item's line with the row's name. A board row about a card (its id or title is the card's name) takes the card's PR, so the card is not listed a second time, and a doing item about a card that has a row is that row's line, with the doing item's next step when the card's says only `작업 중` or `턴 종료`. The collector keeps every PR the board, its items or the inbox names in `prs.json` after the search stops finding it, so such a row shows its state instead of `?`. Each close is a `tidy_done_auto` (or `tidy_done`, `tidy_reopen`) line on the timeline, naming the item, the rule and the evidence.

### The stage table

The per-item stage table (merge / dev check / prod check) that the `stage` skill prints with `orch stage show` and the `brief` skill points to in one line is a record, not something rebuilt for each report. Most of it is generated; the rest is written by hand where only a person or agent can check it.

**Generated rows.** After each collect the fleet tick rewrites `stages-auto.json` in the state directory (`scripts/stage_auto.py`) from what is already recorded, for every program under `$PROGRAMS_HOME` whose ledger had an event in the last 7 days (`_`-prefixed directories are not programs):

| Row or cell | Evidence |
|---|---|
| Group row `<slug>` | the program |
| Row `<slug>/<item>` | each `predicate` item in `program.json` (`item` true) |
| Row `<slug>/<ticket>` | each task of the program's Orca Run (`run`) whose ticket is not a predicate item; the ticket is the task title's first word before `:` or a ticket-ID-shaped first word, `<ticket>-<second word>` when the ledger names that ticket (`ABC-1 K4: …` spawned as `ABC-1-K4`). A task with no ticket, or a second task of a ticket another task already holds, is `<slug>/<task id>`: two cards of one ticket are two rows. A `<ticket>-<second word>` whose second word is a word of exactly one predicate item (`K4` and `P6-K4-ready`) is that item's row, with the ledger's PRs for `<ticket>-<second word>`. A task keeps the row it got first: `stages-auto.json` stores each task's row (`programs.<slug>.cards`), so a task that leaves the Run keeps its row, and a later task of its ticket keys by its own task id instead of taking that row's id, ack and PR. The row's `title` is cut at 120 characters for the text brief; the page shows `title_full`, or a recorded title over it |
| PR | the ledger's PR for the ticket (an event with `pr` and `ticket`), a PR the collector saw the card's worker session own (the session matched to its task by task id, else by task title when no other task shares it), or a PR of the program's repository created after the program started whose head branch ends in `/<ticket>`; with several, an open one before a merged one. Cards are archived once their work lands, so the collector keeps every PR a program's ledger names, with its branch, after its search stops finding it |
| merge | ✅ with the 7-character merge sha: a ledger `landed` event's `sha` (a fold onto another PR's branch, `onto`, does not count), else the PR's merge commit in the collector's list. An open or closed PR, or none, leaves it empty |
| dev, prod | empty: nothing the tick reads records a release or a deploy |
| `done_at` | the program's current `predicate_verified` (one after its last landing, as `orch status` counts Close); its rows then fold like any finished row and leave after 7 days |

A cell with no evidence stays empty; the tick never infers one. A generated row's merge cell is evidence or nothing, unlike a recorded row's (below), which says what holds the PR.

**Recorded cells win.** `orch stage set <slug>/<item> --col dev …` writes a cell over a generated row (stored in `stages.json` as a cells-only overlay), and in the table it replaces the generated cell of that column. A person or agent writes a cell only after checking what the tick cannot see (a deploy, a release on another system) or to correct it, and the tick rewrites its rows every minute, so the other way round would erase that check within a minute. An overlay whose generated row has left (its program went quiet) draws nothing. A group row takes no cells.

**One row per piece of work.** A recorded row (`orch stage add … --pr`) whose PR is a generated row's folds into that row: its title and cells over the generated ones, the rows under it moved along, and a recorded group left with no row of its own goes. So does a recorded row with no PR under a program's group whose id, past the group's, names words of exactly one predicate item (`<slug>-release` under `<slug>` folds into `<slug>/P1-release`); when no item or several match, or another recorded row already took that item, both rows stay. Writing cells on the generated id (`orch stage set <slug>/<item>`) needs no matching and is the way to record against a predicate item. The fold happens on each read, so `orch stage set` on the recorded id still works.

**When the table falls behind.** On each prompt of a declared orchestrator or lead, `hooks/reorient.py` appends one line to its role reminder when an active program (the lead's own; any for the orchestrator) has no stage row or its rows were not rewritten in `board.stage_warn_h` hours of the dashboard config (default 6), which means the dashboard server is not running. Active: generated in the last tick, not finished and not stopped (no ledger `stop` after its last `resume`). The hook reads files only.

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

### Shared resources

`orch hold <resource>` records who uses something lanes take turns on (`browser`, `grafana-login`), and `orch release <resource>` gives it back. It has the exclusive lane's shape (`scripts/holds.py`): one file per resource under `$PROGRAMS_HOME/_locks/`, beside the lane locks, and `lock_acquired` / `lock_released` rows in `_locks/holds.jsonl`. `hold` refuses while someone else holds it and names them; holding again refreshes your own. A hold not refreshed within its `--ttl` (120 minutes) belonged to a session that died holding it, and the next reader breaks it and logs `broken after`. `release --force` frees another's hold and logs who did. `orch hold --list` shows every hold and lane lock; the fleet collector lists the same into `state.json` `holds`, each on the session whose terminal handle holds it, and the Sessions page shows them under its table.

### Automations

Every runs cycle the fleet collector also lists Orca automations (`orca automations list`, then `orca automations runs --id` for each) into `state.json` `automations`: name, schedule, enabled, next and last run, and the six newest runs with their status and a masked one-line summary. `sources.automations` says whether the last listing worked. The Automations page draws them. Only Orca automations are listed here: a session's own `CronCreate` jobs live in that session, and launchd jobs are outside the fleet. The janitor and its precheck are Orca automations, so they are in this table once registered.

### orch-dash jobs

What orch-dash runs on its own clock is listed on the same page, below the Orca automations, from `scripts/jobs.py`. Each job records its run as it runs (`jobs.record()`) into `jobs.json` in the state directory, replaced whole under `jobs.lock`: when it ran, whether it failed and the error (masked, whole), what it did in words, how many it closed, linked or added and when it last did any, and when its own loop runs it next at the latest (a wake-up can run it sooner). Nothing else writes the file, so a job that has not run in this store shows `no record`, never an estimate. `record()` never raises: a record or timeline line it cannot write goes to orch-dash's stderr, and the job and its loop carry on.

| Job | Cadence | Records |
|---|---|---|
| `fleet-tick` (Fleet tick) | every 10 s while a page is open, else 60 s | the Needs you count; a source that failed stays the sources bar's, the tick itself ran |
| `requests-reconcile` | each fleet collect (rides on `fleet-tick`) | links, dones and drops written (Owner requests, above) |
| `tidy` | after each fleet collect (rides on `fleet-tick`) | items closed (`tidy.py`) |
| `relay` | after each fleet collect (rides on `fleet-tick`) | decision answers relayed and skill reloads sent |
| `selfcheck` | every 5 min, 90 s while a finding waits for its second read | the self-check's Needs you items |
| `skill-usage` | every 15 min | new usage signals |
| `pr-stats` | every 6 h, 1 h after a failure | that the counts refreshed |

Two launchd jobs keep a record of their own and are read as they are: skills-sync from `sync.json` beside the state directory (its `interval` gives the next run) and reap-hard from its last pass (`reap_hard.read_last()`: closed and kept counts, a failure count or the error that stopped it; its notes are not shown); a record it cannot read is an error on that row only. A failed run of an orch-dash job puts one line on the timeline in the fleet's event format, `job_failed` (`requests_error` for the reconcile), when the job starts failing or its error changes, not every run it keeps failing; the record keeps that line's time and the job's row links to the Timeline with it. The line carries the job's key and is cut to fit the 4 KiB event line (`EVENT_TEXT_BYTES`, counted as written, in JSON-escaped UTF-8), so it links back to the Automations page, where the row holds the whole error, two lines until clicked. The row holds the latest error only: once the error changes, a superseded line's cut text is all that is left of it. The launchd jobs write no timeline line from here: a stopped sync is already a Needs you item.

The table's columns are name (with a small `orch-dash`/`launchd` badge), how often in short (`10s`, `5m`, `6h`; the sentence above on hover), last run, result and next run. A job that rides on another (`group` in `dash.JOBS`) folds into that job's row, which names the riders that changed something or failed, counts the rest as `no change` and `no record` apart (`3 more: 2 no change · 1 no record`), and opens to one line each. A run that counted 0 says `no change` with its words in grey under it; one that counted more is bold; the last run that changed anything sits under the result. A job with no record is one grey line with the reason (reap-hard: its launchd agent is not installed here or has not finished a pass).

Rules applied whenever data is read or drawn have no run, so they are listed apart with the value in force: the 7-day inbox item expiry, the 12-hour request expiry, the brief's ✅ fold (`board.done_fold_h`) and the stage table's idle fold (`board.stage_idle_d`), and the 7 days a finished stage row stays. `GET /api/fleet/jobs` reads `jobs.json` on every poll rather than through `state.json`, which a tick that fails never rewrites.

### Monitors

In the same cycle the collector lists the background watches into `state.json` `monitors`, without a model and without touching the processes. Live sessions come from Claude Code's `sessions/<pid>.json` under every config dir in use, whose pid and `procStart` must match `ps`: `$CLAUDE_CONFIG_DIR`, `~/.claude` and each Orca profile's (`~/Library/Application Support/orca/claude-profiles/*/home`), since Orca starts every agent with its profile's own `CLAUDE_CONFIG_DIR` and the session registers there only. `ORCH_CLAUDE_HOMES` (paths separated by `:`, globs allowed) replaces that list. The transcript is looked up under the dir the session registered in. The orchestrator and lead roles (`declared_roles`) and Sent read the same sessions. Each session's transcript gives every Bash run with `run_in_background` and every `Monitor`: the start, each event, and the task notification that ended it with its status and exit code. A notification that arrives while a turn runs is written only as a `queue-operation` enqueue line and a `queued_command` attachment, not as a user turn, so all three are read; the first one dates the end. Each tick reads only what was appended since the last one. A task without an end record is `running` only when a shell the session started directly runs exactly its command (the `eval '…'` payload, not a substring) and started within 15 seconds of the task; a foreground run of the same command does not count. Otherwise it is `lost`, because stopping a task from the UI, a Monitor's expiry and a teardown leave no record. A Monitor past the expiry it was given counts as ended. A task that failed or exited non-zero is `dead`. One `killed` or `stopped` was ended on purpose (the UI) and counts as ended; so does one named by a successful `TaskStop`, which writes no notification, only its tool result. A dead task whose notification was written as a user turn or an attachment reached the session's model and is marked `seen`: the page lists it without a ▲. One still only in the queue is unseen. A dead or lost task stays listed for 6 hours, or until the same command starts again in that session; a cleanly ended one is not listed. A transcript that ends in a `continued-in` line is skipped: its session goes on in a new transcript that repeats the tasks it started. `sources.monitors` says whether the last listing worked; its `warning` names a live session whose transcript was found neither under its cwd's directory nor by session id. Commands are masked before they are written, including a URL's password, `-p<pw>`, `--password <pw>` and hyphenated header names. Not collected: tasks inside a subagent, a session's `CronCreate` jobs, and launchd jobs.

### Sent instructions

`orch sent add --to WHO (--text-file F | - | --text T) [--kind K] [--channel HOW] [--repo R] [--subject S]` logs one instruction as it is sent: to another session or card, a subagent, an inbox, Slack or a tracker. Pass the text in a file or on stdin (`-`); `--text` puts it in the process list and in the shell's quoting, so it is only for a short string with nothing secret. The text is kept as sent, one JSON object a line in `<fleet state>/sent/YYYY-MM-DD.jsonl` (UTC day, the file 0600 in a 0700 directory): `ts, from, to, kind, channel, repo, subject, text`. `from` defaults to `$ORCA_TERMINAL_HANDLE`. `kind` is one of `card terminal inbox subagent agent slack tracker other`. The log is the only source that outlives the sending session and the only one for Slack and tracker sends.

The page adds two sources that need no logging. The dashboard's own send box (`sends.jsonl`, lines that were typed). And, each runs cycle, the transcripts of the live Claude Code sessions (found as for Monitors, read incrementally): an `Agent` call's prompt, a `SendMessage`, and in a Bash command every `orca terminal send --text`, `orca worktree create --prompt` and `orca orchestration send` (`--body`, else `--subject`; heartbeats skipped), parsed with `shlex`. A command shlex cannot split, or a send without its text, is skipped; a text passed as `"$(cat file)"` shows as that, since the file is not read. A row from a transcript or the dashboard whose text matches a log row within 10 minutes is folded into that row, which then names both sources. An ended session's transcript rows leave the page with it. `GET /api/fleet/sent?days=N` (default 1, 1 to 30) returns at most the newest 1000 rows, each through `mask()` (text, subject, sender, receiver, channel, repository, cwd); a line of the wrong shape in either log is skipped, and any other failure comes back as `{"error"}`; `orch sent list [--days N] [--json]` prints the same. Each poll reads only what the log gained since the last one, and the collector drops transcript rows older than 30 days.

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
| `requests.jsonl` | Owner requests and their links and closes (Owner requests, above); append-only |
| `stages.json`, `stages-auto.json` | The stage table: what `orch stage` recorded, and the rows each tick generates per program (The stage table, above) |
| `jobs.json` | Each orch-dash job's last run (orch-dash jobs, above) |
| `sends.jsonl`, `adoption.jsonl` | Send and prompt-answer attempts; adoption writes and undos |
| `prompts/` | The latest permission request per terminal, from `hooks/permission.py`; dropped after a day or once answered |
| `avatars/` | Reviewer avatars |

The optional config is `$ORCH_FLEET_CONFIG` or `~/.config/agent-skills/dashboard/config.json`:

```json
{"root_worktree": "<orca worktree id>", "adopt": {"write_orca_parent": false},
 "include_main_worktrees": false, "bots_in_inbox": false, "reload_alert_tries": 3, "board": {"stage_warn_h": 6}}
```

`board` holds the brief renderer's limits (`state_min`, `done_fold_h`, `stage_idle_d`, … in `scripts/board.py` `LIMITS`) and `stage_warn_h`, the hours after which the reorient hook says a program's generated stage rows are stale (default 6).

Everything collected passes through one masking step before it is written or shown: GitHub, Slack, Anthropic, OpenAI and AWS token shapes, JWTs, `Authorization`/`Bearer` values, and `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_API_KEY`, `*_CUSTOM_HEADERS` assignments. No credential is read or stored: `gh` uses its own login. `ORCH_FLEET=off` turns the fleet collector off; `orch-dash demo` and the tests point `ORCH_FLEET_STATE` at a temporary directory and replay an invented machine.

```sh
orch-dash fleet                          # one collect, prints a summary
orch-dash inbox add --type login --title "Log in to the registry" [--session <worktree id>] [--url URL] [--command CMD] [--key KEY]
orch-dash inbox resolve --key KEY
orch-dash inbox list                     # added items not resolved, oldest first
orch-dash adopt [--apply] [--undo all|<worktree id>]
```

## Skill usage

`scripts/skill_usage.py` (`orch-dash skills`) reads the local Claude Code transcripts (`$CLAUDE_PROJECTS_DIR` or `~/.claude/projects`, subagent transcripts included) and the installed skills: this checkout's `skills/`, each plugin in `~/.claude/plugins/installed_plugins.json`, and `~/.claude/skills`. `serve` runs it when `<fleet state>/skills.json` is missing or 15 minutes old; the page reads `/api/fleet/skills`. `ORCH_SKILLS=off` turns it off.

| Trigger | Counted from |
|---|---|
| auto | a `Skill` tool call |
| chained | a `Skill` tool call whose `attributionSkill` is another skill loaded earlier in the same turn |
| slash | a human message carrying `<command-name>/<skill></command-name>`; a built-in command that is not a skill is dropped |

A **miss** is a human prompt (not a subagent's, a slash command, a notification, a compaction summary or an interruption marker; text inside a `<pasted_content>` block is ignored) that contains, as whole words, a quoted trigger phrase from a skill's description, with no invocation of that skill before the next human prompt. A phrase counts when it has two or more words, or four or more non-ASCII characters; phrases in a sentence starting with "Not", "Never" or "Don't" are skipped. It is a heuristic: read the number as "look here", not as proof.

| Flag | When |
|---|---|
| `unused_30d` | An installed skill with no use in 30 days. Shown here only: zero uses alone is not a signal |
| `retire_candidate` | No use for three 30-day windows, counted from its last use or from when the collector first saw it (both kept in the cache, so a deleted transcript does not age a skill), with misses or another skill quoting the same trigger phrase (`overlaps`) as evidence, and not rare by design: `RARE` in `skill_usage.py`, plus `rare` in `~/.config/agent-skills/skill-usage.json` for the user's own skills |
| `slash_only` | Used in 30 days, but only ever typed as `/name`: the description does not make it fire. A skill with `disable-model-invocation` set to true (`slash_by_design`) is never flagged: only a human can call it |
| `misses` | More than 3 misses in 30 days (`--misses N`), and at least a fifth of the prompts that carried its phrases (misses plus auto and chained uses): a skill that fires on most of them is not flagged for the rest |

Per skill, the report also keeps what a miss needs to be checked: the matched phrases with their counts (`miss_phrases`, top 3, text from the skill's own description), how many sessions they came from and the last one's time. `skill_usage.py misses <skill>` lists each miss in 30 days with its time, session id and phrase, and `claude --resume <session>` opens one. The page shows the count and top phrase on the chip and the rest on hover, and hides flags on sources this machine cannot edit unless *Flags on all sources* is picked. No prompt or message text is copied, repositories are counted, not named, and the incremental cache (`skill-usage-cache.json`, keyed by a hash of each transcript's path, checked by size and mtime) stores working directories as hashes. The first scan reads every transcript; later ones re-read only changed files.

### Signals for the improvement loop

Each collect appends one row per flag other than `unused_30d`, per skill, per ISO week to `$PROGRAMS_HOME/_skill-usage/ledger.jsonl`, only for sources the loop can edit (`agent-skills` and `user`). The row has the shape of a program ledger's `signal` row, with `kind` `skill_usage`:

```json
{"ts": "2026-09-27T14:51:28Z", "ev": "signal", "kind": "skill_usage", "skill": "agent-skills:swarm",
 "source": "agent-skills", "flag": "retire_candidate", "suggest": ["merge", "retire"],
 "evidence": "skill-usage:agent-skills:swarm:retire_candidate@2026-W39",
 "note": "retire_candidate; suggest: merge|retire; 0 uses in 30 days (0 auto, 0 slash, 0 chained), 0 misses, last used never; basis: overlaps agent-skills:arena; unused since first seen 2026-06-20T09:00:00Z"}
```

`evidence` (skill, flag and the ISO-week window) is the idempotency key, so a flag that persists shows up once a week. The collector writes only this file. The flow improver reads it as one input, and `reflect` promotes a signal into the lessons ledger (Source `skill-usage`, Kind `usage`, Evidence a pointer to the row and its window, one row per skill and flag whose Occurrences rise per window); no script writes the lessons ledger. `suggest` lists what may be proposed; retiring a skill is only ever proposed, never applied by the loop.

## PR stats

`scripts/pr_stats.py` (`orch-dash prstats`) searches GitHub with `gh api graphql` for every pull request created since `since`, one search per source per month (a month at the 1,000-hit search cap is split in halves until each part fits), and writes counts to `<fleet state>/pr-stats.json`. `serve` runs it when the file is missing or 6 hours old, and an hour after a failed collect, which keeps the last counts and adds the error the page shows above them; the page reads `/api/fleet/stats`. `ORCH_PR_STATS=off` turns it off.

The first source is always the PRs I authored (`author:@me`). Agents that open PRs on my behalf are added under `pr_stats` in the fleet config:

```json
{"pr_stats": {"since": "2025-01-01", "sources": [
  {"name": "Devin", "query": "author:devin-ai-integration[bot] involves:@me"},
  {"name": "Claude", "query": "in:body \"Requested by Your Name\"", "author": "claude"}]}}
```

`query` is GitHub search syntax added to `is:pr archived:false`; `author` keeps only hits whose PR author has that login, for a body line other bots also write. Search cannot OR qualifiers such as `author:` and `in:body`, so each source is its own search. A PR several sources find counts once, for the first. An agent whose commits carry my name is told apart only by what its PR shows (its author login or a line in the body), and `involves:@me` also matches agent PRs I only reviewed. `since` defaults to January 1 of last year.

Days, weeks (from Monday) and hours are in the server's time zone. A trend line is a least-squares fit without the last month or week, which is still filling. A campaign is one title on 10 or more PRs (`BATCH_MIN`); the "Without campaigns" view drops them. The file holds counts, repository names and campaign titles, no PR text.

## Freshness

The bar under the headline shows each source's last success, both as a local time and as a relative time that ticks every second.

| Source | Warn after | Stale after |
|---|---|---|
| Ledger, Orca | 60 s | 180 s |
| Tracker, GitHub | 3 × interval | 6 × interval |

When a source is stale, the panels built from it are greyed out. Treat a grey panel as possibly wrong, not as current. A closed or quiet program's unpolled sources are not greyed out.

Every table sorts by a column when its head is clicked. A relative-time column sorts by how long ago, so ascending is newest first. The second click reverses, the third returns to the view's own order, and the browser remembers the choice.

## Panels

- **Headline.** The Orca run objective, with one segment per predicate ticket underneath.
- **Overview**
  - The `next` line mirrors `orch status` step 4, for example "may spawn 1 more" or "SAFETY STOP: main is red".
  - KPI tiles: predicate done (with what the last open item waits on, and the current scope: predicate items plus admitted follow-ups, done out of total, and how many untriaged follow-ups may still join; a count, not a percentage, because the scope can grow), main (with the commit and, when the ledger note names one, its CI run), in-flight/cap with free slots, oldest open PR, PRs awaiting landing, landed in 24 h; human wait only under human-gate.
  - **Now.** Every in-flight worker and what its session is doing, from the tail of its transcript: a tool call (with its description or command), thinking, a finished turn waiting for input, or waiting for a wake-up it armed itself (ScheduleWakeup, Monitor, a background command). A pulse marks the ones moving; a row flashes once when its state changes. Workers without a matching transcript show only the available host activity.
  - **Stages.** Each top-level issue of the tracker project that has children, with its leaf tickets done (green) and in progress (moving stripes). Linear's list carries no parent links, so the tree is crawled with `tracker.py children` a few nodes per collect and cached in `tree.json`: a new program shows "counting N" for a few minutes. Canceled leaves are left out.
  - **Land order**, from `prog.land_order`, shown only while a PR waits to land or a base's exclusive lane is held. Each row shows:
    - position, PR and ticket
    - an age badge (amber over 2 h, red over 3 h)
    - a state chip: ready, catching up, blocked or waiting
    - the first reason it is not moving
  - **Landing.** Who holds each base's lock, from `lock_acquired`/`lock_released`. If there are no lock events, it reads "no one landing".
  - **Needs attention.** A red main, PRs waiting on the human gate, a worker on a permission prompt, stalled workers (turn ended 15+ min with its task open, nothing written 10+ min after dispatch, one tool call over 45 min, nothing written 20+ min after input such as a task-notification that never started a turn), free slots with ready tasks (only when `next` says "may spawn"), landed cards still open, ledger gaps (running dispatches never recorded, merged PRs the ledger never saw, landings without a main CI result), untriaged open follow-ups, the latest risk note, and source errors. `orch status` prints the same stalls, slots and gaps.
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

Workers without measured usage show `n/a`. Subagent transcripts in subdirectories are not counted.

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
