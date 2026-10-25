#!/usr/bin/env python3
"""copyparty -mtp parser: print the first 4 KiB of a text file's text on one line so search can match its body.

copyparty sends every tag with each listing and search hit, so the value is kept small; HTML is reduced to its
visible text first.
"""
import html.parser, sys

LIMIT = 4096


class Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style")

    def handle_endtag(self, tag):
        self.skip -= self.skip and tag in ("script", "style")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def text(path):
    with open(path, "rb") as f:
        raw = f.read(LIMIT * 64).decode("utf-8", "replace")
    if path.lower().endswith((".html", ".htm")):
        p = Text()
        p.feed(raw)
        raw = " ".join(p.out)
    return " ".join(raw.split()).encode()[:LIMIT].decode("utf-8", "ignore")


if __name__ == "__main__":
    print(text(sys.argv[1]))
