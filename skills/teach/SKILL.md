---
name: teach
description: "Explain a body of work plainly so a person actually understands it, weaving what the how and why skills find into one account at the person's pace. Use for 'teach me this', \"이거 제대로 이해하고 싶어\", \"이 변경 이해시켜줘\", \"이 구조 설명 좀 해줘, 처음 봐\", or explaining a change or subsystem to someone new to it. A single how-it-works question is how; a single why question is why."
---

# Teach

**You explain what a thing is, how it works, and why it's built that way, in one plain account at the person's pace. The goal is that they understand it, not that you change anything.**

Teach sits on top of `how` and `why`. Get your bearings on what the work is and what it touches, then run `how` for how it works and `why` for why it's that way. Those are real skill invocations that do their own digging. Blend what they find into one plain explanation, lead with what matters to the person, and go deeper when they ask. Reword freely for teaching, with one exception: keep `why`'s confidence language intact, since its hedges are findings, not style.

1. Decide the few things they should walk away understanding. Choose them from why they're asking (about to change it, reviewing it, debugging it, new to it) and what they already know, both read from the conversation, not quizzed out of them. Skip what they plainly know. Put the depth where their question is.
2. Let `how` and `why` do the work. Read the code yourself to get oriented, then run them in parallel and combine the results. Match the size to the question: both for a subsystem, maybe one for a small change. Keep `why` narrow by default, since its full sweep is slow: put the narrowing in the ask itself (a scoped question, git plus a source or two), and widen it only when the reasons are the point.
3. Start with a plain definition. Name the thing and say what it is in general terms, the way a senior engineer would say it out loud, with its common name if it has one. Then tie it to the case in front of you ("in X, we use this to ...") and build from there: how it works, the deeper reasons, the edge cases. For each part, explain the problem it solves and how it actually works. Walk through what happens as the person does the thing (opens a long chat, scrolls up) when that makes it land. Listing functions and constants is reference, not teaching. Give the smallest complete answer first, a sentence or two, then stop. Add layers when they ask.
4. Keep it a conversation. Offer to go deeper or move on, and follow their lead. When you would pause, stop and let them respond. With no live human (a one-shot run), deliver it cleanly and put any offer to go deeper at the end.
5. Show, don't only tell. Open the diff or the code when that is the fastest way to land it. Draw when a picture lands faster than words: a mermaid diagram for a flow or structure whose labels carry the meaning, a small ASCII sketch for layout or before-and-after. For anything with three or more moving parts, draw a short series where each diagram redraws the last and adds one part, so the reader watches the system assemble. One all-at-once diagram, especially saved for the end, is a reference, not teaching. A single simple point needs no figure.

Write every response with `write-plainly`, in the user's language, the way you'd explain it to a colleague. Be tight, not terse: state the concrete mechanism, not a metaphor, a framing, or a preview of what is coming. This is the target density: "Virtualization runs in two parts, one for rendering and one for loading from disk. When an item scrolls out past the buffer, both its DOM node and its in-memory data are evicted." Give each concept one name and keep it. The words in these steps are directions to you, not labels to print.

**Reply:** the explanation itself, never a report about what you did. Lead with the main point, then the plain account of what it is, how it works, and why, and the threads worth chasing with `how` or `why`.
