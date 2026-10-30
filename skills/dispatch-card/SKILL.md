---
name: dispatch-card
description: "Use when sending work to another repo, or a side branch of this one, as a new Orca card while this session keeps going — \"별도 세션으로 진행해줘\", \"현재 세션은 두고 ~\", \"~ repo 에 작업 지시해줘\", \"dispatch to another repository\". Not the next ticket (handoff-ticket), subagents."
---

# Orca dispatch

Pack what the new session needs to know into a brief, create the card in the target repo,
confirm it started, come back to your own work.

## 0. Dispatch or handoff — and is it even this skill

The user's phrasing decides it. **"위임하고 너는 X 계속해"** — "~에 위임하고 너는 손 떼",
"위임해서 진행시키고 너는 원래 작업 진행해줘", "기존 작업 하면서 ~도 진행해줘" — is dispatch:
this session has its own work after the instruction. **"끝났으니 다음"** — "작업 끝나면 다음
티켓", "남은 것 진행해줘" — is `handoff-ticket`. "병렬로" alone usually means handoff or a
sub-agent; it is dispatch only when this session keeps working. "서브에이전트로" is the Agent tool.

| | dispatch-card | handoff-ticket |
|---|---|---|
| This session | keeps going | finishes and closes |
| Target repo | another repo, or a side branch of this one | this repo |
| Input | a brief you write (§2) | a ticket, `/goal <TICKET>` |
| Relationship | parent → child card, both alive | successor card, this one gone |

This skill never calls `end-session` — dispatching is the reason this session stays alive. If
the target is this repo and this session is done, stop here and follow `handoff-ticket` instead.

Target is another repo → always this skill, whatever the phrasing; handoff never crosses
repos. The reverse does not hold — same repo is still dispatch when this session keeps working.

Where the work goes is a separate question, and only one answer is this skill:

- "서브에이전트로", "sub-agent 로 진행해두고" → the Agent tool, in this session. Not this skill.
- A new Orca card / worktree, in this repo or another → this skill.
- Several tickets supervised to a finish line, with this session as the lead that lands
  their PRs → `orchestrate`. Not this skill.

"병렬로" by itself says nothing about which: most of the time it is same-repo, ticket-based
parallel work (handoff) or sub-agents. Read the rest of the sentence.

## 1. Resolve the target repo

```bash
orca repo list                       # <id>  <name>  <path>
orca repo add --path <path> --json   # only when the repo is not listed
```

The selector for §3 is `name:<name>` from that list. Register first, then create — a
`--repo` pointing at an unknown repo does not fall back to anything useful.

A repo you have been working in from a plain shell is not automatically an Orca repo; check
the list before assuming it is.

## 2. Write the brief

This is the whole point of the skill. The new session knows nothing about repo A — not what
you did there, not why this task exists, not where the outputs are. Default: one note in the
notes store (`use-notes`; its rules file says where it goes), and a `--prompt` of
`/goal <the goal and its done, one sentence>. 브리프: <note path>` plus a three-line summary.
The `/goal` keeps the card working until the goal holds, and the first prompt is the only place
an approval reaches it: the card's auto-mode classifier treats a later message from another
session as not the human's, so an approval sent afterwards is refused. The brief has:

- **Background** — what happened in repo A and why this work follows from it.
- **Sources** — links, not summaries: repo, PR, commit, note paths the target session reads itself.
- **Deliverable and done** — the exact output and what makes it complete.
- **Do not** — repo A is off limits; anything else the target must leave alone. Paste from
  `orchestrate` `references/brief.md` FORBIDDEN, verbatim, the refused-act paragraph (a card
  restarted after a `no verdict` needs it most) and the sentences from "No force-push" through
  "each of them then counts as refused": no force-push, no rebase only to catch up, file edits
  with Edit and Write only, one file-changing act per Bash call, nothing chained. A card gets
  these rules from no other place. Copy them by those first and last words, not by line numbers.
- **Approved** — an `APPROVED:` row: the acts the human already approved for this work, each with
  their words and the date (merge, deploy, restarting an app, a settings or language change, an
  upgrade). Written as a row, it reaches the first prompt (§3); a heading or bullet does not. The
  card decides inside them with the recommended default and reports what it chose; it does not
  ask.
- **Human only** — what stays the human's: permission dialogs, logins, payments, document
  submissions, and any irreversible or outward act not under Approved. The card puts these to
  the human in one batched message and keeps doing the rest.
- **Model calls** — one line: "다른 모델 호출(서브에이전트, API)은 필요할 때만 하고, 모델은 그때
  고른다. 무엇을 왜 불렀는지는 마지막 보고에 적는다". Never name a model in the brief: the card reads
  a named model as an order and calls it whether or not the task needs it. The calls go in the
  final report because nobody reads an unattended card's chat before a call.
- **Target repo rules** — one line: "그 repo의 CLAUDE.md와 스킬을 확인하고 적용하라". Never paste
  its CLAUDE.md or skills into the brief; that session reads them itself.
- **Close** — one line: "`CLOSE`: 완료 기준을 채우고 결과를 적으면 `end-session` §8대로 같은 턴에
  스스로 닫는다". Without it the card idles after its last report and nobody closes it.
  `KEEP` instead only when this session will reuse the card.
  The line goes in `--prompt` itself, written out, wherever the rest of the brief lives: the
  plugin's guard hook refuses an `orca worktree create --prompt` without `CLOSE` or `KEEP`.
  `skills-sync card` (§3) copies it from the brief file for you.

A card that will run a project for this session through cards of its own (a lead) gets
`orchestrate` `references/brief.md` "Lead" instead of the list above, and its `--prompt` also
carries the `MODE`, `ROLE: lead <slug>`, `INBOX`, `DELEGATE`, `PREDICATE`, `TASKS` and `GATE` lines; the hook refuses a lead's prompt (a `MODE`
line or a `<project>: lead` head) that lacks `MODE`, `ROLE`, `DELEGATE` or `PREDICATE`. The `ROLE` line binds the
lead role to the card's session, so the reorient hook reminds it of its program on every prompt. It stands
in for `claude --agent agent-skills:lead`: `orca worktree create --agent` takes an Orca agent id, not CLI flags. Without these lines a lead reports done and waits for an order that never comes.

Exceptions. A task that fits in three or four lines goes inline in `--prompt` after `/goal`, no
note; the Approved and `CLOSE` lines still go in. Product backlog goes through `write-ticket`
first and the prompt becomes `/goal <TICKET>`, with the Approved line after the ticket ID.
Feedback the user gives on work a card is already doing goes by §3's rule for a card already on
the task: feedback that changes the work is a new ticket and a new card, never an addition to the
running card's brief. So is a wider approval: when a card stops on an act
its first prompt did not approve, start a new `/goal` card whose Approved line carries it; do
not send the approval by message, and do not do the act yourself.

## 3. Create the card

Same command and conventions as `handoff-ticket` §3. What differs:

```bash
orca worktree ps                     # a card already on this task? the first rule below
# the brief is a file holding a `/goal <goal>` line and its APPROVED, CLOSE or KEEP (and a lead's) rows
skills-sync card <brief file> \
  --repo name:<repo> \
  --name "<target>-<topic>" \
  --display-name "<ID or target> <short title>" \
  --comment "<brief path>"
# close the setup terminal once setup finishes; it stays a shell, so `terminal wait --for exit` never fires
# (run it in the background: it waits up to 30 min, past the 10-min foreground Bash limit;
#  a setup stopped on a trust prompt never ends: trust as in orchestrate references/top-level.md "Starting a worker")
skills-sync close-setup "<result.worktree.path>"
```

- A card already on the task (the same PR, branch or ticket). If it only needs to be told
  something, send that to it (`skills-sync nudge <handle> "<message>"`, logged with `orch sent
  add`) and create no card. If the work needs a different implementation, ask it for a state
  summary (done, left, branch and PR), put that summary in the new card's brief, and in the turn
  you create the new card tell the old one `superseded by <new card>: stop and close by
  end-session §8` and drop the questions it left waiting (`orchestrate` "Stay answerable").
  Without the summary the new card redoes or undoes finished work; left open, the old card waits
  on a question about work the new card already did.
- `skills-sync card` writes the brief's `/goal` line (plus the brief's path, unless the line
  names one), PROGRAM, APPROVED, lead and CLOSE or KEEP rows into `--prompt` itself, logs it with
  `orch sent add`, sets the display name and prints Orca's create output. A row is a line outside
  a ``` fence that starts with its key and a colon (`CLOSE: …`), or the Lead template's wide gap; a
  `## Close` heading is not one. The brief is a local file: a note only the notes store reaches is
  copied to one first. The guard hook checks that file as the helper does, but only for a plain
  `skills-sync card` call: a wrapper (`env …`, `bash -c`, running `scripts/card.py` itself) skips
  its lead-starts-lead test, since the hook is a guardrail, not a sandbox. The auto-mode
  classifier sees only the command and its path, and the helper is not auto-allowed. Never pass
  `--prompt "$(cat <brief>)"`: the hook reads only the command text, so it refuses that card even
  when the file holds the lines. A nonzero exit is a failing check, not a refusal: 2 names the
  missing line (add it to the brief), 3 is the pressure gate's reason (wait as it says, then run
  the same command), 4 is Orca's create error (Orca can fail a launch whose outcome it could not
  confirm: check `orca worktree ps` before running it again), 5 means the card exists but its log
  or name failed (do that step by hand; do not create it again). An inline brief with no file goes
  straight to `orca worktree create --prompt`, its lines written out.
- `--repo` is required. Omitted, Orca infers the current worktree's repo and the card lands in
  the wrong place.
- `--name` is `<target>-<topic>`, e.g. `blog-launch-post`. A ticket-backed card uses the
  ticket ID in its display name: `"ENG-123 <short title>"`.
- `--parent-worktree active` is the default lineage; `--no-parent` only for work with no
  relation to this session.
- Omit `--base-branch`; the target repo's default base is the right one.

## 4. Confirm the start, then go back to work

```bash
orca terminal wait --terminal <result.agentTerminalHandle> --for tui-idle --timeout-ms 60000
orca terminal read --terminal <handle> --screen
orch watch-cards --card <handle>   # under run_in_background: STARTED, ENDED, CLOSED, or STUCK on a lost login, a prompt or no /goal
```

Take the handle only from the `agentTerminalHandle` field of the create output, never from a
loose `term_…` match: the brief can carry another handle (your own, in its REPORT line), and a
`/goal` or nudge sent to the first match lands on the sending session. Before any send,
check that the handle is not your own (`$ORCA_TERMINAL_HANDLE`).

Once. Read that the agent picked up the brief, report the worktree path, branch and handle, and
return to what this session was doing. The `--card` check reports about four minutes later, since a
card can lose its login a few turns in; on `STUCK`, read its screen, and for a lost login or a
missing `/goal` start a new card on the same brief and supersede this one (§3, first rule). `ENDED`
and `CLOSED` are a card that finished or stopped inside the window: read its report or screen first, and
start it again only when that shows the work is not done. `--parent-worktree active` is how this session finds the
card again when it ends (`end-session` §3); a `--no-parent` card goes in the worklog instead. Results are fire-and-forget by default: do not loop on
`tui-idle`, do not block repo A on repo B.

Only when the user says "결과 받아와": tell the target session in the brief to append its result
to the brief note, and read the note when this session actually needs it.
