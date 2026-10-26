import { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
export default function CheckEmail() {
  const { state } = useLocation(), heading = useRef(null)
  useEffect(() => { heading.current?.focus() }, [])
  return <main className="auth-screen"><section className="auth-box" aria-labelledby="check-email-title">
    <h1 id="check-email-title" ref={heading} tabIndex={-1}>Check your email</h1>
    <p className="auth-lead">Verify your account to continue.</p>
    <p>{state?.email ? <>Check <strong>{state.email}</strong></> : 'Check the email address you registered with'} for a confirmation link. Open it to verify your account, then sign in.</p>
    <p>Check spam or junk if it hasn&apos;t arrived.</p>
    <p className="auth-links"><Link className="save" to="/login" state={{ email: state?.email }}>Go to Login</Link></p>
    <p className="auth-links">Wrong address? <Link to="/register">Back to registration</Link></p>
  </section></main>
}
