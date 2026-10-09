import type { OrchPanelItem, OrchPanelItemKind, OrchPanelSnapshot } from '../types'

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

export function snapshotOf(text: string, checkedAt: number, cwd: string): OrchPanelSnapshot {
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
  items.sort((a, b) => (Date.parse(a.at ?? '') || 0) - (Date.parse(b.at ?? '') || 0))
  const orca = (state.sources as Raw | undefined)?.orca as Raw | undefined
  return {
    status: 'ok',
    checkedAt,
    generatedAt: str(state.generated_at),
    // The root id is `<id>::<worktree path>`: only the coordinator's own session gets toasts and an unasked pane.
    isCoordinator: str(state.root)?.endsWith(`::${cwd}`) ?? false,
    orca: orca ? { updatedAt: str(orca.updated_at), isOk: orca.ok !== false } : null,
    items,
  }
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
  const parts = [decide && `결정 ${decide}`, human && `사람만 ${human}`].filter(Boolean)
  return parts.length ? parts.join(' · ') : undefined
}

export function age(fromIso: string | null, now: number): string {
  const t = Date.parse(fromIso ?? '')
  if (!t) return '?'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86400)}d`
}

export function ageMs(fromIso: string | null, now: number): number {
  const t = Date.parse(fromIso ?? '')
  return t ? now - t : Infinity
}

export function toastText(item: OrchPanelItem): string {
  const head = item.kind === 'decide' ? '결정 대기' : '사람만'
  return `${head}${item.decision ? ` ${item.decision}` : ''}: ${item.title}`
}

export function sessionUrl(session: string): string {
  return `${DASH_URL}/#/fleet/session/${encodeURIComponent(session)}`
}
