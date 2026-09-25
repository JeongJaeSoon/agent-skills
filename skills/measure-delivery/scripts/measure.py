#!/usr/bin/env python3
"""Delivery metrics for one repo + tracker project over a window. Read-only.

Usage:
  python3 measure.py --repo OWNER/NAME (--project TRACKER_PROJECT | --issues ISSUES.json) --since ISO8601 [--until ISO8601]
                     [--baseline-until ISO8601] [--tz +09:00] [--bug-label Bug]
                     [--usage-match TEXT] [--json OUT.json]

--project: read issues through the tracker adapter (use-tracker/scripts/tracker.py; Linear or Jira
per ~/.claude/agent-skills.json).
--issues: a saved issue list instead: tracker.py's normalized JSON, a Linear MCP list_issues result,
or `orca linear list-issues --json` output (no completedAt: completed issues fall back to updatedAt,
and the report says so). A top-level list, {"issues": [...]}, or {"result": {"issues": [...]}}.
--baseline-until: end of the initial design batch. Default: the first gap of 6h+ in creation times.
--usage-match: substring of Claude project dirs / Codex session cwds to count tokens for
(default: the repo name). No prices are applied.
"""
import collections, datetime as dt, glob, json, os, pathlib, statistics, subprocess, sys

TRACKER = pathlib.Path(__file__).resolve().parents[2] / "use-tracker" / "scripts" / "tracker.py"

H6 = dt.timedelta(hours=6)


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def load_issues(path=None, project=None):
    if project:
        r = subprocess.run([sys.executable, str(TRACKER), "list", "--project", project], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"tracker list failed: {r.stderr.strip()[:300]}")
        d = json.loads(r.stdout)
    else:
        d = json.load(open(path))
    if isinstance(d, dict):
        d = (d.get("result") or d).get("issues", d.get("nodes", []))
    out = []
    for i in d:
        if "state_type" in i:  # tracker.py normalized schema
            out.append({"id": i["id"], "created": ts(i["created_at"]), "done": ts(i.get("completed_at")),
                        "done_approx": i.get("closed_approx", False), "canceled": ts(i.get("canceled_at")), "type": i["state_type"],
                        "labels": i.get("labels") or [], "desc": i.get("description") or ""})
            continue
        state = i.get("state")
        stype = (state or {}).get("type") if isinstance(state, dict) else (i.get("statusType") or "")
        labels = [l.get("name") if isinstance(l, dict) else l for l in i.get("labels") or []]
        done_at = i.get("completedAt")
        approx = False
        if not done_at and stype == "completed":
            done_at, approx = i.get("updatedAt"), True
        out.append({"id": i.get("identifier") or i.get("id"), "created": ts(i["createdAt"]),
                    "done": ts(done_at), "done_approx": approx,
                    "canceled": ts(i.get("canceledAt")) or (ts(i.get("updatedAt")) if stype == "canceled" else None),
                    "type": stype, "labels": labels, "desc": i.get("description") or ""})
    return out


def baseline_cut(issues):
    cs = sorted(i["created"] for i in issues)
    for a, b in zip(cs, cs[1:]):
        if b - a >= H6:
            return b - dt.timedelta(seconds=1)
    return cs[-1]


def parse_tz(s):
    sign = -1 if s.startswith("-") else 1
    h, _, m = s.lstrip("+-").partition(":")
    return dt.timezone(sign * dt.timedelta(hours=int(h), minutes=int(m or 0)))


def utc_q(t):
    """GitHub search qualifiers take a full UTC timestamp; a local date would cut hours off the window."""
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_between(repo, a, b):
    # The runs API returns at most 1000 results per query, so split any window that hits it.
    got = gh("run", "list", "--repo", repo, "--limit", "1000", "--created", f"{utc_q(a)}..{utc_q(b)}",
             "--json", "databaseId,headBranch,event,conclusion,createdAt,workflowName")
    if len(got) < 1000:
        return got
    if b - a <= dt.timedelta(minutes=10):
        sys.exit(f"1000+ workflow runs between {utc_q(a)} and {utc_q(b)}: the API cannot list them all, so run counts would be wrong")
    mid = a + (b - a) / 2
    left = runs_between(repo, a, mid)
    seen = {r["databaseId"] for r in left}
    return left + [r for r in runs_between(repo, mid, b) if r["databaseId"] not in seen]


def blocks(start, end, step):
    t = start
    while t < end:
        yield t, min(t + step, end)
        t += step


def usage(match, since, until):
    home = os.path.expanduser("~")
    c, seen = collections.Counter(), set()
    for proj in glob.glob(f"{home}/.claude/projects/*{match}*"):
        for f in glob.glob(proj + "/**/*.jsonl", recursive=True):
            for line in open(f, errors="replace"):
                if '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                m = o.get("message") or {}
                u, t = m.get("usage"), ts(o.get("timestamp"))
                if o.get("type") != "assistant" or not u or not t or not since <= t < until:
                    continue
                # Streamed messages repeat usage per content block.
                key = (m.get("id"), o.get("requestId"))
                if key in seen:
                    continue
                seen.add(key)
                c["claude_calls"] += 1
                c["claude_output"] += u.get("output_tokens") or 0
                c["claude_cache_read"] += u.get("cache_read_input_tokens") or 0
                c["claude_cache_write"] += u.get("cache_creation_input_tokens") or 0
    for f in glob.glob(f"{home}/.codex/sessions/*/*/*/*.jsonl"):
        # total_token_usage is cumulative per session, so count only the growth between events
        # that fall inside the window; a session that straddles `since` contributes its tail.
        cwd, prev, got = None, None, collections.Counter()
        for line in open(f, errors="replace"):
            if cwd is None and '"session_meta"' in line:
                try:
                    cwd = json.loads(line)["payload"].get("cwd", "")
                except (ValueError, KeyError):
                    cwd = ""
                if match not in cwd:
                    break
            if '"token_count"' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            info = (o.get("payload") or {}).get("info") or {}
            tot, t = info.get("total_token_usage"), ts(o.get("timestamp"))
            if not tot or not t:
                continue
            if since <= t < until:
                for k in ("input_tokens", "output_tokens"):
                    got[k] += max(0, tot.get(k, 0) - (prev or {}).get(k, 0))
            prev = tot
        if cwd and match in cwd and got:
            c["codex_sessions"] += 1
            c["codex_input"] += got["input_tokens"]
            c["codex_output"] += got["output_tokens"]
    return c


def main():
    repo, issues_path, project, since = opt("--repo"), opt("--issues"), opt("--project"), opt("--since")
    if not (repo and (issues_path or project) and since):
        sys.exit(__doc__)
    since = ts(since)
    until = ts(opt("--until")) or dt.datetime.now(dt.timezone.utc)
    tz = parse_tz(opt("--tz", "+09:00"))
    bug = opt("--bug-label", "Bug")
    local = lambda t: t.astimezone(tz).strftime("%m-%d %H:%M")

    issues = [i for i in load_issues(issues_path, project) if i["created"] < until]
    cut = ts(opt("--baseline-until")) or baseline_cut(issues)
    base = [i for i in issues if i["created"] <= cut]
    derived = [i for i in issues if i["created"] > cut]
    marked = [i for i in derived if "follow-up" in i["labels"] or i["desc"].lstrip().startswith("파생:")]
    approx = any(i["done_approx"] for i in issues)
    out = []
    w = out.append
    w(f"# 전달 지표: {repo}\n")
    w(f"- 구간 {local(since)} ~ {local(until)} (UTC{opt('--tz', '+09:00')})")
    w(f"- 기준선(초기 설계): {len(base)}건, {local(cut)}까지 생성. 이후 생성 {len(derived)}건을 파생으로 본다"
      f" ({len(marked)}건은 `follow-up` 라벨이나 `파생:` 첫 줄로 표시됨)")
    if approx:
        w("- 완료 시각: completedAt이 없어 완료 이슈의 updatedAt으로 근사했다. 정확히 하려면 Linear MCP list_issues 결과를 넣는다")

    w("\n## 파생 증가\n")
    w("| 구간 | 파생 생성 | 완료 | 파생÷완료 | 열린 이슈(끝) |\n|---|---:|---:|---:|---:|")
    for a, b in blocks(since, until, H6):
        made = sum(a <= i["created"] < b for i in derived)
        done = sum(bool(i["done"]) and a <= i["done"] < b for i in issues)
        open_ = sum(i["created"] < b and not (i["done"] and i["done"] < b) and not (i["canceled"] and i["canceled"] < b) for i in issues)
        if made or done:
            ratio = f"{made / done:.2f}" if done else "—"
            w(f"| {local(a)}–{b.astimezone(tz).strftime('%H:%M')} | {made} | {done} | {ratio} | {open_} |")
    made = sum(since <= i["created"] < until for i in derived)
    done = sum(bool(i["done"]) and since <= i["done"] < until for i in issues)
    w(f"\n구간 합계: 파생 {made} · 완료 {done} · 파생÷완료 {made / max(done, 1):.2f} · 기준선 대비 {len(derived) / max(len(base), 1):.1f}배")

    prs = gh("pr", "list", "--repo", repo, "--state", "merged", "--limit", "1000", "--search",
             f"merged:>={utc_q(since)}", "--json", "number,title,createdAt,mergedAt,headRefName")
    prs = [p for p in prs if since <= ts(p["mergedAt"]) < until]
    for p in prs:
        # Author dates survive rebases, so they separate commits written after the PR opened.
        p["commits"] = [{"authoredDate": c["commit"]["author"]["date"]}
                        for c in gh("api", f"repos/{repo}/pulls/{p['number']}/commits?per_page=100")]
    # PR runs are attributed by branch inside each PR's open interval, which may start before `since`.
    first = min([since] + [ts(p["createdAt"]) for p in prs])
    all_runs = sorted(runs_between(repo, first, until), key=lambda r: r["createdAt"], reverse=True)
    runs = [r for r in all_runs if since <= ts(r["createdAt"]) < until]
    pr_runs = lambda p: sum(r["event"] == "pull_request" and r["headBranch"] == p["headRefName"]
                            and ts(p["createdAt"]) <= ts(r["createdAt"]) <= ts(p["mergedAt"]) for r in all_runs)
    late = [sum(ts(c["authoredDate"]) > ts(p["createdAt"]) for c in p.get("commits") or []) for p in prs]
    to_merge = [(ts(p["mergedAt"]) - ts(p["createdAt"])).total_seconds() / 60 for p in prs]
    ci_per = [pr_runs(p) for p in prs]
    med = lambda xs: statistics.median(xs) if xs else 0

    w("\n## 수용된 변경과 재작업\n")
    w(f"- 머지된 PR: **{len(prs)}**")
    w(f"- PR을 연 뒤 author date가 찍힌 커밋: 합계 {sum(late)}, 중앙값 {med(late)}, 1개 이상인 PR {sum(x > 0 for x in late)}/{len(prs)}")
    w(f"- PR당 pull_request CI run: 중앙값 {med(ci_per)}, 합계 {sum(ci_per)}")
    w(f"- PR 연 뒤 머지까지: 중앙값 {med(to_merge):.0f}분")

    main_red = [r for r in runs if r["event"] == "push" and r["headBranch"] in ("main", "master") and r["conclusion"] == "failure"]
    main_all = [r for r in runs if r["event"] == "push" and r["headBranch"] in ("main", "master")]
    reverts = [p for p in prs if p["title"].lower().startswith("revert")]
    bugs = [i for i in issues if bug in i["labels"] and since <= i["created"] < until]
    w("\n## 머지 뒤 결함 (escaped)\n")
    w(f"- `{bug}` 라벨 이슈 생성: {len(bugs)}건 (PR당 {len(bugs) / max(len(prs), 1):.2f}) — 머지된 코드에서 나온 것인지는 본문으로 확인할 것")
    w(f"- main push CI 실패: {len(main_red)}/{len(main_all)}" + "".join(f"\n  - {local(ts(r['createdAt']))} {r['workflowName']} run {r['databaseId']}" for r in main_red[:10]))
    w(f"- revert PR: {len(reverts)}")

    match = opt("--usage-match", repo.split("/")[-1])
    u = usage(match, since, until)
    n = max(len(prs), 1)
    w("\n## 비용 (토큰, 금액 아님)\n")
    w(f"- 대상: `~/.claude/projects/*{match}*`, cwd에 `{match}`가 든 Codex 세션")
    w(f"- Claude: 호출 {u['claude_calls']:,} · output {u['claude_output']:,} · cache read {u['claude_cache_read']:,} · cache write {u['claude_cache_write']:,}")
    # codex-companion threads are ephemeral and write no session file, so its reviews never show up here.
    skipped = "codex-companion의 review·task는 ephemeral이라 세션 파일을 남기지 않아 빠진다"
    if u["codex_sessions"]:
        w(f"- Codex: 세션 {u['codex_sessions']:,} · input {u['codex_input']:,} · output {u['codex_output']:,} ({skipped})")
    else:
        w(f"- Codex: 미측정. 맞는 세션 파일이 없다 ({skipped})")
    codex = f"{u['codex_input'] // n:,}" if u["codex_sessions"] else "미측정"
    w(f"- 머지 PR당: Claude output {u['claude_output'] // n:,} · Codex input {codex}")
    w("- 이 머신의 로컬 기록만 센다. 다른 머신·클라우드 세션은 빠진다")

    print("\n".join(out))
    if opt("--json"):
        json.dump({"prs": len(prs), "derived": len(derived), "baseline": len(base), "done": done,
                   "late_commits": sum(late), "main_red": len(main_red), "bugs": len(bugs),
                   "usage": dict(u, codex_measured=bool(u["codex_sessions"]))},
                  open(opt("--json"), "w"), indent=2)


if __name__ == "__main__":
    main()
