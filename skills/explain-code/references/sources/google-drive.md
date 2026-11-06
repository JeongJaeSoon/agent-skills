# Google Drive Docs

## What this source contains

- Design docs, RFCs, and PRDs written in Google Docs
- Slide decks from design reviews and planning
- Spreadsheets behind a number (capacity plans, pricing tables, thresholds)
- Meeting notes and postmortems kept outside Notion

## How to search it

Use the Google Drive MCP.

1. **Keyword searches with `search_files`.** The feature name, key symbols, error strings, the PR author's name, and ticket IDs. Narrow by modified date when you know when the code shipped.
2. **Read candidates in full with `read_file_content`.** Rationale is often mid-document or in an appendix. Use `download_file_content` for formats it can't read.
3. **Check `get_file_metadata`** for owner, created and modified dates, so you can place the doc before or after the code.
4. **Follow links inside the doc** to other Drive files. Record links to other sources (tickets, Slack threads, PRs) under Additional Leads.

## Common pitfalls

- **Stale drafts.** Several copies of the same doc often exist ("v2", "Copy of"). Prefer the latest finalized one and say which you used.
- **Comments are not in the text.** Review comments where the decision was argued may not come back from `read_file_content`. Note that as a gap when it matters.
- **Access-restricted files.** Note them as gaps rather than guessing at their content.

## What to return

For each relevant file: title, link, owner, created and modified dates, the motivation text quoted verbatim with its section, and whether it reads as a draft or final.
