# Cards: which Orca card goes

The one rule for closing finished Orca cards, for a person's `cards.py scan` and the janitor's `card` kind alike.
`scripts/cards.py` `judge()` is this table in code; `test_cards.py` holds a case for each trap below.

Every row of `orca worktree list` on this host except the main checkout is read, whatever made it: the app, `orca
worktree create`, an orchestration worker. The first row that matches decides.

| # | When | Verdict |
|---|---|---|
| 0 | `orca worktree rm` of this card was refused and recorded (`scripts/rm_refused.py add <path>`, kept in `<state>/rm-refused.jsonl` by path and folder inode); the record exists and cannot be read keeps it too | 사람 결정: the reason is `사람에게 넘김` and the one `orca worktree rm` line a person runs; nothing is acted on, and `apply`, reap-hard and the guard skip it |
| 1 | Protected: pinned; its directory or display name starts with a `reap.cards.protect_prefixes` entry (default `lead-`); the card this scan runs in; an Orca automation's `--workspace`; a worker turn `in_progress`; a child card still open; a Claude session in it started as a lead or the orchestrator, or that declared that `ROLE`; `orch patrol` judged it working, waiting on a human or a Run worker, or kept it as parked or unknown, in a round under an hour old (the scan reads the verdicts file the way the guard does, and the reason names patrol's word for it; a `working` or `run` verdict reads a terminal, so it keeps a card only while the card still has an Orca terminal or a Claude process, and the rest of the rules decide a card with neither); the verdicts file exists but cannot be read; its newest worker Dispatch is `abandoned` or `interrupted` (a restart cut the worker off and a resume is due, `reboot`) and the card has any change in `git status` (untracked and regenerated copies included), an open PR, or no PR at all (an investigation card: the directory is what the resume goes on with) | 남김 |
| 2 | git status, the terminal list or a transcript could not be read | 남김 |
| 3 | An agent turn is running: a Claude session in the card runs a shell (a Bash call, a background shell, a Monitor), or its agent terminal wrote output in the last 5 minutes | 남김 |
| 4 | The agent's screen waits on a person (a question or permission dialog) | 사람 결정 |
| 4a | An agent terminal's screen could not be read: a question cannot be ruled out | 남김 |
| 5 | Any other process has its cwd in the card (a dev server, a command a person started) | 남김 |
| 6 | A shell terminal wrote output within `reap.idle_minutes` (default 30) | 남김 |
| 7 | The branch's PR could not be read (`gh`'s own error is in the reason) | 남김 |
| 8 | Open PR, every agent idle | 터미널만 닫기: the listed terminals go, the worktree stays |
| 9 | Changes, untracked files included (ignored files and Orca's regenerated `.agents/skills` copies aside) | 남김 |
| 10 | A commit off every remote whose message is on the remote with another patch | 사람 결정 |
| 11 | A commit off every remote with no such message | 남김 |
| 12 | Merged or closed PR that carried HEAD (HEAD is its head or behind it), every agent idle | 삭제 |
| 13 | No PR, HEAD on the remote default branch, no Claude session in the card, or every agent terminal said it is done | 삭제 |
| 14 | Anything else (HEAD past a merged PR's head, no PR and HEAD off the default branch, an agent not idle yet) | 남김 |

"Idle" for an agent: its transcript quiet for `reap.idle_minutes` (the terminal's own output time when it has no
transcript), or its screen says it is done. A Claude process in the card with no Orca agent terminal (a headless
`claude -p`, a session another tool started) is an agent too: idle only by its transcript, never when it has none.

Rule 8b, after rules 9 to 14: a card they keep (남김; a 사람 결정 stays one) whose transcript has been quiet for a
number of seconds and whose every agent terminal printed nothing for as long gives its terminals back: 터미널만 닫기,
the worktree and its changes stay. "Done" on the screen is no evidence of that quiet, an unreadable transcript is
none, nor is a terminal Orca gives no output time for; a keep for what could not be read (unpushed commits, the
default branch) never times out, and a Claude process with no agent terminal has nothing to close. The threshold is
`reap.cards.idle_close_hours` (default 2) for a card kept only because a session is there and never said it was done
(rule 13 with a live session: clean, nothing unpushed, HEAD on the default branch); it has nothing to lose, and with
its session gone the next round removes it by rule 13. That keep exists so a card just started (HEAD still on the
default branch) is not removed while its first turn thinks; two hours of silence on both counts is past that.
Every other keep (changes, unpushed commits, no PR off the default branch) uses `reap.cards.stale_hours` (default 24).
The reason names the session record folder (`~/.claude/projects/<cwd>`), and the close's line in `reap-actions.jsonl` keeps it, so it says where
`claude --resume` finds it. `0` turns either half off.

삭제 closes each listed terminal (`orca terminal close --terminal <handle>`, never `--all`), judges the card again
once they have exited, moves its ignored files to the trash and runs `orca worktree rm --worktree path:<p>
--run-hooks`. `apply` and the janitor's `reap` act only when the verdict and HEAD are still the plan's, checked before the
terminals close and again before the removal. A re-read that cannot see the worktree, worker or automation list or
the process cwds (what makes `scan` judge nothing) keeps the card.

Both `apply` here and reap-hard close a card only through `scripts/cardguard.py`: they hold the card's lock
(`<state>/locks/card-<hash>.lock`, so the janitor and reap-hard never work one card at once; the one that finds it
held skips the card), and `guard()` must pass before the terminals close and again before `worktree rm`. The guard
keeps the card when patrol judged it parked, unknown or waiting, or working or running while a terminal is open or to be closed or a Claude process runs in the card (one it could not look for keeps the card too), HEAD moved, a terminal opened since the judgement, Orca's agent-wait
or an agent's screen shows a prompt or cannot be read, or the session armed its own wake-up (parked); anything it
cannot read keeps the card too. When the card changed since the plan (a verdict or HEAD), `apply` says which one
and skips it. The scan also lists, apart, a card whose settled worker dispatch was never released: the Run that
owns it and `orca orchestration worker-release --dispatch <id>`, for that Run's lead or the orchestrator to run;
the cleaner never releases one.

## Traps the rule covers

- **A stale `origin/HEAD`.** A failed `git fetch` (an SSO re-authorization, a network error) leaves the local
  default branch behind, so "HEAD is on main" reads false. A local no is asked of GitHub again:
  `gh api repos/<o>/<r>/compare/<base>...<head>` with `ahead_by` 0 is on the base; a 404 is a commit never pushed.
- **A copy from before a rebase.** An unpushed commit with the same message as one on the remote is the same work
  only when `git patch-id --stable` matches; then it counts as pushed. Another patch goes to a person. A PR that
  carried HEAD needs no such check: after a squash merge and a deleted branch, its commits are on no remote.
- **Shells holding the card.** A setup shell or a finished background shell sits in the card as a process. An Orca
  terminal's shell at its prompt is not a reason to keep it; a shell terminal is closed with the card once silent
  for `reap.idle_minutes`, and the card judged again after.
- **"Done, close me" on an idle screen.** An agent that wrote that it is finished counts as idle before its
  transcript has been quiet long enough (`reap.cards.done_patterns`). The screen never outweighs changes, unpushed
  commits or a running turn, and a question on it (`reap.cards.question_patterns`) sends the card to a person.
- **A Claude session with no Orca terminal.** A headless `claude -p` thinking or editing between shell calls has
  no agent terminal and no shell child, so neither rule 3 nor rule 5 sees it. Its process is an agent for rules 8
  and 12, released once its transcript has been quiet for `reap.idle_minutes`; with no PR (rule 13) it keeps the
  card, since it has no screen to say it is done.

- **A worker cut off by a restart.** Its terminals are gone, so rules 3 and 5 see nothing, and the resumed session
  waits idle for its instruction: rule 8 would close that terminal and rule 9 ignores the regenerated copies. Rule 1
  keeps the card while the newest Dispatch on it is `abandoned` or `interrupted` and it holds changes, an open PR, or
  has no PR (an investigation card with nothing in git: the directory is what the resume needs); a resume (a newer
  `dispatched` row), or a PR that merged or closed with a clean tree, ends the hold.

## Config

```json
{"reap": {"idle_minutes": 30, "cards": {"protect_prefixes": ["lead-"], "done_patterns": ["닫아도\\s*(된|돼)"],
          "question_patterns": ["Enter to select"], "idle_close_hours": 2, "stale_hours": 24}}}
```

Patterns replace the defaults in `cards.py` (`DONE_PATTERNS`, `QUESTION_PATTERNS`); they are matched against the
last 40 rendered lines of each agent terminal (`orca terminal read --screen`).
