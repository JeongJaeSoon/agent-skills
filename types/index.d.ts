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

// The verdict of `orch handoff ready` (skills/orchestrate/scripts/handoff.py).
export type ClearBandView = {
  // An orchestrator session whose restart is due (compactions.due); leads and role-less sessions never show it.
  show: boolean
  // Safe to /clear now: inbox empty, no question asked, no subagent running, handoff saved.
  ready: boolean
  context: number
  // Why a restart is due, such as "3 auto-compactions"; empty when it is not.
  due: string
  // The session's handoff key, null for a session with no orchestrator or lead role. A lead's is not asked again for
  // ten minutes; one with no role is asked at each turn, since it may declare a role later.
  key: string | null
  // Why not ready yet; empty when ready.
  reason: string
  // The session the verdict is for: a /clear starts a new one in the same process.
  session: string
  // One line about the latest failed check; empty after a success.
  error: string
}

declare module 'claude-code' {
  interface PluginState {
    'agent-skills': { 'brief-pane': Shaped<BriefPaneView>; 'clear-band': Shaped<ClearBandView | null> }
  }
}
