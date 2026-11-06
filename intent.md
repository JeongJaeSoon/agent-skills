# Intent: what to reach for first

This file says which mechanism a new feature in this repository uses. Read it before adding a scheduled job, a daemon, a hook or a helper script. `AGENTS.md` and the `write-skill` skill point here.

## Order

1. **Orca first.** Automations (periodic jobs), worktrees and terminals, orchestration messages, the embedded browser. Before building something, check what exists: `orca --help` and `orca <command> --help`. A periodic job is an Orca automation registered in `scripts/automations.py` (`AUTOMATIONS`) and turned on with `skills-sync setup enable <name>`.
2. **Claude Code native second.** Hooks, Monitor and background tasks, `/goal`, subagents, skills, settings.
3. **Our own script last**, and only for what the two above do not cover.

macOS LaunchAgents, cron entries and self-run daemons are exceptions, not a third option. A job that has to run while Orca is closed, or a server that must stay up, can be one. Every exception is a row in the table below with its reason.

## LaunchAgent exceptions

This table is read by a test (`scripts/test_intent.py`, part of CI). Code that writes a LaunchAgent plist (`plistlib.dump` or `plistlib.dumps`) in a file that is not a row here fails it, and so does an `exception` row whose file no longer writes one. Add the row in the same change that adds the code, with the reason.

| File | Why it is not an Orca automation | Status |
|---|---|---|
| `scripts/preview_watch.py` | Closes the tailnet `serve` entries it opened and must keep watching when Orca is closed | exception |
| `scripts/share_server.py` | A read-only file server that has to stay up (KeepAlive) whether or not Orca runs | exception |
| `skills/orchestrate/scripts/dash.py` | The dashboard web server is a long-running process with KeepAlive, not a periodic round (the `orch-dash` automation only checks it) | exception |
| `scripts/reap_hard.py` | Old `install` command from before the `reap-hard` automation; the automation replaced it | migrating |
| `skills/reap-resources/scripts/pressure.py` | Old `install` command from before the `pressure-watch` automation; the automation replaced it | migrating |
| `skills/reap-resources/scripts/sweep.py` | `install` now turns on the `sweep` automation; `uninstall` only removes the LaunchAgent an earlier install wrote | migrating |

`migrating` rows are on their way out: a row is deleted with its code, and a new `migrating` row needs a reason in the change that adds it.
