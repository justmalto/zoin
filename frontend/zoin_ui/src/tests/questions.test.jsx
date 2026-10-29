// @vitest-environment jsdom
import {afterEach,beforeEach,expect,test,vi} from 'vitest'
import {cleanup,render,screen,fireEvent,waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import JobQuestions from '../components/JobQuestions'
const mocks=vi.hoisted(()=>({data:null,api:vi.fn(),reload:vi.fn()}))
vi.mock('../lib/api',()=>({api:mocks.api}))
vi.mock('../lib/useResource',()=>({useResource:()=>({data:mocks.data,loading:false,error:'',reload:mocks.reload})}))
beforeEach(()=>{
 HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false}
 mocks.data={state:'needs_answers',revision:'v1',questions:[{key:'work',label:'Eligible to work in India?',required:true,type:'select',options:['Yes','No'],context:{country:'India',employer:'Example'}}]}
})
afterEach(()=>{cleanup();vi.clearAllMocks()})
function setup(){render(<MemoryRouter><JobQuestions job={{id:'job',status:'found',company:'Example',job_title:'Engineer'}}/></MemoryRouter>);fireEvent.click(screen.getByRole('button',{name:'Needs answers · 1'}))}
test('job-only answers save and close without nesting forms',async()=>{
 mocks.api.mockResolvedValue({state:'ready'});setup()
 fireEvent.change(screen.getByLabelText('Eligible to work in India?'),{target:{value:'Yes'}})
 fireEvent.click(screen.getByLabelText('Remember for future applications with the same context'))
 fireEvent.submit(screen.getByRole('button',{name:'Save answers & resume agent'}).closest('form'))
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('/discovery/applications/job/questions',{method:'PUT',body:{revision:'v1',answers:[{key:'work',value:'Yes',remember:false}]}}))
 await screen.findByRole('status');expect(document.querySelector('dialog').open).toBe(false)
})
test('failure preserves answers and permits retry; cancel never saves',async()=>{
 mocks.api.mockRejectedValueOnce(new Error('Form changed. Reload.'));setup()
 fireEvent.change(screen.getByLabelText('Eligible to work in India?'),{target:{value:'No'}})
 fireEvent.submit(screen.getByRole('button',{name:'Save answers & resume agent'}).closest('form'))
 await screen.findByRole('alert');expect(screen.getByLabelText('Eligible to work in India?').value).toBe('No');expect(document.querySelector('dialog').open).toBe(true)
 fireEvent.click(screen.getByRole('button',{name:'Answer later'}));expect(mocks.api).toHaveBeenCalledTimes(1)
})
test('shows a highlighted site blocker and allows an explicit safe retry',async()=>{
 mocks.data={state:'not_inspected',questions:[],inspection:{state:'blocked',note:'The site requires sign-in.'},attempt:{state:'blocked_before_submission',note:'LOGIN_REQUIRED'}};mocks.api.mockResolvedValue({})
 render(<MemoryRouter><JobQuestions job={{id:'job',status:'found',company:'Example',job_title:'Engineer',application_url:'https://example.com/job'}}/></MemoryRouter>)
 expect(screen.getByText(/Automatic application paused/).tagName).toBe('STRONG')
 fireEvent.click(screen.getByRole('button',{name:'Retry application'}))
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('/discovery/applications/job/retry',{method:'POST'}))
})
test('an unknown submission outcome never offers an automatic retry',()=>{
 mocks.data={state:'ready',questions:[],attempt:{state:'outcome_unknown',note:'Check the inbox before retrying.'}}
 render(<MemoryRouter><JobQuestions job={{id:'job',status:'found',company:'Example',job_title:'Engineer'}}/></MemoryRouter>)
 expect(screen.queryByRole('button',{name:'Retry application'})).toBeNull()
 expect(screen.getByText(/Automatic application paused/)).toBeTruthy()
})
