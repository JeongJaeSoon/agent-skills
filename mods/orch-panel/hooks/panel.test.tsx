import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { age, bar, progress, snapshotOf, stateDir, statusText, tree, workOf } from './model'

const CWD = '/work/acme/coordinator'
const NOW = Date.parse('2026-10-09T12:00:00Z')
const STATE_DIR = '/state'

const STATE = {
  generated_at: '2026-10-09T11:59:48Z',
  root: `repo-1::${CWD}`,
  sessions: [{ id: 'repo-2::/work/acme/web-search', name: 'acme-web-search' }],
  sources: { orca: { ok: true, updated_at: '2026-10-09T11:59:48Z' } },
  items: [
    {
      key: 'decision:d7', type: 'decision', decision: 'd7', title: '내보내기 형식', at: '2026-10-09T10:00:00Z',
      session: `repo-1::${CWD}`, options: [{ label: 'CSV' }, { label: 'JSON' }], recommend: 1,
    },
    { key: 'wait:p1', type: 'permission', title: 'Bash 권한 대기', at: '2026-10-09T11:57:00Z', session: 'repo-2::/work/acme/web-search' },
    { key: 'pr:acme/web#45:ci_failed', type: 'ci_failed', title: 'CI 실패', at: '2026-10-09T11:00:00Z' },
    { key: 'reload:t:1', type: 'reload_pending', title: 'reload 못 보냄', at: '2026-10-09T11:00:00Z' },
  ],
}

const PANE = {
  plugin: 'orch-panel',
  component: 'Pane',
  requestId: 'orch',
  props: { title: 'orch', isFocused: false, bodyColumns: 72, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

const statuses: (string | undefined)[] = []

// The kit has no file system: answer the two calls the mod makes beneath it.
function world(on: On, files: Record<string, { text: string; mtimeMs: number }>) {
  mock.env(on, { HOME: '/home/me', ORCH_FLEET_STATE: STATE_DIR })
  on('fs.stat', ($, e) => {
    const f = files[e.path]
    if (!f) throw new Error('ENOENT')
    return { value: { kind: 'file', size: f.text.length, mtimeMs: f.mtimeMs, isLink: false } }
  })
  on('fs.read', ($, e) => {
    const f = files[e.path]
    if (!f) throw new Error('ENOENT')
    return { value: f.text }
  })
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('ui.status', ($, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
}

test('items split into decide and human; PR and sync items stay out', () => {
  const snap = snapshotOf(JSON.stringify(STATE), NOW, CWD)
  expect(snap.status).toBe('ok')
  if (snap.status !== 'ok') return
  expect(snap.items.map(i => [i.key, i.kind])).toEqual([
    ['decision:d7', 'decide'],
    ['wait:p1', 'human'],
  ])
  expect(snap.items[0]?.recommend).toBe('CSV')
  expect(snap.items[1]?.sessionName).toBe('acme-web-search')
  expect(snap.isCoordinator).toBe(true)
  expect(statusText(snap)).toBe('결정 1 · 사람만 1')
})

test('status line clears when nothing waits or the file is unreadable', () => {
  expect(statusText(snapshotOf(JSON.stringify({ ...STATE, items: [] }), NOW, CWD))).toBeUndefined()
  expect(snapshotOf('{not json', NOW, CWD).status).toBe('unreadable')
  expect(statusText(snapshotOf('{not json', NOW, CWD))).toBeUndefined()
})

test('ages read both Z and +0900 offsets', () => {
  expect(age('2026-10-09T11:00:00Z', NOW)).toBe('1h')
  expect(age('2026-10-09T20:00:00+0900', NOW)).toBe('1h')
  expect(age(null, NOW)).toBe('?')
})

test('state dir follows ORCH_FLEET_STATE, else the collector default under HOME', () => {
  expect(stateDir({ ORCH_FLEET_STATE: '/x', HOME: '/h' })).toBe('/x')
  expect(stateDir({ HOME: '/h' })).toBe('/h/.local/state/agent-skills/dashboard')
})

test('the pane draws both groups with the answer hint, on every surface that has panes', async ($, on) => {
  mock.clock(on, { now: NOW })
  world(on, { [`${STATE_DIR}/state.json`]: { text: JSON.stringify(STATE), mtimeMs: 1 } })
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Button', key: 'toggle-needs' }))?.text).toContain('결정·사람만 2')
    expect(await ui.find({ text: /답: 프롬프트에 "d7: CSV"/ })).toBeDefined()
    expect(await ui.find({ text: /acme-web-search: Bash 권한 대기/ })).toBeDefined()
    expect(await ui.find({ text: /CI 실패/ })).toBeUndefined()
    await ui.press({ key: 'toggle-needs' })
    expect(await ui.find({ text: /답: 프롬프트/ })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'toggle-needs' }))?.text).toContain('가장 오래된 것 d7 2h')
    await ui.press({ key: 'toggle-needs' })
    await ui.unmount()
  }
})

test('without state.json the pane says the collector is off', async ($, on) => {
  mock.clock(on, { now: NOW })
  world(on, {})
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ text: /orch-dash 꺼짐/ })).toBeDefined()
})

test('a new item toasts in the coordinator session, never for the backlog found at start', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const file = { text: JSON.stringify(STATE), mtimeMs: 1 }
  world(on, { [`${STATE_DIR}/state.json`]: file })
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  await clock.advance(5000)
  expect(toasts).toEqual([])
  const added = { key: 'mail:m1', type: 'question', title: '캐시 TTL 5분 vs 1시간?', at: '2026-10-09T12:00:00Z' }
  file.text = JSON.stringify({ ...STATE, items: [...STATE.items, added] })
  file.mtimeMs = 2
  await clock.advance(5000)
  expect(toasts).toEqual(['결정 대기: 캐시 TTL 5분 vs 1시간?'])
  expect(statuses.at(-1)).toBe('결정 2 · 사람만 1')
})

const STAGES = {
  rows: {
    search: { id: 'search', title: '검색 개선', pr: 'acme/web#42', cells: { dev: { mark: 'ok', evidence: 'v1.8.0' }, prod: { mark: 'checking', evidence: '확인 중', by: 'QA 리드' } } },
    sort: { id: 'sort', title: '정렬 버그', pr: 'acme/web#45', parent: 'search', cells: {} },
    login: { id: 'login', title: '로그인 개선', pr: 'acme/web#40', done_at: '2026-10-08T00:00:00Z', cells: { dev: { mark: 'ok', evidence: 'v1.7.2' }, prod: { mark: 'ok', evidence: '10/1' } } },
    reindex: { id: 'reindex', title: '인덱스 재구축', parent: 'search', cells: { merge: { mark: 'ok', evidence: '10/2' }, dev: { mark: 'ok', evidence: '1,204건' }, prod: { mark: 'na' } } },
  },
  order: ['search', 'sort', 'login', 'reindex'],
  ['env']: { prod: 'v1.7.2', dev: 'v1.8.0' },
  next: ['acme/web#45 리뷰 → 머지 (에이전트, 오늘)'],
}

const PRS = [
  { key: 'acme/web#42', state: 'MERGED', merged_at: '2026-10-02T03:00:00Z' },
  { key: 'acme/web#40', state: 'MERGED', merged_at: '2026-09-28T03:00:00Z' },
  { key: 'acme/web#45', state: 'OPEN', ci: 'failure' },
  { key: 'acme/web#47', state: 'OPEN', ci: 'success', merge_state: 'CLEAN' },
  { key: 'acme/web#48', state: 'OPEN', ci: 'pending' },
]

test('the stage record reads as orch stage show: parents then children, merge from the PR list', () => {
  const work = workOf(JSON.stringify(STAGES), { prs: PRS })
  expect(work.rows.map(r => [r.id, r.isChild])).toEqual([
    ['search', false],
    ['sort', true],
    ['reindex', true],
    ['login', false],
  ])
  const [search, sort, reindex, login] = work.rows
  expect(search?.merge).toEqual({ mark: 'ok', evidence: '10/2', by: null })
  expect(sort?.merge).toEqual({ mark: 'fail', evidence: 'CI 실패', by: null })
  expect(sort?.chip).toEqual({ text: '✗CI', tone: 'error' })
  expect(reindex?.merge?.evidence).toBe('10/2')
  expect(login?.isDone).toBe(true)
  expect(work.versions).toEqual({ prod: 'v1.7.2', dev: 'v1.8.0' })
  expect(work.untrackedPrs).toBe(2)
  // reindex (merge ✓, dev ✓, prod –) and the finished login row are through; search waits on prod, sort on all.
  expect(progress(work)).toEqual({ done: 2, total: 4 })
  expect(bar(2, 4)).toEqual({ filled: '▰▰', empty: '▱▱' })
  expect(bar(3, 20)).toEqual({ filled: '▰▰', empty: '▱▱▱▱▱▱▱▱' })
  expect(workOf('{', {}).error).toBeTruthy()
  expect(workOf(null, {}).rows).toEqual([])
})

test('the pane draws the work table, folds it, and opens a row onto its evidence', async ($, on) => {
  mock.clock(on, { now: NOW })
  world(on, {
    [`${STATE_DIR}/state.json`]: { text: JSON.stringify({ ...STATE, prs: PRS }), mtimeMs: 1 },
    [`${STATE_DIR}/stages.json`]: { text: JSON.stringify(STAGES), mtimeMs: 1 },
  })
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Button', key: 'toggle-work' }))?.text).toContain('작업 진행 ▰▰▱▱ 2/4')
  expect(await ui.find({ text: /prod v1\.7\.2 · dev v1\.8\.0/ })).toBeDefined()
  expect(await ui.find({ text: /✗CI/ })).toBeDefined()
  expect(await ui.find({ text: /다음: acme\/web#45 리뷰/ })).toBeDefined()
  expect(await ui.find({ text: /표에 없는 열린 PR 2/ })).toBeDefined()
  expect(await ui.find({ text: /prod ◐ 확인 중 \(QA 리드\)/ })).toBeUndefined()
  await ui.press({ key: 'row-search' })
  expect(await ui.find({ text: /머지 ✓ 10\/2 · dev ✓ v1\.8\.0 · prod ◐ 확인 중 \(QA 리드\)/ })).toBeDefined()
  await ui.press({ key: 'toggle-work' })
  expect(await ui.find({ text: /다음:/ })).toBeUndefined()
  await ui.unmount()
})

test('with no stage rows the work section says how to start one', async ($, on) => {
  mock.clock(on, { now: NOW })
  world(on, { [`${STATE_DIR}/state.json`]: { text: JSON.stringify(STATE), mtimeMs: 1 } })
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Button', key: 'toggle-work' }))?.text).toContain('기록 없음')
  expect(await ui.find({ text: /기록된 단계 없음 — orch stage add/ })).toBeDefined()
})

const COORD = `repo-1::${CWD}`
const FLEET = {
  ...STATE,
  sessions: [
    { id: COORD, name: 'coordinator', kind: 'orchestrator', phase: 'working', parent: null },
    { id: 'lead-web', name: 'acme-web-search', kind: 'orchestration', phase: 'waiting', parent: COORD, linked_pr: { number: 42, state: 'open' } },
    { id: 'w-sort', name: 'sort-fix', kind: 'task', phase: 'working', parent: 'lead-web', task_title: '정렬 버그', terminals: [{ handle: 'term_sort' }] },
    { id: 'w-idx', name: 'reindex', kind: 'task', phase: 'idle', parent: 'lead-web', linked_pr: { number: 88, state: 'merged' } },
    { id: 'lead-ops', name: 'acme-ops-alerts', kind: 'orchestration', phase: 'open', parent: COORD },
    { id: 'gone', name: 'old-spike', kind: 'standalone', phase: 'offline', parent: COORD },
    { id: 'gone-2', name: 'old-try', kind: 'standalone', phase: 'offline', parent: COORD },
  ],
  holds: [
    { kind: 'lane', resource: 'acme/web@main', by: '#45', note: 'search', at: '2026-10-09T11:48:00Z', session: null },
    { kind: 'resource', resource: 'browser', by: 'term_sort', note: 'staging 로그인', at: '2026-10-09T11:57:00Z', session: 'w-sort' },
  ],
}

test('the relations tree nests by parent and folds offline sessions into a count', () => {
  const snap = snapshotOf(JSON.stringify(FLEET), NOW, CWD)
  if (snap.status !== 'ok') throw new Error(snap.status)
  // An item waiting on an offline session keeps it in the tree.
  const rel = tree(snap.sessions, new Set(['gone-2']))
  expect(rel.lines.map(l => l.prefix + l.session.name)).toEqual([
    'coordinator',
    '├─ acme-web-search',
    '│  ├─ sort-fix',
    '│  └─ reindex',
    '├─ acme-ops-alerts',
    '└─ old-try',
  ])
  expect(rel.offline).toBe(1)
  expect(snap.sessions[0]?.isMe).toBe(true)
  expect(snap.sessions[3]).toMatchObject({ pr: 88, isPrMerged: true })
  expect(snap.holds.map(h => [h.kind, h.resource, h.byName ?? h.by])).toEqual([
    ['lane', 'acme/web@main', '#45'],
    ['resource', 'browser', 'sort-fix'],
  ])
})

test('the relations section opens on press and names who holds each shared resource', async ($, on) => {
  mock.clock(on, { now: NOW })
  world(on, { [`${STATE_DIR}/state.json`]: { text: JSON.stringify(FLEET), mtimeMs: 1 } })
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const head = await ui.find({ type: 'Button', key: 'toggle-relations' })
  expect(head?.text).toContain('▸ 관계 세션 5 · ◉ 작업 중 2 · ◍ 대기 1 · 자원 2')
  expect(await ui.find({ text: /공유 자원/ })).toBeUndefined()
  await ui.press({ key: 'toggle-relations' })
  expect(await ui.find({ text: /coordinator \(나\)/ })).toBeDefined()
  expect(await ui.find({ text: /└─ ○ reindex #88 머지됨/ })).toBeDefined()
  expect(await ui.find({ text: /\+ offline 2/ })).toBeDefined()
  expect(await ui.find({ text: /acme\/web@main 착지 lane/ })).toBeDefined()
  expect(await ui.find({ text: /sort-fix staging 로그인/ })).toBeDefined()
  await ui.unmount()
})
