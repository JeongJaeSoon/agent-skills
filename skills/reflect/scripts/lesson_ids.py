"""Next free lesson ID, counting both the lessons ledger and the IDs this repo's history already cites.

Run: python3 lesson_ids.py [--repo <checkout>] [--rev origin/main] < ledger.md
Prints `next L-<n>`, then `foreign L-<a>, ...` when history cites IDs the ledger has no row for:
those were minted somewhere else, so a ledger row that later takes one of them collides.
"""
import argparse, re, subprocess, sys

ROW = re.compile(r"^\|\s*L-(\d+)\s*\|", re.M)
CITED = re.compile(r"\bL-(\d+)\b")


def ledger_ids(text):
    return {int(n) for n in ROW.findall(text)}


def history_ids(repo, rev):
    log = subprocess.run(["git", "-C", repo, "log", "--format=%B", rev],
                         capture_output=True, text=True, check=True).stdout
    return {int(n) for n in CITED.findall(log)}


def report(ledger, history):
    lines = [f"next L-{max(ledger | history, default=0) + 1}"]
    foreign = sorted(history - ledger)
    if foreign:
        lines.append("foreign " + ", ".join(f"L-{n}" for n in foreign))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--rev", default="HEAD")
    a = ap.parse_args()
    print("\n".join(report(ledger_ids(sys.stdin.read()), history_ids(a.repo, a.rev))))


if __name__ == "__main__":
    main()
