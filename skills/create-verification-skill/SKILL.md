---
name: create-verification-skill
description: "Generate a project-local verify-* skill that drives the app like a user. Use for /create-verification-skill, \"verify 스킬 만들어줘\", or when a reusable verification harness is in scope; checking one change is not this. An orchestrator dispatches it instead."
---

# Create a verification skill

Every serious project needs a scripted way to drive the real app and prove behavior: launch it, exercise a feature the way a user would, and capture evidence. This skill generates that as a project-local skill (`.claude/skills/verify-<app>/`) tailored to the repo. You write the generator's output for the next agent, not for a human: it will be read cold, mid-task, by an agent that has never seen the app.

## 1. Interview the repo, not the user

Answer these from the codebase and only ask the user what you cannot observe:

- **Surface:** what does a user actually touch? A web UI, a CLI/TUI, a desktop app, an API, a mobile app, a library? A repo can have several; pick the primary one and note the rest.
- **Run:** how does the app start locally? Prefer the repo's own documented dev command (package scripts, Makefile, README quickstart). Note ports, env vars, seed data, auth.
- **Drive:** how can an agent interact with it programmatically? Existing harnesses first — Playwright/Cypress specs, expect scripts, PTY helpers, curl-able endpoints, a debug port. Only then pick a generic recipe: browser/CDP for web and Electron, a tmux/PTY harness for CLI/TUI, plain HTTP for services.
- **Observe:** what evidence can be captured? Screenshots, terminal transcripts, response bodies, logs, exit codes, DB state.
- **Isolate:** can two instances run side by side (ports, data dirs, profiles)? If not, say so in the generated skill: refusing to double-drive a shared instance beats corrupting the user's session.

If the checkout doesn't build or start as-is, fix that first (or report it precisely) before generating; a skill written against a broken base teaches wrong steps. When an irrelevant missing asset blocks startup (a static dir the API never serves, a sample config), the generated skill may create it, clearly marked as verification scaffolding, and remove it in cleanup.

## 2. Generate the skill

Write `.claude/skills/verify-<app>/SKILL.md` with YAML frontmatter (`name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it — without frontmatter the skill never registers) and these sections, each grounded in what the interview actually found (no placeholders left):

- **Launch:** the exact command that starts the app for verification, and how to tell it's ready (a log line, a prompt). Launch must reach a usable state from an empty data dir, including any migrations and seed data. Ready means usable, not just that a process answers. A database health check can pass while its init scripts are still running. Include teardown. For a short-lived CLI or TUI there is no server to keep alive: launch means build the binary (or install deps) once, then start each drive in its own isolated PTY or tmux session.
- **Doctor:** one read-only check that answers "is this instance worth driving?" — process up, right version/build, port owned by us, auth valid. An agent runs this first whenever anything looks off.
- **Drive:** the harness recipe with real selectors/commands from this repo, not examples. Prefer stable handles (ARIA labels, data attributes, prompt strings, route paths) over coordinates and tab order.
- **Evidence:** what to capture for a proof and where it goes. State the proof standards: exercise the real user path, not internal setters or test-only endpoints; capture the action and the resulting state, not just the final screen; verify side effects (files written, rows inserted, messages sent) alongside what's visible; mocks only where a production boundary already isolates the external system. When the safe path is a dry-run or test mode, verify what it actually skips by observing (files, network, git refs) rather than trusting its name: some dry-runs still touch the network or open a browser.
- **Provenance labels:** when `python3 <reap-resources>/scripts/reap.py origin-label verify-<app>` prints a label (`<namespace>.origin=verify-<app>`; exit 1 means the namespace is unset, so leave labels out), Launch puts it on every volume it creates new and throws away after the run: a compose `volumes.<name>.labels` entry, or `docker run --mount type=volume,dst=<path>,volume-label=<label>`. A path an image declares as `VOLUME` gets such a mount too, or it becomes an unlabeled anonymous volume that no cleaner can ever remove. The label is also permission to delete once nothing references the volume (`reap-resources`), so never put it on a shared or `external` volume, data reused across runs, or a volume holding evidence.
- **Cleanup:** how to tear down instances the run created. Never kill by process name; kill what you started. A container Launch started outside compose has its full ID in the state file, and Cleanup stops that ID. The cleaners remove what `reap.stopped_containers` and `reap.labeled_volumes` list; Cleanup removes this run's stopped container and throwaway volumes itself, by exact ID or name, when those settings do not list them. Cleanup requires the instance to remove (its run id or state file) and refuses without one: a default such as "the latest run" resolves to another agent's instance. Cleanup removes instances and scratch state, never the evidence: proof artifacts survive the teardown, in a location the skill names.
- **Helpers:** any script the skill ships is executable and its invocation is shown in the skill body.
- **State between steps:** in Claude Code each Bash call starts a fresh shell, so a PID, port or run directory set in Launch is gone by Drive. Have Launch write them to a state file of that instance's own (named by its run id) and print its path; the later sections `source` that path.

## 3. Seed the feature map

Create `.claude/skills/verify-<app>/features/README.md` plus one file per user-facing feature you can identify (aim for the top 3-5 to start, from routes, commands, menus, or docs). Follow the shape in [`references/feature-map-example/`](references/feature-map-example/), with a README index and one file per feature. Each file answers, from the user's point of view: what the feature is, how to reach it, how to drive it with the harness, and what observable end state proves it works. The four H2s are `Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, and `Gotchas`. If the seed data lacks a precondition that a journey needs, the journey reports a failure and the reason goes in that feature's `Gotchas`. Never weaken the journey to make it pass. The map is the repo's maintained verification source; a proof that drives one convenient entry point is incomplete when the map lists others.

## 4. Prove the generated skill before handing it over

Run `python3 "${CLAUDE_SKILL_DIR}/scripts/verify_skill_lint.py" .claude/skills/verify-<app>` first and fix every finding. It reads files only and checks the rules above that a file can show: frontmatter, the five sections, state-file names (no secret-file names, no shared `latest` link), a cleanup script that requires its instance, no pattern kills, executable and documented helpers, and the feature-map index. Then run its own instructions end to end once: launch, doctor, drive ONE mapped feature (one is enough; the map exists so later runs can cover the rest), capture evidence, clean up. After cleanup, confirm the evidence still exists at the named location — a cleanup that eats the proof fails this step. Where the app can run two instances side by side, launch a second one before cleaning up the first and confirm it is still running afterwards; then clean it up too. Fix what fails, and run the generated cleanup after every failed iteration too, so broken attempts don't strand processes and ports. A generated skill that was never executed is a draft, not a deliverable. If you proved it in a stand-in for the real target (a container in place of a fresh VM, a local stack in place of a remote one), list every setup step the stand-in did that the target will not. Those steps stay unverified until a run on the real target covers them.

## 5. Offer the maintenance loop

Point the user at `maintain-verification-skill` for keeping the map honest as the app changes. Suggest a cadence only if they ask. If the repo ignores `.claude/`, say so: the skill then stays on this machine unless the ignore rule lets `.claude/skills/` through.
