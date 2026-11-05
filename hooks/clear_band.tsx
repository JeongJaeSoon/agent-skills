import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ClearBandView } from '../types'

const FLOOR = 50_000
const PERIOD_MS = 60_000
const READY_TIMEOUT_MS = 20_000

// Bump the tag when ClearBandView changes: it declines what an earlier version stored.
const view = atom(
  { plugin: 'agent-skills', key: 'clear-band' } as const,
  null as ClearBandView | null,
  { shape: 'clear-band-1' },
)

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
    const why = `check failed: ${error instanceof Error ? error.message : String(error)}`.slice(0, 160)
    const session = await $.session.id().catch(() => '')
    const blank = { show: true, ready: false, context: 0, threshold: 0, reason: '', session }
    await update($, view, old => ({ ...(old ?? blank), ready: false, error: why }))
    // The timer is the retry: without it a first failure would leave the band blank until the next turn.
    if (!timer) timer = $.clock.every(PERIOD_MS, () => void check($))
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
    let line
    if (state.error !== '') {
      line = <Text dimColor>clear check: {state.error}</Text>
    } else if (e.props.isWorking) {
      // A verdict from before this turn says nothing about the work it started.
      line = <Text dimColor>context {k(state.context)} · /clear not while a turn runs</Text>
    } else if (state.ready) {
      line = <Text color="yellow">context {k(state.context)} · /clear is safe · handoff saved → /clear recommended</Text>
    } else {
      line = (
        <Text dimColor>
          context {k(state.context)} &gt; {k(state.threshold)} · /clear not yet: {state.reason}
        </Text>
      )
    }

    return (
      <Box flexDirection="column">
        {line}
        {await next(e)}
      </Box>
    )
  })
}
