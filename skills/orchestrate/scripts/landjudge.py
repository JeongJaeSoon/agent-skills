"""One PR's land judgment, a pure function of facts: no gh, no ledger, no clock.

`orch land-check` and the brief's "next" cell (rowstate.merge_next) used to decide this separately. Each caller still
reads its own facts and words its own text; judge() only classifies them, so both agree on what a PR waits on.

Scope is one PR's own facts. Program-wide and ordering checks (main red, stack order, review threads, visual evidence,
the no-CI base) stay with their callers. The PR-wide rules of a policy:

  check  `orch land-check`: every problem is listed. With CI, a verdict whose patch-id still matches holds across a head
         move; the no-CI lane needs the verdict at the exact head.
  row    the brief's next step: one state, the first that applies. The patch-id is not computed there, so the verdict's
         sha, compared as a prefix either way, stands in for it.

A required fact (REQUIRED, per policy) the caller tried and failed to read is UNREAD: the judgment is `unknown`, naming
it. The patch_match fact may be NOT_COMPUTED, a caller's choice: the policy's stand-in applies. A malformed verdict (no
sha, no pass) reads as a mismatch or a fail instead of raising. Callers pass UNREAD for no other fact.
"""
UNREAD = object()
NOT_COMPUTED = object()

REQUIRED = {"check": ("state", "draft", "merge_state"), "row": ("draft", "merge_state")}


class Judgment:
    """state: check gives "ready" or "hold"; row gives draft, conflict, changes, ci, program_unknown, review, fix or
    ready; either gives "unknown". why: {reason code: detail}, in the order the codes were found."""

    def __init__(self, state, why):
        self.state, self.why = state, why

    def __repr__(self):
        return f"Judgment({self.state!r}, {self.why!r})"


def act_on_count(v):
    """The Act-on count of a verdict row's `act_on`, or None when it is absent or not a real int (a bool is not one)."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def effective_result(v):
    """A row's result as the land gate reads it: a pass with one or more Act-on findings is a fail, so a row that
    says pass and carries act_on >= 1 (a hand-written or older one) cannot land."""
    result = v.get("result")
    return "fail" if result == "pass" and (act_on_count(v.get("act_on")) or 0) >= 1 else result


FAMILIES = {"codex": ("codex", "gpt", "openai"), "claude": ("claude", "opus", "sonnet", "haiku", "fable", "anthropic")}


def family(v):
    """The reviewer family of a verdict row, from --reviewer else the name inside a review:/verifier: source; None when
    no name is given (independent-review, subagent-review). An unlisted name is its own family. This is a guardrail on
    the recorded name, not proof of who ran the review."""
    kind, _, what = str(v.get("source") or "").partition(":")
    name = (v.get("reviewer") or (what if kind in ("review", "verifier") else "") or "").strip().casefold()
    if not name:
        return None
    return next((fam for fam, marks in FAMILIES.items() if any(m in name for m in marks)), name)


def cross_family_problem(history, cur):
    """Why `cur`, the verdict standing for a PR, needs a reviewer of another family, else None. `history` is the PR's
    earlier verdict rows, oldest first. The reference is the newest earlier row on a different patch-id: a rebase keeps
    the patch-id, so a head that only moved is not reviewed again, while a tree that changed since the last review
    (Act-on fixed, an edit, a conflict resolution) is. A row with no patch-id or no family never blocks."""
    prev = next((h for h in reversed(history) if h.get("patch_id") and h["patch_id"] != cur.get("patch_id")), None)
    if prev is None or not cur.get("patch_id"):
        return None
    before, now = family(prev), family(cur)
    if before and now and before == now:
        return (f"the tree changed since {str(prev.get('sha') or '')[:8]} was reviewed by {before}: the verdict for this"
                f" head must come from a reviewer of another family, not {now} again")
    return None


def _prefix(a, b):
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def judge(f, policy="check"):
    """Facts f:
      state, draft, merge_state, head                    the PR as GitHub shows it
      changes_requested                                  a reviewer asked for changes
      ci_failed, ci_pending (lists), ci_unknown (bool)   what CI says; ci_unknown: no CI value to read
      stopped, held                                      a STOP line, a parked record
      program                                            False when no single program owns the PR (row only)
      verdict                                            None, or {"pass": bool, "sha": str}
      patch_match                                        True/False, or NOT_COMPUTED
      no_ci                                              the repo has no CI (verdict must be at the exact head)
    """
    for k in REQUIRED[policy]:
        if f.get(k, UNREAD) is UNREAD:
            return Judgment("unknown", {"unknown": k})
    return _check(f) if policy == "check" else _row(f)


def _verdict_reasons(f, why, sha_first):
    v, head = f.get("verdict"), f.get("head") or ""
    if v is None:
        why["no_verdict"] = True
        return
    match = f.get("patch_match", NOT_COMPUTED)
    sha = v.get("sha") or ""
    same_sha = sha == head if sha_first is False else _prefix(head, sha)
    if match is NOT_COMPUTED:
        match = same_sha
    if sha_first:
        if not match:
            why["review"] = True
        elif not v.get("pass"):
            why["fix"] = True
        return
    if not v.get("pass"):
        why["no_verdict"] = True
    elif not match:
        why["patch_changed"] = True
    elif f.get("no_ci") and not same_sha:
        why["no_ci_sha"] = True
    elif not same_sha:
        why["head_moved"] = True  # a note, not a problem: the patch-id still holds


def _check(f):
    why = {}
    if f.get("stopped"):
        why["stopped"] = True
    if f.get("held"):
        why["held"] = True
    if f["state"] != "OPEN" or f["draft"]:
        why["not_open"] = True
    _verdict_reasons(f, why, sha_first=False)
    if f["merge_state"] in ("DIRTY", "BEHIND"):
        why[f["merge_state"].lower()] = True
    if f.get("ci_failed"):
        why["ci_failed"] = list(f["ci_failed"])
    if f.get("ci_pending"):
        why["ci_pending"] = list(f["ci_pending"])
    problems = set(why) - {"head_moved"}
    return Judgment("hold" if problems else "ready", why)


def _row(f):
    """The brief's next step, in the order its cell has always decided: draft, conflict, change request, CI, program,
    review, then ready only with CI success and a clean merge state."""
    why = {}
    if f["draft"]:
        return Judgment("draft", {"draft": True})
    if f["merge_state"] == "DIRTY":
        return Judgment("conflict", {"dirty": True})
    if f.get("changes_requested"):
        return Judgment("changes", {"changes_requested": True})
    if f.get("ci_failed") or f.get("ci_pending"):
        return Judgment("ci", {"ci_failed": list(f.get("ci_failed") or []), "ci_pending": list(f.get("ci_pending") or [])})
    if not f.get("program", True):
        return Judgment("program_unknown", {"program": False})
    _verdict_reasons(f, why, sha_first=True)
    if "review" in why or "no_verdict" in why:
        return Judgment("review", why)
    if "fix" in why:
        return Judgment("fix", why)
    if not f.get("ci_unknown") and f["merge_state"] in ("CLEAN", "HAS_HOOKS"):
        return Judgment("ready", why)
    return Judgment("ci", {"ci_unknown": bool(f.get("ci_unknown")), "merge_state": f["merge_state"]})
