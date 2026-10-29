import { supabase } from './supabaseClient'

export class ApiError extends Error {
  constructor(message, status, detail, retryAfter) {
    super(message); this.status = status; this.detail = detail; this.retryAfter = retryAfter
  }
}

export async function api(path, { body, timeoutMs = 120000, responseType, ...options } = {}) {
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new ApiError('Please sign in again.', 401)
  const headers = { Authorization: `Bearer ${session.access_token}`, ...options.headers }
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'
  let response
  const timerSignal = timeoutMs ? AbortSignal.timeout(timeoutMs) : null
  const signal = timerSignal ? (options.signal ? AbortSignal.any([options.signal, timerSignal]) : timerSignal) : options.signal
  try {
    response = await fetch(`${(import.meta.env.VITE_API_URL || '/api/v1').replace(/\/+$/, '')}${path}`, {
      ...options, signal, headers, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    if (timerSignal?.aborted) throw new ApiError('The request exceeded the two-minute response window.', 504)
    if (error.name === 'AbortError') throw error
    throw new ApiError('Unable to reach Zoin. Check your connection and retry.', 0)
  }
  if (response.ok && responseType === 'blob') return response.blob()
  const data = response.status === 204 ? null : await response.json().catch(() => null)
  if (response.ok && response.status !== 204 && data === null) throw new ApiError('Zoin returned an invalid response. Check the backend URL and retry.', 502)
  if (!response.ok) {
    const detail = data?.detail
    const message = typeof detail === 'string' ? detail : detail?.message || (Array.isArray(detail) ? detail.map(x => x.msg).join('. ') : 'This request could not be completed.')
    const retryAfter = Number(response.headers.get('Retry-After')) || null
    throw new ApiError(message + (retryAfter ? ` Try again in ${retryAfter} seconds.` : ''), response.status, detail, retryAfter)
  }
  return data
}

export async function uploadResume(file, replaceId = null) {
  if (file.size > 10 * 1024 * 1024) throw new Error('Resume must be under 10 MB.')
  const body = new FormData(); body.append('file', file)
  try { return await api(replaceId ? `/resumes?replace_id=${encodeURIComponent(replaceId)}` : '/resumes', { method: 'POST', body }) }
  catch (error) {
    if (error.status === 409 && error.detail?.resume_id) return { id: error.detail.resume_id, file_name: file.name }
    throw error
  }
}
