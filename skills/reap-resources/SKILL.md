---
name: reap-resources
description: "Use when the machine is slow or cluttered with what finished sessions and agents left behind — \"방치 리소스 정리해줘\", \"죽은 프로세스 정리\", \"메모리·CPU 누가 먹고 있어\", \"끝낸 orca 세션·worktree 정리해줘\", \"janitor\" — and for the resource steward's and janitor's rounds. Inventories first, reaps only listed targets, files go to the trash."
---

# Reap resources

Sessions end and leave things running or lying around: test
servers reparented to pid 1, the volumes and images of finished compose stacks, local branches
of merged PRs, git worktrees in the scratchpad of a finished session.

This skill needs no program. Finished Orca cards are removed with `orca worktree rm`, the same command the resource
steward uses (`orchestrate` `references/roles.md`), after moving the worktree's ignored files to the trash.

## Cards: take stock of every Orca card

"끝난 카드 정리해줘", or `/agent-skills:reap-resources` with it: every Orca card on this host, whatever made it,
gets one verdict: 삭제, 터미널만 닫기 (open PR), 남김, or 사람 결정. The rule and the traps it covers (a stale
`origin/HEAD`, a pre-rebase copy, idle shells holding the card, a screen that says "done"):
[references/cards.md](references/cards.md), the only place it is written.

```bash
C="${CLAUDE_SKILL_DIR}/scripts/cards.py"
python3 "$C" scan --plan <scratchpad>/cards-plan.json    # read only: 카드 | repo/branch | PR | 판정 | 이유
python3 "$C" apply --plan <scratchpad>/cards-plan.json   # after the person has read the table
```

Show the table and stop before `apply`: the person running it confirms. A 사람 결정 row is theirs to settle; `apply`
never touches it. From a shell the same two lines work with the checkout's path in place of `${CLAUDE_SKILL_DIR}`.
The janitor's `card` kind is this same code, so the scheduled round closes what `scan` would.

## Run

```bash
R="${CLAUDE_SKILL_DIR}/scripts/reap.py"
python3 "$R" scan --plan <scratchpad>/reap-plan.json    # read only: inventory + target list
python3 "$R" reap --plan <scratchpad>/reap-plan.json    # act on that list, nothing else
```

- `scan` prints a Korean report: per kind the count, targets, processes, RSS, CPU and oldest
  age; every target with its reason; what was kept and why; notes; alerts. `--json` prints the
  same data, `--plan FILE` saves it.
- `reap` re-scans and acts only on the plan's targets that are still targets with the same
  identity (pid and start time, branch tip). Anything that changed is printed as `건너뜀`.
- `--kinds orphan,docker,branch,worktree` limits the scan, `--hours N` sets the age
  threshold (default 6), `--repo PATH` (repeatable) picks the repos for `branch` and
  `worktree` (default: every repo in `orca repo list`).


## Janitor: what agents created

`card` is the section above. The other kinds look only at what an agent registered in the ledger, an Orca orchestration worker
made, or an agent made with `orca worktree create`, with or without `--parent-worktree` (Orca records
`cliProvenance` `created-by-cli`; a registration step skipped still leaves it). A worktree the user made in the app and the
main checkout never show up; anything not registered shows up
only as the unregistered-media lines of `evidence`, never as a target. `reap --plan` re-checks each
target and runs its action. A file or directory goes to the trash (`/usr/bin/trash`, macOS 15+, so Finder's
Put Back works; never another `trash` on PATH, which may read `-s` as "empty the trash"; else `~/.Trash`;
`$AGENT_SKILLS_TRASH` replaces it with a plain folder). The tool never deletes for good and never
empties the trash; the OS setting may (Finder's "Remove items from the Trash after 30 days"), and
scan and precheck report it under `## 경보`.

```bash
python3 "$R" ledger add --kind tmpdir --path /tmp/build-x --pr acme/app#12     # right after creating it
python3 "$R" ledger add --kind gui-app --pid 4242 --worktree <worktree path>   # pid + start time are recorded
python3 "$R" ledger add --kind chrome-window --id <window or tab id> --url <its URL> --pr acme/app#12
python3 "$R" ledger add --kind evidence --path /tmp/task/shots --pr acme/app#12   # a screenshot or recording, once it is on the PR
python3 "$R" ledger add --kind compose-stack --id <project> --worktree <card path> [--pr acme/app#12]   # `orch heavy` does this itself
python3 "$R" ledger add --kind compose-stack --id <project> --previewed            # the user has looked at the preview
python3 "$R" ledger add --kind compose-stack --id <project> --keep "<reason>" --until 2026-10-27T18:00+09:00
python3 "$R" ledger list
python3 "$R" scan --kinds janitor          # a plain scan leaves the janitor kinds out
```

The ledger is append-only JSON lines `{ts, run, kind, id, links: {pr, worktree}, by}` at
`~/.local/state/agent-skills/ledger.jsonl` (`--state` or `$AGENT_SKILLS_STATE` moves the folder,
`--ledger` or `$AGENT_SKILLS_LEDGER` the file). The last line per id wins. A link is "done" when its
PR (`owner/repo#N`, read with `gh`) is merged or closed, or its worktree is gone. For `compose-stack` a line with a PR or worktree link
is a registration and starts that id over (a stack brought up again after a reap retired it); a `--previewed` or `--keep`
line adds to it and keeps the owner links. `--pid` is recorded, never checked: the launcher exits and its pid is reused.

| Kind | Target when | Kept when | Action |
|---|---|---|---|
| card | [references/cards.md](references/cards.md) 삭제 or 터미널만 닫기 | 남김, 사람 결정 (reported, never acted on) | the listed terminals closed one by one; for 삭제, judged again, ignored files to the trash, `orca worktree rm --worktree path:<p> --run-hooks` |
| orca-worker | a row of `orca orchestration worker-list --terminal-state reclaimable` | - | `orca orchestration worker-release --dispatch <id>` |
| tmpdir | under a temp dir, its link is done, and no process has its cwd or executable under it | outside a temp dir, an open PR, no link, a process in it; a path already gone is not listed | `lsregister -u` each `.app` under it, then the trash |
| gui-app | the registered pid with the same start time still runs and its link is done | an open PR, no link | `kill -TERM <pid>` |
| chrome-window | its link is done and its URL is recorded | an open PR, no link, no URL (Chrome reuses ids after a restart, so without the URL the agent cannot tell it is the same tab) | `reap` prints `에이전트가 처리` and retires the id in the ledger, so it is asked once; the agent closes it with its browser tools only when the URL matches, and leaves it otherwise or when it has no browser tools |
| evidence | a file or directory of screenshots and recordings whose PR has been merged or closed for N hours (`--hours`) | an open PR, no PR link (a worktree link does not prove it was uploaded), a PR state or close time that could not be read, a directory holding anything but images and videos | the trash |
| compose-stack | in the ledger (`orch heavy` registers a project its command left with a config file in the card's worktree); its PR merged or closed (ledger link, else the worktree branch's own PR, an open one first, counted only when it carried the worktree's HEAD) or its worktree gone; every container stopped, or none started or stopped for `--hours`; on a host port in `reap.preview_ports` (default 4500–4599, hand-served previews) only after a `--previewed` line | an open PR, no PR, a PR it could not read, a `--keep` line before its `--until`, containers created after the latest registration (a new stack under a reused name), a stack nobody registered (the `docker` kind reports those) | `docker compose -p <project> down`, never `-v`: volumes stay. The id is retired in the ledger |
| remote-branch | interface only, never a target | always | - |

`evidence` also reports images and videos (png, jpg, jpeg, gif, webp, mp4, mov, webm) older than N
hours that nobody registered, one line per directory. It looks only directly in `/tmp` and
`$TMPDIR` and one directory below them (`reap.evidence_roots` replaces that list), never deeper and
never through a symlink. These lines are never targets, so they do not wake the precheck.

## User folders: explicit opt-in

The `user-folder` kind looks only at the folders explicitly listed in `reap.user_folders`.
Unset, empty or malformed configuration disables this kind. An entry is a target when nothing in it has been modified or changed
(mtime or ctime, so a freshly unpacked archive with old dates stays) for `reap.user_folder_days`
(default 7; 0 or less turns the kind off). A top-level screenshot or screen recording file (a name
macOS gives: `Screenshot `, `Screen Recording `, `스크린샷 `, `화면 기록 ` followed by an image or video
extension) waits `reap.user_folder_screenshot_days` instead (default 1; an invalid value falls back to
the general count). Kept: a download in progress (`.download`, `.crdownload`,
`.part`), an entry any of this user's processes holds open or uses as cwd (`lsof`; an unreadable list
keeps everything), a directory it cannot read all of or with more than 20,000 entries, and hidden
entries (`.DS_Store`). Targets go to the trash. The precheck sweeps them at most once a day.
While Finder empties the trash on its own, every entry is kept, with that reason, unless
`$AGENT_SKILLS_TRASH` names a plain folder: the user's own files would otherwise be gone for good in 30 days.

```bash
python3 "$R" scan --kinds user-folder
```

A folder the process may not read (macOS privacy) is a note, not an error: granting access is the
user's call.

## Program and hook state

`program` looks at `~/.claude/programs` (`$PROGRAMS_HOME`). A program is closed when its ledger has a
`predicate_verified` with no landing, predicate edit or admitted follow-up after it (orchestrate's own rule). A closed
one whose record has not changed for `reap.program_days` (default 7) moves to `<programs>/_archive/<slug>`, never
the trash: reflect reads old ledgers. The record is everything but `dashboard/` and the top-level `.json` caches,
which the dashboard and `orch status` keep rewriting for every program. Kept: an open program (the scan says its last
event, so the owner sees which to close), and any program with a file whose name contains `resume` or `handoff` (a
parked program waiting to be picked up).

`state-file` covers what the plugin's own hooks and sync leave in `<state>`: the reorient hook's per-session files
older than `reap.state_days` (default 7; a session alive past that only asks Orca once more) go to the trash as one
folder, and `sync.log` past `reap.log_max_kb` (default 512) is renamed to `sync.log.1`, the previous `.1` going to
the trash (launchd and cron reopen the log on each run).

New session files appear every day, so the precheck looks at program and state-file once a day, as it does user-folder.

## Scheduled run

```bash
python3 "$R" precheck    # janitor kinds + user-folder, program, state-file; writes <state>/janitor-plan.json; exit 0 when there are targets, unless they are exactly what the last reap failed on, less than a day ago, or the same set was planned reap.plan_repeat times with no reap since (retried once a day)
python3 "$R" precheck --load    # the same, but exit 1 at once unless the 5-minute load is at the limit
python3 "$R" schedule [--load]    # prints the `orca automations create` command; nothing is created without --write
```

`schedule` builds an existing-workspace automation on `17 */3 * * *` (every 3 hours) whose precheck is
`python3 <absolute reap.py> precheck` (its cwd is the repo's main checkout, so paths are absolute)
with `--precheck-timeout 300`: Orca's default 60 seconds is shorter than a precheck that reads `gh` and git for dozens of cards.
`schedule --load` builds the second job, `agent-skills janitor load`, on `27,57 * * * *` with `precheck --load` and the
same prompt: a loaded machine is when finished cards cost the most. Its precheck reads only the load average and goes on
to the full precheck when the 5-minute load is at `reap.load_limit` (default the CPU count), the periodic job is
registered and on (removed, or an unreadable automation list, counts as off), and no precheck of either job ran within
`reap.load_cooldown_minutes` (default 60). The cooldown holds back only the load job; the periodic one runs on its
schedule. It needs no pressure-watch LaunchAgent: Orca runs it.
`skills-sync setup` lists the two as `janitor` and `janitor-load`, `enable` brings a job whose trigger, precheck or
timeout differs back to these values, and `disable janitor` turns both off (`enable janitor` on a janitor that was off
turns a registered load job back on too).
Every command it builds starts with `python3`, with `--state`/`--ledger` after the subcommand when
those are off the default: a scheduled session can refuse a leading `env` or `VAR=` assignment.
Its prompt has the agent run `reap --plan <state>/janitor-plan.json` once, close the chrome-window
targets it can, and report what went, what was skipped and what stayed. To change the prompt of an
automation that exists, `orca automations edit <id> --prompt "<text>"`.
Run `schedule` from the checkout that loads the skills, never a card's worktree: the command keeps
that path. The run's model is whatever Claude Code settings its `--workspace` checkout has; without
`--workspace`, `schedule` also prints how to make a dedicated runner worktree for that
([docs/automation.md](../../docs/automation.md)). The janitor never lists the worktree an Orca
automation runs in (`orca automations list`); when that list is unreadable, scan says so in a note and keeps every Orca worktree that round.
`orca automations run` skips the precheck, so test it by running it directly, with
`--state` pointing at a scratch folder: a `reap` records what it failed on, and the next
scheduled precheck would skip that set for a day. A run marked completed is not a cleanup: judge it
by the reap output in its report.

- Before turning the automation on, auto mode must allow the `reap --plan` command (draft the rule
  with `tune-automode`). A scheduled prompt is not the owner's chat instruction, so a rule that
  needs one does not cover it.
- A refused `reap` is not worked around with another command. The run sends the refused command,
  one line, to `reap.inbox_to` and puts it first in its report. `inbox_to` is an Orca address; a
  run address (`run:<id>`) outlives a terminal restart, a terminal handle does not. Unset, only the
  report line and the precheck's stdout carry it.
- The precheck counts how often it planned the same set. After `reap.plan_repeat` (default 2)
  rounds with no `reap`, it stops waking the model for that set, retries it once a day, and sends
  one message to `reap.inbox_to` with the command to run by hand; a failed send is tried again on
  the next round. A `reap` or a different set starts the count over.

A precheck prints what it could not read (`gh`, `orca`) and the alerts to stderr.
`worker-list` without `--run` sees only the bound Run from a run-bound terminal,
so a scan there can see fewer workers than the automation does. What keeps a card from removal is
[references/cards.md](references/cards.md); `orca-worker` reports a reclaimable worker terminal. The kinds `card`
replaced, `orca-worktree` and `done-card` (a nudge asking an idle session to close itself), run only when named in
`--kinds`; their rule is [references/janitor-kinds.md](references/janitor-kinds.md). `card` and reap-hard
are sweeps for cards that neither closed themselves nor were closed by the session that started them
(`end-session` §8, "Who closes a finished card"). Which device fits which job:
[docs/automation.md](../../docs/automation.md).

## Hard pass: reap-hard

The janitor only sees what was registered and waits on a model and on the card's own session to close.
`bin/reap-hard` is the last sweep: fixed rules, no model, every Orca card under
`~/orca/workspaces` whatever made it, run by a LaunchAgent every 15 minutes.

```bash
bin/reap-hard run --dry-run      # decide and log, touch nothing
bin/reap-hard run                # what the LaunchAgent runs
bin/reap-hard install            # from the main checkout; `status`, `uninstall`, `last`
```

- It reads `~/.config/agent-skills/reap-protect.txt` first (`port`, `pid`, `path`, `project` lines) and does nothing
  without it. List the lead and orchestrator worktrees and the always-on ports there.
- Card: its own PR merged or closed and carrying the worktree's HEAD, clean, terminals silent and transcripts quiet
  for `idle_minutes` (30), no process a person started, no live lead or orchestrator session in it → terminals closed, ignored files to the trash,
  `orca worktree rm`. Stack: every compose working directory gone → `docker compose down` (never `-v`). An orphaned
  dev server whose card is gone → SIGTERM to that pid. A `tailscale serve` entry in the preview ranges with no
  listener two passes running → off.
- Each action is a line in `<state>/reap-hard.jsonl`; `orch brief` shows the last pass and flags one that stopped.
- The rules and settings: [docs/reap-hard.md](../../docs/reap-hard.md).

No cleanup path (orphan, pressure, gui-app, reap-hard) ever stops a process a person started: walking up from it,
a login shell (or a shell under tmux, screen, sshd) comes before any Claude session. An orphan whose tree holds such a
process is kept whole.

## Load

When the machine is slow, or its 5-minute load reaches the limit (`reap.load_limit`, default the
CPU count):

```bash
python3 "$R" load [--top N] [--json]    # read only
```

It groups processes into consumers (a Claude session with its children, a leftover tree, any
other process), lists the top ones by CPU with a total per class, and prints the action for each
class when the load is at or above the limit:

| Class | What | Action |
|---|---|---|
| ours-idle | a Claude session whose transcripts (its subagents' included) have been quiet for `reap.idle_minutes` (default 30) and whose children use under 25% CPU (a background benchmark keeps a session working) | ask it whether work is left and to close itself if none; never kill it |
| leftover | an `orphan` target of `scan` | `scan --plan`, then `reap --plan` |
| ours-working | anything else of this user's, from any path: a live session, a benchmark, a test run, a container VM | leave it; stop new dispatches and queue heavy runs behind `orch heavy` |
| system | another user's process | report it as not ours; never touch it |

A session with no transcript on record counts as working. A benchmark or performance number
taken while the load is at or above the limit is not evidence: measure again once it is under.

## Pressure: before the machine slows down

`scripts/pressure.py` watches the machine without a model and reaps what is safe before anyone notices.

```bash
P="${CLAUDE_SKILL_DIR}/scripts/pressure.py"
python3 "$P" check                     # measure only; exit 0 ok, 1 warn, 2 danger
python3 "$P" queue                     # heavy starts the hook held at danger: waiting, retrying, done, failed, gave_up
python3 "$P" round --dry-run --set load_per_cpu=0.5,1   # what a round would reap and send, with lowered thresholds
python3 "$P" round                     # one round, what the LaunchAgent runs
python3 "$P" install | uninstall | status   # macOS LaunchAgent, every `interval` seconds, from the main checkout
```

`uninstall` leaves `<state>/pressure-disabled` so `skills-sync setup apply` keeps the job off; `install` removes it,
and `status` shows it.

Each metric has a `[warn, danger]` pair under `reap.pressure`; a danger of `null` is warn only:
`load_per_cpu` (5-minute load / logical CPUs; danger only while CPU idle over the last round is at or below
`load_idle_pct`, or unknown), `mem_free_pct` (`kern.memorystatus_level`, at or below), `vm_pressure` (the kernel's
own level, 2 warn, 4 critical), `swapout_pps` (pages swapped out per second between rounds), `swap_gb` (in use, warn
only), `swap_growth_gb_h` (net growth over 30-60 minutes, warn only, with one leak note per `renotify_minutes`),
`disk_free_gb` (the volume holding `$HOME`, at or below), `docker_mem_pct` (all containers' memory / the Docker VM's),
`containers` (running, warn only; a Kubernetes pod counts once, its pause container included, and `kube-system` not
at all; the compose projects, container names or namespaces in `always_on` are left out and shown apart). A metric the
OS does not offer is left out. The level is the worst metric's.

One rule, one input: `round` samples the rates (swapout, CPU idle, Docker memory, swap growth) from counters it keeps
between rounds and saves them before it sweeps; `check`, the hook and `round` judge the metrics read now plus that
sample while it is younger than `sample_fresh_minutes` (7). An older sample is left out and shown as such.

- **warn**: `reap.py scan --kinds <safe_kinds>` then `reap --plan`, so only what reap itself lists goes. Default
  `safe_kinds`: `orphan`, `compose-stack`, `gui-app`, `orca-worker` (a kind this reap.py does not know is dropped,
  with a note). A target inside an Orca card with a live terminal is spared; when Orca or the process cwds cannot be
  read, the round reaps nothing.
  When something went, one `status` line to `notify_to` (else `reap.inbox_to`): what and why. A machine that stays at
  warn is swept again only after `rescan_minutes` (10).
- **danger**: the same sweep. With nothing reaped (none listed, or reap skipped or failed them all), the top consumers (reap's `load` classes, compose projects with
  their CPU and memory, each with the Orca card it lies in) go out as one `status` message (an `escalation` needs an
  active dispatch, which the LaunchAgent has none of), then the round stops. Nothing
  is stopped or killed, whoever owns it. Sent again only on entering danger or after `renotify_minutes` (30). A send
  that fails (the LaunchAgent has no Orca sender terminal) is not retried each round: the danger goes to the orchestrate
  dashboard's Needs-you list (`inbox-external.jsonl`, key `pressure:danger`) and is resolved there once the level
  leaves danger. A round run within half an `interval` of the last one keeps that round's sample.
- Never: `compose down -v`, `docker system prune`, volume or image removal, a kill by pattern, anything of a card or
  lead that is still working (reap's own rules keep those).

`hooks/pressure_gate.py` (PreToolUse, Bash) denies `orch heavy`, `docker compose … up` and `orca worktree create`
while the level is danger, and at warn until a round ran its scan in the last `gate_fresh_minutes` (15; a failed scan
counts, so warn never holds for good). At danger the deny puts the command in a queue
(`<state>/pressure-queue.json`, one entry per session and command, a repeat deny counting the same entry) with a
deadline `queue_minutes` (15) away, and on the dashboard's Needs-you list (key `pressure:queue:<id>`, its copy button
`pressure.py queue`, never the held command). The deny names the wait: `round` first unless one is fresh, then
`Monitor` on `queue wait <id>`, which exits 0 once the hook would let that command through, so the session runs the
same command again, and 3 when it must not run (deadline passed, recorded as given up; or already retried). The queue
never runs a command itself: the retry is the asking session's own tool call, through the same hooks and permission
checks, and another session's run of the same text is not that entry's retry. The same script as PostToolUse and
PostToolUseFailure records the retry's outcome: done takes the row off the dashboard, a failure (`Exit code N`)
stays on it, and the same session's later success closes a failed or given-up entry too. A retry is marked before
other hooks and the permission check decide, so one that gets no outcome fails after one more window. A command run
with `run_in_background` gets its PostToolUse when it starts, so its entry reads done before the command ends. A round
ends overdue entries. `python3 "$P" queue` lists entries for a week. A queue that cannot be written (lock held past
3 seconds, unwritable state) still denies, with the old wait on `check`. Danger from disk alone asks for space to be
freed instead of a wait, and queues nothing. Each simple command of the line is read on its own (quotes, `sudo`, `env`, `bash -c` included).
`AGENT_SKILLS_PRESSURE_GATE=0` (environment, or before the heavy command when the user said to go ahead) turns it
off. The gate assumes the LaunchAgent is installed: without it, a machine that sits at warn costs every heavy start
a deny and a manual `round`.

Defaults and the measurements behind them: [docs/pressure.md](../../docs/pressure.md).

## What goes

| Kind | Target when | Kept when | How |
|---|---|---|---|
| orphan | ppid 1, ≥ N hours, and either the executable or the script it runs (`argv[1]`) lies under a path in `reap.orphan_paths`, or its cwd was removed (gone, or another directory at that path: a finished card's dev server or headless Chrome, a deleted review worktree's broker) | anything else, a cwd lsof cannot list, a tree holding a person's shell or its child (judged again before the kill) | `kill -TERM` the tree once pid and start time match, `SIGKILL` what is left after 5 s |
| docker | a dangling volume whose compose project has no container in any state, ≥ N hours; an untagged image no container uses, ≥ N hours | a project with any container, a volume whose name holds such a project's name, a volume with no compose label (reported only) | `docker volume rm <name>`, `docker image rm <id>` |
| branch | every PR from that branch is merged or closed, no worktree has it checked out, and its tip is what a PR carried | an open PR, commits after the PR, the default branch, no PR; the whole repo when `origin/HEAD` is unset | `git branch -D`; the output has the restore command |
| worktree | a worktree whose directory in a temp dir is gone and that Orca does not manage (a truncated Orca list keeps them all); a detached-HEAD worktree under a Claude scratchpad whose session transcript is quiet for N hours, with no process in it, clean, not locked, its HEAD on a remote | any of those fails, no transcript to prove the session quiet, a branch checked out (it may back an open PR); Orca's worktrees and any other worktree | `git worktree remove <path>`, which for a vanished directory drops only that entry (never `--force`, never a repo-wide `prune`) |

Finished compose stacks (every container stopped) that nobody registered are reported with their `docker compose -p
<project> down` command, not removed: whether their data is still wanted is the owner's call. A registered stack is the
janitor's `compose-stack` kind.

Never: `pkill`/`killall` by pattern, `docker system prune`, `docker volume prune`, `git worktree prune`, `--force`,
or anything not in the plan. A kill the auto-mode classifier refuses is not worked around: hand
the exact `reap --plan` command to the user (a scheduled run: to `reap.inbox_to`, see Scheduled run).

## Config

`~/.claude/agent-skills.json`, all optional, changed through `skills-config` (the agent emits, the user applies).
Paths of a particular project's test cache stay here, not in this repo:

```json
{"reap": {"hours": 6, "orphan_paths": ["/private/tmp/<project>-uv-cache"], "evidence_roots": ["/tmp"], "user_folder_days": 7, "user_folder_screenshot_days": 1,
          "program_days": 7, "state_days": 7, "log_max_kb": 512,
          "inbox_to": "run:<id>", "plan_repeat": 2, "done_card_hours": 2, "idle_minutes": 30, "load_cooldown_minutes": 60,
          "user_folders": [], "regenerated_untracked": true, "preview_ports": [4500, 4599], "alert": {"orphan_procs": 5,
                    "dangling_volumes": 50, "stale_branches": 30, "stale_worktrees": 10},
          "pressure": {"interval": 180, "load_per_cpu": [1.3, 2.0], "load_idle_pct": 10, "mem_free_pct": [20, 10],
                       "vm_pressure": [2, 4], "swapout_pps": [300, 1500], "swap_gb": [16, null],
                       "swap_growth_gb_h": [2, null], "disk_free_gb": [50, 20], "docker_mem_pct": [70, 90],
                       "containers": [40, null], "sample_fresh_minutes": 7, "always_on": [],
                       "notify_to": "run:<id>", "rescan_minutes": 10, "renotify_minutes": 30, "gate_fresh_minutes": 15,
                       "queue_minutes": 15}}}
```

Alerts count everything the scan saw, kept items included: orphan processes, dangling volumes, and branch and worktree
targets. A total at or above its limit prints under `## 경보` and in `alerts`.
