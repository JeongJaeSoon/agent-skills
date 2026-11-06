---
type: llm
weight: 1
---

Pass when the response says the card already exists but its start (or link or name) is not confirmed, so it looks at that card (reads its terminal screen, for example a trust or permission prompt) instead of creating the card again, and keeps this session open until the successor's start is confirmed. Fail when it runs the handoff again to create another card, or closes this session before the successor has visibly started.
