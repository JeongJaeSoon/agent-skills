---
name: todo
description: "Use for \"할 일 보여줘\", \"보류 큐 보여줘\", \"to-do list\", `/agent-skills:todo`: the to-dos and held queue the brief only counts."
---

# To-do

```bash
orch board todo list [--telegram]
```

Send what it prints, whole and unchanged: `--telegram` when the human wrote over Telegram. It prints two tables. The to-dos are promises kept on a date or a condition, `⏰` once that has come (the brief then lists it under 지금 오너가 할 일 or 진행 중) and `📅` before, the come ones first, then by date. The held queue is work set aside with no date, in its order, numbered from 1 without closed items, the first marked `▶` as next and one whose trigger has been met marked `❔`. The captions count as the brief's line does: come to-dos and `❔` items apart. To fit 78 columns it cuts a long cell to `…[n]` and lists the whole text under the table; it exits 1 when a table still passes 78: send it as printed.

Change the record, then list again; never edit the output. A to-do is `orch board todo add <work> (--due <date> | --after <condition>) --who <who>`, an `--after` that names PRs coming once every one of them has merged; `orch board done do:<k>` closes it (`orch board open` lists the ids), and `orch board todo from-later <n> --who <who>` turns held item n into one. A held item is `orch board later add <work> --trigger <what makes it next> [--who W] [--at N]` (the end by default), moved with `orch board later move <n> --to N` (n and N are the numbers the list shows) and closed with `orch board done later:<k>`.
