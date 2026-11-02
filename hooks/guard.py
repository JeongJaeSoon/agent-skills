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
The dialog says what they do only when <checkout> is this plugin's root or the install dialog's `checkout`;
another clone's setup.py still gets `ask`, with a sentence that promises nothing. `ask` shows the human a permission dialog even in auto mode, so these persistent jobs start only on a click.

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
# The helper reads its brief from a file this hook can open, so the card checks run on that file (dispatch-card §3).
CARD_HELPER = re.compile(r"(?:^|[;&|(`])\s*(?:\S*/)?skills-sync\s+card\s+(\S+)", re.M)
CHDIR = re.compile(r"(?:^|[;&|(])\s*(?:cd|pushd)\b", re.M)
LEAD_FROM_LEAD = ("This session is a lead, and a lead does not start another lead. Give the card a "
                  "worker brief (the one at the top of orchestrate references/brief.md, or its \"Single worker\") "
                  "without the Lead's MODE or ROLE line or `<project>: lead` head; only the human or the "
                  "top-level starts a lead.")
CLOSE_OR_KEEP = re.compile(r"\b(?:CLOSE|KEEP)\b")
MODE = re.compile(r"""(?m)(?:^|--prompt[\s=]+["']?)\s*MODE\b""")
# The head is a line of its own, or opens the prompt; "take the lead" or "(lead: x)" in a worker's text is neither.
LEAD_HEAD = re.compile(r"""(?m)(?:^|--prompt[\s=]+)\s*["']?[\w.-]+:\s*lead\s*(?:["']|$)""")
LEAD_LINES = ("MODE", "DELEGATE", "PREDICATE")
# A whole line reorient.py records, the prompt's closing quote (or a transcript's `</command-args>`) allowed after it;
# the template's unfilled `<slug>`, `ROLE: none`, trailing words and a `\n` escape the shell passes through literally
# do not count. It also marks a lead, like MODE: a resumed lead's prompt carries only this line.
LEAD_ROLE = re.compile(r"""(?m)(?:^|--prompt[\s=]+["']?)[ \t]*ROLE:?[ \t]+lead[ \t]+[\w.-]+[ \t]*(?:$|["']|</command-args>)""")
# The orchestrate refused-act rule lets a session follow only a block carrying a gate marker.
SLEEP_MARK = "[gate:sleep-loop]"
# The hook's name after the marker: the model sees only the reason text, not which hook wrote it.
SLEEP_SOURCE = "(agent-skills hooks/guard.py)"
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


def lead_session(agent, session, transcript):
    return agent == "agent-skills:lead" or declared_lead(session) or bool(transcript and started_as_lead(transcript))


def command_text(cmd):
    """`cmd` with what bash does not run as a command blanked, offsets kept: quoted text, heredoc bodies and
    comments. `$(…)` and backticks inside double quotes or an unquoted heredoc body stay, since bash runs them."""
    out, n, opened = list(cmd), len(cmd), set()  # opened: just past an opening backtick, where a word starts

    def blank(i, j):
        out[i:j] = " " * (j - i)

    def expanding(i, end, close):
        """Double-quoted text (close='"') or an unquoted heredoc body (close=None) from i; returns the index after it."""
        while i < end:
            c = cmd[i]
            if c == close:
                return i + 1
            if c == "\\":
                blank(i, min(i + 2, end))
                i += 2
            elif cmd.startswith("$(", i):
                i = command(i + 2, ")")
            elif c == "`":
                i = command(i + 1, "`")
            else:
                blank(i, i + 1)
                i += 1
        return end

    def bodies(i, pending):
        for strip, delim, expands in pending:
            start = i
            while i < n:
                eol = cmd.find("\n", i)
                eol = n if eol < 0 else eol
                line = cmd[i:eol]
                if expands and line.endswith("\\"):  # bash joins the next line, which may end the body sooner
                    return n
                if (line.lstrip("\t") if strip else line) == delim:
                    if expands:
                        expanding(start, i, None)
                    else:
                        blank(start, i)
                    blank(i, eol)
                    i = eol + 1
                    break
                i = eol + 1
            else:  # no terminator line: the delimiter was misread, so nothing after it is taken for a body
                return n
        return i

    def starts_word(i):
        """`#` at i opens a comment: it starts a word, line continuations (`\\<newline>`) removed."""
        def escaped(j):  # the character at j follows an odd run of backslashes
            return (j - len(cmd[:j].rstrip("\\"))) % 2 == 1

        while i >= 2 and cmd[i - 1] == "\n" and escaped(i - 1):
            i -= 2
        # Not after `)` or a closing backtick: in `$(x)#` and `` `x`# `` the # is inside a word.
        return i == 0 or i in opened or (cmd[i - 1] in " \t\n;&|(" and not escaped(i - 1))  # `\\ #` is a comment, `\ #` is not

    def heredoc(i):
        """The delimiter word after `<<` at i, quotes removed: (index after it, (strip tabs, word, expands) or None)."""
        strip = cmd.startswith("-", i)
        i += strip
        while i < n and cmd[i] in " \t":
            i += 1
        # A delimiter whose escapes bash would rewrite ("…\…", $'…\…', \<newline>) is not read: its body is left
        # unblanked, since a misread delimiter could match a later line and blank the commands before it.
        word, quoted, sure, ansi = "", False, True, False
        while i < n and cmd[i] not in " \t\n;&|<>()":
            c = cmd[i]
            if c == "$" and cmd[i + 1:i + 2] in ("'", '"'):  # $'EOF' and $"EOF" delimit with EOF
                ansi = cmd[i + 1] == "'"
                i += 1
            elif c in "'\"":
                j = cmd.find(c, i + 1)
                j = n if j < 0 else j
                part = cmd[i + 1:j]
                sure = sure and not ((c == '"' or ansi) and "\\" in part)
                word, quoted, ansi = word + part, True, False
                blank(i + 1, j)
                i = j + 1
            elif c == "\\":
                sure = sure and cmd[i + 1:i + 2] != "\n"
                word, quoted = word + cmd[i + 1:i + 2], True
                i += 2
            else:
                word += c
                i += 1
        sure = sure and "`" not in word and cmd[i:i + 1] != "("  # `<<$(x)`: bash reads the substitution as the word
        return i, ((strip, word, not quoted) if sure and (word or quoted) else None)

    def command(i, close):
        depth, arith, pending = 0, 0, []
        if close == "`":
            opened.add(i)
        while i < n:
            c = cmd[i]
            if c == close and (close != ")" or depth == 0):
                return i + 1
            if c == "(":
                depth += 1
            elif c == ")" and depth:
                depth -= 1
            elif cmd.startswith("$[", i):  # old arithmetic, where `<<` is a shift too
                arith += 1
            elif c == "]" and arith:
                arith -= 1
            if c == "\\":
                blank(i, min(i + 2, n))
                i += 2
            elif c == "'":
                ansi = i > 0 and cmd[i - 1] == "$"
                j = i + 1
                while j < n and cmd[j] != "'":
                    j += 2 if ansi and cmd[j] == "\\" else 1
                blank(i + 1, min(j, n))
                i = j + 1
            elif c == '"':
                i = expanding(i + 1, n, '"')
            elif c == "`":
                i = command(i + 1, "`")
            elif cmd.startswith("$(", i):
                i = command(i + 2, ")")
            elif c == "#" and starts_word(i):
                eol = cmd.find("\n", i)
                blank(i, n if eol < 0 else eol)
                i = n if eol < 0 else eol
            elif cmd.startswith("<<<", i):  # a here-string, whose suffix is not a heredoc
                i += 3
            # Inside parentheses `<<` may be a shift (`$((1<<2))`); a subshell's heredoc then stays unblanked.
            elif not depth and not arith and cmd.startswith("<<", i):
                i, doc = heredoc(i + 2)
                if doc:
                    pending.append(doc)
            elif c == "\n" and pending:
                i, pending = bodies(i + 1, pending), []
            else:
                i += 1
        return n

    command(0, None)
    return "".join(out)


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


def bash(cmd, background=False, transcript=None, agent=None, session=None, cwd=None):
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
                                  "its brief file with `skills-sync card <brief>` (dispatch-card §3), which writes those lines "
                                  "into --prompt, or write them literally in --prompt (not from a file or variable): "
                                  "`CLOSE: 완료 기준을 채우고 결과를 적으면 end-session §8대로 같은 턴에 스스로 닫는다`, or KEEP "
                                  "when this session will reuse the card; a lead also gets its MODE, `ROLE: lead <slug>`, DELEGATE "
                                  "and PREDICATE lines, copied from orchestrate references/brief.md \"Lead\" (dispatch-card §2).")
        if lead and lead_session(agent, session, transcript):
            return decide("deny", LEAD_FROM_LEAD)
    for helper in CARD_HELPER.finditer(command_text(cmd)):
        brief = re.compile(r"\S+").match(cmd, helper.start(1))[0]  # the masked text blanks a quoted path
        if brief.startswith("-"):  # `--help` or no brief: the helper prints its usage and creates nothing
            continue
        prompt, refused = helper_card(brief, cwd, bool(CHDIR.search(cmd)))
        if refused:
            return decide("deny", refused)
        if is_lead(prompt) and lead_session(agent, session, transcript):
            return decide("deny", LEAD_FROM_LEAD)
    words = simple_words(cmd)
    if not words or REBINDS.search(cmd):
        return
    # The verb must come first: a leading flag could put an excluded verb out of sight.
    if words[:2] == ["orca", "orchestration"] and len(words) > 2 and words[2] not in ORCA_ASK and words[2][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orca orchestration {words[2]}")
    if words[0] == "orch" and len(words) > 1 and words[1] not in ORCH_ASK and words[1][:1].isalpha():
        return decide("allow", f"{PLUGIN}: orch {words[1]} (the program ledger and landing gate)")
    reason = setup_actions(words)
    if reason:
        return decide("ask", reason)


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


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool, args = event.get("tool_name"), event.get("tool_input") or {}
    if tool == "Bash":
        bash(args.get("command") or "", args.get("run_in_background") is True, event.get("transcript_path"),
             event.get("agent_type"), str(event.get("session_id") or ""), event.get("cwd"))
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
