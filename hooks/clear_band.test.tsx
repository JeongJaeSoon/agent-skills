import { expect, mock, test } from 'claude-code/testing'

const BAND = {
  plugin: 'agent-skills',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 80,
    scroll: { offset: 0, bodyRows: 5 },
    view: {},
  },
  surface: 'terminal',
} as const

const out = (stdout: string, exitCode = 0, stderr = '') => ({
  value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false },
})

const turn = (answer: string) => ({
  answer,
  durationMs: 1000,
  isAborted: false,
  turnId: 't1',
  reason: 'answer' as const,
})

const verdict = (v: object) =>
  out(JSON.stringify({ show: true, ready: false, context: 150_000, threshold: 130_000, reason: '', ...v }) + '\n')

async function bandText($: Parameters<Parameters<typeof test>[1]>[0], isWorking = false): Promise<string> {
  const ui = await $.ui.mount({ ...BAND, props: { ...BAND.props, isWorking } })
  const text = (await ui.findAll({ type: 'Text' })).map(t => t.text).join(' ')
  await ui.unmount()
  return text
}

test('asks orch only past the floor, says when /clear is safe, rechecks every minute, hides after a /clear', async ($, on) => {
  const clock = mock.clock(on)
  let tokens: number | undefined = 40_000
  let session = 'sess-1'
  const argvs: string[][] = []
  const results = [
    verdict({ reason: '2 message(s) in the inbox' }),
    verdict({ ready: true, reason: 'handoff saved' }),
    verdict({ reason: 'your last answer asks a question' }),
  ]
  on('session.usage', () => ({ value: { startedAt: 0, context: { tokens, window: 200_000 }, rateLimits: {}, cost: {} } }))
  on('session.id', () => ({ value: session }))
  let agents = [{ id: 'a1', description: 'reboot check', type: 'general-purpose', status: 'running' }]
  on('agent.list', () => ({ value: agents }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  // What the engine draws there itself stays under the band.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  let fail = false
  on('process.run', (_, e) => {
    if (fail) throw new Error('spawn refused')
    argvs.push([...e.argv])
    return results.shift() ?? verdict({ show: false })
  })

  await $.turn.complete(turn('done'))
  await clock.settle()
  expect(argvs).toHaveLength(0)
  expect(await bandText($)).toBe('engine')

  tokens = 150_000
  await $.turn.complete(turn('done'))
  await clock.settle()
  expect(argvs[0]?.[0]).toMatch(/\/bin\/orch$/)
  expect(argvs[0]?.slice(1)).toEqual(['handoff', 'ready', '--session', 'sess-1', '--context', '150000', '--agents', '1'])
  expect(await bandText($)).toContain('/clear not yet: 2 message(s) in the inbox')
  agents = []

  await clock.advance(60_000)
  expect(argvs).toHaveLength(2)
  expect(await bandText($)).toContain('/clear is safe')
  expect(await bandText($)).toContain('engine')
  // While a turn runs, the verdict from before it is not shown as safe.
  expect(await bandText($, true)).not.toContain('/clear is safe')

  await $.turn.complete(turn('which one should I land first?'))
  await clock.settle()
  expect(argvs[2]?.at(-1)).toBe('--asking')
  expect(await bandText($)).toContain('asks a question')

  // A check that throws is shown, not a silent blank, and the minute timer tries again.
  fail = true
  await $.turn.complete(turn('done'))
  await clock.settle()
  expect(await bandText($)).toContain('clear check: check failed:')
  expect(await bandText($)).not.toContain('/clear is safe')
  fail = false
  await clock.advance(60_000)
  expect(argvs).toHaveLength(4)

  session = 'sess-2'
  expect(await bandText($)).toBe('engine')
})

test('drops a verdict begun before a turn, and shows a failure after a hidden state', async ($, on) => {
  const clock = mock.clock(on)
  let tokens = 150_000
  let release: (v: ReturnType<typeof verdict>) => void = () => {}
  const queue: Array<ReturnType<typeof verdict> | Promise<ReturnType<typeof verdict>> | Error> = []
  const argvs: string[][] = []
  on('session.usage', () => ({ value: { startedAt: 0, context: { tokens, window: 200_000 }, rateLimits: {}, cost: {} } }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('agent.list', () => ({ value: [] }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  on('process.run', async (_, e) => {
    argvs.push([...e.argv])
    const next = queue.shift() ?? verdict({ show: false })
    if (next instanceof Error) throw next
    return next
  })

  queue.push(verdict({ reason: '1 message(s) in the inbox' }))
  await $.turn.complete(turn('done'))
  await clock.settle()

  // The minute check is still waiting on orch when a turn starts and ends on a question.
  const asks = verdict({ reason: 'your last answer asks a question' })
  let releaseRerun: (v: ReturnType<typeof verdict>) => void = () => {}
  queue.push(new Promise(r => (release = r)), new Promise(r => (releaseRerun = r)), asks)
  const pending = clock.advance(60_000)
  await clock.settle()
  await $.turn.start({ text: '', turnId: 't2' })
  await $.turn.complete({ ...turn('which one first?'), turnId: 't2' })
  release(verdict({ ready: true, reason: 'handoff saved' }))
  await clock.settle()
  // The rerun is still out: what the band shows meanwhile is not the verdict from before the turn.
  expect(await bandText($)).not.toContain('/clear is safe')
  releaseRerun(asks)
  await pending
  await clock.settle()
  expect(argvs.slice(0, 3).map(a => a.at(-1))).toEqual(['0', '0', '--asking'])
  expect(await bandText($)).not.toContain('/clear is safe')
  expect(await bandText($)).toContain('asks a question')

  // Below the threshold the band hides; a failure once past it must still show.
  queue.length = 0
  queue.push(verdict({ show: false }), new Error('spawn refused'))
  await $.turn.complete(turn('done'))
  await clock.settle()
  expect(await bandText($)).toBe('engine')
  tokens = 160_000
  await $.turn.complete(turn('done'))
  await clock.settle()
  expect(await bandText($)).toContain('clear check: check failed:')
})
