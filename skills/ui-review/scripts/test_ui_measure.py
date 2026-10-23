"""Fixture tests for ui_measure.py: the clean page passes every check, the broken page fails each one.

Run: python3 test_ui_measure.py   (needs Chrome; CHROME overrides the binary)
"""
import contextlib, functools, http.server, io, json, pathlib, sys, tempfile, threading

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import ui_measure

if not ui_measure.find_chrome():
    sys.exit("test_ui_measure: no Chrome found; set CHROME")


def run(*args):
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d) / "r.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            rc = ui_measure.main([*map(str, args), "--json", str(out)])
        return rc, json.loads(out.read_text()), printed.getvalue()


def status(result, width):
    return {c["id"]: c["status"] for c in result[str(width)]["checks"]}


rc, res, printed = run(HERE / "testdata/pass.html")
assert rc == 0, printed
for w in (1280, 390):
    assert set(status(res, w).values()) == {"PASS"}, (w, status(res, w))
assert "| row-height | PASS | 0 |" in printed and "#### 390px" in printed

rc, res, printed = run(HERE / "testdata/fail.html", "--baseline", HERE / "testdata/pass.html")
assert rc == 1 and "판정: FAIL" in printed
FAILS = {"row-height", "control-column", "edge-alignment", "vertical-center", "spacing-grid", "link-underline",
         "table-head", "field-consistency", "bare-glyph", "toggle-as-button", "baseline"}
WARNS = {"value-wrap", "generic-action", "no-color"}
for w in (1280, 390):
    st = status(res, w)
    assert {k for k, v in st.items() if v == "FAIL"} >= FAILS, (w, st)
    assert {k for k, v in st.items() if v == "WARN"} >= WARNS, (w, st)
assert status(res, 390)["horizontal-overflow"] == "FAIL" and status(res, 1280)["horizontal-overflow"] == "PASS"

checks = {c["id"]: c for c in res["1280"]["checks"]}
assert any("60px, the others 48px" in i["detail"] for i in checks["row-height"]["items"]), checks["row-height"]
assert any(i["detail"].startswith("definition list") for i in checks["row-height"]["items"]), checks["row-height"]
assert any("underlined link outside running text" in i["detail"] for i in checks["link-underline"]["items"])
assert any(i["detail"].startswith('"!"') for i in checks["bare-glyph"]["items"])
assert any(i["detail"] == "icon with no name or tooltip" for i in checks["bare-glyph"]["items"]), checks["bare-glyph"]
assert any("3px is off the 4px grid" in i["detail"] for i in checks["spacing-grid"]["items"])
assert any(i["detail"].startswith("field ") for i in checks["baseline"]["items"]), checks["baseline"]

# Options loosen a rule without editing the script: an allowed value and an ignored subtree.
rc, res, _ = run(HERE / "testdata/fail.html", "--widths", "1280", "--allow", "3,7", "--exclude", ".shifted", "--exclude", ".odd")
assert status(res, 1280)["spacing-grid"] == "PASS" and status(res, 1280)["bare-glyph"] == "PASS", status(res, 1280)

# --before runs page JS before measuring, e.g. to open the screen under review.
rc, res, _ = run(HERE / "testdata/pass.html", "--widths", "1280", "--before", "document.querySelector('main').insertAdjacentHTML('beforeend', '<span>!</span>')")
assert status(res, 1280)["bare-glyph"] == "FAIL"

# --scope on the set itself still measures it: the scope element is a target too.
for sel in (".kv", "section.card"):
    rc, res, _ = run(HERE / "testdata/fail.html", "--widths", "1280", "--scope", sel)
    assert status(res, 1280)["row-height"] == "FAIL", (sel, status(res, 1280))

# With --scope the page's own width does not fail the part under review.
rc, res, _ = run(HERE / "testdata/fail.html", "--widths", "390", "--scope", ".kv")
assert status(res, 390)["horizontal-overflow"] == "PASS", status(res, 390)

# A scope wider than the screen overflows even when its parent is wider still.
rc, res, _ = run(HERE / "testdata/fail.html", "--widths", "390", "--scope", ".inner")
assert status(res, 390)["horizontal-overflow"] == "FAIL", status(res, 390)

# Unbroken text past the screen fails even with an ignored subtree on the page, and inside a scope.
LONG = "document.querySelector('main').insertAdjacentHTML('beforeend', '<div id=long style=\"width: 100px; white-space: nowrap\">' + 'A'.repeat(120) + '</div>')"
for extra in ([], ["--scope", "main"]):
    rc, res, _ = run(HERE / "testdata/pass.html", "--widths", "390", "--before", LONG, *extra)
    assert status(res, 390)["horizontal-overflow"] == "FAIL", (extra, status(res, 390))

# A page or scope that cannot be measured is an error, not a PASS of something else.
with contextlib.redirect_stderr(io.StringIO()) as err:
    assert ui_measure.main([str(HERE / "testdata/pass.html"), "--widths", "1280", "--scope", "#missing"]) == 2
    assert ui_measure.main([str(HERE / "testdata/missing.html"), "--widths", "1280"]) == 2
    assert ui_measure.main([str(HERE / "testdata/pass.html"), "--widths", "1280", "--scope", "#closed"]) == 2
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "testdata")))
    server.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    assert ui_measure.main([base + "/missing.html", "--widths", "1280"]) == 2
    with contextlib.redirect_stdout(io.StringIO()):
        assert ui_measure.main([base + "/pass.html", "--widths", "1280"]) == 0
    server.shutdown()
e = err.getvalue()
assert "matches nothing" in e and "cannot open" in e and "HTTP 404" in e and "nothing visible to measure in #closed" in e, e

print("test_ui_measure: ok")
