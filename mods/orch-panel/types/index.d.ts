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
    }

declare module 'claude-code' {
  interface PluginState {
    'orch-panel': {
      snapshot: OrchPanelSnapshot | null
      seen: string[] | null
      collapsed: string[]
    }
  }
}
