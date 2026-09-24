# Plain English

Patterns that make English prose read machine-written, with the fix for each. They apply to skill text, code comments, and any English prose (a subagent report, a Codex prompt, an English PR).

Rule numbers are stable ids that other text can cite. A removed rule leaves a gap.

## Content

3. **Superficial -ing phrases.** "highlighting...", "ensuring...", "reflecting...", "showcasing...", "fostering...". Delete them or expand them with real sources.
5. **Vague attributions.** "Experts believe", "Industry reports suggest", "Some critics argue". Name the source or delete the claim.

## Language

7. **AI vocabulary.** Additionally, crucial, delve, enduring, enhance, fostering, garner, interplay, intricate, landscape (abstract), pivotal, showcase, tapestry (abstract), testament, underscore, vibrant. Replace with plain words.
8. **Fancy ways to say "is".** "serves as", "stands as", "boasts", "features". Say "is" or "has".
9. **"Not just X, but Y."** State the point directly.
10. **Rule of three.** Forcing ideas into groups of three. Use the natural number.
11. **Synonym cycling.** Protagonist, main character, central figure, and hero all in one paragraph. Pick one name and repeat it.
12. **False ranges.** "from X to Y" where X and Y aren't on a meaningful scale. List the topics directly.

## Style

13. **Em dashes.** Avoid them entirely. Use periods or commas, not parentheses, en dashes, or hyphens standing in for a dash. If a thought needs separation, end the sentence or use a comma.
14. **Colon as a connector.** A colon is fine before a list or an example, not as a mid-sentence connector. "If you're coming from traditional automation: instead of registering event handlers, you describe conditions" gains nothing from the colon. Let the point stand without the comparison framing: "Describing when the scheduler should fire works best as plain English."
15. **Boldface overuse.** Don't bold every proper noun or acronym.
16. **Inline-header lists.** The tell is a bold label and colon that restates the line: "**Performance:** Performance improved...". Convert those to prose. A bold lead-in that ends in a period, names the item, and is followed by new detail ("**Schema in TypeScript.** Tables live in one file.") is fine.
17. **Title case headings.** Use sentence case.
18. **Decorative emoji.** Remove them from headings and bullets.
19. **Curly quotes.** Replace them with straight quotes.

## Communication artifacts

20. **Chatbot phrases.** "I hope this helps!", "Let me know if...", "Of course!", "Certainly!", "Found the smoking gun!" Remove them.
22. **Sycophantic tone.** "Great question! You're absolutely right!" Respond directly.

## Filler

23. **Filler phrases.** "In order to" becomes "to". "Due to the fact that" becomes "because". "It is important to note that" gets deleted.
24. **Excessive hedging.** "could potentially possibly be argued that it might" becomes "may".
25. **Generic conclusions.** "The future looks bright." State specific plans or facts.

## Jargon

26. **Abstract metaphor nouns.** Substrate, wedge, vector, locus, vantage, nexus, primitive (as a noun), harness (as a metaphor), surface (as in "API surface"), bedrock, scaffolding (as a metaphor), modality, paradigm, gold-plating, ratchet (as a metaphor), evacuate (for moving code), endgame, north star, flywheel. These read as technical but usually have a plainer concrete word. "Substrate" becomes "base". "Wedge in" becomes "add". "Vector" becomes "way" or "method". "Gold-plating" becomes "more than the job needs". "Ratchet" becomes the mechanism's real name or "a limit that only tightens". "Evacuate" becomes "move out". "Endgame" becomes "the last phase". A named pattern is fine when the text says what it means the first time.

## Plain speech

27. **Say what it does, not how it feels.** "the database stays close at hand", "SQL you can read", and "types that follow your schema" name a feeling. The fix names the mechanism or a number: "`.toSQL()` returns the exact string sent to the database", "a column rename fails the build". Ask what the sentence tells the reader to do or know, then write that. If you can't restate it as a concrete instruction, fact, or number, cut it. If the sentence could appear unchanged in another project's docs, it says nothing about this one. Cut it.
28. **Shorten or split dense sentences.** If the reader has to backtrack to parse a sentence, break it in two or drop clauses. One idea per sentence.
29. **Active voice.** Catch "is/are/was/were + past participle" and name the actor. "Queries are validated" becomes "the compiler validates queries". The passive is fine only when the actor is unknown or doesn't matter.
30. **Cut adverbs, or use a stronger verb.** "runs quickly" becomes "is fast" or the number. "significantly improves" becomes the measured delta. An adverb propping up a weak verb means the verb is wrong.
31. **Prefer the plain word.** "utilize" becomes "use", "leverage" becomes "use", "facilitate" becomes "help", "numerous" becomes "many", "in the event that" becomes "if".
32. **Mannered prose.** Metaphor or flourish where a literal phrase exists: aphorisms ("wire it or delete it"), rhetorical fragments for effect, personified code ("the plan holds it"), figurative verbs ("rides along", "stands on"), and stock framing phrases. "A dial worth turning" becomes "a parameter worth varying". Rule 26 covers the metaphor nouns.
33. **Over-compression.** Dropped articles, verbless fragments, symbol-speak, and abbreviations that make the reader decode instead of read. "Parser rejects bad date → exit 2, no write" becomes "The parser rejects a bad date, exits with code 2, and writes nothing." Write whole sentences with their articles and verbs, and spell out arrows and abbreviations.

## Sentences that read one way

- Address the reader as "you", in the present tense. Use "will" only for what happens later.
- Write instructions as commands ("Install the component."), not narration and not "should be done". No "please", "simply", "easy", or "quickly" in a procedure.
- Don't pre-announce ("we will soon support...") and don't start consecutive sentences with the same phrase.
- Keep "the" and "a". "Remove backup file" reads two ways. "Remove the backup file" reads one.
- Give each word one meaning and each action one word. If "check" means inspect, don't also use it for restrain. Don't write "start" here and "initiate" there.
- Keep "only" and "not" next to the word they change. "Only fails on growth" and "fails only on growth" say different things.
- Break up noun strings. "The proto import budget check script" becomes "the script that checks the proto-import budget".
- Make every "it", "they", and "this" point at one obvious thing. Repeat the noun when in doubt.
- Don't drop verbs. "Phase 1 moves the converters and Phase 2 the runtime" leaves Phase 2 without one.
- Use periods, not semicolons. Text in parentheses is a full grammatical unit or its own sentence. No plurals with "(s)", no slashes ("a, b, or both", not "a/b" or "and/or").
- Skip idioms and Latin abbreviations (e.g., i.e., etc.). Say up front that a list is partial.
- Link with words that say where the link goes, never "click here".
- Headings carry the point ("Pick the mode first", not "Modes"). A task heading is a bare verb phrase.
- Use numbered lists for sequences and bullets for everything else. Introduce a list with a complete sentence and keep its items parallel.

## Code comments

Keep a comment only for a non-obvious "why" the code can't show. A comment that restates the code is noise, so cut it in code you are already touching. A test or verify script gets no phase-narrating comments such as `// Phase 1: add cards`. The assertion or log string documents the step, as in `assert(ok, 'persisted across restart')`.

## Skill text

A skill is read by a model that follows plain instructions well.

- State the instruction and the reason for it. The reason lets the model handle the case the rule didn't name.
- No ALL-CAPS emphasis, and no boosters such as "be thorough", "IMPORTANT", or "you MUST". They make the model overapply the rule.
- Use a list only where the reader scans it. Reasoning reads better as sentences.
- The `description` says when to use the skill, with the phrasings a user types. It doesn't summarize the workflow, because the model may follow the summary instead of reading the body.

## Worked example

Before:

> Configuration of the proto import ratchet budget script parameters is performed via budget.json. Note that it's important to remember that running with --write, which updates the committed budget to reflect the current count, should only be done when lowering it. If exceeded, CI fails.

After:

> `budget.mjs` reads the committed budget from `budget.json` and counts the files that import protos. If the count exceeds the budget, CI fails. Run `budget.mjs --write` only to lower the budget.
