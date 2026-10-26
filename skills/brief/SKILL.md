---
name: brief
description: "Use when the human asks where the work stands — \"현황 보고해줘\", \"상태 보고해줘\", \"브리핑해줘\", \"진행 상황 알려줘\", \"진행상황 표로\", \"status update\", `/brief` — or a turn for the human ends with a report spanning several items. \"어디까지 했지\" is recall; \"남은 작업 있어?\" is handoff-ticket."
---

# Brief

A status report has one job: the human reads it in under a minute and knows what is done, what is moving, and what is waiting on them. This skill fixes its sections and picks its layout from the channel the human is reading.

Gather first, then format. The facts come from where they live: `orch status` and the program note inside an `orchestrate` program, `recall` for past sessions, `gh pr view` and CI for PRs. This skill only decides what goes in and how it looks.

## 1. Sections

Every report opens with a headline: bold, led by the time, one sentence with the conclusion the human most needs (`**14:30 · 검색 개선 머지, 남은 건 리뷰 1건**`). Then the sections in this order, each left out when empty except Only you:

1. **Done** — each item with its evidence: merge sha, check result, test count, or the PR link. An item with no real output behind it is not done; it goes under In progress with what is missing.
2. **In progress** — what is moving, who or what moves it, and the next event (CI running, review requested, worker on step N).
3. **Decision needed** — numbered across the whole report. Each item: the question in one line, the options, one recommendation with its reason, and what happens if the human does not answer. Only a choice outside the scope the human approved goes here: their budget, a permission they hold, another team's scope, or an irreversible or outward act the approval does not cover (`orchestrate` "What reaches the human"). Inside that scope, take the recommended default, act, and report it under Done: merging an approved PR, picking a reviewer, closing a stale draft, a setting or a design option. A decision is a choice; an action only a person can perform is Only you, and one topic goes in one of the two. Inside a program, a decision registered with `orch decide add` keeps its id beside the number (`1. (d3) …`), so an answer by either closes it.
4. **Only you** — what the human alone can do: a permission dialog, a login, a payment, an approval a ruleset requires from a person. Say where and what to press or run. Always there: with nothing for the human, `없음` and when the next one appears, so the human sees there is nothing for them.
5. **Held** — work set aside on purpose that nobody is moving now, each item with the condition that brings it back (a merge, a date, a decision). An item waiting on a reply, under investigation or being fixed is In progress, with who moves it and the next event.

A block the human's settings or another skill add to the reply (an output style's insight box, a summary) goes after In progress and before Decision needed, so the report ends on what the human acts on. In a periodic report sent from `orch brief render`, it goes after the printed output, never inside it.

When the report covers several work items (tickets, tracks, a program's predicate items), each In progress item carries three fields:

- **Progress** — an estimate that says so and names its basis: stages passed out of the item's stages (implement → PR → review → merge → dev deploy → dev check → prod deploy → prod check, trimmed to the stages the item has), or the epic's exit items N/M (`use-tracker`). Written as `4/8 단계 (추정 50%)`. No basis, no number.
- **Done so far** — only what was verified, with the evidence Done asks for.
- **Left** — the next stage, who moves it (an agent or a person), and when it is expected; "미정" when no time is fixed. A step that is the human's also goes under Only you.

When those items ship through PRs and deploys, the three fields take the form of a stage table, one row per item:

- Columns: 작업 / feature·대표 PR / PR 머지 / dev 확인 / prod 확인. The last three are checkpoints of the stage list above, so the row's cells are its Progress and the ✅ cells are its Done so far. Left becomes a `다음 순서` list under the table: next stage, owner, expected time.
- Every item gets a row, finished ones too: the human wants the whole set in one view. The table goes first, above Done; Done and In progress then hold only items without a row.
- A child item (a sub-ticket, a follow-up PR) goes on its own row under its parent, its name prefixed `ㄴ`. A PR in another repo than the parent's carries the repo name (`acme/ops#88`).
- Each cell is one mark and its evidence in a few words (a version, a time, a count): ✅ done and verified · ❌ not done or failed · ⚠️ partial, or inferred without a direct check · 🔄 being checked now, by whom · `해당 없음` when the item has no such stage. A cell with no evidence is not ✅.
- Above the table, two or three lines of the facts it reads against: the version on prod and on dev, and the legend. These are part of the report, not a preamble.
- Where `orch` is installed (orchestrate), the table is a record: write each cell when you verify it (`orch stage set <row> --col dev --mark ok --evidence v1.8.0`), then report the output of `orch stage show --md`, or `--telegram` for Telegram, instead of rebuilding the table. In the terminal, check its width as §2 Terminal says and move long evidence to a footnote. The merge cell comes from the PR list. Build the table by hand only when no rows are recorded.

When the human names the columns they want, save them to `~/.local/state/agent-skills/brief-format.json` and use them in place of the default columns above in the next reports, until they change them.

### Periodic report

A report sent on a schedule (a heartbeat, a cron or `/loop` round, a "report every N hours" the human set) keeps one shape every time, so the human can compare it with the last one.

Where `orch` is installed (orchestrate), the report is the output of `orch brief render --periodic --commit` (add `--telegram` for Telegram), sent as printed. The renderer builds items 1 to 9 below from the board, the fleet collector's sessions and PR list, the open decisions and the last sent table. A lead or card with no board row is derived from its session: its next step is the newest milestone in the report file its brief's REPORT line names, so do not copy a report onto the board. Write to the board (`orch board set`, `doing`, `later`) only what a session and its report cannot give: a phase split, progress, a correction. The renderer is the one implementation of these rules, and its test (`orchestrate/scripts/test_board.py`) holds a full example. Give it the headline sentence (`--headline`) and item 5 (`--skipped`); add a line of your own only to explain a decision. `--commit` saves the table as the last one, so render with it only for the copy you send. A row past its staleness limit shows its age and a PR list probed long ago is in the headline. A wrong row is fixed in the record (`orch board set`), not in the output, so the next render keeps it. Build the report by hand, to the shape below, only when the render prints no table:

1. The headline, bold and led by the time: `**14:30 · <what changed, one sentence>**`, or `**14:30 · 변화 없음 — <why, a few words>**`.
2. The legend, one line: `🔄 진행 · ⏳ 사람 대기 · ⏸️ 보류 · 막대=단계(추정)`, with `✅ 완료` and `🔴 막힘` added before `막대` when a row uses them.
3. One section per repository, headed by a bold line, `**owner/repo → main**`, ending in the merge target most of its PRs share; a repository running several programs gets one section per program, `**owner/repo — <program> → main**`. Record each lead with its project (epic, program) as `--program`; a lead without one beside another lead in the same repository is headed `(프로젝트 미지정)`. Each section has one table, headed `| | 리드/Phase | PR | 진행 |`, with one row per lead, or per phase when a lead works in phases (per item when there are no leads). Every row goes out every time, changed or not.
   - Status, one icon: 🔄 moving · ⏳ waiting on a person · ⏸️ held until something else lands · 🔴 blocked · ✅ done.
   - Name: the lead or phase. Phases waiting in the same state may share a row (`ph4 · ph5`).
   - PR: its PR as a link and the PR's state from the PR list, or `—` when it has none. Merged: the merge commit's first 7 characters (`[#12](https://…) a1b2c3d`); closed: `닫힘`; open: what stands before the merge, in this order — `초안`, `❗DIRTY`, `❗리뷰` (a required review missing), `수정요청`, `CI✗`, `CI…`, `BEHIND`, `BLOCKED` — or `✓` when it can merge (`열림` while GitHub has not computed it). `❗` marks what a person must resolve: bold is kept for changed rows. A PR whose base is not the section's adds its own (`[#50](https://…) ❗DIRTY→main`). Under the legend, one line `PR: ❗ 사람이 풀 것 · …` explains the marks the tables use.
   - Progress: an 8-cell bar and N/M from the Progress rule above, and nothing else (`██████░░ 6/8`, filled cells = round(8·N/M)). M is the item's own stage count, so it changes with the item (`3/4`); `—` when there is no basis.

   The next step is not a column: a row's next stage and who moves it go under item 6, or item 9 for a held row, so no cell is ever cut to fit.

   Under each lead or phase row, one row per sub-task it owns (each card, worker and open PR), named `└ <short name>` with its PR in the PR column, or `└ <identifier> <short name>` without one (`└ 설정`, `└ w2 발송`, `└ ABC-1 색인`), filling the same four columns. A card, its worker and the PR it opened are one sub-task, one row, named by the furthest of them: PR, then worker, then card. Several PRs never share a cell, except in the reviewer row of item 4. A merged sub-task stays, ✅, until its lead is done. A row with sub-task rows takes the worst of their statuses (🔴, ⏳, ⏸️, 🔄; ✅ only when the lead is done). A sub-task in another repository goes in that repository's section, under a row with only the lead's name and `—` in status and progress. With no leads there are no sub-task rows. The rows come from four reads at each report: the list of leads (`orch status` inside a program), each lead's latest report, one read per repository of the human's open PRs, and the worker list; a merged sub-task comes from the saved last table.
4. When two or more of a lead's PRs wait only on the same reviewer, they fold into one sub-task row under that lead, `└ <reviewer> 대기` with `#a·#b·#c` in its PR cell, a PR with another base keeping its `→main`, and the lead row stays. PRs with no lead fold into a bullet under the table, `⏳ N건 <reviewer> 대기 — 분야 a·b·c`. A PR returns to its own row when anything else happens to it.
5. After the last section, one line naming the periodic actions this round did not take and why (`21:00 이후라 Slack 미전송 · 머지 가능 PR 없음`). Left out when it took them all.
6. `🔄 **진행 중**`, a list under the lead line, one item per open row of the tables in their order, held rows aside: `<status> <name> — <next stage> · <who>`, a phase named with its lead (`검색 개선 ph3`), a sub-task as `└ …` after its head row (the head named alone when it is done). Then the moving work with no row of its own (a wait on another team's answer, an investigation; `orch board doing add <title> --owner W --next E`) in the same form. A done row has no item: its PR cell is the evidence.
7. `❓ **결정 필요**`, numbered as §1 says, each item one line: `1. <question> → 추천: X — <reason>`. An open decision is asked again in the same words, with `(지난 보고와 동일)` and its `orch decide` id when it has one, until the human answers it.
8. `🙋 **지금 오너가 할 일**`, one line: what to run, press or log in to, and where; or `없음` and when the next one appears.
9. `⏸️ **보류**`, a list under the lead line, one `<condition> → <work>` item each: the ⏸️ rows (their next stage is the condition), then the work set aside with `orch board later add <work> --trigger <condition>`. Held as §1 defines it. One piece of work appears once: adding an item with the same condition and a reworded title replaces the old one.

Items 6, 7 and 9 are left out when empty; item 8 is always there (§1 Only you).

A row that changed since the last report (status, progress, next or PR state), or that it did not have, has its name in bold (after the `└` on a sub-task row). Bold is the only mark: a symbol beside the status icon reads as part of the status. The last report is `~/.local/state/agent-skills/brief-last/<scope>.json` (scope: the program slug, or `top-level`), each row keyed so the next report can match it, with its status, progress, next and PR state; save it after sending. With no saved table, send it with no row in bold and say so in the headline. On Telegram, which has no bold, each row is one line with its PR and state after the name (`✅ ph2 #42 9f8e7d6 — …`), a sub-task line starts with `└`, and a changed row has `(바뀜)` after its name.

Call each PR, branch or worker by a name the human already knows, not a label made up during the work. No preamble and no closing offer, and never an invented item.

## 2. Channel

A message that is only `/brief` is a request for this report: a bot command the Telegram plugin does not handle reaches the session as plain text (registering it in the bot's menu is in §3). In Claude Code the user types `/agent-skills:brief`; a bare `/brief` there is a built-in command and never reaches this skill.

The human's last message decides it. It came in as a Telegram `<channel source="plugin:telegram:...">` tag → Telegram layout, sent with the Telegram reply tool. Anything else → terminal layout.

### Telegram

The reply tool sends plain text by default, so every markdown mark shows as typed: `**`, backticks, `#`, `|` tables, `[text](url)`. Its `markdownv2` format fails the whole send on one unescaped character. So:

- No markdown syntax at all. Emphasis comes only from the emoji leads.
- One emoji lead per section: 📋 the stage table, ✅ Done, 🔄 In progress, ❓ Decision needed, 🙋 Only you, ⏸️ Held. Inside a stage line, 🔄 is the cell mark (being checked now).
- One line per item, two at most; per-item progress as `<item> — 4/8 단계(추정 50%) · 완료: … · 남음: … (담당, 시각)`. A stage-table row becomes one line with the same four fields: `<item> (owner/repo#123) — 머지 ✅ … · dev ✅ … · prod 🔄 …`, a child line prefixed `ㄴ`, the versions on the 📋 lead line, `다음 순서` below. A chat post elsewhere that renders no tables takes this form too. Links as bare URLs. PRs as `owner/repo#123` plus the URL when the human will open it.
- Decision items numbered `1.` `2.` … so the human can answer "1, 3 진행" or "2번은 B". Read such a reply against the numbers in this report.
- The reply tool splits text over 4096 characters by itself, by default at the character count, mid-line. Keep sections short enough that one report stays in one message; a report that cannot goes out as one reply per section. (A `chunkMode` of `newline` in the Telegram access settings makes it split at paragraph breaks instead; that setting is the human's.) A table the human truly needs (a long comparison) goes as an attached image or file, with its one-line conclusion in the text.

An illustrative shape (the items are made up):

```text
📋 작업별 (prod v1.7.2 · dev v1.8.0)
- 로그인 개선 (acme/web#40) — 머지 ✅ 9/28 · dev ✅ v1.7.2 · prod ✅ 10/1
- 검색 개선 (acme/web#42) — 머지 ✅ 10/2 · dev ✅ v1.8.0 · prod 🔄 QA 리드 확인 중
ㄴ 인덱스 재구축 (acme/ops#88) — 머지 ✅ · dev ✅ 1,204건 · prod 해당 없음
다음 순서: v1.8.0 prod 배포 (사람, 미정)

✅ 완료
- acme/web#42 머지 (a1b2c3d, CI 통과 38/38)

🔄 진행 중
- acme/web#43 리뷰 대기, CI 실행 중 · 리뷰어 B · 다음: CI 결과

❓ 결정 필요
1. 검색 API 유료 플랜(월 $50, 승인 예산 밖): 올림 / 현행 유지 → 추천 현행 유지 (무료 한도의 60%만 씀)

🙋 사람만
- GitHub에서 배포 승인 버튼

⏸️ 보류
- 10/20 → 캐시 TTL 다시 보기
```

### Terminal

- Markdown tables where items share columns (item, state, evidence). In progress with several items is one table, `| 작업 | 상태 | 다음 |`: 상태 is what is done so far, with its evidence; 다음 is the next event and who moves it. A `진행` column (§1 Progress) goes after 작업 only when the items have a basis for one, and a child item is a row under its parent, named `ㄴ <name>` as in the stage table. Items that ship through PRs and deploys take the stage table (§1) instead; a periodic report has one table per repository or program (below).
- A table fits in 78 columns as Claude Code draws it (an 80-column terminal less the 2-column gutter in front of each reply line), or it wraps and the columns break apart. Count Hangul and emoji as two columns each, and add three per column for borders and padding. In the periodic table no cell is cut and nothing goes to a footnote: the next step is not a column (§1 items 6 and 9), and a table wider than 78 anyway overflows and the render reports it. In other tables, detail that does not fit goes in a one-line footnote under the table (`¹ …`), not in the cell. `python3 scripts/table_width.py < draft.md` (in this skill's directory) prints each table's width.
- Decision needed, Only you and Held, and In progress when it is not a table, each start with a bold lead and take one line per item, the first item on the lead line and each further item below it after a blank line: `❓ **결정 필요** — 1. 질문: 선택지 → 추천: X — 이유 · 답이 없으면 …`, `🙋 **지금 오너가 할 일** — …`, `🔄 **진행 중** — 할 일 · 담당 · 다음: 이벤트`, `⏸️ **보류** — 조건 → 할 일`; a periodic report takes the forms in §1 Periodic report, where 진행 중 and 보류 are lists under their lead line. Leave a blank line between lines that must stay separate; markdown joins adjacent lines.
- `file:line` for code, `owner/repo#123` for PRs and issues, so both are clickable.
- The same sections and the same numbering of decisions.

An illustrative report on request (the items are made up):

```markdown
**14:45 · 검색 개선 PR 생성, 유료 플랜 하나만 정해 주세요**

✅ **완료**

- [acme/web#42](https://example.test/42) 생성 (head a1b2c3d, 테스트 38/38 통과)

🔄 **진행 중**

| 작업 | 상태 | 다음 |
|---|---|---|
| 검색 개선 | #42 CI 실행 중 | CI 통과 → 리뷰 의뢰 · 에이전트 |
| ㄴ 정렬 | 워커가 구현 중 | PR 생성 · 워커 |
| 재색인 | 리뷰어 B 대기 | 승인 → 머지 · B |

❓ **결정 필요** — 1. 검색 API 유료 플랜(월 $50, 승인 예산 밖): 올림 / 현행 유지 → 추천: 현행 유지 — 무료 한도의 60%만 씀 · 답이 없으면 현행 유지

🙋 **지금 오너가 할 일** — GitHub acme/web Environments → production → 대기 중인 배포 승인 (규칙상 사람만)

⏸️ **보류** — 10/20 → 캐시 TTL 다시 보기
```

An illustrative periodic report (the items are made up):

```markdown
**23:45 · 검색 개선 ph2 머지, 나머지는 변화 없음**

🔄 진행 · ⏳ 사람 대기 · ⏸️ 보류 · ✅ 완료 · 막대=단계(추정)

PR: ❗ 사람이 풀 것 · CI✗ CI 실패 · CI… CI 진행 · 7자리=머지 sha

**acme/web — 검색 개선 → main**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| ✅ | ph1 | [#40](https://example.test/40) a1b2c3d | ████████ 8/8 |
| ✅ | **ph2** | [#42](https://example.test/42) 9f8e7d6 | ████████ 8/8 |
| 🔄 | ph3 | — | ███░░░░░ 3/8 |
| 🔄 | └ 정렬 | [#45](https://example.test/45) CI… | ████░░░░ 4/8 |
| 🔄 | └ **w2 색인** | — | ██░░░░░░ 2/8 |
| ⏸️ | ph4 · ph5 | — | ░░░░░░░░ 0/8 |

**acme/web — 알림 정리 → feature/notify**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| ⏳ | 알림 정리 | [#50](https://example.test/50) ❗DIRTY→main | ██████░░ 3/4 |
| ⏳ | └ A 대기 | [#51](https://example.test/51)·#52·#53 | ██████░░ 3/4 |
| 🔄 | └ 문구 | [#54](https://example.test/54) CI✗ | ████░░░░ 2/4 |

- ⏳ 2건 리뷰어 B 대기 — 분야 로그·대시보드

**acme/ops → main**

| | 리드/Phase | PR | 진행 |
|---|---|---|---|
| ⏳ | 재색인 | [#88](https://example.test/88) ❗리뷰 | ██░░░░░░ 2/8 |

21:00 이후라 Slack 미전송 · 머지 가능 PR 없음

🔄 **진행 중**

- 🔄 검색 개선 ph3 — 하위 2건 · 워커
- └ 🔄 정렬 — CI 대기 · 워커
- └ 🔄 w2 색인 — PR 작성 · 워커
- ⏳ 알림 정리 — 충돌 해소 · 사람
- └ ⏳ A 대기 — 10/13 09:30 의뢰
- └ 🔄 문구 — CI 재실행 · 워커
- ⏳ 재색인 — 권한 · 결정 1
- 🔄 다른 팀 API 답변 대기 — 10/14 회신 · 사람

❓ **결정 필요** — 1. 재색인 권한(오너가 가진 권한): 서비스 계정 발급 / 개인 토큰 → 추천: 서비스 계정 — 만료 없음 (지난 보고와 동일)

🙋 **지금 오너가 할 일** — 없음 · 10/13 09:30 리뷰 의뢰 뒤 승인 1건이 생김

⏸️ **보류**

- ph3 머지 뒤 → 검색 개선 ph4 · ph5
- 10/16 → prod 배포 창
- 10/20 → 캐시 TTL 다시 보기
```

An illustrative stage table (the items are made up):

```markdown
prod: v1.7.2 (10/1 배포) · dev: v1.8.0 (10/3 배포)
✅ 완료·확인 · ❌ 미완료·실패 · ⚠️ 일부·추정 · 🔄 확인 중 · 해당 없음

| 작업 | feature·대표 PR | PR 머지 | dev 확인 | prod 확인 |
|---|---|---|---|---|
| 로그인 개선 | acme/web#40 | ✅ 9/28 | ✅ v1.7.2 | ✅ 10/1 |
| 검색 개선 | acme/web#42 | ✅ 10/2 | ✅ v1.8.0 | 🔄 QA 확인 중 |
| ㄴ 재색인 | acme/ops#88 | ✅ 10/2 | ✅ 1,204건 | 해당 없음 |
| ㄴ 정렬 버그 | acme/web#45 | ❌ 리뷰 대기 | ❌ | ❌ |
| 알림 정리 | acme/web#47 | ✅ 10/3 | ⚠️ 로그만¹ | ❌ 다음 배포 |

¹ 알림 정리의 dev 확인은 로그로만 했다. 화면 확인은 아직.

다음 순서
1. acme/web#45 리뷰 → 머지 (에이전트, 오늘)
2. v1.8.0 prod 배포 (사람, 미정)
```

Write the text per `write-plainly`, in the language the human writes in. A skill that delegates its report here keeps its own required items and language (`end-session` §6); this skill adds only the sections' order and the layout.

## 3. The `/brief` menu entry (once per bot)

Only the session that holds the bot token registers it, after the human approves: it changes the bot, and a card never gets the token. Check first with `getMyCommands` for the human's chat; if `brief` is there, stop. Otherwise call `setMyCommands` scoped to that chat (`{"type": "chat", "chat_id": …}`): the plugin resets the all-private-chats list on every start, and the narrower scope wins. A chat-scoped list replaces that chat's whole menu, so register `brief` together with the plugin's `start`, `help` and `status`. The token is in the `.env` of the channel's state directory (`$TELEGRAM_STATE_DIR` when the session set it, else the plugin's default). Read it into the environment of the one command that calls the API and pass the URL to `curl -K -` on standard input, so it is not in argv; never print it or put it in a file.
