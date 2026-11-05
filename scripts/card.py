#!/usr/bin/env python3
"""Create an Orca card from a brief file, with the lines the card's first prompt must carry written out.

Usage:
  skills-sync card <brief.md> --repo <selector> --name <name> [--display-name <title>] [--comment <text>]
                   [--reference <ticket URL or Jira key>]... [--parent-worktree <selector> | --no-parent] [--card]
                   [--role impl|verify|review] [--supersede] [--no-forbidden] [--agent claude|codex] [--model <id>]
  skills-sync handoff <TICKET> ...   (handoff() below)

The session's model: --model when the human named one, else `orchestrate.models.lead` for a lead's brief, else the
brief's `KIND: design|edit|lookup — <reason>` row (opus | sonnet | haiku; another kind or no reason is exit 2), else
`orchestrate.models.worker` (agent-skills.json). With none, no flag goes out, the card runs on the account's default
and one warning line says so. A Codex card takes only --model: those names are Claude models. The `card` object and
the cards.jsonl row carry model and model_from (what picked it).

--agent picks the card's CLI (default claude). Every brief check runs the same for either. Codex is a supervised
worker only: its close-setup gets no agent handle, since that reads Claude's screen and worker-start's receipt says it
is ready. A Codex /goal card, handoff or lead is refused (exit 2) with its reason, never switched to claude.

Before the start: the brief file's FORBIDDEN section is set to the current paragraph of orchestrate references/brief.md
(the worker template's FORBIDDEN through "run it again."), between `<!-- FORBIDDEN:begin -->` and
`<!-- FORBIDDEN:end -->`: replaced there, never appended; with no markers an old copy opening the row is replaced, or
the section goes first in the row, or a new row before REPORT. The row's other lines (a repository's data-sharing
restriction, the unit's bans) stay. A lead's brief is left alone; --no-forbidden leaves any brief alone and logs that
with `orch sent add`. Then, with a --reference, one card per key (repo, reference, role): under a lock, a live Orca
card of the same repo whose linkedItems, or this helper's $AGENT_SKILLS_STATE/cards.jsonl, carry the reference with
the same --role (default impl) stops this one (exit 2, the cards listed). --supersede starts it anyway, tells the old
card `superseded by <new>: stop and close by end-session §8` (sync.try_send's idle checks), logs it with `orch sent
add --kind supersede` and records it in cards.jsonl. An unread card list is a warning, not a stop.
After the start: `skills-sync close-setup <path> --agent <handle>` runs in the background, its log in
$AGENT_SKILLS_STATE/close-setup/<name>.log ending in `result: ok|failed` (a trust or permission dialog, or no start,
is a failure). Its path goes in the `card` object's close_setup; a close-setup that did not start is printed and does
not fail the card.

Which kind of card: a brief whose REPORT or PARENT line sends to `run:<id>`, from a session bound to that Run or
itself a dispatched worker on it (a lead started with worker-start, whose `run-current` is null), starts a supervised
worker (`orca orchestration worker-start --worktree new-child`, so Orca records this session's worktree as its
parent; `new-top-level` with --no-parent; any other --parent-worktree is ignored, since worker-start takes none): a
Dispatch, so the card's worker_done, escalation and ask reach this session and Orca watches it for prompts. Its spec
is the prompt below, `/goal` line included. Orca puts its own preamble before a spec, and bracketed-pastes a /goal
card's prompt, which Claude Code folds into <pasted_content>, so that line does not run as the slash command on
either path: for any Claude card or worker (a handoff too) a detached helper (`card.py goal-after-idle`, log in
$AGENT_SKILLS_STATE/goal-input/<name>.log, its path in the `card` object's goal_input) reads the new worktree's
transcript first: a /goal command row means it ran and nothing is typed, no user row yet means it waits. Otherwise
it polls `sync.try_send` (the reload broadcast's screen checks: no spinner or dialog, an empty prompt box, two equal
reads) for at most GOAL_IDLE_S until the first turn ends, then types that one line. Past the limit, or when the screen
does not confirm the line, it types nothing more and logs a warning with the manual command. When this session is bound to no Run, works for another Run than the
brief names, or Orca does not answer, nothing is created (exit 6): the lines printed say how to bind it
(`orca orchestration run-use`) or to make a /goal card on purpose (--card). A refused new-child is not retried as
new-top-level.

The parent: the worktree of the terminal running this (`orca terminal show`'s worktreeId, passed as
`--parent-worktree id:<it>`), never the focused one or a `path:` selector, which resolves to a repo's main worktree
when several Orca workspaces share its path. No terminal handle, or no worktree for it, creates nothing (exit 6).
--no-parent skips the lookup; an explicit --parent-worktree other than `path:` is passed as given.
A lead's brief starts that way too when Orca's Nested worker depth is 3 or more (`orch doctor`, orca_settings.py),
so the lead's workers' helpers still fit; below that it is a `/goal` card, with the one-line settings proposal as a
warning. A lead's brief whose MERGE row says `merge_policy human-gate` stays a `/goal` card at any depth: its landings wait on
Orca gates, and Orca lets no dispatched worker create a Task or gate on the Run it works for. Anything else (a handoff, or --card) is a `/goal` card from `orca worktree create`. A brief that sends anything to a `term_…`
handle (`orchestration send --to term_…`, in any line) is refused: a handle dies with its terminal, so the mail is
lost on a restart (`terminal_not_found`), and while it lives the mail is only stored.
--reference links the ticket in Orca (linkedItems): on create for a card, by `orca reference add` right after the
start for a worker, which takes no link flag.

The prompt is the brief's `/goal` line (with ` 브리프: <brief path>` appended unless it already names a brief), then
its PROGRAM, APPROVED, lead, CLOSE/KEEP and FORBIDDEN rows (the FORBIDDEN section whole, markers and the lines after
a blank line included) and every REPORT row, so both paths carry the bans and the report address. A row is a line outside a ``` fence that starts with the key and a
colon (`CLOSE: …`, `` `CLOSE`: … ``) or the template's wide gap (`DELEGATE    …`), plus the indented lines under it.
A lead's brief (a MODE row, a `ROLE: lead <slug>` row or a `<project>: lead` head) needs every row of orchestrate
references/brief.md "Lead", its MERGE row naming exactly one merge_policy; any brief needs CLOSE or KEEP, and one with an Approved heading or bullet needs its
`APPROVED:` row. The brief is a local file; a note only the notes store can reach is copied to one first.

`--prompt "$(cat brief.md)"` hides those lines from the guard hook, which reads only the command text; this reads
the file and checks them itself, with the hook's own patterns. The card is created from a subprocess, so no hook
sees that `orca worktree create`: the pressure gate's decision is taken here instead.

The created output is printed with a `card` object: kind (worker | card), path, handle (launch.outcome.handle, else
the worker's terminal field), run and parent (the `id:` selector, or None with --no-parent).

Exit codes: 0 created; 2 usage, unreadable brief, a missing line, a `term_` report address, a `path:` parent or a
form --agent codex does not support;
3 the pressure gate refused (wait as its reason says, then run this again); 4 the create or worker-start failed
(its whole receipt, with any recovery commands and the choices for a refused parent, goes to stderr); 5 the card
exists but linking, logging or naming it failed, or a started worker's receipt names no worktree (the create
output is still printed; do not create it again); 6 nothing created because the Run or the parent did not check
out (the choices are printed).
"""
import contextlib, datetime, fcntl, glob, itertools, json, os, pathlib, re, shlex, subprocess, sys, time

REPO = pathlib.Path(__file__).resolve().parents[1]
STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE", "~/.local/state/agent-skills")).expanduser()
TEMPLATE = REPO / "skills" / "orchestrate" / "references" / "brief.md"
BEGIN, END = "<!-- FORBIDDEN:begin -->", "<!-- FORBIDDEN:end -->"
INDENT = " " * 12  # the template's column
PARAGRAPH_END = "run it again."
WORK_ROLES = ("impl", "verify", "review")
AGENTS = ("claude", "codex")
# Orca documents worker-start for Codex. A leading /goal and the start read from the screen are known only for Claude.
CODEX_GOAL_CARD = ("--agent codex starts only a supervised worker (no --card, and a REPORT or PARENT row in the form "
                   "`orca orchestration send --to run:<id>`): a /goal card's first prompt and its start check are "
                   "not proven on Codex, so nothing was created.")
CODEX_LEAD = ("--agent codex cannot start a lead: its ROLE row works through this plugin's Claude Code hooks, "
              "so nothing was created.")
CLOSE_LINE = "CLOSE: 완료 기준을 채우고 결과를 적으면 end-session §8대로 같은 턴에 스스로 닫는다"
# hooks/lib/skillload.py reads a `LOAD: none` row as a read-only card; any other LOAD row is only text for the worker.
LOAD_LINE = ("LOAD: 코드·문서를 바꾸는 카드는 첫 편집 전에 deliver-ticket을 로드한다"
             "(Claude는 Skill 도구, 다른 CLI는 skills/deliver-ticket/SKILL.md를 읽는다). 읽기 전용 조사 카드는 `LOAD: none`이라 적는다")
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO / "skills" / "orchestrate" / "scripts"))
import guard  # noqa: E402
import orca_settings  # noqa: E402

ORCA = os.environ.get("SKILLS_SYNC_ORCA", "orca")
ORCH = os.environ.get("SKILLS_SYNC_ORCH", str(REPO / "bin" / "orch"))
GOAL = re.compile(r"(?m)^/goal[ \t]+\S.*$")
GOAL_IDLE_S = 3600  # how long the detached helper waits for a worker's first turn to end
GOAL_POLL_S = 10
GOAL_FATAL = ("orca ", "not a connected", "no rendered screen")  # try_send reasons that waiting will not change
LEAD_ROWS = ("MODE", "ROLE", "INBOX", "DELEGATE", "PREDICATE", "MERGE", "TASKS", "GATE")
MERGE_VALUE = re.compile(r"\bmerge_policy (autonomous|human-gate)\b")
ROWS = ("PROGRAM", "APPROVED", *LEAD_ROWS, "CLOSE", "KEEP", "LOAD", "FORBIDDEN", "REPORT")
FENCE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```[^\n]*$")
# dispatch-card §2's prose form of the approval, which carries nothing into the prompt without an APPROVED row
APPROVED_PROSE = re.compile(r"(?im)^[ \t]*(?:#{1,6}[ \t]*|[-*][ \t]+\*\*)approved\b")
ME = "skills-sync card"


class Refuse(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


def starts_row(line, key):
    # A colon or the template's column gap: "KEEP the old API" in prose is not a KEEP row.
    return re.match(rf"^[ \t]*`?{key}`?(?:[ \t]*:|[ \t]{{2,}}\S)", line) is not None


def continues(line):
    return bool(line.strip()) and line[:1].isspace() and not any(starts_row(line, k) for k in ROWS)


def row_at(lines, i, across_blank=False):
    """The row starting at lines[i] with its indented continuation lines. With across_blank, a blank line does not end
    it when the next line still continues it: the FORBIDDEN section a brief writes in two paragraphs."""
    out = [lines[i].strip()]
    j = i + 1
    while j < len(lines):
        if continues(lines[j]):
            out.append(lines[j].rstrip())
            j += 1
            continue
        end = j
        while across_blank and end < len(lines) and not lines[end].strip():
            end += 1
        if end == j or end == len(lines) or not continues(lines[end]):
            break
        out += [""] * (end - j)
        j = end
    return "\n".join(out)


def row(text, key, across_blank=False):
    """The first row for `key`, with its indented continuation lines, else None."""
    lines = text.splitlines()
    return next((row_at(lines, i, across_blank) for i, line in enumerate(lines) if starts_row(line, key)), None)


def all_rows(text, key):
    """Every row for `key`: a brief may keep a short REPORT line near the top and the full one at the end."""
    lines = text.splitlines()
    return [row_at(lines, i) for i, line in enumerate(lines) if starts_row(line, key)]


def merge_policy(merge_row):
    """The one merge policy a MERGE row names, else None (it names both or neither)."""
    found = set(MERGE_VALUE.findall(" ".join((merge_row or "").split())))
    return found.pop() if len(found) == 1 else None


SEND_TO = re.compile(r"orchestration\s+send\b(?:[^\n]|\\\n)*?--to[ =]+[`'\"]?([\w:.@<>-]+)")
RUN_NAME = re.compile(r"\brun:\w[\w.-]*")


def report_rows(text):
    """The brief's REPORT and PARENT rows, each with its continuation lines."""
    lines = text.splitlines()
    return ["\n".join([line] + list(itertools.takewhile(lambda l: l.strip() and l[:1].isspace(), lines[i + 1:])))
            for key in ("REPORT", "PARENT") for i, line in enumerate(lines) if starts_row(line, key)]


def report_to(text):
    """The addresses the brief's REPORT and PARENT rows send to (`orca orchestration send --to <address>`). A row
    that names only a file has none."""
    return [a for row in report_rows(text) for a in SEND_TO.findall(row)]


def unsent_run_note(text):
    """One sentence when those rows name a Run but none sends to it, the case report_to cannot see; else empty."""
    if any(a.startswith("run:") for a in report_to(text)):
        return ""
    named = sorted({a for row in report_rows(text) for a in RUN_NAME.findall(row)})
    return (f"The brief's REPORT or PARENT row names {', '.join(named)} but sends to no Run: only the form "
            "`orca orchestration send --to run:<id>` counts as a report address.") if named else ""


def compose(brief_path):
    """The card's first prompt from the brief file, or Refuse(2)."""
    path = pathlib.Path(brief_path).expanduser().resolve()
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        raise Refuse(2, f"cannot read the brief: {e}")
    text = FENCE.sub("", text)  # a quoted template is not the brief's own rows
    goal = GOAL.search(text)
    rows = {k: row(text, k, across_blank=k == "FORBIDDEN") for k in ROWS}
    lead = guard.is_lead(text)
    missing = [] if goal else ["`/goal <goal>` line"]
    if not (rows["CLOSE"] or rows["KEEP"]):
        missing.append("CLOSE or KEEP row")
    if APPROVED_PROSE.search(text) and not rows["APPROVED"]:
        missing.append("`APPROVED:` row (an Approved heading or bullet is not copied into the prompt)")
    if lead:
        missing += [f"{k} row" for k in LEAD_ROWS if not rows[k]]
        if rows["ROLE"] and not guard.LEAD_ROLE.search(rows["ROLE"]):
            missing.append("`ROLE: lead <slug>` (the ROLE row names no slug)")
        if rows["MERGE"] and not merge_policy(rows["MERGE"]):
            missing.append("MERGE row with exactly one of `merge_policy autonomous` or `merge_policy human-gate` "
                           "(the template's row names both; keep the one this program lands by)")
    if missing:
        raise Refuse(2, f"{path} has no {', no '.join(missing)}. Add it as a line of its own in the brief "
                        "(dispatch-card §2; a lead's rows are orchestrate references/brief.md \"Lead\").")
    bad = [a for a in SEND_TO.findall(text) if a.startswith("term_")]
    if bad:
        raise Refuse(2, f"{path}: it sends to {bad[0]}, a terminal handle. The handle dies with its terminal, so a "
                        "report sent after a restart is lost (terminal_not_found), and until then the mail is only "
                        "stored. Address the reporting session's Run instead (`orca orchestration send --to "
                        "run:<its run id>`; `orca orchestration run-current` prints it, `orch doctor` says when the "
                        "session has none).")
    if not lead and not rows["LOAD"]:
        rows["LOAD"] = LOAD_LINE  # an older brief has no LOAD row; a lead does not edit
    head = goal.group(0).rstrip()
    if not any(s in head for s in ("브리프:", str(path), brief_path)):
        head += f" 브리프: {path}"
    # FORBIDDEN (markers included) and every REPORT row go in whole: the worker gets its bans and where to report from
    # no other place than this first prompt, and a path to the brief file is not a substitute for them.
    prompt = "\n".join([head, *(r for k in ROWS for r in (all_rows(text, k) if k == "REPORT" else [rows[k]]) if r)])
    # The hook's own test on the result: if its patterns move, this refuses instead of creating what it would refuse.
    if not guard.CLOSE_OR_KEEP.search(prompt) or guard.is_lead(prompt) != lead or lead and (
            not guard.LEAD_ROLE.search(prompt) or any(not re.search(rf"\b{w}\b", prompt) for w in guard.LEAD_LINES)):
        raise Refuse(2, "the composed prompt does not pass hooks/guard.py's card check; fix the brief's rows")
    return prompt


def fenced_lines(lines):
    """Indices of lines inside a ``` fence (the fence lines included): a quoted template is not the brief's own."""
    inside, out = False, set()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            out.add(i)
            inside = not inside
        elif inside:
            out.add(i)
    return out


def row_span(lines, key, skip):
    """(first, end) line indices of the first `key` row outside `skip`, its indented continuation included."""
    for i, line in enumerate(lines):
        if i not in skip and starts_row(line, key):
            j = i + 1
            while j < len(lines) and lines[j].strip() and lines[j][:1].isspace() and j not in skip:
                j += 1
            return i, j
    return None


def forbidden_paragraph(template=None):
    """The worker brief's FORBIDDEN paragraph from orchestrate references/brief.md, as stripped lines through the
    refused-act paragraph's last sentence; the template's placeholders after it are the brief's own lines."""
    lines = pathlib.Path(template or TEMPLATE).read_text().splitlines()
    fence = fenced_lines(lines)
    first = min(fence)
    body = [l for i, l in enumerate(lines) if i in fence and i > first]
    span = row_span(body, "FORBIDDEN", set())
    if not span:
        raise ValueError(f"{template or TEMPLATE} has no FORBIDDEN row in its first template")
    out = []
    for line in body[span[0]:span[1]]:
        out.append(re.sub(r"^\s*FORBIDDEN\s+", "", line).strip() if not out else line.strip())
        if line.rstrip().endswith(PARAGRAPH_END):
            return out
    raise ValueError(f"the template's FORBIDDEN row does not end its paragraph with {PARAGRAPH_END!r}")


def place_forbidden(text, paragraph):
    """(text, how): the brief with its FORBIDDEN section set to `paragraph`. Between the markers it is replaced, never
    appended to; with no markers, an unmarked copy identical to the current paragraph is wrapped in markers, and any
    other text (an older or edited copy included) stays while the marked section goes first in the row; with
    no row, a new row goes before REPORT (else at the end). Lines outside the section (a repository's data-sharing
    restriction, the unit's own bans) stay as they are. how: unchanged | replaced | inserted."""
    lines = text.splitlines()
    skip = fenced_lines(lines)
    body = [INDENT + l for l in paragraph]
    b = next((i for i, l in enumerate(lines) if i not in skip and BEGIN in l), None)
    e = next((i for i, l in enumerate(lines) if b is not None and i > b and i not in skip and END in l), None)
    if b is not None and e is not None:
        head = lines[b][:lines[b].index(BEGIN) + len(BEGIN)]
        tail = INDENT + lines[e][lines[e].index(END):]
        new = lines[:b] + [head] + body + [tail] + lines[e + 1:]
        how = "replaced"
    else:
        block = ["FORBIDDEN   " + BEGIN] + body + [INDENT + END]
        span = row_span(lines, "FORBIDDEN", skip)
        if span:
            first, end = span
            # An unmarked copy is replaced only when it is the current paragraph word for word (wrapped differently);
            # an older or edited copy may hold a repository's own restriction, so it stays and the section goes first.
            opening = paragraph[0].split(".")[0] + "."
            strip = lambda l: re.sub(r"^\s*`?FORBIDDEN`?\s*:?\s*", "", l).strip()  # noqa: E731
            s = next((i for i in range(first, end) if strip(lines[i]).startswith(opening)), None)
            old = next((i for i in range(s, end) if lines[i].rstrip().endswith(PARAGRAPH_END)), None) \
                if s is not None else None
            words = lambda ls: " ".join(" ".join(strip(l) for l in ls).split())  # noqa: E731
            if old is not None and words(lines[s:old + 1]) != words(paragraph):
                old = None
            if old is not None and s == first:
                new = lines[:first] + block + lines[old + 1:]
            elif old is not None:
                new = lines[:s] + [INDENT + BEGIN] + body + [INDENT + END] + lines[old + 1:]
            else:
                rest = re.sub(r"^\s*`?FORBIDDEN`?\s*:?\s*", "", lines[first])
                new = lines[:first] + block + ([INDENT + rest] if rest.strip() else []) + lines[first + 1:]
        else:
            report = row_span(lines, "REPORT", skip)
            at = report[0] if report else len(lines)
            new = lines[:at] + block + lines[at:]
        how = "inserted"
    out = "\n".join(new) + ("\n" if text.endswith("\n") else "")
    return (text, "unchanged") if out == text else (out, how)


def refresh_forbidden(brief_path):
    """Writes the current FORBIDDEN paragraph into the brief file; returns how (place_forbidden), or Refuse(2)."""
    path = pathlib.Path(brief_path).expanduser().resolve()
    try:
        text, how = place_forbidden(path.read_text(), forbidden_paragraph())
        if how != "unchanged":
            tmp = path.with_name(path.name + ".card-tmp")
            tmp.write_text(text)
            tmp.replace(path)
    except (OSError, ValueError) as e:
        raise Refuse(2, f"[{ME}] could not refresh the brief's FORBIDDEN section ({e}); fix it, or pass "
                        "--no-forbidden to start the card with the brief as it is (that is logged)")
    return how


# ---- one card per (repo, reference, role)


def ledger():
    return STATE / "cards.jsonl"


@contextlib.contextmanager
def card_lock():
    """Held from the duplicate check through the record, so two starts of the same key cannot both pass."""
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "cards.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def ledger_rows():
    """{worktree path: the merged record} from cards.jsonl; a supersede line adds `superseded_by` to its path, and a
    creation line (one with `role`) starts the path's record over, since Orca reuses a removed card's path."""
    out = {}
    try:
        text = ledger().read_text()
    except OSError:
        return out
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("path"), str):
            if "role" in r:
                out[r["path"]] = dict(r)
            else:
                out.setdefault(r["path"], {}).update(r)
    return out


def ledger_add(**row):
    STATE.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **row}
    with open(ledger(), "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def orca_result(*args):
    p, _ = run([ORCA, *args, "--json"], timeout=30)
    try:
        res = json.loads(p.stdout).get("result") if p and not p.returncode else None
    except (ValueError, AttributeError):
        return None
    return res if isinstance(res, dict) else None


def repo_id(selector):
    """Orca's repo id for a --repo selector, or for this terminal's worktree when selector is None; None unread."""
    res = orca_result("repo", "show", "--repo", selector) if selector else orca_result("worktree", "current")
    node = (res or {}).get("repo" if selector else "worktree")
    node = node if isinstance(node, dict) else {}
    return node.get("id" if selector else "repoId")


def duplicates(repo, refs, role):
    """Live Orca cards on the same key: the same repo, any of `refs` (in Orca's linkedItems or this ledger) and the
    same role (the ledger's, `impl` for a card it never recorded). None when Orca's list could not be read."""
    res = orca_result("worktree", "ps", "--limit", "500")
    rows = (res or {}).get("worktrees")
    if not isinstance(rows, list):
        return None
    known = ledger_rows()
    out = []
    for w in rows:
        if not isinstance(w, dict) or w.get("isArchived") or w.get("isMainWorktree") or w.get("repoId") != repo:
            continue
        rec = known.get(w.get("path"), {})
        if rec.get("superseded_by") or (rec.get("role") or "impl") != role:
            continue
        linked = json.dumps([w.get(k) for k in ("linkedItems", "linkedLinearIssue", "linkedIssue")], ensure_ascii=False)
        # whole references only: ABC-2 is not ABC-20, nor a URL's prefix
        if any(re.search(rf"(?<![\w-]){re.escape(r)}(?![\w/-])", linked) or r in (rec.get("references") or [])
               for r in refs):
            out.append({"path": w.get("path"), "name": w.get("displayName"), "worktree": w.get("worktreeId"),
                        "role": rec.get("role") or "impl", "dispatch": rec.get("dispatch")})
    return out


def check_duplicates(repo_selector, refs, role, supersede):
    """(repo id, the cards to supersede) or Refuse(2) when a card on this key is live and --supersede is absent.
    An unread repo or card list is printed and lets the card through."""
    if not refs:
        return None, []
    repo = repo_id(repo_selector)
    found = duplicates(repo, refs, role) if repo else None
    if found is None:
        print(f"[{ME}] warning: Orca's repo or card list could not be read; the duplicate-card check was skipped",
              file=sys.stderr)
        return repo, []
    if found and not supersede:
        listed = "\n".join(f"  {d['path']}  {d['name'] or ''}  role {d['role']}" for d in found)
        raise Refuse(2, f"[{ME}] a card on the same key (repo, {', '.join(refs)}, {role}) is already live:\n{listed}\n"
                        "Send it what it needs instead (dispatch-card §3), or pass --supersede to start this one and "
                        "tell that one to stop (it is recorded).")
    return repo, found


def supersede(old, new_path, new_name):
    """Tells a superseded card to stop, logs it with `orch sent add --kind supersede` and records it. Returns the
    failures."""
    import sync
    text = f"superseded by {new_name} ({new_path}): stop and close by end-session §8"
    failed = []
    ledger_add(path=old["path"], superseded_by=new_path)
    if old.get("dispatch"):  # a worker: typed into its terminal, it would be taken over
        p, err = run([ORCA, "orchestration", "send", "--to", f"dispatch:{old['dispatch']}", "--type", "status",
                      "--subject", "superseded", "--body", text, "--json"])
        failed += [] if p and not p.returncode else [f"dispatch:{old['dispatch']} was not told ({err}): {text}"]
        res = {"terminals": []}
    else:
        res = orca_result("terminal", "list", "--worktree", f"path:{old['path']}")
    agents = [t.get("handle") for t in (res or {}).get("terminals") or [] if isinstance(t, dict) and t.get("agentIdentity")]
    if not agents and not old.get("dispatch"):
        failed.append(f"no agent terminal found on {old['path']} to tell it: {text}")
    for h in agents:
        ok, why = sync.try_send(h, text)
        if not ok:
            failed.append(f"{h} was not told ({why}); send it by hand: {text}")
    p, err = run([ORCH, "sent", "add", "--to", old.get("name") or old["path"], "--kind", "supersede", "--channel", ME,
                  "-"], stdin=text)
    if not p or p.returncode:
        failed.append(f"orch sent add: {err}")
    return failed


def start_close_setup(path, handle, name):
    """Starts `skills-sync close-setup <path> --agent <handle>` in the background. Returns {log, pid} or {error}: a
    failure here never fails the card, it is printed."""
    log = STATE / "close-setup" / f"{name}.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as out:
            cmd = [sys.executable, str(REPO / "scripts" / "sync.py"), "close-setup", path, *(["--agent", handle] if handle else [])]
            p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True)
        return {"log": str(log), "pid": p.pid}
    except OSError as e:
        print(f"[{ME}] warning: close-setup did not start ({e}); run `skills-sync close-setup {path}` in the "
              "background yourself", file=sys.stderr)
        return {"error": str(e)}


def start_goal_input(handle, line, name, worktree=None):
    """Starts `card.py goal-after-idle <handle> <line> [<worktree>]` detached: the first turn can last the whole task,
    and this command must not wait for it. Returns {log, pid} or {error}; a failure to start is printed, never fatal."""
    log = STATE / "goal-input" / f"{name}.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as out:
            p = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), "goal-after-idle", handle, line,
                                  *([worktree] if worktree else [])],
                                 stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        return {"log": str(log), "pid": p.pid}
    except OSError as e:
        print(f"[{ME}] warning: the /goal input did not start ({e}); once the worker is idle, run: "
              f"{manual_goal_command(handle, line)}", file=sys.stderr)
        return {"error": str(e)}


def manual_goal_command(handle, line):
    return shlex.join([ORCA, "terminal", "send", "--terminal", handle, "--text", line, "--enter"])


def type_when_idle(handle, line, deadline=None):
    """None once `line` was typed into an idle Claude session and the screen shows it was taken, else the reason it
    was not (sync.UNCONFIRMED when it may have been typed). This is `sync.send_to` with the tab title left out of the
    judgement: a worker's title is the session's topic (`GOAL-DEMO-OK …`), never the `✳` that `sync.classify` wants,
    so the title would read every worker as busy. The screen checks (spinner, dialog, empty prompt box, two equal
    reads) stay."""
    import sync

    def look():
        meta = sync.orca("terminal", "show", "--terminal", handle)
        meta = meta.get("terminal", meta)
        if meta.get("agentIdentity") != "claude" or not meta.get("connected") or not meta.get("writable"):
            return [], "not a connected, writable Claude terminal"
        t = sync.orca("terminal", "read", "--terminal", handle, "--screen")["terminal"]
        if t.get("source") not in (None, "screen"):
            return [], f"no rendered screen ({t.get('source')})"
        tail = t.get("tail") or []
        return tail, sync.classify(tail, "✳", t.get("draft") or "")

    with sync.locked(10) as got:  # the lock sync, broadcast and nudge share: no other writer types between the reads
        if not got:
            return "a sync or broadcast is running"
        try:
            first, reason = look()
            if reason:
                return reason
            time.sleep(sync.SETTLE_S)
            second, reason = look()
            if reason:
                return reason
            if second != first:
                return "screen changed between two reads (someone may be typing)"
        except sync.Stop as e:
            return str(e)
        if deadline is not None and time.monotonic() >= deadline:
            return "time limit reached while waiting"
        try:
            sync.orca("terminal", "send", "--terminal", handle, "--text", line, "--enter")
        except sync.Stop:  # Orca may have taken the input and lost the answer: typing it again could set the goal twice
            return sync.UNCONFIRMED
        time.sleep(sync.CONFIRM_S)
        try:
            after, _ = look()
        except sync.Stop:  # typed already: an unread confirmation must not read as never sent
            return sync.UNCONFIRMED
        return None if sync.turn_started(second, after, line) else sync.UNCONFIRMED


GOAL_RAN = "<command-name>/goal</command-name>"
GOAL_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def user_texts(path):
    """The text of each user row in a transcript that is not Claude Code's own (isMeta)."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for raw in lines:
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("type") != "user" or r.get("isMeta") or not isinstance(r.get("message"), dict):
            continue
        content = r["message"].get("content")
        texts = [content] if isinstance(content, str) else [
            b.get("text") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        out += [t for t in texts if isinstance(t, str)]
    return out


def goal_state(worktree, line):
    """What the worktree's sessions say about `line`: "ran" once a user row is the /goal command with this goal as
    its args, "other" once a user row holds the whole line (the folded paste, a worker's spec), else "none": Orca has
    not typed the first prompt yet, and a reload broadcast's rows or another session's prompt do not count. No file
    is picked by its age: another session can share the folder. The folders are Claude Code's under
    CLAUDE_PROJECTS_DIR, else under each of sync.HOMES (only the Orca profile pattern's `*` is a pattern),
    named after the path with every non-alphanumeric character as `-`."""
    import sync
    line = line.strip()
    goal = line.split(None, 1)[-1]
    name = re.sub(r"[^A-Za-z0-9]", "-", worktree)
    homes = [h for home in sync.HOMES for h in (glob.glob(os.path.expanduser(home)) if "*" in home
                                                else [os.path.expanduser(home)])]
    roots = [os.environ["CLAUDE_PROJECTS_DIR"]] if os.environ.get("CLAUDE_PROJECTS_DIR") else [
        str(pathlib.Path(h) / "projects") for h in homes]
    state = "none"
    for root in dict.fromkeys(roots):
        for path in pathlib.Path(root).expanduser().joinpath(name).glob("*.jsonl"):
            for t in user_texts(path):
                if t.lstrip().startswith(GOAL_RAN) and any(a.strip() == goal for a in GOAL_ARGS.findall(t)):
                    return "ran"
                if any(l.strip() == line for l in t.splitlines()):  # a whole line: `/goal ABC-7. more` is not it
                    state = "other"
    return state


def goal_after_idle(handle, line, timeout_s=GOAL_IDLE_S, worktree=None):
    """Types the /goal line into the session's terminal once its first turn ended and an empty prompt is back. Orca puts
    its own preamble before a worker's spec, and bracketed-pastes a card's prompt, which Claude Code folds into
    <pasted_content> when it is long: either way the line inside never runs as the slash command. With `worktree`,
    goal_state decides first: "ran" types nothing, "none" waits for the first prompt. The idle
    judgement is `type_when_idle`'s, polled until the limit: `orca terminal wait --for tui-idle` is not used, since it
    answered during UserPromptSubmit hooks (docs/platform.md). Nothing is typed while the turn runs or after the
    limit. 0 sent or already run, 1 not sent (the manual command is printed)."""
    import sync
    manual = manual_goal_command(handle, line)
    deadline = time.monotonic() + timeout_s
    while True:
        if time.monotonic() >= deadline:
            print(f"[{ME}] warning: the /goal line was not typed into {handle} (waited up to {timeout_s}s). "
                  f"Once the session is idle, run: {manual}", file=sys.stderr)
            return 1
        state = goal_state(worktree, line) if worktree else "other"
        if state == "ran":
            print(f"[{ME}] {handle} already ran /goal; nothing typed")
            return 0
        if state == "none":
            time.sleep(GOAL_POLL_S)
            continue
        reason = type_when_idle(handle, line, deadline)
        if reason is None:
            print(f"[{ME}] typed the /goal line into {handle}: {line}")
            return 0
        if reason == sync.UNCONFIRMED:  # it was typed; typing it again would set the goal twice
            print(f"[{ME}] warning: the /goal line may have reached {handle}, but its screen does not show it. Read "
                  f"the screen before typing it again: {manual}", file=sys.stderr)
            return 1
        if reason.startswith(GOAL_FATAL):
            print(f"[{ME}] warning: the /goal line was not typed into {handle} ({reason}; waited up to {timeout_s}s). "
                  f"Once the worker is idle, run: {manual}", file=sys.stderr)
            return 1
        time.sleep(GOAL_POLL_S)


def pressure_check(cmd):
    """(the pressure gate's deny reason or None, a function recording this start's outcome in the gate's queue).
    The hook's own steps for a command it sees: a danger deny queues it, a pass marks that entry's retry. Running the
    same `skills-sync card` again builds the same command, so its entry is found again; no decision on any failure."""
    def none(*_):
        return None
    if os.environ.get("AGENT_SKILLS_PRESSURE_GATE") == "0":
        return None, none
    # The terminal stands in for the hook event's session: the same session's rerun finds its queue entry.
    session = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    try:
        import pressure_gate
        sys.path.insert(0, str(pressure_gate.SCRIPT.parent))
        import pressure
        cfg = pressure.config()
        state = pressure.load_state()
        out = pressure_gate.gate(cmd, pressure.snapshot(cfg, state, idle_fn=pressure.live(cfg)), state, cfg, p=pressure,
                                 session=session, cwd=os.getcwd())
    except Exception:
        return None, none
    if out and out[0] == "deny":
        return out[1], none

    def mark(outcome, error=None):
        try:
            pressure.queue_mark(session, cmd, outcome, error)
        except Exception:
            pass
    mark("retrying")
    return None, mark


def github_repo(worktree):
    """`owner/name` of the card's origin, for the Sent page; None when it is not a GitHub remote."""
    p, _ = run(["git", "-C", worktree or ".", "remote", "get-url", "origin"], timeout=10) if worktree else (None, "")
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", p.stdout.strip()) if p and not p.returncode else None
    return m and m.group(1)


# What to do when Orca refuses the start over its parent or depth, by a code or text in its refusal; Orca checks
# both before it creates anything.
REFUSED_UNSTARTED = {
    "could not be resolved to a git worktree Orca manages":
        "this terminal's worktree is not one Orca manages, so new-child has no parent: run this from a terminal in "
        "the worktree that should be the parent, or pass --no-parent for a card with no parent",
    "LINEAGE_PARENT_NOT_FOUND":
        "the parent selector names no worktree Orca knows: pass the right `--parent-worktree id:<worktree id>` "
        "(`orca worktree show --worktree active --json`), or --no-parent for a card with no parent",
    "nested_worker_depth_exceeded":
        "Orca's Nested worker depth leaves no room for this worker: start it as a /goal card with --card, or put "
        "`orch doctor`'s settings line before the human",
}


def run(cmd, stdin=None, timeout=120):
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return p, (p.stderr or p.stdout).strip()[:400]


def stop(why, *choices):
    lines = "\n".join(f"  {i}. {c}" for i, c in enumerate(choices, 1))
    return Refuse(6, f"[{ME}] nothing was created: {why}. Choose one, then run this again:\n{lines}")


def supervisor(wanted):
    """The Run this card's worker goes to: this session's bound Run, else the Run of its own Dispatch. Refuse(6)
    unless that is the one Run the brief reports to, or Orca cannot say."""
    card = "pass --card for a /goal card with no Dispatch (its reports go to the Run as `--type question`)"
    bound = orca_settings.binding()
    if bound == "unknown":
        raise stop("Orca did not say which Run this terminal is bound to (`orca orchestration run-current --json` "
                   "failed, or ORCA_TERMINAL_HANDLE is unset)", "run this from an Orca terminal once that command "
                   "answers", card)
    own = None if bound else orca_settings.own_dispatch(ORCA, failed="unknown")
    if own == "unknown":
        raise stop("this terminal is bound to no Run, and Orca did not answer whether it is a dispatched worker "
                   "(`orca orchestration worker-list --terminal-state active --json` failed)",
                   "run this again once that command answers", card)
    have = (bound or {}).get("id") or (own or {}).get("runId")
    want = " and ".join(f"run:{w}" for w in sorted(wanted))
    if not have:
        raise stop(f"the brief reports to {want}, but this session is bound to no Run and is no dispatched worker",
                   f"bind this terminal: `orca orchestration run-use --id {sorted(wanted)[0]}` "
                   "(orchestrate references/top-level.md \"Your Run\")", card)
    if wanted != {have}:
        raise stop(f"the brief reports to {want}, but this session works for run:{have}",
                   f"fix the brief's REPORT or PARENT line to run:{have}",
                   "run this from the session that coordinates the brief's Run", card)
    return bound or {"id": have, "via": "own_dispatch"}


def caller_worktree():
    """`id:<worktree id>` of the terminal running this, or Refuse(6). Not `active` or a path: the parent is the
    session that makes the card, which `orca terminal show` names for this terminal's handle."""
    me = os.environ.get("ORCA_TERMINAL_HANDLE")
    choices = ("run this from an Orca terminal in the worktree that should be the parent",
               "pass `--parent-worktree id:<worktree id>` (`orca worktree show --worktree active --json`)",
               "pass --no-parent for a card with no parent")
    if not me:
        raise stop("ORCA_TERMINAL_HANDLE is unset, so this terminal's worktree cannot be looked up", *choices)
    p, err = run([ORCA, "terminal", "show", "--terminal", me, "--json"], timeout=30)
    try:
        wt = ((json.loads(p.stdout).get("result") or {}).get("terminal") or {}).get("worktreeId") \
            if p and not p.returncode else None
    except (ValueError, AttributeError):
        wt = None
    if not isinstance(wt, str) or not wt:
        raise stop(f"`orca terminal show` named no worktree for this terminal ({err or 'no answer'})", *choices)
    return f"id:{wt}"


def worktree_path(res):
    """The new worktree's path in a create or worker-start receipt, wherever this Orca version puts it."""
    if isinstance(res, dict):
        w = res.get("worktree")
        if isinstance(w, dict) and w.get("path"):
            return w["path"]
        # worker-start (1.4.239) names it only as an effect: {"kind": "worktree", "id": "<repo id>::<path>"}
        if res.get("kind") == "worktree" and isinstance(res.get("id"), str) and "/" in res["id"]:
            return res["id"].split("::", 1)[-1]
        for k in ("worktreePath", "worktreeId", "workspace"):
            if isinstance(res.get(k), str) and "/" in res[k]:
                return res[k].split("::", 1)[-1]
        for v in res.values():
            if isinstance(v, (dict, list)) and (found := worktree_path(v)):
                return found
    elif isinstance(res, list):
        return next(filter(None, map(worktree_path, res)), None)
    return None


def handle_of(res):
    """The new agent's handle: launch.outcome.handle (a chat or a terminal), else the older worker terminal fields."""
    return ((((res.get("launch") or {}).get("outcome") or {}).get("handle")) or orca_settings.worker_terminal(res)
            or orca_settings.worker_terminal(res.get("worker")) or ((res.get("dispatch") or {}).get("assigneeHandle"))
            or next((e.get("id") for e in res.get("effects") or [] if isinstance(e, dict)
                     and e.get("kind") == "terminal" and e.get("role") == "agent"), None))


def parse_args(rest):
    opts, refs = {}, []
    valued = {"--repo", "--name", "--display-name", "--comment", "--parent-worktree", "--role", "--title", "--note",
              "--base-branch", "--agent", "--model"}
    i = 0
    while i < len(rest):
        if rest[i] == "--reference" and i + 1 < len(rest):
            refs.append(rest[i + 1])
            i += 2
        elif rest[i] in valued and i + 1 < len(rest):
            opts[rest[i]], i = rest[i + 1], i + 2
        elif rest[i] in ("--no-parent", "--card", "--supersede", "--no-forbidden"):
            opts[rest[i]], i = True, i + 1
        else:
            raise Refuse(2, f"unexpected argument {rest[i]!r}\n{__doc__}")
    if opts.setdefault("--role", "impl") not in WORK_ROLES:
        raise Refuse(2, f"--role is one of {', '.join(WORK_ROLES)}")
    if opts.setdefault("--agent", "claude") not in AGENTS:
        raise Refuse(2, f"--agent is one of {', '.join(AGENTS)}")
    return opts, refs


KIND_MODEL = {"design": "opus", "edit": "sonnet", "lookup": "haiku"}
# The kind is the whole word before the dash: `edit-typo — x` is the kind `edit-typo`, refused, never `edit`.
KIND_ROW = re.compile(r"^`?KIND`?(?:[ \t]*:|[ \t]{2,})[ \t]*([^\s—]*)(?:[ \t]*—|[ \t]+--?(?=\s))[ \t]*(\S.*)?$", re.S)


def brief_kind(brief_path):
    """The brief's `KIND: design|edit|lookup — <reason>` row as its kind, None without one, else Refuse(2)."""
    text = FENCE.sub("", pathlib.Path(brief_path).expanduser().read_text())
    r = row(text, "KIND")
    if r is None:
        return None
    m = KIND_ROW.match(r)
    if not m or m.group(1) not in KIND_MODEL or not m.group(2):
        raise Refuse(2, f"[{ME}] {brief_path}: the KIND row is `KIND: design|edit|lookup — <reason>` (found "
                        f"{r.splitlines()[0]!r}); fix it, or drop the row to use orchestrate.models.worker")
    return m.group(1)


def session_model(opts, lead, name, kind=None):
    """(model, model_from): what picked it, or (None, None) after a warning line. See the module docstring."""
    if opts.get("--model"):
        return opts["--model"], "--model"
    if opts["--agent"] != "claude":
        return None, None
    if kind and not lead:
        return KIND_MODEL[kind], f"KIND: {kind}"
    role = "lead" if lead else "worker"
    model = orca_settings.role_model(role)
    if not model:
        print(f"[{name}] orchestrate.models.{role} is not set: this card runs on the account's default model",
              file=sys.stderr)
    return model, f"orchestrate.models.{role}" if model else None


def refuse_codex(brief, opts):
    """Refuse(2) for a form Codex does not run. It comes before the brief is rewritten and the Run is checked, so a
    refused start changes nothing and says one thing."""
    raw = text = pathlib.Path(brief).expanduser().read_text()
    # create_locked's condition: the refresh can drop a row between the markers and split one from its head
    if not opts.get("--no-forbidden") and not guard.is_lead(raw):
        try:
            text = place_forbidden(raw, forbidden_paragraph())[0]
        except (OSError, ValueError):
            pass  # refresh_forbidden reports it
    text = FENCE.sub("", text)
    if guard.is_lead(FENCE.sub("", raw)) or guard.is_lead(text):
        raise Refuse(2, f"[{ME}] {CODEX_LEAD}")
    if opts.get("--card"):
        raise Refuse(2, f"[{ME}] {CODEX_GOAL_CARD}")
    if not any(a.startswith("run:") for a in report_to(text)):
        raise Refuse(2, f"[{ME}] {CODEX_GOAL_CARD} {unsent_run_note(text)}".rstrip())


def create(argv):
    if not argv or argv[0].startswith("-"):
        raise Refuse(2, __doc__)
    brief = argv[0]
    opts, refs = parse_args(argv[1:])
    for k in ("--repo", "--name"):
        if not opts.get(k):
            raise Refuse(2, f"{k} is required (dispatch-card §3)")
    prompt = compose(brief)
    kind = brief_kind(brief)
    if opts["--agent"] == "codex":
        refuse_codex(brief, opts)
    with card_lock():
        return create_locked(brief, opts, refs, prompt, kind)


def create_locked(brief, opts, refs, prompt, kind=None):
    repo, superseded = check_duplicates(opts["--repo"], refs, opts["--role"], opts.get("--supersede"))
    # A lead's brief has no FORBIDDEN row in its template: the worker paragraph is not its rule set.
    forbidden = ("skipped (--no-forbidden)" if opts.get("--no-forbidden") else "skipped (lead brief)"
                 if guard.is_lead(pathlib.Path(brief).expanduser().read_text()) else refresh_forbidden(brief))
    prompt = compose(brief)  # again: the refresh may have rewritten the FORBIDDEN section the prompt carries
    text = FENCE.sub("", pathlib.Path(brief).expanduser().read_text())
    wanted = {a[len("run:"):] for a in report_to(text) if a.startswith("run:")}
    to_run = bool(wanted)
    if (note := unsent_run_note(text)) and not opts.get("--card"):
        print(f"[{ME}] {note} Starting a /goal card.", file=sys.stderr)
    if (opts.get("--parent-worktree") or "").startswith("path:"):
        raise Refuse(2, f"[{ME}] --parent-worktree {opts['--parent-worktree']}: a path selector resolves to the "
                        "repo's main worktree when several Orca workspaces share that path. Pass `id:<worktree id>` "
                        "(`orca worktree show --worktree <it> --json`), or leave it out for this terminal's worktree.")
    lead = guard.is_lead(text)
    # Checked before the lead's form is picked: a lead started as a /goal card still reports to this Run.
    checked = supervisor(wanted) if to_run and not opts.get("--card") else None
    if lead and to_run and not opts.get("--card"):
        settings, why = orca_settings.read()
        if merge_policy(row(text, "MERGE")) == "human-gate":
            to_run = False
            print(f"[{ME}] the lead starts as a /goal card: its program merges through Orca gates (human-gate), and a "
                  "dispatched worker can create no Task or gate on the Run it works for "
                  "(worker_cannot_coordinate_own_run)", file=sys.stderr)
        elif not orca_settings.lead_as_worker(settings):
            to_run = False
            fix = "; ".join(orca_settings.suggestions(settings)) if settings else f"Orca's settings unread: {why}"
            print(f"[{ME}] the lead starts as a /goal card: {fix}", file=sys.stderr)
    run_id = checked if to_run else None
    worker = bool(run_id)
    if opts["--agent"] == "codex" and not worker:  # whatever the brief turned out to be, never a Codex /goal card
        raise Refuse(2, f"[{ME}] {CODEX_GOAL_CARD}")
    parent = None if opts.get("--no-parent") else (
        opts["--parent-worktree"] if opts.get("--parent-worktree") and not worker else caller_worktree())
    model, model_from = session_model(opts, lead, ME, kind)
    if worker:
        # Orca puts its own preamble before the spec, so the /goal line is not the first thing the session reads and
        # Claude Code may not run it as the slash command. It stays: the line still states the goal, and stripping it
        # dropped the owner's goal from the worker altogether.
        spec = prompt
        # worker-start takes no parent selector: new-child parents it to this session, --no-parent drops lineage
        cmd = [ORCA, "orchestration", "worker-start", "--spec", spec, "--worktree",
               "new-top-level" if opts.get("--no-parent") else "new-child",
               "--repo", opts["--repo"], "--name", opts["--name"], "--agent", opts["--agent"],
               # Orca times out before this process does, so a slow start still returns its receipt
               "--timeout-ms", "840000"]
        cmd += ["--display-name", opts["--display-name"]] if opts.get("--display-name") else []
    else:
        cmd = [ORCA, "worktree", "create", "--repo", opts["--repo"], "--name", opts["--name"]]
        cmd += ["--parent-worktree", parent] if parent else ["--no-parent"]
        cmd += ["--agent", opts["--agent"], "--prompt", prompt]
        for r in refs:
            cmd += ["--reference", r]
    if opts.get("--comment"):
        cmd += ["--comment", opts["--comment"]]
    cmd += ["--model", model] if model else []
    cmd.append("--json")
    reason, mark = pressure_check(shlex.join(["orca", *cmd[1:]]))
    if reason:
        raise Refuse(3, f"[{ME}] not created, the pressure gate refused it (for `queue wait`, run this same "
                        f"`skills-sync card` command again once it ends with 0): {reason}")
    p, err = run(cmd, timeout=900 if worker else 120)
    try:
        out = json.loads(p.stdout) if p else None
    except ValueError:
        out = None
    res = (out or {}).get("result") or {}
    path = worktree_path(res)
    started = bool(p and not p.returncode and out and out.get("ok") is not False)
    if worker and started and not path:  # it runs: a retry would start a second worker
        mark("done")
        print(json.dumps({**res, "card": {"kind": "worker", "path": None, "handle": handle_of(res), "run": run_id,
                                          "parent": parent}},
                         ensure_ascii=False, indent=1))
        raise Refuse(5, f"[{ME}] the worker started but its receipt names no worktree, so nothing was linked. "
                        "Do not create it again; find it with `orca orchestration worker-list`.")
    if not started or not (path if worker else res.get("worktree")):
        e = (out or {}).get("error") or {}
        err = f"{e.get('code')}: {e.get('message')}" if e else err
        mark("failed", err)
        if out:  # a failed or outcome_unknown start names its stage, its effects and the recovery commands
            print(json.dumps(out, ensure_ascii=False, indent=1), file=sys.stderr)
        verb = "orca orchestration worker-start" if worker else "orca worktree create"
        data = e.get("data") or {}
        # Orca's own recovery query: worker-show when it names the Dispatch, request-show otherwise.
        query = (data.get("recovery") or {}).get("queryCommand")
        query = shlex.join(query) if isinstance(query, list) and query else (
            f"orca orchestration worker-show --dispatch {data['dispatchId']} --json" if data.get("dispatchId") else
            f"orca orchestration request-show --request {data['orchestrationRequestId']} --json"
            if data.get("orchestrationRequestId") else None)
        refused = next((v for k, v in REFUSED_UNSTARTED.items() if p and k in p.stdout + p.stderr), None)
        if refused:  # refused before anything was created; a start without the parent is the caller's choice
            hint = f"Nothing was created: {refused}"
        elif worker and p is None:
            hint = ("Orca did not answer in time, so the worker may be starting: read `orca orchestration "
                    "worker-list` and `orca worktree ps` before starting it again")
        else:
            hint = f"Before starting it again, ask Orca whether this start took effect: {query}" if query else ""
        raise Refuse(4, f"[{ME}] {verb} failed: {err}" + (f". {hint}" if hint else ""))
    mark("done")
    handle = handle_of(res)
    # close-setup judges the start from Claude's screen; worker-start's receipt already says a Codex worker is ready.
    setup = start_close_setup(path, handle if opts["--agent"] == "claude" else None, opts["--name"])
    # The /goal line is typed by a detached helper once the first turn ends, unless the transcript shows it ran.
    goal_input = (start_goal_input(handle, prompt.splitlines()[0], opts["--name"], path)
                  if handle and path and opts["--agent"] == "claude" else None)  # /goal is Claude Code's command
    print(json.dumps({**res, "card": {"kind": "worker" if worker else "card", "path": path, "handle": handle,
                                      "run": run_id, "parent": parent, "forbidden": forbidden, "close_setup": setup,
                                      "model": model, "model_from": model_from,
                                      **({"goal_input": goal_input} if goal_input else {})}},
                     ensure_ascii=False, indent=1))
    failed = []
    if refs:
        ledger_add(repo=repo, references=refs, role=opts["--role"], path=path, name=opts["--name"],
                   dispatch=res.get("dispatchId") if worker else None, model=model, model_from=model_from)
    for old in superseded:
        failed += supersede(old, path, opts.get("--display-name") or opts["--name"])
    if opts.get("--no-forbidden"):
        p, err = run([ORCH, "sent", "add", "--to", opts["--name"], "--kind", "other", "--channel", ME, "-"],
                     stdin=f"started with --no-forbidden: the brief's FORBIDDEN section was not refreshed ({brief})")
        if not p or p.returncode:
            failed.append(f"orch sent add: {err}")
    if worker and refs and path:  # worker-start takes no link flag; only this new card is written
        p, err = run([ORCA, "reference", "add", *refs, "--worktree", f"path:{path}", "--json"])
        if not p or p.returncode:
            failed.append(f"orca reference add: {err}")
    if not worker:  # a worker's brief is its Orca Task spec, which the Sent page reads
        gh = github_repo(path)
        p, err = run([ORCH, "sent", "add", "--to", opts["--name"], "--kind", "card", "--channel", ME,
                      *(["--repo", gh] if gh else []), "-"], stdin=prompt)
        if not p or p.returncode:
            failed.append(f"orch sent add: {err}")
        if opts.get("--display-name") and path:
            p, err = run([ORCA, "worktree", "set", "--worktree", f"path:{path}", "--display-name", opts["--display-name"], "--json"])
            if not p or p.returncode:
                failed.append(f"orca worktree set: {err}")
    if failed:
        raise Refuse(5, f"[{ME}] the card was created but: {'; '.join(failed)}. Do not create it again.")
    return 0


HANDOFF = "skills-sync handoff"


def handoff_parent():
    """`id:<…>` of this card's parent worktree, else of this card itself (handoff-ticket §3), or Refuse(6)."""
    mine = caller_worktree()
    res = orca_result("worktree", "show", "--worktree", mine)
    parent = ((res or {}).get("worktree") or {}).get("parentWorktreeId") if isinstance((res or {}).get("worktree"), dict) else None
    if res is None:
        print(f"[{HANDOFF}] warning: `orca worktree show` did not answer; the new card goes under this one",
              file=sys.stderr)
    return f"id:{parent}" if isinstance(parent, str) and parent else mine


def confirm_start(handle):
    """(started, detail): wait for the new agent to settle, then read its screen (handoff-ticket §4)."""
    import sync
    run([ORCA, "terminal", "wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms", "60000", "--json"],
        timeout=90)
    try:
        state, detail = sync.start_state(handle)
    except (sync.Stop, KeyError, TypeError) as e:
        return False, f"screen unreadable: {e}"
    return state == "started", f"{state}: {detail}"


def handoff(argv):
    """skills-sync handoff <TICKET> [--title <short title>] [--note <text>] [--base-branch <b>] [--reference <url>]...
    [--role impl|verify|review] [--supersede] [--no-parent]

    The next ticket's /goal card, as handoff-ticket §3 makes it: named after the ticket, under this card's parent (or
    this card), its prompt `/goal <TICKET>. <note> CLOSE: …`, linked to --reference, displayed as `<TICKET> <title>`,
    its setup closed in the background. The duplicate-card key uses the ticket and every --reference. Then its start is
    read from its screen: exit 0 only once it shows the prompt taken; 5 when the card exists but the start, the link
    or the name is not confirmed (do not create it again; look at the card). 2, 3, 4 and 6 as `skills-sync card`."""
    try:
        if not argv or argv[0].startswith("-"):
            raise Refuse(2, handoff.__doc__)
        ticket = argv[0]
        opts, refs = parse_args(argv[1:])
        if opts["--agent"] == "codex":
            raise Refuse(2, f"[{HANDOFF}] {CODEX_GOAL_CARD}")
        # CLOSE on a line of its own: roles.py reads a CLOSE card by its row
        prompt = f"/goal {ticket}." + (f" {opts['--note']}" if opts.get("--note") else "") + f"\n{CLOSE_LINE}\n{LOAD_LINE}"
        if not guard.CLOSE_OR_KEEP.search(prompt):
            raise Refuse(2, f"[{HANDOFF}] the prompt does not pass hooks/guard.py's card check")
        with card_lock():
            return handoff_locked(ticket, opts, refs, prompt)
    except Refuse as e:
        print(e, file=sys.stderr)
        return e.code


def handoff_locked(ticket, opts, refs, prompt):
    keys = [ticket, *refs]
    repo, superseded = check_duplicates(None, keys, opts["--role"], opts.get("--supersede"))
    parent = None if opts.get("--no-parent") else handoff_parent()
    name = opts.get("--name") or ticket.lower()
    cmd = [ORCA, "worktree", "create", "--name", name]
    cmd += ["--base-branch", opts["--base-branch"]] if opts.get("--base-branch") else []
    cmd += ["--parent-worktree", parent] if parent else ["--no-parent"]
    cmd += ["--agent", "claude", "--prompt", prompt]
    model, model_from = session_model(opts, False, HANDOFF)
    cmd += ["--model", model] if model else []
    for r in refs:
        cmd += ["--reference", r]
    cmd.append("--json")
    reason, mark = pressure_check(shlex.join(["orca", *cmd[1:]]))
    if reason:
        raise Refuse(3, f"[{HANDOFF}] not created, the pressure gate refused it (run this same command again once "
                        f"its `queue wait` ends with 0): {reason}")
    p, err = run(cmd)
    try:
        out = json.loads(p.stdout) if p else None
    except ValueError:
        out = None
    res = (out or {}).get("result") or {}
    path = worktree_path(res)
    if not (p and not p.returncode and out and out.get("ok") is not False and path):
        mark("failed", err)
        if out:
            print(json.dumps(out, ensure_ascii=False, indent=1), file=sys.stderr)
        refused = next((v for k, v in REFUSED_UNSTARTED.items() if p and k in p.stdout + p.stderr), None)
        raise Refuse(4, f"[{HANDOFF}] orca worktree create failed: {err}"
                        + (f". Nothing was created: {refused}" if refused else ""))
    mark("done")
    handle = handle_of(res)
    failed = []
    ledger_add(repo=repo, references=keys, role=opts["--role"], path=path, name=name, model=model,
               model_from=model_from)
    title = f"{ticket} {opts['--title']}" if opts.get("--title") else ticket
    p, err = run([ORCA, "worktree", "set", "--worktree", f"path:{path}", "--display-name", title, "--json"])
    if not p or p.returncode:
        failed.append(f"orca worktree set: {err}")
    p, err = run([ORCH, "sent", "add", "--to", name, "--kind", "card", "--channel", HANDOFF,
                  *(["--repo", gh] if (gh := github_repo(path)) else []), "-"], stdin=prompt)
    if not p or p.returncode:
        failed.append(f"orch sent add: {err}")
    for old in superseded:
        failed += supersede(old, path, title)
    started, detail = confirm_start(handle) if handle else (False, "the receipt names no agent handle")
    if not started:
        failed.append(f"the start is not confirmed ({detail}); read `orca terminal read --terminal {handle} --screen`")
    setup = start_close_setup(path, None, name)  # the start was just read; close-setup only closes the setup shell
    goal_input = start_goal_input(handle, prompt.splitlines()[0], name, path) if handle else None
    print(json.dumps({**res, "card": {"kind": "card", "path": path, "handle": handle, "parent": parent,
                                      "start": detail, "close_setup": setup, "model": model, "model_from": model_from,
                                      **({"goal_input": goal_input} if goal_input else {})}},
                     ensure_ascii=False, indent=1))
    if failed:
        raise Refuse(5, f"[{HANDOFF}] the card was created but: {'; '.join(failed)}. Do not create it again.")
    return 0


def main(argv):
    if argv[:1] == ["goal-after-idle"] and len(argv) in (3, 4):  # the detached helper start_goal_input starts
        return goal_after_idle(argv[1], argv[2], worktree=argv[3] if len(argv) == 4 else None)
    try:
        return create(argv)
    except Refuse as e:
        print(e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
