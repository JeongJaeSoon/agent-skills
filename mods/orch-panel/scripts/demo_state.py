#!/usr/bin/env python3
"""Write an invented fleet state.json for trying the mod without a real fleet (and for screenshots of a public repo).

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
        "prs": [], "runs": [], "tasks": [], "timeline": [],
        "sources": {"orca": {"ok": True, "updated_at": ago(seconds=12), "error": None},
                    "runs": {"ok": True, "updated_at": ago(seconds=40), "error": None},
                    "github": {"ok": True, "updated_at": ago(minutes=1), "error": None}},
    }
    path = pathlib.Path(out).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    tmp = path / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    os.replace(tmp, path / "state.json")
    print(path / "state.json")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], os.path.realpath(sys.argv[2] if len(sys.argv) > 2 else os.getcwd()))
