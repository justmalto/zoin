// @vitest-environment jsdom
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import MailSyncSettings from '../components/MailSyncSettings'
import { syncAge } from '../lib/syncStatus'
const mocks = vi.hoisted(() => ({api:vi.fn(),reload:vi.fn(),setMailbox:vi.fn(),data:null}))
vi.mock('../lib/api', () => ({api:mocks.api}))
vi.mock('../lib/useResource', () => ({useResource:() => ({data:mocks.data,reload:mocks.reload,loading:false,error:''})}))
vi.mock('../lib/session', () => ({useSession:() => ({user:{email:'fixture@example.invalid'},setMailbox:mocks.setMailbox})}))
beforeEach(() => { mocks.api.mockReset();mocks.setMailbox.mockReset();mocks.reload.mockReset();mocks.data={configured:true,connection:null,runs:[]} })
afterEach(cleanup)
test('connect saves through backend and keeps only metadata in browser context',async()=>{
 mocks.api.mockResolvedValue({email_address:'fixture@example.invalid',persistence:'encrypted-server-storage'})
 render(<MailSyncSettings />)
 fireEvent.change(screen.getByLabelText('App Password'),{target:{value:'abcdefghijklmnop'}})
 fireEvent.submit(screen.getByRole('button',{name:'Connect & save securely'}).closest('form'))
 await screen.findByText('Gmail connected. The App Password is encrypted on the server.')
 expect(mocks.api.mock.calls[0][0]).toBe('/mail-sync/connect')
 expect(mocks.setMailbox.mock.calls[0][0]).not.toHaveProperty('app_password')
 expect(screen.getByLabelText('App Password').value).toBe('')
})
test('frequency supports daily to weekly and posts chosen schedule',async()=>{
 mocks.data.connection={email_address:'fixture@example.invalid',interval_days:1,enabled:true,next_sync_at:new Date().toISOString()}
 mocks.api.mockResolvedValue({saved:true});render(<MailSyncSettings />)
 expect(screen.getAllByRole('option')).toHaveLength(7)
 fireEvent.change(screen.getByLabelText('Sync frequency'),{target:{value:'7'}})
 fireEvent.click(screen.getByRole('button',{name:'Save schedule'}))
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('/mail-sync/settings',{method:'PATCH',body:{interval_days:7,enabled:true}}))
})
test('sync errors are visible without claiming a successful sync',async()=>{
 mocks.data.connection={email_address:'fixture@example.invalid',interval_days:1,enabled:true,next_sync_at:new Date().toISOString()}
 mocks.api.mockRejectedValue(new Error('Gmail unavailable'))
 render(<MailSyncSettings />);fireEvent.click(screen.getByRole('button',{name:'Sync now'}))
 await screen.findByRole('alert');expect(screen.getByText(/Gmail unavailable/)).toBeTruthy()
 expect(screen.getByText('Not synced yet')).toBeTruthy()
})
test('relative sync labels cover minutes and missing dates',()=>{
 const now=Date.parse('2026-10-26T00:00:00Z')
 expect(syncAge(null,now)).toBe('Not synced yet')
 expect(syncAge('2026-10-25T23:55:00Z',now)).toBe('Synced 5 mins ago')
})
