#!/usr/bin/env python3
"""The catalog build renders every markdown heading in docs/skills*.md as a heading, never as a paragraph."""
import pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "catalog.html"
        p = subprocess.run([sys.executable, "-I", str(HERE / "build.py"), str(out)],
                           cwd=REPO, capture_output=True, text=True)
        assert p.returncode == 0, p.stdout + p.stderr
        page = out.read_text()
    leaked = re.findall(r"<p>#{1,6} [^<]{0,40}", page)
    assert not leaked, f"headings rendered as paragraphs: {leaked[:5]}"
    for name in ("skills.md", "skills.en.md", "skills.ja.md"):
        want = sum(1 for ln in (REPO / "docs" / name).read_text().splitlines() if ln.startswith("#### "))
        prefix = {"skills.md": "", "skills.en.md": "en-", "skills.ja.md": "ja-"}[name]
        got = len(re.findall(rf'<h4 id="{prefix}(?!en-|ja-)', page))
        assert got == want, f"{name}: {want} #### lines, {got} <h4> with id prefix {prefix!r}"
    print("test_build: ok")


if __name__ == "__main__":
    main()
