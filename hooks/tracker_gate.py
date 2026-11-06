#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: ticket work goes through `orca linear` / `orca jira`, not the tracker MCP.

Long-lived sessions are not reloaded and subagents do not read their parent's skills, so skill text alone left most
ticket calls on MCP. This hook looks at Linear and Atlassian MCP calls and, when Orca can run the same call as written,
denies it with the command to run instead.

A call is denied only when ALL of these hold; anything else passes (the gate is a nudge, not a boundary):
- the tool is in TOOLS and every top-level argument is one its command can carry. One field Orca lacks (cycle,
  milestone, delegate, a reply, a comment on a project, Jira `fields`/`expand`...) passes the whole call.
- every ticket operand is a key Orca accepts (`ABC-12` or a linear.app issue URL), and for Linear its team key is one
  Orca has connected. UUIDs, numeric Jira ids and keys of another workspace pass.
- Orca has a connection for the tool's own tracker (`orca linear team list` / `orca jira connection`). A probe that
  fails or times out passes. Jira `cloudId` must match a saved site record, and that site goes in the command.
- the session has not already run Orca for this ticket in the last 30 minutes (a paired Bash call and result in the
  transcript): after one real attempt, MCP is the fallback.

Level `tracker.orca_first` in ~/.claude/agent-skills.json: deny (default), ask, warn, off. AGENT_SKILLS_TRACKER_GATE=0
turns it off. Every decision adds a row to $AGENT_SKILLS_STATE/tracker-gate.jsonl with a code, so a fallback and a
hook fault can be told apart. Any error passes silently.
"""
import datetime, json, os, re, shlex, subprocess, sys, tempfile, time

from lib import config, shell

KEY = "tracker.orca_first"
ISSUE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
LINEAR_URL = re.compile(r"^https://linear\.app/[^/]+/issue/([A-Za-z][A-Za-z0-9_]*-\d+)(?:/[^?#]*)?(?:[?#].*)?$")
PROBE_SECONDS, EVIDENCE_WINDOW, TAIL_BYTES = 2, 30 * 60, 512 * 1024
CACHE_TTL, UNKNOWN_TTL = 600, 30
PRIORITY = {"0": "none", "1": "urgent", "2": "high", "3": "medium", "4": "low"}
PRIORITY_NAMES = set(PRIORITY.values())


class Pass(Exception):
    """The call is not one Orca covers as written; the message is the log code."""


def provider_of(name):
    low = name.lower()
    if "linear" in low:
        return "linear"
    if "jira" in low or "atlassian" in low:
        return "jira"
    return None


def tool_of(name):
    return re.sub(r"^(?:linear|jira|atlassian)_", "", name.split("__")[-1].lower())


def q(v):
    return shlex.quote(str(v))


def scalar(v):
    if isinstance(v, bool) or v is None or not isinstance(v, (str, int, float)):
        raise Pass("pass-unsupported-key")
    return v


def only(args, allowed):
    if not set(args) <= set(allowed):
        raise Pass("pass-unsupported-key")


def issue_key(v):
    """The key Orca accepts for a ticket operand, or Pass."""
    if isinstance(v, str):
        if ISSUE.match(v):
            return v
        m = LINEAR_URL.match(v)
        if m:
            return m.group(1)
    raise Pass("pass-operand")


def as_list(v):
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and v:
        return v
    raise Pass("pass-unsupported-key")


def operands(args):
    """Every ticket key the call names, for the connection check."""
    out = []
    for k in ("id", "issueId", "issueIdOrKey", "parentId", "relatedTo", "blockedBy", "blocks", "removeRelatedTo",
              "removeBlocks", "removeBlockedBy"):
        v = args.get(k)
        for x in ([v] if isinstance(v, str) else v if isinstance(v, list) else []):
            if isinstance(x, str) and (ISSUE.match(x) or LINEAR_URL.match(x)):
                out.append(issue_key(x))
    return out


# ---- Linear: each handler returns the commands Orca runs for the call, or raises Pass

LIST_FLAGS = {"team": "--team", "project": "--project", "state": "--state", "assignee": "--assignee",
              "delegate": "--delegate", "label": "--label", "query": "--query", "limit": "--limit",
              "cursor": "--cursor", "orderBy": "--order-by", "priority": "--priority", "createdAt": "--created-at",
              "updatedAt": "--updated-at", "cycle": "--cycle", "release": "--release"}


def lin_get_issue(a):
    only(a, {"id"})
    return [f"orca linear issue {q(issue_key(a['id']))}  # add --comments --attachments --relations --children"]


def lin_list_issues(a):
    only(a, set(LIST_FLAGS) | {"parentId", "includeArchived"})
    parts = []
    for k, v in a.items():
        if k == "parentId":
            parts.append(f"--parent-id {q(issue_key(v) if v != 'null' else v)}")
        elif k == "includeArchived":
            if v is True:
                parts.append("--include-archived")
            elif v is not False:
                raise Pass("pass-unsupported-key")
        else:
            parts.append(f"{LIST_FLAGS[k]} {q(scalar(v))}")
    return ["orca linear list-issues " + " ".join(parts)]


def lin_team_read(sub):
    def handler(a):
        only(a, {"team"})
        if "team" not in a:  # the CLI needs a team; an all-teams read has no Orca form
            raise Pass("pass-unsupported-key")
        return [f"orca linear team {sub} --team {q(scalar(a['team']))}"]
    return handler


def lin_team_list(a):
    only(a, set())
    return ["orca linear team list"]


def lin_list_comments(a):
    only(a, {"issueId"})
    return [f"orca linear issue {q(issue_key(a['issueId']))} --comments"]


def lin_save_comment(a):
    only(a, {"issueId", "body"})
    scalar(a["body"])
    return [f"orca linear comment add {q(issue_key(a['issueId']))} --body-file <file>"]


def lin_save_issue(a):
    if "id" not in a:
        only(a, {"team", "title", "description", "state", "assignee", "priority", "estimate", "dueDate", "labels",
                 "project", "parentId"})
        if "title" not in a:
            raise Pass("pass-unsupported-key")
        parts = [f"--title {q(scalar(a['title']))}"]
        for k, flag in (("team", "--team"), ("state", "--state"), ("assignee", "--assignee"), ("project", "--project"),
                        ("estimate", "--estimate"), ("dueDate", "--due-date")):
            if k in a:
                parts.append(f"{flag} {q(scalar(a[k]))}")
        if "description" in a:
            scalar(a["description"])
            parts.append("--body-file <file>")
        if "priority" in a:
            parts.append(f"--priority {q(priority(a['priority']))}")
        for label in as_list(a["labels"]) if "labels" in a else []:
            parts.append(f"--label {q(scalar(label))}")
        if "parentId" in a:
            parts.append(f"--parent {q(issue_key(a['parentId']))}")
        return ["orca linear create " + " ".join(parts)]
    only(a, {"id", "title", "description", "state", "assignee", "priority", "estimate", "dueDate", "labels",
             "addLabels", "removeLabels", "project", "parentId", "relatedTo", "blockedBy", "blocks", "removeRelatedTo",
             "removeBlocks", "removeBlockedBy", "links"})
    ident = q(issue_key(a["id"]))
    cmds, save = [], []
    if "state" in a:
        cmds.append(f"orca linear status set {ident} --to {q(scalar(a['state']))}")
    for k, flag in (("title", "--title"), ("assignee", "--assignee"), ("project", "--project"),
                    ("estimate", "--estimate"), ("dueDate", "--due-date")):
        if k in a:
            save.append(f"{flag} {q(scalar(a[k]))}")
    if "description" in a:
        scalar(a["description"])
        save.append("--body-file <file>")
    if "priority" in a:
        save.append(f"--priority {q(priority(a['priority']))}")
    if "parentId" in a:
        save.append(f"--parent-id {q(issue_key(a['parentId']))}")
    if save:
        cmds.append(f"orca linear save-issue {ident} " + " ".join(save))
    for k, sub in (("labels", "set"), ("addLabels", "add"), ("removeLabels", "remove")):
        if k in a:
            cmds.append(f"orca linear label {sub} {ident} " + " ".join(f"--label {q(scalar(x))}" for x in as_list(a[k])))
    for k, sub, kind in (("relatedTo", "add", "related"), ("blockedBy", "add", "blocked-by"), ("blocks", "add", "blocks"),
                         ("removeRelatedTo", "remove", "related"), ("removeBlocks", "remove", "blocks"),
                         ("removeBlockedBy", "remove", "blocked-by")):
        for other in as_list(a[k]) if k in a else []:
            cmds.append(f"orca linear relation {sub} {ident} --related {q(issue_key(other))} --type {kind}")
    for link in as_list(a["links"]) if "links" in a else []:
        if not isinstance(link, dict) or not set(link) <= {"url", "title"} or "url" not in link:
            raise Pass("pass-unsupported-key")
        title = f" --title {q(scalar(link['title']))}" if "title" in link else ""
        cmds.append(f"orca linear attach {ident} --url {q(scalar(link['url']))}{title}")
    if not cmds:
        raise Pass("pass-unsupported-key")
    return cmds


def priority(v):
    name = PRIORITY.get(str(v), str(v).lower())
    if name not in PRIORITY_NAMES:
        raise Pass("pass-unsupported-key")
    return name


LINEAR = {
    "get_issue": lin_get_issue, "list_issues": lin_list_issues, "list_comments": lin_list_comments,
    "save_comment": lin_save_comment, "save_issue": lin_save_issue,
    "list_issue_statuses": lin_team_read("states"), "list_issue_labels": lin_team_read("labels"),
    "list_users": lin_team_read("members"),
    "list_teams": lambda a: lin_team_list(a),
}


# ---- Jira

def jira_key(v):
    if isinstance(v, str) and ISSUE.match(v):
        return v
    raise Pass("pass-operand")


def jira_get(a):
    only(a, {"issueIdOrKey", "cloudId"})
    return [f"orca jira issue {q(jira_key(a['issueIdOrKey']))}"]


def jira_search(a):
    only(a, {"jql", "cloudId", "maxResults"})
    limit = f" --limit {q(scalar(a['maxResults']))}" if "maxResults" in a else ""
    return [f"orca jira search {q(scalar(a['jql']))}{limit}"]


def jira_transitions(a):
    only(a, {"issueIdOrKey", "cloudId"})
    return [f"orca jira status list {q(jira_key(a['issueIdOrKey']))}"]


def jira_transition(a):
    only(a, {"issueIdOrKey", "transition", "cloudId"})
    t = a.get("transition")
    if not isinstance(t, dict) or set(t) != {"id"}:
        raise Pass("pass-unsupported-key")
    return [f"orca jira status set {q(jira_key(a['issueIdOrKey']))} --to-id {q(scalar(t['id']))}"]


def jira_comment(a):
    only(a, {"issueIdOrKey", "commentBody", "cloudId"})
    scalar(a["commentBody"])
    return [f"orca jira comment add {q(jira_key(a['issueIdOrKey']))} --body-file <file>"]


def jira_create(a):
    only(a, {"cloudId", "projectKey", "issueTypeName", "summary", "description", "contentFormat", "additional_fields"})
    if a.get("contentFormat", "markdown") != "markdown":
        raise Pass("pass-unsupported-key")
    project = q(scalar(a["projectKey"]))
    types = f"orca jira project types --project {project}"
    cmd = f"orca jira create --project {project} --type <issueTypeId> --title {q(scalar(a['summary']))}"
    if "description" in a:
        scalar(a["description"])
        cmd += " --body-file <file>"
    if "additional_fields" in a:
        if not isinstance(a["additional_fields"], dict):
            raise Pass("pass-unsupported-key")
        cmd += f" --fields-json {q(json.dumps(a['additional_fields'], separators=(',', ':')))}"
    return [types, cmd]


def jira_edit(a):
    only(a, {"issueIdOrKey", "cloudId", "fields"})
    fields = a.get("fields")
    if not isinstance(fields, dict) or not fields or not set(fields) <= {"labels", "assignee"}:
        raise Pass("pass-unsupported-key")
    key, cmds = q(jira_key(a["issueIdOrKey"])), []
    if "labels" in fields:
        cmds.append(f"orca jira label set {key} " + " ".join(f"--label {q(scalar(x))}" for x in as_list(fields["labels"])))
    if "assignee" in fields:
        who = fields["assignee"]
        if not isinstance(who, dict) or set(who) != {"accountId"}:
            raise Pass("pass-unsupported-key")
        cmds.append(f"orca jira assignee set {key} --to-id {q(scalar(who['accountId']))}")
    return cmds


JIRA = {
    "getjiraissue": jira_get, "searchjiraissuesusingjql": jira_search, "gettransitionsforjiraissue": jira_transitions,
    "transitionjiraissue": jira_transition, "addcommenttojiraissue": jira_comment, "createjiraissue": jira_create,
    "editjiraissue": jira_edit,
}
HANDLERS = {"linear": LINEAR, "jira": JIRA}


# ---- state: probes and cache, each failure contained

def state_file(name):
    return config.state() / name


def read_cache():
    try:
        data = json.loads(state_file("tracker-gate-cache.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_cache(data):
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=config.state(), prefix=".tracker-gate-cache.")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, state_file("tracker-gate-cache.json"))
    except Exception:
        pass


def probe(provider):
    """What Orca has connected for the tracker: {"keys": [...]} for Linear, {"sites": [...]} for Jira; None if unknown."""
    cmd = ["orca", "linear", "team", "list", "--json"] if provider == "linear" else ["orca", "jira", "connection", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_SECONDS)
        result = json.loads(r.stdout)["result"]
    except Exception:
        return None
    if provider == "linear":
        return {"keys": sorted({str(t.get("key", "")).upper() for t in result.get("teams") or [] if isinstance(t, dict)} - {""})}
    return {"sites": [s for s in result.get("sites") or [] if isinstance(s, dict)]}


def connection(provider):
    now = time.time()
    cache = read_cache()
    row = cache.get(provider)
    if isinstance(row, dict) and now - row.get("at", 0) < (CACHE_TTL if row.get("data") else UNKNOWN_TTL):
        return row.get("data")
    data = probe(provider)
    nonempty = data is not None and (data.get("keys") or data.get("sites"))
    cache[provider] = {"at": now, "data": data if nonempty else None, "known": data is not None}
    write_cache(cache)
    return data if nonempty else (None if data is None else {})


def check_connection(provider, args, keys):
    conn = connection(provider)
    if conn is None:
        raise Pass("pass-unknown-probe")
    if not conn:
        raise Pass("pass-no-connection")
    if provider == "linear":
        if any(k.split("-")[0].upper() not in conn["keys"] for k in keys):
            raise Pass("pass-operand")
        return ""
    cloud = args.get("cloudId")
    if cloud is None:
        return ""
    for site in conn["sites"]:
        if cloud in [v for v in site.values() if isinstance(v, str)] and isinstance(site.get("id"), str):
            return f" --site {q(site['id'])}"
    raise Pass("pass-operand")


# ---- evidence: has this session already run Orca for the ticket

def orca_attempted(event, provider, keys, subs):
    path = event.get("transcript_path")
    if not path:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    lines = raw.splitlines()[1:] if size > TAIL_BYTES else raw.splitlines()
    uses, done = {}, set()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                uses[block.get("id")] = (entry.get("timestamp"), (block.get("input") or {}).get("command"))
            elif block.get("type") == "tool_result":
                done.add(block.get("tool_use_id"))
    wanted = {k.lower() for k in keys}
    names = [re.compile(rf"(?:^|/){re.escape(k)}(?:[/?#]|$)") for k in wanted]
    for tid in done & set(uses):
        stamp, command = uses[tid]
        if not isinstance(command, str) or not recent(stamp):
            continue
        for c in shell.commands(command):
            w = c.words
            if len(w) > 2 and w[0] == "orca" and w[1] == provider:
                if names and not any(rx.search(word.lower()) for rx in names for word in w[2:]):
                    continue
                if not wanted and w[2] not in subs:
                    continue
                return True
    return False


def recent(stamp):
    try:
        t = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return 0 <= time.time() - t <= EVIDENCE_WINDOW


# ---- decision

def log(provider, tool, decision, code):
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "provider": provider, "tool": tool, "decision": decision, "code": code}
        with open(state_file("tracker-gate.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def decide(event):
    """(provider, tool, commands, site flag) for a call to deny, else raises Pass(code)."""
    name = event.get("tool_name") or ""
    provider = provider_of(name)
    args = event.get("tool_input")
    if not provider or not name.startswith("mcp__") or not isinstance(args, dict):
        raise Pass("")
    tool = tool_of(name)
    handler = HANDLERS[provider].get(tool)
    if not handler:
        raise Pass("pass-unmapped")
    cmds = handler(args)
    keys = operands(args)
    site = check_connection(provider, args, keys)
    subs = {c.split()[2] for c in cmds}
    if orca_attempted(event, provider, keys, subs):
        raise Pass("pass-evidence")
    return provider, tool, [c + site for c in cmds]


def message(commands, level):
    verb = "Run" if level != "warn" else "Prefer"
    return ("tracker_gate: Orca runs this call as written. " + verb + ":\n  " + "\n  ".join(commands) +
            "\n(`<file>` is a file holding the text; add --json only to parse. If Orca cannot do what you need, run it "
            "once for this ticket first: after a real `orca` call on the same ticket, the MCP call passes. "
            f"{KEY} in ~/.claude/agent-skills.json: deny, ask, warn or off.)")


def main():
    event = json.load(sys.stdin)
    cfg = config.load()
    level = config.level(cfg, KEY, "deny")
    if os.environ.get("AGENT_SKILLS_TRACKER_GATE") == "0":
        level = "off"
    name = event.get("tool_name") or ""
    provider = provider_of(name)
    if not name.startswith("mcp__") or not provider:
        return
    if level == "off":
        return log(provider, tool_of(name), "pass", "pass-off")
    try:
        provider, tool, commands = decide(event)
    except Pass as p:
        if str(p) and str(p) != "pass-unmapped":
            log(provider, tool_of(name), "pass", str(p))
        return
    log(provider, tool, level, "deny")
    text = message(commands, level)
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if level in ("deny", "ask"):
        out["hookSpecificOutput"].update(permissionDecision=level, permissionDecisionReason=f"[gate:tracker] {text}")
    else:
        out["hookSpecificOutput"]["additionalContext"] = text
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        config.log_error("tracker_gate", e)
        log("", "", "pass", "pass-error")
    sys.exit(0)
