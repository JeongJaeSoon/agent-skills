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

async function bandText($: Parameters<Parameters<typeof test>[1]>[0]): Promise<string> {
  const ui = await $.ui.mount(BAND)
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
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  on('process.run', (_, e) => {
    argvs.push([...e.argv])
    return results.shift() ?? verdict({ show: false })
  })

  await $.turn.complete(turn('done'))
  await clock.settle()
  expect(argvs).toHaveLength(0)
  expect(await bandText($)).toBe('')

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

  await $.turn.complete(turn('which one should I land first?'))
  await clock.settle()
  expect(argvs[2]?.at(-1)).toBe('--asking')
  expect(await bandText($)).toContain('asks a question')

  session = 'sess-2'
  expect(await bandText($)).toBe('')
})
