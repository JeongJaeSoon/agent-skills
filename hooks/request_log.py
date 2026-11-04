#!/usr/bin/env python3
"""Retired UserPromptSubmit hook: the owner-request ledger it fed was removed, and hooks.json no longer runs it.

A session started before that release still runs this path until /reload-plugins or a restart, so it stays as a
no-op; hooks/test_retired.py fails when any path hooks.json ever ran is missing.
"""
import sys

sys.exit(0)
