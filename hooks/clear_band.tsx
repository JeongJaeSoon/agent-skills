import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { ClearBandView } from '../types'

const PERIOD_MS = 60_000
const READY_TIMEOUT_MS = 20_000
// A session `orch handoff ready` said has no orchestrator key (a lead, a worker, a general one) is not asked again
// for this long; a new session id (a /clear) is asked at once.
const NOT_ORCHESTRATOR_MS = 600_000

// Bump the tag when ClearBandView changes: it declines what an earlier version stored.
const view = atom(
  { plugin: 'agent-skills', key: 'clear-band' } as const,
  null as ClearBandView | null,
  { shape: 'clear-band-2' },
)

let timer: { cancel: () => void } | undefined
let skipped: { session: string; until: number } | undefined
let asking = false
let running = false
let working = false
// Bumped at each turn start and end: a check begun before one is about a turn that no longer stands.
let turn = 0
let again = false

function stop(): void {
  timer?.cancel()
  timer = undefined
}

async function check($: EngineInterface): Promise<void> {
  if (working) return
  if (running) {
    again = true
    return
  }
  running = true
  const began = turn
  try {
    const tokens = (await $.session.usage()).context.tokens ?? 0
    const session = await $.session.id()
    if (skipped?.session === session && Date.now() < skipped.until) {
      stop()
      await update($, view, () => null)
      return
    }
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
      got = { show: true, ready: false, context: tokens, due: '', key: '', reason: '', session, error }
    }
    if (got.error === '' && got.key !== 'orchestrator') skipped = { session, until: Date.now() + NOT_ORCHESTRATOR_MS }
    if (began !== turn) {
      again = true
      return
    }
    await update($, view, () => got)
    if (got.show && !timer) timer = $.clock.every(PERIOD_MS, () => void check($))
    if (!got.show) stop()
  } catch (error) {
    const why = `check failed: ${error instanceof Error ? error.message : String(error)}`.slice(0, 160)
    const session = await $.session.id().catch(() => '')
    const blank = { show: true, ready: false, context: 0, due: '', key: '', reason: '', session }
    await update($, view, old => ({
      ...(old && old.session === session ? old : blank),
      show: true,
      ready: false,
      session,
      error: why,
    }))
    // The timer is the retry: without it a first failure would leave the band blank until the next turn.
    if (!timer) timer = $.clock.every(PERIOD_MS, () => void check($))
  } finally {
    running = false
    if (again && !working) {
      again = false
      void check($)
    }
  }
}

function k(n: number): string {
  return `${Math.round(n / 1000)}k`
}

export const register: Register = on => {
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      working = true
      turn += 1
      // A turn may take mail or start subagents: the verdict from before it stops counting.
      await update($, view, old => (old ? { ...old, ready: false } : old))
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId === undefined) {
      working = false
      turn += 1
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
      line = (
        <Text dimColor>
          context {k(state.context)} · {state.due} · /clear not while a turn runs
        </Text>
      )
    } else if (state.ready) {
      line = (
        <Text color="yellow">
          context {k(state.context)} · {state.due} · /clear is safe · handoff saved → /clear recommended
        </Text>
      )
    } else {
      line = (
        <Text dimColor>
          context {k(state.context)} · {state.due} · /clear not yet: {state.reason}
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
