import { useState } from 'react'
import { downloadResume } from '../lib/downloadResume'

export default function ResumeDownload({ resumeId, file, disabled = false }) {
  const [pending, setPending] = useState(false), [error, setError] = useState('')
  async function download() {
    setPending(true); setError('')
    try { await downloadResume({ resumeId, file }) }
    catch (failure) { setError(failure.name === 'TimeoutError' ? 'Download timed out. Please retry.' : failure.message || 'Unable to download the resume. Please retry.') }
    finally { setPending(false) }
  }
  return <span><button type="button" onClick={download} disabled={disabled || pending || (!file && !resumeId)}>{pending ? 'Downloading…' : file ? 'Download temporary resume' : 'Download resume'}</button>{error && <span className="inline-message" role="alert">{error}</span>}</span>
}
