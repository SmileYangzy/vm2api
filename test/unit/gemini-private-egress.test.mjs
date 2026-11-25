import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { createGeminiPanelHandler, geminiProxyFor } from '../../src/lib/gemini/routes.mjs'

test('the private guest exit is Gemini-only and public discovery omits all credentials', async (t) => {
  let secret = 'socks5h://user:secret@10.67.55.5:1080'
  let mode = 0o400
  t.mock.method(fs, 'openSync', () => 123)
  t.mock.method(fs, 'fstatSync', () => ({ isFile: () => true, uid: 0, mode, size: secret.length }))
  t.mock.method(fs, 'readFileSync', () => secret)
  t.mock.method(fs, 'closeSync', () => {})
  const pool = { snapshot: () => ({ proxies: [] }), getProxyByIdWithAuth: () => assert.fail('must not use the VM pool') }
  const handler = createGeminiPanelHandler({ json: (_res, status, body) => ({ status, body }), proxyPool: () => pool })
  const options = () => handler({ method: 'GET' }, { setHeader() {} }, '/api/panel/gemini/proxies')
  const result = await options()
  assert.equal(result.status, 200)
  const [entry] = result.body.data.proxies
  assert.equal(entry.id, 'gemini-private-egress')
  assert.equal(entry.status, 'unknown')
  assert.doesNotMatch(JSON.stringify(entry), /secret|10\.67\.55\.5|socks5h:/)
  assert.equal(geminiProxyFor(pool, entry.id), secret)
  assert.deepEqual(pool.snapshot().proxies, [])
  for (const [permissions, url] of [[0o644, secret], [0o400, 'socks5h://user:secret@127.0.0.1:1080']]) {
    mode = permissions
    secret = url
    assert.deepEqual((await options()).body.data.proxies, [])
    assert.throws(() => geminiProxyFor(pool, entry.id), { code: 'gemini_proxy_unavailable' })
  }
})
