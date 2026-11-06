"""Test-stack status for the dashboard: is each configured stack ready, which commit does it run, and how far is
that behind main. Without `dashboard.stacks` in agent-skills.json nothing is read and nothing is shown; no stack,
port or environment name is assumed.

    "dashboard": {"stacks": [{"name": "staging", "url": "https://stack.example.test", "repo": "acme/app"}]}

Each entry may add "sha_field": the key in the /readyz JSON that holds the deployed commit (else sha, git_sha,
commit or revision is tried). collect() never raises: a stack that cannot be read is a row with `error`, and the
stacks are read together under one deadline. Needs curl and gh on PATH; fleet.py imports this without a cycle.
"""
import concurrent.futures, json, os, pathlib, re, subprocess, threading, urllib.parse

SHA_KEYS = ("sha", "git_sha", "commit", "revision")
SHA_RE = re.compile(r"[0-9a-f]{7,40}")
OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
FIELD_RE = re.compile(r"\w{1,40}", re.ASCII)
HTTP_TIMEOUT_S = 5
GH_TIMEOUT_S = 10
DEADLINE_S = 20  # for all stacks together, which are read at once; one stack takes at most 8 + 10 s
MAX_STACKS = 8
MAX_BODY = 65536
_lock, _inflight = threading.Lock(), {}  # (name, url) -> the future of a read that has not ended


def clean_url(url):
    """The base URL without a trailing slash, or None unless it is a plain http(s) host with an optional port and
    path: no credentials, query, fragment or control characters, which would land in state.json or move /readyz."""
    if not isinstance(url, str) or not url.isprintable() or " " in url:
        return None
    try:
        u = urllib.parse.urlsplit(url)
        u.port  # raises ValueError for a bad port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment or "?" in url or "#" in url:
        return None
    return url.rstrip("/")


def clean_repo(repo):
    if not isinstance(repo, str) or repo.count("/") != 1:
        return None
    owner, name = repo.split("/")
    ok = OWNER_RE.fullmatch(owner) and REPO_RE.fullmatch(name) and name not in (".", "..")
    return repo if ok else None


def configured():
    """The valid `dashboard.stacks` entries of agent-skills.json; [] when the key is missing or malformed."""
    path = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()
    try:
        raw = (json.loads(path.read_text()).get("dashboard") or {}).get("stacks")
    except (OSError, ValueError, AttributeError):
        return []
    return normalized(raw if isinstance(raw, list) else [])


def normalized(entries):
    """The valid entries, cleaned, at most MAX_STACKS; configured() and collect() both go through here."""
    out, seen = [], set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        name, url, repo, field = e.get("name"), clean_url(e.get("url")), clean_repo(e.get("repo")), e.get("sha_field")
        if isinstance(name, str) and name.strip() and url and repo and (name.strip()[:60], url) not in seen:
            seen.add((name.strip()[:60], url))  # (name, url) keys a read in collect(); the first of a pair wins
            out.append({"name": name.strip()[:60], "url": url, "repo": repo,
                        "sha_field": field if isinstance(field, str) and FIELD_RE.fullmatch(field) else None})
    return out[:MAX_STACKS]


CURL_EXIT = {6: "host not found", 7: "connection refused", 28: "timed out"}  # curl's own phrases are not kept


def http_get(url):
    """(status, body) of a GET; a 3xx/4xx/5xx is an answer, not an error. Raises OSError when there is none.
    curl, not urllib: --max-time is one clock for DNS, connect, TLS and the whole body, and a curl that outlives
    it is killed, so a slow or dripping server can never keep a thread. A redirect is an answer (not ready) and is
    not followed: the stack named in the config is the one read."""
    # -q first: a user's .curlrc (location, insecure) must not change the policy; --globoff: {a,b} and [1-9] in a URL
    # are literal, not several requests.
    cmd = ["curl", "-q", "-sS", "--globoff", "--max-time", str(HTTP_TIMEOUT_S), "--proto", "=http,https",
           "-H", "Accept: application/json", "-w", "\n%{http_code}", "--url", url]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise OSError("curl not found")
    late = threading.Timer(HTTP_TIMEOUT_S + 3, p.kill)  # --max-time ends curl; this is for a curl that does not
    late.start()
    try:
        out = p.stdout.read(MAX_BODY + 16)  # at most this much is ever held, whatever the server sends
    finally:
        late.cancel()
        p.kill()
        p.stdout.close()
        rc = p.wait()
    if len(out) > MAX_BODY:
        raise OSError("invalid response")
    if rc and rc != -9:
        raise OSError(CURL_EXIT.get(rc, "unreachable"))
    body, _, code = out.decode("utf-8", "replace").rpartition("\n")
    if rc == -9 or not code.isdigit() or int(code) == 0:  # -9: killed by us after the deadline, nothing complete came
        raise OSError("timed out" if rc == -9 else "invalid response")
    return int(code), body


def gh_behind(repo, sha):
    """How many commits main has that sha lacks, from the GitHub compare API."""
    r = subprocess.run(["gh", "api", f"repos/{repo}/compare/main...{sha}", "--jq", ".behind_by"],
                       capture_output=True, text=True, timeout=GH_TIMEOUT_S, stdin=subprocess.DEVNULL)
    if r.returncode:
        raise OSError("github compare failed")  # gh's own text is not kept: it can quote the request
    return int(r.stdout.strip())


def deployed_sha(body, field=None):
    """The commit a /readyz JSON body names, or None. Only lower-case hex passes, as it goes into an API path."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for k in ((field,) if field else ()) + SHA_KEYS:
        v = data.get(k)
        if isinstance(v, str) and SHA_RE.fullmatch(v.lower()):
            return v.lower()
    return None


def failed(entry, now, error):
    return {"name": entry["name"], "url": entry["url"], "repo": entry["repo"], "ready": None, "status": None,
            "sha": None, "behind": None, "error": error, "checked_at": now}


def probe(entry, now, get=http_get, behind=gh_behind):
    row = failed(entry, now, None)
    try:
        row["status"], body = get(entry["url"] + "/readyz")
        row["ready"] = 200 <= row["status"] < 300
        row["sha"] = deployed_sha(body, entry.get("sha_field"))
    except Exception as e:  # one stack's surprise must not cost the others their row
        return failed(entry, now, f"readyz: {e}"[:160] if isinstance(e, OSError) else "readyz: unreadable")
    if row["sha"]:
        try:
            row["behind"] = behind(entry["repo"], row["sha"])
        except subprocess.TimeoutExpired:
            row["error"] = "behind: timed out"
        except OSError as e:
            row["error"] = f"behind: {e}"[:160]
        except Exception:
            row["error"] = "behind: unreadable"
    return row


def collect(now, entries=None, deadline=DEADLINE_S, **fakes):
    """One row per stack, in order, read at once; [] when none is configured. A stack still unread at the deadline is a
    row saying so (its thread ends on its own timeouts)."""
    entries = configured() if entries is None else normalized(entries)
    if not entries:
        return []
    with _lock:
        for k in [k for k, f in _inflight.items() if f.done()]:
            del _inflight[k]
        # A stack whose last read is still stuck is not asked again, so threads cannot pile up behind it; the others go on.
        run = [e for e in entries if (e["name"], e["url"]) not in _inflight]
        pool = concurrent.futures.ThreadPoolExecutor(max(len(run), 1))
        futures = {(e["name"], e["url"]): pool.submit(probe, e, now, **fakes) for e in run}
        _inflight.update(futures)
    concurrent.futures.wait(futures.values(), timeout=deadline)
    pool.shutdown(wait=False, cancel_futures=True)
    rows = []
    for e in entries:
        f = futures.get((e["name"], e["url"]))
        rows.append(failed(e, now, "previous read still running") if f is None
                    else f.result() if f.done() else failed(e, now, "timed out"))
    return rows
