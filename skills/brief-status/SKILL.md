---
name: brief-status
description: "Use when the human asks where the work stands — \"현황 보고해줘\", \"상황 정리해줘\", \"브리핑해줘\", \"진행 상황 알려줘\", \"status update\", or a message that is just `/brief` (a channel bot command) — and when a turn for the human ends with a report spanning several items (finished work, open work, decisions to take). Not for reports whose shape is fixed by a contract: a subagent's result, an Orca worker_done body, JSON or a script's output. Picks the layout by channel: plain short lines with no markdown when the human writes from Telegram, markdown tables in the terminal. \"어디까지 했지\" (reconstructing past sessions) is recall; \"남은 작업 있어?\" (what runs next) is handoff-ticket; a program's numbers come from orchestrate's `orch status`."
---

# Brief status

A status report has one job: the human reads it in under a minute and knows what is done, what is moving, and what is waiting on them. This skill fixes its sections and picks its layout from the channel the human is reading.

Gather first, then format. The facts come from where they live: `orch status` and the program note inside an `orchestrate` program, `recall` for past sessions, `gh pr view` and CI for PRs. This skill only decides what goes in and how it looks.

## 1. Sections

In this order, each left out when empty:

1. **Done** — each item with its evidence: merge sha, check result, test count, or the PR link. An item with no real output behind it is not done; it goes under In progress with what is missing.
2. **In progress** — what is moving, who or what moves it, and the next event (CI running, review requested, worker on step N).
3. **Decision needed** — numbered across the whole report. Each item: the question in one line, the options, and one recommendation with its reason. Only decisions outside the approved scope reach the human (`orchestrate` "What reaches the human"); decide the rest and report them under Done. Inside a program, a decision registered with `orch decide add` keeps its id beside the number (`1. (d3) …`), so an answer by either closes it.
4. **Only you** — what the human alone can do: a permission dialog, a login, a payment, an approval a ruleset requires from a person. Say where and what to press or run.

No preamble and no closing offer. If nothing is left for the human, say so in one line; never invent an item.

## 2. Channel

A message that is only `/brief` is a request for this report (a bot command the Telegram plugin does not handle reaches the session as plain text; registering it in the bot's menu is in §3).

The human's last message decides it. It came in as a Telegram `<channel source="plugin:telegram:...">` tag → Telegram layout, sent with the Telegram reply tool. Anything else → terminal layout.

### Telegram

The reply tool sends plain text by default, so every markdown mark shows as typed: `**`, backticks, `#`, `|` tables, `[text](url)`. Its `markdownv2` format fails the whole send on one unescaped character. So:

- No markdown syntax at all. Emphasis comes only from the emoji leads.
- One emoji lead per section: ✅ Done, 🔄 In progress, ❓ Decision needed, 🙋 Only you.
- One line per item, two at most. Links as bare URLs. PRs as `owner/repo#123` plus the URL when the human will open it.
- Decision items numbered `1.` `2.` … so the human can answer "1, 3 진행" or "2번은 B". Read such a reply against the numbers in this report.
- The reply tool splits text over 4096 characters by itself, by default at the character count, mid-line. Keep sections short enough that one report stays in one message; a report that cannot goes out as one reply per section. (A `chunkMode` of `newline` in the Telegram access settings makes it split at paragraph breaks instead; that setting is the human's.) A table the human truly needs (a long comparison) goes as an attached image or file, with its one-line conclusion in the text.

An illustrative shape (the items are made up):

```text
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

- Markdown tables where items share columns (item, state, evidence).
- `file:line` for code, `owner/repo#123` for PRs and issues, so both are clickable.
- The same four sections and the same numbering of decisions.

Write the text per `write-plainly`, in the language the human writes in. A skill that delegates its report here keeps its own required items and language (`end-session` §6); this skill adds only the sections' order and the layout.

## 3. The `/brief` menu entry (once per bot)

Only the session that holds the bot token registers it, after the human approves: it changes the bot, and a card never gets the token. Check first with `getMyCommands` for the human's chat; if `brief` is there, stop. Otherwise call `setMyCommands` scoped to that chat (`{"type": "chat", "chat_id": …}`): the plugin resets the all-private-chats list on every start, and the narrower scope wins. A chat-scoped list replaces that chat's whole menu, so register `brief` together with the plugin's `start`, `help` and `status`. Read the token from the channel's `.env` into the environment of the one command that calls the API; never print it or put it in a file.
