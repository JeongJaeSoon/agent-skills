"""tracker_gate.py: MCP ticket calls on stdin fixtures, against a fake `orca` on PATH.
Run: python3 hooks/test_tracker_gate.py"""
import datetime, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
BIN, STATE, CONFIG, MODE = TMP / "bin", TMP / "state", TMP / "config.json", TMP / "mode.json"
BIN.mkdir()

# The fake answers the two probes from MODE: {"linear": [team keys] | "fail", "jira": [site objects] | "fail"}.
(BIN / "orca").write_text(f"""#!/usr/bin/env python3
import json, sys
mode = json.load(open("{MODE}"))
which = sys.argv[1]
if mode.get(which) == "fail":
    sys.exit(1)
if which == "linear":
    print(json.dumps({{"ok": True, "result": {{"teams": [{{"key": k}} for k in mode["linear"]]}}}}))
else:
    print(json.dumps({{"ok": True, "result": {{"connected": bool(mode["jira"]), "sites": mode["jira"]}}}}))
""")
(BIN / "orca").chmod(0o755)


def connect(linear=("ABC",), jira=({"id": "site-1", "cloudId": "cloud-1"},)):
    MODE.write_text(json.dumps({"linear": list(linear) if linear != "fail" else "fail",
                                "jira": list(jira) if jira != "fail" else "fail"}))
    cache = STATE / "tracker-gate-cache.json"
    if cache.exists():
        cache.unlink()


def run(name, args, level=None, transcript=None, env=None):
    CONFIG.write_text(json.dumps({"tracker": {"orca_first": level}} if level else {}))
    event = {"tool_name": name, "tool_input": args, "transcript_path": str(transcript) if transcript else None}
    e = {"PATH": f"{BIN}:/usr/bin:/bin", "AGENT_SKILLS_CONFIG": str(CONFIG), "AGENT_SKILLS_STATE": str(STATE), **(env or {})}
    r = subprocess.run([sys.executable, HERE / "tracker_gate.py"], input=json.dumps(event), capture_output=True,
                       text=True, env=e)
    assert r.returncode == 0, r
    if not r.stdout:
        return None, ""
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return out.get("permissionDecision", "context"), out.get("permissionDecisionReason") or out.get("additionalContext")


def last_code():
    return json.loads((STATE / "tracker-gate.jsonl").read_text().splitlines()[-1])["code"]


def denied(name, args, *needles, **kw):
    decision, text = run(name, args, **kw)
    assert decision == "deny", (name, args, decision, text)
    for n in needles:
        assert n in text, (n, text)
    return text


def passed(name, args, code, **kw):
    decision, text = run(name, args, **kw)
    assert decision is None, (name, args, text)
    assert last_code() == code, (name, last_code(), code)


def transcript(*entries):
    path = TMP / "t.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def bash_pair(tid, command, ago=0, result=True):
    stamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=ago)).isoformat()
    rows = [{"timestamp": stamp, "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash",
                                                          "input": {"command": command}}]}}]
    if result:
        rows.append({"timestamp": stamp, "message": {"content": [{"type": "tool_result", "tool_use_id": tid}]}})
    return rows


LIN = "mcp__linear__"
connect()

# A covered Linear call is denied with the command, under every server prefix.
for prefix in ("mcp__linear__", "mcp__claude_ai_Linear__", "mcp__codex_apps__linear_"):
    denied(prefix + "get_issue", {"id": "ABC-1"}, "orca linear issue ABC-1")
denied(LIN + "get_issue", {"id": "https://linear.app/acme/issue/ABC-9/some-slug"}, "orca linear issue ABC-9")
denied(LIN + "list_issues", {"project": "P", "state": "Todo", "includeArchived": True, "limit": 5},
       "orca linear list-issues --project P --state Todo --include-archived --limit 5")
denied(LIN + "list_comments", {"issueId": "ABC-1"}, "orca linear issue ABC-1 --comments")
denied(LIN + "save_comment", {"issueId": "ABC-1", "body": "hi"}, "orca linear comment add ABC-1 --body-file <file>")
denied(LIN + "list_issue_statuses", {"team": "ABC"}, "orca linear team states --team ABC")
denied(LIN + "list_teams", {}, "orca linear team list")
for tool in ("list_users", "list_issue_labels", "list_issue_statuses"):
    passed(LIN + tool, {}, "pass-unsupported-key")  # the CLI needs --team
t = denied(LIN + "save_issue", {"id": "ABC-1", "state": "Done", "title": "T", "addLabels": ["bug"], "blockedBy": ["ABC-2"],
                                "priority": 2, "links": [{"url": "https://e.test/pr/1", "title": "PR"}]},
           "orca linear status set ABC-1 --to Done", "orca linear save-issue ABC-1 --title T --priority high",
           "orca linear label add ABC-1 --label bug", "orca linear relation add ABC-1 --related ABC-2 --type blocked-by",
           "orca linear attach ABC-1 --url https://e.test/pr/1 --title PR")
denied(LIN + "save_issue", {"id": "ABC-1", "removeBlocks": ["ABC-3"]}, "relation remove ABC-1 --related ABC-3 --type blocks")
denied(LIN + "save_issue", {"team": "ABC", "title": "New", "description": "d", "labels": ["x"], "parentId": "ABC-5"},
       "orca linear create --title New --team ABC", "--body-file <file>", "--label x", "--parent ABC-5")

# One field Orca lacks, or an operand it rejects, passes the whole call.
passed(LIN + "save_issue", {"id": "ABC-1", "title": "T", "cycle": "c"}, "pass-unsupported-key")
for field in ("milestone", "template", "delegate", "addReleases", "setReleases", "patch", "slaType"):
    passed(LIN + "save_issue", {"id": "ABC-1", field: "x"}, "pass-unsupported-key")
passed(LIN + "save_issue", {"team": "ABC", "title": "T", "relatedTo": ["ABC-1"]}, "pass-unsupported-key")
passed(LIN + "save_issue", {"id": "ABC-1", "assignee": None}, "pass-unsupported-key")
passed(LIN + "list_issues", {"creator": "me"}, "pass-unsupported-key")
passed(LIN + "list_issues", {"fields": ["completedAt"]}, "pass-unsupported-key")
passed(LIN + "save_comment", {"issueId": "ABC-1", "body": "x", "parentId": "c1"}, "pass-unsupported-key")
passed(LIN + "save_comment", {"projectId": "p", "body": "x"}, "pass-unsupported-key")
passed(LIN + "get_issue", {"id": "11111111-2222-3333-4444-555555555555"}, "pass-operand")
passed(LIN + "get_issue", {"id": "9X-1"}, "pass-operand")
passed(LIN + "get_issue", {"id": "XYZ-1"}, "pass-operand")
passed(LIN + "save_issue", {"id": "ABC-1", "parentId": "9X-1"}, "pass-operand")
for tool in ("save_document", "get_document", "list_cycles", "list_milestones", "create_attachment_from_upload",
             "list_diffs", "save_project", "list_releases", "extract_images", "whoami", "no_such_tool"):
    decision, _ = run(LIN + tool, {"id": "ABC-1"})
    assert decision is None, tool

# Connection: none, a failed probe, and the probe's cache.
connect(linear=())
passed(LIN + "get_issue", {"id": "ABC-1"}, "pass-no-connection")
connect(linear="fail")
passed(LIN + "get_issue", {"id": "ABC-1"}, "pass-unknown-probe")
connect()
denied(LIN + "get_issue", {"id": "ABC-1"})
MODE.write_text(json.dumps({"linear": "fail", "jira": []}))
denied(LIN + "get_issue", {"id": "ABC-1"})  # served from the cache

# Jira.
connect()
JIRA = "mcp__atlassian__"
denied(JIRA + "getJiraIssue", {"issueIdOrKey": "ENG-1"}, "orca jira issue ENG-1")
denied("mcp__claude_ai_Atlassian__getJiraIssue", {"issueIdOrKey": "ENG-1", "cloudId": "cloud-1"},
       "orca jira issue ENG-1 --site site-1")
passed(JIRA + "getJiraIssue", {"issueIdOrKey": "ENG-1", "cloudId": "other"}, "pass-operand")
passed(JIRA + "getJiraIssue", {"issueIdOrKey": "10001"}, "pass-operand")
passed(JIRA + "getJiraIssue", {"issueIdOrKey": "ENG-1", "fields": ["summary"]}, "pass-unsupported-key")
denied(JIRA + "searchJiraIssuesUsingJql", {"jql": "project = ENG", "maxResults": 5}, "orca jira search 'project = ENG' --limit 5")
passed(JIRA + "searchJiraIssuesUsingJql", {"jql": "x", "nextPageToken": "t"}, "pass-unsupported-key")
denied(JIRA + "addCommentToJiraIssue", {"issueIdOrKey": "ENG-1", "commentBody": "x"}, "orca jira comment add ENG-1 --body-file <file>")
denied(JIRA + "getTransitionsForJiraIssue", {"issueIdOrKey": "ENG-1"}, "orca jira status list ENG-1")
denied(JIRA + "transitionJiraIssue", {"issueIdOrKey": "ENG-1", "transition": {"id": "31"}}, "orca jira status set ENG-1 --to-id 31")
passed(JIRA + "transitionJiraIssue", {"issueIdOrKey": "ENG-1", "transition": {"id": "31"}, "fields": {"a": 1}},
       "pass-unsupported-key")
denied(JIRA + "createJiraIssue", {"projectKey": "ENG", "issueTypeName": "Task", "summary": "S", "description": "d",
                                  "additional_fields": {"parent": {"key": "ENG-2"}}},
       "orca jira project types --project ENG\n", "orca jira create --project ENG --type <issueTypeId> --title S --body-file <file> --fields-json")
denied(JIRA + "createJiraIssue", {"projectKey": "ENG", "issueTypeName": "Task", "summary": "S", "cloudId": "cloud-1"},
       "orca jira project types --project ENG --site site-1\n", "--title S --site site-1")
passed(JIRA + "createJiraIssue", {"projectKey": "ENG", "issueTypeName": "Task", "summary": "S", "assignee_account_id": "u"},
       "pass-unsupported-key")
passed(JIRA + "createJiraIssue", {"projectKey": "ENG", "issueTypeName": "Task", "summary": "S", "parent": "x"},
       "pass-unsupported-key")
denied(JIRA + "editJiraIssue", {"issueIdOrKey": "ENG-1", "fields": {"labels": ["a", "b"]}}, "orca jira label set ENG-1 --label a --label b")
denied(JIRA + "editJiraIssue", {"issueIdOrKey": "ENG-1", "fields": {"assignee": {"accountId": "u1"}}}, "assignee set ENG-1 --to-id u1")
passed(JIRA + "editJiraIssue", {"issueIdOrKey": "ENG-1", "fields": {"summary": "x"}}, "pass-unsupported-key")
passed(JIRA + "editJiraIssue", {"issueIdOrKey": "ENG-1", "fields": {"labels": ["a"], "priority": {"name": "High"}}},
       "pass-unsupported-key")
for tool in ("getConfluencePage", "createIssueLink", "lookupJiraAccountId", "getAccessibleAtlassianResources"):
    assert run(JIRA + tool, {"issueIdOrKey": "ENG-1"})[0] is None, tool
connect(jira=())
passed(JIRA + "getJiraIssue", {"issueIdOrKey": "ENG-1"}, "pass-no-connection")
connect(jira="fail")
passed(JIRA + "getJiraIssue", {"issueIdOrKey": "ENG-1"}, "pass-unknown-probe")
connect()

# Evidence: a paired `orca` run for the same ticket lets the MCP call through; look-alikes do not.
call = (LIN + "get_issue", {"id": "ABC-1"})
passed(*call, "pass-evidence", transcript=transcript(*bash_pair("a", "orca linear issue ABC-1 --json")))
passed(*call, "pass-evidence", transcript=transcript(*bash_pair("a", "cd /tmp && orca linear comment add abc-1 --body x")))
denied(*call, transcript=transcript(*bash_pair("a", "orca linear issue ABC-1", result=False)))
denied(*call, transcript=transcript(*bash_pair("a", 'echo "orca linear issue ABC-1"')))
denied(*call, transcript=transcript(*bash_pair("a", "orca linear issue ABC-2")))
denied(*call, transcript=transcript(*bash_pair("a", "orca jira issue ABC-1")))
denied(*call, transcript=transcript(*bash_pair("a", "python3 tracker.py get ABC-1")))
denied(*call, transcript=transcript(*bash_pair("a", "orca linear issue ABC-1", ago=3600)))
denied(*call, transcript=TMP / "missing.jsonl")
denied(*call, transcript=transcript(*bash_pair("a", "orca linear issue ABC-10")))  # ABC-10 is not ABC-1
passed(*call, "pass-evidence", transcript=transcript(*bash_pair("a", "orca linear issue https://linear.app/x/issue/ABC-1/slug")))
bad = bash_pair("a", "orca linear issue ABC-1")
bad[0].pop("timestamp")
denied(*call, transcript=transcript(*bad))  # no timestamp is no proof of recency
t = transcript(*bash_pair("a", "orca linear issue ABC-1"))
t.write_text("[]\n3\n" + '{"message": []}\n' + t.read_text())  # malformed rows are skipped, not fatal
passed(*call, "pass-evidence", transcript=t)
passed(LIN + "list_issues", {"project": "P"}, "pass-evidence", transcript=transcript(*bash_pair("a", "orca linear list-issues --team ABC")))
denied(LIN + "list_issues", {"project": "P"}, transcript=transcript(*bash_pair("a", "orca linear issue ABC-1")))

# Levels and switches.
decision, text = run(*call, level="warn")
assert decision == "context" and "orca linear issue ABC-1" in text, (decision, text)
assert run(*call, level="ask")[0] == "ask"
assert run(*call, level="off") == (None, "") and last_code() == "pass-off"
assert run(*call, env={"AGENT_SKILLS_TRACKER_GATE": "0"}) == (None, "")
assert run(*call, level="deny")[0] == "deny"
assert run("Bash", {"command": "orca linear issue ABC-1"}) == (None, "")
assert run("mcp__other__get_issue", {"id": "ABC-1"}) == (None, "")

# A fault of its own never stops the call, and leaves a line.
r = subprocess.run([sys.executable, HERE / "tracker_gate.py"], input="not json", capture_output=True, text=True,
                   env={"PATH": "/usr/bin:/bin", "AGENT_SKILLS_STATE": str(STATE)})
assert r.returncode == 0 and r.stdout == "" and "tracker_gate" in (STATE / "guard-errors.jsonl").read_text(), r
# An unwritable state directory changes no verdict.
bad = TMP / "afile"
bad.write_text("x")
connect()
r = subprocess.run([sys.executable, HERE / "tracker_gate.py"], text=True, capture_output=True,
                   input=json.dumps({"tool_name": call[0], "tool_input": call[1]}),
                   env={"PATH": f"{BIN}:/usr/bin:/bin", "AGENT_SKILLS_CONFIG": str(CONFIG), "AGENT_SKILLS_STATE": str(bad)})
assert r.returncode == 0 and "permissionDecision" in r.stdout, r
print("tracker_gate: all pass")
