# Stage table mode

```bash
orch stage show [--program P] [--telegram] [--all]
```

Send what it prints, whole and unchanged, even when a wide table comes out as one line per row: `--telegram` when the human wrote over Telegram, `--program` when they named one. It folds rows finished or unchanged for `board.stage_idle_d` days (7) and says how many; `--all` keeps them.

The dashboard generates most rows and cells from evidence and leaves a cell without evidence empty. When you verify a cell (a dev or prod check), record it, then show again: `orch stage set <row> --col dev --mark ok --evidence v1.8.0` (a generated row's id is `<program>/<item>`; your cell wins). Never edit the output. The record's rules are orchestrate's `references/stage-table.md`, "The stage table".
