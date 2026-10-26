import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useResource } from '../lib/useResource'
import { labels } from '../lib/status'
import JobCard from '../components/jobCard'
import StatusSummary from '../components/StatusSummary'

export default function Home() {
  const location = useLocation(), navigate = useNavigate()
  const { data, loading, error, reload } = useResource('/applications', location.key)
  const lastReturnRefresh = useRef(0)
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible' || Date.now() - lastReturnRefresh.current < 1000) return
      lastReturnRefresh.current = Date.now(); reload()
    }
    window.addEventListener('focus', onVisible)
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('zoin:applications-changed', reload)
    return () => {
      window.removeEventListener('focus', onVisible)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('zoin:applications-changed', reload)
    }
  }, [reload])
  const [filter, setFilter] = useState('all')
  const jobs = data || [], filtered = jobs.filter(job => filter === 'all' || job.status === filter)
  const open = path => navigate(path, { state: { backgroundLocation: location } })
  return <main id="main">
    <div className="top"><h1>Applications</h1><div className="tools">
      <select aria-label="Filter applications" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All</option>{Object.entries(labels).filter(([value]) => value !== 'draft').map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <button id="add" onClick={() => open('/jobs/new')}><span aria-hidden="true">＋</span><span>Add Job</span></button>
    </div></div>
    <StatusSummary jobs={jobs} onSelect={setFilter} />
    {error && <div className="alert" role="alert">{error} <button onClick={reload}>Retry</button></div>}
    {loading && data === null && <p className="muted" role="status">Loading applications…</p>}
    <section className="cards" aria-label="Job applications">{filtered.map(job => <JobCard key={job.id} job={job} onClick={() => open(`/jobs/${job.id}`)} />)}
      {!loading && !error && !filtered.length && <div className="screen-state"><h2>{jobs.length ? 'No matching applications' : 'No applications yet'}</h2><p>{jobs.length ? 'Choose another status to see your applications.' : 'Add your first job to start tracking your search.'}</p><button onClick={() => jobs.length ? setFilter('all') : open('/jobs/new')}>{jobs.length ? 'Show all' : '＋ Add Job'}</button></div>}
    </section>
  </main>
}
