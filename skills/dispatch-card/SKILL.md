---
name: dispatch-card
description: Use when sending work to another repo, or to a side branch of this one, as a new Orca card while this session keeps going — "~ repo 에 지시해줘", "~ 에 위임하고 너는 X 계속해", "기존 작업 하면서 ~ 도 진행해줘", "다른 repo 에 맡겨줘", "곁가지로 띄워줘", "dispatch to <repo>". Not "끝났으니 다음" (handoff-ticket), sub-agents, or Codex.
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
notes store (`use-notes`; its rules file says where it goes), and a `--prompt` of the note path
plus a three-line summary. The brief has:

- **Background** — what happened in repo A and why this work follows from it.
- **Sources** — links, not summaries: repo, PR, commit, note paths the target session reads itself.
- **Deliverable and done** — the exact output and what makes it complete.
- **Do not** — repo A is off limits; anything else the target must leave alone.
- **Target repo rules** — one line: "그 repo의 CLAUDE.md와 스킬을 확인하고 적용하라". Never paste
  its CLAUDE.md or skills into the brief; that session reads them itself.

Exceptions. A task that fits in three or four lines goes inline in `--prompt`, no note. Product
backlog goes through `write-ticket` first and the prompt becomes `/goal <TICKET>`.
Feedback the user gives on work a card is already doing is a new ticket and a new card, never
an addition to the running card's brief.

## 3. Create the card

Same command and conventions as `handoff-ticket` §3. What differs:

```bash
orca worktree ps                     # a card already on this task? report it, do not add another
orca worktree create \
  --repo name:<repo> \
  --name "<target>-<topic>" \
  --parent-worktree active \
  --agent claude \
  --prompt "<note path> + 3-line summary, or the inline brief>" \
  --comment "<note path>" \
  --json
# name the card by what it does, not Orca's automatic title (the tab title is the agent's; a rename does not stick)
orca worktree set --worktree "path:<result.worktree.path>" --display-name "<ID or target> <short title>" --json
# the setup terminal is the card's row without agentIdentity; close it once setup exits
orca terminal list --worktree "path:<result.worktree.path>" --json
orca terminal wait --terminal <setup handle> --for exit --timeout-ms 1800000 && orca terminal close --terminal <setup handle>
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
return to what this session was doing. Results are fire-and-forget by default: do not loop on
`tui-idle`, do not block repo A on repo B.

Only when the user says "결과 받아와": tell the target session in the brief to append its result
to the brief note, and read the note when this session actually needs it.
