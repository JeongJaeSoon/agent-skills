#!/usr/bin/env python3
"""Retired PreToolUse, PermissionDenied, Stop and SubagentStop hook: the refused-act ledger it kept was removed, and
hooks.json no longer runs it.

A session that loaded hooks.json before then still runs this path until /reload-plugins or a restart. Without the
file, python3 exits 2, which Claude Code reads as a block on every tool call of that session. So it stays as a no-op;
hooks/test_retired.py fails when any path hooks.json ever ran is missing.
"""
import sys

sys.exit(0)
