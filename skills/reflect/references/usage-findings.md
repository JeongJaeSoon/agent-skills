# Usage findings

How step 5 routes a `usage` row (Source `skill-usage`, Learning `<skill>: <flag>`).

A `usage` row about a skill that other skills invoke (the development flow) or that runs only at a rare moment (Close, setup or onboarding, incident response, a legacy alias) is rejected with that reason. For the rest, judge why the skill goes unused; the flag picks the route and the collector's `suggest` is only a hint. `misses` and `slash_only` go to `tune description`. Zero uses alone is no finding: the collector sends no signal for `unused_30d`, and an `unused_30d` row an older collector wrote is rejected with that reason. `retire_candidate` means unused for three 30-day windows, with misses or an overlapping skill as its basis (in the note), and not on the rare list; with an overlap it goes to a merge into that skill (a substantive edit), with misses only to `tune description` first, and a retirement is only ever a Backlog ticket for the human. Never delete a skill.
