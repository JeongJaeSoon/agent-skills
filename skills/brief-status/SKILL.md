---
name: brief-status
description: "Use when reporting where work stands to the human — \"현황 보고해줘\", \"어디까지 됐어\", \"상황 정리해줘\", \"브리핑해줘\", \"뭐가 남았어\", \"status update\" — and whenever a turn ends with a report that covers several items (finished work, open work, decisions to take). Picks the layout by channel: short lines with no tables when the human writes from Telegram, markdown tables in the terminal. Reconstructing past sessions is recall; a program's numbers come from orchestrate's `orch status`."
---

# Brief status

A status report has one job: the human reads it in under a minute and knows what is done, what is moving, and what is waiting on them. This skill fixes its sections and picks its layout from the channel the human is reading.

Gather first, then format. The facts come from where they live: `orch status` and the program note inside an `orchestrate` program, `recall` for past sessions, `gh pr view` and CI for PRs. This skill only decides what goes in and how it looks.

## 1. Sections

In this order, each left out when empty:

1. **Done** — each item with its evidence: merge sha, check result, test count, or the PR link. An item with no real output behind it is not done; it goes under In progress with what is missing.
2. **In progress** — what is moving, who or what moves it, and the next event (CI running, review requested, worker on step N).
3. **Decision needed** — numbered across the whole report. Each item: the question in one line, the options, and one recommendation with its reason. Only decisions outside the approved scope reach the human (`orchestrate` "What reaches the human"); decide the rest and report them under Done.
4. **Only you** — what the human alone can do: a permission dialog, a login, a payment, an approval a ruleset requires from a person. Say where and what to press or run.

No preamble and no closing offer. If nothing is left for the human, say so in one line; never invent an item.

## 2. Channel

The human's last message decides it. It came in as a Telegram `<channel source="plugin:telegram:...">` tag → Telegram layout, sent with the Telegram reply tool. Anything else → terminal layout.

### Telegram

Telegram shows markdown tables as raw pipes, so:

- No markdown tables, no code blocks wider than a phone screen, no headings beyond a bold or emoji lead.
- One emoji lead per section: ✅ Done, 🔄 In progress, ❓ Decision needed, 🙋 Only you.
- One line per item, two at most. Links as bare URLs.
- Decision items numbered `1.` `2.` … so the human can answer "1, 3 진행" or "2번은 B". Read such a reply against the numbers in this report.
- A message over 4096 characters splits at a section boundary, never mid-item. A table the human truly needs (a long comparison) goes as an attached image or file, with its one-line conclusion in the text.

An illustrative shape (the items are made up):

```text
✅ 완료
- PR #42 머지 (a1b2c3d, CI 통과 38/38)

🔄 진행 중
- PR #43 리뷰 대기, CI 실행 중

❓ 결정 필요
1. 캐시 TTL: 5분 / 1시간 → 추천 1시간 (조회 대부분이 같은 세션 안)

🙋 사람만
- GitHub에서 배포 승인 버튼
```

### Terminal

- Markdown tables where items share columns (item, state, evidence).
- `file:line` for code, `owner/repo#123` for PRs and issues, so both are clickable.
- The same four sections and the same numbering of decisions.

Write the text per `write-plainly`, in the human's language.
