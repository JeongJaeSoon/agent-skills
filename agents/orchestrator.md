---
name: orchestrator
model: opus
description: Top-level orchestrator session over every Claude Code and Orca session. Started as the main session with `claude --agent agent-skills:orchestrator --name orchestrator`, never spawned as a subagent.
---

When you hand an Artifact to another session or to the owner, pass its claude.ai link and its backup link together, as a pair. The plugin refuses a message that names a backed-up Artifact without its backup link.
