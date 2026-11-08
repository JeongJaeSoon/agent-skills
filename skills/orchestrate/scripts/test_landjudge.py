"""landjudge.judge is the old decision, moved: the brief's next cell and land-check's verdict, merge-state and CI rules.
The oracles below are the code as it stood before the move; every input combination must agree. Run: python3 test_landjudge.py"""
import itertools, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import landjudge, rowstate
from landjudge import NOT_COMPUTED, UNREAD, judge


def old_merge_next(p, prog):
    if p.get("draft"):
        return "초안 → ready"
    if p.get("merge_state") == "DIRTY":
        return "충돌 해소"
    if p.get("decision") == "CHANGES_REQUESTED":
        return "리뷰 지적 반영"
    if p.get("ci") in ("failure", "pending"):
        return "체크"
    if not prog:
        return "리뷰·머지"
    v = prog.get("verdict")
    head = p.get("head") or ""
    if not v or not head or not v["sha"] or not (head.startswith(v["sha"]) or v["sha"].startswith(head)):
        return "리뷰"
    if not v["pass"]:
        return "리뷰 지적 반영"
    return "머지" if p.get("ci") == "success" and p.get("merge_state") in ("CLEAN", "HAS_HOOKS") else "체크"


def old_check(f):
    """land-check's PR-level problems as codes, from the removed inline code."""
    out = []
    if f["stopped"]:
        out.append("stopped")
    if f["held"]:
        out.append("held")
    if f["state"] != "OPEN" or f["draft"]:
        out.append("not_open")
    v = f["verdict"]
    if not v or not v["pass"]:
        out.append("no_verdict")
    elif not f["patch_match"]:
        out.append("patch_changed")
    elif f["no_ci"] and f["head"] != v["sha"]:
        out.append("no_ci_sha")
    elif f["head"] != v["sha"]:
        out.append("head_moved")
    if f["merge_state"] in ("DIRTY", "BEHIND"):
        out.append(f["merge_state"].lower())
    if f["ci_failed"]:
        out.append("ci_failed")
    if f["ci_pending"]:
        out.append("ci_pending")
    return out


HEAD = "a1b2c3d4e5f6"
n = 0
# Every combination of what the brief row reads, through the real rowstate.merge_next.
verdicts = [None, {"pass": True, "sha": HEAD}, {"pass": True, "sha": HEAD[:7]}, {"pass": True, "sha": HEAD + "ff"},
            {"pass": True, "sha": "ffffffff"}, {"pass": True, "sha": ""}, {"pass": False, "sha": HEAD}]
for draft, ms, dec, ci, head, v, program in itertools.product(
        (True, False, None), ("CLEAN", "HAS_HOOKS", "DIRTY", "BEHIND", "BLOCKED", "UNSTABLE", None),
        (None, "CHANGES_REQUESTED", "APPROVED"), ("success", "failure", "pending", None), (HEAD, ""), verdicts, (True, False)):
    p = {"draft": draft, "merge_state": ms, "decision": dec, "ci": ci, "head": head}
    prog = {"verdict": v, "policy": "autonomous"} if program else None
    f = {"programs": {"k": [prog]} if prog else {}}
    got = rowstate.merge_next("k", p, f)
    assert got == old_merge_next(p, prog), (p, v, program, got)
    n += 1
print(f"merge_next agrees with the old decision on {n} inputs")

# The four cases checked against the real function before the move.
base = {"draft": False, "merge_state": "CLEAN", "ci": "success", "head": HEAD}
cases = [
    ("no program", base, None, "리뷰·머지"),
    ("abbreviated sha", base, {"policy": "autonomous", "verdict": {"pass": True, "sha": HEAD[:7]}}, "머지"),
    ("missing ci", dict(base, ci=None), {"policy": "autonomous", "verdict": {"pass": True, "sha": HEAD}}, "체크"),
]
for name, p, prog, want in cases:
    assert rowstate.merge_next("k", p, {"programs": {"k": [prog]} if prog else {}}) == want, name
# Two programs with different policies own the PR: no single program, so the same text as no program.
two = {"programs": {"k": [{"policy": "autonomous", "verdict": None}, {"policy": "human-gate", "verdict": None}]}}
assert rowstate.merge_next("k", base, two) == "리뷰·머지"

# check policy: every PR-level problem code agrees with the old inline rules.
m = 0
for state, draft, ms, v, match, no_ci, head, stopped, held, failed, pending in itertools.product(
        ("OPEN", "MERGED"), (False, True), ("CLEAN", "DIRTY", "BEHIND", "UNSTABLE"),
        (None, {"pass": True, "sha": HEAD}, {"pass": True, "sha": "ffff"}, {"pass": False, "sha": HEAD}),
        (True, False), (True, False), (HEAD, "other"), (True, False), (True, False), ([], ["build"]), ([], ["lint"])):
    f = {"state": state, "draft": draft, "merge_state": ms, "head": head, "stopped": stopped, "held": held,
         "no_ci": no_ci, "verdict": v, "patch_match": match, "ci_failed": failed, "ci_pending": pending}
    j = judge(f, "check")
    assert list(j.why) == old_check(f), (f, j)  # the order too: land-check words its problems in this order
    assert j.state == ("hold" if set(old_check(f)) - {"head_moved"} else "ready"), (f, j)
    m += 1
print(f"check agrees with the old rules on {m} inputs")

# A patch-id nobody computed falls back to the sha, both ways; a failed pass never reaches the patch.
f = {"state": "OPEN", "draft": False, "merge_state": "CLEAN", "head": HEAD, "no_ci": False,
     "verdict": {"pass": True, "sha": "ffff"}, "patch_match": NOT_COMPUTED}
assert "patch_changed" in judge(f, "check").why
assert "head_moved" in judge(dict(f, patch_match=True), "check").why and not judge(dict(f, patch_match=True), "check").why.keys() - {"head_moved"}
assert judge(dict(f, verdict={"pass": True, "sha": HEAD}), "check").state == "ready"

# The earlier answer wins before a verdict is read: a draft, a conflict or a failing CI returns with the verdict's
# fields missing, as the old code did, and a malformed verdict afterwards reads as a mismatch or a fail, never raises.
for p, want in (({"draft": True, "merge_state": "CLEAN", "head": HEAD, "ci": "success"}, "초안 → ready"),
                ({"draft": False, "merge_state": "CLEAN", "head": HEAD, "ci": "failure"}, "체크"),
                ({"draft": False, "merge_state": "DIRTY", "head": HEAD, "ci": "success"}, "충돌 해소")):
    for v in ({}, {"pass": True}, {"sha": HEAD}):
        assert rowstate.merge_next("k", p, {"programs": {"k": [{"policy": "autonomous", "verdict": v}]}}) == want, (p, v)
ok = {"draft": False, "merge_state": "CLEAN", "head": HEAD, "ci": "success"}
for v, want in (({"pass": True}, "리뷰"), ({"sha": HEAD}, "리뷰 지적 반영")):
    assert rowstate.merge_next("k", ok, {"programs": {"k": [{"policy": "autonomous", "verdict": v}]}}) == want, v

# land-check reads CI after the stack lookups, as it did before the move: a run that finishes in between is seen as
# finished, which a CI read placed earlier turns into a HOLD.
src = (pathlib.Path(__file__).parent / "prog.py").read_text()
body = src[src.index("def cmd_land_check"):src.index("def cmd_landed")]
assert body.index("read_stacks(") < body.index("ci_summary("), "CI must be read after the stack lookups"
assert body.index('print("note: main CI') < body.index("patch_id(repo, pr)"), "the main-CI note comes before the patch-id call"

# A fact the caller tried and failed to read stops the judgment, and names the fact.
assert judge({"state": "OPEN", "draft": False, "merge_state": UNREAD}, "check").why == {"unknown": "merge_state"}
assert judge({"draft": UNREAD, "merge_state": "CLEAN"}, "row").state == "unknown"

# Rule 1 (L-465): an independent review with one or more Act-on findings is a fail, whatever result the row says.
eff = landjudge.effective_result
assert eff({"result": "pass"}) == "pass" and eff({"result": "pass", "act_on": 0}) == "pass"
assert eff({"result": "pass", "act_on": 1}) == "fail" and eff({"result": "pass", "act_on": 3}) == "fail"
assert eff({"result": "fail", "act_on": 0}) == "fail"
for bad in (True, -1, "1", 1.5, None):  # a malformed count reads as no count: only a real int >= 1 flips a pass
    assert eff({"result": "pass", "act_on": bad}) == "pass", bad
assert landjudge.act_on_count("2") is None and landjudge.act_on_count(2) == 2 and landjudge.act_on_count(True) is None

# Rule 2 (L-465): a tree that changed since a reviewed head needs a verdict from another reviewer family. The same tree
# at a new sha (a rebase) does not. Fixtures are shaped after one PR's review flow; the values are placeholders.
fam = landjudge.family
assert fam({"reviewer": "codex"}) == fam({"source": "review:codex"}) == "codex"
assert fam({"reviewer": "gpt-5.5"}) == "codex"
assert fam({"reviewer": "fresh-claude-subagent"}) == fam({"source": "review:opus"}) == "claude"
assert fam({"source": "independent-review"}) is None and fam({}) is None  # unnamed: unknown, never blocks
assert fam({"source": "review:acme-linter"}) == "acme-linter"


def row(tree, who, result="pass", **kw):
    return {"ev": "verdict", "pr": 7, "sha": "s" + tree, "patch_id": tree, "reviewer": who, "result": result, **kw}


cross = landjudge.cross_family_problem
# first review of a PR: nothing to cross with
assert cross([], row("A", "codex")) is None
# Act-on 0 from one family, then a rebase only (same patch-id, new sha): no second family required
assert cross([row("A", "codex")], row("A", "codex", sha="rebased")) is None
# Act-on handled, tree changed: the same family again is refused, the other family passes
history = [row("A", "codex", "fail", act_on=1)]
assert "claude" not in (cross(history, row("B", "codex")) or "") and cross(history, row("B", "codex"))
assert cross(history, row("B", "claude-reviewer")) is None
# The reference is the newest earlier verdict on a different tree: B reviewed by claude, then C needs codex
history = [row("A", "codex", "fail", act_on=1), row("B", "claude-reviewer")]
assert cross(history, row("C", "claude-reviewer")) and cross(history, row("C", "codex")) is None
# A rebase of B after that review is still B: the reference stays the A row, whose family differs
assert cross(history, row("B", "claude-reviewer", sha="rebased")) is None
# Rows with no patch-id or no family (recorded before the rule) never block
assert cross([row("A", "codex", patch_id=None)], row("B", "codex")) is None
assert cross([{"ev": "verdict", "patch_id": "A", "source": "independent-review"}], row("B", "codex")) is None
print("ok")
