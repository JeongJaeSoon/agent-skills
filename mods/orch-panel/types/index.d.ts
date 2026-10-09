// Kept small on purpose: the session holds only what the panes draw, never the collector's whole state.json.

export type OrchPanelItemKind = 'decide' | 'human'

export type OrchPanelItem = {
  key: string
  kind: OrchPanelItemKind
  type: string
  title: string
  at: string | null
  decision: string | null
  recommend: string | null
  session: string | null
  sessionName: string | null
}

export type OrchPanelSource = { updatedAt: string | null; isOk: boolean }

export type OrchPanelMark = 'ok' | 'fail' | 'partial' | 'checking' | 'na'

export type OrchPanelCell = { mark: OrchPanelMark; evidence: string | null; by: string | null }

// A PR's live state beside its key: what holds it, or that it is ready to merge.
export type OrchPanelPrChip = { text: string; tone: 'success' | 'warning' | 'error' }

export type OrchPanelStageRow = {
  id: string
  title: string
  pr: string | null
  isChild: boolean
  isDone: boolean
  merge: OrchPanelCell | null
  dev: OrchPanelCell | null
  prod: OrchPanelCell | null
  chip: OrchPanelPrChip | null
}

// The `orch stage` record (stages.json) with its merge cells filled from state.json's PR list, as `orch stage show`.
export type OrchPanelWork = {
  rows: OrchPanelStageRow[]
  versions: { prod: string | null; dev: string | null }
  next: string[]
  untrackedPrs: number
  error: string | null
}

export type OrchPanelPhase = 'working' | 'waiting' | 'idle' | 'open' | 'offline'

// One Orca session of the fleet tree: what the relations section draws, nothing more.
export type OrchPanelSession = {
  id: string
  name: string
  kind: string
  phase: OrchPanelPhase
  parent: string | null
  task: string | null
  pr: number | null
  isPrMerged: boolean
  isMe: boolean
}

// A landing lane's lock or a resource taken with `orch hold`, as the collector listed it.
export type OrchPanelHold = {
  kind: 'lane' | 'resource'
  resource: string
  by: string | null
  byName: string | null
  note: string | null
  at: string | null
}

export type OrchPanelSnapshot =
  | { status: 'missing'; checkedAt: number }
  | { status: 'unreadable'; checkedAt: number; error: string }
  | {
      status: 'ok'
      checkedAt: number
      generatedAt: string | null
      isCoordinator: boolean
      orca: OrchPanelSource | null
      items: OrchPanelItem[]
      work: OrchPanelWork
      sessions: OrchPanelSession[]
      holds: OrchPanelHold[]
    }

declare module 'claude-code' {
  interface PluginState {
    'orch-panel': {
      snapshot: OrchPanelSnapshot | null
      seen: string[] | null
      collapsed: string[]
      expanded: string[]
    }
  }
}
