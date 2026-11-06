#!/usr/bin/env python3
"""The land executor: merges program PRs that agents asked to land, with no model in the loop (references/landing.md,
"Land executor").

With `orchestrate.land_executor` on for a repo, `orch land` records a request (program, PR, head sha) in
agent-skills.db and waits; nothing in an agent's turn merges. The `land-executor` automation (an Orca automation whose
precheck runs `round` every minute through scripts/orca_job.py and never starts an agent; `skills-sync setup enable
land-executor`) takes each queued request and re-checks, in order, the setting and the repo's owner, the program, the
PR at the requested sha, the latest verdict at that exact sha from an allowed source, the test-results comment, the
merge state, and then every check of `orch land` itself (prog.attempt with the sha pinned). Any failure leaves the PR
unmerged and records why; a wait (CI running, a dependency, the lane) keeps the request queued for the next round.

These checks are procedural: they catch a step skipped or recorded wrongly. Every agent runs as the same user with the
same gh token, so nothing here proves who reviewed or who asked.

Usage: land_exec.py <command>   (also `orch land-exec <command>`)
  round [--budget-s 240]   one pass over the queue; the automation runs it. Refuses inside an agent session (CLAUDECODE)
  queue [--json]           requests, newest last: status, sha, reason, merge sha
  setup                    add the land_request table to agent-skills.db (a recorded step; enabling the automation runs it)
  status                   the last round and the queue counts
  drift                    one warning line per gap between the scope and the repos in use (`skills-sync setup status`)
"""
import contextlib, datetime as dt, fcntl, json, os, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # run with -I, this file's directory is not on sys.path
import land_scope  # noqa: E402
import prog  # noqa: E402
import store  # noqa: E402

STATE = prog.LAND_HEARTBEAT.parent
LOCK = STATE / "land-exec.lock"
LOG = STATE / "land-exec.log"
INTERVAL_S = 60
BY = "land-executor"
TELL_DAYS = 7


def now():
    return dt.datetime.now(dt.timezone.utc)


def log(msg):
    line = f"{now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with contextlib.suppress(OSError):
        STATE.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(line + "\n")


def beat(**fields):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = prog.LAND_HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": now().isoformat(timespec="seconds"), "pid": os.getpid(), **fields}) + "\n")
    os.replace(tmp, prog.LAND_HEARTBEAT)


def rev():
    r = subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()[:12] or None


class Round:
    def __init__(self):
        self.login = None
        self.rev = rev()

    def owner_login(self):
        """The gh account's login, or None when GitHub did not answer (not cached: the next request asks again)."""
        if self.login is None:
            r = prog.run(["gh", "api", "user", "--jq", ".login"], check=False)
            self.login = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
        return self.login

    def process(self, row):
        """Claim one queued request and settle it: merged, refused, or back in the queue with why it waits."""
        rid = row["id"]
        if not store.land_claim(rid, by=BY):
            return "skipped"
        audit = {"request": rid, "sha": row["sha"], "base_sha": row.get("base_sha"), "executor_rev": self.rev}
        p = None

        def settle(status, code, why, merge_sha=None):
            if status == "refused" and p is not None and code == 1:
                prog.stop_land(p, row["pr"], why)  # releases its lanes, withdraws the land call (cmd_land does the same)
            store.land_finish(rid, status, code, why, merge_sha, {**audit, "message": why[:500]}, by=BY)
            log(f"request {rid} {row['repo']}#{row['pr']} {status}{f' ({code})' if code is not None else ''}: {why[:300]}")
            return status

        prog.HOME = pathlib.Path(row["home"])  # the programs home the request came from; Program and its locks read it
        try:
            p = prog.Program(row["program"])
        except SystemExit as e:
            return settle("refused", 1, f"program unreadable: {e.code}")
        if p.cfg.get("repo") != row["repo"]:
            return settle("refused", 1, f"program {row['program']} is on {p.cfg.get('repo')}, not the requested {row['repo']}")
        repo, pr, sha = row["repo"], row["pr"], row["sha"]
        age_h = (now() - prog.parse_ts(row["created_at"])).total_seconds() / 3600

        def unanswered(what):
            """Wait for GitHub, but not past max_wait_hours: a repo gone or a token revoked never answers."""
            try:
                limit = (prog.land_executor() or {}).get("max_wait_hours", 24)
            except SystemExit:
                limit = 24
            if age_h > limit:
                return settle("refused", 1, f"expired: queued {age_h:.0f} h while {what}")
            return settle("queued", None, what)

        try:
            v = prog.pr_view(repo, pr)
        except SystemExit as e:
            return unanswered(f"GitHub did not answer: {str(e.code)[:200]}")
        audit["merge_state"] = v.get("mergeStateStatus")
        if v["state"] == "MERGED":
            # A round that died after the merge, or a merge made elsewhere: record what GitHub shows, once. This comes
            # before the setting and the expiry, which decide whether to merge, not whether a merge happened.
            msha = (v.get("mergeCommit") or {}).get("oid")
            if "landed" not in prog.pr_state(p.events()).get(pr, {}):
                p.append("landed", pr=pr, sha=msha, note=f"land-exec request {rid}, recorded after the merge")
            same = v["headRefOid"] == sha
            return settle("merged", 0, "merged" if same else
                          f"merged at {v['headRefOid'][:8]}, not the requested {sha[:8]}, outside this request", msha)
        try:
            on = prog.land_executor()
        except SystemExit as e:
            return settle("refused", 1, f"the settings cannot be read: {e.code}")
        if not on:
            return settle("refused", 1, "orchestrate.land_executor is off: land again (it merges directly)")
        login = self.owner_login()
        if login is None:
            return unanswered("GitHub did not answer `gh api user`: the repo's owner is unchecked")
        # GitHub follows a rename or a transfer from the recorded name: scope and owner are judged on the repo it is now,
        # by the rule `orch land` used to send the request here (prog.executor_for).
        try:
            ex, meta = prog.executor_for(repo)
        except SystemExit as e:
            if str(e.code).startswith("refused: "):
                return settle("refused", 1, str(e.code)[len("refused: "):])
            return unanswered(f"GitHub did not answer `gh api repos/{repo}`: {str(e.code)[:160]}")
        except (KeyError, TypeError, ValueError) as e:
            return unanswered(f"GitHub did not answer `gh api repos/{repo}`: {e}"[:200])
        current = meta["nameWithOwner"]
        renamed = "" if current.casefold() == repo.casefold() else f" ({repo} is {current} on GitHub now)"
        if not ex:
            return settle("refused", 1, f"orchestrate.land_executor does not cover {repo}{renamed}: land again"
                                        " (it merges directly)")
        # An org's repo gets in only by name; its branch protection still holds the merge (BLOCKED waits, never bypassed).
        if why := land_scope.owner_refused(ex["scope"], current, login):
            return settle("refused", 1, why + renamed)
        if why := land_scope.wildcard_only(ex["scope"], current, meta):
            return settle("refused", 1, why)
        if age_h > ex["max_wait_hours"]:
            return settle("refused", 1, f"expired: queued {age_h:.0f} h, over max_wait_hours {ex['max_wait_hours']:g}")
        if p.cfg.get("merge_policy") != "autonomous":
            return settle("refused", 1, f"merge_policy is {p.cfg.get('merge_policy')}; the executor lands autonomous programs only")
        if v["state"] != "OPEN":
            return settle("refused", 1, f"#{pr} is {v['state']}")
        if v.get("isDraft"):
            return settle("refused", 3, f"#{pr} is a draft: gh pr ready, then land again")
        if v["headRefOid"] != sha:
            return settle("refused", 3, f"the head moved from {sha[:8]} to {v['headRefOid'][:8]} since the request:"
                                         " review the new head, orch verdict, land again")
        verdict = prog.pr_state(p.events()).get(pr, {}).get("verdict")
        audit["verdict"] = verdict
        if why := prog.executor_verdict_problem(verdict, sha, ex["verdict_sources"]):
            return settle("refused", 3, why)
        try:
            comments = prog.gh_json("pr", "view", str(pr), "--repo", repo, "--json", "comments").get("comments") or []
        except SystemExit as e:
            return settle("queued", None, f"GitHub did not answer: {str(e.code)[:200]}")
        if not any(prog.TEST_RESULTS in (c.get("body") or "") for c in comments):
            return settle("refused", 3, f"no {prog.TEST_RESULTS} comment on #{pr}: post the test results"
                                         " (deliver-ticket sticky-comment.sh), then land again")
        mss = v.get("mergeStateStatus")
        if mss == "DIRTY":
            return settle("refused", 3, f"#{pr} conflicts with its base: resolve, re-review, land again")
        if mss == "BEHIND":
            return settle("refused", 3, f"#{pr} is behind a base that requires up-to-date branches:"
                                         f" {prog.update_advice([pr], repo)}, then land again")
        try:
            code, msg = prog.attempt(p, pr, row["klass"], expect=sha, verdict_sources=ex["verdict_sources"])
        except SystemExit as e:
            if prog.TRANSIENT.search(str(e.code)):
                return settle("queued", None, f"GitHub or Orca did not answer: {str(e.code)[:200]}")
            return settle("refused", 1, str(e.code))
        if getattr(p, "land_verdict", None) is not None:
            audit["verdict"] = p.land_verdict  # the one standing at the merge call, re-read there
        if code == 0:
            landed = prog.pr_state(p.events()).get(pr, {}).get("landed") or {}
            return settle("merged", 0, msg, landed.get("sha"))
        if code == 2:
            return settle("queued", None, msg)
        return settle("refused", code, msg)  # a HoldRefusal keeps its type: stop_land then leaves the hold alone

    def tell(self):
        """Put each refusal in the dashboard inbox once. The requester learns the outcome from its own `orch land`,
        which waits on the row, and a merge is in the program's ledger; a refusal the requester stopped waiting for is
        what would otherwise go unseen. Orca mail is not an option here: a precheck has no sender terminal."""
        try:
            rows = [r for r in store.land_rows(("refused",)) if not r.get("told_at")
                    and (now() - prog.parse_ts(r["closed_at"])).days < TELL_DAYS]
        except store.NotMigrated:
            return
        if not rows:
            return
        import fleet  # not at import: only a round with a refusal to report needs the dashboard module
        for r in rows:
            try:
                fleet.inbox_add("land", f"머지 거절 {r['repo']}#{r['pr']} (exit {r.get('code')}): {r.get('reason') or ''}",
                                url=f"https://github.com/{r['repo']}/pull/{r['pr']}", key=f"land-exec:{r['id']}",
                                source=BY)
            except OSError as e:
                log(f"request {r['id']}: the inbox write failed: {e}")
                continue
            store.land_told(r["id"])


@contextlib.contextmanager
def round_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def cmd_round(argv):
    if os.environ.get("CLAUDECODE"):
        # A guardrail against an agent running the merge itself, not an identity check.
        print("refused: the land executor runs from its automation, not inside an agent session", file=sys.stderr)
        return 1
    budget = float(prog.opt(argv, "--budget-s", "240"))
    with round_lock() as mine:
        if not mine:
            print("another round is running")
            return 0
        beat(phase="start")
        try:
            if stale := store.land_requeue_running(by=BY):
                log(f"requeued {stale}: left running by a round that did not finish")
            rows = store.land_rows(("queued",))
        except store.NotMigrated as e:
            beat(phase="error", error=str(e)[:300])
            log(str(e))
            return 1
        r, t0, done = Round(), time.monotonic(), 0
        for row in rows:
            if time.monotonic() - t0 > budget:
                log(f"round budget {budget:g}s spent; {len(rows) - done} request(s) wait for the next round")
                break
            try:
                r.process(row)
            except Exception as e:  # one request's failure never stops the queue
                log(f"request {row['id']}: {type(e).__name__}: {e}")
                with contextlib.suppress(Exception):
                    store.land_finish(row["id"], "queued", None, f"executor error: {type(e).__name__}: {e}", by=BY)
            done += 1
            beat(phase="running", done=done)
        r.tell()
        beat(phase="done", done=done)
    print(f"{done} request(s) processed")
    return 0


def cmd_queue(argv):
    rows = store.land_rows()
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for x in rows:
        print(f"{x['id']:>4} {x['status']:<10} {x['repo']}#{x['pr']} {x['sha'][:8]} {x['program']}"
              f"{' → ' + x['merge_sha'][:8] if x.get('merge_sha') else ''}  {(x.get('reason') or '')[:120]}")
    return 0


def cmd_setup(argv):
    try:
        print(store.land_setup())
    except store.NotMigrated as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_status(argv):
    with contextlib.suppress(OSError, ValueError):
        print(f"last round: {json.loads(prog.LAND_HEARTBEAT.read_text())}")
    try:
        rows = store.land_rows()
    except store.NotMigrated as e:
        print(f"queue: {e}")
        return 0
    counts = {}
    for x in rows:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    print("queue: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "empty"))
    return 0


DRIFT_DAYS = 7


def drift_lines(today=None):
    """One warning line per gap between the executor's scope and the repos in use: an owned repo with a PR of yours from
    the last DRIFT_DAYS days that the scope leaves out, a repos entry GitHub now names differently, and a covered repo
    with no `<name>-standalone` program. [] with the executor off; one line when GitHub or the settings do not answer."""
    try:
        ex = prog.land_executor()
    except SystemExit as e:
        return [f"경고: land executor 설정을 읽지 못해 범위를 점검하지 못함: {e.code}"]
    if not ex:
        return []
    since = (today or dt.date.today()) - dt.timedelta(days=DRIFT_DAYS)
    try:
        login = prog.run(["gh", "api", "user", "--jq", ".login"]).stdout.strip()
        found = prog.gh_json("search", "prs", "--author", "@me", "--created", f">={since.isoformat()}", "--limit", "1000",
                             "--json", "repository")
    except SystemExit as e:
        return [f"경고: GitHub가 답하지 않아 land executor 범위를 점검하지 못함: {str(e.code)[:160]}"]
    scope, out, covered, seen = ex["scope"], [], {}, {}
    for p in found:
        name = (p.get("repository") or {}).get("nameWithOwner")
        if name:
            seen.setdefault(name.casefold(), name)
    for repo in sorted(seen.values(), key=str.casefold):
        if repo.split("/")[0].casefold() != login.casefold() or land_scope.excluded(scope, repo):
            continue
        if land_scope.how(scope, repo):
            covered.setdefault(repo.casefold(), repo)
        else:
            out.append(f"경고: {repo}는 최근 {DRIFT_DAYS}일 PR이 있는 소유 repo인데 land executor 범위 밖이다"
                       " (skills-config로 orchestrate.land_executor.repos에 넣는다)")
    for owner, name in sorted(scope["names"]):
        entry = f"{owner}/{name}"
        if land_scope.excluded(scope, entry):
            continue
        try:
            now_named = prog.repo_meta(entry)["nameWithOwner"]
        except (SystemExit, KeyError, TypeError, ValueError) as e:
            out.append(f"경고: repos의 {entry}를 GitHub에서 확인하지 못함: {str(getattr(e, 'code', e))[:120]}")
            continue
        if now_named.casefold() != entry and land_scope.how(scope, now_named) \
                and not land_scope.owner_refused(scope, now_named, login):
            covered.setdefault(now_named.casefold(), now_named)  # OWNER/* still covers the new name
        elif now_named.casefold() != entry:
            out.append(f"경고: repos의 {entry}는 GitHub에서 {now_named}로 이름이 바뀌어 범위에서 빠졌다"
                       " (skills-config로 새 이름을 넣는다)")
        else:
            covered.setdefault(entry, now_named)
    # OWNER/* covers every source repo of the owner that is not archived: exactly what this listing returns.
    for owner in sorted(scope["owners"]):
        if owner != login.casefold():
            out.append(f"경고: {owner}/*는 gh 계정({login})이 아니어서 executor가 거절한다"
                       " (org repo는 OWNER/NAME으로 하나씩 넣는다)")
            continue
        try:
            listed = prog.gh_json("repo", "list", owner, "--source", "--no-archived", "--limit", "1000",
                                  "--json", "nameWithOwner")
        except SystemExit as e:
            out.append(f"경고: {owner}/*의 repo 목록을 GitHub에서 받지 못함: {str(e.code)[:120]}")
            continue
        for r in listed:
            name = r.get("nameWithOwner") or ""
            if name and land_scope.how(scope, name):
                covered.setdefault(name.casefold(), name)
    missing = []
    for repo in sorted(covered.values(), key=str.casefold):
        slug = f"{repo.split('/')[-1]}-standalone"
        try:
            ok = json.loads((prog.HOME / slug / "program.json").read_text())["repo"].casefold() == repo.casefold()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            ok = False
        if not ok:
            missing.append(repo)
    if missing:
        out.append(f"경고: 범위 안인데 <name>-standalone program이 없는 repo {len(missing)}개: {', '.join(missing)}"
                   " (`orch verdict|land <name>-standalone --repo OWNER/NAME`가 처음 부를 때 만든다)")
    return out


def cmd_drift(argv):
    for line in drift_lines():
        print(line)
    return 0


COMMANDS = {"round": cmd_round, "queue": cmd_queue, "setup": cmd_setup, "status": cmd_status, "drift": cmd_drift}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        sys.exit(__doc__)
    sys.exit(COMMANDS[argv[0]](argv[1:]))


if __name__ == "__main__":
    main(sys.argv[1:])
