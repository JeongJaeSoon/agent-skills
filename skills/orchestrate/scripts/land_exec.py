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
  auto                     read-only: what auto_queue would queue and why not for the rest, whatever its mode says

With `land_executor.auto_queue` = {"mode": "shadow" | "on", "max_new_per_round": 1}, each round first looks at every open
PR of the repos above and requests the ones that are ready without anyone calling `orch land`. Mode "off" (also a
missing or malformed value) does nothing; "shadow" only logs what "on" would do; "on" writes the same `ready` event and
request `orch land` does, at most max_new_per_round new ones per round. See references/landing.md, "Auto-queue".
"""
import contextlib, datetime as dt, fcntl, json, os, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # run with -I, this file's directory is not on sys.path
import autoqueue  # noqa: E402
import land_scope  # noqa: E402
import prog  # noqa: E402
import store  # noqa: E402

STATE = prog.LAND_HEARTBEAT.parent
LOCK = STATE / "land-exec.lock"
LOG = STATE / "land-exec.log"
AUTO_LAST = STATE / "land-exec-auto.last"
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
        if autoqueue.withdrawn(p.events(), pr):
            # `orch land` writes a new `ready` when none is live (a standing withdrawal counts as none) and the
            # auto-queue checks this before it writes, so a request that meets one is a request a stop outran. The
            # same check runs again right before the merge call, in prog.attempt.
            return settle("refused", 3, f"#{pr}'s land was stopped after this request was made: record a verdict at"
                                        " the head or land again")
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


def programs_on(repo):
    """([(slug, Program, events)], unread): every program under the programs home that is on `repo`. unread: one could
    not be read, so it might own a PR the others do not."""
    found, unread = [], False
    try:
        dirs = sorted(prog.HOME.iterdir())
    except FileNotFoundError:
        return found, False
    except OSError:
        return found, True
    for d in dirs:
        if d.name.startswith((".", "_")):
            continue
        try:
            cfg = json.loads((d / "program.json").read_text())
        except FileNotFoundError:
            # A directory with a ledger but no config was a program once: it may own a PR, and nobody can say which.
            unread = unread or (d / "ledger.jsonl").exists()
            continue
        except NotADirectoryError:
            continue
        except (OSError, ValueError):
            unread = True
            continue
        if not isinstance(cfg, dict):
            unread = True
        elif isinstance(cfg.get("repo"), str) and cfg["repo"].casefold() == repo.casefold():  # GitHub names ignore case
            try:
                p = prog.Program(d.name)
                found.append((d.name, p, p.events()))
            except (SystemExit, Exception):
                unread = True
    return found, unread


def program_repos():
    """The repo each readable program records, in directory order. An unreadable program adds none: it cannot be walked."""
    out = []
    try:
        dirs = sorted(prog.HOME.iterdir())
    except OSError:
        return out
    for d in dirs:
        if d.name.startswith((".", "_")):
            continue
        try:
            repo = json.loads((d / "program.json").read_text()).get("repo")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(repo, str) and repo.count("/") == 1:
            out.append(repo)
    return out


def auto_repos(ex, say=lambda msg: None):
    """The concrete repos the auto-queue walks: the repos entries by name, plus the repo every program here records when
    the scope covers it (so OWNER/* needs no GitHub listing, a renamed repo is found under the name its program holds,
    and the call count follows the programs, not the owner's repo count). Each is judged as the executor judges a
    request (prog.executor_for: exclude, a rename or transfer; then the wildcard's owner, fork and archived rules), so
    the auto-queue never writes a `ready` event or a request the executor would only refuse and withdraw. A wildcard
    repo whose owner cannot be checked (`gh api user` unanswered) is left out."""
    scope, out, seen, login = ex["scope"], [], set(), False
    # A program's recorded spelling comes first: programs_on matches it exactly, so a differently cased entry must not win.
    candidates = program_repos() + [e for e in ex["repos"] if not e.endswith("/*")]
    for repo in candidates:
        if repo.casefold() in seen:
            continue
        seen.add(repo.casefold())
        if land_scope.excluded(scope, repo):
            say(f"{repo}: skipped, it is in orchestrate.land_executor.exclude")
            continue
        # A program's repo the scope does not name may still be covered under the name GitHub gives it now, as the
        # executor judges it; when it is not, it is another executor's or none, and the round says nothing about it.
        named = bool(land_scope.how(scope, repo))
        try:
            on, meta = prog.executor_for(repo)
        except SystemExit as e:
            if named:
                say(f"{repo}: skipped, the land executor's scope could not be judged: {str(e.code)[:200]}")
            continue
        if not on:
            if named:
                say(f"{repo}: skipped, orchestrate.land_executor does not cover it now (excluded, or renamed out of scope)")
            continue
        current = meta["nameWithOwner"]
        if land_scope.how(scope, current) == "owner":
            if login is False:
                r = prog.run(["gh", "api", "user", "--jq", ".login"], check=False)
                login = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
            if login is None:
                say(f"{repo}: skipped, covered only by OWNER/* and GitHub did not answer `gh api user`")
                continue
            why = land_scope.owner_refused(scope, current, login)
        else:
            why = None
        why = why or land_scope.wildcard_only(scope, current, meta)
        if why:
            say(f"{repo}: skipped, {why}")
            continue
        out.append(repo)
    return out


def auto_decisions(ex, say=lambda msg: None):
    """[(repo, row, program or None, queue?, reasons)] for each open PR of the executor's repos, oldest PR first.
    say: where a problem with a whole repo goes (the round's log; `auto` prints it and writes nothing)."""
    out = []
    for repo in auto_repos(ex, say):
        try:
            rows = prog.open_rows(repo)
        except SystemExit as e:
            say(f"{repo}: the open PR listing failed: {str(e.code)[:200]}")
            continue
        if len(rows) >= prog.OPEN_LIMIT:
            say(f"{repo}: {len(rows)} open PRs is the listing's limit; PRs beyond it are not considered")
        programs, unread = programs_on(repo)
        no_ci = prog.no_ci_repo(repo)
        for r in sorted(rows, key=lambda r: r["number"]):
            pr, head = r["number"], r["headRefOid"]
            known = {slug: (p, ev) for slug, p, ev in programs if pr in prog.pr_state(ev)}
            slug = next(iter(known)) if len(known) == 1 else None
            p, events = known[slug] if slug else (None, [])
            st = prog.pr_state(events).get(pr, {})
            verdict = st.get("verdict")
            c = {"owners": list(known), "programs_unread": unread,
                 "owner_autonomous": bool(p and p.cfg.get("merge_policy") == "autonomous"),
                 "requested": bool(p) and store.land_seen(slug, str(p.dir.parent.resolve()), repo, pr, head),
                 "withdrawn": autoqueue.withdrawn(events, pr), "main_red": bool(p) and prog.main_state(events) == "red",
                 "verdict_problem": prog.executor_verdict_problem(verdict, head, ex["verdict_sources"]),
                 "facts": {"state": "OPEN", "draft": bool(r.get("isDraft")), "merge_state": r.get("mergeStateStatus"),
                           "head": head, "stopped": bool(p) and prog.stopped(events),
                           "held": bool(p) and prog.on_hold(events, pr), "no_ci": no_ci,
                           "verdict": {"pass": verdict.get("result") == "pass", "sha": verdict.get("sha") or ""}
                           if verdict else None}}

            def late(r=r, head=head, pr=pr):
                failed, pending, _ = prog.ci_summary(r.get("statusCheckRollup"), no_ci)
                if not failed and not pending:
                    pending = prog.runs_unfinished(repo, head, no_ci)
                comments = prog.gh_json("pr", "view", str(pr), "--repo", repo, "--json", "comments").get("comments") or []
                return {"ci_failed": failed, "ci_pending": pending, "sticky": any(
                    prog.TEST_RESULTS in (x.get("body") or "") and autoqueue.names_head(x.get("body"), head) for x in comments)}

            try:
                ok, why = autoqueue.decide(c, late)
            except SystemExit as e:
                ok, why = False, [f"GitHub did not answer: {str(e.code)[:120]}"]
            out.append((repo, r, (slug, p) if slug else None, ok, why))
    return out


def queue_pr(slug, p, repo, r):
    """What `orch land` records for a PR, minus the wait: the `ready` event when no land call is live, then the request.
    None when the ledger or the request table changed since the decision: a stop or a request made meanwhile wins."""
    pr = r["number"]
    if store.land_seen(slug, str(p.dir.parent.resolve()), repo, pr, r["headRefOid"]):
        return None
    stopped = []

    def ok(events):
        """Read under the ledger lock, so a stop recorded after the decision is seen here, not written over."""
        if autoqueue.withdrawn(events, pr):
            stopped.append(pr)
            return False
        return not prog.claimed(events, pr)  # a live land call keeps its own `ready`

    p.append_if("ready", ok, pr=pr, ticket=None, klass=None)
    if stopped:
        return None
    klass = next((e["klass"] for e in reversed(p.events()) if e.get("pr") == pr and e.get("klass")), "normal")
    base = prog.run(["gh", "api", f"repos/{repo}/commits/{r.get('baseRefName') or 'main'}", "--jq", ".sha"], check=False)
    return store.land_put(slug, str(p.dir.parent.resolve()), repo, pr, r["headRefOid"], base.stdout.strip() or None, klass,
                          None, by=BY, only_new=True)


def auto_lines(ex, write, say=lambda msg: None):
    """One line per open PR: what the auto-queue did (write) or would do. Mode "on" writes up to max_new_per_round."""
    cap, made, lines = ex["auto_queue"]["max_new_per_round"], 0, []
    for repo, r, owner, ok, why in auto_decisions(ex, say):
        tag = f"{repo}#{r['number']} {r['headRefOid'][:8]}"
        if not ok:
            lines.append(f"skip {tag}: {'; '.join(why)}")
        elif not write:
            lines.append(f"would queue {tag} ({owner[0]})")
        elif made >= cap:
            lines.append(f"ready {tag} ({owner[0]}): over max_new_per_round {cap}, next round")
        else:
            got = queue_pr(*owner, repo, r)
            if got is None or got[1] != "new":
                lines.append(f"not queued {tag} ({owner[0]}): stopped or requested meanwhile"
                             if got is None or got[1] == "seen" else f"not queued {tag} ({owner[0]}): {got[1]}")
                continue
            made += 1
            lines.append(f"queued {tag} ({owner[0]}) as request {got[0]['id']}")
    return lines


def auto_round():
    """Before the queue is read: request the PRs that are ready. A failure here never stops the queue behind it."""
    try:
        ex = prog.land_executor()
        mode = (ex or {}).get("auto_queue", {}).get("mode", "off")
        if mode == "off":
            return
        lines = auto_lines(ex, write=mode == "on", say=lambda msg: log(f"auto-queue {msg}"))
    except store.NotMigrated:
        raise
    except (SystemExit, Exception) as e:
        log(f"auto-queue: {type(e).__name__}: {getattr(e, 'code', e)}"[:300])
        return
    try:
        data = json.loads(AUTO_LAST.read_text())
        last = {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()
    except (OSError, ValueError):
        last = set()
    for line in lines:
        if line not in last:  # a PR that stays skipped for the same reason is logged once, not every minute
            log(f"auto-queue {mode}: {line}")
    with contextlib.suppress(OSError):
        AUTO_LAST.write_text(json.dumps(lines))


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
            auto_round()
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


def cmd_auto(argv):
    try:
        ex = prog.land_executor()
    except (SystemExit, Exception) as e:
        print(f"refused: {getattr(e, 'code', e)}", file=sys.stderr)
        return 1
    if not ex:
        print("the land executor is off: nothing to queue")
        return 0
    print(f"auto_queue is {ex['auto_queue']['mode']} (max_new_per_round {ex['auto_queue']['max_new_per_round']}); "
          "this report writes nothing")
    try:
        print("\n".join(auto_lines(ex, write=False, say=lambda msg: print(f"warning: {msg}"))) or "no open PR")
    except (SystemExit, Exception) as e:  # NotMigrated included: a report says what it could not read and writes nothing
        print(f"refused: {getattr(e, 'code', e)}", file=sys.stderr)
        return 1
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


COMMANDS = {"round": cmd_round, "queue": cmd_queue, "setup": cmd_setup, "status": cmd_status, "drift": cmd_drift,
            "auto": cmd_auto}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        sys.exit(__doc__)
    sys.exit(COMMANDS[argv[0]](argv[1:]))


if __name__ == "__main__":
    main(sys.argv[1:])
