"""Offline tests for pr_stats.py: a fake GraphQL search and invented PRs, no GitHub call.

Run: python3 test_pr_stats.py
"""
import datetime as dt, json, os, pathlib, re, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pr_stats as ps

ps.PACE = 0
os.environ["ORCH_FLEET_STATE"] = tempfile.mkdtemp(prefix="test-pr-stats-")

D = dt.date


def pr(url, day, state="MERGED", title=None, repo="o/a", author="me", hour=12):
    t = dt.datetime.combine(day, dt.time(hour)).astimezone()
    return {"url": url, "title": title or url, "createdAt": t.isoformat(), "state": state,
            "author": {"login": author}, "repository": {"nameWithOwner": repo}}


def fake_gh(hits, cap_ranges=()):
    """Stands in for fleet.graphql: answers each search from `hits` by query text and created range."""
    calls = []

    def graphql(query):
        q = json.loads(re.search(r'query: ("(?:[^"\\]|\\.)*")', query).group(1))
        calls.append(q)
        lo, hi = map(D.fromisoformat, re.search(r"created:(\S+)\.\.(\S+)", q).groups())
        nodes = [h for key, h in hits if key in q and lo <= dt.datetime.fromisoformat(h["createdAt"]).date() <= hi]
        count = ps.SEARCH_CAP if (lo, hi) in cap_ranges else len(nodes)
        return {"search": {"issueCount": count, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}, []
    ps.fleet.graphql = graphql
    return calls


# settings: "Me" is always first; a source without a query is dropped.
since, sources = ps.settings({"pr_stats": {"since": "2026-01-01", "sources": [{"name": "Bot", "query": "author:bot"}, {"name": "x"}]}})
assert since == D(2026, 1, 1) and [s["name"] for s in sources] == ["Me", "Bot"], sources
assert ps.settings({})[0] == D(D.today().year - 1, 1, 1)

# fetch: one PR found by two sources counts once, for the first; `author` drops hits by someone else.
a, b, c = pr("u1", D(2026, 1, 5)), pr("u2", D(2026, 2, 3), author="claude"), pr("u3", D(2026, 2, 4), author="other")
calls = fake_gh([("author:@me", a), ("in:body", a), ("in:body", b), ("in:body", c)])
got = ps.fetch(D(2026, 1, 1), [{"name": "Me", "query": "author:@me"}, {"name": "Claude", "query": 'in:body "x y"', "author": "claude"}], D(2026, 2, 10))
assert sorted((p["url"], p["source"]) for p in got) == [("u1", "Me"), ("u2", "Claude")], got
assert len(calls) == 4 and all("is:pr archived:false" in q for q in calls), calls  # 2 sources x 2 months
assert calls[2].startswith('is:pr archived:false in:body "x y" created:'), calls  # quotes survive the GraphQL string
assert "created:2026-02-01..2026-02-10" in calls[1], calls  # the current month stops at today

# search_range: a range at the search cap is split in half until each part fits.
calls = fake_gh([("q", pr("u4", D(2026, 3, 20)))], cap_ranges={(D(2026, 3, 1), D(2026, 3, 31))})
assert [h["url"] for h in ps.search_range("q", D(2026, 3, 1), D(2026, 3, 31))] == ["u4"]
assert len(calls) == 3, calls
fake_gh([], cap_ranges={(D(2026, 3, 1), D(2026, 3, 1))})
try:
    ps.search_range("q", D(2026, 3, 1), D(2026, 3, 1))
    raise AssertionError("one day at the cap must fail, not drop PRs")
except ps.fleet.SourceError:
    pass

# trend leaves the still-filling last bucket out of the fit and never goes below zero.
assert ps.trend([2, 4, 6, 0]) == ([2.0, 4.0, 6.0, 8.0], 2.0)
assert ps.trend([9, 5, 1, 0])[0][-1] == 0
assert ps.moving([1, 2, 3, 4], 4) == [None, None, None, 2.5]

# report: states, sources, months and weeks, campaigns and the view without them.
raw = [dict(pr(f"m{i}", D(2026, 1, 5 + i % 3), state="MERGED" if i % 4 else "CLOSED", repo="o/a"), source="Me") for i in range(8)]
raw += [dict(pr(f"o{i}", D(2026, 2, 2), state="OPEN", repo="o/b", hour=23), source="Bot") for i in range(2)]
raw += [dict(pr(f"c{i}", D(2026, 2, 9), title="Bump config", repo=f"o/s{i}"), source="Bot") for i in range(ps.BATCH_MIN)]
raw += [dict(pr("old", D(2025, 12, 31)), source="Me")]  # before since: left out
rep = ps.report(raw, D(2026, 1, 1), [{"name": "Me"}, {"name": "Bot"}], D(2026, 2, 11))
a, nb = rep["views"]["all"], rep["views"]["no_batch"]
assert a["total"] == 20 and a["states"] == {"merged": 16, "open": 2, "closed": 2}, a["states"]
assert a["by_source"] == {"Me": 8, "Bot": 12} and a["months"] == ["2026-01", "2026-02"]
assert a["monthly"]["merged"] == [6, 10] and a["monthly"]["by_source"]["Bot"] == [0, 12]
assert a["weeks"][0] == "2025-12-29" and a["weeks"][-1] == "2026-02-09", a["weeks"]  # weeks start on Monday
assert sum(map(sum, a["punch"])) == 20 and a["punch"][0][23] == 2  # Monday 23:00
assert a["repos"][0] == ["o/a", 8] or a["repos"][0] == ("o/a", 8)
assert rep["batch_titles"] == ["Bump config"] and rep["batch_count"] == ps.BATCH_MIN
assert nb["total"] == 10 and nb["by_source"] == {"Me": 8, "Bot": 2}
json.dumps(rep)  # the report is what the page reads

# A failed collect keeps the last good counts and says why.
ps.fleet.write_atomic(ps.out_path(), json.dumps(rep))
ps.record_error(ps.fleet.SourceError("API rate limit exceeded"))
kept = json.loads(ps.out_path().read_text())
assert kept["views"] == json.loads(json.dumps(rep["views"])) and kept["error"] == "API rate limit exceeded" and kept["failed_at"]

print("test_pr_stats: ok")
