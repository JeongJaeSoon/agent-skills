"""Run copyparty (argv as for `python -m copyparty`) with two changes for a read-only share.

- Hidden paths are not served. copyparty leaves dotfiles out of listings, search and zips but still serves
  /.git/config, lists /.git/?ls and zips /.git/?zip to anyone who knows the name; here any path segment starting
  with "." answers 404 (copyparty's own /.cpr/ resources excepted), and a zip of selected files drops them.
- A browser's plain link to a .md file opens as a document. copyparty renders markdown only for `file.md?v`; a
  request with no query string that accepts text/html gets the viewer, while the viewer's "view raw" link
  (`file.md?`) and non-browser clients still get the raw file.

Written against the copyparty version pinned in share_server.PINS; the Live tests in test_share_server.py
cover both.
"""
import sys

from copyparty import httpcli
from copyparty.__main__ import main

Cli = httpcli.HttpCli
_handle_get, _handle_post, _tx_zip = Cli.handle_get, Cli.handle_post, Cli.tx_zip


def hidden(vpath):
    parts = vpath.split("/")
    return parts[0] != ".cpr" and any(p.startswith(".") for p in parts)


def handle_get(self):
    if hidden(self.vpath):
        return self.tx_404()
    if "?" not in self.req and self.vpath.lower().endswith(".md") and \
            "text/html" in self.headers.get("accept", ""):
        self.uparam["v"] = ""
    return _handle_get(self)


def handle_post(self):
    return self.tx_404() if hidden(self.vpath) else _handle_post(self)


def tx_zip(self, fmt, uarg, vpath, vn, rem, items):
    return _tx_zip(self, fmt, uarg, vpath, vn, rem, [i for i in items if not hidden(i)])


Cli.handle_get, Cli.handle_post, Cli.tx_zip = handle_get, handle_post, tx_zip
sys.exit(main(["copyparty", *sys.argv[1:]]))
