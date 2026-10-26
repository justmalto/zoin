export function generationError(error) {
  const reasons = {
    0: 'Zoin could not be reached. Check that the API is running and your connection is working.',
    401: 'Your session expired. Sign in again before retrying.',
    409: 'The job or resume changed during generation. Reload the latest inputs before generating again.',
    413: 'The full document exceeds the free-tier request budget. Shorten non-essential text in the job description before retrying.',
    422: 'Check that the job and a readable saved resume are available, then retry.',
    429: 'The Groq free-tier token or request limit is busy. Your documents are saved; wait for the cooldown shown below.',
    502: 'The AI response could not be verified, or the result could not be saved. An unverified score will not be displayed. Retry after a short pause.',
    503: 'The AI service is unavailable or its server key/model configuration needs attention.',
    504: 'Generation timed out. Check saved results before retrying because the server may still finish saving.',
  }
  return { message: error.message || 'Unable to generate an analysis.', explanation: reasons[error.status] || 'The server could not complete generation. Your saved job and resume are unchanged.' }
}
