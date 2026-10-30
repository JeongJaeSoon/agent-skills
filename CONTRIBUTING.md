# Contributing

Thanks for taking a look. This repository is a Claude Code plugin: skills under `skills/`, agents under `agents/`, hooks under `hooks/`, and helper scripts under `scripts/` and `bin/`. Issues and pull requests are welcome.

## Before you start

- **This repository is public.** Never put a company or organization name, an internal repository or service name, a chat channel name or ID, a person's name or ID, a ticket key or URL, an internal domain, or raw logs and transcripts into a file, a commit message, a PR title or body, or a PR comment. Write the rule in general terms instead ("a review request in chat", "a tracker ticket"), and use placeholders in examples: `acme/app#1`, `ABC-1`, `example.test`, `U0FAKE…`.
- **Skills stay general.** A value that belongs to one setup (a review channel, a tracker project, a merge policy) is read at run time from `~/.claude/agent-skills.json` or `~/.config/agent-skills/`, never written into a skill. When the setting is missing, the skill turns that feature off or stops and asks.
- **If you are an AI agent,** read [AGENTS.md](AGENTS.md) first. It holds the same rules plus the languages each kind of file is written in.

## Set up a checkout

You need git, Python 3.9 or newer, and Claude Code. The tests use only the standard library.

```bash
git clone https://github.com/JeongJaeSoon/agent-skills.git
cd agent-skills
git switch -c <your-branch>
```

To try your changes in a session, start Claude Code with the checkout loaded as a plugin directory, for example `claude --plugin-dir <path to your checkout>`. If you also have the marketplace copy installed, disable it first (`claude plugin disable agent-skills@jeongjaesoon`) so the two copies don't both load.

## Tests

There is no CI. Run this from the repository root before you open a pull request:

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/measure-delivery/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_cards.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/skills-config/scripts/test_config_emit.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/reflect/scripts/test_lesson_ids.py skills/deliver-ticket/scripts/test_shots.py skills/ui-review/scripts/test_ui_measure.py hooks/test_*.py scripts/test_sync.py scripts/test_card.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py scripts/test_skill_tokens.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

The test files are listed one by one because only some of them use `unittest`, so there is no single discovery command. When you add a test file, add it to this loop.

Also run:

- `claude plugin validate .` for the manifests.
- `python3 scripts/catalog/build.py /tmp/skill-catalog.html` when you change `docs/skills.md`, `docs/skills.en.md` or `docs/skills.ja.md`. It fails when the three catalogs have a different number of headings or skills, and warns when the English or Japanese catalog lags the Korean one.

`scripts/test_public_shapes.py` checks for the shapes of private data (home directory paths, chat IDs, ticket keys, tenant hostnames). It cannot catch every internal name, so read your own diff for them too.

## Changing a skill

- Edit `skills/<name>/SKILL.md` and its `references/` and `scripts/`. The `description` in the frontmatter decides when the model picks the skill, so keep the trigger phrases in it.
- Update the skill's entry in the catalogs (`docs/skills.md`, then `docs/skills.en.md` and `docs/skills.ja.md`, whose first line records the commit they were translated from).
- If you changed when a skill fires, check it with `claude plugin eval . --ablation none --no-publish --trust-plugin --case <skill>`. The cases live under `evals/<skill>/`.

## Pull requests

- Keep one change per pull request, and say in the body why it is shaped this way and what you ruled out.
- Commits are signed.
- Paste the test output into the pull request.

## Reporting a security issue

Don't open a public issue. See [SECURITY.md](SECURITY.md).
