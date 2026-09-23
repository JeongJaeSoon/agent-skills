#!/usr/bin/env python3
"""Ticket-tracker adapter: one CLI and one issue schema over Linear (via `orca linear`) and Jira.

    tracker.py [--adapter linear|jira] [--config PATH] list --project P [--since ISO8601] [--limit N]
    tracker.py [...] get ID
    tracker.py [...] create --project P --title T --body-file F [--label L ...] [--parent ID] [--related ID]
    tracker.py [...] label ID --add L [--add L2]
    tracker.py [...] comment ID --body-file F
    tracker.py [...] transition ID --to started|completed|canceled

stdout is always JSON; errors go to stderr with a non-zero exit.
TRACKER_FIXTURES=<dir> serves list/get from <dir>/issues.json and refuses writes.
"""

import argparse
import base64
import copy
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_CONFIG = {
    "tracker": {
        "adapter": "linear",
        "linear": {"workspace": None},
        "jira": {"base_url": None, "email_env": "JIRA_EMAIL", "token_env": "JIRA_API_TOKEN",
                 "issue_type": "Task"},
    },
}
DEFAULT_CONFIG_PATH = "~/.claude/agent-skills.json"
TRANSITION_TARGETS = ("started", "completed", "canceled")


class TrackerError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def iso_z(value):
    """Normalize any ISO-8601 timestamp (Linear or Jira style) to UTC 'YYYY-MM-DDTHH:MM:SSZ'."""
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Jira emits +0900; fromisoformat before 3.11 needs +09:00.
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    # Drop fractional seconds of any width.
    s = re.sub(r"\.\d+", "", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(path=None):
    """Explicit --config must exist; the default path is optional."""
    explicit = path is not None
    path = os.path.expanduser(path or DEFAULT_CONFIG_PATH)
    if not os.path.exists(path):
        if explicit:
            raise TrackerError(f"config file not found: {path}")
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise TrackerError(f"cannot read config {path}: {exc}")
    if not isinstance(data, dict):
        raise TrackerError(f"config {path} must be a JSON object")
    return deep_merge(DEFAULT_CONFIG, data)


def read_body(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise TrackerError(f"cannot read body file {path}: {exc}")


def issue_template():
    return {"id": None, "title": None, "url": None, "state": None, "state_type": None,
            "created_at": None, "updated_at": None, "completed_at": None, "canceled_at": None,
            "labels": [], "parent": None, "description": "", "assignee": None, "priority": 0}


def finish_list(issues, since, limit):
    """Shared list contract: created_at ascending; --limit keeps the N most recently created."""
    if since:
        cut = iso_z(since)
        issues = [i for i in issues if (i.get("created_at") or "") >= cut]
    issues = sorted(issues, key=lambda i: i.get("created_at") or "")
    return issues[-limit:] if limit else issues


# ---------------------------------------------------------------- fixtures

class FixtureAdapter:
    def __init__(self, directory):
        self.path = os.path.join(directory, "issues.json")

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise TrackerError(f"TRACKER_FIXTURES: cannot read {self.path}: {exc}")
        if not isinstance(data, list):
            raise TrackerError(f"TRACKER_FIXTURES: {self.path} must hold a JSON list of issues")
        return data

    def list(self, project, since=None, limit=None):
        return finish_list(self._load(), since, limit)

    def get(self, issue_id):
        for issue in self._load():
            if issue.get("id") == issue_id:
                return issue
        raise TrackerError(f"TRACKER_FIXTURES: {issue_id} not in {self.path}")

    def _refuse(self, *_a, **_k):
        raise TrackerError("TRACKER_FIXTURES is set: write operations are disabled")

    create = label = comment = transition = _refuse


# ---------------------------------------------------------------- linear (orca CLI)

# Orca's CLI parser only accepts identifiers matching this; others (e.g. team key "9X") are rejected.
ORCA_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")
LINEAR_STATE_TYPE = {"duplicate": "canceled"}


def normalize_linear(raw):
    out = issue_template()
    state = raw.get("state") or {}
    stype = (state.get("type") or "").lower()
    stype = LINEAR_STATE_TYPE.get(stype, stype) or None
    updated = iso_z(raw.get("updatedAt"))
    # Orca's issue JSON has no completedAt/canceledAt; updatedAt is the closest proxy for a closed
    # issue. Use the real field whenever Orca starts emitting it.
    completed = iso_z(raw.get("completedAt")) or (updated if stype == "completed" else None)
    canceled = iso_z(raw.get("canceledAt")) or (updated if stype == "canceled" else None)
    parent = raw.get("parent")
    assignee = raw.get("assignee") or {}
    labels = raw.get("labels") or []
    if isinstance(labels, dict):
        labels = labels.get("nodes") or []
    out.update({
        "id": raw.get("identifier") or raw.get("id"),
        "title": raw.get("title"),
        "url": raw.get("url"),
        "state": state.get("name"),
        "state_type": stype,
        "created_at": iso_z(raw.get("createdAt")),
        "updated_at": updated,
        "completed_at": completed,
        "canceled_at": canceled,
        "labels": [l.get("name") for l in labels if isinstance(l, dict) and l.get("name")],
        "parent": (parent.get("identifier") if isinstance(parent, dict) else parent) or None,
        "description": raw.get("description") or "",
        "assignee": assignee.get("displayName") or assignee.get("name") or None,
        "priority": raw.get("priority") or 0,
    })
    return out


class LinearAdapter:
    def __init__(self, cfg):
        self.workspace = (cfg or {}).get("workspace")

    def _orca(self, *args):
        cmd = ["orca", "linear", *args, "--json"]
        if self.workspace:
            cmd += ["--workspace", self.workspace]
        label = " ".join(args[:2])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except FileNotFoundError:
            raise TrackerError("`orca` not found on PATH; the linear adapter needs the Orca CLI")
        except subprocess.TimeoutExpired:
            raise TrackerError(f"orca linear {label}: timed out")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            msg = (proc.stderr or proc.stdout or "").strip()[:500]
            raise TrackerError(f"orca linear {label}: exit {proc.returncode}, non-JSON output: {msg}")
        if not data.get("ok"):
            err = data.get("error") or {}
            code = err.get("code", "error")
            raise TrackerError(f"orca linear {label}: {code}: {err.get('message', '')}".rstrip(": "))
        return data.get("result") or {}

    @staticmethod
    def _check_id(issue_id, op):
        if not ORCA_IDENTIFIER.match(issue_id):
            raise TrackerError(
                f"{op} {issue_id}: Orca's Linear CLI rejects identifiers whose team key does not "
                "start with a letter, so it cannot write to this issue. From an agent session use the "
                "Linear MCP tools instead (see use-tracker references/linear.md).")

    def list(self, project, since=None, limit=None):
        base = ["list-issues", "--project", project, "--include-archived", "--order-by", "createdAt"]
        if since:
            base += ["--created-at", iso_z(since)]
        issues, cursor = [], None
        while True:
            args = list(base)
            if limit:
                args += ["--limit", str(limit - len(issues))]
            # A cursor does not carry the filters, so every page repeats them.
            if cursor:
                args += ["--cursor", cursor]
            result = self._orca(*args)
            meta = result.get("meta") or {}
            if meta.get("partial"):
                print(f"tracker: warning: partial Linear result: {meta.get('workspaceErrors')}",
                      file=sys.stderr)
            issues += [normalize_linear(i) for i in result.get("issues") or []]
            cursor = meta.get("nextCursor")
            if not (meta.get("hasMore") and cursor) or (limit and len(issues) >= limit):
                break
        # Orca orders createdAt newest first, so a capped read already holds the newest N.
        return finish_list(issues, since, limit)

    def get(self, issue_id):
        if ORCA_IDENTIFIER.match(issue_id):
            try:
                result = self._orca("issue", issue_id)
                return normalize_linear(result.get("issue") or result)
            except TrackerError as exc:
                if "linear_issue_required" not in str(exc):
                    raise
        return self._get_via_search(issue_id)

    def _get_via_search(self, issue_id):
        # `orca linear issue` cannot parse this identifier; search finds it, then list-issues
        # (which carries description, labels and createdAt) re-reads it in full.
        want = issue_id.upper()
        hits = self._orca("search", issue_id, "--limit", "20").get("issues") or []
        hit = next((h for h in hits if (h.get("identifier") or "").upper() == want), None)
        if not hit:
            raise TrackerError(f"get {issue_id}: issue not found")
        team = (hit.get("team") or {}).get("key") or (hit.get("team") or {}).get("id")
        if team and hit.get("updatedAt"):
            result = self._orca("list-issues", "--team", team, "--include-archived",
                                "--updated-at", hit["updatedAt"], "--limit", "250")
            for raw in result.get("issues") or []:
                if (raw.get("identifier") or "").upper() == want:
                    return normalize_linear(raw)
        return normalize_linear(hit)

    def _team_for_project(self, project):
        result = self._orca("project", "list", "--query", project, "--limit", "20")
        for proj in result.get("projects") or []:
            if project in (proj.get("id"), proj.get("name")):
                teams = proj.get("teams") or []
                if len(teams) == 1:
                    return teams[0].get("key") or teams[0].get("id")
                return None
        raise TrackerError(f"create: Linear project not found: {project}")

    def create(self, project, title, body_file, labels=(), parent=None, related=None):
        args = ["create", "--title", title, "--body-file", body_file, "--project", project]
        team = self._team_for_project(project)
        if team:
            args += ["--team", team]
        for label in labels:
            args += ["--label", label]
        if parent:
            args += ["--parent", parent]
        result = self._orca(*args)
        created = normalize_linear(result.get("issue") or result)
        new_id = created["id"]
        if not new_id:
            raise TrackerError("create: orca returned no issue identifier")
        if related:
            try:
                self._check_id(new_id, "relate")
                self._orca("relation", "add", new_id, "--related", related, "--type", "related")
            except TrackerError as exc:
                print(json.dumps(created, ensure_ascii=False))
                raise TrackerError(f"created {new_id} but could not relate it to {related}: {exc}")
        try:
            return self.get(new_id)
        except TrackerError:
            return created

    def label(self, issue_id, labels):
        self._check_id(issue_id, "label")
        args = ["label", "add", issue_id]
        for label in labels:
            args += ["--label", label]
        self._orca(*args)
        return {"ok": True, "op": "label", "id": issue_id, "added": list(labels)}

    def comment(self, issue_id, body_file):
        self._check_id(issue_id, "comment")
        self._orca("comment", "add", issue_id, "--body-file", body_file)
        return {"ok": True, "op": "comment", "id": issue_id}

    def transition(self, issue_id, target):
        self._check_id(issue_id, "transition")
        team = issue_id.rsplit("-", 1)[0]
        states = self._orca("team", "states", "--team", team).get("states") or []
        matches = [s for s in states if (s.get("type") or "").lower() == target]
        if not matches:
            names = ", ".join(f"{s.get('name')} ({s.get('type')})" for s in states)
            raise TrackerError(f"transition {issue_id}: team {team} has no '{target}' state; states: {names}")
        state = min(matches, key=lambda s: s.get("position") or 0)
        self._orca("status", "set", issue_id, "--to", state["name"])
        return {"ok": True, "op": "transition", "id": issue_id, "state": state["name"], "state_type": target}


# ---------------------------------------------------------------- jira (REST v3)

JIRA_FIELDS = ["summary", "status", "created", "updated", "resolutiondate", "resolution",
               "labels", "parent", "description", "assignee", "priority"]
JIRA_CANCEL_RESOLUTIONS = {"won't do", "wont do", "cancelled", "canceled", "duplicate"}
JIRA_CANCEL_NAME = re.compile(r"cancel|won'?t|reject|declin|duplicate", re.I)
JIRA_CATEGORY = {"new": "unstarted", "indeterminate": "started", "done": "completed"}
JIRA_PRIORITY = {"highest": 1, "high": 2, "medium": 3, "low": 4, "lowest": 4}
TARGET_CATEGORY = {"started": "indeterminate", "completed": "done", "canceled": "done"}


def adf_to_text(node):
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content"))
    if kind in ("paragraph", "heading", "listItem", "codeBlock", "blockquote", "rule"):
        return inner.rstrip("\n") + "\n"
    return inner


def text_to_adf(text):
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        content = []
        for n, line in enumerate(block.split("\n")):
            if n:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs or [{"type": "paragraph", "content": []}]}


def jira_state_type(fields):
    status = fields.get("status") or {}
    category = ((status.get("statusCategory") or {}).get("key") or "").lower()
    resolution = ((fields.get("resolution") or {}).get("name") or "").lower()
    if category == "done" and resolution in JIRA_CANCEL_RESOLUTIONS:
        return "canceled"
    if category == "new" and (status.get("name") or "").strip().lower() == "backlog":
        return "backlog"
    return JIRA_CATEGORY.get(category, "unstarted")


def normalize_jira(raw, base_url):
    fields = raw.get("fields") or {}
    out = issue_template()
    stype = jira_state_type(fields)
    resolved = iso_z(fields.get("resolutiondate"))
    parent = fields.get("parent") or {}
    assignee = fields.get("assignee") or {}
    priority = ((fields.get("priority") or {}).get("name") or "").lower()
    out.update({
        "id": raw.get("key"),
        "title": fields.get("summary"),
        "url": f"{base_url}/browse/{raw.get('key')}",
        "state": (fields.get("status") or {}).get("name"),
        "state_type": stype,
        "created_at": iso_z(fields.get("created")),
        "updated_at": iso_z(fields.get("updated")),
        "completed_at": resolved if stype == "completed" else None,
        "canceled_at": resolved if stype == "canceled" else None,
        "labels": list(fields.get("labels") or []),
        "parent": parent.get("key") or None,
        "description": adf_to_text(fields.get("description")).strip(),
        "assignee": assignee.get("displayName") or None,
        "priority": JIRA_PRIORITY.get(priority, 0),
    })
    return out


class JiraAdapter:
    def __init__(self, cfg):
        cfg = cfg or {}
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise TrackerError("jira adapter: tracker.jira.base_url is not set in the config")
        email_env = cfg.get("email_env") or "JIRA_EMAIL"
        token_env = cfg.get("token_env") or "JIRA_API_TOKEN"
        email, token = os.environ.get(email_env), os.environ.get(token_env)
        missing = [name for name, val in ((email_env, email), (token_env, token)) if not val]
        if missing:
            raise TrackerError(f"jira adapter: environment variable(s) not set: {', '.join(missing)}")
        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        self.issue_type = cfg.get("issue_type") or "Task"

    def _request(self, method, path, body=None, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self._auth, "Accept": "application/json",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read() or b"{}")
                msgs = list(payload.get("errorMessages") or [])
                msgs += [f"{k}: {v}" for k, v in (payload.get("errors") or {}).items()]
                detail = "; ".join(msgs)
            except (ValueError, AttributeError):
                pass
            err = TrackerError(f"jira {method} {path}: HTTP {exc.code} {detail}".rstrip())
            err.status = exc.code
            raise err
        except urllib.error.URLError as exc:
            raise TrackerError(f"jira {method} {path}: {exc.reason}")
        return json.loads(raw) if raw else {}

    def _jql(self, project, since, newest_first=False):
        jql = f'project = "{project}"'
        if since:
            # JQL takes minutes in the Jira user's timezone; UTC is close enough for a lower bound.
            jql += f' AND created >= "{iso_z(since)[:16].replace("T", " ")}"'
        return jql + (" ORDER BY created DESC" if newest_first else " ORDER BY created ASC")

    def list(self, project, since=None, limit=None):
        jql = self._jql(project, since, newest_first=bool(limit))
        try:
            raws = self._search_jql(jql, limit)
        except TrackerError as exc:
            if getattr(exc, "status", None) not in (404, 405, 410):
                raise
            raws = self._search_legacy(jql, limit)
        return finish_list([normalize_jira(r, self.base_url) for r in raws], since, limit)

    def _search_jql(self, jql, limit):
        out, token = [], None
        while True:
            body = {"jql": jql, "fields": JIRA_FIELDS, "maxResults": min(100, limit - len(out)) if limit else 100}
            if token:
                body["nextPageToken"] = token
            page = self._request("POST", "/rest/api/3/search/jql", body)
            out += page.get("issues") or []
            token = page.get("nextPageToken")
            if not token or page.get("isLast") or (limit and len(out) >= limit):
                return out

    def _search_legacy(self, jql, limit):
        out = []
        while True:
            body = {"jql": jql, "fields": JIRA_FIELDS, "startAt": len(out),
                    "maxResults": min(100, limit - len(out)) if limit else 100}
            page = self._request("POST", "/rest/api/3/search", body)
            batch = page.get("issues") or []
            out += batch
            if not batch or len(out) >= page.get("total", 0) or (limit and len(out) >= limit):
                return out

    def get(self, issue_id):
        raw = self._request("GET", f"/rest/api/3/issue/{urllib.parse.quote(issue_id)}",
                            params={"fields": ",".join(JIRA_FIELDS)})
        return normalize_jira(raw, self.base_url)

    def create(self, project, title, body_file, labels=(), parent=None, related=None):
        fields = {"project": {"key": project}, "summary": title,
                  "description": text_to_adf(read_body(body_file)),
                  "issuetype": {"name": self.issue_type}}
        if labels:
            fields["labels"] = list(labels)
        if parent:
            fields["parent"] = {"key": parent}
        key = self._request("POST", "/rest/api/3/issue", {"fields": fields}).get("key")
        if not key:
            raise TrackerError("create: Jira returned no issue key")
        if related:
            try:
                self._request("POST", "/rest/api/3/issueLink", {
                    "type": {"name": "Relates"}, "inwardIssue": {"key": key},
                    "outwardIssue": {"key": related}})
            except TrackerError as exc:
                print(json.dumps({"id": key}), flush=True)
                raise TrackerError(f"created {key} but could not relate it to {related}: {exc}")
        return self.get(key)

    def label(self, issue_id, labels):
        self._request("PUT", f"/rest/api/3/issue/{urllib.parse.quote(issue_id)}",
                      {"update": {"labels": [{"add": l} for l in labels]}})
        return {"ok": True, "op": "label", "id": issue_id, "added": list(labels)}

    def comment(self, issue_id, body_file):
        self._request("POST", f"/rest/api/3/issue/{urllib.parse.quote(issue_id)}/comment",
                      {"body": text_to_adf(read_body(body_file))})
        return {"ok": True, "op": "comment", "id": issue_id}

    def transition(self, issue_id, target):
        path = f"/rest/api/3/issue/{urllib.parse.quote(issue_id)}/transitions"
        transitions = self._request("GET", path).get("transitions") or []
        category = TARGET_CATEGORY[target]

        def matches(t):
            to = t.get("to") or {}
            if ((to.get("statusCategory") or {}).get("key") or "").lower() != category:
                return False
            if category != "done":
                return True
            is_cancel = bool(JIRA_CANCEL_NAME.search(f"{t.get('name', '')} {to.get('name', '')}"))
            return is_cancel == (target == "canceled")

        pick = next((t for t in transitions if matches(t)), None)
        if not pick:
            avail = ", ".join(f"{t.get('name')} -> {(t.get('to') or {}).get('name')}" for t in transitions)
            raise TrackerError(f"transition {issue_id}: no transition leads to '{target}'; available: {avail or 'none'}")
        self._request("POST", path, {"transition": {"id": pick["id"]}})
        return {"ok": True, "op": "transition", "id": issue_id,
                "state": (pick.get("to") or {}).get("name"), "state_type": target}


# ---------------------------------------------------------------- CLI

def make_adapter(name, config):
    fixtures = os.environ.get("TRACKER_FIXTURES")
    if fixtures:
        return FixtureAdapter(fixtures)
    tracker_cfg = config.get("tracker") or {}
    if name == "linear":
        return LinearAdapter(tracker_cfg.get("linear"))
    if name == "jira":
        return JiraAdapter(tracker_cfg.get("jira"))
    raise TrackerError(f"unknown tracker adapter: {name!r} (expected linear or jira)")


def build_parser():
    p = argparse.ArgumentParser(prog="tracker.py", description="Ticket-tracker adapter (Linear, Jira).")
    p.add_argument("--adapter", choices=["linear", "jira"], help="override tracker.adapter from the config")
    p.add_argument("--config", help=f"config path (default {DEFAULT_CONFIG_PATH})")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--project", required=True)
    s.add_argument("--since")
    s.add_argument("--limit", type=int)
    s = sub.add_parser("get")
    s.add_argument("id")
    s = sub.add_parser("create")
    s.add_argument("--project", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--body-file", required=True)
    s.add_argument("--label", action="append", default=[])
    s.add_argument("--parent")
    s.add_argument("--related")
    s = sub.add_parser("label")
    s.add_argument("id")
    s.add_argument("--add", action="append", required=True)
    s = sub.add_parser("comment")
    s.add_argument("id")
    s.add_argument("--body-file", required=True)
    s = sub.add_parser("transition")
    s.add_argument("id")
    s.add_argument("--to", required=True, choices=TRANSITION_TARGETS)
    return p


def run(argv):
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    adapter = make_adapter(args.adapter or (config.get("tracker") or {}).get("adapter") or "linear", config)
    if args.cmd == "list":
        if args.limit is not None and args.limit < 1:
            raise TrackerError("--limit must be >= 1")
        return adapter.list(args.project, args.since, args.limit)
    if args.cmd == "get":
        return adapter.get(args.id)
    if args.cmd == "create":
        read_body(args.body_file)
        return adapter.create(args.project, args.title, args.body_file, args.label, args.parent, args.related)
    if args.cmd == "label":
        return adapter.label(args.id, args.add)
    if args.cmd == "comment":
        read_body(args.body_file)
        return adapter.comment(args.id, args.body_file)
    return adapter.transition(args.id, args.to)


def main(argv=None):
    try:
        result = run(sys.argv[1:] if argv is None else argv)
    except TrackerError as exc:
        print(f"tracker: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
