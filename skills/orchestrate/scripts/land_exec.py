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
"""
import contextlib, datetime as dt, fcntl, json, os, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # run with -I, this file's directory is not on sys.path
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


def kick_reap_hard():
    """A landed PR frees its card: ask for a reap-hard pass now rather than at its next 30-minute beat. Best effort; a
    pass the beat started a few minutes ago is not repeated."""
    with contextlib.suppress(Exception):
        sys.path.insert(1, str(HERE.parent.parent / "reap-resources" / "scripts"))
        import reap  # noqa: E402
        reap.spawn_hard("land")


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
            if status == "merged":
                kick_reap_hard()
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
                limit = (prog.land_executor(repo) or {}).get("max_wait_hours", 24)
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
            ex = prog.land_executor(repo)
        except SystemExit as e:
            return settle("refused", 1, f"the settings cannot be read: {e.code}")
        if not ex:
            return settle("refused", 1, f"orchestrate.land_executor is off for {repo}: land again (it merges directly)")
        login = self.owner_login()
        if login is None:
            return unanswered("GitHub did not answer `gh api user`: the repo's owner is unchecked")
        if repo.split("/")[0].casefold() != login.casefold():
            return settle("refused", 1, f"{repo} is not owned by the gh account '{login}'")
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
        if not verdict or verdict.get("sha") != sha or verdict.get("result") != "pass" \
                or verdict.get("source") not in ex["verdict_sources"]:
            got = (f"{verdict.get('result')} from {verdict.get('source')} at {(verdict.get('sha') or '')[:8]}"
                   if verdict else "none")
            return settle("refused", 3, f"the latest verdict ({got}) is not a pass at {sha[:8]} from"
                                         f" {', '.join(ex['verdict_sources'])}: review this head, orch verdict, land again")
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


COMMANDS = {"round": cmd_round, "queue": cmd_queue, "setup": cmd_setup, "status": cmd_status}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        sys.exit(__doc__)
    sys.exit(COMMANDS[argv[0]](argv[1:]))


if __name__ == "__main__":
    main(sys.argv[1:])
