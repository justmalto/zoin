import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { useResource } from '../lib/useResource'

export default function JobQuestions({ job }) {
 const resource=useResource(`/discovery/applications/${job.id}/questions`)
 const [search]=useSearchParams(),dialog=useRef(null),trigger=useRef(null),autoOpened=useRef(false)
 const [edited,setEdited]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[action,setAction]=useState(''),[actionError,setActionError]=useState('')
 const data=resource.data
 const answers=edited||Object.fromEntries((data?.questions||[]).map(q=>[q.key,{key:q.key,value:q.answer||'',remember:q.remember??true}]))
 const pending=data?.questions?.filter(q=>q.required&&!q.answer).length||0
 function open(){setEdited(null);setError('');dialog.current?.showModal()}
 function close(){if(busy)return;dialog.current?.close();trigger.current?.focus()}
 useEffect(()=>{if(!autoOpened.current&&search.get('answers')==='open'&&data?.questions?.length&&!dialog.current?.open){autoOpened.current=true;dialog.current?.showModal()}},[search,data])
 async function save(e){
  e.preventDefault();e.stopPropagation();setBusy(true);setError('')
  try{
   await api(`/discovery/applications/${job.id}/questions`,{method:'PUT',body:{revision:data.revision,answers:Object.values(answers)}})
   setMessage('Answers saved. The agent will continue with form checks, your automation level and daily limit.');resource.reload();window.dispatchEvent(new Event('zoin:applications-changed'));dialog.current?.close();trigger.current?.focus()
  }catch(failure){setError(failure.message)}finally{setBusy(false)}
 }
 async function inspect(){
  setAction('inspecting');setActionError('');setMessage('')
  try{const result=await api(`/discovery/applications/${job.id}/inspect`,{method:'POST'});resource.reload();setMessage(result.inspection?.state==='blocked'?'':result.questions?.length?'Form inspected. Open the questions to review the required answers.':'Form inspected. No application was submitted.')}catch(failure){setActionError(failure.message)}finally{setAction('')}
 }
 async function retry(){
  setAction('retrying');setActionError('');setMessage('')
  try{await api(`/discovery/applications/${job.id}/retry`,{method:'POST'});resource.reload();setMessage('Retry queued. Your automation level, approval requirements and daily limit still apply.')}catch(failure){setActionError(failure.message)}finally{setAction('')}
 }
 function change(key,field,value){setEdited(old=>({...answers,...old,[key]:{...answers[key],[field]:value}}))}
 const blocked=data?.inspection?.state==='blocked'?data.inspection.note:['blocked_before_submission','outcome_unknown','needs_reconciliation'].includes(data?.attempt?.state)?data.attempt.note:null
 const submitted=job.status!=='found'||job.submitted_at||job.receipt_confirmed_at
 return <><section className="wide panel job-questions"><div className="sectionhead"><div><p className="eyebrow">APPLICATION ASSISTANT</p><h2>{data?.state==='needs_answers'?'A few answers to continue':'Application answers'}</h2></div>{data?.questions?.length>0&&<button ref={trigger} type="button" onClick={open}>{data.state==='ready'?'Review answers':`Needs answers${pending?` · ${pending}`:''}`}</button>}</div>
  {resource.error?<p role="alert">{resource.error} <button type="button" onClick={resource.reload}>Retry</button></p>:!data?<p role="status">Checking application questions…</p>:!data.questions?.length?<p className="small muted">{data.state==='unsupported'?'This link cannot be opened as a public HTTPS application. Use a direct posting link or apply manually.':data.state==='stale'?'Job details changed. The agent must inspect the form again before answers can be used.':data.state==='ready'?'Form inspected. No unanswered required questions. Your automation settings still control submission.':'Inspect the job link to discover its actual application questions. No answers are guessed.'}</p>:<p className="small muted">{data.state==='ready'?'Answers ready · Form checks still apply.':'Answer here to continue this job. Remembered answers stay private to your account.'}</p>}
  {blocked&&<div className="agent-blocker" role="alert"><strong>Automatic application paused — {blocked}</strong><p>No confirmed submission is being assumed. Open the posting to resolve the issue.</p></div>}
  {actionError&&<p role="alert" className="agent-blocker"><strong>{actionError}</strong></p>}
  {!submitted&&<div className="draft-actions"><button type="button" disabled={!!action||busy||!job.application_url} onClick={inspect}>{action==='inspecting'?'Inspecting application…':'Inspect application link'}</button>{data?.attempt?.state==='blocked_before_submission'&&<button type="button" disabled={!!action||busy} onClick={retry}>{action==='retrying'?'Queuing retry…':'Retry application'}</button>}{job.application_url&&<a href={job.application_url} target="_blank" rel="noreferrer">Open posting ↗</a>}</div>}
  {action==='inspecting'&&<p role="status">Opening the real job link and checking its form. Usually 10–60 seconds. Inspection does not submit or upload your résumé.</p>}
  {message&&<p role="status">{message}</p>}
 </section>{createPortal(<dialog ref={dialog} className="questions-dialog" aria-labelledby="questions-title" onCancel={e=>{e.preventDefault();e.stopPropagation();close()}}>
   <form onSubmit={save}><header className="dialoghead"><div><p className="eyebrow">{job.company} · {job.job_title}</p><h2 id="questions-title">{data?.state==='ready'?'Review application answers':'A few answers to continue'}</h2></div><button type="button" aria-label="Close questions" disabled={busy} onClick={close}>×</button></header>
    <div className="questions-body"><p className="small muted">These questions come from this application. We ask whenever a confirmed answer is unavailable or its context changes.</p>
     {(data?.questions||[]).map((q,i)=><fieldset key={q.key} disabled={busy||!!submitted}><legend>{String(i+1).padStart(2,'0')} · {q.label}{q.required&&!q.label.trim().endsWith('*')?' *':''}</legend><p className="small muted">{[q.context?.country,q.context?.employer].filter(Boolean).join(' · ')}{q.source==='saved_answer'?' · Reused from your confirmed answers':''}</p><label><span className="sr-only">{q.label}</span>{q.type==='select'?<select aria-label={q.label} required={q.required} value={answers[q.key]?.value||''} onChange={e=>change(q.key,'value',e.target.value)}><option value="">Choose an answer</option>{q.options.map(v=><option key={v} value={v}>{v}</option>)}</select>:<input aria-label={q.label} required={q.required} maxLength={4000} value={answers[q.key]?.value||''} onChange={e=>change(q.key,'value',e.target.value)}/>}</label><label className="agent-check"><input type="checkbox" checked={answers[q.key]?.remember??true} onChange={e=>change(q.key,'remember',e.target.checked)}/>Remember for future applications with the same context</label></fieldset>)}
     {submitted&&<p>This job has already been submitted or is no longer Found. Answers are read-only.</p>}{error&&<p role="alert" className="alert">{error}</p>}
    </div><footer className="footer"><button type="button" disabled={busy} onClick={close}>Answer later</button><button className="primary" disabled={busy||!!submitted}>{busy?'Saving…':'Save answers & resume agent'}</button></footer>
   </form>
  </dialog>,document.body)}</>
}
