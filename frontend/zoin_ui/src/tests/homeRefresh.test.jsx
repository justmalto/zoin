// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Home from '../pages/home'
const mocks=vi.hoisted(()=>({state:{data:null,loading:true,error:'',reload:vi.fn()}}))
vi.mock('../lib/useResource',()=>({useResource:()=>mocks.state}))
afterEach(()=>{cleanup();vi.useRealTimers();mocks.state={data:null,loading:true,error:'',reload:vi.fn()}})
test('initial loading is shown, but a loaded empty list refresh has no loading flash',()=>{
 const view=render(<MemoryRouter><Home/></MemoryRouter>)
 expect(screen.getByText('Loading applications…')).toBeTruthy()
 mocks.state={...mocks.state,data:[],loading:true};view.rerender(<MemoryRouter><Home/></MemoryRouter>)
 expect(screen.queryByText('Loading applications…')).toBeNull()
})
test('idle dashboard never polls, and refreshes on return or job changes',()=>{
 vi.useFakeTimers();mocks.state.loading=false;mocks.state.data=[]
 const view=render(<MemoryRouter><Home/></MemoryRouter>)
 vi.advanceTimersByTime(120000);expect(mocks.state.reload).not.toHaveBeenCalled()
 window.dispatchEvent(new Event('focus'));expect(mocks.state.reload).toHaveBeenCalledTimes(1)
 document.dispatchEvent(new Event('visibilitychange'));expect(mocks.state.reload).toHaveBeenCalledTimes(1)
 vi.advanceTimersByTime(1000);document.dispatchEvent(new Event('visibilitychange'));expect(mocks.state.reload).toHaveBeenCalledTimes(2)
 window.dispatchEvent(new Event('zoin:applications-changed'));expect(mocks.state.reload).toHaveBeenCalledTimes(3)
 view.unmount();window.dispatchEvent(new Event('focus'));expect(mocks.state.reload).toHaveBeenCalledTimes(3)
})
