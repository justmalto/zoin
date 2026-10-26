// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ResumeDownload from '../components/ResumeDownload'
const mocks=vi.hoisted(()=>({api:vi.fn()}))
vi.mock('../lib/api',()=>({api:mocks.api}))
beforeEach(()=>{
 vi.useFakeTimers();mocks.api.mockReset()
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,blob:async()=>new Blob(['resume'])})))
 URL.createObjectURL=vi.fn(()=> 'blob:fixture');URL.revokeObjectURL=vi.fn()
 vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(function(){ expect(this.download).toMatch(/resume\.txt$/) })
})
afterEach(()=>{cleanup();vi.runAllTimers();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})
test('saved resume downloads its private signed file with original filename',async()=>{
 mocks.api.mockResolvedValue({url:'https://storage.example.invalid/signed',file_name:'resume.txt'})
 render(<ResumeDownload resumeId="saved"/>);fireEvent.click(screen.getByRole('button'))
 await vi.waitFor(()=>expect(URL.createObjectURL).toHaveBeenCalledOnce())
 expect(mocks.api).toHaveBeenCalledWith('/resumes/saved/download')
 expect(fetch).toHaveBeenCalledWith('https://storage.example.invalid/signed',expect.objectContaining({credentials:'omit'}))
 expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
})
test('temporary resume downloads locally without sending or saving it',async()=>{
 const file=new File(['local resume'],'resume.txt',{type:'text/plain'})
 render(<ResumeDownload file={file}/>);fireEvent.click(screen.getByRole('button',{name:'Download temporary resume'}))
 await vi.waitFor(()=>expect(URL.createObjectURL).toHaveBeenCalledWith(file))
 expect(mocks.api).not.toHaveBeenCalled();expect(fetch).not.toHaveBeenCalled()
})
test('failed download exposes retry without creating an empty file',async()=>{
 mocks.api.mockRejectedValue(new Error('Please sign in again.'))
 render(<ResumeDownload resumeId="saved"/>);fireEvent.click(screen.getByRole('button'))
 await vi.waitFor(()=>expect(screen.getByRole('alert').textContent).toBe('Please sign in again.'))
 expect(URL.createObjectURL).not.toHaveBeenCalled();expect(screen.getByRole('button').disabled).toBe(false)
})
