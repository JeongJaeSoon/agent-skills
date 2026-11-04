"""Offline tests for tidy.py: what closes on evidence, what only turns 확인 필요, what stays, the brief and the
dashboard's Done button, on invented data.

Run: python3 test_tidy.py
"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-tidy-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import board, fleet, tidy  # noqa: E402

now = fleet.utcnow()
sd = fleet.state_dir()
ago = lambda h: fleet.iso(now - dt.timedelta(hours=h))  # noqa: E731


def run(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def brief():
    code, out, err = run("brief", "render", "--periodic", "--json", "--no-refresh")
    assert code == 0, (out, err)
    return json.loads(out)


def section(text, head):
    """The bullet lines under one heading of the brief."""
    lines, on = [], False
    for line in text.splitlines():
        if line.startswith("📌") or line.startswith("⏳") or line.startswith("⏸️") or line.startswith("🙋") or line.startswith("❓"):
            on = line.startswith(head)
            continue
        if on and line.startswith("- "):
            lines.append(line[2:])
        elif on and line.startswith("**"):
            on = False
    return lines


def pr(key, state, **kw):
    repo, n = key.split("#")
    return {"probed_at": fleet.iso(now), "detail": {"key": key, "repo": repo, "number": int(n), "url": f"https://example.test/{n}",
            "state": state, "merged_at": ago(30) if state == "MERGED" else None, "merge_sha": "abc1234def" if state == "MERGED" else None,
            "draft": False, "base": "main", "reviewers": [], "decision": None, "ci": "success", "merge_state": "CLEAN",
            "created": ago(48), "updated": ago(1), **kw}}


def card(sid, name, phase, title=None):
    return {"id": sid, "name": name, "kind": "standalone", "phase": phase, "parent": "s-coord", "task_title": title,
            "gh_repo": "acme/app", "project": "app", "terminals": [{"handle": f"term_{sid}"}], "last_activity": ago(2), "report": None}


PRS = {
    "acme/app#1": pr("acme/app#1", "MERGED"),            # card-done's PR
    "acme/app#2": pr("acme/app#2", "MERGED"),            # card-release's PR, its doing item has a release left
    "acme/app#3": pr("acme/app#3", "OPEN", reviewers=[{"login": "rev-a", "requested": True}], decision="REVIEW_REQUIRED"),
    "acme/app#4": pr("acme/app#4", "CLOSED"),            # card-closed's PR
    "acme/app#5": pr("acme/app#5", "OPEN", draft=True),  # card-draft's PR
    "acme/app#6": pr("acme/app#6", "MERGED"),            # card-busy's PR: merged but the card still works
    "acme/app#7": pr("acme/app#7", "MERGED"),            # named by full key in a todo and an inbox item
    "acme/app#8": pr("acme/app#8", "OPEN"),              # open: what names it stays
    "acme/app#9": pr("acme/app#9", "MERGED"),            # the PR a held item's trigger waits on
    "acme/app#10": pr("acme/app#10", "OPEN"),            # card-quiet's PR: no reviewer, no draft
}
SESSIONS = [{**card("s-coord", "coordinator", "working"), "kind": "orchestrator", "parent": None}, card("s1", "card-done", "offline", "로그인 개편"), card("s2", "card-release", "offline", "결제 릴리스"),
            card("s3", "card-review", "offline", "청구 화면"), card("s4", "card-closed", "offline", "실험 정리"),
            card("s5", "card-draft", "open", "초안 작업"), card("s6", "card-busy", "working", "검색 개선"),
            card("s7", "card-quiet", "offline", "설정 화면"), card("s8", "card-twin-a", "working"),
            card("s9", "card-twin-b", "working"), card("s10", "card-dup", "offline", "중복 카드")]
OWNER_OF = {"acme/app#1": "s1", "acme/app#2": "s2", "acme/app#3": "s3", "acme/app#4": "s4", "acme/app#5": "s5",
            "acme/app#6": "s6", "acme/app#10": "s7", "acme/app#8": "s10"}
sd.mkdir(parents=True, exist_ok=True)
fleet.write_atomic(sd / "prs.json", json.dumps({"prs": PRS, "me": "owner-me"}))
fleet.write_atomic(sd / "state.json", json.dumps({"generated_at": fleet.iso(now), "sessions": SESSIONS, "prs": [
    {"key": k, "session": OWNER_OF.get(k), "state": v["detail"]["state"]} for k, v in PRS.items()]}))

# The board: doing items about cards, held items with triggers, the owner's to-do lines, rows.
for title, owner, nxt, waiting in [
        ("로그인 개편 (card-done 카드)", "card", "PR → CI green → 머지", False),
        ("결제 릴리스 (card-release 카드)", "card", "PR → 머지 → 릴리스", False),
        ("검색 개선 (card-busy 카드)", "card", "PR → 머지", False),
        ("쌍둥이 (card-twin-a, card-twin-b 카드)", "card", "PR → 머지", False),
        ("bare ref #1 정리", "card", "PR → 머지", False),
        ("acme/app#7 보안 검토", "sec-team", "승인 → 머지", True),
        ("acme/app#7 오너 확인", "오너", "확인", False),
        ("청구 화면 (card-review 카드)", "card", "리뷰 반영 → 머지", False),
        ("설정 화면 (card-quiet 카드)", "card", "머지 대기", False)]:
    board.doing_add(title, owner, nxt, waiting)
board.later_add("다음 일 시작", "card-done PR 머지")
board.later_add("다른 일", "acme/app#9 머지 뒤")
board.later_add("열린 PR 뒤의 일", "acme/app#8 머지 뒤")
board.later_add("모호한 일", "#9 머지 뒤")
board.later_add("날짜 일", "목 10/30 09:00")
LONG_TODO = "acme/app#7 리뷰 " + "긴 설명 " * 30  # over the 120 characters a close used to keep, under the board's 200
board.set_now([LONG_TODO, "acme/app#8 리뷰", "회의 준비"])
board.set_row("stale-row", title="오래된 일", repo="acme/app", status="moving", next="조사")
board.set_row("fresh-row", title="새 일", repo="acme/app", status="moving", next="조사")
board.set_row("card-dup", title="중복 카드 작업", repo="acme/app", status="moving", next="정리")
st = fleet.read_json(sd / "board.json")
st["rows"]["stale-row"]["reported_at"] = ago(12)
fleet.write_atomic(sd / "board.json", json.dumps(st))
# Inbox items.
fleet.inbox_add("review", "acme/app#7 리뷰 요청", key="k-merged")
fleet.inbox_add("review", "acme/app#8 리뷰 요청", key="k-open")
fleet.inbox_add("notice", "사라진 세션의 알림", session="s-gone", key="k-gone")
fleet.inbox_add("run_command", "명령 실행", session="s1", command="make deploy", key="k-cmd")
# What the owner acts on stays whatever the PR or session: a command to run, a decision, a question.
fleet.inbox_add("run_command", "권한 규칙 필요", session="s-gone", command="tune --apply", key="k-rule")
fleet.inbox_add("run_command", "acme/app#7 뒤에 실행", session="s1", command="make deploy", key="k-after")
fleet.inbox_add("decision", "배포 순서 결정", session="s-gone", key="k-decide")
fleet.inbox_add("question", "사라진 세션의 질문", session="s-gone", key="k-ask")

S = fleet.read_json(sd / "board.json")
ids = lambda kind, items: [tidy.item_id(kind, i) for i in items]  # noqa: E731
doing, later, todo = ids("doing", S["doing"]), ids("later", S["later"]), ids("todo", S["owner_todo"])
v = tidy.assess(S, fleet.read_json(sd / "state.json"), {k: p["detail"] for k, p in PRS.items()}, "owner-me", now)
got = {k: (x["verdict"], x["rule"]) for k, x in v.items()}
want = {doing[0]: ("done", "pr_merged"),                  # its card's PR merged, nothing after the merge
        doing[1]: ("check", "pr_merged_steps_left"),      # a release is left after the merge
        doing[2]: ("check", "pr_merged_card_busy"),       # the card still works
        later[0]: ("check", "trigger_met"), later[1]: ("check", "trigger_met"),
        todo[0]: ("done", "pr_over"),
        "row:stale-row": ("check", "stale"),
        "card:s4": ("done", "pr_closed"),
        "ext:k-merged": ("done", "pr_over"), "ext:k-gone": ("done", "session_gone")}
assert got == want, {k: got.get(k) for k in set(got) | set(want) if got.get(k) != want.get(k)}
# Never: two cards named (no single one), a bare #N, a doing item waiting on a person or the owner's, an open PR,
# a date trigger, a fresh row, a command item, a to-do line with no PR.
for k in (doing[3], doing[4], doing[5], doing[6], doing[7], doing[8], later[2], later[3], later[4], todo[1], todo[2],
          "row:fresh-row", "ext:k-open", "ext:k-cmd", "ext:k-rule", "ext:k-after", "ext:k-decide", "ext:k-ask"):
    assert k not in v, (k, v.get(k))

# The brief before the tick: a check is ❔ 에이전트 조치 필요, in its row's 다음 cell or, for an item with no row, in
# 카드 없는 일; a quiet card follows its PR, a fired trigger leaves 보류.
b = brief()
moving, held, theirs = section(b["text"], "📌"), section(b["text"], "⏸️"), section(b["text"], "⏳")
full = b["full"]  # no cell cut: the next step is read whole
assert "- [" not in b["text"], b["text"]
assert any("결제 릴리스 (card-release 카드) — 에이전트 조치 필요: acme/app#2 머지 abc1234 · 남은 단계" in m for m in moving), moving
assert any("다음 일 시작 — 에이전트 조치 필요: 조건 충족: acme/app#1 머지 abc1234" in m for m in moving), moving
assert "| ❔ | 오래된 일 |" in b["text"] and "에이전트 조치 필요: 12h 갱신 없음" in full, b["text"]
# Nothing on #10 is the agent's to fix and nobody is asked, but who merges it is not recorded: ❔, not a guess.
assert "| ❔ | card-quiet | [#10]" in b["text"] and "에이전트 조치 필요: 머지 주체 기록 없음 · 2h 전" in full, b["text"]
# A draft no session works on and nothing holds: ❔ for the agent to resume it or record the hold, not the owner's.
assert "| ❔ | card-draft |" in b["text"] and not any("card-draft" in t for t in section(b["text"], "🙋")), b["text"]
assert "에이전트 조치 필요: 초안 · 세션 없음 — 재개하거나 보류로 기록 · 2h 전" in full, full
assert any(t.startswith("rev-a · 리뷰") and "card-review" in t for t in theirs), theirs  # was ⏸️ 세션 꺼짐, now the PR's wait
assert not any("다음 일 시작" in h or "다른 일" in h for h in held), held
queued = [o.get("title") for o in b["open"] if o.get("kind") == "later"]  # the queue stays; the pointer line counts it
assert "열린 PR 뒤의 일" in queued and "모호한 일" in queued, queued
assert not any(h.startswith("세션 꺼짐") for h in held), held
# A doing item about a card that has a row is one line: the card's, with the doing item's next step.
assert not any("(card-review 카드)" in m for m in moving + theirs), moving
assert not any("card-quiet" in m for m in moving), moving
# A board row about a card takes the card's PR, so the card's own row is not listed again.
assert "| ❔ | card-dup | [#8]" in b["text"] and "중복 카드 |" not in b["text"], b["text"]
open_ids = {o["id"] for o in b["open"]}
assert {doing[0], doing[1], later[0], todo[0], "row:stale-row", "card:s4"} <= open_ids, open_ids

# The tick closes what the evidence settles, once, with its evidence; it writes nothing for a check.
code, out, err = run("board", "tidy", "--dry-run", "--json")
dry = json.loads(out)
assert not tidy.path().exists(), "a dry run writes nothing"
rows = tidy.run(now=now)
assert [r["id"] for r in rows] == [r["id"] for r in dry]
assert {r["id"] for r in rows} == {doing[0], todo[0], "card:s4", "ext:k-merged", "ext:k-gone"}, rows
assert all(r["by"] == "auto" and r["evidence"] for r in rows), rows
assert next(r for r in rows if r["id"] == todo[0])["label"] == S["owner_todo"][0] and len(S["owner_todo"][0]) > 120, "a label is kept whole"
assert tidy.run(now=now) == [], "a second tick finds nothing new"
assert tidy.path().stat().st_mode & 0o777 == 0o600
# Inbox: the merged PR's and the gone session's items leave; the open PR's and the command item stay.
OWNER_ITEMS = {"ext:k-cmd", "ext:k-rule", "ext:k-after", "ext:k-decide", "ext:k-ask"}
assert {i["key"] for i in fleet.external_items({})} == {"ext:k-open"} | OWNER_ITEMS
b = brief()
assert "로그인 개편 (card-done 카드)" not in b["text"] and "acme/app#7 리뷰" not in b["text"].split("🙋")[1], b["text"]
assert "회의 준비" in b["text"] and "acme/app#8 리뷰" in b["text"]
assert "| ✅ | 실험 정리 |" in b["text"], b["text"]  # the closed-PR card is ✅ now, so the ack and the fold take it

# The timeline gets one line per close.
tl = tidy.timeline()
assert len(tl) == 5 and all(e["kind"] == "tidy_done_auto" for e in tl), tl

# Done and Reopen by hand: only an open id closes; a reopen keeps the tick from closing it again on the same evidence.
code, out, err = run("board", "done", "row:fresh-row", "nonsense", "--why", "다른 카드로 옮김")
assert code == 1 and out == "closed 1: row:fresh-row" and "refused nonsense:" in err, (out, err)
assert "새 일" not in brief()["text"].split("📌")[-1]
code, out, err = run("board", "reopen", "row:fresh-row", "card:s4")
assert code == 0 and out == "reopened 2: row:fresh-row card:s4", (out, err)
assert tidy.run(now=now) == [], "the tick does not undo the owner's reopen"
assert "| ⏸️ | card-closed |" in brief()["text"]
code, out, err = run("board", "reopen", "row:fresh-row")
assert code == 1 and "not closed" in err, err

# Reopen undoes the close outside the ledger too: the inbox item is back.
code, out, err = run("board", "reopen", "ext:k-gone")
assert code == 0 and out == "reopened 1: ext:k-gone", (out, err)
assert "ext:k-gone" in {i["key"] for i in fleet.external_items({})}
# One that cannot be reopened is refused, writes nothing, and the page shows no Reopen for it: an inbox item whose line
# is gone, and a request the removed owner-request ledger closed.
tidy.append([{"ev": "done", "id": i, "ts": fleet.iso(now), "by": "auto", "rule": "pr_over", "evidence": "x",
              "label": "지워진 줄"} for i in ("ext:k-none", "req:r99")], tidy.latest())
code, out, err = run("board", "reopen", "ext:k-none", "req:r99")
assert code == 1 and "its inbox line is gone" in err and "owner-request ledger was removed" in err, err
assert {"ext:k-none", "req:r99"} <= set(tidy.closed()) and not tidy.can_reopen("ext:k-none") and not tidy.can_reopen("req:r99")

# A --why is stored whole; one that would pass the 4 KiB line is refused, not cut.
code, out, err = run("board", "done", todo[2], "--why", "가" * 1500)
assert code == 1 and "line over 4 KiB" in err and todo[2] not in tidy.closed(), err
code, out, err = run("board", "done", todo[2], "--why", "가" * 300)
assert code == 0 and tidy.closed()[todo[2]]["evidence"] == "가" * 300, (out, err)

# The dashboard's POST: token and same-origin guard, then the same board.close.
import dash  # noqa: E402
import http.server, threading, urllib.request, urllib.error  # noqa: E402
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
port = srv.server_address[1]
dash.HEALTH["port"] = port
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(body, tok=None, origin=None):
    h = {"Content-Type": "application/json", "X-Dash-Token": tok or dash.TOKEN, **({"Origin": origin} if origin else {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/fleet/done", json.dumps(body).encode(), h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


assert post({"ids": [doing[1]]}, tok="nope")[0] == 403
assert post({"ids": [doing[1]]}, origin="http://evil.example.test")[0] == 403
code, out = post({"ids": [doing[1], "row:nope"]})
assert code == 200 and out["written"] == [doing[1]] and [i for i, _ in out["refused"]] == ["row:nope"], out
assert "(card-release 카드)" not in brief()["text"]
code, out = post({"ids": [doing[1]], "undo": True})
assert code == 200 and out["written"] == [doing[1]], out
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/fleet/brief", timeout=30) as r:
    page = json.loads(r.read())
assert doing[1] in {o["id"] for o in page["open"]} and {c["id"] for c in page["closed"]} >= {doing[0], "ext:k-merged"}, page["closed"]
reopen = {c["id"]: c["reopen"] for c in page["closed"]}
assert reopen["ext:k-none"] is reopen["req:r99"] is False and reopen[doing[0]] is True and reopen["ext:k-merged"] is True, reopen
srv.shutdown()

# Rotation: the ledger moves to .1 and every id's last line comes along, so closes and reopens hold.
before = tidy.latest()
fleet.ROTATE_BYTES = 1
try:
    assert tidy.run(now=now) == [], "a reopen still holds after the rotation"
finally:
    fleet.ROTATE_BYTES = 5 * 1024 * 1024
assert tidy.path().with_name("tidy.jsonl.1").exists()
assert {k: {**r, "carried": True} for k, r in before.items()} == tidy.latest()
assert tidy.closed()[todo[2]]["by"] == "owner" and tidy.latest()["card:s4"]["ev"] == "reopen"
assert tidy.timeline() == [], "carried lines are not new events"
assert tidy.run(now=now) == []

# Card keys: the worktree directory and a slug display name, in any case; two cards sharing a key find neither; the
# owner field is never a key. The ticket ID alone and the branch tail are not keys: a follow-up cites its parent's ID,
# and a branch tail is often a generic word pair, so either would close the wrong item.
c_id = {**card("k1", "ABC-1 short title", "offline"), "path": "/w/abc-1-login", "branch": "user/abc-1-login"}
c_dir = {**card("k2", "Readable Title", "offline"), "path": "/w/payments-fix", "branch": "feature/fix-tests"}
cards = {"k1": c_id, "k2": c_dir}
assert tidy.named("ABC-1-LOGIN 머지 대기", cards) is c_id and tidy.named("abc-1-login done", cards) is c_id
assert tidy.named("payments-fix 카드", cards) is c_dir and tidy.named("PAYMENTS-FIX", cards) is c_dir
assert tidy.named("ABC-1 follow-up: backfill old rows", cards) is None, "the parent's ticket ID is not the card"
assert tidy.named("fix-tests on the release branch", cards) is None, "a branch tail is not the card"
# Workers are named after their bare ticket ID (`--name abc-1`): that directory is no key either.
bare = {"k9": {**card("k9", "ABC-1 initial schema", "offline"), "path": "/w/abc-1"}}
assert tidy.keys_of(bare["k9"]) == set() and tidy.named("ABC-1 follow-up: backfill old rows", bare) is None
assert all(tidy.keys_of({"path": f"/w/{k}"}) == set() for k in ("my_proj-12", "9x-1"))
assert tidy.named("abc-1-login-v2 other", cards) is None, "a longer key is not this card"
assert tidy.named("abc-1-login and payments-fix", cards) is None, "two cards named: ambiguous"
twin = {**card("k3", "abc-1-login", "offline"), "path": "/w/other-dir"}
assert tidy.named("abc-1-login", {**cards, "k3": twin}) is None, "two cards with one key: ambiguous"
# Orca's observed linkedPR is merge evidence even when prs.json does not hold that PR.
oc = {**card("k4", "ABC-2 orca linked", "offline"), "path": "/w/abc-2-orca",
      "linked_pr": {"number": 77, "state": "merged", "key": "acme/app#77", "url": "https://github.com/acme/app/pull/77"}}
st2 = {"sessions": [oc], "prs": []}
got = tidy.assess({"doing": [{"title": "abc-2-orca 작업", "owner": "card", "next": "머지"}]}, st2, {}, "owner-me", now)
assert [v["rule"] for k, v in got.items() if k.startswith("doing:")] == ["pr_merged"], got
open_lp = {**oc, "linked_pr": {**oc["linked_pr"], "state": "open"}}
got = tidy.assess({"doing": [{"title": "abc-2-orca 작업", "owner": "card", "next": "머지"}]},
                  {"sessions": [open_lp], "prs": []}, {}, "owner-me", now)
assert not [k for k in got if k.startswith("doing:")], got
print("test_tidy: ok")
