"""Run copyparty (argv as for `python -m copyparty`), opening a browser's plain link to a .md file as a document.

copyparty renders markdown only for `file.md?v` and sends the raw text otherwise, so a shared link to a report
shows its source. A request with no query string at all that accepts text/html gets the viewer; the viewer's
"view raw" link (`file.md?`) and non-browser clients still get the raw file.
"""
import sys

from copyparty import httpcli
from copyparty.__main__ import main

_handle_get = httpcli.HttpCli.handle_get


def handle_get(self):
    if "?" not in self.req and self.vpath.lower().endswith(".md") and \
            "text/html" in self.headers.get("accept", ""):
        self.uparam["v"] = ""
    return _handle_get(self)


httpcli.HttpCli.handle_get = handle_get
sys.exit(main(["copyparty", *sys.argv[1:]]))
