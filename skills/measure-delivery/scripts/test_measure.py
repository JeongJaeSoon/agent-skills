"""Offline transcript-adapter checks; no real accounts or session data are read."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

import measure

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    old_config, old_argv, old_gh = os.environ.get("CLAUDE_CONFIG_DIR"), sys.argv, measure.gh
    os.environ["CLAUDE_CONFIG_DIR"] = str(root)
    try:
        a, b = measure.ts("2026-01-01T00:00:00Z"), measure.ts("2026-01-02T00:00:00Z")
        assert not measure.usage("app", a, b)
        project = root / "projects" / "-example-app"
        project.mkdir(parents=True)
        row = {"type": "assistant", "timestamp": "2026-01-01T12:00:00Z", "requestId": "r",
               "message": {"id": "m", "usage": {"output_tokens": 7, "cache_read_input_tokens": 11}}}
        (project / "session.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
        usage = measure.usage("app", a, b)
        assert usage["claude_calls"] == 1 and usage["claude_output"] == 7
        assert usage["claude_cache_read"] == 11
        assert not measure.usage("unrelated", a, b)
        assert not measure.usage("app", b, measure.ts("2026-01-03T00:00:00Z"))
        issues = root / "issues.json"
        issues.write_text(json.dumps([{"id": "ABC-1", "state_type": "started",
                                      "created_at": "2026-01-01T01:00:00Z"}]))
        measure.gh = lambda *args: []
        output = root / "report.json"
        sys.argv = ["measure.py", "--repo", "acme/app", "--issues", str(issues),
                    "--since", a.isoformat(), "--until", b.isoformat(), "--json", str(output)]
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            measure.main()
        report = json.loads(output.read_text())
        assert report["usage_measured"] and report["usage"]["claude_output"] == 7
        assert str(root / "projects") in text.getvalue()
        sys.argv += ["--usage-match", "unrelated"]
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            measure.main()
        assert not json.loads(output.read_text())["usage_measured"]
        assert "미측정" in text.getvalue()
    finally:
        sys.argv, measure.gh = old_argv, old_gh
        if old_config is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_config

print("measure.py: all pass")
