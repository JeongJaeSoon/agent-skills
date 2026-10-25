"""Run copyparty (argv as for `python -m copyparty`) with two changes for a read-only share.

- Hidden paths are not served. copyparty leaves dotfiles out of listings, search and zips but still serves
  /.git/config, lists /.git/?ls and zips /.git/?zip to anyone who knows the name; here a request whose path, or
  the real path it resolves to, has a segment starting with "." answers 404 (GETs of copyparty's own /.cpr/
  resources excepted), and a zip of selected files drops them.
- A browser's plain link to a .md file opens as a document. copyparty renders markdown only for `file.md?v`; a
  request with no query string that accepts text/html gets the viewer, while the viewer's "view raw" link
  (`file.md?`) and non-browser clients still get the raw file.

Written against the copyparty version pinned in share_server.PINS; the Live tests in test_share_server.py
cover both.
"""
import os, sys

from copyparty import httpcli
from copyparty.__main__ import main

Cli = httpcli.HttpCli
_handle_get, _handle_post, _tx_zip = Cli.handle_get, Cli.handle_post, Cli.tx_zip


def hidden(vpath):
    return any(p.startswith(".") for p in vpath.split("/"))


def blocked(cli, resources_ok):
    if resources_ok and cli.vpath.split("/")[0] == ".cpr":  # GET serves these from copyparty itself, never the share
        return False
    if hidden(cli.vpath):
        return True
    root = os.path.realpath(cli.vn.realpath)
    rel = os.path.relpath(os.path.realpath(os.path.join(root, cli.rem)), root)
    return rel != "." and hidden(rel.replace(os.sep, "/"))


def handle_get(self):
    if blocked(self, True):
        return self.tx_404()
    if "?" not in self.req and self.vpath.lower().endswith(".md") and \
            "text/html" in self.headers.get("accept", ""):
        self.uparam["v"] = ""
    return _handle_get(self)


def handle_post(self):
    return self.tx_404() if blocked(self, False) else _handle_post(self)


def tx_zip(self, fmt, uarg, vpath, vn, rem, items):
    kept = [i for i in items if not hidden(i)]
    if items and not kept:  # copyparty reads an empty selection as the whole folder
        return self.tx_404()
    return _tx_zip(self, fmt, uarg, vpath, vn, rem, kept)


Cli.handle_get, Cli.handle_post, Cli.tx_zip = handle_get, handle_post, tx_zip
sys.exit(main(["copyparty", *sys.argv[1:]]))
