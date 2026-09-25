---
name: reap-resources
description: Use when dead sessions may have left resources piling up on this machine — "방치 리소스 정리해줘", "죽은 프로세스 정리", "codex 프로세스 너무 많아", "메모리·CPU 누가 먹고 있어", leftover Codex plugin broker trees, orphaned test processes, dangling Docker volumes and untagged images, merged local branches, stale scratchpad worktrees — and for the resource steward's periodic round. Inventories first (count, RSS, CPU, age per kind), then reaps only the listed targets.
---

# Reap resources

Sessions end and leave things running or lying around: a Codex plugin broker tree per worktree
(broker → `codex app-server` → computer-use, xcodebuildmcp, cua-repl, code-mode helpers), test
servers reparented to pid 1, the volumes and images of finished compose stacks, local branches
of merged PRs, git worktrees in the scratchpad of a finished session. One machine once held 884
such helpers (7.6 GB, 84% CPU), 21 orphaned test servers and 108 dangling volumes.

This skill needs no program. Orca worktrees of settled workers are not its job: the resource
steward removes those with `orca worktree rm` (`orchestrate` `references/roles.md`).

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

## What goes

| Kind | Target when | Kept when | How |
|---|---|---|---|
| codex | a broker (`node …/app-server-broker.mjs serve --cwd D`) whose `D` does not exist, or no `claude` process has its cwd in `D` and the broker is ≥ N hours old | a live `claude` in `D` (even when `D` is gone), younger than N, or this user's process cwds could not all be read | SIGTERM the whole tree by pid, SIGKILL what is left after 5 s |
| orphan | ppid 1, and the executable or the script it runs lies under a path in `reap.orphan_paths`, ≥ N hours | anything else | same |
| docker | a dangling volume whose compose project has no container in any state, ≥ N hours; an untagged image no container uses, ≥ N hours | a project with any container, a volume whose name holds such a project's name, a volume with no compose label (reported only) | `docker volume rm <name>`, `docker image rm <id>` |
| branch | every PR from that branch is merged or closed, no worktree has it checked out, and its tip is what a PR carried | an open PR, commits after the PR, the default branch, no PR | `git branch -D`; the output has the restore command |
| worktree | a worktree whose directory is gone; a worktree under a Claude scratchpad whose session transcript is quiet for N hours, with no process in it, clean, not locked, its HEAD on a remote | any of those fails, no transcript to prove the session quiet; any other worktree | `git worktree remove <path>`, which for a vanished directory drops only that entry (never `--force`, never a repo-wide `prune`) |

Finished compose stacks (every container stopped) are reported with their `docker compose -p
<project> down` command, not removed: whether their data is still wanted is the owner's call.

Never: `pkill`/`killall` by pattern, `docker system prune`, `docker volume prune`, `git worktree prune`, `--force`,
or anything not in the plan. A kill the auto-mode classifier refuses is not worked around: hand
the exact `reap --plan` command to the user.

## Config

`~/.claude/agent-skills.json`, all optional. Paths of a particular project's test cache stay here,
not in this repo:

```json
{"reap": {"hours": 6, "orphan_paths": ["/private/tmp/<project>-uv-cache"],
          "alert": {"codex_procs": 300, "codex_rss_mb": 4096, "orphan_procs": 5,
                    "dangling_volumes": 50, "stale_branches": 30, "stale_worktrees": 10}}}
```

Alerts count everything the scan saw, kept items included (a live broker that leaked 50 helpers
counts): Codex processes and RSS, orphan processes, dangling volumes, and branch and worktree
targets. A total at or above its limit prints under `## 경보` and in `alerts`.
