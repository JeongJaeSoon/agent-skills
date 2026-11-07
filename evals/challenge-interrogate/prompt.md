---
max_turns: 10
allowed_tools: [Read, Glob, Grep, Skill]
tags: [trigger]
---

Adversarial review of this plan, find blind spots: we cache every API response for 24 hours in one shared Redis key per endpoint, and invalidate the whole key whenever any user writes to that endpoint.
