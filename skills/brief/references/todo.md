# To-do mode

```bash
orch board todo list [--telegram]
```

Send what it prints, whole and unchanged, even when a wide table comes out as one line per row: `--telegram` when the human wrote over Telegram. It prints two tables: the to-dos, promises kept on a date or a condition, and the held queue, work set aside with no date, in its order.

Change the record, then list again; never edit the output. A to-do is `orch board todo add <work> (--due <date> | --after <condition>) --who <who>`, an `--after` that names PRs coming once every one of them has merged; `orch board done <id>` closes it (`orch board open` lists the ids), and `orch board todo from-later <n> --who <who>` turns held item n into one. A held item is `orch board later add <work> --trigger <what makes it next> [--who W] [--at N]` (the end by default), moved with `orch board later move <n> --to N` (n and N are the numbers the list shows) and closed with `orch board done <id>`.
