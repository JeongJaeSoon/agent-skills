#!/usr/bin/env python3
"""Stop hook of the agent-skills plugin: block a main-session reply that omits the claude.ai or backup link of an
Artifact published in this turn (artifact_archive.py queues the links in <state>/artifact-backup/links/<session>.json),
and a reply that names a backed-up Artifact without its backup link even when this session did not publish it: a
page published in another session, or one an earlier message handed over, loses its backup link otherwise.

A subagent is never blocked; it hands its links back in its report, and the session that started it is checked.
Delivering tools: artifact_send_check.py applies the stateless half to them before the message is sent.
"""
import contextlib, datetime, fcntl, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import artifact_links as al, config

MAX_AGE = datetime.timedelta(hours=6)
# Tools that deliver text to a person; search/read/list tools never count.
DELIVERY_TOOL_RES = [re.compile(p, re.I) for p in (
    r'^mcp__.*telegram.*__(reply|send_message)$',
    r'^mcp__.*slack.*__(slack_)?(send_message|post_message|reply_to_thread|chat_post_message)$',
)]


def link_state():
    return os.path.join(al.state_dir(), 'links')


def state_path(session_id):
    return os.path.join(link_state(), re.sub(r'[^A-Za-z0-9_-]', '_', session_id) + '.json')


@contextlib.contextmanager
def locked(path):
    os.makedirs(link_state(), mode=0o700, exist_ok=True)
    fd = os.open(path + '.lock', os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save(path, pending):
    if not pending:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'pending': pending}, f, ensure_ascii=False)
    os.replace(tmp, path)


def private_keys():
    keys = set()
    for row in al.read_rows(os.path.join(al.private_dir(), 'index.jsonl')):
        keys.add(row.get('key'))
    return keys


# Owner `!` shell lines land as user entries but start no model turn.
SHELL_ECHO_RE = re.compile(r'^<(bash|local-command)-(input|stdout|stderr)>')


def is_turn_start(entry):
    if entry.get('type') != 'user' or entry.get('isMeta'):
        return False
    content = (entry.get('message') or {}).get('content')
    if isinstance(content, str):
        return not content.startswith('Stop hook feedback') and not SHELL_ECHO_RE.match(content)
    return any(isinstance(b, dict) and b.get('type') != 'tool_result'
               and not (b.get('text') or '').startswith('Stop hook feedback')
               and not SHELL_ECHO_RE.match(b.get('text') or '') for b in content or [])


def epoch(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


def read_transcript(transcript_path):
    """(final text of the current turn, current turn start epoch, [(epoch, text)] of earlier final
    replies and of every delivering tool call whose result was not an error)."""
    try:
        with open(transcript_path, encoding='utf-8') as f:
            entries = [json.loads(line) for line in f if line.strip()]
    except (OSError, TypeError, ValueError):
        return '', None, []
    ok = {}
    for e in entries:
        content = (e.get('message') or {}).get('content') if e.get('type') == 'user' else None
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                ok[b.get('tool_use_id')] = not b.get('is_error')
    finals, delivered, turn_start, final = [], [], None, None
    for e in entries:
        if is_turn_start(e):
            if final:
                finals.append(final)
            final, turn_start = None, epoch(e.get('timestamp'))
            continue
        if e.get('type') != 'assistant':
            continue
        ts = epoch(e.get('timestamp'))
        for b in (e.get('message') or {}).get('content') or []:
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'text':
                final = (ts, b.get('text') or '')
            elif (b.get('type') == 'tool_use' and ok.get(b.get('id'))
                  and any(r.search(b.get('name') or '') for r in DELIVERY_TOOL_RES)):
                delivered.append((ts, json.dumps(b.get('input'), ensure_ascii=False)))
    return (final[1] if final else ''), turn_start, finals + delivered


def missing_links(entry, text):
    out = []
    if entry.get('url') and entry['url'] not in text:
        out.append(entry['url'])
    # Older entries hold a folder URL; any file inside it counts for those.
    if entry.get('backup') and entry['backup'].rstrip('/') not in text:
        out.append(entry['backup'])
    return out


def published_at(entry):
    if entry.get('epoch') is not None:
        return entry['epoch']
    try:
        return datetime.datetime.fromisoformat(entry['time']).timestamp()  # local time, as written
    except (KeyError, TypeError, ValueError):
        return None


def current(entry, now, turn_start):
    at = published_at(entry)
    if turn_start is not None and at is not None and at < turn_start:
        return False  # published in an earlier (aborted or already answered) turn
    return at is None or now - at < MAX_AGE.total_seconds()


def check(data, path):
    try:
        with open(path, encoding='utf-8') as f:
            pending = json.load(f).get('pending') or []
    except (OSError, ValueError):
        return None
    final, turn_start, said = read_transcript(data.get('transcript_path'))
    now = datetime.datetime.now().timestamp()
    pending = [p for p in pending if current(p, now, turn_start)]
    hidden = private_keys()
    for p in pending:
        if p.get('key') in hidden:
            p['backup'] = None  # its latest copy went private, maybe from another session
    base = data.get('last_assistant_message') or final

    def text_for(p):
        at = published_at(p)
        return base + '\n' + '\n'.join(t for ts, t in said if at is None or ts is None or ts >= at)

    owed = [(p, m) for p in pending for m in [missing_links(p, text_for(p))] if m]
    if not owed:
        save(path, [])
        return None
    links = [link for _, m in owed for link in m]
    privates = [p['url'] for p, _ in owed if p.get('url') and not p.get('backup')]
    if data.get('stop_hook_active'):
        # Already blocked once this turn: let it stop rather than loop, and drop the debt.
        save(path, [])
        return {'systemMessage': 'Artifact links were not given to the owner: ' + ', '.join(links)}
    save(path, [p for p, _ in owed])
    reason = ('Your final reply must give the owner these Artifact links verbatim, which it omits: '
              + ', '.join(links) + '.')
    if privates:
        reason += (' These pages have no public backup (kept private, or no backup URL is configured); say so '
                   'instead of giving a backup link: ' + ', '.join(privates) + '.')
    return {'decision': 'block', 'reason': reason}


def stateless(data):
    """The reply names a backed-up artifact and lacks its newest backup link, whoever published it."""
    text = data.get('last_assistant_message')
    if text is None:
        text = read_transcript(data.get('transcript_path'))[0]
    mode = al.level()
    links = al.owed_links(text) if mode != 'off' else []
    if not links:
        return None
    if mode != 'deny' or data.get('stop_hook_active'):
        return {'systemMessage': 'Artifact backup links were not given to the owner: ' + ', '.join(links)}
    return {'decision': 'block', 'reason': al.reason(links)}


def main():
    data = json.load(sys.stdin)
    # Subagents hand links back in their report; only the main session's reply is checked.
    if data.get('hook_event_name') not in (None, 'Stop') or data.get('agent_id'):
        return None
    session_id = data.get('session_id')
    out = None
    path = state_path(session_id) if session_id else None
    if path and os.path.exists(path):
        with locked(path):
            out = check(data, path)
    return out or stateless(data)


if __name__ == '__main__':
    try:
        out = main()
    except Exception as e:  # a broken check must never trap the session
        config.log_error('artifact_links_stop', e)
        out = {'systemMessage': f'artifact_links_stop.py failed: {type(e).__name__}'}
    if out:
        print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)
