---
name: brief
description: "Use when the human asks where the work stands — \"현황 보고해줘\", \"상태 보고해줘\", \"브리핑해줘\", \"진행 상황 알려줘\", \"진행상황 표로\", \"status update\", `/brief` — or a turn for the human ends with a report spanning several items. \"어디까지 했지\" is recall; \"남은 작업 있어?\" is handoff-ticket."
---

# Brief

A status report has one job: the human reads it in under a minute and knows what is done, what is moving, and what is waiting on them. Its shape is fixed in code, not in memory: one renderer prints every report, and you add only judgment.

## 1. The report is the renderer's output

```bash
orch brief render [--telegram] --headline "<one sentence>" [--decision "…"]... [--insight "…"] --commit             # on request
orch brief render --periodic [--telegram] --headline "<one sentence>" [--skipped "…"] [--decision "…"]... --commit  # on a schedule
```

Send what it prints, whole and unchanged. Add `--telegram` when §2 says Telegram. A periodic report is one sent on a schedule: a heartbeat, a cron or `/loop` round, a "report every N hours" the human set. Every other report is on request.

The renderer owns the shape. It prints the headline time from the system clock in the session's time zone, the legend, one table per repository or program with each row's PR state and `██████░░ 6/8` bar, then 진행 중, 결정 필요, 지금 오너가 할 일 (always, `없음` when empty) and 보류. On request, the stage table (작업 / feature·대표 PR / PR 머지 / dev 확인 / prod 확인) goes first; a periodic report adds the actions the round skipped. It builds the rows each time from what is live: the Orca cards and leads and their Runs, the last milestone in each card's report file, and the PR state on GitHub. It collects these again first when the saved copy is older than the dashboard config's `board.state_min` (5 minutes by default), and when it cannot, the headline says how old the copy it used is. The board adds only what those cannot give. A ✅ row with its merge sha is the Done item; there is no separate Done list.

You give it four things:

- `--headline`: the conclusion the human most needs, one sentence, with no time. The renderer puts the time before it and keeps its own facts after it (a stale PR list, old board rows, a failed collect).
- `--decision`, once per item: `<question>: <options> → 추천: <X> — <reason> · 답이 없으면 <default>`. The render refuses an item without `→ 추천:`. Numbers follow the decisions registered with `orch decide add`; register one that will stay open past this report, so it keeps its id and `(지난 보고와 동일)`.
- `--insight`: a block the human's settings or another skill add to the reply (an output style's insight box, a summary). It goes after 진행 중, so the report still ends on what the human acts on.
- `--skipped` (periodic): one line naming the periodic actions this round did not take and why (`21:00 이후라 Slack 미전송 · 머지 가능 PR 없음`).

What goes where:

- **Decision needed** is only a choice outside the scope the human approved: their budget, a permission they hold, another team's scope, or an irreversible or outward act the approval does not cover (`orchestrate` "What reaches the human"). Inside that scope, take the recommended default, act, and let the row show it.
- **Only you** is an action only a person can perform: a permission dialog, a login, a payment, an approval a ruleset requires. Record it with `orch board todo "<where and what to press or run>"...`, which replaces the whole list. One topic goes under Decision needed or Only you, not both.
- **Held** is work set aside on purpose that nobody is moving now, with the condition that brings it back: `orch board later add <work> --trigger <condition>`. Work waiting on a reply, under investigation or being fixed is moving: `orch board doing add <title> --owner W --next E`.

A wrong or missing row is fixed in the record, then rendered again: `orch board set` for a phase split, progress or a correction (`--progress N/M` is stages passed out of the item's stages, or the epic's exit items; no basis, no number), and `orch stage set <row> --col dev --mark ok --evidence v1.8.0` for each stage cell when you verify it. Never edit the output: the next render would undo the edit. `--commit` saves what you sent as the last report, so the next one bolds what changed; render with it only for the copy you send. The render exits 1 when a table is wider than 78 columns: send it as printed, then shorten the title in the record.

Build a report by hand only in two cases, to the shape in `references/hand-format.md`:

- The render failed: a traceback, an error exit other than the width check, or no output. The headline says `렌더러 실패(<reason>)`, and the time comes from `date`.
- The report is one session's account of its own work, not of several cards or leads (a card reporting its ticket, `end-session` §6). That one is short and is the session's own.

Call each PR, branch or worker by a name the human already knows. No preamble and no closing offer, and never an invented item. Write your own words per `write-plainly`, in the language the human writes in. A skill that delegates its report here keeps its own required items and language (`end-session` §6).

## 2. Channel

A message that is only `/brief` is a request for this report: a bot command the Telegram plugin does not handle reaches the session as plain text (registering it in the bot's menu is in §3). In Claude Code the user types `/agent-skills:brief`; a bare `/brief` there is a built-in command and never reaches this skill.

The human's last message decides the channel. It came in as a Telegram `<channel source="plugin:telegram:...">` tag → `--telegram`, sent with the Telegram reply tool in its default plain-text format (its `markdownv2` format fails the whole send on one unescaped character). The tool splits text over 4096 characters mid-line; a report longer than that goes out as one reply per section. Anything else → the terminal form, as printed.

## 3. The `/brief` menu entry (once per bot)

Only the session that holds the bot token registers it, after the human approves: it changes the bot, and a card never gets the token. Check first with `getMyCommands` for the human's chat; if `brief` is there, stop. Otherwise call `setMyCommands` scoped to that chat (`{"type": "chat", "chat_id": …}`): the plugin resets the all-private-chats list on every start, and the narrower scope wins. A chat-scoped list replaces that chat's whole menu, so register `brief` together with the plugin's `start`, `help` and `status`. The token is in the `.env` of the channel's state directory (`$TELEGRAM_STATE_DIR` when the session set it, else the plugin's default). Read it into the environment of the one command that calls the API and pass the URL to `curl -K -` on standard input, so it is not in argv; never print it or put it in a file.
