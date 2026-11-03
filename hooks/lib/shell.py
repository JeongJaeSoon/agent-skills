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
# A quoted or escaped operator character is text: it is swapped for a private-use stand-in before shlex, which drops
# the quotes and would otherwise read `">"` as a redirection, and swapped back in the words.
LITERAL = {"<": "", ">": "", "&": "", "|": ""}
UNLITERAL = str.maketrans({v: k for k, v in LITERAL.items()})
# A `$` or `~` bash leaves as text (single-quoted or escaped; `~` quoted at all) keeps a stand-in in the words, so a
# path check does not expand it.
TEXT_DOLLAR, TEXT_TILDE = "", ""
# Marks digits written right against `<` or `>`: only those are a redirection's fd (`2>f`, not `echo 2 > f`).
FD = ""
FD_DIGITS = re.compile(r"(?<![^\s;&|(])\d+(?=[<>])")
# Wrapper options that take the next word as their value.
WRAPPER_OPTS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group", "--host", "--prompt",
             "--chdir", "--role", "--type", "--other-user", "--command-timeout", "--close-from"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
}


@dataclasses.dataclass
class Command:
    words: list  # argv as bash would pass it, the command first; redirections left out
    redirects: list  # (fd, operator, target), fd "" when not written
    masked: str  # this command's text with quoted text blanked
    raw: str  # its original text
    piped_from: bool = False  # a pipe feeds it
    piped_to: bool = False  # it feeds a pipe
    assigns: list = dataclasses.field(default_factory=list)  # `NAME=value` words before it, `env`'s included
    span: tuple = (0, 0)  # its offsets in the whole command line; a substitution's commands lie inside it

    @property
    def name(self):
        return os.path.basename(self.words[0]) if self.words else ""


def command_text(cmd, marks=None):
    """`cmd` with what bash does not run as a command blanked, offsets kept: quoted text, heredoc bodies and
    comments. `$(…)` and backticks inside double quotes or an unquoted heredoc body stay, since bash runs them.
    From a construct this parser does not follow (`${…}`, whose quotes nest; arithmetic; `case` in `$(…)`; a
    backslash in backticks; a heredoc delimiter it cannot read) on, nothing is blanked. `marks`, when given, gets
    the (start, end) of each comment ("comment") and of each single-quoted or backslash-escaped run, where `$` and
    `~` are text ("literal"), and the offset it stopped following at ("unsure")."""
    out, n = list(cmd), len(cmd)
    opened, closed = set(), set()  # just past an opening backtick (a word starts) and past a closing `)` or backtick
    marks = {"comment": [], "literal": [], "unsure": None} if marks is None else marks

    class Unsure(Exception):
        pass

    def blank(i, j, literal=False):
        out[i:j] = " " * (j - i)
        if literal:
            marks["literal"].append((i, j))

    def expanding(i, end, close):
        """Double-quoted text (close='"') or an unquoted heredoc body (close=None) from i; returns the index after it."""
        while i < end:
            c = cmd[i]
            if c == close:
                return i + 1
            if cmd.startswith(("${", "$((", "$["), i):
                raise Unsure(i)
            if c == "\\":
                blank(i, min(i + 2, end), literal=True)
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
                blank(i, min(i + 2, n), literal=True)
                i += 2
            elif c == "'":
                ansi = i > 0 and cmd[i - 1] == "$" and not escaped(i - 1)
                j = i + 1
                while j < n and cmd[j] != "'":
                    j += 2 if ansi and cmd[j] == "\\" else 1
                blank(i + 1, min(j, n), literal=True)
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
                marks["comment"].append((i, n if eol < 0 else eol))
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
        marks["unsure"] = e.args[0]
        return "".join(out[:e.args[0]]) + cmd[e.args[0]:]
    return "".join(out)


def quotes_only(cmd, marks):
    """`cmd` with quoted text, escapes and comments blanked by a plain quote scan: the fallback past the point
    command_text stops following, so an operator inside quotes or a comment there is not a command boundary. A
    `$(…)` or backtick substitution inside double quotes stays, since bash runs it. Single-quoted and escaped runs go
    to marks["literal"], comments to marks["comment"]."""
    out, n = list(cmd), len(cmd)

    def blank(i, j, literal=False):
        out[i:j] = " " * (j - i)
        if literal:
            marks["literal"].append((i, j))

    def heredoc(i):
        """The `<<` delimiter at i: (index after it, (strip tabs, word, expands))."""
        strip = cmd.startswith("-", i)
        i += strip
        while i < n and cmd[i] in " \t":
            i += 1
        j = i
        while j < n and cmd[j] not in " \t\n;&|<>()":
            j += 1
        word = cmd[i:j]
        quoted = any(q in word for q in "'\"\\")
        return j, (strip, re.sub(r"['\"\\]", "", word), not quoted)

    def bodies(i, pending):
        """Blank the heredoc bodies from line start i (an expanding body keeps its substitutions); the index after."""
        for strip, word, expands in pending:
            start = i
            while i < n:
                eol = cmd.find("\n", i)
                eol = n if eol < 0 else eol
                if (cmd[i:eol].lstrip("\t") if strip else cmd[i:eol]) == word:
                    break
                i = eol + 1
            body_end = min(i, n)
            if expands:
                j = start
                while j < body_end:
                    if cmd.startswith("$(", j):
                        j = scan(j + 2, ")")
                    elif cmd[j] == "`":
                        j = scan(j + 1, "`")
                    else:
                        blank(j, j + 1)
                        j += 1
            else:
                blank(start, body_end)
            eol = cmd.find("\n", i)
            eol = n if eol < 0 else eol
            blank(i, eol)  # the delimiter line is not a command either
            i = eol + 1
        return i

    def scan(i, close):
        """From i to the `close` that ends this level (`)` of `$(`, a backtick, or the end); the index after it."""
        depth, pending = 0, []
        while i < n:
            c = cmd[i]
            if close == ")" and c == ")" and not depth or close == "`" and c == "`":
                return i + 1
            if cmd.startswith("<<<", i):
                i += 3
                continue
            if cmd.startswith("<<", i):
                i, doc = heredoc(i + 2)
                pending.append(doc)
                continue
            if c == "\n" and pending:
                i, pending = bodies(i + 1, pending), []
                continue
            if c == "\\":
                blank(i, min(i + 2, n), literal=True)
                i += 2
            elif c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
                eol = cmd.find("\n", i)
                eol = n if eol < 0 else eol
                blank(i, eol)
                marks["comment"].append((i, eol))
                i = eol
            elif c == "'":
                j = cmd.find("'", i + 1)
                j = n if j < 0 else j
                blank(i + 1, j, literal=True)
                i = j + 1
            elif c == '"':
                i = double(i + 1)
            elif cmd.startswith("$(", i):
                i = scan(i + 2, ")")
            elif c == "`":
                i = scan(i + 1, "`")
            else:
                depth += {"(": 1, ")": -1}.get(c, 0) if close == ")" else 0
                i += 1
        return n

    def double(j):
        while j < n and cmd[j] != '"':
            if cmd[j] == "\\":
                blank(j, min(j + 2, n), literal=True)
                j += 2
            elif cmd.startswith("$(", j):
                j = scan(j + 2, ")")
            elif cmd[j] == "`":
                j = scan(j + 1, "`")
            else:
                blank(j, j + 1)
                j += 1
        return j + 1

    scan(0, None)
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
            fd = words.pop()[1:] if words and words[-1].startswith(FD) and t[0] in "<>" else ""
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
        elif w in WRAPPER_OPTS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or ASSIGN.match(words[i])):
                if words[i] == "--":
                    i += 1
                    while i < len(words) and ASSIGN.match(words[i]):
                        i += 1
                    break
                i += 2 if words[i] in WRAPPER_OPTS[w] else 1
        else:
            break
    return words[i:]


def lift(cmd, masked):
    """[(offset, raw, masked)]: `cmd` with each `$(…)`, `<(…)`, `>(…)` and backtick substitution replaced by a same-length
    `$___` word, then each substitution's body the same way, by position. The outer command then keeps its
    arguments and redirections whole: `echo "$(cat f)" > out` still writes `out`. An opener without its close is
    left in place, for `commands` to cut at."""
    inner = []

    def put(s, e, body):
        nonlocal cmd, masked
        inner.append((body[0], cmd[body[0]:body[1]], masked[body[0]:body[1]]))
        pad = "$" + "_" * (e - s - 1)
        cmd, masked = cmd[:s] + pad + cmd[e:], masked[:s] + pad + masked[e:]

    while True:
        for s in reversed([m.start() for m in re.finditer(r"\$\(|<\(|>\(", masked)]):
            depth, j = 0, s + 2
            while j < len(masked) and (masked[j] != ")" or depth):
                depth += {"(": 1, ")": -1}.get(masked[j], 0)
                j += 1
            if j < len(masked):
                put(s, j + 1, (s + 2, j))
                break
        else:
            break
    while m := re.search(r"`([^`]*)`", masked):
        put(m.start(), m.end(), m.span(1))
    return [(0, cmd, masked)] + sorted(inner, key=lambda x: x[0])


def commands(cmd):
    """Each simple command `cmd` would run, the outer ones first, then `$(…)` and backtick contents. A piece this
    reader cannot split is left out: a guardrail, not a sandbox."""
    marks, out = {"comment": [], "literal": [], "unsure": None}, []
    masked = command_text(cmd, marks)
    if marks["unsure"] is not None:
        u = marks["unsure"]
        fallback = {"comment": [], "literal": []}
        masked = masked[:u] + quotes_only(cmd, fallback)[u:]
        marks["comment"] += [(max(s, u), e) for s, e in fallback["comment"] if e > u]
        marks["literal"] += [(max(s, u), e) for s, e in fallback["literal"] if e > u]
    chars = list(cmd)
    for s, e in marks["comment"]:  # shlex is not told about comments: `x # > f` writes nothing
        chars[s:e] = " " * (e - s)
    for s, e in marks["literal"]:
        for i in range(s, e):
            chars[i] = TEXT_DOLLAR if chars[i] == "$" else chars[i]
    for i, c in enumerate(chars):
        if masked[i] != c and c in LITERAL:
            chars[i] = LITERAL[c]
        elif masked[i] != c and c == "~":
            chars[i] = TEXT_TILDE
    for offset, text, masked in lift("".join(chars), masked):
        start, prev_op = 0, ""
        cuts = [(m.start(), m.end(), m.group()) for m in CUT.finditer(masked)] + [(len(text), len(text), "")]
        for at, end, op in cuts:
            raw, mask = text[start:at], masked[start:at]
            if mask.strip():
                toks = tokens(FD_DIGITS.sub(lambda m: FD + m.group() if mask[m.start():m.end()] == m.group()
                                            else m.group(), raw))
                if toks:
                    words, redirects = split_words(toks)
                    words = [w.replace(FD, "") for w in words]
                    run = strip_prefix(words)
                    assigns = [w.translate(UNLITERAL) for w in words[:len(words) - len(run)] if ASSIGN.match(w)]
                    words = [w.translate(UNLITERAL) for w in run]
                    redirects = [(fd, o, t.translate(UNLITERAL)) for fd, o, t in redirects]
                    if words or redirects:
                        out.append(Command(words, redirects, mask, raw.translate(UNLITERAL),
                                           piped_from=prev_op == "|", piped_to=op == "|", assigns=assigns,
                                           span=(offset + start, offset + at)))
            start, prev_op = end, op
    return out
