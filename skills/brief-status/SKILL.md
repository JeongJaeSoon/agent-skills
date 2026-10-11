---
name: brief-status
description: "Use when the human asks where the work stands — \"현황 보고해줘\", \"상황 정리해줘\", \"브리핑해줘\", \"진행 상황 알려줘\", \"status update\", a message that is just `/brief` — including progress as a table (\"진행상황 표로\", spaced or not), detailed progress per lead or per item, and what the human has to check or do; and when a turn for the human ends with a report spanning several items. Not for reports whose shape a contract fixes: a subagent's result, an Orca worker_done body, JSON or a script's output. Layout by channel: plain lines on Telegram, markdown tables in the terminal. \"어디까지 했지\" is recall; \"남은 작업 있어?\" is handoff-ticket; a program's numbers come from orchestrate's `orch status`."
---

# Brief status

A status report has one job: the human reads it in under a minute and knows what is done, what is moving, and what is waiting on them. This skill fixes its sections and picks its layout from the channel the human is reading.

Gather first, then format. The facts come from where they live: `orch status` and the program note inside an `orchestrate` program, `recall` for past sessions, `gh pr view` and CI for PRs. This skill only decides what goes in and how it looks.

## 1. Sections

In this order, each left out when empty:

1. **Done** — each item with its evidence: merge sha, check result, test count, or the PR link. An item with no real output behind it is not done; it goes under In progress with what is missing.
2. **In progress** — what is moving, who or what moves it, and the next event (CI running, review requested, worker on step N).
3. **Decision needed** — numbered across the whole report. Each item: the question in one line, the options, and one recommendation with its reason, and what happens if the human does not answer. Only decisions outside the approved scope reach the human (`orchestrate` "What reaches the human"); decide the rest and report them under Done. Inside a program, a decision registered with `orch decide add` keeps its id beside the number (`1. (d3) …`), so an answer by either closes it.
4. **Only you** — what the human alone can do: a permission dialog, a login, a payment, an approval a ruleset requires from a person. Say where and what to press or run.
5. **Later** — work deliberately deferred, each item with the condition that brings it back (a merge, a date, a decision).

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
- Where `orch` is installed (orchestrate), the table is a record: write each cell when you verify it (`orch stage set <row> --col dev --mark ok --evidence v1.8.0`), then report the output of `orch stage show --md`, or `--telegram` for Telegram, instead of rebuilding the table. The merge cell comes from the PR list. Build the table by hand only when no rows are recorded.

When the human names the columns they want, save them to `~/.local/state/agent-skills/brief-format.json` and use them in place of the default columns above in the next reports, until they change them.

### Periodic report

A report sent on a schedule (a heartbeat, a cron or `/loop` round, a "report every N hours" the human set) keeps one shape every time, so the human can compare it with the last one:

1. One summary line, led by the time (`14:30 · …`); `변화 없음` when nothing changed since the last report.
2. One table per repository, or per group the human named, with one row per lead (per item when there are no leads). Every row goes out every time, changed or not. Always these four columns, headed `| | 리드 | 진행 | 다음 |`:
   - Status, one icon: 🟢 moving · 🟡 waiting on a person or something outside · 🔴 blocked · ✅ done.
   - Name, the lead or item.
   - Progress: an 8-cell bar and N/M from the Progress rule above (`▓▓▓▓░░░░ 4/8`; `—` when there is no basis; the legend says the bar is an estimate), then the evidence for what is done: a PR number, or a 7-character sha when there is no PR.
   - Next: the next stage and who moves it, in a few words.
3. Decision needed, as §1 describes it.
4. Only you, as §1 describes it: what to run, press or log in to, and where.
5. Later, each item with the condition that brings it back.

A report with nothing new still carries sections 3 to 5 when they hold items: an open decision is asked again, in the same words and with its `orch decide` id when it has one, until the human answers it.

A row that changed since the last report has its name in bold, and the legend says so. Bold is the only mark: a symbol beside the status icon reads as part of the status. After sending, save the table to `~/.local/state/agent-skills/brief-last/<scope>.json` (scope: the program slug, or `top-level`) for the next report to compare against. With no saved table, send it with no row in bold and say so in the summary line. On Telegram, which has no bold, each row is one line and a changed row has `(바뀜)` after its name.

Call each PR, branch or worker by a name the human already knows, not a label made up during the work. No preamble and no closing offer. If nothing is left for the human, say so in one line; never invent an item.

## 2. Channel

A message that is only `/brief` is a request for this report (a bot command the Telegram plugin does not handle reaches the session as plain text; registering it in the bot's menu is in §3).

The human's last message decides it. It came in as a Telegram `<channel source="plugin:telegram:...">` tag → Telegram layout, sent with the Telegram reply tool. Anything else → terminal layout.

### Telegram

The reply tool sends plain text by default, so every markdown mark shows as typed: `**`, backticks, `#`, `|` tables, `[text](url)`. Its `markdownv2` format fails the whole send on one unescaped character. So:

- No markdown syntax at all. Emphasis comes only from the emoji leads.
- One emoji lead per section: 📋 the stage table, ✅ Done, 🔄 In progress, ❓ Decision needed, 🙋 Only you, ⏸️ Later. Inside a stage line, 🔄 is the cell mark (being checked now).
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
- acme/web#43 리뷰 대기, CI 실행 중

❓ 결정 필요
1. 캐시 TTL: 5분 / 1시간 → 추천 1시간 (조회 대부분이 같은 세션 안)

🙋 사람만
- GitHub에서 배포 승인 버튼
```

### Terminal

- Markdown tables where items share columns (item, state, evidence). Per-item progress is one table: item, progress, done so far, left; or the stage table (§1) when the items ship through PRs and deploys.
- A table fits in 80 columns as Claude Code draws it, or it wraps and the columns break apart. Count Hangul and emoji as two columns each, and add three per column for borders and padding. In the periodic table the name takes at most 14 columns, the progress 20, the next step 28. Detail that does not fit goes in a one-line footnote under the table (`¹ …`), not in the cell. `python3 scripts/table_width.py --cols 80 < draft.md` (in this skill's directory) prints each table's width.
- Decision needed, Only you and Later each start with a bold lead and take one line per item: `**❓ 결정 필요** — 1. 질문: 선택지 → 추천 (이유) · 답이 없으면 …`, `**🙋 지금 사람만 할 일** — …`, `**⏸ 나중에** — 조건 → 할 일`.
- `file:line` for code, `owner/repo#123` for PRs and issues, so both are clickable.
- The same sections and the same numbering of decisions.

An illustrative periodic report (the items are made up):

```markdown
**14:30 · 검색 개선 머지, 나머지는 변화 없음**
🟢 진행 · 🟡 대기(사람·외부) · 🔴 막힘 · ✅ 완료 · **굵은 이름** 직전 보고 뒤 바뀜 · 진행 막대는 단계 기준 추정

### acme/web
| | 리드 | 진행 | 다음 |
|---|---|---|---|
| ✅ | **검색 개선** | ▓▓▓▓▓▓▓▓ 8/8 #42 | — |
| 🟢 | 알림 정리 | ▓▓▓▓░░░░ 4/8 #47 | dev 확인 · 에이전트 |
| 🟡 | 로그인 개선 | ▓▓▓▓▓▓░░ 6/8 #40 | prod 배포 승인 · 사람¹ |

### acme/ops
| | 리드 | 진행 | 다음 |
|---|---|---|---|
| 🔴 | 인덱스 재구축 | ▓▓░░░░░░ 2/8 | 권한 오류 · 결정 1 |

¹ 배포 창이 금요일뿐이라 다음 창은 10/16.

**❓ 결정 필요** — 1. 재색인 권한: 서비스 계정 / 개인 토큰 → 추천 서비스 계정 (만료 없음) · 답이 없으면 재색인 보류
**🙋 지금 사람만 할 일** — acme/web Actions의 deploy-prod 승인 버튼
**⏸ 나중에** — v1.8.0 prod 배포 → 캐시 TTL 다시 보기
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
