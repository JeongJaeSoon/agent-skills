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

# Every table in a markdown example of SKILL.md is what a report copies, so each must fit 80 columns.
skill = (HERE.parent / "SKILL.md").read_text()
blocks = re.findall(r"```markdown\n(.*?)```", skill, re.S)
assert len(blocks) >= 2, len(blocks)
widths = [table_width(rows) for block in blocks for _, rows in tables(block)]
assert len(widths) >= 3 and max(widths) <= 80, widths

wide = "| " + "가" * 40 + " | b |\n|---|---|\n"
r = subprocess.run([sys.executable, str(HERE / "table_width.py"), "--cols", "80"], input=wide, capture_output=True, text=True)
assert r.returncode == 1 and r.stdout == "line 1: 88 cols > 80\n", (r.returncode, r.stdout)
r = subprocess.run([sys.executable, str(HERE / "table_width.py"), "--cols", "120"], input=wide, capture_output=True, text=True)
assert r.returncode == 0, r.stdout

print(f"ok: table_width, SKILL.md example tables {widths}")
