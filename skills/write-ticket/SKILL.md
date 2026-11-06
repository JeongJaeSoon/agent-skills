---
name: write-ticket
description: "Use when asked to write or file a ticket or issue (\"티켓 만들어줘\", \"티켓 기표해줘\", \"이슈로 남겨줘\"), including a ticket request inside another question and follow-ups filed from a finding."
---

# Writing a Ticket

Turn a one-line request into a ticket that both this user and a coding agent can act on:
repo-verified implementation hints, checkable acceptance criteria, explicit out-of-scope.
Every tracker operation here ("file a ticket", "read a template", "search the tracker",
"relate") goes through `use-tracker`, whichever tracker is active.

## 0. Where the request came from

- **A ticket request inside another question** ("…왜 이래? 그리고 이거 티켓 만들어줘"). Split
  it out and file it first, then answer the question. The ticket must not wait on the answer,
  and the answer must not swallow the ticket.
- **Improvement feedback on in-flight work** ("이거 더 낫게", "이 부분도 고쳐줘" about a
  running card or PR). It becomes its own ticket. Never append it to the running task's scope,
  brief or PR.
- **A finding made while working** (review, E2E, `handoff-ticket` §0). Classify it first
  (`use-tracker` [references/exit-condition.md](../use-tracker/references/exit-condition.md)):
  only NOW becomes a follow-up ticket (§4).

## 1. Classify

| Type | Label | Skeleton |
|---|---|---|
| New behavior | `Feature` | 개발 티켓 |
| Something is broken | `Bug` | 개발 티켓 |
| Refactor, infra, cleanup | `Improvement` | 개발 티켓 |
| Find something out | `Spike` | 조사 티켓 |

A Spike's output is a conclusion, not code. Prefix its title with `[조사]`.

## 2. Read the skeleton

Where the skeleton lives depends on the config (`use-tracker` → `references/linear.md`,
"Config"):

- `tracker.<adapter>.templates` names the tracker templates (e.g. `{"dev": "개발 티켓",
  "spike": "조사 티켓"}`) → read that template from the tracker. **If the read fails, stop and
  say so.** Never write the ticket from a remembered skeleton; a stale copy is how two sources
  drift apart.
- No templates configured → [references/templates.md](references/templates.md) is the source.

## 3. Fill it against the repo

Explore the repo before writing `🛠 구현 힌트`. Every path and symbol in it must be one you
actually opened.

- `진입점` — the file the change starts in, with a line number where it helps
- `재사용` — **search for this first.** Existing helpers, utils, patterns, types, with paths.
  Not finding what already exists, and building it again, is the default failure mode of
  agent implementation. This line is the fix.
- `흐름` — the real call path, end to end
- `제약` — only what is specific to this ticket. General repo rules live in CLAUDE.md.
- `함정`, `예시` — when you know them: what has gone wrong here, an input with its expected output.

Write down as much as you verified: an agent that picks this up later has only the ticket. A
researcher's report counts as opened when you name it as the source. These lines apply to a
tracker template too, even when it has no slot for them.

**No repo context (invoked outside a repo)? Drop the whole `🛠 구현 힌트` section.** A guessed
file path is the most harmful thing this skill can produce — an agent will believe it.

Then delete sections that don't apply, strip the italic hint lines, and add the conditional
sections when they apply, before `## 🔗 참고`:

- `## ↩️ 롤백` — deploys, migrations, data changes. Feature flag or revert? Is the data recoverable?
- `## 🔒 보안` — auth, crypto, untrusted input. This ticket's specifics only.

Section headings carry an emoji. Keep the skeleton's headings exactly as they are, and match
that style on the conditional sections above.

## Writing rules

- **Title**: start with a verb, name the outcome. Not "대시보드 수정" but
  "대시보드 메트릭 로딩 실패 시 fallback 노출". The list view shows nothing else.
- **Language**: the site rules file (`use-tracker`, "Site rules"), else
  `tracker.<adapter>.language` in the config, sets the title and body language; without
  either, this skill sets none. Code identifiers, logs, error messages, and commands stay
  verbatim.
  Write the prose per `write-plainly`.
- **Acceptance criteria must be checkable**, each with how to check it. "잘 동작한다" has no
  check, so it isn't a criterion. Bug reports keep error text and stack traces unsummarized.
- **`🚫 범위 밖` is not optional.** Write "없음" rather than leaving it blank — it is the line
  that stops an agent from widening the work.

## Structure

The tracker's native fields carry structure; the body does not repeat it.

- **Dependencies are tracker relations** (blocks / blocked-by), never a prose list.
- **Dependent tickets that will both be PRs are planned as a GitHub stack**: the lower ticket's
  branch is the upper's base, so the chain lands in one merge (`deliver-ticket` §5,
  `orchestrate` `references/landing.md` "GitHub stacks"). Say it in the upper ticket's
  `🛠 구현 힌트` → `제약`: "base: <lower ticket> 브랜치 위에 stack".
- **One ticket is one PR.** Too big to review as a single PR → propose sub-issues (parent
  relation) now; splitting the PR later instead of the ticket is not an option.
- **A parent issue's body has no acceptance criteria and no implementation hints** — those
  belong to the children, and duplicating them guarantees they diverge. Keep
  `🎯 배경 & 목표`, `✅ 완료 조건` (numbered exit items, each checkable), `🚫 범위 밖` and an
  empty `⏸️ 보류` (`use-tracker` `references/exit-condition.md`). Write the exit items before the first
  child is filed; the children serve them.
- Clear outcome and a foreseeable end date → suggest a project. Until then a parent issue is
  enough. Only offer a project or milestone that already exists; don't create structure ahead
  of need.
- Search the tracker first so you do not file a duplicate.

## 4. File, then report

File without asking first: the request is the approval, and so is a NOW finding. Then report
each ticket's URL with a one-line summary (title, type, relations) and any assumption or scope
call you made that the request did not state, so a misread is caught on the ticket; a batch is
reported together. A NOW follow-up a session files on its own (a finding made while working a ticket,
`handoff-ticket` §0, a review or E2E run) is the same: its class, and whether two findings share
one ticket, is the session's call, and "shall I file this?" is not a question to put to the
user. When the user's own instructions say to show drafts first, show them first.

Ask first only for a large epic (a parent with its exit items and children planned at once) or
a ticket in another team's tracker or project. That ask goes through the
[owner-ask gate](../orchestrate/references/owner-ask-gate.md), whose step 1 often settles it;
what is left goes with the full draft and a recommendation, and while it waits its "if no
answer" holds.

A follow-up is marked as one so growth after the initial design can be counted
(`reflect`'s measure mode, `orchestrate`). Shape it exactly as `use-tracker`
`references/exit-condition.md` "Follow-up (derived) tickets" says: label, first body lines,
relation and parent; add blocked-by only when it truly blocks.

File each ticket through `use-tracker`: assignee me, the type label, the filled body. The
team and project come from the program or the originating ticket, else from
`tracker.<adapter>.team` / `project` in the config; with none set, that is a question for the
owner-ask gate. File the body only —
do not also apply the tracker template (on Linear a description replaces the template body, so
passing both silently discards your work). File parents before children, and the lower layer
of a dependency before the one it blocks.

Stop at the report — branch, implementation, PR and merge belong to `deliver-ticket`.
