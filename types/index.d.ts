export type BriefPaneView = {
  // Raw `orch brief render` output of the last good run; never parsed.
  text: string
  // When `text` last differed from the run before it.
  changedAt: number
  // When a run last succeeded, even if `text` did not change.
  checkedAt: number
  // One line about the latest failed run; empty after a success.
  error: string
}

declare module 'claude-code' {
  interface PluginState {
    'agent-skills': { 'brief-pane': Shaped<BriefPaneView> }
  }
}
