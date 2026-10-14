#!/usr/bin/env python3
"""Wake a session when a relevant GitHub notification arrives, without spending model tokens while it waits.

Usage:
  python3 notify.py seed  [--config PATH] [--state DIR]
  python3 notify.py watch [--config PATH] [--state DIR]

watch polls `gh api notifications` until a new relevant notification appears, prints one line per item
(reason, repo, title, web URL; tab-separated) and exits 0. It exits 1 when max_minutes pass with nothing
new (restart it) and 2 when it cannot work: no "prs" key in the config, or `gh` failing max_failures
times in a row (the last error is printed). Run under a background shell so the exit wakes the session.
seed records every current notification as seen and prints the count; watch seeds by itself when no
seen file exists, so the first run does not dump the backlog.
Read-only: it never marks a notification read and never writes to GitHub.
Config: ~/.claude/agent-skills.json → "prs" → "notify" (see SKILL.md); the "prs" repo scope applies too.
State: <--state or $AGENT_SKILLS_STATE or ~/.local/state/agent-skills>/prs/notify-seen.txt, keys only.
"""
import json, os, pathlib, re, subprocess, sys, time


def opt(name, default=None):
    a = sys.argv
    i = a.index(name) + 1 if name in a else 0
    return a[i] if 0 < i < len(a) else default


CONFIG = pathlib.Path(opt("--config") or "~/.claude/agent-skills.json").expanduser()
STATE = pathlib.Path(opt("--state") or os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser() / "prs"
GH = os.environ.get("AGENT_SKILLS_GH", "gh")
DEFAULTS = {"reasons": ["mention", "author", "assign", "state_change", "review_requested", "comment"],
            "exclude_title_patterns": [r"^\[Auto\]", r"^chore\(deps", r"^Bump "],
            "personal_review_only": True, "human_activity_only": True, "interval_s": 30, "backoff_s": 60,
            "max_failures": 10, "max_minutes": 720, "keep_seen": 2000}
SCOPE = {"orgs": [], "repos": [], "exclude_orgs": [], "exclude_repos": []}
# A re-request or reassignment bumps updated_at on the same thread; keying these by thread fires them once.
ONCE_PER_THREAD = ("review_requested", "assign")
# These also fire on the user's own pushes and comments, CI and bot comments.
ACTIVITY = ("author", "comment")


def config(path=CONFIG):
    try:
        prs = json.loads(pathlib.Path(path).read_text()).get("prs")
    except (OSError, ValueError):
        prs = None
    if prs is None:
        return None
    return {**DEFAULTS, **{k: prs.get(k, v) for k, v in SCOPE.items()}, **prs.get("notify", {})}


def gh(path):
    r = subprocess.run([GH, "api", path], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(f"gh api {path}: {(r.stderr or r.stdout).strip()[:300]}")
    return json.loads(r.stdout)


def key(n):
    return f"{n['id']}:{n['reason']}" if n["reason"] in ONCE_PER_THREAD else f"{n['id']}@{n['updated_at']}"


def web_url(api_url):
    url = re.sub(r"^https://api\.github\.com/repos/", "https://github.com/", api_url or "")
    return re.sub(r"/pulls/(\d+)$", r"/pull/\1", url)


def in_scope(n, cfg):
    repo = n["repository"]["full_name"]
    org = repo.split("/")[0]
    if repo in cfg["exclude_repos"] or org in cfg["exclude_orgs"]:
        return False
    if (cfg["orgs"] or cfg["repos"]) and org not in cfg["orgs"] and repo not in cfg["repos"]:
        return False
    return n["reason"] in cfg["reasons"] and not any(re.search(p, n["subject"]["title"]) for p in cfg["exclude_title_patterns"])


def line(n):
    return "\t".join((n["reason"], n["repository"]["full_name"], n["subject"]["title"][:70], web_url(n["subject"].get("url"))))


def actor(fetch, n):
    """The user behind a thread's latest activity, or None when the notification carries none (a push)."""
    url, latest = n["subject"].get("url"), n["subject"].get("latest_comment_url")
    if not latest:
        return None
    if latest != url:
        return fetch(latest).get("user")
    # GitHub points latest_comment_url at the PR itself when the latest activity is a review.
    reviews = fetch(url + "/reviews?per_page=100") if "/pulls/" in url else []
    return reviews[-1].get("user") if reviews else None


def is_human(user, me):
    return bool(user) and user["login"] != me and user.get("type") != "Bot" and not user["login"].endswith("[bot]")


def poll(fetch, me, cfg, seen):
    """Returns (lines to print, keys to record). A team-only review request is recorded per updated_at,
    so it is checked again only on the next re-request, and still fires once if the user is later asked by name."""
    lines, keys = [], []
    for n in fetch("notifications?per_page=50"):
        k = key(n)
        if k in seen or not in_scope(n, cfg):
            continue
        if n["reason"] == "review_requested" and cfg["personal_review_only"] and n["subject"].get("url"):
            team = f"{n['id']}@{n['updated_at']}:team"
            if team in seen:
                continue
            try:
                users = fetch(n["subject"]["url"] + "/requested_reviewers").get("users", [])
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                continue  # a PR gone or unreadable must not hold back the rest; asked again next poll
            if me not in [u["login"] for u in users]:
                keys.append(team)
                continue
        latest = n["subject"].get("latest_comment_url")
        # A mention in a PR or issue body has no comment to attribute it to, so only a comment can silence one.
        mention_comment = n["reason"] == "mention" and latest not in (None, n["subject"].get("url"))
        if (n["reason"] in ACTIVITY or mention_comment) and cfg["human_activity_only"]:
            try:
                user = actor(fetch, n)
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                continue
            if not is_human(user, me):
                keys.append(k)
                continue
        keys.append(k)
        lines.append(line(n))
    return lines, keys


def read_seen(path):
    try:
        return pathlib.Path(path).read_text().split()
    except OSError:
        return []


def write_seen(path, keys, keep):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}\n" for k in keys[-keep:]))


def seed(fetch, path, cfg):
    keys = [key(n) for n in fetch("notifications?per_page=50")]
    write_seen(path, read_seen(path) + keys, cfg["keep_seen"])
    return len(keys)


def watch(fetch, cfg, path, sleep=time.sleep, clock=time.monotonic):
    """Returns (exit code, output lines)."""
    failures, last = 0, ""
    try:
        me = fetch("user")["login"]
        if not pathlib.Path(path).exists():
            seed(fetch, path, cfg)
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
        return 2, [f"notify: {e}"]
    deadline, seen = clock() + cfg["max_minutes"] * 60, read_seen(path)
    while clock() < deadline:
        try:
            lines, keys = poll(fetch, me, cfg, set(seen))
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
            failures, last = failures + 1, str(e)
            if failures >= cfg["max_failures"]:
                return 2, [f"notify: {failures} failures in a row; last: {last}"]
            sleep(cfg["backoff_s"])
            continue
        failures = 0
        if keys:
            seen = (seen + keys)[-cfg["keep_seen"]:]
            write_seen(path, seen, cfg["keep_seen"])
        if lines:
            return 0, lines
        sleep(cfg["interval_s"])
    return 1, []


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "watch"
    cfg = config()
    if cfg is None:
        print(f'notify: no "prs" key in {CONFIG}')
        return 2
    path = STATE / "notify-seen.txt"
    if cmd == "seed":
        try:
            print(f"seeded {seed(gh, path, cfg)}")
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            print(f"notify: {e}")
            return 2
        return 0
    if cmd != "watch":
        print(__doc__)
        return 2
    code, out = watch(gh, cfg, path)
    if out:
        print("\n".join(out))
    elif code == 1:
        print(f"notify: nothing new in {cfg['max_minutes']} min; restart", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
