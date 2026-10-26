import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { supabase } from '../lib/supabaseClient'
import { passwordRules, validPassword } from '../lib/passwordPolicy'
export default function Register() {
  const navigate = useNavigate()
  const [error, setError] = useState(''), [password, setPassword] = useState(''), [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault(); setError('')
    const form = event.currentTarget, fields = new FormData(form)
    if (!validPassword(password)) { setError('Use at least 12 characters with uppercase, lowercase, a number and a symbol.'); form.elements.password.focus(); return }
    if (password !== fields.get('confirmPassword')) { setError('Passwords do not match. Please try again.'); form.elements.confirmPassword.focus(); return }
    setBusy(true)
    try {
      const email = fields.get('email').trim()
      const { data, error: failure } = await supabase.auth.signUp({ email, password, options: { data: { name: fields.get('name') }, emailRedirectTo: `${window.location.origin}/login` } })
      if (failure) throw failure
      setPassword(''); form.reset()
      navigate(data.session ? '/home' : '/check-email', { replace: true, state: { email } })
    } catch (failure) { setError(failure.message || 'Unable to create the account. Please retry.') }
    finally { setBusy(false) }
  }
  return <main className="auth-screen"><form className="auth-box" onSubmit={submit}>
    <h1>REGISTER</h1><p className="auth-lead">A place for your next opportunity.</p>
    <label>Name<input name="name" autoComplete="name" placeholder="Your name" maxLength={100} required disabled={busy} /></label>
    <label>Email<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required disabled={busy} /></label>
    <label>Password<input name="password" type="password" autoComplete="new-password" minLength={12} value={password} onChange={event => setPassword(event.target.value)} placeholder="Create a password" required disabled={busy} aria-describedby="password-rules" /></label>
    <ul id="password-rules" className="password-rules">{passwordRules.map(([label, check]) => <li key={label}>{check(password) ? '✓' : '○'} {label}</li>)}</ul>
    <label>Confirm Password<input name="confirmPassword" type="password" autoComplete="new-password" minLength={12} placeholder="Repeat your password" required disabled={busy} aria-describedby={error ? 'register-error' : undefined} /></label>
    {error && <p id="register-error" className="inline-message" role="alert">{error}</p>}
    <button className="save" disabled={busy}>{busy ? 'Creating account…' : 'Sign Up'}</button>
    <p className="auth-links">Already have an account? <Link to="/login">LOGIN</Link></p>
  </form></main>
}
