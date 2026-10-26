import { afterEach, expect, test, vi } from 'vitest'
vi.mock('../lib/supabaseClient', () => ({ supabase: { auth: { getSession: vi.fn(async () => ({data:{session:{access_token:'fixture'}}})) } } }))
import { api } from '../lib/api'
afterEach(() => vi.unstubAllGlobals())
test('a successful non-JSON proxy response produces an actionable error', async () => {
 vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Wrong backend</html>', {status:200})))
 await expect(api('/applications')).rejects.toMatchObject({status:502})
})
test('requests have a response deadline and preserve caller cancellation', async () => {
 const controller = new AbortController()
 const fetchMock=vi.fn(async (_url, options) => { expect(options.signal).toBeInstanceOf(AbortSignal); return new Response('[]',{status:200}) })
 vi.stubGlobal('fetch', fetchMock)
 await expect(api('/applications',{signal:controller.signal})).resolves.toEqual([])
 expect(fetchMock.mock.calls[0][1].signal).not.toBe(controller.signal)
})
