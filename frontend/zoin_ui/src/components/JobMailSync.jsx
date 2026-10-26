import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { useResource } from '../lib/useResource'
import { labels, transitions } from '../lib/status'
import SyncTime from './SyncTime'
import { syncErrors } from '../lib/syncStatus'
export default function JobMailSync({ job, onUpdate, onTransition, onSynced }) {
  const logs = useResource(`/mail-sync/messages/${job.id}`)
  const reloadLogs = logs.reload
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('')
  useEffect(() => {
    if (!busy || busy === 'queueing') return
    const started = Date.now()
    const timer = setInterval(async () => {
      try {
        const state = await api('/mail-sync')
        const run = state.runs.find(r => r.id === busy)
        if (run && run.status !== 'running') {
          onUpdate(await api(`/applications/${job.id}`)); reloadLogs(); onSynced?.(); window.dispatchEvent(new Event('zoin:applications-changed')); setBusy(false)
          setMessage(run.status === 'success' ? 'Email sync completed.' : syncErrors[run.error_code] || 'Sync partially completed. Check settings for details.')
        } else if (Date.now() - started > 180000) { setBusy(false); setMessage('Sync is taking longer than expected. Check the sync log in settings.') }
      } catch (failure) { setBusy(false); setError(failure.message) }
    }, 2000)
    return () => clearInterval(timer)
  }, [busy, job.id, onUpdate, reloadLogs, onSynced])
  async function sync() {
    setError(''); setMessage(''); setBusy('queueing')
    try { const result = await api('/mail-sync/now', { method:'POST', body:{application_id:job.id} }); setBusy(result.run_id); setMessage('Sync queued…') }
    catch (failure) { setBusy(false); setError(failure.message) }
  }
  const eligible = ['found','applied'].includes(job.status)
  return <section className="wide panel"><div className="sectionhead"><h2>Job Emails</h2><button type="button" disabled={busy || !eligible} onClick={sync}>{busy ? 'Syncing…' : 'Sync now'}</button></div>
    <p><SyncTime value={job.last_mail_synced_at} />{job.mail_sync_error && ` · ${syncErrors[job.mail_sync_error] || 'Last sync failed.'}`}</p>
    {job.mail_before_uid && <p>More inbox candidates remain. Sync again to continue.</p>}
    <p className="hint">Found jobs are checked for confirmation after saving. Only Applied jobs qualify for scheduled tracking. <Link to="/profile">Manage Gmail and schedule</Link>.</p>
    {!eligible && <p>Scheduled email tracking stops when a job leaves Applied.</p>}
    {(error || message) && <p role={error ? 'alert' : 'status'}>{error || message}</p>}
    {logs.error && <p role="alert">{logs.error} <button type="button" onClick={logs.reload}>Retry</button></p>}
    {logs.data?.map(item => <article className="criterion" key={item.id}><h3>{labels[item.suggested_status] || 'Related email'} · {Math.round(item.confidence * 100)}% match</h3><p>{item.subject || 'Job email'}{item.sender && ` · ${item.sender}`}</p>{item.interview_details && <p>Interview: {item.interview_details}</p>}{item.deadline_text && <p>Deadline: {item.deadline_text}</p>}<p>{item.changed ? 'Application confirmation applied automatically.' : 'Matched email recorded. Review before changing the status.'}</p><p className="small">{new Date(item.processed_at).toLocaleString()}</p>{transitions[job.status]?.includes(item.suggested_status) && <button type="button" onClick={() => onTransition(item.suggested_status, 'Reviewed synced email suggestion')}>Apply reviewed status</button>}</article>)}
  </section>
}
