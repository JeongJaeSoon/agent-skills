"""Offline tests for the order of a brief: one table per repository, rows by status, sections in the fixed order, and
the stage table sorted by the same ranks. Invented data.

Run: python3 test_brief_order.py
"""
import json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-brief-order-"))
state = tmp / "dashboard"
env = {**os.environ, "ORCH_FLEET_STATE": str(state), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
NOW = "2026-10-13T14:30:00Z"


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, out, err)
    return out


def pr(key, **kw):
    n = key.rsplit("#", 1)[1]
    return key, {"probed_at": NOW, "detail": {
        "key": key, "url": f"https://example.test/{n}", "state": "OPEN", "merged_at": None, "draft": False, "ci": "success",
        "decision": None, "base": "main", "merge_state": "CLEAN", "reviewers": [], "created": "2026-10-13T10:00:00Z", **kw}}


def term(h):
    return [{"handle": h, "agent": "claude", "connected": True, "writable": True}]


ok("db", "import", "--apply", "--rehearsal")
state.mkdir(parents=True, exist_ok=True)
(state / "prs.json").write_text(json.dumps({"me": "octo", "prs": dict([
    pr("acme/app#1", reviewers=[{"login": "octo", "requested": True}], decision="REVIEW_REQUIRED", merge_state="BLOCKED"),
    pr("acme/app#2", reviewers=[{"login": "rev-a", "requested": True}], decision="REVIEW_REQUIRED", merge_state="BLOCKED"),
    pr("acme/app#3", state="MERGED", merged_at="2026-10-13T14:00:00Z", merge_state="UNKNOWN", merge_sha="3c0ffee0000"),
])}))
(state / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    {"id": "wt-a", "kind": "orchestration", "phase": "working", "name": "lead-a", "terminals": term("term_0FAKEA")},
    {"id": "wt-b", "kind": "orchestration", "phase": "working", "name": "lead-b", "terminals": term("term_0FAKEB")},
    {"id": "wt-c", "kind": "orchestration", "phase": "working", "name": "lead-c", "terminals": term("term_0FAKEC")}]}))

# Two programs in one repository, written three ways, and two other repositories.
ok("board", "set", "A", "--kind", "lead", "--repo", "acme/app", "--program", "first", "--worktree", "wt-a")
ok("board", "set", "B", "--kind", "lead", "--repo", "Acme/App", "--program", "second", "--worktree", "wt-b")
ok("board", "set", "Z", "--kind", "lead", "--repo", "acme/zed", "--worktree", "wt-c")
ok("board", "set", "A", "--title", "리드A")
ok("board", "set", "B", "--title", "리드B")
ok("board", "set", "Z", "--title", "리드Z")
ok("board", "set", "A/done", "--title", "끝난일", "--pr", "acme/app#3", "--status", "done", "--next", "-")
ok("board", "set", "A/held", "--title", "멈춘일", "--status", "held", "--next", "다음 분기")
ok("board", "set", "A/move", "--title", "도는일", "--next", "구현")
ok("board", "set", "A/mine", "--title", "내일", "--pr", "acme/app#1")
ok("board", "set", "A/wait", "--title", "남일", "--pr", "acme/app#2")
ok("board", "set", "A/gap", "--title", "빈일", "--status", "waiting")
ok("board", "set", "B/move", "--title", "또도는일", "--next", "구현")
ok("board", "set", "Z/move", "--title", "저쪽일", "--next", "구현")
ok("board", "set", "Z/more", "--title", "저쪽일2", "--next", "구현")
ok("board", "set", "Z/most", "--title", "저쪽일3", "--next", "구현")
ok("board", "set", "K", "--kind", "lead", "--repo", "aaa", "--program", "bare")
ok("board", "set", "K", "--title", "리드K", "--status", "held", "--next", "나중에")
ok("decide", "add", "--title", "캐시 정책", "--option", "TTL 1h", "--option", "TTL 1d", "--recommend", "1")
ok("board", "doing", "add", "표 밖 일", "--owner", "워커", "--next", "조사")
ok("board", "todo", "add", "다음에 할 일", "--after", "11/1", "--who", "워커")
ok("board", "later", "add", "미룬일", "--trigger", "다음 분기")
ok("stage", "add", "login", "--title", "로그인 개선")

text = ok("brief", "render", "--periodic", "--now", NOW, "--skipped", "건너뜀", "--insight", "덧붙임")
tg = ok("brief", "render", "--periodic", "--now", NOW, "--telegram", "--skipped", "건너뜀", "--insight", "덧붙임")

# 1. One table per repository, whatever the program and the spelling.
headings = re.findall(r"^\*\*([^*]*)\*\*$", text, re.M)[1:]
assert [h.split(" →")[0] for h in headings] == ["acme/app", "acme/zed", "aaa"], text
assert re.findall(r"^📋 (.*)$", tg, re.M) == headings, tg


# 2. Rows by status: heads with their sub-rows as one block, the block at the rank of its best row, sub-rows sorted too.
full = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))["full"]  # tables with every cell whole
block = full[full.index("**acme/app"):full.index("**acme/zed")]
rows = [l for l in block.splitlines() if l.startswith("| ") and "리드/Phase" not in l]
marks = [r.split("|")[1].strip() + " " + r.split("|")[2].strip().removeprefix("└ ") for r in rows]
assert marks == ["🙋 리드A", "🙋 내일", "❔ gap 빈일", "🔄 move 도는일", "⏳ 남일", "⏸️ held 멈춘일", "✅ 끝난일",
                 "🔄 리드B", "🔄 move 또도는일"], marks
assert "| ↑ 오너 |" in block and "| ↓ 대기 |" in block and "| ↓ 보류 |" in block, block  # each points where its list is

# 3. Sections in the fixed order: what the owner acts on first, what explains the rest last.
# The pointer and legend lines open the report, on the lines right under the headline: the owner reads them first.
ORDER = ("🙋 **지금 오너가 할 일**", "❓ **결정 필요**", "**acme/app", "📌 **카드 없는 일**", "⏳ **다른 사람 대기**",
         "⏸️ **보류**", "덧붙임", "건너뜀")
at = [text.index(s) for s in ORDER]
assert at == sorted(at), [s for _, s in sorted(zip(at, ORDER))]
TG_ORDER = ("🙋 지금 오너가 할 일", "❓ 결정 필요", "📋 acme/app", "📌 카드 없는 일", "⏳ 다른 사람 대기", "⏸️ 보류",
            "덧붙임", "건너뜀")
body = tg[tg.index("🙋 지금 오너가 할 일"):]  # the legend above it names ⏳ and ⏸️ too
at = [body.index(s) for s in TG_ORDER]
assert at == sorted(at), [s for _, s in sorted(zip(at, TG_ORDER))]
for report, owner in ((text, "🙋 **지금 오너가 할 일**"), (tg, "🙋 지금 오너가 할 일")):
    top = [l for l in report.splitlines()[1:] if l]
    top = top[:next(i for i, l in enumerate(top) if l.startswith(owner))]
    assert [l.split(" ")[0] for l in top] == ["단계표", "📅", "🔄", "PR:"], top
    assert "진행 · 🙋 오너 차례" in top[2] and report.count("🔄 진행 · 🙋 오너 차례") == 1 and report.count("\nPR: ") == 1, report

# The lists follow the tables, and every row still shows once.
out = json.loads(ok("brief", "render", "--periodic", "--now", NOW, "--json"))
assert not out["duplicates"] and not out["missing"], (out["duplicates"], out["missing"])
assert [t["table"].split(" →")[0] for t in out["table_rows"]] == ["acme/app", "acme/zed", "aaa"], out["table_rows"]

import rowstate  # noqa: E402

# The ranks.
line = lambda key, status, depth=0, sec="r": {"key": key, "status": status, "depth": depth, "sec": sec}  # noqa: E731
got = rowstate.order_rows([line("a", "done"), line("a1", "moving", 1), line("a2", "owner", 1), line("b", "held"),
                           line("c", "moving"), line("d", "moving"), line("e", None), line("e1", "check", 1)])
assert [l["key"] for l in got] == ["a", "a2", "a1", "e", "e1", "c", "d", "b"], [l["key"] for l in got]

# Tables: the owner's or a gap's first, then the most 🔄, then the name.
got = rowstate.order_tables({"b/z": ["moving"], "a/m": ["moving", "moving"], "a/c": ["held"], "a/d": ["done"],
                             "c/q": ["check"], "D/q": ["owner", "moving"], "e/r": ["blocked"]})
assert got == ["D/q", "c/q", "e/r", "a/m", "b/z", "a/c", "a/d"], got

# Spellings of one owner/name are one repository; a bare name is a project or directory and may be any owner's.
got = rowstate.repo_names(["acme/app", "Acme/App", "app", "App", "other/app", "—", None])
assert got == {"acme/app": "acme/app", "Acme/App": "acme/app", "app": "app", "App": "App", "other/app": "other/app",
               "—": "—"}, got

# The stage table: siblings by the same ranks, a group at its best row, each row still right after its parent.
stage = [{"id": "g1", "parent": None, "group": True}, {"id": "w1", "parent": "g1", "now": "done"},
         {"id": "w2", "parent": "g1", "now": "owner"}, {"id": "w2a", "parent": "w2", "now": "held"},
         {"id": "w3", "parent": "g1"}, {"id": "w4", "parent": "g1", "done_at": "2026-10-01T00:00:00Z"},
         {"id": "w5", "parent": "g1", "now": "held"},
         {"id": "g2", "parent": None, "group": True}, {"id": "x1", "parent": "g2", "now": "moving"},
         {"id": "g3", "parent": None, "group": True}, {"id": "y1", "parent": "g3", "now": "check"}]
got = [r["id"] for r in rowstate.order_tree(stage)]
assert got == ["g1", "w2", "w2a", "w5", "w3", "w1", "w4", "g3", "y1", "g2", "x1"], got
# A work row with no status of its own stands at its best child's rank too, not only a group.
got = [r["id"] for r in rowstate.order_tree([{"id": "a", "parent": None, "now": "moving"}, {"id": "b", "parent": None},
                                             {"id": "b1", "parent": "b", "now": "owner"}])]
assert got == ["b", "b1", "a"], got

# A folded reviewer's line takes its table's place in the lists: its owner item comes before a later table's.
prs = json.loads((state / "prs.json").read_text())
prs["prs"].update(dict([pr(k, reviewers=[{"login": "octo", "requested": True}], decision="REVIEW_REQUIRED", merge_state="BLOCKED")
                        for k in ("mid/x#8", "mid/x#9", "zzz/y#7")]))
(state / "prs.json").write_text(json.dumps(prs))
for rid, repo, n in (("loose8", "mid/x", 8), ("loose9", "mid/x", 9), ("loose7", "zzz/y", 7)):
    ok("board", "set", rid, "--kind", "task", "--title", f"낱개{n}", "--repo", repo, "--pr", f"{repo}#{n}")
text = ok("brief", "render", "--periodic", "--now", NOW)
assert "- 🙋 2건 오너 리뷰 — 분야 낱개8·낱개9" in text and text.index("**mid/x") < text.index("**zzz/y"), text
mine = text[text.index("🙋 **지금 오너가 할 일**"):text.index("❓ **결정 필요**")]
assert mine.index("#8") < mine.index("#7"), mine

# A row-less report still has its one owner line first.
empty = tmp / "empty"
env2 = {**env, "AGENT_SKILLS_STATE": str(empty), "ORCH_FLEET_STATE": str(empty / "d")}
r = subprocess.run([sys.executable, str(HERE / "prog.py"), "brief", "render", "--now", NOW, "--no-fill"], capture_output=True,
                   text=True, env=env2)
assert r.returncode == 0 and "🙋 **지금 오너가 할 일** — 없음" in r.stdout, (r.stdout, r.stderr)

print("ok")
