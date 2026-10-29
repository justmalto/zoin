import { useEffect, useState } from 'react'
import SyncTime from './SyncTime'
import { labels, dateLabel, statusTone } from '../lib/status'
export default function JobCard({ job, onClick }) {
  const [now,setNow]=useState(()=>Date.now())
  const followupState=job.found_followup?.state
  useEffect(()=>{
    if(followupState!=='scheduled')return
    const timer=setInterval(()=>setNow(Date.now()),60000)
    return()=>clearInterval(timer)
  },[followupState])
  return <button type="button" className="job" onClick={onClick} aria-label={`View ${job.job_title} at ${job.company}`}>
    <div className="cardtop"><span className="company">{job.company}</span><span className={`status ${statusTone(job.status)}`}>{labels[job.status] || job.status}</span></div>
    {job.agent_submission&&<span className="chip">Agent · {job.agent_submission.state.replaceAll('_',' ')}</span>}
    {job.agent_submission?.state==='blocked_before_submission'&&<p className="agent-blocker small"><strong>Automatic application paused — {job.agent_submission.note}</strong></p>}
    {job.agent_submission?.state==='outcome_unknown'&&<p className="agent-blocker small"><strong>Submission could not be confirmed. Check the employer site or inbox before retrying.</strong></p>}
    {job.agent_questions&&<span className="chip">{job.agent_questions.state==='ready'?'Answers ready':`Needs answers · ${job.agent_questions.pending}`}</span>}
    <h2 title={job.job_title}>{job.job_title}</h2>
    <div className="details"><div>Status:<b>{labels[job.status]}</b>Latest update:<b>{dateLabel(job.updated_at)}</b></div>
      <div>Date applied:<b>{dateLabel(job.applied_at)}</b>Resume:<b title={job.resume_name}>{job.tailored_resume?.file_name || job.resume_name || '—'}</b></div>
      <div className="match">Match<b>{job.match_score == null ? '—' : `${job.match_score}%`}</b></div>
    </div>
    {job.found_followup&&<p className="small muted">{job.found_followup.state==='excluded'?'Already submitted · follow-up excluded':job.found_followup.state==='needs_answers'?'Agent waiting for your answers':job.found_followup.state==='paused'?'Agent follow-up paused':job.found_followup.state==='unsupported'?'Unsupported form · apply manually':Date.parse(job.found_followup.eligible_at)<=now?'Ready for agent checks':`Agent check in ${Math.max(1,Math.ceil((Date.parse(job.found_followup.eligible_at)-now)/3600000))} hours`}</p>}
    {job.tailored_resume&&<p className="small muted">Tailored résumé · {job.tailored_resume.state.replaceAll('_',' ')}</p>}
    <div className="small muted sync-card"><SyncTime value={job.last_mail_synced_at} />{job.mail_sync_error && " · Sync needs attention"}{job.confirmation_pending && " · Confirmation check pending"}</div>
  </button>
}
