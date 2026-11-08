#!/usr/bin/env python3
"""Program bookkeeping for orchestrate: the numbers a lead decides from.

Usage: orch <command> <slug> [options]

  init <slug> --repo OWNER/NAME --run RUN_ID [--tracker-project NAME] [--tracker linear|jira]
              [--predicate ID,ID,...] [--merge-policy autonomous|human-gate] [--ceiling 6]
              [--deadline ISO8601] [--note NOTES_PATH] [--final-check TEXT]
  set <slug> <merge_policy|ceiling|deadline|predicate|exclusive_paths|visual_paths|note|final_check|stages> VALUE
                                     mirror a change made in the program note; stages: the deploy steps
                                     the stage table waits on (dev,prod), unset: dev and prod are 해당 없음
  status <slug>                      predicate, flow, growth and the next move, from live sources
  record <slug> <event> [--ticket T] [--pr N] [--sha S] [--class K] [--role R] [--note TEXT]
                                     `record spawned --role guardian --note <dispatchId>` for a standing
                                     role; roles do not count against the cap
                                     `record reprioritized --pr N --class urgent` moves a PR in the land order
                                     main_green / main_red need --sha of a landed merge commit; main_green also takes
                                     a full sha another program landed, while main is red, if it is on the base
                                     branch and descends from every red landing (no landing is counted)
  record <slug> signal --kind human_correction|brief_gap|stall|tooling --evidence PTR [--ticket T] [--note TEXT]
                                     a lesson candidate for reflect at Close: a human correction, a
                                     question the brief should have answered, a stall the process
                                     caused, a skill or script defect. PTR points at the proof (message
                                     id, PR comment URL, transcript path:line); nothing is copied in
  verdict <slug> --pr N --sha REVIEWED_HEAD --source WHO [--reviewer NAME] [--evidence LINK_OR_SHA]
                 [--result pass|fail] [--note TEXT]
                                     the row keeps the PR's author sessions, the reviewer, the evidence and the
                                     recorder. A pass is stored only when all four are named and neither the
                                     reviewer, the --source reviewer nor the recorder is the author; otherwise it
                                     is refused and nothing is written. An orchestrator records a review someone
                                     else ran with --reviewer <who ran it>.
  gate <slug> --pr N [--parent TASK_ID]
                                     human-gate: open an Orca decision gate "land PR N?" on a
                                     lead-owned landing Task; the user resolves it in Orca
  dep <slug> --ticket A --after B[,C]
                                     A cannot land before B and C: a land-after edge (A stacked on
                                     B's branch), or one found after dispatch. Start-after edges are
                                     Orca task deps (task-create --deps), read from the Run
  queue <slug> [--json]              the land order and why each PR is not moving
  overlap <slug> --table [--max-open N]
                                     the files two or more open PRs of the repo change (any program), the PRs
                                     in the order to land them (ready first, then by number; advice only), and an
                                     OVER line for a file with more than N open PRs
  overlap <slug> [--paths P,P]       open program PRs with no land-after relation that change one file
                                     (or one exclusive_paths glob): stack the later on the earlier. With
                                     --paths, the open PRs a unit not started yet would collide with.
                                     Also warns, one line per file, where they share a file with an open
                                     PR another program on the same repo has in its ledger (OVERLAP
                                     <other-slug> …, no command; UNKNOWN when unreadable).
                                     status prints the same OVERLAP lines
  land <slug> --pr N [--class main-fix|gate|urgent|normal] [--wait-minutes M] [--onto-lower]
                                     the one way a program PR reaches its base. Normal lane: lands
                                     as soon as it is ready (verdict patch-id, CI green at head, no
                                     conflict, deps landed, main not red), behind or not, unless the
                                     base changed files it also changes. Exclusive lane (migrations,
                                     CI, Dockerfile, compose, exclusive_paths, gates): one PR per
                                     base at a time, on the latest base, in land order. A PR
                                     touching visual_paths needs an image in its test-results
                                     comment (or a person's no-visual-change label), and every
                                     failed CI run of its branch a line there naming the run id
                                     and what resolved it. Exit 0
                                     landed, 2 yield (nothing to do yet), 3 act (fix, update or
                                     re-review now), 1 refused
                                     With orchestrate.land_executor on for the repo it merges nothing: it
                                     records a request for the current head and waits for the land
                                     executor's result, with the same exit codes (land_exec.py)
  land-exec round|queue|setup|status
                                     the land executor (land_exec.py); round runs from the land-executor
                                     automation's precheck
  land-check <slug> --pr N [--main-fix]
                                     readiness only, no merge (lead diagnostics)
  landed <slug> --pr N               record a merge made outside `land` and print the main-CI watch
  backfill <slug> --since ISO8601 [--dry-run]
                                     a program registered mid-flight: add the PRs merged from --since
                                     (when its work began) until it, at their merge time with their main
                                     push CI result, ahead of the program's own rows, and move created_at
                                     to the first of them. Rerun to add CI results that came in since
  heavy <slug|-> [--wait-minutes 60] [--pr owner/repo#N] -- <command…>
                                     run a heavy local command (compose stack, image build, local
                                     E2E) holding one of heavy_slots machine-wide slots (a
                                     quarter of the CPUs, at least 2); `-`
                                     outside a program. A compose project it leaves behind, with a
                                     config file in this worktree, goes to the reap ledger
                                     (compose-stack) so the janitor downs it once the card is done
  wait <slug> [--timeout-ms 540000] [--rounds 3]
                                     lead only: block until the Run inbox holds actionable
                                     mail; acks heartbeat-only batches; never acks actionable ones.
                                     With orchestrate.auto_wake_min it wakes a stopped worker once
                                     (references/waking.md, "Automatic wake")
  inbox [--ack DELIVERY] [--wait] [--timeout-ms 3300000]
                                     no slug, a session without a program: ack the batch you handled,
                                     peek-wait (no --types, so Orca's typed notice stays out), then
                                     print the next batch one line per message with its ack line;
                                     with --wait, acks heartbeat-only batches and waits on; a failed
                                     --ack is reported and the wait still runs
  inbox --stream [--debounce 300] [--ttl-ms 1740000] [--no-cards]
                                     no slug, the orchestrator under the Monitor tool: one stdout line
                                     per message (each line a notification), the card watch as its
                                     child, RESTART DUE / SESSION CLEARED lines, STREAM END before
                                     Monitor's 30-minute limit (inbox_stream.py)
  handoff save --next T [--waiting T] [--question T] [--reply T] [--note T] [--key K] [--restart] | handoff show
                                     no slug: what the session after a /clear does first, in
                                     agent-skills.db; --restart has the stream type /clear (handoff.py)
  watch-cards [--interval 180] [--idle-min 30] [--goal-lost-min 15] [--min-free-gb 15] [--timeout-ms 3300000] [--once]
                                     no slug: exit on a new stuck screen in any agent terminal but this
                                     one (cards no Run lists), a lost login or goal, or low disk; never types
                                     (card_watch.py). --card HANDLE [--within 240]: one new card's start
  migrate [--apply | --restore BACKUP_DIR]
                                     no slug: close the board doing lines that name a card, now that the
                                     card's Orca worktree is its record (orca_migrate.py). Bare: dry-run;
                                     --apply backs up first; --restore reopens what that run closed
  doctor [--json]                    no slug: Orca's orchestration settings (read-only), whether a lead starts
                                     as a dispatched worker (Nested worker depth >= 3) or a /goal card, a
                                     one-line proposal per setting to change, and this terminal's Run
                                     (orca_settings.py)
  model lead|worker                  no slug: the role's model from orchestrate.models; prints nothing when unset
  decide add --title T [--body-file F] --option "label::description"... --recommend N [--link URL] [--order oN]
             --if-no-answer <the --recommend label | 진행 못 함> --reason <owner-asked | hard-after-review | owner-hands-only>
                                     the owner-ask gate (references/owner-ask-gate.md): the recommended option
                                     has its reason, and the last two lead the body
  decide answer <id> | --title T  (--quote TEXT | --quote-file F) [--by SESSION]
  decide list | show <id> | done <id> [--answer TEXT] | drop <id> | keep <id>
                                     no slug: a decision you wait on the human for, shown on the fleet
                                     dashboard until done or dropped; add prints its id. Each option is a
                                     button that closes it with that answer, then types
                                     "decision <id>: <label>" into this terminal once it is idle. keep: you
                                     checked it and it still stands (checked_at); the brief counts one
                                     unchecked for board.decision_stale_h hours as 오래된 결정. answer records
                                     the owner's words as given, with the time and the session (--by, else
                                     this terminal), and prints the line to quote; --title records an order
                                     nobody asked for. A quote over the limit is refused, never cut. show
                                     prints that line again. Decisions live in agent-skills.db, or in
                                     decisions.json while no DB file exists; a DB not imported yet stops with that
  order setup | add TEXT [--kind order|feedback] [--source S] [--owner-action] | link oN --unit UNIT [--stand-in]
        | done oN | drop oN | reopen oN | links-complete oN [--undo] | shadow | list [--status S] [--json]
        | import FILE [--skip REGEX] [--dry-run | --check]
                                     no slug: the owner's instructions (order.py). Write one before you delegate or
                                     start it; link it to the card, agent, board item, decision or PR that carries it
                                     (UNIT: card:<worktree> | agent:<id> | board:<bN> | decision:<dN> | pr:<owner/repo#n>).
                                     The brief counts an open one with no link after order_unlinked_min minutes
                                     (10) as 연결 없는 오너 지시. setup runs once per PC; import takes a hand list
                                     once per line (the original is only read; --check lists what is not taken yet).
                                     --stand-in links a unit that only stands for the order, such as the temporary
                                     to-do an import replaced: it never counts as carrying or resolving the order.
                                     links-complete is the receiving session's word that every unit is linked; only
                                     such an order can be closed by tidy, which records a would_close event until
                                     orchestrate.order_auto_close is true. shadow sets those events against what the
                                     sessions closed by hand
  db import [--dry-run | --apply] [--rehearsal] | export --legacy --target 4a|pre-4a DIR | dump [TABLE]
  db backup FILE | restore FILE [--rehearsal] | rollback 4b|state|sendlog DIR | rollback refs [--rehearsal] | retention [--dry-run] | cutover-plan 4a|4b
  db state [--auto]                  acks.jsonl and stages.json, then watch-prs's prs/sendlog.jsonl -> tables, after a backup,
                                     with tombstones in their place (setup runs it with --auto after an update; a rerun
                                     changes nothing); export --legacy --target state|sendlog DIR writes the files back
                                     for rollback state|sendlog DIR.
                                     The same run adds the 8-refs step: pr_reviewer, card_ref, the reap_card view and
                                     the pr/program/kind columns (no file moves). rollback refs takes them out again,
                                     and goes first, before sendlog and state. retention drops cards gone 30 days and
                                     merged or closed PRs nothing points at after 90 (the collector runs it hourly)
  ask add OWNER/REPO#N REVIEWER | list [--status S] [--json] | cancel ID   the review-ask queue watch-prs sends from
  db migrate [--check] | init        migrate is the whole cutover in one rerunnable command, run by the owner from a
                                     plain terminal (cutover.py); init is for a PC with no state (setup runs it)
                                     no slug: agent-skills.db (store.py). import moves decisions.json,
                                     board.json and tidy.jsonl in and leaves an empty directory in each
                                     place; --apply refuses while old code, an unreloaded session or a
                                     loaded LaunchAgent could still write. rollback 4b puts back what
                                     export --target 4a wrote. cutover-plan prints the commands you run
                                     around it and runs none
  stage add <id> --title T [--pr owner/repo#N] [--parent ID] [--group]
  stage set <id> --col merge|dev|prod --mark ok|fail|partial|checking|na [--evidence TEXT] [--by WHO]
  stage env [--prod V] [--dev V] | next [LINE]... | done <id> | drop <id> | show [--program P] [--all] [--md|--telegram|--json]
                                     no slug: the stage table (brief's stage table mode), one row per work item. show
                                     leaves out a row finished or 7 days unchanged ("stage_idle_d") unless --all,
                                     --program keeps one program's rows, and exits 1 past 78 columns.
                                     Write a cell where you verified it; ok needs --evidence. The merge
                                     cell of a row with --pr comes from the fleet PR list. `next` replaces
                                     the next-steps list (no lines clears it); done rows leave after 7
                                     days; drop removes a row and its children. --group makes a heading
                                     row (a feature or an epic) with no PR and no cells; --parent rows to it
  board lead add <name> --handle H --repo OWNER/NAME [--program P] [--run RUN] [--stale-min N] [--worktree ID] | lead close <name>
  board set <id> [--kind lead|phase|task] [--title T] [--repo OWNER/NAME] [--program P] [--parent ID]
                 [--status moving|owner|waiting|held|blocked|done] [--progress N/M|-] [--next TEXT] [--owner WHO] [--pr owner/repo#N]
                 [--worktree ID] [--priority RANK:NAME|-]
                                     --priority: the owner's priority group of the row and of the rows under
                                     it (a row's own wins; groups show in RANK order). The brief opens with one
                                     line per group (진행·대기·완료 counts, the next step); with no group on
                                     any row there is no such section. - clears it.
  board priority set <MATCH> RANK:NAME | priority drop <MATCH> | priority list
                                     a rule that gives a group to rows the board has no row of (the fleet's
                                     lead and card rows). MATCH is worktree:ID (that card and the rows under
                                     it), program:NAME (a lead's program) or repo:OWNER/NAME; a row takes its
                                     own --priority, else the nearest worktree rule, else program, else repo.
                                     --worktree: the Orca worktree id whose session works the row (lead add
                                     records the handle's own); the brief's 세션 column and the dashboard
                                     link the row to it by that id, which a terminal restart does not change.
                                     A row linked to a card takes neither --pr nor another --worktree: Orca
                                     knows them. lead add refuses a lead Orca knows; board set links a row
  board set card:<worktree> [--phase TEXT] [--progress N/M] [--after owner/repo#N] [--owner WHO --next TEXT] | annot-migrate [--apply]
                                     A card's annotation: what Orca, GitHub and the card's report do not
                                     know. A new row for a card is refused; `-` clears a field. --owner
                                     with --next is the owner block (what the owner does); --next alone is
                                     a step after merge. annot-migrate moves card-linked rows into their
                                     card's annotation (dry run unless --apply) and lists the rest as
                                     중복 N건 with a command each.
  board drop <id> | doing add <title> --owner W --next E [--waiting] [--order oN] | doing drop <n>
        | later add <title> --trigger T [--due D] [--who W] [--note TEXT] [--at N] [--order oN] | later move <n> --to N | later drop <n>
        | todo add <title> (--due D | --after C) --who W [--order oN] | todo drop <n> | todo from-later <n> [--due D] --who W
          --order oN (on the three adds): the new item carries the owner's order oN (orch order); an unknown or closed
          oN adds nothing
        | todo list [--telegram] [--json] [--now ISO8601] [--no-refresh] | now [LINE]... | show
  board open [--json] | done <id>... [--why T] [--json] | reopen <id>... [--json] | tidy [--dry-run] [--json] | closed
                                     close what is over (tidy.py): open lists what can be closed now (a row,
                                     card:<session>, a list item's b<N>); done closes it, reopen undoes it.
                                     tidy is one pass of what the dashboard's collector runs after each tick:
                                     it closes on evidence only (a merged or closed PR, a session gone) and
                                     marks what it cannot settle 확인 필요; closed lists every close with its
                                     evidence. The dashboard's Done and Reopen buttons call the same function.
                                     no slug: the board the periodic report is rendered from (agent-skills.db),
                                     one row per lead, phase and sub-task. doing add refuses a live card's or
                                     subagent's work: it has its own row. set changes only the
                                     fields given and stamps reported_at; an id a/b hangs off a. A row without
                                     --parent is a lead (needs --repo); under a parent it is a task unless
                                     --kind phase. PR state is joined from the collector's prs.json at render.
                                     --program is the lead's project (epic, program): a program report (--scope)
                                     covers only those; lead add and set warn while a lead beside another in its
                                     repository has none
                                     A waiting row waits on a person: the PR's requested reviewers say who, else
                                     --owner (오너, owner or the gh login is the owner; another name is someone
                                     else); with neither the report marks it 확인 필요. --status owner is a row
                                     only the owner unblocks.
                                     now replaces the owner's now-list (no lines clears it); doing lists work
                                     that is moving but has no row (an investigation), --waiting when W is a
                                     person it waits on (a reply from another team), --owner 오너 when it waits
                                     on the owner; todo is a promise kept on a date (--due) or condition
                                     (--after: a date, or every PR it names merged) by --who: when it comes it
                                     moves to the owner's list (W is the owner) or 진행 중 until done do:<k>;
                                     from-later moves held item n there; later is the queue of work set aside
                                     with no date, in order (the first is next; --at N, move <n> --to N), with
                                     what makes it next (--trigger; its --due only lifts the PR hold). todo list
                                     prints both as tables; the report only counts them
  brief render [--periodic] [--scope top-level|PROGRAM] [--telegram] [--headline T] [--decision T]... [--insight T]
               [--skipped T] [--now ISO8601] [--commit] [--json] [--no-fill] [--no-refresh]
                                     the brief skill's report, on request or (--periodic) on a schedule, from the
                                     board, the fleet's sessions, prs.json, open decisions and brief-last/<scope>.json
                                     (bold = changed since it). The time is the system clock's in the local time
                                     zone. The stage table is one line: its row count and the dashboard's Stages
                                     page (`orch stage show` while the dashboard is down); --periodic adds
                                     --skipped and the last reap. The model gives only --headline (a sentence),
                                     --decision ("<question>: <options> → 추천: X — <reason> · 답이 없으면
                                     <X | 진행 못 함> · 근거: <tag>", the owner-ask gate,
                                     registered on the spot, so it shows with its id; the same words again
                                     are the same decision, and one answered within 24 h is not asked again.
                                     If it cannot be registered the report still prints, with a warning line
                                     above it, the item marked (미등록), and exit 1. --now registers nothing)
                                     and --insight (after 진행 중). state.json
                                     older than 5 min ("state_min") is collected again first unless --now or
                                     --no-refresh; a failed collect is in the headline with the files' age. --commit
                                     saves this table as the last one (after sending). Staleness: a row past 90
                                     min shows its age (board rows past it are counted in the headline), an open
                                     PR probed over 30 min ago is in the headline,
                                     over 120 min marks it ⚠️ (merged and closed PRs do not age), a lead whose terminal left the fleet turns 🔴;
                                     a ✅ row merged over 24 h ago folds into its section's count;
                                     override in the dashboard config: {"board": {"row_min", "pr_warn_min",
                                     "pr_stale_min", "state_min", "done_fold_h", "stage_idle_d"}}. Exit 1 when a table is wider than 78 columns. Rows the
                                     board lacks come from the fleet's sessions (state.json): each lead the board
                                     has no lead for, its cards and the rendering session's own cards, each with the
                                     last milestone of the report file its brief's REPORT line names. --no-fill
                                     renders the board alone
  sent add --to WHO (--text-file F | - | --text T) [--kind card|terminal|inbox|subagent|agent|slack|tracker|other]
           [--channel HOW] [--repo OWNER/NAME] [--subject S] [--from WHO]
  sent list [--days 1] [--json]
                                     no slug: log an instruction as you send it to another session, card,
                                     inbox, Slack or tracker (the text as sent, from a file or stdin `-`;
                                     --text only for a short string with nothing secret), for the dashboard's
                                     Sent page. WHO sending defaults to $ORCA_TERMINAL_HANDLE.
                                     list prints the log with what live transcripts and the dashboard sent
  hold <resource> [--note TEXT] [--ttl MIN] [--by WHO] | hold --list [--json]
  release <resource> [--by WHO] [--force]
                                     no slug: take a resource lanes share (browser, a monitoring login)
                                     before using it, release it after. hold refuses while someone else
                                     holds it and refreshes your own; a hold idle past --ttl (120) is
                                     broken. WHO defaults to $ORCA_TERMINAL_HANDLE. --list shows these
                                     and the exclusive landing lanes

Store: ~/.claude/programs/<slug>/ (program.json holds identifiers only; ledger.jsonl is
append-only). Events: spawned, ready, verdict, landed, main_green, main_red, land_failed,
admitted, parked, approved, gate_opened, stop, resume, predicate_verified, config, dep,
land_check, yield, lane, lock_acquired, lock_released, reprioritized, signal.
Tickets come from the tracker adapter (use-tracker/scripts/tracker.py), never from a tracker directly.
"""
import contextlib, datetime as dt, fcntl, fnmatch, hashlib, heapq, json, math, os, pathlib, re, shlex, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # loaded by path or run with -I, this dir is not on it
import land_scope  # noqa: E402

TRACKER = pathlib.Path(__file__).resolve().parents[2] / "use-tracker" / "scripts" / "tracker.py"
HOME = pathlib.Path(os.environ.get("PROGRAMS_HOME", "~/.claude/programs")).expanduser()
GIT_CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "agent-skills" / "orchestrate" / "git"
PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}
ACTIONABLE = {"worker_done", "escalation", "question"}
# GitHub or network hiccups that a later land pass gets past; attempt() rereads PR state, so a merge that went
# through before the error is recorded on the next pass, never merged twice.
TRANSIENT = re.compile(r"timeout|timed out|connection (reset|refused)|unexpected EOF|TLS handshake|HTTP 50[234]|"
                       r"could not resolve host|temporarily unavailable|something went wrong", re.I)


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# None waits for every command; `orch patrol` sets seconds for its own round.
RUN_TIMEOUT = None


def run(cmd, check=True):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(cmd, 124, "", f"timed out after {RUN_TIMEOUT}s")
    if check and r.returncode != 0:
        # Orca 1.4.240 puts its error, "Unknown flag --full" included, on stdout with stderr empty; with_full
        # reads this message to decide whether to retry without the flag.
        said = " | ".join(s.strip()[:400] for s in (r.stderr, r.stdout) if s and s.strip())
        sys.exit(f"{' '.join(cmd[:4])} … failed ({r.returncode}): {said}")
    return r


def orca_json(*args):
    out = json.loads(run(["orca", *args, "--json"]).stdout)
    if not out.get("ok", True):
        sys.exit(f"orca {' '.join(args[:2])}: {json.dumps(out.get('error'))[:400]}")
    return out.get("result", out)


def all_workers(run_id):
    """Every Dispatch of the Run. worker-list pages at 100, newest first, and a program passed 86 in two days:
    one page would drop the oldest cards from LANDED-BUT-OPEN."""
    import orca_settings
    return orca_settings.all_workers(orca_json, "--run", run_id, "--limit", "100", error=SystemExit)


# The program's lead Dispatch (program.json `lead_dispatch`) once a Program is loaded: set for a lead started with
# worker-start, which works on its coordinator's Run beside the coordinator's other units.
LEAD = {"dispatch": None}


def run_workers(run_id):
    """The program's Dispatches: the whole Run for a lead that coordinates it, the Dispatches under the lead's Task
    for a lead that is one of the Run's workers (lead_scope)."""
    return lead_scope(all_workers(run_id), LEAD["dispatch"])


def own_worker(workers, me):
    """This terminal's own Dispatch among the Run's workers."""
    return next((w for w in workers if me and worker_terminal(w) == me), None)


def worker_terminal(row):
    import orca_settings
    return orca_settings.worker_terminal(row)


def lead_scope(workers, lead_dispatch):
    import orca_settings
    return orca_settings.lead_scope(workers, lead_dispatch)


def gh_json(*args):
    return json.loads(run(["gh", *args]).stdout)


def opt(argv, name, default=None):
    if name not in argv:
        return default
    i = argv.index(name) + 1
    if i >= len(argv) or argv[i].startswith("--"):
        # An unquoted empty shell variable vanishes from argv; say which flag lost its value.
        sys.exit(f"{name} needs a value (an empty shell variable drops out of the command line)")
    return argv[i]


class Program:
    def __init__(self, slug):
        self.dir = HOME / slug
        self.slug = slug
        path = self.dir / "program.json"
        if not path.exists():
            sys.exit(f"no program '{slug}' at {self.dir}; run init first")
        self.cfg = json.loads(path.read_text())
        self.ledger_path = self.dir / "ledger.jsonl"
        LEAD["dispatch"] = self.cfg.get("lead_dispatch")

    def events(self):
        if not self.ledger_path.exists():
            return []
        return [json.loads(l) for l in self.ledger_path.read_text().splitlines() if l.strip()]

    def update(self, change):
        """Apply change(cfg) to program.json as it is now, so a concurrent `set` or backfill is not reverted."""
        with self.locked():
            self.cfg = json.loads((self.dir / "program.json").read_text())
            change(self.cfg)
            # A shared file overwritten in place was once left empty mid-write; replace it whole.
            tmp = self.dir / "program.json.tmp"
            tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False) + "\n")
            os.replace(tmp, self.dir / "program.json")

    @contextlib.contextmanager
    def locked(self):
        """Held to append to the ledger or rewrite it: backfill replaces the file, and a row appended to the
        file it replaced would be lost."""
        with (self.dir / "ledger.lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def append(self, ev, **fields):
        row = {"ts": now(), "ev": ev, **{k: v for k, v in fields.items() if v is not None}}
        with self.locked(), self.ledger_path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def append_if(self, ev, ok, **fields):
        """append, unless ok(events) is false; None then. One lock over the read and the write, so a row another
        process appends in between is seen by ok, not overwritten in effect."""
        row = {"ts": now(), "ev": ev, **{k: v for k, v in fields.items() if v is not None}}
        with self.locked():
            if not ok(self.events()):
                return None
            with self.ledger_path.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def append_once(self, ev, seen, **fields):
        """append, unless a row already satisfies seen; None then. One lock over both, or two rounds both pass."""
        row = {"ts": now(), "ev": ev, **{k: v for k, v in fields.items() if v is not None}}
        with self.locked():
            if any(seen(e) for e in self.events()):
                return None
            with self.ledger_path.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row


def stack_lower(events):
    """A stack's lower layers land in their top's push, so main CI never runs on their commits. A layer counts
    only once its top's landing is recorded: a merge cut off in between leaves the layer's own CI to watch."""
    tops = {e.get("pr") for e in events if e["ev"] == "landed"}
    return {e["sha"]: e["stack_top"] for e in events
            if e["ev"] == "landed" and e.get("sha") and e.get("stack_top") in tops}


def landed_shas(events):
    lower = stack_lower(events)
    return {e["sha"] for e in events if e["ev"] == "landed" and e.get("sha") and e["sha"] not in lower}


def cap_from(events, ceiling):
    # AIMD over merge commits: +1 the first time a landed commit is proven green on main,
    # halve the first time one goes red, and on every failed landing.
    landed, seen, cap = landed_shas(events), set(), 1
    for e in events:
        if e.get("via"):
            continue  # another program's commit proved main green; it is no landing of this one
        if e["ev"] in ("main_green", "main_red"):
            key = (e["ev"], e.get("sha"))
            if e.get("sha") not in landed or key in seen:
                continue
            seen.add(key)
        if e["ev"] == "main_green":
            cap = min(ceiling, cap + 1)
        elif e["ev"] in ("main_red", "land_failed"):
            cap = max(1, cap // 2)
    return cap


def main_state(events):
    """red, pending or green, judged in landing order.

    The newest landed commit that has a result decides: a red there stays red while the repair
    landed after it is still pending, and a late green for an older commit never clears it.
    A result without --sha (ledgers from before it was required) belongs to the last landing
    before it.
    """
    order, result = landing_results(events)
    if not order:
        return "green"
    decided = [s for s in order if s in result]
    if decided and result[decided[-1]] == "main_red":
        return "red"
    return "green" if order[-1] in result else "pending"


def landing_results(events):
    """(landing order, {landed sha: main_green|main_red}). A green with `via` is another program's commit: it
    clears only the reds it lists in `clears` (pinned when it was recorded), so a red recorded later stays."""
    order, result, last, lower = [], {}, None, stack_lower(events)
    for e in events:
        if e["ev"] == "landed" and e.get("sha") and e["sha"] not in lower:
            order.append(e["sha"]); last = e["sha"]
        elif e["ev"] == "main_green" and e.get("via"):
            for sha in e.get("clears") or []:
                if result.get(sha) == "main_red":
                    result[sha] = "main_green"
        elif e["ev"] in ("main_green", "main_red"):
            sha = e.get("sha") or last
            if sha in order:
                result[sha] = e["ev"]
    return order, result


def open_reds(events):
    """The red landings main_state is stopped on: the unbroken run of reds at the end of the decided landings."""
    order, result = landing_results(events)
    reds = []
    for sha in reversed([s for s in order if s in result]):
        if result[sha] != "main_red":
            break
        reds.append(sha)
    return reds[::-1]


def final_check_at(events):
    """When the current predicate_verified was recorded, or None. It counts only if nothing it covered changed after
    it: no landing, no predicate edit, no newly admitted follow-up."""
    since = max((i for i, e in enumerate(events) if e["ev"] == "landed" or (e["ev"] == "admitted" and e.get("ticket"))
                 or (e["ev"] == "config" and (e.get("note") or "").startswith("predicate="))), default=-1)
    return next((e.get("ts") or "" for e in reversed(events[since + 1:]) if e["ev"] == "predicate_verified"), None)


def final_check_current(events):
    return final_check_at(events) is not None


def pr_state(events):
    """Latest event of each kind per PR, in ledger order."""
    st = {}
    for e in events:
        if e.get("pr") is not None:
            st.setdefault(e["pr"], {"pr": e["pr"]})[e["ev"]] = e
    return st


def ready_prs(events):
    return [s for s in pr_state(events).values() if ("ready" in s or "verdict" in s) and "landed" not in s]


def claimed(events, pr):
    """Is a `land` call for this PR live? `ready` is written only by land, and a verdict alone asks for nothing.
    A land that ended at exit 1 will not come back on its own, so it withdraws the call until the next land."""
    marks = [e["ev"] for e in events if e.get("pr") == pr and
             (e["ev"] == "ready" or e["ev"] == "land_check" and e.get("outcome") == "stopped")]
    return bool(marks) and marks[-1] == "ready"


def stopped(events):
    marks = [e["ev"] for e in events if e["ev"] in ("stop", "resume")]
    return bool(marks) and marks[-1] == "stop"


KLASS = {"main-fix": 0, "gate": 1, "urgent": 2, "normal": 3}
LANDING = {"aging_hours": 2.0, "stale_hours": 3.0, "max_backlog_hours": 2.0, "land_interval_minutes": 25,
           "exclusive_stale_minutes": 90,
           # Changes that can break a merge they do not textually touch land one at a time, on the latest base.
           "exclusive_paths": ["*migrations/*", ".github/*", "*Dockerfile*", "*compose*.yml", "*compose*.yaml"],
           # A PR touching these changes what a person sees; fnmatch's * also crosses "/".
           "visual_paths": ["apps/web/*", "*.css", "*.stories.*", "packages/ui/*"]}


def knob(cfg, key):
    return (cfg.get("landing") or {}).get(key, LANDING[key])


def slot_count(value):
    """A usable slot count (a positive int, not a bool) or None, so a typo in a config falls through to the next layer."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def heavy_slots(cfg):
    """program.json `landing.heavy_slots`, else `orchestrate.heavy_slots` of agent-skills.json, else a quarter of the
    CPUs (at least 2). `orch heavy -` passes no program, so the user file is what a bare card tunes."""
    landing = cfg.get("landing")
    n = slot_count(landing.get("heavy_slots")) if isinstance(landing, dict) else None
    if n is None:
        path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
        try:
            n = slot_count((json.loads(path.read_text()).get("orchestrate") or {}).get("heavy_slots"))
        except (OSError, ValueError, AttributeError):
            n = None
    return n or max(2, (os.cpu_count() or 1) // 4)


def pr_ticket(events):
    return {e["pr"]: e["ticket"] for e in events if e.get("pr") is not None and e.get("ticket")}


def dep_map(events, extra=None):
    """{ticket: tickets it lands after}: Orca task deps (extra) plus ledger `dep` events."""
    after = {t: set(b) for t, b in (extra or {}).items()}
    for e in events:
        if e["ev"] == "dep":
            # A self edge (layers of one ticket recorded as a dep) would hold the ticket forever.
            after.setdefault(e["ticket"], set()).update(t for t in e.get("after") or [] if t != e["ticket"])
    return after


def orca_ts(s):
    """An Orca task time ("YYYY-MM-DD HH:MM:SS", UTC unless it carries an offset) in the ledger's format, or None."""
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return (t.astimezone(dt.timezone.utc) if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()


def with_orca_spawns(events, workers, tasks):
    """events plus a `spawned` line for each Dispatch no `spawned` line names, from its Orca Task: the ticket its
    spec opens with, dated by the Task's creation, marked "orca". Orca's own record of the start, so a lead no
    longer writes one; a line already in the ledger (an older brief, a sub-unit ticket, a --role) wins."""
    have = {d for e in events if e["ev"] == "spawned" for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or ""))}
    by_task = {t.get("id"): t for t in tasks or []}
    extra = []
    for w in workers or []:
        d, t = w.get("dispatchId"), by_task.get(w.get("taskId")) or {}
        ticket, ts = ticket_of(t), orca_ts(t.get("created_at"))
        if d and d not in have and ticket and ts:
            extra.append({"ts": ts, "ev": "spawned", "ticket": ticket, "note": d, "orca": True})
            have.add(d)
    return events + extra


def dispatch_tickets(workers, tasks):
    """dispatchId -> ticket, from the Orca Task each Dispatch works (its spec opens with the ticket ID)."""
    by_task = {t.get("id"): t for t in tasks or []}
    out = {}
    for w in workers or []:
        d = w.get("dispatchId") or w.get("dispatch")
        t = ticket_of(by_task.get(w.get("taskId") or w.get("task")) or {})
        if d and t:
            out[d] = t
    return out


def live_units(events, dispatches, orca_tickets=None):
    """Live workers as the cap counts them: the layers of one land-after chain (ledger `dep`) land as one
    merge, so they count once. A Dispatch's ticket comes from a `spawned` line naming it, else from its Orca Task
    (orca_tickets): a lead records `spawned` for sub-units (ABC-1-K2) whose Task specs all open with the parent ID."""
    ticket = dict(orca_tickets or {})
    for e in events:
        if e["ev"] == "spawned" and e.get("ticket"):
            for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or "")):
                ticket[d] = e["ticket"]
    live = {ticket.get(d, d) for d in dispatches}
    root = {t: t for t in live}

    def find(t):
        while root[t] != t:
            t = root[t]
        return t
    for t, below in dep_map(events).items():
        for b in below:
            if t in root and b in root:
                root[find(t)] = find(b)
    return len({find(t) for t in live})


TICKET_ID = re.compile(r"\b[A-Z0-9]*[A-Z][A-Z0-9]*-\d+\b")  # 94S-12, ENG-3
CLEANUP = 'end-session "Who cleans up"; a lead\'s card, or a machine with no cleaner: end-session §4'


def ticket_of(task):
    # The brief opens with the ticket ID; a card name like "W-372" only looks like one.
    m = TICKET_ID.search(((task.get("spec") or "").splitlines() or [""])[0])
    if m:
        return m.group(0)
    word = ((task.get("display_name") or task.get("task_title") or "").split() or [""])[0]
    return word if TICKET_ID.fullmatch(word) else None


OPEN_STATES = {"triage", "backlog", "unstarted", "started"}


def landed_but_open(events, workers, tasks, worktrees, issues_by_id=None, here="", unjudged=()):
    """Cards left behind: (ticket, dispatch, terminal still active, path) for each Orca worktree whose every
    dispatch worked a ticket that has landed. A released worker's card stays until `orca worktree rm`. here: the
    caller's own worktree id (own_worktree_id), left out: a `--worktree current` worker reports it as its card.
    unjudged: dispatches whose rows the caller could not judge (row_gaps); their cards come back with active None.

    A ticket can take several PRs, a backfilled landing among them: while the tracker says it is open,
    a landing is not its live worker's end. Without the tracker, a landing is, except for a card spawned after it."""
    ticket_by_task = {t["id"]: ticket_of(t) for t in tasks}
    events = with_orca_spawns(events, workers, tasks)
    # A fold into a lower PR's branch reaches the base only with that PR: its ticket is still in flight.
    tickets_of = pr_ticket(events)
    last_land = {}
    for e in events:
        t = tickets_of.get(e.get("pr")) if e["ev"] == "landed" and not e.get("onto") else None
        if t:
            last_land[t] = max(last_land.get(t, ""), e["ts"])
    landed = set(last_land)
    # A card spawned on a ticket after its last landing carries follow-up work that has not landed.
    spawned = {d: e["ts"] for e in events if e["ev"] == "spawned"
               for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or ""))}
    still_open = {t for t in landed if ((issues_by_id or {}).get(t) or {}).get("state_type") in OPEN_STATES}
    present = {w["id"]: w.get("path") for w in worktrees if not w.get("isMainWorktree")}
    cards = {}
    for w in workers:
        wt = (w.get("resource") or {}).get("worktreeId")
        if wt in present and not (here and wt == here):
            cards.setdefault(wt, []).append(w)
    out = []
    for wt, ws in cards.items():
        tickets = {ticket_by_task.get(w.get("taskId")) for w in ws}
        later = any(spawned.get(w.get("dispatchId"), "") > last_land.get(ticket_by_task.get(w.get("taskId")), "~")
                    for w in ws)
        if tickets <= landed and not later:
            if any(not w.get("terminalState") or not w.get("dispatchId") or w["dispatchId"] in unjudged for w in ws):
                # A row without terminalState is not known to be released: active None, so no rm is offered.
                out.append((sorted(tickets)[0], next((w["dispatchId"] for w in reversed(ws) if w.get("dispatchId")), "?"),
                            None, present[wt]))
                continue
            active = [w for w in ws if w.get("terminalState") == "active"]
            if active and tickets & still_open:
                continue
            out.append((sorted(tickets)[0], (active or ws)[-1]["dispatchId"], bool(active), present[wt]))
    return sorted(out, key=lambda c: (c[0], c[1], c[3]))


def run_tasks(run_id):
    """The Run's tasks this program owns (orca_settings.scope_tasks)."""
    r = run(["orca", "orchestration", "task-list", "--run", run_id, "--json"], check=False)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return []
    tasks = out.get("result", out) if isinstance(out, dict) else out
    tasks = tasks.get("tasks", []) if isinstance(tasks, dict) else tasks or []
    if not LEAD["dispatch"]:
        return tasks
    import orca_settings
    return orca_settings.scope_tasks(tasks, run_workers(run_id), LEAD["dispatch"])


def orca_deps(run_id):
    """Dependencies as the Run's tasks declare them (`task-create --deps`), keyed by ticket.

    Orca deps are the source of truth; they are immutable once set, so a superseded task
    (status failed) is skipped in favour of its replacement, and a dependency found after
    dispatch is recorded with `orch dep` instead. Returns {} when Orca cannot answer.
    """
    return deps_from_tasks(run_tasks(run_id))


def deps_from_tasks(tasks):
    by_id = {t["id"]: ticket_of(t) for t in tasks}
    after = {}
    for t in tasks:
        tk = ticket_of(t)
        if not tk or t.get("status") == "failed":
            continue
        deps = t.get("deps") or []
        deps = json.loads(deps) if isinstance(deps, str) else deps
        after.setdefault(tk, set()).update(by_id[d] for d in deps if by_id.get(d))
    return after


def unblocks(ticket, after, open_tickets):
    """How many open tickets wait on this one, directly or through others."""
    rev = {}
    for t, befores in after.items():
        for b in befores:
            rev.setdefault(b, set()).add(t)
    seen, todo = set(), [ticket]
    while todo:
        for t in rev.get(todo.pop(), ()):
            if t not in seen:
                seen.add(t)
                todo.append(t)
    return len(seen & open_tickets)


PROTECTED = "branch protection blocks it (required review or check)"


def row_state(row, verdict, no_ci=False):
    """ready, catching_up (only an update-branch or CI time stands between it and landing), or blocked."""
    if row is None:
        return "gone", ["PR is no longer open"]
    blocked, catching = [], []
    failed, pending, _ = ci_summary(row.get("statusCheckRollup"), no_ci)
    mss = row.get("mergeStateStatus")
    if row.get("isDraft"):
        blocked.append("draft")
    if mss == "DIRTY":
        blocked.append("conflicts with base")
    if failed:
        blocked.append("CI failed: " + ", ".join(failed[:3]))
    if not verdict or verdict.get("result") != "pass":
        blocked.append("no passing verdict")
    if mss == "BEHIND":
        catching.append("behind base")
    if pending:
        catching.append(f"CI pending ({len(pending)})")
    elif mss == "BLOCKED" and not failed:
        blocked.append(PROTECTED)
    if mss == "UNKNOWN":
        catching.append("GitHub is computing mergeability")
    if blocked:
        return "blocked", blocked + catching
    if catching:
        return "catching_up", catching
    note = [] if verdict.get("sha") == row.get("headRefOid") else [
        f"verdict is for {verdict['sha'][:8]}, not the head {row['headRefOid'][:8]}: the direct path re-checks the"
        " patch-id; the land executor and the no-CI lane need a verdict at exactly the head"]
    return "ready", note


def land_order(events, rows, cfg, when, stacks=None, deps=None):
    """Every unlanded program PR in the order it may land, with why each is not moving.

    Class first (main-fix, gate, urgent, normal). A PR that has waited aging_hours counts as
    urgent, so nothing starves behind a stream of fresh, easy PRs. Within a class the PR that
    unblocks more open tickets goes first, then the one that has waited longest. Normal-lane PRs
    land in parallel as soon as they are ready; the order decides only who takes the exclusive
    lane next and what the lead unsticks first.

    A GitHub stack is one landing unit that lands from its top in one merge: a lower layer waits
    for the top while the top is not blocked, and the top is ready only when every open layer
    below it is.
    """
    st, tickets, after = pr_state(events), pr_ticket(events), dep_map(events, deps)
    no_ci = no_ci_repo(cfg.get("repo"))
    rows = {r["number"]: r for r in rows}
    landed_t = {tickets.get(e.get("pr")) for e in events if e["ev"] == "landed"}
    pending = ready_prs(events)
    open_t = {tickets.get(s["pr"]) for s in pending} - {None}
    stack_of = {}
    for members in (stacks or {}).values():
        live = [m for m in members if m in rows]
        if len(live) >= 2:
            stack_of.update({m: live for m in live})
    out = []
    for s in pending:
        pr = s["pr"]
        first = min(parse_ts(e["ts"]) for e in events if e.get("pr") == pr and e["ev"] in ("ready", "verdict"))
        klass = next((e["klass"] for e in reversed(events) if e.get("pr") == pr and e.get("klass")), "normal")
        age_h = (when - first).total_seconds() / 3600
        rank = KLASS.get(klass, 3)
        if age_h >= knob(cfg, "aging_hours"):
            rank = min(rank, KLASS["urgent"])
        ticket = tickets.get(pr)
        state, reasons = row_state(rows.get(pr), s.get("verdict"), no_ci)
        if on_hold(events, pr) and state != "gone":
            state, reasons = "blocked", ["on hold"] + reasons
        live = stack_of.get(pr, [])
        below_t = {tickets.get(b) for b in live[:live.index(pr)]} if live else set()
        waits = sorted(b for b in after.get(ticket, ()) if b not in landed_t | below_t)
        if waits and state != "gone":
            state, reasons = "waiting", [f"lands after {', '.join(waits)}"] + reasons
        lane = (s.get("lane") or {}).get("exclusive", False) or klass == "gate" or no_ci
        wants = claimed(events, pr)
        if lane and not wants and state in ("ready", "catching_up"):
            reasons = reasons + ["READY-stop: no live land call, passed over for the exclusive lane"]
        out.append({"pr": pr, "ticket": ticket, "klass": klass, "rank": rank,
                    "unblocks": unblocks(ticket, after, open_t) if ticket else 0,
                    "since": first.isoformat(), "age_h": round(age_h, 2), "state": state, "reasons": reasons,
                    "exclusive": bool(lane), "claimed": wants, "base": (rows.get(pr) or {}).get("baseRefName"),
                    "stack": None})
    by_pr = {e["pr"]: e for e in out}
    own = {e["pr"]: e["state"] for e in out}  # before stack rules rewrite them
    for live in {tuple(v) for v in stack_of.values()}:
        base = rows[live[0]].get("baseRefName")
        for i, m in enumerate(live):
            e = by_pr.get(m)
            if not e:
                continue
            e["stack"], e["base"] = list(live), base
            upper = [by_pr.get(u) for u in live[i + 1:]]
            if any(u and own[u["pr"]] not in ("blocked", "gone") for u in upper):
                top = max(u["pr"] for u in upper if u and own[u["pr"]] not in ("blocked", "gone"))
                e["state"], e["reasons"] = "waiting", [f"lands with its stack from #{top}"] + e["reasons"]
                continue
            if held := [b for b in live[:i] if on_hold(events, b)]:  # the merge takes that layer too
                e["state"], e["reasons"] = "blocked", [f"stack layer {', '.join(f'#{b}' for b in held)} on hold"] + e["reasons"]
                continue
            unready =[f"#{b} {own.get(b, 'not in the program')}" for b in live[:i] if own.get(b) != "ready"]
            if unready and e["state"] == "ready":
                e["state"], e["reasons"] = "catching_up", [f"stack layer {', '.join(unready)}"] + e["reasons"]
    out.sort(key=lambda e: (e["state"] == "gone", e["rank"], -e["unblocks"], e["since"], e["pr"]))
    return out


def exclusive_turn(order, pr):
    """The exclusive-lane entry that goes before this one on its base, or None when it is this PR's turn.

    Only exclusive entries that could land soon (ready, or catching up) and asked to land contend.
    Blocked and waiting ones are passed over, so a stuck PR never holds the lane; so is one merged
    outside land, which would never use its turn. Passing over leaves the order alone: a PR that
    calls land contends from its old place.
    """
    base = next(e for e in order if e["pr"] == pr).get("base")
    for e in order:
        if e["pr"] == pr:
            return None
        if e["exclusive"] and e["claimed"] and e.get("base") == base and e["state"] in ("ready", "catching_up"):
            return e
    return None


def pr_files(repo, pr):
    return [f["path"] for f in gh_json("pr", "view", str(pr), "--repo", repo, "--json", "files").get("files", [])]


def is_exclusive(cfg, files):
    pats = knob(cfg, "exclusive_paths")
    return sorted(f for f in files if any(fnmatch.fnmatch(f, g) for g in pats))


REST_FILES_CAP = 3000


def rest_files(repo, pr):
    # pr view stops at 100 files; the REST listing pages up to 3000.
    out = run(["gh", "api", f"repos/{repo}/pulls/{pr}/files", "--paginate", "--jq", ".[].filename"], check=False)
    return [f for f in out.stdout.split("\n") if f] if out.returncode == 0 else None


def program_prs(events, rows, tickets):
    """{open PR: ticket or None} for the program: PRs the ledger names, plus open PRs whose head branch or title
    carries a program ticket. The ledger learns a PR only at `ready`, and two parallel PRs collide before that."""
    led, open_ = pr_ticket(events), {r["number"]: r for r in rows}
    landed = {s["pr"] for s in pr_state(events).values() if "landed" in s}
    out = {pr: led.get(pr) for pr in pr_state(events) if pr in open_ and pr not in landed}
    for n, r in open_.items():
        text = f"{r.get('headRefName') or ''} {r.get('title') or ''}".upper()
        hit = next((t for t in sorted(tickets, key=len, reverse=True)
                    if re.search(rf"(?<![A-Z0-9]){re.escape(t.upper())}(?!\d)", text)), None)
        if hit and n not in landed:
            out[n] = out.get(n) or hit
    return out


def related(nodes, rows, stacks=None, edges=()):
    """find(n) over PRs, equal for two PRs tied by an edge, one GitHub stack, or one PR's base being the other's
    head, transitively."""
    root = {n: n for n in [*nodes, *(r["number"] for r in rows), *(m for s in (stacks or {}).values() for m in s)]}

    def find(n):
        while root[n] != n:
            n = root[n]
        return n

    def join(a, b):
        if a in root and b in root:
            root[find(a)] = find(b)
    for x, y in edges:
        join(x, y)
    for members in (stacks or {}).values():
        for x, y in zip(members, members[1:]):
            join(x, y)
    head = {r.get("headRefName"): r["number"] for r in rows}
    for r in rows:
        join(r["number"], head.get(r.get("baseRefName")))
    return find


def path_hit(p, f):
    """A planned path p (a file, a directory or a glob) covers changed file f."""
    return f == p or fnmatch.fnmatch(f, p) or f.startswith(p.rstrip("/") + "/")


def path_overlaps(slug, files, prs, cfg, rows, after, stacks=None):
    """OVERLAP lines for two open PRs that change one file, or files under one exclusive_paths glob, with no
    land-after relation: whichever merges first leaves the other in conflict, and the fix push dismisses its
    approval. A is the lower PR number, which the remedy keeps as the base.

    files {pr: [paths]}, prs {pr: ticket or None}, after {ticket: tickets it lands after}. PRs are related
    through `orch dep` / Orca deps, one GitHub stack, or one PR's base being the other's head, transitively,
    so the ends of a chain A <- B <- C are not reported, even when B is not a program PR."""
    by_ticket = {}
    for n, t in prs.items():
        by_ticket.setdefault(t, []).append(n)
    edges = [(x, y) for t, below in after.items() for b in below
             for x in by_ticket.get(t, []) for y in by_ticket.get(b, [])]
    find = related(files, rows, stacks, edges)
    pats = knob(cfg, "exclusive_paths")
    out = []
    for a in sorted(files):
        for b in sorted(files):
            if b <= a or find(a) == find(b):
                continue
            shared = sorted(set(files[a]) & set(files[b]))
            if not shared:
                shared = [g for g in pats if any(fnmatch.fnmatch(f, g) for f in files[a])
                          and any(fnmatch.fnmatch(f, g) for f in files[b])]
            if not shared:
                continue
            more = f" (+{len(shared) - 1} more)" if len(shared) > 1 else ""
            ra = next((r for r in rows if r["number"] == a), {})
            # A merge, not a rebase: the conflict is settled once now, before review, and nothing is force-pushed.
            out.append(f"OVERLAP #{a} #{b} {shared[0]}{more} → stack #{b} on #{a} (orch dep): "
                       f"in #{b}'s worktree git merge origin/{ra.get('headRefName') or f'<#{a} branch>'} and push;"
                       f" gh stack link {a} {b} --base {ra.get('baseRefName') or 'main'};"
                       f" orch dep {slug} --ticket {prs.get(b) or '<ticket>'} --after {prs.get(a) or '<ticket>'}")
    return out


def plan_overlaps(slug, paths, files, prs, cfg, rows):
    """OVERLAP lines for a unit not started yet: the open PRs that change one of its planned paths (a file, a
    directory or a glob), or a file under the same exclusive_paths glob. The unit is to be stacked on that PR."""
    pats = knob(cfg, "exclusive_paths")
    out = []
    for n in sorted(files):
        shared = sorted({p for p in paths for f in files[n] if path_hit(p, f)})
        if not shared:
            shared = [g for g in pats if any(fnmatch.fnmatch(p, g) for p in paths)
                      and any(fnmatch.fnmatch(f, g) for f in files[n])]
        if shared:
            more = f" (+{len(shared) - 1} more)" if len(shared) > 1 else ""
            branch = next((r.get("headRefName") for r in rows if r["number"] == n), None) or f"<#{n}'s branch>"
            out.append(f"OVERLAP plan #{n} {shared[0]}{more} → stack the unit on #{n} (orch dep): "
                       f"Base: stacked on #{n} ({branch}); orch dep {slug} --ticket <ticket> --after {prs.get(n) or '<ticket>'}")
    return out


def program_files(cfg, events, rows, tasks):
    """({open program PR: ticket or None}, {PR: changed files}); a PR whose files gh cannot list is left out."""
    tickets = (set(cfg.get("predicate") or []) | set(pr_ticket(events).values()) | {ticket_of(t) for t in tasks}) - {None}
    prs = program_prs(events, rows, tickets)
    files = {n: f for n in prs for f in [rest_files(cfg["repo"], n)] if f is not None}
    return prs, files


def other_program_files(slug, repo, mine, rows, list_files=rest_files):
    """{other program on repo: {open PR: changed files, or None when unreadable}, or None when its state is
    unreadable}. Its PRs are the ones its own ledger names, not PRs guessed from a branch or title: short
    predicate ids repeat across programs, and a PR in `mine` is left to this program."""
    open_, out = {r["number"] for r in rows}, {}
    for d in sorted(HOME.iterdir()) if HOME.is_dir() else []:
        if d.name == slug or d.name.startswith((".", "_")) or not (d / "program.json").is_file():
            continue
        try:
            theirs_repo = json.loads((d / "program.json").read_text())["repo"]
            if theirs_repo.lower() != repo.lower():
                continue
            led = d / "ledger.jsonl"
            events = [json.loads(l) for l in led.read_text().splitlines() if l.strip()] if led.exists() else []
            if not all(isinstance(e.get("ev"), str) for e in events):
                raise TypeError("a ledger row with no event")
            states = pr_state(events)
            if not all(type(n) is int for n in states):
                raise TypeError("a ledger PR number that is not an integer")
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            out[d.name] = None
            continue
        # open_rows stops at OPEN_LIMIT: at the limit, a PR missing from it may still be open.
        out[d.name] = {n: list_files(repo, n) if n in open_ else None for n in sorted(states)
                       if (n in open_ or len(rows) >= OPEN_LIMIT) and n not in mine and "landed" not in states[n]}
    return out


def cross_inputs(slug, repo, events, rows, prs, files, list_files=rest_files):
    """(prs, files, theirs, unread): this program's PRs less those another program's ledger names (a PR matched
    only by a ticket in its branch or title loses to a ledger), other_program_files, and this program's PRs
    whose file list could not be read."""
    theirs = other_program_files(slug, repo, set(pr_state(events)), rows, list_files)
    claimed = {n for x in theirs.values() for n in x or {}}
    prs = {n: t for n, t in prs.items() if n not in claimed}
    return prs, {n: f for n, f in files.items() if n in prs}, theirs, sorted(set(prs) - set(files))


def cross_overlaps(files, theirs, rows, stacks=None, paths=None, unread=()):
    """Warning lines for this program's open PRs (or planned paths) that change a file another program's open PR
    changes, one per shared file. No stack or `orch dep` command: a dep names this program's tickets only, and
    which side lands first is the two leads' call. PRs already stacked are skipped; what could not be read is
    UNKNOWN, including this program's own PRs in unread. files {pr: [paths]}, theirs from other_program_files."""
    out = [f"OVERLAP UNKNOWN {other} → its program state could not be read; compare it by hand"
           for other in sorted(theirs) if theirs[other] is None]
    out += [f"OVERLAP UNKNOWN #{n} → this program's file list for it could not be read; compare it by hand"
            for n in unread]
    out += [f"OVERLAP UNKNOWN {other} #{n} → its file list could not be read; compare it by hand"
            for other in sorted(theirs) for n, changed in sorted((theirs[other] or {}).items()) if changed is None]
    if not files and not paths:
        return out
    find = related([*files, *(n for prs in theirs.values() for n in prs or {})], rows, stacks)
    warn = "→ warning: settle the landing order with the other lead through PARENT"
    for other in sorted(o for o in theirs if theirs[o]):
        for n, changed in sorted(theirs[other].items()):
            if changed is None:
                continue
            if paths:
                out += [f"OVERLAP {other} plan #{n} {f} {warn}"
                        for f in sorted({f for p in paths for f in changed if path_hit(p, f)})]
            else:
                out += [f"OVERLAP {other} #{a} #{n} {f} {warn}" for a in sorted(files) if find(a) != find(n)
                        for f in sorted(set(files[a]) & set(changed))]
    return out


def repo_verdicts(repo, home=None):
    """({PR: its latest verdict event across every program on `repo`}, ["program ledger X" for each unreadable one]).
    A PR can be in several programs' ledgers; the newest verdict is the one that counts, and at the same second a
    failing one wins over a passing one, so the table never reads ready on a coin flip."""
    home = home or HOME
    best, unknown = {}, []
    try:
        dirs = sorted(home.iterdir())
    except FileNotFoundError:
        return best, unknown
    except OSError:
        return best, ["program ledgers"]
    for d in dirs:
        if d.name.startswith((".", "_")):
            continue
        try:
            cfg = (d / "program.json").read_text()
        except (FileNotFoundError, NotADirectoryError):
            continue  # not a program directory
        except (OSError, ValueError):
            unknown.append(f"program ledger {d.name}")
            continue
        try:
            if json.loads(cfg)["repo"].lower() != repo.lower():
                continue
            led = d / "ledger.jsonl"
            try:
                text = led.read_text()
            except FileNotFoundError:
                text = ""  # a program that has not recorded anything yet
            events = [json.loads(l) for l in text.splitlines() if l.strip()]
            mine = dict(best)  # merged into `best` only when this whole ledger read, so a bad one adds nothing
            for e in events:
                if e.get("ev") == "verdict" and type(e.get("pr")) is int:
                    ts = parse_ts(e["ts"])
                    if ts.tzinfo is None:
                        raise ValueError("timestamp without a zone")
                    old = mine.get(e["pr"])
                    if old is None or ts > old[0] or (ts == old[0] and e.get("result") != "pass"):
                        mine[e["pr"]] = (ts, e)
            best = mine
        except (OSError, ValueError, AttributeError, TypeError, KeyError):  # UnicodeDecodeError is a ValueError
            unknown.append(f"program ledger {d.name}")
    return {n: e for n, (_, e) in best.items()}, unknown


def table_facts(rows, verdicts):
    """{PR: what overlap_table orders by} from the open PR listing and repo_verdicts."""
    out = {}
    for r in rows:
        failed, pending, _ = ci_summary(r.get("statusCheckRollup"))  # no checks reads pending, even on a no-CI repo
        v = verdicts.get(r["number"])
        state = ("none" if not v else "fail" if v.get("result") != "pass"
                 else "current" if v.get("sha") == r.get("headRefOid") else "stale")
        out[r["number"]] = {"verdict": state, "ci": "failed" if failed else "pending" if pending else "ok",
                            "merge_state": r.get("mergeStateStatus"), "draft": bool(r.get("isDraft")),
                            "base": r.get("baseRefName") or "main"}
    return out


def cmd_overlap_table(p, argv):
    """The hot files of every open PR on the repo, whichever program owns it, and the order to land them in."""
    import overlap_table
    repo = p.cfg["repo"]
    limit = opt(argv, "--max-open")
    if limit is not None and not (limit.isdigit() and int(limit) > 0):
        sys.exit("--max-open takes a positive integer")
    verdicts, unknown = repo_verdicts(repo)
    try:
        rows = open_rows(repo)
    except (SystemExit, Exception):
        rows = []
        unknown.append("open PR listing")
    if len(rows) >= OPEN_LIMIT:
        unknown.append(f"open PR listing (stops at {OPEN_LIMIT})")
    files, unread = {}, []
    for r in rows:
        try:
            changed = rest_files(repo, r["number"])
        except (OSError, ValueError):
            changed = None
        # A listing that stops at the REST cap may have dropped files, so it counts as unread.
        if changed is None or len(changed) >= REST_FILES_CAP:
            unread.append(r["number"])
        else:
            files[r["number"]] = changed
    print("\n".join(overlap_table.hot_table(files, table_facts(rows, verdicts),
                                            int(limit) if limit else None, unread, unknown)))


def cmd_overlap(argv):
    p = Program(argv[0])
    if "--table" in argv:
        return cmd_overlap_table(p, argv)
    rows, tasks = open_rows(p.cfg["repo"]), run_tasks(p.cfg["run"])
    events = p.events()
    prs, files, theirs, unread = cross_inputs(p.slug, p.cfg["repo"], events, rows,
                                              *program_files(p.cfg, events, rows, tasks))
    paths = [x for x in (opt(argv, "--paths") or "").split(",") if x]
    stacks = repo_stacks(p.cfg["repo"])
    if paths:
        lines = plan_overlaps(p.slug, paths, files, prs, p.cfg, rows)
    else:
        lines = path_overlaps(p.slug, files, prs, p.cfg, rows, dep_map(events, deps_from_tasks(tasks)), stacks)
    lines += cross_overlaps(files, theirs, rows, stacks, paths, unread)
    print("\n".join(lines) or f"no overlap among {len(files)} open program PR(s)"
          f" and {sum(len(x or {}) for x in theirs.values())} open PR(s) of other programs on {p.cfg['repo']}")


TEST_RESULTS = "<!-- test-results -->"
NO_VISUAL = "no-visual-change"
FAILED_RUN = ("failure", "timed_out", "startup_failure")


def rendered_images(body):
    """Images the comment renders: markdown or <img> pointing at a URL, outside code and HTML comments."""
    body = re.sub(r"(?s)<!--.*?-->|```.*?(```|$)|~~~.*?(~~~|$)|`[^`\n]*`", " ", body)
    return re.findall(r"!\[[^\]]*\]\(\s*<?https?://[^)\s>]+|<img\b[^>]*\bsrc\s*=\s*[\"']?https?://", body, re.I)


def explained(body, run_id):
    """A line names the run and says something beyond its id and link."""
    for line in body.splitlines():
        if re.search(rf"(?<!\d){run_id}(?!\d)", line) and \
                len(re.findall(r"\w", re.sub(r"https?://\S+", " ", line).replace(run_id, " "))) >= 10:
            return True
    return False


def results_comment(comments):
    """The first marker comment's body, the one sticky-comment.sh keeps updating; "" when there is none."""
    return next((c.get("body") or "" for c in comments or [] if TEST_RESULTS in (c.get("body") or "")), "")


def no_results_problem(pr):
    return (f"no {TEST_RESULTS} comment on #{pr}: post the test results"
            " (deliver-ticket sticky-comment.sh), then land again")


def evidence_problems(cfg, repo, pr, need_comment=False):
    """(problems, notes) from the PR's sticky test-results comment. The brief's rule alone once let a screen
    change land with no screenshot, so land checks it: an image for a visual change unless a person labelled
    the PR no-visual-change, and each failed CI run of the branch named with what resolved it. need_comment: the
    land executor also refuses a PR with no such comment, so its land-check asks for it too."""
    v = gh_json("pr", "view", str(pr), "--repo", repo, "--json", "headRefName,labels,comments")
    body = results_comment(v.get("comments"))
    problems, notes = [], []
    if need_comment and not body:
        problems.append(no_results_problem(pr))
    pats = knob(cfg, "visual_paths")
    # pr view stops at 100 files; the REST listing pages up to 3000.
    files = run(["gh", "api", f"repos/{repo}/pulls/{pr}/files", "--paginate", "--jq", ".[].filename"]).stdout.split("\n")
    visual = [f for f in files if f and any(fnmatch.fnmatch(f, g) for g in pats)]
    if visual and NO_VISUAL in {l.get("name") for l in v.get("labels") or []}:
        notes.append(f"#{pr} changes the screen ({', '.join(visual[:3])}) and is exempt from screenshots by label {NO_VISUAL}")
    elif visual and not rendered_images(body):
        problems.append(f"#{pr} changes the screen ({', '.join(visual[:3])}) but its {TEST_RESULTS} comment"
                        f" {'renders no image' if body else 'does not exist'}: upload the screenshots into it"
                        f" (deliver-ticket visual-evidence); only a person exempts it, with the label {NO_VISUAL}")
    # The branch name alone also matches a fork's `main` or an earlier PR's reused branch; keep this PR's commits.
    # pr view returns the oldest 100 commits only; the REST listing pages up to 250, newest included.
    shas = set(run(["gh", "api", f"repos/{repo}/pulls/{pr}/commits", "--paginate", "--jq", ".[].sha"]).stdout.split())
    runs = gh_json("run", "list", "--repo", repo, "--branch", v["headRefName"], "--json", "databaseId,conclusion,name,headSha",
                   "--limit", "200")
    unexplained = [f"{r['databaseId']} ({r.get('name')} {r['conclusion']})" for r in runs
                   if r.get("conclusion") in FAILED_RUN and r.get("headSha") in shas
                   and not explained(body, str(r["databaseId"]))]
    if unexplained:
        problems.append(f"#{pr} has failed CI runs its {TEST_RESULTS} comment does not account for:"
                        f" {', '.join(unexplained)}; add a line per run with its id and what resolved it")
    return problems, notes


def compare(repo, base, head):
    return gh_json("api", f"repos/{repo}/compare/{base}...{head}")


def base_overlap(repo, base, c):
    """Files this PR changes that the base also changed since the PR branched, or [] when not behind.

    Without required up-to-date branches a PR merges while behind; this is the mechanical floor of
    the pre-merge self-check (the worker still judges contracts and test assumptions itself).
    """
    if not c.get("behind_by"):
        return []
    mine = {f["filename"] for f in c.get("files") or []}
    moved = gh_json("api", f"repos/{repo}/compare/{c['merge_base_commit']['sha']}...{base}")
    return sorted(mine & {f["filename"] for f in moved.get("files") or []})


OPEN_LIMIT = 200


def open_rows(repo):
    return gh_json("pr", "list", "--repo", repo, "--state", "open", "--limit", str(OPEN_LIMIT), "--json",
                   "number,title,headRefOid,headRefName,mergeStateStatus,isDraft,statusCheckRollup,baseRefName")


def read_stacks(repo):
    """{stack number: [open PR numbers bottom→top]} of the open stacks, or None when GitHub did not answer in that
    shape. https://docs.github.com/en/rest/pulls/stacks. A finished stack stays listed with `open` false and the
    listing has no state filter, so all pages are read.
    """
    r = run(["gh", "api", f"repos/{repo}/stacks?per_page=100", "--paginate", "--slurp"], check=False)
    try:
        return {s["number"]: [p["number"] for p in s["pull_requests"] if p["state"] == "open"]
                for page in json.loads(r.stdout) for s in page if s["open"]} if r.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        return None


def repo_stacks(repo):
    """read_stacks with an unreadable listing as no stacks: it turns stack-aware landing off, never landing itself."""
    return read_stacks(repo) or {}


def in_a_stack(repo, pr):
    """The PR's own `stack` field says it is in one. False also when the field cannot be read."""
    r = run(["gh", "api", f"repos/{repo}/pulls/{pr}", "--jq", ".stack"], check=False)
    return r.returncode == 0 and r.stdout.strip() not in ("", "null")


def stack_level():
    """`orchestrate.stack_dependent` of ~/.claude/agent-skills.json, read as hooks/lib/config.level reads it, warn
    by default. hooks/stack_gate.py reads the same key."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        value = json.loads(path.read_text())["orchestrate"]["stack_dependent"]
    except (OSError, ValueError, KeyError, TypeError):
        return "warn"
    return "off" if value is False else value if value in ("deny", "off") else "warn"


def stack_gaps(events, rows, stacks, deps, pr):
    """(gaps, flipped) among the land-after edges at this PR whose two PRs are both open, as (lower, upper) pairs.
    A gap is an edge outside any stack. Flipped is an edge a stack holds upside down: the upper waits for the lower's
    layer and the dep waits the other way, so neither ever lands."""
    after = dep_map(events, deps)
    prs = program_prs(events, rows, set(after) | {t for lowers in after.values() for t in lowers})
    mine = prs.get(pr)
    edges = [(n, pr) for n, t in prs.items() if t in after.get(mine, ())] + \
            [(pr, n) for n, t in prs.items() if mine in after.get(t, ())] if mine else []
    gaps, flipped = [], []
    for low, up in edges:
        members = next((m for m in stacks.values() if low in m and up in m), None)
        if members is None:
            gaps.append((low, up))
        elif members.index(low) > members.index(up):
            flipped.append((low, up))
    return gaps, flipped


def gap_text(repo, gaps, base):
    return "; ".join(f"#{up} lands after #{low} and they are not a GitHub stack: gh stack link {low} {up} --base {base},"
                     f" confirm with gh api repos/{repo}/pulls/{up} --jq .stack" for low, up in gaps)


FLIPPED = "the stack has #{up} below #{low}, which it lands after, so neither can land: fix the dep or the stack's order"


def patch_id(repo, pr):
    r = run(["gh", "pr", "diff", str(pr), "--repo", repo], check=False)
    # gh refuses a PR over 300 files (HTTP 406); the local diff reproduces GitHub's, so a verdict's id still matches.
    diff = r.stdout if r.returncode == 0 else local_diff(repo, pr, r.stderr.strip()[:200])
    # --verbatim: whitespace-only edits (Python indentation) must change the id and void the verdict.
    ids = subprocess.run(["git", "patch-id", "--verbatim"], input=diff, capture_output=True, text=True).stdout.split()
    if not ids:
        # An empty id equals every other empty id, so a later recheck would pass without comparing anything.
        sys.exit(f"#{pr}: empty patch-id (no diff against its base); nothing to record or compare")
    return ids[0]


def local_diff(repo, pr, gh_err):
    """#pr's merge-base diff from a cached clone, flagged to match GitHub's: renames on, myers."""
    v = pr_view(repo, pr)
    g = GIT_CACHE / f"{repo.replace('/', '__')}.git"
    # The diff runs offline without user config or GIT_* variables (diff.context, GIT_DIFF_OPTS and the like
    # change the id); fetch keeps them for proxies and CAs, minus any that would select another repository.
    local = set(run(["git", "rev-parse", "--local-env-vars"]).stdout.split())
    fetch_env = {k: x for k, x in os.environ.items() if k not in local}
    offline = {**{k: x for k, x in os.environ.items() if not k.startswith("GIT_")},
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_LAZY_FETCH": "1"}

    def git(*a, check=True, env=fetch_env):
        # The gh helper authenticates private repos.
        r = subprocess.run(["git", "-C", str(g), "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", *a],
                           capture_output=True, text=True, env=env)
        if check and r.returncode != 0:
            sys.exit(f"#{pr}: no patch-id: gh pr diff failed ({gh_err}) and git {a[0]} failed too: {r.stderr.strip()[:400]}")
        return r

    GIT_CACHE.mkdir(parents=True, exist_ok=True)
    # Two landings falling back at once would race on init and on the fetched refs.
    with (GIT_CACHE / f"{g.name}.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        g.mkdir(exist_ok=True)
        git("init", "-q", "--bare")
        if git("remote", "get-url", "origin", check=False).returncode != 0:
            git("remote", "add", "origin", f"https://{os.environ.get('GH_HOST', 'github.com')}/{repo}")
        git("fetch", "-q", "origin", f"+refs/heads/{v['baseRefName']}:refs/base/{v['baseRefName']}",
            f"+refs/pull/{pr}/head:refs/pull/{pr}")
        mb = git("merge-base", f"refs/base/{v['baseRefName']}", v["headRefOid"], env=offline).stdout.strip()
        return git("diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", "--diff-algorithm=myers",
                   "--src-prefix=a/", "--dst-prefix=b/", mb, v["headRefOid"], env=offline).stdout


# No checks already holds (ci_summary), so statusCheckRollup is not among them.
LAND_CHECK_FIELDS = ("state", "isDraft", "headRefOid", "mergeStateStatus", "url")


def pr_view(repo, pr):
    return gh_json("pr", "view", str(pr), "--repo", repo, "--json",
                   "state,isDraft,headRefOid,headRefName,baseRefName,mergeStateStatus,mergeable,statusCheckRollup,mergeCommit,url,title")


def no_ci_repo(repo):
    """The human declared this repo CI-less in agent-skills.json (orchestrate.no_ci_repos). A user file, not a program
    key: `orch set` raises no dialog, and a worker must not be able to drop CI from its own landing. Unreadable: False."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        repos = (json.loads(path.read_text()).get("orchestrate") or {}).get("no_ci_repos")
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(repos, list) and repo in repos


def check_run_id(c):
    url = c.get("detailsUrl") or c.get("targetUrl") or ""
    return url.split("/actions/runs/")[1].split("/")[0] if "/actions/runs/" in url else ""


def latest_checks(rollup):
    """The newest entry of each check. A head keeps every run started at it, so a run superseded by a PR edit
    (cancel-in-progress) stays in the rollup as CANCELLED beside the run that replaced it. The workflow is part of the
    check's identity: one workflow's pass must not hide another's failure under the same job name. A check still going
    outranks a finished one, since a queued run has no start time and an older pass must not stand in for it. Start
    time decides before the run id, as a rerun keeps its old, lower id. A check that never started is older than a start
    after its end; past that its end says nothing, so only the run id places it against the started ones."""
    def started(c):
        t = c.get("startedAt") or ""  # gh prints the zero time for a check that never started
        return "" if t.startswith("0001-") else t

    def going(c):
        return (c.get("status") or "COMPLETED").upper() != "COMPLETED"

    def run(c):
        r = check_run_id(c)
        return int(r) if r.isdigit() else 0

    def newest(checks):
        timed = [c for c in checks if started(c)]
        # Starts in one second are unordered and the id cannot settle them, so a pass does not win that tie.
        rank = lambda c: (going(c), started(c), (c.get("conclusion") or "").upper() not in PASSING, run(c))
        best = [max(timed, key=rank)] if timed else []
        rest = [c for c in checks if not started(c)
                and not (best and not going(c) and (c.get("completedAt") or "~") < started(best[0]))]
        return max(best + rest, key=lambda c: (going(c), run(c)))
    groups = {}
    for c in rollup:
        groups.setdefault((c.get("workflowName"), c.get("name") or c.get("context")), []).append(c)
    kept = []
    for checks in groups.values():
        last = newest(checks)
        # Only another run replaces a job: two jobs of one run may share a display name.
        kept += [c for c in checks if c is last or (run(last) and run(c) == run(last))]
    return kept


def ci_summary(rollup, no_ci=False):
    # No checks on a head usually means CI has not reported yet, so it never counts as green. Only a declared no-CI
    # repo reads a confirmed empty rollup as nothing to wait for; a missing one stays unknown.
    if not rollup:
        return [], [] if no_ci and rollup is not None else ["no CI checks reported yet"], []
    failed, pending, runs = [], [], set()
    for c in latest_checks(rollup):
        name = c.get("name") or c.get("context")
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        status = (c.get("status") or "COMPLETED").upper()
        if run := check_run_id(c):
            runs.add(run)
        if status != "COMPLETED" or concl in ("", "PENDING", "EXPECTED"):
            pending.append(name)
        elif concl not in PASSING:
            failed.append(f"{name}={concl}")
    return failed, pending, sorted(runs)


def runs_unfinished(repo, sha, no_ci=False):
    """Workflow runs at `sha` that have not finished. The check rollup alone is not enough: a bot's status
    (a review app) can be its only entry before Actions starts, and a job behind `needs` has no check until
    the jobs before it end, so an early all-green rollup once landed a PR before its CI ran."""
    try:
        rs = gh_json("run", "list", "--repo", repo, "--commit", sha, "--json", "status,name", "--limit", "100")
    except (SystemExit, Exception) as e:
        return [f"workflow runs unreadable ({type(e).__name__})"]
    if not rs:
        return [] if no_ci else ["no workflow run at the head yet"]
    return [f"{r.get('name')} {r.get('status')}" for r in rs if r.get("status") != "completed"]


def tracker_issues(cfg, ids=()):
    """Normalized issues (use-tracker schema) for the program's tracker project, or None if unset.
    `ids` that the project list lacks (tickets filed elsewhere) are fetched one by one."""
    t = cfg.get("tracker") or {}
    proj = t.get("project") or cfg.get("linear_project")  # linear_project: programs made before the adapter
    if not proj:
        return None
    base = [sys.executable, str(TRACKER)] + (["--adapter", t["adapter"]] if t.get("adapter") else [])
    issues = json.loads(run(base + ["list", "--project", proj]).stdout)
    have = {i.get("id") for i in issues}
    for tid in dict.fromkeys(ids):
        if tid in have:
            continue
        r = run(base + ["get", tid], check=False)
        if r.returncode == 0:
            got = json.loads(r.stdout)
            issues.append(got.get("issue", got) if isinstance(got, dict) else got)
    return issues


def admitted_tickets(events):
    """Tickets whose latest triage is `admitted`."""
    triage = {}
    for e in events:
        if e["ev"] in ("admitted", "parked") and e.get("ticket"):
            triage[e["ticket"]] = e["ev"]
    return [t for t, ev in triage.items() if ev == "admitted"]


def gate_rows(events, pr, warn=True):
    """The PR's gate_opened rows that name both their Task and gate. A row missing either is no gate anyone can
    resolve, so it is skipped (with one warning line) and `orch gate` may open a new one."""
    rows = [e for e in events if e["ev"] == "gate_opened" and e.get("pr") == pr]
    good = [e for e in rows if e.get("task") and e.get("gate")]
    if warn and len(good) < len(rows):
        bad = [e for e in rows if e not in good]
        print(f"warning: skipped {len(bad)} gate_opened row(s) for #{pr} with no task or gate: {json.dumps(bad[-1])[:300]}",
              file=sys.stderr)
    return good


def gate_resolution(cfg, events, pr, warn=True):
    """The resolution of the newest landing gate opened for this PR, or None while it is pending."""
    opened = gate_rows(events, pr, warn)
    if not opened:
        return None
    res = orca_json("orchestration", "gate-list", "--task", opened[-1]["task"], "--run", cfg["run"])
    for g in res.get("gates", res if isinstance(res, list) else []):
        if g.get("id") == opened[-1]["gate"] and g.get("status") == "resolved":
            return g.get("resolution")
    return None


def gate_hold(p, events, pr):
    """None when the human gate clears this PR, else why not. An approval holds only until a newer gate opens."""
    rows = [e for e in events if e.get("pr") == pr and e["ev"] in ("gate_opened", "approved")]
    if rows and rows[-1]["ev"] == "approved":
        return None
    resolution = gate_resolution(p.cfg, events, pr)
    if resolution == "land":
        p.append("approved", pr=pr, note="orca gate resolved: land")
        return None
    if resolution:
        close_gate_task(p, pr, "failed")
        return f"human-gate: the user resolved the gate as '{resolution}'"
    if gate_rows(events, pr, warn=False):
        return "human-gate: gate open in Orca, waiting for the user"
    return f"human-gate: open the gate first (orch gate {p.slug} --pr {pr})"


GATE_TASK = "Land #"


def close_gate_task(p, pr, status):
    """No worker settles the lead-owned `Land #N` Task, so it would sit in `task-list --ready` for good:
    completed when the PR lands, failed when the gate is held or the PR is closed. A new gate makes a new Task."""
    opened = gate_rows(p.events(), pr, warn=False)
    if opened:
        run(["orca", "orchestration", "task-update", "--id", opened[-1]["task"], "--status", status,
             "--run", p.cfg["run"], "--json"], check=False)


def is_derived(issue):
    """The follow-up convention from use-tracker: label `follow-up` or a first body line `파생: …`."""
    return "follow-up" in (issue.get("labels") or []) or (issue.get("description") or "").lstrip().startswith("파생:")


def skills_version(root):
    """A commit reflect can later diff against HEAD. An installed plugin sits in a cache dir named by its
    commit; a local commit may never reach the repo, so a checkout ahead of origin records its pushed base."""
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True).stdout.strip()
    head = git("rev-parse", "--short", "HEAD")
    if not head:
        return root.name
    pushed = bool(git("branch", "-r", "--contains", "HEAD"))
    if pushed and not git("status", "--porcelain", "--", "skills"):
        return head
    base = head if pushed else git("merge-base", "HEAD", "origin/main")
    return (git("rev-parse", "--short", base) if base else head) + "+local"


STANDALONE_RUN = "standalone"  # a standalone card has no Orca Run; Run lookups on it answer empty


def repo_meta(repo):
    """{"nameWithOwner", "isFork", "isArchived"} of the repo GitHub serves under `repo` now (a rename or a transfer
    redirects). REST, not `gh repo view`: the executor asks every round, and GraphQL's budget is the one that runs out."""
    v = gh_json("api", f"repos/{repo}")
    return {"nameWithOwner": v["full_name"], "isFork": bool(v.get("fork")), "isArchived": bool(v.get("archived"))}


def standalone_run():
    """The Run the existing `*-standalone` programs share, else the placeholder. Programs that disagree, or none,
    give the placeholder: guessing one of several Runs would file a repo's PRs under the wrong coordinator."""
    runs = set()
    for f in HOME.glob("*-standalone/program.json"):
        try:
            runs.add(json.loads(f.read_text()).get("run"))
        except (OSError, ValueError, AttributeError):
            return STANDALONE_RUN
    runs.discard(STANDALONE_RUN)
    return runs.pop() if len(runs) == 1 and all(isinstance(r, str) and r for r in runs) else STANDALONE_RUN


def standalone_program(argv):
    """The `<name>-standalone` program of the repo `orch land --repo OWNER/NAME` names, created when missing.

    Only where the land executor covers the repo under the name GitHub gives it now (a renamed repo answers with its
    new name): elsewhere a standalone card merges itself and needs no program.
    """
    asked = opt(argv, "--repo")
    if not asked or asked.count("/") != 1 or not argv[0].endswith("-standalone"):
        sys.exit(f"no program '{argv[0]}'; a standalone PR lands as `orch land <name>-standalone --repo OWNER/NAME`")
    ex = land_executor()
    if not ex:
        sys.exit(f"no program '{argv[0]}', and orchestrate.land_executor is off: a standalone card merges its own PR")
    meta = repo_meta(asked)
    repo = meta["nameWithOwner"]
    if not land_scope.how(ex["scope"], repo) or land_scope.excluded(ex["scope"], asked):
        now_named = "" if repo.casefold() == asked.casefold() else f" ({repo} on GitHub now)"
        sys.exit(f"no program '{argv[0]}', and orchestrate.land_executor is off for {asked}{now_named}: a standalone"
                 " card merges its own PR")
    if land_scope.how(ex["scope"], repo) == "owner":
        login = run(["gh", "api", "user", "--jq", ".login"]).stdout.strip()
        if why := land_scope.owner_refused(ex["scope"], repo, login) or land_scope.wildcard_only(ex["scope"], repo, meta):
            sys.exit(f"{why}; nothing created")
    slug = f"{repo.split('/')[-1]}-standalone"
    if slug.casefold() != argv[0].casefold():
        sys.exit(f"{asked} is {repo} on GitHub now: name its program {slug} (`orch land {slug} --repo {repo} …`)")
    slug = argv[0]
    d = HOME / slug
    cfg = {"slug": slug, "repo": repo, "run": opt(argv, "--run") or standalone_run(), "tracker": {}, "predicate": [],
           "merge_policy": "autonomous", "ceiling": 6, "deadline": None, "created_at": now(),
           "skills_commit": skills_version(pathlib.Path(__file__).resolve().parents[3]), "note": "standalone cards",
           "final_check": None}
    try:
        write_program(d, cfg, cfg["run"], "autonomous", lead=False)
        print(f"created standing program {slug} for {repo} (merge_policy autonomous, Run {cfg['run']})")
    except FileExistsError:  # another card created it between the check and here
        pass


def standalone_target(argv):
    """The Program for argv[0], created first when `--repo` is given and it is missing. A program that exists must
    belong to that repo: `--repo` is what the executor setting was checked against."""
    if "--repo" in argv and not (HOME / argv[0] / "program.json").exists():
        standalone_program(argv)
    p = Program(argv[0])
    asked = opt(argv, "--repo")
    # An old name reaches the program GitHub's current name made; only a mismatch costs the lookup.
    if asked and p.cfg["repo"].casefold() != asked.casefold() \
            and repo_meta(asked)["nameWithOwner"].casefold() != p.cfg["repo"].casefold():
        sys.exit(f"program '{argv[0]}' is for {p.cfg['repo']}, not {asked}")
    return p


def cmd_init(argv):
    slug = argv[0]
    d = HOME / slug
    if (d / "program.json").exists():
        sys.exit(f"{d}/program.json exists; programs are resumed, not re-initialised")
    repo, run_id = opt(argv, "--repo"), opt(argv, "--run")
    if not repo or not run_id:
        sys.exit("init needs --repo OWNER/NAME and --run RUN_ID")
    policy = opt(argv, "--merge-policy", "autonomous")
    if policy not in ("autonomous", "human-gate"):
        sys.exit("--merge-policy is autonomous or human-gate")
    skills_commit = skills_version(pathlib.Path(__file__).resolve().parents[3])
    if skills_commit.endswith("+local"):
        print(f"note: the skills checkout has unpushed or uncommitted changes; recorded their pushed base {skills_commit}")
    cfg = {
        "slug": slug, "repo": repo, "run": run_id,
        "tracker": {k: v for k, v in {"adapter": opt(argv, "--tracker"),
                                        "project": opt(argv, "--tracker-project") or opt(argv, "--linear-project")}.items() if v},
        "predicate": [p for p in (opt(argv, "--predicate", "") or "").split(",") if p],
        "merge_policy": policy, "ceiling": int(opt(argv, "--ceiling", "6")),
        "deadline": opt(argv, "--deadline"), "created_at": now(), "skills_commit": skills_commit,
        "note": opt(argv, "--note"), "final_check": opt(argv, "--final-check"),
    }
    try:
        write_program(d, cfg, run_id, policy)
    except FileExistsError:  # a standalone `orch land` created it after the check above
        sys.exit(f"{d}/program.json exists; programs are resumed, not re-initialised")
    print(f"initialised {d}")
    print("\n".join(dashboard_lines(slug)))


def write_program(d, cfg, run_id, policy, lead=True):
    """Create program.json for cmd_init and standalone_program; FileExistsError when another caller got there first.
    lead=False skips the dispatched-lead lookup: a standalone program belongs to no lead, whichever card creates it."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    try:
        mine = own_worker(all_workers(run_id), me) if lead and me and run_id != STANDALONE_RUN else None
    except SystemExit as e:  # guessing "coordinating lead" would scope a worker lead's program to the whole Run
        sys.exit(f"orch init: could not list {run_id}'s workers to tell whether this terminal is a dispatched lead"
                 f" ({e}). Retry when `orca orchestration worker-list --run {run_id}` answers.")
    if mine and policy == "human-gate":
        sys.exit(f"orch init: this lead is dispatch {mine.get('dispatchId')} on {run_id}, and a dispatched lead cannot "
                 "land under human-gate: Orca lets no worker create a gate on the Run it works for, so `orch land` "
                 "never sees `approved`. Ask PARENT to restart this lead as a /goal card (its brief's MERGE line "
                 "says human-gate), or init with --merge-policy autonomous.")
    if mine:
        # A lead started with worker-start: its units are the Dispatches under its own, and its mail is its Dispatch's.
        cfg["lead_dispatch"] = mine.get("dispatchId")
        print(f"lead is dispatch {cfg['lead_dispatch']} on {run_id}: `orch wait` reads its own mailbox; start units "
              "with worker-start --spec (Orca refuses a worker's task-create and gate-create on this Run)")
    (d / "briefs").mkdir(parents=True, exist_ok=True)
    # A reader must never see a half-written file: write whole, then link it into place (link fails if one exists).
    tmp = d / f"program.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    try:
        os.link(tmp, d / "program.json")
    finally:
        tmp.unlink()


STALL_TEXT = {
    "idle": "turn ended {minutes} min ago with its task open and no wake-up armed; an orchestration message "
            "does not wake an idle session: send the content to dispatch:{dispatch}, then wake it by "
            "references/waking.md (skills-sync wake-target <terminal>)",
    "start_unconfirmed": "dispatched {minutes} min ago and its session has written nothing: check it got the brief",
    "long_tool": "one {tool} call running for {minutes} min ({detail}): still waiting on purpose?",
    "silent": "its session has written nothing for {minutes} min though its last row is input, not an ended turn "
              "(a notification that arrived after the turn ended looks like this): read its terminal, and if it sits "
              "at the prompt, send the content to dispatch:{dispatch} and wake it as for an idle one",
}


def dash_state(pdir):
    """The collector's state.json, or None when it is missing or older than 3 minutes."""
    try:
        st = json.loads((pdir / "dashboard" / "state.json").read_text())
    except (OSError, ValueError):
        return None
    if not st.get("generated_at") or dt.datetime.now(dt.timezone.utc) - parse_ts(st["generated_at"]) > dt.timedelta(minutes=3):
        return None
    return st


def stall_line(x):
    return f"  STALLED {x.get('ticket') or '?'} ({x['dispatch']}): " + STALL_TEXT[x["kind"]].format(**{"tool": "", "detail": "", **x})


def new_stalls(pdir, renotify=dt.timedelta(minutes=30), skip=frozenset()):
    """STALLED lines `orch wait` has not shown in the last 30 minutes. A lead that only loops `orch wait`
    never reads `orch status`, so a stall shown only there went unseen for hours. skip: dispatches auto-wake holds,
    so the lead is not told to wake them beside it."""
    st = dash_state(pdir)
    if st is None:
        return []
    state, now = pdir / "stalls-seen.json", dt.datetime.now(dt.timezone.utc)
    try:
        seen = json.loads(state.read_text())
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    stalls = [x for x in (st.get("summary") or {}).get("stalls") or [] if x["dispatch"] not in skip]
    fresh = [x for x in stalls if f"{x['dispatch']}:{x['kind']}" not in seen
             or now - parse_ts(seen[f"{x['dispatch']}:{x['kind']}"]) >= renotify]
    keep = {f"{x['dispatch']}:{x['kind']}": seen.get(f"{x['dispatch']}:{x['kind']}") for x in stalls}
    keep.update({f"{x['dispatch']}:{x['kind']}": now.isoformat() for x in fresh})
    try:
        state.write_text(json.dumps({k: v for k, v in keep.items() if v}))
    except OSError:
        pass
    return [stall_line(x) for x in fresh]


def new_leftovers(pdir, renotify=dt.timedelta(minutes=30)):
    """LANDED-BUT-OPEN lines for released cards that `orch wait` has not shown in the last 30 minutes. A worker
    ended with worker-stop sends no worker_done, so no CLOSE OUT line names its card."""
    st = dash_state(pdir)
    if st is None:
        return []
    state, now = pdir / "leftovers-seen.json", dt.datetime.now(dt.timezone.utc)
    try:
        seen = json.loads(state.read_text())
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    # active None: the worker row had no terminalState, so the card is not known to be released (landed_but_open).
    cards = [c for c in st.get("landed_open") or [] if c.get("active") is False]
    fresh = [c for c in cards if c["dispatch"] not in seen or now - parse_ts(seen[c["dispatch"]]) >= renotify]
    keep = {c["dispatch"]: seen.get(c["dispatch"]) for c in cards}
    keep.update({c["dispatch"]: now.isoformat() for c in fresh})
    try:
        state.write_text(json.dumps({k: v for k, v in keep.items() if v}))
    except OSError:
        pass
    if not fresh:
        return []
    # The collector runs in its own process and cannot leave out this session's worktree; do it here.
    here = own_worktree_id()
    try:
        mine = {w.get("path") for w in orca_json("worktree", "list").get("worktrees", []) if here and w.get("id") == here}
    except (SystemExit, Exception):
        mine, here = set(), None
    if here and not mine:  # its id is known but its path is not: any card may be this session's own
        here = None
    return [f"  LANDED-BUT-OPEN {c.get('ticket') or '?'} (dispatch {c['dispatch']}): released, card {c['path']} still"
            f" there for the cleaners ({CLEANUP})" for c in fresh if c.get("path") not in mine]


def ensure_dash():
    """Start the dashboard server, or restart it when its code changed; returns its one-line answer.
    ORCH_DASH=off skips it (tests)."""
    if os.environ.get("ORCH_DASH") == "off":
        return None
    try:
        r = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("dash.py")), "ensure"],
                           capture_output=True, text=True, timeout=40)
        return ((r.stdout or r.stderr).strip().splitlines() or ["dashboard: ensure printed nothing"])[-1]
    except subprocess.TimeoutExpired:
        return "dashboard: `orch-dash ensure` timed out; the lines below may be stale"


def note_file(cfg, slug):
    """(path, why not) of the program note when it sits in a markdown notes root this process may read. An Obsidian
    vault is read only through its MCP or CLI path (use-notes), so it is never opened from here."""
    try:
        user = json.loads(pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json")
                          .expanduser().read_text()).get("notes")
    except (OSError, ValueError, AttributeError):
        user = None
    user = user if isinstance(user, dict) else {}
    mine = cfg.get("notes") if isinstance(cfg.get("notes"), dict) else {}
    if (mine.get("adapter") or user.get("adapter")) != "markdown":
        return None, "the notes store is not a markdown root"
    root = mine.get("root") or (user.get("markdown") or {}).get("root") or "~/notes"
    if cfg.get("note"):  # `orch init --note`: the note the lead named, or nothing; never another copy
        named = pathlib.Path(str(cfg["note"])).expanduser()
        return (named, None) if named.is_file() else (None, f"the note named in program.json is not a file: {cfg['note']}")
    found = sorted(pathlib.Path(root).expanduser().rglob(f"program-{slug}.md"))
    if len(found) > 1:
        return None, f"{len(found)} files named program-{slug}.md under {root}; `orch set {slug} note <path>` names one"
    return (found[0], None) if found else (None, f"no program-{slug}.md under {root}")


def note_values(text):
    """The values a program note spells in the template's own shape, as {key: value}. A line in another shape, a
    template placeholder (`<…>`) and a deadline that is no timestamp are left out: only a fixed format is compared."""
    out = {}
    head = re.search(r"^- 머지 정책:(.*)$", text, re.M)
    if head:
        line = head.group(1)
        if m := re.match(r"\s*(autonomous|human-gate)\s*(?:·|$)", line):
            out["merge_policy"] = m.group(1)
        if m := re.search(r"동시성 천장\s*(\d+)", line):
            out["ceiling"] = int(m.group(1))
        if m := re.search(r"마감\s+(\S+)", line):
            v = m.group(1)
            if v == "없음":
                out["deadline"] = None
            elif not v.startswith("<"):
                try:
                    parse_ts(v)
                    out["deadline"] = v
                except ValueError:
                    pass
    section = re.search(r"^## 완료 조건.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if section:  # a section with no ticket is a predicate emptied in the note, not a value left out
        out["predicate"] = re.findall(r"^\s*- \[[ xX]\]\s+([A-Z0-9]*[A-Z][A-Z0-9]*-\d+(?:-[A-Z0-9]+)*)\b",
                                      section.group(1), re.M)
    return out


def note_lines(p):
    """Step 5 of `status`: the program note is a copy of program.json's values, so say when a value in it differs.
    Reads the note, writes nothing; program.json stays the source and `orch set` is the only way to change it."""
    path, why = note_file(p.cfg, p.slug)
    if path is None:
        return [f"note values: not compared ({why})"]
    try:
        have = note_values(path.read_text())
    except OSError as e:
        return [f"note values: not compared ({path.name} not read: {type(e).__name__})"]
    if not have:
        return ["note values: none in the note (values come from `orch status`)"]
    diffs = []
    cfg = p.cfg
    for key in ("merge_policy", "ceiling"):
        if key in have and have[key] != cfg.get(key):
            diffs.append(f"{key}: note {have[key]}, program.json {cfg.get(key)}")
    if "deadline" in have:
        mine = cfg.get("deadline")
        same = (have["deadline"] is None and not mine) or (
            have["deadline"] and mine and parse_ts(have["deadline"]) == parse_ts(mine))
        if not same:
            diffs.append(f"deadline: note {have['deadline'] or 'none'}, program.json {mine or 'none'}")
    if "predicate" in have:
        note_only = [t for t in have["predicate"] if t not in cfg.get("predicate", [])]
        json_only = [t for t in cfg.get("predicate", []) if t not in have["predicate"]]
        if note_only or json_only:
            diffs.append("predicate: " + ", ".join(
                x for x in (f"note only {' '.join(note_only)}" if note_only else "",
                            f"program.json only {' '.join(json_only)}" if json_only else "") if x))
    if diffs:
        return ["note values: DIFFER from program.json — " + "; ".join(diffs)
                + " (program.json is the source; the note is a copy, so correct the note, and `orch set` only if "
                  "program.json is the stale one)"]
    return [f"note values: match program.json ({', '.join(have)})"]


def dashboard_lines(slug):
    """Keep the dashboard server up (`orch-dash ensure`), and bring back what only its collector sees:
    whether each worker's session is moving, free capacity and ledger gaps. Stale data prints nothing.
    ORCH_DASH=off skips the server (tests)."""
    out = [line] if (line := ensure_dash()) else []
    st = dash_state(HOME / slug)
    if st is None:
        return out
    s = st.get("summary") or {}
    out += [stall_line(x) for x in s.get("stalls") or []]
    sp, gaps = s.get("spare") or {}, s.get("gaps") or {}
    if sp.get("slots") and sp.get("ready"):
        out.append(f"  SPARE {sp['slots']} slot(s) under the cap; tasks ready to start: {', '.join(map(str, sp['ready'][:6]))}")
    if gaps.get("spawns"):
        out.append(f"  LEDGER GAP: dispatches never recorded ({', '.join(gaps['spawns'][:6])}): "
                   f"`orch record {slug} spawned --ticket T --note <dispatchId>`")
    if gaps.get("landings"):
        out.append(f"  LEDGER GAP: merged PRs the ledger never saw (#{', #'.join(map(str, gaps['landings'][:6]))}): "
                   f"land through `orch land`; recover with `orch backfill {slug} --since ISO`")
    if gaps.get("ci"):
        out.append(f"  LEDGER GAP: landings with no main CI result (#{', #'.join(map(str, gaps['ci'][:6]))}): "
                   f"record main_green or main_red --sha")
    return out


def open_admitted(events, by_id):
    """Admitted follow-ups block Close like predicate items: admission means they block one or fix a defect."""
    return [t for t in admitted_tickets(events)
            if by_id.get(t, {}).get("state_type") not in ("completed", "canceled")]


def inbox_wait(p):
    """(the `inbox:` line, whether it holds a command). Built from program.json and ORCA_TERMINAL_HANDLE alone, so a
    failed lookup later in status never costs the lead its wait. A lead that is a worker of the Run reads its own
    Dispatch mailbox: the Run inbox is its coordinator's, and an empty --terminal would read someone else's."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    if p.cfg.get("lead_dispatch"):
        if not me:
            return "inbox: cannot build the wait: lead_dispatch is set but ORCA_TERMINAL_HANDLE is empty", False
        box = f"--terminal {shlex.quote(me)}"
    else:
        box = f"--run {shlex.quote(p.cfg['run'])}"
    # stderr carries a keepalive every 15 seconds; a pipe would hide orca's exit code.
    return (f"inbox: orca orchestration check {box} --wait --types {','.join(sorted(ACTIONABLE))} --timeout-ms 1800000"
            f" --full --json 2>>{shlex.quote(str(p.dir / 'wait.err'))}"), True


def row_gaps(w, named, task_ids):
    """The fields a status judgment needs that this worker row lacks; named: dispatch -> ticket, from the ledger or the
    row's Task; task_ids: the Tasks status read (a listed Task that names no ticket holds no program PR)."""
    gaps = [k for k in ("dispatchId", "terminalState") if not w.get(k)]
    if w.get("terminalState") == "active" and w.get("dispatchId") not in named and w.get("taskId") not in task_ids:
        # An active worker whose ticket cannot be found may hold any PR.
        gaps.append("taskId" if not w.get("taskId") else "taskId in task-list")
    if w.get("terminalState") == "retained":
        # Read "resource": null as no resource, so not retained for a user (seen on settled rows with --full); this only
        # gates the self-check ask, never release, rm or LAND. An absent key is a gap.
        res = w.get("resource")
        reason = ("resource" in w and res is None) or (isinstance(res, dict) and res.get("retainedReason"))
        gaps += [k for k, v in (("workerState", w.get("workerState")), ("resource.retainedReason", reason),
                                ("terminal handle", worker_terminal(w))) if not v]
    return gaps


# The fields that say whether a worker holds a PR; a retained row's other gaps only stop TAKEN OVER.
HOLDS_FIELDS = {"dispatchId", "terminalState", "taskId", "taskId in task-list"}


def taken_over_lines(workers, state, now=None, unknown=()):
    """TAKEN OVER for each worker Orca's release refuses, asking each for a self-check once. Its own state file, not
    `orch wait`'s self-check.json: one command's ask would silence the other's. unknown: dispatches whose row could
    not be judged this time; their asks are kept, not forgotten."""
    try:
        asked = json.loads(state.read_text())
    except (OSError, ValueError):
        asked = {}
    asked = asked if isinstance(asked, dict) else {}
    before, out = dict(asked), []
    for w in filter(taken_over, workers):
        d = w["dispatchId"]
        if d in asked:
            out.append(f"  TAKEN OVER {d} ({w['workerState']}): self-check asked at {asked[d]}; wait for its answer")
            continue
        asked[d] = now or dt.datetime.now().isoformat(timespec="minutes")
        out += [f"  TAKEN OVER {d} ({w['workerState']}): worker-release refuses it; ask it to check for work left and close itself",
                f"    orca terminal send --terminal {worker_terminal(w)} --text {shlex.quote(SELF_CHECK)} --enter"]
    live = {w["dispatchId"] for w in filter(taken_over, workers)}
    # None in unknown: a row without dispatchId may be any of them, so every ask is kept.
    keep = {d: t for d, t in asked.items() if d in live or d in unknown or None in unknown}
    if keep == before:
        return out
    try:
        fd, tmp = tempfile.mkstemp(dir=state.parent, prefix=".status-self-check-")
        with os.fdopen(fd, "w") as f:
            json.dump(keep, f)
        os.replace(tmp, state)
    except OSError as e:
        out.append(f"  TAKEN OVER: could not record the self-checks ({e}); the next status asks again")
    return out


def cmd_status(argv):
    p = Program(argv[0])
    wait_line, wait_ok = inbox_wait(p)
    print(wait_line, flush=True)
    if "--inbox" in argv[1:]:
        sys.exit(0 if wait_ok else 1)
    if watched := watch_lines(os.environ.get("ORCA_TERMINAL_HANDLE"), False):
        print("\n".join(watched), flush=True)
    cfg, events = p.cfg, p.events()
    t0, tnow = parse_ts(cfg["created_at"]), dt.datetime.now(dt.timezone.utc)
    lines = []

    # 1. predicate
    pred = cfg["predicate"]
    issues = tracker_issues(cfg, pred + admitted_tickets(events))
    by_id = {i["id"]: i for i in issues or []}
    done = [t for t in pred if by_id.get(t, {}).get("state_type") == "completed"]
    open_ = [t for t in pred if t not in done]
    blocking = open_ + [t for t in open_admitted(events, by_id) if t not in pred]
    tickets_done = bool(pred) and not blocking and issues is not None
    verified = final_check_current(events)
    lines.append(f"predicate: {len(done)}/{len(pred)} tickets done"
                 + ((" — final check recorded" if verified else " — final check not yet recorded") if tickets_done
                    else f" (open: {', '.join(blocking[:8])}{'…' if len(blocking) > 8 else ''})")
                 if issues is not None else f"predicate: {len(pred)} items (no tracker project; check by hand)")

    # 2. flow
    workers = run_workers(cfg["run"])
    events = with_orca_spawns(events, workers, run_tasks(cfg["run"]))
    roles = {e.get("note"): e["role"] for e in events if e["ev"] == "spawned" and e.get("role")}
    # A row without dispatchId is not counted here; it comes out below as a ROW and its WARN.
    running = [w for w in workers if w.get("dispatchId") and (w.get("projection") or {}).get("outcome") == "in_progress"]
    live = [w for w in running if w["dispatchId"] not in roles]
    role_live = [f"{roles[w['dispatchId']]} {w['dispatchId']}" for w in running if w["dispatchId"] in roles]
    units = live_units(events, [w["dispatchId"] for w in live])
    waiting = [w["dispatchId"] for w in live if ((w.get("projection") or {}).get("stage") or {}).get("activity") == "waiting"]
    ready = ready_prs(events)
    human = [s for s in ready if cfg["merge_policy"] == "human-gate" and "approved" not in s]
    main = main_state(events)
    window = dt.timedelta(hours=3)
    landed_recent = [e for e in events if e["ev"] == "landed" and tnow - parse_ts(e["ts"]) <= window]
    fails_recent = [e for e in events if e["ev"] in ("main_red", "land_failed") and tnow - parse_ts(e["ts"]) <= window]
    cap = cap_from(events, cfg["ceiling"])
    hours = max((tnow - t0).total_seconds() / 3600, 0.25)
    rate = len([e for e in events if e["ev"] == "landed"]) / hours
    tasks = run_tasks(cfg["run"])
    rows, stacks, deps = open_rows(cfg["repo"]), repo_stacks(cfg["repo"]), deps_from_tasks(tasks)
    order = land_order(events, rows, cfg, tnow, stacks, deps)
    order = [e for e in order if e["state"] != "gone"]
    gaps = sorted(parse_ts(b["ts"]) - parse_ts(a["ts"]) for a, b in zip(landed_recent, landed_recent[1:]))
    interval = (gaps[len(gaps) // 2].total_seconds() / 60 if gaps else knob(cfg, "land_interval_minutes"))
    backlog_h = len(order) * interval / 60
    held = [e for e in order if held_pr(events, e)]
    stale = [e for e in order if e["age_h"] >= knob(cfg, "stale_hours") and e not in held]
    lines.append(f"flow: main {main} · in-flight {units}/{cap} cap (ceiling {cfg['ceiling']}) · ready-to-land {len(ready)}"
                 f" · human-wait {len(human)} · landed {len(landed_recent)} in 3h · {rate:.1f}/h overall"
                 + (f" · idle-waiting: {', '.join(waiting)} (prompt? worker-read --source terminal)" if waiting else "")
                 + (f" · roles: {', '.join(role_live)}" if role_live else ""))
    lines.append(f"land order ({len(order)}, ~{interval:.0f} min per landing, backlog ~{backlog_h:.1f}h): "
                 + (" → ".join(f"#{e['pr']}{'*' if e['state'] == 'ready' else ''}" for e in order[:8]) or "empty")
                 + ("  (* ready; `orch queue` says why the rest wait)" if order else ""))
    for e in stale:
        lines.append(f"  STALE {fmt_entry(order.index(e) + 1, e)}")
    for e in held:
        note = next((x.get("note") for x in reversed(events) if x["ev"] == "parked" and x.get("pr") == e["pr"]), None)
        lines.append(f"  HELD #{e['pr']}{f' ({note})' if note else ''}: not counted as stale; if superseded, "
                     f"gh pr close {e['pr']} --repo {cfg['repo']} --comment '<what replaced it>', "
                     f"or `orch record {p.slug} admitted --pr {e['pr']}` to land it")
    worktrees = orca_json("worktree", "list").get("worktrees", [])
    named = {**dispatch_tickets(workers, tasks), **{d: e["ticket"] for e in events if e["ev"] == "spawned" and e.get("ticket")
                                                    for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or ""))}}
    task_ids = {t.get("id") for t in tasks} - {None}
    gaps = [(w, g) for w in workers if (g := row_gaps(w, named, task_ids))]
    judged = [w for w in workers if not row_gaps(w, named, task_ids)]
    here = own_worktree_id()
    leftover = landed_but_open(events, workers, tasks, worktrees, by_id, here=here or "",
                               unjudged={w.get("dispatchId") for w, _ in gaps} - {None, ""})
    for tk, dispatch, active, path in leftover:
        lines.append(f"  LANDED-BUT-OPEN {tk} (dispatch {dispatch}): "
                     + ("worker state unknown (row below); release nothing on this line" if active is None
                        else f"once its main CI is recorded, worker-release"
                             f"{' and close its agent terminal' if here is not None else ''}; card {path}"
                             f" stays for the cleaners ({CLEANUP})" if active
                        else f"released, card {path} still there for the cleaners ({CLEANUP})"))
    lines += taken_over_lines(judged, p.dir / "status-self-check.json", unknown={w.get("dispatchId") or None for w, _ in gaps})
    # A row status cannot judge may hold any PR its ticket names, or any PR at all when its ticket is unknown.
    unknown = {named.get(w.get("dispatchId")) or "*" for w, g in gaps if HOLDS_FIELDS & set(g)}
    lines += [f"  {x}" for x in review_lines(p, workers, tasks, unknown)]
    for w, g in gaps:
        lines += [f"  ROW {json.dumps(w, ensure_ascii=False)}", f"  WARN status: worker row not judged (missing {', '.join(g)})"]
    prs, files, theirs, unread = cross_inputs(p.slug, cfg["repo"], events, rows, *program_files(cfg, events, rows, tasks))
    lines += [f"  {x}" for x in path_overlaps(p.slug, files, prs, cfg, rows, dep_map(events, deps), stacks)]
    lines += [f"  {x}" for x in cross_overlaps(files, theirs, rows, stacks, unread=unread)]

    # 3. growth
    admitted = {e["ticket"] for e in events if e["ev"] == "admitted" and e.get("ticket")}
    parked = {e["ticket"] for e in events if e["ev"] == "parked" and e.get("ticket")}
    if issues is not None:
        new = [i for i in issues if parse_ts(i["created_at"]) > t0 and i["id"] not in pred]
        derived = [i for i in new if is_derived(i)]
        # A follow-up already closed, or already being worked, needs no admit/park call.
        spawned = {e.get("ticket") for e in events if e["ev"] == "spawned"}
        untriaged = [i["id"] for i in derived
                     if i["id"] not in admitted | parked | spawned and i.get("state_type") in OPEN_STATES]
        ratio = len(new) / max(len(pred), 1)
        lines.append(f"growth: {len(new)} tickets filed since start outside the predicate ({ratio:.1f} per predicate item),"
                     f" {len(derived)} marked derived"
                     f" · admitted {len(admitted)} · parked {len(parked)}"
                     + (f" · untriaged: {', '.join(untriaged[:6])}" if untriaged else ""))

    # 4. next move
    nxt, used = next_move(cfg, events, tnow, tickets_done=tickets_done if issues is not None else None,
                          live=units, human=len(human), order=order,
                          leftover=sum(active is False for _, _, active, _ in leftover))
    budget_note = f" (budget {used:.0%} used)" if used is not None else ""
    lines.append(f"next: {nxt}{budget_note}")
    lines += note_lines(p)
    print("\n".join(lines + dashboard_lines(p.slug)))
    if not wait_ok:
        sys.exit(1)


def next_move(cfg, events, tnow, *, tickets_done, live, human, order, leftover=0):
    """Step 4 of `status`, first matching rule wins; safety outranks completion. orch-dash shows the same line.

    tickets_done is None when the tracker could not say: a current predicate_verified then decides Close.
    leftover counts released LANDED-BUT-OPEN cards; they go before spawning. While "may spawn 3 more" topped
    them, a compacted lead left eight open.
    Returns (next line, share of the deadline used or None)."""
    used = None
    if cfg.get("deadline"):
        t0, dl = parse_ts(cfg["created_at"]), parse_ts(cfg["deadline"])
        used = (tnow - t0) / max(dl - t0, dt.timedelta(seconds=1))
    verified = final_check_current(events)
    window = dt.timedelta(hours=3)
    landed_recent = [e for e in events if e["ev"] == "landed" and tnow - parse_ts(e["ts"]) <= window]
    fails_recent = [e for e in events if e["ev"] in ("main_red", "land_failed") and tnow - parse_ts(e["ts"]) <= window]
    gaps = sorted(parse_ts(b["ts"]) - parse_ts(a["ts"]) for a, b in zip(landed_recent, landed_recent[1:]))
    interval = gaps[len(gaps) // 2].total_seconds() / 60 if gaps else knob(cfg, "land_interval_minutes")
    backlog_h = len(order) * interval / 60
    # A PR the lead put on hold waits on purpose; counting it would block spawning until someone closes it.
    stale = [e for e in order if e.get("age_h", 0) >= knob(cfg, "stale_hours") and not held_pr(events, e)]
    cap = cap_from(events, cfg.get("ceiling", 6))
    if stopped(events):
        return "STOP line active: spawn nothing; let in-flight finish", used
    if main_state(events) == "red":
        return "SAFETY STOP: main is red — land only the fix (orch land <slug> --pr N --class main-fix), then record main_green --sha", used
    if verified and tickets_done is not False:
        return "predicate met and verified: Close", used
    if tickets_done:
        return "tickets done: run the final check on the real artifact, then record predicate_verified", used
    if (used or 0) >= 0.7:
        return "stop spawning: land what is verified", used
    if stale:
        return (f"unstick first: {len(stale)} PR(s) waited ≥{knob(cfg, 'stale_hours'):g}h — for each, remove the reason"
                " `queue` gives (fix task, stack the dependent chain, reprioritize), not more parallel work"), used
    if backlog_h > knob(cfg, "max_backlog_hours") or human >= 3:
        return (f"stop spawning implementation: landing backlog ~{backlog_h:.1f}h — finish before starting"
                " (stack dependent or independent ready PRs so one merge lands several)"), used
    if not landed_recent and len(fails_recent) >= 2:
        return "stop spawning: no landing and 2+ failures in 3h — find the cause", used
    if leftover:
        return f"close out {leftover} released card(s) before spawning: remove each LANDED-BUT-OPEN card", used
    if live < cap:
        return f"may spawn {cap - live} more", used
    return "at cap: drain and land", used


OWNED = {"approved": "land, gate", "gate_opened": "gate", "landed": "land, landed", "land_check": "land",
         "land_failed": "land", "ready": "land-check", "lane": "land", "yield": "land",
         "lock_acquired": "land", "lock_released": "land", "dep": "dep", "config": "set",
         "auto_wake_claimed": "wait", "auto_wake": "wait", "auto_wake_failed": "wait", "auto_wake_escalated": "wait"}


# land_failed, main_red and failed verdicts are already in the ledger; a signal records what only the lead sees.
SIGNAL_KINDS = ("human_correction", "brief_gap", "stall", "tooling")


def record_external_green(p, sha):
    """main_green for a commit another program landed: allowed only to clear a red, and only for a full sha that
    descends from every red landing the stop rests on and sits on the base branch. Anything GitHub cannot confirm
    refuses. The row names the reds it clears, so the state it frees is the state that was checked."""
    reds = open_reds(p.events())
    if main_state(p.events()) != "red" or not reds:
        sys.exit("main_green needs --sha of a merge commit recorded by `landed`; a commit another program landed "
                 "counts only while this program's main is red (main is not red)")
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        sys.exit("main_green for a commit this program did not land needs its full 40-char sha (a branch or a short sha can move)")
    repo, base = p.cfg["repo"], p.cfg.get("base") or "main"
    try:
        for r in reds:
            if compare(repo, r, sha).get("status") != "ahead":
                sys.exit(f"{sha[:8]} is not a descendant of the red landing {r[:8]}: it cannot show that red is fixed")
        if compare(repo, sha, base).get("status") not in ("ahead", "identical"):
            sys.exit(f"{sha[:8]} is not on {base}: record the green for a commit {base} contains")
    except (SystemExit, ValueError, KeyError, TypeError, AttributeError) as e:
        if isinstance(e, SystemExit) and str(e.code).startswith(sha[:8]):
            raise
        sys.exit(f"{sha[:8]} could not be checked against {base} ({e}); main_green stays refused")
    row = p.append_if("main_green", lambda evs: open_reds(evs) == reds, sha=sha, via="descendant", clears=reds,
                      note="descendant of the red landing; no landing of this program")
    if row is None:
        sys.exit("the ledger changed while the commit was checked (a new landing or result); run the record again")
    print(json.dumps(row, ensure_ascii=False))


def cmd_record(argv):
    p = Program(argv[0])
    ev = argv[1]
    pr, sha = opt(argv, "--pr"), opt(argv, "--sha")
    if ev in ("main_green", "main_red") and sha not in landed_shas(p.events()):
        top = stack_lower(p.events()).get(sha)
        if ev == "main_green" and not top:
            return record_external_green(p, sha)
        sys.exit(f"{sha[:8]} is a lower stack layer: main CI ran once, on the merge commit of its top #{top}; record it there" if top
                 else f"{ev} needs --sha of a merge commit recorded by `landed` (the commit that CI run tested)")
    if ev == "verdict":
        sys.exit("use the verdict command; it pins the reviewed head and its patch-id")
    if ev in OWNED:
        # A hand-written `approved` would pass a human gate nobody resolved.
        sys.exit(f"{ev} is written only by the command that checks it ({OWNED[ev]})")
    klass = opt(argv, "--class")
    if klass and klass not in KLASS:
        sys.exit(f"--class is one of {', '.join(KLASS)}")
    kind, evidence = opt(argv, "--kind"), opt(argv, "--evidence")
    if ev == "signal" and (kind not in SIGNAL_KINDS or not evidence):
        sys.exit(f"signal needs --kind ({', '.join(SIGNAL_KINDS)}) and --evidence pointing at the proof")
    row = p.append(ev, ticket=opt(argv, "--ticket"), pr=int(pr) if pr else None, sha=sha, klass=klass,
                   role=opt(argv, "--role"), note=opt(argv, "--note"), kind=kind, evidence=evidence)
    print(json.dumps(row, ensure_ascii=False))


def set_run(p, run_id):
    """Move the program to the Run this terminal is bound to, for a dispatched lead whose restart cost it the Dispatch it
    worked under: the old Run belongs to its coordinator, and `lead_dispatch` names a Dispatch that no longer answers.
    Only the bound Run is accepted, so mail cannot be pointed at a Run nobody reads."""
    import orca_settings
    bound = (orca_settings.run_current(orca_json) or {}).get("id")
    if bound != run_id:
        sys.exit(f"this terminal is {'bound to ' + bound if bound else 'not bound to any Run'}, not {run_id}: "
                 f"`orca orchestration run-create` (or `run-use --id {run_id}` for a Run you coordinate), then set again")
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    if me and own_worker(all_workers(run_id), me):
        sys.exit(f"this terminal is a dispatched worker of {run_id}: it still works for that Run's coordinator, so the "
                 "program stays there")
    old = p.cfg.get("run")
    p.update(lambda cfg: (cfg.__setitem__("run", run_id), cfg.pop("lead_dispatch", None)))
    print(json.dumps(p.append("config", note=f"run={old}->{run_id}; lead_dispatch cleared"), ensure_ascii=False))
    # Units started under the old Dispatch stay on the old Run, where this lead's checks no longer look.
    print(f"units this lead started stay on {old}: list them with `orca orchestration worker-list --run {old}`; resume an "
          "abandoned one from this terminal (reboot SKILL.md \"Resume a worker\"), which attaches it to the new Run")


def cmd_set(argv):
    p = Program(argv[0])
    key, value = argv[1], argv[2]
    if key == "run":
        return set_run(p, value)
    if key == "merge_policy" and value not in ("autonomous", "human-gate"):
        sys.exit("merge_policy is autonomous or human-gate")
    split = lambda v: [x for x in v.split(",") if x]
    conv = {"ceiling": int, "predicate": split, "exclusive_paths": split, "visual_paths": split, "stages": split}.get(key, str)
    if key not in ("merge_policy", "ceiling", "deadline", "predicate", "exclusive_paths", "visual_paths", "note", "final_check",
                   "stages"):
        sys.exit("settable: run, merge_policy, ceiling, deadline, predicate, exclusive_paths, visual_paths, note, final_check, stages")
    if key == "visual_paths" and not conv(value):
        sys.exit("visual_paths cannot be empty: the screenshot check has no off switch, only the no-visual-change label")
    if key in ("exclusive_paths", "visual_paths"):
        p.update(lambda cfg: cfg.setdefault("landing", {}).__setitem__(key, conv(value)))
    else:
        p.update(lambda cfg: cfg.__setitem__(key, conv(value)))
    print(json.dumps(p.append("config", note=f"{key}={value}"), ensure_ascii=False))


def cmd_verdict(argv):
    p = standalone_target(argv)
    pr, sha, src = int(opt(argv, "--pr")), opt(argv, "--sha"), opt(argv, "--source")
    if not src or not sha:
        sys.exit("verdict needs --sha (the head that was reviewed) and --source (who reviewed: independent-review, "
                 "review:<reviewer>, verifier:<reviewer>, live:<feature>)")
    # Independence is established by the review process, not a model-name allowlist.
    kind, _, what = src.partition(":")
    if not (src in ("independent-review", "subagent-review") or
            (kind in ("review", "verifier", "live") and what.strip() and
             what.strip().casefold() not in ("self", "implementer"))):
        sys.exit("--source is independent-review, subagent-review, review:<reviewer>, "
                 "verifier:<reviewer> or live:<feature>; never the implementer")
    view = pr_view(p.cfg["repo"], pr)
    head = view["headRefOid"]
    if not head.startswith(sha):
        sys.exit(f"head is {head[:8]}, not the reviewed {sha[:8]}: the new head has not been reviewed")
    recorder = os.environ.get("ORCA_TERMINAL_HANDLE")
    reviewer = (opt(argv, "--reviewer") or (what.strip() if kind in ("review", "verifier") else "")).strip()
    evidence = (opt(argv, "--evidence") or "").strip()
    authors = author_sessions(view.get("headRefName"))
    if authors == []:  # Orca answered: no worktree holds the branch name. Look for the card by the PR's head sha.
        authors = worktree_author_sessions(head)
    result = opt(argv, "--result", "pass")
    if authors is None:  # Orca did not give a complete answer: not "no card", so a pass cannot be judged
        if result == "pass":
            sys.exit("verdict refused, nothing recorded: Orca's terminal or worktree list could not show who wrote this"
                     " PR (failed, cut off or unreadable): run it again")
        authors = []
    fields = dict(author=authors, reviewer=reviewer or None, evidence=evidence or None, recorder=recorder, by=recorder)
    if not authors:  # Orca answered and no card was found: the row says so, and verdict_identity_problem decides
        fields["author_unverified"] = True
        print("warning: no card holding this PR was found; the author is recorded as unverified", file=sys.stderr)
    # A fail blocks a landing and needs no independence. A pass that cannot stand as an independent review is not
    # stored at all: a stored pass would let the direct `orch land` path, which reads no identity, merge on it.
    if result == "pass" and (bad := verdict_identity_problem({**fields, "source": src})):
        sys.exit(f"verdict refused, nothing recorded: {bad}")
    row = p.append("verdict", pr=pr, sha=head, patch_id=patch_id(p.cfg["repo"], pr),
                   source=src, result=result, note=opt(argv, "--note"), **fields)
    print(json.dumps(row, ensure_ascii=False))


SELF_REVIEWERS = {"self", "implementer", "author"}


def complete_answer(out, key):
    """(rows, hostIds, omittedHostIds) of an Orca list answer that says it is complete, else None. Each of the three
    must be present and well typed: a missing or mistyped `truncated` or host list was read as its empty default, so
    a partial answer passed for a complete one ("no card")."""
    if not isinstance(out, dict) or not isinstance(out.get(key), list) or out.get("truncated") is not False:
        return None
    scope = out.get("hostScope")
    if not isinstance(scope, dict):
        return None
    hosts = []
    for field in ("hostIds", "omittedHostIds"):
        ids = scope.get(field)
        if not isinstance(ids, list) or not all(isinstance(h, str) and h for h in ids):
            return None
        hosts.append(ids)
    return out[key], hosts[0], hosts[1]


def worktree_rows():
    """Orca's worktree list when it can be trusted to be complete and every row is readable, else None: a cut-off
    list, a host Orca did not reach, or a row that cannot say which worktree it is, where, or what it holds can hide
    the very worktree that wrote the PR, and skipping it as "no match" would turn a partial answer into "no card".
    An empty branch or head is a real state (detached HEAD, no commit yet): only a missing or mistyped field fails."""
    answer = complete_answer(orca_json("worktree", "list"), "worktrees")
    if answer is None:
        return None
    rows, hosts, omitted = answer
    if omitted:
        return None
    for w in rows:
        if not (isinstance(w, dict) and all(isinstance(w.get(k), str) for k in ("id", "hostId", "branch", "head"))
                and w["id"] and w["hostId"] in hosts and isinstance(w.get("isMainWorktree"), bool)):
            return None
    return rows


def worktree_sessions(w):
    """Terminal handles of one worktree row, or None when Orca's answer cannot be trusted for it. An answer counts only
    when it says it covered this worktree's host: an empty or partial answer from a host Orca did not reach says
    nothing about its sessions. Another host being left out is an ordinary scope limit."""
    answer = complete_answer(orca_json("terminal", "list", "--worktree", f"id:{w['id']}"), "terminals")
    if answer is None:
        return None
    rows, hosts, omitted = answer
    if w["hostId"] not in hosts or w["hostId"] in omitted:
        return None
    # A terminal row with no usable handle could be the author's own: dropping it would read as "no card".
    if not all(isinstance(t, dict) and isinstance(t.get("handle"), str) and t["handle"] for t in rows):
        return None
    return {t["handle"] for t in rows}


def worktree_holders(matches):
    """Sorted handles of the sessions in every worktree row `matches` picks, [] when it picks none (Orca answered and
    no card is there), None when the list or any picked row's answer cannot show who is there. A partial union is
    never returned: the missing worktree may be the one that wrote the PR."""
    try:
        rows = worktree_rows()
        if rows is None:
            return None
        handles = set()
        for w in rows:
            if not matches(w):
                continue
            part = worktree_sessions(w)
            if part is None:
                return None
            handles |= part
        return sorted(handles)
    except (SystemExit, ValueError, KeyError, TypeError, AttributeError, OSError):
        return None


def author_sessions(branch):
    """Terminal handles of every card whose worktree holds the PR's branch: the sessions that wrote it. [] when Orca
    answered completely and no worktree holds the branch; None when the answer cannot show who the author is (Orca
    failed, a list or a host is missing or cut off). Only [] leaves the author unverified: None must refuse, since a
    cut-off list can miss the very card that wrote the PR. A branch selector is rejected as ambiguous once a closed
    card's worktree and its successor both hold the branch, so the worktrees are listed and each is asked by id.
    A PR with no branch name to look for is None too: nothing was looked up, which is not "no worktree holds it"."""
    if not isinstance(branch, str) or not branch:
        return None
    return worktree_holders(lambda w: w.get("branch") in (branch, f"refs/heads/{branch}"))


def worktree_author_sessions(head):
    """Fallback for a card whose local branch is not the PR's branch (it pushed `local:remote`, or was renamed): the
    terminals of the non-main worktrees sitting at the PR's head sha. The PR number is not matched, because the listing
    spans repositories and another repository's card linking the same number would stand in for the author. [] when
    Orca answered and no worktree sits there; None when the answer cannot show it, which must refuse, not pass as an
    unverified author. A matching row that cannot be read is None too, and so is a head that is not a sha to look for."""
    if not isinstance(head, str) or not head:
        return None
    return worktree_holders(lambda w: not w["isMainWorktree"] and w["head"] == head)


def verdict_identity_problem(v):
    """Why this verdict record cannot stand as an independent review, else None. `author` is the PR's sessions as
    seen when it was recorded, `reviewer` who reviewed, `evidence` a link or sha of the review, `recorder` the
    terminal that wrote the row (an orchestrator may record a review someone else ran). The recorder's handle is an
    environment value, so this proves nothing against a forged one: it stops the honest mistake and the plain
    self-review, and leaves the rest to the review evidence."""
    authors = v.get("author")
    # A row is JSON anyone can append: a wrong type must refuse, not raise and leave the request queued forever.
    if not all(v.get(k) is None or isinstance(v.get(k), str) for k in ("reviewer", "evidence", "recorder")) \
            or not (authors is None or isinstance(authors, list) and all(isinstance(a, str) and a for a in authors)) \
            or not (v.get("author_unverified") is None or isinstance(v.get("author_unverified"), bool)) \
            or (v.get("author_unverified") is True and authors != []):
        # `author_unverified` vouches for a lookup that answered "no card", which is exactly the list []: a missing or
        # null author says nothing was looked up, and a found author contradicts the mark.
        return ("the identity fields are malformed (author is a list of handles, author_unverified a boolean that"
                " needs author == [], the rest strings)")
    if not (v.get("reviewer") or "").strip():
        return "no reviewer named (--reviewer, or a review:<reviewer> or verifier:<reviewer> source)"
    if not (v.get("evidence") or "").strip():
        return "no evidence (--evidence: a link to the review or the reviewed sha)"
    if not v.get("recorder"):
        return "no recorder (ORCA_TERMINAL_HANDLE is unset)"
    if self_review(v):
        return self_review(v)
    if not v.get("author"):
        # No card was found (its branch can differ from the PR's). The row may stand only when it says so and the
        # reviewer is not the session that recorded it; a row without the mark predates this rule and is refused.
        if v.get("author_unverified") is not True:
            return "the PR's author session could not be identified (no card holds its branch)"
        for role, name in stated_reviewers(v):  # either name, so a review:<recorder> source cannot hide it
            if name == v["recorder"]:
                return f"the author is unverified and the {role} is the recorder: another session records the review"
    return None


def executor_verdict_problem(verdict, head, sources):
    """Why the land executor would refuse this verdict at `head`, else None. The executor and `land-check` both call
    it, so READY and a refusal never disagree: the verdict must be a pass for exactly this head (a matching patch-id
    is not enough, the executor merges the sha it was asked about), from an allowed source, and attributable."""
    if not verdict or verdict.get("sha") != head or verdict.get("result") != "pass" \
            or verdict.get("source") not in sources:
        got = (f"{verdict.get('result')} from {verdict.get('source')} at {(verdict.get('sha') or '')[:8]}"
               if verdict else "none")
        return (f"the latest verdict ({got}) is not a pass at {head[:8]} from {', '.join(sources)}:"
                " review this head, orch verdict, land again")
    bad = verdict_identity_problem(verdict)
    if bad:  # the executor merges on a review it can attribute: who wrote, who reviewed, the proof, who recorded
        return (f"the verdict cannot stand as an independent review: {bad}."
                " Record it again with orch verdict --reviewer <who> --evidence <link or sha>")
    return None


def stated_reviewers(v):
    """(role, name) for every reviewer the row names: --reviewer and the one inside a review:/verifier: source."""
    kind, _, what = str(v.get("source") or "").partition(":")
    named = [("reviewer", v.get("reviewer")), ("source reviewer", what if kind in ("review", "verifier") else None)]
    return [(role, name.strip()) for role, name in named if isinstance(name, str) and name.strip()]


def self_review(v):
    """The reason the review is the PR's own author's, else None. Every identity the row states counts: --reviewer, the
    reviewer inside a review:/verifier: source (so `--source review:author --reviewer codex` does not pass), and the
    recorder, since an author who records its own pass wrote the verdict it will be merged on."""
    authors = v.get("author") or []
    for role, name in stated_reviewers(v):
        if name.casefold() in SELF_REVIEWERS or name in authors:
            return f"{role} '{name}' is the PR's author session"
    if isinstance(v.get("recorder"), str) and v["recorder"] in authors:
        return f"recorder '{v['recorder']}' is the PR's author session: another session records the review"
    return None


def cmd_dep(argv):
    p = Program(argv[0])
    ticket, after = opt(argv, "--ticket"), [t for t in (opt(argv, "--after") or "").split(",") if t]
    if not ticket or not after:
        sys.exit("dep needs --ticket A --after B[,C]")
    if ticket in after:
        sys.exit(f"dep is per ticket, so {ticket} after {ticket} would wait on itself forever. Layers of one"
                 " ticket's stack land bottom-up with orch land, one layer at a time; record nothing for them")
    print(json.dumps(p.append("dep", ticket=ticket, after=after), ensure_ascii=False))


def fmt_entry(i, e):
    head = f"{i}. #{e['pr']} {e['ticket'] or '-'} [{e['klass']}] {e['state']} · waited {e['age_h']:.1f}h"
    if e["unblocks"]:
        head += f" · unblocks {e['unblocks']}"
    if e["exclusive"]:
        head += " · exclusive lane"
    return head + (f" — {'; '.join(e['reasons'])}" if e["reasons"] else "")


def cmd_queue(argv):
    p = Program(argv[0])
    order = land_order(p.events(), open_rows(p.cfg["repo"]), p.cfg, dt.datetime.now(dt.timezone.utc),
                       repo_stacks(p.cfg["repo"]), orca_deps(p.cfg["run"]))
    if "--json" in argv:
        print(json.dumps(order, ensure_ascii=False, indent=1))
        return
    for i, e in enumerate(order, 1):
        print(fmt_entry(i, e))
    if not order:
        print("land order: empty")


class ExclusiveLock:
    """The exclusive lane of one repo base branch, shared by every program on this machine.

    The holder keeps it from its first turn (restack, CI) until it lands; each attempt refreshes
    it, and one idle for exclusive_stale_minutes belonged to a lander that died, so it is broken.
    """

    def __init__(self, p, base):
        self.p = p
        self.path = HOME / "_locks" / (f"{p.cfg['repo']}@{base}".replace("/", "__") + ".lock")

    def holder(self):
        with self.serialized():
            return self.read()

    def read(self):
        """Call with serialized() held: it can delete a stale lock. flock does not nest across descriptors."""
        try:
            held = json.loads(self.path.read_text() or "{}")
        except (OSError, ValueError):
            return None
        age = dt.datetime.now(dt.timezone.utc) - parse_ts(held.get("ts", now()))
        if age >= dt.timedelta(minutes=knob(self.p.cfg, "exclusive_stale_minutes")):
            self.path.unlink(missing_ok=True)
            self.p.append("lock_released", pr=held.get("pr"), note=f"broken after {age}")
            return None
        return held

    @contextlib.contextmanager
    def serialized(self):
        """Every check-then-delete of a lock excludes the others: it could otherwise delete a lock the next PR took
        between the two. The flock is on the directory, so no sidecar file sits among the locks."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def take(self, pr):
        """True if this PR now holds the lane (newly or already)."""
        with self.serialized():
            held = self.read()
            mine = {"pr": pr, "program": self.p.slug, "ts": now()}
            if held:
                if (held.get("pr"), held.get("program")) != (pr, self.p.slug):
                    return False
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(mine))
                os.replace(tmp, self.path)
                return True
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                return False
            os.write(fd, json.dumps(mine).encode())
            os.close(fd)
            self.p.append("lock_acquired", pr=pr, note=self.path.name)
            return True

    def break_if_closed(self, held):
        """A holder whose PR was closed or merged elsewhere never releases; free the lane now, not after the stale window."""
        r = run(["gh", "pr", "view", str(held.get("pr")), "--repo", self.p.cfg["repo"], "--json", "state", "--jq", ".state"],
                check=False)
        state = r.stdout.strip()
        if r.returncode != 0 or state not in ("CLOSED", "MERGED"):
            return held
        with self.serialized():
            current = self.read()
            if current != held:  # the lane changed hands while gh answered
                return current
            self.path.unlink(missing_ok=True)
            self.p.append("lock_released", pr=held.get("pr"), note=f"holder is {state}")
            return None

    def release(self, pr):
        with self.serialized():
            held = self.read()
            if held and (held.get("pr"), held.get("program")) == (pr, self.p.slug):
                self.path.unlink(missing_ok=True)
                self.p.append("lock_released", pr=pr, note=self.path.name)


def release_own_lanes(p, pr):
    """A land that ended at exit 1 will not come back on its own, so it holds no lane for the stale window. Another
    PR's lock stays: release checks the owner."""
    prefix = f"{p.cfg['repo']}@".replace("/", "__")
    for path in (HOME / "_locks").glob(f"{prefix}*.lock"):
        ExclusiveLock(p, path.stem[len(prefix):]).release(pr)


class HoldRefusal(str):
    """A land's exit-1 message that keeps its land call: the hold passes the PR over, `record admitted` brings it back."""


def stop_land(p, pr, why):
    release_own_lanes(p, pr)
    if not isinstance(why, HoldRefusal):
        p.append("land_check", pr=pr, outcome="stopped", note=why[:300])


def note_attempt(p, events, pr, outcome, reasons):
    """Refresh the reservation at most every 10 minutes, or when the outcome changes."""
    last = pr_state(events).get(pr, {}).get("land_check")
    if last and last.get("outcome") == outcome and \
            dt.datetime.now(dt.timezone.utc) - parse_ts(last["ts"]) < dt.timedelta(minutes=10):
        return
    p.append("land_check", pr=pr, outcome=outcome, note="; ".join(reasons)[:300])


KEEP = " A clean update keeps the verdict (same patch-id); re-review only if you resolved conflicts."
NO_CI_KEEP = " The no-CI lane needs a verdict at the exact head: review the updated head and record it."
EXECUTOR_KEEP = (" The land executor needs a pass verdict at exactly the head it merges, so an update changes the head:"
                 " after it, review the new head, orch verdict, land again.")


def keep_note(no_ci, executor):
    """What the update advice may say about the verdict. Only the direct CI path carries one across a clean update."""
    return NO_CI_KEEP if no_ci else EXECUTOR_KEEP if executor else KEEP

ADVICE = {
    "conflicts with base": "rebase onto the base, resolve, re-run the review on the resolution, record a new verdict",
    "no passing verdict": "finish the review loop, then orch verdict",
    "draft": "gh pr ready",
    "behind base": "gh pr update-branch {pr} --repo {repo} (or rebase and restack migrations), then land again",
}


def update_advice(unit, repo, stacked=False):
    """How to bring a PR, or a stack bottom-up, onto the latest base. A stack merges only as a linear chain: a merge
    commit, or a lower layer rebased alone, gets "Stack needs to be rebased"; every layer rebased bottom-up clears it,
    a merge commit already in the chain included."""
    if len(unit) == 1 and not stacked:
        return f"gh pr update-branch {unit[0]} --repo {repo}"
    return ("rebase the stack bottom-up, each after the previous finishes: "
            + "; ".join(f"gh pr update-branch {n} --rebase --repo {repo}" for n in unit)
            + " (tell the lower layers' owners)")


def async_answer(r):
    """(status, details) of a merge-async response, (None, {}) when it is not one. gh prints the body on stdout on an
    HTTP error too."""
    try:
        body = json.loads(r.stdout)
        return (body.get("status"), body["details"]) if isinstance(body["details"], dict) else (None, {})
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, {}


def merge_async(repo, pr, head):
    """Request the merge and poll its uuid until it has a result: (gh result, status, details). Status is pending
    when 15 minutes were not enough, busy on a 409 that names no request to follow, None when the response had no
    status or uuid. https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously"""
    # merge_method applies only to a direct merge; on a merge queue branch the queue picks, and `enqueued` comes back.
    r = run(["gh", "api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/merge-async",
             "-f", "merge_method=squash", "-f", f"sha={head}"], check=False)
    status, details = async_answer(r)
    conflict = r.returncode != 0 and "HTTP 409" in r.stderr  # a request is running; it answers with that one's uuid
    if not details.get("uuid"):
        return r, "busy" if conflict else status if details else None, details
    if status != "pending" and not conflict:
        return r, status, details
    for _ in range(90):
        polled = run(["gh", "api", f"repos/{repo}/pulls/{pr}/merge-async/{details['uuid']}"], check=False)
        got, answer = async_answer(polled)
        if polled.returncode == 0 and got in ("merged", "enqueued", "failed"):
            return r, got, answer
        time.sleep(10)
    return r, "pending", details


def merge_unit(p, pr, unit, klass, events, onto=None, stacked=False, expect=None, verdict_sources=None):
    """Merge a PR (or a stack from its top) after re-checking every layer at its current head.
    `onto`: the lower PR whose branch a `--onto-lower` fold merges into; its ticket has not landed yet.
    `stacked`: the PR is in a GitHub stack, also when it lands alone as a lower layer.
    `expect`: the land executor's requested head; any other head, or a merge state other than clean, does not merge.
    `verdict_sources`: with `expect`, the sources whose pass at exactly that head the merge needs."""
    repo, states, runs = p.cfg["repo"], pr_state(events), []
    no_ci = no_ci_repo(repo)
    for n in unit:
        v = pr_view(repo, n)
        if expect and n == pr:
            if v["headRefOid"] != expect:
                return 3, (f"act: #{pr}'s head moved from {expect[:8]} to {v['headRefOid'][:8]} since the request:"
                           " review the new head, orch verdict, land again")
            if v["mergeStateStatus"] not in ("CLEAN", "HAS_HOOKS"):
                return 2, f"yield: #{pr}'s merge state is {v['mergeStateStatus']}, not CLEAN; land again"
        verdict = states.get(n, {}).get("verdict") or {}
        if patch_id(repo, n) != verdict.get("patch_id"):
            note_attempt(p, events, pr, "blocked", [f"patch of #{n} changed since its verdict"])
            return 3, f"act: the patch of #{n} changed since its verdict (conflict fix, restack or edit): re-review, then orch verdict"
        failed, pending, r_ = ci_summary(v["statusCheckRollup"], no_ci)
        runs += r_
        if failed or pending or v["mergeStateStatus"] in ("BEHIND", "DIRTY"):
            return 2, f"#{n} moved since the order was read; land again"
        if unfinished := runs_unfinished(repo, v["headRefOid"], no_ci):
            return 2, f"yield: CI has not finished at #{n}'s head ({', '.join(unfinished[:3])}); land again"
        if no_ci:
            # No run proves this head on this base, so the review has to be of exactly what merges.
            if verdict.get("sha") != v["headRefOid"]:
                return 3, (f"act: no-CI lane: #{n}'s verdict is for {(verdict.get('sha') or 'nothing')[:8]}, not the head"
                           f" {v['headRefOid'][:8]}: review that head, run the VERIFY commands, orch verdict, land again")
            if compare(repo, v.get("baseRefName") or "main", v["headRefOid"]).get("behind_by") != 0:
                return 3, (f"act: no-CI lane: #{n}'s head does not contain its base: {update_advice(unit, repo, stacked)},"
                           " review the new head, orch verdict, land again")
        try:
            threads = unresolved_threads(repo, n)
        except (SystemExit, Exception) as e:
            return 2, f"yield: #{n}'s review threads are unreadable ({type(e).__name__}); land again"
        if threads:
            note_attempt(p, events, pr, "blocked", [f"{threads} unresolved thread(s) on #{n}"])
            return 3, f"act: #{n} has {threads} unresolved review thread(s): answer and resolve them, then land again"
    if expect:
        # The verdict the executor checked may have been replaced since: merge only on the one standing now.
        import autoqueue
        now_events = p.events()
        if autoqueue.withdrawn(now_events, pr):  # a stop recorded during the checks is read from this same snapshot
            return 3, f"act: #{pr}'s land was stopped during the checks: record a verdict at the head or land again"
        latest = pr_state(now_events).get(pr, {}).get("verdict") or {}
        p.land_verdict = latest
        if latest.get("sha") != expect or latest.get("result") != "pass" or \
                (verdict_sources is not None and (latest.get("source") not in verdict_sources
                                                  or verdict_identity_problem(latest))):
            return 3, (f"act: #{pr}'s verdict changed during the checks ({latest.get('result')} from"
                       f" {latest.get('source')} at {(latest.get('sha') or '')[:8]}): review this head, orch verdict, land again")
    head = expect or v["headRefOid"]
    use_async = stacked or len(unit) > 1
    status, details = None, {}
    if not use_async:
        r = run(["gh", "pr", "merge", str(pr), "--repo", repo, "--squash", "--match-head-commit", head], check=False)
    else:
        # A stacked PR refuses `gh pr merge`, also as the last open layer; merge-async on a layer lands it and every
        # layer below, not above.
        r, status, details = merge_async(repo, pr, head)
        if status == "busy":
            return 2, f"yield: a merge request for #{pr} is already running; land again"
        if status in ("enqueued", "pending"):
            note = (f"in the merge queue: {details.get('message') or 'enqueued'}" if status == "enqueued"
                    else "merge request still pending after 15 minutes")
            p.append("yield", pr=pr, note=note, unit=unit)  # the layers may close apart, hours later
            return 2, f"yield: #{pr} {note}; land again records it once it has merged"
        if status is None and r.returncode == 0:
            print(f"warning: merge-async answered without a status or uuid; reading #{pr} instead: {r.stdout[:200]}", flush=True)
    merged = {}
    # `merged` is the requested PR's result and a stack's layers close seconds apart; with no result the PRs are all there is.
    reads = 1 if not use_async else 6 if status == "merged" else 90 if status is None and r.returncode == 0 else 1
    for i in range(reads):
        merged = {n: pr_view(repo, n) for n in unit}
        if all(x["state"] == "MERGED" for x in merged.values()) or i == reads - 1:
            break
        time.sleep(10)
    one_push = merged[pr]["state"] == "MERGED"  # a layer merged without its top got its own main CI run
    if one_push:
        record_queued(p, pr, skip=unit)
    for n, x in merged.items():
        if x["state"] == "MERGED" and "landed" not in states.get(n, {}):
            p.append("landed", pr=n, sha=(x.get("mergeCommit") or {}).get("oid"),
                     klass=klass if klass != "normal" else None, note=f"stack of {len(unit)}" if len(unit) > 1 else None,
                     stack_top=pr if n != pr and one_push else None, onto=onto)
            close_gate_task(p, n, "completed")
    if merged[pr]["state"] != "MERGED":
        why = details.get("message") if status == "failed" else r.stderr or ("" if use_async else r.stdout)
        why = (why or "").strip()[:300] or "merge-async did not finish in 15 minutes"
        if "head branch was modified" in why:
            return 2, f"#{pr} moved since the order was read; land again"
        if r.returncode != 0 and TRANSIENT.search(why):
            return 2, f"yield: GitHub did not answer the merge ({why[:160]}); land again"
        p.append("land_failed", pr=pr, note=why)
        if "needs to be rebased" in why:
            return 3, f"act: {why} {update_advice(unit, repo, True)}, then land again." + keep_note(no_ci, bool(expect))
        if "not up to date" in why or "behind" in why.lower():
            return 3, (f"act: the base requires up-to-date branches: {update_advice(unit, repo, stacked)}, then land again."
                       + keep_note(no_ci, bool(expect)))
        return 1, f"merge refused: {why}"
    sha = (merged[pr].get("mergeCommit") or {}).get("oid")
    if no_ci:
        return 0, (f"landed #{pr} (merge commit {sha[:8]}; no-CI repo, so no run proves main): run the brief's VERIFY"
                   f" commands at {sha}, then orch record {p.slug} main_green|main_red --pr {pr} --sha {sha}")
    return 0, (f"landed {' '.join('#' + str(n) for n in unit)} (merge commit {sha[:8]}; CI runs {' '.join(runs) or '-'}; read their logs in the report). "
               f"Watch main: gh run list --repo {repo} --commit {sha} --json databaseId,workflowName,status, "
               f"then gh run watch <id> --repo {repo} --exit-status --interval 60 (one watcher per run) and orch record {p.slug} main_green|main_red --pr {pr} --sha {sha}"
               + base_ci_hint(repo, merged[pr].get("baseRefName")))


def record_queued(p, pr, skip=()):
    """Record the layers that earlier requests for this PR left unfinished, bottom first, and return them all."""
    # Every request's unit: a retry after the lower layer merged names a smaller one.
    queued = list(dict.fromkeys(n for e in p.events() if e.get("pr") == pr for n in e.get("unit") or []))
    for n in queued:
        if n in skip:
            continue
        x = pr_view(p.cfg["repo"], n)
        if x["state"] == "MERGED" and "landed" not in pr_state(p.events()).get(n, {}):
            # No stack_top: the queue may have put the layers in separate merge groups.
            p.append("landed", pr=n, sha=(x.get("mergeCommit") or {}).get("oid"), note="earlier merge request")
            close_gate_task(p, n, "completed")
    return queued


def base_ci_hint(repo, base):
    """A push-triggered CI often covers only the default branch, so a landing on a feature base starts no run."""
    if not base or base in ("main", "master"):
        return ""
    return (f". The base is {base}: if gh run list shows no run for the merge commit, its CI does not run on push"
            f" there; start it with gh workflow run <ci workflow file> --repo {repo} --ref {base} and watch that run")


def stack_refusal(p, pr, stacks):
    """The land executor's refusal of a PR in a stack. Only the bottom layer has a way in: once the layer above
    stands on the base, `pr` is a single PR. Retargeting rewrites that layer's diff, so its verdict is stale; the
    layers further up stay on it. Any other layer waits for the ones below."""
    why = f"#{pr} is in a stack: the land executor lands single PRs only"
    members = next((m for m in stacks.values() if pr in m), [])
    if not members:
        return why
    if members[0] != pr:
        return f"{why}. Land #{members[0]} first, then the layers above it in order"
    if len(members) < 2:
        return why
    # Read here, not from open_rows (one page of PRs): a guessed base would send the retarget to the wrong branch.
    base = pr_view(p.cfg["repo"], pr).get("baseRefName")
    if not base:
        return f"{why}. #{pr} is the bottom layer; retarget #{members[1]} onto #{pr}'s base (it could not be read), then land again"
    return (f"{why}. #{pr} is the bottom layer; do: gh pr edit {members[1]} --base {base} --repo {p.cfg['repo']},"
            f" then orch land {p.slug} --pr {pr} (#{members[1]} needs a new review: its diff changed)")


def attempt(p, pr, klass, onto_lower=False, expect=None, verdict_sources=None):
    """One pass: returns (exit_code, message). `expect`: the head the land executor was asked to merge, and
    `verdict_sources` the sources its verdict must come from (merge_unit)."""
    events, cfg, repo = p.events(), p.cfg, p.cfg["repo"]
    st = pr_state(events).get(pr, {})
    if "landed" in st:
        return 0, f"#{pr} already landed as {st['landed'].get('sha', '')[:8]}"
    if stopped(events):
        return 1, "STOP line active: nothing lands"
    main = main_state(events)
    if main == "red" and klass != "main-fix":
        return 1, "safety stop: main is red; only the repair lands (--class main-fix)"
    rows = open_rows(repo)
    stacks = read_stacks(repo)
    unread = stacks is None
    if unread:
        print("warning: the repository's stacks could not be read; dependent PRs are not checked", flush=True)
        if in_a_stack(repo, pr):
            if expect:
                return 1, f"#{pr} is in a stack: the land executor lands single PRs only"
            # merge-async takes every open layer below; without the listing those layers go unverified.
            return 2, f"yield: #{pr} is in a stack and the stack listing did not answer; land again"
        stacks = {}
    deps = orca_deps(cfg["run"])
    order = land_order(events, rows, cfg, dt.datetime.now(dt.timezone.utc), stacks, deps)
    stacked = any(pr in members for members in stacks.values())
    if expect and (stacked or onto_lower):
        # Its checks pin one PR at one sha; the lower layers merge-async would take have no such pin.
        return 1, stack_refusal(p, pr, stacks)
    if expect and unread:
        return 2, "yield: the stack listing did not answer, so the land executor cannot tell a single PR; land again"
    members = next((m for m in stacks.values() if pr in m), [])
    seen = {r["number"] for r in rows}
    if unseen := [m for m in members[:members.index(pr)] if m not in seen] if members else []:
        return 3, (f"act: stack layer {', '.join(f'#{m}' for m in unseen)} under #{pr} is not among the {OPEN_LIMIT} open"
                   " PRs read, so it cannot be checked, and merge-async would take it too. Land it first")
    me = next((e for e in order if e["pr"] == pr), None)
    tracked = pr_state(events)  # an integration branch's own PR (feature → main) is never landed through orch
    below = next((r for r in rows if me and not me["stack"] and r.get("headRefName") == me.get("base")
                  and r["number"] in tracked), None)
    lower = None if onto_lower else below
    onto = below["number"] if onto_lower and below else None
    if lower:
        # Based on another open PR's branch without being a GitHub stack: a squash would land in that branch.
        return 3, (f"act: #{pr} is based on #{lower['number']}'s branch {me['base']}, not a GitHub stack, so a merge would"
                   f" land in that branch. Stack them: gh stack link {lower['number']} {pr} --base"
                   f" {lower.get('baseRefName')}, confirm with gh api repos/{repo}/pulls/{pr} --jq .stack, and land again."
                   f" To fold it into #{lower['number']} on purpose (top-down), land again with --onto-lower")
    if me is None or me["state"] == "gone":
        v = pr_view(repo, pr)
        if v["state"] == "MERGED":
            sha = (v.get("mergeCommit") or {}).get("oid")
            st = pr_state(p.events()).get(pr, {})  # the top's land call may have recorded this layer since we read
            if "landed" in st:  # a stack layer lands with its top, whose land call recorded it
                return 0, (f"#{pr} already landed as {(st['landed'].get('sha') or '')[:8]} ({st['landed'].get('note') or 'recorded'});"
                           " main CI ran once on the stack top's commit, and the top's owner records it")
            if queued := record_queued(p, pr):
                return 0, f"#{pr} was merged by an earlier request (merge queue or a slow merge); recorded {' '.join(f'#{n}' for n in queued)}"
            p.append("landed", pr=pr, sha=sha, note="merged outside land")
            close_gate_task(p, pr, "completed")
            return 0, f"#{pr} was merged outside land; recorded"
        if v["state"] == "CLOSED":
            close_gate_task(p, pr, "failed")
        return 1, f"#{pr} is {v['state']}"
    if on_hold(events, pr):
        return 1, HoldRefusal(f"#{pr} is on hold (record parked); `orch record {p.slug} admitted --pr {pr}` releases it")
    # Before the waits below: a dependent PR outside a stack waits on them for as long as its lower PR takes.
    gaps, flipped = stack_gaps(events, rows, stacks, deps, pr) if not unread else ([], [])
    if flipped:
        note_attempt(p, events, pr, "blocked", ["stack order flipped"])
        return 3, "act: " + "; ".join(FLIPPED.format(low=low, up=up) for low, up in flipped)
    level = stack_level()
    if gaps and level != "off":
        text = gap_text(repo, gaps, me.get("base") or "main")
        if level == "deny" and klass != "main-fix":
            note_attempt(p, events, pr, "blocked", ["dependent PR outside a stack"])
            return 3, f"act: {text} (orchestrate.stack_dependent: deny)"
        print(f"warning: {text}", flush=True)
    if me["state"] == "waiting":
        if st.get("yield", {}).get("note") != me["reasons"][0]:
            p.append("yield", pr=pr, note=me["reasons"][0])
        return 2, f"yield: {me['reasons'][0]}"
    # The land executor waits on GitHub's own rule (a required review or check, which can still come) only once
    # the checks below that row_state does not cover (evidence, threads) pass too.
    github_block = bool(expect) and me["reasons"] == [PROTECTED]
    if me["state"] == "blocked" and not github_block:
        note_attempt(p, events, pr, "blocked", me["reasons"])
        todo = [ADVICE.get(r, r).format(pr=pr, repo=repo) for r in me["reasons"]]
        if "behind base" in me["reasons"]:
            todo.append(keep_note(no_ci_repo(repo), bool(expect)).strip())
        return 3, "act: " + " | ".join(todo)
    unit = (me["stack"] or [pr])[: (me["stack"] or [pr]).index(pr) + 1]
    if cfg["merge_policy"] == "human-gate":
        for n in unit:
            if hold := gate_hold(p, p.events(), n):
                return 1, f"#{n}: {hold}"
    missing = []
    for n in unit:
        problems, notes = evidence_problems(cfg, repo, n)
        missing += problems
        for line in notes:
            print(line, flush=True)
    if missing:
        note_attempt(p, events, pr, "blocked", missing)
        return 3, "act: " + " | ".join(missing)
    if github_block:
        try:
            threads = unresolved_threads(repo, pr)
        except (SystemExit, Exception) as e:
            return 2, f"yield: #{pr}'s review threads are unreadable ({type(e).__name__}); land again"
        if threads:
            note_attempt(p, events, pr, "blocked", [f"{threads} unresolved thread(s) on #{pr}"])
            return 3, f"act: #{pr} has {threads} unresolved review thread(s): answer and resolve them, then land again"
        note_attempt(p, events, pr, "blocked", me["reasons"])
        release_own_lanes(p, pr)  # a wait for a person must not hold a lane, also one taken in an earlier round
        return 2, f"yield: GitHub blocks #{pr}: a required review or check is missing; it lands once GitHub allows it"
    no_ci = no_ci_repo(repo)
    keep = keep_note(no_ci, bool(expect))  # the executor passes `expect`; the direct path never does
    touched = is_exclusive(cfg, [f for n in unit for f in pr_files(repo, n)])
    if (bool(touched) or no_ci) != bool((st.get("lane") or {}).get("exclusive")):
        p.append("lane", pr=pr, exclusive=bool(touched) or no_ci, note=", ".join(touched[:5]) or ("no-CI repo" if no_ci else None))
        me["exclusive"] = bool(touched) or no_ci or me["klass"] == "gate"
    if me["exclusive"]:
        # PRs ahead whose lane is not known yet (they have not called land) are classified now.
        for e in order[:order.index(me)]:
            if e.get("base") == me["base"] and e["state"] in ("ready", "catching_up") and not e["exclusive"] \
                    and e["claimed"] and "lane" not in pr_state(events).get(e["pr"], {}):
                hit = is_exclusive(cfg, pr_files(repo, e["pr"]))
                p.append("lane", pr=e["pr"], exclusive=bool(hit), note=", ".join(hit[:5]) or None)
                e["exclusive"] = bool(hit)
        lane = ExclusiveLock(p, me["base"] or "main")
        ahead = exclusive_turn(order, pr)
        held = lane.holder()
        if held and (held.get("pr"), held.get("program")) != (pr, p.slug):
            held = lane.break_if_closed(held)
        if held and (held.get("pr"), held.get("program")) != (pr, p.slug):
            why = ", on hold" if held.get("program") == p.slug and on_hold(events, held.get("pr")) else ""
            return 2, f"yield: the exclusive lane of {me['base']} is held by #{held.get('pr')} ({held.get('program')}{why})"
        if not held and ahead:
            return 2, f"yield: #{ahead['pr']} ({ahead['ticket']}) takes the exclusive lane first ({ahead['state']})"
        if not lane.take(pr):
            return 2, "yield: another lander just took the exclusive lane"
        if me["state"] == "catching_up":
            note_attempt(p, events, pr, "catching_up", me["reasons"])
            return 3, ("act: you hold the exclusive lane; bring the branch onto the latest base (restack migrations),"
                       f" let CI run, land again — {'; '.join(me['reasons'])}." + keep)
        if compare(repo, me["base"] or "main", pr_view(repo, pr)["headRefOid"]).get("behind_by"):
            return 3, (f"act: you hold the exclusive lane and it lands on the latest base only: {update_advice(unit, repo, stacked)},"
                       " let CI run, land again." + keep)
        code, msg = merge_unit(p, pr, unit, klass, events, onto, stacked, expect, verdict_sources)
        if code == 0:
            lane.release(pr)
        elif code == 1:  # parked, so exclusive_turn passes it over until `record admitted`
            p.append("parked", pr=pr, note=msg[:300])
            return code, HoldRefusal(msg)
        return code, msg
    if me["state"] == "catching_up":
        note_attempt(p, events, pr, "catching_up", me["reasons"])
        if "behind base" in me["reasons"]:
            return 3, f"act: {update_advice(unit, repo, stacked)}, then land again." + keep
        return 2, f"not yet:{'; '.join(me['reasons'])}"
    base = me["base"] or "main"
    overlap = base_overlap(repo, base, compare(repo, base, pr_view(repo, pr)["headRefOid"]))
    if overlap:
        note_attempt(p, events, pr, "act", [f"base changed {', '.join(overlap[:5])}"])
        return 3, (f"act: since your branch point the base changed files you also change ({', '.join(overlap[:5])}):"
                   f" {update_advice(unit, repo, stacked)}, check your contract and test assumptions still hold,"
                   " let CI run, land again." + keep)
    return merge_unit(p, pr, unit, klass, events, onto, stacked, expect, verdict_sources)


LAND_HEARTBEAT = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() \
    / "land-exec.json"
LAND_STALE_S = 600


def land_executor(repo=None):
    """`orchestrate.land_executor` of agent-skills.json when it is on (and lists `repo`, when given), else None. A file
    that names the key but cannot be parsed stops the caller: a direct merge then would skip the executor's checks."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        sys.exit(f"{path}: unreadable ({e}); land stops rather than decide without orchestrate.land_executor")
    try:
        cfg = (json.loads(text).get("orchestrate") or {}).get("land_executor")
    except (ValueError, AttributeError) as e:
        if "land_executor" in text:
            sys.exit(f"{path}: unreadable ({e}); land stops rather than merge around orchestrate.land_executor")
        return None
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    try:
        scope = land_scope.parse(cfg)
    except land_scope.ScopeError as e:
        sys.exit(f"{path}: {e}; land stops rather than guess the executor's repos")
    if repo is not None and not land_scope.how(scope, repo):
        return None
    repos = [r for r in cfg.get("repos") or [] if isinstance(r, str)]
    hours = cfg.get("max_wait_hours")
    try:
        hours = 24.0 if hours is None else float(hours)
    except (TypeError, ValueError):
        hours = float("nan")
    if not (math.isfinite(hours) and hours > 0) or isinstance(cfg.get("max_wait_hours"), bool):
        # A wait that can never end (NaN, inf) or a typo ("24h") would keep requests queued for ever.
        sys.exit(f"{path}: orchestrate.land_executor.max_wait_hours must be a positive number of hours, "
                 f"not {cfg.get('max_wait_hours')!r}")
    sources = cfg.get("verdict_sources") or ["review:codex"]
    if not isinstance(sources, list) or not all(isinstance(s, str) and s for s in sources):
        # A bare string would match by substring ("review:codex" in "review:codex-old") and index by letter.
        sys.exit(f"{path}: orchestrate.land_executor.verdict_sources must be a list of sources, not {sources!r}")
    import autoqueue
    return {"repos": repos, "scope": scope, "verdict_sources": sources, "max_wait_hours": hours,
            "auto_queue": autoqueue.config(cfg.get("auto_queue"))}


def executor_for(repo):
    """(land_executor(), repo_meta) when the executor covers the repo GitHub serves under `repo` now, else (None, meta).
    The recorded name alone cannot choose the direct merge: a program made before a rename still names the old one.
    An exclusion under either name keeps the repo out. A GitHub that does not answer exits; never a guessed direct merge."""
    ex = land_executor()
    if not ex:
        return None, None
    meta = repo_meta(repo)
    now_named = meta["nameWithOwner"]
    if land_scope.excluded(ex["scope"], repo) or land_scope.excluded(ex["scope"], now_named):
        return None, meta
    if not land_scope.how(ex["scope"], now_named):
        if land_scope.how(ex["scope"], repo):
            # Covered under the recorded name, not under GitHub's: a rename or transfer moved it out. Merging it directly
            # would route around the executor exactly when the repo's owner or name changed.
            sys.exit(f"refused: {repo} is {now_named} on GitHub now, which orchestrate.land_executor does not cover;"
                     " list the new name (skills-config) or exclude it, then land again")
        return None, meta
    return ex, meta


LAND_EXIT = {"merged": 0, "superseded": 2}


def land_request(p, pr, klass, argv):
    """With the land executor on: record a request for the current head and wait for its result. Nothing here merges."""
    import store
    repo = p.cfg["repo"]
    if "--onto-lower" in argv:
        print("refused: the land executor lands single PRs only; --onto-lower needs orchestrate.land_executor off")
        sys.exit(1)
    v = pr_view(repo, pr)
    if v["state"] != "OPEN":
        print(f"#{pr} is {v['state']}; nothing to request")
        sys.exit(0 if v["state"] == "MERGED" else 1)
    base = run(["gh", "api", f"repos/{repo}/commits/{v.get('baseRefName') or 'main'}", "--jq", ".sha"], check=False)
    try:
        row, outcome = store.land_put(p.slug, str(p.dir.parent.resolve()), repo, pr, v["headRefOid"], base.stdout.strip() or None,
                                      klass, os.environ.get("ORCA_TERMINAL_HANDLE"), by=os.environ.get("ORCA_TERMINAL_HANDLE"))
    except store.NotMigrated as e:
        print(f"refused: {e}")
        sys.exit(1)
    if outcome == "busy":
        print(f"yield: the land executor is running request {row['id']} for {row['sha'][:8]}; land again")
        sys.exit(2)
    print(f"{'queued' if outcome == 'new' else 'waiting on'} land request {row['id']}: #{pr} at {row['sha'][:8]}."
          " The land executor re-checks and merges it; this command only reads the result.", flush=True)
    deadline = time.monotonic() + 60 * float(opt(argv, "--wait-minutes", "0"))
    while True:
        row = store.land_get(row["id"])
        if row["status"] not in store.LAND_OPEN:
            note = f" as {row['merge_sha'][:8]}" if row.get("merge_sha") else ""
            print(f"{row['status']}{note} (land request {row['id']}): {row.get('reason') or ''}".rstrip(": "))
            sys.exit(LAND_EXIT.get(row["status"], row.get("code") or 1))
        try:
            beat = json.loads(LAND_HEARTBEAT.read_text()).get("ts")
            stale = beat is None or (dt.datetime.now(dt.timezone.utc) - parse_ts(beat)).total_seconds() > LAND_STALE_S
        except (OSError, ValueError):
            stale = True
        if stale:
            print(f"yield: no land executor round in the last {LAND_STALE_S // 60} minutes ({LAND_HEARTBEAT}); the request"
                  " stays queued. Turn it on: skills-sync setup enable land-executor")
            sys.exit(2)
        if time.monotonic() >= deadline:
            print(f"yield: land request {row['id']} is still {row['status']}: {row.get('reason') or 'not tried yet'}")
            sys.exit(2)
        time.sleep(15)


def cmd_land(argv):
    p = standalone_target(argv)
    pr, klass = int(opt(argv, "--pr")), opt(argv, "--class")
    if klass and klass not in KLASS:
        sys.exit(f"--class is one of {', '.join(KLASS)}")
    try:
        executor = executor_for(p.cfg["repo"])[0]
    except (SystemExit, KeyError, TypeError, ValueError) as e:
        if isinstance(e, SystemExit) and not TRANSIENT.search(str(e.code)):
            raise
        print(f"yield: GitHub did not say which repo {p.cfg['repo']} is now, so executor or direct merge is undecided"
              f" ({str(getattr(e, 'code', e))[:160]}); land again")
        sys.exit(2)
    events = p.events()
    st = pr_state(events).get(pr, {})
    if not claimed(events, pr) or (klass and klass != st["ready"].get("klass", "normal")):
        p.append("ready", pr=pr, ticket=opt(argv, "--ticket"), klass=klass)
    klass = klass or next((e["klass"] for e in reversed(p.events()) if e.get("pr") == pr and e.get("klass")), "normal")
    if executor:
        land_request(p, pr, klass, argv)
    deadline = time.monotonic() + 60 * float(opt(argv, "--wait-minutes", "0"))
    while True:
        try:
            code, msg = attempt(p, pr, klass, "--onto-lower" in argv)
        except SystemExit as e:
            if not TRANSIENT.search(str(e.code)):
                stop_land(p, pr, str(e.code))
                raise
            code, msg = 2, f"yield: GitHub or Orca did not answer ({str(e.code)[:160]}); land again"
        print(msg, flush=True)
        if code == 1:
            stop_land(p, pr, msg)
        if code != 2 or time.monotonic() >= deadline:
            sys.exit(code)
        time.sleep(60)


def cmd_land_check(argv):
    p = Program(argv[0])
    repo, pr = p.cfg["repo"], int(opt(argv, "--pr"))
    events = p.events()
    st = pr_state(events).get(pr, {})
    v = pr_view(repo, pr)
    if unread := [k for k in LAND_CHECK_FIELDS if v.get(k) is None]:
        print(f"warning: gh returned no {','.join(unread)} for #{pr}: {json.dumps(v)[:400]}", file=sys.stderr)
        print(f"HOLD: PR state unknown (no {','.join(unread)} from gh); run land-check again")
        sys.exit(1)
    import landjudge
    problems = []
    main = main_state(events)
    no_ci = no_ci_repo(repo)
    verdict = st.get("verdict")
    passing = bool(verdict) and verdict.get("result") == "pass"
    if main == "pending":
        print("note: main CI for the last landing has not reported yet")
    cur = patch_id(repo, pr) if passing else None  # only a passing verdict is compared with the patch
    facts = {
        "state": v["state"], "draft": v["isDraft"], "merge_state": v["mergeStateStatus"], "head": v["headRefOid"],
        "stopped": stopped(events), "held": on_hold(events, pr), "no_ci": no_ci,
        "verdict": {"pass": passing, "sha": (verdict or {}).get("sha") or ""} if verdict else None,
        "patch_match": cur == verdict.get("patch_id") if passing else landjudge.NOT_COMPUTED}
    why = landjudge.judge(facts, "check").why
    if main == "red" and "--main-fix" not in argv:
        problems.append("safety stop: main is red (only the repairing PR lands, with land --class main-fix)")
    if "stopped" in why:
        problems.append("STOP line active")
    if "held" in why:
        problems.append(f"on hold (record parked); `orch record {p.slug} admitted --pr {pr}` releases it")
    if "not_open" in why:
        problems.append(f"PR is {v['state']}{' draft' if v['isDraft'] else ''}")
    if "no_verdict" in why:
        problems.append("no passing verdict recorded (orch verdict)")
    if "patch_changed" in why:
        problems.append(f"patch changed since verdict ({verdict.get('patch_id', '')[:10]} -> {cur[:10]}): re-review")
    if "no_ci_sha" in why:
        problems.append(f"no-CI lane: verdict is for {verdict['sha'][:8]}, not the head {v['headRefOid'][:8]}: re-review")
    ex, ex_unknown = None, False
    # The same decision `orch land` makes, on the repo GitHub serves now: the recorded name alone reads a renamed
    # repo as one the executor does not cover, and passes a verdict the executor then refuses. It is read also with no
    # passing verdict, since the update advice below depends on the path, not on the verdict.
    try:
        ex = executor_for(repo)[0]
    except SystemExit as e:  # a repo renamed out of the executor's scope, or GitHub not answering: orch land stops too
        ex_unknown = True
        if passing:
            problems.append(str(e.code)[:300])
    if passing and ex and (executor_why := executor_verdict_problem(verdict, v["headRefOid"], ex["verdict_sources"])):
        problems.append(executor_why)  # the executor would refuse it: say so now
    if "head_moved" in why and not ex and not ex_unknown:  # the executor, or not knowing it, never promises the carry
        print(f"note: head moved {verdict['sha'][:8]} -> {v['headRefOid'][:8]} with the same patch-id; verdict holds, CI must pass at the new head")
    if p.cfg["merge_policy"] == "human-gate" and (hold := gate_hold(p, events, pr)):
        problems.append(hold)
    try:
        rows = open_rows(repo)
    except SystemExit:  # this listing serves only the check below, which passes when it cannot read
        rows = None
    stacks = read_stacks(repo)
    members = next((m for m in (stacks or {}).values() if pr in m), [])
    unread = stacks is None or rows is None
    gaps, flipped = ([], []) if unread else stack_gaps(events, rows, stacks, orca_deps(p.cfg["run"]), pr)
    level = stack_level()
    if unread:
        print("warning: the repository's stacks could not be read; dependent PRs are not checked")
    problems += [FLIPPED.format(low=low, up=up) for low, up in flipped]
    if gaps and level == "deny" and "--main-fix" not in argv:
        problems.append(gap_text(repo, gaps, v.get("baseRefName") or "main"))
    elif gaps and level != "off":
        print(f"warning: {gap_text(repo, gaps, v.get('baseRefName') or 'main')}")
    mss = v["mergeStateStatus"]
    if "dirty" in why:
        problems.append("conflicts with base: fix task in the PR's worktree")
    elif "behind" in why:
        unit = members[:members.index(pr) + 1] if members else [pr]
        problems.append(f"behind base: {update_advice(unit, repo, bool(members))}, then wait for CI."
                        + (EXECUTOR_KEEP if ex or ex_unknown else keep_note(no_ci, False)))
    # CI is read here, after the stack lookups, as it always was: a run that finishes meanwhile is seen as finished.
    failed, pending, runs = ci_summary(v.get("statusCheckRollup"), no_ci)
    if not failed and not pending:
        pending = runs_unfinished(repo, v["headRefOid"], no_ci)
    why = landjudge.judge({**facts, "ci_failed": failed, "ci_pending": pending}, "check").why
    if "ci_failed" in why:
        problems.append("CI failed: " + ", ".join(failed))
    if "ci_pending" in why:
        problems.append("CI pending: " + ", ".join(pending[:5]))
    if no_ci and compare(repo, v.get("baseRefName") or "main", v["headRefOid"]).get("behind_by") != 0:
        problems.append("no-CI lane: the head does not contain its base")
    try:
        if threads := unresolved_threads(repo, pr):
            problems.append(f"{threads} unresolved review thread(s)")
    except (SystemExit, Exception) as e:
        problems.append(f"review threads unreadable ({type(e).__name__})")
    missing, notes = evidence_problems(p.cfg, repo, pr, need_comment=bool(ex))
    problems += missing
    for line in notes:
        print(f"note: {line}")
    print(f"PR {v['url']} head {v['headRefOid'][:8]} mergeState {mss} runs {' '.join(runs) or '-'}")
    for r in runs:
        print(f"  read the log before landing: gh run view {r} --repo {repo} --log | tail -40")
    if problems:
        print("HOLD: " + " | ".join(problems))
        sys.exit(1)
    print(f"READY: orch land {p.slug} --pr {pr}")


def cmd_landed(argv):
    p = Program(argv[0])
    repo, pr = p.cfg["repo"], int(opt(argv, "--pr"))
    v = pr_view(repo, pr)
    if v["state"] != "MERGED":
        p.append("land_failed", pr=pr, note=f"state {v['state']}")
        if v["state"] == "CLOSED":
            close_gate_task(p, pr, "failed")
        sys.exit(f"PR {pr} is {v['state']}, recorded land_failed")
    sha = (v.get("mergeCommit") or {}).get("oid")
    p.append("landed", pr=pr, sha=sha)
    close_gate_task(p, pr, "completed")
    print(f"landed #{pr} as {sha[:8]}. Watch main CI in the background, then record main_green or main_red:")
    print(f"  gh run list --repo {repo} --commit {sha} --json databaseId,workflowName,status")
    print(f"  gh run watch <databaseId> --repo {repo} --exit-status --interval 60   # one watcher per run")
    if hint := base_ci_hint(repo, v.get("baseRefName")):
        print(" " + hint.lstrip(". "))


def cmd_gate(argv):
    p = Program(argv[0])
    pr = int(opt(argv, "--pr"))
    if p.cfg["merge_policy"] != "human-gate":
        sys.exit("gates are for merge_policy human-gate; autonomous programs land with orch land")
    if p.cfg.get("lead_dispatch"):
        sys.exit("this lead is a dispatched worker, and Orca lets no worker create a Task or gate on the Run it works "
                 "for (worker_cannot_coordinate_own_run), so `orch land` never sees `approved`. Have the orchestrator "
                 "restart this lead as a /goal card (`skills-sync card`, its brief's MERGE line saying human-gate)")
    if (opened := gate_rows(p.events(), pr)) and gate_resolution(p.cfg, p.events(), pr, warn=False) is None:
        gid = opened[-1]["gate"]
        sys.exit(f"a gate for #{pr} is already open; the user resolves it in Orca"
                 f" (or: orca orchestration gate-resolve --id {gid} --resolution land|hold)")
    v = pr_view(p.cfg["repo"], pr)
    if v.get("state") != "OPEN":
        sys.exit(f"PR #{pr} is {v.get('state') or 'in an unknown state'}; a landing gate is only for an open PR")
    url = v.get("url") or f"#{pr}"
    if title := v.get("title"):
        question = f"Land PR #{pr}: {title}?"
    else:
        print(f"warning: gh returned no title for #{pr}; the gate asks with the PR URL instead", file=sys.stderr)
        question = f"Land PR #{pr} ({url})?"
    spec = (f"Land PR #{pr} ({url}) with orch land once the user resolves the gate. "
            "Lead-owned; no worker is dispatched for this Task.")
    args = ["orchestration", "task-create", "--run", p.cfg["run"], "--spec", spec, "--task-title", f"{GATE_TASK}{pr}"]
    if opt(argv, "--parent"):
        args += ["--parent", opt(argv, "--parent")]
    task = orca_json(*args)
    task_id = (task.get("task") or task).get("id")
    if not task_id:
        sys.exit(f"orca task-create returned no Task id, so no gate was opened: {json.dumps(task)[:400]}")
    try:
        gate = orca_json("orchestration", "gate-create", "--task", task_id,
                         "--question", question, "--options", json.dumps(["land", "hold"]))
        gate_id = (gate.get("gate") or gate).get("id")
        if not gate_id:
            sys.exit(f"orca gate-create returned no gate id: {json.dumps(gate)[:400]}")
    except SystemExit as e:
        # Unrecorded, the gate-less Task would sit in task-list --ready for good; the next `orch gate` makes a new one.
        r = run(["orca", "orchestration", "task-update", "--id", task_id, "--status", "failed",
                 "--run", p.cfg["run"], "--json"], check=False)
        closed = "marked failed" if r.returncode == 0 else f"left open, task-update failed ({r.returncode}); close it in Orca"
        sys.exit(f"{e}. No gate recorded; Task {task_id} {closed}")
    print(json.dumps(p.append("gate_opened", pr=pr, task=task_id, gate=gate_id), ensure_ascii=False))
    print(f"the user resolves it in Orca (or: orca orchestration gate-resolve --id {gate_id} --resolution land)")


def ticket_in(keys, *texts):
    """The first ticket ID with one of the program's team keys in the texts (title, then branch)."""
    for text in texts:
        for key, num in re.findall(r"(?<![A-Za-z0-9])([A-Za-z0-9]+)-(\d+)", text or ""):
            if key.upper() in keys:
                return f"{key.upper()}-{num}"
    return None


def main_results(repo, base, since):
    """{commit: (main_green|main_red, ts)} from the push CI on base. Every workflow of a commit counts;
    a cancelled run (superseded) does not, and a commit with a run still going has no result yet."""
    runs = gh_json("run", "list", "--repo", repo, "--branch", base, "--event", "push", "--created", f">={since}",
                   "--limit", "5000", "--json", "headSha,status,conclusion,updatedAt")
    by_sha = {}
    for r in runs:
        if r.get("conclusion") != "cancelled":
            by_sha.setdefault(r["headSha"], []).append(r)
    out = {}
    for sha, rs in by_sha.items():
        if all(r.get("status") == "completed" for r in rs):
            red = any((r.get("conclusion") or "").upper() not in PASSING for r in rs)
            out[sha] = ("main_red" if red else "main_green", max(r["updatedAt"] for r in rs))
    return out


def cmd_backfill(argv):
    """Merges from before the program was registered, as landings at their real merge time with their
    main push CI result, so cap, rate and the dashboard count them. Rows carry note "backfill" and go
    before the program's own rows; a PR already in the ledger is skipped, so a rerun adds only what is new
    and the results that came in since."""
    p = Program(argv[0])
    cfg, repo = p.cfg, p.cfg["repo"]
    if not opt(argv, "--since"):
        sys.exit("backfill needs --since ISO8601, when the program's work began: older merges in the repo are not its landings")
    since = parse_ts(opt(argv, "--since"))
    since = (since if since.tzinfo else since.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)
    text = p.ledger_path.read_text() if p.ledger_path.exists() else ""
    events = [json.loads(l) for l in text.splitlines() if l.strip()]
    until = parse_ts(cfg["created_at"])
    have = {e.get("pr") for e in events if e["ev"] == "landed"}
    merged = gh_json("pr", "list", "--repo", repo, "--state", "merged", "--limit", "5000",
                     "--search", f"merged:>={since.date().isoformat()}", "--json", "number,title,headRefName,mergedAt,mergeCommit")
    merged = sorted((m for m in merged if m.get("mergeCommit") and m["number"] not in have
                     and since <= parse_ts(m["mergedAt"]) < until), key=lambda m: m["mergedAt"])
    # CI still running at an earlier backfill: its landings are asked again.
    decided = {e.get("sha") for e in events if e["ev"] in ("main_green", "main_red")}
    pending = [e for e in events if e["ev"] == "landed" and e.get("note") == "backfill" and e.get("sha") not in decided]
    rows = backfill_rows(cfg, merged, pending, events) if merged or pending else []
    if "--dry-run" in argv:
        # The window is the lead's judgment: in a repo shared with other work, read what it holds.
        for m in merged:
            print(f"  #{m['number']} {m['mergedAt']} {m['title']}")
    if rows:
        write_backfill(p, text, events, rows, "--dry-run" in argv)
    else:
        print(f"nothing to backfill: every PR merged between {since.isoformat()} and {cfg['created_at']} is in the ledger")
    if "--dry-run" in argv:
        return
    # The program's clock starts at its first landing, or rate, growth and the dashboard window miss the
    # history. Reconciled from the ledger on every run, so a run stopped between the two files is repaired.
    first = min((e["ts"] for e in p.events() if e["ev"] == "landed" and e.get("note") == "backfill"),
                key=parse_ts, default=None)
    moved = []

    def earlier(cfg):
        if first and parse_ts(first) < parse_ts(cfg["created_at"]):
            cfg["created_at"] = first
            moved.append(first)
    p.update(earlier)
    if moved:
        p.append("config", note=f"created_at={first} (backfill)")
        print(f"created_at moved to {first}")


def utc(ts):
    return parse_ts(ts).astimezone(dt.timezone.utc).isoformat()


def backfill_rows(cfg, merged, pending, events):
    """Landing rows for the merged PRs, and result rows for them and for the pending backfilled landings."""
    base = cfg.get("base") or gh_json("repo", "view", cfg["repo"], "--json", "defaultBranchRef")["defaultBranchRef"]["name"]
    # Matched by commit, not by the PR's base: a stack's lower layer names a branch yet lands on the base.
    results = main_results(cfg["repo"], base, min(utc(t) for t in [m["mergedAt"] for m in merged] + [e["ts"] for e in pending])[:10])
    keys = {t.rsplit("-", 1)[0].upper() for t in cfg["predicate"] + [e["ticket"] for e in events if e.get("ticket")]}
    rows = [{"ts": utc(m["mergedAt"]), "ev": "landed", "pr": m["number"], "sha": m["mergeCommit"]["oid"],
             "ticket": ticket_in(keys, m["title"], m["headRefName"]), "note": "backfill"} for m in merged]
    for sha in [r["sha"] for r in rows] + [e["sha"] for e in pending]:
        if sha in results:
            ev, at = results[sha]
            rows.append({"ts": utc(at), "ev": ev, "sha": sha, "note": "backfill"})
    return sorted(({k: v for k, v in r.items() if v is not None} for r in rows), key=lambda r: parse_ts(r["ts"]))


def write_backfill(p, text, events, rows, dry_run):
    landed = sum(r["ev"] == "landed" for r in rows)
    red = sum(r["ev"] == "main_red" for r in rows)
    print(f"backfill {landed} landings ({sum('ticket' in r for r in rows)} with a ticket),"
          f" {len(rows) - landed - red} main green, {red} main red")
    if dry_run:
        return
    old = [e for e in events if e.get("note") == "backfill"]
    own = [e for e in events if e.get("note") != "backfill"]
    # Every landing merged before the program's own rows, so history goes first; a result that came in
    # later (CI finishing after registration) takes its place in time among them, as cap_from reads in order.
    merged = heapq.merge(sorted(old + rows, key=lambda r: parse_ts(r["ts"])), own, key=lambda r: parse_ts(r["ts"]))
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged)
    with p.locked():
        if (p.ledger_path.read_text() if p.ledger_path.exists() else "") != text:
            sys.exit("the ledger changed while backfilling (a lander appended); run backfill again")
        (p.dir / f"ledger.jsonl.bak-{now().replace(':', '')}").write_text(text)
        tmp = p.dir / "ledger.jsonl.tmp"
        tmp.write_text(body)
        os.replace(tmp, p.ledger_path)
    print(f"written; the old ledger is kept beside it as {p.dir.name}/ledger.jsonl.bak-*")


def check(*args):
    """`orca orchestration check` with the full record: Orca 1.4.246's concise view drops deliveryId, payload and
    from_handle, and a wait that cannot tell its batch was delivered takes the next, empty one."""
    import orca_settings
    return orca_settings.with_full(orca_json, "orchestration", "check", *args)


def batch(res):
    return res.get("deliveryId"), res.get("messages") or []


def revoked_echo(m):
    """Orca's refusal of a message a released worker sent again (usually a repeated worker_done). It arrives with
    the original type, so it would wake the wait and print a CLOSE OUT for a card already handled. A refusal for a
    missing capability is not one: that worker is live and needs its capability resent. Returns the dispatch id."""
    try:
        p = m.get("payload")
        p = json.loads(p) if isinstance(p, str) else p or {}
        rej = p.get("_orcaLifecycleRejection") or {}
    except (ValueError, AttributeError):
        return None
    return (p.get("dispatchId") or "?") if "revoked" in (rej.get("reason") or "") else None


def payload_of(m):
    payload = m.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def own_worktree_id():
    """The worktree Orca attaches this terminal to, not the cwd: a lead may run `orch wait` from another checkout."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    try:
        wt = (orca_json("terminal", "show", "--terminal", me).get("terminal") or {}).get("worktreeId") if me else None
    except (SystemExit, ValueError, AttributeError, OSError, subprocess.SubprocessError):
        return None
    return wt if isinstance(wt, str) and wt else None


def close_out(msgs, workers, worktrees, here=""):
    """What to do with each worker_done's card, printed beside the message. `here` is the worktree id of the caller's
    own terminal: a worker started with `--worktree current` reports that card, and Orca lists no worker for the caller
    itself, so it is the one card whose terminal the caller must not treat as a finished card's."""
    card = {w["dispatchId"]: (w.get("resource") or {}).get("worktreeId") for w in workers}
    task = {w["dispatchId"]: w.get("taskId") or "<task>" for w in workers}
    path = {w["id"]: w.get("path") for w in worktrees if not w.get("isMainWorktree")}
    out = []
    for m in msgs:
        if m.get("type") != "worker_done":
            continue
        payload = payload_of(m)
        d = payload.get("dispatchId")
        if not d or any(taken_over(w) for w in workers if w["dispatchId"] == d):
            continue
        release = f"  orca orchestration worker-release --dispatch {d}"
        wt = card.get(d)
        p = path.get(wt)
        busy = [w["dispatchId"] for w in workers if w["dispatchId"] != d and w.get("terminalState") == "active"
                and (w.get("resource") or {}).get("worktreeId") == wt]
        if not p:
            out += [f"CLOSE OUT {d}: release it; its card is not in `orca worktree list`", release]
        elif busy:
            out += [f"CLOSE OUT {d}: release it; its card stays, {', '.join(busy)} works on it", release]
        elif here and wt == here:
            out += [f"CLOSE OUT {d}: release it and close its terminal; its card is this session's own worktree"
                    " (`--worktree current`), so it stays", release]
        elif payload.get("outcome") != "succeeded":
            out += [f"CLOSE OUT {d} ({payload.get('outcome') or 'no outcome'}): release it, then retry on its card"
                    f" (worker-start --retry-of {d} --task {task[d]} --worktree path:{shlex.quote(p)} --agent <agent> [--model <id>])",
                    release]
        elif here is None:
            out += [f"CLOSE OUT {d}: release it; this session's own worktree could not be read (`orca terminal show`),"
                    f" so close its agent terminal only once it is not this session's", release]
        elif handed_off(p):
            out += [f"CLOSE OUT {d}: release it and close its agent terminal if still open; HANDED TO A PERSON: "
                    f"`orca worktree rm` of {shlex.quote(p)} was refused, so no cleaner takes it and nothing here runs "
                    f"it again. The person runs: {rm_refused().command(p)}", release]
        else:
            out += [f"CLOSE OUT {d}: release it and close its agent terminal if still open, unless its next task starts"
                    f" there; the cleaners take {shlex.quote(p)} ({CLEANUP})", release]
    return out


def rm_refused():
    """reap-resources' record of cards whose `orca worktree rm` was refused (one predicate for every reader)."""
    scripts = str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import rm_refused as mod
    return mod


def handed_off(path):
    """True when the card's removal is recorded as refused. A record that cannot be read is not (the cleaners keep a
    card then, and say why)."""
    return bool(rm_refused().held(path))


def taken_over(w):
    """Orca's worker-release never closes a terminal someone typed into, an orchestrator's or lead's `terminal send` included."""
    return (w.get("terminalState") == "retained" and (w.get("resource") or {}).get("retainedReason") == "user_takeover"
            and w.get("workerState") in ("succeeded", "failed"))


def gh_pr(path, branch):
    try:
        r = subprocess.run(["gh", "pr", "view", branch, "--json", "number,state,headRefOid"], capture_output=True,
                           text=True, cwd=path, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"state": f"unknown ({type(e).__name__})"}
    if r.returncode:
        if re.search(r"no (pull requests found|git remotes)", r.stderr):
            return None
        return {"state": f"unknown ({r.stderr.strip()[:80]})"}
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"state": "unknown (unreadable gh output)"}


def regenerated_copies(path):
    """True when every untracked file is in an `.agents/skills/<name>/` that Orca regenerates from a tracked
    `.claude/skills/<name>` (reap-resources' rule): such a copy is not work, and `orca worktree rm` goes after trashing it."""
    out = run(["git", "-C", path, "ls-files", "--others", "--exclude-standard", "-z"], check=False)
    names = set()
    for f in filter(None, out.stdout.split("\0")):
        parts = f.split("/")
        if len(parts) < 4 or parts[:2] != [".agents", "skills"]:
            return False
        names.add(parts[2])
    tracked = run(["git", "-C", path, "ls-files", "-z", "--", ".claude/skills", ".agents/skills"], check=False)
    have = {tuple(f.split("/")[:3]) for f in filter(None, tracked.stdout.split("\0"))}
    return out.returncode == 0 and tracked.returncode == 0 and bool(names) and \
        all((".claude", "skills", n) in have and (".agents", "skills", n) not in have for n in names)


def card_holds(path, pr=gh_pr):
    if not os.path.isdir(path):
        return None
    status = run(["git", "-C", path, "status", "--porcelain"], check=False).stdout.strip()
    if status and not (all(line.startswith("?? ") for line in status.splitlines()) and regenerated_copies(path)):
        return "uncommitted changes"
    branch = run(["git", "-C", path, "symbolic-ref", "-q", "--short", "HEAD"], check=False).stdout.strip()
    state = (pr(path, branch) or {}) if branch else {}
    if state.get("state") == "OPEN":
        return f"open PR #{state['number']}"
    if state.get("state", "").startswith("unknown"):
        return f"PR state {state['state']}"
    if state.get("state") == "MERGED":
        r = run(["git", "-C", path, "rev-list", "--count", "HEAD", "--not", str(state.get("headRefOid")), "--remotes"], check=False)
        if r.returncode == 0:
            after = r.stdout.strip()
            return f"{after} commit(s) after its merged PR" if after not in ("", "0") else None
    ahead = run(["git", "-C", path, "rev-list", "--count", "HEAD", "--not", "--remotes"], check=False).stdout.strip()
    return f"{ahead} unpushed commit(s)" if ahead not in ("", "0") else None


SELF_CHECK = ("Self-check: is any work left on this card? If none, run end-session and close yourself. "
              "If some, say what it is and keep it.")


def auto_release_ahead(w, now):
    scripts = str(pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import auto_release
    return auto_release.ahead(w, now)


def settle_sweep(workers, worktrees, skip=(), holds=card_holds, asked=None, now=None, clock=time.time):
    path = {w["id"]: w.get("path") for w in worktrees if not w.get("isMainWorktree")}
    asked = {} if asked is None else asked
    seen, out, kept = {}, [], []
    for w in workers:
        d, res, taken = w.get("dispatchId"), w.get("resource") or {}, taken_over(w)
        if not d or (not taken and (w.get("terminalState") not in ("reclaimable", "release_unknown") or d in skip
                                    or auto_release_ahead(w, clock()))):
            continue
        p = path.get(res.get("worktreeId"))
        if p and p not in seen:
            seen[p] = holds(p)
        why = seen.get(p)
        if why:
            kept.append(f"KEEP {d} ({w.get('workerState')}): {why}")
        elif taken and d in asked:
            out.append(f"TAKEN OVER {d} ({w.get('workerState')}): self-check asked at {asked[d]}; wait for its answer")
        elif taken:
            asked[d] = now or dt.datetime.now().isoformat(timespec="minutes")
            out += [f"TAKEN OVER {d} ({w.get('workerState')}): worker-release refuses it; ask it to check for work left and close itself",
                    f"  orca terminal send --terminal {worker_terminal(w)} --text {shlex.quote(SELF_CHECK)} --enter"]
        else:
            out += [f"SETTLED {d} ({w.get('workerState')}): release it; "
                    + ("no open PR, nothing unpushed" if p else "its card is gone"),
                    f"  orca orchestration worker-release --dispatch {d}"]
    return out + kept


def print_sweep(run_id, state, skip=()):
    try:
        asked = json.loads(state.read_text())
    except (OSError, ValueError):
        asked = {}
    asked = asked if isinstance(asked, dict) else {}
    try:
        workers = run_workers(run_id)
        lines = settle_sweep(workers, orca_json("worktree", "list").get("worktrees", []), skip, asked=asked)
    except (SystemExit, Exception) as e:  # the batch and its ack line matter more than the sweep
        lines, workers = [f"SETTLED: sweep failed ({type(e).__name__}: {e}); worker-list --run {run_id} --terminal-state reclaimable lists them"], None
    if workers is not None:
        live = {w.get("dispatchId") for w in workers if taken_over(w)}
        try:
            fd, tmp = tempfile.mkstemp(dir=state.parent, prefix=".self-check-")
            with os.fdopen(fd, "w") as f:
                json.dump({d: t for d, t in asked.items() if d in live}, f)
            os.replace(tmp, state)
        except OSError as e:
            lines.append(f"SETTLED: could not record the self-checks ({e}); the next wake prints them again")
    if lines:
        print("\n".join(lines), flush=True)


PROMPTS = [(re.compile(r"Enter to select"), "a select menu"),
           (re.compile(r"^\s*❯\s*1\.\s*Yes\b", re.M), "a permission prompt"),
           (re.compile(r"What should Claude do instead\?"), "an interrupted turn"),
           (re.compile(r"ENOSPC|No space left on device"), "a full disk"),
           (re.compile(r"Waiting for API response · will retry"), "an API retry loop")]


# A file diff Claude Code drew: a run of line-number gutter lines (a number, then +, - or a context space) with at
# least one + or -. It quotes code, so an ENOSPC in it is the reviewed text, not the machine. Other output keeps
# counting: a real "npm ERR! ENOSPC" has no gutter, and "28  No space left" alone has no + or - line beside it.
GUTTER = re.compile(r"^\s*\d+(?:\s([+\-])|\s\s)")


DIFF_HEAD = re.compile(r"\b(?:Update|Edit|Write|MultiEdit)\(|\b(?:Added|Removed) \d+ lines?\b|\bUpdated .* with \d+ (?:addition|removal)")


def without_diffs(lines, tail=25):
    """The last `tail` lines minus the drawn diffs: a gutter run with a + or - line that follows a diff header. Shape
    alone is not enough: a command can print "27 - step" and "28  No space left". A run with no header is kept, so a
    real disk error is never dropped. The whole screen is walked so a header is seen even when the tail cut it off,
    but only lines that were in the tail come back. A diff line too long for the terminal wraps onto a line with no
    gutter; that line stays, because no rule for it told it apart from command output right after a diff."""
    out, run, head, in_diff = [], [], False, False
    first = len(lines) - tail
    for i, l in enumerate([*lines, None]):
        m = GUTTER.match(l) if l is not None else None
        if m:
            run.append((i, l, bool(m.group(1))))
            continue
        if run:
            in_diff = head and any(marked for _, _, marked in run)
            if not in_diff:
                out += [(j, x) for j, x, _ in run]
            run = []
        if l is None:
            continue
        out.append((i, l))
        # A wrapped tail has no mark and leaves the header standing for the gutter lines after it.
        if DIFF_HEAD.search(l):
            head = True
        elif l.lstrip().startswith(("⏺", "❯", "⎿", "✻")):
            head = False
    return [l for i, l in out if i >= first]


MIN_FREE_GB = 15  # watch-cards' --min-free-gb default: under it the disk is full enough to stop work


def low_disk(min_free_gb=MIN_FREE_GB, free=lambda: shutil.disk_usage(pathlib.Path.home()).free):
    """True under min_free_gb, False at or over it, None when the disk could not be measured."""
    try:
        return free() / 2**30 < min_free_gb
    except Exception:
        return None


def screen_prompt(lines, activity=None, low_disk=None):
    """low_disk False (measured fine) drops the full-disk reading: the screen only hints at it, and a quoted ENOSPC
    (code, grep output, prose) looks the same as a real one. True or None (unmeasured) keeps the screen's word."""
    tail = lines[-25:]
    text = "\n".join(without_diffs(lines))
    for rx, kind in PROMPTS:
        if kind == "a full disk" and low_disk is False:
            continue
        if rx.search(text):
            return kind, next((l.strip() for l in reversed(tail) if l.strip().endswith("?")), "")
    return ("waiting for input", "") if activity == "waiting" else None


def prompt_block(tail):
    """Twelve lines up to the prompt's last option, or its question: anything below it (the status bar) would change the key every round."""
    q = max((i for i, l in enumerate(tail) if l.strip().endswith("?")), default=None)
    ends = [i for i, l in enumerate(tail) if re.search(r"Enter to select|\(esc\)|Esc to cancel", l) and (q is None or i >= q)]
    end = ends[0] if ends else q
    return [] if end is None else tail[max(0, end - 11):end + 1]


def read_screen(handle):
    return orca_json("terminal", "read", "--terminal", handle, "--screen").get("terminal", {}).get("tail", [])


NOT_LOOKED = object()
# The screen kinds Orca's agentWait does not judge: a worker in them is not parked on a human-only prompt.
NOT_A_WAIT = {"a full disk", "an API retry loop"}


def agent_waits(strict=False):
    """{terminal handle: Orca's agentWait} for every agent terminal, a Run's worker or not, from one
    `worktree ps --agent-wait`: a dict naming the human-only prompt and its evidence, None when Orca looked and found
    no wait. A handle left out (an older host, a row past the page, an unreadable pane) was not looked at. An
    unreadable listing is {}, or raises with strict (a listing without its list counts as unreadable then)."""
    try:
        ps = orca_json("worktree", "ps", "--agent-wait", "--limit", "500").get("worktrees")
        terms = orca_json("terminal", "list").get("terminals")
        if strict and not (isinstance(ps, list) and isinstance(terms, list)):
            raise ValueError("worktree ps or terminal list answered without its list")
        panes = {a["paneKey"]: a["agentWait"]
                 for w in ps or [] for a in w.get("agents") or [] if a.get("paneKey") and "agentWait" in a}
        # An orphaned terminal lists its pty as tabId and leafId; its pane is recordedPaneKey.
        return {t["handle"]: panes[k] for t in terms or []
                if t.get("handle") and (k := t.get("recordedPaneKey") or f"{t.get('tabId')}:{t.get('leafId')}") in panes}
    except (SystemExit, Exception):
        if strict:
            raise
        return {}


def judge_wait(waited, lines, activity=None, low_disk=None):
    """(kind, question) for a Dispatch's terminal, or None. Orca's agentWait first: a wait it found stands even on a
    screen the regexes miss; when it looked and found none, only what it does not judge (disk, API retries) is read
    from the screen; when it never looked, the screen decides as for a card."""
    if isinstance(waited, dict):
        # kind is the dedupe key: only the evidence's source, never a payload that may carry a fresh timestamp
        ev = waited.get("evidence") or waited.get("source") or "orca"
        ev = ev if isinstance(ev, str) else (ev.get("source") or ev.get("kind") or "orca") if isinstance(ev, dict) else "orca"
        return (f"waiting on a prompt (Orca agentWait, {str(ev)[:40]})",
                str(waited.get("prompt") or waited.get("question") or waited.get("text") or "")[:200])
    if lines is None:
        return None
    hit = screen_prompt(lines, None if waited is None else activity, low_disk)
    return None if hit and waited is None and hit[0] not in NOT_A_WAIT else hit


def sweeps_screen(w):
    """A worker whose agent may still be at work: active, or retained (Orca keeps the terminal of a card someone typed
    into, a `terminal send` nudge included) and not yet settled. Reclaimable, released and release_unknown rows are
    done; so is a retained row that succeeded or failed. A retained row with no workerState is read, not skipped."""
    state = w.get("terminalState")
    return state == "active" or (state == "retained" and w.get("workerState") not in ("succeeded", "failed"))


def stuck_sweep(workers, slug, seen, read=read_screen, only_new=False, waits=None, low_disk=None):
    """A worker's terminal has no human watching it: a menu or permission prompt there blocked a program for hours.
    Orca's agentWait decides first; the screen is read for what it does not cover (disk, API retries), and for every
    kind when Orca never looked."""
    out, now = [], {}
    waits = agent_waits() if waits is None else waits
    for w in workers:
        d, h = w.get("dispatchId"), worker_terminal(w)
        if not sweeps_screen(w) or not d or not h:
            continue
        waited = waits.get(h, NOT_LOOKED)
        lines = None
        # A wait Orca found decides by itself (judge_wait); None or never looked still needs the screen.
        if not isinstance(waited, dict):
            try:
                lines = read(h)
            except (SystemExit, Exception):
                lines = None
        hit = judge_wait(waited, lines, ((w.get("projection") or {}).get("stage") or {}).get("activity"), low_disk)
        if not hit:
            continue
        kind, question = hit
        key = hashlib.sha1(("\n".join(prompt_block((lines or [])[-25:])) or kind + question).encode()).hexdigest()[:12]
        now[d] = key
        if only_new and seen.get(d) == key:
            continue
        out += [f"STUCK {d} ({kind}): " + (f'"{question}"' if question else "read its screen")
                + "; answer it from the program's decisions, or put it to the human now if none covers it, and record it",
                f"  orca terminal read --terminal {h} --screen",
                f"  orch record {slug} signal --kind stall --evidence {d} --note \"{kind}: answered <what> | escalated\""]
    seen.clear()
    seen.update(now)
    return out


def print_stuck(p, run_id, only_new=False):
    state = p.dir / "stuck.json"
    try:
        seen = json.loads(state.read_text())
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    try:
        lines = stuck_sweep(run_workers(run_id), p.slug, seen, read=read_screen, only_new=only_new,
                            low_disk=low_disk())
    except (SystemExit, Exception) as e:  # the batch and its ack line matter more than the sweep
        lines, seen = ([] if only_new else [f"STUCK: screen sweep failed ({type(e).__name__}: {e})"]), None
    if seen is not None:
        try:
            fd, tmp = tempfile.mkstemp(dir=state.parent, prefix=".stuck-")
            with os.fdopen(fd, "w") as f:
                json.dump(seen, f)
            os.replace(tmp, state)
        except OSError:
            pass
    if lines:
        print("\n".join(lines), flush=True)
    return lines


HOLD = re.compile(r"\bhold\b|do.?not.?merge|\bwip\b", re.I)


def held_pr(events, entry):
    return entry.get("pr") is not None and on_hold(events, entry["pr"])


def on_hold(events, pr):
    """`record parked --pr N` holds a PR until `record admitted --pr N`; the later of the two wins."""
    marks = [e["ev"] for e in events if e.get("pr") == pr and e["ev"] in ("parked", "admitted")]
    return bool(marks) and marks[-1] == "parked"


def review_sweep(events, view, active, unresolved, slug, gate=None, unknown=frozenset(), no_ci=False):
    """A review that outlasts its worker's session leaves a ready PR nobody lands. unknown: tickets whose worker row
    could not be judged ("*" for a row tied to no ticket); their PRs, or every PR with "*", are never offered to land."""
    ticket = pr_ticket(events)
    out, waiting = [], []
    for st in ready_prs(events):
        n = st["pr"]
        t = ticket.get(n)
        if t in active:
            continue
        name = f"#{n}" + (f" ({t})" if t else "")
        r = view(n)
        if r.get("state") == "MERGED":
            out += [f"MERGED OUTSIDE LAND {name}: record it and finish its ticket as for a LAND", f"  orch landed {slug} --pr {n}"]
            continue
        if r.get("state") != "OPEN":
            waiting.append(f"{name}: {(r.get('state') or 'unknown').lower()} without merging; ask its owner or drop its ticket")
            continue
        failed, pending, _ = ci_summary(r.get("statusCheckRollup"), no_ci)
        mss = r.get("mergeStateStatus")
        verdict = (st.get("verdict") or {}).get("result")
        decision = r.get("reviewDecision")
        why = [w for w, bad in (
            ("worker state unknown", "*" in unknown or t in unknown),
            ("no ticket, so no worker is ruled out", not t),
            ("no passing verdict", verdict != "pass"),
            ("draft", r.get("isDraft")),
            ("on hold", on_hold(events, n) or any(HOLD.search(l.get("name") or "") for l in r.get("labels") or [])),
            # A declared no-CI repo reviews through the ledger verdict: no GitHub decision is not a wait there.
            ((decision or "no review").lower().replace("_", " "), decision != "APPROVED" and not (no_ci and not decision)),
            (f"merge state {mss}" + {"DIRTY": ", dispatch a fix", "BEHIND": f", gh pr update-branch {n} (the land executor and a no-CI repo then need a verdict at the new head)"}.get(mss, ""), mss != "CLEAN"),
            ("CI failed, dispatch a fix", failed), ("CI pending", pending)) if bad]
        threads = 0 if why else unresolved(n)
        if threads:
            why.append(f"{threads} unresolved thread(s)")
        if not why and gate and "approved" not in st:
            try:  # one unreadable gate must not cost the other PRs their lines
                gw = gate_wait(st, gate, slug, n)
            except (SystemExit, Exception) as e:
                gw = f"human gate unreadable ({type(e).__name__}: {e})"
            why += [gw] if gw else []
        if why:
            waiting.append(f"{name}: {', '.join(why)}")
        else:
            out += [f"LAND {name}: approved, clean and green at {(r.get('headRefOid') or '')[:8]}, and no worker holds it",
                    f"  orch land {slug} --pr {n}"]
    return out + (["WAITING ON REVIEW " + "; ".join(waiting)] if waiting else [])


def gate_wait(st, gate, slug, n):
    """Why the human gate still holds this PR, or None once the user resolved it as land."""
    if "gate_opened" not in st:
        return f"human gate not opened (orch gate {slug} --pr {n})"
    res = gate(n)
    return None if res == "land" else f"human gate resolved as '{res}'" if res else "human gate open, waiting for the user"


def unresolved_threads(repo, n):
    owner, name = repo.split("/")
    q = ("query($o:String!,$r:String!,$n:Int!,$after:String){repository(owner:$o,name:$r){pullRequest(number:$n)"
         "{reviewThreads(first:100,after:$after){nodes{isResolved} pageInfo{hasNextPage endCursor}}}}}")
    count, after = 0, []
    while True:
        res = gh_json("api", "graphql", "-f", f"query={q}", "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={n}", *after)
        threads = res["data"]["repository"]["pullRequest"]["reviewThreads"]
        count += sum(not t["isResolved"] for t in threads["nodes"])
        if not threads["pageInfo"]["hasNextPage"]:
            return count
        after = ["-f", f"after={threads['pageInfo']['endCursor']}"]


def print_reviews(p):
    if lines := review_lines(p):
        print("\n".join(lines), flush=True)


def review_lines(p, workers=None, tasks=None, unknown=frozenset()):
    """review_sweep for the program; unknown as in review_sweep. A failure is one line, never the caller's other lines."""
    try:
        events = p.events()
        if not ready_prs(events):
            return []
        workers = run_workers(p.cfg["run"]) if workers is None else workers
        tasks = run_tasks(p.cfg["run"]) if tasks is None else tasks
        spawned = {d: e["ticket"] for e in with_orca_spawns(events, workers, tasks)
                   if e["ev"] == "spawned" and e.get("ticket")
                   for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or ""))}
        # A Task without created_at gets no spawned line from with_orca_spawns; its ticket still names the holder.
        by_task = dispatch_tickets(workers, tasks)
        active = {spawned.get(w.get("dispatchId")) or by_task.get(w.get("dispatchId"))
                  for w in workers if w.get("terminalState") == "active"}
        repo = p.cfg["repo"]
        fields = "state,reviewDecision,mergeStateStatus,isDraft,labels,statusCheckRollup,headRefOid"
        lines = review_sweep(events, lambda n: gh_json("pr", "view", str(n), "--repo", repo, "--json", fields),
                             active - {None}, lambda n: unresolved_threads(repo, n), p.slug,
                             (lambda n: gate_resolution(p.cfg, events, n)) if p.cfg.get("merge_policy") == "human-gate" else None,
                             unknown, no_ci_repo(repo))
    except (SystemExit, Exception) as e:  # the batch and its ack line matter more than the sweep
        lines = [f"REVIEWS: sweep failed ({type(e).__name__}: {e}); orch status lists the ready PRs"]
    return lines


WAKE_DUE_S = 1800  # sync.py's WAKE_PENDING_S


class PendingError(SystemExit):
    """The wait's own request file failed, not Orca: never mistaken for a lost answer."""


def pending_request(pending):
    """The consuming request a dead wait left in `pending`: {"retry_request", "ack", "shown"}, or None when there is
    none. Any other read failure stops the wait: guessing "none" would lose a batch or print it twice."""
    if pending is None:
        return None
    try:
        req = json.loads(pending.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise PendingError(f"wait request {pending} is unreadable ({e}); check it, then remove it to go on")
    if not isinstance(req, dict) or not req.get("retry_request"):
        raise PendingError(f"wait request {pending} holds no retry_request; check it, then remove it to go on")
    return req


def write_request(pending, req):
    try:
        pending.parent.mkdir(parents=True, exist_ok=True)
        tmp = pending.with_suffix(".tmp")
        tmp.write_text(json.dumps(req))
        os.replace(tmp, pending)
    except OSError as e:
        raise PendingError(f"wait request {pending} could not be written ({e})")


def next_ack(pending, box, did):
    """Swap the printed batch's request for the ack of it in one replace, carrying its marks: until the ack request
    is on disk, the printed batch's request (and what it printed) stays."""
    import uuid
    if pending is not None:
        old = pending_request(pending) or {}
        write_request(pending, {"retry_request": str(uuid.uuid4()), "ack": did, "shown": old.get("shown") or [],
                                "box": list(box)})


def take(box, pending=None, ack=None):
    """The owner's consuming check (with `ack`, the ack of that delivery, which an older Orca answers with the next
    batch), under a retry id chosen before it: an answer lost on the way is asked for again with the same id, and Orca
    replays the same batch instead of handing out the next one. With `pending`, the request is written there first, so
    a wait that died with the answer lost twice replays it on its next start; the caller clears it (`done_take`) once
    the batch is printed."""
    import uuid
    req = pending_request(pending)
    if req is None:
        req = {"retry_request": str(uuid.uuid4()), "ack": ack}
        if pending is not None:
            write_request(pending, {**req, "box": list(box)})
    args = (*box, *(("--ack", req["ack"]) if req.get("ack") else ()), "--retry-request", req["retry_request"])
    try:
        return batch(check(*args))
    except SystemExit:
        return batch(check(*args))


def mark_shown(pending, mid):
    """Note a message of the pending batch as printed, so a replay after a crash does not print it again. Written
    after the print: a crash between the two prints it twice rather than never. A failed write raises, so nothing
    more is printed past the one message it could not note."""
    req = pending_request(pending) if mid is not None else None
    if req is None:
        return
    write_request(pending, {**req, "shown": [*(req.get("shown") or []), mid]})


def done_take(pending):
    if pending is not None:
        try:
            pending.unlink(missing_ok=True)
        except OSError as e:  # a stale request left behind would be replayed in place of the next take
            raise PendingError(f"wait request {pending} could not be removed ({e}); remove it to go on")


def wait_take(box, timeout_ms, clock=time.monotonic, out=print, pending=None, seen=None):
    """One lead wait: ([delivery ids], [messages], {ids already printed}, actionable). It returns once an ACTIONABLE
    message is taken (actionable True; that last batch is neither printed nor acked here, and `pending` stays for the
    caller to clear after printing it), or at the timeout.

    The wait is a peek with no --types: a typed wait leaves Orca's "You have N orchestration message" nudge on, which
    wakes the lead a second time for the same mail. An untyped peek wakes on any unread row, so the owner takes each
    batch it wakes for; status mail is printed at once (a killed wait leaves it in the output file), heartbeats are
    only acked, and the wait goes on for the time left. Each such batch is acked for an older Orca, whose ack may hand
    out the next batch: that one is handled like a take, and the ack goes under a retry id like a take. `seen` holds
    the deliveries handled in earlier rounds, so one handed out again is not printed twice."""
    end = clock() + int(timeout_ms) / 1000
    dids, msgs, shown = [], [], set()
    seen = set() if seen is None else seen
    replay = pending_request(pending) is not None  # a request a dead wait left: replay it before waiting
    ack_next = None
    while True:
        if ack_next is not None:
            try:
                did, got = take(box, pending, ack=ack_next)
            except PendingError:
                raise
            except SystemExit:  # both answers lost: the ack stays in `pending`, and the next wait replays it
                break
            ack_next = None
            if not did and not got:
                done_take(pending)
                if clock() >= end:
                    break
                continue
        else:
            if not replay:
                left = int((end - clock()) * 1000)
                wait = ["--wait", "--timeout-ms", str(max(left, 1000))] if left > 0 else []
                _, unread = batch(check(*box, "--peek", *wait))
                if not unread:
                    break
            replayed, replay = replay, False
            replayed_ack = replayed and bool((pending_request(pending) or {}).get("ack"))
            try:
                did, got = take(box, pending)
            except SystemExit as e:
                if not replayed_ack or isinstance(e, PendingError):
                    raise
                if '"code"' not in str(e):  # no answer from Orca: the ack may still hold a batch, keep it for later
                    out(f"wait request replay failed ({e}); kept in {pending} for the next wait", flush=True)
                    break
                out(f"wait request dropped: Orca refused the replayed ack ({e})", flush=True)
                done_take(pending)
                continue
            if not did and not got:
                done_take(pending)
                if replayed_ack:  # the replayed ack had no next batch: go on to the wait
                    continue
                break  # rows peeked but none handed out: waiting again would spin on them
        if did and (did in dids or did in seen):  # a batch already handled: Orca is not moving, stop, not spin
            done_take(pending)
            break
        if did:
            dids.append(did)
        msgs += got
        printed = set((pending_request(pending) or {}).get("shown") or ())  # a dead wait printed these already
        shown |= {m.get("id") for m in got} & printed
        if any(m.get("type") in ACTIONABLE and not revoked_echo(m) for m in got):
            return dids, msgs, shown, True
        for m in got:
            if m.get("type") != "heartbeat" and not revoked_echo(m) and (m.get("id") is None or m.get("id") not in shown):
                out(inbox_line(m), flush=True)
                shown.add(m.get("id"))
                mark_shown(pending, m.get("id"))
        if did:
            next_ack(pending, box, did)
            seen.add(did)
            ack_next = did
        else:
            done_take(pending)
            if clock() >= end:
                break
    return dids, msgs, shown, False


def cmd_wait(argv):
    p = Program(argv[0])
    timeout = opt(argv, "--timeout-ms", "540000")
    rounds = int(opt(argv, "--rounds", "3"))
    run_id = p.cfg["run"]
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    # A lead started with worker-start is a worker of the Run: its units report to its Dispatch mailbox, and the
    # Run inbox is its coordinator's, which it must never take. Any other worker of the Run keeps no standing wait.
    lead = p.cfg.get("lead_dispatch")
    if lead:
        row = next((w for w in all_workers(run_id) if w.get("dispatchId") == lead), None)
        if not row or not me or worker_terminal(row) != me:
            sys.exit(f"orch wait reads the lead's own mailbox, and this terminal is not lead dispatch {lead}"
                     f" ({'not listed on ' + run_id if not row else 'its terminal is ' + str(worker_terminal(row))})."
                     " The Run's inbox is its coordinator's: never wait on it from here.")
        box = ["--terminal", me]
    elif me and own_worker(all_workers(run_id), me):
        sys.exit("orch wait reads and acks the lead's inbox, and this terminal is one of the Run's other"
                 " workers. Wake on your own mailbox (check --terminal $ORCA_TERMINAL_HANDLE --wait) or a"
                 " background command such as `gh run watch <id>`.")
    else:
        box = ["--run", run_id]
    # STALLED comes from the collector; a lead that only loops `wait` would otherwise keep an old server.
    if (line := ensure_dash()) and not line.startswith("dashboard: http"):
        print(line, flush=True)
    if watched := watch_lines(me, False):
        print("\n".join(watched), flush=True)
    import autowake
    auto_min = autowake.minutes()

    def auto_round():
        try:
            r = autowake.sweep(p, auto_min) if auto_min else autowake.Round()
        except (SystemExit, Exception) as e:  # the batch and its ack line matter more than the sweep
            r = autowake.Round(lines=[f"auto-wake failed ({type(e).__name__}: {e}); STALLED lines stay the lead's"])
        if r.lines:
            print("\n".join(r.lines), flush=True)
        return r

    pending, seen = p.dir / "wait-request.json", set()
    for i in range(rounds):
        if wakes := wake_lines(me):  # before the wait: a busy inbox returns early and must not hide these
            print("\n".join(wakes), flush=True)
        auto = auto_round()  # here too: busy mail returns before the empty branch below and would starve a wake
        if auto.stop:
            return
        dids, msgs, shown, actionable = wait_take(box, auto.timeout_ms(timeout), pending=pending, seen=seen)
        did = dids[-1] if dids else None
        work = [m for m in msgs if m.get("type") != "heartbeat" and not revoked_echo(m)]
        for m in msgs:
            if revoked_echo(m):
                print(f"ignored: Orca refused a {m.get('type')} from released dispatch {revoked_echo(m)}", flush=True)
        if actionable:
            for m in work:
                if m.get("id") is None or m.get("id") not in shown:
                    print(inbox_line(m), flush=True)
                    mark_shown(pending, m.get("id"))
            done_take(pending)
            closed = {payload_of(m).get("dispatchId") for m in work if m.get("type") == "worker_done"}
            if closed:
                try:
                    lines = close_out(work, run_workers(run_id), orca_json("worktree", "list").get("worktrees", []),
                                      here=own_worktree_id())
                except SystemExit as e:  # the batch and its ack line matter more than the hint
                    lines = [f"CLOSE OUT: could not read the cards ({e}); `orch status` lists them"]
                print("\n".join(lines))
            print_sweep(run_id, p.dir / "self-check.json", closed)
            print_stuck(p, run_id)
            print_reviews(p)
            print(f"ACTIONABLE delivery={did} ({len(work)} of {len(msgs)}): process every message, then "
                  f"orca orchestration check {' '.join(box)} --ack {did} --json")
            return
        # Every batch of this round was acked inside wait_take; status mail in it is printed above.
        statuses = f", {len(shown)} status message(s) above" if shown else ""
        print(f"wait {i + 1}/{rounds}: nothing actionable ({len(msgs) - len(shown)} heartbeat(s) or refusals acked"
              f"{statuses})", flush=True)
        if print_stuck(p, run_id, only_new=True):
            print_sweep(run_id, p.dir / "self-check.json")
            print("STUCK: a worker waits on a prompt nobody sees; answer or escalate it now", flush=True)
            return
        if leftovers := new_leftovers(p.dir):
            print("\n".join(leftovers), flush=True)
        auto = auto_round()
        if auto.stop:
            return
        if stalled := new_stalls(p.dir, skip=auto.skip):
            print("\n".join(stalled), flush=True)
            if not auto_min:
                print(autowake.OFF_LINE, flush=True)
            print("STALLED: a worker stopped with its ticket open and nothing will wake it; wake it now", flush=True)
            return
        if watched := watch_lines(me, False):  # each shows at most once per 30 minutes, so this ends no wait twice
            print("\n".join(watched), flush=True)
            print("PATROL: act on the lines above, then wait again", flush=True)
            return
    print_sweep(run_id, p.dir / "self-check.json")
    print_stuck(p, run_id)
    print_reviews(p)
    moves = next_moves(run_workers(run_id), auto.skip)
    print("\n".join([f"EMPTY x{rounds}: " + (f"{len(moves)} live worker(s), each with its move:" if moves else "no live worker")]
                    + moves))


def wake_lines(me, now=None):
    """Signals this session sent that stayed unconfirmed past `skills-sync wake-target`'s deadline. The background
    recheck dies with a restart, so the wait keeps the deadline. A record sync's wake_entry would refuse, or one with no
    sender, belongs to nobody it can name and shows to every session."""
    root = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
    now = time.time() if now is None else now
    out = []
    for f in sorted((root / "wakes").glob("*.json")):
        try:
            entry = json.loads(f.read_text())
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            entry = None
        valid = isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("session", "to", "nonce")) \
            and isinstance(entry.get("at"), (int, float)) and isinstance(entry.get("offset"), int)
        if valid and (now - entry["at"] < WAKE_DUE_S or entry.get("from") and entry["from"] != me):
            continue
        out.append(f"WAKE {f.stem}: unconfirmed past its deadline; skills-sync wake-check {f.stem}, and without "
                   "`started` follow references/waking.md (inbox item, then wake-reset once the human has answered)")
    return out


def watch_lines(me, unowned):
    """What the session's watchers left for it: its `orch watch-cards` gone quiet, and unacked `orch patrol` findings
    routed to it. Each repeats every 30 minutes until dealt with."""
    import card_watch, patrol
    out = []
    with contextlib.suppress(OSError, ValueError):
        if line := card_watch.down_line(me):
            out.append(line)
    with contextlib.suppress(OSError, ValueError, TypeError):
        out += patrol.pending_lines(me, unowned)
    return out


def inbox_line(m):
    # Whole: a body cut short dropped the options of a decision and the evidence of a worker_done.
    body = (m.get("body") or "").replace("\n", "\n    ")
    thread = f" thread={m['thread_id']}" if m.get("thread_id") else ""
    payload = m.get("payload")
    if isinstance(payload, str):  # Orca often stores it as a JSON string; print it once, not escaped
        try:
            payload = json.loads(payload)
        except ValueError:
            pass
    return (f"INBOX id={m.get('id')} type={m.get('type')} from={m.get('from_handle')}{thread} "
            f"subject={m.get('subject')} payload={json.dumps(payload, ensure_ascii=False)} body={body}")


def cmd_inbox(argv):
    """The inbox wait of a session without a program in one call per wake: the raw peek, take and ack replies are
    whole JSON batches, and a top-level session read each of them in full."""
    if "--stream" in argv:
        import inbox_stream
        return inbox_stream.main(argv)
    acked = opt(argv, "--ack")
    try:
        did, msgs = batch(check(*(["--ack", acked] if acked else [])))
    except SystemExit as e:  # a stale delivery id must not cost the wait the caller is about to rely on
        if not acked:
            raise
        print(f"ack {acked} failed ({e}); taking the inbox without it", flush=True)
        did, msgs = batch(check())
    deadline, skipped = time.monotonic() + int(opt(argv, "--timeout-ms", "3300000")) / 1000, 0
    work = [m for m in msgs if m.get("type") != "heartbeat" and not revoked_echo(m)]
    # Like orch wait: a batch of only heartbeats or refusals is acked and the wait goes on, until the deadline.
    while "--wait" in argv and not work:
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0:
            break
        if did:
            skipped += len(msgs)
            did, msgs = batch(check("--ack", did))
        else:
            check("--peek", "--wait", "--timeout-ms", str(left))
            did, msgs = batch(check())
            if not msgs:
                break
        work = [m for m in msgs if m.get("type") != "heartbeat" and not revoked_echo(m)]
    if work:
        print("\n".join(map(inbox_line, work)))
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    # Findings routed to nobody are the orchestrator's, the session that runs the watch over cards outside a Run.
    if watched := watch_lines(me, bool(me and __import__("card_watch").last_scan(me))):
        print("\n".join(watched))
    if did:
        print(f"{len(work)} of {len(msgs)} to act on (the rest heartbeats or refusals); then: orch inbox --ack {did} --wait")
    else:
        print("inbox empty" + (f" ({skipped} heartbeat(s) or refusals acked)" if skipped else "") + "; wait again: orch inbox --wait")


def next_moves(workers, auto=frozenset()):
    """The move for each live worker after a wait that stayed empty, read from Orca's projection here so the
    lead does not list the workers and work it out again. auto: dispatches auto-wake holds."""
    out = []
    for w in workers:
        pj = w.get("projection") or {}
        if pj.get("outcome") != "in_progress":
            continue
        d, h, act = w["dispatchId"], worker_terminal(w), pj.get("nextAction") or {}
        activity = (pj.get("stage") or {}).get("activity") or "unknown"
        if act.get("kind") not in (None, "none"):
            out.append(f"  {d} {act['kind']}: {' '.join(act.get('argv') or [])}")
        elif not h or (pj.get("liveness") or {}).get("verdict") == "exited":
            out.append(f"  {d} no terminal: worker-stop or worker-abandon (recovery-and-cleanup.md)")
        elif activity in ("idle", "done") and d in auto:
            out.append(f"  {d} {activity}: auto-wake holds it (orch wait)")
        elif activity == "idle":  # the turn ended without worker_done; send alone does not wake it (SKILL.md, terminal send)
            out.append(f"  {d} idle: orchestration send --to dispatch:{d}, then skills-sync wake-target {h}")
        elif activity == "working":
            out.append(f"  {d} working")
        else:  # waiting may be a permission prompt; unknown needs a look either way
            out.append(f"  {d} {activity}: orca orchestration worker-read --dispatch {d} --source terminal")
    return out


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


REAP = pathlib.Path(__file__).resolve().parents[2] / "reap-resources" / "scripts" / "reap.py"


def compose_projects():
    """{name: [config files]} of every compose project here, stopped ones included; None when docker cannot say."""
    try:
        r = subprocess.run(["docker", "compose", "ls", "-a", "--format", "json"], capture_output=True, text=True, timeout=30)
        return {p["Name"]: [f for f in (p.get("ConfigFiles") or "").split(",") if f] for p in json.loads(r.stdout or "[]")} \
            if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def register_stacks(before, owner, pr, skip=()):
    """A stack left running after verification held gigabytes until someone downed it by hand: the ones this run
    created go to the reap ledger. Only a project with a config file in this worktree: the other slot's card may
    have started one meanwhile."""
    after = compose_projects()
    if before is None or after is None:
        return []
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    wt = os.path.realpath(top.stdout.strip() if top.returncode == 0 else os.getcwd())
    done = []
    for name in sorted(set(after) - set(before) - set(skip)):
        if not any(os.path.realpath(f).startswith(wt + os.sep) for f in after[name]):
            print(f"heavy: compose 스택 {name} 은 이 worktree 밖에서 떠서 등록하지 않음", file=sys.stderr)
            continue
        r = subprocess.run([sys.executable, str(REAP), "ledger", "add", "--kind", "compose-stack", "--id", name,
                            "--worktree", wt, "--pid", str(os.getpid()), "--by", "orch heavy",
                            *(["--run", owner] if owner != "-" else []), *(["--pr", pr] if pr else [])],
                           capture_output=True, text=True)
        print(f"heavy: compose 스택 {name} 을 reap ledger 에 등록했다. 검증이 끝나면 `docker compose -p {name} down`(-v 금지)"
              if r.returncode == 0 else f"heavy: compose 스택 {name} 등록 실패: {r.stderr.strip()}", file=sys.stderr)
        if r.returncode == 0:
            done.append(name)
    return done


def in_slot():
    """Whether this process runs inside a slot still held: ORCH_HEAVY_SLOT names a slot file whose holder is alive
    and one of this process's ancestors. A background child the slot's command left behind inherits the variable
    after the slot is freed or taken by another run."""
    path = os.environ.get("ORCH_HEAVY_SLOT")
    try:
        holder = int(json.loads(pathlib.Path(path).read_text() or "{}").get("pid", 0)) if path else 0
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    pid = os.getppid()
    while holder and pid > 1:
        if pid == holder:
            return True
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
        try:
            pid = int(r.stdout.strip())
        except ValueError:
            return False
    return False


def heavy_run(cmd, env, owner, pr, nested=False):
    """Run cmd and register the compose stacks it started; its exit code. A nested `orch heavy` lists what it
    registered beside the slot so the outer run, which sees the same new stacks, keeps the inner owner and PR."""
    log = pathlib.Path(env["ORCH_HEAVY_SLOT"] + ".stacks")
    before = compose_projects()
    try:
        return subprocess.run(cmd, env=env).returncode
    finally:
        try:
            inner = log.read_text().split() if log.exists() else []
            names = register_stacks(before, owner, pr, skip=inner)
            if nested:  # every level skips what a deeper one registered; only the outermost removes the list
                with log.open("a") as f:
                    f.writelines(f"{n}\n" for n in names)
            else:
                log.unlink(missing_ok=True)
        except (OSError, subprocess.SubprocessError) as e:
            # The command's exit code is the result; a failed registration is only reported.
            print(f"heavy: compose 스택 등록 실패: {e}", file=sys.stderr)


def cmd_heavy(argv):
    """Docker stacks from parallel cards starved the whole machine (Orca UI included), so heavy local work
    queues on slots shared by every program on this machine, like CI shares its runners."""
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        sys.exit("heavy <slug|-> [--wait-minutes 60] [--pr owner/repo#N] -- <command…>")
    cut = argv.index("--")
    cmd = argv[cut + 1:]
    cfg, owner = ({}, "-") if argv[0] == "-" else (Program(argv[0]).cfg, argv[0])
    pr = opt(argv[:cut], "--pr")
    if in_slot():  # already inside a slot: taking a second would wait on itself
        sys.exit(heavy_run(cmd, os.environ, owner, pr, nested=True))
    slots = HOME / "_heavy"
    slots.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 60 * float(opt(argv[:cut], "--wait-minutes", "60"))
    waited = False
    while True:
        # Read each pass so a raised or lowered limit reaches a waiting caller. Slot files past a lowered limit are
        # left alone: their holders run to the end, and only new runs follow the smaller count.
        limit = heavy_slots(cfg)
        for i in range(limit):
            path = slots / f"slot-{i}"
            try:
                held = json.loads(path.read_text() or "{}")
            except (OSError, ValueError):
                held = {}
            if isinstance(held, dict) and held and not pid_alive(held.get("pid", 0)):
                path.unlink(missing_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            os.write(fd, json.dumps({"pid": os.getpid(), "program": owner, "cmd": " ".join(cmd)[:200], "ts": now()}).encode())
            os.close(fd)
            pathlib.Path(f"{path}.stacks").unlink(missing_ok=True)  # left by a run that was killed in this slot
            try:
                sys.exit(heavy_run(cmd, {**os.environ, "ORCH_HEAVY_SLOT": str(path)}, owner, pr))
            finally:
                path.unlink(missing_ok=True)
        holders, freed = [], False
        for f in sorted(slots.glob("slot-*")):
            if not re.fullmatch(r"slot-\d+", f.name):  # `slot-0.stacks` lists a nested run's stacks
                continue
            try:
                held = json.loads(f.read_text() or "{}")
                holders.append(held if isinstance(held, dict) else {"slot": f.name, "unreadable": True})
            except FileNotFoundError:  # its holder finished while this read: take the slot now
                freed = True
            except ValueError:
                holders.append({"slot": f.name, "unreadable": True})
        if freed:
            continue
        if time.monotonic() >= deadline:
            sys.exit(f"heavy: every slot busy after waiting: {json.dumps(holders, ensure_ascii=False)}")
        if not waited:
            print(f"heavy: 슬롯 {limit}개가 모두 사용 중이라 빌 때까지 기다린다(최대 {opt(argv[:cut], '--wait-minutes', '60')}분): "
                  + "; ".join(f"pid {h.get('pid')} {h.get('cmd', '')[:80]}" for h in holders), file=sys.stderr, flush=True)
            waited = True
        time.sleep(15)


def cmd_decide(argv):
    import fleet, store  # the decision table lives in the fleet state directory, beside the page that shows it
    sub, rest = argv[0], argv[1:]
    try:
        if sub == "add" and opt(rest, "--title"):
            rec, body = opt(rest, "--recommend"), opt(rest, "--body-file")
            if rec is not None and not rec.isdigit():
                sys.exit("--recommend is an option number")
            import board
            options = [rest[i + 1] for i, a in enumerate(rest[:-1]) if a == "--option"]
            # Both are made one line and cut as decision_add stores a label, so the gate line checked is the one stored.
            label, _, cost = options[int(rec) - 1].partition("::") if rec and 1 <= int(rec) <= len(options) else ("", "", "")
            label = fleet._one_line(label, 120)
            default, reason = opt(rest, "--if-no-answer"), opt(rest, "--reason")
            default = fleet._one_line(default, 120) if default else default
            if not label:
                why = "--recommend N, one of the --option numbers, is missing"
            elif not cost.strip():
                why = f"the recommended option needs its reason: --option '{label}::<why>'"
            else:
                why = board.ask_lint(default, reason, label)
            if why:
                sys.exit(f"decide add: {why}. It needs --option 'label::why' --recommend N --if-no-answer "
                         f"<that label | {board.CANNOT_PROCEED}> --reason <{' | '.join(board.ASK_REASONS)}> ({board.ASK_GATE})")
            # The decision table has no column for these. The body's first line carries them: the page shows that line,
            # the report appends it, and a long body loses its end, not them.
            text = (pathlib.Path(body).read_text() if body else "").strip()
            text = board.ask_line(default, reason) + (f"\n\n{text}" if text else "")
            order = opt(rest, "--order")
            if order:  # before the decision exists: a mistyped oN adds nothing
                store.order_get(order, require_open=True)
            d = fleet.decision_add(opt(rest, "--title"), text, options,
                                   int(rec) if rec else None, opt(rest, "--link"), os.environ.get("ORCA_TERMINAL_HANDLE"))
            print(d["id"])
            if order:
                try:
                    store.order_link(order, f"decision:{d['id']}", os.environ.get("ORCA_TERMINAL_HANDLE"))
                    print(f"{order} linked decision:{d['id']}")
                except (ValueError, store.NotMigrated) as e:
                    sys.exit(f"{d['id']} was added but not linked to {order} ({e}); link it with "
                             f"orch order link {order} --unit decision:{d['id']}")
        elif sub == "list":
            for d in fleet.open_decisions():
                print(f"{d['id']}  {d['created_at']}  {d['title']}")
                for i, o in enumerate(d["options"], 1):
                    mark = " (recommended)" if i == d.get("recommend") else ""
                    print(f"    {i}. {o['label']}{mark}" + (f" — {o['description']}" if o["description"] else ""))
            for d in fleet.pending_relays():
                print(f"{d['id']}  answered on the dashboard, not relayed yet: {d.get('answer')}  ({d['title']})")
        elif sub == "answer":
            did = rest[0] if rest and not rest[0].startswith("--") else None
            title, quote_file = opt(rest, "--title"), opt(rest, "--quote-file")
            quote = pathlib.Path(quote_file).read_text() if quote_file else opt(rest, "--quote")
            if quote is None or (did is None) == (title is None):
                sys.exit("usage: orch decide answer <id> | --title T, with --quote TEXT | --quote-file F [--by SESSION]")
            d = fleet.decision_quote(did, quote, opt(rest, "--by") or os.environ.get("ORCA_TERMINAL_HANDLE"), title)
            d = store.decision_record(d["id"])
            if d.get("answer") != fleet.mask(quote):
                print(f"{d['id']} keeps the answer it already had; this quote was not stored", file=sys.stderr)
            print(fleet.decision_cite(d))
        elif sub == "show" and rest:
            d = store.decision_record(rest[0])
            if not d:
                sys.exit(f"no decision {rest[0]}")
            print(fleet.decision_cite(d) if d["status"] == "done" else f"{d['id']} {d['status']}")
            print(f"  {d['title']}")
            if "\n" in (d.get("answer") or ""):
                print(d["answer"])
        elif sub in ("done", "drop") and rest:
            d = fleet.decision_close(rest[0], "done" if sub == "done" else "dropped", opt(rest, "--answer"),
                                     by=os.environ.get("ORCA_TERMINAL_HANDLE"))
            print(f"{d['id']} {d['status']}")
        elif sub == "keep" and rest:
            d = fleet.decision_keep(rest[0])
            print(f"{d['id']} checked {d['checked_at']}")
        else:
            sys.exit("usage: orch decide add --title T [--body-file F] --option 'label::description'... --recommend N "
                     "[--link URL] [--order oN] --if-no-answer LABEL|'진행 못 함' --reason TAG | list | show <id> | answer <id>|--title T --quote TEXT|--quote-file F [--by SESSION] | "
                     "done <id> [--answer TEXT] | drop <id> | keep <id>")
    except store.NotMigrated as e:
        sys.exit(f"decisions are not in {store.DB_NAME} yet ({e})")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


def cmd_model(argv):
    import orca_settings
    if argv not in (["lead"], ["worker"]):
        sys.exit("usage: orch model lead|worker")
    print(orca_settings.role_model(argv[0]) or "")


def cmd_db(argv):
    import store
    store.main(argv)


def cmd_stage(argv):
    import stages
    sub, rest = argv[0], argv[1:]
    rid = rest[0] if rest and not rest[0].startswith("--") else None
    try:
        if sub == "add" and rid:
            stages.add(rid, opt(rest, "--title", ""), opt(rest, "--pr"), opt(rest, "--parent"), "--group" in rest)
        elif sub == "set" and rid and opt(rest, "--col") and opt(rest, "--mark"):
            stages.set_cell(rid, opt(rest, "--col"), opt(rest, "--mark"), opt(rest, "--evidence"), opt(rest, "--by"))
        elif sub == "env" and (opt(rest, "--prod") or opt(rest, "--dev")):
            stages.set_env(opt(rest, "--prod"), opt(rest, "--dev"))
        elif sub == "next":
            stages.set_next(rest)
        elif sub in ("done", "drop") and rid:
            stages.finish(rid, drop=sub == "drop", evidence=opt(rest, "--evidence"))
            if sub == "done" and opt(rest, "--evidence"):
                import fleet
                print(f"{rid}: done — {fleet.mask(' '.join(opt(rest, '--evidence').split()))}")
        elif sub == "show":
            import board, fleet, table_width
            state = fleet.read_json(fleet.state_dir() / "state.json", {})
            t, gone = stages.table(state), 0
            if opt(rest, "--program"):
                t = stages.of_program(t, opt(rest, "--program"))
            import rowstate
            try:  # 지금: the brief's own judgment of each row's linked board row; none when the board cannot be read
                by_pr, known = rowstate.snapshot()["by_pr"], True
            except Exception as e:  # noqa: BLE001 — the stage table still shows, without 지금 and folding no PR row
                by_pr, known = {}, False
                print(f"warning: 지금 칸 없음: {type(e).__name__}: {e}", file=sys.stderr)
            t = stages.with_now(t, by_pr)
            if "--all" not in rest:  # the rows the brief's line counts
                idle_d = {**board.LIMITS, **(fleet.config().get("board") or {})}["stage_idle_d"]
                t, gone = stages.current(t, fleet.utcnow(), idle_d * 86400, state, by_pr, known)
            if "--json" in rest:
                return print(json.dumps(t, ensure_ascii=False, indent=1))
            text = (stages.render_telegram if "--telegram" in rest else stages.render_md)(t)
            fold = f"접음 {gone}행 (끝남 또는 {idle_d}일 그대로) · --all 로 전부" if gone else ""
            print("\n\n".join(filter(None, (text, fold, stages.ignored_note(t)))) or "기록된 단계 없음")
            if "--telegram" not in rest and any(table_width.table_width(x) > stages.COLS for _, x in table_width.tables(text)):
                sys.exit("a table is wider than 78 columns")
        else:
            sys.exit("usage: orch stage add <id> --title T [--pr owner/repo#N] [--parent ID] [--group] | set <id> --col merge|dev|prod "
                     "--mark ok|fail|partial|checking|na [--evidence TEXT] [--by WHO] | env [--prod V] [--dev V] | "
                     "next [LINE]... | done <id> | drop <id> | show [--program P] [--all] [--md|--telegram|--json]")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


def say_linked(order, item):
    """The line `--order` adds to a board add: the item now carries the order."""
    if order and item:
        print(f"{order} linked board:{item['id']}")


def cmd_board(argv):
    import board, store, tidy
    sub, rest = argv[0], argv[1:]
    rid = rest[0] if rest and not rest[0].startswith("--") else None
    try:
        if sub == "lead" and rest[:1] == ["add"] and len(rest) > 1:
            board.lead_add(rest[1], opt(rest, "--handle"), opt(rest, "--repo"), opt(rest, "--run"), opt(rest, "--stale-min"),
                           opt(rest, "--program"), opt(rest, "--worktree"))
        elif sub == "lead" and rest[:1] == ["close"] and len(rest) > 1:
            board.lead_close(rest[1])
        elif sub == "set" and rid:
            board.set_row(rid, **{f: opt(rest, f"--{f}") for f in
                                  ("kind", "title", "repo", "program", "parent", "status", "progress", "next", "owner", "pr", "worktree",
                                   "after", "priority", "phase")})
        elif sub == "annot-migrate":
            move, left = board.annot_migrate("--apply" in rest)
            print(f"{'moved' if '--apply' in rest else 'would move'} {len(move)} row(s) into card annotations"
                  + ("" if "--apply" in rest else "  (dry run; --apply writes)"))
            for wt, (row, fields) in move.items():
                print(f"  row:{row} -> card:{wt}  " + (" ".join(f"{k}={v}" for k, v in fields.items()) or "(no field to carry)"))
            print(f"중복 {len(left)}건" + (": 옮기지 않았다. 처리 명령을 쓴 뒤 다시 돌린다" if left else ""))
            for row, why, cmd in left:
                print(f"  row:{row}  {why}  → {cmd}")
        elif sub == "drop" and rid:
            board.drop(rid)
        elif sub == "priority" and rest[:1] == ["set"] and len(rest) == 3:
            board.priority_set(rest[1], rest[2])
        elif sub == "priority" and rest[:1] == ["drop"] and len(rest) == 2:
            board.priority_drop(rest[1])
        elif sub == "priority" and rest == ["list"]:
            for g in sorted(board.read()["groups"].values(), key=lambda g: (g["priority"].split(":", 1)[0].zfill(3), g["match"])):
                print(f"{g['match']}  {g['priority']}")
        elif sub == "doing" and rest[:1] == ["add"] and len(rest) > 1:
            if "--force" in rest:
                sys.exit("board doing add has no --force: reword a title that only shares a name with live work")
            say_linked(opt(rest, "--order"), board.doing_add(rest[1], opt(rest, "--owner"), opt(rest, "--next"), "--waiting" in rest,
                                                              opt(rest, "--order")))
        elif sub == "doing" and rest[:1] == ["drop"] and len(rest) > 1:
            board.doing_drop(int(rest[1]))
        elif sub == "later" and rest[:1] == ["add"] and len(rest) > 1:
            say_linked(opt(rest, "--order"), board.later_add(rest[1], opt(rest, "--trigger"), opt(rest, "--due"), opt(rest, "--who"),
                                                              opt(rest, "--at"), opt(rest, "--order"), opt(rest, "--note")))
        elif sub == "later" and rest[:1] == ["move"] and "".join(rest[1:2]).isdigit():
            board.later_move(int(rest[1]), opt(rest, "--to"))
        elif sub == "later" and rest[:1] == ["drop"] and len(rest) > 1:
            board.later_drop(int(rest[1]))
        elif sub == "todo" and rest[:1] == ["add"] and len(rest) > 1:
            say_linked(opt(rest, "--order"), board.todo_add(rest[1], opt(rest, "--due"), opt(rest, "--after"), opt(rest, "--who"),
                                                             opt(rest, "--order")))
        elif sub == "todo" and rest[:1] == ["drop"] and "".join(rest[1:2]).isdigit():
            board.todo_drop(int(rest[1]))
        elif sub == "todo" and rest[:1] == ["from-later"] and "".join(rest[1:2]).isdigit():
            board.todo_from_later(int(rest[1]), opt(rest, "--due"), opt(rest, "--who"))
        elif sub == "todo" and rest[:1] == ["list"]:
            now = opt(rest, "--now")
            if "--json" in rest:
                items = board.render(now=now and fleet_parse(now), fill=True, periodic=False, fresh=not now)["open"]
                return print(json.dumps([o for o in items if o["kind"] in ("do", "later")], ensure_ascii=False, indent=1))
            text, ok = board.todo_tables(now and fleet_parse(now), "--telegram" in rest, "--no-refresh" not in rest)
            print(text)
            sys.exit(0 if ok else 1)
        elif sub == "todo":
            # It replaced the owner's list before the to-do list took the name: an old line must not overwrite it.
            sys.exit("board todo is the to-do list now: todo add <title> (--due D | --after C) --who W | todo drop <n> | "
                     "todo from-later <n> [--due D] --who W | todo list [--telegram] [--json]. The owner's now-list moved to `orch board now [LINE]...`")
        elif sub == "now":
            board.set_now(rest)
        elif sub == "show":
            print(json.dumps(board.read(), ensure_ascii=False, indent=1))
        elif sub in ("done", "reopen") and rid:
            ids = [a for a in rest if not a.startswith("--") and a != opt(rest, "--why")]
            written, refused = board.close(ids, undo=sub == "reopen", why=opt(rest, "--why"))
            if "--json" in rest:
                print(json.dumps({"written": written, "refused": refused}, ensure_ascii=False))
            else:
                print(f"{'closed' if sub == 'done' else 'reopened'} {len(written)}" + (f": {' '.join(written)}" if written else ""))
                for i, why in refused:
                    print(f"refused {i}: {why}", file=sys.stderr)
            sys.exit(1 if refused else 0)
        elif sub == "open":
            items = board.render(fill=True)["open"]
            try:  # a list without the board's items before the cutover is not the whole board: say what is missing
                store.open_db().close()
            except store.NotMigrated:
                print(f"이행 대기: 보드를 읽을 수 없어 목록에 보드 항목이 없다. 한 줄로 끝낸다: {store.migrate_command()}", file=sys.stderr)
            if "--json" in rest:
                return print(json.dumps(items, ensure_ascii=False, indent=1))
            for o in items:
                print(f"{o['id']}  {o['kind']}  {o['label']}" + (f"  (확인 필요: {o['check']})" if o["check"] else ""))
        elif sub == "tidy":
            rows = tidy.run(dry="--dry-run" in rest)
            if "--json" in rest:
                return print(json.dumps(rows, ensure_ascii=False, indent=1))
            for r in rows:
                print(f"{r['id']}  {r['rule']}: {r['evidence']}  ({r['label']})")
            print(f"{'would close' if '--dry-run' in rest else 'closed'} {len(rows)}")
        elif sub == "closed":
            for k, r in tidy.closed().items():
                print(f"{k}  {r['ts']}  {r.get('by')}  {r.get('rule')}: {r.get('evidence')}  ({r.get('label')})")
        else:
            sys.exit("usage: orch board lead add <name> --handle H --repo R [--program P] [--run RUN] [--stale-min N] [--worktree ID] | "
                     "lead close <name> | set <id> [--kind K] [--title T] [--repo R] [--program P] [--parent ID] [--status S] "
                     "[--progress N/M] [--next T] [--owner W|-] [--after owner/repo#N|-] [--pr owner/repo#N] [--worktree ID] [--priority RANK:NAME|-] | drop <id> | priority set|drop|list | set card:<worktree> [--phase P] [--progress N/M] [--after PR] [--owner W --next T] | annot-migrate [--apply] | doing add <title> --owner W --next E [--waiting] [--order oN] | doing drop <n> | later add <title> --trigger T [--due D] [--order oN] | "
                     "later drop <n> | todo add <title> (--due D | --after C) --who W [--order oN] | todo drop <n> | todo from-later <n> [--due D] --who W | "
                     "now [LINE]... | show | open [--json] | done <id>... [--why T] [--json] | reopen <id>... [--json] | "
                     "tidy [--dry-run] [--json] | closed")
    except store.NotMigrated as e:
        sys.exit(f"the board is not in {store.DB_NAME} yet ({e})")
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    if sub in ("lead", "set") and (warning := board.unnamed_warning()):
        print(warning, file=sys.stderr)


def register_decisions(items):
    """Register a brief's --decision items; registered ones render from the store with their id. Returns the items
    still to print as given, marked unregistered, and the warning line, or None. A store that fails must not cost the
    report: it is how the owner hears of everything else, so every failure lands here."""
    import fleet
    left, errors = [], []
    for item in items:
        try:
            d, answered = fleet.decision_ensure(item, os.environ.get("ORCA_TERMINAL_HANDLE"))
            if answered:
                print(f"--decision skipped: {d['id']} already has its answer (orch decide show {d['id']})", file=sys.stderr)
        except Exception as e:
            left.append(f"(미등록) {item}")
            errors.append(str(e))
    return left, f"⚠️ 결정 등록 실패 {len(left)}건: {errors[0]}" if errors else None


def cmd_brief(argv):
    import board
    if argv[:1] != ["render"]:
        sys.exit("usage: orch brief render [--periodic] [--scope S] [--telegram] [--headline T] [--decision T]... "
                 "[--insight T] [--skipped T] [--now ISO8601] [--commit] [--json] [--no-fill] [--no-refresh]")
    now = opt(argv, "--now")
    scope = opt(argv, "--scope", "top-level")
    fill = "--no-fill" not in argv  # --fill, the old opt-in, is now the default and still accepted
    items = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--decision"]
    failed = None
    if not now:  # --now replays a past moment; only a report of this moment is sent
        for d in items:
            if why := board.decision_lint(d):
                sys.exit(f"--decision is one line: {board.DECISION_FORM}\n{why} ({board.ASK_GATE})")
        items, failed = register_decisions(items)
    try:
        out = board.render(scope, "telegram" if "--telegram" in argv else "terminal", now and fleet_parse(now),
                           opt(argv, "--headline"), opt(argv, "--skipped"), fill, periodic="--periodic" in argv,
                           decisions=items, insight=opt(argv, "--insight"),
                           # --now replays a past moment, so the fleet as it is now does not belong in it
                           fresh=not now and "--no-refresh" not in argv)
    except ValueError as e:
        sys.exit(str(e))
    if failed:
        print(failed, file=sys.stderr if "--json" in argv else sys.stdout)
    print(json.dumps(out, ensure_ascii=False, indent=1) if "--json" in argv else out["text"])
    if "--commit" in argv:
        board.commit(scope, out["snapshot"])
    if not out["width_ok"]:
        sys.exit("a table is wider than 78 columns")
    if failed:
        sys.exit(1)


def fleet_parse(s):
    try:
        t = parse_ts(s)
    except ValueError:
        sys.exit(f"--now {s!r} is not ISO8601")
    return t if t.tzinfo else t.astimezone()


def cmd_hold(argv, release=False):
    import holds
    target = argv[0] if not argv[0].startswith("--") else None
    by = opt(argv, "--by") or os.environ.get("ORCA_TERMINAL_HANDLE") or os.environ.get("USER") or "unknown"
    try:
        if not release and "--list" in argv:
            rows = holds.current()
            if "--json" in argv:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            for r in [] if "--json" in argv else rows:
                print(f"{r['kind']:8} {r['resource']:24} {r['by'] or '?'}  since {r['at']}" + (f"  {r['note']}" if r["note"] else ""))
            if not rows and "--json" not in argv:
                print("nothing held")
        elif not target:
            sys.exit("usage: orch hold <resource> [--note TEXT] [--ttl MIN] [--by WHO] | hold --list [--json] | "
                     "release <resource> [--by WHO] [--force]")
        elif release:
            held = holds.release(target, by, force="--force" in argv)
            print(f"released {target}" if held else f"{target} was not held")
        else:
            row = holds.hold(target, by, opt(argv, "--note"), int(opt(argv, "--ttl", holds.TTL_MIN)))
            print(f"{target} held by {row['by']} (ttl {row['ttl_min']}m)")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


def cmd_sent(argv):
    import sent
    sub, rest = argv[0], argv[1:]
    usage = ("usage: orch sent add --to WHO (--text-file F | - | --text T) [--kind K] [--channel HOW] [--repo R] "
             "[--subject S] [--from WHO] | list [--days N] [--json]  (no <slug>)")
    if sub == "add":
        # Every other subcommand takes <slug> first, so a stray slug or flag here is likely; refuse it rather than drop it.
        valued, i = {"--to", "--text", "--text-file", "--kind", "--channel", "--repo", "--subject", "--from"}, 0
        while i < len(rest):
            if rest[i] in valued:
                i += 2
            elif rest[i] == "-":
                i += 1
            else:
                sys.exit(f"unexpected argument {rest[i]!r}\n{usage}")
    try:
        if sub == "add" and opt(rest, "--to"):
            text, f = opt(rest, "--text"), opt(rest, "--text-file")
            if text is None:
                text = pathlib.Path(f).read_text() if f else sys.stdin.read() if "-" in rest else None
            row = sent.add(opt(rest, "--to"), text, opt(rest, "--kind"), opt(rest, "--channel"), opt(rest, "--repo"),
                           opt(rest, "--subject"), opt(rest, "--from") or os.environ.get("ORCA_TERMINAL_HANDLE"))
            print(f"logged {row['ts']} → {row['to']}")
        elif sub == "list":
            import fleet
            try:
                messages = fleet.orca("orchestration", "inbox", "--limit", str(fleet.INBOX_LIMIT)).get("messages")
            except fleet.SourceError as e:
                messages = None
                print(f"orca inbox not read, its sends are missing: {e}", file=sys.stderr)
            days = int(opt(rest, "--days", "1"))
            pg = sent.page(fleet.fetch_sent() + fleet.orca_messages_sent(messages), days, inbox_since=fleet.inbox_cut(messages))
            rows = pg["rows"]
            if pg["inbox_since"] and pg["inbox_since"] > fleet.iso(fleet.utcnow() - dt.timedelta(days=days)):
                print(f"orca messages before {pg['inbox_since']} are past the inbox read and not listed", file=sys.stderr)
            if pg["unrecorded"]:
                print(f"{pg['unrecorded']} send(s) in live session transcripts match no log or Orca row: "
                      "the sender skipped `orch sent add`", file=sys.stderr)
            if "--json" in rest:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            for r in [] if "--json" in rest else rows:
                print(f"{r['ts']}  {r.get('from') or '?'} → {r.get('to') or '?'}  [{r['source']}] {(r.get('subject') or r['text'] or '?').splitlines()[0][:100]}")
                if r.get("warn"):
                    print(f"    ! {r['warn']}")
        else:
            sys.exit(usage)
    except (ValueError, OSError) as e:
        sys.exit(f"{e}\n{usage}")


COMMANDS = {"init": cmd_init, "set": cmd_set, "status": cmd_status, "record": cmd_record, "verdict": cmd_verdict,
            "gate": cmd_gate, "dep": cmd_dep, "queue": cmd_queue, "overlap": cmd_overlap, "land": cmd_land, "land-check": cmd_land_check,
            "landed": cmd_landed, "heavy": cmd_heavy, "wait": cmd_wait, "inbox": cmd_inbox, "backfill": cmd_backfill, "decide": cmd_decide,
            "db": cmd_db, "model": cmd_model,
            "stage": cmd_stage, "board": cmd_board, "brief": cmd_brief, "hold": cmd_hold, "sent": cmd_sent, "release": lambda argv: cmd_hold(argv, release=True),
            "land-exec": lambda argv: __import__("land_exec").main(argv),
            "order": lambda argv: __import__("order").main(argv),
            "ask": lambda argv: __import__("ask").main(argv),
            "watch-cards": lambda argv: __import__("card_watch").main(argv),
            "handoff": lambda argv: __import__("handoff").main(argv),
            "patrol": lambda argv: __import__("patrol").main(argv),
            "migrate": lambda argv: __import__("orca_migrate").main(argv),
            "doctor": lambda argv: __import__("orca_settings").main(argv)}

if __name__ == "__main__":
    if len(sys.argv) < 3 - (sys.argv[1:2] in (["watch-cards"], ["patrol"], ["inbox"], ["migrate"], ["doctor"])) or sys.argv[1] not in COMMANDS or sys.argv[1] not in ("watch-cards", "patrol") and {"-h", "--help"} & set(sys.argv[2:(sys.argv + ["--"]).index("--")]):
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
