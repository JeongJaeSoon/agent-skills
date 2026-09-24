---
name: handoff-ticket
description: Use when handing tickets to fresh Orca worktree cards — "핸드오프", "다음 티켓/작업 진행해줘", "남은 것 진행해줘", "새 카드 띄워줘", "새세션 실행하고 /goal", "병렬로 진행", "handoff" — and the moment this session's ticket is done (every acceptance criterion met) to decide what runs next. Not when the target is another repo or this session keeps working (dispatch-card).
---

# Orca handoff

Finish here, start each follow-up ticket in its own worktree card, never in this one.
`orca worktree create` is the only way work leaves this session. Ticket operations go through
`use-tracker`; the worklog goes through `use-notes`.

Triggers, all meaning this skill: "핸드오프", "핸드오프해줘", "핸드오프해서 진행해줘"; "다음 티켓",
"다음 작업", "다음 개발 아이템" (진행해줘·제안해줘·정해줘·확인해줄래); "남은 것 진행해줘",
"나머지 진행해줘"; "새 세션", "새 worktree 세션", "새 카드 띄워줘", "새세션 실행하고 /goal";
"병렬로 진행", "동시에 진행", "병렬 세션 실행"; "handoff", "handoff to next session". In this
setup a handoff always means an Orca worktree card — no other dispatch mechanism.

Not a handoff: the target is another repo, or this session still has its own work to finish
after spawning. That is `dispatch-card` — it never closes this session.

## 0. At "done", decide what happens next

Done means every acceptance criterion on the ticket is met (`deliver-ticket` §6) — usually the merge
plus whatever the ticket asks for after it, such as a release or a verification on main. That
last criterion, not the merge commit, is the decision point.

**Inside a program.** If this session's brief or prompt has a `PROGRAM: <slug>` line
(`orchestrate` `references/brief.md`), it does not pick or spawn the next ticket: after
`worker_done` it idles, and the coordinator decides what runs next. Nothing else makes a
session a program worker. The rest of this skill is for sessions without that line.

**Loose ends.** A review comment, or a fix that lands right here, is just work: do it. File a
ticket (`write-ticket`) when the thing needs its own investigation, its own decision, or its
own diff. Then it is the next ticket, not this one.

**Feedback on in-flight work is a new ticket.** When the user says "이거 더 낫게" about a card
or PR that is already running, file it as its own ticket and hand it off (or queue it). Never
append it to the running card's prompt or scope.

**Dependent tickets.** When ticket B can only start after A, record it in the tracker (A
blocks B) and, when both will be PRs, plan them as a GitHub stack: B's card starts from A's
branch (§3 `--base-branch`), and the two land in one merge from the top (`deliver-ticket` §5,
`orchestrate` `references/landing.md` "GitHub stacks").

**Who runs it.** A small defect in the code this session just shipped stays here, on its own
branch and its own PR. A session can ship N PRs this way — one ticket each, one at a time — so
no count ends a session; compaction is the brake. Anything that stands on its own is handed
off. If this session has already compacted, hand off the small ones too — a fresh card with a
well-written `--prompt` beats a session that lost its own first half.

**What is next.** Rank the unstarted tickets in the same tracker project or parent by priority,
dependency (blocked tickets wait), and file collision with cards already running. Spawn it
yourself when the user said to keep going, or when one candidate is clearly next. Ask when the
ranking is genuinely contested — then give the ranking, a recommendation, and any
parallel-safe combination.

A standing instruction is that permission, already given: "작업 끝나면 다음 티켓 진행해줘",
"알아서 다음 작업 진행해줘", "남은 것 진행해줘". Act on it, do not re-ask.

**What may be asked.** The one question this section allows is *which* ticket comes next, and
only when the ranking is genuinely contested. Whether to file a loose-end ticket, whether to
spawn a card, and whether to close this session are already decided above — never ask them.
File the loose-end tickets before asking about the ranking, so the findings survive a stalled
answer; `write-ticket` skips its approval gate for exactly this case.

## 1. Confirm this work is actually done

Done is defined in `deliver-ticket` §6 and nowhere else — walk it there, and if anything is still open,
finish it there first. The exception is work the user asked to hold: the ticket stays in review
(`started`) and this session keeps its worktree (see §5).

## 2. Resolve the tickets to hand off

Take the identifiers from the user's message. If none was given, take the next unstarted ticket
in the same tracker project or parent. Never guess a ticket number; ask with `AskUserQuestion`
if unsure.

Parallel requests ("병렬로", "동시에") still mean one card per ticket — repeat step 3 per ticket,
never two tickets in one prompt. Hold back a ticket that would edit the same files as one already
running until the first lands, and say what you held — unless it is a dependent layer, which
starts now as a stack on the lower one's branch.

## 3. Spawn the worktree with its agent

```bash
orca worktree create \
  --name "<ticket-id-lowercase>" \
  --base-branch "<branch this ticket must stack on, else omit>" \
  --no-parent \
  --agent claude \
  --prompt "/goal <TICKET-ID>" \
  --comment "<ticket URL>" \
  --json
# name the card after the ticket, not Orca's automatic title (the tab title is the agent's; a rename does not stick)
orca worktree set --worktree "path:<result.worktree.path>" --display-name "<TICKET-ID> <short title>" --json
# the setup terminal is the card's row without agentIdentity; close it once setup exits
orca terminal list --worktree "path:<result.worktree.path>" --json
orca terminal wait --terminal <setup handle> --for exit --timeout-ms 1800000 && orca terminal close --terminal <setup handle>
```

- Link the card to the ticket the way the active tracker allows (`use-tracker` → "Link an Orca
  card"; on Linear, add `--linear-issue "<TICKET-ID>"`). If the link is rejected, the
  `--comment` URL is enough and the new session moves the ticket itself.
- `--name` becomes the branch name (with the user's branch prefix, if Orca adds one).
- Stacked work: pass the lower ticket's branch as `--base-branch` and say in the prompt
  "stack on <lower ticket> (`gh stack link <lower PR> <this PR> --base main`)".
- Do not use `orca terminal create`: that adds a tab to this worktree instead of a new card.
- `--prompt` is the whole context the new session gets. It knows nothing from here, so put
  anything non-derivable from the ticket (decisions, base branch, gotchas) after the ticket id.

## 4. Verify it started

```bash
orca terminal wait --terminal <result.agentTerminalHandle> --for tui-idle --timeout-ms 60000
orca terminal read --terminal <handle>
```

`tui-idle` also fires on a permission or question prompt. Read the output and confirm the
agent picked up `/goal` before reporting success. Report the worktree path, branch and handle.

## 5. Close this session

Only after step 4 is verified. **REQUIRED SUB-SKILL:** read and follow the `end-session`
skill for the state check, the command and the order inside
the final turn. Which ending applies comes from `orca worktree current`, never from an
assumption that this session is a card — `isMainWorktree: true` rules out `worktree rm`.
In addition to what that skill asks for, the report names the cards you spawned (path,
branch, handle) and any tickets you filed, and the same content must already be in the
ticket's completion comment and the worklog before the command runs.
