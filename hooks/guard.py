#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: the permission rules a plugin cannot ship as settings.

Deny: `orca orchestration check|inbox --terminal <handle>` for a handle that is not the caller's own
($ORCA_TERMINAL_HANDLE, set in every Orca terminal). It would read, and with --ack consume, another
agent's messages.

Deny: `orca worktree create --prompt` whose command text has no `CLOSE` or `KEEP` line, or is a lead's (a `MODE`
line, a `ROLE: lead <slug>` line, or the template's `<project>: lead` head) without all of `MODE`, `ROLE`, `DELEGATE`
and `PREDICATE`. A card whose first prompt lacks them reports done and idles for days, a lead without DELEGATE did the
work itself, and one without ROLE never gets the reorient hook's per-prompt reminder (dispatch-card §2).
The text is read as written: a prompt built from a file or variable is denied too.
`skills-sync card <brief>` (scripts/card.py) is checked on the brief file it names, with that helper's own rules,
and so is the lead test below; a relative path after a `cd` or `pushd`, or a check that cannot run, is denied.
Only a call bash would run counts: the words inside quotes, a heredoc body or a comment are text.
A wrapper (`env …`, `bash -c`, scripts/card.py run directly) is not seen: a guardrail, not a sandbox.

Deny: a lead's card started from a session that is itself a lead by roles.py (its `agent_type`, its `ROLE: lead`
record, or a first user prompt passing the same lead test; a `ROLE: none` record clears it). Only the human or the
top-level starts a lead (orchestrate references/brief.md "Lead").
A missing or unreadable transcript, or one restarted by /clear, gets no decision.

Deny: a foreground Bash call that polls with a sleep loop (`until/while/for …; do … sleep N` with N of 5 s or a
variable). Workers kept one running for up to ten minutes over a background call's output file; the
call's completion notice already wakes them, and `Monitor` runs a real until-loop.
A Monitor command gets every Bash rule here except this one, as a call that does not block the turn. hooks.json
names Monitor in the matcher: a matcher that meets it only through `Bash` gets it as a foreground Bash call, so the
gate would refuse the Monitor its own deny offers.

Allow, so workers never stall on a prompt hours after they loaded a skill:
- this plugin's skills
- `orca orchestration <verb>`, except reset, worker-abandon and gate-resolve
- `orch <subcommand>` (the program ledger and the landing gate), except heavy, which runs any command,
  set and init, which can change a program's merge policy, backfill, which rewrites the ledger, and land-exec,
  the land executor, whose round merges and belongs to its LaunchAgent

Ask: `python3 <checkout>/scripts/setup.py --actions-only`, the setup steps that write no Claude settings (setup-pc).
The dialog says what they do only when <checkout> is this plugin's root or the install dialog's `checkout`;
another clone's setup.py still gets `ask`, with a sentence that promises nothing. `ask` shows the human a permission dialog even in auto mode, so these persistent jobs start only on a click.

An allow here skips the auto mode classifier, so it covers only one simple command whose words bash passes
as written: outside quotes, plain characters only (no operators, redirections, substitutions, expansions or
variables other than $ORCA_TERMINAL_HANDLE), so the allowed prefix cannot carry another command or verb. Everything else gets no decision and takes the normal permission flow.

Before all of that, the rules of lib/danger.py (pattern kill, prune, `compose down -v`, forced worktree removal,
writes to the user's own settings and the rest of its RULES, on Bash and on Edit, Write and NotebookEdit) and
lib/coordination.py (AskUserQuestion, foreground waits and a send to /dev/null from a role session, by roles.py's
matrix). A deny there wins; a warn rides along with whatever else is decided; an ask turns an allow into a dialog.
Each rule has a key in ~/.claude/agent-skills.json, and `false` puts back the behavior from before it.
Warn (`guard.deliver_load`): a card's edit inside its working directory before it loaded deliver-ticket
(lib/skillload.py); `deny` blocks such an edit.
A check of this hook that raises lets the call through with a warning and a line in
$AGENT_SKILLS_STATE/guard-errors.jsonl: the hook never stops a session over its own fault.
"""
import functools, json, os, pathlib, re, shlex, string, sys

import roles
from lib import config, coordination, danger, shell, skillload

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
PLUGIN = "agent-skills"
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "Monitor")
MAILBOX = re.compile(r"\borca\s+orchestration\s+(?:check|inbox)\b[^;&|\n]*?\s--terminal(?:\s+|=)(\S+)")
OWN_VAR = ("$ORCA_TERMINAL_HANDLE", "${ORCA_TERMINAL_HANDLE}")
# Only as a whole value (`--terminal $X`, `--terminal="$X"`): inside a word it could spell an excluded verb.
OWN_REF = re.compile(r"(?<![^\s\"'=])(?:\$\{ORCA_TERMINAL_HANDLE\}|\$ORCA_TERMINAL_HANDLE)(?![^\s\"'])")
PLAIN = set(string.ascii_letters + string.digits + " \t_./:=,@%+-")
# The hook sees unexpanded text: the variable is the caller's own handle unless the command rebinds it
# anywhere, nested shells and quotes included. A guardrail against mistakes, not a sandbox.
REBINDS = re.compile(r"(?<![$\w{])ORCA_TERMINAL_HANDLE=(?!=)|\$\{ORCA_TERMINAL_HANDLE:?=|"
                     r"\b(?:read|printf\s+-v)\b[^;&|\n]*\bORCA_TERMINAL_HANDLE")
# In command position only: a grep or echo that quotes the words is not a card being made.
CARD = re.compile(r"(?:^|[;&|(])\s*orca\s+worktree\s+create\b", re.M)
# The helper reads its brief from a file this hook can open, so the card checks run on that file (dispatch-card §3).
CARD_HELPER = re.compile(r"(?:^|[;&|(`])\s*(?:\S*/)?skills-sync\s+card\s+(\S+)", re.M)
CHDIR = re.compile(r"(?:^|[;&|(])\s*(?:cd|pushd)\b", re.M)
LEAD_FROM_LEAD = ("This session is a lead, and a lead does not start another lead. Give the card a "
                  "worker brief (the one at the top of orchestrate references/brief.md, or its \"Single worker\") "
                  "without the Lead's MODE or ROLE line or `<project>: lead` head; only the human or the "
                  "top-level starts a lead.")
CLOSE_OR_KEEP = re.compile(r"\b(?:CLOSE|KEEP)\b")
MODE, LEAD_HEAD, LEAD_ROLE = roles.MODE, roles.LEAD_HEAD, roles.LEAD_ROLE
LEAD_LINES = ("MODE", "DELEGATE", "PREDICATE")
# The orchestrate refused-act rule lets a session follow only a block carrying a gate marker.
SLEEP_MARK = "[gate:sleep-loop]"
# The hook's name after the marker: the model sees only the reason text, not which hook wrote it.
SLEEP_SOURCE = "(agent-skills hooks/guard.py)"
SLEEP_LOOP = re.compile(r"(?:^|[;&|(\s])(?:until|while|for)\b[\s\S]*?\bdo\b[\s\S]*?\bsleep\s+(\$|\d+)")
ORCA_ASK = {"reset", "worker-abandon", "gate-resolve"}
ORCH_ASK = {"heavy", "set", "init", "backfill", "land-exec"}
SETUP_ASK = (f"{PLUGIN} setup: install the 15-minute sync job, turn on the default-on automations (janitor, pressure-watch, "
             "and watch-prs/watch-mentions when their keys are set) and print the check table. It writes no Claude settings.")


def background(tool, args):
    """Monitor runs its command beside the turn, like a Bash call with run_in_background."""
    return tool == "Monitor" or args.get("run_in_background") is True


def decide(decision, reason):
    return decision, reason


def emit(decision, reason, warnings):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision:
        out["hookSpecificOutput"].update(permissionDecision=decision, permissionDecisionReason=reason)
    warnings = list(dict.fromkeys(warnings))
    if warnings:
        out["systemMessage"] = "\n".join(warnings)
        out["hookSpecificOutput"]["additionalContext"] = "\n".join(warnings)
    if decision or warnings:
        print(json.dumps(out))


def simple_words(cmd):
    """The words of one simple command, or None when bash could split or expand the text into anything else.
    Outside quotes only plain characters pass: no operators, and no brace, glob, tilde or history expansion."""
    text, quote = OWN_REF.sub("H", cmd), None
    for c in text:
        if c < " " and c != "\t":  # newline, carriage return and other controls
            return None
        if quote:  # '...' is literal; in "..." only $ ` \ are special
            if c == quote:
                quote = None
            elif quote == '"' and c in "$`\\":
                return None
        elif c in "'\"":
            quote = c
        elif c not in PLAIN:
            return None
    return None if quote else shlex.split(cmd) or None


def own_skills():
    return {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}


is_lead = roles.lead_text
command_text = shell.command_text


def lead_session(who):
    """The calling session is a lead (roles.py): a lead does not start another lead."""
    return who().role == "lead"


def helper_card(brief, cwd, moves):
    """(the prompt the helper would send, None) or (None, the deny reason): the helper's own checks on that file.
    `moves`: the command changes directory first, so a relative path would be read from the wrong place."""
    brief = brief.strip("'\"")
    if set(brief) & set("$`*?"):
        return None, "Write the brief's path out in `skills-sync card <brief>`; this hook reads that file and cannot expand it."
    path = pathlib.Path(brief).expanduser()
    if moves and not path.is_absolute():
        return None, "This command changes directory, so give `skills-sync card` the brief's absolute path."
    full = path if path.is_absolute() else pathlib.Path(cwd or ".") / path
    if full.is_dir():
        return None, f"`skills-sync card {brief}` names a directory, not a brief file; give it the brief's file path."
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import card
        try:
            return card.compose(str(full)), None
        except card.Refuse as e:
            return None, str(e)
    except Exception as e:  # a check that cannot run does not let the card through
        return None, f"The guard hook could not check the brief of `skills-sync card` ({type(e).__name__}: {e})."


def bash(cmd, background, who, cwd=None):
    loop = None if background else SLEEP_LOOP.search(cmd)
    # A short retry loop (sleep 1-4) is left alone; polling loops seen in practice slept 5 to 30 s.
    if loop and (loop[1] == "$" or int(loop[1]) >= 5):
        return decide("deny", f"{SLEEP_MARK} {SLEEP_SOURCE} Waiting is fine; only this foreground form is refused. A foreground sleep loop "
                              "blocks this session until it ends. Run the same command with run_in_background: true and end your turn (its completion notice wakes you), or watch "
                              "the condition with the Monitor tool. A call already running in the background wakes "
                              "you by itself: do not poll its output file.")
    own = os.environ.get("ORCA_TERMINAL_HANDLE")
    for handle in MAILBOX.findall(cmd):
        handle = handle.strip("'\"")
        if handle != own and (REBINDS.search(cmd) or handle not in OWN_VAR):
            return decide("deny", f"check/inbox --terminal {handle} reads another terminal's Orca mailbox and can "
                                  f"consume its messages. Your own handle is {own or 'unknown'}.")
    if CARD.search(cmd) and "--prompt" in cmd and "--help" not in cmd:
        missing = []
        if not CLOSE_OR_KEEP.search(cmd):
            missing.append("CLOSE or KEEP")
        lead = is_lead(cmd)
        if lead:
            missing += [w for w in LEAD_LINES if not re.search(rf"\b{w}\b", cmd)]
            if not LEAD_ROLE.search(cmd):
                missing.append("`ROLE: lead <slug>`")
        if missing:
            return decide("deny", f"The card's --prompt has no {' and no '.join(missing)} line. Create the card from "
                                  "its brief file with `skills-sync card <brief>` (dispatch-card §3; `--agent codex` for a "
                                  "supervised Codex worker), which writes those lines "
                                  "into --prompt, or write them literally in --prompt (not from a file or variable): "
                                  "`CLOSE: 완료 기준을 채우고 결과를 적으면 end-session §8대로 같은 턴에 스스로 닫는다`, or KEEP "
                                  "when this session will reuse the card; a lead also gets its MODE, `ROLE: lead <slug>`, DELEGATE "
                                  "and PREDICATE lines, copied from orchestrate references/brief.md \"Lead\" (dispatch-card §2).")
        if lead and lead_session(who):
            return decide("deny", LEAD_FROM_LEAD)
    for helper in CARD_HELPER.finditer(command_text(cmd)):
        brief = re.compile(r"\S+").match(cmd, helper.start(1))[0]  # the masked text blanks a quoted path
        if brief.startswith("-"):  # `--help` or no brief: the helper prints its usage and creates nothing
            continue
        prompt, refused = helper_card(brief, cwd, bool(CHDIR.search(cmd)))
        if refused:
            return decide("deny", refused)
        if is_lead(prompt) and lead_session(who):
            return decide("deny", LEAD_FROM_LEAD)
    words = simple_words(cmd)
    if not words or REBINDS.search(cmd):
        return None, None
    # The verb must come first: a leading flag could put an excluded verb out of sight.
    if words[:2] == ["orca", "orchestration"] and len(words) > 2 and words[2] not in ORCA_ASK and words[2][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orca orchestration {words[2]}")
    if words[0] == "orch" and len(words) > 1 and words[1] not in ORCH_ASK and words[1][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orch {words[1]} (the program ledger and landing gate)")
    reason = setup_actions(words)
    if reason:
        return decide("ask", reason)
    return None, None


def setup_actions(words):
    """The dialog's reason for `python3 <agent-skills checkout>/scripts/setup.py --actions-only [--orca-run]`, else None."""
    if words[0] != "python3" or set(words[2:]) - {"--orca-run"} != {"--actions-only"}:
        return None
    script = pathlib.Path(words[1])
    if not (script.is_absolute() and script.name == "setup.py" and script.parent.name == "scripts"
            and (script.parents[1] / "skills" / "setup-pc").is_dir()):
        return None
    # SETUP_ASK vouches for the code, so only for the plugin running this hook or the checkout the install dialog
    # named (setup-pc step 3, same default).
    named = os.environ.get("CLAUDE_PLUGIN_OPTION_CHECKOUT", "").strip() or "~/src/agent-skills"
    if script.parents[1].resolve() in {ROOT.resolve(), pathlib.Path(named).expanduser().resolve()}:
        return SETUP_ASK
    return (f"{PLUGIN} setup: runs `{script}`, which is not this plugin's checkout or the one the install dialog named; "
            "it may do anything.")


def rules(tool, args, event, who):
    """[danger.Finding] of lib/danger.py and lib/coordination.py for this call."""
    cfg = config.load()
    if tool in SHELL_TOOLS:
        cmd = args.get("command") or ""
        if not isinstance(cmd, str):
            raise TypeError(f"{tool} command is not a string")
        return (danger.check_bash(cmd, cfg, event.get("cwd"), who())
                + coordination.check_bash(cmd, background(tool, args), who(), cfg))
    if tool in WRITE_TOOLS:
        found = danger.check_write(args.get("file_path") or args.get("notebook_path"), cfg)
        try:  # its own fault never drops the danger findings above
            found += skillload.check_write(event, who(), cfg)
        except Exception as e:
            config.log_error("guard", e)
        return found
    if tool == "AskUserQuestion":
        return coordination.check_ask(who(), cfg)
    return []


def decision(tool, args, event, who):
    """This hook's own decision before lib's rules: (decision or None, reason)."""
    if tool in SHELL_TOOLS:
        return bash(args.get("command") or "", background(tool, args), who, event.get("cwd"))
    if tool == "Skill":
        skill = str(args.get("skill") or "")
        name = skill.removeprefix(f"{PLUGIN}:")
        # A bare name may resolve to a user or project skill of the same name instead of this plugin's.
        shadowed = name == skill and any((pathlib.Path(d) / ".claude/skills" / name).exists()
                                         for d in (pathlib.Path.home(), event.get("cwd") or "."))
        if name in own_skills() and not shadowed:
            return decide("allow", f"{PLUGIN} skill {name}")
    return None, None


def failed(e, warnings):
    config.log_error("guard", e)
    warnings.append(f"agent-skills hooks/guard.py could not finish its checks ({type(e).__name__}) and let this call "
                    "through without them; see guard-errors.jsonl in the agent-skills state directory.")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    tool, args = event.get("tool_name"), event.get("tool_input") or {}
    args = args if isinstance(args, dict) else {}
    who = functools.cache(lambda: roles.session(event))
    found, warnings = [], []
    try:
        found = rules(tool, args, event, who)
    except Exception as e:
        failed(e, warnings)
    denied = [f.reason for f in found if f.level == "deny"]
    if denied:
        return emit("deny", "\n".join(denied), warnings)
    warnings += [f.reason for f in found if f.level == "warn"]
    try:
        verdict, reason = decision(tool, args, event, who)
    except Exception as e:
        verdict, reason = None, None
        failed(e, warnings)
    asks = [f.reason for f in found if f.level == "ask"]
    if asks and verdict != "deny":
        verdict, reason = "ask", "\n".join(asks)
    emit(verdict, reason, warnings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
