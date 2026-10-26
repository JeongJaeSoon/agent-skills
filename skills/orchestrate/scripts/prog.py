#!/usr/bin/env python3
"""Program bookkeeping for orchestrate: the numbers a coordinator decides from.

Usage: orch <command> <slug> [options]

  init <slug> --repo OWNER/NAME --run RUN_ID [--tracker-project NAME] [--tracker linear|jira]
              [--predicate ID,ID,...] [--merge-policy autonomous|human-gate] [--ceiling 6]
              [--deadline ISO8601] [--note NOTES_PATH] [--final-check TEXT]
  set <slug> <merge_policy|ceiling|deadline|predicate|exclusive_paths|visual_paths|note|final_check> VALUE
                                     mirror a change made in the program note
  status <slug>                      predicate, flow, growth and the next move, from live sources
  record <slug> <event> [--ticket T] [--pr N] [--sha S] [--class K] [--role R] [--note TEXT]
                                     `record spawned --role guardian --note <dispatchId>` for a standing
                                     role; roles do not count against the cap
                                     `record reprioritized --pr N --class urgent` moves a PR in the land order
                                     main_green / main_red need --sha of a landed merge commit
  record <slug> signal --kind human_correction|brief_gap|stall|tooling --evidence PTR [--ticket T] [--note TEXT]
                                     a lesson candidate for reflect at Close: a human correction, a
                                     question the brief should have answered, a stall the process
                                     caused, a skill or script defect. PTR points at the proof (message
                                     id, PR comment URL, transcript path:line); nothing is copied in
  verdict <slug> --pr N --sha REVIEWED_HEAD --source WHO [--result pass|fail] [--note TEXT]
  gate <slug> --pr N [--parent TASK_ID]
                                     human-gate: open an Orca decision gate "land PR N?" on a
                                     coordinator-owned landing Task; the user resolves it in Orca
  dep <slug> --ticket A --after B[,C]
                                     A cannot land before B and C: a land-after edge (A stacked on
                                     B's branch), or one found after dispatch. Start-after edges are
                                     Orca task deps (task-create --deps), read from the Run
  queue <slug> [--json]              the land order and why each PR is not moving
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
  land-check <slug> --pr N [--main-fix]
                                     readiness only, no merge (coordinator diagnostics)
  landed <slug> --pr N               record a merge made outside `land` and print the main-CI watch
  backfill <slug> --since ISO8601 [--dry-run]
                                     a program registered mid-flight: add the PRs merged from --since
                                     (when its work began) until it, at their merge time with their main
                                     push CI result, ahead of the program's own rows, and move created_at
                                     to the first of them. Rerun to add CI results that came in since
  heavy <slug|-> [--wait-minutes 60] [--pr owner/repo#N] -- <command…>
                                     run a heavy local command (compose stack, image build, local
                                     E2E) holding one of heavy_slots (2) machine-wide slots; `-`
                                     outside a program. A compose project it leaves behind, with a
                                     config file in this worktree, goes to the reap ledger
                                     (compose-stack) so the janitor downs it once the card is done
  wait <slug> [--timeout-ms 540000] [--rounds 3]
                                     coordinator only: block until the Run inbox holds actionable
                                     mail; acks heartbeat-only batches; never acks actionable ones
  inbox [--ack DELIVERY] [--wait] [--timeout-ms 3300000]
                                     no slug, a session without a program: ack the batch you handled,
                                     peek-wait (no --types, so Orca's typed notice stays out), then
                                     print the next batch one line per message with its ack line;
                                     with --wait, acks heartbeat-only batches and waits on; a failed
                                     --ack is reported and the wait still runs
  watch-cards [--interval 180] [--idle-min 30] [--min-free-gb 15] [--timeout-ms 3300000] [--once]
                                     no slug: exit on a new stuck screen in any agent terminal but this
                                     one (cards no Run lists), or low disk; never types (card_watch.py)
  decide add --title T [--body-file F] [--option "label::description"]... [--recommend N] [--link URL]
  decide list | done <id> [--answer TEXT] | drop <id>
                                     no slug: a decision you wait on the human for, shown on the fleet
                                     dashboard until done or dropped; add prints its id. Each option is a
                                     button that closes it with that answer, then types
                                     "decision <id>: <label>" into this terminal once it is idle
  req list [--all] [--json] | link <id>... <WHERE> [--done [--evidence T]] | done <id>... --evidence T
  req drop <id>... --reason T
                                     no slug: the owner's requests to this coordinator, logged by the
                                     request_log hook from each human prompt of an orchestrator or lead.
                                     link says where they went (board row, card:<name>, owner/repo#N,
                                     ticket; nothing is written to the tracker), --done closes them too;
                                     done and drop close them. The fleet tick links what the asking
                                     session started next, closes ones whose links all finished, and
                                     drops unlinked ones whose terminal is gone. One unlinked for 12 h
                                     expires: out of the counts and list (--all shows it), still closable
  stage add <id> --title T [--pr owner/repo#N] [--parent ID] [--group]
  stage set <id> --col merge|dev|prod --mark ok|fail|partial|checking|na [--evidence TEXT] [--by WHO]
  stage env [--prod V] [--dev V] | next [LINE]... | done <id> | drop <id> | show [--md|--telegram|--json]
                                     no slug: the stage table the brief skill reports, one row per work item.
                                     Write a cell where you verified it; ok needs --evidence. The merge
                                     cell of a row with --pr comes from the fleet PR list. `next` replaces
                                     the next-steps list (no lines clears it); done rows leave after 7
                                     days; drop removes a row and its children. --group makes a heading
                                     row (a feature or an epic) with no PR and no cells; --parent rows to it
  board lead add <name> --handle H --repo OWNER/NAME [--program P] [--run RUN] [--stale-min N] | lead close <name>
  board set <id> [--kind lead|phase|task] [--title T] [--repo OWNER/NAME] [--program P] [--parent ID]
                 [--status moving|owner|waiting|held|blocked|done] [--progress N/M|-] [--next TEXT] [--owner WHO] [--pr owner/repo#N]
  board drop <id> | doing add <title> --owner W --next E [--waiting] | doing drop <n> | later add <title> --trigger T [--due D]
        | later drop <n> | todo [LINE]... | show
                                     no slug: the board the periodic report is rendered from (board.json beside
                                     stages.json), one row per lead, phase and sub-task. set changes only the
                                     fields given and stamps reported_at; an id a/b hangs off a. A row without
                                     --parent is a lead (needs --repo); under a parent it is a task unless
                                     --kind phase. PR state is joined from the collector's prs.json at render.
                                     --program is the lead's project (epic, program): leads sharing a repository
                                     get one table each only when each has one; lead add and set warn until
                                     they do, and the report heads the rest (프로젝트 미지정)
                                     A waiting row waits on a person: the PR's requested reviewers say who, else
                                     --owner (오너, owner or the gh login is the owner; another name is someone
                                     else); with neither the report marks it 확인 필요. --status owner is a row
                                     only the owner unblocks.
                                     todo replaces the owner's to-do list (no lines clears it); doing lists work
                                     that is moving but has no row (an investigation), --waiting when W is a
                                     person it waits on (a reply from another team), --owner 오너 when it waits
                                     on the owner; later only work set aside on purpose, with the trigger that
                                     brings it back
  brief render [--periodic] [--scope top-level|PROGRAM] [--telegram] [--headline T] [--decision T]... [--insight T]
               [--skipped T] [--now ISO8601] [--commit] [--json] [--no-fill] [--no-refresh]
                                     the brief skill's report, on request or (--periodic) on a schedule, from the
                                     board, the fleet's sessions, prs.json, open decisions and brief-last/<scope>.json
                                     (bold = changed since it). The time is the system clock's in the local time
                                     zone. On request the stage table (stages.json) goes first; --periodic adds
                                     --skipped and the last reap. The model gives only --headline (a sentence),
                                     --decision ("<question>: <options> → 추천: X — <reason> · 답이 없으면 Y",
                                     numbered after the registered ones) and --insight (after 진행 중). state.json
                                     older than 5 min ("state_min") is collected again first unless --now or
                                     --no-refresh; a failed collect is in the headline with the files' age. --commit
                                     saves this table as the last one (after sending). Staleness: a row past 90
                                     min shows its age (board rows past it are counted in the headline), an open
                                     PR probed over 30 min ago is in the headline,
                                     over 120 min marks it ⚠️ (merged and closed PRs do not age), a lead whose terminal left the fleet turns 🔴;
                                     a ✅ row merged over 24 h ago folds into its section's count, a stage row
                                     finished or 7 days unchanged leaves the stage table;
                                     override in the dashboard config: {"board": {"row_min", "pr_warn_min",
                                     "pr_stale_min", "state_min", "done_fold_h", "stage_idle_d"}}. Exit 1 when a table is wider than 78 columns. Rows the
                                     board lacks come from the fleet's sessions (state.json): each lead the board
                                     has no lead for, its cards and the coordinator's own cards, each with the
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
import contextlib, datetime as dt, fcntl, fnmatch, hashlib, heapq, json, os, pathlib, re, shlex, subprocess, sys, tempfile, time

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


def run(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(cmd[:4])} … failed ({r.returncode}): {r.stderr.strip()[:400]}")
    return r


def orca_json(*args):
    out = json.loads(run(["orca", *args, "--json"]).stdout)
    if not out.get("ok", True):
        sys.exit(f"orca {' '.join(args[:2])}: {json.dumps(out.get('error'))[:400]}")
    return out.get("result", out)


def run_workers(run_id):
    """Every Dispatch of the Run. worker-list pages at 100, newest first, and a program passed 86 in two days:
    one page would drop the oldest cards from LANDED-BUT-OPEN."""
    workers, cursor = [], None
    while True:
        res = orca_json("orchestration", "worker-list", "--run", run_id, "--limit", "100", *(["--cursor", cursor] if cursor else []))
        workers += res.get("workers", [])
        cursor = (res.get("page") or {}).get("nextCursor")
        if not (res.get("page") or {}).get("hasMore") or not cursor:
            return workers


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
    order, result, last, lower = [], {}, None, stack_lower(events)
    for e in events:
        if e["ev"] == "landed" and e.get("sha") and e["sha"] not in lower:
            order.append(e["sha"]); last = e["sha"]
        elif e["ev"] in ("main_green", "main_red"):
            sha = e.get("sha") or last
            if sha in order:
                result[sha] = e["ev"]
    if not order:
        return "green"
    decided = [s for s in order if s in result]
    if decided and result[decided[-1]] == "main_red":
        return "red"
    return "green" if order[-1] in result else "pending"


def final_check_current(events):
    """A predicate_verified counts only if nothing it covered changed after it: no landing, no predicate edit,
    no newly admitted follow-up."""
    since = max((i for i, e in enumerate(events) if e["ev"] == "landed" or (e["ev"] == "admitted" and e.get("ticket"))
                 or (e["ev"] == "config" and (e.get("note") or "").startswith("predicate="))), default=-1)
    return any(e["ev"] == "predicate_verified" for e in events[since + 1:])


def pr_state(events):
    """Latest event of each kind per PR, in ledger order."""
    st = {}
    for e in events:
        if e.get("pr") is not None:
            st.setdefault(e["pr"], {"pr": e["pr"]})[e["ev"]] = e
    return st


def ready_prs(events):
    return [s for s in pr_state(events).values() if ("ready" in s or "verdict" in s) and "landed" not in s]


def stopped(events):
    marks = [e["ev"] for e in events if e["ev"] in ("stop", "resume")]
    return bool(marks) and marks[-1] == "stop"


KLASS = {"main-fix": 0, "gate": 1, "urgent": 2, "normal": 3}
LANDING = {"aging_hours": 2.0, "stale_hours": 3.0, "max_backlog_hours": 2.0, "land_interval_minutes": 25,
           "exclusive_stale_minutes": 90, "heavy_slots": 2,
           # Changes that can break a merge they do not textually touch land one at a time, on the latest base.
           "exclusive_paths": ["*migrations/*", ".github/*", "*Dockerfile*", "*compose*.yml", "*compose*.yaml"],
           # A PR touching these changes what a person sees; fnmatch's * also crosses "/".
           "visual_paths": ["apps/web/*", "*.css", "*.stories.*", "packages/ui/*"]}


def knob(cfg, key):
    return (cfg.get("landing") or {}).get(key, LANDING[key])


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


def live_units(events, dispatches):
    """Live workers as the cap counts them: the layers of one land-after chain (ledger `dep`) land as one
    merge, so they count once."""
    ticket = {}
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


def ticket_of(task):
    # The brief opens with the ticket ID; a card name like "W-372" only looks like one.
    m = TICKET_ID.search(((task.get("spec") or "").splitlines() or [""])[0])
    if m:
        return m.group(0)
    word = ((task.get("display_name") or task.get("task_title") or "").split() or [""])[0]
    return word if TICKET_ID.fullmatch(word) else None


OPEN_STATES = {"triage", "backlog", "unstarted", "started"}


def landed_but_open(events, workers, tasks, worktrees, issues_by_id=None):
    """Cards left behind: (ticket, dispatch, terminal still active, path) for each Orca worktree whose every
    dispatch worked a ticket that has landed. A released worker's card stays until `orca worktree rm`.

    A ticket can take several PRs, a backfilled landing among them: while the tracker says it is open,
    a landing is not its live worker's end. Without the tracker, a landing is, except for a card spawned after it."""
    ticket_by_task = {t["id"]: ticket_of(t) for t in tasks}
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
        if wt in present:
            cards.setdefault(wt, []).append(w)
    out = []
    for wt, ws in cards.items():
        tickets = {ticket_by_task.get(w.get("taskId")) for w in ws}
        later = any(spawned.get(w.get("dispatchId"), "") > last_land.get(ticket_by_task.get(w.get("taskId")), "~")
                    for w in ws)
        if tickets <= landed and not later:
            active = [w for w in ws if w.get("terminalState") == "active"]
            if active and tickets & still_open:
                continue
            out.append((sorted(tickets)[0], (active or ws)[-1]["dispatchId"], bool(active), present[wt]))
    return sorted(out)


def run_tasks(run_id):
    r = run(["orca", "orchestration", "task-list", "--run", run_id, "--json"], check=False)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return []
    tasks = out.get("result", out) if isinstance(out, dict) else out
    return tasks.get("tasks", []) if isinstance(tasks, dict) else tasks or []


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


def row_state(row, verdict):
    """ready, catching_up (only an update-branch or CI time stands between it and landing), or blocked."""
    if row is None:
        return "gone", ["PR is no longer open"]
    blocked, catching = [], []
    failed, pending, _ = ci_summary(row.get("statusCheckRollup"))
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
        blocked.append("branch protection blocks it (required review or check)")
    if mss == "UNKNOWN":
        catching.append("GitHub is computing mergeability")
    if blocked:
        return "blocked", blocked + catching
    if catching:
        return "catching_up", catching
    note = [] if verdict.get("sha") == row.get("headRefOid") else [
        f"verdict is for {verdict['sha'][:8]}; land re-checks the patch-id at {row['headRefOid'][:8]}"]
    return "ready", note


def land_order(events, rows, cfg, when, stacks=None, deps=None):
    """Every unlanded program PR in the order it may land, with why each is not moving.

    Class first (main-fix, gate, urgent, normal). A PR that has waited aging_hours counts as
    urgent, so nothing starves behind a stream of fresh, easy PRs. Within a class the PR that
    unblocks more open tickets goes first, then the one that has waited longest. Normal-lane PRs
    land in parallel as soon as they are ready; the order decides only who takes the exclusive
    lane next and what the coordinator unsticks first.

    A GitHub stack is one landing unit that lands from its top in one merge: a lower layer waits
    for the top while the top is not blocked, and the top is ready only when every open layer
    below it is.
    """
    st, tickets, after = pr_state(events), pr_ticket(events), dep_map(events, deps)
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
        state, reasons = row_state(rows.get(pr), s.get("verdict"))
        if on_hold(events, pr) and state != "gone":
            state, reasons = "blocked", ["on hold"] + reasons
        live = stack_of.get(pr, [])
        below_t = {tickets.get(b) for b in live[:live.index(pr)]} if live else set()
        waits = sorted(b for b in after.get(ticket, ()) if b not in landed_t | below_t)
        if waits and state != "gone":
            state, reasons = "waiting", [f"lands after {', '.join(waits)}"] + reasons
        lane = (s.get("lane") or {}).get("exclusive", False) or klass == "gate"
        out.append({"pr": pr, "ticket": ticket, "klass": klass, "rank": rank,
                    "unblocks": unblocks(ticket, after, open_t) if ticket else 0,
                    "since": first.isoformat(), "age_h": round(age_h, 2), "state": state, "reasons": reasons,
                    "exclusive": bool(lane), "base": (rows.get(pr) or {}).get("baseRefName"), "stack": None})
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
            unready = [f"#{b} {own.get(b, 'not in the program')}" for b in live[:i] if own.get(b) != "ready"]
            if unready and e["state"] == "ready":
                e["state"], e["reasons"] = "catching_up", [f"stack layer {', '.join(unready)}"] + e["reasons"]
    out.sort(key=lambda e: (e["state"] == "gone", e["rank"], -e["unblocks"], e["since"], e["pr"]))
    return out


def exclusive_turn(order, pr):
    """The exclusive-lane entry that goes before this one on its base, or None when it is this PR's turn.

    Only exclusive entries that could land soon contend (ready, or catching up); blocked and
    waiting ones are passed over, so a stuck PR never holds the lane.
    """
    base = next(e for e in order if e["pr"] == pr).get("base")
    for e in order:
        if e["pr"] == pr:
            return None
        if e["exclusive"] and e.get("base") == base and e["state"] in ("ready", "catching_up"):
            return e
    return None


def pr_files(repo, pr):
    return [f["path"] for f in gh_json("pr", "view", str(pr), "--repo", repo, "--json", "files").get("files", [])]


def is_exclusive(cfg, files):
    pats = knob(cfg, "exclusive_paths")
    return sorted(f for f in files if any(fnmatch.fnmatch(f, g) for g in pats))


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


def evidence_problems(cfg, repo, pr):
    """(problems, notes) from the PR's sticky test-results comment. The brief's rule alone once let a screen
    change land with no screenshot, so land checks it: an image for a visual change unless a person labelled
    the PR no-visual-change, and each failed CI run of the branch named with what resolved it."""
    v = gh_json("pr", "view", str(pr), "--repo", repo, "--json", "headRefName,labels,comments")
    # The first marker comment, the one sticky-comment.sh keeps updating.
    body = next((c.get("body") or "" for c in v.get("comments") or [] if TEST_RESULTS in (c.get("body") or "")), "")
    problems, notes = [], []
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
                        f" {', '.join(unexplained[:5])}; add a line per run with its id and what resolved it")
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


def open_rows(repo):
    return gh_json("pr", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json",
                   "number,title,headRefOid,headRefName,mergeStateStatus,isDraft,statusCheckRollup,baseRefName")


def repo_stacks(repo):
    """{stack id: [PR numbers bottom→top]} from GitHub stacked PRs, or {} when unavailable.

    This is the endpoint the gh-stack extension itself reads; it is not a documented API, so a
    failure only turns stack-aware landing off, never landing itself.
    """
    r = run(["gh", "api", f"repos/{repo}/cli_internal/pulls/stacks"], check=False)
    try:
        return {s["id"]: s["pull_requests"] for s in json.loads(r.stdout)} if r.returncode == 0 else {}
    except (ValueError, KeyError, TypeError):
        return {}


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


def pr_view(repo, pr):
    return gh_json("pr", "view", str(pr), "--repo", repo, "--json",
                   "state,isDraft,headRefOid,headRefName,baseRefName,mergeStateStatus,mergeable,statusCheckRollup,mergeCommit,url")


def ci_summary(rollup):
    # No checks on a head usually means CI has not reported yet, so it never counts as green.
    # A repo with no CI at all therefore cannot land; add a require_ci knob if one ever joins a program.
    if not rollup:
        return [], ["no CI checks reported yet"], []
    failed, pending, runs = [], [], set()
    for c in rollup or []:
        name = c.get("name") or c.get("context")
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        status = (c.get("status") or "COMPLETED").upper()
        url = c.get("detailsUrl") or c.get("targetUrl") or ""
        if "/actions/runs/" in url:
            runs.add(url.split("/actions/runs/")[1].split("/")[0])
        if status != "COMPLETED" or concl in ("", "PENDING", "EXPECTED"):
            pending.append(name)
        elif concl not in PASSING:
            failed.append(f"{name}={concl}")
    return failed, pending, sorted(runs)


def runs_unfinished(repo, sha):
    """Workflow runs at `sha` that have not finished. The check rollup alone is not enough: a bot's status
    (a review app) can be its only entry before Actions starts, and a job behind `needs` has no check until
    the jobs before it end, so an early all-green rollup once landed a PR before its CI ran."""
    try:
        rs = gh_json("run", "list", "--repo", repo, "--commit", sha, "--json", "status,name", "--limit", "100")
    except (SystemExit, Exception) as e:
        return [f"workflow runs unreadable ({type(e).__name__})"]
    if not rs:
        return ["no workflow run at the head yet"]
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


def gate_resolution(cfg, events, pr):
    """The resolution of the newest landing gate opened for this PR, or None while it is pending."""
    opened = [e for e in events if e["ev"] == "gate_opened" and e.get("pr") == pr]
    if not opened:
        return None
    res = orca_json("orchestration", "gate-list", "--task", opened[-1]["task"], "--run", cfg["run"])
    for g in res.get("gates", res if isinstance(res, list) else []):
        if g.get("id") == opened[-1]["gate"] and g.get("status") == "resolved":
            return g.get("resolution")
    return None


GATE_TASK = "Land #"


def close_gate_task(p, pr, status):
    """No worker settles the coordinator-owned `Land #N` Task, so it would sit in `task-list --ready` for good:
    completed when the PR lands, failed when the gate is held or the PR is closed. A new gate makes a new Task."""
    opened = [e for e in p.events() if e["ev"] == "gate_opened" and e.get("pr") == pr]
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
    (d / "briefs").mkdir(parents=True, exist_ok=True)
    (d / "program.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"initialised {d}")
    print("\n".join(dashboard_lines(slug)))


STALL_TEXT = {
    "idle": "turn ended {minutes} min ago with its task open and no wake-up armed; an orchestration message "
            "does not wake an idle session: send the content to dispatch:{dispatch}, then nudge its terminal with "
            "one line (orca terminal send --text 'run your orchestration check' --enter)",
    "start_unconfirmed": "dispatched {minutes} min ago and its session has written nothing: check it got the brief",
    "long_tool": "one {tool} call running for {minutes} min ({detail}): still waiting on purpose?",
    "silent": "its session has written nothing for {minutes} min though its last row is input, not an ended turn "
              "(a notification that arrived after the turn ended looks like this): read its terminal, and if it sits "
              "at the prompt, send the content to dispatch:{dispatch} and nudge its terminal as for an idle one",
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


def new_stalls(pdir, renotify=dt.timedelta(minutes=30)):
    """STALLED lines `orch wait` has not shown in the last 30 minutes. A coordinator that only loops `orch wait`
    never reads `orch status`, so a stall shown only there went unseen for hours."""
    st = dash_state(pdir)
    if st is None:
        return []
    state, now = pdir / "stalls-seen.json", dt.datetime.now(dt.timezone.utc)
    try:
        seen = json.loads(state.read_text())
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    stalls = (st.get("summary") or {}).get("stalls") or []
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
    cards = [c for c in st.get("landed_open") or [] if not c.get("active")]
    fresh = [c for c in cards if c["dispatch"] not in seen or now - parse_ts(seen[c["dispatch"]]) >= renotify]
    keep = {c["dispatch"]: seen.get(c["dispatch"]) for c in cards}
    keep.update({c["dispatch"]: now.isoformat() for c in fresh})
    try:
        state.write_text(json.dumps({k: v for k, v in keep.items() if v}))
    except OSError:
        pass
    return [f"  LANDED-BUT-OPEN {c.get('ticket') or '?'} (dispatch {c['dispatch']}): released, but its card is still"
            f" there: `orca worktree rm --worktree path:{c['path']}`" for c in fresh]


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


def cmd_status(argv):
    p = Program(argv[0])
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
    roles = {e.get("note"): e["role"] for e in events if e["ev"] == "spawned" and e.get("role")}
    running = [w for w in workers if (w.get("projection") or {}).get("outcome") == "in_progress"]
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
    order = land_order(events, open_rows(cfg["repo"]), cfg, tnow, repo_stacks(cfg["repo"]), deps_from_tasks(tasks))
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
    leftover = landed_but_open(events, workers, tasks, worktrees, by_id)
    for tk, dispatch, active, path in leftover:
        rm = f"`orca worktree rm --worktree path:{path}`"
        lines.append(f"  LANDED-BUT-OPEN {tk} (dispatch {dispatch}): "
                     + (f"once its main CI is recorded, worker-release, close its terminals and {rm}" if active
                        else f"released, but its card is still there: {rm}"))

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
                          leftover=sum(not active for _, _, active, _ in leftover))
    budget_note = f" (budget {used:.0%} used)" if used is not None else ""
    lines.append(f"next: {nxt}{budget_note}")
    print("\n".join(lines + dashboard_lines(p.slug)))


def next_move(cfg, events, tnow, *, tickets_done, live, human, order, leftover=0):
    """Step 4 of `status`, first matching rule wins; safety outranks completion. orch-dash shows the same line.

    tickets_done is None when the tracker could not say: a current predicate_verified then decides Close.
    leftover counts released LANDED-BUT-OPEN cards; they go before spawning. While "may spawn 3 more" topped
    them, a compacted coordinator left eight open.
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
    # A PR the coordinator put on hold waits on purpose; counting it would block spawning until someone closes it.
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
         "lock_acquired": "land", "lock_released": "land", "dep": "dep", "config": "set"}


# land_failed, main_red and failed verdicts are already in the ledger; a signal records what only the coordinator sees.
SIGNAL_KINDS = ("human_correction", "brief_gap", "stall", "tooling")


def cmd_record(argv):
    p = Program(argv[0])
    ev = argv[1]
    pr, sha = opt(argv, "--pr"), opt(argv, "--sha")
    if ev in ("main_green", "main_red") and sha not in landed_shas(p.events()):
        top = stack_lower(p.events()).get(sha)
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


def cmd_set(argv):
    p = Program(argv[0])
    key, value = argv[1], argv[2]
    if key == "merge_policy" and value not in ("autonomous", "human-gate"):
        sys.exit("merge_policy is autonomous or human-gate")
    split = lambda v: [x for x in v.split(",") if x]
    conv = {"ceiling": int, "predicate": split, "exclusive_paths": split, "visual_paths": split}.get(key, str)
    if key not in ("merge_policy", "ceiling", "deadline", "predicate", "exclusive_paths", "visual_paths", "note", "final_check"):
        sys.exit("settable: merge_policy, ceiling, deadline, predicate, exclusive_paths, visual_paths, note, final_check")
    if key == "visual_paths" and not conv(value):
        sys.exit("visual_paths cannot be empty: the screenshot check has no off switch, only the no-visual-change label")
    if key in ("exclusive_paths", "visual_paths"):
        p.update(lambda cfg: cfg.setdefault("landing", {}).__setitem__(key, conv(value)))
    else:
        p.update(lambda cfg: cfg.__setitem__(key, conv(value)))
    print(json.dumps(p.append("config", note=f"{key}={value}"), ensure_ascii=False))


def cmd_verdict(argv):
    p = Program(argv[0])
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
    head = pr_view(p.cfg["repo"], pr)["headRefOid"]
    if not head.startswith(sha):
        sys.exit(f"head is {head[:8]}, not the reviewed {sha[:8]}: the new head has not been reviewed")
    row = p.append("verdict", pr=pr, sha=head, patch_id=patch_id(p.cfg["repo"], pr),
                   source=src, result=opt(argv, "--result", "pass"), note=opt(argv, "--note"))
    print(json.dumps(row, ensure_ascii=False))


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

    def take(self, pr):
        """True if this PR now holds the lane (newly or already)."""
        held = self.holder()
        mine = {"pr": pr, "program": self.p.slug, "ts": now()}
        if held:
            if (held.get("pr"), held.get("program")) != (pr, self.p.slug):
                return False
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(mine))
            os.replace(tmp, self.path)
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
        if r.returncode == 0 and state in ("CLOSED", "MERGED"):
            self.path.unlink(missing_ok=True)
            self.p.append("lock_released", pr=held.get("pr"), note=f"holder is {state}")
            return None
        return held

    def release(self, pr):
        held = self.holder()
        if held and (held.get("pr"), held.get("program")) == (pr, self.p.slug):
            self.path.unlink(missing_ok=True)
            self.p.append("lock_released", pr=pr, note=self.path.name)


def note_attempt(p, events, pr, outcome, reasons):
    """Refresh the reservation at most every 10 minutes, or when the outcome changes."""
    last = pr_state(events).get(pr, {}).get("land_check")
    if last and last.get("outcome") == outcome and \
            dt.datetime.now(dt.timezone.utc) - parse_ts(last["ts"]) < dt.timedelta(minutes=10):
        return
    p.append("land_check", pr=pr, outcome=outcome, note="; ".join(reasons)[:300])


KEEP = " A clean update keeps the verdict (same patch-id); re-review only if you resolved conflicts."

ADVICE = {
    "conflicts with base": "rebase onto the base, resolve, re-run the review on the resolution, record a new verdict",
    "no passing verdict": "finish the review loop, then orch verdict",
    "draft": "gh pr ready",
    "behind base": "gh pr update-branch {pr} --repo {repo} (or rebase and restack migrations), then land again",
}


def update_advice(unit, repo):
    """How to bring a PR, or a stack bottom-up, onto the latest base. update-branch merges a PR's own base
    into it, so on a stack top it pulls only the layer below."""
    if len(unit) == 1:
        return f"gh pr update-branch {unit[0]} --repo {repo}"
    return ("update the stack bottom-up, each after the previous finishes: "
            + "; ".join(f"gh pr update-branch {n} --repo {repo}" for n in unit)
            + " (tell the lower layers' owners)")


def merge_unit(p, pr, unit, klass, events, onto=None, stacked=False):
    """Merge a PR (or a stack from its top) after re-checking every layer at its current head.
    `onto`: the lower PR whose branch a `--onto-lower` fold merges into; its ticket has not landed yet.
    `stacked`: the PR is in a GitHub stack, also when it lands alone as a lower layer."""
    repo, states, runs = p.cfg["repo"], pr_state(events), []
    for n in unit:
        v = pr_view(repo, n)
        if patch_id(repo, n) != (states.get(n, {}).get("verdict") or {}).get("patch_id"):
            note_attempt(p, events, pr, "blocked", [f"patch of #{n} changed since its verdict"])
            return 3, f"act: the patch of #{n} changed since its verdict (conflict fix, restack or edit): re-review, then orch verdict"
        failed, pending, r_ = ci_summary(v["statusCheckRollup"])
        runs += r_
        if failed or pending or v["mergeStateStatus"] in ("BEHIND", "DIRTY"):
            return 2, f"#{n} moved since the order was read; land again"
        if unfinished := runs_unfinished(repo, v["headRefOid"]):
            return 2, f"yield: CI has not finished at #{n}'s head ({', '.join(unfinished[:3])}); land again"
    head = v["headRefOid"]
    use_async = stacked or len(unit) > 1
    if not use_async:
        r = run(["gh", "pr", "merge", str(pr), "--repo", repo, "--squash", "--match-head-commit", head], check=False)
    else:
        # A stacked PR refuses `gh pr merge`; merge-async on a layer lands it and every layer below, not above.
        r = run(["gh", "api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/merge-async",
                 "-f", "merge_method=squash", "-f", f"sha={head}"], check=False)
    merged = {}
    for _ in range(90 if use_async else 1):
        merged = {n: pr_view(repo, n) for n in unit}
        if all(x["state"] == "MERGED" for x in merged.values()) or r.returncode != 0:
            break
        time.sleep(10)
    one_push = merged[pr]["state"] == "MERGED"  # a layer merged without its top got its own main CI run
    for n, x in merged.items():
        if x["state"] == "MERGED" and "landed" not in states.get(n, {}):
            p.append("landed", pr=n, sha=(x.get("mergeCommit") or {}).get("oid"),
                     klass=klass if klass != "normal" else None, note=f"stack of {len(unit)}" if len(unit) > 1 else None,
                     stack_top=pr if n != pr and one_push else None, onto=onto)
            close_gate_task(p, n, "completed")
    if merged[pr]["state"] != "MERGED":
        why = (r.stderr or r.stdout).strip()[:300] or "merge-async did not finish in 15 minutes"
        if r.returncode != 0 and TRANSIENT.search(why):
            return 2, f"yield: GitHub did not answer the merge ({why[:160]}); land again"
        p.append("land_failed", pr=pr, note=why)
        if "not up to date" in why or "behind" in why.lower():
            return 3, f"act: the base requires up-to-date branches: {update_advice(unit, repo)}, then land again"
        return 1, f"merge refused: {why}"
    sha = (merged[pr].get("mergeCommit") or {}).get("oid")
    return 0, (f"landed {' '.join('#' + str(n) for n in unit)} (merge commit {sha[:8]}; CI runs {' '.join(runs) or '-'}; read their logs in the report). "
               f"Watch main: gh run list --repo {repo} --commit {sha} --json databaseId,workflowName,status, "
               f"then gh run watch <id> --repo {repo} --exit-status and orch record {p.slug} main_green|main_red --pr {pr} --sha {sha}"
               + base_ci_hint(repo, merged[pr].get("baseRefName")))


def base_ci_hint(repo, base):
    """A push-triggered CI often covers only the default branch, so a landing on a feature base starts no run."""
    if not base or base in ("main", "master"):
        return ""
    return (f". The base is {base}: if gh run list shows no run for the merge commit, its CI does not run on push"
            f" there; start it with gh workflow run <ci workflow file> --repo {repo} --ref {base} and watch that run")


def attempt(p, pr, klass, onto_lower=False):
    """One pass: returns (exit_code, message)."""
    events, cfg, repo = p.events(), p.cfg, p.cfg["repo"]
    st = pr_state(events).get(pr, {})
    if "landed" in st:
        return 0, f"#{pr} already landed as {st['landed'].get('sha', '')[:8]}"
    if stopped(events):
        return 1, "STOP line active: nothing lands"
    if cfg["merge_policy"] == "human-gate" and "approved" not in st and "gate_opened" in st:
        resolution = gate_resolution(cfg, events, pr)
        if resolution == "land":
            p.append("approved", pr=pr, note="orca gate resolved: land")
            events, st = p.events(), pr_state(p.events()).get(pr, {})
        elif resolution:
            close_gate_task(p, pr, "failed")
            return 1, f"human-gate: the user resolved the gate as '{resolution}'"
    main = main_state(events)
    if main == "red" and klass != "main-fix":
        return 1, "safety stop: main is red; only the repair lands (--class main-fix)"
    rows = open_rows(repo)
    stacks = repo_stacks(repo)
    order = land_order(events, rows, cfg, dt.datetime.now(dt.timezone.utc), stacks, orca_deps(cfg["run"]))
    stacked = any(pr in members for members in stacks.values())
    me = next((e for e in order if e["pr"] == pr), None)
    tracked = pr_state(events)  # an integration branch's own PR (feature → main) is never landed through orch
    below = next((r for r in rows if me and not me["stack"] and r.get("headRefName") == me.get("base")
                  and r["number"] in tracked), None)
    lower = None if onto_lower else below
    onto = below["number"] if onto_lower and below else None
    if lower:
        # Based on another open PR's branch without being a GitHub stack: a squash would land in that branch.
        return 3, (f"act: #{pr} is based on #{lower['number']}'s branch {me['base']}, not a GitHub stack, so a merge would"
                   f" land in that branch. Land #{lower['number']} first, then gh pr edit {pr} --repo {repo} --base"
                   f" {lower.get('baseRefName')} and gh pr update-branch {pr} --repo {repo}, and land again."
                   f" To fold it into #{lower['number']} on purpose (top-down), land again with --onto-lower")
    if me is None or me["state"] == "gone":
        v = pr_view(repo, pr)
        if v["state"] == "MERGED":
            sha = (v.get("mergeCommit") or {}).get("oid")
            st = pr_state(p.events()).get(pr, {})  # the top's land call may have recorded this layer since we read
            if "landed" in st:  # a stack layer lands with its top, whose land call recorded it
                return 0, (f"#{pr} already landed as {(st['landed'].get('sha') or '')[:8]} ({st['landed'].get('note') or 'recorded'});"
                           " main CI ran once on the stack top's commit, and the top's owner records it")
            p.append("landed", pr=pr, sha=sha, note="merged outside land")
            close_gate_task(p, pr, "completed")
            return 0, f"#{pr} was merged outside land; recorded"
        if v["state"] == "CLOSED":
            close_gate_task(p, pr, "failed")
        return 1, f"#{pr} is {v['state']}"
    if on_hold(events, pr):
        return 1, f"#{pr} is on hold (record parked); `orch record {p.slug} admitted --pr {pr}` releases it"
    if me["state"] == "waiting":
        if st.get("yield", {}).get("note") != me["reasons"][0]:
            p.append("yield", pr=pr, note=me["reasons"][0])
        return 2, f"yield: {me['reasons'][0]}"
    if me["state"] == "blocked":
        note_attempt(p, events, pr, "blocked", me["reasons"])
        todo = [ADVICE.get(r, r).format(pr=pr, repo=repo) for r in me["reasons"]]
        return 3, "act: " + " | ".join(todo)
    unit = (me["stack"] or [pr])[: (me["stack"] or [pr]).index(pr) + 1]
    missing = []
    for n in unit:
        problems, notes = evidence_problems(cfg, repo, n)
        missing += problems
        for line in notes:
            print(line, flush=True)
    if missing:
        note_attempt(p, events, pr, "blocked", missing)
        return 3, "act: " + " | ".join(missing)
    touched = is_exclusive(cfg, [f for n in unit for f in pr_files(repo, n)])
    if bool(touched) != bool((st.get("lane") or {}).get("exclusive")):
        p.append("lane", pr=pr, exclusive=bool(touched), note=", ".join(touched[:5]) or None)
        me["exclusive"] = bool(touched) or me["klass"] == "gate"
    if me["exclusive"]:
        # PRs ahead whose lane is not known yet (they have not called land) are classified now.
        for e in order[:order.index(me)]:
            if e.get("base") == me["base"] and e["state"] in ("ready", "catching_up") and not e["exclusive"] \
                    and "lane" not in pr_state(events).get(e["pr"], {}):
                hit = is_exclusive(cfg, pr_files(repo, e["pr"]))
                p.append("lane", pr=e["pr"], exclusive=bool(hit), note=", ".join(hit[:5]) or None)
                e["exclusive"] = bool(hit)
        lane = ExclusiveLock(p, me["base"] or "main")
        ahead = exclusive_turn(order, pr)
        held = lane.holder()
        if held and (held.get("pr"), held.get("program")) != (pr, p.slug):
            held = lane.break_if_closed(held)
        if held and (held.get("pr"), held.get("program")) != (pr, p.slug):
            return 2, f"yield: the exclusive lane of {me['base']} is held by #{held.get('pr')} ({held.get('program')})"
        if not held and ahead:
            return 2, f"yield: #{ahead['pr']} ({ahead['ticket']}) takes the exclusive lane first"
        if not lane.take(pr):
            return 2, "yield: another lander just took the exclusive lane"
        if me["state"] == "catching_up":
            note_attempt(p, events, pr, "catching_up", me["reasons"])
            return 3, ("act: you hold the exclusive lane; bring the branch onto the latest base (restack migrations),"
                       f" let CI run, land again — {'; '.join(me['reasons'])}." + KEEP)
        if compare(repo, me["base"] or "main", pr_view(repo, pr)["headRefOid"]).get("behind_by"):
            return 3, (f"act: you hold the exclusive lane and it lands on the latest base only: {update_advice(unit, repo)},"
                       " let CI run, land again." + KEEP)
        code, msg = merge_unit(p, pr, unit, klass, events, onto, stacked)
        if code == 0:
            lane.release(pr)
        return code, msg
    if me["state"] == "catching_up":
        note_attempt(p, events, pr, "catching_up", me["reasons"])
        if "behind base" in me["reasons"]:
            return 3, f"act: {update_advice(unit, repo)}, then land again"
        return 2, f"not yet: {'; '.join(me['reasons'])}"
    base = me["base"] or "main"
    overlap = base_overlap(repo, base, compare(repo, base, pr_view(repo, pr)["headRefOid"]))
    if overlap:
        note_attempt(p, events, pr, "act", [f"base changed {', '.join(overlap[:5])}"])
        return 3, (f"act: since your branch point the base changed files you also change ({', '.join(overlap[:5])}):"
                   f" {update_advice(unit, repo)}, check your contract and test assumptions still hold,"
                   " let CI run, land again." + KEEP)
    return merge_unit(p, pr, unit, klass, events, onto, stacked)


def cmd_land(argv):
    p = Program(argv[0])
    pr, klass = int(opt(argv, "--pr")), opt(argv, "--class")
    if klass and klass not in KLASS:
        sys.exit(f"--class is one of {', '.join(KLASS)}")
    st = pr_state(p.events()).get(pr, {})
    if "ready" not in st or (klass and klass != st["ready"].get("klass", "normal")):
        p.append("ready", pr=pr, ticket=opt(argv, "--ticket"), klass=klass)
    klass = klass or next((e["klass"] for e in reversed(p.events()) if e.get("pr") == pr and e.get("klass")), "normal")
    deadline = time.monotonic() + 60 * float(opt(argv, "--wait-minutes", "0"))
    while True:
        try:
            code, msg = attempt(p, pr, klass, "--onto-lower" in argv)
        except SystemExit as e:
            if not TRANSIENT.search(str(e.code)):
                raise
            code, msg = 2, f"yield: GitHub or Orca did not answer ({str(e.code)[:160]}); land again"
        print(msg, flush=True)
        if code != 2 or time.monotonic() >= deadline:
            sys.exit(code)
        time.sleep(60)


def cmd_land_check(argv):
    p = Program(argv[0])
    repo, pr = p.cfg["repo"], int(opt(argv, "--pr"))
    events = p.events()
    st = pr_state(events).get(pr, {})
    v = pr_view(repo, pr)
    problems = []
    main = main_state(events)
    if main == "red" and "--main-fix" not in argv:
        problems.append("safety stop: main is red (only the repairing PR lands, with land --class main-fix)")
    if stopped(events):
        problems.append("STOP line active")
    if on_hold(events, pr):
        problems.append(f"on hold (record parked); `orch record {p.slug} admitted --pr {pr}` releases it")
    if main == "pending":
        print("note: main CI for the last landing has not reported yet")
    if v["state"] != "OPEN" or v["isDraft"]:
        problems.append(f"PR is {v['state']}{' draft' if v['isDraft'] else ''}")
    verdict = st.get("verdict")
    if not verdict or verdict.get("result") != "pass":
        problems.append("no passing verdict recorded (orch verdict)")
    else:
        cur = patch_id(repo, pr)
        if cur != verdict.get("patch_id"):
            problems.append(f"patch changed since verdict ({verdict.get('patch_id', '')[:10]} -> {cur[:10]}): re-review")
        elif v["headRefOid"] != verdict.get("sha"):
            print(f"note: head moved {verdict['sha'][:8]} -> {v['headRefOid'][:8]} with the same patch-id; verdict holds, CI must pass at the new head")
    if p.cfg["merge_policy"] == "human-gate" and "approved" not in st:
        resolution = gate_resolution(p.cfg, events, pr)
        if resolution == "land":
            p.append("approved", pr=pr, note="orca gate resolved: land")
        elif resolution:
            close_gate_task(p, pr, "failed")
            problems.append(f"human-gate: the user resolved the gate as '{resolution}'")
        elif any(e["ev"] == "gate_opened" and e.get("pr") == pr for e in events):
            problems.append("human-gate: gate open in Orca, waiting for the user")
        else:
            problems.append(f"human-gate: open the gate first (orch gate {p.slug} --pr {pr})")
    mss = v["mergeStateStatus"]
    if mss == "DIRTY":
        problems.append("conflicts with base: fix task in the PR's worktree")
    elif mss == "BEHIND":
        problems.append(f"behind base: gh pr update-branch {pr} --repo {repo}, then wait for CI")
    failed, pending, runs = ci_summary(v["statusCheckRollup"])
    if not failed and not pending:
        pending = runs_unfinished(repo, v["headRefOid"])
    if failed:
        problems.append("CI failed: " + ", ".join(failed))
    if pending:
        problems.append("CI pending: " + ", ".join(pending[:5]))
    missing, notes = evidence_problems(p.cfg, repo, pr)
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
    print(f"  gh run watch <databaseId> --repo {repo} --exit-status")
    if hint := base_ci_hint(repo, v.get("baseRefName")):
        print(" " + hint.lstrip(". "))


def cmd_gate(argv):
    p = Program(argv[0])
    pr = int(opt(argv, "--pr"))
    if p.cfg["merge_policy"] != "human-gate":
        sys.exit("gates are for merge_policy human-gate; autonomous programs land with orch land")
    if any(e["ev"] == "gate_opened" and e.get("pr") == pr for e in p.events()) and gate_resolution(p.cfg, p.events(), pr) is None:
        gid = next(e.get("gate") for e in reversed(p.events()) if e["ev"] == "gate_opened" and e.get("pr") == pr)
        sys.exit(f"a gate for #{pr} is already open; the user resolves it in Orca"
                 f" (or: orca orchestration gate-resolve --id {gid} --resolution land|hold)")
    v = pr_view(p.cfg["repo"], pr)
    spec = (f"Land PR #{pr} ({v['url']}) with orch land once the user resolves the gate. "
            "Coordinator-owned; no worker is dispatched for this Task.")
    args = ["orchestration", "task-create", "--run", p.cfg["run"], "--spec", spec, "--task-title", f"{GATE_TASK}{pr}"]
    if opt(argv, "--parent"):
        args += ["--parent", opt(argv, "--parent")]
    task = orca_json(*args)
    task_id = (task.get("task") or task).get("id")
    gate = orca_json("orchestration", "gate-create", "--task", task_id,
                     "--question", f"Land PR #{pr}: {v['title']}?", "--options", json.dumps(["land", "hold"]))
    gate_id = (gate.get("gate") or gate).get("id")
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
        # The window is the coordinator's judgment: in a repo shared with other work, read what it holds.
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


def close_out(msgs, workers, worktrees):
    """What to do with each worker_done's card, printed beside the message: after a compaction a coordinator kept
    releasing workers but dropped `worktree rm`, and eight cards stayed open."""
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
        elif payload.get("outcome") != "succeeded":
            out += [f"CLOSE OUT {d} ({payload.get('outcome') or 'no outcome'}): release it, then retry on its card"
                    f" (worker-start --retry-of {d} --task {task[d]} --worktree path:{shlex.quote(p)} --agent <agent> [--model <id>])"
                    " or remove the card", release]
        else:
            out += [f"CLOSE OUT {d}: release it, close its terminals and remove its card (checks and --run-hooks in end-session §4),"
                    " unless its next task starts there", release, f"  orca worktree rm --worktree path:{shlex.quote(p)}"]
    return out


def taken_over(w):
    """Orca's worker-release never closes a terminal someone typed into, a coordinator's `terminal send` included."""
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


def settle_sweep(workers, worktrees, skip=(), holds=card_holds, asked=None, now=None):
    path = {w["id"]: w.get("path") for w in worktrees if not w.get("isMainWorktree")}
    asked = {} if asked is None else asked
    seen, out, kept = {}, [], []
    for w in workers:
        d, res, taken = w.get("dispatchId"), w.get("resource") or {}, taken_over(w)
        if not d or (not taken and (w.get("terminalState") not in ("reclaimable", "release_unknown") or d in skip)):
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
                    f"  orca terminal send --terminal {w.get('agentTerminalHandle')} --text {shlex.quote(SELF_CHECK)} --enter"]
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


def screen_prompt(lines, activity=None):
    tail = lines[-25:]
    for rx, kind in PROMPTS:
        if rx.search("\n".join(tail)):
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


def stuck_sweep(workers, slug, seen, read=read_screen, only_new=False):
    """A worker's terminal has no human watching it: a menu or permission prompt there blocked a program for hours."""
    out, now = [], {}
    for w in workers:
        d, h = w.get("dispatchId"), w.get("agentTerminalHandle")
        if w.get("terminalState") != "active" or not d or not h:
            continue
        try:
            lines = read(h)
        except (SystemExit, Exception):
            continue
        hit = screen_prompt(lines, ((w.get("projection") or {}).get("stage") or {}).get("activity"))
        if not hit:
            continue
        kind, question = hit
        key = hashlib.sha1("\n".join(prompt_block(lines[-25:])).encode()).hexdigest()[:12]
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
        lines = stuck_sweep(run_workers(run_id), p.slug, seen, read=read_screen, only_new=only_new)
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


def review_sweep(events, view, active, unresolved, slug, gate=None):
    """A review that outlasts its worker's session leaves a ready PR nobody lands."""
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
        failed, pending, _ = ci_summary(r.get("statusCheckRollup"))
        mss = r.get("mergeStateStatus")
        verdict = (st.get("verdict") or {}).get("result")
        why = [w for w, bad in (
            ("no ticket, so no worker is ruled out", not t),
            ("no passing verdict", verdict != "pass"),
            ("draft", r.get("isDraft")),
            ("on hold", on_hold(events, n) or any(HOLD.search(l.get("name") or "") for l in r.get("labels") or [])),
            ((r.get("reviewDecision") or "no review").lower().replace("_", " "), r.get("reviewDecision") != "APPROVED"),
            (f"merge state {mss}" + {"DIRTY": ", dispatch a fix", "BEHIND": f", gh pr update-branch {n}"}.get(mss, ""), mss != "CLEAN"),
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
    q = ("query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n)"
         "{reviewThreads(first:100){nodes{isResolved} pageInfo{hasNextPage}}}}}")
    res = gh_json("api", "graphql", "-f", f"query={q}", "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={n}")
    threads = res["data"]["repository"]["pullRequest"]["reviewThreads"]
    return sum(not t["isResolved"] for t in threads["nodes"]) or int(threads["pageInfo"]["hasNextPage"])


def print_reviews(p):
    try:
        events = p.events()
        if not ready_prs(events):
            return
        spawned = {d: e["ticket"] for e in events if e["ev"] == "spawned" and e.get("ticket")
                   for d in re.findall(r"ctx_[0-9a-f]+", str(e.get("note") or ""))}
        active = {spawned.get(w.get("dispatchId")) for w in run_workers(p.cfg["run"]) if w.get("terminalState") == "active"}
        repo = p.cfg["repo"]
        fields = "state,reviewDecision,mergeStateStatus,isDraft,labels,statusCheckRollup,headRefOid"
        lines = review_sweep(events, lambda n: gh_json("pr", "view", str(n), "--repo", repo, "--json", fields),
                             active - {None}, lambda n: unresolved_threads(repo, n), p.slug,
                             (lambda n: gate_resolution(p.cfg, events, n)) if p.cfg.get("merge_policy") == "human-gate" else None)
    except (SystemExit, Exception) as e:  # the batch and its ack line matter more than the sweep
        lines = [f"REVIEWS: sweep failed ({type(e).__name__}: {e}); orch status lists the ready PRs"]
    if lines:
        print("\n".join(lines), flush=True)


def cmd_wait(argv):
    p = Program(argv[0])
    timeout = opt(argv, "--timeout-ms", "540000")
    rounds = int(opt(argv, "--rounds", "3"))
    run_id = p.cfg["run"]
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    workers = run_workers(run_id) if me else []
    if me and me in {w.get("agentTerminalHandle") for w in workers}:
        sys.exit("orch wait reads and acks the coordinator's Run inbox, and this terminal is one of the Run's"
                 " workers. Wake on your own mailbox (check --terminal $ORCA_TERMINAL_HANDLE --wait) or a"
                 " background command such as `gh run watch <id>`.")
    # STALLED comes from the collector; a coordinator that only loops `wait` would otherwise keep an old server.
    if (line := ensure_dash()) and not line.startswith("dashboard: http"):
        print(line, flush=True)
    for i in range(rounds):
        res = orca_json("orchestration", "check", "--run", run_id, "--wait", "--types", ",".join(sorted(ACTIONABLE)),
                        "--timeout-ms", timeout)
        did, msgs = batch(res)
        if not did:
            # A typed wait never wakes for heartbeats, so they pile up in the FIFO; pull that batch.
            did, msgs = batch(orca_json("orchestration", "check", "--run", run_id))
        work = [m for m in msgs if m.get("type") != "heartbeat" and not revoked_echo(m)]
        for m in msgs:
            if revoked_echo(m):
                print(f"ignored: Orca refused a {m.get('type')} from released dispatch {revoked_echo(m)}", flush=True)
        if work:
            print("\n".join(map(inbox_line, work)))
            closed = {payload_of(m).get("dispatchId") for m in work if m.get("type") == "worker_done"}
            if closed:
                try:
                    lines = close_out(work, run_workers(run_id), orca_json("worktree", "list").get("worktrees", []))
                except SystemExit as e:  # the batch and its ack line matter more than the hint
                    lines = [f"CLOSE OUT: could not read the cards ({e}); `orch status` lists them"]
                print("\n".join(lines))
            print_sweep(run_id, p.dir / "self-check.json", closed)
            print_stuck(p, run_id)
            print_reviews(p)
            print(f"ACTIONABLE delivery={did} ({len(work)} of {len(msgs)}): process every message, then "
                  f"orca orchestration check --run {run_id} --ack {did} --json")
            return
        if did:
            orca_json("orchestration", "check", "--run", run_id, "--ack", did)
        print(f"wait {i + 1}/{rounds}: nothing actionable ({len(msgs)} heartbeat(s) or refusals acked)", flush=True)
        if print_stuck(p, run_id, only_new=True):
            print_sweep(run_id, p.dir / "self-check.json")
            print("STUCK: a worker waits on a prompt nobody sees; answer or escalate it now", flush=True)
            return
        if leftovers := new_leftovers(p.dir):
            print("\n".join(leftovers), flush=True)
        if stalled := new_stalls(p.dir):
            print("\n".join(stalled), flush=True)
            print("STALLED: a worker stopped with its ticket open and nothing will wake it; wake it now", flush=True)
            return
    print_sweep(run_id, p.dir / "self-check.json")
    print_stuck(p, run_id)
    print_reviews(p)
    moves = next_moves(run_workers(run_id))
    print("\n".join([f"EMPTY x{rounds}: " + (f"{len(moves)} live worker(s), each with its move:" if moves else "no live worker")]
                    + moves))


def inbox_line(m):
    body = (m.get("body") or "").replace("\n", " ")
    return (f"INBOX id={m.get('id')} type={m.get('type')} from={m.get('from_handle')} "
            f"subject={m.get('subject')} payload={str(m.get('payload'))[:200]} body={body[:800]}")


def cmd_inbox(argv):
    """The inbox wait of a session without a program in one call per wake: the raw peek, take and ack replies are
    whole JSON batches, and a top-level session read each of them in full."""
    acked = opt(argv, "--ack")
    try:
        did, msgs = batch(orca_json("orchestration", "check", *(["--ack", acked] if acked else [])))
    except SystemExit as e:  # a stale delivery id must not cost the wait the caller is about to rely on
        if not acked:
            raise
        print(f"ack {acked} failed ({e}); taking the inbox without it", flush=True)
        did, msgs = batch(orca_json("orchestration", "check"))
    deadline, skipped = time.monotonic() + int(opt(argv, "--timeout-ms", "3300000")) / 1000, 0
    work = [m for m in msgs if m.get("type") != "heartbeat" and not revoked_echo(m)]
    # Like orch wait: a batch of only heartbeats or refusals is acked and the wait goes on, until the deadline.
    while "--wait" in argv and not work:
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0:
            break
        if did:
            skipped += len(msgs)
            did, msgs = batch(orca_json("orchestration", "check", "--ack", did))
        else:
            orca_json("orchestration", "check", "--peek", "--wait", "--timeout-ms", str(left))
            did, msgs = batch(orca_json("orchestration", "check"))
            if not msgs:
                break
        work = [m for m in msgs if m.get("type") != "heartbeat" and not revoked_echo(m)]
    if work:
        print("\n".join(map(inbox_line, work)))
    if did:
        print(f"{len(work)} of {len(msgs)} to act on (the rest heartbeats or refusals); then: orch inbox --ack {did} --wait")
    else:
        print("inbox empty" + (f" ({skipped} heartbeat(s) or refusals acked)" if skipped else "") + "; wait again: orch inbox --wait")


def next_moves(workers):
    """The move for each live worker after a wait that stayed empty, read from Orca's projection here so the
    coordinator does not list the workers and work it out again."""
    out = []
    for w in workers:
        pj = w.get("projection") or {}
        if pj.get("outcome") != "in_progress":
            continue
        d, h, act = w["dispatchId"], w.get("agentTerminalHandle"), pj.get("nextAction") or {}
        activity = (pj.get("stage") or {}).get("activity") or "unknown"
        if act.get("kind") not in (None, "none"):
            out.append(f"  {d} {act['kind']}: {' '.join(act.get('argv') or [])}")
        elif not h or (pj.get("liveness") or {}).get("verdict") == "exited":
            out.append(f"  {d} no terminal: worker-stop or worker-abandon (recovery-and-cleanup.md)")
        elif activity == "idle":  # the turn ended without worker_done; send alone does not wake it (SKILL.md, terminal send)
            out.append(f"  {d} idle: orchestration send --to dispatch:{d}, then nudge {h}")
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


def register_stacks(before, owner, pr):
    """A stack left running after verification held gigabytes until someone downed it by hand: the ones this run
    created go to the reap ledger. Only a project with a config file in this worktree: the other slot's card may
    have started one meanwhile."""
    after = compose_projects()
    if before is None or after is None:
        return
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    wt = os.path.realpath(top.stdout.strip() if top.returncode == 0 else os.getcwd())
    for name in sorted(set(after) - set(before)):
        if not any(os.path.realpath(f).startswith(wt + os.sep) for f in after[name]):
            print(f"heavy: compose 스택 {name} 은 이 worktree 밖에서 떠서 등록하지 않음", file=sys.stderr)
            continue
        r = subprocess.run([sys.executable, str(REAP), "ledger", "add", "--kind", "compose-stack", "--id", name,
                            "--worktree", wt, "--pid", str(os.getpid()), "--by", "orch heavy",
                            *(["--run", owner] if owner != "-" else []), *(["--pr", pr] if pr else [])],
                           capture_output=True, text=True)
        print(f"heavy: compose 스택 {name} 을 reap ledger 에 등록했다. 검증이 끝나면 `docker compose -p {name} down`(-v 금지)"
              if r.returncode == 0 else f"heavy: compose 스택 {name} 등록 실패: {r.stderr.strip()}", file=sys.stderr)


def cmd_heavy(argv):
    """Docker stacks from parallel cards starved the whole machine (Orca UI included), so heavy local work
    queues on slots shared by every program on this machine, like CI shares its runners."""
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        sys.exit("heavy <slug|-> [--wait-minutes 60] [--pr owner/repo#N] -- <command…>")
    cut = argv.index("--")
    cmd = argv[cut + 1:]
    cfg, owner = ({}, "-") if argv[0] == "-" else (Program(argv[0]).cfg, argv[0])
    slots = HOME / "_heavy"
    slots.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 60 * float(opt(argv[:cut], "--wait-minutes", "60"))
    while True:
        for i in range(int(knob(cfg, "heavy_slots"))):
            path = slots / f"slot-{i}"
            held = json.loads(path.read_text() or "{}") if path.exists() else {}
            if held and not pid_alive(held.get("pid", 0)):
                path.unlink(missing_ok=True)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                continue
            os.write(fd, json.dumps({"pid": os.getpid(), "program": owner, "cmd": " ".join(cmd)[:200], "ts": now()}).encode())
            os.close(fd)
            before = compose_projects()
            try:
                sys.exit(subprocess.run(cmd).returncode)
            finally:
                path.unlink(missing_ok=True)
                try:
                    register_stacks(before, owner, opt(argv[:cut], "--pr"))
                except (OSError, subprocess.SubprocessError) as e:
                    # The command's exit code is the result; a failed registration is only reported.
                    print(f"heavy: compose 스택 등록 실패: {e}", file=sys.stderr)
        if time.monotonic() >= deadline:
            holders = [json.loads(f.read_text() or "{}") for f in sorted(slots.glob("slot-*"))]
            sys.exit(f"heavy: every slot busy after waiting: {json.dumps(holders, ensure_ascii=False)}")
        time.sleep(15)


def cmd_decide(argv):
    import fleet  # the store lives in the fleet state directory, beside the page that shows it
    sub, rest = argv[0], argv[1:]
    try:
        if sub == "add" and opt(rest, "--title"):
            rec, body = opt(rest, "--recommend"), opt(rest, "--body-file")
            if rec is not None and not rec.isdigit():
                sys.exit("--recommend is an option number")
            d = fleet.decision_add(opt(rest, "--title"), pathlib.Path(body).read_text() if body else "",
                                   [rest[i + 1] for i, a in enumerate(rest[:-1]) if a == "--option"],
                                   int(rec) if rec else None, opt(rest, "--link"), os.environ.get("ORCA_TERMINAL_HANDLE"))
            print(d["id"])
        elif sub == "list":
            for d in fleet.open_decisions():
                print(f"{d['id']}  {d['created_at']}  {d['title']}")
                for i, o in enumerate(d["options"], 1):
                    mark = " (recommended)" if i == d.get("recommend") else ""
                    print(f"    {i}. {o['label']}{mark}" + (f" — {o['description']}" if o["description"] else ""))
            for d in fleet.pending_relays():
                print(f"{d['id']}  answered on the dashboard, not relayed yet: {d.get('answer')}  ({d['title']})")
        elif sub in ("done", "drop") and rest:
            d = fleet.decision_close(rest[0], "done" if sub == "done" else "dropped", opt(rest, "--answer"))
            print(f"{d['id']} {d['status']}")
        else:
            sys.exit("usage: orch decide add --title T [--body-file F] [--option 'label::description']... [--recommend N] "
                     "[--link URL] | list | done <id> [--answer TEXT] | drop <id>")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


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
            stages.finish(rid, drop=sub == "drop")
        elif sub == "show":
            t = stages.table()
            if "--json" in rest:
                print(json.dumps(t, ensure_ascii=False, indent=1))
            else:
                print((stages.render_telegram if "--telegram" in rest else stages.render_md)(t) or "기록된 단계 없음")
        else:
            sys.exit("usage: orch stage add <id> --title T [--pr owner/repo#N] [--parent ID] [--group] | set <id> --col merge|dev|prod "
                     "--mark ok|fail|partial|checking|na [--evidence TEXT] [--by WHO] | env [--prod V] [--dev V] | "
                     "next [LINE]... | done <id> | drop <id> | show [--md|--telegram|--json]")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


def cmd_board(argv):
    import board
    sub, rest = argv[0], argv[1:]
    rid = rest[0] if rest and not rest[0].startswith("--") else None
    try:
        if sub == "lead" and rest[:1] == ["add"] and len(rest) > 1:
            board.lead_add(rest[1], opt(rest, "--handle"), opt(rest, "--repo"), opt(rest, "--run"), opt(rest, "--stale-min"),
                           opt(rest, "--program"))
        elif sub == "lead" and rest[:1] == ["close"] and len(rest) > 1:
            board.lead_close(rest[1])
        elif sub == "set" and rid:
            board.set_row(rid, **{f: opt(rest, f"--{f}") for f in
                                  ("kind", "title", "repo", "program", "parent", "status", "progress", "next", "owner", "pr")})
        elif sub == "drop" and rid:
            board.drop(rid)
        elif sub == "doing" and rest[:1] == ["add"] and len(rest) > 1:
            board.doing_add(rest[1], opt(rest, "--owner"), opt(rest, "--next"), "--waiting" in rest)
        elif sub == "doing" and rest[:1] == ["drop"] and len(rest) > 1:
            board.doing_drop(int(rest[1]))
        elif sub == "later" and rest[:1] == ["add"] and len(rest) > 1:
            board.later_add(rest[1], opt(rest, "--trigger"), opt(rest, "--due"))
        elif sub == "later" and rest[:1] == ["drop"] and len(rest) > 1:
            board.later_drop(int(rest[1]))
        elif sub == "todo":
            board.set_todo(rest)
        elif sub == "show":
            print(json.dumps(board.read(), ensure_ascii=False, indent=1))
        else:
            sys.exit("usage: orch board lead add <name> --handle H --repo R [--program P] [--run RUN] [--stale-min N] | lead close <name> | "
                     "set <id> [--kind K] [--title T] [--repo R] [--program P] [--parent ID] [--status S] [--progress N/M] "
                     "[--next T] [--owner W] [--pr owner/repo#N] | drop <id> | doing add <title> --owner W --next E [--waiting] | doing drop <n> | later add <title> --trigger T [--due D] | "
                     "later drop <n> | todo [LINE]... | show")
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    if sub in ("lead", "set") and (warning := board.unnamed_warning()):
        print(warning, file=sys.stderr)


def cmd_brief(argv):
    import board
    if argv[:1] != ["render"]:
        sys.exit("usage: orch brief render [--periodic] [--scope S] [--telegram] [--headline T] [--decision T]... "
                 "[--insight T] [--skipped T] [--now ISO8601] [--commit] [--json] [--no-fill] [--no-refresh]")
    now = opt(argv, "--now")
    scope = opt(argv, "--scope", "top-level")
    fill = "--no-fill" not in argv  # --fill, the old opt-in, is now the default and still accepted
    try:
        out = board.render(scope, "telegram" if "--telegram" in argv else "terminal", now and fleet_parse(now),
                           opt(argv, "--headline"), opt(argv, "--skipped"), fill, periodic="--periodic" in argv,
                           decisions=[argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--decision"],
                           insight=opt(argv, "--insight"),
                           # --now replays a past moment, so the fleet as it is now does not belong in it
                           fresh=not now and "--no-refresh" not in argv)
    except ValueError as e:
        sys.exit(str(e))
    print(json.dumps(out, ensure_ascii=False, indent=1) if "--json" in argv else out["text"])
    if "--commit" in argv:
        board.commit(scope, out["snapshot"])
    if not out["width_ok"]:
        sys.exit("a table is wider than 78 columns")


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
            rows = sent.page(fleet.fetch_sent(), int(opt(rest, "--days", "1")))["rows"]
            if "--json" in rest:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            for r in [] if "--json" in rest else rows:
                print(f"{r['ts']}  {r.get('from') or '?'} → {r.get('to') or '?'}  [{r['source']}] {(r.get('subject') or r['text'] or '?').splitlines()[0][:100]}")
        else:
            sys.exit(usage)
    except (ValueError, OSError) as e:
        sys.exit(f"{e}\n{usage}")


def cmd_req(argv):
    import itertools, owner_requests as req
    sub, rest = argv[0], argv[1:]
    ids = list(itertools.takewhile(lambda a: not a.startswith("--"), rest))
    note = opt(rest, "--reason" if sub == "drop" else "--evidence")
    try:
        if sub == "list":
            now = req.fleet.utcnow()
            qs = [q for q in req.fold(req.read()).values()
                  if "--all" in rest or req.visible(q, now)]
            if "--json" in rest:
                print(json.dumps(qs, ensure_ascii=False, indent=1))
                return
            for q in qs:
                where = " → " + ", ".join(q["links"]) if q["links"] else ""
                state = (" [expired]" if req.expired(q, now) else "" if q["status"] == "open"
                         else f" [{q['status']}: {q.get('note') or ''}]")
                print(f"{q['id']}  {req.ago(req.age_min(q, now))}  {q['text']}{where}{state}")
            print(req.line(req.summary(now)) or "열린 요청 없음")
        elif sub == "link" and len(ids) >= 2:
            *rids, where = ids
            for rid in rids:
                req.close(rid, "link", to=where)
                if "--done" in rest:
                    req.close(rid, "done", note=note or f"settled at {where}")
            print(f"{' '.join(rids)} → {where}" + (" (done)" if "--done" in rest else ""))
        elif sub in ("done", "drop") and ids and note:
            for rid in ids:
                req.close(rid, sub, note=note)
            print(f"{sub}: {' '.join(ids)}")
        else:
            sys.exit("usage: orch req list [--all] [--json] | link <id>... <WHERE> [--done [--evidence T]] | done <id>... --evidence T | "
                     "drop <id>... --reason T")
    except (ValueError, OSError) as e:
        sys.exit(str(e))


COMMANDS = {"init": cmd_init, "set": cmd_set, "status": cmd_status, "record": cmd_record, "verdict": cmd_verdict,
            "gate": cmd_gate, "dep": cmd_dep, "queue": cmd_queue, "land": cmd_land, "land-check": cmd_land_check,
            "landed": cmd_landed, "heavy": cmd_heavy, "wait": cmd_wait, "inbox": cmd_inbox, "backfill": cmd_backfill, "decide": cmd_decide,
            "stage": cmd_stage, "board": cmd_board, "brief": cmd_brief, "hold": cmd_hold, "sent": cmd_sent, "req": cmd_req, "release": lambda argv: cmd_hold(argv, release=True),
            "watch-cards": lambda argv: __import__("card_watch").main(argv)}

if __name__ == "__main__":
    if len(sys.argv) < 3 - (sys.argv[1:2] in (["watch-cards"], ["inbox"])) or sys.argv[1] not in COMMANDS or sys.argv[1] != "watch-cards" and {"-h", "--help"} & set(sys.argv[2:(sys.argv + ["--"]).index("--")]):
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
