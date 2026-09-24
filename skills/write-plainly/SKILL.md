---
name: write-plainly
description: "Use before writing a ticket, PR body, commit message, note, worklog, report to the user, code comment, or skill text, and whenever asked to fix prose that reads padded, vague, translated, or machine-written, such as \"문서 다듬어줘\", \"읽기 쉽게 고쳐줘\", \"AI 티 안 나게\", \"번역투 고쳐줘\", \"문장 다듬어줘\", \"unslop\", \"make this plain\", \"tighten this up\". Covers Korean and English. Product UI strings follow the product's own copy guidelines instead."
---

# Writing plainly

Write for a tired engineer who reads the text once. That reader may be the user, a reviewer, or an agent that acts on every word, so padding costs them time and a vague sentence sends them the wrong way.

Read the reference for the language before you draft. A cleanup pass over a finished draft misses most of these patterns.

- Korean prose (tickets, PR bodies, commit messages, notes, reports to the user): `references/korean.md`.
- English prose, code comments, and skill text: `references/english.md`.

Identifiers, commands, logs, and error text stay verbatim in either language.

## Principles

- **Cut every word that does no work.** If the sentence survives without a word, the word goes. "In order to" is "to", and "~하는 것이 중요합니다" is nothing.
- **The codebase is the word list.** Write the real symbol, file, flag, or command, not a synonym or a description of it. Call each thing by one name everywhere. A doc that says "the gate", "the check", and "the budget" for one thing teaches the reader three things.
- **Condition first.** Put the condition or warning before the step it guards, so the reader can skip what does not apply. "To delete the note, run `x`." "노트를 지우려면 `x`를 실행한다."
- **One instruction per sentence.** Everywhere else, one thought per sentence. Split an instruction longer than about 20 words and any other sentence the reader has to reread to parse.
- **Name the actor.** "The loader parses the file", not "the file is parsed". Use the passive only when the actor is unknown or beside the point.
- **Say what it does, not how it feels.** Name the mechanism or the number. Not "schema changes can cause issues" but "a column rename fails the build". If the sentence could appear unchanged in another project's docs, it says nothing about this one, so cut it.
- **Don't churn what didn't change.** When you revise, leave the sentences that were already right. A reworded sentence costs the reviewer a reread and carries no change.
- **Vary the rhythm, but don't over-compress.** Mix short sentences with longer ones that carry a fact together with its condition. Keep articles, particles, and verbs. Arrows, dropped verbs, and private abbreviations make the reader decode instead of read.

When a rule makes a sentence worse, fix the sentence another way or leave it alone. The rules serve the reader.

## Pick the document type first

One document has one type. Two questions pick it: does the reader need to act or to understand, and are they learning or working?

| | Learning | Working |
|---|---|---|
| Act | Tutorial | How-to |
| Understand | Explanation | Reference |

- **Tutorial.** Open with what the reader will build. Every step produces a visible result, and the text says what the reader should see. Background shrinks to one clause and a link.
- **How-to.** Solve a problem the reader has. Assume competence, give only the steps, and allow forks ("If you want x, do y."). Title it by the task.
- **Reference.** Describe and only describe: facts, options, limits, and errors, stated without hedging. Mirror the structure of the thing described so the reader can move between code and doc.
- **Explanation.** One bounded topic anchored on a real "why": design decisions, history, constraints, and alternatives. This is the only type where opinion belongs.

Don't mix types. A reference table inside a tutorial moves to its own page and gets a link. Tickets, PR bodies, and commit messages take their shape from the skill that writes them, and every principle above still applies. A PR body is a briefing a reviewer reads in under a minute, so link long logs instead of pasting them.

## Writing the reply

A report to the user is prose too, and it is read more often than anything else.

- Lead with the answer to what was asked. Evidence comes next, then what is left.
- Every claim carries its evidence or its label in the same sentence. The label is measured (say how), inferred (say from what), or a guess. A prediction or a cause you have not observed is a guess. Don't hand the user a check you could run yourself.
- Never fabricate a link, citation, or transcript reference. Link only what you produced or read in this session.
- "No" is an acceptable answer. When asked whether to do something, or shown an approach, give your real judgment, and decline or push back when that is what you think. Candor comes before agreement.
- Short sentences are not a reason to drop content. Tradeoffs, choices, and open decisions stay in.
