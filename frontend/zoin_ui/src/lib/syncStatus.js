export function syncAge(value, now = Date.now()) {
  if (!value) return 'Not synced yet'
  const minutes = Math.max(0, Math.floor((now - new Date(value).getTime()) / 60000))
  if (!Number.isFinite(minutes)) return 'Not synced yet'
  if (minutes < 1) return 'Synced just now'
  if (minutes < 60) return `Synced ${minutes} min${minutes === 1 ? '' : 's'} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `Synced ${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.floor(hours / 24)
  return `Synced ${days} day${days === 1 ? '' : 's'} ago`
}
export const syncErrors = {
  gmail_unavailable: 'Gmail could not be read. Check or reconnect your App Password.',
  credential_unavailable: 'Saved credentials could not be decrypted. Reconnect Gmail.',
  ai_rate_limit: 'Groq free-tier limit reached. Try Sync now later.',
  ai_unavailable: 'Email analysis is unavailable. Try again later.',
  more_candidates: 'More inbox candidates remain. Sync again to continue.',
  sync_failed: 'Sync failed. Try again or reconnect Gmail.',
  worker_interrupted: 'The server stopped before this sync finished. A new sync can retry.',
}
