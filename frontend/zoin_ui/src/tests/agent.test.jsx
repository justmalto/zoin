// @vitest-environment jsdom
import {afterEach,expect,test,vi} from 'vitest'
import {cleanup,render,screen,fireEvent,waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import AgentSettings from '../components/AgentSettings'
import Recommendations from '../components/Recommendations'
import {agentDefaults} from '../lib/agent'
const mocks=vi.hoisted(()=>({data:null,api:vi.fn(),reload:vi.fn()}))
vi.mock('../lib/api',()=>({api:mocks.api}))
vi.mock('../lib/useResource',()=>({useResource:()=>({data:mocks.data,loading:false,error:'',reload:mocks.reload})}))
afterEach(()=>{cleanup();vi.clearAllMocks()})
test('four automation levels and daily cap persist with weekly schedule',async()=>{
 mocks.data={preferences:agentDefaults};mocks.api.mockResolvedValue({})
 render(<MemoryRouter><AgentSettings/></MemoryRouter>)
 fireEvent.change(screen.getByLabelText('Automation level'),{target:{value:'review_resume'}})
 fireEvent.change(screen.getByLabelText('Daily application limit'),{target:{value:'30'}})
 fireEvent.change(screen.getByLabelText('Recommendation frequency'),{target:{value:'7'}})
 fireEvent.click(screen.getByRole('button',{name:'Save preferences'}))
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('/discovery/preferences',expect.objectContaining({body:expect.objectContaining({automation_level:'review_resume',daily_application_limit:30,frequency_days:7,daily_enabled:true})})))
})
test('saving next-search threshold preserves visible recommendations',async()=>{
 HTMLDialogElement.prototype.showModal=function(){this.open=true};HTMLDialogElement.prototype.close=function(){this.open=false}
 mocks.data={preferences:agentDefaults,run:{id:'run',status:'complete',created_at:'2026-10-29T00:00:00Z',result:{jobs:[{url:'https://example.com/job',title:'Nurse',company:'Hospital',match_score:85,assessment:{}}]}}};mocks.api.mockResolvedValue({})
 render(<MemoryRouter><Recommendations/></MemoryRouter>)
 fireEvent.click(screen.getByRole('button',{name:/Threshold/}))
 fireEvent.change(screen.getByLabelText(/Minimum match threshold/),{target:{value:'95'}})
 fireEvent.click(screen.getByRole('button',{name:'Save for next search'}))
 await waitFor(()=>expect(mocks.api).toHaveBeenCalled())
 expect(screen.getByRole('heading',{name:'Nurse'})).toBeTruthy()
 expect(screen.getByText('Filter saved. Changes apply to your next search.')).toBeTruthy()
})
test('Found follow-up saves the chosen cooldown without changing automation or caps',async()=>{
 mocks.data={preferences:{...agentDefaults,automation_level:'review_resume',daily_application_limit:12}};mocks.api.mockResolvedValue({})
 render(<MemoryRouter><AgentSettings/></MemoryRouter>)
 fireEvent.click(screen.getByLabelText('Let the agent check unsubmitted Found jobs'))
 fireEvent.change(screen.getByLabelText('Wait after saving a job'),{target:{value:'50'}})
 fireEvent.click(screen.getByRole('button',{name:'Save preferences'}))
 await waitFor(()=>expect(mocks.api).toHaveBeenCalledWith('/discovery/preferences',expect.objectContaining({body:expect.objectContaining({found_followup_enabled:true,found_followup_hours:50,automation_level:'review_resume',daily_application_limit:12})})))
})
