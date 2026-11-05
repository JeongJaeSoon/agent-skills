import { expect, mock, test } from 'claude-code/testing'

const PANE = {
  plugin: 'agent-skills',
  component: 'Pane',
  requestId: 'brief',
  props: {
    title: 'Brief',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
  surface: 'terminal',
} as const

const out = (stdout: string, exitCode = 0, stderr = '') => ({
  value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false },
})

// One flow in one test: the module's timer is a module variable, so a second
// test would inherit the first one's. The `ui.close` hook itself is checked
// in a live session; here the pane list going empty stands for a close.
test('refreshes every 30s, keeps the last good text on failure, accepts a wide-table exit 1', async ($, on) => {
  const clock = mock.clock(on)
  const argvs: string[][] = []
  const results = [
    out('## 지금 오너가 할 일\n- one'),
    out('', 2, 'boom'),
    out('wide', 1, 'a table is wider than 78 columns'),
  ]
  on('command.register', () => ({ value: { command: 'brief-pane' } }))
  let isOpen = false
  on('ui.open', () => {
    isOpen = true
    return { value: { isPlaced: true } }
  })
  on('ui.panes', () => ({
    value: isOpen ? [{ id: 'brief', title: 'Brief', isShown: true, isFocused: false, isPlaced: true }] : [],
  }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('process.run', (_, e) => {
    argvs.push([...e.argv])
    return results.shift() ?? out('late')
  })

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'brief-pane', args: '' })
  await clock.settle()
  expect(argvs).toHaveLength(1)
  expect(argvs[0][0]).toMatch(/\/bin\/orch$/)
  expect(argvs[0].slice(1)).toEqual(['brief', 'render', '--no-refresh'])

  let ui = await $.ui.mount(PANE)
  expect((await ui.find({ type: 'Markdown' }))?.text).toBe('## 지금 오너가 할 일\n- one')
  await ui.unmount()

  await clock.advance(30_000)
  expect(argvs).toHaveLength(2)
  ui = await $.ui.mount(PANE)
  expect((await ui.find({ type: 'Markdown' }))?.text).toBe('## 지금 오너가 할 일\n- one')
  expect((await ui.find({ type: 'Text', text: /render failed \(exit 2\) boom/ }))?.text).toContain('retrying')
  await ui.unmount()

  await clock.advance(30_000)
  ui = await $.ui.mount(PANE)
  expect((await ui.find({ type: 'Markdown' }))?.text).toBe('wide')
  expect(await ui.find({ type: 'Text', text: /render failed/ })).toBeUndefined()
  await ui.unmount()

  // The pane is gone (closed, or unloaded by the engine): the next tick ends
  // the timer without running the render.
  isOpen = false
  await clock.advance(30_000)
  await clock.advance(90_000)
  expect(argvs).toHaveLength(3)
})
