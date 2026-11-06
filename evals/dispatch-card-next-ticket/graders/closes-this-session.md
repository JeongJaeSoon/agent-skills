---
type: llm
weight: 1
---

Pass when the response treats the request as the next-ticket flow: it checks whether this ticket is done and looks up the next ticket in the tracker, and then either starts that ticket on a fresh card and closes this session (end-session) only once the card has started, or, when no ticket is next or the remaining item is a small defect in what this session just shipped, says so and handles it here as that flow allows. When it cannot look the ticket up (no tracker or shell reachable), refusing to guess a ticket number, creating no card and asking for the ticket or project also passes. Carrying this out and laying it out as the plan both pass. Fail when it starts a successor card and keeps this session working, guesses a ticket number, or treats the request as sending side work to another repo while staying open.
