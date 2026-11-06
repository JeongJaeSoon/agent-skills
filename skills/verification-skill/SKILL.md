---
name: verification-skill
description: "A verify-* skill drives the app like a user. Create: /create-verification-skill, \"verify 스킬 만들어줘\", a reusable verification harness in scope. Maintain: \"verify 스킬 점검해줘\", the weekly round, a verify-* skill that failed to drive a feature. Checking one change is not this. An orchestrator dispatches it instead."
---

# Verification skill

A project-local `verify-<app>` skill (`.claude/skills/verify-<app>/`) drives the real app the way a user does and captures evidence; its feature map says what to drive. Pick the mode, then read its file and follow it:

- **create**: no verify-* skill covers the app yet, and a reusable harness is asked for or in scope. Interview the repo, generate the skill and its feature map, and prove it end to end once. [references/create.md](references/create.md).
- **maintain**: a verify-* skill exists and may have drifted: the weekly round, a check of it, or a run that reported `verify skill stale: <feature>`. Read every feature from source, drive every feature live, and ship at most one PR of proven fixes to that skill's own directory. [references/maintain.md](references/maintain.md).

A verify-* skill that failed to drive a feature is repaired in maintain mode, not generated again; maintain with no verify-* skill to find stops and points at create. Proving that one change works is `deliver-ticket` §5, which uses the verify skill and does not build or repair it unless that is in scope. An orchestrator dispatches either mode to a worker instead of running it in its own session.
