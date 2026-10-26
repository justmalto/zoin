export const labels = { found: 'Found', draft: 'Found', applied: 'Applied', assessment: 'Assessment', interview: 'Interview', offer: 'Offer', rejected: 'Rejected', withdrawn: 'Withdrawn', failed: 'Failed' }
export const transitions = { found: ['assessment', 'interview', 'offer', 'rejected', 'withdrawn'], draft: ['found', 'withdrawn'], applied: ['assessment', 'interview', 'offer', 'rejected', 'withdrawn'], assessment: ['interview', 'offer', 'rejected', 'withdrawn'], interview: ['offer', 'rejected', 'withdrawn'] }
export const statusTone = status => status === 'assessment' ? 'interview' : status
export function dateLabel(value) { return value ? new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—' }
