import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useSession } from '../lib/session'
import { useResource } from '../lib/useResource'
import { api, uploadResume } from '../lib/api'
import ResumeDownload from '../components/ResumeDownload'
import MailSyncSettings from '../components/MailSyncSettings'
import StatusSummary from '../components/StatusSummary'
import placeholder from '../assets/profilep.png'

export default function Profile() {
  const { user, profile, setProfile, logout } = useSession()
  const resumes = useResource('/resumes'), jobs = useResource('/applications')
  const navigate = useNavigate()
  const [replacement, setReplacement] = useState('')
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('')
  async function perform(name, action, success) {
    setBusy(name); setError(''); setMessage('')
    try { await action(); if (success) setMessage(success) } catch (failure) { setError(failure.message) } finally { setBusy('') }
  }
  async function selectResume(event) {
    const file = event.target.files?.[0]; if (!file) return
    await perform('upload', async () => { await uploadResume(file,replacement || null); setReplacement(''); resumes.reload() }, 'Resume uploaded.')
    event.target.value = ''
  }
  async function openResume(id) {
    await perform('download', async () => {
      const result = await api(`/resumes/${id}/download`)
      const link = document.createElement('a'); link.href = result.url; link.target = '_blank'; link.rel = 'noopener'; link.click()
    })
  }
  async function avatar(event) {
    const file = event.target.files?.[0]; if (!file) return
    await perform('avatar', async () => {
      const body = new FormData(); body.append('file', file)
      setProfile(await api('/profile/avatar', { method: 'POST', body }))
    }, 'Profile picture updated.')
    event.target.value = ''
  }
  const name = profile?.display_name || user?.user_metadata?.name || 'User'
  return <main className="screen">
    <Link className="back-link" to="/home">← Applications</Link><h1 className="screen-title">Profile</h1>
    {error && <p className="alert" role="alert">{error}</p>}{message && <p className="inline-message" role="status">{message}</p>}
    <div className="profile-layout"><div className="profile-content">
      <h2 className="profile-name">{name}</h2><p className="profile-email">{user?.email}</p>
      <MailSyncSettings />
      <section aria-labelledby="statsTitle"><h2 className="section-title" id="statsTitle">Application Stats</h2>
        {jobs.error ? <p className="alert" role="alert">{jobs.error} <button onClick={jobs.reload}>Retry</button></p> : <StatusSummary jobs={jobs.data || []} />}
      </section>
      <section className="panel"><div className="sectionhead"><h2>My Resumes</h2><label className={`upload-label ${busy ? 'disabled' : ''}`}>＋ Upload<input type="file" className="visually-hidden" aria-label="Upload resume" accept=".pdf,.docx,.txt" onChange={selectResume} disabled={!!busy || (resumes.data?.length >= 10 && !replacement)} /></label></div>
        <p>PDF, DOCX or TXT · Up to 10 MB · {resumes.data?.length || 0}/10 saved resumes. Choose your default resume.</p>
        {resumes.data?.length >= 10 && <label>Library full — choose a resume to replace<select aria-label="Profile resume replacement" value={replacement} onChange={e => setReplacement(e.target.value)}><option value="">Choose a saved resume</option>{resumes.data.map(r => <option key={r.id} value={r.id}>{r.file_name}</option>)}</select><span>Replacing clears old matches for this entry.</span></label>}
        {resumes.loading && <p role="status">Loading resumes…</p>}
        {resumes.error && <p role="alert">{resumes.error} <button onClick={resumes.reload}>Retry</button></p>}
        <div className="resume-list">{resumes.data?.map(resume => <div className="resume-row" key={resume.id}><div><strong>{resume.file_name}</strong><small>{resume.is_active ? 'Default resume' : 'Ready to use'}</small></div><div className="resume-actions">
          <button onClick={() => openResume(resume.id)} disabled={!!busy}>Open</button>
          <ResumeDownload resumeId={resume.id} disabled={!!busy} />
          {resume.is_active ? <span className="active-resume">Active</span> : <button disabled={!!busy} onClick={() => perform('activate', async () => { await api(`/resumes/${resume.id}/activate`, { method: 'POST' }); resumes.reload() }, 'Default resume updated.')}>Set active</button>}
        </div></div>)}</div>
        {!resumes.loading && !resumes.data?.length && !resumes.error && <p>No resumes yet. Upload one to start matching.</p>}
      </section>
      <button disabled={!!busy} onClick={() => perform('logout', async () => { await logout(); navigate('/login', { replace: true }) })}>Logout</button>
    </div><aside className="profile-photo"><img src={profile?.avatar_url || placeholder} alt="Profile picture" /><label>Edit Profile Picture<input className="visually-hidden" type="file" aria-label="Edit profile picture" accept="image/png,image/jpeg,image/webp" disabled={!!busy} onChange={avatar} /></label><p className="muted small">PNG, JPEG or WebP · 2 MB</p></aside></div>
  </main>
}
