import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type {
  OrchPanelAutomation,
  OrchPanelCell,
  OrchPanelItem,
  OrchPanelRun,
  OrchPanelSession,
  OrchPanelSnapshot,
  OrchPanelStageRow,
} from '../types'
import {
  DASH_URL,
  STALE_COLLECTOR_MS,
  STALE_ORCA_MS,
  age,
  ageMs,
  automationIssue,
  bar,
  cadence,
  parseAt,
  progress,
  sessionUrl,
  snapshotOf,
  stateDir,
  statusText,
  toastText,
  tree,
  until,
} from './model'

const PANE = 'orch'
const POLL_MS = 5000
const snapshot = atom({ plugin: 'orch-panel', key: 'snapshot' } as const, null)
const seen = atom({ plugin: 'orch-panel', key: 'seen' } as const, null)
const collapsed = atom({ plugin: 'orch-panel', key: 'collapsed' } as const, [])
const expanded = atom({ plugin: 'orch-panel', key: 'expanded' } as const, [])

const GLYPH: Record<string, string> = {
  decision: '◆',
  approval: '◆',
  question: '?',
  permission: '!',
  prompt: '!',
  login: '!',
  run_command: '$',
}

// Stage marks as symbol plus theme color, so the table reads without color too.
const MARK: Record<OrchPanelCell['mark'], { glyph: string; color?: string; dim?: boolean }> = {
  ok: { glyph: '✓', color: 'success' },
  fail: { glyph: '✗', color: 'error' },
  partial: { glyph: '△', color: 'warning' },
  checking: { glyph: '◐', color: 'warning' },
  na: { glyph: '–', dim: true },
}

// The dashboard's phase colors, each with its own dot so the tree reads without color too.
const PHASE: Record<OrchPanelSession['phase'], { glyph: string; color?: string; word: string }> = {
  working: { glyph: '◉', color: 'permission', word: '작업 중' },
  waiting: { glyph: '◍', color: 'warning', word: '대기' },
  idle: { glyph: '○', color: 'success', word: 'idle' },
  open: { glyph: '·', word: 'open' },
  offline: { glyph: '◌', word: 'offline' },
}

// One block per automation run: filled for a run that ran, hollow for one its precheck skipped.
const RUN: Record<string, { glyph: string; color?: string }> = {
  completed: { glyph: '■', color: 'success' },
  failed: { glyph: '■', color: 'error' },
  error: { glyph: '■', color: 'error' },
  running: { glyph: '◧', color: 'warning' },
  dispatched: { glyph: '◧', color: 'warning' },
  skipped: { glyph: '□' },
  other: { glyph: '▪' },
}

// The name column is fixed so the columns after it line up; a truncating flex box drifted past its width live.
const NAME_WIDTH = 24
const clip = (text: string, max: number) => (text.length > max ? `${text.slice(0, max - 1)}…` : text)

// Module state: a hot reload starts it over, and session.start fires again to refill it.
const live = {
  dir: '',
  cwd: '',
  mtimes: '',
  parsed: null as OrchPanelSnapshot | null,
  shownStatus: undefined as string | undefined,
}

async function poll($: EngineInterface) {
  const now = await $.clock.now()
  const [stat, stagesStat] = await Promise.all(
    ['state.json', 'stages.json'].map(f => $.fs.stat(`${live.dir}/${f}`).catch(() => undefined)),
  )
  const mtimes = `${stat?.mtimeMs}:${stagesStat?.mtimeMs}`
  if (!stat) {
    live.mtimes = ''
    live.parsed = { status: 'missing', checkedAt: now }
  } else if (mtimes !== live.mtimes || !live.parsed) {
    live.mtimes = mtimes
    const [text, stagesText] = await Promise.all([
      $.fs.read(`${live.dir}/state.json`).catch(() => ''),
      stagesStat ? $.fs.read(`${live.dir}/stages.json`).catch(() => null) : null,
    ])
    live.parsed = snapshotOf(String(text), now, live.cwd, stagesText === null ? null : String(stagesText))
    await announce($, live.parsed)
  }
  // checkedAt moves on every poll so an open pane's ages keep counting while the file is unchanged.
  const parsed = live.parsed
  await update($, snapshot, () => ({ ...parsed, checkedAt: now }))
  const text = statusText(parsed)
  if (text !== live.shownStatus) {
    live.shownStatus = text
    $.ui.status(text)
  }
}

async function announce($: EngineInterface, next: OrchPanelSnapshot) {
  if (next.status !== 'ok') return
  const before = await read($, seen)
  await update($, seen, () => next.items.map(i => i.key))
  // The first read of a session only seeds: an existing backlog must not toast on every start or reload.
  if (before === null || !next.isCoordinator) return
  const fresh = next.items.filter(i => !before.includes(i.key))
  const [first] = fresh
  if (!first) return
  $.ui.toast(fresh.length === 1 ? toastText(first) : `결정·사람만 새 항목 ${fresh.length}개`)
  void $.ui.open({ id: PANE, title: 'orch' })
}

// The one network call: asking the dashboard for an immediate collection, as opening its page does.
async function refresh($: EngineInterface) {
  await $.http.fetch(`${DASH_URL}/api/fleet/state`).catch(() => undefined)
  live.mtimes = ''
  await poll($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    live.cwd = e.cwd
    const [dir, home] = await Promise.all([$.env.get('ORCH_FLEET_STATE'), $.env.get('HOME')])
    live.dir = stateDir({ ORCH_FLEET_STATE: dir, HOME: home })
    await $.command.register({ name: 'orch-panel', description: '오케스트레이션 현황 패널을 연다 (결정 대기·사람만, 작업 진행)' })
    // Not awaited: parsing a few hundred KB must not hold the first prompt.
    void poll($)
    $.clock.every(POLL_MS, () => void poll($))
    return next(e)
  })

  on('command.run', { command: 'orch-panel' }, async $ => {
    await $.ui.open({ id: PANE, title: 'orch' })
    return { text: 'orch 패널을 열었다.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const snap = await read($, snapshot)
    const folded = await read($, collapsed)
    const opened = await read($, expanded)
    const now = snap?.checkedAt ?? (await $.clock.now())

    if (!snap || snap.status === 'missing') {
      return (
        <Box flexDirection="column">
          <Text color="warning">■ orch-dash 꺼짐</Text>
          <Text dimColor>orch-dash ensure 로 켜면 결정 대기·사람만 항목이 여기 나온다</Text>
        </Box>
      )
    }
    if (snap.status === 'unreadable') {
      return (
        <Box flexDirection="column">
          <Text color="error">■ state.json 을 읽지 못함</Text>
          <Text dimColor wrap="truncate-end">{snap.error}</Text>
        </Box>
      )
    }

    const isStale = ageMs(snap.generatedAt, now) > STALE_COLLECTOR_MS
    const isOrcaStale = !!snap.orca && (!snap.orca.isOk || ageMs(snap.orca.updatedAt, now) > STALE_ORCA_MS)
    const flip = (ids: string[], id: string) => (ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id])
    const toggle = (id: string) => () => update($, collapsed, ids => flip(ids, id))
    const expand = (id: string) => () => update($, expanded, ids => flip(ids, id))

    const decideItems = snap.items.filter(i => i.kind === 'decide')
    const humanItems = snap.items.filter(i => i.kind === 'human')
    const total = snap.items.length
    const isOpen = total > 0 && !folded.includes('needs')
    const oldest = snap.items[0]
    const summary = !oldest
      ? '— 없음'
      : `· 가장 오래된 것 ${oldest.decision ?? GLYPH[oldest.type] ?? ''} ${age(oldest.at, now)}`

    const row = (item: OrchPanelItem, n: number) => {
      const waited = ageMs(item.at, now)
      const ageColor = waited > 86_400_000 ? 'error' : waited > 3_600_000 ? 'warning' : undefined
      const who = item.kind === 'human' && item.sessionName ? `${item.sessionName}: ` : ''
      return (
        <Box key={item.key} flexDirection="column">
          <Box>
            <Text dimColor>{String(n).padStart(2)} </Text>
            <Text color={item.kind === 'decide' ? 'warning' : 'permission'} bold>
              {item.decision ?? GLYPH[item.type] ?? '·'}{' '}
            </Text>
            <Box flexGrow={1} flexShrink={1}>
              <Text wrap="truncate-end">
                {who}
                {item.title}
                {item.recommend ? <Text color="success"> → 추천 {item.recommend}</Text> : null}
              </Text>
            </Box>
            <Text color={ageColor} dimColor={!ageColor}>
              {' '}
              {age(item.at, now)}
            </Text>
            {item.session ? <Link href={sessionUrl(item.session)} label=" ↗" /> : null}
          </Box>
          {item.decision ? (
            <Text dimColor>
              {'     '}답: 프롬프트에 "{item.decision}: {item.recommend ?? '<답>'}"
            </Text>
          ) : null}
        </Box>
      )
    }
    const work = snap.work
    const { done, total: rowCount } = progress(work)
    const { filled, empty } = bar(done, rowCount)
    const versions = [work.versions.prod && `prod ${work.versions.prod}`, work.versions.dev && `dev ${work.versions.dev}`]
      .filter(Boolean)
      .join(' · ')
    const isWorkOpen = !folded.includes('work')
    const mark = (cell: OrchPanelCell | null, width: number) => {
      const m = cell ? MARK[cell.mark] : undefined
      return (
        <Box width={width}>
          <Text color={m?.color} dimColor={!m || m.dim}>
            {m?.glyph ?? '·'}
          </Text>
        </Box>
      )
    }
    const evidence = (label: string, cell: OrchPanelCell | null) =>
      cell && (cell.evidence || cell.by)
        ? `${label} ${MARK[cell.mark].glyph} ${[cell.evidence, cell.by && `(${cell.by})`].filter(Boolean).join(' ')}`
        : null
    const stageRow = (r: OrchPanelStageRow) => {
      const lines = [evidence('머지', r.merge), evidence('dev', r.dev), evidence('prod', r.prod)].filter(Boolean)
      return (
        <Box key={r.id} flexDirection="column">
          <Box>
            <Box flexGrow={1} flexShrink={1}>
              <Button key={`row-${r.id}`} plain dimColor={r.isDone} onPress={expand(r.id)}>
                {r.isChild ? '  ㄴ ' : '  '}
                {r.title}
                {r.isDone ? <Text dimColor> (끝남)</Text> : ''}
              </Button>
            </Box>
            <Box width={20}>
              <Text dimColor wrap="truncate-end">
                {r.pr ?? '–'}
              </Text>
              {r.chip ? <Text color={r.chip.tone}> {r.chip.text}</Text> : null}
            </Box>
            {mark(r.merge, 5)}
            {mark(r.dev, 4)}
            {mark(r.prod, 4)}
          </Box>
          {opened.includes(r.id) && lines.length ? <Text dimColor>{'      ' + lines.join(' · ')}</Text> : null}
        </Box>
      )
    }
    const workSection = !isWorkOpen ? null : work.error ? (
      <Text color="error">  ■ stages.json 을 읽지 못함: {work.error}</Text>
    ) : !rowCount ? (
      <Text dimColor>  기록된 단계 없음 — orch stage add 로 행을 만든다</Text>
    ) : (
      <Box flexDirection="column">
        <Box>
          <Box flexGrow={1}>
            <Text dimColor>  작업</Text>
          </Box>
          <Box width={20}>
            <Text dimColor>PR</Text>
          </Box>
          <Box width={5}>
            <Text dimColor>머지</Text>
          </Box>
          <Box width={4}>
            <Text dimColor>dev</Text>
          </Box>
          <Box width={4}>
            <Text dimColor>prod</Text>
          </Box>
        </Box>
        {work.rows.map(stageRow)}
        {work.next.map((line, i) => (
          <Text key={`next-${i}`} dimColor wrap="truncate-end">
            {i === 0 ? '  다음: ' : '        '}
            {line}
          </Text>
        ))}
      </Box>
    )

    const isAutoOpen = opened.includes('section:automations')
    const autos = snap.automations
    const troubled = autos.filter(a => automationIssue(a, now))
    const upcoming = autos
      .filter(a => a.isEnabled && a.nextAt)
      .sort((a, b) => parseAt(a.nextAt) - parseAt(b.nextAt))[0]
    const isAutoSourceBad = !!snap.automationSource && !snap.automationSource.isOk
    const runBlock = (r: OrchPanelRun, i: number) => {
      const b = RUN[r.status] ?? (r.status.startsWith('skipped') ? RUN.skipped : RUN.other)
      return (
        <Text key={`run-${i}`} color={b?.color} dimColor={!b?.color}>
          {b?.glyph}
        </Text>
      )
    }
    const autoRow = (a: OrchPanelAutomation) => {
      const issue = automationIssue(a, now)
      const last = a.recent[0]
      return (
        <Box key={a.id} flexDirection="column">
          <Box>
            <Box width={2} justifyContent="flex-end">
              <Button
                key={`auto-${a.id}`}
                plain
                dimColor
                label={opened.includes(`auto:${a.id}`) ? '▾' : '▸'}
                onPress={expand(`auto:${a.id}`)}
              />
            </Box>
            <Box width={NAME_WIDTH}>
              <Text dimColor={!a.isEnabled}>
                {issue ? <Text color="error"> ▲</Text> : ''} {clip(a.isEnabled ? a.name : `${a.name} (꺼짐)`, NAME_WIDTH - (issue ? 4 : 2))}
              </Text>
            </Box>
            <Box width={17}>
              <Text dimColor wrap="truncate-end">
                {cadence(a.rrule)}
              </Text>
            </Box>
            <Box width={9}>
              <Text dimColor={!issue} color={issue ? 'warning' : undefined}>
                {a.isEnabled ? until(a.nextAt, now) : '–'}
              </Text>
            </Box>
            <Box width={8}>
              <Text dimColor>{a.lastAt ? `${age(a.lastAt, now)} 전` : '–'}</Text>
            </Box>
            <Box width={7}>{[...a.recent].reverse().map(runBlock)}</Box>
          </Box>
          {opened.includes(`auto:${a.id}`) ? (
            <Text dimColor wrap="truncate-end">
              {'      '}
              {issue ? `${issue} · ` : ''}마지막: {last ? `${last.status} ${age(last.at, now)} 전${last.summary ? ` — ${last.summary}` : ''}` : '없음'}
            </Text>
          ) : null}
        </Box>
      )
    }
    const autoSection = !isAutoOpen ? null : (
      <Box flexDirection="column">
        {isAutoSourceBad ? (
          <Text color="warning">  ▲ Orca automation 목록을 못 읽음 ({age(snap.automationSource?.updatedAt ?? null, now)} 전 값)</Text>
        ) : null}
        {autos.length ? (
          <Box>
            <Box width={NAME_WIDTH + 2}>
              <Text dimColor>  이름</Text>
            </Box>
            <Box width={17}>
              <Text dimColor>주기</Text>
            </Box>
            <Box width={9}>
              <Text dimColor>다음</Text>
            </Box>
            <Box width={8}>
              <Text dimColor>마지막</Text>
            </Box>
            <Box width={7}>
              <Text dimColor>최근</Text>
            </Box>
          </Box>
        ) : (
          <Text dimColor>  등록된 automation 없음</Text>
        )}
        {autos.map(autoRow)}
        <Text dimColor>  Orca automation 만 표시 (세션 CronCreate·launchd 는 수집 밖)</Text>
      </Box>
    )

    // Collapsed until asked for: the tree is the coordinator's long view, not what changes minute to minute.
    const isRelOpen = opened.includes('section:relations')
    const rel = tree(snap.sessions, new Set(snap.items.map(i => i.session ?? '')))
    const working = rel.lines.filter(l => l.session.phase === 'working').length
    const waiting = rel.lines.filter(l => l.session.phase === 'waiting').length
    const sessionLine = ({ session: s, prefix }: { session: OrchPanelSession; prefix: string }) => {
      const p = PHASE[s.phase]
      return (
        <Box key={`s-${s.id}`}>
          <Box flexGrow={1} flexShrink={1}>
            <Text wrap="truncate-end">
              <Text dimColor>{'  ' + prefix}</Text>
              <Text color={p.color} dimColor={!p.color}>
                {p.glyph}{' '}
              </Text>
              {s.name}
              {s.isMe ? <Text dimColor> (나)</Text> : ''}
              {s.task ? <Text dimColor> {s.task}</Text> : ''}
              {s.pr ? <Text dimColor={s.isPrMerged}> #{s.pr}{s.isPrMerged ? ' 머지됨' : ''}</Text> : ''}
            </Text>
          </Box>
          <Text color={p.color} dimColor={!p.color}>
            {' '}
            {p.word}
          </Text>
          <Link href={sessionUrl(s.id)} label=" ↗" />
        </Box>
      )
    }
    const relSection = !isRelOpen ? null : (
      <Box flexDirection="column">
        {rel.lines.map(sessionLine)}
        {rel.offline ? <Text dimColor>  + offline {rel.offline}</Text> : null}
        <Text dimColor>  공유 자원</Text>
        {snap.holds.length === 0 ? (
          <Text dimColor>    기록 없음 — 브라우저 같은 공유 자원은 orch hold &lt;자원&gt; 으로 잡는다</Text>
        ) : (
          snap.holds.map(hold => (
            <Box key={`h-${hold.kind}-${hold.resource}`}>
              <Box width={28}>
                <Text wrap="truncate-end">
                  {'    '}
                  {hold.kind === 'lane' ? `${hold.resource} 착지 lane` : hold.resource}
                </Text>
              </Box>
              <Box flexGrow={1} flexShrink={1}>
                <Text wrap="truncate-end">
                  <Text color="warning">■ </Text>
                  {hold.byName ?? hold.by ?? '?'}
                  {hold.note ? <Text dimColor> {hold.note}</Text> : ''}
                </Text>
              </Box>
              <Text color={ageMs(hold.at, now) > 3_600_000 ? 'warning' : undefined} dimColor={ageMs(hold.at, now) <= 3_600_000}>
                {' '}
                {age(hold.at, now)}
              </Text>
            </Box>
          ))
        )}
      </Box>
    )

    return (
      <Box flexDirection="column">
        <Box>
          <Box flexGrow={1}>
            <Text color={isStale ? 'warning' : undefined} dimColor={!isStale}>
              {isStale ? '▲ ' : ''}orch-dash {age(snap.generatedAt, now)} 전
            </Text>
            {isOrcaStale ? <Text color="warning"> ▲ Orca {age(snap.orca?.updatedAt ?? null, now)} 전</Text> : null}
          </Box>
          <Button key="refresh" label="↻" plain onPress={() => refresh($)} />
        </Box>
        <Button key="toggle-needs" plain onPress={toggle('needs')}>
          {isOpen ? '▾' : '▸'} 결정·사람만 <Text bold>{total}</Text>
          {isOpen ? '' : <Text dimColor> {summary}</Text>}
        </Button>
        {isOpen && decideItems.length > 0 ? <Text dimColor>  결정 대기</Text> : null}
        {isOpen ? decideItems.map((item, i) => row(item, i + 1)) : null}
        {isOpen && humanItems.length > 0 ? <Text dimColor>  사람만</Text> : null}
        {isOpen ? humanItems.map((item, i) => row(item, decideItems.length + i + 1)) : null}
        <Box>
          <Box flexGrow={1}>
            <Button key="toggle-work" plain onPress={toggle('work')}>
              {isWorkOpen ? '▾' : '▸'} 작업 진행 <Text color="success">{filled}</Text>
              <Text dimColor>{empty}</Text>
              {rowCount ? ` ${done}/${rowCount}` : <Text dimColor>— 기록 없음</Text>}
            </Button>
          </Box>
          {versions ? <Text dimColor>{versions}</Text> : null}
        </Box>
        {workSection}
        {isWorkOpen && work.untrackedPrs ? (
          <Link href={`${DASH_URL}/#/fleet/prs`} label={`  + 표에 없는 열린 PR ${work.untrackedPrs} ↗`} />
        ) : null}
        <Button key="toggle-automations" plain onPress={expand('section:automations')}>
          {isAutoOpen ? '▾' : '▸'} 자동화 {autos.length}
          {troubled.length || isAutoSourceBad ? (
            <Text color="warning"> · ▲ {troubled.length || '수집 실패'}</Text>
          ) : (
            <Text dimColor> · 문제 없음</Text>
          )}
          {upcoming ? <Text dimColor> · 다음 {upcoming.name} {until(upcoming.nextAt, now)}</Text> : ''}
        </Button>
        {autoSection}
        <Button key="toggle-relations" plain onPress={expand('section:relations')}>
          {isRelOpen ? '▾' : '▸'} 관계 세션 {rel.lines.length}
          {working ? <Text color="permission"> · ◉ 작업 중 {working}</Text> : ''}
          {waiting ? <Text color="warning"> · ◍ 대기 {waiting}</Text> : ''}
          {snap.holds.length ? <Text dimColor> · 자원 {snap.holds.length}</Text> : ''}
        </Button>
        {relSection}
      </Box>
    )
  })
}
