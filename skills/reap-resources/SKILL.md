---
name: reap-resources
description: "Use when the machine is slow or cluttered with what finished sessions and agents left behind — \"방치 리소스 정리해줘\", \"죽은 프로세스 정리\", \"메모리·CPU 누가 먹고 있어\", \"끝낸 orca 세션·worktree 정리해줘\", \"janitor\" — and for the resource steward's and janitor's rounds. Inventories first, reaps only listed targets, files go to the trash."
---

# Reap resources

Sessions end and leave things running or lying around: test
servers reparented to pid 1, the volumes and images of finished compose stacks, local branches
of merged PRs, git worktrees in the scratchpad of a finished session.

This skill needs no program. Its janitor and reap-hard remove finished Orca cards with `orca worktree rm`, after moving
the worktree's ignored files to the trash; sessions leave that to them (`end-session`, "Who cleans up"). A cleanup
command you run by hand runs alone (`end-session` §4).

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
only as the unregistered-media lines of `evidence`, never as a target. Three kinds go by what the user listed instead of
the ledger, and each is off until listed. `stack-volume`: only stacks whose names start with a prefix in
`reap.stack_volumes.prefixes` (each ends in `-`, at least 4 characters; `label` is the extra owner label, never a
`com.docker.*` key; `protect` lists owners it never touches). `stopped-container`: containers named under
`reap.stopped_containers.prefixes` (the same shape; no wildcard) that compose did not make. `labeled-volume`: volumes
whose provenance label (below) is in `reap.labeled_volumes.origins`. `reap --plan` re-checks each
target and runs its action. A file or directory goes to the trash (`/usr/bin/trash`, macOS 15+, so Finder's
Put Back works; never another `trash` on PATH, which may read `-s` as "empty the trash"; else `~/.Trash`;
`$AGENT_SKILLS_TRASH` replaces it with a plain folder). Apart from the `docker volume rm` of `stack-volume` and
`labeled-volume` and the `docker rm` of `stopped-container`, the tool never
deletes for good, and it never empties the trash; the OS setting may (Finder's "Remove items from the Trash after 30 days"), and
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
| stack-volume | a volume whose compose project or `reap.stack_volumes.label` value starts with a `reap.stack_volumes.prefixes` entry and appears in the volume's name, every owner label on it is under a prefix, no container of any state carries an owner, ≥ N hours old (stacks those prefixes declare throwaway, such as test runs; `compose-stack` brings one down, a later round takes its volumes), the owner's card closed (below) | a container of that owner, running or stopped; an owner in `protect`, the protect file or `always_on`; a card not proved closed; `reap.stack_volumes` unset; any other prefix is not listed | read again first (same creation time and owners, still no container of an owner), then `docker volume rm <name>`, one exact name each; never `prune`, `down -v` or a pattern |
| stopped-container | a container named under a `reap.stopped_containers.prefixes` entry, not made by compose (no `com.docker.compose.oneoff` label), `exited` or `created`, stopped for ≥ N hours (`created`: made that long ago) | any other state; its name or compose project in `protect`, the protect file or `reap.pressure.always_on`; a protected host port; another container of its project not stopped; a stop time it cannot read; `reap.stopped_containers` unset | every container read again and the rule run again without the age; then `docker rm <full id>`, never `-f` or `-v`, so its volumes stay |
| labeled-volume | a volume whose `<namespace>.origin` value is in `reap.labeled_volumes.origins`, referenced by no container in any state, ≥ N hours old, the owner's card closed (below) | an origin not listed (reported only); a reference; its name or compose project in `protect`, the protect file or `always_on`; any container left of its compose project; a card not proved closed; `reap.labeled_volumes` unset. A volume without the label is never listed | the volume and every container read again and the rule run again without the age, same creation time and origin; then `docker volume rm <name>`, one exact name each, never `-f` |
| remote-branch | interface only, never a target | always | - |

`stopped-container` and `labeled-volume` act one target at a time, under a `guard.py` lock, and leave a line in `<state>/reap-actions.jsonl` (reap-hard:
`<state>/reap-hard.jsonl`). Never `prune`, `down -v`, a filter, a label or a pattern as the target of a delete. A
container that stopped this round still references its volumes, so those become targets on a later round.

**The owner of a volume.** `stack-volume`, `labeled-volume` and the manual `docker` volumes all go through
`reap.owner_hold`, at the scan and again inside the target's lock (`guard.item`), for reap and reap-hard alike. A stack's
containers name its card only while they exist, so the record is the ledger: the `compose-stack` lines `orch heavy`
writes and the `stack-seen` lines reap-hard adds for each working directory it sees on a stack's containers. A volume goes
only when each owner stack has at least one such line, every worktree any line names is gone from disk and from
`orca worktree list` with no process left inside it, and every linked PR is merged or closed. A worktree still there
holds its stacks' volumes whatever its PR says, because closing a card is what weighs its PR, sessions and terminals;
the volumes go on the first round after the card closes. Everything else stays, reported with its reason: no owner
label, no ledger line (a stack started outside `orch heavy` that reap-hard never saw up), no worktree link, a damaged
ledger line anywhere, no `orca` on PATH, a card list, process list or PR that cannot be read. Such a volume waits for a person.
One gap stays: a card that reuses a stack name another, closed card registered, and brings it up and down between two
reap-hard passes, leaves no line, so the closed card's record lets the volumes go. Stamped stack names keep this rare. This guard starts as a block,
not a warning: a kept volume can still go, a deleted one cannot come back.

**The provenance label.** `<namespace>.origin=<verify skill>`, the namespace from `reap.labeled_volumes.namespace`;
`python3 "$R" origin-label verify-<app>` prints it and exits 1 when the namespace is unset. The label is both a
statement of origin and permission to delete: this volume was made new for a verification run, and may go once nothing
references it. Leave it off a shared or `external` volume, data reused across runs, and a volume holding evidence.
Age counts from creation, not last use, so a labeled volume can go soon after its stack comes down. Nothing else proves
where a volume came from: that no container uses it, or that a verification container once mounted it, does not.

```bash
python3 "$R" inventory --unlabeled-volumes [--json]   # read only: anonymous volumes with no label; the owner decides
```

Those volumes are never a target, of any kind, in any cleaner. The inventory only lists them: the count, then the ones
no container references, for the owner to check by hand.

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
python3 "$R" precheck --act    # the scheduled round: plan, reap that plan in this process, always exit 1 (no agent)
python3 "$R" precheck --load --act    # the same, but exit 1 at once unless the 5-minute load is at the limit
python3 "$R" precheck    # plan only: writes <state>/janitor-plan.json, exit 0 when there are targets (see below)
python3 "$R" schedule [--load]    # prints the `orca automations create` command; nothing is created without --write
```

The round runs no model. Orca runs a job's precheck itself, outside any Claude session, so the reap there meets no
permission prompt or auto-mode classifier, and an exit other than 0 starts no agent (the run shows as
`skipped_precheck`). `precheck --act` uses that: under `<state>/janitor.lock` (a second round meanwhile exits at once)
it scans the janitor kinds (user-folder, program and state-file at most once a day), writes the plan, and runs `reap`'s
own path on it in the same process: the scheduled kinds only, each target re-scanned and acted on only with the same
identity, inside its lock, after the guard passes again. A target whose act failed rests a day, on its own
(`<state>/janitor-fails.json`); a chrome-window is never acted on, since closing it needs a URL check in a browser.
What a person has to look at (cards judged 사람 결정, chrome windows, resting targets) goes to `reap.inbox_to` as one
message, sent again only when a key not in the last sent set appears. Each round leaves one row in
`<state>/janitor-runs.jsonl` (targets, acted, skipped, failed, left past the 540-second budget, error), the only record
of a cleanup: Orca's run history shows a skip either way. `inbox_to` and `reap.pressure.notify_to` are Orca addresses.
Use `run:<id>`: a `term_` handle changes when its terminal restarts, and `orch patrol` raises one Orca no longer lists
(T).

`schedule` builds an existing-workspace automation on `7,37 * * * *` (every 30 minutes) whose precheck is
`python3 <absolute reap.py> precheck --act` (its cwd is the repo's main checkout, so paths are absolute)
with `--precheck-timeout 600`. Its prompt is only for Orca's Run now, which skips the precheck: the agent runs the
same `precheck --act` line once and reports.
`schedule --load` builds the second job, `agent-skills janitor load`, on `27,57 * * * *` with `precheck --load --act`:
a loaded machine is when finished cards cost the most. Its precheck reads only the load average and goes on
to the full round when the 5-minute load is at `reap.load_limit` (default the CPU count), the periodic job is
registered and on (removed, or an unreadable automation list, counts as off), and no precheck of either job ran within
`reap.load_cooldown_minutes` (default 60). The cooldown holds back only the load job; the periodic one runs on its
schedule.
`skills-sync setup` lists the two as `janitor` and `janitor-load`, `enable` brings a job whose trigger, precheck,
timeout or prompt differs back to these values, and `disable janitor` turns both off (`enable janitor` on a janitor
that was off turns a registered load job back on too).
Every command it builds starts with `python3`, with `--state`/`--ledger` after the subcommand when
those are off the default. To change the prompt of an automation that exists,
`orca automations edit <id> --prompt "<text>"`.
Run `schedule` from the checkout that loads the skills, never a card's worktree: the command keeps
that path. The janitor never lists the worktree an Orca automation runs in (`orca automations list`); when that list
is unreadable, scan says so in a note and keeps every Orca worktree that round.
Test a round by running `precheck --act --kinds <kind>` directly with `--state` and `--ledger` pointing at a scratch
folder: `--state` alone still scans every real card.

`precheck` without `--act` is the round of a job still on the agent: it exits 0 when there are targets, except the set
the last reap failed on, for a day, and a set already planned `reap.plan_repeat` (default 2) times with no `reap`
since, retried once a day with one message to `reap.inbox_to` carrying the command to run by hand.

A precheck prints what it could not read (`gh`, `orca`) and the alerts to stderr.
`worker-list` without `--run` sees only the bound Run from a run-bound terminal,
so a scan there can see fewer workers than the automation does. What keeps a card from removal is
[references/cards.md](references/cards.md); `orca-worker` reports a reclaimable worker terminal. The kinds `card`
replaced, `orca-worktree` and `done-card`, are gone: `--kinds` or a saved plan naming them is refused. `card` and reap-hard
are who removes a finished card (`end-session`, "Who cleans up"). Which device fits which job:
[docs/automation.md](../../docs/automation.md).

## Hard pass: reap-hard

The janitor only sees what was registered and waits on its half-hourly round and on the card's own session to close.
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
  listener two passes running → off. The `stopped-container` and `labeled-volume` rules above, read from the same
  `reap.*` settings → `docker rm <id>`, `docker volume rm <name>`.
- Each action is a line in `<state>/reap-hard.jsonl`; `orch brief` shows the last pass and flags one that stopped.
- Every cleaner (reap-hard, `reap.py reap --plan`, `cards.py apply`, preview-watch's serve off) acts on a target
  only through `scripts/guard.py`: a non-blocking lock per target under `<state>/locks/`, taken in one fixed order;
  the ownership file `<state>/sweep-owner.json` read again (a rule the sweep owns is left, unless its last good round
  is three intervals old; an unreadable file stops the round); and guards that only keep (the protect file, the same
  process). A card's lock is `cardguard.card_lock`'s, and its guards are `cardguard.guard`'s, which also keep a card
  patrol's `activity` marks parked or unknown.
- `scripts/sweep.py` is the v2 cleaner, in its shadow stage: after each reap-hard pass it reads the newest complete
  patrol round's `candidates` and records in `<state>/sweep-shadow.jsonl` what it would do after the same checks
  (sources, guards, `sweep.max_per_round`; it takes no target lock). It closes and deletes nothing; `run` without
  `--shadow` refuses.
  `python3 scripts/sweep.py diff --hours 48` compares that with what reap-hard (`reap-hard.jsonl`) and
  `reap --plan` / `cards.py apply` (`reap-actions.jsonl`) really did: every round's plan should be met by an old
  cleaner's act of the same effect on the same target within its rule's lag, and `넘침` (excess) should be 0.
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
A round whose level turns warn or danger also starts `orch patrol --if-due` detached, which ties the top consumers
to the state of the card behind each (its `L` rule); reap-hard starts it every other pass. Patrol verdicts only keep.

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
`load_per_cpu` (5-minute load / logical CPUs; danger only while CPU idle is at or below `load_idle_pct`, or
unknown), `mem_free_pct` (`kern.memorystatus_level`, at or below: what the kernel could reclaim, file cache included, so far above memory in use; `mem_active_wired_gb` and `mem_anon_wired_gb` are recorded, never judged; the dashboard shows the first), `vm_pressure` (the kernel's
own level, 2 warn, 4 critical), `swapout_pps` (pages swapped out per second between rounds), `swap_gb` (in use, warn
only), `swap_growth_gb_h` (net growth over 30-60 minutes, warn only, with one leak note per `renotify_minutes`),
`disk_free_gb` (the volume holding `$HOME`, at or below), `docker_mem_pct` (all containers' memory / the Docker VM's),
`docker_cpu_pct` (all containers' CPU / the Docker VM's CPUs, warn only: host CPU idle already holds the VM's vCPUs).
`containers` (running; a Kubernetes pod counts once, its pause container included, and `kube-system` not at all; the
compose projects, container names or namespaces in `always_on` are left out and shown apart) is shown, never judged:
a count of mostly idle containers is no load, and its key still loads from an older config. A metric the
OS does not offer is left out. The level is the worst metric's.

One rule, one input: `round` samples the rates (swapout, CPU idle, Docker memory and CPU, swap growth) from counters
it keeps between rounds and saves them before it sweeps; `check`, the hook and `round` judge the metrics read now plus
that sample while it is younger than `sample_fresh_minutes` (7). An older sample is left out and shown as such. CPU
idle is read again where `check`, the hook and `queue wait` judge, over `idle_window_seconds` (2), and replaces the
round's: under the load it measures, the round runs late and its sample expires. `round` reads it on the spot only
when it has no rate (first round, or one run late).

- **warn**: `reap.py scan --kinds <safe_kinds>` then `reap --plan`, so only what reap itself lists goes. Default
  `safe_kinds`: `orphan`, `compose-stack`, `gui-app`, `orca-worker` (a kind this reap.py does not know is dropped,
  with a note). A target inside an Orca card with a live terminal, or one `orch patrol` judged working, waiting or a
  Run worker within the hour, is spared; when Orca or the process cwds cannot be read, the round reaps nothing.
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

Every round that saves also appends one point (level, the charted metrics, the held-queue count) to
`<state>/pressure-history.jsonl`, which keeps the last 24 hours. `pressure.py history [--hours H]` prints those points
with the last round, the thresholds and the queue as JSON, reading files only; the orchestrate dashboard's Overview
draws its PC load panel from it.

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
The same hook holds two more things at any level. A line that runs a whole test suite (`tests.sh`, a `for` loop over
five or more test files, `npm test`, a bare `bun test`, `pytest`, `jest` or `vitest`, `go test ./...`), an E2E (`playwright test`,
`cypress run`, an `e2e` script) or a headless Chrome outside `orch heavy` is denied with the same line wrapped in
`orch heavy - -- bash -c '…'`, so the two machine-wide slots cap what already-running cards run, not only what
starts. A new card (`orca worktree create`, a worker into a new worktree) is denied while Orca
shows `active_turns_per_cpu` (0.5, 9 on 18 CPUs; null is off) agent turns per CPU in progress (a main turn
running: an agent whose turn ended and that only waits on a background shell, `orch wait`, an orchestration `ask` or
a subagent does not count; an Orca that gives no `workingMode` or `mainAgent` is read by `state` alone); the deny names
`Monitor` on `python3 "$P" turns --wait 15`, which exits 0 once under the cap and 3 after the wait (2 when `--wait` has no minute count: it never skips the wait). A count Orca cannot
give (no answer, a cut-short list) holds like a full one, in the hook and in `turns`/`turns --wait` alike (`turn_hold`):
the wait polls until the count is readable and exits 3 with "cannot count" if it never is; only `null` turns the cap off.
The caller's own turn is left out of the count (its pane key comes from `$ORCA_TERMINAL_HANDLE`), so the wait, which runs
while the caller's turn is over, and the hook, which runs inside it, give one verdict; a caller that cannot be identified
is counted like any other turn and the output carries a warning line.
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
the exact `reap --plan` command to the user (the scheduled round meets no classifier: it runs in Orca's precheck).

## Config

`~/.claude/agent-skills.json`, all optional, changed through `skills-config` (the agent emits, the user applies).
`reap.model` names the janitor rounds' Claude model: `schedule` passes it with `--extra-agent-args`.
Paths of a particular project's test cache stay here, not in this repo:

```json
{"reap": {"hours": 6, "orphan_paths": ["/private/tmp/<project>-uv-cache"], "evidence_roots": ["/tmp"], "user_folder_days": 7, "user_folder_screenshot_days": 1,
          "program_days": 7, "state_days": 7, "log_max_kb": 512,
          "inbox_to": "run:<id>", "plan_repeat": 2, "idle_minutes": 30, "load_cooldown_minutes": 60,
          "user_folders": [], "preview_ports": [4500, 4599],
          "stack_volumes": {"prefixes": ["<throwaway project prefix>-"], "label": "<owner label key>", "protect": ["<project>"]},
          "stopped_containers": {"prefixes": ["<throwaway container prefix>-"], "protect": ["<project or container>"]},
          "labeled_volumes": {"namespace": "<label namespace>", "origins": ["verify-<app>"], "protect": ["<project>"]}, "alert": {"orphan_procs": 5,
                    "dangling_volumes": 50, "stale_branches": 30, "stale_worktrees": 10},
          "pressure": {"interval": 180, "load_per_cpu": [1.3, 2.0], "load_idle_pct": 10, "mem_free_pct": [20, 10],
                       "vm_pressure": [2, 4], "swapout_pps": [300, 1500], "swap_gb": [16, null],
                       "swap_growth_gb_h": [2, null], "disk_free_gb": [50, 20], "docker_mem_pct": [70, 90],
                       "docker_cpu_pct": [50, null], "idle_window_seconds": 2, "sample_fresh_minutes": 7, "always_on": [],
                       "notify_to": "run:<id>", "rescan_minutes": 10, "renotify_minutes": 30, "gate_fresh_minutes": 15,
                       "queue_minutes": 15}}}
```

Alerts count everything the scan saw, kept items included: orphan processes, dangling volumes, and branch and worktree
targets. A total at or above its limit prints under `## 경보` and in `alerts`.
