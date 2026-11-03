#!/usr/bin/env python3
"""Retired SessionStart hook: the panel whose install it hinted at was removed, and hooks.json no longer runs it.

A session that loaded hooks.json before then still runs this path until /reload-plugins or a restart, so it stays
as a no-op; hooks/test_retired.py fails when any path hooks.json ever ran is missing.
"""
import sys

sys.exit(0)
