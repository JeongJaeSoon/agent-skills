---
name: handoff-ticket
description: "Use to hand the next ticket to a fresh Orca card — \"핸드오프\", \"다음 티켓 진행해줘\", \"남은 작업 있어?\", \"머지하고 다음 진행해줘\" — and when this ticket is done, to pick what runs next. Another repo is dispatch-card. An orchestrator dispatches it instead."
---

# Orca handoff

Finish here, and start each follow-up ticket that stands on its own in its own worktree card
(§0 "Who runs it" keeps a small defect in what this session just shipped here).
`skills-sync handoff` (§3) is the only way work leaves this session. Ticket operations go through
`use-tracker`; the worklog goes through `use-notes`.

Not a handoff: the target is another repo, or this session still has its own work after
spawning (`dispatch-card`, which never closes this session). "병렬로 진행" and "동시에 진행" mean
this skill only when they name same-repo ticket cards; settle which before creating cards.

## 0. At "done", decide what happens next

Done means every acceptance criterion on the ticket is met (`deliver-ticket` §6) — usually the merge
plus whatever the ticket asks for after it, such as a release or a verification on main. That
last criterion, not the merge commit, is the decision point.

**Inside a program.** If this session's brief or prompt has a `PROGRAM: <slug>` line
(`orchestrate` `references/brief.md`), it does not pick or spawn the next ticket: after
`worker_done` it closes itself or idles per `end-session` §7, and the orchestrator or lead that dispatched it decides what
runs next. Nothing else makes a session a program worker. The rest of this skill is for sessions without that line.

**Loose ends.** A review comment, or a fix that lands right here, is just work: do it. Anything
else is classified NOW / LATER / DROP (`use-tracker`
[references/exit-condition.md](../use-tracker/references/exit-condition.md)); only a NOW finding
becomes a ticket (`write-ticket`), and then it is the next ticket, not this one.

**Feedback on in-flight work is a new ticket.** When the user says "이거 더 낫게" about a card
or PR that is already running, file it as its own ticket and hand it off (or queue it). Never
append it to the running card's prompt or scope.

**Dependent tickets.** When ticket B can only start after A, record it in the tracker (A
blocks B). Only when B uses A's code is it a stack: B's card starts from A's branch (§3
`--base-branch`). Same files → B starts after A lands, unless they share only a hotspot (a
`translated-from` line, a generated file); a deploy order alone → both on the default branch (`deliver-ticket` [pull-request.md](../deliver-ticket/references/pull-request.md) "Stacks").

**Who runs it.** A small defect in the code this session just shipped stays here, on its own
branch and its own PR. A session can ship N PRs this way — one ticket each, one at a time — so
no count ends a session; compaction is the brake. Anything that stands on its own is handed
off. If this session has already compacted, hand off the small ones too — a fresh card with a
well-written `--prompt` beats a session that lost its own first half.

**What is next.** Rank the unstarted tickets in the same tracker project or parent that serve
an open exit item (the parent's `✅ 완료 조건`) by priority, dependency (blocked tickets wait),
and file collision with cards already running. When every exit item is met, close the parent
and report it instead of starting more, even with lines left on `⏸️ 보류`. Otherwise start the
recommended ticket and report the ranking, the recommendation and any parallel-safe combination.
When the ranking is genuinely hard, settle it with an independent review (Codex when it answers,
else a fresh-context Claude reviewer), record the verdict and the choice (`orch decide add …
--recommend <N> --if-no-answer <that label> --reason hard-after-review`, then at once `orch decide
done <id> --answer "<choice>: <verdict>"`), start it, and list it in the report for the owner to
review later.

A standing instruction is that permission, already given: "작업 끝나면 다음 티켓 진행해줘",
"알아서 다음 작업 진행해줘", "남은 것 진행해줘". Act on it, do not re-ask.

**Nothing here is asked.** Whether to file a loose-end ticket, whether to spawn a card, which
ticket comes next and whether to close this session are decided above. A question that still
seems needed goes through the owner-ask gate
([owner-ask-gate.md](../orchestrate/references/owner-ask-gate.md)) first. File the NOW loose-end
tickets before the next card starts, so the findings survive.

## 1. Confirm this work is actually done

Done is defined in `deliver-ticket` §6 and nowhere else — walk it there, and if anything is still open,
finish it there first. The exception is work the user asked to hold: the ticket stays in review
(`started`) and this session keeps its worktree (see §5).

## 2. Resolve the tickets to hand off

Take the identifiers from the user's message. If none was given, take the next unstarted ticket
in the same tracker project or parent. Never guess a ticket number: query the tracker first
(`use-tracker`: the parent's children, the project's unstarted tickets, a search on the user's
words), and only when that still names none, go through the owner-ask gate
([owner-ask-gate.md](../orchestrate/references/owner-ask-gate.md)).

Parallel requests ("병렬로", "동시에") still mean one card per ticket — repeat step 3 per ticket,
never two tickets in one prompt. Hold back a ticket that would edit the same files as one already
running until the first lands, and say what you held — unless they share only a hotspot (parallel),
or it uses the first one's code: a stack layer starts now on the lower one's branch.

## 3. Spawn the worktree with its agent

```bash
skills-sync handoff <TICKET-ID> \
  --title "<short title>" \
  --reference "<ticket URL, or a Jira key>" \
  --note "<what the ticket does not say: decisions, gotchas, approvals>" \
  --base-branch "<lower branch whose code this ticket uses, else omit>"
```

It creates the `/goal <TICKET-ID>. <note>` card with its fixed rows, names it `<TICKET-ID> <short
title>` (the tab title is the agent's, a rename does not stick), logs it with `orch sent add`, closes
its setup terminal in the background and reads the start (§4). It stops with exit 2 when a live
card already holds the ticket (or a `--reference`) in the same `--role`; `--supersede` replaces it
as `dispatch-card` §3 says. Exit 5 means the card exists but its start, link or name is not
confirmed: look at it, do not create it again.

- `--reference` links the ticket as `dispatch-card` §3 says. Do not add the legacy
  `--linear-issue` or `--issue` flags beside it: Orca refuses the mix. If the reference is
  rejected, create the card without it and put the ticket URL in the prompt; the new session
  moves the ticket itself.
- This is a handoff, not supervised work: it has no REPORT line and stays a `/goal` card, not an
  Orca worker (Orca's roles: a handoff creates no Run, Task or Dispatch).
- The parent keeps the next card in the same Orca lineage: the helper passes this card's own
  parent (`orca worktree show`'s `parentWorktreeId`), so it stays under the lead or orchestrator
  after this card closes, or this card when it has none. `--no-parent` only for a ticket
  unrelated to this session's work.
- The name, the ticket id in lowercase, becomes the branch name (with the user's branch prefix,
  if Orca adds one).
- Stacked work: pass the lower ticket's branch as `--base-branch` and say in `--note`
  "stack on <lower ticket> (`gh stack link <lower PR> <this PR> --base main`); rebase only".
- Not `orca terminal create`: that adds a tab to this worktree instead of a new card.
- The prompt is the whole context the new session gets: put anything not derivable from the
  ticket (decisions, base branch, gotchas) in `--note`, and approvals beyond the ticket's normal
  flow (a deploy, a restart, a settings change) as an `APPROVED:` line of the note (without one the
  card gets `APPROVED: none`) with the Human only and Model calls lines
  (`dispatch-card` §2). The first prompt is the only place an approval reaches the card; one sent
  later by message is refused as not the human's.

## 4. Verify it started

`skills-sync handoff` waits for `tui-idle` and reads the new agent's screen: exit 0 and
`card.start` `started: …` only when a turn runs or the `/goal` prompt was taken. `tui-idle` also
fires on a permission or question prompt, which it reports as `trust` or `dialog` with exit 5;
read `orca terminal read --terminal <card.handle> --screen` then. Report the worktree path,
branch and handle.

## 5. Close this session

Only after step 4 is verified. Read and follow the `end-session`
skill for the record, the command and the order inside the final turn. This session closes
its own terminal; `end-session`'s "Who cleans up" takes the card.
In addition to what that skill asks for, the report names the cards you spawned (path,
branch, handle) and any tickets you filed, and the same content must already be in the
ticket's completion comment and the worklog before the command runs.
