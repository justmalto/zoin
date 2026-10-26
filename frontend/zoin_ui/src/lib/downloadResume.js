import { api } from './api'

export async function downloadResume({ resumeId, file }) {
  let blob = file, name = file?.name
  if (!blob) {
    const result = await api(`/resumes/${encodeURIComponent(resumeId)}/download`)
    if (!result?.url) throw new Error('The resume download is unavailable. Please retry.')
    const response = await fetch(result.url, { credentials: 'omit', signal: AbortSignal.timeout(60000) })
    if (!response.ok) throw new Error('Unable to download the resume. Please retry.')
    blob = await response.blob(); name = result.file_name
  }
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url; link.download = name?.split(/[\\/]/).pop() || 'resume'
  document.body.appendChild(link); link.click(); link.remove()
  // Keep the object URL alive long enough for browsers to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}
