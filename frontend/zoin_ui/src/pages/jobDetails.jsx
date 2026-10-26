import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, uploadResume } from '../lib/api'
import { useResource } from '../lib/useResource'
import ResumeDownload from '../components/ResumeDownload'
import JobMailSync from '../components/JobMailSync'
import { labels, transitions, dateLabel } from '../lib/status'
import AnalysisResult from '../components/AnalysisResult'
import GenerationStatus from '../components/GenerationStatus'
import { generationError } from '../lib/generationError'

export default function JobDetails({ modal = false }) {
  const { id } = useParams()
  const job = useResource(id ? `/applications/${id}` : null)
  const location = useLocation(), navigate = useNavigate()
  const close = () => navigate(location.state?.backgroundLocation?.pathname || '/home')
  if (id && !job.data) return <main className="screen"><p role={job.error ? 'alert' : 'status'}>{job.error || 'Loading job…'}</p>{job.error && <button onClick={job.reload}>Retry</button>} <button onClick={close}>Back</button></main>
  return <JobForm key={id || 'new'} job={job.data} modal={modal} onClose={close} />
}

function JobForm({ job, modal, onClose }) {
  const [currentJob, setCurrentJob] = useState(job)
  const [editing, setEditing] = useState(!job)
  const [draft, setDraft] = useState(() => ({ company: job?.company || '', job_title: job?.job_title || '', job_description: job?.job_description || '', job_id: job?.job_id || '', submitted_date: job?.submitted_at?.slice(0, 10) || '', selected_resume_id: job?.selected_resume_id ?? null, initial_status: 'found', location: job?.location || '' }))
  const [matchFailure, setMatchFailure] = useState(null), [matchStarted, setMatchStarted] = useState(0), [retryAt, setRetryAt] = useState(null)
  const [replaceResumeId, setReplaceResumeId] = useState('')
  const [file, setFile] = useState(null), [busy, setBusy] = useState(''), [error, setError] = useState('')
  const [message, setMessage] = useState(''), [analysis, setAnalysis] = useState(null), [nextStatus, setNextStatus] = useState('')
  const resumes = useResource('/resumes'), recs = useResource(job ? `/applications/${job.id}/recommendations` : null), timeline = useResource(job ? `/applications/${job.id}/timeline` : null)
  const dialog = useRef(null), form = useRef(null)
  const [search, setSearch] = useSearchParams(), location = useLocation()
  const expanded = !modal || search.get('view') === 'full'
  const selectedResume = draft.selected_resume_id ?? ((!job ? resumes.data?.find(r => r.is_active)?.id : '') || '')
  const savedAnalysis = recs.data?.find(r => r.resume_id === selectedResume)?.analysis
  const result = analysis || (!file && !editing ? savedAnalysis : null)
  const readOnly = !editing
  useEffect(() => {
    const element = dialog.current
    if (modal && element && !element.open) { element.showModal(); if (!job) element.querySelector('input:not([type="file"])')?.focus() }
    return () => { if (element?.open) element.close() }
  }, [modal, job])
  function change(event) {
    const { name, value } = event.target
    setDraft(old => ({ ...old, [name]: value }))
    if (name === 'job_description' || name === 'selected_resume_id') { setAnalysis(null); setMatchFailure(null); setRetryAt(null); setMessage('') }
  }
  async function perform(name, action) {
    setBusy(name); setError(''); setMessage('')
    try { await action() } catch (failure) { setError(failure.message) }
    finally { setBusy('') }
  }
  async function save(event) {
    event.preventDefault()
    await perform('saving', async () => {
      let resumeId = selectedResume
      // A temporary file never enters Storage or the library implicitly.
      if (file) resumeId = ''
      const body = { company: draft.company, job_title: draft.job_title, job_description: draft.job_description, job_id: draft.job_id || null, submitted_at: draft.submitted_date ? new Date(`${draft.submitted_date}T00:00:00Z`).toISOString() : null, selected_resume_id: resumeId || null, location: draft.location || null }
      if (!job) body.initial_status = draft.initial_status
      await api(job ? `/applications/${job.id}` : '/applications', { method: job ? 'PATCH' : 'POST', body })
      onClose()
    })
  }
  async function extract() {
    if (draft.job_description.trim().length < 20) { setError('Paste at least 20 characters of the job description first.'); return }
    await perform('extracting', async () => {
      const data = await api('/analysis/job', { method: 'POST', body: { text: draft.job_description } })
      setDraft(old => ({ ...old, company: data.company || old.company, job_title: data.job_title || old.job_title, job_id: data.job_id || old.job_id, location: data.location || old.location }))
      setMessage('Job details extracted. Review them before saving.')
    })
  }
  async function match() {
    if (!file && !selectedResume) { setError('Choose or upload a resume before matching.'); return }
    if (draft.job_description.trim().length < 20) { setError('Enter at least 20 characters of the job description before matching.'); return }
    if (retryAt && Date.now() < retryAt) return
    const started = Date.now()
    setMatchStarted(started); setMatchFailure(null); setRetryAt(null); setBusy('matching'); setError(''); setMessage('')
    try {
      if (file || editing || !job) {
        let preview
        if (file) {
          const body = new FormData(); body.append('file', file); body.append('job_description', draft.job_description)
          preview = await api('/analysis/resume-preview', { method: 'POST', body, timeoutMs: 120000 })
        } else {
          preview = await api('/analysis/resume-match', { method: 'POST', body: {job_description:draft.job_description,resume_id:selectedResume}, timeoutMs:120000 })
        }
        setAnalysis({...preview,temporary:true}); setMessage('Temporary match ready. The file and preview are discarded when this form closes or refreshes; save permanently to keep a resume.')
      } else {
        const rows = await api(`/applications/${job.id}/recommendations?resume_id=${selectedResume}`, { method: 'POST', timeoutMs: 120000 })
        if (!rows?.[0]?.analysis || !Number.isFinite(rows[0].analysis.match_score)) throw new Error('The server did not return a saved match score. Reload saved results before retrying.')
        setAnalysis(rows[0].analysis); setMessage(`Match score and recommendations saved in ${Math.max(1, Math.round((Date.now() - started) / 1000))} seconds.`); recs.reload()
      }
    } catch (failure) {
      setMatchFailure(generationError(failure)); setRetryAt(Date.now() + Math.max(5, failure.retryAfter || 0) * 1000)
      if (failure.status === 504) recs.reload()
    } finally { setBusy('') }
  }

  function chooseFile(event) {
    const chosen = event.target.files?.[0]
    if (!chosen) return
    if (chosen.size > 10 * 1024 * 1024) { setError('Maximum upload size is 10 MB. Choose a smaller file.'); event.target.value = ''; return }
    setFile(chosen); setEditing(true); setAnalysis(null); setMatchFailure(null); setRetryAt(null); setReplaceResumeId(''); setError(''); setMessage('Temporary resume selected. It has not been saved to your library.')
  }
  async function savePermanent() {
    if (!file) return
    await perform('resume', async () => {
      const resume = await uploadResume(file, replaceResumeId || null)
      setDraft(old => ({...old,selected_resume_id:resume.id})); setFile(null); setAnalysis(null); resumes.reload(); recs.reload()
      setMessage('Resume saved permanently. Save this job to attach it; generate again after saving to keep the match.')
    })
  }
  async function correctReceipt() {
    await perform('status', async () => {
      const data = await api(`/applications/${job.id}/receipt-correction`, {method:'POST'})
      setCurrentJob(data); timeline.reload(); window.dispatchEvent(new Event('zoin:applications-changed')); setMessage('Incorrect email confirmation removed. This message will not mark Applied again.')
    })
  }
  async function transition(status, note) {
    await perform('status', async () => {
      const data = await api(`/applications/${job.id}/transition`, { method: 'POST', body: { to_status: status, note: note || null } })
      setCurrentJob(Array.isArray(data) ? data[0] : data); setNextStatus(''); timeline.reload(); window.dispatchEvent(new Event('zoin:applications-changed')); setMessage('Application status updated.')
    })
  }
  const content = <form ref={form} onSubmit={save}>
    <div className="dialoghead"><div><h1>{!job ? 'Add Job' : editing ? 'Edit Job' : 'Job Details'}</h1><p>{currentJob ? `${currentJob.company} · ${labels[currentJob.status]}` : 'Keep track of your next opportunity.'}</p></div><div className="headbuttons">
      {modal && <button type="button" onClick={() => setSearch(expanded ? {} : { view: 'full' }, { replace: true, state: location.state })}>{expanded ? '↙ Compact window' : '↗ Full page'}</button>}
      <button type="button" onClick={onClose} disabled={!!busy} aria-label="Close job">×</button>
    </div></div>
    <div className="fields">
      {(error || message) && <div className="wide"><p className="inline-message" role={error ? 'alert' : 'status'}>{error || message}</p></div>}
      <label>Job Role *<input name="job_title" value={draft.job_title} onChange={change} readOnly={readOnly} disabled={!!busy} maxLength={300} required autoFocus={editing} /></label>
      <label>Company Name *<input name="company" value={draft.company} onChange={change} readOnly={readOnly} disabled={!!busy} maxLength={300} required /></label>
      <div className="wide"><div className="sectionhead"><label htmlFor="job-description">Job Description *</label>{editing && <button type="button" disabled={!!busy} onClick={extract}>{busy === 'extracting' ? 'Extracting…' : 'Extract details'}</button>}</div><textarea id="job-description" name="job_description" value={draft.job_description} onChange={change} readOnly={readOnly} disabled={!!busy} minLength={20} maxLength={18000} required rows={6} placeholder="Paste the job description" /></div>
      <label>Submission date (optional)<input type="date" name="submitted_date" value={draft.submitted_date} onChange={change} readOnly={readOnly} disabled={!!busy} /></label>
      <label>Job ID<input name="job_id" value={draft.job_id} onChange={change} readOnly={readOnly} disabled={!!busy} maxLength={300} placeholder="Optional" /></label>
      {!job && <p className="wide small">New jobs start as Found. Applied is set automatically only when a matching application-received email is confirmed during Gmail search.</p>}
      <section className="wide resume"><div className="sectionhead"><h2>Resume Matching</h2><label className="upload-label">Upload Resume<input className="visually-hidden" type="file" accept=".pdf,.docx,.txt" disabled={!!busy} onChange={chooseFile} /></label></div>
        {resumes.error && <p role="alert">{resumes.error} <button type="button" onClick={resumes.reload}>Retry</button></p>}
        <label>Resume<select aria-label="Select resume" value={selectedResume} onChange={e => { setDraft(old => ({ ...old, selected_resume_id: e.target.value })); setAnalysis(null); setMatchFailure(null); setRetryAt(null) }} disabled={!!busy || readOnly}><option value="">No resume selected</option>{resumes.data?.map(r => <option key={r.id} value={r.id}>{r.file_name}{r.is_active ? ' · Default' : ''}</option>)}</select></label>
        <div className="resume-actions"><ResumeDownload resumeId={selectedResume} file={file} disabled={!!busy || (!file && resumes.loading)} /></div>
        {file && <div className="temporary-resume"><p><strong>Temporary resume: {file.name}</strong></p><p>This file stays only in the open form. It is used for preview matching, not added to your library or attached when you save the job.</p>
          <p>{resumes.data?.length || 0}/10 saved resumes. {resumes.data?.length >= 10 ? 'Library full — select a resume to replace.' : 'Save as a new library entry or choose an existing one to replace.'}</p>
          <label>Save permanently / replace<select aria-label="Replace saved resume" value={replaceResumeId} disabled={!!busy} onChange={e => setReplaceResumeId(e.target.value)}><option value="">{resumes.data?.length >= 10 ? 'Choose a resume to replace' : 'Save as new resume'}</option>{resumes.data?.map(r => <option key={r.id} value={r.id}>{r.file_name}</option>)}</select></label>
          {replaceResumeId && <p>Replacing updates this library entry for jobs using it and clears its old match results.</p>}
          <div className="resume-actions"><button type="button" onClick={savePermanent} disabled={!!busy || resumes.loading || (resumes.data?.length >= 10 && !replaceResumeId)}>{busy === 'resume' ? 'Saving resume…' : replaceResumeId ? 'Replace selected resume' : 'Save permanently'}</button><button type="button" disabled={!!busy} onClick={() => {setFile(null);setAnalysis(null);setMessage('Temporary resume discarded.')}}>Discard temporary resume</button></div></div>}
        <p>Best Resume: {file?.name || resumes.data?.find(r => r.id === selectedResume)?.file_name || '—'} · Match Score: {result ? `${result.match_score}%` : '—'}</p>
        {editing && <p className="small">Generate a temporary preview now. Save the job with a permanent resume, then generate to keep a match.</p>}
      </section>
      <section className="wide suggestions"><div className="sectionhead"><h2>AI Resume Improvement Suggestions</h2><button type="button" onClick={match} disabled={!!busy || (!file && !selectedResume) || !!matchFailure}>{busy === 'matching' ? 'Generating…' : 'Generate Match & Recommendations'}</button></div>
        {<GenerationStatus pending={busy === 'matching'} startedAt={matchStarted} error={matchFailure} retryAt={retryAt} onRetry={match} />}
        {result?.temporary && <p className="small">Temporary preview — this score is not saved to the job.</p>}
        {recs.error && <p role="alert">{recs.error} <button type="button" onClick={recs.reload}>Retry</button></p>}<AnalysisResult analysis={result} />
      </section>
      {job && !editing && <>
        <section className="wide panel"><h2>Application Status</h2><div className="status-controls"><span className={`status ${currentJob.status}`}>{labels[currentJob.status]}</span>{currentJob.receipt_confirmed_at && <button type="button" disabled={!!busy} onClick={correctReceipt}>Correct mistaken email confirmation</button>}{transitions[currentJob.status]?.length ? <><select aria-label="Next status" value={nextStatus} onChange={e => setNextStatus(e.target.value)} disabled={!!busy}><option value="">Choose next status</option>{transitions[currentJob.status].map(s => <option key={s} value={s}>{labels[s]}</option>)}</select><button type="button" disabled={!nextStatus || !!busy} onClick={() => transition(nextStatus)}>Update</button></> : <p className="small muted">No further status changes.</p>}</div></section>
        <JobMailSync job={currentJob} onUpdate={setCurrentJob} onTransition={transition} onSynced={timeline.reload} />
        <section className="wide panel"><h2>Timeline</h2>{timeline.error && <p role="alert">{timeline.error} <button type="button" onClick={timeline.reload}>Retry</button></p>}{timeline.data?.map(event => <div key={event.id} className="timeline-row"><span>{labels[event.to_status] || event.event_type}</span><small>{dateLabel(event.happened_at)}</small>{event.note && <p>{event.note}</p>}</div>)}</section>
      </>}
    </div>
    <div className="footer"><small>{editing ? '* Required fields' : 'Your application and resume analysis'}</small><div><button type="button" onClick={onClose} disabled={!!busy}>{editing ? 'Cancel' : 'Close'}</button>{editing ? <button className="save" disabled={!!busy}>{busy === 'saving' ? 'Saving…' : job ? 'Update Job' : 'Save Job'}</button> : <button type="button" disabled={!!busy} onClick={event => { event.preventDefault(); setEditing(true); setAnalysis(null) }}>Edit Job</button>}</div></div>
  </form>
  return modal ? <dialog ref={dialog} className={expanded ? 'expanded' : ''} aria-label={!job ? 'Add Job' : 'Job Details'} onCancel={e => { e.preventDefault(); if (!busy) onClose() }}>{content}</dialog> : <main className="job-page">{content}</main>
}
