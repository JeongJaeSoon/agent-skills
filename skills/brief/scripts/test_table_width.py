"""Offline tests for table_width.py, and a width check on the terminal examples in SKILL.md.

Run: python3 test_table_width.py
"""
import pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
from table_width import table_width, tables, text_width

assert text_width("검색 개선") == 9
assert text_width("⚠️ 로그만") == 9
assert text_width("▓▓▓▓░░░░ 4/8 #42") == 16
assert text_width("**검색 개선**") == 9
assert text_width("[acme/web#42](https://example.test/42)") == 11

found = list(tables("intro\n| a | 가나 |\n|---|---|\n| 🟢 | x |\n\nafter\n"))
assert found == [(2, [["a", "가나"], ["🟢", "x"]])], found
assert table_width(found[0][1]) == 2 + 4 + 3 * 2 + 1

# Every table in a markdown example of SKILL.md is what a report copies, so each must fit 78 columns,
# and a periodic table's cells must keep to their caps.
skill = (HERE.parent / "SKILL.md").read_text()
blocks = re.findall(r"```markdown\n(.*?)```", skill, re.S)
assert len(blocks) >= 2, len(blocks)
widths = [table_width(rows) for block in blocks for _, rows in tables(block)]
assert len(widths) >= 3 and max(widths) <= 78, widths
CAPS = {1: 14, 2: 22, 3: 26}
periodic = [rows for block in blocks for _, rows in tables(block) if rows[0] == ["", "리드", "진행", "다음"]]
assert len(periodic) >= 2, len(periodic)
for rows in periodic:
    for row in rows[1:]:
        for i, cap in CAPS.items():
            assert text_width(row[i]) <= cap, (row, i)

# Each sub-task of a lead gets its own └ row right under it, so some lead in the example shows at least two.
def is_sub(row):
    return row[1].replace("**", "").startswith("└")


def subtask_runs(rows):
    """Number of └ rows after each lead row; the first entry counts └ rows before any lead."""
    runs, n = [], 0
    for row in rows[1:]:
        if is_sub(row):
            n += 1
        else:
            runs.append(n)
            n = 0
    return runs + [n]


def check_periodic(rows):
    assert subtask_runs(rows)[0] == 0, "a └ row comes before any lead"
    for i, row in enumerate(rows[1:], 1):
        for cell in row:
            assert len(re.findall(r"#\d+", cell)) <= 1, ("several PRs in one cell", row)
        if not is_sub(row) and i + 1 < len(rows) and is_sub(rows[i + 1]):
            assert re.fullmatch(r"[▓░]{8} \d+/\d+|—", row[2]), ("a lead with └ rows keeps evidence out of its cell", row)


lead = ["", "리드", "진행", "다음"]
assert subtask_runs([lead, ["🟢", "a", "", ""], ["🟢", "└ #1 x", "", ""], ["🟡", "**└ #2 y**", "", ""], ["🟢", "b", "", ""]]) == [0, 2, 0]
for bad in (
    [lead, ["🟢", "└ #1 x", "", ""], ["🟢", "a", "", ""]],
    [lead, ["🟢", "a", "▓▓░░░░░░ 2/8 #1 #2", ""]],
    [lead, ["🟢", "a", "▓▓░░░░░░ 2/8 #1", ""], ["🟢", "└ #1 x", "", ""]],
):
    try:
        check_periodic(bad)
    except AssertionError:
        continue
    raise AssertionError(("check_periodic passed a bad table", bad))
for rows in periodic:
    check_periodic(rows)
assert max(n for rows in periodic for n in subtask_runs(rows)) >= 2

wide = "| " + "가" * 40 + " | b |\n|---|---|\n"
r = subprocess.run([sys.executable, str(HERE / "table_width.py"), "--cols", "78"], input=wide, capture_output=True, text=True)
assert r.returncode == 1 and r.stdout == "line 1: 88 cols > 78\n", (r.returncode, r.stdout)
r = subprocess.run([sys.executable, str(HERE / "table_width.py"), "--cols", "120"], input=wide, capture_output=True, text=True)
assert r.returncode == 0, r.stdout

print(f"ok: table_width, SKILL.md example tables {widths}")
