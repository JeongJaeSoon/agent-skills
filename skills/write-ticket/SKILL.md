---
name: write-ticket
description: "Use when asked to write, create, or file a ticket or issue (\"티켓 만들어줘\", \"티켓 기표해줘\", \"Linear 티켓으로 만들어줘\", \"티켓으로 남겨두고 종료하자\", \"이슈로 남겨줘\") — including a one-line ticket request mixed into another question, feedback on in-flight work that should become its own ticket, and follow-up tickets filed from a finding."
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
- **A finding made while working** (review, E2E, `handoff-ticket` §0). A follow-up (§4).

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
- **Body in Korean.** Code identifiers, logs, error messages, and commands stay verbatim.
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
  `🎯 배경 & 목표` + `🚫 범위 밖`.
- Clear outcome and a foreseeable end date → suggest a project. Until then a parent issue is
  enough. Only offer a project or milestone that already exists; don't create structure ahead
  of need.
- Search the tracker first so you do not file a duplicate.

## 4. Get approval, then file

Show the full draft in chat first. Several tickets? Show **all** of them — title, type,
relationships — and take **one** approval for the batch.

**Exceptions — file first, then name the ticket and its URL:**

- A ticket request split out of another question (§0). The user already asked for it.
- A follow-up a session files on its own — from a finding made while working a ticket, at
  `handoff-ticket` §0, or from a review or E2E run. The user is not necessarily there, and
  losing the finding is worse than filing it unreviewed. Whether to file, and whether two
  findings share one ticket, is the session's call (`deliver-ticket` §2 "Findings outside the
  ticket"); "shall I file this?" is not a question to put to the user.

A follow-up is marked as one so growth after the initial design can be counted
(`measure-delivery`, `orchestrate`). Use `use-tracker`'s follow-up format: the `follow-up`
label next to its type, first body line
`파생: <originating ticket> · 원인: <리뷰 지적 | 계약 불일치 | QA | 스펙 공백 | 구현 한계 | 기타>`,
and a related relation to the originating ticket (blocked-by when it truly blocks).

Then file each ticket through `use-tracker`: assignee me, the type label, the filled body. The
team and project come from the program or the originating ticket, else from
`tracker.<adapter>.team` / `project` in the config; if none is set, ask. File the body only —
do not also apply the tracker template (on Linear a description replaces the template body, so
passing both silently discards your work). File parents before children, and the lower layer
of a dependency before the one it blocks.

Print the ticket URLs. Stop there — branch, implementation, PR and merge belong to `deliver-ticket`.
