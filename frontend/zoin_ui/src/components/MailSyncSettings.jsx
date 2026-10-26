import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { useResource } from '../lib/useResource'
import { useSession } from '../lib/session'
import SyncTime from './SyncTime'
import { syncErrors } from '../lib/syncStatus'
export default function MailSyncSettings() {
  const { user, setMailbox } = useSession()
  const state = useResource('/mail-sync'), password = useRef(null)
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('')
  const [queuedRunId, setQueuedRunId] = useState(null)
  const connection = state.data?.connection
  const completedRun = state.data?.runs?.find(run => run.id === queuedRunId && run.status !== 'running')
  const syncMessage = completedRun ? (completedRun.status === 'success' ? `Sync completed: ${completedRun.checked_jobs} jobs checked, ${completedRun.changed_jobs} changed.` : syncErrors[completedRun.error_code] || 'Sync did not fully complete. Check the log.') : message
  const [interval, setIntervalDays] = useState(null), [enabled, setEnabled] = useState(null)
  const awaiting = !!(queuedRunId && !completedRun) || !!connection?.syncing
  useEffect(() => {
    const timer = setInterval(state.reload, awaiting ? 3000 : 30000)
    return () => clearInterval(timer)
  }, [state.reload, awaiting])
  async function perform(name, action) {
    setBusy(name); setError(''); setMessage(''); setQueuedRunId(null)
    try { await action(); state.reload() } catch (failure) { setError(failure.message) } finally { setBusy('') }
  }
  async function connect(event) {
    event.preventDefault(); const fields = new FormData(event.currentTarget)
    await perform('connect', async () => {
      const data = await api('/mail-sync/connect', { method: 'POST', body: { email_address: fields.get('email'), app_password: fields.get('password') } })
      setMailbox(data); password.current.value = ''; setMessage('Gmail connected. The App Password is encrypted on the server.')
    })
  }
  return <section className="panel" aria-labelledby="mail-settings-title">
    <div className="sectionhead"><h2 id="mail-settings-title">Email tracking settings</h2><span>{connection ? 'Connected' : 'Not connected'}</span></div>
    <p>Your Google account must have two-factor authentication (2-Step Verification) turned on.</p>
    <p>Gmail → Manage your Google Account → Security &amp; sign-in → Search for “App Passwords” → Name it “zoin” (recommended) → Create.</p>
    {state.loading && !state.data && <p role="status">Loading email settings…</p>}
    {(error || state.error) && <p role="alert">{error || state.error} <button onClick={state.reload}>Retry</button></p>}
    {syncMessage && <p role="status">{syncMessage}</p>}
    {state.data?.configured === false && <p role="alert">The server needs its email encryption and worker keys before Gmail can be saved.</p>}
    {!connection ? <form onSubmit={connect}>
      <label>Gmail address<input name="email" type="email" defaultValue={user?.email || ''} required disabled={!!busy} autoComplete="email" /></label>
      <div className="mail-fields"><label>App Password<input ref={password} name="password" type="password" required disabled={!!busy} autoComplete="off" placeholder="16-character App Password" /></label><button disabled={!!busy || !state.data || !state.data.configured}>{busy === 'connect' ? 'Connecting…' : 'Connect & save securely'}</button></div>
      <p>Encrypted on the server for scheduled checks, including when you are logged out. Disconnect deletes the saved credential.</p>
    </form> : <>
      <p>{connection.email_address}</p>
      <label>Sync frequency<select value={interval ?? connection.interval_days} disabled={!!busy} onChange={e => setIntervalDays(Number(e.target.value))}>{[1,2,3,4,5,6,7].map(n => <option key={n} value={n}>{n === 1 ? 'Daily' : n === 7 ? 'Once a week' : `Every ${n} days`}</option>)}</select></label>
      <label className="sync-toggle"><input type="checkbox" checked={enabled ?? connection.enabled} disabled={!!busy} onChange={e => setEnabled(e.target.checked)} /> Enable scheduled tracking</label>
      <div className="resume-actions"><button disabled={!!busy} onClick={() => perform('settings', async () => { await api('/mail-sync/settings', { method:'PATCH', body:{ interval_days: interval ?? connection.interval_days, enabled: enabled ?? connection.enabled } }); setIntervalDays(null); setEnabled(null); setMessage('Sync settings saved.') })}>Save schedule</button>
      <button disabled={!!busy || !!connection.syncing} onClick={() => perform('sync', async () => { const result = await api('/mail-sync/now', { method:'POST', body:{} }); setQueuedRunId(result.run_id); setMessage('Sync queued. Results and timestamps update when it finishes.') })}>{connection.syncing ? 'Syncing…' : 'Sync now'}</button>
      <button disabled={!!busy} onClick={() => perform('disconnect', async () => { await api('/mail-sync/connection', { method:'DELETE' }); setMailbox(null); setMessage('Gmail disconnected; saved credential deleted.') })}>Disconnect</button></div>
      <p><SyncTime value={connection.last_synced_at} />{connection.last_error && ` · ${syncErrors[connection.last_error] || 'Last sync failed.'}`}</p>
      <p className="small">Scheduled checks track Applied jobs only. Saving a Found job checks for confirmation immediately; Sync now also rechecks Found jobs. The backend must remain running.</p>
      <p className="small">Next scheduled check: {connection.enabled ? new Date(connection.next_sync_at).toLocaleString() : 'Paused'}</p>
    </>}
    {!!state.data?.runs?.length && <details><summary>Recent sync log</summary><ul>{state.data.runs.map(run => <li key={run.id}>{new Date(run.started_at).toLocaleString()} · {run.trigger.replace('_',' ')} · {run.status} · {run.checked_jobs} jobs checked · {run.changed_jobs} changed{run.error_code && ` · ${syncErrors[run.error_code] || 'Sync error'}`}</li>)}</ul></details>}
    <p className="small muted">Logs with no changes expire after 7 days. Status-change history is retained. Email bodies and App Passwords are not logged.</p>
  </section>
}
