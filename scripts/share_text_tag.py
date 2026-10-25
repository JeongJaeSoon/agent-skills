#!/usr/bin/env python3
"""copyparty -mtp parser: print a text file's first 64 KiB on one line so search can match its body."""
import sys

with open(sys.argv[1], "rb") as f:
    print(" ".join(f.read(65536).decode("utf-8", "replace").split()))
