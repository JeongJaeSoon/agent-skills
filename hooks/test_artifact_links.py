#!/usr/bin/env python3
"""Plain-python tests for the Artifact backup hooks: artifact_archive.py (PostToolUse), artifact_links_stop.py (Stop)
and artifact_send_check.py (PreToolUse). Each hook runs as a subprocess under a temporary HOME with a stub
agent-guard, so the real state, index and config stay untouched."""
import concurrent.futures, datetime, json, os, shutil, stat, subprocess, sys, tempfile, time

HOOKS = os.path.dirname(os.path.abspath(__file__))
# Env overrides let a failing-first run point at an older copy of a hook.
ARCHIVE = os.environ.get('ARCHIVE_HOOK', os.path.join(HOOKS, 'artifact_archive.py'))
STOP = os.environ.get('STOP_HOOK', os.path.join(HOOKS, 'artifact_links_stop.py'))
SEND = os.environ.get('SEND_HOOK', os.path.join(HOOKS, 'artifact_send_check.py'))
HOME = tempfile.mkdtemp(prefix='artifact-links-test-')
BIN = os.path.join(HOME, 'bin')
BASE = 'https://files.example.test/artifacts/'
ENV = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_SKILLS_')}
ENV.update(HOME=HOME, PATH=BIN + os.pathsep + os.environ.get('PATH', ''))
STATE_ROOT = os.path.join(HOME, '.local/state/agent-skills/artifact-backup')
STATE = os.path.join(STATE_ROOT, 'links')
SHARE = os.path.join(HOME, 'share/artifacts')
CONFIG = os.path.join(HOME, '.claude/agent-skills.json')
GUARD_CALLS = os.path.join(HOME, 'guard.calls')
results = []

os.makedirs(BIN)
with open(os.path.join(BIN, 'agent-guard'), 'w') as f:
    # The stub scanner counts its calls and fails when a scanned file holds the word SECRET.
    f.write(f'#!/bin/sh\necho "$@" >> "{GUARD_CALLS}"\n! grep -rq SECRET "$2"\n')
os.chmod(os.path.join(BIN, 'agent-guard'), 0o755)


def write_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, 'w') as f:
        json.dump(cfg, f)


DENY = {'artifact-backup': {'base_url': BASE}, 'guard': {'artifact_links': 'deny'}}
write_config(DENY)


def run(script, payload, env=None):
    r = subprocess.run([sys.executable, script], input=json.dumps(payload), capture_output=True,
                       text=True, env=env or ENV, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def check(name, cond, detail=''):
    results.append(bool(cond))
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f'  -- {detail}'))


def page(name, body='', text='hi'):
    path = os.path.join(HOME, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'<!doctype html><html><head><title>{name}</title>{body}</head><body>{text}</body></html>')
    return path


def publish(session, src, art_id, env=None, tool_input=None, **extra):
    inp = tool_input if tool_input is not None else {'file_path': src, 'icon': 'doc'}
    return run(ARCHIVE, dict({'session_id': session, 'cwd': HOME, 'hook_event_name': 'PostToolUse',
                              'tool_name': 'Artifact', 'tool_input': inp,
                              'tool_response': {'result': f'Published https://claude.ai/code/artifact/{art_id}'}},
                             **extra), env)


def pending(session):
    path = os.path.join(STATE, session + '.json')
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)['pending']


def transcript(name, entries):
    path = os.path.join(HOME, name)
    with open(path, 'w') as f:
        f.writelines(json.dumps(e) + '\n' for e in entries)
    return path


def user(text):
    return {'type': 'user', 'message': {'role': 'user', 'content': text}}


def assistant(*blocks):
    return {'type': 'assistant', 'message': {'role': 'assistant', 'content': list(blocks)}}


def result(tool_id, error=False):
    return {'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': tool_id, 'content': 'error' if error else 'sent', 'is_error': error}]}}


def iso(offset):
    return datetime.datetime.fromtimestamp(time.time() + offset, datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def stop(session, tpath, last=None, active=False, event='Stop', **extra):
    payload = dict({'session_id': session, 'transcript_path': tpath, 'hook_event_name': event,
                    'stop_hook_active': active}, **extra)
    if last is not None:
        payload['last_assistant_message'] = last
    return run(STOP, payload)


def send(tool, inp):
    return run(SEND, {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': inp, 'session_id': 'x'})


def denied(out):
    return (out or {}).get('hookSpecificOutput', {}).get('permissionDecision') == 'deny'


def errors():
    path = os.path.join(STATE_ROOT, 'errors.log')
    return open(path).read() if os.path.exists(path) else ''


def al_links(out):
    ctx = out['hookSpecificOutput']['additionalContext']
    return [l.split(': ', 1)[1] for l in ctx.splitlines() if l.startswith(('claude.ai link:', 'backup link:'))]


def latest_url(folder):
    newest = sorted(f for f in os.listdir(os.path.join(SHARE, folder)) if f.endswith('.html'))[-1]
    return f'{BASE}{folder}/{newest}'


# --- PostToolUse: public page
CLAUDE = 'https://claude.ai/code/artifact/aaaa1111-2222-3333-4444-555566667777'
out = publish('s1', page('plan.html'), 'aaaa1111-2222-3333-4444-555566667777')
ctx = (out or {}).get('hookSpecificOutput', {}).get('additionalContext', '')
folders = os.listdir(SHARE)
folder = next((d for d in folders if d.endswith('-plan-aaaa1111')), None)
BACKUP = latest_url(folder) if folder else None
check('public: hookEventName is PostToolUse', (out or {}).get('hookSpecificOutput', {}).get('hookEventName') == 'PostToolUse', out)
check('public: backup folder created', folder is not None, folders)
check('public: context has claude.ai URL', CLAUDE in ctx, ctx)
check('public: context has backup URL', BACKUP in ctx, ctx)
check('public: backup link is the version html, not the folder', f'artifacts/{folder}/\n' not in ctx + '\n' and BACKUP.endswith('.html'), ctx)
check('public: context addresses owner', 'owner' in ctx, ctx)
p = pending('s1')
check('public: pending pair recorded', p and p[0]['url'] == CLAUDE and p[0]['backup'] == BACKUP, p)
mode = stat.S_IMODE(os.stat(os.path.join(STATE, 's1.json')).st_mode)
check('public: state file is 0600', mode == 0o600, oct(mode))
check('public: index.jsonl appended', os.path.exists(os.path.join(STATE_ROOT, 'index.jsonl')))
check('public: index.html still regenerated', os.path.exists(os.path.join(SHARE, 'index.html')))
check('served folder: no index.jsonl', not os.path.exists(os.path.join(SHARE, 'index.jsonl')), os.listdir(SHARE))
check('served folder: no errors.log', not os.path.exists(os.path.join(SHARE, 'errors.log')), os.listdir(SHARE))

# Republishing the same artifact keeps one pending entry
time.sleep(1.1)
out = publish('s1', page('plan.html', text='second version'), 'aaaa1111-2222-3333-4444-555566667777')
check('republish: still one pending entry', len(pending('s1')) == 1, pending('s1'))
FIRST, BACKUP = BACKUP, latest_url(folder)
check('republish: link moves to the newest version file', BACKUP != FIRST and pending('s1')[0]['backup'] == BACKUP
      and BACKUP in out['hookSpecificOutput']['additionalContext'], (FIRST, pending('s1')))

# --- PostToolUse: private page
PRIV = 'https://claude.ai/code/artifact/bbbb1111-2222-3333-4444-555566667777'
out = publish('s2', page('secret.html', '<meta name="backup" content="no">'), 'bbbb1111-2222-3333-4444-555566667777')
ctx = (out or {}).get('hookSpecificOutput', {}).get('additionalContext', '')
check('private: context has claude.ai URL', PRIV in ctx, ctx)
check('private: no backup link in context', BASE not in ctx, ctx)
check('private: context says no public backup', 'backup link: none' in ctx, ctx)
check('private: pending backup is None', pending('s2')[0]['backup'] is None, pending('s2'))
check('private: copy went to artifacts-private', any(d.endswith('-secret-bbbb1111') for d in os.listdir(os.path.join(HOME, 'artifacts-private'))))

# --- PostToolUse: subagent wording, non-publish action
out = publish('s3', page('sub.html'), 'cccc1111-2222-3333-4444-555566667777', agent_id='a1', agent_type='general-purpose')
ctx = (out or {}).get('hookSpecificOutput', {}).get('additionalContext', '')
check('subagent publish: context addresses caller report', 'caller' in ctx, ctx)
out = run(ARCHIVE, {'session_id': 's9', 'tool_name': 'Artifact', 'tool_input': {'action': 'read', 'url': CLAUDE}, 'tool_response': {}})
check('read action: no output, no state', out is None and pending('s9') is None, out)

# --- Stop: reply lacks links -> block
t_none = transcript('t_none.jsonl', [user('make a plan'), assistant({'type': 'text', 'text': 'Published the plan.'})])
out = stop('s1', t_none)
check('stop missing: decision block', (out or {}).get('decision') == 'block', out)
check('stop missing: reason names both links', CLAUDE in out.get('reason', '') and BACKUP in out.get('reason', ''), out)
check('stop missing: pending kept', len(pending('s1') or []) == 1)

# --- Stop: only one link given -> block naming just the other
out = stop('s1', t_none, last=f'Done: {CLAUDE}')
check('stop partial: blocks for backup only', (out or {}).get('decision') == 'block' and BACKUP in out['reason'] and CLAUDE not in out['reason'], out)

# --- Stop: the older version's file link does not satisfy the newest one
out = stop('s1', t_none, last=f'{CLAUDE} {FIRST}')
check('stop older version link: block for newest file', (out or {}).get('decision') == 'block' and BACKUP in out['reason'], out)

# --- Stop: links in transcript -> pass and clear
t_ok = transcript('t_ok.jsonl', [user('make a plan'),
                                 assistant({'type': 'tool_use', 'name': 'Artifact', 'input': {'file_path': 'x'}}),
                                 {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'content': 'ok'}]}},
                                 assistant({'type': 'text', 'text': f'링크: {CLAUDE}\n백업: {BACKUP}'})])
out = stop('s1', t_ok)
check('stop transcript links: allowed (no output)', out is None, out)
check('stop transcript links: pending cleared', pending('s1') is None, pending('s1'))

# --- Stop: links via Telegram reply tool count
publish('s4', page('tg.html'), 'dddd1111-2222-3333-4444-555566667777')
t4 = pending('s4')[0]
t_tg = transcript('t_tg.jsonl', [user('x'), assistant({'type': 'tool_use', 'id': 'tg1', 'name': 'mcp__plugin_telegram_telegram__reply',
                                                       'input': {'text': f"{t4['url']} {t4['backup']}"}}),
                                 result('tg1'), assistant({'type': 'text', 'text': '보냈습니다.'})])
check('stop telegram links: allowed', stop('s4', t_tg) is None and pending('s4') is None)

# --- Stop: last_assistant_message carries the links
publish('s5', page('last.html'), 'eeee1111-2222-3333-4444-555566667777')
t5 = pending('s5')[0]
check('stop last_assistant_message: allowed', stop('s5', '/nonexistent', last=f"{t5['url']} {t5['backup']}") is None and pending('s5') is None)

# --- Stop: private page needs only the claude.ai link
out = stop('s2', t_none)
check('stop private missing: block without backup link', (out or {}).get('decision') == 'block' and BASE not in out['reason'] and 'no public backup' in out['reason'], out)
check('stop private given: allowed', stop('s2', t_none, last=f'{PRIV} (백업 비공개)') is None and pending('s2') is None)

# --- Stop: stop_hook_active and still missing -> allow with warning, clear
publish('s6', page('loop.html'), 'ffff1111-2222-3333-4444-555566667777')
out = stop('s6', t_none, active=True)
check('stop_hook_active: no block', out is not None and 'decision' not in out, out)
check('stop_hook_active: systemMessage warns', 'systemMessage' in (out or {}), out)
check('stop_hook_active: pending cleared', pending('s6') is None)

# --- Subagents are never blocked
out = stop('s3', t_none, event='SubagentStop', agent_id='a1')
check('SubagentStop: no output', out is None, out)
check('SubagentStop: pending untouched', pending('s3') is not None)
check('Stop with agent_id: no output', stop('s3', t_none, agent_id='a1') is None)

# --- Stale entries (older than 6h) are ignored
os.makedirs(STATE, exist_ok=True)
with open(os.path.join(STATE, 's7.json'), 'w') as f:
    json.dump({'pending': [{'key': 'k', 'url': CLAUDE, 'backup': BACKUP, 'time': '2020-01-01T00:00:00'}]}, f)
check('stale entry: allowed and cleared', stop('s7', t_none) is None and pending('s7') is None)

# --- No state at all
check('no pending: no output', stop('s8', t_none) is None)

# --- Artifact turned private in another session -> no public link demanded
LATER = 'https://claude.ai/code/artifact/abab1111-2222-3333-4444-555566667777'
publish('s10', page('later.html'), 'abab1111-2222-3333-4444-555566667777')
check('later-private: public pending at first', pending('s10')[0]['backup'] is not None, pending('s10'))
publish('s11', page('later.html', '<meta name="backup" content="no">'), 'abab1111-2222-3333-4444-555566667777')
out = stop('s10', t_none)
check('later-private: block names no public backup link', (out or {}).get('decision') == 'block'
      and BASE not in out['reason'] and 'no public backup' in out['reason'], out)
check('later-private: claude.ai link alone satisfies', stop('s10', t_none, last=f'{LATER} (백업 비공개)') is None, pending('s10'))

# --- Parallel publishes on one session keep every pending entry
ids = [f'{i:04x}9999-2222-3333-4444-555566667777' for i in range(12)]
with concurrent.futures.ThreadPoolExecutor(12) as ex:
    list(ex.map(lambda a: publish('s12', page(f'par{a[:4]}.html'), a), ids))
check('parallel publish: all 12 pending kept', len(pending('s12') or []) == 12, len(pending('s12') or []))

# --- Only the final reply counts, not earlier text in the turn
publish('s13', page('final.html'), '13131111-2222-3333-4444-555566667777')
e13 = pending('s13')[0]
t_mid = transcript('t_mid.jsonl', [user('x'), assistant({'type': 'text', 'text': f"{e13['url']} {e13['backup']}"}),
                                   assistant({'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ls'}}),
                                   {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'content': 'ok'}]}},
                                   assistant({'type': 'text', 'text': '완료했습니다.'})])
check('final-only: earlier text does not satisfy (transcript)', (stop('s13', t_mid) or {}).get('decision') == 'block')
check('final-only: earlier text does not satisfy (last_assistant_message)',
      (stop('s13', t_mid, last='완료했습니다.') or {}).get('decision') == 'block')

# --- Only delivering tools count; Slack search does not
publish('s14', page('slack.html'), '14141111-2222-3333-4444-555566667777')
e14 = pending('s14')[0]
links14 = f"{e14['url']} {e14['backup']}"
t_search = transcript('t_search.jsonl', [user('x'), assistant({'type': 'tool_use', 'name': 'mcp__claude_ai_Slack__slack_search_public',
                                                               'input': {'query': links14}}),
                                         assistant({'type': 'text', 'text': 'ok'})])
check('delivery: slack search input does not count', (stop('s14', t_search) or {}).get('decision') == 'block')
t_send = transcript('t_send.jsonl', [user('x'), assistant({'type': 'tool_use', 'id': 'sl1', 'name': 'mcp__claude_ai_Slack__slack_send_message',
                                                           'input': {'text': links14}}),
                                     result('sl1'), assistant({'type': 'text', 'text': 'ok'})])
check('delivery: slack send_message counts', stop('s14', t_send) is None and pending('s14') is None)

# --- A publish from an earlier (aborted) turn does not block a later turn
publish('s15', page('aborted.html'), '15151111-2222-3333-4444-555566667777')
later = datetime.datetime.fromtimestamp(time.time() + 5, datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
t_new = transcript('t_new.jsonl', [dict(user('new topic'), timestamp=later), assistant({'type': 'text', 'text': 'ok'})])
check('turn tie: earlier-turn publish not enforced', stop('s15', t_new) is None and pending('s15') is None)

# --- Private store is owner-only
pdir = next(os.path.join(HOME, 'artifacts-private', d) for d in os.listdir(os.path.join(HOME, 'artifacts-private')) if d.endswith('-secret-bbbb1111'))
modes = [stat.S_IMODE(os.stat(x).st_mode) for x in [os.path.join(HOME, 'artifacts-private'), pdir]]
fmodes = {stat.S_IMODE(os.stat(os.path.join(pdir, f)).st_mode) for f in os.listdir(pdir)}
check('private perms: dirs 0700', modes == [0o700, 0o700], [oct(m) for m in modes])
check('private perms: copies 0600', fmodes == {0o600}, [oct(m) for m in fmodes])
lpdir = next(os.path.join(HOME, 'artifacts-private', d) for d in os.listdir(os.path.join(HOME, 'artifacts-private')) if d.endswith('-later-abab1111'))
lmodes = {stat.S_IMODE(os.stat(os.path.join(lpdir, f)).st_mode) for f in os.listdir(lpdir)}
check('private perms: moved copies 0600', lmodes == {0o600}, [oct(m) for m in lmodes])

# --- An entry written before `epoch` existed is still dropped once a later turn starts
os.makedirs(STATE, exist_ok=True)
old_time = (datetime.datetime.now() - datetime.timedelta(minutes=10)).isoformat(timespec='seconds')
with open(os.path.join(STATE, 's16.json'), 'w') as f:
    json.dump({'pending': [{'key': 'k16', 'url': CLAUDE, 'backup': BACKUP, 'time': old_time}]}, f)
t16 = transcript('t16.jsonl', [dict(user('unrelated'), timestamp=iso(-60)), assistant({'type': 'text', 'text': 'ok'})])
check('no-epoch entry before turn start: not enforced', stop('s16', t16) is None and pending('s16') is None)

# --- An owner `!` shell line is not a turn start
publish('s17', page('shell.html'), '17171111-2222-3333-4444-555566667777')
t17 = transcript('t17.jsonl', [dict(user('publish it'), timestamp=iso(-60)),
                               dict(user('<bash-input>ls</bash-input>'), timestamp=iso(5)),
                               dict(user('<bash-stdout>x</bash-stdout>'), timestamp=iso(5)),
                               assistant({'type': 'text', 'text': 'ok'})])
e17 = pending('s17')[0]
check('shell echo is not a turn start: still enforced', (stop('s17', t17) or {}).get('decision') == 'block')

# --- Links already given in an earlier reply (after the publish) clear the entry
with open(os.path.join(STATE, 's18.json'), 'w') as f:
    json.dump({'pending': [dict(e17, epoch=time.time() - 30)]}, f)
t18 = transcript('t18.jsonl', [dict(user('publish it'), timestamp=iso(-60)),
                               dict(assistant({'type': 'text', 'text': f"{e17['url']} {e17['backup']}"}), timestamp=iso(-20)),
                               dict(user('next question'), timestamp=iso(-40)),
                               dict(assistant({'type': 'text', 'text': 'unrelated answer'}), timestamp=iso(-10))])
check('links in an earlier reply after publish: cleared, not blocked', stop('s18', t18) is None and pending('s18') is None)

# --- A failed delivering call does not count
publish('s19', page('tgfail.html'), '19191111-2222-3333-4444-555566667777')
e19 = pending('s19')[0]
t19 = transcript('t19.jsonl', [user('x'), assistant({'type': 'tool_use', 'id': 'tgf', 'name': 'mcp__plugin_telegram_telegram__reply',
                                                     'input': {'text': f"{e19['url']} {e19['backup']}"}}),
                               result('tgf', error=True), assistant({'type': 'text', 'text': '보냈습니다.'})])
check('failed telegram reply: does not count', (stop('s19', t19) or {}).get('decision') == 'block')
t19b = transcript('t19b.jsonl', [user('x'), assistant({'type': 'tool_use', 'id': 'tgn', 'name': 'mcp__plugin_telegram_telegram__reply',
                                                       'input': {'text': f"{e19['url']} {e19['backup']}"}}),
                                 assistant({'type': 'text', 'text': '보냈습니다.'})])
check('telegram reply without a result: does not count', (stop('s19', t19b) or {}).get('decision') == 'block')

# --- Stateless check: a session that published nothing still owes the backup link of an artifact it names
BACKUP_PLAN = pending('s1') or None
PLAN_LINK = latest_url(folder)
check('stateless stop: no pending file for the session', pending('s20') is None)
out = stop('s20', t_none, last=f'Plan is at {CLAUDE}')
check('stateless stop: names backed-up artifact without backup -> block', (out or {}).get('decision') == 'block' and PLAN_LINK in out['reason'], out)
check('stateless stop: backup link given -> allowed', stop('s20', t_none, last=f'{CLAUDE} {PLAN_LINK}') is None)
check('stateless stop: older version link does not satisfy', (stop('s20', t_none, last=f'{CLAUDE} {FIRST}') or {}).get('decision') == 'block')
check('stateless stop: private artifact needs no backup link', stop('s20', t_none, last=f'see {PRIV}') is None)
check('stateless stop: unknown artifact needs no backup link',
      stop('s20', t_none, last='see https://claude.ai/code/artifact/zzzz0000-0000-0000-0000-000000000000') is None)
out = stop('s20', t_none, last=f'Plan is at {CLAUDE}', active=True)
check('stateless stop: stop_hook_active warns, no block', out is not None and 'decision' not in out and 'systemMessage' in out, out)
check('stateless stop: subagent not checked', stop('s20', t_none, last=f'Plan is at {CLAUDE}', agent_id='a1') is None)
t_sl = transcript('t_sl.jsonl', [user('x'), assistant({'type': 'text', 'text': f'Plan is at {CLAUDE}'})])
check('stateless stop: reads the final reply from the transcript', (stop('s20', t_sl) or {}).get('decision') == 'block')

# --- PreToolUse send check
bare = f'Plan: {CLAUDE}'
full = f'Plan: {CLAUDE} backup: {PLAN_LINK}'
check('send telegram: link missing -> deny', denied(send('mcp__plugin_telegram_telegram__reply', {'text': bare})))
check('send telegram: deny names the backup link', PLAN_LINK in json.dumps(send('mcp__plugin_telegram_telegram__reply', {'text': bare})))
check('send telegram: pair present -> allowed', send('mcp__plugin_telegram_telegram__reply', {'text': full}) is None)
check('send telegram: private artifact allowed', send('mcp__plugin_telegram_telegram__reply', {'text': PRIV}) is None)
check('send telegram: no artifact link allowed', send('mcp__plugin_telegram_telegram__reply', {'text': 'hello'}) is None)
ORCA = 'orca orchestration send --to term_1 --subject s --body '
check('send orca: literal body missing link -> deny', denied(send('Bash', {'command': f"{ORCA}'{bare}'"})))
check('send orca: literal body with pair -> allowed', send('Bash', {'command': f"{ORCA}'{full}'"}) is None)
check('send orca: --text and terminal send are checked',
      denied(send('Bash', {'command': f"orca terminal send --terminal t --text '{bare}'"})))
check('send orca: worker_done is checked', denied(send('Bash', {'command': f"{ORCA}'{bare}' --type worker_done"})))
body_file = os.path.join(HOME, 'body.md')
with open(body_file, 'w') as f:
    f.write(bare)
check('send orca: body from a file is read', denied(send('Bash', {'command': f'{ORCA}"$(cat {body_file})"'})))
with open(body_file, 'w') as f:
    f.write(full)
check('send orca: body file with pair -> allowed', send('Bash', {'command': f'{ORCA}"$(cat {body_file})"'}) is None)
check('send bash: other commands not checked', send('Bash', {'command': f"echo '{bare}'"}) is None)
write_config({'artifact-backup': {'base_url': BASE}, 'guard': {'artifact_links': 'warn'}})
out = send('mcp__plugin_telegram_telegram__reply', {'text': bare})
check('send warn level: no deny, context names the link', out and not denied(out) and PLAN_LINK in out['hookSpecificOutput']['additionalContext'], out)
write_config({'artifact-backup': {'base_url': BASE}, 'guard': {'artifact_links': 'off'}})
check('send off level: nothing', send('mcp__plugin_telegram_telegram__reply', {'text': bare}) is None)
write_config({'artifact-backup': {'base_url': BASE}})  # no guard key: the default is warn
out = send('mcp__plugin_telegram_telegram__reply', {'text': bare})
check('send default (no guard key): warns, does not deny', out and not denied(out) and PLAN_LINK in out['systemMessage'], out)
check('send default: orca body also only warns', not denied(send('Bash', {'command': f"{ORCA}'{bare}'"})))
out = stop('s20', t_none, last=f'Plan is at {CLAUDE}')
check('stop default (no guard key): one systemMessage, no block', out is not None and 'decision' not in out and PLAN_LINK in out['systemMessage'], out)
write_config({'artifact-backup': {'base_url': BASE}, 'guard': {'artifact_links': 'off'}})
check('stop off level: nothing', stop('s20', t_none, last=f'Plan is at {CLAUDE}') is None)
write_config(DENY)

# --- Slack is not a checked send path
check('send slack: not checked', send('mcp__claude_ai_Slack__slack_send_message', {'message': bare}) is None)

# --- Until the cutover both the old settings.json hook and the plugin copy run: a second run is harmless
before = len(open(os.path.join(STATE_ROOT, 'index.jsonl')).read().splitlines())
TWICE = 'bb5f1111-2222-3333-4444-555566667777'
src_twice = page('twice.html')
first = publish('s33', src_twice, TWICE)
second = publish('s33', src_twice, TWICE)
tf = [d for d in os.listdir(SHARE) if d.endswith('-twice-bb5f1111')]
check('double run: one folder, one pending entry', len(tf) == 1 and len(pending('s33')) == 1, (tf, pending('s33')))
check('double run: both announce the same links', first == second or
      al_links(first) == al_links(second), (first, second))
check('double run: the backup link in the pending entry exists on disk',
      os.path.isfile(os.path.join(SHARE, pending('s33')[0]['backup'][len(BASE):])), pending('s33'))
check('double run: stop passes with the announced pair', stop('s33', t_none, last=' '.join(al_links(second))) is None)

# --- Supporting files: a list of strings is backed up and scanned like the map form
os.makedirs(os.path.join(HOME, 'site/css'))
for rel, body in (('site/css/a.css', 'a{}'), ('site/css/b.css', 'b{}'), ('site/app.js', 'x=1')):
    with open(os.path.join(HOME, rel), 'w') as f:
        f.write(body)
open(GUARD_CALLS, 'w').close()
LISTED = 'aa5a1111-2222-3333-4444-555566667777'
out = publish('s21', page('listed.html'), LISTED, tool_input={
    'file_path': os.path.join(HOME, 'listed.html'), 'root': 'site', 'files': ['css/a.css', 'css/b.css', 'app.js']})
lf = next(d for d in os.listdir(SHARE) if d.endswith('-listed-aa5a1111'))
check('files list: supporting files copied at their published paths',
      all(os.path.isfile(os.path.join(SHARE, lf, p)) for p in ('css/a.css', 'css/b.css', 'app.js')), os.listdir(os.path.join(SHARE, lf)))
check('files list: one scan for the page and its three files', len(open(GUARD_CALLS).read().splitlines()) == 1, open(GUARD_CALLS).read())
with open(os.path.join(HOME, 'site/css/b.css'), 'w') as f:
    f.write('SECRET')
SECRET_ART = 'ab5b1111-2222-3333-4444-555566667777'
out = publish('s22', page('leaky.html'), SECRET_ART, tool_input={
    'file_path': os.path.join(HOME, 'leaky.html'), 'root': 'site', 'files': ['css/a.css', 'css/b.css']})
check('files list: a secret in a listed file keeps the page private', 'backup link: none' in out['hookSpecificOutput']['additionalContext']
      and any(d.endswith('-leaky-ab5b1111') for d in os.listdir(os.path.join(HOME, 'artifacts-private')))
      and not any(d.endswith('-leaky-ab5b1111') for d in os.listdir(SHARE)), out)
check('files list: staging folder left no trace in the private store',
      not any(d.startswith('.scan-') for d in os.listdir(os.path.join(HOME, 'artifacts-private'))))

# --- A publish with no file_path backs up the page among its files
NOFILE = 'ac5c1111-2222-3333-4444-555566667777'
out = publish('s23', None, NOFILE, tool_input={'files': {'index.html': page('nofile.html'), 'img/x.txt': os.path.join(HOME, 'site/app.js')}})
nf = next((d for d in os.listdir(SHARE) if d.endswith('-nofile-ac5c1111')), None)
check('no file_path: page backed up from files', nf is not None and os.path.isfile(os.path.join(SHARE, nf, 'img/x.txt')), os.listdir(SHARE))
check('no file_path: links announced', f'artifact/{NOFILE}' in (out or {}).get('hookSpecificOutput', {}).get('additionalContext', ''), out)
check('no file_path and no files: nothing to keep', publish('s24', None, 'ad5d1111-2222-3333-4444-555566667777', tool_input={'type_url': 'x'}) is None)

# --- No agent-guard: the page is kept private, not copied unscanned
env_noguard = dict(ENV, PATH='/usr/bin:/bin')
NOGUARD = 'ae5e1111-2222-3333-4444-555566667777'
out = publish('s25', page('noguard.html'), NOGUARD, env=env_noguard)
check('no agent-guard: page kept private', any(d.endswith('-noguard-ae5e1111') for d in os.listdir(os.path.join(HOME, 'artifacts-private')))
      and not any(d.endswith('-noguard-ae5e1111') for d in os.listdir(SHARE)), out)
check('no agent-guard: announced as private', 'backup link: none' in out['hookSpecificOutput']['additionalContext'], out)
check('no agent-guard: logged', 'agent-guard not found' in errors(), errors())

# --- A killed run is noticed and its publish backed up again
dead = subprocess.Popen([sys.executable, '-c', 'pass'])
dead.wait()
KILLED = 'af5f1111-2222-3333-4444-555566667777'
os.makedirs(os.path.join(STATE_ROOT, 'inflight'), exist_ok=True)
with open(os.path.join(STATE_ROOT, 'inflight', f'{dead.pid}-1.json'), 'w') as f:
    json.dump({'pid': dead.pid, 'data': {'session_id': 's26', 'cwd': HOME, 'tool_name': 'Artifact',
                                         'tool_input': {'file_path': page('killed.html')},
                                         'tool_response': {'result': f'https://claude.ai/code/artifact/{KILLED}'}}}, f)
publish('s27', page('after.html'), 'b05f1111-2222-3333-4444-555566667777')
check('killed run: reported in errors.log', 'previous run was killed' in errors() and 'killed.html' in errors(), errors())
check('killed run: publish backed up again', any(d.endswith('-killed-af5f1111') for d in os.listdir(SHARE)), os.listdir(SHARE))
check('killed run: marker removed', not os.listdir(os.path.join(STATE_ROOT, 'inflight')), os.listdir(os.path.join(STATE_ROOT, 'inflight')))
check('killed run: recovery queues no pending link for a dead session', pending('s26') is None)
with open(os.path.join(STATE_ROOT, 'inflight', f'{os.getpid()}-2.json'), 'w') as f:
    json.dump({'pid': os.getpid(), 'data': {}}, f)
publish('s28', page('live.html'), 'b15f1111-2222-3333-4444-555566667777')
check('running marker is left alone', os.path.exists(os.path.join(STATE_ROOT, 'inflight', f'{os.getpid()}-2.json')))
os.remove(os.path.join(STATE_ROOT, 'inflight', f'{os.getpid()}-2.json'))

# --- An index and error log an older hook left in the served folder move out of it
with open(os.path.join(SHARE, 'index.jsonl'), 'w') as f:
    f.write(json.dumps({'key': 'legacy', 'time': '2020-01-01T00:00:00', 'url': None, 'title': 'legacy', 'copy': os.path.join(SHARE, 'x/y.html'), 'dir': os.path.join(SHARE, 'x')}) + '\n')
with open(os.path.join(SHARE, 'errors.log'), 'w') as f:
    f.write('old error\n')
publish('s29', page('migrate.html'), 'b25f1111-2222-3333-4444-555566667777')
check('legacy files: moved out of the served folder', not os.path.exists(os.path.join(SHARE, 'index.jsonl'))
      and not os.path.exists(os.path.join(SHARE, 'errors.log')), os.listdir(SHARE))
check('legacy files: rows and log kept', '"legacy"' in open(os.path.join(STATE_ROOT, 'index.jsonl')).read() and 'old error' in errors())

# --- No base_url: copy kept, one-line warning, no backup link demanded
write_config({})
NOBASE = 'b35f1111-2222-3333-4444-555566667777'
out = publish('s30', page('nobase.html'), NOBASE)
ctx = out['hookSpecificOutput']['additionalContext']
check('no base_url: claude.ai link announced, backup says why', f'artifact/{NOBASE}' in ctx and 'base_url is not set' in ctx, ctx)
check('no base_url: copy still made', any(d.endswith('-nobase-b35f1111') for d in os.listdir(SHARE)))
check('no base_url: warning logged', 'base_url unset' in errors(), errors())
out = stop('s30', t_none)
check('no base_url: stop asks for the claude.ai link only', (out or {}).get('decision') == 'block'
      and f'artifact/{NOBASE}' in out['reason'] and 'artifacts/' not in out['reason'].replace('/artifact/', ''), out)
check('no base_url: stateless check demands nothing', stop('s31', t_none, last=f'{CLAUDE}') is None)
write_config({'artifact-backup': {'base_url': BASE}})

# --- Categories come from the config; without any, every page is "기타"
index_html = open(os.path.join(SHARE, 'index.html'), encoding='utf-8').read()
check('categories: unset config puts pages under 기타', '기타' in index_html and 'Docs' not in index_html)
write_config({'artifact-backup': {'base_url': BASE, 'categories': [['Docs', 'plan|later'], ['broken']]}})
publish('s32', page('plan.html', text='again'), 'aaaa1111-2222-3333-4444-555566667777')
publish('s32', page('plan2.html'), 'b45f1111-2222-3333-4444-555566667777')
rows = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl'))]
check('categories: a configured rule groups a new page', next(r for r in rows if r['key'].startswith('b45f1111'))['category'] == 'Docs', rows[-1])
check('categories: an earlier category sticks to its key', next(r for r in reversed(rows) if r['key'].startswith('aaaa1111'))['category'] == '기타')
write_config({'artifact-backup': {'base_url': BASE}})

# --- Codex round 1: leaks, races and lost versions
index_html = open(os.path.join(SHARE, 'index.html'), encoding='utf-8').read()
check('served index: a private page shows neither its title nor its claude.ai link',
      'secret.html' not in index_html and 'noguard.html' not in index_html and 'bbbb1111-2222' not in index_html
      and '비공개 bbbb1111' in index_html)

SAME = 'bc5f1111-2222-3333-4444-555566667777'
src_same = page('same.html', text='v1')
publish('s34', src_same, SAME)
with open(src_same, 'w') as f:
    f.write('<title>same.html</title>changed')
publish('s34', src_same, SAME)
versions = [f for f in os.listdir(os.path.join(SHARE, next(d for d in os.listdir(SHARE) if d.endswith('-same-bc5f1111')))) if f.endswith('.html')]
check('same second, changed content: both versions kept', len(versions) == 2, versions)

DELAY = 'bd5f1111-2222-3333-4444-555566667777'
src_delay = page('delay.html')
a = publish('s35', src_delay, DELAY)
time.sleep(1.2)
b = publish('s35', src_delay, DELAY)
check('duplicate run a second later: same content reuses the version and announces the same links',
      al_links(a) == al_links(b), (a, b))
check('duplicate run a second later: one version file', len([f for f in os.listdir(os.path.join(SHARE, next(
      d for d in os.listdir(SHARE) if d.endswith('-delay-bd5f1111')))) if f.endswith('.html')]) == 1)

# A source edited while the scanner runs does not reach the public folder: the stub rewrites it mid-scan.
RACE = 'be5f1111-2222-3333-4444-555566667777'
src_race = page('race.html', text='clean')
with open(os.path.join(BIN, 'agent-guard'), 'w') as f:
    f.write(f'#!/bin/sh\necho "$@" >> "{GUARD_CALLS}"\necho SECRET > "{src_race}"\n! grep -rq SECRET "$2"\n')
publish('s36', src_race, RACE)
os.chmod(os.path.join(BIN, 'agent-guard'), 0o755)
copied = [open(os.path.join(SHARE, d, f)).read() for d in os.listdir(SHARE) if d.endswith('-race-be5f1111')
          for f in os.listdir(os.path.join(SHARE, d)) if f.endswith('.html')]
check('scan race: the published copy is the scanned snapshot', copied and all('SECRET' not in c for c in copied), copied)
with open(os.path.join(BIN, 'agent-guard'), 'w') as f:
    f.write(f'#!/bin/sh\necho "$@" >> "{GUARD_CALLS}"\n! grep -rq SECRET "$2"\n')

# A retry that is killed again is found again; a marker is dropped after two tries.
dead2 = subprocess.Popen([sys.executable, '-c', 'pass'])
dead2.wait()
RETRY = 'bf5f1111-2222-3333-4444-555566667777'
mark = os.path.join(STATE_ROOT, 'inflight', f'{dead2.pid}-9.json')
with open(mark, 'w') as f:
    json.dump({'pid': dead2.pid, 'tries': 2, 'data': {'cwd': HOME, 'tool_input': {'file_path': page('retry.html')},
                                                      'tool_response': f'https://claude.ai/code/artifact/{RETRY}'}}, f)
publish('s37', page('after2.html'), 'c05f1111-2222-3333-4444-555566667777')
check('marker with two tries: dropped, not retried again', not os.path.exists(mark)
      and not any(d.endswith('-retry-bf5f1111') for d in os.listdir(SHARE)))

# Parallel publishes of different artifacts keep every row of the public index.
ids2 = [f'{i:04x}7777-2222-3333-4444-555566667777' for i in range(8)]
with concurrent.futures.ThreadPoolExecutor(8) as ex:
    list(ex.map(lambda a: publish('s38', page(f'idx{a[:4]}.html'), a), ids2))
keys = {json.loads(l)['key'] for l in open(os.path.join(STATE_ROOT, 'index.jsonl'))}
check('parallel publishes: no public index row lost', all(a in keys for a in ids2), len(keys))

# --- Codex round 2
write_config(DENY)
LEAK = 'c15f1111-2222-3333-4444-555566667777'
src_leak = page('leakytitle.html')
publish('s39', src_leak, LEAK)
check('same content turned private: public at first', 'leakytitle.html' in open(os.path.join(SHARE, 'index.html'), encoding='utf-8').read())
publish('s39', src_leak, LEAK, env=env_noguard)
check('same content turned private: the served index drops its title and link',
      'leakytitle.html' not in open(os.path.join(SHARE, 'index.html'), encoding='utf-8').read())

RACE2 = 'c25f1111-2222-3333-4444-555566667777'
publish('s4x', page('conc.html', text='seed'), RACE2)  # the folder exists, so the six below share it
for i in range(6):
    os.makedirs(os.path.join(HOME, f'd{i}'), exist_ok=True)
    page(f'd{i}/conc.html', text=f'version {i}')
with concurrent.futures.ThreadPoolExecutor(6) as ex:
    list(ex.map(lambda i: publish(f's4{i}', os.path.join(HOME, f'd{i}/conc.html'), RACE2), range(6)))
cdir = next(os.path.join(SHARE, d) for d in os.listdir(SHARE) if d.endswith('-conc-c25f1111'))
crows = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if RACE2 in l]
check('concurrent publishes of one artifact: one file per version',
      len({r['copy'] for r in crows}) == len(crows) == 7 and len([f for f in os.listdir(cdir) if f.endswith('.html')]) == 7,
      (len(crows), os.listdir(cdir)))

with open(os.path.join(STATE_ROOT, 'inflight', '.tmp-half'), 'w') as f:
    f.write('{')
publish('s47', page('half.html'), 'c35f1111-2222-3333-4444-555566667777')
check('a marker being written is left alone', os.path.exists(os.path.join(STATE_ROOT, 'inflight', '.tmp-half')))
os.remove(os.path.join(STATE_ROOT, 'inflight', '.tmp-half'))

# --- Codex round 3: a run that read "public" cannot publish after another run made the page private
SLOWBIN = os.path.join(HOME, 'slowbin')
os.makedirs(SLOWBIN)
with open(os.path.join(SLOWBIN, 'agent-guard'), 'w') as f:
    f.write('#!/bin/sh\nsleep 2\n! grep -rq SECRET "$2"\n')
os.chmod(os.path.join(SLOWBIN, 'agent-guard'), 0o755)
env_slow = dict(ENV, PATH=SLOWBIN + os.pathsep + '/usr/bin:/bin')
FLIP = 'c55f1111-2222-3333-4444-555566667777'
with concurrent.futures.ThreadPoolExecutor(2) as ex:
    slow = ex.submit(publish, 's49', page('flip.html', text='a'), FLIP, env_slow)
    time.sleep(0.8)
    fast = ex.submit(publish, 's4a', page('flip2.html', text='b'), FLIP, env_noguard)
    slow.result(), fast.result()
public_rows = [l for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if FLIP in l]
check('private flip during a slow scan: no public row or copy is left', not public_rows
      and not any(d.endswith('-flip-c55f1111') or d.endswith('-flip2-c55f1111') for d in os.listdir(SHARE)),
      (public_rows, os.listdir(SHARE)))
check('private flip during a slow scan: the served index does not name it',
      'flip' not in open(os.path.join(SHARE, 'index.html'), encoding='utf-8').read())

# A slow scan leaves no room for a retry: this publish's links come first and the old marker waits.
STALE = 'c65f1111-2222-3333-4444-555566667777'
dead3 = subprocess.Popen([sys.executable, '-c', 'pass'])
dead3.wait()
mark3 = os.path.join(STATE_ROOT, 'inflight', f'{dead3.pid}-7.json')
with open(mark3, 'w') as f:
    json.dump({'pid': dead3.pid, 'data': {'cwd': HOME, 'tool_input': {'file_path': page('stale.html')},
                                          'tool_response': f'https://claude.ai/code/artifact/{STALE}'}}, f)
SLOWER = os.path.join(HOME, 'slowerbin')
os.makedirs(SLOWER)
with open(os.path.join(SLOWER, 'agent-guard'), 'w') as f:
    f.write('#!/bin/sh\nsleep 6\n')
os.chmod(os.path.join(SLOWER, 'agent-guard'), 0o755)
out = publish('s4b', page('quick.html'), 'c75f1111-2222-3333-4444-555566667777', env=dict(ENV, PATH=SLOWER + os.pathsep + '/usr/bin:/bin'))
check('slow publish: announced, and the old marker is left for a quicker run', out is not None and os.path.exists(mark3), out)
publish('s4c', page('quick2.html'), 'c85f1111-2222-3333-4444-555566667777')
check('quick publish afterwards: the old marker is retried', not os.path.exists(mark3)
      and any(d.endswith('-stale-c65f1111') for d in os.listdir(SHARE)))

# --- Codex round 4: a private move killed part-way is finished by the next run; a retry never outranks a newer version
HALF = 'c95f1111-2222-3333-4444-555566667777'
publish('s4d', page('half2.html', text='one'), HALF)
time.sleep(1.1)
publish('s4d', page('half2.html', text='two'), HALF)
hrows = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if HALF in l]
pfolder = os.path.join(HOME, 'artifacts-private', os.path.basename(hrows[-1]['dir']))
os.makedirs(pfolder, mode=0o700)
moved = dict(hrows[0], dir=pfolder, copy=os.path.join(pfolder, os.path.basename(hrows[0]['copy'])))
shutil.move(hrows[0]['copy'], moved['copy'])  # the first file moved, then the run was killed
with open(os.path.join(HOME, 'artifacts-private', 'index.jsonl'), 'a') as f:
    f.write(json.dumps(moved) + '\n')
out = publish('s4d', page('half2.html', text='three'), HALF)
left = [l for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if HALF in l]
check('killed private move: next run finishes it, nothing stays public, no public link announced',
      not left and not any(d.endswith('-half2-c95f1111') for d in os.listdir(SHARE))
      and 'backup link: none' in out['hookSpecificOutput']['additionalContext'], (left, out))

NEWER = 'ca5f1111-2222-3333-4444-555566667777'
publish('s4e', page('newer.html', text='NEW'), NEWER)
dead4 = subprocess.Popen([sys.executable, '-c', 'pass'])
dead4.wait()
mark4 = os.path.join(STATE_ROOT, 'inflight', f'{dead4.pid}-5.json')
with open(mark4, 'w') as f:
    json.dump({'pid': dead4.pid, 'at': time.time() - 30, 'data': {'cwd': HOME, 'tool_input': {'file_path': page('older.html', text='OLD')},
                                                                  'tool_response': f'https://claude.ai/code/artifact/{NEWER}'}}, f)
publish('s4f', page('go.html'), 'cb5f1111-2222-3333-4444-555566667777')
nrows = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if NEWER in l]
check('killed publish older than a newer version: dropped, the newest stays the newest',
      not os.path.exists(mark4) and len(nrows) == 1 and 'NEW' in open(nrows[-1]['copy']).read() and 'superseded' in errors(), nrows)

# --- Codex round 5
SAMESEC = 'cc5f1111-2222-3333-4444-555566667777'
publish('s4g', page('samesec.html', text='first'), SAMESEC)
row = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if SAMESEC in l][-1]
dead5 = subprocess.Popen([sys.executable, '-c', 'pass'])
dead5.wait()
mark5 = os.path.join(STATE_ROOT, 'inflight', f'{dead5.pid}-6.json')
with open(mark5, 'w') as f:
    # started in the same second as the row, but after it was written
    json.dump({'pid': dead5.pid, 'at': row['epoch'] + 0.01, 'data': {'cwd': HOME, 'tool_input': {'file_path': page('samesec2.html', text='second')},
                                                                     'tool_response': f'https://claude.ai/code/artifact/{SAMESEC}'}}, f)
publish('s4h', page('go2.html'), 'cd5f1111-2222-3333-4444-555566667777')
srows = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if SAMESEC in l]
check('killed publish started after a same-second row: restored', len(srows) == 2 and 'second' in open(srows[-1]['copy']).read(), srows)

MERGE = 'ce5f1111-2222-3333-4444-555566667777'
os.makedirs(os.path.join(HOME, 'site/assets'), exist_ok=True)
for n in ('a.txt', 'b.txt'):
    open(os.path.join(HOME, 'site/assets', n), 'w').write(n)
publish('s4i', page('merge.html', text='m'), MERGE, tool_input={'file_path': os.path.join(HOME, 'merge.html'),
                                                                  'root': 'site', 'files': ['assets/a.txt', 'assets/b.txt']})
mrows = [json.loads(l) for l in open(os.path.join(STATE_ROOT, 'index.jsonl')) if MERGE in l]
mfolder = mrows[-1]['dir']
mp = os.path.join(HOME, 'artifacts-private', os.path.basename(mfolder))
os.makedirs(os.path.join(mp, 'assets'), mode=0o700)
shutil.move(os.path.join(mfolder, 'assets/a.txt'), os.path.join(mp, 'assets/a.txt'))  # half of the directory moved
with open(os.path.join(HOME, 'artifacts-private', 'index.jsonl'), 'a') as f:
    f.write(json.dumps(dict(mrows[-1], dir=mp, copy=os.path.join(mp, os.path.basename(mrows[-1]['copy'])))) + '\n')
shutil.move(mrows[-1]['copy'], os.path.join(mp, os.path.basename(mrows[-1]['copy'])))
publish('s4i', page('merge.html', text='m'), MERGE, tool_input={'file_path': os.path.join(HOME, 'merge.html'),
                                                                  'root': 'site', 'files': ['assets/a.txt', 'assets/b.txt']})
check('half-moved directory: merged, not nested', os.path.isfile(os.path.join(mp, 'assets/b.txt'))
      and os.path.isfile(os.path.join(mp, 'assets/a.txt')) and not os.path.exists(os.path.join(mp, 'assets/assets')), os.listdir(mp))

# --- Reported: a reply in a later turn repeats an Artifact link published in an earlier turn
EARLIER = 'c45f1111-2222-3333-4444-555566667777'
publish('s48', page('earlier.html'), EARLIER)
e48 = pending('s48')[0]
t_turns = transcript('t_turns.jsonl', [dict(user('publish it'), timestamp=iso(-120)),
                                       dict(assistant({'type': 'text', 'text': f"{e48['url']} {e48['backup']}"}), timestamp=iso(-100)),
                                       dict(user('what was that page again?'), timestamp=iso(5)),
                                       dict(assistant({'type': 'text', 'text': f"It is {e48['url']}"}), timestamp=iso(6))])
out = stop('s48', t_turns, last=f"It is {e48['url']}")
check('later turn repeats the link without the backup: blocked under deny, though the publish is an earlier turn',
      (out or {}).get('decision') == 'block' and e48['backup'] in out['reason'], out)
check('later turn with both links: allowed', stop('s48', t_turns, last=f"{e48['url']} {e48['backup']}") is None)
write_config({'artifact-backup': {'base_url': BASE}})
out = stop('s48', t_turns, last=f"It is {e48['url']}")
check('later turn, default level: one warning line naming the backup link', out is not None and 'decision' not in out
      and e48['backup'] in out['systemMessage'], out)
write_config(DENY)

print(f'\n{sum(results)}/{len(results)} passed (HOME={HOME})')
if all(results):
    shutil.rmtree(HOME)
sys.exit(0 if all(results) else 1)
