"""Shared by the Artifact backup hooks: where the backup lives, what the public link of a backed-up page is, and the
stateless check "this outgoing text names a backed-up artifact but not its backup link".

Settings, under `artifact-backup` in ~/.claude/agent-skills.json ($AGENT_SKILLS_CONFIG):
  - `base_url`: the URL that serves `public_dir`. Without it no public link can be built: the copy is still made, the
    announcement says so, and nothing demands a backup link.
  - `public_dir` (default ~/share/artifacts): copies of pages that passed the secret scan, and the generated index.html.
  - `private_dir` (default ~/artifacts-private): copies that must not be served, and their index.jsonl.
  - `guard.artifact_links` (a separate top-level key): `warn` (default) tells the sender once, `deny` blocks the reply
    or message that omits the backup link, `off` does nothing. It governs only the stateless check; the Stop hook
    blocks a reply that omits the links of a page this session published this turn whatever the level, because the
    links owed are known right after publishing, which is judged to make a false positive unlikely.
  - `categories`: [[name, regex], ...] checked in order against title + cwd; the first match groups the page in the index.
The public index.jsonl and errors.log are kept in $AGENT_SKILLS_STATE/artifact-backup, outside the served folder: they
hold session ids and local paths.
"""
import json, os, pathlib, re, urllib.parse

from lib import config

URL_RE = re.compile(r"https://claude\.ai/(?:code/)?artifact/([A-Za-z0-9-]+)")


def settings():
    cfg = config.get(config.load(), "artifact-backup", {})
    return cfg if isinstance(cfg, dict) else {}


def _dir(key, default):
    value = settings().get(key)
    return os.path.expanduser(value if isinstance(value, str) and value else default)


def public_dir():
    return _dir("public_dir", "~/share/artifacts")


def private_dir():
    return _dir("private_dir", "~/artifacts-private")


def base_url():
    value = settings().get("base_url")
    return (value.rstrip("/") + "/") if isinstance(value, str) and value else None


def state_dir():
    return str(config.state() / "artifact-backup")


def public_index():
    return os.path.join(state_dir(), "index.jsonl")


def read_rows(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def backup_url(copy):
    """The public URL of one saved version, or None without `base_url`."""
    base = base_url()
    if not base or not copy:
        return None
    return base + urllib.parse.quote(os.path.relpath(copy, public_dir()))


def latest_backup(key):
    """The public URL of the newest version of this artifact; None when it has none or went private."""
    mine = [r for r in read_rows(public_index()) if r.get("key") == key]
    return backup_url(mine[-1].get("copy")) if mine else None


def owed_links(text):
    """Backup links `text` should carry: it names a claude.ai artifact that has a public backup and omits the
    backup's newest version. An older version's link does not stand in for it."""
    owed = []
    for m in URL_RE.finditer(text or ""):
        link = latest_backup(m.group(1))
        if link and link not in text and link not in owed:
            owed.append(link)
    return owed


def level():
    """`guard.artifact_links`: warn (default; one line, nothing blocked), deny (only by this key) or off."""
    return config.level(config.load(), "guard.artifact_links", "warn")


def reason(links):
    return ("This text names a backed-up Artifact without its backup link. Add these verbatim next to the claude.ai "
            "link: " + ", ".join(links) + ".")
