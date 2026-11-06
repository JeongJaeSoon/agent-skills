---
name: challenge
description: "Blast radius: what a change could break beyond its diff, proven by running code; 'blast radius of X', \"이거 바꾸면 뭐가 깨져\", \"영향 범위 봐줘\", a small diff you don't trust. Interrogate: \"interrogate\", \"adversarial review\", \"challenge this\", \"find blind spots\" on a design, plan or decision. A PR headed to main is deliver-ticket's review."
---

# Challenge

Attack a change or a plan before it ships. Pick the mode by what is being challenged, then read its file and follow it:

- **blast-radius**: a change, often a small diff you don't trust. What it breaks beyond the diff, and the one fact it is safe because of, proven by running real code. [references/blast-radius.md](references/blast-radius.md).
- **interrogate**: a design, plan, decision or diff put to independent reviewers. One verdict sorted into Act on, Consider, Noted and Dismissed. [references/interrogate.md](references/interrogate.md).

A design objection does not finish a blast-radius run: the safety fact is proven by code that ran, or marked unproven. The verdict buckets, which `deliver-ticket` also sorts its review findings by, are in [references/lead-judgment.md](references/lead-judgment.md). The independent review of a PR headed to main is `deliver-ticket`'s, not this skill's.
