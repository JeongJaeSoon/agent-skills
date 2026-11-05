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
        if ch == "️":
            # VS16 turns a narrow symbol such as ⚠ into a two-column emoji.
            w += 1
        elif unicodedata.combining(ch) or ch == "‍":
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


def md_row(row):
    return "| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"


def as_lines(rows, lead):
    """The body rows of a table as one bullet line each, for a table wider than the width: the first `lead` cells
    (a status mark and a name) lead the line and the rest follow after ` — `, joined by ` · `, an empty or `—` cell
    left out. No cell is cut, because a name or a reason cut to fit reads alike in every row."""
    out = []
    for row in rows:
        rest = [c for c in row[lead:] if c and c not in ("—", "–")]
        out.append("- " + " ".join(c for c in row[:lead] if c) + (f" — {' · '.join(rest)}" if rest else ""))
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
