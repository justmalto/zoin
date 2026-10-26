import { useCallback, useEffect, useRef, useState } from 'react'
import { SessionContext } from '../lib/session'
import { supabase } from '../lib/supabaseClient'
import { api } from '../lib/api'

export default function SessionProvider({ children }) {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [authError, setAuthError] = useState('')
  const [mailbox, setMailbox] = useState(null)
  const [profile, setProfile] = useState(null)
  const owner = useRef(null)
  useEffect(() => {
    let alive = true, eventSeen = false
    const update = next => {
      if (!alive) return
      if (owner.current !== next?.user?.id) { setMailbox(null); setProfile(null) }
      owner.current = next?.user?.id
      setSession(next); setLoading(false)
    }
    const { data: listener } = supabase.auth.onAuthStateChange((_event, next) => { eventSeen = true; update(next) })
    supabase.auth.getSession().then(({ data, error }) => {
      if (error) throw error
      if (!eventSeen) update(data.session)
    }).catch(() => { if (alive) { setAuthError('Unable to restore your session. Please reload.'); setLoading(false) } })
    return () => { alive = false; listener.subscription.unsubscribe() }
  }, [])
  const userId = session?.user?.id
  useEffect(() => {
    if (!userId) return
    let alive = true
    api('/mail-sync').then(data => { if (alive) setMailbox(data.connection ? {email_address: data.connection.email_address} : null) }).catch(() => {})
    api('/profile').then(data => { if (alive) setProfile(data) }).catch(() => {})
    return () => { alive = false }
  }, [userId])
  const logout = useCallback(async () => {
    const { error } = await supabase.auth.signOut()
    if (error) throw error
    setMailbox(null); setProfile(null); setSession(null)
  }, [])
  return <SessionContext.Provider value={{ session, user: session?.user, loading, authError, mailbox, setMailbox, profile, setProfile, logout }}>{children}</SessionContext.Provider>
}
