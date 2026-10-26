import { Navigate, useLocation } from 'react-router-dom'
import { useSession } from '../lib/session'
export default function ProtectedRoute({ children }) {
  const { session, loading, authError } = useSession()
  const location = useLocation()
  if (loading) return <main className="screen"><p role="status">Loading your session…</p></main>
  if (authError) return <main className="screen"><p role="alert">{authError}</p><button onClick={() => window.location.reload()}>Reload</button></main>
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return children
}
