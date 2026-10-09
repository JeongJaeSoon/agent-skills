import type {
  OrchPanelAutomation,
  OrchPanelCell,
  OrchPanelItem,
  OrchPanelItemKind,
  OrchPanelMark,
  OrchPanelPhase,
  OrchPanelPrChip,
  OrchPanelSession,
  OrchPanelSnapshot,
  OrchPanelStageRow,
  OrchPanelWork,
} from '../types'

// The same split as brief-status: what needs the owner's call, and what only a person at the keyboard can do.
// PR items (changes_requested, ci_failed, ...) belong to the progress table, sync items to the dashboard.
const KIND_OF: Record<string, OrchPanelItemKind> = {
  decision: 'decide',
  approval: 'decide',
  question: 'decide',
  prompt: 'human',
  permission: 'human',
  login: 'human',
  run_command: 'human',
}

export const DASH_URL = 'http://127.0.0.1:4780'
export const STALE_COLLECTOR_MS = 3 * 60_000
export const STALE_ORCA_MS = 90_000

type Raw = Record<string, unknown>

const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)

export function stateDir(env: { ORCH_FLEET_STATE?: string; HOME?: string }): string {
  const dir = env.ORCH_FLEET_STATE ?? '~/.local/state/agent-skills/dashboard'
  return dir.startsWith('~/') && env.HOME ? env.HOME + dir.slice(1) : dir
}

function recommendLabel(raw: Raw): string | null {
  const options = Array.isArray(raw.options) ? (raw.options as Raw[]) : []
  const n = typeof raw.recommend === 'number' ? raw.recommend : 0
  return str(options[n - 1]?.label)
}

export function snapshotOf(text: string, checkedAt: number, cwd: string, stagesText: string | null = null): OrchPanelSnapshot {
  let state: Raw
  try {
    state = JSON.parse(text)
  } catch (err) {
    return { status: 'unreadable', checkedAt, error: String(err).slice(0, 200) }
  }
  const sessions = new Map<string, string>()
  for (const s of (state.sessions as Raw[] | undefined) ?? []) {
    if (str(s.id)) sessions.set(s.id as string, str(s.name) ?? (s.id as string))
  }
  const items: OrchPanelItem[] = []
  for (const it of (state.items as Raw[] | undefined) ?? []) {
    const kind = KIND_OF[it.type as string]
    if (!kind || !str(it.key)) continue
    const session = str(it.session)
    items.push({
      key: it.key as string,
      kind,
      type: it.type as string,
      title: str(it.title) ?? '(제목 없음)',
      at: str(it.at),
      decision: str(it.decision),
      recommend: recommendLabel(it),
      session,
      sessionName: session ? (sessions.get(session) ?? null) : null,
    })
  }
  // Oldest first: the one waiting longest is the one most likely to be costing something.
  items.sort((a, b) => parseAt(a.at) - parseAt(b.at))
  const sources = (state.sources ?? {}) as Record<string, Raw | undefined>
  const sourceOf = (raw: Raw | undefined) => (raw ? { updatedAt: str(raw.updated_at), isOk: raw.ok !== false } : null)
  return {
    status: 'ok',
    checkedAt,
    generatedAt: str(state.generated_at),
    // The root id is `<id>::<worktree path>`: only the coordinator's own session gets toasts and an unasked pane.
    isCoordinator: str(state.root)?.endsWith(`::${cwd}`) ?? false,
    orca: sourceOf(sources.orca),
    items,
    work: workOf(stagesText, state),
    sessions: sessionsOf(state, cwd),
    automations: ((state.automations as Raw[] | undefined) ?? []).flatMap(a =>
      str(a.id)
        ? [{
            id: a.id as string,
            name: str(a.name) ?? (a.id as string),
            rrule: str(a.rrule),
            isEnabled: a.enabled !== false,
            nextAt: str(a.next_run_at),
            lastAt: str(a.last_run_at),
            recent: ((a.recent as Raw[] | undefined) ?? []).map(r => ({
              status: str(r.status) ?? 'unknown',
              at: str(r.at),
              summary: str(r.summary),
            })),
          }]
        : [],
    ),
    automationSource: sourceOf(sources.automations),
    holds: ((state.holds as Raw[] | undefined) ?? []).flatMap(raw =>
      str(raw.resource)
        ? [{
            kind: raw.kind === 'lane' ? ('lane' as const) : ('resource' as const),
            resource: raw.resource as string,
            by: str(raw.by),
            byName: sessions.get(str(raw.session) ?? '') ?? null,
            note: str(raw.note),
            at: str(raw.at),
          }]
        : [],
    ),
  }
}

const PHASES: readonly OrchPanelPhase[] = ['working', 'waiting', 'idle', 'open', 'offline']

function sessionsOf(state: Raw, cwd: string): OrchPanelSession[] {
  return ((state.sessions as Raw[] | undefined) ?? []).flatMap(s => {
    if (!str(s.id)) return []
    const pr = s.linked_pr as Raw | null | undefined
    return [{
      id: s.id as string,
      name: str(s.name) ?? (s.id as string),
      kind: str(s.kind) ?? 'task',
      phase: PHASES.find(p => p === s.phase) ?? 'offline',
      parent: str(s.parent),
      task: str(s.task_title),
      pr: typeof pr?.number === 'number' ? pr.number : null,
      isPrMerged: pr?.state === 'merged',
      isMe: (s.id as string).endsWith(`::${cwd}`),
    }]
  })
}

// The relations tree as drawn: offline sessions leave it (counted apart) unless something waits on them;
// each line carries the prefix (`├─ `, `│  └─ `) its depth and place give it.
export function tree(
  sessions: OrchPanelSession[],
  busy: Set<string>,
): { lines: { session: OrchPanelSession; prefix: string }[]; offline: number } {
  const shown = sessions.filter(s => s.phase !== 'offline' || busy.has(s.id))
  const ids = new Set(shown.map(s => s.id))
  const lines: { session: OrchPanelSession; prefix: string }[] = []
  const walk = (parent: string | null, indent: string, seen: Set<string>) => {
    const kids = shown.filter(s => (parent === null ? !s.parent || !ids.has(s.parent) : s.parent === parent))
    kids.forEach((s, i) => {
      if (seen.has(s.id)) return
      const isLast = i === kids.length - 1
      lines.push({ session: s, prefix: parent === null ? '' : indent + (isLast ? '└─ ' : '├─ ') })
      walk(s.id, parent === null ? '' : indent + (isLast ? '   ' : '│  '), new Set([...seen, s.id]))
    })
  }
  walk(null, '', new Set())
  return { lines, offline: sessions.length - shown.length }
}

const MARKS: readonly OrchPanelMark[] = ['ok', 'fail', 'partial', 'checking', 'na']

function cellOf(raw: unknown): OrchPanelCell | null {
  const c = raw as Raw | undefined
  const mark = MARKS.find(m => m === c?.mark)
  return mark ? { mark, evidence: str(c?.evidence), by: str(c?.by) } : null
}

// The same reading as stages.py merge_cell: the merge column is never written, only read off the PR list.
function mergeOf(pr: Raw): OrchPanelCell {
  if (str(pr.merged_at) || pr.state === 'MERGED') {
    const at = new Date(parseAt(str(pr.merged_at)))
    return { mark: 'ok', evidence: at.getTime() ? `${at.getUTCMonth() + 1}/${at.getUTCDate()}` : '머지됨', by: null }
  }
  if (pr.state === 'CLOSED') return { mark: 'fail', evidence: '닫힘', by: null }
  const why =
    pr.ci === 'failure' ? 'CI 실패'
    : pr.decision === 'CHANGES_REQUESTED' ? '변경 요청'
    : pr.draft ? 'draft'
    : '리뷰 대기'
  return { mark: 'fail', evidence: why, by: null }
}

function chipOf(pr: Raw): OrchPanelPrChip | null {
  if (pr.state !== 'OPEN') return null
  if (pr.ci === 'failure') return { text: '✗CI', tone: 'error' }
  if (pr.decision === 'CHANGES_REQUESTED') return { text: '✎', tone: 'warning' }
  if (pr.ci === 'pending') return { text: '◐CI', tone: 'warning' }
  if (pr.merge_state === 'CLEAN') return { text: '✓', tone: 'success' }
  return null
}

export function workOf(stagesText: string | null, state: Raw): OrchPanelWork {
  const prs = new Map<string, Raw>()
  for (const p of (state.prs as Raw[] | undefined) ?? []) if (str(p.key)) prs.set(p.key as string, p)
  let store: Raw = {}
  let error: string | null = null
  try {
    store = stagesText ? JSON.parse(stagesText) : {}
  } catch (err) {
    error = String(err).slice(0, 200)
  }
  const byId = (store.rows ?? {}) as Record<string, Raw>
  const order = ((store.order as string[] | undefined) ?? []).filter(id => byId[id])
  const toRow = (id: string): OrchPanelStageRow => {
    const r = byId[id] as Raw
    const cells = (r.cells ?? {}) as Raw
    const pr = str(r.pr)
    const live = pr ? prs.get(pr) : undefined
    return {
      id,
      title: str(r.title) ?? id,
      pr,
      isChild: !!str(r.parent),
      isDone: !!str(r.done_at),
      merge: live ? mergeOf(live) : cellOf(cells.merge),
      dev: cellOf(cells.dev),
      prod: cellOf(cells.prod),
      chip: live ? chipOf(live) : null,
    }
  }
  // Each parent followed by its children, in the order they were added: `orch stage show`'s order.
  const rows = order
    .filter(id => !str(byId[id]?.parent))
    .flatMap(id => [toRow(id), ...order.filter(k => byId[k]?.parent === id).map(toRow)])
  const tracked = new Set(rows.map(r => r.pr))
  const versions = (store['env'] ?? {}) as Raw
  return {
    rows,
    versions: { prod: str(versions.prod), dev: str(versions.dev) },
    next: ((store.next as unknown[] | undefined) ?? []).filter((n): n is string => !!str(n)),
    untrackedPrs: [...prs.values()].filter(p => p.state === 'OPEN' && !tracked.has(p.key as string)).length,
    error,
  }
}

// A row is through when it is marked done or every stage it has is verified.
export function progress(work: OrchPanelWork): { done: number; total: number } {
  const through = (r: OrchPanelStageRow) =>
    r.isDone || [r.merge, r.dev, r.prod].every(c => c?.mark === 'ok' || c?.mark === 'na')
  return { done: work.rows.filter(through).length, total: work.rows.length }
}

// `▰▰▰▱▱`: one cell per row up to ten, scaled past that.
export function bar(done: number, total: number): { filled: string; empty: string } {
  const width = Math.min(total, 10)
  const filled = total ? Math.round((done / total) * width) : 0
  return { filled: '▰'.repeat(filled), empty: '▱'.repeat(width - filled) }
}

export function counts(snapshot: OrchPanelSnapshot | null): { decide: number; human: number } {
  const items = snapshot?.status === 'ok' ? snapshot.items : []
  return {
    decide: items.filter(i => i.kind === 'decide').length,
    human: items.filter(i => i.kind === 'human').length,
  }
}

// Plain text by necessity: the status line takes no color. Zero counts are left out, all zero clears it.
export function statusText(snapshot: OrchPanelSnapshot | null): string | undefined {
  if (snapshot?.status !== 'ok') return undefined
  const { decide, human } = counts(snapshot)
  const troubled = snapshot.automations.filter(a => automationIssue(a, snapshot.checkedAt)).length
  const parts = [decide && `결정 ${decide}`, human && `사람만 ${human}`, troubled && `자동화 ▲${troubled}`].filter(Boolean)
  return parts.length ? parts.join(' · ') : undefined
}

// The collector writes most times as `...Z`, but some sources as `+0900`, which Date.parse need not accept.
export function parseAt(at: string | null): number {
  return Date.parse((at ?? '').replace(/([+-]\d\d)(\d\d)$/, '$1:$2')) || 0
}

export function age(fromIso: string | null, now: number): string {
  const t = parseAt(fromIso)
  if (!t) return '?'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

export function ageMs(fromIso: string | null, now: number): number {
  const t = parseAt(fromIso)
  return t ? now - t : Infinity
}

export function toastText(item: OrchPanelItem): string {
  const head = item.kind === 'decide' ? '결정 대기' : '사람만'
  return `${head}${item.decision ? ` ${item.decision}` : ''}: ${item.title}`
}

export function sessionUrl(session: string): string {
  return `${DASH_URL}/#/fleet/session/${encodeURIComponent(session)}`
}

const STALL_MS = 10 * 60_000

// What is wrong with an automation, if anything: its last run failed, or its next run is overdue (the scheduler
// stopped, or the app was closed). A switched-off automation is not a problem.
export function automationIssue(a: OrchPanelAutomation, now: number): string | null {
  if (!a.isEnabled) return null
  if (a.recent[0] && /fail|error/.test(a.recent[0].status)) return '마지막 실행 실패'
  if (a.nextAt && now - parseAt(a.nextAt) > STALL_MS) return `예정 ${age(a.nextAt, now)} 지남`
  return null
}

// The few cron shapes automations use, in words; anything else stays as written.
export function cadence(rrule: string | null): string {
  if (!rrule) return '?'
  const m = (re: RegExp) => rrule.trim().match(re)
  let g = m(/^(\d+) \*\/(\d+) \* \* \*$/)
  if (g) return `${g[2]}시간마다 :${g[1]?.padStart(2, '0')}`
  g = m(/^\*\/(\d+) (\d+)-(\d+) \* \* 1-5$/)
  if (g) return `평일 ${g[2]}–${g[3]}시 ${g[1]}분마다`
  g = m(/^\*\/(\d+) \* \* \* \*$/)
  if (g) return `${g[1]}분마다`
  g = m(/^(\d+) (\d+) \* \* \*$/)
  if (g) return `매일 ${g[2]}:${g[1]?.padStart(2, '0')}`
  return rrule
}

export function until(iso: string | null, now: number): string {
  const t = parseAt(iso)
  if (!t) return '–'
  return t >= now ? `${age(new Date(now).toISOString(), t)} 후` : `${age(iso, now)} 지남`
}
