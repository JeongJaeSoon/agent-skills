"""Width of each markdown table as a terminal draws it, to check a report fits before sending.

Run: python3 table_width.py [--cols 78] < report.md
Prints one line per table (first line number, width) and exits 1 when any table is wider than --cols.
"""
import argparse, re, sys, unicodedata

# Claude Code draws a table with a border and one space of padding on each side of every cell.
# Overcounts on the safe side: VS16 after an already-wide emoji adds 1.
BORDER_PER_COL, BORDER_EDGE = 3, 1


def text_width(s):
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s).replace("**", "").replace("`", "")
    w = 0
    for ch in s:
        if ch == "\ufe0f":
            # VS16 turns a narrow symbol such as ⚠ into a two-column emoji.
            w += 1
        elif unicodedata.combining(ch) or ch == "\u200d":
            continue
        else:
            w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def cells(line):
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def tables(text):
    """Yield (first line number, rows) for each table; the |---| separator row is dropped."""
    rows, start = [], 0
    for n, line in enumerate(text.splitlines() + [""], 1):
        if line.lstrip().startswith("|"):
            if not rows:
                start = n
            if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line):
                rows.append(cells(line))
        elif rows:
            yield start, rows
            rows = []


def table_width(rows):
    ncol = max(len(r) for r in rows)
    widths = [max(text_width(r[i]) if i < len(r) else 0 for r in rows) for i in range(ncol)]
    return sum(widths) + BORDER_PER_COL * ncol + BORDER_EDGE


def cut(text, width):
    """The head of text that fits in width columns."""
    out, w = [], 0
    for ch in text:
        w += text_width(ch)
        if w > width:
            break
        out.append(ch)
    return "".join(out)


def md_row(row):
    return "| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"


def shown(text, cap):
    """text as a column capped at cap columns draws it: whole, or its head and `…`."""
    return text if text_width(text) <= cap else cut(text, cap - 1) + "…"


def apart(texts, floor):
    """The smallest cap, floor at least, at which texts that differ still differ once the longer ones are cut."""
    texts = set(texts)
    top = max(map(text_width, texts), default=0)
    return next((k for k in range(floor, top) if len({shown(t, k) for t in texts}) == len(texts)), max(top, floor))


def fit(rows, cols, cuttable, floor=10, deco=None, keep=()):
    """rows (the header first) with the longest cells of the cuttable columns cut to their head and `…` until the table
    fits in cols, the widest column first and none below its floor. The cut words are not listed anywhere: a name has
    to read as itself, so a column in keep never goes below the width at which its distinct cells still differ (two
    cards `xx-agent-actions-lead` and `xx-agent-wizard-lead` stay two), and the rest below floor. deco(row, column,
    text) dresses a body cell after the cut (a bold, a └), so the `…` never lands inside the markup. When no cut gets
    it into cols, nothing is cut: the table overflows whole, for the width check to report."""
    deco = deco or (lambda i, c, s: s)
    low = {c: max(floor, apart([r[c] for r in rows[1:]], floor) if c in keep else 0) for c in cuttable}
    cap, first, spent = {}, None, set()
    while True:
        out, plain = [list(rows[0])], []
        for i, r in enumerate(rows[1:], 1):
            row = list(r)
            for c, k in cap.items():
                row[c] = shown(row[c], k)
            plain.append(row)
            out.append([deco(i, c, s) for c, s in enumerate(row)])
        over = table_width(out) - cols
        width = {c: max(text_width(r[c]) for r in plain) for c in cuttable} if plain else {}
        wide = {c: w for c, w in width.items() if w > low[c] and c not in spent}
        first = first or out
        if over <= 0:
            return out
        if not wide:
            return first
        # The widest column gives way first, but only down to the next widest: the cut is shared out among the long
        # cells instead of one column paying all of it.
        c = max(wide, key=wide.get)
        runner_up = max((w for k, w in width.items() if k != c), default=0)
        if cap.get(c) == (new := max(low[c], wide[c] - over, min(runner_up, wide[c] - 1))):
            spent.add(c)
        cap[c] = new


def tiers(keep=(), floor=10, tight=6):
    """The (keep, floor) pairs to try in turn, the most readable first: every cut cell at floor columns or more, then
    two fewer at a time down to tight, always with the names in keep kept apart. A table that cannot fit that way
    overflows whole and the width check reports it: two rows that read alike cannot be told apart, nor checked."""
    return [(keep, f) for f in range(floor, tight - 1, -2)]


def fit_best(rows, cols, cuttable, keep=(), floor=10, **kw):
    """fit with the first of tiers() that brings the table into cols; the last one's (whole) table when none does."""
    for k, f in tiers(keep, floor):
        out = fit(rows, cols, cuttable, keep=k, floor=f, **kw)
        if table_width(out) <= cols:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=78)
    cols = ap.parse_args().cols
    over = False
    for start, rows in tables(sys.stdin.read()):
        w = table_width(rows)
        over |= w > cols
        print(f"line {start}: {w} cols{' > ' + str(cols) if w > cols else ''}")
    sys.exit(1 if over else 0)


if __name__ == "__main__":
    main()
