"""log.sh: the header once, single-line cells, and a leading =, +, -, @ made inert. Run: python3 test_log.py"""
import atexit, pathlib, shutil, subprocess, tempfile

LOG_SH = pathlib.Path(__file__).resolve().parent / "log.sh"
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-log-sh-"))
atexit.register(shutil.rmtree, tmp, True)


def log(path, *cells):
    return subprocess.run(["bash", str(LOG_SH), str(path), *cells], capture_output=True, text=True)


path = tmp / "deep" / "dir" / "trail.tsv"
r = log(path, "frame", "sized the work", "know the size first", "commit 3a9f1c2", "open")
assert r.returncode == 0, r.stderr
log(path, "build", "second row", "why", "PR 12", "tests green")
rows = [line.split("\t") for line in path.read_text().splitlines()]
assert rows[0] == ["ts", "phase", "decision", "why", "evidence", "result"], rows[0]
assert len(rows) == 3 and all(len(r) == 6 for r in rows), rows
assert rows[1][1:] == ["frame", "sized the work", "know the size first", "commit 3a9f1c2", "open"], rows[1]
assert rows[1][0].endswith("Z") and "T" in rows[1][0], rows[1][0]

# Tabs and newlines inside a cell would break the row; a leading formula character would run in a spreadsheet.
path = tmp / "hostile.tsv"
log(path, "p", "line one\nline two\twith a tab", "=HYPERLINK(\"x\")", "+1", "@SUM(A1)")
log(path, "p", "-2", "ok", "ok", "ok")
rows = [line.split("\t") for line in path.read_text().splitlines()]
assert len(rows) == 3 and all(len(r) == 6 for r in rows), rows
assert rows[1][2] == "line one line two with a tab", rows[1]
assert rows[1][3:] == ["'=HYPERLINK(\"x\")", "'+1", "'@SUM(A1)"], rows[1]
assert rows[2][2] == "'-2", rows[2]

# Wrong argument count is refused and writes nothing.
r = log(tmp / "none.tsv", "only", "two")
assert r.returncode == 1 and "usage:" in r.stderr and not (tmp / "none.tsv").exists(), (r.returncode, r.stderr)
print("test_log: ok")
