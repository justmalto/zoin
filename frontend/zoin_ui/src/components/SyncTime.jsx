import { useEffect, useState } from 'react'
import { syncAge } from '../lib/syncStatus'
export default function SyncTime({ value }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 60000); return () => clearInterval(timer) }, [])
  return <span>{syncAge(value, now)}</span>
}
