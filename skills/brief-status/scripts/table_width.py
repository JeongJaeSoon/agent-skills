"""Width of each markdown table as a terminal draws it, to check a report fits before sending.

Run: python3 table_width.py [--cols 78] < report.md
Prints one line per table (first line number, width) and exits 1 when any table is wider than --cols.
"""
import argparse, re, sys, unicodedata

# Claude Code draws a table with a border and one space of padding on each side of every cell.
# Overcounts on the safe side: an escaped \| splits a cell, and VS16 after an already-wide emoji adds 1.
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
    return [c.strip() for c in line.strip().strip("|").split("|")]


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
