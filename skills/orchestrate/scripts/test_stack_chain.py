"""Offline tests for fleet.stack_chain: chains, forks, broken links, on invented data.

Run: python3 test_stack_chain.py
"""
import os, pathlib, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dash_demo

root = pathlib.Path(tempfile.mkdtemp(prefix="test-stack-chain-"))
os.environ.update(dash_demo.build(root))
import fleet  # noqa: E402  after the env points the state directory at root


def pr(n, branch, base="main", state="OPEN", repo="acme/app"):
    return {"key": f"{repo}#{n}", "repo": repo, "number": n, "branch": branch, "base": base, "state": state}


def keys(*ns, repo="acme/app"):
    return [f"{repo}#{n}" for n in ns]


# A plain PR on main is no stack.
assert fleet.stack_chain([pr(1, "a")]) == {"acme/app#1": []}

# Three in a row: every member sees the whole chain, base first.
three = [pr(3, "c", base="b"), pr(1, "a"), pr(2, "b", base="a")]
got = fleet.stack_chain(three)
assert got == {k: keys(1, 2, 3) for k in keys(1, 2, 3)}, got

# A fork: each child sees root → itself; the root's chain stops at the fork, so it reads as no stack.
fork = [pr(1, "a"), pr(2, "b", base="a"), pr(3, "c", base="a")]
got = fleet.stack_chain(fork)
assert got["acme/app#2"] == keys(1, 2) and got["acme/app#3"] == keys(1, 3), got
assert got["acme/app#1"] == [], got
# Below a fork the chain goes on in the branch it belongs to.
got = fleet.stack_chain(fork + [pr(4, "d", base="b")])
assert got["acme/app#4"] == keys(1, 2, 4) and got["acme/app#2"] == keys(1, 2, 4) and got["acme/app#3"] == keys(1, 3), got

# A broken link: the parent merged, so the child (retargeted or not) starts its own chain.
got = fleet.stack_chain([pr(1, "a", state="MERGED"), pr(2, "b", base="a"), pr(3, "c", base="b")])
assert got["acme/app#2"] == keys(2, 3) and got["acme/app#3"] == keys(2, 3), got
assert "acme/app#1" not in got, "only open PRs are in a chain"
# A base nobody has a PR for (a branch with no PR) is no link either.
assert fleet.stack_chain([pr(2, "b", base="release")]) == {"acme/app#2": []}

# The same branch name in another repository is not a parent.
other = [pr(1, "a", repo="acme/lib"), pr(2, "b", base="a")]
assert fleet.stack_chain(other) == {"acme/lib#1": [], "acme/app#2": []}

# Two open PRs from one branch name make the parent unknown: no guess.
dup = [pr(1, "a"), pr(5, "a"), pr(2, "b", base="a")]
assert fleet.stack_chain(dup)["acme/app#2"] == [], fleet.stack_chain(dup)

# A cycle and a self-base end instead of looping.
loop = fleet.stack_chain([pr(1, "a", base="b"), pr(2, "b", base="a")])
assert loop == {k: [] for k in keys(1, 2)}, "a cycle has no first PR, so it gets no order"
loop3 = fleet.stack_chain([pr(1, "a", base="c"), pr(2, "b", base="a"), pr(3, "c", base="b"), pr(4, "d", base="c")])
assert loop3 == {k: [] for k in keys(1, 2, 3, 4)}, "a child hanging off a cycle is not ordered either"
# A closed parent is no link, like a merged one.
assert fleet.stack_chain([pr(1, "a", state="CLOSED"), pr(2, "b", base="a")]) == {"acme/app#2": []}
assert fleet.stack_chain([pr(1, "a", base="a")]) == {"acme/app#1": []}

# A PR without a branch (not collected yet) is left out, and prs can be empty.
assert fleet.stack_chain([{"key": "acme/app#9", "state": "OPEN"}]) == {}
assert fleet.stack_chain([]) == {}
print("ok")
