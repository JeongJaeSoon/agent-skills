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

Deny: a lead's card started from a session that is itself a lead, known by its `agent_type` (`claude --agent
agent-skills:lead`), its `ROLE: lead` record from the reorient hook, or its transcript's first user prompt passing the
same lead test. Only the human or the top-level starts a lead (orchestrate references/brief.md "Lead").
A missing or unreadable transcript, or one restarted by /clear, gets no decision.

Deny: a foreground Bash call that polls with a sleep loop (`until/while/for …; do … sleep N` with N of 5 s or a
variable). Workers kept one running for up to ten minutes over a background call's output file; the
call's completion notice already wakes them, and `Monitor` runs a real until-loop.

Allow, so workers never stall on a prompt hours after they loaded a skill:
- this plugin's skills
- `orca orchestration <verb>`, except reset, worker-abandon and gate-resolve
- `orch <subcommand>` (the program ledger and the landing gate), except heavy, which runs any command,
  set and init, which can change a program's merge policy, and backfill, which rewrites the ledger

Ask: `python3 <checkout>/scripts/setup.py --actions-only`, the setup steps that write no Claude settings (setup-pc).
`ask` shows the human a permission dialog even in auto mode, so these persistent jobs start only on a click.

An allow here skips the auto mode classifier, so it covers only one simple command whose words bash passes
as written: outside quotes, plain characters only (no operators, redirections, substitutions, expansions or
variables other than $ORCA_TERMINAL_HANDLE), so the allowed prefix cannot carry another command or verb. Everything else gets no decision and takes the normal permission flow.
"""
import json, os, pathlib, re, shlex, string, sys

ROOT = pathlib.Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or pathlib.Path(__file__).resolve().parents[1])
ROLES = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "roles"
PLUGIN = "agent-skills"
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
CLOSE_OR_KEEP = re.compile(r"\b(?:CLOSE|KEEP)\b")
MODE = re.compile(r"""(?m)(?:^|--prompt[\s=]+["']?)\s*MODE\b""")
# The head is a line of its own, or opens the prompt; "take the lead" or "(lead: x)" in a worker's text is neither.
LEAD_HEAD = re.compile(r"""(?m)(?:^|--prompt[\s=]+)\s*["']?[\w.-]+:\s*lead\s*(?:["']|$)""")
LEAD_LINES = ("MODE", "DELEGATE", "PREDICATE")
# A whole line reorient.py records, the prompt's closing quote (or a transcript's `</command-args>`) allowed after it;
# the template's unfilled `<slug>`, `ROLE: none`, trailing words and a `\n` escape the shell passes through literally
# do not count. It also marks a lead, like MODE: a resumed coordinator's prompt carries only this line.
LEAD_ROLE = re.compile(r"""(?m)(?:^|--prompt[\s=]+["']?)[ \t]*ROLE:?[ \t]+lead[ \t]+[\w.-]+[ \t]*(?:$|["']|</command-args>)""")
# The orchestrate refused-act rule lets a session follow only a block carrying a gate marker.
SLEEP_MARK = "[gate:sleep-loop]"
SLEEP_LOOP = re.compile(r"(?:^|[;&|(\s])(?:until|while|for)\b[\s\S]*?\bdo\b[\s\S]*?\bsleep\s+(\$|\d+)")
ORCA_ASK = {"reset", "worker-abandon", "gate-resolve"}
ORCH_ASK = {"heavy", "set", "init", "backfill"}
SETUP_ASK = (f"{PLUGIN} setup: install the 15-minute sync job, turn on the default-on automations (janitor, pressure-watch, "
             "and watch-prs/watch-mentions when their keys are set) and print the check table. It writes no Claude settings.")


def decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                             "permissionDecisionReason": reason}}))


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


def is_lead(text):
    return bool(MODE.search(text) or LEAD_HEAD.search(text) or LEAD_ROLE.search(text))


def declared_lead(session):
    """The session took the lead role with a later `ROLE: lead <slug>` prompt, recorded by reorient.py."""
    if not re.fullmatch(r"[\w-]{1,128}", session or ""):
        return False
    try:
        return json.loads((ROLES / f"{session}.json").read_text()).get("role") == "lead"
    except (OSError, ValueError, AttributeError):
        return False


def started_as_lead(transcript):
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("type") != "user" or rec.get("isMeta"):
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, list):  # tool results carry no text block
                    content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if content:
                    return is_lead(content)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return False


def bash(cmd, background=False, transcript=None, agent=None, session=None):
    loop = None if background else SLEEP_LOOP.search(cmd)
    # A short retry loop (sleep 1-4) is left alone; polling loops seen in practice slept 5 to 30 s.
    if loop and (loop[1] == "$" or int(loop[1]) >= 5):
        return decide("deny", f"{SLEEP_MARK} Waiting is fine; only this foreground form is refused. A foreground sleep loop "
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
            return decide("deny", f"The card's --prompt has no {' and no '.join(missing)} line. Write it literally in "
                                  "--prompt (not from a file or variable): `CLOSE: 완료 기준을 채우고 결과를 적으면 "
                                  "end-session §8대로 같은 턴에 스스로 닫는다`, or KEEP when this session will reuse the card; "
                                  "a lead also gets its MODE, `ROLE: lead <slug>`, DELEGATE and PREDICATE lines, copied from orchestrate "
                                  "references/brief.md \"Lead\" (dispatch-card §2).")
        if lead and (agent == "agent-skills:lead" or declared_lead(session)
                     or transcript and started_as_lead(transcript)):
            return decide("deny", "This session is a lead, and a lead does not start another lead. Give the card a "
                                  "worker brief (the one at the top of orchestrate references/brief.md, or its \"Single worker\") "
                                  "without the Lead's MODE or ROLE line or `<project>: lead` head; only the human or the "
                                  "top-level starts a lead.")
    words = simple_words(cmd)
    if not words or REBINDS.search(cmd):
        return
    # The verb must come first: a leading flag could put an excluded verb out of sight.
    if words[:2] == ["orca", "orchestration"] and len(words) > 2 and words[2] not in ORCA_ASK and words[2][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orca orchestration {words[2]}")
    if words[0] == "orch" and len(words) > 1 and words[1] not in ORCH_ASK and words[1][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orch {words[1]} (the program ledger and landing gate)")
    if setup_actions(words):
        return decide("ask", SETUP_ASK)


def setup_actions(words):
    """`python3 <agent-skills checkout>/scripts/setup.py --actions-only [--orca-run]`."""
    if words[0] != "python3" or set(words[2:]) - {"--orca-run"} != {"--actions-only"}:
        return False
    script = pathlib.Path(words[1])
    return (script.is_absolute() and script.name == "setup.py" and script.parent.name == "scripts"
            and (script.parents[1] / "skills" / "setup-pc").is_dir())


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool, args = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Bash":
        bash(args.get("command") or "", args.get("run_in_background") is True, event.get("transcript_path"),
             event.get("agent_type"), str(event.get("session_id") or ""))
    elif tool == "Skill":
        skill = str(args.get("skill") or "")
        name = skill.removeprefix(f"{PLUGIN}:")
        # A bare name may resolve to a user or project skill of the same name instead of this plugin's.
        shadowed = name == skill and any((pathlib.Path(d) / ".claude/skills" / name).exists()
                                         for d in (pathlib.Path.home(), event.get("cwd") or "."))
        if name in own_skills() and not shadowed:
            decide("allow", f"{PLUGIN} skill {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
