#!/usr/bin/env python3
"""Transition shim: settings written by the old install.py still run this path as a PreToolUse hook, and
sessions started before the plugin keep that hook. The rules now live in hooks/guard.py.
Delete once scripts/migrate.py has removed that hook and those sessions have ended."""
import pathlib, runpy

runpy.run_path(str(pathlib.Path(__file__).resolve().parents[3] / "hooks" / "guard.py"), run_name="__main__")
