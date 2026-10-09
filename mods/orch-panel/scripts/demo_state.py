#!/usr/bin/env python3
"""Write an invented fleet state.json and stages.json for trying the mod without a real fleet (and for screenshots of a public repo).

    python3 demo_state.py <state dir> [<coordinator cwd>]
    ORCH_FLEET_STATE=<state dir> claude --plugin-dir mods/orch-panel

Times are relative to now, so ages read the same whenever it runs. Everything in it is made up (acme/*).
"""
import datetime as dt, json, os, pathlib, sys


def main(out, coord):
    now = dt.datetime.now(dt.timezone.utc)
    ago = lambda **kw: (now - dt.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = f"demo-1::{coord}"
    search = "demo-2::/work/acme/web-search-lead"
    ops = "demo-3::/work/acme/ops-alerts-lead"
    state = {
        "generated_at": ago(seconds=12),
        "root": root,
        "me": "you-dev",
        "sessions": [
            {"id": root, "name": "coordinator", "kind": "orchestrator", "phase": "working", "parent": None},
            {"id": search, "name": "acme-web-search", "kind": "orchestration", "phase": "waiting", "parent": root},
            {"id": ops, "name": "acme-ops-alerts", "kind": "orchestration", "phase": "working", "parent": root},
            {"id": "demo-4::/work/acme/web-sort", "name": "acme-web-sort", "kind": "task", "phase": "working",
             "parent": search, "task_title": "정렬 버그", "linked_pr": {"number": 45, "state": "open"},
             "terminals": [{"handle": "term_sort"}]},
            {"id": "demo-5::/work/acme/web-reindex", "name": "acme-web-reindex", "kind": "task", "phase": "idle",
             "parent": search, "task_title": "인덱스 재구축", "linked_pr": {"number": 88, "state": "merged"}},
            {"id": "demo-6::/work/acme/docs", "name": "acme-docs", "kind": "standalone", "phase": "open", "parent": root},
            {"id": "demo-7::/work/acme/old-spike", "name": "acme-old-spike", "kind": "standalone", "phase": "offline",
             "parent": root},
        ],
        "holds": [
            {"kind": "lane", "resource": "acme/web@main", "by": "#45", "note": "web-search", "at": ago(minutes=12),
             "session": None},
            {"kind": "resource", "resource": "browser", "by": "term_sort", "note": "staging 로그인 확인",
             "at": ago(minutes=3), "session": "demo-4::/work/acme/web-sort"},
        ],
        "items": [
            {"key": "decision:d7", "type": "decision", "decision": "d7", "title": "내보내기 형식 — CSV / JSON",
             "session": root, "at": ago(hours=2, minutes=4), "options": [{"label": "CSV"}, {"label": "JSON"}], "recommend": 1},
            {"key": "mail:m1", "type": "question", "title": "acme/web 리드: 캐시 TTL 5분 vs 1시간?", "session": search,
             "at": ago(minutes=14)},
            {"key": f"wait:{ops}:1", "type": "permission", "title": "Bash 권한 대기", "session": ops, "at": ago(minutes=3)},
            {"key": "pr:acme/web#45:ci_failed", "type": "ci_failed", "title": "acme/web#45 CI 실패", "session": search,
             "at": ago(minutes=30)},
        ],
        "prs": [
            {"key": "acme/web#40", "state": "MERGED", "merged_at": ago(days=11)},
            {"key": "acme/web#42", "state": "MERGED", "merged_at": ago(days=7)},
            {"key": "acme/ops#88", "state": "MERGED", "merged_at": ago(days=7)},
            {"key": "acme/web#45", "state": "OPEN", "ci": "failure", "decision": None},
            {"key": "acme/web#47", "state": "MERGED", "merged_at": ago(days=6)},
            {"key": "acme/web#51", "state": "OPEN", "ci": "success", "merge_state": "CLEAN"},
        ],
        "runs": [], "tasks": [], "timeline": [],
        "sources": {"orca": {"ok": True, "updated_at": ago(seconds=12), "error": None},
                    "runs": {"ok": True, "updated_at": ago(seconds=40), "error": None},
                    "github": {"ok": True, "updated_at": ago(minutes=1), "error": None}},
    }
    cell = lambda mark, evidence=None, by=None: {"mark": mark, "evidence": evidence, "by": by}
    stages = {
        "rows": {
            "login": {"id": "login", "title": "로그인 개선", "pr": "acme/web#40",
                      "cells": {"dev": cell("ok", "v1.7.2"), "prod": cell("ok", "로그인 성공률 99.8%")}},
            "search": {"id": "search", "title": "검색 개선", "pr": "acme/web#42",
                       "cells": {"dev": cell("ok", "v1.8.0"), "prod": cell("checking", "확인 중", "QA 리드")}},
            "reindex": {"id": "reindex", "title": "인덱스 재구축", "pr": "acme/ops#88", "parent": "search",
                        "cells": {"dev": cell("ok", "1,204건 재색인"), "prod": cell("na")}},
            "sort": {"id": "sort", "title": "정렬 버그", "pr": "acme/web#45", "parent": "search", "cells": {}},
            "alerts": {"id": "alerts", "title": "알림 정리", "pr": "acme/web#47",
                       "cells": {"dev": cell("partial", "로그로만 확인"), "prod": cell("fail", "다음 배포 대기")}},
        },
        "order": ["login", "search", "reindex", "sort", "alerts"],
        "env": {"prod": "v1.7.2", "dev": "v1.8.0"},
        "next": ["acme/web#45 리뷰 → 머지 (에이전트, 오늘)", "v1.8.0 prod 배포 (사람, 미정)"],
    }
    path = pathlib.Path(out).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    for name, data in (("stages.json", stages), ("state.json", state)):
        tmp = path / f"{name}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, path / name)
    print(path / "state.json")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], os.path.realpath(sys.argv[2] if len(sys.argv) > 2 else os.getcwd()))
