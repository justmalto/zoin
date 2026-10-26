import { useState } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { supabase } from '../lib/supabaseClient'
import { useSession } from '../lib/session'
export default function Login() {
  const { user, loading } = useSession(), location = useLocation()
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  if (user && !loading) return <Navigate to={location.state?.from?.startsWith('/jobs/') ? location.state.from : '/home'} replace />
  async function submit(event) {
    event.preventDefault(); setError(''); setBusy(true)
    const fields = new FormData(event.currentTarget)
    try {
      const { error: failure } = await supabase.auth.signInWithPassword({ email: fields.get('email'), password: fields.get('password') })
      if (failure) throw failure
    } catch (failure) { setError(failure.message || 'Unable to sign in. Please retry.') }
    finally { setBusy(false) }
  }
  return <main className="auth-screen"><form className="auth-box" onSubmit={submit}>
    <h1>LOGIN</h1><p className="auth-lead">Your applications, all in one place.</p>
    <label>Email<input name="email" type="email" defaultValue={location.state?.email || ''} autoComplete="email" placeholder="you@example.com" required disabled={busy} /></label>
    <label>Password<input name="password" type="password" autoComplete="current-password" placeholder="Enter your password" required disabled={busy} /></label>
    {error && <p className="inline-message" role="alert">{error}</p>}
    <button className="save" disabled={busy || loading}>{busy ? 'Signing in…' : 'Sign In'}</button>
    <p className="auth-links">Don&apos;t have an account? <Link to="/register">REGISTER</Link></p>
  </form></main>
}
