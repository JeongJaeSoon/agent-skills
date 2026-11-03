"""Reading a Bash command the way bash would run it: which words are commands, and each command's own arguments.

`command_text` finds what runs (quoted text, heredoc bodies and comments blanked). `commands` cuts that at the
operators and splits each command's original text into words with `shlex`, so a path, URL or option value is read
whole: blanking would have erased it.
"""
import dataclasses, os, re, shlex

# Words that run the next word as the command.
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time", "exec", "command", "nohup"}
ASSIGN = re.compile(r"[A-Za-z_]\w*=")
REDIRECT = {">", ">>", ">|", "&>", "&>>", ">&", "<", "<<<", "<&", "<>"}
# Operators between commands; `&` and `|` inside a redirection (`2>&1`, `&>`, `>|`) are not.
CUT = re.compile(r"\$\(|<\(|>\(|&&|\|\||\|&|(?<![<>])&(?![>&])|(?<!>)\||[;()\n`]")


@dataclasses.dataclass
class Command:
    words: list  # argv as bash would pass it, the command first; redirections left out
    redirects: list  # (fd, operator, target), fd "" when not written
    masked: str  # this command's text with quoted text blanked
    raw: str  # its original text
    piped_from: bool = False  # a pipe feeds it
    piped_to: bool = False  # it feeds a pipe

    @property
    def name(self):
        return os.path.basename(self.words[0]) if self.words else ""


def command_text(cmd):
    """`cmd` with what bash does not run as a command blanked, offsets kept: quoted text, heredoc bodies and
    comments. `$(…)` and backticks inside double quotes or an unquoted heredoc body stay, since bash runs them.
    From a construct this parser does not follow (`${…}`, whose quotes nest; arithmetic; `case` in `$(…)`; a
    backslash in backticks; a heredoc delimiter it cannot read) on, nothing is blanked."""
    out, n = list(cmd), len(cmd)
    opened, closed = set(), set()  # just past an opening backtick (a word starts) and past a closing `)` or backtick

    class Unsure(Exception):
        pass

    def blank(i, j):
        out[i:j] = " " * (j - i)

    def expanding(i, end, close):
        """Double-quoted text (close='"') or an unquoted heredoc body (close=None) from i; returns the index after it."""
        while i < end:
            c = cmd[i]
            if c == close:
                return i + 1
            if cmd.startswith(("${", "$((", "$["), i):
                raise Unsure(i)
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

    def escaped(j):  # the character at j follows an odd run of backslashes
        return (j - len(cmd[:j].rstrip("\\"))) % 2 == 1

    def starts_word(i):
        """`#` at i opens a comment: it starts a word, line continuations (`\\<newline>`) removed."""
        while i >= 2 and cmd[i - 1] == "\n" and escaped(i - 1):
            i -= 2
        # A subshell's `)` ends a word, a substitution's does not: `(x)#` is a comment, `$(x)#` and `` `x`# `` are not.
        return i == 0 or i in opened or (cmd[i - 1] in " \t\n;&|()" and i not in closed and not escaped(i - 1))

    def heredoc(i):
        """The delimiter word after `<<` at i, quotes removed: (index after it, (strip tabs, word, expands) or None)."""
        strip = cmd.startswith("-", i)
        i += strip
        while i < n and cmd[i] in " \t":
            i += 1
        # A delimiter with a `$` ($'…', $"…", $$) or an escape bash would rewrite ("…\…", \<newline>) is not read
        # (None), since a misread delimiter could match a later line and blank the commands before it.
        word, quoted, sure = "", False, True
        while i < n and cmd[i] not in " \t\n;&|<>()":
            c = cmd[i]
            if c == "$":
                sure = False
                word += c
                i += 1
            elif c in "'\"":
                j = cmd.find(c, i + 1)
                j = n if j < 0 else j
                part = cmd[i + 1:j]
                sure = sure and not (c == '"' and "\\" in part)
                word, quoted = word + part, True
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
        depth, pending = 0, []
        # Bash cuts a backtick substitution at the next backtick before parsing it: nothing in it runs past there.
        end, prev = (cmd.find("`", i) % (n + 1) if close == "`" else n), i
        if close == "`":
            opened.add(i)
        while i < n:
            if i > end:
                raise Unsure(prev)
            c, prev = cmd[i], i
            if c == close and (close != ")" or depth == 0):
                closed.add(i + 1)
                return i + 1
            # Bash also rewrites backslashes inside backticks before running them, and runs a substitution inside
            # an arithmetic expression's quotes.
            if (cmd.startswith(("${", "$((", "((", "$["), i) or close == "`" and c == "\\"
                    or c == "'" and cmd[i - 2:i] == "$$"
                    or close == ")" and re.match(r"case\b", cmd[i:]) and starts_word(i)):
                raise Unsure(i)
            if c == "(":
                depth += 1
            elif c == ")" and depth:
                depth -= 1
            if c == "\\":
                blank(i, min(i + 2, n))
                i += 2
            elif c == "'":
                ansi = i > 0 and cmd[i - 1] == "$" and not escaped(i - 1)
                j = i + 1
                while j < n and cmd[j] != "'":
                    j += 2 if ansi and cmd[j] == "\\" else 1
                blank(i + 1, min(j, n))
                i = j + 1
            elif c == '"':
                i = expanding(i + 1, n, '"')
            elif c == "`":
                i = command(i + 1, "`")
            elif cmd.startswith(("$(", "<(", ">("), i):  # its `)` does not end the word
                i = command(i + 2, ")")
            elif c == "#" and starts_word(i):
                eol = cmd.find("\n", i)
                blank(i, n if eol < 0 else eol)
                i = n if eol < 0 else eol
            elif cmd.startswith("<<<", i):  # a here-string, whose suffix is not a heredoc
                i += 3
            elif cmd.startswith("<<", i):
                at, (i, doc) = i, heredoc(i + 2)
                # Unread, its body would be scanned as commands and a quote in it could blank what follows. A
                # subshell's heredoc is not followed either.
                if not doc or depth:
                    raise Unsure(at)
                pending.append(doc)
            elif c == "\n" and pending:
                i, pending = bodies(i + 1, pending), []
            else:
                i += 1
        if close == "`":  # the scan ran out before the closing backtick
            raise Unsure(prev)
        return n

    try:
        command(0, None)
    except Unsure as e:
        return "".join(out[:e.args[0]]) + cmd[e.args[0]:]
    return "".join(out)


def tokens(text):
    """shlex words of one command's text with redirection operators split off; None when the quotes do not balance
    (the cut fell inside a quote this reader did not follow)."""
    lex = shlex.shlex(text, posix=True, punctuation_chars="<>&|")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError:
        return None


def split_words(toks):
    """(argv, redirects) from a command's tokens."""
    words, redirects, i = [], [], 0
    while i < len(toks):
        t = toks[i]
        if t in REDIRECT or t.startswith(("<<", ">>")) and set(t) <= set("<>&|-"):
            fd = words.pop() if words and words[-1].isdigit() and t[0] in "<>" else ""
            target = toks[i + 1] if i + 1 < len(toks) else ""
            redirects.append((fd, t, target))
            i += 2
            continue
        words.append(t)
        i += 1
    return words, redirects


def strip_prefix(words):
    """The words from the command bash runs: leading assignments, shell keywords and `sudo`/`env` wrappers dropped."""
    i = 0
    while i < len(words):
        w = words[i]
        if ASSIGN.match(w) or w in KEYWORDS:
            i += 1
        elif w in ("sudo", "env"):
            i += 1
            while i < len(words) and (words[i].startswith("-") or ASSIGN.match(words[i])):
                i += 1
        else:
            break
    return words[i:]


def commands(cmd):
    """Each simple command `cmd` would run, `$(…)` and backtick contents included, in order. A piece this reader
    cannot split is left out: a guardrail, not a sandbox."""
    masked = command_text(cmd)
    out, start, prev_op = [], 0, ""
    cuts = [(m.start(), m.end(), m.group()) for m in CUT.finditer(masked)] + [(len(cmd), len(cmd), "")]
    for at, end, op in cuts:
        raw, mask = cmd[start:at], masked[start:at]
        if mask.strip():
            toks = tokens(raw)
            if toks:
                words, redirects = split_words(toks)
                words = strip_prefix(words)
                if words or redirects:
                    out.append(Command(words, redirects, mask, raw, piped_from=prev_op == "|", piped_to=op == "|"))
        start, prev_op = end, op
    return out
