import { labels } from '../lib/status'
export default function StatusSummary({ jobs, onSelect }) {
  return <div className="summary" aria-label="Application summary">{['applied', 'interview', 'offer', 'rejected'].map(status => {
    const text = `${labels[status]} · ${jobs.filter(job => job.status === status && (status !== 'applied' || job.receipt_confirmed_at)).length}`
    return onSelect ? <button key={status} className={`status ${status}`} onClick={() => onSelect(status)}>{text}</button> : <span key={status} className={`status ${status}`}>{text}</span>
  })}</div>
}
