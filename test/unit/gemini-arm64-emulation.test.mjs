import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { supportsGeminiRuntime } from '../../src/lib/gemini/payload.mjs'
import { createGeminiPanelHandler } from '../../src/lib/gemini/routes.mjs'

test('Gemini ARM64 is opt-in; native Linux x64 remains the default', () => {
  for (const flag of ['', '0', 'true', 'yes']) {
    assert.equal(supportsGeminiRuntime('linux', 'arm64', flag), false)
    assert.equal(supportsGeminiRuntime('linux', 'x64', flag), true)
  }
  assert.equal(supportsGeminiRuntime('linux', 'arm64', '1'), true)
  assert.equal(supportsGeminiRuntime('darwin', 'arm64', '1'), false)
  assert.equal(supportsGeminiRuntime('linux', 'riscv64', '1'), false)
})

test('ARM host preflight requires working isolation and the amd64 userspace', () => {
  execFileSync('python3', ['-B', fileURLToPath(new URL('./gemini-emulation-preflight.py', import.meta.url))], {
    stdio: 'pipe',
    timeout: 15_000,
  })
})

test('an old or unready ARM host cannot consume a voucher', { skip: process.platform !== 'linux' || process.arch !== 'arm64' }, async () => {
  for (const host of [{}, { payload_state: 'installed', amd64_emulation_ready: false }]) {
    let redemptionCalls = 0
    const handler = createGeminiPanelHandler({
      json: (_res, status, body) => ({ status, body }),
      readBody: () => assert.fail('must not read a voucher before the host is ready'),
      callHost: async (operation) => {
        assert.equal(operation, 'list')
        return host
      },
      cragSubscription: { redeem: () => { redemptionCalls += 1 } },
    })
    const result = await handler({ method: 'POST' }, { setHeader() {} }, '/api/panel/gemini/crag-subscription/redeem')
    assert.equal(result.status, 503)
    assert.equal(result.body.error.code, 'gemini_emulation_not_ready')
    assert.equal(redemptionCalls, 0)
  }
})
