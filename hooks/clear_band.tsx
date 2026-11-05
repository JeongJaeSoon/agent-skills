import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ClearBandView } from '../types'

// No restart_at worth setting is this low: below it the script is not run at all.
const FLOOR = 50_000
// While shown, the inbox is looked at again this often: mail can arrive after the turn ended.
const PERIOD_MS = 60_000
const READY_TIMEOUT_MS = 20_000

// Bump the tag when ClearBandView changes: it declines what an earlier version stored.
const view = atom(
  { plugin: 'agent-skills', key: 'clear-band' } as const,
  null as ClearBandView | null,
  { shape: 'clear-band-1' },
)

// A reload drops these; the next turn or session.start sets them again.
let timer: { cancel: () => void } | undefined
let asking = false
let running = false

function stop(): void {
  timer?.cancel()
  timer = undefined
}

async function check($: EngineInterface): Promise<void> {
  if (running) return
  running = true
  try {
    const tokens = (await $.session.usage()).context.tokens ?? 0
    if (tokens < FLOOR) {
      stop()
      await update($, view, () => null)
      return
    }
    const session = await $.session.id()
    // A subagent's result would land in the cleared session, which knows nothing of it.
    const agents = (await $.agent.list()).filter(a => a.status === 'running').length
    // The module's process has no plugin bin/ on PATH, so name orch by path.
    const args = ['handoff', 'ready', '--session', session, '--context', String(tokens), '--agents', String(agents)]
    const run = await $.process.run([`${$.plugin.root}/bin/orch`, ...args, ...(asking ? ['--asking'] : [])], {
      timeoutMs: READY_TIMEOUT_MS,
    })
    const line = run.stdout.trim().split('\n').pop() ?? ''
    let got: ClearBandView
    try {
      got = { ...JSON.parse(line), session, error: '' }
    } catch {
      const detail = (run.stderr.trim().split('\n').pop() ?? '').slice(0, 120)
      const error = `check failed (exit ${run.exitCode}) ${detail}`
      got = { show: true, ready: false, context: tokens, threshold: 0, reason: '', session, error }
    }
    await update($, view, () => got)
    if (got.show && !timer) timer = $.clock.every(PERIOD_MS, () => void check($))
    if (!got.show) stop()
  } catch (error) {
    const why = error instanceof Error ? error.message : String(error)
    await update($, view, old => (old ? { ...old, error: `check failed: ${why}`.slice(0, 160) } : old))
  } finally {
    running = false
  }
}

function k(n: number): string {
  return `${Math.round(n / 1000)}k`
}

export const register: Register = on => {
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId === undefined) {
      asking = /[?？]\s*$/.test(e.answer)
      void check($)
    }

    return done
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const state = await read($, view)
    // After a /clear the session id is new: what the band said belonged to the old session.
    if (!state || !state.show || state.session !== (await $.session.id())) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    if (state.error !== '') {
      return (
        <Box>
          <Text dimColor>context {k(state.context)} · clear check: {state.error}</Text>
        </Box>
      )
    }

    return (
      <Box>
        {state.ready ? (
          <Text color="yellow">
            context {k(state.context)} · /clear is safe · handoff saved → /clear recommended
          </Text>
        ) : (
          <Text dimColor>
            context {k(state.context)} &gt; {k(state.threshold)} · /clear not yet: {state.reason}
          </Text>
        )}
      </Box>
    )
  })
}
