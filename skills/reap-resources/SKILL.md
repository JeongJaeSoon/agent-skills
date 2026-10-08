---
name: reap-resources
description: Use when dead sessions may have left resources piling up on this machine — "방치 리소스 정리해줘", "죽은 프로세스 정리", "codex 프로세스 너무 많아", "메모리·CPU 누가 먹고 있어", "머신 부하가 높아", leftover Codex plugin broker trees, orphaned test processes, dangling Docker volumes and untagged images, merged local branches, stale scratchpad worktrees — and for the resource steward's periodic round. Also the janitor ("janitor", "자동 정리", "정기 정리", "에이전트가 만든 리소스 정리"): a ledger of what agents created (temp dirs, GUI apps, browser windows) plus settled Orca worker worktrees and terminals, cleaned up on a 3-hour Orca automation with a model-free precheck. Also files in ~/Downloads and ~/Desktop untouched for 7 days ("다운로드 폴더 정리", "바탕화면 정리"). Inventories first (count, RSS, CPU, age per kind), then reaps only the listed targets; files go to the trash, never deleted for good.
---

# Reap resources

Sessions end and leave things running or lying around: a Codex plugin broker tree per worktree
(broker → `codex app-server` → computer-use, xcodebuildmcp, cua-repl, code-mode helpers), test
servers reparented to pid 1, the volumes and images of finished compose stacks, local branches
of merged PRs, git worktrees in the scratchpad of a finished session. One machine once held 884
such helpers (7.6 GB, 84% CPU), 21 orphaned test servers and 108 dangling volumes.

This skill needs no program. The janitor kinds below remove Orca worktrees of settled workers with
`orca worktree rm`, the same command the resource steward uses (`orchestrate` `references/roles.md`).

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
- `--kinds codex,orphan,docker,branch,worktree` limits the scan, `--hours N` sets the age
  threshold (default 6), `--repo PATH` (repeatable) picks the repos for `branch` and
  `worktree` (default: every repo in `orca repo list`).
- Show the user the scan before the first `reap` in a session unless they asked to reap.

## Janitor: what agents created

These kinds look only at what an agent registered in the ledger or an Orca orchestration worker
made. A user's own worktree and the main checkout never show up; anything not registered shows up
only as the unregistered-media lines of `evidence`, never as a target. `reap --plan` re-checks each
target and runs its action. A file or directory goes to the trash (`trash`, macOS 15+, so Finder's
Put Back works; else `~/.Trash`; `$AGENT_SKILLS_TRASH` replaces it with a plain folder). Nothing is
deleted for good and the trash is never emptied: emptying it is the user's call.

```bash
python3 "$R" ledger add --kind tmpdir --path /tmp/build-x --pr acme/app#12     # right after creating it
python3 "$R" ledger add --kind gui-app --pid 4242 --worktree <worktree path>   # pid + start time are recorded
python3 "$R" ledger add --kind chrome-window --id <window or tab id> --pr acme/app#12
python3 "$R" ledger add --kind evidence --path /tmp/task/shots --pr acme/app#12   # a screenshot or recording, once it is on the PR
python3 "$R" ledger list
python3 "$R" scan --kinds janitor          # a plain scan leaves the janitor kinds out
```

The ledger is append-only JSON lines `{ts, run, kind, id, links: {pr, worktree}, by}` at
`~/.local/state/agent-skills/ledger.jsonl` (`$AGENT_SKILLS_STATE` moves the folder,
`$AGENT_SKILLS_LEDGER` or `--ledger` the file). The last line per id wins. A link is "done" when its
PR (`owner/repo#N`, read with `gh`) is merged or closed, or its worktree is gone.

| Kind | Target when | Kept when | Action |
|---|---|---|---|
| orca-worktree | in the ledger or made by an orchestration worker; its PR (ledger link, else the branch's own PR, never a fork's) merged or closed; no changes, untracked files included; nothing unpushed unless the PR carried HEAD; no live worker turn and no process with its cwd in it | an open PR, no PR, changes, unpushed commits, a live turn or a process in it; skipped: the main checkout, worktrees outside `--repo`, and any worktree with a `retained` worker row (a context-only dispatch or a card the user took over) | `orca worktree rm --worktree path:<p> --run-hooks` (end-session §4) |
| orca-worker | a row of `orca orchestration worker-list --terminal-state reclaimable` | - | `orca orchestration worker-release --dispatch <id>` |
| tmpdir | under a temp dir, its link is done, and no process has its cwd or executable under it | outside a temp dir, an open PR, no link, a process in it; a path already gone is not listed | `lsregister -u` each `.app` under it, then the trash |
| gui-app | the registered pid with the same start time still runs and its link is done | an open PR, no link | `kill -TERM <pid>` |
| chrome-window | its link is done | an open PR, no link | `reap` prints `에이전트가 처리`; the agent closes it with its browser tools, or leaves it when it has none |
| evidence | a file or directory of screenshots and recordings whose PR has been merged or closed for N hours (`--hours`) | an open PR, no PR link (a worktree link does not prove it was uploaded), a PR state or close time that could not be read, a directory holding anything but images and videos | the trash |
| remote-branch | interface only, never a target | always | - |

`evidence` also reports images and videos (png, jpg, jpeg, gif, webp, mp4, mov, webm) older than N
hours that nobody registered, one line per directory. It looks only directly in `/tmp` and
`$TMPDIR` and one directory below them (`reap.evidence_roots` replaces that list), never deeper and
never through a symlink. These lines are never targets, so they do not wake the precheck.

## User folders: ~/Downloads and ~/Desktop

The `user-folder` kind looks at the top-level entries of `~/Downloads` and `~/Desktop` (screenshots
included), and nowhere else. An entry is a target when nothing in it has been modified or changed
(mtime or ctime, so a freshly unpacked archive with old dates stays) for `reap.user_folder_days`
(default 7). Kept: a download in progress (`.download`, `.crdownload`, `.part`), an entry any of this
user's processes holds open or uses as cwd (`lsof`; an unreadable list keeps everything), and hidden
entries (`.DS_Store`). Targets go to the trash. The precheck sweeps them at most once a day.

```bash
python3 "$R" scan --kinds user-folder
```

A folder the process may not read (macOS privacy) is a note, not an error: granting access is the
user's call.

## Scheduled run

```bash
python3 "$R" precheck    # janitor kinds + user-folder; writes <state>/janitor-plan.json; exit 0 only when the target set is non-empty and changed since the last report
python3 "$R" schedule    # prints the `orca automations create` command; nothing is created without --write
```

`schedule` builds an existing-workspace automation on `17 */3 * * *` whose precheck is
`python3 <absolute reap.py> precheck` (its cwd is the repo's main checkout, so paths are absolute).
Its prompt has the agent run `reap --plan <state>/janitor-plan.json` once, close the chrome-window
targets it can, and report what went, what was skipped and what stayed. To change the prompt of an
automation that exists, `orca automations edit <id> --prompt "<text>"`.
Run `schedule` from the checkout that loads the skills, never a card's worktree: the command keeps
that path. `orca automations run` skips the precheck, so test it by running it directly, with
`AGENT_SKILLS_STATE` pointing at a scratch folder: a run records the target set, and the next
scheduled precheck would skip an unchanged set. A precheck prints what it could not read (`gh`,
`orca`) to stderr. `worker-list` without `--run` sees only the bound Run from a run-bound terminal,
so a scan there can see fewer workers than the automation does. Any process in a worktree keeps
it, Orca's idle shell in an open card included: an orca-worktree becomes a target once its card's
terminals are closed, and until then `orca-worker` reports the reclaimable terminal. Which device fits which job:
[docs/automation.md](../../docs/automation.md).

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
| ours-idle | a Claude session whose transcripts (its subagents' included) have been quiet for `reap.idle_minutes` (default 30) and whose children use under 25% CPU (a background benchmark keeps a session working) | ask it whether work is left and to close itself if none; never kill it. Closing 8 idle sessions once took a load of 19.5 down to 11 |
| leftover | a `codex` or `orphan` target of `scan` | `scan --plan`, then `reap --plan` |
| ours-working | anything else of this user's, from any path: a live session, a benchmark, a test run, a container VM | leave it; stop new dispatches and queue heavy runs behind `orch heavy` |
| system | another user's process | report it as not ours; never touch it |

A session with no transcript on record counts as working. A benchmark or performance number
taken while the load is at or above the limit is not evidence: measure again once it is under.

## What goes

| Kind | Target when | Kept when | How |
|---|---|---|---|
| codex | a broker (`node …/app-server-broker.mjs serve --cwd D`) whose `D` does not exist, or no `claude` process has its cwd in `D` and the broker is ≥ N hours old | a live `claude` in `D` (even when `D` is gone), younger than N, or the cwd of any of this user's `claude` processes could not be read | SIGTERM the whole tree by pid, SIGKILL what is left after 5 s |
| orphan | ppid 1, and the executable or the script it runs (`argv[1]`) lies under a path in `reap.orphan_paths`, ≥ N hours | anything else | same |
| docker | a dangling volume whose compose project has no container in any state, ≥ N hours; an untagged image no container uses, ≥ N hours | a project with any container, a volume whose name holds such a project's name, a volume with no compose label (reported only) | `docker volume rm <name>`, `docker image rm <id>` |
| branch | every PR from that branch is merged or closed, no worktree has it checked out, and its tip is what a PR carried | an open PR, commits after the PR, the default branch, no PR; the whole repo when `origin/HEAD` is unset | `git branch -D`; the output has the restore command |
| worktree | a worktree whose directory in a temp dir is gone and that Orca does not manage (a truncated Orca list keeps them all); a detached-HEAD worktree under a Claude scratchpad whose session transcript is quiet for N hours, with no process in it, clean, not locked, its HEAD on a remote | any of those fails, no transcript to prove the session quiet, a branch checked out (it may back an open PR); Orca's worktrees and any other worktree | `git worktree remove <path>`, which for a vanished directory drops only that entry (never `--force`, never a repo-wide `prune`) |

Finished compose stacks (every container stopped) are reported with their `docker compose -p
<project> down` command, not removed: whether their data is still wanted is the owner's call.

Never: `pkill`/`killall` by pattern, `docker system prune`, `docker volume prune`, `git worktree prune`, `--force`,
or anything not in the plan. A kill the auto-mode classifier refuses is not worked around: hand
the exact `reap --plan` command to the user.

## Config

`~/.claude/agent-skills.json`, all optional. Paths of a particular project's test cache stay here,
not in this repo:

```json
{"reap": {"hours": 6, "orphan_paths": ["/private/tmp/<project>-uv-cache"], "evidence_roots": ["/tmp"], "user_folder_days": 7,
          "alert": {"codex_procs": 300, "codex_rss_mb": 4096, "orphan_procs": 5,
                    "dangling_volumes": 50, "stale_branches": 30, "stale_worktrees": 10}}}
```

Alerts count everything the scan saw, kept items included (a live broker that leaked 50 helpers
counts): Codex processes and RSS, orphan processes, dangling volumes, and branch and worktree
targets. A total at or above its limit prints under `## 경보` and in `alerts`.
