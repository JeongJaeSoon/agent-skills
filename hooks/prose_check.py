#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: points out write-plainly's fixed-string patterns in text about to be published.

Looks at the text a call is about to publish:
  - Bash `gh pr create|edit` (--title, --body, --body-file) and `git commit` (-m, --message, -F, --file),
    including a heredoc body,
  - a Linear or Jira MCP call that saves an issue, a comment or a document (its title, description, body or
    content fields).
Fenced and inline code is skipped. A hit adds one note listing the patterns found to the model's context and never
blocks or decides the call. The patterns are the deterministic subset of skills/write-plainly; anything that needs
judgment stays with that skill. AGENT_SKILLS_PROSE_CHECK=0 turns it off. Any error exits 0 silently.
"""
import json, os, re, shlex, sys

MAX_HITS = 8
TEXT_KEYS = {"title", "description", "body", "content", "commentBody", "comment", "summary"}
SAVE_TOOL = re.compile(r"^mcp__.*(?:linear|jira|atlassian).*__(?:save_|create|update|edit|add)", re.I)
PR_CMD = re.compile(r"\bgh\s+pr\s+(?:create|edit)\b")
COMMIT_CMD = re.compile(r"\bgit\b[^;&|\n]*\bcommit\b")  # a cheap prefilter; the parser below finds git's subcommand
PUBLISH = re.compile(f"{PR_CMD.pattern}|{COMMIT_CMD.pattern}")
SEPARATOR = re.compile(r"[;\n]|&&|\|\|")
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n\s*\2\s*(?:\n|$)", re.S)
FENCE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.S)
INLINE = re.compile(r"`[^`\n]*`")

# (pattern, what write-plainly says instead). Korean entries come from references/korean.md, English ones from
# references/english.md; a pattern belongs here only when a hit is almost never right.
PATTERNS = [
    (r"—|\s–\s", "em or en dash: end the sentence or use a comma"),
    (r"[“”‘’]", "curly quotes: use straight quotes"),
    (r"^\s*[-*]?\s*\*\*[^*\n]{1,40}[:：]\*\*", "bold label with a colon: write the item as a sentence"),
    (r"다양한", "다양한: name the things"),
    (r"효율적으로|원활한|핵심적인", "stock adjective: give the mechanism or the number"),
    (r"[을를]\s*통해", "~를 통해: use ~로"),
    (r"진행(?:했|하였|합니다|한다)|이루어(?:졌|지|집)|수행(?:했|하였)", "진행하다/이루어지다/수행하다 around a noun: use the verb"),
    (r"해당\s", "해당: name the thing"),
    (r"에\s*대(?:해|한)\s", "~에 대해/대한: use a particle"),
    (r"(?:으)?로\s*인(?:해|하여)", "~로 인해: say the cause"),
    (r"되어진|보여진|에\s*있어서", "translationese: plain verb or particle"),
    (r"것\s*같(?:습니다|다|아요)", "것 같다: state what you measured or label it a guess"),
    (r"좋은 질문|도움이 되셨|결론적으로|요약하자면", "chatbot phrase: delete it"),
    (r"\b(?:delve|tapestry|testament|underscores?|pivotal|showcas\w*|foster\w*|garner\w*|intricate|vibrant|"
     r"interplay|crucial|additionally)\b", "AI vocabulary: plain word"),
    (r"\b(?:serves as|stands as|boasts)\b", "fancy 'is': say is or has"),
    (r"\b(?:in order to|due to the fact that|it is important to note)\b", "filler phrase: cut it"),
    (r"\b(?:leverag\w*|utiliz\w*|facilitat\w*)\b", "plain word: use, help"),
    (r"\b(?:I hope this helps|let me know if|great question)\b", "chatbot phrase: delete it"),
]
PATTERNS = [(re.compile(p, re.I | re.M), why) for p, why in PATTERNS]


def files_text(paths):
    out = []
    for base, p in paths:
        if p == "-":
            continue
        full = os.path.join(base, os.path.expanduser(p))
        try:
            with open(full, encoding="utf-8") as f:
                out.append(f.read(200_000))
        except OSError:
            pass
    return out


def bash_text(command, cwd):
    if not PUBLISH.search(command):
        return None
    # A heredoc counts only when the command it feeds is the publishing one (`git commit -F - <<EOF`,
    # `-m "$(cat <<EOF`), not a `cat <<EOF > file` beside it.
    parts = [m.group(3) for m in HEREDOC.finditer(command)
             if PUBLISH.search(SEPARATOR.split(command[:m.start()])[-1])]
    try:
        # A newline separates commands like `;` does; inside a quoted message it only adds a harmless `;`.
        text = HEREDOC.sub(";", command).replace("\\\n", " ").replace("\n", " ; ")
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        words = list(lex)
    except ValueError:
        return "\n".join(parts) if parts else command
    take = {"--message", "--title", "--body"}
    read = {"--file", "--body-file"}
    # Short options that take a value, per command: "text" is the value itself, "file" a path to read.
    short = {"git": {"m": "text", "F": "file"}, "gh": {"t": "text", "b": "text", "F": "file"}}
    # A relative -F or --body-file path resolves where the command runs: after `cd <dir>` and git's own `-C <dir>`.
    # `cd` moves the shell for every later command; git's `-C` moves only its own invocation (`here`).
    shell = here = cwd or "."
    prog, git_globals, paths = None, False, []

    def add(kind, value):
        if value is None:
            return
        if kind == "text":
            parts.append(value)
        else:
            paths.append((here, value))

    # Only the publishing command's own arguments count: collection starts at `git … commit` or `gh pr create|edit`
    # and stops at the next shell operator.
    publishing = False
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        key, eq, val = w.partition("=")
        if set(w) <= set(";&|()"):
            prog, git_globals, publishing, here = None, False, False, shell
            continue
        if w in short and not publishing:
            prog, git_globals = w, w == "git"
        elif git_globals and not w.startswith("-") and words[i - 1] not in GIT_VALUE_OPTS:
            git_globals, publishing = False, w == "commit"  # git's subcommand, not any later "commit" word
        elif prog == "gh" and w in ("create", "edit") and words[i - 1] == "pr":
            publishing = True
        if nxt and w == "cd":
            shell = here = os.path.join(shell, os.path.expanduser(nxt))
        elif nxt and git_globals and w == "-C":
            here = os.path.join(here, os.path.expanduser(nxt))
        elif not publishing:
            continue
        elif (key if eq else w) in take | read:
            add("text" if (key if eq else w) in take else "file", val if eq else nxt)
        elif re.match(r"-[A-Za-z]", w):
            # A cluster such as -am "msg", -m"msg" or -Fmsg.txt: the value follows its letter or is the next word.
            for j, ch in enumerate(w[1:], 1):
                if ch in short[prog]:
                    add(short[prog][ch], w[j + 1:] or nxt)
                    break
    return "\n".join(parts + files_text(paths))


def mcp_text(args):
    out = []

    def walk(v, key=None):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and key in TEXT_KEYS:
            out.append(v)

    walk(args)
    return "\n".join(out)


def findings(text):
    text = INLINE.sub(" ", FENCE.sub(" ", text))
    hits = []
    for rx, why in PATTERNS:
        m = rx.search(text)
        if m:
            hits.append(f"\"{m.group(0).strip()}\" ({why})")
    return hits[:MAX_HITS]


def main():
    if os.environ.get("AGENT_SKILLS_PROSE_CHECK", "1") == "0":
        return
    event = json.load(sys.stdin)
    name, args = event.get("tool_name") or "", event.get("tool_input") or {}
    if name == "Bash":
        text = bash_text(args.get("command") or "", event.get("cwd"))
    elif SAVE_TOOL.search(name):
        text = mcp_text(args)
    else:
        return
    hits = findings(text or "")
    if hits:
        note = ("prose_check (write-plainly): the text this call publishes has " + "; ".join(hits) + ". If a hit is "
                "real, fix the text and publish again (edit the PR, amend the commit, or update the ticket); "
                "a quoted identifier or a template label is fine as it is.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note}},
                         ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
