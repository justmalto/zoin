import { useEffect, useState } from 'react'
export default function GenerationStatus({ pending, startedAt, error, retryAt, onRetry }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!pending && !retryAt) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [pending, retryAt])
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  const cooldown = Math.max(0, Math.ceil(((retryAt || 0) - now) / 1000))
  if (pending) return <div className="generation-status" role="status" aria-live="polite">
    <strong>Generating match score and recommendations…</strong>
    <p>Estimated time: 10–60 seconds · {elapsed}s elapsed. This is an estimate, not a guaranteed finish time.</p>
    <p>{elapsed < 60 ? 'Comparing your saved resume with the job requirements, validating evidence and saving the result.' : 'Taking longer than expected. The server is still responding; please avoid starting another generation.'}</p>
  </div>
  if (error) return <div className="generation-status" role="alert">
    <strong>Generation did not complete</strong><p>{error.message}</p><p>{error.explanation}</p>
    <button type="button" onClick={onRetry} disabled={cooldown > 0}>{cooldown ? `Retry in ${cooldown}s` : 'Retry generation'}</button>
  </div>
  return <p className="small muted">Match score and recommendations are generated together when you click Generate. Estimated time: 10–60 seconds, plus any free-tier cooldown.</p>
}
