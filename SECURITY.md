# Security policy

## Supported versions

Only the latest release gets fixes. Releases are tagged `vYYYY.MM.DD.N`; see [Releases](https://github.com/JeongJaeSoon/agent-skills/releases).

## Reporting a vulnerability

Please report it privately, not in a public issue or pull request:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** and fill in the form.

Include what is affected (a skill, hook or script, and its path), how to reproduce it, and what an attacker could do with it. Leave out real secrets, tokens and personal data; a placeholder is enough to show where one would go.

You'll get a reply on the report itself. Say there if you'd like to be credited when the fix is released.

## Scope

This plugin runs inside Claude Code with your permissions. Its hooks make permission decisions (`hooks/guard.py`, `hooks/orchestrator_guard.py`), and some scripts write settings files only after you approve a diff (`scripts/setup.py`, `skills/skills-config`, `skills/tune-automode`). Reports about these are especially welcome:

- a hook that allows something it should leave to the normal permission flow,
- a script that writes a settings file without the approval step, or writes more than the diff it showed,
- a path where a secret ends up in a log, a file or command output.

Bugs in Claude Code itself go to Anthropic, and bugs in other plugins or tools go to their owners.
