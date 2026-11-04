#!/usr/bin/env python3
"""The one map of agent-skills' data: every fact, the store that holds it, its role there, who may write it.

STORES is the registry. Each Store is one file pattern (or an outside source, or later a DB table) and the facts it
holds, each with a role: exactly one store is canonical for a fact; the rest derive, cache or log it. writers name
the functions allowed to write the store ("module:qualname", module = file stem, `<owner>/<stem>` when two scripts
share a stem: hooks/guard and reap-resources/guard); readers and producer are the
Data flow screen's nodes. check() enforces the registry against the code: one canonical store per fact, no write to
a file the registry does not name, no write from a function its store does not list (an AST scan of every script).

Usage: registry.py check | registry.py writes      (the violations, or every write site the scan found)
"""
import ast, dataclasses, fnmatch, json, pathlib, re, sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCES = ("skills/*/scripts/*.py", "hooks/*.py", "hooks/lib/*.py", "scripts/*.py", "scripts/*/*.py")

CANONICAL, DERIVED, CACHE, LOG, BACKUP, LOCK, HAND, REMOVED = (
    "canonical", "derived", "cache", "log", "backup", "lock", "hand", "removed")
ROLES = {CANONICAL, DERIVED, CACHE, LOG, BACKUP, LOCK, HAND, REMOVED}
# Store states on the Data flow screen; a consumer is starved when none of its inputs is PRESENT.
PRESENT, OFF, NOT_YET, ERROR = "present", "off", "not yet", "error"


@dataclasses.dataclass(frozen=True, eq=False)
class Store:
    id: str
    path: str               # "<root>/<pattern>" with a root from ROOTS, "ext:<source>", or "db:<table>.<column>"
    facts: dict             # fact key -> role (ROLES); one store is canonical for each fact
    writers: tuple = ()     # "module:qualname" a scan checks, or "ext:<who>" for a writer outside the scripts
    readers: tuple = ()     # CONSUMERS ids
    stale_s: int = None     # older than this while present: stale
    feature: str = None     # agent-skills.json key that turns its writer on; missing key and missing file: OFF
    rule: str = None        # what it holds, one line for the screen


# Where each root is on this machine; flow.py resolves them (env overrides included).
ROOTS = {
    "D": "the fleet state directory: $ORCH_FLEET_STATE, else S/dashboard",
    "S": "the shared state directory: $AGENT_SKILLS_STATE, else ~/.local/state/agent-skills",
    "P": "the programs home: $PROGRAMS_HOME, else ~/.claude/programs",
    "C": "~/.claude",
    "K": "~/.config/agent-skills",
    "X": "$XDG_STATE_HOME, else ~/.local/state",
    "H": "~",
}

# Producers on the screen: who writes, by module of the writer (ext: writers by name).
PRODUCERS = {
    "fleet": ("fleet collector · dash serve loop", ("fleet", "stage_auto")),
    "dash": ("program views · dash serve", ("dash",)),
    "skills": ("skill usage collect · 15 min", ("skill_usage",)),
    "tokens": ("token usage collect · 15 min", ("token_usage",)),
    "prstats": ("PR stats collect", ("pr_stats",)),
    "orch": ("orch CLI · board, decide, stage, sent, ack, hold, brief --commit", (
        "board", "stages", "sent", "acks", "holds", "jobs", "orca_migrate", "store")),
    "tidy": ("tidy · each tick and orch board done", ("tidy",)),
    "programs": ("program leads · orch land, wait, heavy", ("prog",)),
    "patrol": ("patrol round", ("patrol",)),
    "janitor": ("janitor · reap precheck and reap, sweep shadow", ("reap", "cards", "cardguard", "reap-resources/guard",
                                                                    "sweep")),
    "pressure": ("pressure round · launchd", ("pressure",)),
    "reaphard": ("reap-hard · launchd", ("reap_hard",)),
    "cardwatch": ("card-watch monitor", ("card_watch", "agent_signal")),
    "hooks": ("hooks · reorient, roles, permission, onboard, record_check", (
        "reorient", "roles", "config", "permission", "onboard", "record_check")),
    "sync": ("skills-sync · launchd, card, handoff, end-check", ("sync", "card", "end_check")),
    "preview": ("preview-watch · launchd", ("preview_watch",)),
    "share": ("share-server", ("share_server",)),
    "watchprs": ("watch-prs round", ("prs", "notify")),
    "mentions": ("watch-mentions round", ("mentions",)),
    "reflect": ("reflect · orchestrator feedback", ("orchestrator_feedback",)),
    "securefill": ("secure-fill", ("secure_fill",)),
    "launchd": ("launchd stdout", ("ext:launchd",)),
    "cards": ("card sessions · report files", ("ext:cards",)),
    "human": ("you · config, briefs, notes", ("ext:human", "ext:leads")),
    "outside": ("Orca · GitHub · Claude Code · tracker", ("ext:orca", "ext:github", "ext:claude-code", "ext:tracker")),
}
CONSUMERS = {
    "page": "dashboard · Overview, Inbox, PRs, Sessions, Graph, Timeline, Automations",
    "brief": "orch brief render · Brief, Stages and To-do pages, /brief",
    "skillspage": "dashboard · Skills, Skill changes",
    "statspage": "dashboard · PR stats",
    "tokenspage": "dashboard · Tokens",
    "sentpage": "dashboard · Sent",
    "fleet": "fleet collector (its own inputs)",
    "programview": "dashboard · program views, orch status/wait, measure-delivery",
    "reflect": "reflect precheck · improvement loop",
    "watchprs": "watch-prs round",
    "mentions": "watch-mentions round",
    "reorient": "hooks · reorient, guard, delegate_gate",
    "patrol": "patrol round · orch patrol timing",
    "cardwatch": "orch watch-cards round",
    "janitor": "janitor · reap, reap-hard, cards, sweep",
    "pressure": "pressure round · pressure gate hook",
    "tidy": "tidy",
    "sync": "skills-sync",
    "skillsrc": "every script reading its config",
    "nobody": "no reader",
}

STORES = [
    # -- outside sources: agent-skills reads them and never writes them
    Store("orca", "ext:orca", {"work.unit": CANONICAL, "work.parent": CANONICAL, "work.agent_state": CANONICAL,
                                "run.dispatch": CANONICAL, "run.gate": CANONICAL, "mail.message": CANONICAL,
                                "card.linked_pr": CANONICAL},
          ("ext:orca",), ("fleet", "patrol", "programview", "janitor"), rule="worktrees, terminals, Runs, inbox"),
    Store("github", "ext:github", {"pr.state": CANONICAL, "pr.ci": CANONICAL, "pr.review": CANONICAL,
                                    "pr.author": CANONICAL, "repo.git": CANONICAL},
          ("ext:github",), ("fleet", "statspage", "programview", "watchprs"), rule="PRs, CI, reviews"),
    Store("tracker", "ext:tracker", {"ticket.state": CANONICAL}, ("ext:tracker",), ("programview",),
          rule="Linear or Jira through use-tracker"),
    Store("machine", "ext:machine", {"machine.resource": CANONICAL, "machine.load": CANONICAL}, ("ext:os",),
          ("janitor", "pressure"), rule="processes, disk, containers, memory"),
    Store("transcripts", "C/projects/*/*.jsonl", {"session.transcript": CANONICAL}, ("ext:claude-code",),
          ("skillspage", "tokenspage", "fleet", "patrol", "reflect", "janitor"), rule="Claude Code transcripts"),
    Store("claudesessions", "C/sessions/*.json", {"session.live": CANONICAL}, ("ext:claude-code",), ("fleet",)),
    Store("plugins", "C/plugins/installed_plugins.json", {"skill.installed": CANONICAL}, ("ext:claude-code",),
          ("skillspage",)),
    Store("memory", "C/projects/*/memory/*.md", {"owner.feedback": CANONICAL}, ("ext:claude-code",), ("nobody",),
          rule="read by the sessions themselves, no script"),
    Store("notes", "ext:notes", {"program.config": CANONICAL, "lesson.row": CANONICAL}, ("ext:leads",), ("reflect",),
          feature="reflect.ledger", rule="notes store; the lessons ledger when it is a plain file"),
    Store("briefs", "P/*/briefs/*.md", {"card.brief": CANONICAL}, ("ext:leads", "card:create"), ("fleet",),
          rule="card briefs; their REPORT line names the report file; skills-sync card rewrites the marked "
               "FORBIDDEN section of the brief it is given"),
    Store("reports", "ext:card-report", {"card.milestone": CANONICAL}, ("ext:cards",), ("fleet", "brief"),
          rule="a card's own report: the file its brief names, else its newest milestone message to its Run"),
    # -- configuration
    Store("config", "C/agent-skills.json", {"config.skills": CANONICAL}, ("ext:human", "setup:main"),
          ("skillsrc",), rule="per-skill settings; a missing key turns a feature off"),
    Store("settings", "C/settings.json", {"config.claude": CANONICAL},
          ("ext:human", "setup:main", "bootstrap:main", "migrate:main", "config_emit:run",
           "config_emit:main", "automode_rule:main"), ("reorient",), rule="Claude Code settings, through emitted scripts"),
    Store("configbak", "C/*.bak-*", {"config.claude": BACKUP, "config.skills": BACKUP},
          ("bootstrap:main", "migrate:main", "setup:write"), ("nobody",), rule="a settings file before setup changed it"),
    Store("fleetcfg", "K/dashboard/config.json", {"config.fleet": CANONICAL}, ("ext:human",), ("fleet",)),
    Store("usagecfg", "K/skill-usage.json", {"config.rare_skills": CANONICAL}, ("ext:human",), ("skillspage",)),
    # -- D: the fleet state
    Store("state", "D/state.json", {"work.unit": DERIVED, "work.agent_state": DERIVED, "card.milestone": DERIVED,
                                     "card.linked_pr": DERIVED, "session.role": DERIVED, "mail.message": CACHE},
          ("fleet:Fleet.tick",), ("page", "brief", "tidy", "patrol"), 300,
          rule="sessions (phase, report with its source, current PR), PRs, inbox, timeline: one collect"),
    Store("prs", "D/prs.json", {"pr.state": CACHE}, ("fleet:Fleet.tick",),
          ("page", "brief", "tidy", "skillspage"), 1800, rule="PRs any session, board line or ledger names"),
    Store("fleetmem", "D/memory.json", {"fleet.memory": CANONICAL, "inbox.dismissed": CANONICAL},
          ("fleet:Fleet.tick", "fleet:dismiss"), ("fleet", "reorient"), 300,
          rule="phase memory, sticky turn ends, dismissals; stage_auto_at, which stage_auto.warning reads in the hook"),
    Store("events", "D/events.jsonl", {"timeline.event": CANONICAL}, ("fleet:Fleet.event",), ("fleet",),
          rule="phase and PR changes; the timeline reads its tail"),
    Store("subagents", "D/subagents.jsonl", {"work.subagent": CANONICAL}, ("fleet:subagent_log",),
          ("fleet", "brief", "page"), rule="observed subagent start/stop, from hooks/subagent_log.py"),
    Store("subagentsold", "D/subagents.jsonl.1", {"work.subagent": BACKUP}, ("fleet:subagent_log",), ("nobody",),
          rule="the generation before the last rotation; open starts are carried into the new file"),
    Store("subagentslock", "D/subagents.jsonl.lock", {"work.subagent": LOCK}, ("fleet:subagent_log",)),
    Store("eventsold", "D/events.jsonl.1", {"timeline.event": BACKUP}, ("fleet:Fleet.event",), ("nobody",),
          rule="the generation before the last rotation"),
    Store("preventsold", "D/pr-events.jsonl.1", {"timeline.event": BACKUP}, ("fleet:Fleet.emit_pr_event",), ("nobody",),
          rule="the generation before the last rotation"),
    Store("prevents", "D/pr-events.jsonl", {"timeline.event": LOG}, ("fleet:Fleet.emit_pr_event",), ("nobody",),
          rule="the events.jsonl pr_* rows again; nothing reads it"),
    Store("jobs", "D/jobs.json", {"job.last_run": CANONICAL}, ("jobs:record",), ("page",), 300,
          rule="each serve job's last run"),
    Store("jobslock", "D/jobs.lock", {"job.last_run": LOCK}, ("jobs:locked",)),
    Store("avatars", "D/avatars/*.png", {"pr.author": CACHE}, ("fleet:Fleet.fetch_avatars",), ("page",)),
    Store("prompts", "D/prompts/*.json", {"permission.prompt": CANONICAL}, ("permission:main",), ("fleet",),
          rule="one file per pending permission prompt"),
    Store("decisions", "D/decisions.json", {"decision.item": REMOVED}, ("store:export_legacy",), ("nobody",),
          rule="decisions before PR 4a; `orch db import` leaves an empty directory here so older code fails to write, "
               "and `orch db export --legacy` writes one for a rollback"),
    Store("decisionslock", "D/decisions.lock", {"decision.item": LOCK}, ("store:_locks",)),
    # -- D: agent-skills.db, every connection through store.py (check() refuses one anywhere else)
    Store("db", "D/agent-skills.db", {"db.file": CANONICAL}, ("store:import_apply", "store:restore"),
          ("fleet", "brief", "reorient"), rule="SQLite (WAL) holding the db: tables below; only `orch db import` "
                                                "creates it"),
    Store("dbwal", "D/agent-skills.db-*", {"db.file": LOCK}, ("ext:sqlite",), ("nobody",),
          rule="SQLite's -wal and -shm beside the DB: part of it, never copied alone (`orch db backup`)"),
    Store("dbbackup", "D/backup-db-*/*", {"decision.item": BACKUP}, ("store:import_apply",), ("nobody",),
          rule="the legacy files `orch db import` replaced, as they were, with their sha256 in db:migration"),
    Store("decision", "db:decision.status", {"decision.item": CANONICAL},
          ("store:change_decisions", "store:_insert_decision", "store:restore"), ("fleet", "brief", "reorient"),
          rule="`orch decide` and the page's answers; closed ones stay, filtered by status and closed_at"),
    Store("counter", "db:counter.next", {"decision.id": CANONICAL}, ("store:change_decisions", "store:import_apply",
                                                                     "store:restore"), ("fleet",),
          rule="the next d<N>; never reused"),
    Store("migration", "db:migration.stage", {"db.migration": CANONICAL},
          ("store:import_apply", "store:_set_stage", "store:restore"), ("fleet", "brief"),
          rule="data steps imported (4a-decisions) with the legacy files' sha256; the access gate reads it"),
    Store("dbevent", "db:event.seq", {"decision.item": LOG, "db.migration": LOG}, ("store:_event", "store:restore"),
          ("nobody",), rule="each change's before and after, in the same transaction; import keeps the legacy original"),
    Store("inbox", "D/inbox-external.jsonl", {"inbox.external_item": CANONICAL, "pressure.queue_item": LOG},
          ("fleet:inbox_add", "fleet:inbox_resolve", "pressure:dash_note"), ("fleet",),
          rule="items added from outside a session; pressure waits copied here too"),
    Store("sends", "D/sends.jsonl", {"send.page": CANONICAL}, ("fleet:send", "fleet:open_session", "fleet:answer_prompt"),
          ("sentpage",), rule="text sent from the page"),
    Store("sent", "D/sent/*.jsonl", {"send.instruction": CANONICAL}, ("sent:add",), ("sentpage", "patrol"),
          rule="`orch sent` log, one file a day"),
    Store("board", "D/board.json", {"board.todo": CANONICAL, "board.later": CANONICAL, "board.doing": CANONICAL,
                                     "board.owner_todo": CANONICAL, "board.row": CANONICAL, "board.lead": CANONICAL,
                                     # hand copies of Orca's facts: doing lines and leads naming cards, rows' --pr
                                     "work.unit": CANONICAL, "card.linked_pr": CANONICAL},
          ("board:change",), ("brief", "tidy", "patrol", "fleet"),
          rule="to-dos, held items, doing lines, rows' phase and progress, leads Orca cannot see"),
    Store("boardprev", "D/board.prev.json", {"board.todo": BACKUP}, ("board:change",), ("nobody",),
          rule="the board before its last write"),
    Store("boardlock", "D/board.lock", {"board.todo": LOCK}, ("board:change", "orca_migrate:board_lock", "store:_locks")),
    Store("tidy", "D/tidy.jsonl", {"board.closure": CANONICAL}, ("tidy:append",), ("brief", "fleet", "tidy"),
          rule="done and reopen lines for board items, rows and cards"),
    Store("tidyold", "D/tidy.jsonl.1", {"board.closure": BACKUP}, ("tidy:append",), ("nobody",),
          rule="the generation before the last rotation"),
    Store("tidylock", "D/tidy.lock", {"board.closure": LOCK}, ("tidy:locked", "store:_locks")),
    Store("migrate", "D/backup-orca-v2-*/*", {"board.todo": BACKUP, "board.closure": BACKUP},
          ("orca_migrate:backup", "orca_migrate:apply"), ("nobody",), rule="`orch migrate` copies and its undo list"),
    Store("adoption", "D/adoption.jsonl", {"work.parent_adopted": CANONICAL}, ("fleet:adopt",), ("fleet",),
          feature="adopt.write_orca_parent", rule="parents `orch adopt --apply` set, for --undo"),
    Store("acks", "D/acks.jsonl", {"ack": CANONICAL}, ("acks:write",), ("brief",), rule="rows acknowledged on the page"),
    Store("ackslock", "D/acks.lock", {"ack": LOCK}, ("acks:write",)),
    Store("stages", "D/stages.json", {"stage.manual": CANONICAL}, ("stages:change",), ("brief",),
          rule="`orch stage` cells and rows; win over stages-auto"),
    Store("stageslock", "D/stages.lock", {"stage.manual": LOCK}, ("stages:change",)),
    Store("stagesauto", "D/stages-auto.json", {"program.event": DERIVED, "run.dispatch": DERIVED},
          ("stage_auto:refresh",), ("brief", "reorient", "fleet"),
          rule="stage rows per program from its ledger, Run cards and PRs, built each tick and written when they "
               "change; its freshness is memory.json stage_auto_at"),
    Store("skillsj", "D/skills.json", {"session.transcript": DERIVED, "skill.signal": DERIVED},
          ("skill_usage:refresh", "skill_usage:main"), ("skillspage",), 3600, rule="usage per skill from transcripts"),
    Store("skillcache", "D/skill-usage-cache.json", {"session.transcript": CACHE}, ("skill_usage:collect",),
          ("skillspage",), 3600),
    Store("tokensj", "D/tokens.json", {"session.transcript": DERIVED}, ("token_usage:refresh",), ("tokenspage", "brief"),
          3600, rule="token use per who, skill and turn origin"),
    Store("tokencache", "D/token-usage-cache.json", {"session.transcript": CACHE}, ("token_usage:collect",),
          ("tokenspage",), 3600),
    Store("prstats", "D/pr-stats.json", {"pr.state": DERIVED}, ("pr_stats:refresh", "pr_stats:record_error",
          "pr_stats:main"), ("statspage",), 86400),
    Store("requests", "D/requests.jsonl", {"order.request": REMOVED}, (), ("nobody",),
          rule="the removed request ledger; nothing writes or reads it"),
    # -- S: hooks, watchers and the janitor
    Store("roles", "S/roles/*.json", {"session.role": CANONICAL}, ("roles:declare",), ("reorient", "fleet", "janitor", "patrol",
                                                                                  "tokenspage"),
          rule="the role a session declared with a ROLE: line"),
    Store("terminals", "S/terminals/*.json", {"session.terminal": CANONICAL}, ("roles:record_terminal",), ("sync", "cardwatch", "patrol"),
          rule="which session started in an Orca terminal; handle, session id, cwd and time only"),
    Store("wakes", "S/wakes/*.json", {"session.wake": CANONICAL}, ("sync:wake_target", "sync:wake_check", "sync:wake_reset"),
          ("sync", "programview"), rule="per terminal: the SendMessage wake not yet confirmed in the receiver's transcript"),
    Store("wakeslock", "S/wakes/*.lock", {"session.wake": LOCK}, ("sync:wake_lock",)),
    Store("rolespans", "S/transcripts/*.json", {"session.transcript": DERIVED}, ("reorient:register",), ("reflect",),
          rule="where an orchestrator's transcript starts"),
    Store("brieflast", "S/brief-last/*.json", {"brief.last_sent": CANONICAL}, ("board:commit",), ("brief",),
          rule="the last sent table, for bold = changed"),
    Store("cardwatch", "S/card-watch/*.json", {"card.watch": CANONICAL}, ("card_watch:main",),
          ("fleet", "patrol", "programview"), rule="per monitor: what it saw last on each card"),
    Store("cardwatchdown", "S/card-watch/.down-*", {"card.watch_down": CANONICAL}, ("card_watch:down_line",), ("fleet",)),
    Store("agentsignal", "S/agent-signal.jsonl*", {"card.agent_signal": CANONICAL},
          ("agent_signal:append", "agent_signal:log_round"), ("nobody",),
          rule="both signals per card, `claude agents` beside the screen, to compare misses; one rotated generation kept"),
    Store("agentsignallock", "S/agent-signal.lock", {"card.agent_signal": LOCK}, ("agent_signal:append",)),
    Store("reaplocks", "S/locks/*-*.lock", {"work.unit": LOCK, "machine.resource": LOCK}, ("reap-resources/guard:hold",),
          rule="one lock per card, project, volume, process or port a cleaner acts on; cardguard takes the card one "
               "through guard.hold"),
    Store("sync", "S/sync.json", {"sync.result": CANONICAL}, ("sync:sync",), ("fleet",), 3600,
          rule="last skills-sync result"),
    Store("synclock", "S/sync.lock", {"sync.result": LOCK}, ("sync:locked",)),
    Store("cards", "S/cards.jsonl", {"card.key": CANONICAL}, ("card:ledger_add",), ("sync",),
          rule="cards skills-sync card and handoff started: repo, references, role, superseded_by"),
    Store("cardslock", "S/cards.lock", {"card.key": LOCK}, ("card:card_lock",)),
    Store("closesetup", "S/close-setup/*.log", {"close_setup.output": CANONICAL},
          ("card:start_close_setup", "end_check:apply"), ("nobody",),
          rule="each background close-setup's output, ending in result: ok or failed"),
    Store("reload", "S/reload-pending.json", {"sync.reload_pending": CANONICAL},
          ("sync:queue_reload", "sync:broadcast_locked"), ("fleet",), rule="reloads not yet sent, per terminal"),
    Store("restarts", "S/restarts.json", {"sync.stale_restart": CANONICAL}, ("sync:restart_stale",), ("sync",),
          rule="per terminal: the /restart or /reload-plugins typed once for a stale screen, or why it waits"),
    Store("synclog", "S/sync.log*", {"sync.output": CANONICAL}, ("ext:launchd", "reap:reap_state_file"), ("nobody",),
          rule="launchd stdout; the janitor rotates it"),
    Store("guarderrors", "S/guard-errors.jsonl", {"hook.error": CANONICAL}, ("config:log_error",), ("nobody",),
          rule="a hook's own exception, let through; read by a person"),
    Store("recordcheck", "S/record-check.jsonl", {"record.obligation": CANONICAL}, ("record_check:append",),
          ("nobody",), rule="merges, lands and PR opens that still owe a record line, their closes, and the warn "
                            "rows a week of observation reads before guard.record_check goes to deny"),
    Store("onboard", "S/onboard-declined.json", {"onboard.declined": CANONICAL}, ("onboard:decline",), ("reorient",)),
    Store("ledger", "S/ledger.jsonl", {"resource.made": CANONICAL}, ("reap:act", "reap:main", "reap:note_stack_dirs"), ("janitor",),
          rule="what agents made, retired when reaped"),
    Store("janitorplan", "S/janitor-plan.json", {"machine.resource": DERIVED}, ("reap:main", "cards:main"), ("janitor",),
          rule="the precheck's scan; the same scanner as pressure-plan"),
    Store("janitorstamps", "S/janitor-*.json", {"janitor.round": CANONICAL},
          ("reap:precheck", "reap:record_reap", "reap:stamp_round"), ("janitor",),
          rule="last plan, last round and repeat guard"),
    Store("sweepstamps", "S/*-last.json", {"janitor.sweep_stamp": CANONICAL},
          ("reap:stamp_user_folder", "reap:record_reap", "reap_hard:write_last"), ("janitor", "brief"),
          rule="daily sweep stamps and the last reap-hard pass"),
    Store("patrolverdicts", "S/patrol-verdicts.json", {"card.verdict": CANONICAL},
          ("patrol:write_round", "patrol:candidates_phase", "patrol:mark_cut"),
          ("janitor", "pressure", "tidy", "brief", "patrol"),
          rule="each card's keep or remove verdict, one round; the cleanup v2 shadow fields (round, sources, "
               "activity, candidates), which sweep reads"),
    Store("patrolrounds", "S/patrol-rounds.jsonl", {"patrol.round_timing": CANONICAL}, ("patrol:log_round",),
          ("patrol",), rule="per-source seconds per round, the last ROUNDS_KEEP; `orch patrol timing` reads it"),
    Store("reapactions", "S/reap-actions.jsonl", {"reap.action": CANONICAL}, ("reap:log_action",), ("janitor",),
          rule="what reap --plan and cards.py apply really did; `sweep diff` matches the shadow's plan against it"),
    Store("sweepshadow", "S/sweep-shadow.jsonl", {"sweep.shadow": CANONICAL}, ("sweep:run_shadow",), ("janitor",),
          rule="what sweep would act on or leave per patrol round; it acts on nothing yet"),
    Store("sweeplock", "S/sweep.lock", {"sweep.shadow": LOCK}, ("sweep:run_lock",)),
    Store("sweepout", "S/sweep.log", {"sweep.output": CANONICAL}, ("reap:spawn_sweep_shadow",), ("nobody",),
          rule="the detached sweep run's stdout"),
    Store("patrollog", "S/patrol.jsonl", {"patrol.finding": CANONICAL}, ("patrol:write_round", "patrol:ack"),
          ("patrol", "programview"), rule="findings and their acks"),
    Store("patrolstate", "S/patrol-*.json", {"patrol.memo": CANONICAL}, ("patrol:round_once", "patrol:pending_lines"),
          ("patrol",), rule="round memo and re-notice guard"),
    Store("patrollock", "S/patrol.lock", {"card.verdict": LOCK}, ("patrol:round_lock",)),
    Store("patrolout", "S/patrol.log", {"patrol.output": CANONICAL}, ("reap:spawn_patrol",), ("nobody",)),
    Store("pressure", "S/pressure.json", {"pressure.level": CANONICAL}, ("pressure:save_state",),
          ("pressure", "page", "patrol"), 600, rule="the machine's load level, one round"),
    Store("pressurehist", "S/pressure-history.jsonl", {"pressure.sample": CANONICAL}, ("pressure:history_add",),
          ("page",), rule="24 h of load samples"),
    Store("pressurequeue", "S/pressure-queue.json", {"pressure.queue_item": CANONICAL}, ("pressure:queue_change",),
          ("pressure", "page"), rule="heavy starts waiting for the load to drop"),
    Store("pressureplan", "S/pressure-plan*.json", {"machine.resource": DERIVED}, ("pressure:plan_targets",),
          ("pressure",), rule="the load round's scan; the same scanner as janitor-plan"),
    Store("pressurelock", "S/pressure*.lock", {"pressure.level": LOCK}, ("pressure:locked", "pressure:queue_change")),
    Store("pressureoff", "S/pressure-disabled", {"pressure.disabled": CANONICAL}, ("pressure:cmd_uninstall",),
          ("pressure",)),
    Store("pressureout", "S/pressure.log", {"pressure.output": CANONICAL}, ("pressure:log",), ("nobody",)),
    Store("reaphard", "S/reap-hard.jsonl", {"reap_hard.pass": CANONICAL}, ("reap_hard:log",), ("brief", "page")),
    Store("reaphardout", "S/reap-hard.log", {"reap_hard.pass": LOG}, ("ext:launchd",), ("nobody",),
          rule="launchd stdout; the jsonl rows again"),
    Store("reaphardlock", "S/reap-hard.lock", {"reap_hard.pass": LOCK}, ("reap_hard:locked",)),
    Store("preview", "S/preview-watch.json", {"preview.link": CANONICAL}, ("preview_watch:save_state",),
          ("janitor",)),
    Store("previewaux", "S/preview-watch.*", {"preview.link": LOG}, ("preview_watch:log", "preview_watch:locked"),
          ("nobody",), rule="its log and lock"),
    Store("previewbad", "S/preview-watch.json.corrupt-*", {"preview.link": BACKUP}, ("preview_watch:load_state",),
          ("nobody",), rule="a state that did not parse, set aside"),
    Store("shareout", "S/share-server.log", {"share.output": CANONICAL}, ("ext:launchd",), ("nobody",)),
    Store("sharestate", "S/share-caddy/*", {"share.server": CANONICAL}, ("ext:launchd",), ("nobody",)),
    Store("prsstate", "S/prs/*", {"watchprs.acted": CANONICAL}, ("prs:commit", "prs:deliver", "prs:result",
          "prs:round_steps", "prs:verify", "prs:main", "prs:log", "notify:main"), ("watchprs", "brief"),
          feature="prs", rule="what watch-prs acted on, sent and is about to send"),
    Store("mentionsstate", "S/mentions/*.json", {"mentions.seen": CANONICAL},
          ("mentions:annotate", "mentions:commit", "mentions:plan"), ("mentions", "watchprs"), feature="mentions"),
    Store("securefill", "X/secure-fill/log.jsonl", {"securefill.use": CANONICAL},
          ("secure_fill:append_log", "secure_fill:main"), ("nobody",), rule="which origin got a secret, never the value"),
    Store("orphans", "S/brief-format.json", {"brief.format": REMOVED}, (), ("nobody",),
          rule="no writer and no reader on main"),
    Store("oldlog", "S/orch-dash.log*", {"dash.old_output": REMOVED}, (), ("nobody",), rule="stopped; now P/.dash.log"),
    # -- P: programs
    Store("program", "P/*/program.json", {"program.config": CANONICAL}, ("prog:Program.update", "prog:cmd_init"),
          ("programview", "fleet", "watchprs", "reorient"),
          rule="repo, Run, predicate, merge policy; stages (`orch set`, cmd_set through Program.update), which "
               "stage_auto reads in the collector and, for a lead not yet in stages-auto, in the hook"),
    Store("progledger", "P/*/ledger.jsonl", {"program.event": CANONICAL}, ("prog:Program.append", "prog:write_backfill"),
          ("programview", "fleet", "reflect", "skillspage"), rule="one ledger per program"),
    Store("progledgerlock", "P/*/ledger.lock", {"program.event": LOCK}, ("prog:Program.locked",)),
    Store("progwait", "P/*/*.json", {"program.wait_memo": CANONICAL},
          ("prog:cmd_wait", "prog:print_stuck", "prog:new_stalls", "prog:new_leftovers"), ("programview",),
          rule="`orch wait` memos: self-check, stuck, stalls and leftovers seen"),
    Store("progstatus", "P/*/status-self-check.json", {"program.status_memo": CANONICAL}, ("prog:cmd_status",),
          ("programview",), rule="`orch status` TAKEN OVER memo: when each self-check was asked, apart from `orch wait`'s"),
    Store("progbackup", "P/*/ledger.jsonl.bak-*", {"program.event": BACKUP}, ("prog:write_backfill",), ("nobody",)),
    Store("progdash", "P/*/dashboard/*", {"program.event": DERIVED, "pr.state": DERIVED}, ("dash:_merge", "dash:fetch_stages",
          "dash:collect_usage", "dash:cmd_note"), ("programview",), rule="one collect per program: state, PR rows, tree, history"),
    Store("progdecisions", "P/*/decisions.tsv", {"program.judgement": CANONICAL}, ("ext:leads",), ("reflect",),
          rule="show-me-your-work log; judgement record, not questions"),
    Store("prog_hand", "P/*/*", {"program.hand_file": HAND}, ("ext:leads",), ("nobody",),
          rule="a lead's own files (reports, plans, evidence): no code reads them"),
    Store("progreflect", "P/*/reflect/*.md", {"program.event": DERIVED}, ("ext:leads",), ("reflect",)),
    Store("dashmeta", "P/.dash.*", {"dash.server": CANONICAL}, ("dash:serve", "dash:ensure"), ("programview",),
          rule="the serving process, its lock and log"),
    Store("landlocks", "P/_locks/*.lock", {"program.event": LOCK, "resource.hold": CANONICAL},
          ("prog:ExclusiveLock.take", "holds:hold", "holds:_locked"), ("programview", "page"),
          rule="landing lanes and `orch hold` resources"),
    Store("holdslog", "P/_locks/holds.jsonl", {"resource.hold": LOG}, ("holds:_log",), ("page",)),
    Store("heavy", "P/_heavy/*", {"heavy.slot": CANONICAL}, ("prog:cmd_heavy", "prog:heavy_run"), ("pressure",),
          rule="`orch heavy` slots, removed when the command ends"),
    Store("signals", "P/_skill-usage/ledger.jsonl", {"skill.signal": CANONICAL}, ("skill_usage:refresh",),
          ("reflect", "skillspage"), rule="one row per flagged editable skill per week"),
    Store("signalsbak", "P/_skill-usage/ledger.jsonl.bak-*", {"skill.signal": BACKUP}, ("ext:human",), ("nobody",)),
    Store("reflectstate", "P/_standing/reflect/*", {"reflect.cursor": CANONICAL},
          ("orchestrator_feedback:advance", "orchestrator_feedback:digest", "ext:leads"), ("reflect",),
          rule="reflect cursors, waiting list and packs"),
    Store("standing", "P/_standing/*", {"standing.hand_file": HAND}, ("ext:leads",), ("nobody",)),
    Store("archive", "P/_archive/*", {"program.event": BACKUP}, ("reap:archive_program",), ("nobody",)),
    Store("gitcache", "H/.cache/agent-skills/orchestrate/git/*", {"repo.git": CACHE}, ("prog:local_diff",),
          ("programview",)),
]

# Writes the scan finds that are not agent-skills' data: the user's own files a command was pointed at, temp
# files, repo outputs, launch agents. module:qualname -> why.
OUTSIDE = {
    "dash_demo:*": "the demo writes an invented state into its own temporary root",
    "build:<module>": "builds the skill catalog page in the repo",
    "pstack-sync:merge3": "vendors a pinned upstream into the repo",
    "pstack-sync:main": "vendors a pinned upstream into the repo",
    "shots:upload": "a PR screenshot to the path its caller names",
    "measure:main": "a report to the --out path",
    "store:main": "`orch db backup FILE`: a copy of the DB to the path the owner names",
    "ui_measure:main": "a measurement to the --out path",
    "skill_tokens:measure_ref": "a tar in its own temporary directory",
    "reap:trash": "moves a reaped target to the trash",
    "orchestrator_feedback:trash": "moves an old digest to the trash",
    "reap:reap_state_file": "rotates sync.log and moves old session files to the trash",
    "bootstrap:scheduler": "a launch agent plist",
    "dash:install_agent": "a launch agent plist",
    "pressure:cmd_install": "a launch agent plist",
    "preview_watch:cmd_install": "a launch agent plist",
    "reap_hard:cmd_install": "a launch agent plist",
    "share_server:cmd_install": "a launch agent plist and its backup",
    "config_emit:backup": "a backup of the settings file it changes",
    "automode_rule:backup": "a backup of the settings file it changes",
}

# Facts with two canonical stores on main today. Each is a known defect a later PR removes; a new one fails check().
# fact -> (the stores allowed to be canonical for it, why). A third store, or a different pair, fails check().
DUAL_CANONICAL = {
    "work.unit": (("orca", "board"), "board doing lines and leads re-type Orca's cards and leads by hand "
                                     "(SSOT design §5 A, PR 4b)"),
    "card.linked_pr": (("orca", "board"), "board rows' --pr re-types a card's PR by hand (SSOT design §5 C, PR 4b)"),
    "program.config": (("notes", "program"), "the program note repeats program.json and `orch set` mirrors it by "
                                            "hand (SSOT design §5 I, PR 8)"),
}
TEMP = ("*.tmp", "*.tmp.*", ".*.tmp")
# The directory name each root has by default, the parent a write's path names for a file at the root's top.
ROOT_DIRS = {"D": {"dashboard"}, "S": {"agent-skills"}, "P": {"programs"}, "C": {".claude"}, "K": {"agent-skills"},
             "X": {"state"}, "H": set()}


def _base(pattern):
    return pattern.rstrip("/").rsplit("/", 1)[-1]


def _either(a, b):
    return fnmatch.fnmatchcase(a, b) or fnmatch.fnmatchcase(b, a)


def _matches(target, store):
    """A write's `parent/name` against a store's pattern: the name always, the parent when the write names one."""
    if store.path.startswith(("ext:", "db:")):
        return False
    t = target.split("/")
    root, _, rel = store.path.partition("/")
    s = rel.split("/")
    if not _either(t[-1], s[-1]):
        return False
    if len(t) < 2 or t[0] in ("~", "*", ""):
        return True
    if len(s) >= 2:
        return _either(t[0], s[-2])
    return t[0] in ROOT_DIRS.get(root, set())


def _broad(store):
    return re.fullmatch(r"\*(\.\w+)?", _base(store.path)) is not None


def _outside(writer):
    return any(fnmatch.fnmatchcase(writer, k) for k in OUTSIDE)


def check(stores=None, writes=None, dual=None, modules=None, db=None):
    """Violations: [{kind, key, detail}]. kind: registry (the map itself is wrong), dual (two canonical stores for a
    fact), unregistered (a write to a file no store names), writer (a write from a function its store does not list),
    db (db_violations). db: precomputed (kind, key, detail) rows; the scripts are scanned when writes is None too."""
    stores = STORES if stores is None else stores
    if writes is not None and db is None:
        db = []
    writes = scan() if writes is None else writes
    dual = DUAL_CANONICAL if dual is None else dual
    out = []
    bad = lambda kind, key, detail: out.append({"kind": kind, "key": key, "detail": detail})  # noqa: E731

    ids = [s.id for s in stores]
    for i in sorted({i for i in ids if ids.count(i) > 1}):
        bad("registry", i, "store id used twice")
    funcs = modules if modules is not None else {f"{w.module}:{w.func}" for w in writes} | _defined()
    for s in stores:
        root = s.path.split("/", 1)[0]
        if not s.path.startswith(("ext:", "db:")) and root not in ROOTS:
            bad("registry", s.id, f"unknown root {root!r}")
        if not s.facts:
            bad("registry", s.id, "no fact")
        for f, role in s.facts.items():
            if role not in ROLES:
                bad("registry", s.id, f"{f}: unknown role {role!r}")
        for r in s.readers:
            if r not in CONSUMERS:
                bad("registry", s.id, f"unknown reader {r!r}")
        for w in s.writers:
            if not w.startswith("ext:") and w not in funcs:
                bad("registry", s.id, f"writer {w} is not a function in the scripts")

    canon = {}
    for s in stores:
        for f, role in s.facts.items():
            canon.setdefault(f, [])
            if role in (CANONICAL, HAND):  # a hand file is its own source
                canon[f].append(s.id)
    for f, where in sorted(canon.items()):
        if len(where) > 1 and set(where) != set(dual.get(f, ((),))[0]):
            bad("dual", f, f"canonical in {', '.join(where)}")
        elif not where and any(s.facts.get(f) != REMOVED for s in stores if f in s.facts):
            bad("dual", f, "no canonical store")
    for f in sorted(set(dual) - {f for f, w in canon.items() if len(w) > 1}):
        bad("registry", f, "listed in DUAL_CANONICAL but has one canonical store now: drop it from the list")

    # A writer the registry names must write that store where the scan can read the path.
    by_writer = {}
    for w in writes:
        by_writer.setdefault(w.writer, []).append(w)
    for s in stores:
        for name in s.writers:
            sites = by_writer.get(name)
            if sites and all(w.targets for w in sites) and not any(
                    _matches(t, s) for w in sites for t in w.targets if not _temp(t)):
                bad("registry", s.id, f"writer {name} writes no file this store names")

    for w in writes:
        if _outside(w.writer):
            continue
        names = [t for t in w.targets if not _temp(t)]
        if w.targets and not names:
            continue  # a temporary beside the file; the rename that follows is the write
        if not names:
            if not any(w.writer in s.writers for s in stores):
                bad("writer", w.writer, f"line {w.line}: writes a path the code does not name, and no store lists it")
            continue
        for t in names:
            hit = [s for s in stores if _matches(t, s)]
            # A store named only by a wildcard (`P/*/*`, `S/prs/*`) names a file through its writers: a function it
            # does not list writes a file nobody registered.
            if not hit or (all(_broad(s) for s in hit) and not any(w.writer in s.writers for s in hit)):
                bad("unregistered", t, f"{w.writer} line {w.line}: no store names this file")
            elif not any(w.writer in s.writers for s in hit):
                bad("writer", w.writer, f"line {w.line}: writes {t} ({', '.join(s.id for s in hit)}) without being "
                                        "its writer")

    for kind, key, detail in (db_violations(stores) if db is None else db):
        bad(kind, key, detail)
    return out


# Modules allowed to import sqlite3, and why. agent-skills.db is store.py's alone.
DB_CONNECT = {"store": "agent-skills.db", "orca_settings": "Orca's own database, read-only (mode=ro)"}
# UPDATE needs its SET so that prose ("Update the checkout") is not read as SQL; keywords in any case.
# A table may carry a schema (main.decision) and any of SQLite's quotes ("x", [x], `x`).
_TABLE = r"\s+(?:[\"\[`]?\w+[\"\]`]?\s*\.\s*)?[\"\[`]?(\w+)[\"\]`]?"
SQL_WRITE = re.compile(rf"(?<!DO )\b(?:(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO{_TABLE}|DELETE\s+FROM{_TABLE}"
                       rf"|UPDATE(?:\s+OR\s+\w+)?{_TABLE}\s+SET\b)", re.I)


def _strings(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


def _importer(func):
    """Whether a call's target is __import__ or some import_module (importlib's, under any name)."""
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name in ("__import__", "import_module")


def db_scan(paths=None):
    """(connects, sql writes): [(module, line)] of every import of sqlite3 (under any name, or through __import__ or
    importlib), since a module that cannot import it cannot connect; and [(module:qualname, table, line)] of every
    SQL statement in store.py that changes a table."""
    connects, sql = [], []
    for p, name in _names(paths if paths is not None else sources()).items():
        tree = ast.parse(p.read_text(), str(p))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "sqlite3" for a in n.names) or \
                    isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "sqlite3" or \
                    isinstance(n, ast.Call) and _importer(n.func) and \
                    any(isinstance(a, ast.Constant) and str(a.value).split(".")[0] == "sqlite3"
                        for a in [*n.args, *(k.value for k in n.keywords)]):
                connects.append((name, n.lineno))
        if name != "store":
            continue
        for qual, fn in _Module(name, tree).funcs.items():
            for s in _strings(fn):
                for m in SQL_WRITE.finditer(s):
                    sql.append((f"store:{qual}", next(g for g in m.groups() if g).lower(), fn.lineno))
    return connects, sql


def db_violations(stores, scanned=None):
    """kind db: a connection outside DB_CONNECT, or a store.py function changing a table whose db: store does not
    list it as a writer (so going through store.py is not a licence to change any table from anywhere)."""
    connects, sql = db_scan() if scanned is None else scanned
    out = [("db", f"{m}:{line}", "sqlite3 imported outside store.py: agent-skills.db goes through store.py only")
           for m, line in connects if m not in DB_CONNECT]
    tables = {}
    for s in stores:
        if s.path.startswith("db:"):
            tables.setdefault(s.path[3:].split(".")[0], set()).update(s.writers)
    for writer, table, line in sql:
        if table not in tables:
            out.append(("db", f"db:{table}", f"{writer} line {line}: no store names this table"))
        elif writer not in tables[table]:
            out.append(("db", writer, f"line {line}: changes table {table} without being its writer"))
    return out


def _defined():
    """Every module:qualname in the scripts, so a writer the scan saw no write in still counts as a function."""
    out = set()
    for p, name in _names(sources()).items():
        tree = ast.parse(p.read_text(), str(p))
        out |= {f"{name}:{q}" for q in _Module(name, tree).funcs}
    return out


def _owner(p):
    """The skill a script belongs to (skills/<skill>/scripts/x.py), else the directory it sits in."""
    parts = p.parent.parts
    return parts[-2] if len(parts) >= 3 and parts[-1] == "scripts" and parts[-3] == "skills" else parts[-1]


def _names(paths):
    """Each source's module name: its stem, or `<owner>/<stem>` for every source sharing the stem with another."""
    paths = [pathlib.Path(p) for p in paths]
    stems = [p.stem for p in paths]
    return {p: p.stem if stems.count(p.stem) == 1 else f"{_owner(p)}/{p.stem}" for p in paths}


# ------------------------------------------------------------ write scan

@dataclasses.dataclass(frozen=True)
class Write:
    module: str
    func: str          # qualname of the outermost def holding the write; "<module>" at top level
    line: int
    targets: tuple     # file-name patterns the target resolves to ("*" for a formatted part); () when unresolved

    @property
    def writer(self):
        return f"{self.module}:{self.func}"


WRITE_METHODS = {"write_text", "write_bytes", "touch"}
OPEN_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"}


def _mode(call, pos):
    m = call.args[pos] if len(call.args) > pos else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return m.value if isinstance(m, ast.Constant) and isinstance(m.value, str) else "r"


def _writes_mode(mode):
    return any(c in mode for c in "wax+")


class _Module:
    """One source file: its top-level constants, functions and classes, for resolving a write's target."""

    def __init__(self, name, tree, home=None):
        self.name, self.tree, self.home = name, tree, home
        self.stem = name.rsplit("/", 1)[-1]
        self.consts, self.funcs, self.attrs = {}, {}, {}
        self.packaged = {}  # `from lib import danger`: danger -> "lib"
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for a in node.names:
                    self.packaged[a.asname or a.name] = node.module.replace(".", "/")
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.consts[t.id] = node.value
                    elif isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple):  # A, B = x, y
                        for e, v in zip(t.elts, node.value.elts):
                            if isinstance(e, ast.Name):
                                self.consts[e.id] = v
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node.name] = node
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.funcs[f"{node.name}.{sub.name}"] = sub
                        returns = [r for r in ast.walk(sub) if isinstance(r, ast.Return) and r.value is not None]
                        if any(isinstance(d, ast.Name) and d.id == "property" for d in sub.decorator_list) and returns:
                            self.attrs[sub.name] = returns[0].value
                        for a in ast.walk(sub):
                            if isinstance(a, ast.Assign):
                                for t in a.targets:
                                    if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                            and t.value.id == "self"):
                                        self.attrs.setdefault(t.attr, a.value)


UNKNOWN = ast.Constant(value=None)


def _tail(path):
    """At most the last two components: the scan matches a file by its name and the directory it sits in."""
    return "/".join(path.rstrip("/").split("/")[-2:])


def _temp(path):
    return any(fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], p) for p in TEMP)


def _params(fn):
    a = fn.args
    return [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]


def _default(fn, i):
    a = fn.args
    pos = a.posonlyargs + a.args
    if i < len(pos):
        k = i - (len(pos) - len(a.defaults))
        return a.defaults[k] if k >= 0 else None
    return a.kw_defaults[i - len(pos)]


class _Scan:
    def __init__(self, modules):
        self.mods = modules  # name -> _Module
        self.near = {(m.home, m.stem): m for m in modules.values()}
        self.sinks = {}      # (module, qualname) -> indexes of the parameters it writes to
        self.kw_sinks = set()  # qualnames writing each **kwargs key as a file name (Fleet.save)

    # -- resolving a target expression to file-name patterns

    def resolve(self, mod, fn, expr, seen=()):
        """What expr names: path patterns (str, at most `parent/name`) and the enclosing function's parameters
        (int index). () when the code does not say."""
        if id(expr) in seen:
            return ()
        seen = seen + (id(expr),)
        r = lambda e: self.resolve(mod, fn, e, seen)  # noqa: E731
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return (_tail(expr.value),)
        if isinstance(expr, ast.JoinedStr):
            head, rest = expr.values[0], expr.values[1:]
            if isinstance(head, ast.FormattedValue) and head.conversion == -1 and head.format_spec is None and rest \
                    and all(isinstance(v, ast.Constant) for v in rest):
                tail = "".join(v.value for v in rest)
                renamed = [x for x in r(head.value) if isinstance(x, tuple)]  # f"{p.name}.1" is p.name + ".1"
                if renamed:
                    return tuple((i, s + tail) for i, s in renamed)
            return (_tail("".join(v.value if isinstance(v, ast.Constant) else "*" for v in expr.values)),)
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            right = r(expr.right)
            left = [x for x in r(expr.left) if isinstance(x, str)]
            if not left:
                return right
            return tuple(dict.fromkeys(x if not isinstance(x, str) else _tail(f"{lf}/{x}") for x in right for lf in left))
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):  # path.name + ".lock"
            left = r(expr.left)
            right = [x for x in r(expr.right) if isinstance(x, str)]
            return tuple(dict.fromkeys(_tail(a + b) if isinstance(a, str) else (a[0], a[1] + b)
                                       for a in left if isinstance(a, (str, tuple)) for b in right))
        if isinstance(expr, ast.Name):
            local = self._local(fn, expr.id, expr.lineno)
            got = r(local) if local is not None else ()
            if got:
                return got
            if fn is not None and expr.id in _params(fn):
                return (_params(fn).index(expr.id),)
            if local is None and expr.id in mod.consts:
                return self.resolve(mod, None, mod.consts[expr.id], seen)
            return ()
        if isinstance(expr, ast.Attribute):
            if expr.attr == "name":  # a Path's file name; a parameter's name is (index, "") until a suffix is added
                return tuple(dict.fromkeys(x.rsplit("/", 1)[-1] if isinstance(x, str) else (x, "") if isinstance(x, int)
                                           else x for x in r(expr.value)))
            if isinstance(expr.value, ast.Name) and expr.value.id == "self" and expr.attr in mod.attrs:
                return self.resolve(mod, None, mod.attrs[expr.attr], seen)
            if isinstance(expr.value, ast.Name) and (other := self.imported(mod, expr.value.id)):
                if expr.attr in other.consts:
                    return self.resolve(other, None, other.consts[expr.attr], seen)
            return ()
        if isinstance(expr, ast.Call):
            f = expr.func
            if isinstance(f, ast.Attribute) and f.attr in ("with_name", "with_suffix") and expr.args:
                base = r(f.value)
                params = tuple(x for x in base if isinstance(x, int))
                arg = tuple(x for x in r(expr.args[0]) if isinstance(x, str))
                parent = [b.rsplit("/", 1)[0] + "/" for b in base if isinstance(b, str) and "/" in b] or [""]
                if f.attr == "with_name":
                    # `p.with_name(p.name + ".1")` on a parameter: the caller's path with ".1" added.
                    # A base already renamed in a wrapper is (index, suffix) too; its name carries that suffix on.
                    of = {x if isinstance(x, int) else x[0] for x in base if isinstance(x, (int, tuple))}
                    renamed = tuple(x for x in r(expr.args[0]) if isinstance(x, tuple) and x[0] in of and x[1]
                                    and not _temp("x" + x[1]))
                    if renamed:
                        return renamed
                    if arg and not all(_temp(a) for a in arg):
                        return tuple(_tail(p + a) for a in arg for p in parent)
                    return params or tuple(_tail(p + a) for a in arg for p in parent)
                if params:
                    # A temporary beside the parameter's file is the parameter's write; another suffix is unknown.
                    return params if arg and all(_temp("x" + a) for a in arg) else ()
                if not arg:
                    return ()
                return tuple(b.rsplit(".", 1)[0] + t for b in base if isinstance(b, str) for t in arg)
            if isinstance(f, ast.Attribute) and f.attr == "get" and isinstance(f.value, ast.Attribute) \
                    and f.value.attr == "environ":
                return r(expr.args[1]) if len(expr.args) > 1 else ()  # os.environ.get(KEY, default)
            if isinstance(f, ast.Attribute) and f.attr in ("expanduser", "resolve", "absolute"):
                return r(f.value)
            if (isinstance(f, ast.Attribute) and f.attr == "Path") or (isinstance(f, ast.Name) and f.id == "Path"):
                return r(expr.args[0]) if expr.args else ()
            callee = self._callee(mod, f)
            if callee is not None:
                m2, fn2 = callee
                returns = [x.value for x in ast.walk(fn2) if isinstance(x, ast.Return) and x.value is not None]
                if len(returns) == 1:
                    return tuple(x for x in self.resolve(m2, fn2, returns[0], seen) if isinstance(x, str))
            return ()
        if isinstance(expr, (ast.IfExp, ast.BoolOp)):
            # `path or DEFAULT`: both, the caller's path resolved at the caller and the default here.
            parts = [r(e) for e in ([expr.body, expr.orelse] if isinstance(expr, ast.IfExp) else expr.values)]
            return tuple(dict.fromkeys(x for p in parts for x in p))
        return ()

    @staticmethod
    def _local(fn, name, line):
        """The value of the last assignment to name in fn on a line before `line` (a branch is not followed)."""
        if fn is None:
            return None
        best = None
        for node in ast.walk(fn):
            values = []
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        values.append(node.value)
                    if isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple):
                        values += [v for e, v in zip(t.elts, node.value.elts) if isinstance(e, ast.Name) and e.id == name]
            if isinstance(node, (ast.With, ast.AsyncWith)):
                values += [ast.Constant(value=None, lineno=node.lineno) for item in node.items
                           if isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == name]
            for v in values:
                if node.lineno < line and (best is None or node.lineno >= best[0]):
                    best = (node.lineno, v)
        return best[1] if best else None

    def _callee(self, mod, f):
        """(module, def) for a call to a function this scan knows: bare name, module.name or self.name."""
        if isinstance(f, ast.Name) and f.id in mod.funcs:
            return mod, mod.funcs[f.id]
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            other = self.imported(mod, f.value.id)
            if other and f.attr in other.funcs:
                return other, other.funcs[f.attr]
            if f.value.id == "self":
                for q, d in mod.funcs.items():
                    if q.endswith("." + f.attr):
                        return mod, d
        return None

    def imported(self, mod, name):
        """The module `name` reaches from mod: for `from <pkg> import name`, name in the nearest <pkg> directory at or
        above mod's own; else the one in mod's own directory first, as Python's import does; else the only source
        with that name."""
        if (pkg := mod.packaged.get(name)) and mod.home is not None:
            for up in (mod.home, *mod.home.parents):
                if (found := self.near.get((up / pkg, name))) is not None:
                    return found
        return self.near.get((mod.home, name)) or self.mods.get(name)

    def _callee_key(self, mod, f):
        c = self._callee(mod, f)
        if c is None:
            return None
        m2, d = c
        return m2.name, next(q for q, x in m2.funcs.items() if x is d)

    # -- finding writes

    def targets_of(self, mod, call):
        """The path expressions a call writes to; [] when it writes nothing. UNKNOWN stands for a path the call
        passes in a way the scan cannot read (*args, a missing argument)."""
        f = call.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
        recv = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
        arg = lambda i, *kw: call.args[i] if len(call.args) > i else next(  # noqa: E731
            (k.value for k in call.keywords if k.arg in kw), None)
        if name in WRITE_METHODS and isinstance(f, ast.Attribute):
            return [f.value]
        if name == "open" and recv == "os":
            flags = {n.attr for n in ast.walk(arg(1, "flags") or ast.Constant(0)) if isinstance(n, ast.Attribute)}
            return [arg(0, "path")] if flags & OPEN_FLAGS and arg(0, "path") is not None else []
        if name == "open" and (isinstance(f, ast.Name) or recv in ("io", "codecs", "builtins")):
            mode = arg(1, "mode")
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and _writes_mode(mode.value):
                return [arg(0, "file", "filename") or UNKNOWN]
            return []
        if name == "open" and isinstance(f, ast.Attribute):  # Path.open(mode)
            mode = arg(0, "mode")
            return [f.value] if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and _writes_mode(mode.value) else []
        if name in ("replace", "rename") and isinstance(f, ast.Attribute):
            if recv == "os":
                dst = arg(1, "dst")
                return [dst] if dst is not None else [UNKNOWN]
            if len(call.args) == 1 and not call.keywords:  # Path.replace(target); str.replace takes two
                return [call.args[0]]
        if recv == "shutil" and name in ("copy", "copy2", "copyfile", "copytree", "move"):
            return [arg(1, "dst") or UNKNOWN]
        key = self._callee_key(mod, f)
        if key not in self.sinks:
            return []
        self.called.add(key)
        fn = self.mods[key[0]].funcs[key[1]]
        params = _params(fn)
        # A method called on an object gets that object as its first parameter.
        shift = 1 if "." in key[1] and params[:1] in (["self"], ["cls"]) and isinstance(f, ast.Attribute) else 0
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            return [UNKNOWN]
        out = []
        for i, suffix in sorted(self.sinks[key]):
            j = i - shift
            given = call.args[j] if 0 <= j < len(call.args) else next(
                (k.value for k in call.keywords if k.arg == params[i]), None)
            if given is not None and suffix:  # the sink renames it: given.with_name(given.name + suffix)
                given = ast.copy_location(ast.Call(
                    func=ast.Attribute(value=given, attr="with_name", ctx=ast.Load()),
                    args=[ast.BinOp(left=ast.Attribute(value=given, attr="name", ctx=ast.Load()), op=ast.Add(),
                                    right=ast.Constant(value=suffix))], keywords=[]), given)
            if given is not None:
                out.append(given)
            elif _default(fn, i) is None:
                out.append(UNKNOWN)
            # else: the default, which the writer's own site already records
        return out

    def kw_targets(self, call):
        f = call.func
        if isinstance(f, ast.Attribute) and any(q.endswith("." + f.attr) or q == f.attr for q in self.kw_sinks):
            for k in call.keywords:
                if k.arg is None and isinstance(k.value, ast.Dict):
                    return tuple(x.value for x in k.value.keys if isinstance(x, ast.Constant))
        return None

    def writes(self):
        # A function that writes to its own parameter passes the write to its callers; repeat until nothing changes.
        while True:
            before = ({k: set(v) for k, v in self.sinks.items()}, set(self.kw_sinks))
            self.called, self.defaulted = set(), set()
            out = list(self._walk())
            if (self.sinks, self.kw_sinks) == before:
                break
        # A writer only ever called indirectly (passed as a function) keeps its write, as one the code does not name.
        for m, q in sorted(set(self.sinks) - self.called - self.defaulted):
            fn = self.mods[m].funcs.get(q)
            out.append(Write(m, q, fn.lineno if fn else 0, ()))
        return out

    def _walk(self):
        for mod in self.mods.values():
            for qual, fn in [("<module>", None)] + list(mod.funcs.items()):
                nodes = mod.tree.body if fn is None else [fn]
                for top in nodes:
                    if fn is None and isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue
                    for node in ast.walk(top):
                        if not isinstance(node, ast.Call):
                            continue
                        kws = self.kw_targets(node)
                        if kws:
                            yield Write(mod.name, qual, node.lineno, kws)
                            continue
                        for expr in self.targets_of(mod, node):
                            yield from self._site(mod, qual, fn, node, expr)

    def _site(self, mod, qual, fn, node, expr):
        got = self.resolve(mod, fn, expr)
        names = tuple(x for x in got if isinstance(x, str))
        params = [(x, "") if isinstance(x, int) else x for x in got if isinstance(x, (int, tuple))]
        if params:
            # The function writes where its caller says: each call becomes a write site. A parameter's default is
            # written here when a caller passes none.
            self.sinks.setdefault((mod.name, qual), set()).update(params)
            for i, suffix in params:
                default = _default(fn, i)
                if default is not None:
                    names += tuple(_tail(x + suffix) for x in self.resolve(mod, None, default) if isinstance(x, str))
            if names:
                self.defaulted.add((mod.name, qual))
                yield Write(mod.name, qual, node.lineno, tuple(dict.fromkeys(names)))
            return
        if not got and fn is not None and fn.args.kwarg and isinstance(expr, ast.BinOp) and \
                isinstance(expr.right, ast.Name) and self._is_kwarg_key(fn, expr.right.id):
            self.kw_sinks.add(qual)
            return
        yield Write(mod.name, qual, node.lineno, names)

    @staticmethod
    def _is_kwarg_key(fn, name):
        for node in ast.walk(fn):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Call) and isinstance(node.iter.func, ast.Attribute) \
                    and isinstance(node.iter.func.value, ast.Name) and node.iter.func.value.id == fn.args.kwarg.arg:
                tgt = node.target
                names = [tgt.id] if isinstance(tgt, ast.Name) else [e.id for e in getattr(tgt, "elts", []) if isinstance(e, ast.Name)]
                if name in names:
                    return True
        return False


def sources(repo=REPO):
    return sorted(p for pat in SOURCES for p in repo.glob(pat) if not p.name.startswith("test_"))


def scan(paths=None):
    """Every write the scripts make: one Write per call site, its target resolved where the code says it."""
    mods = {}
    for p, name in _names(paths if paths is not None else sources()).items():
        mods[name] = _Module(name, ast.parse(p.read_text(), str(p)), p.parent)
    return _Scan(mods).writes()


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["writes"]:
        for w in scan():
            print(f"{w.writer}:{w.line}\t{','.join(w.targets) or '?'}")
    elif a[:1] == ["check"]:
        found = check()
        for v in found:
            print(f"{v['kind']}\t{v['key']}\t{v['detail']}")
        sys.exit(1 if found else 0)
    else:
        sys.exit(__doc__)
