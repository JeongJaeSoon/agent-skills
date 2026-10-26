"""Shape lint for the public repo: tracked text must not carry a real home path, Slack id, ticket key or SaaS
tenant host. Catches what the name grep (write-skill §4) cannot, since a new name is not on the local list yet.
Placeholders are allowlisted here; real names stay in ~/.config/agent-skills/internal-names.txt.
Run: python3 scripts/test_public_shapes.py"""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PLACEHOLDER_USERS = {"me", "you", "someone", "nobody", "user", "example", "a", "alice", "bob", "bench"}
PLACEHOLDER_KEYS = {"ABC", "ACME", "ENG", "BILL", "XYZ", "PROJ", "FILL", "MARKER", "PR", "HANDOFF", "BUG",
                    "UTF", "ISO", "Z0", "SHA"}
PLACEHOLDER_TENANTS = {"x", "h", "example", "acme"}
# Vendor-wide hosts, not a workspace: api.slack.com, hooks.slack.com and the like.
VENDOR_HOSTS = {"api", "app", "hooks", "files"}

HOME = re.compile(r"/(?:Users|home)/([A-Za-z][\w.-]*)")
SLACK_ID = re.compile(r"\b[CDGUW][0-9][A-Z0-9]{7,}\b")
TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-[0-9]{1,6}\b")
TENANT = re.compile(r"\b([a-z0-9-]+)\.(?:atlassian\.net|slack\.com|ts\.net)\b|linear\.app/([a-z0-9-]+)/")
# A hostname on a private suffix names an internal network.
PRIVATE_HOST = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|corp|intra|lan)\b")


def hits(line):
    out = [f"home path {m.group(0)}" for m in HOME.finditer(line) if m.group(1) not in PLACEHOLDER_USERS]
    out += [f"Slack id {m}" for m in SLACK_ID.findall(line) if "FAKE" not in m and "EXAMPLE" not in m]
    out += [f"ticket key {m.group(0)}" for m in TICKET.finditer(line) if m.group(1) not in PLACEHOLDER_KEYS]
    out += [f"tenant host {m.group(0)}" for m in TENANT.finditer(line)
            if (m.group(1) or m.group(2)) not in PLACEHOLDER_TENANTS | VENDOR_HOSTS]
    out += [f"private host {m}" for m in PRIVATE_HOST.findall(line)]
    return out


def scan():
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True,
                           check=True).stdout.split("\0")
    found = []
    for f in filter(None, files):
        if f == "scripts/test_public_shapes.py":
            continue
        try:
            text = (ROOT / f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            found += [f"{f}:{n}: {h}" for h in hits(line)]
    return found


for bad in ["/Users/jdoe/src", "/home/build/x", "C0" + "123ABCD45", "U9" + "ABCDEFGH", "OPS-12", "corpname.atlassian.net",
            "https://linear.app/corpname/issue/X", "team.slack.com", "tail1a2b.ts.net", "wiki.corp",
            "db.prod.internal"]:
    assert hits(bad), bad
for ok in ["/Users/me/wt", "C0FAKE01", "ABC-1", "ACME-102", "UTF-8", "x.atlassian.net", "https://linear.app/acme/x/",
           "example.slack.com", "<host>.ts.net", "settings.local", "U0FAKEUSER", "SHA-256",
           "https://api.slack.com/methods", "hooks.slack.com/services"]:
    assert not hits(ok), (ok, hits(ok))

found = scan()
if found:
    sys.exit("public-shape hits (rewrite generically, or add a placeholder to the allowlist):\n" + "\n".join(found))
print("ok")
