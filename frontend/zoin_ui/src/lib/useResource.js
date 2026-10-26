import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { useSession } from './session'

export function useResource(path, refreshKey = '') {
  const session = useSession()
  const key = JSON.stringify([path, session?.user?.id || null])
  const [state, setState] = useState({ key: null, data: null, loading: !!path, error: '' })
  const [revision, setRevision] = useState(0)
  const reload = useCallback(() => {
    setState(old => ({ ...old, loading: true, error: '' })); setRevision(n => n + 1)
  }, [])
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    api(path, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setState({ key, data, loading: false, error: '' })
    }).catch(error => {
      if (!controller.signal.aborted) setState(old => ({ key, data: old.key === key ? old.data : null, loading: false, error: error.message }))
    })
    return () => controller.abort()
  }, [path, key, revision, refreshKey])
  if (!path) return { data: null, loading: false, error: '', reload }
  // Never render another route/account's cached value, even for the render
  // before the effect starts its new request.
  return { ...(state.key === key ? state : { data: null, loading: true, error: '' }), reload }
}
