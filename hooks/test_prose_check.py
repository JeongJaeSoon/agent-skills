"""prose_check.py: notes write-plainly's fixed-string patterns in text a PR, commit or ticket save publishes; never blocks.
Run: python3 hooks/test_prose_check.py"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())


def hook(tool_name, tool_input, env=None, raw=None):
    ev = {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(TMP), "tool_name": tool_name, "tool_input": tool_input}
    out = subprocess.run([sys.executable, HERE / "prose_check.py"], input=raw if raw is not None else json.dumps(ev),
                         capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **(env or {})})
    assert out.returncode == 0 and out.stderr == "", out
    if not out.stdout:
        return None
    spec = json.loads(out.stdout)["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and "permissionDecision" not in spec, spec
    return spec["additionalContext"]


def bash(command):
    return hook("Bash", {"command": command})


n = bash('gh pr create --title "fix: 다양한 개선" --body "원활한 리뷰를 위해 diff를 나눴다 — 세 개로."')
assert n and "다양한" in n and "원활한" in n and "—" in n and "write-plainly" in n, n
assert "해당" in bash("gh pr edit 12 --body='해당 파일을 고친다.'")
(TMP / "body.md").write_text("## 왜\n\n스크립트를 통해 결과를 확인한다.\n")
assert "통해" in bash("gh pr create --title t --body-file body.md")
(TMP / "sub").mkdir()
(TMP / "sub" / "msg.txt").write_text("fix: 해당 파일을 고친다\n")
(TMP / "msg.txt").write_text("fix: 실패한 요청을 다시 보낸다\n")
assert "해당" in bash("cd sub && gh pr create --title t --body-file msg.txt")
assert "해당" in bash(f"git -C {TMP / 'sub'} commit -F msg.txt")
assert bash("git commit -C HEAD -F msg.txt") is None
assert "진행" in bash('git commit -m "fix(x): 리뷰를 진행했다"')
assert "다양한" in bash('git -c commit.gpgsign=false -C /r commit -m "docs: 다양한 개선"')
assert "다양한" in bash('git commit -m"docs: 다양한 개선"') and "다양한" in bash('git commit -am "docs: 다양한 개선"')
assert "해당" in bash(f"git -C {TMP / 'sub'} commit -Fmsg.txt")
assert "다양한" in bash('gh pr create -t "다양한 개선" -b ok') and "통해" in bash("gh pr create -t t -F body.md")
assert "다양한" in bash('git -C "/tmp/my repo" -c a=b commit -m "docs: 다양한 개선"')
assert bash('git log --grep commit -m "다양한"') is None
assert bash("gh pr create --title clean --body clean && echo --body 다양한") is None
assert bash('git add -A; gh issue create --body "다양한"; git commit -m "fix: 실패한 요청을 다시 보낸다"') is None
assert bash('echo -m 다양한\ngit commit -m "fix: 다시 보낸다"') is None
assert "다양한" in bash('git add -A && git commit -m "docs: 다양한 개선" && git push')
assert "통해" in bash("git -C sub status && gh pr create --title t --body-file body.md")
assert bash("cat <<EOF > notes.txt\n다양한 —\nEOF\ngit commit -m clean") is None
assert "crucial" in bash('gh pr create --title "fix; parser" --body "$(cat <<EOF\nA crucial fix.\nEOF\n)"')
assert "crucial" in bash("git commit -F - \\\n<<EOF\nA crucial fix.\nEOF\n")
assert "crucial" in bash("cat <<EOF > a.txt\ndon't\nEOF\ngit commit -F - <<EOF\nA crucial fix.\nEOF\n")
heredoc = "git commit -F - <<'EOF'\nfeat(x): add y\n\nThis change serves as a crucial testament.\nEOF\n"
n = bash(heredoc)
assert n and "serves as" in n and "crucial" in n, n
assert "것 같" in bash('git commit -m "$(cat <<\'EOF\'\nfix: 캐시 문제인 것 같습니다\nEOF\n)"')
assert "효율적으로" in hook("mcp__claude_ai_Linear__save_issue", {"id": "ABC-1", "description": "요청을 효율적으로 처리한다."})
assert "In order to" in hook("mcp__atlassian__addCommentToJiraIssue", {"issueIdOrKey": "ABC-1", "commentBody": "In order to ship"})
assert "bold label" in hook("mcp__claude_ai_Linear__save_comment", {"issueId": "x", "body": "**성능:** 성능이 좋아졌다"})

assert bash('gh pr create --title "fix(sync): 실패한 요청을 세 번까지 다시 보낸다" --body "## 왜\n\n`orch land`만 머지한다."') is None
assert bash('git commit -m "docs: `--body` 와 ```\n다양한 —\n``` 은 코드다"') is None
assert bash("echo '다양한 — 해당 '") is None and bash("git log --grep '다양한'") is None
assert hook("Edit", {"file_path": "/r/a.md", "new_string": "다양한"}) is None
assert hook("mcp__claude_ai_Linear__get_issue", {"id": "다양한"}) is None
assert hook("mcp__claude_ai_Linear__save_issue", {"id": "다양한", "description": "Linear와 Jira를 지원한다."}) is None
assert hook("Bash", {"command": 'git commit -m "다양한"'}, env={"AGENT_SKILLS_PROSE_CHECK": "0"}) is None
assert hook("Bash", {}, raw="not json") is None
print("test_prose_check: ok")
