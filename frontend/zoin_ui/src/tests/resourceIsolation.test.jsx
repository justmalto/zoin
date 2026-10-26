// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useResource } from '../lib/useResource'
const mocks=vi.hoisted(()=>({api:vi.fn(),user:'owner-a'}))
vi.mock('../lib/api',()=>({api:mocks.api}))
vi.mock('../lib/session',()=>({useSession:()=>({user:{id:mocks.user}})}))
afterEach(()=>{cleanup();mocks.api.mockReset();mocks.user='owner-a'})
test('changing job path hides old job before the next request resolves',async()=>{
 let finish
 mocks.api.mockImplementation(path=>path==='/jobs/a'?Promise.resolve({id:'a'}):new Promise(resolve=>{finish=resolve}))
 const {result,rerender}=renderHook(({path})=>useResource(path),{initialProps:{path:'/jobs/a'}})
 await waitFor(()=>expect(result.current.data?.id).toBe('a'))
 rerender({path:'/jobs/b'})
 expect(result.current.data).toBeNull();expect(result.current.loading).toBe(true)
 await act(async()=>finish({id:'b'}));expect(result.current.data.id).toBe('b')
})
test('clearing a resource path clears its previous result',async()=>{
 mocks.api.mockResolvedValue({id:'a'})
 const {result,rerender}=renderHook(({path})=>useResource(path),{initialProps:{path:'/jobs/a'}})
 await waitFor(()=>expect(result.current.data?.id).toBe('a'))
 rerender({path:null});expect(result.current.data).toBeNull();expect(result.current.loading).toBe(false)
})
test('changing account invalidates cached resource data',async()=>{
 mocks.api.mockResolvedValueOnce([{owner:'a'}]).mockImplementation(()=>new Promise(()=>{}))
 const {result,rerender}=renderHook(()=>useResource('/jobs'))
 await waitFor(()=>expect(result.current.data?.[0].owner).toBe('a'))
 mocks.user='owner-b';rerender();expect(result.current.data).toBeNull()
})
