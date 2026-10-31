import SyncTime from './SyncTime'
import { labels, dateLabel, statusTone } from '../lib/status'
export default function JobCard({ job, onClick }) {
  return <button type="button" className="job" onClick={onClick} aria-label={`View ${job.job_title} at ${job.company}`}>
    <div className="cardtop"><span className="company">{job.company}</span><span className={`status ${statusTone(job.status)}`}>{labels[job.status] || job.status}</span></div>
    <h2 title={job.job_title}>{job.job_title}</h2>
    <div className="details"><div>Status:<b>{labels[job.status]}</b>Latest update:<b>{dateLabel(job.updated_at)}</b></div>
      <div>Date applied:<b>{dateLabel(job.applied_at)}</b>Resume:<b title={job.resume_name}>{job.resume_name || '—'}</b></div>
      <div className="match">Match<b>{job.match_score == null ? '—' : `${job.match_score}%`}</b></div>
    </div>
    <div className="small muted sync-card"><SyncTime value={job.last_mail_synced_at} />{job.mail_sync_error && " · Sync needs attention"}{job.confirmation_pending && " · Confirmation check pending"}</div>
  </button>
}
