---
name: dispatch-card
description: "Use when sending work to another repo, or to a side branch of this one, as a new Orca card while this session keeps going — \"별도의 세션을 만들어서 ~ 해줘\", \"별도 세션으로 진행해줘\", \"현재 세션은 두고 ~\", \"~ 에 위임하고 너는 X 계속해\", \"~ repo 에 작업 지시해줘\", \"dispatch to <repo>\". Not \"끝났으니 다음\" (handoff-ticket), sub-agents, or Codex."
---

# Orca dispatch

Pack what the new session needs to know into a brief, create the card in the target repo,
confirm it started, come back to your own work.

Triggers, all meaning this skill: "~ repo 에 작성하자", "~ repo 에 지시해줘", "orca 로 ~ 업무
새로 지시해줘", "~ 에 위임하고 너는 손 떼", "위임해서 진행시키고 너는 ~ 진행해줘", "기존 작업
하면서 ~ 도 진행해줘", "저쪽 repo 에서 하라고 해줘", "다른 repo 에 맡겨줘", "곁가지로 띄워줘";
"dispatch", "dispatch to <repo>", "cross-repo". "병렬로" alone is not a trigger (it usually
means handoff or a sub-agent); only when paired with this session continuing. Not for
"서브에이전트로" (Agent tool) or "코덱스에게" (`codex:rescue`).

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

Where the work goes is a separate question, and only one answer is this skill:

- "서브에이전트로", "sub-agent 로 진행해두고" → the Agent tool, in this session. Not this skill.
- "코덱스에게 위임", "codex 와 함께" → `codex:rescue`. Not this skill.
- A new Orca card / worktree, in this repo or another → this skill.
- Several tickets supervised to a finish line, with this session as the coordinator that lands
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
session as not the human's, so a "사용자 승인" sent afterwards was refused for a rename, a
workflow dispatch and a merge, while cards that started with `/goal` and these lines ran to the
end. The brief has:

- **Background** — what happened in repo A and why this work follows from it.
- **Sources** — links, not summaries: repo, PR, commit, note paths the target session reads itself.
- **Deliverable and done** — the exact output and what makes it complete.
- **Do not** — repo A is off limits; anything else the target must leave alone.
- **Approved** — the acts the human already approved for this work, each with their words and
  the date (merge, deploy, restarting an app, a settings or language change, an upgrade). The
  card decides inside them with the recommended default and reports what it chose; it does not
  ask.
- **Human only** — what stays the human's: permission dialogs, logins, payments, document
  submissions, and any irreversible or outward act not under Approved. The card puts these to
  the human in one batched message and keeps doing the rest.
- **Model calls** — one line: "다른 모델 호출(서브에이전트, Codex, API)은 필요할 때만, 호출 전에
  무엇을 왜 부르는지 보고하고, 모델은 그때 고른다". Never name a model in the brief: a brief that
  said to use a small model for cheap checks had the card calling it over and over for nothing.
- **Target repo rules** — one line: "그 repo의 CLAUDE.md와 스킬을 확인하고 적용하라". Never paste
  its CLAUDE.md or skills into the brief; that session reads them itself.
- **Close** — one line: "`CLOSE`: 완료 기준을 채우고 결과를 적으면 `end-session` §8대로 같은 턴에
  스스로 닫는다". Without it the card idles after its last report and nobody closes it: a card
  whose PR merged stayed open 38 hours. `KEEP` instead only when this session will reuse the card.

A card that will run a project for this session through cards of its own (a lead) gets
`orchestrate` `references/brief.md` "Lead" instead of the list above.

Exceptions. A task that fits in three or four lines goes inline in `--prompt` after `/goal`, no
note; the Approved and `CLOSE` lines still go in. Product backlog goes through `write-ticket`
first and the prompt becomes `/goal <TICKET>`, with the Approved line after the ticket ID.
Feedback the user gives on work a card is already doing is a new ticket and a new card, never
an addition to the running card's brief. So is a wider approval: when a card stops on an act
its first prompt did not approve, start a new `/goal` card whose Approved line carries it; do
not send the approval by message, and do not do the act yourself.

## 3. Create the card

Same command and conventions as `handoff-ticket` §3. What differs:

```bash
orca worktree ps                     # a card already on this task? report it, do not add another
orca worktree create \
  --repo name:<repo> \
  --name "<target>-<topic>" \
  --parent-worktree active \
  --agent claude \
  --prompt "/goal <goal>. 브리프: <note path> + 3-line summary, or /goal + the inline brief" \
  --comment "<note path>" \
  --json
# name the card by what it does, not Orca's automatic title (the tab title is the agent's; a rename does not stick)
orca worktree set --worktree "path:<result.worktree.path>" --display-name "<ID or target> <short title>" --json
# close the setup terminal once setup finishes; it stays a shell, so `terminal wait --for exit` never fires
# (run it in the background: it waits up to 30 min, past the 10-min foreground Bash limit)
skills-sync close-setup "<result.worktree.path>"
```

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
```

Once. Read that the agent picked up the brief, report the worktree path, branch and handle, and
return to what this session was doing. `--parent-worktree active` is how this session finds the
card again when it ends (`end-session` §3); a `--no-parent` card goes in the worklog instead. Results are fire-and-forget by default: do not loop on
`tui-idle`, do not block repo A on repo B.

Only when the user says "결과 받아와": tell the target session in the brief to append its result
to the brief note, and read the note when this session actually needs it.
