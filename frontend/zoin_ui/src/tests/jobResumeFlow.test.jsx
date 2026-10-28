// @vitest-environment jsdom
import { beforeEach, afterEach, expect, test, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import JobDetails from '../pages/jobDetails'
import StatusSummary from '../components/StatusSummary'
const mocks=vi.hoisted(()=>({api:vi.fn(),upload:vi.fn(),reload:vi.fn(),library:[],job:{},mailbox:null}))
vi.mock('../lib/api',()=>({api:mocks.api,uploadResume:mocks.upload}))
vi.mock('../lib/session',()=>({useSession:()=>({mailbox:mocks.mailbox})}))
vi.mock('../lib/useResource',()=>({useResource:path=>({data:!path?null:path==='/resumes'?mocks.library:path==='/applications/job'?mocks.job:[],loading:false,error:'',reload:mocks.reload})}))
beforeEach(()=>{
 mocks.api.mockReset();mocks.upload.mockReset();mocks.reload.mockReset();mocks.mailbox=null
 mocks.library=[{id:'resume-a',file_name:'saved.txt',is_active:true}]
 mocks.job={id:'job',company:'Example',job_title:'Analyst',job_description:'Python and analytical models required',selected_resume_id:'resume-a',status:'found',submitted_at:null}
})
afterEach(cleanup)
function setup(path='/jobs/job'){
 render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/jobs/:id" element={<JobDetails/>}/><Route path="/jobs/new" element={<JobDetails/>}/><Route path="/home" element={<h1>Home</h1>}/></Routes></MemoryRouter>)
}
function choose(){fireEvent.change(screen.getByLabelText('Upload Resume'),{target:{files:[new File(['Python resume'],'tailored.txt',{type:'text/plain'})]}})}
test('temporary file previews through FormData without library upload',async()=>{
 mocks.api.mockResolvedValue({match_score:70,summary:'Test preview',temporary:true});setup();choose()
 fireEvent.click(screen.getByRole('button',{name:'Generate Match & Recommendations'}))
 await screen.findByText('Temporary preview — this score is not saved to the job.')
 expect(mocks.api.mock.calls[0][0]).toBe('/analysis/resume-preview')
 expect(mocks.api.mock.calls[0][1].body.get('file').name).toBe('tailored.txt')
 expect(mocks.upload).not.toHaveBeenCalled()
 fireEvent.click(screen.getByRole('button',{name:'Close job'}));await screen.findByRole('heading',{name:'Home'})
 expect(screen.queryByText('Temporary resume: tailored.txt')).toBeNull()
})
test('full library offers replacement and requires an explicit save',async()=>{
 mocks.library=Array.from({length:10},(_,n)=>({id:`r${n}`,file_name:`saved-${n}.txt`}))
 mocks.upload.mockResolvedValue({id:'r4'});setup();choose()
 expect(screen.getByRole('button',{name:'Save permanently'}).disabled).toBe(true)
 fireEvent.change(screen.getByLabelText('Replace saved resume'),{target:{value:'r4'}})
 fireEvent.click(screen.getByRole('button',{name:'Replace selected resume'}))
 await waitFor(()=>expect(mocks.upload).toHaveBeenCalledOnce())
 expect(mocks.upload.mock.calls[0][1]).toBe('r4')
 await screen.findByText('Resume saved permanently. Save this job to attach it; generate again after saving to keep the match.')
})
test('saving a new job never saves its temporary file or marks it Applied',async()=>{
 mocks.api.mockResolvedValue({id:'new'});setup('/jobs/new');choose()
 fireEvent.change(screen.getByLabelText('Job Role *'),{target:{value:'Analyst'}})
 fireEvent.change(screen.getByLabelText('Company Name *'),{target:{value:'Example'}})
 fireEvent.change(screen.getByLabelText('Job Description *'),{target:{value:'Python and analytical models required'}})
 fireEvent.submit(screen.getByRole('button',{name:'Save Job'}).closest('form'))
 await screen.findByRole('heading',{name:'Home'})
 expect(mocks.upload).not.toHaveBeenCalled()
 expect(mocks.api.mock.calls[0][1].body).toMatchObject({initial_status:'found',selected_resume_id:null})
})
test('sync now polls confirmation and provides correction',async()=>{
 mocks.api.mockImplementation(path=>Promise.resolve(path==='/mail-sync/now'?{state:'queued',run_id:'run'}:path==='/mail-sync'?{runs:[{id:'run',started_at:new Date().toISOString(),status:'success'}]}:path.endsWith('/receipt-correction')?{...mocks.job,status:'found',receipt_confirmed_at:null}:{...mocks.job,status:'applied',receipt_confirmed_at:'today'}))
 setup();fireEvent.click(screen.getByRole('button',{name:'Sync now'}))
 await screen.findByText('Email sync completed.',{}, {timeout:4000})
 fireEvent.click(screen.getByRole('button',{name:'Correct mistaken email confirmation'}))
 await screen.findByText('Incorrect email confirmation removed. This message will not mark Applied again.')
 expect(mocks.api.mock.calls.at(-1)[0]).toBe('/applications/job/receipt-correction')
})
test('Applied counter excludes unconfirmed/manual entries',()=>{
 render(<StatusSummary jobs={[{status:'applied'},{status:'found'},{status:'applied',receipt_confirmed_at:'today'}]}/>)
 expect(screen.getByText('Applied · 1')).toBeTruthy()
})

test('opening Edit Job never submits or closes the form',async()=>{
 mocks.api.mockResolvedValue({});setup()
 fireEvent.click(screen.getByRole('button',{name:'Edit Job'}))
 await screen.findByRole('heading',{name:'Edit Job'})
 expect(mocks.api).not.toHaveBeenCalled()
})

test('editing the JD invalidates the temporary match preview',async()=>{
 mocks.api.mockResolvedValue({match_score:70,summary:'Old preview',temporary:true});setup();choose()
 fireEvent.click(screen.getByRole('button',{name:'Generate Match & Recommendations'}))
 await screen.findByText('Temporary preview — this score is not saved to the job.')
 fireEvent.change(screen.getByLabelText('Job Description *'),{target:{value:'Different requirements for a cloud platform role'}})
 expect(screen.queryByText('Old preview')).toBeNull()
 expect(screen.queryByText('Temporary preview — this score is not saved to the job.')).toBeNull()
})

test('pasting a job link fills empty fields and keeps manual details',async()=>{
 mocks.api.mockResolvedValue({company:'Imported Co',job_title:'Data Scientist',job_id:'DS42',location:'Remote',employment_type:'FULL_TIME',job_description:'Build machine learning models with Python and deploy them.',warnings:['Review before saving.']})
 setup('/jobs/new')
 fireEvent.change(screen.getByLabelText('Company Name *'),{target:{value:'My company'}})
 fireEvent.paste(screen.getByLabelText('Job posting link'),{clipboardData:{getData:()=> 'https://example.com/jobs/42'}})
 await screen.findByText(/Only empty fields were filled/)
 expect(screen.getByLabelText('Company Name *').value).toBe('My company')
 expect(screen.getByLabelText('Job Role *').value).toBe('Data Scientist')
 expect(screen.getByLabelText('Location').value).toBe('Remote')
 expect(mocks.api.mock.calls[0][0]).toBe('/analysis/job-link')
 fireEvent.submit(screen.getByRole('button',{name:'Save Job'}).closest('form'))
 await screen.findByRole('heading',{name:'Home'})
 expect(mocks.api.mock.calls.at(-1)[1].body.application_url).toBe('https://example.com/jobs/42')
})
test('link import failure preserves editable fields and supports retry',async()=>{
 mocks.api.mockRejectedValueOnce(new Error('Job site blocked access. Paste the description instead.')).mockResolvedValueOnce({company:'Recovered Co',job_title:'Analyst',job_description:'A readable job description with Python analysis requirements.'})
 setup('/jobs/new')
 fireEvent.change(screen.getByLabelText('Job Role *'),{target:{value:'Manual title'}})
 fireEvent.change(screen.getByLabelText('Job posting link'),{target:{value:'https://example.com/job'}})
 fireEvent.click(screen.getByRole('button',{name:'Autofill from link'}))
 await screen.findByRole('alert')
 expect(screen.getByLabelText('Job Role *').value).toBe('Manual title')
 fireEvent.click(screen.getByRole('button',{name:'Autofill from link'}))
 await screen.findByText(/Only empty fields were filled/)
 expect(screen.getByLabelText('Company Name *').value).toBe('Recovered Co')
})
test('edit job import only replaces existing content with explicit option',async()=>{
 mocks.api.mockResolvedValue({company:'New employer',job_title:'New role',job_description:'New requirements for Python modelling and deployment.'})
 setup();fireEvent.click(screen.getByRole('button',{name:'Edit Job'}))
 fireEvent.change(screen.getByLabelText('Job posting link'),{target:{value:'https://example.com/job'}})
 fireEvent.click(screen.getByLabelText('Replace existing job details when importing'))
 fireEvent.click(screen.getByRole('button',{name:'Autofill from link'}))
 await screen.findByText(/Existing job details were replaced/)
 expect(screen.getByLabelText('Company Name *').value).toBe('New employer')
 expect(screen.getByLabelText('Job Role *').value).toBe('New role')
})
