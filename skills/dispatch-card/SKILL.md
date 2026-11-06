---
name: dispatch-card
description: "Use to send work to another repo, or a side branch here, as a new Orca card while this session goes on — \"별도 세션으로 진행해줘\", \"~ repo 에 작업 지시해줘\" — and for an orchestrator's change work. The next ticket is handoff-ticket."
---

# Orca dispatch

Pack what the new session needs to know into a brief, create the card in the target repo,
confirm it started, come back to your own work.

## 0. Dispatch or handoff — and is it even this skill

The user's phrasing decides it. **"위임하고 너는 X 계속해"** — "~에 위임하고 너는 손 떼",
"위임해서 진행시키고 너는 원래 작업 진행해줘", "기존 작업 하면서 ~도 진행해줘" — is dispatch:
this session has its own work after the instruction. **"끝났으니 다음"** — "작업 끝나면 다음
티켓", "남은 것 진행해줘" — is `handoff-ticket`.

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

Where the work goes: "서브에이전트로", "sub-agent 로 진행해두고" → the Agent tool, in this session;
several tickets supervised to a finish line, with this session as the lead that lands their PRs →
`orchestrate`; a new Orca card in this repo or another → this skill. "병렬로" by itself names none
of them (most often same-repo ticket cards, which is handoff, or sub-agents); it is dispatch only
when this session keeps working. Read the rest of the sentence.

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
- **Do not** — repo A and anything else the target must leave alone, in the `FORBIDDEN` row
  after its marked section. `skills-sync card` (§3) writes the current `orchestrate`
  `references/brief.md` FORBIDDEN (through "run it again.") between `<!-- FORBIDDEN:begin -->` and
  `<!-- FORBIDDEN:end -->`, replacing an older copy; lines outside the markers stay yours. An inline
  `--prompt` card with no brief file pastes that paragraph itself, by its first and last words.
  Teardown and owner-facing docs need no row: `skills-sync card` adds its fixed `TEARDOWN` (stacks
  down in the same turn, never `-v`; kill only recorded PIDs, never pkill or killall) and `DOCS`
  (owner-facing documents as an Artifact only, no local `.md` copy or file path) rows to every
  prompt; an inline card writes them out.
- **Approved**, **Human only**, **Model calls** — the `APPROVED`, `HUMAN ONLY` and `MODEL CALLS`
  rows of `orchestrate` `references/brief.md` "Single worker". Only the `APPROVED:` row reaches the
  first prompt (a heading or bullet does not); the card reads the other two in the brief. APPROVED
  cites each owner quote by the `orch decide answer` id that recorded it, as `오너(d150, <date>)`;
  `skills-sync card` refuses an id the store does not hold as answered. A card
  that lands its own PR carries the merge there (`APPROVED: required checks green + independent
  review Act-on 0: merge with orch land` plus the human's words and date); without it, it stops at
  READY. Before an item goes into HUMAN ONLY, check for an agent-only path first (a script, an
  existing allow rule, a card that holds the access); only what still needs the human's own hands
  goes there.
- **Questions** — only for a brief that reports to `run:<id>` and goes out as a `/goal` card (§3
  says which): one line telling the card how to ask what Approved leaves open. Orca refuses
  `ask` and `--type escalation` from a session with no Dispatch, so the card sends
  `orca orchestration send --to run:<id> --type question --subject … --body …`; a status wakes
  no inbox wait and stays off the dashboard's Needs you. The answer is the `reply` to it: a
  `status` message whose `thread_id` is the question's id. Once nothing else can move, wait for
  it with `orch inbox --wait` under `run_in_background`: it prints each message as one line (the
  reply's carries `thread=<question id>`) and the next command, which acks that batch and waits
  again. A card that coordinates a Run of its own (a lead) reads the reply in the one inbox wait
  it already runs, never in a second check loop beside it.
  To look again, `check --all` and match `thread_id`; never a list you sort or cut yourself (a
  card sorting by a field the rows do not have, `createdAt` for `created_at`, missed two replies).
  A supervised worker needs no such line: its preamble's `ask` does this.
- **Changes, Pitfalls, Examples** — optional. A card starts with only what the brief says, so
  write down what you already know: the files and functions to change, what has gone wrong there
  before, an input with its expected output. Leave out what you do not know; the card reads the code.
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
carries the `MODE`, `ROLE: lead <slug>`, `INBOX`, `DELEGATE`, `PREDICATE`, `MERGE`, `TASKS` and `GATE` lines; the hook refuses a lead's prompt (a `MODE`
line or a `<project>: lead` head) that lacks `MODE`, `ROLE`, `DELEGATE` or `PREDICATE`. The `ROLE` line binds the
lead role to the card's session, so the reorient hook reminds it of its program on every prompt. It is also what makes
`skills-sync card` start the card on `orchestrate.models.lead`: a brief without it is a worker's, so the helper refuses
a brief that has `DELEGATE`, `PREDICATE`, `INBOX`, `TASKS` or `GATE` rows, or a card named `lead`, `lead:<slug>` or `<slug>-lead`, and no `ROLE` line. It stands
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
  --comment "<brief path>" \
  --reference "<ticket URL, or a Jira key>"   # one per ticket; omit when there is none
  # --role verify|review for a second card on the same ticket that checks, not implements (default impl)
  # --agent codex for a Codex card (default claude); only a supervised worker, see below
  # --model <id> when the human named one; otherwise orchestrate.models.lead or .worker from agent-skills.json
  # without --model, a brief row `KIND: design|edit|lookup — <reason>` picks opus|sonnet|haiku (not for a lead)
```

The helper starts `skills-sync close-setup <path> --agent <handle>` in the background itself:
the setup terminal stays a shell after its script, so `terminal wait --for exit` never fires. Its
log (the output's `card.close_setup.log`) ends in `result: failed` when the agent sits on a trust
or permission dialog or shows no start; read it before §4, and trust a path as in `orchestrate`
references/top-level.md "Starting a worker".

- A card already on the task (the same PR, branch or ticket). With `--reference`, `skills-sync
  card` stops on a live card of the same repo, reference and `--role` (exit 2, the cards listed);
  `--supersede` starts the new one anyway and sends the old one the line below, logged and
  recorded. If it only needs to be told
  something, send that to it (`skills-sync nudge <handle> "<message>"`, logged with `orch sent
  add`; never `SendMessage`, which is not the user's word) and create no card. If the work needs
  a different implementation, ask it for a state
  summary (done, left, branch and PR), put that summary in the new card's brief, and in the turn
  you create the new card tell the old one `superseded by <new card>: stop and close by
  end-session §8` and drop the questions it left waiting (`orchestrate` "Stay answerable").
  Without the summary the new card redoes or undoes finished work; left open, the old card waits
  on a question about work the new card already did.
- Which card it makes. A brief whose REPORT or PARENT line sends to `run:<id>` is work this session
  waits on. From a session bound to that Run (`orca orchestration run-current`; bind one as in
  `orchestrate` references/top-level.md "Your Run"), or itself a dispatched worker on it (`orch
  doctor` says which), `skills-sync card` starts a supervised worker (`orca orchestration
  worker-start --worktree new-child`, your worktree its Orca parent; `new-top-level` with
  `--no-parent`): a Dispatch whose escalation, `ask` and `worker_done` reach your Run, and which Orca
  watches for a prompt only a human can answer. Everything else is a `/goal` card from `orca worktree
  create`: a brief with no Run report (fire-and-forget), a lead's brief while Orca's Nested worker
  depth is below 3 (the helper prints the setting to change), or `--card`. On neither path does the
  prompt's `/goal` line run as the slash command (Orca's preamble comes first; a long paste folds
  into `<pasted_content>`), so for every Claude card a detached helper types that one line once the
  first turn ends, unless the transcript shows it ran; after an hour it types nothing and logs the
  manual command (`card.goal_input`). A Run brief from a session bound to no Run, or to another Run
  than the brief's `run:<id>`, creates nothing (exit 6): bind the brief's Run (`orca orchestration
  run-use --id <id>`), fix the brief's Run, or pass `--card` for a `/goal` card on purpose. The
  output ends in a `card` object: `kind` (worker or card), `path`, `handle`, `run`, `parent`.
- `--agent codex` passes codex to Orca, and the brief checks are the same as for Claude. It starts
  only a supervised worker. A brief with no Run report, `--card` or a lead's brief is refused (exit
  2, nothing created, never switched to claude): a leading `/goal` and the start read from the screen
  are not proven on Codex, and a lead's `ROLE` row works through this plugin's Claude Code hooks.
  `close-setup` then gets no agent handle, so its log says nothing about the start; the worker-start
  receipt does.
- A brief that sends to a `term_…` handle, in any line, is refused (exit 2): the handle dies with
  its terminal, so a report sent after a restart is refused (`terminal_not_found`), and until then
  it is only stored. Write `orca orchestration send --to run:<your run id>`.
- `--reference` links the ticket in Orca (the card's `linkedItems`), so `orca reference find
  <ticket>` finds the card and `orca linear attach --current` works from it: on create for a
  `/goal` card, by `orca reference add` on the new card right after the start for a worker.
  Linear and GitHub take the full URL; a bare key is read as Jira.
- `skills-sync card` writes the brief's `/goal` line (plus the brief's path, unless the line
  names one), PROGRAM, APPROVED, lead, CLOSE or KEEP and LOAD rows, its fixed TEARDOWN and DOCS rows,
  the FORBIDDEN section whole and every REPORT row into `--prompt` (or the worker's spec) itself, logs a
  `/goal` card with `orch sent add` (a worker's spec is its Orca Task, which the Sent page reads),
  sets the display name and prints Orca's create output. A row is a line outside
  a ``` fence that starts with its key and a colon (`CLOSE: …`), or the Lead template's wide gap; a
  `## Close` heading is not one. The brief is a local file: a note only the notes store reaches is
  copied to one first. The guard hook checks that file as the helper does, but only for a plain
  `skills-sync card` call: a wrapper (`env …`, `bash -c`, running `scripts/card.py` itself) skips
  its lead-starts-lead test, since the hook is a guardrail, not a sandbox. The auto-mode
  classifier sees only the command and its path, and the helper is not auto-allowed. Never pass
  `--prompt "$(cat <brief>)"`: the hook reads only the command text, so it refuses that card even
  when the file holds the lines. A nonzero exit is a failing check, not a refusal; each code and its
  next step are in [references/exit-codes.md](references/exit-codes.md). Exit 4 is Orca's create or
  `worker-start` error: follow its receipt's recovery commands before running it again, and
  `absent` there is no proof that nothing started (check `orca worktree ps` and `orca
  orchestration worker-list` too). Exit 5: the card exists, do not create it again. Exit 6: nothing
  was created; pick one of the printed choices, never `--no-parent` or `--card` just to get past it.
  An inline brief with no file goes straight to `orca worktree create --prompt`, its lines written out.
- `--repo` is required. Omitted, Orca infers the current worktree's repo and the card lands in
  the wrong place.
- `--name` is `<target>-<topic>`, e.g. `blog-launch-post`. A ticket-backed card uses the
  ticket ID in its display name: `"ENG-123 <short title>"`.
- The parent is this terminal's own worktree by default: the helper looks it up (`orca terminal
  show --terminal $ORCA_TERMINAL_HANDLE --json`, `result.terminal.worktreeId`) and passes
  `--parent-worktree id:<it>`; no handle or no answer creates nothing (exit 6). Name another parent
  only as `id:<worktree id>`, never `path:` (refused: Orca workspaces sharing a path resolve to the
  repo's main worktree). `--no-parent` only for work with no relation to this session.
- Omit `--base-branch`; the target repo's default base is the right one.

## 4. Confirm the start, then go back to work

A worker (`card.kind` worker) needs no start check: `worker-start` exits 0 only once Orca saw it
ready. Read it with `orca orchestration worker-show --dispatch <id>` and `worker-read --dispatch
<id> --source auto` (a worker may have no terminal, and terminal verbs refuse such a handle), talk
to it with `orca orchestration send --to dispatch:<id>`, and wait for its `worker_done` in your
one inbox wait (`orchestrate` Drain, "One inbox wait, always"). `orch watch-cards` judges its
prompts from Orca's `agentWait`. A `/goal` card (`card.kind` card):

```bash
orca terminal wait --terminal <card.handle> --for tui-idle --timeout-ms 60000
orca terminal read --terminal <handle> --screen
orch watch-cards --card <handle>   # under run_in_background: STARTED, ENDED, CLOSED, or STUCK on a lost login, a prompt or no /goal
```

Take the handle only from the `card.handle` field of the output (Orca's `launch.outcome.handle`,
or `agentTerminalHandle` on an older Orca), never from a loose `term_…` match: the brief or its context can quote another handle (your own among them), and a
`/goal` or nudge sent to the first match lands on the sending session. Before any send,
check that the handle is not your own (`$ORCA_TERMINAL_HANDLE`).

Once. Read that the agent picked up the brief, report the worktree path, branch and handle, and
return to what this session was doing. The `--card` check reports about four minutes later, since a
card can lose its login a few turns in; on `STUCK`, read its screen, and for a lost login or a
missing `/goal` start a new card on the same brief and supersede this one (§3, first rule). `ENDED`
and `CLOSED` are a card that finished or stopped inside the window: read its report or screen first, and
start it again only when that shows the work is not done. The card's Orca parent is how this session finds the
card again when it ends (`end-session` §3); a `--no-parent` card goes in the worklog instead. Results are fire-and-forget by default: do not loop on
`tui-idle`, do not block repo A on repo B.

Only when the user says "결과 받아와": tell the target session in the brief to append its result
to the brief note, and read the note when this session actually needs it.
