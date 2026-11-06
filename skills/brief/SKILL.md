---
name: brief
description: "Use when the human asks where work stands — \"현황 보고해줘\", \"상태 보고해줘\", \"브리핑해줘\", \"진행 상황 알려줘\", \"진행상황 표로\", `/brief` — or a turn ends with a report over several items. \"어디까지 했지\" is recall."
---

# Brief

A status report has one job: the human reads it in under a minute and knows what is done, what is moving, and what is waiting on them. Its shape is fixed in code, not in memory: one renderer prints every report, and you add only judgment.

## 1. The report is the renderer's output

```bash
orch brief render [--telegram] --headline "<one sentence>" [--decision "…"]... [--insight "…"] --commit             # on request
orch brief render --periodic [--telegram] --headline "<one sentence>" [--skipped "…"] [--decision "…"]... --commit  # on a schedule
```

Send what it prints, whole and unchanged. Add `--telegram` when §2 says Telegram. A periodic report is one sent on a schedule: a heartbeat, a cron or `/loop` round, a "report every N hours" the human set. Send one only on a schedule the human set, never one you started yourself. Every other report is on request.

Priority groups are the owner's order for the work, and once any row has one, the report opens with a `## 🧭 우선순위` section, one line per group. `orch board set <row> --priority RANK:NAME` puts a row, and the rows under it, in a group (`-` clears it); a lead or card only the fleet derives gets one with `orch board priority set <worktree:ID|program:NAME|repo:OWNER/NAME> RANK:NAME` (`priority list` and `priority drop` show and remove rules). Names and ranks are data the owner gives; a rule or example in this repository never carries a real one. When a card changes, the lead that owns that work sets its `--priority` on the board, so the group follows the work and not the card.

The renderer (`board.py`, `rowstate.py`) owns the layout, the order, each row's status and what folds; it builds the rows each time from the live Orca cards, leads, Runs, report files and GitHub PR state, and the board adds only what those cannot give. The stage table and the to-do and held lists appear only as counted pointer lines (the `stage` and `todo` skills print them). What is over but never turns ✅ by itself the collector closes on evidence, or marks ❔ 확인 필요 with the reason; close the rest with `orch board done <id>` (`orch board open` lists the ids; orchestrate's `references/stage-table.md`, "Closing what is over").

You give it four things:

- `--headline`: the conclusion the human most needs, one sentence, with no time. The renderer puts the time before it and keeps its own facts after it (a stale PR list, old board rows, a failed collect). Three of those facts ask for an act:
  - `보고서에 없는 세션 N (names)`: working or waiting sessions with a parent Orca recorded that no row (a row of one of its subagents counts), board lead, doing item or close stands for. Find why the render missed them; do not write them onto the board. `누락 검사 안 함: <reason>` means the session list failed or was old, so nothing was counted.
  - `오래된 결정 N (ids)`: open decisions nobody confirmed for `board.decision_stale_h` hours (12), counted from the later of `created_at` and `checked_at`. Check each: `orch decide keep <id>` if it still stands, `orch decide drop <id>` if not. Nothing closes one by itself.
  - `응답 경로 없음 N (ids)`: open decisions whose answer reaches no one: the terminal the relay would type into (the registering one, else the root's) is gone or has no running, writable agent. Drop it and register it again from the terminal that now waits on it.
  - `등록 터미널 없음 N (ids · 답은 오케스트레이터로)`: the registering terminal is gone, so the answer goes to the orchestrator's terminal instead. Relay it to whoever waits on it, or drop and register it again there.
- `--decision`, once per item, only after the [owner-ask gate](../orchestrate/references/owner-ask-gate.md): `<question>: <options> → 추천: <X> — <reason> · 답이 없으면 <X | 진행 못 함> · 근거: <owner-asked | hard-after-review | owner-hands-only>`. The render refuses an item that lacks a part and names it. It registers each item on the spot, so the item shows with its id, keeps it across reports while the words stay the same, and closes with `orch decide answer <id>`; one answered in the last 24 hours is not asked again. If registering fails the report still prints, with a warning line above it and the item marked `(미등록)`, and the command exits 1: send the report and fix the store.
- `--insight`: a block the human's settings or another skill add to the reply (an output style's insight box, a summary). It goes after 보류, under everything the human acts on.
- `--skipped` (periodic): one line naming the periodic actions this round did not take and why (`21:00 이후라 Slack 미전송 · 머지 가능 PR 없음`).

What goes where:

- **Decision needed** is only a choice outside the scope the human approved that passes the [owner-ask gate](../orchestrate/references/owner-ask-gate.md) (`orchestrate` "What reaches the human"). Inside that scope, take the recommended default, act, and let the row show it.
- **Only you** (🙋 지금 오너가 할 일) is what only the owner can unblock, once the same gate's first step found no agent route: a permission dialog, a login, a payment, an approval or merge a ruleset gives to them, a review they were asked for, an answer only they have. One item per line: what, where, `→` the work it unblocks. Record what no row carries with `orch board now "<what> · <where> → <work>"...`, which replaces the whole list. One topic goes under Decision needed or Only you, not both.
- **Waiting on others** (⏳ 다른 사람 대기) is work stopped on another person's review, approval or reply. One item per line: who, what, since when, `→` the work, and the next step (when to remind). With a send-log configured, the renderer adds the last request or reminder that went out for that PR and person: `· 마지막 리마인드 rev-a 3h 전 (<channel> 스레드)`. The log is the one `"brief": {"sendlog": "<path>"}` in `~/.claude/agent-skills.json` names, in `watch-prs`'s send-log format; there is no default, and without the key or the file the item has no such part.
- Which status a row is, the renderer decides from evidence (`rowstate.py`) and you only record it. A ❔ row's 다음 cell says `에이전트 조치 필요: <what is missing>`: record that fact. A ready PR the agent merges (its program's merge policy, else a repository pattern in `board.agent_merges` of `~/.claude/agent-skills.json`, else a repository the owner owns) is 🔄 with the merge as its next step. Record what evidence cannot give: `orch board set <id> --owner 오너|<who>|-` for who a row waits on, `--after owner/repo#N|-` for a PR it must wait for (`-` clears either). Set them rather than guess.
- **To-do** (a promise kept on a date or a condition, by someone named) and **Held** (work set aside with no date, in turn) are recorded and listed with the `todo` skill; the report only counts them, and moves a to-do whose time came into Only you or 카드 없는 일 until `orch board done` closes it.
- **Doing** is work with no row of its own: `orch board doing add <title> --owner W --next E`, or `--owner <who> --next <remind when> --waiting` when it waits on another person's reply (`--owner 오너` for the owner's). Work a session runs (a card, a worker or a subagent) is never a doing item: the report already shows it as that session's row.

A wrong or missing row is fixed in the record, then rendered again: `orch board set` for a phase split, progress or a correction (`--progress N/M` is stages passed out of the item's stages, or the epic's exit items; no basis, no number). Never edit the output: the next render would undo the edit. `--commit` saves what you sent as the last report, so the next one bolds what changed; render with it only for the copy you send. A table wider than 78 columns comes out as one line per row: send that as printed too.

Build a report by hand only in two cases, to the shape in `references/hand-format.md`:

- The render failed: a traceback, or an error exit with no report printed. The headline says `렌더러 실패(<reason>)`, and the time comes from `date`.
- The report is one session's account of its own work, not of several cards or leads (a card reporting its ticket, `end-session` §6). That one is short and is the session's own.

Call each PR, branch or worker by a name the human already knows. No preamble and no closing offer, and never an invented item. Write your own words per `write-plainly`. A skill that delegates its report here keeps its own required items (`end-session` §6).

## 2. Channel

A message that is only `/brief` is a request for this report: a bot command the Telegram plugin does not handle reaches the session as plain text (registering it in the bot's menu is in §3). In Claude Code the user types `/agent-skills:brief`; a bare `/brief` there is a built-in command and never reaches this skill.

The human's last message decides the channel. It came in as a Telegram `<channel source="plugin:telegram:...">` tag → `--telegram`, sent with the Telegram reply tool in its default plain-text format (its `markdownv2` format fails the whole send on one unescaped character). The tool splits text over 4096 characters mid-line; a report longer than that goes out as one reply per section. Anything else → the terminal form, as printed.

## 3. The `/brief` menu entry (once per bot)

Only the session that holds the bot token registers it, after the human approves: it changes the bot, and a card never gets the token. The API calls are in `references/telegram-menu.md`. The token is in the `.env` of the channel's state directory (`$TELEGRAM_STATE_DIR` when the session set it, else the plugin's default). Read it into the environment of the one command that calls the API and pass the URL to `curl -K -` on standard input, so it is not in argv; never print it or put it in a file.
