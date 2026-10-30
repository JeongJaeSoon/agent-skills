# Cards: which Orca card goes

The one rule for closing finished Orca cards, for a person's `cards.py scan` and the janitor's `card` kind alike.
`scripts/cards.py` `judge()` is this table in code; `test_cards.py` holds a case for each trap below.

Every row of `orca worktree list` on this host except the main checkout is read, whatever made it: the app, `orca
worktree create`, an orchestration worker. The first row that matches decides.

| # | When | Verdict |
|---|---|---|
| 1 | Protected: pinned; its directory or display name starts with a `reap.cards.protect_prefixes` entry (default `lead-`); the card this scan runs in; an Orca automation's `--workspace`; a worker turn `in_progress`; a child card still open; a Claude session in it started as a lead or the orchestrator, or that declared that `ROLE` | 남김 |
| 2 | git status, the terminal list or a transcript could not be read | 남김 |
| 3 | An agent turn is running: a Claude session in the card runs a shell (a Bash call, a background shell, a Monitor), or its agent terminal wrote output in the last 5 minutes | 남김 |
| 4 | The agent's screen waits on a person (a question or permission dialog) | 사람 결정 |
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

삭제 closes each listed terminal (`orca terminal close --terminal <handle>`, never `--all`), judges the card again
once they have exited, moves its ignored files to the trash and runs `orca worktree rm --worktree path:<p>
--run-hooks`. `apply` and the janitor's `reap` act only when the verdict and HEAD are still the plan's.

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

## Config

```json
{"reap": {"idle_minutes": 30, "cards": {"protect_prefixes": ["lead-"], "done_patterns": ["닫아도\\s*(된|돼)"],
          "question_patterns": ["Enter to select"]}}}
```

Patterns replace the defaults in `cards.py` (`DONE_PATTERNS`, `QUESTION_PATTERNS`); they are matched against the
last 40 rendered lines of each agent terminal (`orca terminal read --screen`).
