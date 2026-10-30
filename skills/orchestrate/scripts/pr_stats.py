#!/usr/bin/env python3
"""Pull requests I opened, and the ones agents opened for me, counted over time.

Usage: pr_stats.py [--out PATH]

Searches GitHub (gh api graphql) for every PR since `since`, one search per source, and writes an aggregate report
(default <fleet state>/pr-stats.json): monthly and weekly counts by state with a trend line, monthly counts by source,
a weekday x hour grid and the top repositories, each with and without bulk campaigns (one title on BATCH_MIN or more
PRs). Days and hours are in this machine's time zone. Only counts, repository names and campaign titles are kept.

The dashboard's scheduled collect passes guard=True: a source that lost more than DROP_MAX of its PRs since the last
report is held back, not saved (see drop_check). Running this script by hand saves whatever it finds, which is how a
real drop (a big repository archived) gets accepted.

Config, under "pr_stats" in the fleet config (fleet.config_path()), all optional:
  since    first day counted, YYYY-MM-DD (default: January 1 of last year)
  sources  agents that open PRs for me, each {"name", "query", "author"?}: `query` is GitHub search syntax added to
           `is:pr archived:false`, `author` keeps only hits whose PR author has that login. PRs I authored are always
           the first source, "Me". A PR several sources find counts once, for the first.
"""
import datetime as dt, json, pathlib, sys, time
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fleet  # noqa: E402

BATCH_MIN = 10
STATES = ("merged", "open", "closed")
SEARCH_CAP = 1000  # GitHub search returns at most this many hits for one query
DROP_MAX = 0.25  # share of one source's PRs that may vanish between two collects before the result is held back
PACE = 2.0  # seconds between searches: back-to-back searches trip GitHub's secondary rate limit
NODE = "... on PullRequest { url title createdAt state author { login } repository { nameWithOwner } }"


def out_path():
    return fleet.state_dir() / "pr-stats.json"


def settings(cfg=None):
    c = ((fleet.config() if cfg is None else cfg).get("pr_stats") or {})
    since = dt.date.fromisoformat(c["since"]) if c.get("since") else dt.date(dt.date.today().year - 1, 1, 1)
    sources = [{"name": "Me", "query": "author:@me"}] + [s for s in c.get("sources") or [] if s.get("name") and s.get("query")]
    return since, sources


def next_month(d):
    return dt.date(d.year + d.month // 12, d.month % 12 + 1, 1)


def search(q):
    """Every hit for one query, or None when the query alone matches SEARCH_CAP or more."""
    out, after = [], None
    while True:
        time.sleep(PACE)
        page = f", after: {fleet.gql_str(after)}" if after else ""
        data, _ = fleet.graphql(f"{{ search(query: {fleet.gql_str(q)}, type: ISSUE, first: 100{page}) "
                                f"{{ issueCount pageInfo {{ hasNextPage endCursor }} nodes {{ {NODE} }} }} }}")
        s = data["search"]
        if s["issueCount"] >= SEARCH_CAP:
            return None
        out += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            return out
        after = s["pageInfo"]["endCursor"]


def search_range(base, first, last):
    hits = search(f"{base} created:{first}..{last}")
    if hits is not None:
        return hits
    if first == last:
        raise fleet.SourceError(f"over {SEARCH_CAP} PRs created on {first} for: {base}")
    mid = first + (last - first) // 2
    return search_range(base, first, mid) + search_range(base, mid + dt.timedelta(days=1), last)


def fetch(since, sources, today):
    """PRs keyed by URL, each tagged with the first source that found it."""
    prs = {}
    for src in sources:
        first = since
        # ponytail: refetches every month each collect; cache closed months if the search budget ever matters.
        while first <= today:
            nxt = next_month(first)
            for n in search_range(f"is:pr archived:false {src['query']}", first, min(nxt - dt.timedelta(days=1), today)):
                if src.get("author") and (n.get("author") or {}).get("login") != src["author"]:
                    continue
                prs.setdefault(n["url"], {**n, "source": src["name"]})
            first = nxt
    return list(prs.values())


def linfit(ys):
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mx, my = (n - 1) / 2, sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in enumerate(ys)) / sum((x - mx) ** 2 for x in range(n))
    return b, my - b * mx


def trend(totals):
    """(line, slope). The last bucket is still filling, so it would pull the line down: fit without it."""
    b, a = linfit(totals[:-1])
    return [max(0, round(a + b * i, 1)) for i in range(len(totals))], round(b, 2)


def moving(tot, n):
    return [None if i < n - 1 else round(sum(tot[i - n + 1:i + 1]) / n, 2) for i in range(len(tot))]


def by_state(prs, keys, key_of):
    idx = {k: i for i, k in enumerate(keys)}
    out = {s: [0] * len(keys) for s in STATES}
    for p in prs:
        out[p["state"]][idx[key_of(p)]] += 1
    return out, [sum(v) for v in zip(*out.values())]


def view(prs, since, today, source_names):
    months, m = [], since.replace(day=1)
    while m <= today:
        months.append(m.isoformat()[:7])
        m = next_month(m)
    monday = since - dt.timedelta(days=since.weekday())
    weeks = [(monday + dt.timedelta(weeks=i)).isoformat() for i in range((today - monday).days // 7 + 1)]

    monthly, mt = by_state(prs, months, lambda p: p["day"][:7])
    monthly["trend"] = trend(mt)[0]
    monthly["by_source"] = {s: by_state([p for p in prs if p["source"] == s], months, lambda p: p["day"][:7])[1] for s in source_names}
    weekly, wt = by_state(prs, weeks, lambda p: p["week"])
    weekly["ma4"] = moving(wt, 4)
    weekly["trend"], weekly["slope"] = trend(wt)
    punch = [[0] * 24 for _ in range(7)]
    for p in prs:
        punch[p["weekday"]][p["hour"]] += 1
    return {
        "total": len(prs),
        "states": {s: sum(p["state"] == s for p in prs) for s in STATES},
        "by_source": {s: sum(p["source"] == s for p in prs) for s in source_names},
        "years": dict(sorted(Counter(p["day"][:4] for p in prs).items())),
        "months": months, "monthly": monthly, "weeks": weeks, "weekly": weekly, "punch": punch,
        "repos": Counter(p["repo"] for p in prs).most_common(10),
    }


def report(raw, since, sources, today, now=None):
    prs = []
    for n in raw:
        t = dt.datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")).astimezone()
        if since <= t.date() <= today:
            prs.append({"day": t.date().isoformat(), "week": (t.date() - dt.timedelta(days=t.weekday())).isoformat(),
                        "weekday": t.weekday(), "hour": t.hour, "state": n["state"].lower(), "title": n["title"],
                        "repo": n["repository"]["nameWithOwner"], "source": n["source"]})
    batch = [t for t, c in Counter(p["title"] for p in prs).most_common() if c >= BATCH_MIN]
    in_batch = set(batch)
    names = [s["name"] for s in sources]
    rest = [p for p in prs if p["title"] not in in_batch]
    return {"generated_at": fleet.iso(now or fleet.utcnow()), "since": since.isoformat(),
            "sources": names, "batch_min": BATCH_MIN, "batch_titles": batch, "batch_count": len(prs) - len(rest),
            "views": {"all": view(prs, since, today, names), "no_batch": view(rest, since, today, names)}}


class SuspectDrop(fleet.SourceError):
    pass


def drop_check(prev, rep):
    """Raise SuspectDrop when a source lost more than DROP_MAX of its PRs against the previous report.

    Counts cover a fixed window, so they only shrink when PRs leave the search: an archived or deleted repository, or
    lost access. The last one is silent: when an organization's SAML SSO authorization lapses, search answers 200 with
    that organization's PRs left out, no error and no header saying so. A changed window or source list is not compared.
    """
    if not prev or not prev.get("views") or (prev.get("since"), prev.get("sources")) != (rep["since"], rep["sources"]):
        return
    old, new = prev["views"]["all"]["by_source"], rep["views"]["all"]["by_source"]
    lost = [f"{s} {old[s]} -> {new.get(s, 0)}" for s in rep["sources"] if new.get(s, 0) < old.get(s, 0) * (1 - DROP_MAX)]
    if lost:
        raise SuspectDrop(f"held back as suspicious, {', '.join(lost)}; an organization's SSO authorization may have "
                          f"lapsed. Run `orch-dash prstats` to accept the new counts")


def refresh(out=None, cfg=None, guard=False):
    since, sources = settings(cfg)
    today = dt.datetime.now().astimezone().date()
    rep = report(fetch(since, sources, today), since, sources, today)
    f = pathlib.Path(out) if out else out_path()
    if guard:
        drop_check(fleet.read_json(f, {}), rep)
    fleet.write_atomic(f, json.dumps(rep, ensure_ascii=False))
    return rep


def record_error(e):
    """Keep the last good counts on the page and say why they stopped updating."""
    rep = fleet.read_json(out_path(), {}) or {}
    rep.update(error=fleet.mask(str(e))[:300], failed_at=fleet.iso(fleet.utcnow()))
    fleet.write_atomic(out_path(), json.dumps(rep, ensure_ascii=False))


def main(argv):
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    rep = refresh(out)
    a = rep["views"]["all"]
    print(f"pr stats: {a['total']} PRs since {rep['since']} {a['states']} by source {a['by_source']} · "
          f"{rep['batch_count']} in {len(rep['batch_titles'])} campaigns")


if __name__ == "__main__":
    main(sys.argv[1:])
