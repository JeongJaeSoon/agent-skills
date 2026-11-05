import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { BriefPaneView } from '../types'

const PANE = 'brief'
const COMMAND = 'brief-pane'
const PERIOD_MS = 30_000
const RENDER_TIMEOUT_MS = 10_000
// Same render, no collector pass and no state writes: see docs/platform.md.
const ARGS = ['brief', 'render', '--no-refresh']

// Bump the tag when BriefPaneView changes: it declines what an earlier
// version stored, which outlives a reload.
const view = atom(
  { plugin: 'agent-skills', key: 'brief-pane' } as const,
  { text: '', changedAt: 0, checkedAt: 0, error: '' } as BriefPaneView,
  { shape: 'brief-pane-1' },
)

// A reload drops these; session.start rebuilds the timer from the open pane.
let timer: { cancel: () => void } | undefined
let running = -1
// Bumped when the pane stops or is opened again: work started under an older
// value drops its result and cannot stop a newer timer.
let generation = 0

function clock(at: number): string {
  return at ? new Date(at).toTimeString().slice(0, 8) : '-'
}

function stop(): void {
  generation += 1
  timer?.cancel()
  timer = undefined
}

async function refresh($: EngineInterface): Promise<void> {
  const mine = generation
  // One run per generation, claimed before any await.
  if (running === mine) return
  running = mine
  try {
    // The pane can vanish without our ui.close hook (the engine unloads a
    // pane whose drawing threw): the pane list, not the hook, ends the timer.
    const isUp = await isOpen($)
    if (mine !== generation) return
    if (!isUp) return stop()
    let next: Partial<BriefPaneView>
    try {
      // The module's process has no plugin bin/ on PATH, so name orch by path.
      const run = await $.process.run([`${$.plugin.root}/bin/orch`, ...ARGS], {
        timeoutMs: RENDER_TIMEOUT_MS,
      })
      // Exit 1 with this stderr is a table wider than 78 columns: the
      // output is whole (the one exception to "non-zero is a failure").
      const isWide =
        run.exitCode === 1 &&
        run.stderr.trim().split('\n').every(line => line.includes('wider than 78 columns'))
      if ((run.exitCode !== 0 && !isWide) || run.isStdoutTruncated || run.stdout.trim() === '') {
        const detail = run.stderr.trim().split('\n').pop() ?? ''
        next = { error: `render failed (exit ${run.exitCode}) ${detail}`.slice(0, 160) }
      } else {
        next = { text: run.stdout, error: '', checkedAt: Date.now() }
      }
    } catch (error) {
      const why = error instanceof Error ? error.message : String(error)
      next = { error: `render failed: ${why}`.replace(/\s+/g, ' ').slice(0, 160) }
    }
    // A result that lands after the pane closed is dropped, also inside the
    // write: update() may retry after the pane closed. The run lock is held
    // through the write so a later tick cannot be overtaken by this one.
    if (mine !== generation) return
    await update($, view, old =>
      mine !== generation
        ? old
        : {
            ...old,
            ...next,
            changedAt: next.text !== undefined && next.text !== old.text ? Date.now() : old.changedAt,
          },
    )
  } finally {
    if (running === mine) running = -1
  }
}

async function isOpen($: EngineInterface): Promise<boolean> {
  return (await $.ui.panes()).some(pane => pane.id === PANE)
}

function start($: EngineInterface): void {
  if (timer) return
  timer = $.clock.every(PERIOD_MS, () => void refresh($))
  void refresh($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: COMMAND,
      description: 'Open the brief panel; it refreshes every 30 seconds while open',
    })
    // After a code reload the pane survives and the timer does not.
    if (await isOpen($)) start($)

    return next(e)
  })

  on('command.run', { command: COMMAND }, async $ => {
    await $.ui.open({ id: PANE, title: 'Brief' })
    // Opened again: a close still in flight must not stop this timer.
    generation += 1
    if (timer) void refresh($)
    else start($)

    return {
      text: 'Brief panel opened (refreshes every 30s while open). It docks beside the chat at 110+ columns; narrower terminals show it above the prompt.',
    }
  })

  on('ui.close', { id: PANE }, async ($, e, next) => {
    const mine = generation
    const closed = await next(e)
    // A hook above may have kept it open: trust the pane list, not the origin.
    const isUp = await isOpen($)
    if (!isUp && mine === generation) stop()

    return closed
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Markdown } = $.ui.resolve(e)
    const state = await read($, view)

    return (
      <Box flexDirection="column">
        <Text dimColor>
          {state.checkedAt ? `checked ${clock(state.checkedAt)}, changed ${clock(state.changedAt)}` : 'loading...'}
        </Text>
        {state.error !== '' && <Text color="red">{state.error} (retrying in 30s)</Text>}
        {state.text !== '' && <Markdown text={state.text} />}
      </Box>
    )
  })
}
